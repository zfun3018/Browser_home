from pathlib import Path as FilePath
from datetime import datetime, timezone
import asyncio
import os
import inspect
import secrets
import time
from collections import Counter
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Path, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .backups import BackupCoordinator, BackupServiceError
from .bookmark_store import BookmarkStoreError, SqliteBookmarkStore
from .config import Settings
from .metadata import MetadataFetchError, fetch_public_metadata
from .notes import NoteConflict, NoteStore, NoteUnavailable
from .schemas import CaptureRequest, CollectionCreate, CollectionLayout, CollectionPassword, CollectionUnlock, CollectionUpdate, LinkLayout, LinkOrdersUpdate, LinkUpdate, SearchSettings
from .site_assets import MAX_CUSTOM_ICON_BYTES, NEGATIVE_CACHE_TTL_SECONDS, SiteAssetService, SiteIconNotFound, validate_icon_payload
from .state_store import StateStore


class NoteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=5_000_000)
    expectedHash: str = Field(pattern=r"^[a-f0-9]{64}$")


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


def create_app(settings: Settings | None = None, bookmarks=None, store=None, note_store=None, backups=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="Personal Homepage", docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.state.settings = settings
    app.state.store = store or StateStore(settings.data_dir / "state.db")
    app.state.bookmarks = bookmarks or SqliteBookmarkStore(settings.bookmark_db_path)
    app.state.notes = note_store or NoteStore(settings.obsidian_note_path, settings.data_dir / "note-conflicts")
    app.state.backups = backups or BackupCoordinator(settings.backup_service_url)
    app.state.site_assets = SiteAssetService(settings.data_dir / "site-icons")
    app.state.import_committing = False
    app.state.import_lock = asyncio.Lock()
    app.state.collection_unlocks: dict[str, tuple[int, float]] = {}

    def reject_during_import():
        if app.state.import_committing:
            raise HTTPException(status_code=503, detail="import_in_progress")

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.url.path == "/api/capture":
            return await call_next(request)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            host = request.headers.get("host")
            if (origin not in settings.allowed_origins or host != urlsplit(origin or "").netloc
                    or request.url.scheme != urlsplit(origin or "").scheme
                    or request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if "cache-control" not in response.headers:
            if request.url.path.startswith("/assets/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif request.url.path == "/" or request.url.path.endswith(".html"):
                response.headers["Cache-Control"] = "no-cache"
            else:
                response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(BookmarkStoreError)
    async def bookmark_store_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status_code)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/backup/status")
    async def backup_status():
        try:
            status = await app.state.backups.backup_status()
            timestamp = status.get("lastBackupAt") if "lastBackupAt" in status else app.state.store.last_backup_at()
            if timestamp and timestamp != app.state.store.last_backup_at():
                app.state.store.record_backup(timestamp)
            return {"lastBackupAt": timestamp, "filename": status.get("filename")}
        except BackupServiceError:
            return {"lastBackupAt": app.state.store.last_backup_at(), "filename": None}

    @app.post("/api/backup")
    async def create_backup():
        try:
            result = await app.state.backups.create_backup()
            identity = BackupCoordinator.validate_job_id(result["id"])
        except (BackupServiceError, KeyError, ValueError) as error:
            raise HTTPException(status_code=502, detail=str(error) if isinstance(error, BackupServiceError) else "备份服务返回无效任务") from error
        timestamp = result.get("lastBackupAt") or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        filename = result.get("filename")
        if not isinstance(filename, str) or not filename.endswith(".zip") or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=502, detail="备份服务返回无效文件名")
        app.state.store.record_backup(timestamp)
        return {"id": identity, "filename": filename, "lastBackupAt": timestamp}

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

    @app.post("/api/capture", status_code=201)
    async def capture(request: CaptureRequest, http_request: Request):
        expected = settings.read_capture_token()
        provided = http_request.headers.get("x-capture-token", "")
        if not expected or not provided or not secrets.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Capture token required")
        link = await _maybe_await(app.state.bookmarks.capture(
            request.url, request.title, request.description, request.collectionId,
        ))
        return {"id": link["id"], "created": True, "link": link}

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
            collections = await _maybe_await(app.state.bookmarks.collections())
            preferences = app.state.store.preferences()
            locked_ids = {int(identity) for identity, layout in preferences["collections"].items() if layout.get("locked")}
            count_method = getattr(app.state.bookmarks, "link_counts", None)
            if count_method:
                link_counts = await _maybe_await(count_method())
                try:
                    links = await _maybe_await(app.state.bookmarks.links(exclude_collection_ids=locked_ids))
                except TypeError:
                    all_links = await _maybe_await(app.state.bookmarks.links())
                    links = [link for link in all_links if link["collectionId"] not in locked_ids]
            else:
                all_links = await _maybe_await(app.state.bookmarks.links())
                link_counts = Counter(link["collectionId"] for link in all_links)
                links = [link for link in all_links if link["collectionId"] not in locked_ids]
            collections = [{**collection, "linkCount": link_counts.get(collection["id"], 0)} for collection in collections]
            sync = {"status": "ok"}
        except BookmarkStoreError as error:
            collections, links = [], []
            sync = {"status": "error", "message": str(error)}
            preferences = app.state.store.preferences()
        return {"collections": collections, "links": links, "preferences": preferences, "sync": sync}

    @app.patch("/api/links/layouts")
    def link_layouts(update: LinkOrdersUpdate):
        reject_during_import()
        return {"layouts": app.state.store.patch_link_orders(update.orders)}

    @app.post("/api/collections", status_code=201)
    def create_collection(values: CollectionCreate):
        reject_during_import()
        return app.state.bookmarks.create_collection(values)

    @app.get("/api/collections")
    async def list_collections():
        return await _maybe_await(app.state.bookmarks.collections())

    @app.patch("/api/collections/{identity}")
    def update_collection(values: CollectionUpdate, identity: int = Path(gt=0)):
        reject_during_import()
        return app.state.bookmarks.update_collection(identity, values)

    @app.delete("/api/collections/{identity}", status_code=204)
    def delete_collection(identity: int = Path(gt=0)):
        reject_during_import()
        app.state.bookmarks.delete_collection(identity)
        app.state.store.delete_collection(identity)
        return Response(status_code=204)

    @app.patch("/api/links/{identity}")
    async def update_link(update: LinkUpdate, identity: int = Path(gt=0)):
        reject_during_import()
        return await _maybe_await(app.state.bookmarks.update(identity, update))

    @app.delete("/api/links/{identity}", status_code=204)
    async def delete_link(identity: int = Path(gt=0)):
        reject_during_import()
        await _maybe_await(app.state.bookmarks.delete(identity))
        app.state.store.delete_link(identity)
        return Response(status_code=204)

    @app.post("/api/links/{identity}/metadata")
    async def link_metadata(identity: int = Path(gt=0)):
        reject_during_import()
        try:
            link = await _maybe_await(app.state.bookmarks.get(identity))
            return await fetch_public_metadata(link["url"])
        except MetadataFetchError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/links/{identity}/icon")
    async def link_icon(identity: int = Path(gt=0), refresh: bool = False):
        reject_during_import()
        try:
            link = await _maybe_await(app.state.bookmarks.get(identity))
            custom = await _maybe_await(app.state.bookmarks.custom_icon(identity))
            if custom:
                data, content_type = custom
                return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=3600", "Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff"})
            data, content_type = await app.state.site_assets.get_icon(link["url"], refresh=refresh)
            await _maybe_await(app.state.bookmarks.save_custom_icon(identity, data, content_type))
            return Response(
                content=data,
                media_type=content_type,
                headers={
                    "Cache-Control": "private, max-age=3600",
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except SiteIconNotFound as error:
            return Response(
                status_code=404,
                headers={"Cache-Control": f"private, max-age={NEGATIVE_CACHE_TTL_SECONDS}"},
            )
        except MetadataFetchError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/links/{identity}/icon/custom")
    async def save_custom_icon(request: Request, identity: int = Path(gt=0)):
        reject_during_import()
        data = await request.body()
        try:
            content_type = validate_icon_payload(request.headers.get("content-type", ""), data)
        except ValueError as error:
            raise HTTPException(status_code=415, detail=str(error)) from error
        if len(data) > MAX_CUSTOM_ICON_BYTES:
            raise HTTPException(status_code=413, detail="图标文件不能超过 4 MB")
        await _maybe_await(app.state.bookmarks.save_custom_icon(identity, data, content_type))
        return {"contentType": content_type, "bytes": len(data)}

    @app.post("/api/links/{identity}/icon/fetch")
    async def fetch_and_save_icon(identity: int = Path(gt=0)):
        reject_during_import()
        link = await _maybe_await(app.state.bookmarks.get(identity))
        try:
            data, content_type = await app.state.site_assets.get_icon(link["url"], refresh=True)
            content_type = validate_icon_payload(content_type, data)
        except (SiteIconNotFound, MetadataFetchError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        await _maybe_await(app.state.bookmarks.save_custom_icon(identity, data, content_type))
        return {"contentType": content_type, "bytes": len(data)}

    @app.patch("/api/links/{identity}/icon/size")
    async def save_icon_size(size: int = Query(ge=24, le=96), identity: int = Path(gt=0)):
        reject_during_import()
        saved = await _maybe_await(app.state.bookmarks.save_icon_size(identity, size))
        return {"size": saved}

    @app.patch("/api/links/{identity}/layout")
    def link_layout(update: LinkLayout, identity: int = Path(gt=0)):
        reject_during_import()
        return app.state.store.patch_link(identity, update)

    @app.patch("/api/collections/{identity}/layout")
    def collection_layout(update: CollectionLayout, identity: int = Path(gt=0)):
        reject_during_import()
        return app.state.store.patch_collection(identity, update)

    @app.put("/api/collections/{identity}/password")
    async def set_collection_password(values: CollectionPassword, identity: int = Path(gt=0)):
        reject_during_import()
        collections = await _maybe_await(app.state.bookmarks.collections())
        if not any(collection["id"] == identity for collection in collections):
            raise HTTPException(status_code=404, detail="分组不存在")
        saved = app.state.store.set_collection_password(identity, values.password)
        app.state.collection_unlocks = {token: value for token, value in app.state.collection_unlocks.items() if value[0] != identity}
        return saved

    @app.delete("/api/collections/{identity}/password")
    async def clear_collection_password(identity: int = Path(gt=0)):
        reject_during_import()
        collections = await _maybe_await(app.state.bookmarks.collections())
        if not any(collection["id"] == identity for collection in collections):
            raise HTTPException(status_code=404, detail="分组不存在")
        saved = app.state.store.clear_collection_password(identity)
        app.state.collection_unlocks = {token: value for token, value in app.state.collection_unlocks.items() if value[0] != identity}
        return saved

    @app.post("/api/collections/{identity}/unlock")
    async def unlock_collection(values: CollectionUnlock, identity: int = Path(gt=0)):
        reject_during_import()
        if not app.state.store.collection_is_locked(identity):
            raise HTTPException(status_code=409, detail="该分组未设置密码")
        if not app.state.store.verify_collection_password(identity, values.password):
            raise HTTPException(status_code=403, detail="密码不正确")
        token = secrets.token_urlsafe(32)
        app.state.collection_unlocks[token] = (identity, time.monotonic() + 30 * 60)
        app.state.collection_unlocks = {key: value for key, value in app.state.collection_unlocks.items() if value[1] > time.monotonic()}
        try:
            links = await _maybe_await(app.state.bookmarks.links(include_collection_ids={identity}))
        except TypeError:
            # Compatibility with lightweight bookmark adapters used by integrations.
            all_links = await _maybe_await(app.state.bookmarks.links())
            links = [link for link in all_links if link["collectionId"] == identity]
        return {"collectionId": identity, "unlockToken": token, "links": links}

    @app.get("/api/search-engines")
    def search_engines():
        return app.state.store.search()

    @app.put("/api/search-engines")
    def update_search(settings: SearchSettings):
        reject_during_import()
        return app.state.store.save_search(settings)

    # Serve the production React build from the same origin as the API when configured.
    # Keeping this optional leaves the test/dev API-only app unchanged.
    static_dir = os.environ.get("HOMEPAGE_STATIC_DIR")
    if static_dir and FilePath(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="homepage-static")

    return app


app = create_app()
