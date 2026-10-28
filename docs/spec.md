# Browser Homepage Product and Architecture Spec

## Goal
Provide a local-first, dense browser homepage that keeps Linkwarden as the single source of truth for bookmarks and writes the user's Markdown note directly to the configured Obsidian file.

## User-approved behavior
- Modern, clean, responsive UI with a dense, wrapping bookmark grid.
- Per bookmark, independently show or hide name, icon, and description; size adapts to visible content.
- Reorder bookmark modules; moving a bookmark between groups updates its Linkwarden collection.
- Collections can be individually expanded or folded; retain layout preferences.
- Configure search engines and choose the active provider.
- Large Markdown editor with autosave to `F:\\文件\\Documents\\Obsidian仓库\\Obsidian-note\\0.01A-Day or Day\\浏览器首页笔记.md`.
- Detect Obsidian-side note edits while the homepage is open. Preserve both drafts and report a conflict if both sides changed from the same base.
- Homepage edits to bookmark name, URL, description, icon glyph, icon weight/color, and collection are written to Linkwarden. Linkwarden-side edits are refreshed by polling and on window focus.
- Local Windows Docker Compose deployment under `F:\\浏览器导航页`; homepage is loopback-only on port 3011. Existing Linkwarden remains at `http://localhost:3010`.

## Data ownership
- Linkwarden owns bookmark identity and fields: immutable link ID, URL, title/name, description, icon glyph/weight/color, collection, and website favicon derived from URL.
- Homepage SQLite owns presentation only: per-link visibility flags and order, per-collection order/collapse preference, search engine definitions, default engine, and app schema version.
- The Markdown note remains in the Obsidian file and is not copied into SQLite. Conflict copies may be stored under the homepage data directory.
- API keys stay server-side in a Docker secret file and never enter browser JS or browser storage.

## Synchronization and safety
- Use Linkwarden v2.16.3 API through a backend client. Fetch all pages; writes are idempotent and keyed by Linkwarden link ID.
- Poll Linkwarden on page focus and every 15 seconds; successful local edits update the UI immediately.
- Validate exact Host and Origin for state-changing homepage endpoints; bind published port to 127.0.0.1 and do not enable wildcard CORS.
- Render text as escaped text; allow only http/https bookmark destinations and approved Linkwarden icon fields.
- Bind-mount only the configured Obsidian Markdown file. Poll its content hash so Windows/Docker file notifications are not required. On overlapping edits, surface conflict actions and preserve a copy before write.

## MVP exclusions
- No user accounts or public access; single-user local service.
- No arbitrary uploaded bookmark icon images. Site favicons derive from URLs; supported Linkwarden glyph/icon color fields round-trip.
- No custom bookmark database; Linkwarden remains authoritative.
