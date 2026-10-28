from pathlib import Path as FilePath
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .linkwarden_client import LinkwardenClient, LinkwardenError
from .notes import NoteConflict, NoteStore, NoteUnavailable
from .schemas import CollectionLayout, LinkLayout, LinkUpdate, SearchSettings
from .state_store import StateStore


class NoteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=5_000_000)
    expectedHash: str = Field(pattern=r"^[a-f0-9]{64}$")


def create_app(settings: Settings | None = None, linkwarden=None, store=None, note_store=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="Personal Homepage", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.store = store or StateStore(settings.data_dir / "state.db")
    app.state.linkwarden = linkwarden or LinkwardenClient(settings.linkwarden_url, settings.read_token())
    app.state.notes = note_store or NoteStore(FilePath("/app/data/浏览器首页笔记.md"), settings.data_dir / "note-conflicts")

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

    @app.get("/api/notes")
    def get_note():
        try:
            return app.state.notes.get()
        except NoteUnavailable:
            return {"content": "", "hash": "", "status": "unavailable"}

    @app.put("/api/notes")
    def save_note(update: NoteWrite):
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
        return await app.state.linkwarden.update(identity, update)

    @app.patch("/api/links/{identity}/layout")
    def link_layout(update: LinkLayout, identity: int = Path(gt=0)):
        return app.state.store.patch_link(identity, update)

    @app.patch("/api/collections/{identity}/layout")
    def collection_layout(update: CollectionLayout, identity: int = Path(gt=0)):
        return app.state.store.patch_collection(identity, update)

    @app.get("/api/search-engines")
    def search_engines():
        return app.state.store.search()

    @app.put("/api/search-engines")
    def update_search(settings: SearchSettings):
        return app.state.store.save_search(settings)

    return app


app = create_app()
