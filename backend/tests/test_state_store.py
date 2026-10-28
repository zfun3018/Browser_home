import sqlite3

import pytest
from backend.app.state_store import StateStore
from backend.app.schemas import LinkLayout, CollectionLayout, SearchSettings


def test_preferences_survive_restart_and_patch_retains_other_flags(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.patch_link(12, LinkLayout(showName=False, order=2))
    store.patch_link(12, LinkLayout(showIcon=False))
    store.patch_collection(3, CollectionLayout(collapsed=True))
    prefs = StateStore(path).preferences()
    assert prefs["links"]["12"] == {"showName": False, "showIcon": False, "showDescription": False, "order": 2}
    assert prefs["collections"]["3"]["collapsed"] is True
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_search_templates_persist_and_require_safe_urls_and_selected_provider(tmp_path):
    store = StateStore(tmp_path / "state.db")
    settings = SearchSettings(providers=[{"id": "custom", "name": "Custom", "url": "https://example.org/?q={query}"}], activeProviderId="custom")
    store.save_search(settings)
    assert StateStore(tmp_path / "state.db").search() == settings.model_dump()
    for url in ["javascript:{query}", "https://example.org/", "https://{query}.example.org/"]:
        with pytest.raises(ValueError):
            SearchSettings(providers=[{"id": "x", "name": "X", "url": url}], activeProviderId="x")
    with pytest.raises(ValueError):
        SearchSettings(providers=[], activeProviderId="missing")


def test_layout_cannot_store_bookmark_content():
    with pytest.raises(ValueError):
        LinkLayout(name="canonical content")
