# Personal Browser Homepage Implementation Plan

> **For Codex:** Execute with the superpowers:subagent-driven-development skill, using a fresh implementer and scoped review for each task.

**Goal:** Build a polished local browser homepage with dense draggable Linkwarden collections, configurable web search, and conflict-safe Markdown sync to the user's Obsidian note.

**Architecture:** A FastAPI backend serves a React/Vite frontend and proxies Linkwarden API calls with a server-side bearer token. SQLite stores only homepage presentation/search preferences. The backend mounts and polls the single Obsidian note file, exposing autosave and conflict state. Docker Compose adds one loopback-only service and a narrow Linkwarden/homepage network.

**Tech Stack:** Python 3.12, FastAPI, httpx, SQLite, React, TypeScript, Vite, CSS Grid, dnd-kit, Vitest, pytest, Docker Compose.

---

## Global Constraints
- Project source is `F:\\浏览器导航页\\custom-homepage`; deployment data stays under `F:\\浏览器导航页`.
- Keep current Linkwarden, PostgreSQL, and Meilisearch data. Do not read or commit the existing `F:\\浏览器导航页\\.env`.
- Homepage host port is `127.0.0.1:3011`; Linkwarden stays at `127.0.0.1:3010`.
- Linkwarden is authoritative for bookmark URL, name, description, icon glyph/weight/color, and collection membership. Use immutable Linkwarden IDs. Do not duplicate bookmark records in SQLite.
- Linkwarden v2.16.3 `UpdateLinkSchema` accepts `name`, `url`, `description`, `icon`, `iconWeight`, `color`, `collection`, `tags`, and `pinnedBy`; favicon is derived from URL. Verify requests against the v2.16.3 behavior.
- Homepage preferences only: field visibility, order, group collapse/order, search providers/default. Store in SQLite with a schema version.
- Read/write only the exact note path `F:\\文件\\Documents\\Obsidian仓库\\Obsidian-note\\0.01A-Day or Day\\浏览器首页笔记.md`; never accept a path from a request.
- Keep Linkwarden credentials out of frontend code, local storage, URL, logs, and repository. Use a file-backed Docker secret at `F:\\浏览器导航页\\homepage-secrets\\linkwarden_api_token.txt`.
- Detect external note changes by periodic content hash. Never silently overwrite concurrent changes; preserve both and require resolution.
- Validate Host and Origin on state-changing routes, bind only to loopback, avoid wildcard CORS, escape user content, and allow only http/https bookmark URLs.
- Do not modify or rebuild the existing Sun-Panel package.

### Task 1: Backend core, state store, and Linkwarden API

**Files:**
- Create `backend/app/main.py`
- Create `backend/app/config.py`
- Create `backend/app/linkwarden_client.py`
- Create `backend/app/state_store.py`
- Create `backend/app/schemas.py`
- Create `backend/requirements.txt`
- Create `backend/tests/test_linkwarden_client.py`
- Create `backend/tests/test_state_store.py`
- Create `backend/tests/test_security.py`

**Steps:**
1. Write pytest tests for paginated link/collection reads, Linkwarden API field mapping, 401/error handling, SQLite preference persistence, and Host/Origin rejection.
2. Run `python -m pytest backend/tests -q`; confirm tests fail for missing modules/behavior.
3. Implement a typed Linkwarden client using `httpx`; consume `LINKWARDEN_URL` and token from `LINKWARDEN_API_TOKEN_FILE`. Never log the token.
4. Implement SQLite migrations/schema for presentation settings and search providers keyed by immutable IDs; keep bookmark content out of SQLite.
5. Implement FastAPI routes for collection/link reads, bookmark updates/moves, layout preferences, search settings, health, and same-origin/CSRF guards.
6. Run `python -m pytest backend/tests -q`; require all tests to pass.
7. Commit `feat: add Linkwarden homepage API and local preferences`.

### Task 2: Responsive homepage UI and bookmark interactions

**Files:**
- Create `frontend/package.json`
- Create `frontend/tsconfig.json`
- Create `frontend/vite.config.ts`
- Create `frontend/index.html`
- Create `frontend/src/main.tsx`
- Create `frontend/src/App.tsx`
- Create `frontend/src/api.ts`
- Create `frontend/src/components/SearchBar.tsx`
- Create `frontend/src/components/CollectionSection.tsx`
- Create `frontend/src/components/BookmarkCard.tsx`
- Create `frontend/src/components/SettingsDialog.tsx`
- Create `frontend/src/styles.css`
- Create frontend unit tests under `frontend/src/**/*.test.tsx`

**Steps:**
1. Write Vitest tests for the three independent field visibility switches, safe search URL encoding, collection fold state, and API error states.
2. Run `npm --prefix frontend test -- --run`; confirm the new behavior tests fail.
3. Build a clean light-first page with optional dark mode, responsive wrapping CSS grid, concise search header, compact Linkwarden collection sections, and a large notes area.
4. Use `@dnd-kit` for pointer and keyboard reordering/moving; provide a non-drag “Move to collection” action for touch and accessibility.
5. Persist field visibility and ordering through backend preferences; use Linkwarden IDs; make title/url/description/glyph/color edits and collection moves write through to Linkwarden.
6. Add accessible loading, empty, offline, validation, and sync-error states; render all URL/text fields safely.
7. Run `npm --prefix frontend test -- --run` and `npm --prefix frontend run build`; require both to pass.
8. Commit `feat: add responsive bookmark homepage UI`.

### Task 3: Obsidian Markdown autosave and conflict handling

**Files:**
- Create `backend/app/notes.py`
- Create `backend/tests/test_notes.py`
- Create `frontend/src/components/MarkdownNotes.tsx`
- Create `frontend/src/components/MarkdownNotes.test.tsx`
- Modify `backend/app/main.py`
- Modify `frontend/src/App.tsx`
- Modify `frontend/src/styles.css`

**Steps:**
1. Write tests for exact mounted-file reads, autosave with expected content hash, external change polling, file unavailable/recovery, and concurrent-edit conflict without data loss.
2. Run `python -m pytest backend/tests/test_notes.py -q`; confirm failures before implementation.
3. Implement fixed-path note access, content hash polling, serialized writes, in-place write with a pre-write backup, and conflict copies under the app data directory. Never accept a filesystem path from the browser.
4. Implement a Markdown editor/preview with debounced save and Saving/Saved/Conflict/Sync issue status.
5. Detect external edits while the page is open; reload automatically only when there is no local draft. If both sides changed, keep the local draft and show reload/resolve actions while preserving the external version.
6. Run note tests plus frontend tests/build; require them to pass.
7. Commit `feat: sync homepage notes with Obsidian`.

### Task 4: Docker Compose deployment and operator documentation

**Files:**
- Create `Dockerfile`
- Create `.dockerignore`
- Create `docs/deployment.md`
- Modify parent deployment file `F:\\浏览器导航页\\docker-compose.yml` from the deployment task after reviewing a diff.
- Create the host secret placeholder `F:\\浏览器导航页\\homepage-secrets\\linkwarden_api_token.txt` only if absent.

**Steps:**
1. Build a multi-stage image: Node stage builds Vite assets; Python runtime serves the SPA and API. Run as non-root.
2. Extend Compose with homepage on `127.0.0.1:3011`, server-only Linkwarden token secret, SQLite data volume, and a read/write bind mount of only the specified Obsidian note. Keep existing data volumes intact.
3. Add separate app/data networks so homepage can reach Linkwarden but not Postgres/Meilisearch directly; retain Linkwarden internet access for archiving.
4. Add a health check and retry behavior for Linkwarden API availability; do not assume `depends_on` means API readiness.
5. Document token creation/insertion, startup/stop, browser homepage setup, backup, and recovery. State clearly the token remains on the host and is never pasted into chat.
6. Validate `docker compose config`, build the homepage image, inspect container ports and mounts, and verify `http://localhost:3011/health`.
7. Commit `chore: deploy custom homepage with Docker Compose`.

### Task 5: Cross-service acceptance and security review

**Files:**
- Modify only files with concrete findings from QA/security review.
- Update `docs/deployment.md` and `docs/spec.md` if behavior or limits differ.

**Steps:**
1. Run backend tests and frontend tests/build; no test may be skipped silently.
2. Verify health and local-only binding; inspect Docker mounts to confirm only the requested Obsidian note and app data are mounted.
3. With a locally configured Linkwarden token, exercise a bookmark field round trip, collection move, layout persistence, search URL generation, and external note edit detection.
4. Test one concurrent note edit and confirm both versions remain recoverable.
5. Dispatch a read-only security and whole-branch review; fix concrete Critical/Important findings and document any accepted limitations.
6. Commit any final fixes with scoped messages.

## Interface contract
- `GET /api/home`: collections, paginated Linkwarden bookmarks, current presentation settings, and sync status.
- `PATCH /api/links/{id}`: validated Linkwarden fields only; backend writes through to Linkwarden API and returns canonical updated link.
- `PATCH /api/links/{id}/layout`: show-name/icon/description and order metadata only.
- `PATCH /api/collections/{id}/layout`: order and expanded/collapsed preference only.
- `GET/PUT /api/search-engines`: validated named HTTP(S) query templates and default engine.
- `GET /api/notes`, `PUT /api/notes`, and `GET /api/notes/events`: content/hash, optimistic save, and external-change notification.
- `GET /health`: process health. Linkwarden API connectivity is a separate status and must not prevent static page loading.

## Plan review notes
- Cross-task interfaces: Task 1 defines JSON schemas consumed by Task 2; Task 3 extends `main.py` and `App.tsx` from earlier tasks; Task 4 packages the outputs of Tasks 1–3; Task 5 is read-only verification/fix routing.
- Do not implement until Task 1 route contracts and Task 3 conflict semantics match this spec.
- Linkwarden canonical field edits should be last-write-wins only for a field changed on one side. On a detected same-field concurrent change, return a conflict and refresh canonical Linkwarden data rather than silently overwrite.
