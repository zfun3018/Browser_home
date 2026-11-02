from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


class DummyBookmarks:
    async def get(self, identity: int):
        return {"id": identity, "url": "https://example.com/"}

    async def custom_icon(self, identity: int):
        return None

    async def save_custom_icon(self, identity: int, data: bytes, content_type: str):
        return None


def test_icon_response_keeps_private_cache_header(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path), bookmarks=DummyBookmarks())

    async def fake_icon(_url: str, refresh: bool = False):
        return b"icon", "image/png"

    app.state.site_assets.get_icon = fake_icon
    with TestClient(app, base_url="http://localhost:3011") as client:
        response = client.get("/api/links/1/icon")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=3600"


def test_missing_icon_response_is_cached(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path), bookmarks=DummyBookmarks())

    async def missing_icon(_url: str, refresh: bool = False):
        from backend.app.site_assets import SiteIconNotFound

        raise SiteIconNotFound("missing")

    app.state.site_assets.get_icon = missing_icon
    with TestClient(app, base_url="http://localhost:3011") as client:
        response = client.get("/api/links/1/icon")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "private, max-age=86400"
