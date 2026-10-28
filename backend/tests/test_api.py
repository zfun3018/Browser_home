import httpx
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.linkwarden_client import LinkwardenClient
from backend.app.main import create_app
from backend.app.state_store import StateStore
from backend.tests.test_linkwarden_client import LINK


def test_home_and_canonical_updates_use_linkwarden_and_layout_stays_local(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("collections"):
            data = [{"id": 3, "name": "Work", "ownerId": 7}]
        elif request.url.path.endswith("/12"):
            data = {**LINK, "name": "Updated"} if request.method == "PUT" else LINK
        else:
            data = [] if request.url.params.get("cursor") else [LINK]
        return httpx.Response(200, json={"response": data})

    remote = LinkwardenClient("http://linkwarden:3000", "test-token", transport=httpx.MockTransport(handler))
    app = create_app(Settings(data_dir=tmp_path), remote, StateStore(tmp_path / "state.db"))
    with TestClient(app, base_url="http://localhost:3011", headers={"origin": "http://localhost:3011"}) as client:
        home = client.get("/api/home").json()
        assert home["sync"] == {"status": "ok"}
        assert home["links"][0]["id"] == 12
        assert home["collections"][0]["ownerId"] == 7
        assert client.patch("/api/links/12", json={"name": "Updated", "expected": {"name": "Original"}}).json()["name"] == "Updated"
        assert ("PUT", "/api/v1/links/12") in calls
        call_count = len(calls)
        assert client.patch("/api/collections/3/layout", json={"collapsed": True}).json()["collapsed"] is True
        assert client.patch("/api/links/12/layout", json={"showDescription": True}).json()["showDescription"] is True
        assert len(calls) == call_count
        search = {"providers": [{"id": "custom", "name": "Custom", "url": "https://example.org/?q={query}"}], "activeProviderId": "custom"}
        assert client.put("/api/search-engines", json=search).json() == search
        assert client.get("/api/search-engines").json() == search
