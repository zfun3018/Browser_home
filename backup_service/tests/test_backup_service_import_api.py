from pathlib import Path
import uuid

from fastapi.testclient import TestClient

from backup_service.app import ServiceSettings, create_app
from backup_service.archive import ArchiveSources
from backup_service.archive import ArchiveError


class FakeBackupManager:
    pass


class FakeImportManager:
    def __init__(self, root: Path):
        self.root = root
        self.delivered = False

    async def prepare(self, upload, filename):
        assert filename == "backup.zip"
        assert b"".join([chunk async for chunk in upload]) == b"archive"
        return {"id": "11111111-1111-4111-8111-111111111111", "manifest": {"createdAt": "2026-10-29T00:00:00Z"}}

    def status(self, identity):
        try:
            uuid.UUID(identity)
        except ValueError as error:
            raise ArchiveError("Import job not found") from error
        return {"status": "ready_to_commit" if self.delivered else "ready_for_rollback", "rollbackDelivered": self.delivered}

    def rollback_archive(self, identity):
        return self.root / "rollback.zip"

    def mark_rollback_delivered(self, identity):
        self.delivered = True
        return {"readyToCommit": True}

    def commit(self, identity):
        assert self.delivered
        return {"status": "complete", "lastBackupAt": "2026-10-29T00:00:00Z"}


def service_settings(root: Path) -> ServiceSettings:
    empty = root / "empty"
    empty.mkdir()
    return ServiceSettings(root / "staging", ArchiveSources(empty, empty, empty / "note", empty / "compose", empty / "env", empty / "token"), {})


def test_internal_import_api_requires_rollback_download_before_commit(tmp_path):
    rollback = tmp_path / "rollback.zip"
    rollback.write_bytes(b"current")
    imports = FakeImportManager(tmp_path)
    client = TestClient(create_app(service_settings(tmp_path), FakeBackupManager(), imports))

    prepared = client.post("/internal/imports/prepare", content=b"archive", headers={"x-backup-filename": "backup.zip"}).json()
    identity = prepared["id"]
    assert client.get(f"/internal/imports/{identity}/status").json()["rollbackDelivered"] is False
    assert client.get(f"/internal/imports/{identity}/rollback").content == b"current"
    assert client.post(f"/internal/imports/{identity}/rollback-delivered").json() == {"readyToCommit": True}
    assert client.get(f"/internal/imports/{identity}/status").json()["status"] == "ready_to_commit"
    assert client.post(f"/internal/imports/{identity}/commit").json()["status"] == "complete"


def test_internal_import_api_hides_unknown_jobs(tmp_path):
    imports = FakeImportManager(tmp_path)
    client = TestClient(create_app(service_settings(tmp_path), FakeBackupManager(), imports))

    assert client.get("/internal/imports/not-a-job/status").status_code == 404
