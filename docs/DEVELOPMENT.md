# Development

Version: **0.11.0-beta.317**

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Prepare PostgreSQL, then either export `DATABASE_URL` or create a local `server_config.json` from `server_config.example.json`.

```bash
cp server_config.example.json server_config.json
python main.py --setup
```

Run the server:

```bash
python main.py
```

Run production mode locally:

```bash
python main.py --production
```

## Frontend workflow

The browser runtime is loaded from `static/js/chat_parts/` through the explicit manifest in `constants.py`.

Practical rule:

- edit the relevant `chat_parts` file first
- keep `constants.CHAT_SCRIPT_PARTS` aligned with files loaded by `templates/chat.html`
- keep vendor assets in `static/vendor/` if the frontend references them directly

## Recommended checks before committing

```bash
python main.py --preflight
python main.py --list-migrations
python main.py --schema-version
python tools/config_doctor.py --config server_config.json
python tools/service_smoke.py --url http://127.0.0.1:5000
python tools/log_sanity.py
```

Do not commit local runtime files, generated caches, databases, uploaded files, logs, or secrets.
