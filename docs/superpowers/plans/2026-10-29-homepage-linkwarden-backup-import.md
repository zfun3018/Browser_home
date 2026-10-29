# Homepage and Linkwarden Backup/Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add downloadable full-data backups, safe import with automatic rollback backup, a seven-day homepage reminder, and a one-click Linkwarden navigation link.

**Architecture:** Keep the FastAPI homepage off the Postgres data network. Add an internal backup service on dedicated `backup_api` and `backup_db` networks; it owns `pg_dump`, archive creation, validation, and staged restore. FastAPI proxies streamed downloads/uploads, stores backup timestamps, and guards homepage writes during restore. React exposes backup/import states and the Linkwarden link.

**Tech Stack:** Python 3.12, FastAPI, SQLite, PostgreSQL 16 `pg_dump`/`pg_restore`, Docker Compose, React, TypeScript, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-homepage-and-linkwarden-backup-design.md`

## Global Constraints

- Keep the homepage service off `data` and `backup_db`; do not mount the Docker socket.
- Publish no host port for the backup service.
- Keep Linkwarden data in PostgreSQL and the existing `data` directory; do not include MeiliSearch's rebuildable index.
- Include `.env` and `homepage-secrets/linkwarden_api_token.txt` in downloadable ZIPs as plaintext, and warn that the archive contains credentials.
- Import replaces Linkwarden database data, Linkwarden attachments, homepage SQLite state, Obsidian note, and note-conflict copies; retain the current Compose file, `.env`, and Token without overwriting them.
- Require a fully delivered automatic rollback backup and explicit final confirmation before import changes live data.
- Reject archive traversal, symlinks, duplicate paths, unsupported format versions, invalid SQLite/PG dumps, and archives exceeding configured size limits.
- Stream archive upload/download; never load a full archive into frontend memory.
- Log neither credentials nor archive contents nor user notes.

---

## File Map

- `backend/app/state_store.py`: versioned SQLite migration and persistent last-backup timestamp.
- `backend/app/backups.py`: homepage-facing backup/import job coordination, streamed proxying, maintenance state, and timestamp updates.
- `backend/app/main.py`: same-origin API routes and maintenance gating.
- `backend/tests/test_backups.py`: backup metadata, route, stream completion, and maintenance tests.
- `backend/tests/test_api.py`: route integration with a fake internal backup service.
- `backup_service/Dockerfile`: non-root Python 3.12 image with PostgreSQL 16 client tools.
- `backup_service/requirements.txt`: backup-service HTTP framework and archive dependencies.
- `backup_service/app.py`: internal HTTP endpoints and job lifecycle.
- `backup_service/archive.py`: deterministic manifest, safe ZIP creation, size/path validation, dump checks, and streaming.
- `backup_service/restore.py`: staging, database swap, file swap, rollback journal, and startup recovery.
- `backend/tests/fixtures/backup_minimal.zip`: generated minimal compatible archive fixture; never store production secrets.
- `frontend/src/App.tsx`: backup reminder, backup/import actions, status polling, final replacement confirmation, and Linkwarden navigation.
- `frontend/src/styles.css`: reminder, import dialog, progress/error states, and header action layout.
- `frontend/src/App.test.tsx`: reminder, download, import, and Linkwarden link behavior.
- `F:\浏览器导航页\docker-compose.yml`: backup networks/service, isolated mounts, and existing services' network membership.
- `docs/deployment.md`: archive contents, sensitive-file warning, import/export flow, restore procedure, limits, and failure recovery.
- `docs/superpowers/specs/2026-10-29-homepage-and-linkwarden-backup-design.md`: approved behavior; change only if implementation reveals a material mismatch.

## Task 1: Persistent Backup Status and Homepage API Proxy

**Files:**
- Modify: `backend/app/state_store.py`
- Modify: `backend/app/config.py`
- Create: `backend/app/backups.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_backups.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- `StateStore.last_backup_at() -> str | None`
- `StateStore.record_backup(timestamp: str) -> None`
- `BackupCoordinator.create_backup() -> BackupJob`
- `BackupCoordinator.open_download(job_id: str) -> AsyncIterator[bytes]`
- `BackupCoordinator.prepare_import(upload: AsyncIterator[bytes], filename: str) -> ImportJob`
- `BackupCoordinator.import_status(job_id: str) -> ImportStatus`
- `BackupCoordinator.open_rollback_download(job_id: str) -> AsyncIterator[bytes]`
- `BackupCoordinator.commit_import(job_id: str) -> ImportResult`
- `GET /api/backup/status -> {"lastBackupAt": str | null}`
- `POST /api/backup -> {"id": str, "filename": str}`
- `GET /api/backup/{id}/download -> streamed ZIP`
- `POST /api/import/prepare -> {"id": str, "manifest": object}`
- `GET /api/import/{id}/status -> {"status": str, "rollbackDelivered": bool}`
- `GET /api/import/{id}/rollback -> streamed ZIP`
- `POST /api/import/{id}/commit -> {"status": "complete"}`

- [ ] **Step 1: Add failing state-store tests**

Add tests in `backend/tests/test_backups.py` for a new database having no last-backup value, saving an ISO-8601 UTC timestamp, and reading it after constructing a new `StateStore` on the same path. Add a migration test opening a version-1 database with existing layout and search rows and verify those rows survive the version-2 migration.

- [ ] **Step 2: Run the focused tests and verify failure**

Run `python -m pytest backend/tests/test_backups.py -q`. Expected: failure because backup metadata methods and migration do not exist.

- [ ] **Step 3: Implement the schema migration**

Update `StateStore` to accept schema versions 1 and 2, create a singleton `backup_metadata(id INTEGER PRIMARY KEY CHECK(id=1), last_backup_at TEXT)` table, set `PRAGMA user_version=2`, and implement `last_backup_at()` and `record_backup(timestamp)` with parameterized SQL. Do not rewrite existing preferences.

- [ ] **Step 4: Add failing API tests with an injected fake coordinator**

Test status for never-backed-up and previously-backed-up states, successful job creation, ZIP response headers, route errors, and exact `record_backup` behavior only after the download iterator reaches EOF. Test all POST routes with allowed Host/Origin and reject cross-site requests using the existing middleware.

- [ ] **Step 5: Implement coordinator injection and routes**

Add `backup_service_url` to `Settings.from_env`, inject an optional coordinator in `create_app`, and create routes with `StreamingResponse` and `application/zip`. Set `Content-Disposition` to `attachment; filename="browser-homepage-backup-YYYYMMDD-HHmmss.zip"`. Update the status only after the proxy iterator completes without a disconnect; do not mark a failed stream successful.

- [ ] **Step 6: Add an import maintenance gate**

Track the active import job in app state. While its state is `committing`, reject homepage state-changing endpoints and note PUT with 503 plus `{"detail":"import_in_progress"}`. Keep health and import status available. Clear the gate in `finally` after success or rollback.

- [ ] **Step 7: Run focused backend tests**

Run `python -m pytest backend/tests/test_backups.py backend/tests/test_api.py backend/tests/test_security.py -q`. Expected: all tests pass, with no change to normal home, note, or search behavior.

- [ ] **Step 8: Commit the backend API layer**

Commit only these backend files with message `feat: add backup and import API coordination`.

## Task 2: Internal Archive and PostgreSQL Backup Service

**Files:**
- Create: `backup_service/Dockerfile`
- Create: `backup_service/requirements.txt`
- Create: `backup_service/app.py`
- Create: `backup_service/archive.py`
- Create: `backup_service/tests/test_archive.py`
- Create: `backup_service/tests/test_api.py`
- Create: `backend/tests/fixtures/backup_minimal.zip`

**Interfaces:**
- `POST /internal/backups -> {"id": str, "filename": str}`
- `GET /internal/backups/{id}/download -> streamed ZIP`
- `POST /internal/imports/prepare` accepts streamed ZIP bytes and returns `{id, manifest}`
- `GET /internal/imports/{id}/status -> ImportStatus`
- `GET /internal/imports/{id}/rollback -> streamed ZIP`
- `POST /internal/imports/{id}/rollback-delivered -> {"readyToCommit": true}`
- `POST /internal/imports/{id}/commit -> ImportResult`
- ZIP manifest format version is `1` and contains `createdAt`, `applicationVersion`, and the required file list with SHA-256 hashes.

- [ ] **Step 1: Add failing archive layout tests**

Construct temporary Linkwarden data, SQLite, Obsidian, conflicts, Compose, `.env`, and token files. Assert the archive includes only the documented paths, has a version-1 manifest with matching hashes, and contains a valid `pg_dump` payload produced by a fake `pg_dump` executable.

- [ ] **Step 2: Add archive rejection tests**

Test rejection of `../escape`, absolute paths, symlink entries, duplicate filenames, invalid manifest hashes, unsupported versions, over-limit expanded size, and missing required input files. Assert temporary staging is removed after each rejection.

- [ ] **Step 3: Implement archive creation**

Build a ZIP in a per-job directory under `/staging`. Use `sqlite3.Connection.backup()` for the live homepage database. Run `pg_dump -Fc` with credentials supplied only to the backup container environment; capture stderr only after redacting passwords. Copy Linkwarden and note files using manifest-relative paths. Create `RESTORE.txt`, `manifest.json`, then stream the finished ZIP. Remove the job directory after download completion or TTL expiry.

- [ ] **Step 4: Implement the internal backup HTTP API**

Create FastAPI endpoints for create/download. Validate generated UUID job IDs, enforce one active job per service process, return generic error messages, and keep credential-bearing exception text out of responses and logs.

- [ ] **Step 5: Build the non-root image**

Use `python:3.12-slim`, install the PostgreSQL 16 client, copy only `backup_service`, create a non-root user, and expose the internal port without publishing it through Compose. Pin Python dependencies in `backup_service/requirements.txt`.

- [ ] **Step 6: Run archive-service tests**

Run `python -m pytest backup_service/tests -q`. Expected: deterministic ZIP layout, redaction checks, cleanup checks, and traversal/size rejection pass.

- [ ] **Step 7: Commit the archive service**

Commit the service and tests with message `feat: create internal full-data backup service`.

## Task 3: Compose Networks and Host Data Mounts

**Files:**
- Modify: `F:\浏览器导航页\docker-compose.yml`
- Modify: `Dockerfile` only if the homepage proxy needs an additional pinned runtime dependency.
- Modify: `docs/deployment.md`

- [ ] **Step 1: Record the current Compose structure without reading `.env` contents**

Inspect service/network/volume keys only. Keep existing Linkwarden, PostgreSQL, MeiliSearch, data paths, ports, restart policies, and secrets intact.

- [ ] **Step 2: Add isolated networks**

Create internal `backup_api` and `backup_db` bridge networks. Attach homepage to `app` and `backup_api`; attach the backup service to `backup_api` and `backup_db`; attach PostgreSQL to its existing `data` network and `backup_db`. Do not attach Linkwarden to `backup_api` or `backup_db`; do not attach homepage to `data` or `backup_db`.

- [ ] **Step 3: Add the backup service mounts and environment**

Mount Linkwarden `./data`, `./homepage-data`, the exact Obsidian note, `./docker-compose.yml`, `./.env`, and `./homepage-secrets/linkwarden_api_token.txt` only into the backup service. Supply PostgreSQL host/database/user/password using explicit Compose substitutions from existing `.env` keys. Do not pass the entire `.env` as a container environment file. Do not publish a host port or mount `/var/run/docker.sock`.

- [ ] **Step 4: Add isolated staging storage**

Bind `./homepage-backup-staging` to `/staging`; store job artifacts and restore journal there, never include this directory in generated ZIPs. Add startup cleanup for expired completed jobs and recovery for an interrupted restore journal.

- [ ] **Step 5: Validate Compose without exposing secrets**

Run `docker compose config --quiet` and inspect only the rendered `services.*.ports`, `networks`, and `volumes` fields. Do not print or save rendered environment values. Verify homepage has no database network, backup has no host port, and no service mounts Docker socket.

- [ ] **Step 6: Commit Compose integration**

Commit the Compose change from the parent deployment repository only if it is a tracked repository file; otherwise report its separate workspace change. Commit project documentation updates with message `chore: wire isolated backup networks`.

## Task 4: Safe Import Validation, Staging, Swap, and Rollback

**Files:**
- Create: `backup_service/restore.py`
- Modify: `backup_service/app.py`
- Modify: `backup_service/archive.py`
- Create: `backup_service/tests/test_restore.py`
- Modify: `backend/app/backups.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_backups.py`

**Interfaces:**
- `prepare_import(upload, filename) -> ImportJob`; validates and stages without changing live files or databases.
- `mark_rollback_delivered(job_id) -> None`; only succeeds after the entire current-state backup response has completed.
- `commit_import(job_id) -> ImportResult`; refuses jobs without a delivered rollback archive.
- Import statuses are `validating`, `ready_for_rollback`, `downloading_rollback`, `ready_to_commit`, `committing`, `complete`, `failed`, or `expired`.

- [ ] **Step 1: Add failing preflight validation tests**

Test a valid generated archive, bad ZIP, missing manifest, hash mismatch, unsupported format, oversized input, path traversal, symlink, duplicate path, failed `pg_restore --list`, failed SQLite `integrity_check`, and missing data files. Assert live fixtures remain byte-identical after every prepare failure.

- [ ] **Step 2: Add failing rollback-download and commit-gate tests**

Verify commit is rejected before rollback download completes, rejected on client disconnect, rejected for an expired job, and accepted only after `mark_rollback_delivered`. Verify a second commit request with the same job ID returns the stored result without running restore twice.

- [ ] **Step 3: Implement streaming upload and staging**

Stream the request into a unique `/staging/import-<uuid>/upload.zip` while enforcing compressed size. Validate entries before extraction; extract only whitelisted data paths into a new empty staging directory, enforce expanded-size limits, compute hashes, and verify the PG dump and SQLite database. Never extract Compose, `.env`, or token files into live deployment paths.

- [ ] **Step 4: Generate the pre-import rollback archive**

After validation, generate a complete current-state ZIP into the import job directory. Stream it to the browser. Mark `rollbackDelivered` only when the server-side response stream completes; on disconnect, retain the ready import job but require the rollback download to be restarted and completed.

- [ ] **Step 5: Add transactional restore journal tests**

Use fake database and file operations to test each failure point: stage DB creation, `pg_restore`, DB rename, attachment swap, homepage state swap, note swap, and reopening connections. Assert the restore journal identifies old/new locations and that recovery restores the original data after simulated process restart.

- [ ] **Step 6: Implement staged database restore and file swap**

Restore the archive to a uniquely named staging database and verify its required Linkwarden tables. Write a durable restore journal before touching live state. During commit, gate homepage writes, disable new connections to the live database from a separate admin connection, terminate existing Linkwarden sessions, rename the original database aside, rename the staging database to the configured database name, swap attachment/homepage/note paths while keeping old paths, then re-enable connections. If any step fails, reverse completed swaps from the journal before clearing maintenance. Delete old data only after all swaps and health checks pass.

- [ ] **Step 7: Implement startup recovery and cleanup**

On backup-service startup, inspect restore journals. If the commit is incomplete, restore original database and file paths, re-enable the configured database, record recovery outcome, then remove only abandoned staging jobs older than the configured TTL. Never remove a path named by an unfinished journal.

- [ ] **Step 8: Add API job-state and idempotency tests**

Test prepare/status/rollback/commit route contracts, same-origin validation, maintenance responses, errors with no secret leakage, and last-backup timestamp preservation from the pre-import archive after the imported SQLite database replaces current state.

- [ ] **Step 9: Run focused restore tests**

Run `python -m pytest backup_service/tests/test_restore.py backend/tests/test_backups.py -q`. Expected: all simulated stage/swap/recovery paths pass and no live data fixture changes on failed preparation.

- [ ] **Step 10: Commit import backend**

Commit with message `feat: safely import and roll back full backups`.

## Task 5: Homepage Reminder, Import Flow, and Linkwarden Navigation

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/App.test.tsx`
- Modify: `backend/app/main.py` only if a GET maintenance status response is needed.

- [ ] **Step 1: Add failing reminder tests**

Test no-history reminder, exactly seven days, more than seven days, valid recent backup, local-time display, request failure, and successful download status refresh. Use fixed clock values so the seven-day boundary is deterministic.

- [ ] **Step 2: Add failing Linkwarden navigation test**

Render the top bar and assert an accessible “打开 Linkwarden” link has `href="http://localhost:3010"` and no `target` attribute, so it opens in the current tab.

- [ ] **Step 3: Implement status polling and backup download**

Read `/api/backup/status` on app mount and after window focus. Show the reminder when timestamp is null or `Date.now() - Date.parse(lastBackupAt) > 7 * 24 * 60 * 60 * 1000`. On backup click, create a job, initiate direct streamed download by anchor URL, poll job status until transfer success/failure, and refresh the reminder state.

- [ ] **Step 4: Add failing import-flow tests**

Test invalid file rejection, validation progress, displaying archive date and replace scope, automatic rollback download before enabling final confirmation, cancellation, successful replace, failed replace, and maintenance message handling. Assert the user cannot call commit before rollback download completion.

- [ ] **Step 5: Implement import UI**

Add a hidden `.zip` file input and “导入备份” button. Upload selected files as streamed `FormData`; show validation and rollback download states; display the exact replacement scope; require a second explicit confirm action; call commit only when the server reports `ready_to_commit`; then clear local Linkwarden/link state and refetch all APIs. Preserve the old UI and show a recovery error if the server reports rollback failure.

- [ ] **Step 6: Add Linkwarden header action and responsive styles**

Place the link beside existing top actions with an icon and visible label at desktop widths, preserving an accessible name at narrow widths. Style backup reminder, import dialog, progress, success, and failure states in the existing visual system.

- [ ] **Step 7: Run frontend tests and build**

Run `pnpm --dir frontend test -- --run` and `pnpm --dir frontend build`. Expected: all existing and new tests pass and Vite creates production assets.

- [ ] **Step 8: Commit UI feature**

Commit with message `feat: add backup reminder import flow and Linkwarden link`.

## Task 6: Recovery Documentation and End-to-End Validation

**Files:**
- Modify: `docs/deployment.md`
- Modify: `docs/spec.md`
- Modify: `backup_service/tests/test_restore.py` only for concrete uncovered cases.

- [ ] **Step 1: Document archive contents and plaintext credentials**

List every ZIP path, state that `.env` and Token values are plaintext, explain local download behavior, and tell users to protect or delete archives as password files.

- [ ] **Step 2: Document import replacement semantics**

Explain validation, automatic download of current state, final replacement confirmation, restored datasets, preserved current Compose/`.env`/Token, maintenance outage, and behavior on failure.

- [ ] **Step 3: Document manual cross-machine recovery**

Give ordered steps to restore `.env` and token files securely, restore PostgreSQL and attachments, restore homepage state/note, run Compose, and allow MeiliSearch to rebuild. Never recommend copying a live `pgdata` directory as the only backup.

- [ ] **Step 4: Run backend/frontend and Compose validation**

Run `python -m pytest backend/tests backup_service/tests -q`, `pnpm --dir frontend test -- --run`, `pnpm --dir frontend build`, and `docker compose config --quiet`. Expected: all checks pass without printing secret values.

- [ ] **Step 5: Run isolated archive/restore acceptance**

Generate an archive from disposable data, inspect ZIP paths and hashes, restore to a disposable Postgres database and temp directories, verify Linkwarden rows/attachments/homepage preferences/note/conflicts, and inject a swap failure to verify recovery. Do not run import against the user's live Linkwarden database during this check.

- [ ] **Step 6: Review deployment changes**

Inspect the final Compose diff and verify current service ports, bind paths, restart policies, Linkwarden credentials, and existing data volumes remain unchanged except for the designed internal networks and backup service.

- [ ] **Step 7: Commit documentation and final fixes**

Commit only deployment docs and concrete final fixes with message `docs: describe full backup and restore workflow`.

## Plan Self-Review

- Backup files and seven-day reminder: Tasks 1, 2, 3, and 5.
- Plaintext secrets in downloaded archive and user warning: Tasks 2, 5, and 6.
- Full Linkwarden database and attachment backup: Tasks 2 and 3.
- Import with validation, automatic rollback download, explicit replacement, current config preserved, and failure recovery: Tasks 4, 5, and 6.
- One-click current-tab navigation to Linkwarden: Task 5.
- Network isolation, no host port, and no Docker socket: Task 3.
- Cross-machine recovery and MeiliSearch rebuild: Task 6.
- No TODO/TBD placeholders; route and coordinator contracts are named in Tasks 1, 2, and 4.
