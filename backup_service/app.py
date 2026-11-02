"""Internal-only HTTP service for full local homepage backups."""

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import os
import shutil
import threading
import time
import uuid
import zipfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from .archive import ArchiveError, ArchiveSources, MAX_ARCHIVE_BYTES, build_archive, validate_archive
from .restore import RestoreError, RestoreManager, RestorePaths


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceSettings:
    staging_dir: Path
    sources: ArchiveSources
    output_dir: Path = Path("/backup-output")
    automatic_backup_enabled: bool = True
    automatic_backup_interval_seconds: int = 7 * 24 * 60 * 60
    scheduler_poll_seconds: int = 60 * 60

    @classmethod
    def from_env(cls):
        root = Path(os.environ.get("NATIVE_PROJECT_ROOT", Path.cwd()))
        data_dir = Path(os.environ.get("HOMEPAGE_DATA_DIR", str(root / "homepage-data")))
        note = Path(os.environ.get("OBSIDIAN_NOTE_PATH", str(root / "浏览器首页笔记.md")))
        config = Path(os.environ.get("NATIVE_CONFIG_PATH", str(root / "native" / "native-config.json")))
        return cls(
            staging_dir=Path(os.environ.get("BACKUP_STAGING_DIR", str(root / "homepage-backup-staging"))),
            sources=ArchiveSources(homepage_data=data_dir, obsidian_note=note, native_config=config),
            output_dir=Path(os.environ.get("BACKUP_OUTPUT_DIR", str(root / "backups"))),
            automatic_backup_enabled=os.environ.get("AUTO_BACKUP_ENABLED", "true").lower() not in {"0", "false", "no"},
            automatic_backup_interval_seconds=int(os.environ.get("AUTO_BACKUP_INTERVAL_SECONDS", str(7 * 24 * 60 * 60))),
            scheduler_poll_seconds=int(os.environ.get("AUTO_BACKUP_POLL_SECONDS", str(60 * 60))),
        )


class BackupManager:
    def __init__(self, settings: ServiceSettings):
        self.settings = settings
        self.settings.staging_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        cutoff = time.time() - 24 * 60 * 60
        for directory in self.settings.staging_dir.glob("backup-*"):
            if directory.is_dir() and directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory, ignore_errors=True)

    def latest(self) -> dict | None:
        archives = [
            item for item in self.settings.output_dir.glob("browser-homepage-backup-*.zip")
            if item.is_file() and not item.is_symlink() and self._is_native_archive(item)
        ]
        if not archives:
            return None
        archive = max(archives, key=lambda item: item.stat().st_mtime)
        timestamp = datetime.fromtimestamp(archive.stat().st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return {"filename": archive.name, "lastBackupAt": timestamp}

    @staticmethod
    def _is_native_archive(path: Path) -> bool:
        """Only count versioned SQLite archives; ignore legacy exports."""
        try:
            with zipfile.ZipFile(path) as archive:
                with archive.open("manifest.json") as manifest_file:
                    manifest = json.load(manifest_file)
            return (
                isinstance(manifest, dict)
                and manifest.get("formatVersion") == 2
                and manifest.get("storageBackend") == "sqlite"
            )
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            return False

    def is_due(self, now: float | None = None) -> bool:
        latest = self.latest()
        if latest is None:
            return True
        latest_path = self.settings.output_dir / latest["filename"]
        current = time.time() if now is None else now
        return current - latest_path.stat().st_mtime >= self.settings.automatic_backup_interval_seconds

    def save_archive(self, archive: Path, prefix: str) -> Path:
        identity = str(uuid.uuid4())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = self.settings.output_dir / f"{prefix}-{stamp}-{identity[:8]}.zip"
        temporary = target.with_suffix(".partial")
        try:
            self.settings.output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive, temporary)
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ArchiveError("无法将备份写入本机备份目录") from exc
        return target

    def create(self) -> dict:
        with self._lock:
            identity = str(uuid.uuid4())
            directory = self.settings.staging_dir / f"backup-{identity}"
            try:
                archive = build_archive(directory, self.settings.sources)
                saved = self.save_archive(archive, "browser-homepage-backup")
            except ArchiveError:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            except Exception as exc:
                shutil.rmtree(directory, ignore_errors=True)
                raise ArchiveError("本机备份创建失败") from exc
            shutil.rmtree(directory, ignore_errors=True)
            latest = self.latest()
            return {"id": identity, "filename": saved.name, "lastBackupAt": latest["lastBackupAt"] if latest else None}


class ImportManager:
    def __init__(self, settings: ServiceSettings, restore: RestoreManager | None = None):
        self.settings = settings
        self.settings.staging_dir.mkdir(parents=True, exist_ok=True)
        self.backups = BackupManager(settings)
        self.restore = restore or RestoreManager(RestorePaths(
            staging_dir=settings.staging_dir,
            homepage_data=settings.sources.homepage_data,
            obsidian_note=settings.sources.obsidian_note,
            native_config=settings.sources.native_config,
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
            manifest = await asyncio.to_thread(validate_archive, uploaded, extracted)
            rollback = await asyncio.to_thread(build_archive, directory / "rollback", self.settings.sources)
            rollback_manifest = self._read(directory / "rollback" / "payload" / "manifest.json")
            saved_rollback = await asyncio.to_thread(self.backups.save_archive, rollback, "browser-homepage-rollback")
            job = {
                "id": identity,
                "status": "ready_to_commit",
                "manifest": {"createdAt": manifest["createdAt"], "applicationVersion": manifest.get("applicationVersion")},
                "lastBackupAt": rollback_manifest["createdAt"],
                "rollbackDelivered": True,
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "error": None,
            }
            self._write(directory / "job.json", job)
            return {"id": identity, "manifest": job["manifest"], "rollbackFilename": saved_rollback.name}
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

    async def run_scheduler():
        while True:
            try:
                if await asyncio.to_thread(manager.is_due):
                    await asyncio.to_thread(manager.create)
            except Exception:
                _logger.warning("Scheduled local backup failed")
            await asyncio.sleep(max(60, settings.scheduler_poll_seconds))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.automatic_backup_enabled:
            app.state.backup_scheduler = asyncio.create_task(run_scheduler())
        try:
            yield
        finally:
            task = getattr(app.state, "backup_scheduler", None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Internal Backup Service", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.manager = manager
    app.state.imports = imports

    @app.get("/internal/health")
    def health():
        return {"status": "ok"}

    @app.post("/internal/backups")
    def create_backup():
        try:
            return app.state.manager.create()
        except ArchiveError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/internal/backups/status")
    def backup_status():
        latest = app.state.manager.latest()
        return latest or {"filename": None, "lastBackupAt": None}

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
