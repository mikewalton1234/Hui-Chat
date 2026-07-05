# Configuration

Version: **0.11.0-beta.317**


## Project name vs server display name

`Echo-Chat` remains the project/software name. The runtime display name comes from `server_config.json -> server_name`. Service names and executable names should not be auto-renamed just because `server_name` changes.

## Primary runtime file

Echo-Chat currently uses a local `server_config.json` file as its main runtime settings file.

That file is plain JSON and is intentionally **not** tracked. Production/public mode will not write secret values back into it unless `ECHOCHAT_PERSIST_SECRETS=1` is explicitly set. Create it locally from `server_config.example.json`.

```bash
cp server_config.example.json server_config.json
```

## Environment overrides

Important environment variables supported by the current codebase include:

### Secret persistence
- `ECHOCHAT_PERSIST_SECRETS=0` keeps secrets out of `server_config.json`
- `ECHOCHAT_PERSIST_SECRETS=1` explicitly allows legacy secret persistence


### Core
- `DATABASE_URL`
- `DB_CONNECTION_STRING`
- `SECRET_KEY`
- `JWT_SECRET_KEY`
- `ECHOCHAT_JWT_SECRET`

### SMTP
- `ECHOCHAT_SMTP_ENABLED`
- `SMTP_ENABLED`
- `ECHOCHAT_SMTP_HOST`
- `SMTP_HOST`
- `ECHOCHAT_SMTP_PORT`
- `SMTP_PORT`
- `ECHOCHAT_SMTP_USERNAME`
- `SMTP_USERNAME`
- `ECHOCHAT_SMTP_PASSWORD`
- `SMTP_PASSWORD`
- `ECHOCHAT_SMTP_FROM`
- `SMTP_FROM`
- `ECHOCHAT_SMTP_STARTTLS`
- `SMTP_STARTTLS`
- `ECHOCHAT_SMTP_SSL`
- `SMTP_SSL`
- `ECHOCHAT_PUBLIC_BASE_URL`
- `PUBLIC_BASE_URL`

### GIPHY
- `ECHOCHAT_GIPHY_API_KEY`
- `GIPHY_API_KEY`

### Twilio/SMS 2FA
- `ECHOCHAT_ENABLE_TWO_FACTOR_BETA` / `ENABLE_TWO_FACTOR_BETA`
- `ECHOCHAT_ENABLE_SMS_2FA` / `ECHOCHAT_ENABLE_SMS_TWO_FACTOR`
- `ECHOCHAT_TWILIO_ACCOUNT_SID`
- `TWILIO_ACCOUNT_SID`
- `ECHOCHAT_TWILIO_AUTH_TOKEN`
- `TWILIO_AUTH_TOKEN`
- `ECHOCHAT_TWILIO_VERIFY_SERVICE_SID`
- `TWILIO_VERIFY_SERVICE_SID`
- `ECHOCHAT_TWILIO_VERIFY_CHANNEL` / `ECHOCHAT_TWO_FACTOR_SMS_CHANNEL`
- `ECHOCHAT_TWO_FACTOR_LOGIN_TIMEOUT_SECONDS`

### Echo media / webcam
- `ECHOCHAT_AV_MODE` / `AV_MODE`
- `webcam_enabled`
- `webcam_approval_mode`
- `webcam_max_viewers`
- `default_media_policy`
- `rate_limit_media_mode`

### Server and routing
- `server_name`
- `server_host`
- `server_port`
- `public_base_url`

### Database and pool
- `database_url`
- `db_pool_min`
- `db_pool_max` (runtime floor: 50; lower old values are raised to 50 for UI stability)

### Cookies and origin handling
- `cookie_secure`
- `cookie_samesite`
- `cors_allowed_origins`
- `trust_proxy_headers`
- `proxy_fix_hops`

### Authentication and recovery
- `access_token_minutes`
- `refresh_token_days`
- `password_reset_token_minutes`
- `password_reset_spool_file`
- `password_reset_spool_allow_remote`
- `recovery_pin_max_attempts`
- `recovery_pin_lock_minutes`

Password reset links are emailed through the configured SMTP relay. For internet-facing deployments, set `public_base_url` or `ECHOCHAT_PUBLIC_BASE_URL` to the real URL users open, such as `https://chat.yourdomain.com`. Echo-Chat will only derive the reset-link host from the request for localhost/LAN development.

Recommended free/low-cost SMTP relay presets:

| Provider | Host | Port | TLS mode | Username hint | Notes |
| --- | --- | ---: | --- | --- | --- |
| Brevo | `smtp-relay.brevo.com` | `587` | STARTTLS | Brevo SMTP login | Good default for small servers. |
| Resend | `smtp.resend.com` | `465` | SSL | `resend` | Use the API key as the SMTP password. |
| SMTP2GO | `mail.smtp2go.com` | `2525` | STARTTLS | SMTP2GO SMTP user | Try `587` if `2525` is blocked. |
| MailerSend | `smtp.mailersend.net` | `587` | STARTTLS | generated SMTP user | Requires a verified sending domain. |
| Amazon SES | region-specific SES SMTP host | `587` | STARTTLS | SES SMTP user | Low-cost, but setup is more technical. |

### Uploads and messaging
- `max_message_length`
- `max_attachment_size`
- `max_dm_file_bytes`
- `p2p_file_enabled`
- `p2p_file_chunk_bytes`
- `p2p_file_handshake_timeout_ms`
- `p2p_file_transfer_timeout_ms`

### Voice and WebRTC ICE
- `voice_enabled`
- `voice_max_room_peers`
- `p2p_ice_servers`
- `voice_ice_servers`
- `voice_invite_cooldown_seconds`
- `voice_dm_invite_ttl_seconds`
- `voice_dm_active_ttl_seconds`

### Janitor, room cleanup, and public-room autosplit
- `janitor_interval_seconds`
- `autoscale_rooms_enabled`
- `autoscale_room_capacity`
- `autoscale_room_idle_minutes`
- `custom_room_idle_minutes`
- `custom_private_room_idle_minutes`
- `janitor_debug_custom_rooms`

### Rate limiting and anti-abuse
- HTTP route limits such as `rate_limit_login`, `rate_limit_register`, `rate_limit_upload`
- admin limits such as `admin_rate_limit_get`, `admin_rate_limit_write`
- socket/social limits such as `friend_req_rate_limit`, `social_action_rate_limit`, `admin_socket_write_rate_limit`

### Optional integrations
- WebRTC STUN/TURN settings; see `docs/STUN_TURN_SETUP.md`
- GIPHY settings
- SMTP settings
- Echo media settings
- Twilio Verify settings
- Redis/shared state settings

## Current config caveats

- `settings.example.json` exists, but the main runtime code centers on local `server_config.json`
- `server_key.key` is a generated local key file and should never be committed
- prefer environment variables for credentials and secret material


## Direct-message encryption defaults

New installs default direct messages to encrypted-only mode:

```json
{
  "require_dm_e2ee": true,
  "allow_plaintext_dm_fallback": false
}
```

`allow_plaintext_dm_fallback` is a temporary legacy compatibility switch. Keep it off for production/public servers.

## Group/private-room encryption and sensitive profile fields

Security defaults added in v0.11.0-beta.141:

```json
{
  "require_group_e2ee": true,
  "require_private_room_e2ee": true,
  "require_room_e2ee": false,
  "encrypt_sensitive_profile_fields": true
}
```

`require_group_e2ee` rejects plaintext group messages. `require_private_room_e2ee` applies the same rule to invite-only/private custom rooms. `require_room_e2ee` is a stricter optional setting for every room, including public rooms.

For profile-field encryption, set a stable production key with:

```bash
export ECHOCHAT_PROFILE_FIELD_KEY='replace-with-a-long-random-secret'
```

The fallback is `SECRET_KEY` / `secret_key`. `profile_field_encryption_key` is treated as a secret and scrubbed from `server_config.json` when secret persistence is disabled.

## Privacy retention settings

EchoChat keeps recent IP/user-agent metadata for account-security diagnostics, but old raw values should not be stored indefinitely.

```json
{
  "privacy_retention_enabled": true,
  "privacy_ip_user_agent_retention_days": 30,
  "privacy_audit_detail_retention_days": 90
}
```

`privacy_ip_user_agent_retention_days` controls raw IP/UA retention in auth sessions, auth tokens, and password-reset tokens. `privacy_audit_detail_retention_days` controls scrubbing old audit details that contain `ip=` or `ua=` text. Set either value to `0` only when you intentionally want that sweep disabled.


### Profile-field key rotation

Sensitive profile fields use `ECHOCHAT_PROFILE_FIELD_KEY` when set. To rotate it, deploy the new value as `ECHOCHAT_PROFILE_FIELD_KEY`, place old key material in `ECHOCHAT_PROFILE_FIELD_PREVIOUS_KEYS`, run the Admin Security Dashboard **Rotate profile field key** action, verify no undecryptable fields remain, then remove the previous-key environment variable. The same dashboard can bulk-encrypt legacy plaintext phone/address/location rows.

All-room E2EE strict mode is intentionally fenced with an acknowledgement because public-room message bodies become unavailable to server-side text moderation, search/transcript inspection, and body-based classification.


### Email at-rest encryption

- `encrypt_email_at_rest`: defaults to `true`.
- `ECHOCHAT_EMAIL_FIELD_KEY`: AES-GCM key material for `users.email_encrypted`.
- `ECHOCHAT_EMAIL_HASH_KEY`: keyed lookup material for `users.email_hash`.
- `encrypt_security_backups`: defaults to `true`; new security backup files are `.json.enc` envelopes.
- `ECHOCHAT_SECURITY_BACKUP_KEY`: AES-GCM key material for encrypted security-operation backups.
- `ECHOCHAT_SECURITY_BACKUP_DIR`: optional directory for security-operation backups, default `backups/security`.

For production, prefer environment variables instead of storing these keys in `server_config.json`. Use Admin Security Dashboard **Finish Security Setup** after migrations to create an encrypted backup, encrypt old profile/email rows, and run privacy retention.
