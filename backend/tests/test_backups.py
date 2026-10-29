import sqlite3

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.schemas import LinkLayout, SearchSettings
from backend.app.state_store import StateStore


def test_backup_timestamp_persists_across_restart(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)

    assert store.last_backup_at() is None
    store.record_backup("2026-10-29T02:30:00Z")

    assert StateStore(path).last_backup_at() == "2026-10-29T02:30:00Z"
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_backup_timestamp_migration_preserves_existing_preferences(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.patch_link(12, LinkLayout(showName=False, order=2))
    store.save_search(SearchSettings(
        providers=[{"id": "custom", "name": "Custom", "url": "https://example.org/?q={query}"}],
        activeProviderId="custom",
    ))
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE backup_metadata")
        db.execute("PRAGMA user_version=1")

    migrated = StateStore(path)

    assert migrated.preferences()["links"]["12"]["showName"] is False
    assert migrated.search()["activeProviderId"] == "custom"
    assert migrated.last_backup_at() is None


class FakeBackupCoordinator:
    identity = "11111111-1111-4111-8111-111111111111"
    imported = False

    async def create_backup(self):
        return {"id": self.identity}

    async def backup_download(self, identity):
        assert identity == self.identity
        async def chunks():
            yield b"zip-content"
        return chunks()

    async def prepare_import(self, upload, filename):
        assert filename == "backup.zip"
        content = b"".join([chunk async for chunk in upload])
        assert content == b"zip-content"
        return {"id": self.identity, "manifest": {"createdAt": "2026-10-20T00:00:00Z"}}

    async def import_status(self, identity):
        assert identity == self.identity
        return {"status": "ready_to_commit" if self.imported else "ready_for_rollback", "rollbackDelivered": self.imported}

    async def rollback_download(self, identity):
        async def chunks():
            yield b"rollback"
        return chunks()

    async def mark_rollback_delivered(self, identity):
        self.imported = True
        return {"readyToCommit": True}

    async def commit_import(self, identity):
        return {"status": "complete", "lastBackupAt": "2026-10-29T02:30:00Z"}


def test_backup_download_records_timestamp_only_after_stream_finishes(tmp_path):
    store = StateStore(tmp_path / "state.db")
    app = create_app(Settings(data_dir=tmp_path), store=store, backups=FakeBackupCoordinator())
    headers = {"origin": "http://localhost:3011"}
    with TestClient(app, base_url="http://localhost:3011", headers=headers) as client:
        assert client.get("/api/backup/status").json() == {"lastBackupAt": None}
        created = client.post("/api/backup").json()
        assert created["id"] == FakeBackupCoordinator.identity
        response = client.get(f"/api/backup/{created['id']}/download")

        assert response.status_code == 200
        assert response.content == b"zip-content"
        assert response.headers["content-type"] == "application/zip"
        assert store.last_backup_at() is not None


def test_backup_creation_rejects_invalid_job_id(tmp_path):
    class InvalidCoordinator:
        async def create_backup(self):
            return {"id": "../../state.db"}

    app = create_app(Settings(data_dir=tmp_path), backups=InvalidCoordinator())
    with TestClient(app, base_url="http://localhost:3011", headers={"origin": "http://localhost:3011"}) as client:
        assert client.post("/api/backup").status_code == 502


def test_homepage_mutations_are_paused_during_import(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), store=StateStore(tmp_path / "state.db"), backups=FakeBackupCoordinator())
    app.state.import_committing = True
    with TestClient(app, base_url="http://localhost:3011", headers={"origin": "http://localhost:3011"}) as client:
        response = client.put("/api/search-engines", json={"providers": [{"id": "g", "name": "Google", "url": "https://google.com?q={query}"}], "activeProviderId": "g"})
        assert response.status_code == 503
        assert response.json()["detail"] == "import_in_progress"


def test_import_requires_completed_rollback_download_before_commit(tmp_path):
    store = StateStore(tmp_path / "state.db")
    fake = FakeBackupCoordinator()
    fake.imported = False
    app = create_app(Settings(data_dir=tmp_path), store=store, backups=fake)
    headers = {"origin": "http://localhost:3011"}
    with TestClient(app, base_url="http://localhost:3011", headers=headers) as client:
        prepared = client.post("/api/import/prepare", content=b"zip-content", headers={**headers, "x-backup-filename": "backup.zip"}).json()
        identity = prepared["id"]
        assert client.post(f"/api/import/{identity}/commit").status_code == 409
        rollback = client.get(f"/api/import/{identity}/rollback")
        assert rollback.content == b"rollback"
        assert client.post(f"/api/import/{identity}/commit").json() == {"status": "complete"}
        assert store.last_backup_at() == "2026-10-29T02:30:00Z"


def test_import_filename_cannot_select_a_server_path(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), backups=FakeBackupCoordinator())
    with TestClient(app, base_url="http://localhost:3011", headers={"origin": "http://localhost:3011"}) as client:
        response = client.post("/api/import/prepare", content=b"zip-content", headers={"x-backup-filename": "..\\state.db"})
        assert response.status_code == 400
