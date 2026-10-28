"""SQLite stores presentation and search only, never canonical bookmarks."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .schemas import CollectionLayout, LinkLayout, SearchSettings


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Unsupported preferences schema version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS link_layout (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS collection_layout (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS search_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def preferences(self) -> dict:
        with self.connection() as db:
            return {key: {str(row[0]): json.loads(row[1]) for row in db.execute(f"SELECT id,value FROM {table}")} for key, table in (("links", "link_layout"), ("collections", "collection_layout"))}

    def _patch(self, table, identity, update, defaults):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT value FROM {table} WHERE id=?", (identity,)).fetchone()
            value = {**defaults, **(json.loads(row[0]) if row else {}), **update.model_dump(exclude_none=True)}
            db.execute(f"INSERT INTO {table}(id,value) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (identity, json.dumps(value)))
            return value

    def patch_link(self, identity: int, update: LinkLayout):
        return self._patch("link_layout", identity, update, {"showName": True, "showIcon": True, "showDescription": False, "order": 0})

    def patch_collection(self, identity: int, update: CollectionLayout):
        return self._patch("collection_layout", identity, update, {"collapsed": False, "order": 0})

    def search(self):
        with self.connection() as db:
            row = db.execute("SELECT value FROM search_settings WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"providers": [{"id": "google", "name": "Google", "url": "https://www.google.com/search?q={query}"}, {"id": "bing", "name": "Bing", "url": "https://www.bing.com/search?q={query}"}], "activeProviderId": "google"}

    def save_search(self, settings: SearchSettings):
        value = settings.model_dump()
        with self.connection() as db:
            db.execute("INSERT INTO search_settings(id,value) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(value),))
        return value
