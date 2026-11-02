from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.bookmark_store import SqliteBookmarkStore
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.schemas import CollectionCreate
from backend.app.state_store import StateStore


def make_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, bookmark_db_path=tmp_path / "bookmarks.db")
    bookmarks = SqliteBookmarkStore(settings.bookmark_db_path)
    collection = bookmarks.create_collection(CollectionCreate(name="测试"))
    link = bookmarks.capture("https://example.com/", "Example", collection_id=collection["id"])
    app = create_app(settings, bookmarks=bookmarks, store=StateStore(tmp_path / "state.db"))
    return app, link["id"]


def test_upload_icon_is_saved_in_sqlite_and_served_locally(tmp_path):
    app, identity = make_app(tmp_path)
    png = b"\x89PNG\r\n\x1a\ncustom"
    with TestClient(app, base_url="http://localhost:3011") as client:
        uploaded = client.put(
            f"/api/links/{identity}/icon/custom",
            content=png,
            headers={"Content-Type": "image/png", "Origin": "http://localhost:3011"},
        )
        served = client.get(f"/api/links/{identity}/icon")

    assert uploaded.status_code == 200
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == png
    assert (tmp_path / "bookmarks.db").is_file()


def test_online_fetch_replaces_custom_icon(tmp_path):
    app, identity = make_app(tmp_path)
    app.state.site_assets.get_icon = _online_icon
    with TestClient(app, base_url="http://localhost:3011") as client:
        custom = client.put(
            f"/api/links/{identity}/icon/custom",
            content=b"\x89PNG\r\n\x1a\ncustom",
            headers={"Content-Type": "image/png", "Origin": "http://localhost:3011"},
        )
        fetched = client.post(
            f"/api/links/{identity}/icon/fetch",
            headers={"Origin": "http://localhost:3011"},
        )
        served = client.get(f"/api/links/{identity}/icon")

    assert custom.status_code == 200
    assert fetched.status_code == 200
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/svg+xml"
    assert served.content == b"<svg/>"


def test_upload_icon_rejects_non_image_payload(tmp_path):
    app, identity = make_app(tmp_path)
    with TestClient(app, base_url="http://localhost:3011") as client:
        response = client.put(
            f"/api/links/{identity}/icon/custom",
            content=b"not-an-image",
            headers={"Content-Type": "text/plain", "Origin": "http://localhost:3011"},
        )

    assert response.status_code == 415


def test_any_online_refresh_is_saved_in_sqlite_cache(tmp_path):
    app, identity = make_app(tmp_path)
    app.state.site_assets.get_icon = _online_icon
    with TestClient(app, base_url="http://localhost:3011") as client:
        refreshed = client.get(f"/api/links/{identity}/icon?refresh=1")
        app.state.site_assets.get_icon = _must_not_fetch
        local = client.get(f"/api/links/{identity}/icon")

    assert refreshed.status_code == 200
    assert local.status_code == 200
    assert local.content == b"<svg/>"


def test_icon_display_size_is_saved_and_returned_with_link(tmp_path):
    app, identity = make_app(tmp_path)
    with TestClient(app, base_url="http://localhost:3011") as client:
        response = client.patch(
            f"/api/links/{identity}/icon/size?size=76",
            headers={"Origin": "http://localhost:3011"},
        )
        home = client.get("/api/home")

    assert response.status_code == 200
    assert response.json() == {"size": 76}
    assert home.json()["links"][0]["iconSize"] == 76


async def _online_icon(_url: str, refresh: bool = False):
    assert refresh is True
    return b"<svg/>", "image/svg+xml"


async def _must_not_fetch(_url: str, refresh: bool = False):
    raise AssertionError("saved icon should be returned from SQLite")
