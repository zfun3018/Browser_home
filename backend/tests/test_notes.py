from hashlib import sha256
import pytest

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.notes import NoteConflict, NoteStore, NoteUnavailable
from backend.app.state_store import StateStore


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def test_get_reads_only_configured_file_and_returns_content_hash(tmp_path):
    mounted = tmp_path / "mounted-note.md"
    outside = tmp_path / "other.md"
    mounted.write_text("# 你好\n- 记录", encoding="utf-8", newline="")
    outside.write_text("outside", encoding="utf-8")
    notes = NoteStore(mounted, tmp_path / "conflicts")

    assert notes.get() == {"content": "# 你好\n- 记录", "hash": digest("# 你好\n- 记录"), "status": "ok"}
    assert outside.read_text(encoding="utf-8") == "outside"


def test_save_requires_current_hash_and_writes_content(tmp_path):
    mounted = tmp_path / "note.md"
    mounted.write_text("old", encoding="utf-8", newline="")
    notes = NoteStore(mounted, tmp_path / "conflicts")

    saved = notes.save("new\n内容", digest("old"))

    assert saved == {"content": "new\n内容", "hash": digest("new\n内容"), "status": "saved"}
    assert mounted.read_text(encoding="utf-8") == "new\n内容"
    assert list((tmp_path / "conflicts").glob("backup-*.md"))


def test_save_preserves_existing_crlf_line_endings(tmp_path):
    mounted = tmp_path / "note.md"
    mounted.write_bytes("旧行\r\n".encode("utf-8"))
    notes = NoteStore(mounted, tmp_path / "conflicts")

    result = notes.save("新行\n再一行", digest("旧行\r\n"))

    assert mounted.read_bytes() == "新行\r\n再一行".encode("utf-8")
    assert result["content"] == "新行\r\n再一行"
    assert result["hash"] == digest("新行\r\n再一行")


def test_stale_save_preserves_disk_version_and_saves_local_conflict_copy(tmp_path):
    mounted = tmp_path / "note.md"
    mounted.write_text("base", encoding="utf-8", newline="")
    notes = NoteStore(mounted, tmp_path / "conflicts")
    mounted.write_text("Obsidian edit", encoding="utf-8", newline="")

    with pytest.raises(NoteConflict) as captured:
        notes.save("homepage draft", digest("base"))

    assert captured.value.content == "Obsidian edit"
    assert captured.value.hash == digest("Obsidian edit")
    assert mounted.read_text(encoding="utf-8") == "Obsidian edit"
    conflicts = list((tmp_path / "conflicts").glob("conflict-*.md"))
    assert len(conflicts) == 2
    assert {path.read_text(encoding="utf-8") for path in conflicts} == {"Obsidian edit", "homepage draft"}


def test_save_rechecks_file_after_initial_hash_check_before_truncating(tmp_path, monkeypatch):
    mounted = tmp_path / "note.md"
    mounted.write_text("base", encoding="utf-8", newline="")
    notes = NoteStore(mounted, tmp_path / "conflicts")
    read = notes._read
    calls = 0

    def read_then_external_edit():
        nonlocal calls
        value = read()
        calls += 1
        if calls == 1:
            mounted.write_text("Obsidian changed during save", encoding="utf-8", newline="")
        return value

    monkeypatch.setattr(notes, "_read", read_then_external_edit)
    with pytest.raises(NoteConflict) as captured:
        notes.save("homepage draft", digest("base"))

    external = "Obsidian changed during save"
    assert captured.value.content == external
    assert mounted.read_text(encoding="utf-8") == external
    conflicts = list((tmp_path / "conflicts").glob("conflict-*.md"))
    assert {path.read_text(encoding="utf-8") for path in conflicts} == {external, "homepage draft"}
    assert not list((tmp_path / "conflicts").glob("backup-*.md"))


def test_save_preserves_local_draft_if_mounted_file_disappears_after_first_read(tmp_path, monkeypatch):
    mounted = tmp_path / "note.md"
    mounted.write_text("base", encoding="utf-8", newline="")
    notes = NoteStore(mounted, tmp_path / "conflicts")
    read = notes._read

    def read_then_remove():
        value = read()
        mounted.unlink()
        return value

    monkeypatch.setattr(notes, "_read", read_then_remove)
    with pytest.raises(NoteUnavailable):
        notes.save("homepage draft", digest("base"))

    assert not mounted.exists()
    preserved = list((tmp_path / "conflicts").glob("conflict-homepage-*.md"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "homepage draft"


def test_event_detects_external_edits_and_reports_unavailable_recovery(tmp_path):
    mounted = tmp_path / "note.md"
    mounted.write_text("before", encoding="utf-8", newline="")
    notes = NoteStore(mounted, tmp_path / "conflicts")
    since = digest("before")
    mounted.write_text("after", encoding="utf-8", newline="")

    assert notes.events(since) == {"changed": True, "content": "after", "hash": digest("after"), "status": "ok"}
    mounted.unlink()
    with pytest.raises(NoteUnavailable):
        notes.get()
    mounted.write_text("recovered", encoding="utf-8", newline="")
    assert notes.events(digest("after"))["content"] == "recovered"


def test_api_uses_injected_fixed_note_store_and_protects_mutations(tmp_path):
    mounted = tmp_path / "mounted.md"
    mounted.write_text("initial", encoding="utf-8")
    notes = NoteStore(mounted, tmp_path / "conflicts")
    app = create_app(Settings(data_dir=tmp_path), store=StateStore(tmp_path / "state.db"), note_store=notes)
    headers = {"origin": "http://localhost:3011"}

    with TestClient(app, base_url="http://localhost:3011") as client:
        assert client.get("/api/notes").json() == {"content": "initial", "hash": digest("initial"), "status": "ok"}
        assert client.put("/api/notes", json={"content": "saved", "expectedHash": digest("initial")}, headers=headers).status_code == 200
        assert mounted.read_text(encoding="utf-8") == "saved"
        assert client.put("/api/notes", json={"path": str(tmp_path / "other.md"), "content": "escape", "expectedHash": digest("saved")}, headers=headers).status_code == 422
        assert client.put("/api/notes", json={"content": "cross site", "expectedHash": digest("saved")}, headers={"origin": "http://evil.example"}).status_code == 403
