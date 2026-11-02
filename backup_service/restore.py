"""Validated staged restore for the local SQLite homepage runtime."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid


class RestoreError(Exception):
    pass


@dataclass(frozen=True)
class RestorePaths:
    staging_dir: Path
    homepage_data: Path
    obsidian_note: Path
    native_config: Path


class RestoreManager:
    def __init__(self, paths: RestorePaths):
        self.paths = paths
        self.journal_path = paths.staging_dir / "restore-journal.json"

    def _save(self, journal: dict):
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(journal, ensure_ascii=False), encoding="utf-8")
        with temporary.open("r+b") as file:
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self.journal_path)

    def _paths_for(self, job_id: str):
        try:
            job_id = str(uuid.UUID(job_id))
        except ValueError as exc:
            raise RestoreError("Import task identifier is invalid") from exc
        suffix = job_id.replace("-", "")[:12]
        stage_root = self.paths.staging_dir / f"import-{job_id}"
        return {
            "job_id": job_id,
            "suffix": suffix,
            "data_new": self.paths.staging_dir / f".restore-new-data-{suffix}",
            "data_old": self.paths.staging_dir / f".restore-old-data-{suffix}",
            "note_old": stage_root / "previous-note.md",
            "note_new": stage_root / "imported-note.md",
            "config_old": stage_root / "previous-native-config.json",
            "config_new": stage_root / "imported-native-config.json",
        }

    @staticmethod
    def _check_sqlite(path: Path, label: str) -> None:
        db = sqlite3.connect(path)
        try:
            result = db.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error as exc:
            raise RestoreError(f"{label} database could not be opened") from exc
        finally:
            db.close()
        if not result or result[0] != "ok":
            raise RestoreError(f"{label} database is damaged")

    def commit(self, job_id: str, extracted: Path) -> None:
        paths = self._paths_for(job_id)
        extracted = Path(extracted)
        payload_data = extracted / "homepage"
        payload_note = payload_data / "浏览器首页笔记.md"
        payload_config = extracted / "deployment" / "native-config.json"
        stage_root = self.paths.staging_dir / f"import-{paths['job_id']}"
        stage_root.mkdir(parents=True, exist_ok=True)
        if any(Path(paths[key]).exists() for key in ("data_new", "data_old", "note_old", "note_new", "config_old", "config_new")):
            raise RestoreError("Restore staging path already exists")
        if not payload_data.is_dir() or not (payload_data / "bookmarks.db").is_file() or not (payload_data / "state.db").is_file():
            raise RestoreError("Imported homepage data is incomplete")
        if not payload_note.is_file() or not payload_config.is_file() or not self.paths.obsidian_note.is_file():
            raise RestoreError("Homepage note or native configuration is unavailable")
        try:
            self._check_sqlite(payload_data / "bookmarks.db", "Bookmarks")
            self._check_sqlite(payload_data / "state.db", "Homepage state")
            shutil.copytree(payload_data, paths["data_new"], symlinks=False)
            shutil.copy2(payload_note, paths["note_new"])
            shutil.copy2(payload_config, paths["config_new"])
            journal = {
                "jobId": paths["job_id"], "data": "prepared", "note": "prepared", "config": "prepared",
                "dataNames": [item.name for item in paths["data_new"].iterdir()],
            }
            self._save(journal)
            self._replace_data(paths, journal)
            self._replace_file(paths["note_new"], self.paths.obsidian_note, paths["note_old"], journal, "note")
            self._replace_file(paths["config_new"], self.paths.native_config, paths["config_old"], journal, "config")
            journal["phase"] = "committed"
            self._save(journal)
            self._cleanup(paths)
            self.journal_path.unlink(missing_ok=True)
        except Exception as exc:
            try:
                self.recover()
            except Exception as rollback_error:
                raise RestoreError("Import failed and automatic rollback needs operator attention") from rollback_error
            if isinstance(exc, RestoreError):
                raise
            raise RestoreError("Import failed; the previous data was restored") from exc

    def _replace_data(self, paths: dict, journal: dict):
        live = self.paths.homepage_data
        live.mkdir(parents=True, exist_ok=True)
        paths["data_old"].mkdir(parents=True, exist_ok=True)
        journal["data"] = "started"
        self._save(journal)
        for item in list(live.iterdir()):
            if item not in (paths["data_new"], paths["data_old"]):
                os.replace(item, paths["data_old"] / item.name)
        for item in list(paths["data_new"].iterdir()):
            os.replace(item, live / item.name)
        paths["data_new"].rmdir()
        journal["data"] = "replaced"
        self._save(journal)

    def _replace_file(self, new: Path, live: Path, old: Path, journal: dict, key: str):
        journal[key] = "started"
        self._save(journal)
        old.parent.mkdir(parents=True, exist_ok=True)
        os.replace(live, old)
        os.replace(new, live)
        journal[key] = "replaced"
        self._save(journal)

    def recover(self) -> dict | None:
        if not self.journal_path.is_file():
            return None
        journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        paths = self._paths_for(journal["jobId"])
        try:
            if journal.get("phase") != "committed":
                if journal.get("config") not in (None, "prepared", "untouched") and paths["config_old"].is_file():
                    self.paths.native_config.unlink(missing_ok=True)
                    os.replace(paths["config_old"], self.paths.native_config)
                if journal.get("note") not in (None, "prepared", "untouched") and paths["note_old"].is_file():
                    self.paths.obsidian_note.unlink(missing_ok=True)
                    os.replace(paths["note_old"], self.paths.obsidian_note)
                if journal.get("data") not in (None, "prepared", "untouched"):
                    self._rollback_data(paths)
                outcome = "rolled_back"
            else:
                outcome = "complete"
            self._cleanup(paths)
            self.journal_path.unlink(missing_ok=True)
            return {"jobId": journal["jobId"], "outcome": outcome}
        except OSError as exc:
            raise RestoreError("无法恢复导入前的数据") from exc

    def _rollback_data(self, paths: dict):
        live = self.paths.homepage_data
        for item in list(live.iterdir()) if live.exists() else []:
            if item.name not in {paths["data_old"].name, paths["data_new"].name}:
                shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
        if paths["data_old"].exists():
            live.mkdir(parents=True, exist_ok=True)
            for item in list(paths["data_old"].iterdir()):
                os.replace(item, live / item.name)

    def _cleanup(self, paths: dict):
        for key in ("data_new", "data_old", "note_new", "note_old", "config_new", "config_old"):
            item = paths[key]
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
