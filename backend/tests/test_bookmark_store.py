from pathlib import Path

import pytest

from backend.app.bookmark_store import SqliteBookmarkStore, BookmarkStoreError
from backend.app.schemas import CollectionCreate, CollectionUpdate, LinkUpdate


def test_capture_creates_default_collection_and_returns_link(tmp_path: Path):
    store = SqliteBookmarkStore(tmp_path / "bookmarks.db")

    result = store.capture("https://example.com/a", "Example", "", None)

    assert result["id"] == 1
    assert result["name"] == "Example"
    assert result["collectionId"] == 1
    assert store.collections() == [{"id": 1, "name": "未分类", "ownerId": 1}]


def test_capture_same_url_in_same_collection_is_idempotent(tmp_path: Path):
    store = SqliteBookmarkStore(tmp_path / "bookmarks.db")

    first = store.capture("https://example.com", "Example", "", None)
    second = store.capture("https://example.com", "Changed", "Description", None)

    assert second["id"] == first["id"]
    assert store.links() == [first]


def test_update_requires_expected_values_and_checks_collection(tmp_path: Path):
    store = SqliteBookmarkStore(tmp_path / "bookmarks.db")
    link = store.capture("https://example.com", "Example", "", None)
    store.import_collections([{"id": 9, "name": "Work", "ownerId": 7}])

    updated = store.update(link["id"], LinkUpdate(name="Updated", expected={"name": "Example"}))
    assert updated["name"] == "Updated"

    with pytest.raises(BookmarkStoreError, match="changed"):
        store.update(link["id"], LinkUpdate(name="Again", expected={"name": "Example"}))

    moved = store.update(link["id"], LinkUpdate(collectionId=9, expected={"collectionId": 1}))
    assert moved["collectionId"] == 9


def test_import_preserves_link_and_collection_ids(tmp_path: Path):
    store = SqliteBookmarkStore(tmp_path / "bookmarks.db")

    store.import_collections([{"id": 37, "name": "Work", "ownerId": 12}])
    store.import_links([{
        "id": 91,
        "name": "Imported",
        "url": "https://example.org/imported",
        "description": "Saved",
        "icon": "book-open",
        "iconWeight": "regular",
        "color": "#123456",
        "collectionId": 37,
    }])

    assert store.collections() == [{"id": 37, "name": "Work", "ownerId": 12}]
    assert store.links()[0]["id"] == 91


def test_collection_management_preserves_data_and_blocks_non_empty_delete(tmp_path: Path):
    store = SqliteBookmarkStore(tmp_path / "bookmarks.db")
    created = store.create_collection(CollectionCreate(name="  Work  "))
    assert created["name"] == "Work"
    renamed = store.update_collection(created["id"], CollectionUpdate(name="Projects"))
    assert renamed["name"] == "Projects"
    store.capture("https://example.org", "Example", collection_id=created["id"])
    with pytest.raises(BookmarkStoreError, match="移动"):
        store.delete_collection(created["id"])


def test_empty_collection_can_be_deleted(tmp_path: Path):
    store = SqliteBookmarkStore(tmp_path / "bookmarks.db")
    created = store.create_collection(CollectionCreate(name="Temporary"))
    store.delete_collection(created["id"])
    assert all(item["id"] != created["id"] for item in store.collections())


def test_delete_removes_bookmark_and_rejects_missing_identity(tmp_path: Path):
    store = SqliteBookmarkStore(tmp_path / "bookmarks.db")
    link = store.capture("https://example.org/remove", "Remove me")
    store.save_custom_icon(link["id"], b"icon", "image/png")

    store.delete(link["id"])

    assert store.links() == []
    with pytest.raises(BookmarkStoreError, match="Bookmark not found"):
        store.custom_icon(link["id"])
    with pytest.raises(BookmarkStoreError, match="Bookmark not found"):
        store.delete(link["id"])
