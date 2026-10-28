"""Conflict-aware access to the single mounted Obsidian Markdown file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import threading
import uuid
import os


def content_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


@dataclass
class NoteConflict(Exception):
    content: str
    hash: str


class NoteUnavailable(Exception):
    pass


class NoteStore:
    """Manage one fixed file; callers cannot select or change its path."""

    def __init__(self, note_path: Path, conflict_dir: Path):
        self._note_path = Path(note_path)
        self._conflict_dir = Path(conflict_dir)
        self._lock = threading.RLock()

    def _read(self) -> str:
        try:
            return self._note_path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise NoteUnavailable("Obsidian 笔记暂时不可用") from exc

    def _copy(self, prefix: str, content: str) -> None:
        self._conflict_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self._conflict_dir / f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}.md"
        target.write_text(content, encoding="utf-8", newline="")

    def get(self) -> dict:
        with self._lock:
            content = self._read()
            return {"content": content, "hash": content_hash(content), "status": "ok"}

    def events(self, since: str) -> dict:
        with self._lock:
            try:
                content = self._read()
            except NoteUnavailable:
                return {"changed": False, "status": "unavailable"}
            current = content_hash(content)
            if current == since:
                return {"changed": False, "status": "ok"}
            return {"changed": True, "content": content, "hash": current, "status": "ok"}

    def save(self, content: str, expected_hash: str) -> dict:
        with self._lock:
            current = self._read()
            current_hash = content_hash(current)
            if current_hash != expected_hash:
                # The disk copy remains authoritative; the rejected browser draft is
                # also persisted so a page close cannot discard either version.
                self._copy("conflict", content)
                raise NoteConflict(current, current_hash)
            self._copy("backup", current)
            # Keep a CRLF file in CRLF form when the browser textarea normalizes
            # its value to LF, so ordinary edits do not rewrite the whole vault note.
            if "\r\n" in current and "\n" not in current.replace("\r\n", ""):
                content = content.replace("\r\n", "\n").replace("\n", "\r\n")
            try:
                # Write in place because host editors and Docker Desktop file binds
                # do not consistently reflect host-side atomic file replacement.
                with self._note_path.open("wb") as target:
                    target.write(content.encode("utf-8"))
                    target.flush()
                    os.fsync(target.fileno())
            except OSError as exc:
                raise NoteUnavailable("无法写入 Obsidian 笔记") from exc
            return {"content": content, "hash": content_hash(content), "status": "saved"}
