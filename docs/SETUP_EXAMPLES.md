# Echo-Chat working setup examples

These examples are intentionally copy/paste friendly for a local Linux/Arch/PostgreSQL setup.

## Beginner local PostgreSQL setup

From the Echo-Chat project folder:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Create a local .env that matches your Linux username.
cat > .env <<EOF
DATABASE_URL=postgresql://$USER@localhost:5432/echochat
ECHOCHAT_DB_BOOTSTRAP_URL=postgresql://$USER@localhost:5432/postgres
ECHOCHAT_RUN_MODE=development
ECHOCHAT_PRODUCTION_WORKERS=1
EOF

python main.py --setup
python main.py --development
```

## Local production smoke test

Use this only after setup works in development mode:

```bash
source .venv/bin/activate
export DATABASE_URL=postgresql://$USER@localhost:5432/echochat
export ECHOCHAT_DB_BOOTSTRAP_URL=postgresql://$USER@localhost:5432/postgres
export ECHOCHAT_PRODUCTION_WORKERS=1
python main.py --production
```

## Worker rule

For the built-in Echo-Chat Gunicorn runner, keep:

```bash
ECHOCHAT_PRODUCTION_WORKERS=1
```

Do not set `production_workers=10` in one Gunicorn server for Socket.IO. Scale later by running multiple one-worker Echo-Chat instances on different ports behind a sticky reverse proxy and Redis Socket.IO message queue.

## Why passwordless local DSNs are allowed in config

A local DSN like this does not contain a password:

```text
postgresql://your_linux_username@localhost:5432/echochat
```

Echo-Chat keeps that kind of DSN in `server_config.json` so setup can restart correctly. DSNs with embedded passwords are still treated as secrets and should be stored in `.env` or environment variables.

## Ten-instance production plan

Use this only after Redis and sticky reverse-proxy routing are configured. The important rule is still one worker per instance:

```bash
export ECHOCHAT_PRODUCTION_WORKERS=1
export ECHOCHAT_PRODUCTION_INSTANCES=10
export ECHOCHAT_INSTANCE_BASE_PORT=5000
export ECHOCHAT_SOCKETIO_MESSAGE_QUEUE=redis://127.0.0.1:6379/1
python main.py --write-deployment-kit --deployment-kit-output-dir deploy/generated-deployment
```

That plan means 10 separate backends, usually ports 5000-5009, each running one worker. Your reverse proxy must keep each browser stuck to the same backend while Socket.IO is connected.
