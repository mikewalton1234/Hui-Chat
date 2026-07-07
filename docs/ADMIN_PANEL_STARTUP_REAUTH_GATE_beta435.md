# v0.11.0-beta.435 — Admin panel startup reauth gate

## Problem fixed

The injected admin panel asked for the current admin password, but the panel shell and live admin refreshes could still initialize behind the password dialog. Pressing **Cancel** closed only the dialog, leaving the admin workspace visible.

## New behavior

- The admin panel now starts in a locked startup state.
- Live admin data is not fetched until the current admin password is confirmed.
- Periodic admin refresh timers do not run before unlock.
- Admin body controls are visually locked and non-interactive while the password dialog is open.
- Pressing **Cancel** hides the panel and does not load live data.
- Reopening the panel with the admin hotkey or `window.ECAP.show()` asks for the password again until unlock succeeds.
- Manual admin actions are also guarded so they cannot run before startup unlock.

## Files changed

- `admin_panel_inject.py`
- `VERSION.txt`
- `docs/ADMIN_PANEL_STARTUP_REAUTH_GATE_beta435.md`
- `release_manifest_beta435_admin_panel_startup_reauth_gate.json`

## Validation performed

```bash
python -m py_compile admin_panel_inject.py
python -m compileall -q .
node --check /tmp/ecap_admin.js
for f in static/js/chat_parts/*.js static/js/*.js static/vendor/*.js /tmp/ecap_admin.js; do node --check "$f"; done
```

## Manual smoke test

1. Log in as an admin from a fresh browser session.
2. Open `/chat`.
3. Confirm the admin password dialog appears before live admin data loads.
4. Press **Cancel**.
5. Confirm the admin panel hides and no admin live data loads.
6. Reopen the admin panel with **Ctrl+Alt+P**.
7. Enter the current admin password.
8. Confirm rooms/users/stats/admin controls load only after successful confirmation.
