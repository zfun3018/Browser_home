from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.state_store import StateStore


def app_client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path), store=StateStore(tmp_path / "state.db")), base_url="http://localhost:3011")


@pytest.mark.parametrize("headers", [{}, {"origin": "http://evil.example"}, {"origin": "null"}, {"origin": "http://localhost:3011.evil"}, {"origin": "http://127.0.0.1:3011"}, {"host": "evil.example", "origin": "http://evil.example"}, {"host": "localhost:3011", "origin": "http://localhost:3011", "sec-fetch-site": "cross-site"}])
def test_writes_reject_missing_or_cross_origin_and_unknown_host(tmp_path, headers):
    with app_client(tmp_path) as client:
        assert client.patch("/api/links/12/layout", json={"showName": False}, headers=headers).status_code == 403


def test_valid_same_origin_preferences_and_offline_health(tmp_path):
    with app_client(tmp_path) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.patch("/api/links/12/layout", json={"showName": False}, headers={"origin": "http://localhost:3011"}).status_code == 200
        response = client.get("/api/home")
        assert response.status_code == 200
        assert response.json()["sync"]["status"] == "ok"
        assert response.json()["preferences"]["links"]["12"]["showName"] is False
