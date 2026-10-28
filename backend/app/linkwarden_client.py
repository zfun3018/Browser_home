"""Linkwarden v2.16.3 API adapter. Never expose raw upstream errors."""
import asyncio
from typing import Any
import httpx

from .schemas import LinkUpdate


class LinkwardenError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def map_link(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "name": row.get("name") or "", "url": row.get("url") or "", "description": row.get("description") or "", "icon": row.get("icon"), "iconWeight": row.get("iconWeight"), "color": row.get("color"), "collectionId": row.get("collectionId", row.get("collection", {}).get("id"))}


class LinkwardenClient:
    def __init__(self, base_url: str, token: str | None, *, transport=None):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport
        self._writes = asyncio.Lock()

    async def request(self, method, path, **kwargs):
        if not self._token:
            raise LinkwardenError("Linkwarden API token is unavailable")
        try:
            async with httpx.AsyncClient(base_url=self.base_url, headers={"Authorization": f"Bearer {self._token}"}, timeout=15, transport=self._transport, follow_redirects=False) as client:
                response = await client.request(method, "/api/v1/" + path, **kwargs)
            if response.status_code >= 300:
                status = response.status_code if response.status_code in (400, 401, 403, 404, 409) else 503
                raise LinkwardenError("Linkwarden rejected the request" if status != 503 else "Linkwarden is unavailable", status)
            payload = response.json()
            return payload["response"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise LinkwardenError("Linkwarden returned an unavailable or invalid response") from None

    async def links(self):
        rows, seen, cursor = [], set(), None
        while True:
            page = await self.request("GET", "links", params={"sort": 0, **({"cursor": cursor} if cursor is not None else {})})
            if not isinstance(page, list):
                raise LinkwardenError("Invalid Linkwarden link list")
            if not page:
                return rows
            for row in page:
                if not isinstance(row, dict) or not isinstance(row.get("id"), int) or row["id"] in seen:
                    raise LinkwardenError("Invalid or repeated Linkwarden page")
                seen.add(row["id"])
                rows.append(map_link(row))
            cursor = page[-1]["id"]

    async def collections(self):
        # v2.16.3 returns all accessible collections in one response (no cursor).
        rows = await self.request("GET", "collections")
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not all(key in row for key in ("id", "ownerId", "name")) for row in rows):
            raise LinkwardenError("Invalid Linkwarden collection list")
        return [{key: row[key] for key in ("id", "name", "ownerId")} for row in rows]

    async def update(self, identity: int, patch: LinkUpdate):
        async with self._writes:
            current = await self.request("GET", f"links/{identity}")
            if not isinstance(current, dict) or current.get("id") != identity:
                raise LinkwardenError("Invalid Linkwarden bookmark")
            changes = patch.model_dump(exclude_unset=True, exclude={"expected"})
            canonical = map_link(current)
            for field, value in changes.items():
                if canonical.get(field) != patch.expected[field] and canonical.get(field) != value:
                    raise LinkwardenError("Bookmark changed in Linkwarden; refresh before retrying", 409)
            payload = {key: current.get(key) for key in ("name", "url", "description", "icon", "iconWeight", "color", "tags", "pinnedBy")}
            collection = current.get("collection") or {}
            if "collectionId" in changes:
                target = changes.pop("collectionId")
                collection = next((row for row in await self.collections() if row["id"] == target), None)
                if collection is None:
                    raise LinkwardenError("Target collection does not exist", 404)
            if "id" not in collection or "ownerId" not in collection:
                raise LinkwardenError("Linkwarden collection metadata is incomplete")
            payload.update(changes)
            payload["id"] = identity
            payload["collection"] = {"id": collection["id"], "ownerId": collection["ownerId"]}
            result = await self.request("PUT", f"links/{identity}", json=payload)
            if not isinstance(result, dict) or result.get("id") != identity:
                raise LinkwardenError("Invalid updated Linkwarden bookmark")
            return map_link(result)
