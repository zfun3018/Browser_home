from pathlib import Path
from fastapi.testclient import TestClient

from backup_service.app import BackupManager, ServiceSettings, create_app
from backup_service.archive import ArchiveSources


def settings(tmp_path: Path) -> ServiceSettings:
    empty = tmp_path / "empty"
    empty.mkdir()
    return ServiceSettings(
        staging_dir=tmp_path / "staging",
        sources=ArchiveSources(empty, empty, empty / "note", empty / "compose", empty / "env", empty / "token"),
        pg_env={},
    )


def test_health_endpoint_is_internal_service_ready_check(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))

    assert client.get("/internal/health").json() == {"status": "ok"}


def test_download_removes_only_its_completed_job(tmp_path, monkeypatch):
    service_settings = settings(tmp_path)

    def fake_build(directory, sources, pg_env):
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / "backup.zip"
        archive.write_bytes(b"zip")
        return archive

    monkeypatch.setattr("backup_service.app.build_archive", fake_build)
    client = TestClient(create_app(service_settings, BackupManager(service_settings)))

    created = client.post("/internal/backups").json()
    job_dir = service_settings.staging_dir / f"backup-{created['id']}"
    response = client.get(f"/internal/backups/{created['id']}/download")

    assert response.content == b"zip"
    assert not job_dir.exists()


def test_download_rejects_invalid_or_unknown_job_id(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))

    assert client.get("/internal/backups/not-a-uuid/download").status_code == 404
