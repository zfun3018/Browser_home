import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from backup_service.app import ImportManager, ServiceSettings
from backup_service.archive import ArchiveSources, build_archive


class FakeRestore:
    def __init__(self):
        self.commits = []

    def recover(self):
        return None

    def commit(self, identity, extracted):
        self.commits.append((identity, extracted))


def make_settings(root: Path, restore: FakeRestore) -> ServiceSettings:
    data = root / "data"
    data.mkdir()
    (data / "attachment.bin").write_bytes(b"attachment")
    state = root / "state"
    (state / "note-conflicts").mkdir(parents=True)
    with sqlite3.connect(state / "state.db") as db:
        db.execute("CREATE TABLE settings(value TEXT)")
        db.execute("INSERT INTO settings VALUES ('current')")
    note = root / "note.md"
    note.write_text("current note", encoding="utf-8")
    compose = root / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env = root / ".env"
    env.write_text("POSTGRES_PASSWORD=secret\n", encoding="utf-8")
    token = root / "token"
    token.write_text("token\n", encoding="utf-8")
    return ServiceSettings(root / "staging", ArchiveSources(data, state, note, compose, env, token, "fake-pg-dump"), {}, "fake-pg-restore")


def fake_pg(monkeypatch):
    def run(args, **kwargs):
        if args[0] == "fake-pg-dump":
            Path(args[args.index("--file") + 1]).write_bytes(b"dump")
        return SimpleNamespace(returncode=0, stderr=b"", stdout=b"dump listing")
    monkeypatch.setattr("backup_service.archive.subprocess.run", run)


def archive_bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_import_requires_a_delivered_rollback_before_idempotent_commit(tmp_path, monkeypatch):
    fake_pg(monkeypatch)
    restore = FakeRestore()
    settings = make_settings(tmp_path, restore)
    imported = build_archive(tmp_path / "source", settings.sources, {})
    manager = ImportManager(settings, restore)

    async def chunks():
        content = archive_bytes(imported)
        yield content[:100]
        yield content[100:]

    result = asyncio.run(manager.prepare(chunks(), "previous.zip"))
    identity = result["id"]
    assert manager.status(identity)["status"] == "ready_for_rollback"
    with pytest.raises(Exception, match="必须先完成当前数据回滚备份"):
        manager.commit(identity)
    assert manager.rollback_archive(identity).is_file()
    assert manager.mark_rollback_delivered(identity) == {"readyToCommit": True}

    committed = manager.commit(identity)
    repeated = manager.commit(identity)

    assert committed["status"] == "complete"
    assert committed == repeated
    assert len(restore.commits) == 1
    assert manager.status(identity)["status"] == "complete"


def test_invalid_import_is_removed_without_changing_live_sources(tmp_path):
    restore = FakeRestore()
    settings = make_settings(tmp_path, restore)
    manager = ImportManager(settings, restore)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zipped:
        zipped.writestr("../escape", "bad")

    async def chunks():
        yield bad.read_bytes()

    with pytest.raises(Exception, match="不安全路径"):
        asyncio.run(manager.prepare(chunks(), "bad.zip"))

    with sqlite3.connect(settings.sources.homepage_state / "state.db") as db:
        assert db.execute("SELECT value FROM settings").fetchone()[0] == "current"
    assert (settings.sources.obsidian_note).read_text(encoding="utf-8") == "current note"
