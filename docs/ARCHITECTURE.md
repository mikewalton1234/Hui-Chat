# Architecture

Version: **0.11.0-beta.317**

## High-level stack

- **Backend:** Flask, Flask-SocketIO, Flask-JWT-Extended, Flask-Limiter
- **Database:** PostgreSQL
- **Realtime:** Socket.IO
- **Client runtime:** ordered browser scripts from `static/js/chat_parts/`
- **Optional integrations:** Redis, Twilio Verify, SMTP relay providers

## Runtime entrypoints

- `main.py` handles CLI flags, setup, config load, env overrides, migrations, preflight, and server boot
- `server_init.py` wires the Flask app, Socket.IO runtime, cookies, CORS, rate limiting, routes, and diagnostics
- `wsgi.py` provides the Gunicorn/import entrypoint

## Backend module layout

### Core
- `constants.py` — versioning, DSN helpers, chat-part manifest
- `interactive_setup.py` — setup wizard and default settings
- `database.py` — compatibility facade over the database layer
- `preflight.py` — runtime diagnostics and startup checks
- `security.py`, `permissions.py`, `secrets_policy.py` — security and RBAC helpers

### Routes
- `routes_auth.py` — login, registration, refresh, password reset, 2FA flows
- `routes_main.py` — chat page and general application endpoints
- `routes_chat.py` — room/chat invite and room-related endpoints
- `routes_groups.py` — group chat and group invite endpoints
- `routes_admin_tools.py` — admin APIs and settings endpoints
- `routes_media.py` — Echo media mode and webcam policy routes

### Realtime
- `socket_handlers.py` — Socket.IO handlers and event wiring
- `realtime/` — subsystem helpers for rooms, groups, DMs, files, presence, admin, voice, and anti-abuse utilities

### Data and schema
- `migrations/` — tracked schema migrations
- `chat_rooms.json` — room metadata/bootstrap content

## Frontend layout

The browser client is split into ordered source files under `static/js/chat_parts/`. The active manifest lives in `constants.CHAT_SCRIPT_PARTS`, and `templates/chat.html` loads those files directly.

That means the source-of-truth for the browser runtime is **not** a hand-edited monolithic bundle.

Important files:

- `templates/chat.html` — page shell and script-tag loading
- `static/js/chat_parts/` — ordered runtime modules

## Storage and state model

- PostgreSQL is the durable system of record
- some realtime/session structures are process-local unless Redis-backed shared topology is enabled
- multiple-instance correctness for cross-process realtime delivery depends on a Socket.IO message queue such as Redis

## Current architectural constraints

- the repo is still optimized for iterative development rather than a finalized public release
- some runtime config files are still present in-tree and will need cleanup before release
- test cleanup is still pending
