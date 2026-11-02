"""Fetch and cache public website icons using common site declarations."""
import asyncio
import hashlib
import json
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .metadata import MetadataFetchError, assert_public_url, main_domain_url
from .schemas import safe_url

MAX_HTML_BYTES = 1_000_000
MAX_MANIFEST_BYTES = 512_000
MAX_ICON_BYTES = 512_000
MAX_REDIRECTS = 5
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
NEGATIVE_CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_ICON_CANDIDATES = 16
MAX_APPLE_ICON_CANDIDATES = 4
MAX_MANIFEST_CANDIDATES = 2
MAX_MANIFEST_ICONS = 16
ICON_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/x-icon",
    "image/vnd.microsoft.icon", "image/svg+xml",
}
MAX_CUSTOM_ICON_BYTES = 4 * 1024 * 1024


def _image_type(content_type: str, data: bytes) -> str | None:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime in ICON_TYPES:
        return mime
    if mime != "application/octet-stream":
        return None
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_icon_payload(content_type: str, data: bytes) -> str:
    """Validate an uploaded icon without imposing a pixel dimension."""
    mime = content_type.split(";", 1)[0].strip().lower()
    if len(data) > MAX_CUSTOM_ICON_BYTES:
        raise ValueError("图标文件不能超过 4 MB")
    if not data:
        raise ValueError("图标文件不能为空")
    if mime == "image/svg+xml":
        sample = data[:4096].lstrip().lower()
        if b"<svg" in sample and b"<script" not in sample and b"javascript:" not in sample:
            return mime
        raise ValueError("SVG 图标包含不安全内容")
    detected = _image_type(mime, data)
    if detected:
        return detected
    raise ValueError("请选择有效的图片图标")


class SiteIconNotFound(MetadataFetchError):
    pass


class _IconParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.icons: list[str] = []
        self.apple_icons: list[str] = []
        self.manifests: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "link":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        href = values.get("href", "").strip()
        rels = set(values.get("rel", "").lower().split())
        if not href:
            return
        if "icon" in rels or "shortcut" in rels and "icon" in rels:
            if len(self.icons) < MAX_ICON_CANDIDATES:
                self.icons.append(href)
        elif "apple-touch-icon" in rels or "apple-touch-icon-precomposed" in rels:
            if len(self.apple_icons) < MAX_APPLE_ICON_CANDIDATES:
                self.apple_icons.append(href)
        elif "manifest" in rels:
            if len(self.manifests) < MAX_MANIFEST_CANDIDATES:
                self.manifests.append(href)


class SiteAssetService:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}

    async def get_icon(self, raw_url: str, refresh: bool = False) -> tuple[bytes, str]:
        try:
            canonical_url = main_domain_url(raw_url)
        except ValueError:
            raise MetadataFetchError("书签网址不是有效的 HTTP 或 HTTPS 地址") from None
        cache_key = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        self._lock_users[cache_key] = self._lock_users.get(cache_key, 0) + 1
        try:
            async with lock:
                if not refresh:
                    for candidate_key in self._cache_keys(raw_url, canonical_url):
                        cached = self._read_cache(candidate_key)
                        if cached:
                            if candidate_key != cache_key:
                                self._write_cache(cache_key, *cached)
                            return cached
                    if self._read_negative_cache(cache_key):
                        raise SiteIconNotFound("未找到可用的网站图标")
                try:
                    icon = await self._find_icon(canonical_url)
                except SiteIconNotFound:
                    # Keep a previously cached icon visible when a refresh is temporarily unavailable.
                    for candidate_key in self._cache_keys(raw_url, canonical_url):
                        cached = self._read_cache(candidate_key)
                        if cached:
                            if candidate_key != cache_key:
                                self._write_cache(cache_key, *cached)
                            return cached
                    self._write_negative_cache(cache_key)
                    raise
                self._write_cache(cache_key, *icon)
                return icon
        finally:
            users = self._lock_users[cache_key] - 1
            if users:
                self._lock_users[cache_key] = users
            else:
                self._lock_users.pop(cache_key, None)
                self._locks.pop(cache_key, None)

    @staticmethod
    def _cache_keys(raw_url: str, canonical_url: str) -> list[str]:
        """Include pre-canonicalization keys so existing icons survive migration."""
        legacy_urls = [raw_url]
        parsed = urlsplit(raw_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        legacy_urls.extend((origin, f"{origin}/"))
        keys: list[str] = []
        for value in (canonical_url, *legacy_urls):
            key = hashlib.sha256(value.encode("utf-8")).hexdigest()
            if key not in keys:
                keys.append(key)
        return keys

    def _read_cache(self, cache_key: str) -> tuple[bytes, str] | None:
        image_path = self.cache_dir / f"{cache_key}.bin"
        info_path = self.cache_dir / f"{cache_key}.json"
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if time.time() - float(info["savedAt"]) > CACHE_TTL_SECONDS:
                return None
            content_type = info["contentType"]
            if content_type not in ICON_TYPES:
                return None
            data = image_path.read_bytes()
            return (data, content_type) if data else None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _read_negative_cache(self, cache_key: str) -> bool:
        info_path = self.cache_dir / f"{cache_key}.json"
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            return info.get("status") == "missing" and time.time() - float(info["savedAt"]) <= NEGATIVE_CACHE_TTL_SECONDS
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return False

    def _write_cache(self, cache_key: str, data: bytes, content_type: str):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        image_path = self.cache_dir / f"{cache_key}.bin"
        info_path = self.cache_dir / f"{cache_key}.json"
        image_tmp = image_path.with_name(f"{image_path.name}.tmp")
        info_tmp = info_path.with_name(f"{info_path.name}.tmp")
        image_tmp.write_bytes(data)
        info_tmp.write_text(json.dumps({"contentType": content_type, "savedAt": time.time()}), encoding="utf-8")
        image_tmp.replace(image_path)
        info_tmp.replace(info_path)

    def _write_negative_cache(self, cache_key: str):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        info_path = self.cache_dir / f"{cache_key}.json"
        info_tmp = info_path.with_name(f"{info_path.name}.tmp")
        info_tmp.write_text(json.dumps({"status": "missing", "savedAt": time.time()}), encoding="utf-8")
        info_tmp.replace(info_path)

    async def _find_icon(self, page_url: str) -> tuple[bytes, str]:
        timeout = httpx.Timeout(8, connect=4)
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/manifest+json,application/json;q=0.9,*/*;q=0.1",
            "User-Agent": "BrowserHomepageIcon/1.0",
        }
        candidates: list[str] = []
        apple_candidates: list[str] = []
        page_base = page_url
        manifest_urls: list[str] = []
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, headers=headers) as client:
            try:
                page_type, page_data, page_base = await self._get(client, page_url, MAX_HTML_BYTES)
                if "html" in page_type:
                    parser = _IconParser()
                    parser.feed(page_data.decode("utf-8", errors="replace"))
                    candidates.extend(urljoin(page_base, href) for href in parser.icons)
                    apple_candidates.extend(urljoin(page_base, href) for href in parser.apple_icons)
                    manifest_urls.extend(urljoin(page_base, href) for href in parser.manifests)
            except (httpx.HTTPError, MetadataFetchError, UnicodeError, ValueError):
                pass

            for manifest_url in manifest_urls:
                try:
                    _, manifest_data, final_manifest_url = await self._get(client, manifest_url, MAX_MANIFEST_BYTES)
                    manifest = json.loads(manifest_data)
                    icons = manifest.get("icons", []) if isinstance(manifest, dict) else []
                    for item in icons[:MAX_MANIFEST_ICONS] if isinstance(icons, list) else []:
                        src = item.get("src") if isinstance(item, dict) else None
                        if isinstance(src, str) and src.strip():
                            candidates.append(urljoin(final_manifest_url, src.strip()))
                except (httpx.HTTPError, MetadataFetchError, UnicodeError, ValueError, TypeError):
                    continue

            candidates.extend(apple_candidates)
            origin = urlsplit(page_base)
            candidates.append(f"{origin.scheme}://{origin.netloc}/favicon.ico")
            seen: set[str] = set()
            for candidate in candidates:
                try:
                    candidate = safe_url(candidate)
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    content_type, data, _ = await self._get(client, candidate, MAX_ICON_BYTES)
                    mime = _image_type(content_type, data)
                    if mime and data:
                        return data, mime
                except (httpx.HTTPError, MetadataFetchError, UnicodeError, ValueError):
                    continue
        raise SiteIconNotFound("未找到可用的网站图标")

    async def _get(self, client: httpx.AsyncClient, url: str, max_bytes: int) -> tuple[str, bytes, str]:
        current = safe_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            await assert_public_url(current)
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise SiteIconNotFound("网站重定向缺少目标地址")
                    current = safe_url(urljoin(current, location))
                    continue
                if response.status_code >= 400:
                    raise SiteIconNotFound("网站未提供可读取的图标")
                length = response.headers.get("content-length")
                if length and int(length) > max_bytes:
                    raise SiteIconNotFound("网站资源超过大小限制")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise SiteIconNotFound("网站资源超过大小限制")
                return response.headers.get("content-type", "").lower(), bytes(body), str(response.url)
        raise SiteIconNotFound("网站重定向次数过多")
