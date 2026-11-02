# Website Icon and Metadata Retrieval Implementation Plan

> **For agentic workers:** Implement task-by-task in this workspace, preserving unrelated existing modifications.

**Goal:** Improve website icon and description retrieval using a local multi-source fetcher and persistent cache.

**Architecture:** A backend service validates public URLs, extracts icon candidates, and caches validated icon bytes under the homepage data directory. A same-origin route serves those bytes and supports forced refresh; the existing metadata route gains JSON-LD fallback. The frontend requests the same-origin icon and retains Linkwarden glyph fallback.

**Tech Stack:** Python 3.12, FastAPI, httpx, Python standard-library HTML/JSON parsers; React, TypeScript, Vite.

**Spec:** `docs/superpowers/specs/2026-10-29-site-icon-metadata-retrieval.md`

## Global Constraints

- Only fetch HTTP and HTTPS URLs without credentials.
- Reject any resolved address that is not globally routable on each request and redirect.
- Bound network time, response bytes, and redirect count.
- Keep metadata and icons local; do not add third-party scraping services or browser execution.
- Keep automatic descriptions fill-empty-only and manual overwrite confirmation.
- Do not add production dependencies.

---

### Task 1: Share secure public fetch primitives

**Files:**
- Modify: `backend/app/metadata.py`
- Create: `backend/app/site_assets.py`

**Interfaces:**
- `metadata.py` exposes `assert_public_url(url: str) -> None` as an async validator and retains `fetch_public_metadata(raw_url) -> dict[str, str]`.
- `site_assets.py` exposes `SiteAssetService(cache_dir: Path)` and `async get_icon(raw_url: str, refresh: bool = False) -> tuple[bytes, str]`.

- [x] Extract the existing scheme, credential, DNS, and public-IP validation from `_assert_public_host` into `assert_public_url`; call it before each page request and redirect.
- [x] Implement a bounded HTTP GET helper in `site_assets.py` using `httpx.AsyncClient(follow_redirects=False, trust_env=False)`, 8-second total timeout, 4-second connect timeout, at most five redirects, and 1 MB HTML / 512 KB image bodies.
- [x] Parse HTML icon links with `HTMLParser`; collect `rel=icon`, `rel=shortcut icon`, `rel=apple-touch-icon`, and manifest links, ordered standard icon first, then manifest, Apple, and `/favicon.ico`.
- [x] Fetch a manifest only after validating its URL; parse JSON `icons[].src`; resolve candidates against the final manifest URL and validate each candidate before fetching.
- [x] Accept common raster and SVG image responses, including correctly sniffed icon files served as `application/octet-stream`; reject other content types, oversized responses, and empty bodies.
- [x] Store cache metadata and bytes using `sha256(canonical_url)` filenames under `cache_dir`; keep successful results for seven days and make `refresh=True` bypass the cache. Do not cache failures.
- [x] Return an explicit not-found service error when no candidate yields a valid image.

### Task 2: Expand page description parsing

**Files:**
- Modify: `backend/app/metadata.py`

**Interfaces:**
- `fetch_public_metadata(raw_url: str) -> dict[str, str]` continues returning title and description.

- [x] Extend the parser to collect JSON-LD script blocks and safely parse JSON objects and arrays.
- [x] Preserve meta description priority (`og:description`, `description`, `twitter:description`); use the first non-empty JSON-LD `description` only if those are absent.
- [x] Keep current size limits and preserve empty results on ordinary HTTP/HTML parse failure.

### Task 3: Serve icons through the homepage API

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/config.py` only if it already owns data-directory-derived paths.

**Interfaces:**
- Add `GET /api/links/{identity}/icon?refresh=0|1` returning validated bytes and their image media type.

- [x] Initialize `SiteAssetService` with `settings.data_dir / "site-icons"` in `create_app`.
- [x] Add the icon route, reject requests during import, look up the canonical link in Linkwarden, and return image bytes with `X-Content-Type-Options: nosniff`, a restrictive CSP, and a cache lifetime no longer than the application cache lifetime.
- [x] Map missing/invalid icons to 404 and safe-fetch errors to a concise 422; do not expose local file paths or raw internal exceptions.
- [x] Keep the existing metadata route behavior and error mapping.

### Task 4: Render and refresh local icons

**Files:**
- Modify: `frontend/src/domain.ts`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- `siteFaviconUrl` is replaced or supplemented by a same-origin `siteIconUrl(linkId, refreshRevision?)` helper.

- [x] Point `<img>` to `/api/links/{id}/icon`; append a unique refresh parameter after the user presses the existing refresh control.
- [x] Set `loading="lazy"` and keep the current component error fallback to the Linkwarden glyph.
- [x] Keep the refresh action independent of metadata success: force the icon URL revision in `finally`, including when metadata fetch is rejected or yields no description.
- [x] Ensure a forced refresh resets image failure state and retries the image request.

### Task 5: Verify and deploy

**Files:**
- Modify: `docs/deployment.md` only if the persistent icon cache path needs operator documentation.

- [x] Run backend syntax/import checks and frontend production build.
- [x] Exercise the live same-origin icon endpoint for three public bookmarks and a forced refresh; each returned HTTP 200 with an image content type.
- [x] Rebuild and restart only the homepage service, then confirm `/health` is healthy.
- [ ] Independently verify JSON-LD-only metadata pages and a private-address redirect in a controlled local integration environment.
