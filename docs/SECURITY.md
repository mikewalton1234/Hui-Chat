# Security

Version: **0.11.0-beta.317**

## Secrets handling

Echo-Chat supports environment overrides for sensitive values and should prefer them over plaintext config. In production/public mode, secret values are not persisted back into `server_config.json` unless `ECHOCHAT_PERSIST_SECRETS=1` is explicitly set.

Sensitive values include:

- database DSNs
- Flask/JWT secrets
- SMTP credentials
- GIPHY keys
- Echo media settings
- Twilio credentials
- TURN/WebRTC credentials
- password-bearing Redis/queue/cache URLs


## Private-message encryption policy

Direct messages default to encrypted-only behavior. Missing settings fail closed:

- `require_dm_e2ee`: `true`
- `allow_plaintext_dm_fallback`: `false`

The `ECP1:` plaintext wrapper is now treated as explicit legacy compatibility mode only. Leave fallback disabled for production/public deployments. Only enable fallback temporarily when supporting old clients you fully trust, and turn it back off after those clients are updated.

## Current repository caution

Runtime-oriented files such as `server_config.json`, `settings.json`, `server_key.key`, and `.env` must stay local only. The tracked tree should contain only example templates and documentation.

## Cookies and origin policy

Review and harden these settings before deployment:

- `cookie_secure`
- `cookie_samesite`
- `cors_allowed_origins`
- `trust_proxy_headers`
- `proxy_fix_hops`
- `enforce_same_origin_writes`
- `require_logout_csrf`

## Admin panel authorization and step-up checks

Admin routes are guarded with live RBAC permission checks rather than stale session flags. High-risk admin writes require an admin password confirmation tied to the current auth-session ID. By default, one confirmation unlocks admin actions for the current login session (`admin_reauth_once_per_session=true`) so admins are not repeatedly prompted while working in the panel; set `admin_reauth_once_per_session=false` to restore a timed window controlled by `admin_fresh_auth_window_seconds`. Password confirmation attempts remain rate-limited with `rate_limit_admin_reauth` so the reauth endpoint cannot be used as an unlimited password oracle.

Sensitive audit/login metadata is restricted to admins. Mutating actions use the narrowest available permission, and moderation routes refuse to target admin or privileged accounts unless the actor also has `admin:basic`. Role assignment also refuses to downgrade/change privileged target accounts without `admin:basic`, deleting custom roles that contain privileged permissions requires `admin:basic`, and Test Lab execution is admin plus recent-reauth gated because it can run mutating checks. Compatibility admin-route aliases must reuse the source endpoint methods and fail closed to POST-only rather than accidentally introducing GET access to mutating handlers.


## Rate limiting and anti-abuse

The current codebase includes:

- Flask-Limiter based HTTP route limits
- admin guardrails
- socket/social anti-abuse settings

Examples of relevant settings include:

- `rate_limit_login`
- `rate_limit_register`
- `rate_limit_refresh`
- `admin_rate_limit_get`
- `admin_rate_limit_write`
- `friend_req_rate_limit`
- `social_action_rate_limit`
- `admin_socket_write_rate_limit`

## 2FA

SMS 2FA exists but is optional and disabled by default. It depends on Twilio Verify configuration being present and correct.

## Release-prep security rule

Before public release:

1. keep local runtime secrets and key files out of the tracked tree
2. verify ignore rules
3. prefer env-based secrets
4. keep `ECHOCHAT_PERSIST_SECRETS` unset or set to `0` in production
5. confirm cookie/origin settings for deployment

## Group/private-room encryption and profile field encryption - beta.141

- `require_group_e2ee` defaults to `true`; plaintext group messages are rejected unless an admin intentionally disables that policy.
- `require_private_room_e2ee` defaults to `true`; private custom rooms reject plaintext messages without forcing public rooms to do the same.
- `require_room_e2ee` remains a stricter optional mode for all rooms.
- New sensitive profile-field writes are encrypted at rest when a stable server key is available. Covered fields are `users.phone`, `users.address`, and `users.location_text`. Existing plaintext rows remain readable and are converted on the next write.
- Prefer setting `ECHOCHAT_PROFILE_FIELD_KEY` in production. If it is absent, EchoChat derives the field-encryption key from `SECRET_KEY` / `secret_key`. Do not rotate that key without planning for old encrypted field values.

## Admin security dashboard and privacy retention

The Admin Panel includes a security dashboard backed by `/admin/security/status`. It reports E2EE posture, sensitive profile-field encryption key availability, Test Lab randomized-link protections, secret persistence state, and privacy-retention warnings.

Randomized Test Lab URLs are protected with `Referrer-Policy: no-referrer` on tokenized Test Lab pages. Werkzeug access logs and audit details redact tokenized `/admin/test_lab/<token>` and `/admin/test-lab/<token>` path segments.

Raw IP address and user-agent metadata is retention-limited. By default, old session/token/password-reset IP/UA values older than 30 days are replaced with stable, non-reversible hash labels. Older audit details containing `ip=` / `ua=` text are scrubbed after 90 days.

Relevant settings:

```json
{
  "privacy_retention_enabled": true,
  "privacy_ip_user_agent_retention_days": 30,
  "privacy_audit_detail_retention_days": 90
}
```

The janitor applies this automatically. Admins can also run it immediately from the security dashboard.


### Profile-field key rotation

Sensitive profile fields use `ECHOCHAT_PROFILE_FIELD_KEY` when set. To rotate it, deploy the new value as `ECHOCHAT_PROFILE_FIELD_KEY`, place old key material in `ECHOCHAT_PROFILE_FIELD_PREVIOUS_KEYS`, run the Admin Security Dashboard **Rotate profile field key** action, verify no undecryptable fields remain, then remove the previous-key environment variable. The same dashboard can bulk-encrypt legacy plaintext phone/address/location rows.

All-room E2EE strict mode is intentionally fenced with an acknowledgement because public-room message bodies become unavailable to server-side text moderation, search/transcript inspection, and body-based classification.


## Encrypted email-at-rest

EchoChat supports encrypted-at-rest email storage with two columns: `users.email_hash` for deterministic exact lookup and `users.email_encrypted` for AES-GCM display/send workflows. New encrypted writes clear raw `users.email`; old plaintext rows stay compatible until the Admin Security Dashboard action **Encrypt old emails** rewrites them.

Recommended production variables:

```bash
export ECHOCHAT_EMAIL_FIELD_KEY='long-random-email-encryption-secret'
export ECHOCHAT_EMAIL_HASH_KEY='long-random-email-lookup-secret'
```

Keep these keys backed up. Changing the hash key without a migration plan breaks exact email lookup for already-hashed rows.

## Encrypted security-operation backups

Before profile-field encryption, profile-field key rotation, legacy email encryption, or the one-click **Finish Security Setup** action, EchoChat creates a narrow backup under `backups/security/`. New backups are encrypted by default as `.json.enc` AES-GCM envelopes and include only user email/phone/address/location fields that the operation may rewrite.

Recommended production variable:

```bash
export ECHOCHAT_SECURITY_BACKUP_KEY='long-random-security-backup-secret'
```

Older plaintext `.json` backups remain restorable for rollback compatibility, but new backup writes should be encrypted. Keep `backups/security/` private because legacy backups may contain plaintext values.

The Admin Security Dashboard **Finish Security Setup** action creates an encrypted backup, encrypts old profile fields, encrypts old email rows, and runs privacy retention in that order.
