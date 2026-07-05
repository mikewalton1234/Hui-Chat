# Operations

Version: **0.11.0-beta.346**

## Setup and boot

```bash
python main.py --setup
python main.py
```

## Preflight

Run before debugging deployment issues:

```bash
python main.py --preflight
python tools/preflight.py
```

Preflight is intended to check items such as:

- database reachability
- cookie/secret coherence
- writable runtime paths
- Socket.IO/Redis configuration
- Echo media/webcam configuration completeness

## Migrations

```bash
python main.py --list-migrations
python main.py --migrate
python main.py --schema-version

python tools/migrate.py --list
python tools/migrate.py --migrate
python tools/migrate.py --schema-version
```

## Janitor / room cleanup

The repo includes janitor settings and a `janitor_runner.py` helper. Cleanup timing is controlled by settings such as:

- `janitor_interval_seconds`
- `autoscale_rooms_enabled`
- `autoscale_room_capacity`
- `autoscale_room_idle_minutes`
- `custom_room_idle_minutes`
- `custom_private_room_idle_minutes`
- `janitor_debug_custom_rooms`
- `cleanup_expired_auth_enabled`
- `cleanup_orphan_auth_enabled`
- `auth_token_retention_days`
- `revoked_session_retention_days`
- `password_reset_token_retention_days`
- `orphan_auth_retention_days`
- `auth_cleanup_batch_limit`
- `privacy_retention_batch_limit`

Run one cleanup pass for deployment smoke testing:

```bash
python janitor_runner.py --config server_config.json --once --json
```

## Reset and repair helpers

### Database reset

```bash
bash tools/reset_db_fresh.sh
bash tools/reset_db_schema_only.sh
```

### Postgres repair SQL

See `scripts/README.md` for:

- ownership repair
- collation refresh
- duplicate email cleanup

## Diagnostics and admin operations

The app includes admin/diagnostics support and a number of admin settings endpoints. Production use should treat those as authenticated administrative surfaces and keep origin/cookie settings tight.
