# v0.11.0-beta.434 — End-user GUI Socket.IO boot fix

## Problem fixed

The chat page could render the shell but leave the end-user GUI empty because the packaged `static/vendor/socket.io.min.js` file was only a placeholder. That made `window.io` undefined. The first chat bootstrap module then threw before it could define `socket` and `currentUser`, so every later module failed with errors like:

- `ReferenceError: io is not defined`
- `ReferenceError: socket is not defined`
- `ReferenceError: currentUser is not defined`

Once that happened, the room browser, friends list, group list, presence widgets, and missed-message widgets never finished booting.

## What changed

- Replaced the local Socket.IO placeholder with a stable bootstrap loader that pulls the official Socket.IO browser bundle when a real local bundle has not been installed.
- Added a synchronous fallback loader in `templates/chat.html` before deferred chat modules run.
- Added `https://cdn.socket.io` to the default Content Security Policy so the fallback is allowed.
- Hardened `0001_core_socket_crypto.js` so a missing Socket.IO browser client no longer crashes the entire front end. If the bundle is blocked/offline, the GUI can still continue booting in limited HTTP-only mode instead of going blank.
- Exposed `window.socket` for modules that check the socket through `window.socket`.

## Files changed

- `VERSION.txt`
- `static/vendor/socket.io.min.js`
- `templates/chat.html`
- `server_init.py`
- `static/js/chat_parts/0001_core_socket_crypto.js`
- `docs/ENDUSER_GUI_SOCKETIO_BOOT_FIX_beta434.md`
- `release_manifest_beta434_enduser_gui_socketio_boot_fix.json`

## Verification

Run:

```bash
python -m compileall -q .
for f in static/js/chat_parts/*.js static/vendor/socket.io.min.js; do node --check "$f"; done
```

Then start the server, hard-refresh `/chat`, and confirm the console no longer starts with `io is not defined` / cascading `socket is not defined` errors.
