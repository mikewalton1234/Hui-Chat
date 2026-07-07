# v0.11.0-beta.441 — Emoticon boot preload

## Goal

When a user reaches the authenticated chat page, Echo-Chat should start loading the classic emoticon catalog immediately instead of waiting for the first emoticon-picker click. The picker should feel ready when the user opens it, and typed shortcuts should render as image emoticons as soon as chat messages appear.

## What changed

- `static/js/chat_parts/0008_emoji_picker.js` now exposes `ecPrimeEmoticonsOnChatBoot()`.
- The chat bundle calls the boot primer immediately when the emoticon module loads.
- `static/js/chat_parts/0048_boot_presence_dom.js` calls the primer again during normal DOM boot. The helper is idempotent, so duplicate calls reuse the same catalog/image preload work.
- The first phase fetches `/api/emoticons/catalog` immediately.
- The second phase preloads emoticon image assets during browser idle time with bounded concurrency.
- The browser keeps the existing picker behavior as a fallback if boot loading fails or the admin disables boot preload.

## New client settings

These settings are passed through `window.ECHOCHAT_CFG`:

- `emoticons_boot_preload_enabled` — default `true`.
- `emoticons_boot_preload_limit` — default `180`, clamped from `0` to `240`. Use `0` for catalog-only boot loading.
- `emoticons_boot_preload_concurrency` — default `4`, clamped from `1` to `8`. Phone/mobile hints default to a lower runtime fallback.

## Files changed

- `static/js/chat_parts/0008_emoji_picker.js`
- `static/js/chat_parts/0048_boot_presence_dom.js`
- `routes_auth.py`
- `routes_admin_tools.py`
- `interactive_setup.py`
- `server_config.example.json`
- `settings.example.json`
- `README.md`
- `VERSION.txt`
- `tools/emoticon_boot_preload_doctor.py`

## Validation

Run:

```bash
python tools/emoticon_boot_preload_doctor.py
node --check static/js/chat_parts/0008_emoji_picker.js
node --check static/js/chat_parts/0048_boot_presence_dom.js
python -m py_compile routes_auth.py routes_admin_tools.py interactive_setup.py
```

## Manual test

1. Start the server.
2. Log in and open `/chat`.
3. Open the browser Network tab.
4. Confirm `/api/emoticons/catalog` is requested during page boot, not only after clicking the emoticon button.
5. Wait a second, then click the emoticon button. The picker should populate without the old first-click delay.
