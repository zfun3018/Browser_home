"""Safely read public page metadata for an explicitly selected bookmark."""
import asyncio
import ipaddress
import json
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from .schemas import safe_url

MAX_HTML_BYTES = 1_000_000
MAX_REDIRECTS = 5
_SECOND_LEVEL_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "com.cn", "net.cn", "org.cn", "com.au", "net.au", "co.jp", "co.nz"}
_SYNTHETIC_PROXY_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)


class MetadataFetchError(Exception):
    pass


def main_domain_url(raw_url: str) -> str:
    """Return the site's root URL so metadata is shared across deep links."""
    try:
        parsed = urlsplit(safe_url(raw_url))
        host = (parsed.hostname or "").rstrip(".").lower()
        if host.startswith("www."):
            host = host[4:]
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(labels) > 2:
                suffix = ".".join(labels[-2:])
                host = ".".join(labels[-3:]) if suffix in _SECOND_LEVEL_SUFFIXES else suffix
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme.lower()}://{host}{port}/"
    except (ValueError, TypeError):
        raise MetadataFetchError("书签网址不是有效的 HTTP 或 HTTPS 地址") from None


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = []
        self.descriptions: dict[str, str] = {}
        self.json_ld: list[str] = []
        self._in_title = False
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "script" and "ld+json" in attrs.get("type", "").lower():
            self._in_json_ld = True
            self._json_ld_parts = []
        if tag.lower() != "meta":
            return
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in {"description", "og:description", "twitter:description"}:
            value = " ".join(attrs.get("content", "").split())
            if value:
                self.descriptions[key] = value

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False
        if tag.lower() == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_ld_parts))
            self._in_json_ld = False
            self._json_ld_parts = []

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        if self._in_json_ld:
            self._json_ld_parts.append(data)


def _json_ld_description(blocks: list[str]) -> str:
    def find_description(value):
        if isinstance(value, dict):
            description = value.get("description")
            if isinstance(description, str) and description.strip():
                return " ".join(description.split())
            for child in value.values():
                found = find_description(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_description(child)
                if found:
                    return found
        return ""

    for block in blocks:
        try:
            found = find_description(json.loads(block))
        except (json.JSONDecodeError, RecursionError):
            continue
        if found:
            return found
    return ""


async def assert_public_url(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise MetadataFetchError("仅支持公开的 HTTP 或 HTTPS 网站")
    host = parsed.hostname.rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            records = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(record[4][0].split("%", 1)[0]) for record in records]
        except (OSError, ValueError):
            raise MetadataFetchError("无法解析网站地址") from None
    non_global = [address for address in addresses if not address.is_global]
    synthetic_proxy_dns = non_global and all(
        any(address in network for network in _SYNTHETIC_PROXY_NETWORKS)
        for address in non_global
    )
    if not addresses or (non_global and not synthetic_proxy_dns):
        raise MetadataFetchError("为保护本机安全，不能抓取内网或本机地址")


async def fetch_public_metadata(raw_url: str) -> dict[str, str]:
    try:
        current = main_domain_url(raw_url)
    except ValueError:
        raise MetadataFetchError("书签网址不是有效的 HTTP 或 HTTPS 地址") from None

    headers = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1", "User-Agent": "BrowserHomepageMetadata/1.0"}
    timeout = httpx.Timeout(8, connect=4)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await assert_public_url(current)
            try:
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            break
                        try:
                            current = safe_url(urljoin(current, location))
                        except ValueError:
                            raise MetadataFetchError("网站重定向到无效或不支持的地址") from None
                        continue
                    if response.status_code >= 400 or "html" not in response.headers.get("content-type", "").lower():
                        return {"title": "", "description": ""}
                    length = response.headers.get("content-length")
                    if length and int(length) > MAX_HTML_BYTES:
                        raise MetadataFetchError("网页过大，已停止读取")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_HTML_BYTES:
                            raise MetadataFetchError("网页过大，已停止读取")
                    charset = response.encoding or "utf-8"
                    html = data.decode(charset, errors="replace")
                parser = _MetadataParser()
                parser.feed(html)
                description = next((parser.descriptions[key] for key in ("og:description", "description", "twitter:description") if key in parser.descriptions), "")
                if not description:
                    description = _json_ld_description(parser.json_ld)
                title = " ".join(" ".join(parser.title).split())
                return {"title": title[:2048], "description": description[:2048]}
            except MetadataFetchError:
                raise
            except (httpx.HTTPError, UnicodeError, ValueError):
                return {"title": "", "description": ""}
    raise MetadataFetchError("网站重定向次数过多")
