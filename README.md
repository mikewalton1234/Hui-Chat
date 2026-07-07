# Echo-Chat

Echo-Chat is a self-hosted Python chat server with browser chat rooms, private messages, group chat, admin tooling, file/media controls, room radio helpers, voice/webcam controls, and a guided setup wizard.

Current version: **0.11.0-beta.443-emoticon-path-type-fix**

### v0.11.0-beta.443 — Emoticon path type fix

This release fixes a beta.442 regression where `/emoticons/*.gif` could return HTTP 500 because the local emoticon route called `.is_file()` on the string returned by the shared safe-file helper. The emoticon route now normalizes safe file results back to `pathlib.Path` before self-test checks, MIME lookup, cache headers, and `send_file()`.

### v0.11.0-beta.442 — Emoticon cache hardening

This release fixes the follow-up issue where emoticons could still look like they were loading from the server on every chat-page visit. The catalog fetch no longer uses `cache: "no-store"`, `/api/emoticons/catalog` now returns cacheable ETag-backed JSON, and local emoticon image URLs include a file-derived `?v=` token so the browser can cache image assets long-term and only refresh them when the files change.


## beta.444 — Admin startup bridge + avatar fallback

- Fixed an injected admin panel startup bug where the unlock flow could throw `ReferenceError: refreshVoiceSettings is not defined`.
- Added an internal `adminRuntimeFns` bridge so runtime refresh calls can safely reach functions created inside the panel builder.
- Added a generated initials SVG fallback for stale `/media/avatars/...` database paths when the local uploaded file is missing.
- Added `tools/admin_startup_avatar_fallback_doctor.py` to catch both regressions.

## Highlights

- Flask + Flask-SocketIO browser chat server
- PostgreSQL-backed users, sessions, rooms, groups, moderation, and settings
- Modular frontend in `static/js/chat_parts/`
- Emoticon catalog and image assets warm up automatically when the chat page boots
- Room chat, private messages, group chat, friends, blocking, profiles, notifications, and missed-message handling
- Admin panel with moderation tools, diagnostics, room controls, and safety checks
- Optional room media features, file sharing controls, torrent-card helpers, voice/webcam controls, and radio/station helpers
- End-to-end encryption helpers for private/group flows and room-message encryption support
- Setup wizard, migration utilities, preflight checks, deployment examples, and operational doctors

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --setup
python main.py --production
```

Open the app locally:

```text
http://localhost:5000
```

For a local Linux/PostgreSQL example, see [`docs/SETUP_EXAMPLES.md`](docs/SETUP_EXAMPLES.md).

## Common commands

```bash
python main.py --setup
python main.py --preflight
python main.py --list-migrations
python main.py --migrate
python main.py --schema-version
python tools/config_doctor.py --config server_config.json
python tools/service_smoke.py --url http://127.0.0.1:5000
python tools/log_sanity.py
```

## Repository layout

```text
main.py                     Main server entry point
server_init.py              Flask/Socket.IO application bootstrap
interactive_setup.py        Guided setup wizard
constants.py                Version, paths, and frontend manifest
routes_*.py                 Flask route modules
socket_handlers.py          Socket.IO event handlers
realtime/                   Realtime helper modules
db/                         Database helpers and bootstrap logic
migrations/                 Database migration files
templates/                  Jinja HTML templates
static/css/                 Stylesheets and responsive layout CSS
static/js/chat_parts/       Modular frontend runtime source files
static/vendor/              Local browser vendor assets
tools/                      Setup, migration, smoke-test, SMTP, and diagnostic helpers
scripts/                    Production install/run and database maintenance scripts
deploy/                     Example systemd, nginx, and Caddy files
docs/                       Project documentation
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — codebase structure and runtime layout
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — config files, environment overrides, and settings
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — HTTPS, Redis, Socket.IO topology, Gunicorn, SMTP, and media notes
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — local setup and frontend workflow
- [`docs/FEATURES.md`](docs/FEATURES.md) — feature inventory
- [`docs/FRONTEND_STRUCTURE.md`](docs/FRONTEND_STRUCTURE.md) — chat frontend split-file workflow
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — migrations, preflight checks, janitor tasks, and repair helpers
- [`docs/SECURITY.md`](docs/SECURITY.md) — secrets handling, cookies, rate limits, and security notes
- [`docs/STUN_TURN_SETUP.md`](docs/STUN_TURN_SETUP.md) — WebRTC/STUN/TURN configuration notes
- [`docs/UPGRADE_ROLLBACK.md`](docs/UPGRADE_ROLLBACK.md) — upgrade and rollback guidance

## Files that should stay local

Do not commit runtime secrets, generated databases, logs, uploads, or local server configuration.

```text
server_config.json
settings.json
.env
*.pem
*.key
secrets.json
logs/
uploads/
downloads/
instance/
*.sqlite
*.sqlite3
*.db
```

Safe templates are included instead:

```text
.env.example
server_config.example.json
settings.example.json
```

## Production scaling note

Echo-Chat uses Socket.IO. For production scaling, use **one Gunicorn worker per instance**. To scale horizontally, run multiple one-worker Echo-Chat instances behind sticky reverse-proxy routing with Redis Socket.IO queue enabled.

## License

See [`LICENSE`](LICENSE).
