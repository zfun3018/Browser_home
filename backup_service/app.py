"""Internal-only HTTP service for full homepage and Linkwarden backups."""

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import json
from pathlib import Path
import os
import shutil
import tempfile
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .archive import ArchiveError, ArchiveSources, MAX_ARCHIVE_BYTES, build_archive, validate_archive
from .restore import RestoreError, RestoreManager, RestorePaths


@dataclass(frozen=True)
class ServiceSettings:
    staging_dir: Path
    sources: ArchiveSources
    pg_env: dict[str, str]
    pg_restore: str = "pg_restore"

    @classmethod
    def from_env(cls):
        return cls(
            staging_dir=Path(os.environ.get("BACKUP_STAGING_DIR", str(Path(tempfile.gettempdir()) / "homepage-backups"))),
            sources=ArchiveSources(
                linkwarden_data=Path(os.environ.get("LINKWARDEN_DATA_DIR", "/source/linkwarden-data")),
                homepage_state=Path(os.environ.get("HOMEPAGE_STATE_DIR", "/source/homepage-state")),
                obsidian_note=Path(os.environ.get("OBSIDIAN_NOTE_PATH", "/source/obsidian-note.md")),
                compose_file=Path(os.environ.get("COMPOSE_FILE_PATH", "/source/docker-compose.yml")),
                env_file=Path(os.environ.get("ENV_FILE_PATH", "/source/.env")),
                token_file=Path(os.environ.get("TOKEN_FILE_PATH", "/source/linkwarden-token.txt")),
                pg_dump=os.environ.get("PG_DUMP_BIN", "pg_dump"),
            ),
            pg_env={key: os.environ[key] for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD") if key in os.environ},
            pg_restore=os.environ.get("PG_RESTORE_BIN", "pg_restore"),
        )


class BackupManager:
    def __init__(self, settings: ServiceSettings):
        self.settings = settings
        self.settings.staging_dir.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 24 * 60 * 60
        for directory in self.settings.staging_dir.glob("backup-*"):
            if directory.is_dir() and directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory, ignore_errors=True)

    def create(self) -> dict:
        identity = str(uuid.uuid4())
        directory = self.settings.staging_dir / f"backup-{identity}"
        try:
            archive = build_archive(directory, self.settings.sources, self.settings.pg_env)
        except ArchiveError:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return {"id": identity, "path": archive}


class ImportManager:
    def __init__(self, settings: ServiceSettings, restore: RestoreManager | None = None):
        self.settings = settings
        self.settings.staging_dir.mkdir(parents=True, exist_ok=True)
        self.restore = restore or RestoreManager(RestorePaths(
            staging_dir=settings.staging_dir,
            linkwarden_data=settings.sources.linkwarden_data,
            homepage_state=settings.sources.homepage_state,
            obsidian_note=settings.sources.obsidian_note,
            database=settings.pg_env.get("PGDATABASE", "postgres"),
            pg_env=settings.pg_env,
        ))
        recovered = self.restore.recover()
        if recovered:
            job_file = self._directory(recovered["jobId"]) / "job.json"
            if job_file.is_file():
                job = self._read(job_file)
                job["status"] = "complete" if recovered["outcome"] == "complete" else "failed"
                job["error"] = None if job["status"] == "complete" else "导入中断，已恢复导入前的数据"
                self._write(job_file, job)
        self._expire_jobs()

    def _expire_jobs(self):
        cutoff = datetime.now(timezone.utc).timestamp() - 24 * 60 * 60
        for directory in self.settings.staging_dir.glob("import-*"):
            if not directory.is_dir():
                continue
            job_file = directory / "job.json"
            if not job_file.is_file():
                if directory.stat().st_mtime < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
                continue
            try:
                job = self._read(job_file)
                created = datetime.fromisoformat(job["createdAt"].replace("Z", "+00:00")).timestamp()
            except (OSError, ValueError, KeyError):
                created = directory.stat().st_mtime
                job = {}
            if created < cutoff and job.get("status") != "committing":
                shutil.rmtree(directory, ignore_errors=True)

    @staticmethod
    def _identity(identity: str) -> str:
        try:
            return str(uuid.UUID(identity))
        except ValueError as exc:
            raise ArchiveError("导入任务不存在") from exc

    def _directory(self, identity: str) -> Path:
        return self.settings.staging_dir / f"import-{self._identity(identity)}"

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, value: dict):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    async def prepare(self, upload, filename: str) -> dict:
        if not filename.lower().endswith(".zip") or Path(filename).name != filename:
            raise ArchiveError("请选择 ZIP 备份文件")
        identity = str(uuid.uuid4())
        directory = self._directory(identity)
        directory.mkdir(parents=True)
        uploaded = directory / "upload.zip"
        size = 0
        try:
            with uploaded.open("xb") as target:
                async for chunk in upload:
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise ArchiveError("备份压缩包超过允许大小")
                    target.write(chunk)
            extracted = directory / "extracted"
            manifest = await asyncio.to_thread(validate_archive, uploaded, extracted, self.settings.pg_restore)
            rollback = await asyncio.to_thread(build_archive, directory / "rollback", self.settings.sources, self.settings.pg_env)
            rollback_manifest = self._read(directory / "rollback" / "payload" / "manifest.json")
            job = {
                "id": identity,
                "status": "ready_for_rollback",
                "manifest": {"createdAt": manifest["createdAt"], "applicationVersion": manifest.get("applicationVersion")},
                "lastBackupAt": rollback_manifest["createdAt"],
                "rollbackDelivered": False,
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "error": None,
            }
            self._write(directory / "job.json", job)
            return {"id": identity, "manifest": job["manifest"]}
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def status(self, identity: str) -> dict:
        job_file = self._directory(identity) / "job.json"
        if not job_file.is_file():
            raise ArchiveError("导入任务不存在")
        job = self._read(job_file)
        return {"status": job["status"], "manifest": job["manifest"], "rollbackDelivered": job["rollbackDelivered"], "error": job.get("error")}

    def rollback_archive(self, identity: str) -> Path:
        directory = self._directory(identity)
        job = self._read(directory / "job.json")
        archive = directory / "rollback" / "backup.zip"
        if not archive.is_file() or job["status"] not in {"ready_for_rollback", "ready_to_commit"}:
            raise ArchiveError("回滚备份不存在")
        return archive

    def mark_rollback_delivered(self, identity: str) -> dict:
        directory = self._directory(identity)
        job_file = directory / "job.json"
        job = self._read(job_file)
        if job["status"] not in {"ready_for_rollback", "ready_to_commit"}:
            raise ArchiveError("当前导入任务不能继续")
        if not (directory / "rollback" / "backup.zip").is_file():
            raise ArchiveError("回滚备份不存在")
        job["rollbackDelivered"] = True
        job["status"] = "ready_to_commit"
        self._write(job_file, job)
        return {"readyToCommit": True}

    def commit(self, identity: str) -> dict:
        directory = self._directory(identity)
        job_file = directory / "job.json"
        job = self._read(job_file)
        if job["status"] == "complete":
            return {"status": "complete", "lastBackupAt": job["lastBackupAt"]}
        if job["status"] != "ready_to_commit" or not job["rollbackDelivered"]:
            raise ArchiveError("必须先完成当前数据回滚备份")
        job["status"] = "committing"
        self._write(job_file, job)
        try:
            self.restore.commit(identity, directory / "extracted")
        except RestoreError as error:
            job["status"] = "failed"
            job["error"] = str(error)
            self._write(job_file, job)
            raise ArchiveError(str(error)) from error
        job["status"] = "complete"
        job["error"] = None
        self._write(job_file, job)
        return {"status": "complete", "lastBackupAt": job["lastBackupAt"]}

    def cancel(self, identity: str) -> dict:
        directory = self._directory(identity)
        job_file = directory / "job.json"
        if not job_file.is_file():
            raise ArchiveError("导入任务不存在")
        job = self._read(job_file)
        if job["status"] not in {"ready_for_rollback", "ready_to_commit", "failed"}:
            raise ArchiveError("当前导入任务不能取消")
        shutil.rmtree(directory)
        return {"status": "cancelled"}


def create_app(settings: ServiceSettings | None = None, manager: BackupManager | None = None, imports: ImportManager | None = None):
    settings = settings or ServiceSettings.from_env()
    manager = manager or BackupManager(settings)
    imports = imports or ImportManager(settings)
    app = FastAPI(title="Internal Backup Service", docs_url=None, redoc_url=None)
    app.state.manager = manager
    app.state.imports = imports

    @app.get("/internal/health")
    def health():
        return {"status": "ok"}

    @app.post("/internal/backups")
    def create_backup():
        try:
            result = app.state.manager.create()
            return {"id": result["id"]}
        except ArchiveError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/internal/backups/{identity}/download")
    def download_backup(identity: str):
        try:
            identity = str(uuid.UUID(identity))
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Backup job not found") from error
        archive = settings.staging_dir / f"backup-{identity}" / "backup.zip"
        if not archive.is_file():
            raise HTTPException(status_code=404, detail="Backup job not found")
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"browser-homepage-backup-{identity}.zip",
            background=BackgroundTask(shutil.rmtree, archive.parent, ignore_errors=True),
        )

    @app.post("/internal/imports/prepare")
    async def prepare_import(request: Request):
        try:
            return await app.state.imports.prepare(request.stream(), request.headers.get("x-backup-filename", ""))
        except ArchiveError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/internal/imports/{identity}/status")
    def import_status(identity: str):
        try:
            return app.state.imports.status(identity)
        except (ArchiveError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Import job not found") from error

    @app.get("/internal/imports/{identity}/rollback")
    def download_rollback(identity: str):
        try:
            archive = app.state.imports.rollback_archive(identity)
        except ArchiveError as error:
            raise HTTPException(status_code=404, detail="Rollback archive not found") from error
        return FileResponse(archive, media_type="application/zip", filename=f"browser-homepage-rollback-{identity}.zip")

    @app.post("/internal/imports/{identity}/rollback-delivered")
    def mark_rollback_delivered(identity: str):
        try:
            return app.state.imports.mark_rollback_delivered(identity)
        except ArchiveError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/internal/imports/{identity}/commit")
    def commit_import(identity: str):
        try:
            return app.state.imports.commit(identity)
        except ArchiveError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/internal/imports/{identity}/cancel")
    def cancel_import(identity: str):
        try:
            return app.state.imports.cancel(identity)
        except ArchiveError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return app


app = create_app()
