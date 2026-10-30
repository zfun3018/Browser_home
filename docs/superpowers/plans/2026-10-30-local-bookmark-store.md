# Local Bookmark Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the always-on Linkwarden stack with a local SQLite bookmark store while preserving homepage behavior and adding Chrome/Edge one-click capture.

**Architecture:** The homepage backend selects a bookmark provider through `BOOKMARK_BACKEND`; SQLite is the default production provider and the existing Linkwarden client remains as a migration source and rollback path. A capture endpoint writes validated current-tab data to SQLite, while a Manifest V3 extension calls it with a local token. Compose keeps legacy services behind a profile and starts backup only through a manual profile.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Pydantic, React/Vite, Chrome/Edge Manifest V3, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-30-local-bookmark-store-design.md`

## Global Constraints

- Only HTTP(S) URLs without credentials are accepted.
- Existing integer link and collection IDs are preserved during migration.
- The existing `StateStore` remains the source of layout and search preferences.
- The migration never deletes Linkwarden data.
- The homepage remains bound to `127.0.0.1`.
- Capture requests require a token and are never logged.

---

### Task 1: Local SQLite bookmark provider

**Files:**
- Create: `backend/app/bookmark_store.py`
- Create: `backend/tests/test_bookmark_store.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- `SqliteBookmarkStore(path: Path)` exposes `links()`, `collections()`, `get(identity)`, `update(identity, patch)`, and `capture(url, name, description, collection_id)`.
- `Settings` exposes `bookmark_backend`, `bookmark_db_path`, and `capture_token_file`.

- [ ] Write failing tests for schema creation, capture, duplicate capture, collection validation, optimistic update conflict, and ID preservation.
- [ ] Run `pytest backend/tests/test_bookmark_store.py -q` and verify the new tests fail because the provider does not exist.
- [ ] Implement the SQLite schema and provider with explicit transactions and the existing `LinkUpdate` validation model.
- [ ] Add provider selection while preserving injected Linkwarden clients used by existing tests.
- [ ] Run the focused tests and then the backend API tests.

### Task 2: Capture API and migration utility

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Create: `backend/scripts/migrate_linkwarden.py`
- Create: `backend/tests/test_capture_api.py`

**Interfaces:**
- `POST /api/capture` accepts `{url, title, description?, collectionId?}` and returns `{id, created, link}`.
- `migrate_linkwarden.py` reads the token file and imports collections and paginated links without deleting source data.

- [ ] Write failing API tests for valid capture, invalid URL, bad token, duplicate URL, and default collection.
- [ ] Run the focused tests and verify they fail before implementation.
- [ ] Implement token validation, request-origin handling for the extension, URL validation, and provider capture.
- [ ] Implement the migration script with explicit ID inserts and a dry-run summary.
- [ ] Run API tests and execute a dry-run against a disposable SQLite database.

### Task 3: Browser extension

**Files:**
- Create: `browser-extension/manifest.json`
- Create: `browser-extension/background.js`
- Create: `browser-extension/options.html`
- Create: `browser-extension/options.js`
- Create: `browser-extension/README.md`

- [ ] Add a Manifest V3 toolbar action and context menu for the active tab.
- [ ] Store homepage URL and capture token in extension storage; default URL is `http://127.0.0.1:3011`.
- [ ] POST the active tab URL/title to `/api/capture`, show success/failure notification, and never include the token in logs.
- [ ] Document loading the unpacked extension and configuring the token.

### Task 4: Local backup and manual profile

**Files:**
- Modify: `backup_service/archive.py`
- Modify: `backup_service/app.py`
- Modify: `backup_service/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `docs/deployment.md`
- Create: `backup_service/tests/test_local_archive.py`

- [ ] Write failing tests for local SQLite archive contents and restore validation.
- [ ] Run the focused tests and verify failure for the missing local format.
- [ ] Add a local archive mode that copies `bookmarks.db`, `state.db`, note files and icon cache metadata without requiring PostgreSQL.
- [ ] Put backup behind the `backup` Compose profile and remove the homepage hard dependency on it.
- [ ] Put old Linkwarden services behind the `legacy` profile and set SQLite as the default homepage backend.
- [ ] Run archive tests and `docker compose config --quiet` without printing secrets.

### Task 5: Homepage wording and verification

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/BackupControls.tsx`
- Modify: `frontend/src/components/backup.css`
- Modify: `frontend/src/styles.css`
- Modify: relevant frontend tests and docs

- [ ] Replace Linkwarden-only labels with local bookmark wording and remove the dead Linkwarden navigation.
- [ ] Add a visible capture-token/help link in deployment documentation, not in the normal homepage workflow.
- [ ] Run frontend tests and production build.
- [ ] Run the migration against the current Linkwarden instance, verify imported counts, start SQLite mode, and inspect `docker stats`.
- [ ] Verify homepage load, edit, collection move, layout persistence, icon endpoint, capture endpoint, manual backup, and legacy rollback.
