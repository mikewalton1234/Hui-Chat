# v0.11.0-beta.442 — Emoticon cache hardening

## Problem

Beta.441 correctly started emoticon loading during chat-page boot, but it still made the emoticon system look like it was loading from the server every time because the catalog request used `cache: "no-store"` and the local `/emoticons/<file>` route only sent a short one-hour cache window.

## Fix

- `/api/emoticons/catalog` is now ETag-backed and browser-cacheable with a private cache directive.
- The browser fetch for the catalog now uses `cache: "force-cache"` instead of `cache: "no-store"`.
- Local emoticon image URLs now include a content-derived `?v=<mtime-size>` token.
- Versioned `/emoticons/<file>?v=...` responses now use `Cache-Control: public, max-age=31536000, immutable`.
- Non-versioned `/emoticons/<file>` responses still cache for a safer fallback window.
- `/static/emoticons/...?...v=` fallback assets now get immutable static caching too.
- Added `emoticons_catalog_cache_seconds` with a default of `86400`.

## Expected behavior

The first visit to `/chat` loads the catalog and emoticon images. Later visits should read the catalog and images from the browser cache unless:

- the app version changes,
- the local emoticon file changes,
- the browser cache is disabled,
- the user hard-refreshes with cache bypass, or
- the admin sets `emoticons_catalog_cache_seconds` to `0`.

## Validation

```bash
python tools/emoticon_cache_hardening_doctor.py
python tools/emoticon_boot_preload_doctor.py
python tools/emoticon_catalog_doctor.py
node --check static/js/chat_parts/0008_emoji_picker.js
python -m py_compile routes_main.py routes_admin_tools.py interactive_setup.py emoticon_catalog.py server_init.py
```
