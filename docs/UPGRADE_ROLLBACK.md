# Echo-Chat Upgrade and Rollback Runbook

Version: **0.11.0-beta.391**

This runbook is for moving a live Echo-Chat install to a new zip and having a clean way back if the new build fails.


## Release artifact verification

Before extracting or replacing a live install, verify the release artifact and the packaging rules:

```bash
sha256sum -c Echo-Chat-v0.11.0-beta.391-private-missed-message-active-state-fix.zip.sha256
python tools/release_packaging_doctor.py
python tools/release_packaging_deep_doctor.py
python tools/post_ui12_room_unread_visibility_doctor.py
python tools/post_ui12_deep_recheck_doctor.py
python tools/post_ui12_bug_hunt_doctor.py
python tools/ui12_final_frontend_release_doctor.py
```

The release package should be built with `scripts/build_release_package.py`; do not manually zip a live working directory because it may contain `server_config.json`, `.env`, logs, private uploads, local database files, symlinks to files outside the project, or older release zips. The release manifest intentionally omits the absolute local source path.

## Backup before upgrade

Before replacing files, capture a rollback point:

1. Stop the app service or put the site into maintenance mode.
2. Back up the current project folder, excluding `uploads/` only if that folder is already backed up separately.
3. Back up PostgreSQL with `pg_dump` or your hosting provider's snapshot tool.
4. Copy the current environment secret file, commonly `/etc/echochat/echochat.env`, to a private backup location.
5. Save the current release zip filename and `VERSION.txt` value.

Never paste production passwords, SMTP tokens, TURN credentials, JWT secrets, or database DSNs into support chats or release notes.

## Apply the new zip

1. Extract the new Echo-Chat zip into a fresh folder.
2. Copy local-only runtime files from the old install into the new folder only when they are meant to be local, such as `server_config.json`.
3. Keep `.env`, TLS keys, backup keys, and service env files outside the source tree whenever possible.
4. Install or refresh dependencies from the deployment guide for the target host.
5. Run static checks before starting the server:

```bash
python tools/service_smoke.py --url http://127.0.0.1:5000
python tools/config_doctor.py --config server_config.json
python main.py --preflight
```

6. Start in the same mode you used before, usually:

```bash
python main.py --production
```

or the configured systemd/Gunicorn service.

## Database migrations

Run migrations only after the source is in place and the database backup exists:

```bash
python main.py --schema-version
python main.py --migrate
python main.py --schema-version
```

If migration output looks wrong, stop before opening the site to users. Restore the database backup if the schema moved forward but the app cannot boot.

## Environment secrets

Production/public-beta installs should provide these through environment variables or a protected service env file rather than storing them in JSON:

- database DSN and bootstrap/admin DSN
- Flask/JWT secret material
- SMTP username/password
- Twilio Verify secrets
- TURN/WebRTC credentials
- profile/email/security-backup encryption keys
- Redis/Socket.IO queue passwords

After copying the new build, run:

```bash
python main.py --preflight
python tools/config_doctor.py --config server_config.json --include-db
python tools/config_doctor.py --config server_config.json
python tools/log_sanity.py
```

Those checks do not print secret values; they only report whether the required values and safe operational rules are present.

## Post-upgrade smoke

After the server starts:

1. Open `/login`, sign in as the owner/admin, and verify the visible version label.
2. Join a public room, send a disposable message, and leave the room.
3. Open a PM and a group chat, then verify messages still render.
4. Open Admin Panel, confirm fresh auth, and run Admin Test Lab readiness, full suite, live user flow, browser P2P diagnostics, and the Browser release gate.
5. Complete the Browser release gate for the browsers you actually support.
6. Run `python tools/service_smoke.py --url http://127.0.0.1:5000` using the real URL for the host.
7. Run `python tools/log_sanity.py` or point it at the service logs.
8. Check logs for startup, migration, Socket.IO, and janitor warnings.

## Rollback steps

Use rollback when the new build will not boot, login breaks, migrations fail, or release-gate checks expose a blocker.

1. Stop the app service.
2. Restore the previous project folder or previous release zip.
3. Restore the previous `server_config.json` and protected service env file.
4. Restore the PostgreSQL backup if migrations or data writes happened after upgrade.
5. Start the previous build.
6. Verify `/login`, room join, PM/group open, and Admin Panel readiness.
7. Record the failed version, first error, and which step failed before trying another upgrade.

## Rollback limits

Rollback is safest before users create new content on the upgraded build. If users have already sent messages, uploaded files, or changed account settings, restoring the database backup will remove that new data. In that situation, export evidence/logs first and decide whether data preservation matters more than immediate rollback.

## Final handoff checklist

Keep the release zip, `.sha256` file, generated release manifest, and `Echo-Chat_Server-Side_Audit_Checklist_beta352.md` together. The manifest records package size, file count, checksum, deterministic zip policy, symlink exclusion policy, and the packaging exclusion policy used to keep runtime secrets out of the zip.
