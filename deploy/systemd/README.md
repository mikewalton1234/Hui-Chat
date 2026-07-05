# Echo-Chat systemd deployment

This folder contains ready-to-edit Echo-Chat project service files and an env template. The unit filenames intentionally stay `echochat-*` for stable deployment compatibility; your public chat server name still comes from `server_config.json -> server_name`. You may edit the systemd `Description=` lines if you want local service output to show your custom server name.

## Recommended layout

- Project: `/opt/echochat/Echo-Chat-main`
- Venv: `/opt/echochat/Echo-Chat-main/.venv`
- Env file: `/etc/echochat/echochat.env`
- User: `echochat`

## Install steps (Arch)

### 1) Create a dedicated user

```bash
sudo useradd -r -s /usr/bin/nologin -d /opt/echochat echochat
```

### 2) Put Echo-Chat under /opt

```bash
sudo mkdir -p /opt/echochat
sudo chown -R echochat:echochat /opt/echochat
```

Copy your repo contents to `/opt/echochat/` and ensure:

- `/opt/echochat/Echo-Chat-main` exists

### 3) Create venv + install deps

```bash
cd /opt/echochat/Echo-Chat-main
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 4) Install Redis (optional but recommended)

```bash
sudo pacman -S redis
sudo systemctl enable --now redis
```

### 5) Install env file

```bash
sudo mkdir -p /etc/echochat
sudo cp deploy/systemd/echochat.env.example /etc/echochat/echochat.env
sudo chown root:echochat /etc/echochat/echochat.env
sudo chmod 640 /etc/echochat/echochat.env
```

The service user must be able to read the EnvironmentFile, but the file should not be world-readable because it contains secrets. Edit `/etc/echochat/echochat.env` and set real values.

### 6) Install a unit file

You have two options:

1) **Single-process dev-ish** (`python main.py`) — simplest.
2) **Production** (`Gunicorn gthread, one worker per instance`) — recommended.

#### Option A: python main.py

```bash
sudo cp deploy/systemd/echochat.service /etc/systemd/system/echochat.service
sudo systemctl daemon-reload
sudo systemctl enable --now echochat
```

#### Option B: Gunicorn gthread, one worker per instance

Keep every Gunicorn process at one worker. If you scale with `echochat@5000`, `echochat@5001`, etc., use Redis Socket.IO queue plus sticky reverse-proxy routing.

```bash
sudo cp deploy/systemd/echochat-gunicorn.service /etc/systemd/system/echochat-gunicorn.service
sudo cp deploy/systemd/echochat-janitor.service /etc/systemd/system/echochat-janitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now echochat-gunicorn
sudo systemctl enable --now echochat-janitor
```

## Logs

```bash
journalctl -u echochat -f
journalctl -u echochat-gunicorn -f
journalctl -u echochat-janitor -f
```

## Common tweaks

- If you installed Echo-Chat somewhere else, update `WorkingDirectory=`, the Python path, the config path in each `ExecStartPre=`, and the config path in `ExecStart=`.
- Keep the generated/static `ExecStartPre=` checks. They catch bad config and unsafe Redis/Socket.IO topology before systemd starts the web process.
- If you do NOT want config persistence at all, keep `ECHOCHAT_PERSIST_SECRETS=0` and remove `ReadWritePaths=.../server_config.json`.
- If you move upload, private upload, export, or instance folders outside the project root, add matching `ReadWritePaths=` entries or generate a settings-specific deployment kit.


## Multiple one-worker instances

For Socket.IO scaling, keep each Gunicorn server at one worker and run multiple instances with the template service:

```bash
sudo cp deploy/systemd/echochat@.service /etc/systemd/system/echochat@.service
sudo systemctl daemon-reload
sudo systemctl enable --now echochat@5000 echochat@5001
```

For 10 instances, start `echochat@5000` through `echochat@5009`, configure Redis Socket.IO queue, and put a sticky reverse proxy in front of those backend ports. Keep only one `echochat-janitor.service` active for the whole deployment.

Before enabling services, run the static checks from the project root:

```bash
python tools/deployment_ops_doctor.py
python tools/deployment_ops_deep_doctor.py
```
