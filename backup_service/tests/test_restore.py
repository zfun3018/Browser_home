import sqlite3
from pathlib import Path

import pytest

from backup_service.restore import RestoreError, RestoreManager, RestorePaths


class FakePostgres:
    def __init__(self):
        self.live = "old"
        self.staged = set()
        self.previous = set()

    def prepare(self, dump, stage):
        assert dump.read_bytes() == b"dump"
        self.staged.add(stage)

    def swap(self, stage, previous, journal, save):
        self.previous.add(previous)
        self.live = "new"
        self.staged.remove(stage)
        journal["database"] = "new_renamed"
        save(journal)

    def enable(self):
        return None

    def rollback(self, stage, previous):
        if previous in self.previous:
            self.live = "old"
            self.previous.remove(previous)
        self.staged.discard(stage)

    def finish(self, stage, previous):
        self.staged.discard(stage)
        self.previous.discard(previous)


def setup_restore(tmp_path: Path):
    staging = tmp_path / "staging"
    data = tmp_path / "linkwarden-data"
    state = tmp_path / "homepage-state"
    note = tmp_path / "obsidian.md"
    (data / "nested").mkdir(parents=True)
    (data / "old.bin").write_bytes(b"old")
    (data / "nested" / "keep.bin").write_bytes(b"keep")
    (state / "note-conflicts").mkdir(parents=True)
    (state / "note-conflicts" / "old.md").write_text("old conflict")
    db = sqlite3.connect(state / "state.db")
    with db:
        db.execute("CREATE TABLE marker(value TEXT)")
        db.execute("INSERT INTO marker VALUES ('old')")
    db.close()
    note.write_text("old note", encoding="utf-8")

    extracted = tmp_path / "extracted"
    (extracted / "linkwarden" / "data").mkdir(parents=True)
    (extracted / "linkwarden" / "database.dump").write_bytes(b"dump")
    (extracted / "linkwarden" / "data" / "new.bin").write_bytes(b"new")
    (extracted / "homepage" / "note-conflicts").mkdir(parents=True)
    (extracted / "homepage" / "note-conflicts" / "new.md").write_text("new conflict")
    db = sqlite3.connect(extracted / "homepage" / "state.db")
    with db:
        db.execute("CREATE TABLE marker(value TEXT)")
        db.execute("INSERT INTO marker VALUES ('new')")
    db.close()
    (extracted / "homepage" / "浏览器首页笔记.md").write_text("new note", encoding="utf-8")
    paths = RestorePaths(staging, data, state, note, "postgres", {})
    fake_pg = FakePostgres()
    return RestoreManager(paths, fake_pg), fake_pg, extracted, data, state, note


def state_value(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return db.execute("SELECT value FROM marker").fetchone()[0]


def test_restore_replaces_full_dataset_and_removes_old_copy(tmp_path):
    manager, postgres, extracted, data, state, note = setup_restore(tmp_path)

    manager.commit("11111111-1111-4111-8111-111111111111", extracted)

    assert postgres.live == "new"
    assert (data / "new.bin").read_bytes() == b"new"
    assert not (data / "old.bin").exists()
    assert not (data / "nested").exists()
    assert state_value(state / "state.db") == "new"
    assert (state / "note-conflicts" / "new.md").read_text() == "new conflict"
    assert note.read_text(encoding="utf-8") == "new note"
    assert not manager.journal_path.exists()
    assert not postgres.previous


def test_restore_rolls_back_database_and_files_when_note_swap_fails(tmp_path, monkeypatch):
    manager, postgres, extracted, data, state, note = setup_restore(tmp_path)
    original_replace_note = manager._replace_note

    def fail_after_note_write(new, old, journal):
        original_replace_note(new, old, journal)
        raise OSError("simulated failure after note replacement")

    monkeypatch.setattr(manager, "_replace_note", fail_after_note_write)

    with pytest.raises(RestoreError, match="previous data was restored"):
        manager.commit("22222222-2222-4222-8222-222222222222", extracted)

    assert postgres.live == "old"
    assert (data / "old.bin").read_bytes() == b"old"
    assert (data / "nested" / "keep.bin").read_bytes() == b"keep"
    assert not (data / "new.bin").exists()
    assert state_value(state / "state.db") == "old"
    assert (state / "note-conflicts" / "old.md").read_text() == "old conflict"
    assert note.read_text(encoding="utf-8") == "old note"
    assert not manager.journal_path.exists()


def test_recovery_restores_original_items_if_crash_interrupts_attachment_move(tmp_path):
    manager, postgres, _, data, _, _ = setup_restore(tmp_path)
    identity = "33333333-3333-4333-8333-333333333333"
    paths = manager._paths_for(identity)
    old = paths["data_old"]
    new = paths["data_new"]
    old.mkdir()
    (data / "old.bin").replace(old / "old.bin")
    new.mkdir()
    (new / "new.bin").write_bytes(b"new")
    journal = {"jobId": identity, "database": "prepared", "data": "started", "state": "untouched", "conflicts": "untouched", "note": "untouched", "dataNames": ["new.bin"]}
    manager._save(journal)

    manager.recover()

    assert (data / "old.bin").read_bytes() == b"old"
    assert (data / "nested" / "keep.bin").read_bytes() == b"keep"
    assert not (data / "new.bin").exists()
    assert not manager.journal_path.exists()
