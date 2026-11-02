from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.bookmark_store import SqliteBookmarkStore
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.schemas import LinkLayout
from backend.app.state_store import StateStore


def make_client(tmp_path: Path, token: str = "capture-secret"):
    token_file = tmp_path / "capture-token.txt"
    token_file.write_text(token, encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path,
        bookmark_db_path=tmp_path / "bookmarks.db",
        capture_token_file=token_file,
    )
    store = SqliteBookmarkStore(settings.bookmark_db_path)
    app = create_app(settings, store=StateStore(tmp_path / "state.db"), bookmarks=store)
    return TestClient(app, base_url="http://localhost:3011")


def test_capture_requires_token_and_creates_local_bookmark(tmp_path):
    with make_client(tmp_path) as client:
        denied = client.post("/api/capture", json={"url": "https://example.com", "title": "Example"})
        assert denied.status_code == 401

        response = client.post(
            "/api/capture",
            headers={"x-capture-token": "capture-secret"},
            json={"url": "https://example.com", "title": "Example"},
        )
        assert response.status_code == 201
        assert response.json()["link"]["name"] == "Example"


def test_capture_rejects_non_http_url(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/capture",
            headers={"x-capture-token": "capture-secret"},
            json={"url": "javascript:alert(1)", "title": "Bad"},
        )
        assert response.status_code == 422


def test_capture_uses_selected_collection_and_collections_are_listed(tmp_path):
    with make_client(tmp_path) as client:
        collection = client.post("/api/collections", json={"name": "稍后阅读"}, headers={"origin": "http://localhost:3011"})
        assert collection.status_code == 201
        listed = client.get("/api/collections")
        assert any(item["id"] == collection.json()["id"] for item in listed.json())
        captured = client.post(
            "/api/capture",
            headers={"x-capture-token": "capture-secret"},
            json={"url": "https://example.org/article", "title": "Article", "collectionId": collection.json()["id"]},
        )
        assert captured.status_code == 201
        assert captured.json()["link"]["collectionId"] == collection.json()["id"]


def test_delete_link_removes_it_and_its_layout_preference(tmp_path):
    with make_client(tmp_path) as client:
        captured = client.post(
            "/api/capture",
            headers={"x-capture-token": "capture-secret"},
            json={"url": "https://example.org/remove", "title": "Remove me"},
        )
        identity = captured.json()["link"]["id"]
        client.app.state.store.patch_link(identity, LinkLayout(showName=False))

        deleted = client.delete(f"/api/links/{identity}", headers={"origin": "http://localhost:3011"})

        assert deleted.status_code == 204
        assert client.get("/api/home").json()["links"] == []
        assert str(identity) not in client.get("/api/home").json()["preferences"]["links"]
        assert client.delete(f"/api/links/{identity}", headers={"origin": "http://localhost:3011"}).status_code == 404
