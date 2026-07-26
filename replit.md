# Hui Chat

Self-hosted Python/Flask chat server with rooms, private messages, group chat, admin tools, file/media sharing, voice/webcam (WebRTC), and more.

**Version:** 0.11.0-beta.459

## How to run

The **Start application** workflow runs the server:

```
python3 main.py
```

Serves on port 5000. The Replit preview pane opens the login page automatically.

## Key files

| File | Purpose |
|---|---|
| `main.py` | Server entrypoint — parses args, bootstraps DB, starts Flask/Socket.IO |
| `server_config.json` | Runtime config (CORS, features, limits) |
| `.env` | Generated cryptographic secrets (SECRET_KEY, JWT_SECRET_KEY, Fernet/encryption keys) |
| `replit_bootstrap.py` | One-shot bootstrap script for initial DB schema + admin user creation |
| `db/schema.py` | Full PostgreSQL schema + migration helpers |
| `db/auth.py` | User creation, key generation, auth helpers |
| `interactive_setup.py` | CLI setup wizard (legacy prompt mode: `HUI_SETUP_LEGACY=1 python3 main.py --setup`) |

## Database

Uses Replit's managed PostgreSQL (`DATABASE_URL` env var — set automatically).
Schema is applied via tracked migrations on every boot.

## Secrets

All cryptographic secrets are stored in `.env` and loaded at startup via `env_loader.py`.
Do **not** commit `.env`, `server_config.json`, `*.sqlite`, `logs/`, or `uploads/`.

## Re-running bootstrap (e.g. after a DB reset)

Set the admin credentials as Replit Secrets first:

```
HUI_ADMIN_USER=<username>
HUI_ADMIN_PASS=<password>
HUI_ADMIN_PIN=<4-8 digit PIN>
```

Then run:

```
python3 replit_bootstrap.py
```

## Re-running setup

To change server settings interactively:

```
HUI_SETUP_LEGACY=1 python3 main.py --setup
```

To regenerate secrets:

```
python3 main.py --generate-secrets --write-env-secrets
```
