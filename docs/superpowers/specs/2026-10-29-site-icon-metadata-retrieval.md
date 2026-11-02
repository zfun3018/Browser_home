# Website Icon and Metadata Retrieval Design

## Goal

Improve the success rate of website icons and descriptions on the homepage while keeping fetches local, cached, and protected against requests to localhost or private networks.

## Current limitations

- The frontend asks Linkwarden for one favicon using only the bookmark origin. Sites that declare an icon at a custom path, in a web app manifest, or as an Apple touch icon may not be covered.
- The metadata endpoint reads one static HTML response and supports only common description meta tags. It cannot use descriptions embedded in JSON-LD.
- The frontend's refresh button currently refreshes metadata and changes the favicon cache-busting value, but does not own a local icon cache or retrieval strategy.

## Design

### Icon retrieval

Add a same-origin homepage endpoint `GET /api/links/{id}/icon`. It retrieves the canonical bookmark URL from Linkwarden, then checks a persistent cache under the homepage data directory. Cache keys are hashes of the canonical URL, and cached entries live for seven days. `?refresh=1` bypasses an existing entry.

On a cache miss or forced refresh, a backend asset service fetches the public page and examines icon candidates in this order: HTML `rel=icon`/`rel=shortcut icon`, web app manifest icons, Apple touch icons, and `/favicon.ico`. Relative paths resolve against the page or manifest URL. Each redirect and each target host is checked for public IP addresses. Fetches have time, redirect, and byte limits. Only supported image responses are cached; invalid or missing icons return 404 so the frontend can show the Linkwarden glyph.

The frontend displays the local endpoint image first, with lazy loading. A manual refresh uses `?refresh=1`, clearing the component's failed state. Linkwarden's icon glyph remains the fallback.

### Description retrieval

Extend the existing safe HTML parser to also recognize JSON-LD `description` fields. Preserve current precedence: `og:description`, `description`, then `twitter:description`; structured data fills in only when those are absent. Do not execute JavaScript or send bookmarked URLs to a third-party metadata service. Automatic refresh continues to fill only empty descriptions; manual refresh continues to ask before overwriting an existing description.

### Security and failure behavior

- Allow only HTTP and HTTPS URLs without user information.
- Resolve and reject any non-public target address before each request, including redirects and icon URLs.
- Keep strict response size, timeout, and redirect limits.
- Cache only validated image data. Derive cache filenames from a cryptographic hash, never from a URL path.
- A retrieval failure does not break the bookmark list or description refresh. The frontend falls back to the Linkwarden glyph.

## Components

- `backend/app/site_assets.py`: safe page/icon retrieval, local cache, and cache refresh behavior.
- `backend/app/metadata.py`: metadata and JSON-LD extraction, reusing the same public-address validation rules.
- `backend/app/main.py`: same-origin icon endpoint using Linkwarden's canonical URL.
- `frontend/src/domain.ts`: same-origin icon URL helper.
- `frontend/src/App.tsx`: icon endpoint rendering, forced refresh, lazy loading, and glyph fallback.

## Acceptance criteria

1. A site declaring an icon at a custom HTML path can show that icon.
2. A site with no declared icon can fall back to `/favicon.ico`.
3. A web app manifest icon can be used when earlier sources are unavailable.
4. A normal page description keeps the existing metadata precedence; JSON-LD can supply a description if meta descriptions are absent.
5. A manual icon refresh bypasses the seven-day cache and changes the displayed icon when the origin changes it.
6. Private or loopback targets remain blocked, including redirects and manifest icon targets.
7. Failed icon retrieval leaves the bookmark usable and shows its Linkwarden glyph.

## Tradeoffs

Dynamic sites that expose their description only after JavaScript execution remain unsupported. Running a browser engine for arbitrary bookmarks would add resource cost and a broader attack surface. Third-party scraping services are excluded to avoid disclosing saved URLs.
