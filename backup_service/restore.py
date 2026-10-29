"""Validated staged restore with a durable rollback journal."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import uuid


class RestoreError(Exception):
    pass


@dataclass(frozen=True)
class RestorePaths:
    staging_dir: Path
    linkwarden_data: Path
    homepage_state: Path
    obsidian_note: Path
    database: str
    pg_env: dict[str, str]


class PostgresRestorer:
    def __init__(self, env: dict[str, str], database: str, runner=subprocess.run):
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise RestoreError("Configured database name is unsupported")
        self.env = env
        self.database = database
        self.runner = runner

    def _run(self, args: list[str], database: str = "template1", sql: str | None = None):
        env = {**os.environ, **self.env, "PGDATABASE": database}
        command = args
        if sql is not None:
            command = ["psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--tuples-only", "--no-align", "--command", sql]
        result = self.runner(command, env=env, input=None, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RestoreError("PostgreSQL restore operation failed")
        return result.stdout.strip()

    def exists(self, database: str) -> bool:
        value = self._run([], sql=f"SELECT 1 FROM pg_database WHERE datname = '{database}'")
        return value == "1"

    def prepare(self, dump: Path, stage: str):
        if self.exists(stage):
            self._run(["dropdb", "--if-exists", stage])
        self._run(["createdb", "--template=template0", stage])
        self._run(["pg_restore", "--exit-on-error", "--no-owner", "--dbname", stage, str(dump)])
        count = self._run([], database=stage, sql="SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
        if not count.isdigit() or int(count) == 0:
            raise RestoreError("Restored database is missing application tables")

    def swap(self, stage: str, previous: str, journal: dict, save):
        self._run([], sql=f"ALTER DATABASE {self.database} WITH ALLOW_CONNECTIONS false")
        self._run([], sql=f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{self.database}' AND pid <> pg_backend_pid()")
        self._run([], sql=f"ALTER DATABASE {self.database} RENAME TO {previous}")
        journal["database"] = "previous_renamed"
        save(journal)
        self._run([], sql=f"ALTER DATABASE {stage} WITH ALLOW_CONNECTIONS false")
        self._run([], sql=f"ALTER DATABASE {stage} RENAME TO {self.database}")
        journal["database"] = "new_renamed"
        save(journal)

    def enable(self):
        self._run([], sql=f"ALTER DATABASE {self.database} WITH ALLOW_CONNECTIONS true")

    def rollback(self, stage: str, previous: str):
        has_live = self.exists(self.database)
        has_previous = self.exists(previous)
        if has_previous:
            if has_live:
                self._run([], sql=f"ALTER DATABASE {self.database} WITH ALLOW_CONNECTIONS false")
                self._run([], sql=f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{self.database}' AND pid <> pg_backend_pid()")
                failed = f"{stage}_failed"
                if self.exists(failed):
                    self._run(["dropdb", "--if-exists", failed])
                self._run([], sql=f"ALTER DATABASE {self.database} RENAME TO {failed}")
                self._run([], sql=f"ALTER DATABASE {previous} RENAME TO {self.database}")
                self._run([], sql=f"ALTER DATABASE {self.database} WITH ALLOW_CONNECTIONS true")
                self._run(["dropdb", "--if-exists", failed])
            else:
                self._run([], sql=f"ALTER DATABASE {previous} RENAME TO {self.database}")
                self._run([], sql=f"ALTER DATABASE {self.database} WITH ALLOW_CONNECTIONS true")
        if self.exists(stage):
            self._run(["dropdb", "--if-exists", stage])
        if not has_previous and has_live:
            self._run([], sql=f"ALTER DATABASE {self.database} WITH ALLOW_CONNECTIONS true")

    def finish(self, stage: str, previous: str):
        if self.exists(previous):
            self._run(["dropdb", "--if-exists", previous])
        if self.exists(stage):
            self._run(["dropdb", "--if-exists", stage])


class RestoreManager:
    def __init__(self, paths: RestorePaths, postgres: PostgresRestorer | None = None):
        self.paths = paths
        self.postgres = postgres or PostgresRestorer(paths.pg_env, paths.database)
        self.journal_path = paths.staging_dir / "restore-journal.json"

    def _save(self, journal: dict):
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(journal), encoding="utf-8")
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
        stage_db = f"hp_restore_{suffix}"
        previous_db = f"hp_previous_{suffix}"
        data_new = self.paths.linkwarden_data / f".restore-new-{suffix}"
        data_old = self.paths.linkwarden_data / f".restore-old-{suffix}"
        state_new = self.paths.homepage_state / f".restore-new-state-{suffix}.db"
        state_old = self.paths.homepage_state / f".restore-old-state-{suffix}.db"
        conflicts_new = self.paths.homepage_state / f".restore-new-conflicts-{suffix}"
        conflicts_old = self.paths.homepage_state / f".restore-old-conflicts-{suffix}"
        note_old = self.paths.staging_dir / f"import-{job_id}" / "previous-note.md"
        note_new = self.paths.staging_dir / f"import-{job_id}" / "imported-note.md"
        return locals()

    def commit(self, job_id: str, extracted: Path) -> None:
        paths = self._paths_for(job_id)
        suffix = paths["suffix"]
        payload_data = extracted / "linkwarden" / "data"
        payload_state = extracted / "homepage" / "state.db"
        payload_conflicts = extracted / "homepage" / "note-conflicts"
        payload_note = extracted / "homepage" / "浏览器首页笔记.md"
        stage_db = paths["stage_db"]
        previous_db = paths["previous_db"]
        stage_root = self.paths.staging_dir / f"import-{job_id}"
        stage_root.mkdir(parents=True, exist_ok=True)

        for target in (paths["data_new"], paths["data_old"], paths["state_new"], paths["state_old"], paths["conflicts_new"], paths["conflicts_old"]):
            if target.exists():
                raise RestoreError("Restore staging path already exists")
        if not self.paths.obsidian_note.is_file():
            raise RestoreError("Current Obsidian note is unavailable")

        try:
            self.postgres.prepare(extracted / "linkwarden" / "database.dump", stage_db)
            shutil.copytree(payload_data, paths["data_new"])
            shutil.copy2(payload_state, paths["state_new"])
            shutil.copytree(payload_conflicts, paths["conflicts_new"])
            shutil.copy2(payload_note, paths["note_new"])
            shutil.copy2(self.paths.obsidian_note, paths["note_old"])
            db = sqlite3.connect(paths["state_new"])
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RestoreError("Imported homepage database is damaged")
            finally:
                db.close()

            journal = {
                "jobId": job_id,
                "database": "prepared",
                "data": "untouched",
                "state": "untouched",
                "conflicts": "untouched",
                "note": "untouched",
                "dataNames": [item.name for item in paths["data_new"].iterdir()],
                "paths": {key: str(value) for key, value in paths.items() if key.endswith(("_new", "_old"))},
            }
            self._save(journal)
            self.postgres.swap(stage_db, previous_db, journal, self._save)
            self._replace_data(paths["data_new"], paths["data_old"], paths["data_new"], journal)
            self._replace_file(paths["state_new"], paths["state_old"], paths["state_new"], journal, "state")
            self._replace_directory(paths["conflicts_new"], paths["conflicts_old"], paths["conflicts_new"], journal, "conflicts")
            self._replace_note(paths["note_new"], paths["note_old"], journal)
            self.postgres.enable()
            self._save({**journal, "phase": "committed"})
            self.postgres.finish(stage_db, previous_db)
            self._cleanup(paths)
            self.journal_path.unlink(missing_ok=True)
        except Exception as exc:
            try:
                self.recover()
                if not self.journal_path.exists():
                    self.postgres.finish(stage_db, previous_db)
                    self._cleanup(paths)
            except Exception as rollback_error:
                raise RestoreError("Import failed and automatic rollback needs operator attention") from rollback_error
            if isinstance(exc, RestoreError):
                raise
            raise RestoreError("Import failed; the previous data was restored") from exc

    def _replace_data(self, new: Path, old: Path, staged: Path, journal: dict):
        live = self.paths.linkwarden_data
        old.mkdir()
        journal["data"] = "started"
        self._save(journal)
        for item in list(live.iterdir()):
            if item in (new, old):
                continue
            os.replace(item, old / item.name)
        for item in list(staged.iterdir()):
            os.replace(item, live / item.name)
        new.rmdir()
        journal["data"] = "replaced"
        self._save(journal)

    def _replace_file(self, new: Path, old: Path, staged: Path, journal: dict, key: str):
        journal[key] = "started"
        self._save(journal)
        live = self.paths.homepage_state / "state.db"
        os.replace(live, old)
        os.replace(staged, live)
        journal[key] = "replaced"
        self._save(journal)

    def _replace_directory(self, new: Path, old: Path, staged: Path, journal: dict, key: str):
        journal[key] = "started"
        self._save(journal)
        if old.exists():
            raise RestoreError("Previous conflicts directory already exists")
        live = self.paths.homepage_state / "note-conflicts"
        if live.exists():
            os.replace(live, old)
        os.replace(staged, live)
        journal[key] = "replaced"
        self._save(journal)

    def _replace_note(self, new: Path, old: Path, journal: dict):
        journal["note"] = "started"
        self._save(journal)
        with self.paths.obsidian_note.open("wb") as target, new.open("rb") as source:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        journal["note"] = "replaced"
        self._save(journal)

    def recover(self) -> dict | None:
        if not self.journal_path.is_file():
            return None
        journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        paths = self._paths_for(journal["jobId"])
        if journal.get("phase") != "committed":
            if journal.get("note") != "untouched" and paths["note_old"].is_file():
                shutil.copy2(paths["note_old"], self.paths.obsidian_note)
            if journal.get("conflicts") != "untouched" and paths["conflicts_old"].exists():
                live = self.paths.homepage_state / "note-conflicts"
                shutil.rmtree(live, ignore_errors=True)
                os.replace(paths["conflicts_old"], live)
            elif journal.get("conflicts") != "untouched":
                shutil.rmtree(self.paths.homepage_state / "note-conflicts", ignore_errors=True)
            if journal.get("state") != "untouched" and paths["state_old"].is_file():
                live = self.paths.homepage_state / "state.db"
                live.unlink(missing_ok=True)
                os.replace(paths["state_old"], live)
            if journal.get("data") != "untouched" and paths["data_old"].exists():
                self._rollback_data(paths["data_old"], paths["data_new"], journal.get("dataNames", []))
            self.postgres.rollback(paths["stage_db"], paths["previous_db"])
            outcome = "rolled_back"
        else:
            self.postgres.finish(paths["stage_db"], paths["previous_db"])
            outcome = "complete"
        self._cleanup(paths)
        self.journal_path.unlink(missing_ok=True)
        return {"jobId": journal["jobId"], "outcome": outcome}

    def _rollback_data(self, old: Path, new: Path, new_names: list[str]):
        live = self.paths.linkwarden_data
        remaining = {item.name for item in new.iterdir()} if new.exists() else set()
        moved_new = set(new_names) - remaining
        for name in moved_new:
            item = live / name
            if item.exists():
                shutil.rmtree(item) if item.is_dir() else item.unlink()
        if old.exists():
            for item in list(old.iterdir()):
                target = live / item.name
                if target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
                os.replace(item, live / item.name)

    def _cleanup(self, paths: dict):
        for key in ("data_new", "data_old", "state_new", "state_old", "conflicts_new", "conflicts_old", "note_new", "note_old"):
            item = paths[key]
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
