# Deployment

Version: **0.11.0-beta.317**


## Naming and branding in deployments

Use **Echo-Chat** for the installed software/project path, service filenames, and environment-variable prefixes. Use `server_config.json -> server_name` for the public name of the chat server your users see. For example, keeping `echochat.service` while setting `server_name` to `Family Room` is correct.

## HTTPS

For anything beyond localhost development, run Echo-Chat behind HTTPS.

This matters for:

- secure cookies
- WebCrypto-dependent flows
- browser trust and account/session safety

Helper scripts included in the repo:

```bash
bash tools/enable_https_selfsigned.sh
bash tools/enable_https_mkcert.sh
```

## Public beta readiness

For real internet testing, run this before exposing the server:

```bash
python main.py --public-beta-check
```

For the setup GUI/TUI flow:

```bash
python main.py --setup
```

Then open **Hosting, proxy, and HTTPS** or the easy guided setup path and choose a hosting profile:

- `lan` keeps local/mobile testing friendly.
- `no_domain_yet` keeps a safe waiting-room profile until you have a real HTTPS address.
- `public_beta` applies safer defaults for domain, Dynamic DNS + HTTPS, or stable HTTPS tunnel beta hosting.
- `advanced` keeps custom reverse-proxy values under admin control.

The public beta preset expects a reverse proxy such as Caddy or Nginx to terminate HTTPS. Echo-Chat should normally listen on a private/local backend port while public traffic reaches only 80/443 at the proxy.

## Reverse proxy config generator

Generate ready-to-review Caddy and Nginx configs from your saved `server_config.json`:

```bash
python main.py --generate-proxy-config all
```

Generate only one proxy style:

```bash
python main.py --generate-proxy-config caddy
python main.py --generate-proxy-config nginx
```

Write to a custom folder:

```bash
python main.py --generate-proxy-config all --proxy-output-dir deploy/generated-proxy
```

The generated Nginx config includes the `/socket.io/` location, `Upgrade` and `Connection` headers, proxy buffering disabled for Socket.IO, forwarded protocol/host headers, and a HTTPS redirect server block. The generated Caddyfile uses Caddy's automatic HTTPS and reverse proxy support.

After copying a generated config into Caddy or Nginx, run:

```bash
python main.py --public-beta-check
```

For public beta, keep PostgreSQL, Redis, and the raw Echo-Chat backend port private. Only the reverse proxy should be public on ports 80 and 443.

## Production deployment plan and kit

Print a settings-aware deployment plan:

```bash
python main.py --deployment-plan
```

Validate optional Dynamic DNS settings before relying on a home/public-IP hostname:

```bash
python main.py --dynamic-dns-check
```

Send one Dynamic DNS update only when the provider settings are correct:

```bash
python main.py --dynamic-dns-update
```

Write a reviewable deployment kit:

```bash
python main.py --write-deployment-kit --deployment-kit-output-dir deploy/generated-deployment
```

The generated kit includes:

- `echochat.service` for systemd single-instance production startup
- `echochat.env.example` for secrets and production overrides
- Caddy/Nginx proxy templates under `proxy/`
- `deployment-plan.txt`
- `public-beta-readiness.txt`
- `redis-socketio-check.txt`
- `install-commands.sh` with copy/install commands to review before use

Use a nested output folder such as `deploy/generated-deployment`. The generator refuses unsafe targets like the Echo-Chat source root because the kit writes generic filenames such as `README.md`, `echochat.service`, and `install-commands.sh`.

The generated `echochat.env.example` includes placeholders for production-only secrets, including database/JWT secrets, SMTP, Twilio/SMS 2FA, Dynamic DNS, and WebRTC TURN credentials. Replace every `CHANGE_ME` value before installing it as the systemd EnvironmentFile.

The generated systemd unit deliberately uses the beginner-safe production path: one Gunicorn process, `gthread`, Flask-SocketIO `threading`, and Redis-backed rate limits for public beta. Scale later by adding multiple one-worker instances behind sticky routing rather than starting with unsafe multi-worker Gunicorn for Socket.IO.

Generated systemd services also run lightweight startup gates before the server process starts: `tools/config_doctor.py` checks config shape without needing PostgreSQL, and `main.py --redis-socketio-check` blocks unsafe Socket.IO worker/instance topology. The generated install commands create and chown runtime-writable folders such as `logs/`, `uploads/`, `private_uploads/`, `instance/`, and configured private upload roots. Keep only one `echochat-janitor.service` running, even when multiple `echochat@PORT` web instances are enabled.

## Socket.IO topology

Single-process development and the built-in one-worker production runner can run without a Socket.IO Redis queue. Echo-Chat will not attach the Socket.IO server to a configured Redis queue in a one-worker runtime unless you explicitly set `ECHOCHAT_FORCE_SOCKETIO_REDIS_QUEUE=1`. This avoids noisy Redis pub/sub reconnect loops on local test servers.

For multiple one-worker instances, use a shared Socket.IO message queue such as Redis. Without it, cross-process emits for rooms, invites, DMs, and presence can fail or appear inconsistent.

For the default Gunicorn `gthread` runner, Echo-Chat now advertises `polling` as the browser transport by default. This is intentionally conservative and prevents the client from repeatedly trying a WebSocket-first connection on deployments that are not WebSocket-ready. Advanced deployments can opt into WebSocket transport with `socketio_transports` or `ECHOCHAT_SOCKETIO_TRANSPORTS`.

Relevant settings:

- `socketio_message_queue`
- `ECHOCHAT_FORCE_SOCKETIO_REDIS_QUEUE=1` when a one-worker process intentionally needs external Socket.IO emitters
- `shared_state_redis_url`
- `shared_state_prefix`
- `shared_state_session_ttl_seconds`

## Gunicorn and worker model

The repository includes `gunicorn_conf.py`. For serious deployment, verify that:

- the selected async model matches the Flask-SocketIO runtime mode
- Redis is configured when using multiple one-worker instances
- the janitor is not duplicated across every worker process

## SMTP deployment

SMTP is intended to use a third-party relay provider. Typical required settings are:

- `smtp_enabled`
- `smtp_host`
- `smtp_port`
- `smtp_username`
- `smtp_password`
- `smtp_use_starttls`
- `smtp_from`

For production, prefer environment variables over plaintext config. Production/public mode does not persist secrets back into `server_config.json` by default. Leave `ECHOCHAT_PERSIST_SECRETS` unset or set it to `0`; only set it to `1` when you intentionally want legacy config-file secret persistence.

Example environment setup for Brevo on Linux:

```bash
export ECHOCHAT_PUBLIC_BASE_URL="https://chat.yourdomain.com"
export ECHOCHAT_SMTP_ENABLED="true"
export ECHOCHAT_SMTP_HOST="smtp-relay.brevo.com"
export ECHOCHAT_SMTP_PORT="587"
export ECHOCHAT_SMTP_USERNAME="YOUR_BREVO_SMTP_LOGIN"
export ECHOCHAT_SMTP_PASSWORD="YOUR_BREVO_SMTP_KEY"
export ECHOCHAT_SMTP_STARTTLS="true"
export ECHOCHAT_SMTP_SSL="false"
export ECHOCHAT_SMTP_FROM="Echo-Chat <no-reply@example.com>"
```

For Resend, use `smtp.resend.com`, port `465`, `ECHOCHAT_SMTP_USERNAME="resend"`, `ECHOCHAT_SMTP_SSL="true"`, and the Resend API key as `ECHOCHAT_SMTP_PASSWORD`.

After setting SMTP, test without creating reset tokens:

```bash
python tools/smtp_test.py --config server_config.json --to you@example.com
```

## Echo media / webcam

Echo-Chat uses its built-in browser WebRTC path for room voice and webcam controls. Confirm these runtime settings before public deployment:

- `av_mode` is `echo` for webcam controls or `standard` for voice-only mode
- `webcam_enabled` matches the intended room camera policy
- `webcam_approval_mode` is set to `owner_approval`, `open`, or `disabled`
- `webcam_max_viewers` is `0` for unlimited or a positive cap per camera owner
- `default_media_policy` matches the desired end-user default

## Twilio Verify deployment

SMS 2FA is optional and disabled by default. In production, provide Twilio credentials via `ECHOCHAT_TWILIO_ACCOUNT_SID`, `ECHOCHAT_TWILIO_AUTH_TOKEN`, and `ECHOCHAT_TWILIO_VERIFY_SERVICE_SID`.

When enabled, confirm:

- `enable_two_factor_beta`
- `enable_sms_two_factor`
- `twilio_account_sid`
- `twilio_auth_token`
- `twilio_verify_service_sid`

## Fast path: stop using the Flask development server

Echo-Chat supports two production-start paths now:

1. one-time override: `python main.py --production`
2. saved setup mode: set `run_mode` to `production`, then just run `python main.py`

The saved mode is intended for admins who do not want to remember special startup flags. Echo-Chat reads the saved settings, exports the Gunicorn environment, and replaces the current process with the production runner.

The helper script remains available too. From the project root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

export ECHOCHAT_CONFIG="$PWD/server_config.json"
export ECHOCHAT_SOCKETIO_ASYNC=threading
export ECHOCHAT_WORKERS=1
./scripts/run_production.sh
```

That removes the Flask dev-server warning because Gunicorn, not Flask's built-in server, is serving the app.

Before public beta, run the Redis/Socket.IO checker:

```bash
python main.py --redis-socketio-check
python main.py --redis-socketio-check --redis-live-check  # optional live Redis ping
python tools/deployment_ops_doctor.py
```

Beginner-safe rule: keep the built-in Gunicorn runner at one worker. Do not set `ECHOCHAT_WORKERS=2` in the same Gunicorn process for Socket.IO chat. When you are ready to scale, run multiple one-worker Echo-Chat instances behind sticky routing and configure Redis:

```bash
export ECHOCHAT_RATE_LIMIT_STORAGE_URI=redis://127.0.0.1:6379/0
export ECHOCHAT_SOCKETIO_MESSAGE_QUEUE=redis://127.0.0.1:6379/1
export ECHOCHAT_SHARED_STATE_REDIS_URL=redis://127.0.0.1:6379/2
export ECHOCHAT_WORKERS=1
./scripts/run_production.sh
```

Run the janitor in a separate terminal or service for scaled deployments:

```bash
ECHOCHAT_CONFIG="$PWD/server_config.json" python janitor_runner.py
```

## Current deployment caution

This repo is closer to release-ready than before, but you should still verify final packaging, example config coverage, and deployment notes before publishing a public snapshot.


### Default production runner note

Echo-Chat now defaults to Gunicorn `gthread` with Flask-SocketIO `threading` mode and `simple-websocket`. This is the safest default for current Python environments and avoids Eventlet worker-entrypoint failures. The browser transport default is `polling` in this mode, which prevents reconnect loops on LAN/beta deployments. Eventlet remains an advanced opt-in path only.

## Reverse proxy and forwarded headers

When Echo-Chat runs behind Nginx, Caddy, Apache, or another TLS terminator:

- enable `trust_proxy_headers`
- set `proxy_fix_hops` to the number of trusted proxy layers
- set `public_base_url` to the external HTTPS URL
- use `cookie_secure=true` and an appropriate `cookie_samesite` policy

Safe LAN-only starter configs live at `deploy/caddy/Caddyfile.example` and `deploy/nginx/echochat.conf.example`. For public beta, prefer the settings-aware generator: `python main.py --generate-proxy-config all --proxy-output-dir deploy/generated-proxy`, then review the generated Caddy/Nginx output before copying it into `/etc/caddy/` or `/etc/nginx/`.

## Health endpoint and ops checks

For production, enable the lightweight health endpoint and expose it only where appropriate. Recommended settings are:

- `enable_health_check_endpoint=true`
- `health_check_endpoint=/health` or `/healthz`

These settings can now be supplied through environment overrides as well.
Malformed or app-reserved health paths fall back to `/health`, and probes return no-store responses so stale proxy health state is not cached.

## Production env overrides

The runtime accepts environment overrides for deployment-specific values such as:

- `ECHOCHAT_PUBLIC_BASE_URL`
- `ECHOCHAT_COOKIE_SECURE`
- `ECHOCHAT_COOKIE_SAMESITE`
- `ECHOCHAT_TRUST_PROXY_HEADERS`
- `ECHOCHAT_PROXY_FIX_HOPS`
- `ECHOCHAT_ENABLE_HEALTH_ENDPOINT`
- `ECHOCHAT_HEALTH_ENDPOINT`
- `ECHOCHAT_SOCKETIO_MESSAGE_QUEUE`
- `ECHOCHAT_SHARED_STATE_REDIS_URL`
- `ECHOCHAT_CORS_ALLOWED_ORIGINS`

## LAN origin and cookie notes

If your browser is opened at `http://10.x.x.x:5000` but `cors_allowed_origins` only lists `http://localhost:5000` and `http://127.0.0.1:5000`, Socket.IO may reject the connection and the UI can show a reconnect banner. Keep `auto_allow_lan_origins=true` for LAN/mobile testing, or add the exact LAN origin to both `cors_allowed_origins` and `allowed_origins`.

For plain HTTP LAN testing, `cookie_secure` should be `false`. Use `cookie_secure=true` only with actual HTTPS or a correctly configured HTTPS reverse proxy.

## No domain yet

If you do not have a domain or stable HTTPS tunnel hostname yet, do not choose public beta mode. Choose `no_domain_yet` in setup. This keeps Echo-Chat in safe LAN testing mode and prevents fake placeholder public URLs such as `chat.example.com`, bare public IP addresses, or direct port-forwarded raw app URLs from being treated as real public beta addresses.

Useful commands:

```bash
python main.py --hosting-help
python main.py --generate-proxy-config all
python main.py --public-beta-check
```

When a real domain, Dynamic DNS hostname with HTTPS, or stable HTTPS tunnel hostname is available, set `public_base_url` to that exact HTTPS URL, regenerate proxy configs, and run the public beta check again. See `docs/NO_DOMAIN_YET.md` for the safer decision tree.


## Redis and Socket.IO topology

Echo-Chat now includes a dedicated checker:

```bash
python main.py --redis-socketio-check
python main.py --redis-socketio-check --redis-live-check
```

The checker validates the pieces that usually cause reconnect loops or missing messages online: Gunicorn worker count, Socket.IO message queue, Redis-backed rate limits, Redis DB separation, async mode, worker class, and transport policy.

Use these beginner-safe Redis DBs:

- `redis://127.0.0.1:6379/0` for rate-limit storage
- `redis://127.0.0.1:6379/1` for Socket.IO message queue
- `redis://127.0.0.1:6379/2` for optional shared state

For the built-in Gunicorn runner, keep `production_workers=1`. Later scaling should use multiple one-worker Echo-Chat instances behind sticky proxy routing plus the Redis message queue. A single-worker local production test does not need the Socket.IO Redis queue attached; keep Redis for rate-limit storage, or force Socket.IO queue attachment with `ECHOCHAT_FORCE_SOCKETIO_REDIS_QUEUE=1` only when you intentionally use external emitters.


### Profile-field key rotation

Sensitive profile fields use `ECHOCHAT_PROFILE_FIELD_KEY` when set. To rotate it, deploy the new value as `ECHOCHAT_PROFILE_FIELD_KEY`, place old key material in `ECHOCHAT_PROFILE_FIELD_PREVIOUS_KEYS`, run the Admin Security Dashboard **Rotate profile field key** action, verify no undecryptable fields remain, then remove the previous-key environment variable. The same dashboard can bulk-encrypt legacy plaintext phone/address/location rows.

All-room E2EE strict mode is intentionally fenced with an acknowledgement because public-room message bodies become unavailable to server-side text moderation, search/transcript inspection, and body-based classification.


### Email encryption deployment checklist

1. Back up the database.
2. Set stable `ECHOCHAT_EMAIL_FIELD_KEY` and `ECHOCHAT_EMAIL_HASH_KEY`.
3. Deploy and apply migrations.
4. Open Admin → Security Dashboard.
5. Run **Create security backup**.
6. Run **Encrypt old emails**.
7. Confirm plaintext email count is zero or expected.


## Socket.IO worker rule

For Echo-Chat's built-in Gunicorn runner, use `production_workers=1`. Flask-SocketIO's Gunicorn deployment path is one worker per process because Gunicorn's worker balancer is not sticky for Socket.IO clients. To scale later, run multiple one-worker Echo-Chat instances behind sticky reverse-proxy routing and configure a Redis Socket.IO message queue.


## Production instances vs workers

For Socket.IO, do not use ten Gunicorn workers inside one process. Use `production_workers=1` and, when you are ready to scale, set `production_instance_count` up to `10`. That means multiple separate Echo-Chat services on separate backend ports, usually `5000-5009`, behind sticky reverse-proxy routing and a Redis Socket.IO message queue.

The setup wizard records this as a deployment plan. A direct `python main.py --production` start launches one instance; the generated deployment kit includes `echochat@.service` for starting every planned instance.

### S19 deep deployment recheck notes

The checked-in static systemd templates now match the generated kit safety model: they run config and Redis/Socket.IO topology gates before startup, pass an explicit `ECHOCHAT_CONFIG`, keep one worker per process, include runtime `ReadWritePaths` for uploads/private uploads/instance/static uploads, and keep the janitor as one separate service. The static env-file instructions use `root:echochat` plus `0640` so the service user can read secrets without making them world-readable. For production, the generated deployment kit remains preferred because it fills in your exact folders and config path.
