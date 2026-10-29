import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from backup_service.archive import ArchiveError, ArchiveSources, build_archive, validate_archive


def make_sources(root: Path) -> ArchiveSources:
    data = root / "linkwarden-data"
    data.mkdir()
    (data / "upload.bin").write_bytes(b"attachment")
    state = root / "homepage-state"
    (state / "note-conflicts").mkdir(parents=True)
    with sqlite3.connect(state / "state.db") as db:
        db.execute("CREATE TABLE setting(value TEXT)")
        db.execute("INSERT INTO setting VALUES ('saved')")
    note = root / "note.md"
    note.write_text("# note\n", encoding="utf-8")
    compose = root / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = root / ".env"
    env_file.write_text("POSTGRES_PASSWORD=test-password\n", encoding="utf-8")
    token = root / "token.txt"
    token.write_text("test-token\n", encoding="utf-8")
    return ArchiveSources(data, state, note, compose, env_file, token, "fake-pg-dump")


def fake_pg_tools(monkeypatch):
    def run(args, **kwargs):
        if args[0] == "fake-pg-dump":
            Path(args[args.index("--file") + 1]).write_bytes(b"dump")
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr("backup_service.archive.subprocess.run", run)


def test_archive_has_documented_files_hashes_and_online_sqlite_copy(tmp_path, monkeypatch):
    fake_pg_tools(monkeypatch)
    sources = make_sources(tmp_path)
    archive = build_archive(tmp_path / "job", sources, {"PGDATABASE": "postgres"})

    with zipfile.ZipFile(archive) as zipped:
        names = set(zipped.namelist())
        manifest = json.loads(zipped.read("manifest.json"))
        assert "linkwarden/database.dump" in names
        assert "linkwarden/data/upload.bin" in names
        assert "homepage/state.db" in names
        assert "homepage/浏览器首页笔记.md" in names
        assert "deployment/.env" in names
        assert "deployment/homepage-secrets/linkwarden_api_token.txt" in names
        assert "linkwarden/data/" in names
        assert "homepage/note-conflicts/" in names
        assert {entry["path"] for entry in manifest["files"]} == names - {"manifest.json", "RESTORE.txt", "linkwarden/data/", "homepage/note-conflicts/"}
        copied_state = tmp_path / "copied-state.db"
        copied_state.write_bytes(zipped.read("homepage/state.db"))
    with sqlite3.connect(copied_state) as db:
        assert db.execute("SELECT value FROM setting").fetchone()[0] == "saved"


def test_archive_validator_extracts_valid_backup_and_checks_dump(tmp_path, monkeypatch):
    fake_pg_tools(monkeypatch)
    archive = build_archive(tmp_path / "job", make_sources(tmp_path), {})
    destination = tmp_path / "extract"

    manifest = validate_archive(archive, destination, pg_restore="fake-pg-restore")

    assert manifest["formatVersion"] == 1
    assert (destination / "linkwarden" / "data" / "upload.bin").read_bytes() == b"attachment"
    with sqlite3.connect(destination / "homepage" / "state.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_archive_validator_rejects_traversal_without_writing_outside_extract(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escape.txt", "bad")
    destination = tmp_path / "extract"

    with pytest.raises(ArchiveError, match="不安全路径"):
        validate_archive(archive, destination)

    assert not (tmp_path / "escape.txt").exists()
    assert not destination.exists()


def test_archive_builder_fails_closed_when_a_required_file_is_missing(tmp_path, monkeypatch):
    fake_pg_tools(monkeypatch)
    sources = make_sources(tmp_path)
    sources.token_file.unlink()

    with pytest.raises(ArchiveError, match="Required backup file is missing"):
        build_archive(tmp_path / "job", sources, {})
