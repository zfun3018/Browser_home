import asyncio
import json

import httpx
import pytest

from backend.app.linkwarden_client import LinkwardenClient, LinkwardenError
from backend.app.schemas import LinkUpdate


LINK = {"id": 12, "name": "Original", "url": "https://example.org", "description": "Keep", "icon": "Book", "iconWeight": "regular", "color": "#123456", "collectionId": 3, "collection": {"id": 3, "ownerId": 7}, "tags": [{"id": 9, "name": "retain"}], "pinnedBy": [{"id": 7}]}


def client(handler):
    return LinkwardenClient("http://linkwarden:3000", "test-token", transport=httpx.MockTransport(handler))


def test_links_follow_cursor_until_empty_and_map_canonical_fields():
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-token"
        cursor = request.url.params.get("cursor")
        rows = [LINK] if cursor is None else ([{**LINK, "id": 11}] if cursor == "12" else [])
        return httpx.Response(200, json={"response": rows})
    links = asyncio.run(client(handler).links())
    assert [row["id"] for row in links] == [12, 11]
    assert links[0]["color"] == "#123456"
    assert links[0]["collectionId"] == 3


def test_collections_include_owner_and_do_not_invent_pagination():
    def handler(request):
        assert request.url.path == "/api/v1/collections"
        return httpx.Response(200, json={"response": [{"id": 3, "name": "Work", "ownerId": 7}]})
    assert asyncio.run(client(handler).collections()) == [{"id": 3, "name": "Work", "ownerId": 7}]


def test_update_merges_latest_fields_preserves_tags_and_resolves_move_owner():
    def handler(request):
        if request.url.path.endswith("collections"):
            return httpx.Response(200, json={"response": [{"id": 4, "ownerId": 8, "name": "New"}]})
        if request.method == "GET":
            return httpx.Response(200, json={"response": LINK})
        body = json.loads(request.content)
        assert body["id"] == 12
        assert body["collection"] == {"id": 4, "ownerId": 8}
        assert body["tags"] == LINK["tags"]
        assert body["description"] == "Keep"
        assert body["url"] == "https://example.org"
        assert body["pinnedBy"] == [{"id": 7}]
        return httpx.Response(200, json={"response": {**LINK, **body, "collectionId": 4}})
    result = asyncio.run(client(handler).update(12, LinkUpdate(name="Renamed", collectionId=4, expected={"name": "Original", "collectionId": 3})))
    assert result["name"] == "Renamed"
    assert result["collectionId"] == 4


def test_update_detects_same_field_conflict_before_write():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"response": LINK})
    with pytest.raises(LinkwardenError) as error:
        asyncio.run(client(handler).update(12, LinkUpdate(name="Mine", expected={"name": "Old"})))
    assert error.value.status_code == 409


@pytest.mark.parametrize("status", [401, 403, 500])
def test_upstream_errors_are_sanitized(status):
    with pytest.raises(LinkwardenError) as error:
        asyncio.run(client(lambda r: httpx.Response(status, text="test-token internal details")).links())
    assert "test-token" not in str(error.value)
    assert error.value.status_code == (503 if status == 500 else status)


def test_repeating_cursor_fails_instead_of_looping_forever():
    with pytest.raises(LinkwardenError):
        asyncio.run(client(lambda r: httpx.Response(200, json={"response": [LINK]})).links())


@pytest.mark.parametrize("payload", [{"url": "javascript:alert(1)"}, {"name": "x"}, {"iconWeight": "unsafe", "expected": {"iconWeight": "regular"}}, {"color": "url(evil)", "expected": {"color": "red"}}])
def test_invalid_or_unversioned_bookmark_edits_are_rejected(payload):
    with pytest.raises(ValueError):
        LinkUpdate(**payload)


@pytest.mark.parametrize("field,length", [("name", 2049), ("url", 2049), ("description", 2049), ("icon", 51)])
def test_update_respects_upstream_field_length_limits(field, length):
    value = "https://example.org/" + "x" * length if field == "url" else "x" * length
    with pytest.raises(ValueError):
        LinkUpdate(**{field: value, "expected": {field: "before"}})
