"""SQLite stores presentation and search only, never canonical bookmarks."""
from contextlib import contextmanager
import base64
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3

from .schemas import CollectionLayout, LinkLayout, LinkOrder, SearchSettings


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RuntimeError("Unsupported preferences schema version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS link_layout (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS collection_layout (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS search_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            """)
            if version < 2:
                db.execute("CREATE TABLE IF NOT EXISTS backup_metadata (id INTEGER PRIMARY KEY CHECK(id=1), last_backup_at TEXT)")
            if version < 3:
                db.execute("PRAGMA user_version=3")

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
            links = {str(row[0]): json.loads(row[1]) for row in db.execute("SELECT id,value FROM link_layout")}
            collections = {str(row[0]): self._public_collection_layout(json.loads(row[1])) for row in db.execute("SELECT id,value FROM collection_layout")}
        return {"links": links, "collections": collections}

    @staticmethod
    def _public_collection_layout(value: dict) -> dict:
        return {
            "collapsed": bool(value.get("collapsed", False)),
            "order": int(value.get("order", 0)),
            "displayMode": value.get("displayMode", "grid") if value.get("displayMode", "grid") in ("grid", "list") else "grid",
            "locked": bool(value.get("passwordHash") and value.get("passwordSalt")),
        }

    def _patch(self, table, identity, update, defaults):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT value FROM {table} WHERE id=?", (identity,)).fetchone()
            value = {**defaults, **(json.loads(row[0]) if row else {}), **update.model_dump(exclude_none=True)}
            db.execute(f"INSERT INTO {table}(id,value) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (identity, json.dumps(value)))
            return value

    def patch_link(self, identity: int, update: LinkLayout):
        return self._patch("link_layout", identity, update, {"showName": True, "showIcon": True, "showDescription": True, "order": identity})

    def patch_link_orders(self, orders: list[LinkOrder]):
        defaults = {"showName": True, "showIcon": True, "showDescription": True}
        saved = {}
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for entry in orders:
                row = db.execute("SELECT value FROM link_layout WHERE id=?", (entry.id,)).fetchone()
                value = {**defaults, "order": entry.id, **(json.loads(row[0]) if row else {}), "order": entry.order}
                db.execute("INSERT INTO link_layout(id,value) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (entry.id, json.dumps(value)))
                saved[str(entry.id)] = value
        return saved

    def patch_collection(self, identity: int, update: CollectionLayout):
        value = self._patch("collection_layout", identity, update, {"collapsed": False, "order": identity, "displayMode": "grid"})
        return self._public_collection_layout(value)

    def collection_is_locked(self, identity: int) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT value FROM collection_layout WHERE id=?", (identity,)).fetchone()
        if not row:
            return False
        value = json.loads(row[0])
        return bool(value.get("passwordHash") and value.get("passwordSalt"))

    def set_collection_password(self, identity: int, password: str) -> dict:
        salt = secrets.token_bytes(16)
        iterations = 120_000
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM collection_layout WHERE id=?", (identity,)).fetchone()
            value = {"collapsed": False, "order": identity, "displayMode": "grid", **(json.loads(row[0]) if row else {})}
            value.update({
                "passwordVersion": 1,
                "passwordIterations": iterations,
                "passwordSalt": base64.b64encode(salt).decode("ascii"),
                "passwordHash": base64.b64encode(digest).decode("ascii"),
            })
            db.execute("INSERT INTO collection_layout(id,value) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (identity, json.dumps(value)))
        return self._public_collection_layout(value)

    def clear_collection_password(self, identity: int) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM collection_layout WHERE id=?", (identity,)).fetchone()
            value = {"collapsed": False, "order": identity, "displayMode": "grid", **(json.loads(row[0]) if row else {})}
            for key in ("passwordVersion", "passwordIterations", "passwordSalt", "passwordHash"):
                value.pop(key, None)
            db.execute("INSERT INTO collection_layout(id,value) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (identity, json.dumps(value)))
        return self._public_collection_layout(value)

    def verify_collection_password(self, identity: int, password: str) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT value FROM collection_layout WHERE id=?", (identity,)).fetchone()
        if not row:
            return False
        value = json.loads(row[0])
        try:
            salt = base64.b64decode(value["passwordSalt"], validate=True)
            expected = base64.b64decode(value["passwordHash"], validate=True)
            iterations = int(value.get("passwordIterations", 120_000))
        except (KeyError, ValueError, TypeError):
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(actual, expected)

    def delete_collection(self, identity: int) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM collection_layout WHERE id=?", (identity,))

    def delete_link(self, identity: int) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM link_layout WHERE id=?", (identity,))

    def search(self):
        with self.connection() as db:
            row = db.execute("SELECT value FROM search_settings WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"providers": [{"id": "google", "name": "Google", "url": "https://www.google.com/search?q={query}"}, {"id": "bing", "name": "Bing", "url": "https://www.bing.com/search?q={query}"}], "activeProviderId": "google"}

    def save_search(self, settings: SearchSettings):
        value = settings.model_dump()
        with self.connection() as db:
            db.execute("INSERT INTO search_settings(id,value) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(value),))
        return value

    def last_backup_at(self) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT last_backup_at FROM backup_metadata WHERE id=1").fetchone()
        return row[0] if row else None

    def record_backup(self, timestamp: str) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO backup_metadata(id,last_backup_at) VALUES (1,?) "
                "ON CONFLICT(id) DO UPDATE SET last_backup_at=excluded.last_backup_at",
                (timestamp,),
            )
