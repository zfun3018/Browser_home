"""Local SQLite store for canonical bookmarks and collections."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from .schemas import CollectionCreate, CollectionUpdate, LinkUpdate, safe_url


class BookmarkStoreError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _map_link(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"] or "",
        "url": row["url"] or "",
        "description": row["description"] or "",
        "icon": row["icon"],
        "iconWeight": row["icon_weight"],
        "color": row["color"],
        "iconSize": row["custom_icon_size"],
        "collectionId": row["collection_id"],
        "createdAt": row["created_at"] if "created_at" in row.keys() else None,
    }


class SqliteBookmarkStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS bookmark_collections (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_id INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    icon TEXT,
                    icon_weight TEXT,
                    color TEXT,
                    custom_icon BLOB,
                    custom_icon_type TEXT,
                    custom_icon_size INTEGER NOT NULL DEFAULT 52,
                    collection_id INTEGER NOT NULL REFERENCES bookmark_collections(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS bookmarks_collection_idx ON bookmarks(collection_id, id);
                CREATE INDEX IF NOT EXISTS bookmarks_url_idx ON bookmarks(url, collection_id);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(bookmarks)")}
            if "custom_icon" not in columns:
                db.execute("ALTER TABLE bookmarks ADD COLUMN custom_icon BLOB")
            if "custom_icon_type" not in columns:
                db.execute("ALTER TABLE bookmarks ADD COLUMN custom_icon_type TEXT")
            if "custom_icon_size" not in columns:
                db.execute("ALTER TABLE bookmarks ADD COLUMN custom_icon_size INTEGER NOT NULL DEFAULT 52")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def collections(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT id, name, owner_id FROM bookmark_collections ORDER BY id").fetchall()
        return [{"id": row["id"], "name": row["name"], "ownerId": row["owner_id"]} for row in rows]

    def create_collection(self, values: CollectionCreate) -> dict:
        with self.connection() as db:
            db.execute("INSERT INTO bookmark_collections(name, owner_id) VALUES (?, 1)", (values.name,))
            identity = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
        return {"id": identity, "name": values.name, "ownerId": 1}

    def update_collection(self, identity: int, values: CollectionUpdate) -> dict:
        with self.connection() as db:
            cursor = db.execute("UPDATE bookmark_collections SET name=? WHERE id=?", (values.name, identity))
            if cursor.rowcount == 0:
                raise BookmarkStoreError("分组不存在", 404)
        return {"id": identity, "name": values.name, "ownerId": 1}

    def delete_collection(self, identity: int) -> None:
        with self.connection() as db:
            if db.execute("SELECT 1 FROM bookmark_collections WHERE id=?", (identity,)).fetchone() is None:
                raise BookmarkStoreError("分组不存在", 404)
            if db.execute("SELECT 1 FROM bookmarks WHERE collection_id=? LIMIT 1", (identity,)).fetchone() is not None:
                raise BookmarkStoreError("请先将分组内的网址移动到其他分组", 409)
            db.execute("DELETE FROM bookmark_collections WHERE id=?", (identity,))

    def link_counts(self) -> dict[int, int]:
        with self.connection() as db:
            rows = db.execute("SELECT collection_id, COUNT(*) AS count FROM bookmarks GROUP BY collection_id").fetchall()
        return {int(row["collection_id"]): int(row["count"]) for row in rows}

    def links(self, exclude_collection_ids: set[int] | None = None, include_collection_ids: set[int] | None = None) -> list[dict]:
        with self.connection() as db:
            query = "SELECT id, name, url, description, icon, icon_weight, color, custom_icon_size, collection_id, created_at FROM bookmarks"
            params: tuple[int, ...] = ()
            clauses: list[str] = []
            if exclude_collection_ids:
                placeholders = ",".join("?" for _ in exclude_collection_ids)
                clauses.append(f"collection_id NOT IN ({placeholders})")
                params += tuple(sorted(exclude_collection_ids))
            if include_collection_ids:
                placeholders = ",".join("?" for _ in include_collection_ids)
                clauses.append(f"collection_id IN ({placeholders})")
                params += tuple(sorted(include_collection_ids))
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            rows = db.execute(query + " ORDER BY id", params).fetchall()
        return [_map_link(row) for row in rows]

    def get(self, identity: int) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT id, name, url, description, icon, icon_weight, color, custom_icon_size, collection_id, created_at "
                "FROM bookmarks WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise BookmarkStoreError("Bookmark not found", 404)
        return _map_link(row)

    def delete(self, identity: int) -> None:
        with self.connection() as db:
            cursor = db.execute("DELETE FROM bookmarks WHERE id=?", (identity,))
            if cursor.rowcount == 0:
                raise BookmarkStoreError("Bookmark not found", 404)

    def custom_icon(self, identity: int) -> tuple[bytes, str] | None:
        with self.connection() as db:
            row = db.execute("SELECT custom_icon, custom_icon_type FROM bookmarks WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise BookmarkStoreError("Bookmark not found", 404)
        if not row["custom_icon"] or not row["custom_icon_type"]:
            return None
        return bytes(row["custom_icon"]), row["custom_icon_type"]

    def save_custom_icon(self, identity: int, data: bytes, content_type: str) -> None:
        with self.connection() as db:
            cursor = db.execute("UPDATE bookmarks SET custom_icon=?, custom_icon_type=?, updated_at=? WHERE id=?", (data, content_type, _now(), identity))
            if cursor.rowcount == 0:
                raise BookmarkStoreError("Bookmark not found", 404)

    def save_icon_size(self, identity: int, size: int) -> int:
        with self.connection() as db:
            cursor = db.execute("UPDATE bookmarks SET custom_icon_size=?, updated_at=? WHERE id=?", (size, _now(), identity))
            if cursor.rowcount == 0:
                raise BookmarkStoreError("Bookmark not found", 404)
        return size

    def _ensure_default_collection(self, db: sqlite3.Connection) -> int:
        row = db.execute("SELECT id FROM bookmark_collections ORDER BY id LIMIT 1").fetchone()
        if row:
            return row[0]
        db.execute("INSERT INTO bookmark_collections(name, owner_id) VALUES (?, 1)", ("未分类",))
        return int(db.execute("SELECT last_insert_rowid()").fetchone()[0])

    def _collection_exists(self, db: sqlite3.Connection, identity: int) -> bool:
        return db.execute("SELECT 1 FROM bookmark_collections WHERE id=?", (identity,)).fetchone() is not None

    def capture(self, url: str, name: str, description: str = "", collection_id: int | None = None) -> dict:
        try:
            safe_url(url)
        except ValueError as error:
            raise BookmarkStoreError(str(error)) from error
        title = (name or "").strip() or urlsplit(url).hostname or url
        now = _now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            target = collection_id if collection_id is not None else self._ensure_default_collection(db)
            if not self._collection_exists(db, target):
                raise BookmarkStoreError("Target collection does not exist", 404)
            existing = db.execute(
                "SELECT id, name, url, description, icon, icon_weight, color, custom_icon_size, collection_id, created_at "
                "FROM bookmarks WHERE url=? AND collection_id=? ORDER BY id LIMIT 1", (url, target)
            ).fetchone()
            if existing:
                return _map_link(existing)
            db.execute(
                "INSERT INTO bookmarks(name, url, description, collection_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (title, url, description or "", target, now, now)
            )
            identity = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
            row = db.execute(
                "SELECT id, name, url, description, icon, icon_weight, color, custom_icon_size, collection_id, created_at "
                "FROM bookmarks WHERE id=?", (identity,)
            ).fetchone()
        return _map_link(row)

    def update(self, identity: int, patch: LinkUpdate) -> dict:
        changes = patch.model_dump(exclude_unset=True, exclude={"expected"})
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id, name, url, description, icon, icon_weight, color, custom_icon_size, collection_id, created_at "
                "FROM bookmarks WHERE id=?", (identity,)
            ).fetchone()
            if row is None:
                raise BookmarkStoreError("Bookmark not found", 404)
            current = _map_link(row)
            for field, expected in patch.expected.items():
                if field in changes and current.get(field) != expected:
                    raise BookmarkStoreError("Bookmark changed; refresh before retrying", 409)
            if "collectionId" in changes and not self._collection_exists(db, changes["collectionId"]):
                raise BookmarkStoreError("Target collection does not exist", 404)
            column_names = {"name": "name", "url": "url", "description": "description", "icon": "icon", "iconWeight": "icon_weight", "color": "color", "collectionId": "collection_id"}
            assignments = [f"{column_names[key]}=?" for key in changes]
            values = [changes[key] for key in changes]
            if assignments:
                assignments.append("updated_at=?")
                values.append(_now())
                values.append(identity)
                db.execute(f"UPDATE bookmarks SET {', '.join(assignments)} WHERE id=?", values)
            updated = db.execute(
                "SELECT id, name, url, description, icon, icon_weight, color, custom_icon_size, collection_id, created_at "
                "FROM bookmarks WHERE id=?", (identity,)
            ).fetchone()
        return _map_link(updated)

    def import_collections(self, rows: list[dict]) -> None:
        with self.connection() as db:
            for row in rows:
                db.execute(
                    "INSERT INTO bookmark_collections(id, name, owner_id) VALUES (?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, owner_id=excluded.owner_id",
                    (int(row["id"]), row.get("name") or "未分类", int(row.get("ownerId", 1))),
                )

    def import_links(self, rows: list[dict]) -> None:
        now = _now()
        with self.connection() as db:
            for row in rows:
                collection_id = int(row.get("collectionId") or 0)
                if not collection_id or not self._collection_exists(db, collection_id):
                    collection_id = self._ensure_default_collection(db)
                safe_url(row["url"])
                db.execute(
                    "INSERT INTO bookmarks(id, name, url, description, icon, icon_weight, color, collection_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, description=excluded.description, "
                    "icon=excluded.icon, icon_weight=excluded.icon_weight, color=excluded.color, collection_id=excluded.collection_id, updated_at=excluded.updated_at",
                    (int(row["id"]), row.get("name") or row["url"], row["url"], row.get("description") or "", row.get("icon"), row.get("iconWeight"), row.get("color"), collection_id, row.get("createdAt") or now, now),
                )
