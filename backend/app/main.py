from pathlib import Path as FilePath
from datetime import datetime, timezone
import asyncio
import os
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Path, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .backups import BackupCoordinator, BackupServiceError
from .config import Settings
from .linkwarden_client import LinkwardenClient, LinkwardenError
from .notes import NoteConflict, NoteStore, NoteUnavailable
from .schemas import CollectionLayout, LinkLayout, LinkUpdate, SearchSettings
from .state_store import StateStore


class NoteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=5_000_000)
    expectedHash: str = Field(pattern=r"^[a-f0-9]{64}$")


def create_app(settings: Settings | None = None, linkwarden=None, store=None, note_store=None, backups=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="Personal Homepage", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.store = store or StateStore(settings.data_dir / "state.db")
    app.state.linkwarden = linkwarden or LinkwardenClient(settings.linkwarden_url, settings.read_token())
    app.state.notes = note_store or NoteStore(FilePath("/app/data/浏览器首页笔记.md"), settings.data_dir / "note-conflicts")
    app.state.backups = backups or BackupCoordinator(settings.backup_service_url)
    app.state.import_committing = False
    app.state.import_lock = asyncio.Lock()

    def reject_during_import():
        if app.state.import_committing:
            raise HTTPException(status_code=503, detail="import_in_progress")

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            host = request.headers.get("host")
            if (origin not in settings.allowed_origins or host != urlsplit(origin or "").netloc
                    or request.url.scheme != urlsplit(origin or "").scheme
                    or request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(LinkwardenError)
    async def upstream_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status_code)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/backup/status")
    def backup_status():
        return {"lastBackupAt": app.state.store.last_backup_at()}

    @app.post("/api/backup")
    async def create_backup():
        try:
            result = await app.state.backups.create_backup()
            identity = BackupCoordinator.validate_job_id(result["id"])
        except (BackupServiceError, KeyError, ValueError) as error:
            raise HTTPException(status_code=502, detail=str(error) if isinstance(error, BackupServiceError) else "备份服务返回无效任务") from error
        filename = f"browser-homepage-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"
        return {"id": identity, "filename": filename}

    @app.get("/api/backup/{identity}/download")
    async def download_backup(identity: str = Path()):
        try:
            stream = await app.state.backups.backup_download(identity)
        except BackupServiceError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        async def complete_stream():
            async for chunk in stream:
                yield chunk
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            app.state.store.record_backup(timestamp)

        filename = f"browser-homepage-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"
        return StreamingResponse(complete_stream(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/import/prepare")
    async def prepare_import(request: Request):
        filename = request.headers.get("x-backup-filename", "")
        if not filename.lower().endswith(".zip") or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=400, detail="请选择 ZIP 备份文件")
        try:
            return await app.state.backups.prepare_import(request.stream(), filename)
        except BackupServiceError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/import/{identity}/status")
    async def import_status(identity: str):
        try:
            return await app.state.backups.import_status(identity)
        except BackupServiceError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/import/{identity}/rollback")
    async def download_import_rollback(identity: str):
        try:
            stream = await app.state.backups.rollback_download(identity)
        except BackupServiceError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        async def complete_rollback_stream():
            async for chunk in stream:
                yield chunk
            await app.state.backups.mark_rollback_delivered(identity)

        filename = f"browser-homepage-rollback-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"
        return StreamingResponse(complete_rollback_stream(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/import/{identity}/commit")
    async def commit_import(identity: str):
        async with app.state.import_lock:
            try:
                status = await app.state.backups.import_status(identity)
                if status.get("status") != "ready_to_commit" or not status.get("rollbackDelivered"):
                    raise HTTPException(status_code=409, detail="rollback_backup_required")
                app.state.import_committing = True
                result = await app.state.backups.commit_import(identity)
                timestamp = result.get("lastBackupAt") or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                app.state.store.record_backup(timestamp)
                return {"status": "complete"}
            except HTTPException:
                raise
            except BackupServiceError as error:
                raise HTTPException(status_code=502, detail=str(error)) from error
            finally:
                app.state.import_committing = False

    @app.post("/api/import/{identity}/cancel")
    async def cancel_import(identity: str):
        try:
            return await app.state.backups.cancel_import(identity)
        except BackupServiceError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/notes")
    def get_note():
        try:
            return app.state.notes.get()
        except NoteUnavailable:
            return {"content": "", "hash": "", "status": "unavailable"}

    @app.put("/api/notes")
    def save_note(update: NoteWrite):
        reject_during_import()
        try:
            return app.state.notes.save(update.content, update.expectedHash)
        except NoteConflict as conflict:
            return JSONResponse({"detail": "conflict", "content": conflict.content, "hash": conflict.hash}, status_code=409)
        except NoteUnavailable:
            return JSONResponse({"detail": "unavailable"}, status_code=503)

    @app.get("/api/notes/events")
    def note_events(since: str = ""):
        if since and len(since) != 64:
            return JSONResponse({"detail": "Invalid content hash"}, status_code=422)
        return app.state.notes.events(since)

    @app.get("/api/home")
    async def home():
        try:
            collections = await app.state.linkwarden.collections()
            links = await app.state.linkwarden.links()
            sync = {"status": "ok"}
        except LinkwardenError as error:
            collections, links = [], []
            sync = {"status": "error", "message": str(error)}
        return {"collections": collections, "links": links, "preferences": app.state.store.preferences(), "sync": sync}

    @app.patch("/api/links/{identity}")
    async def update_link(update: LinkUpdate, identity: int = Path(gt=0)):
        reject_during_import()
        return await app.state.linkwarden.update(identity, update)

    @app.patch("/api/links/{identity}/layout")
    def link_layout(update: LinkLayout, identity: int = Path(gt=0)):
        reject_during_import()
        return app.state.store.patch_link(identity, update)

    @app.patch("/api/collections/{identity}/layout")
    def collection_layout(update: CollectionLayout, identity: int = Path(gt=0)):
        reject_during_import()
        return app.state.store.patch_collection(identity, update)

    @app.get("/api/search-engines")
    def search_engines():
        return app.state.store.search()

    @app.put("/api/search-engines")
    def update_search(settings: SearchSettings):
        reject_during_import()
        return app.state.store.save_search(settings)

    # In the container, serve the production React build from the same origin as
    # the API. Keeping this optional leaves the test/dev API-only app unchanged.
    static_dir = os.environ.get("HOMEPAGE_STATIC_DIR")
    if static_dir and FilePath(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="homepage-static")

    return app


app = create_app()
