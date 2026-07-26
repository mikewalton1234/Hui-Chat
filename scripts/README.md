# Postgres repair scripts

These SQL helpers are for local development and repair work.

## Included scripts

- `pg_fix_ownership.sql` — transfer table ownership to the role your app is using
- `pg_refresh_collation.sql` — refresh collation metadata after a system or Postgres upgrade when needed
- `pg_find_duplicate_emails.sql` — locate duplicate user emails
- `pg_dedupe_emails_set_null.sql` — keep one email and null the rest
- `pg_dedupe_emails_keep_lowest_id.sql` — preserve the lowest-id row as the keeper
- `pg_purge_room_history.sql` — targeted room-history cleanup helper

## Typical ownership repair

```bash
sudo -u postgres psql -d YOUR_DB -v new_owner=YOUR_ROLE -f scripts/pg_fix_ownership.sql
```

## Duplicate-email investigation

```bash
sudo -u postgres psql -d YOUR_DB -f scripts/pg_find_duplicate_emails.sql
```

## Duplicate-email cleanup

```bash
sudo -u postgres psql -d YOUR_DB -f scripts/pg_dedupe_emails_set_null.sql
```

## Caution

Run these only against the intended database. Some of them are destructive by design.


# Production/deployment helper scripts

- `install_production_deps.sh` installs the default Gunicorn gthread + simple-websocket runtime into the active virtualenv.
- `run_production.sh` starts the saved production runner through `python main.py --production`.
- `generate_reverse_proxy_config.py` writes reviewable Caddy/Nginx templates.

Before installing generated systemd files, run:

```bash
python tools/deployment_ops_doctor.py
python main.py --redis-socketio-check --redis-live-check
```

## Release package builder

- `build_release_package.py` creates a sanitized release zip, `.sha256` checksum file, and release manifest while excluding local config, secrets, runtime uploads, logs, databases, caches, and old release archives. It also excludes symlinks, validates archive member paths, normalizes zip metadata, and omits absolute local source paths from the manifest.

Typical release build:

```bash
python scripts/build_release_package.py --output-dir dist --label s20-deep-release-package-recheck
```

Verify the generated package with:

```bash
python tools/release_packaging_doctor.py
python tools/release_packaging_deep_doctor.py
```

## Complete server installer

For Arch/EndeavourOS or Debian/Ubuntu:

```bash
sudo bash scripts/install_server.sh
```

This provisions the service account, virtual environment, PostgreSQL database, Valkey/Redis, protected secrets, setup-only wizard flow, migrations, readiness checks, and systemd services. Re-running the installer preserves stable credentials/secrets, stops every active Hui Chat instance before synchronizing code, and restarts the topology selected by `server_config.json`.
