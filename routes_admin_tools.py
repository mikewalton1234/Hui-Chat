#!/usr/bin/env python3
"""routes_admin_tools.py

Admin tool endpoints (PostgreSQL).

This file previously used SQLite (DB_FILE). It now uses get_db() and
PostgreSQL-safe SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import wraps
import base64
import ipaddress
import json
import logging
import os
import time
import random
import re
import secrets
import uuid
from threading import RLock, Timer
from pathlib import Path
from urllib.parse import urlparse

from flask import jsonify, request, session, current_app, render_template, make_response, abort, url_for
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request, unset_jwt_cookies

from database import get_db, get_db_identity, get_schema_version, get_auth_session_state, touch_auth_session_activity, revoke_auth_session, create_room_if_missing, delete_custom_room_persisted_state, ensure_profile_post_engagement_schema
from database import create_user_with_keys, user_exists, email_in_use, revoke_all_sessions_for_user, revoke_all_sessions_and_tokens_for_user, generate_user_keypair_for_password, canonical_username
from permissions import require_permission, get_user_permissions
from security import hash_password, log_audit_event, verify_password_and_upgrade, get_request_ip, parse_rate_limit_value, simple_rate_limit, simple_rate_limit_clear, is_local_request as _is_local_request
from constants import CONFIG_FILE, redact_postgres_dsn, get_db_connection_string, normalize_sound_pack_identifier, sanitize_sound_pack_external_urls, sound_pack_local_builtins_enabled
from preflight import run_preflight
from secrets_policy import scrub_patch_for_persist, scrub_secrets_for_persist, persist_secrets_enabled
from sensitive_fields_crypto import sensitive_field_key_available, sensitive_field_previous_keys_available, SENSITIVE_FIELD_PREFIX
from privacy_retention import privacy_retention_counts, apply_privacy_retention
try:
    from janitor import janitor_status_snapshot
except Exception:  # pragma: no cover - janitor may be unavailable during partial installs
    janitor_status_snapshot = None
from profile_field_migration import profile_field_encryption_counts, encrypt_plaintext_profile_fields, rotate_profile_field_envelopes
from email_field_migration import email_encryption_counts, encrypt_plaintext_emails
from email_at_rest import display_email, hash_email, email_field_key_available, email_hash_key_available
from registration_name_policy import normalize_registration_username, validate_registration_username
from account_creation_policy import (
    PASSWORD_MIN_LENGTH,
    validate_account_password,
    validate_account_username_style,
    validate_recovery_pin,
    recovery_pin_policy_summary,
)
from security_backups import create_security_backup, list_security_backups, restore_security_backup, security_backup_encryption_enabled, security_backup_key_available
from public_room_e2ee_audit import public_room_e2ee_impact_report
from media_mode import client_av_config, resolve_av_mode, webcam_policy
from account_status import effective_account_status_sql, get_effective_account_status
from moderation import add_ip_sanction, add_sanction, expire_ip_sanctions, expire_sanctions
from webrtc_ice_config import (
    apply_turn_credentials,
    ice_server_summary,
    parse_ice_servers_text,
    p2p_ice_servers,
    redact_ice_servers,
    turn_credential_errors,
    voice_ice_servers,
)
from echo_voice_protocol import (
    ECHO_WEBCAM_QUALITY_PROFILES,
    echo_voice_audio_quality,
    echo_voice_bool,
    echo_voice_client_config,
    echo_voice_room_capacity,
    echo_voice_room_limit,
    echo_webcam_quality,
)

try:
    from realtime.state import (
        VOICE_ROOMS,
        VOICE_ROOMS_LOCK,
        connected_room_targets as _state_connected_room_targets,
        connected_sessions_snapshot as _state_connected_sessions_snapshot,
        connected_usernames as _state_connected_usernames,
        live_room_counts as _state_live_room_counts,
        set_room_slowmode_cache as _state_set_room_slowmode_cache,
        update_connected_room as _state_update_connected_room,
        user_sids as _state_user_sids,
        room_users as _state_room_users,
    )
except Exception:  # pragma: no cover
    VOICE_ROOMS = {}
    VOICE_ROOMS_LOCK = None
    _state_connected_room_targets = None
    _state_connected_sessions_snapshot = None
    _state_connected_usernames = None
    _state_live_room_counts = None
    _state_set_room_slowmode_cache = None
    _state_update_connected_room = None
    _state_user_sids = None
    _state_room_users = None


_TESTLAB_ACTIVE_LOADS: dict[str, dict] = {}
_TESTLAB_ACTIVE_LOADS_LOCK = RLock()
_ROOM_CATALOG_WRITE_LOCK = RLock()
_TESTLAB_MAX_MANUAL_HOLD_SECONDS = 30 * 60
_TESTLAB_LINK_TTL_SECONDS = 60 * 60
_TESTLAB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,160}$")
_TESTLAB_LOAD_ID_RE = re.compile(r"^[A-Fa-f0-9]{12,64}$")
_ANALYTICS_OPAQUE_TARGET_RE = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
_ANALYTICS_HEX_TARGET_RE = re.compile(r"^[0-9a-fA-F]{20,128}$")


def _admin_testlab_valid_dm_cipher(label: str = "admin-testlab") -> str:
    """Return a valid-looking EC1 DM envelope for server relay smoke checks.

    The Admin Test Lab uses Flask/Socket.IO test clients, not a real browser
    WebCrypto context. The server never decrypts PM payloads, but F111 now
    validates the non-secret envelope shape before relaying/storing ciphertext.
    """
    label_bytes = str(label or "admin-testlab").encode("utf-8")[:96]
    envelope = {
        "v": 1,
        "alg": "RSA-OAEP+AES-GCM",
        "ek": base64.b64encode(b"E" * 256).decode("ascii"),
        "iv": base64.b64encode(b"I" * 12).decode("ascii"),
        "ct": base64.b64encode(label_bytes + (b"C" * 16)).decode("ascii"),
    }
    raw = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    return "EC1:" + base64.b64encode(raw).decode("ascii")


def _analytics_target_bucket(raw_target) -> tuple[str, str]:
    """Return a safe, human-readable analytics bucket for audit targets.

    Audit targets can contain room/user names, global markers, short database ids,
    opaque invite/session/token ids, or torrent ids. The dashboard should not make
    admins stare at raw random identifiers in the small overview card. Raw values
    remain available in the Audit section when exact forensics are needed.
    """
    raw = "" if raw_target is None else str(raw_target).strip()
    if not raw or raw == "(none)":
        return "No specific target", "none"
    lower = raw.lower()
    if raw == "*":
        return "Global settings / all targets", "global"
    if lower in {"off", "on"}:
        return f"Toggle: {lower}", "toggle"
    if lower in {"soft_lockdown", "hard_lockdown", "raid_mode", "silent_observe"}:
        return f"Incident mode: {raw.replace('_', ' ')}", "incident"
    if "@" in raw and len(raw) <= 96:
        user, room = raw.split("@", 1)
        user = user.strip() or "user"
        room = room.strip() or "room"
        return f"{user} in {room}", "room-user"
    if lower.startswith(("room:", "user:", "group:", "post:", "comment:")):
        prefix, _, value = raw.partition(":")
        value = value.strip()
        if len(value) > 46:
            value = value[:43] + "..."
        return f"{prefix.title()}: {value or 'unknown'}", prefix.lower()
    # Long hex strings are commonly torrent infohashes, password-reset token
    # hashes, auth/session ids, or other internal ids. Group them instead of
    # showing each opaque value as a top target.
    if _ANALYTICS_HEX_TARGET_RE.fullmatch(raw):
        if len(raw) in {40, 64}:
            return "Torrent / internal hash IDs", "internal-hash"
        return "Internal hash IDs", "internal-hash"
    # Mixed-case base64/base62/url-safe ids make the dashboard look broken and
    # can expose implementation detail. Keep exact values in Audit Log only.
    if _ANALYTICS_OPAQUE_TARGET_RE.fullmatch(raw) and not any(ch in raw for ch in ".:/@#"):
        return "Internal token/session IDs", "internal-id"
    if raw.isdigit() and len(raw) >= 5:
        return "Numeric object IDs", "internal-id"
    if len(raw) > 56:
        return raw[:53] + "...", "truncated"
    return raw, "named"


def _analytics_bucketed_top_targets(rows, limit: int = 6) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows or []:
        if not row:
            continue
        label, kind = _analytics_target_bucket(row[0])
        try:
            count = int(row[1] or 0)
        except Exception:
            count = 0
        if label not in buckets:
            buckets[label] = {"label": label, "count": 0, "kind": kind}
        buckets[label]["count"] += count
    items = sorted(buckets.values(), key=lambda r: (-int(r.get("count") or 0), str(r.get("label") or "")))
    for item in items:
        kind = str(item.get("kind") or "")
        if kind.startswith("internal"):
            item["meta"] = "Grouped internal IDs; open Audit Log for exact values."
        elif kind == "global":
            item["meta"] = "Server-wide setting or all-room action."
        elif kind == "none":
            item["meta"] = "Audit event had no specific target."
    return items[: max(1, int(limit or 6))]


def _room_catalog_path() -> Path:
    """Return the official room catalog path used by /api/room_catalog."""
    return Path(__file__).resolve().parent / "chat_rooms.json"


def _read_room_catalog_raw() -> dict:
    """Read chat_rooms.json without dropping admin-editable metadata."""
    path = _room_catalog_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        logging.exception("[ADMIN] failed to read room catalog")
        return {"version": 2, "categories": []}
    if not isinstance(data, dict):
        return {"version": 2, "categories": []}
    data.setdefault("version", 2)
    cats = data.get("categories")
    if not isinstance(cats, list):
        data["categories"] = []
    return data


def _write_room_catalog_raw(data: dict) -> None:
    """Atomically write chat_rooms.json with stable formatting."""
    path = _room_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _safe_station_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except Exception:
        return ""
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return ""
    return value


def _normalize_admin_station_payload(stations) -> tuple[list[dict], str | None]:
    """Validate and normalize admin-edited radio station rows."""
    if stations is None:
        stations = []
    if not isinstance(stations, list):
        return [], "stations must be a list"
    if len(stations) > 16:
        return [], "A room can have at most 16 stations"
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for idx, raw in enumerate(stations, start=1):
        if not isinstance(raw, dict):
            return [], f"Station {idx} must be an object"
        label = str(raw.get("label") or raw.get("name") or "").strip()
        provider = str(raw.get("provider") or "iHeartRadio").strip()[:80]
        page_url = _safe_station_url(raw.get("page_url") or raw.get("url") or "")
        embed_url = _safe_station_url(raw.get("embed_url") or "")
        if not label:
            return [], f"Station {idx} is missing a label"
        if len(label) > 80:
            return [], f"Station {idx} label is too long"
        if not page_url:
            return [], f"Station {idx} needs an HTTPS source/page URL"
        if not embed_url:
            return [], f"Station {idx} needs an HTTPS embed/player URL"
        key = (page_url.lower(), embed_url.lower())
        if key in seen:
            return [], f"Station {idx} duplicates another station URL"
        seen.add(key)
        out.append({
            "label": label,
            "provider": provider or "Radio",
            "page_url": page_url,
            "embed_url": embed_url,
        })
    return out, None


def _iter_catalog_room_refs(catalog: dict):
    """Yield mutable room references from the official catalog."""
    for ci, cat in enumerate(catalog.get("categories") or []):
        if not isinstance(cat, dict):
            continue
        for si, sub in enumerate(cat.get("subcategories") or []):
            if not isinstance(sub, dict):
                continue
            rooms = sub.get("rooms")
            if not isinstance(rooms, list):
                continue
            for ri, room in enumerate(rooms):
                if isinstance(room, str):
                    name = room.strip()
                elif isinstance(room, dict):
                    name = str(room.get("name") or "").strip()
                else:
                    name = ""
                if not name:
                    continue
                yield {
                    "category_index": ci,
                    "subcategory_index": si,
                    "room_index": ri,
                    "category": str(cat.get("name") or "").strip(),
                    "subcategory": str(sub.get("name") or "").strip(),
                    "room": room,
                    "name": name,
                }


def _radio_room_admin_summary(ref: dict) -> dict:
    room = ref.get("room")
    data = room if isinstance(room, dict) else {"name": ref.get("name") or ""}
    features = data.get("features") if isinstance(data, dict) else []
    stations = data.get("stations") if isinstance(data, dict) else []
    if not isinstance(features, list):
        features = []
    if not isinstance(stations, list):
        stations = []
    clean_stations, _ = _normalize_admin_station_payload(stations[:16])
    return {
        "name": ref.get("name") or "",
        "category": ref.get("category") or "",
        "subcategory": ref.get("subcategory") or "",
        "features": [str(x) for x in features if str(x).strip()],
        "station_count": len(clean_stations),
        "stations": clean_stations,
        "description": str(data.get("description") or "") if isinstance(data, dict) else "",
        "topic": str(data.get("topic") or "") if isinstance(data, dict) else "",
        "radio_enabled": ("room_radio" in {str(x).strip() for x in features}) or bool(clean_stations),
    }

def _utcnow():
    return datetime.now(timezone.utc)



def _admin_testlab_session_tokens(now: int | None = None) -> dict[str, int]:
    """Return unexpired Test Lab tokens stored in the current admin session."""
    now = int(time.time()) if now is None else int(now)
    raw = session.get('admin_testlab_tokens')
    tokens: dict[str, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            tok = str(key or '').strip()
            try:
                exp = int(value or 0)
            except Exception:
                exp = 0
            if tok and _TESTLAB_TOKEN_RE.match(tok) and exp > now:
                tokens[tok] = exp
    # Backward-compatible cleanup/migration from the older single-token shape.
    legacy = str(session.get('admin_testlab_token') or '').strip()
    try:
        legacy_exp = int(session.get('admin_testlab_token_expires_at') or 0)
    except Exception:
        legacy_exp = 0
    if legacy and _TESTLAB_TOKEN_RE.match(legacy) and legacy_exp > now:
        tokens[legacy] = legacy_exp
    return tokens


def _admin_testlab_store_session_tokens(tokens: dict[str, int]) -> None:
    ordered = sorted(tokens.items(), key=lambda item: int(item[1]), reverse=True)[:5]
    session['admin_testlab_tokens'] = {token: int(expires_at) for token, expires_at in ordered}
    session.pop('admin_testlab_token', None)
    session.pop('admin_testlab_token_expires_at', None)
    session.modified = True


def _admin_testlab_issue_link() -> tuple[str, int]:
    """Create a short-lived, admin-session-bound Test Lab URL token."""
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    expires_at = now + _TESTLAB_LINK_TTL_SECONDS
    tokens = _admin_testlab_session_tokens(now)
    tokens[token] = expires_at
    _admin_testlab_store_session_tokens(tokens)
    return token, expires_at


def _admin_testlab_clear_link() -> None:
    changed = False
    for key in ('admin_testlab_tokens', 'admin_testlab_token', 'admin_testlab_token_expires_at'):
        if key in session:
            session.pop(key, None)
            changed = True
    if changed:
        session.modified = True


def _admin_testlab_token_valid(token: str) -> bool:
    """Validate the random Test Lab URL token without revealing route existence."""
    token = str(token or '').strip()
    if not token or not _TESTLAB_TOKEN_RE.match(token):
        return False
    tokens = _admin_testlab_session_tokens()
    if not tokens:
        _admin_testlab_clear_link()
        return False
    _admin_testlab_store_session_tokens(tokens)
    try:
        return any(secrets.compare_digest(token, expected) for expected in tokens.keys())
    except Exception:
        return False


def _admin_testlab_require_link_or_404(token: str) -> None:
    if not _admin_testlab_token_valid(token):
        abort(404)


def _admin_testlab_require_admin_or_404() -> str:
    """Require admin:test_lab while keeping the random Test Lab surface dark."""
    try:
        verify_jwt_in_request(optional=False)
        username = get_jwt_identity()
    except Exception:
        abort(404)
    if not username or 'admin:test_lab' not in get_user_permissions(username):
        abort(404)
    return str(username)


def _admin_testlab_normalize_cleanup_load_id(raw) -> tuple[str | None, str | None]:
    """Bound the manual autosplit cleanup selector before touching the load registry."""
    load_id = str(raw if raw is not None else "latest").strip() or "latest"
    if load_id in {"latest", "all", "expired"}:
        return load_id, None
    if len(load_id) > 64 or not _TESTLAB_LOAD_ID_RE.match(load_id):
        return None, "Invalid autosplit cleanup id"
    return load_id, None


def _coerce_idle_minutes_from_settings(settings_obj, minute_key: str, hour_key: str, default_hours: int) -> int:
    """Resolve custom-room TTL minutes with backwards-compatible hour fallback."""
    try:
        minute_value = settings_obj.get(minute_key)
        if minute_value is not None:
            return max(1, min(int(minute_value), 24 * 60 * 365))
    except Exception:
        pass
    try:
        hour_value = settings_obj.get(hour_key)
        if hour_value is not None:
            return max(1, min(int(hour_value) * 60, 24 * 60 * 365))
    except Exception:
        pass
    return max(1, min(int(default_hours) * 60, 24 * 60 * 365))


def _safe_db_identity() -> dict:
    try:
        return get_db_identity()
    except Exception:
        logging.exception("[ADMIN] failed to read database identity")
        return {"error": "unavailable"}


def _safe_schema_state() -> str:
    try:
        return get_schema_version()
    except Exception:
        logging.exception("[ADMIN] failed to read schema state")
        return "unknown"


def _admin_json_response(payload: dict, status: int = 200):
    """Return non-cacheable admin JSON without exposing browser-stale control state."""
    resp = jsonify(payload or {})
    resp.status_code = int(status or 200)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _admin_no_store_html_response(html: str):
    """Return non-cacheable admin HTML and prevent tokenized URLs leaking as referrers."""
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _coerce_admin_action_limit(raw, default_limit: int = 2500, maximum: int = 100000) -> tuple[int | None, str | None]:
    """Safely parse admin maintenance limits from JSON input."""
    try:
        if raw is None or str(raw).strip() == "":
            value = int(default_limit)
        else:
            value = int(raw)
    except Exception:
        return None, "limit must be an integer"
    return max(1, min(value, int(maximum or 100000))), None



_ADMIN_ACCOUNT_EMAIL_RE = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+$", re.IGNORECASE)


def _admin_normalize_account_email(raw_email: str) -> tuple[str, str | None]:
    """Normalize and validate admin-created account email input."""
    email = str(raw_email or "").strip().lower()
    if not email:
        return "", "Email required"
    if len(email) > 254 or any(ord(ch) < 32 for ch in email):
        return "", "Email is invalid"
    if email.count("@") != 1:
        return "", "Email is invalid"
    local, domain = email.split("@", 1)
    if not local or not domain or len(local) > 64:
        return "", "Email is invalid"
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return "", "Email is invalid"
    if not _ADMIN_ACCOUNT_EMAIL_RE.fullmatch(email):
        return "", "Email is invalid"
    labels = domain.split(".")
    if any((not label or label.startswith("-") or label.endswith("-")) for label in labels):
        return "", "Email is invalid"
    return email, None


def _admin_like_pattern(raw: str, *, prefix: bool = False, max_len: int = 96) -> str:
    """Build a bounded ILIKE pattern with wildcard characters escaped."""
    value = str(raw or "").strip()[:max(1, int(max_len or 96))]
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%" if prefix else f"%{escaped}%"


def _admin_table_columns(cur, table_name: str) -> set[str]:
    """Return known columns for a table without assuming every migration ran."""
    safe_table = str(table_name or "").strip()
    if not safe_table:
        return set()
    try:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = %s
               AND table_schema = ANY (current_schemas(false));
            """,
            (safe_table,),
        )
        return {str(r[0]) for r in (cur.fetchall() or []) if r and r[0]}
    except Exception:
        return set()


def _admin_table_exists(cur, table_name: str) -> bool:
    """Return whether a table exists in the active search path."""
    safe_table = str(table_name or "").strip()
    if not safe_table:
        return False
    try:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (safe_table,))
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        return False




def _admin_operation_error(action: str, exc: Exception | None = None, *, status: int = 500, ok_style: bool = False):
    """Return a generic admin error while logging the internal exception server-side.

    Admin endpoints should not echo database, filesystem, or stack details back
    to the browser.  Those details can disclose schema/table names, connection
    state, filesystem paths, or deployment topology to a compromised admin
    browser session.
    """
    if exc is not None:
        logging.exception("[ADMIN] %s failed", action)
    payload = {"error": "Admin operation failed", "code": "admin_operation_failed"}
    if ok_style:
        payload["ok"] = False
    return _admin_json_response(payload, status)


_ADMIN_AUDIT_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(password|pass|secret|token|jwt|api[_-]?key|credential|authorization|cookie|session|sid)\b\s*[:=]\s*([^\s,;}&]+)"
)
_ADMIN_AUDIT_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_ADMIN_TESTLAB_URL_RE = re.compile(r"(/admin/test[-_]lab/)[A-Za-z0-9._~:-]{8,}")
_ADMIN_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])(?:/[A-Za-z0-9_.@ -]+){3,}")


def _admin_safe_audit_text(value, *, max_len: int = 500) -> str:
    """Redact secret-like values before audit/diagnostic details hit admin JSON.

    The raw audit table remains useful for incident response, but browser-facing
    views should not casually expose credentials, session ids, tokenized Test Lab
    URLs, or long filesystem paths.  This is intentionally conservative: normal
    usernames, room names, IP addresses, short reasons, and counts still show.
    """
    text = str(value or "")
    if not text:
        return ""
    text = _ADMIN_TESTLAB_URL_RE.sub(r"\1[redacted]", text)
    text = _ADMIN_AUDIT_BEARER_RE.sub("Bearer [redacted]", text)
    text = _ADMIN_AUDIT_SECRET_VALUE_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = _ADMIN_ABSOLUTE_PATH_RE.sub("[path-redacted]", text)
    return text[: max(1, min(int(max_len or 500), 2000))]


def _admin_safe_audit_event(actor, action, target, timestamp, details) -> dict:
    return {
        "actor": _admin_safe_audit_text(actor, max_len=120),
        "action": _admin_safe_audit_text(action, max_len=120),
        "target": _admin_safe_audit_text(target, max_len=180) if target is not None else None,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "details": _admin_safe_audit_text(details, max_len=500),
    }


def _admin_recover_query_error(conn, label: str = "admin_query") -> None:
    """Recover a psycopg transaction after an optional diagnostics query fails."""
    try:
        conn.rollback()
    except Exception:
        logging.debug("%s rollback failed", label, exc_info=True)


def _admin_sanitize_preflight_snapshot(snapshot):
    """Return a browser-safe copy of a preflight/diagnostics snapshot.

    Preflight is an admin tool, but the raw object can include absolute local
    paths and redacted-but-still-topological DSNs/Redis URLs.  The panel needs
    status, counts, and summaries; it does not need full server filesystem paths.
    """
    if snapshot is None:
        return None
    if isinstance(snapshot, list):
        return [_admin_sanitize_preflight_snapshot(item) for item in snapshot]
    if not isinstance(snapshot, dict):
        return snapshot
    safe = {}
    for key, value in snapshot.items():
        key_s = str(key)
        low = key_s.lower()
        if low in {"dsn", "database_url", "message_queue", "shared_state_url"}:
            safe[key_s] = _admin_safe_audit_text(value, max_len=260) if value else value
        elif low == "dsn_parts":
            safe[key_s] = {"present": bool(value), "redacted": True}
        elif low == "identity":
            safe[key_s] = "available" if value else None
        elif low == "schema_state":
            safe[key_s] = str(value or "unknown")[:80]
        elif low in {"settings_file", "path"}:
            safe[key_s] = Path(str(value)).name if value else value
        elif low == "paths" and isinstance(value, dict):
            safe_paths = {}
            for name, meta in value.items():
                if isinstance(meta, dict):
                    safe_paths[str(name)] = {
                        "path_name": Path(str(meta.get("path") or "")).name if meta.get("path") else None,
                        "writable": bool(meta.get("writable")),
                        "error": _admin_safe_audit_text(meta.get("error"), max_len=220) if meta.get("error") else None,
                    }
                else:
                    safe_paths[str(name)] = meta
            safe[key_s] = safe_paths
        elif isinstance(value, (dict, list)):
            safe[key_s] = _admin_sanitize_preflight_snapshot(value)
        elif isinstance(value, str):
            safe[key_s] = _admin_safe_audit_text(value, max_len=1000)
        else:
            safe[key_s] = value
    return safe


# Process start time (best-effort) for uptime reporting in /admin/stats.
STARTED_AT = _utcnow()


def _global_broadcast_delivery_estimate() -> dict[str, int]:
    """Best-effort count of live Socket.IO recipients before a global emit.

    Socket.IO broadcast emits do not return an acknowledgement count. EchoChat
    keeps its own presence registry, backed by Redis when configured, so the
    admin panel can show a realistic delivery estimate instead of a hard-coded 0.
    """
    session_ids: set[str] = set()
    usernames: set[str] = set()

    try:
        if _state_connected_sessions_snapshot is not None:
            snapshot = _state_connected_sessions_snapshot() or {}
            for sid, sess in snapshot.items():
                sid_s = str(sid or "").strip()
                if sid_s:
                    session_ids.add(sid_s)
                if isinstance(sess, dict):
                    username = str(sess.get("username") or "").strip()
                    if username:
                        usernames.add(username)
    except Exception:
        session_ids.clear()
        usernames.clear()

    # Fallback for unusual imports or a partially unavailable snapshot helper.
    if not usernames:
        try:
            if _state_connected_usernames is not None:
                usernames.update(str(u).strip() for u in (_state_connected_usernames() or []) if str(u or "").strip())
        except Exception:
            pass

    if not session_ids and usernames and _state_user_sids is not None:
        for username in sorted(usernames):
            try:
                session_ids.update(str(s).strip() for s in (_state_user_sids(username) or []) if str(s or "").strip())
            except Exception:
                continue

    return {"sessions": len(session_ids), "users": len(usernames)}


def register_admin_tools(app, settings, socketio=None, limiter=None):
    """Register admin endpoints.


    socketio is optional; if provided, global_broadcast will emit live.
    
    """

    # Snapshot existing routes/endpoints so we can add robust alias rules at the end
    # (prevents admin UI 404s when URL prefixes drift between versions).
    _ecap_pre_rules = {r.rule for r in app.url_map.iter_rules()}
    _ecap_pre_endpoints = set(app.view_functions.keys())


    # --------------------------------------------------------------
    # Debug config endpoint (admin only, local by default)
    # --------------------------------------------------------------
    def _scrub(obj):
        # Redact likely-secret fields recursively.
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                kl = str(k).lower()
                if any(x in kl for x in ("password", "pass", "secret", "token", "jwt", "key")):
                    # Preserve shape but redact values.
                    out[k] = "***" if v not in (None, "", False, 0) else v
                elif kl in ("database_url", "db_connection_string", "database", "dsn"):
                    out[k] = redact_postgres_dsn(str(v)) if v else v
                else:
                    out[k] = _scrub(v)
            return out
        if isinstance(obj, list):
            return [_scrub(x) for x in obj]
        return obj

    @app.get("/api/debug/config")
    @require_permission("admin:settings")
    def _debug_config():
        # Config snapshots can expose deployment topology and redacted secret
        # shape.  Keep them behind the settings permission and the same recent
        # admin re-auth gate used by settings writers, even when the local-only
        # default is relaxed for remote diagnostics.
        status = _admin_reauth_status(_actor())
        if not status.get("ok"):
            return _admin_reauth_required_response(status)
        # In production we default to local-only. Can be overridden by setting:
        #   debug_config_allow_remote: true
        allow_remote = bool(settings.get("debug_config_allow_remote", False))
        if not allow_remote and not _is_local_request():
            return jsonify({"error": "Forbidden (local requests only)"}), 403

        settings_file_path = current_app.config.get("ECHOCHAT_SETTINGS_FILE")
        runtime_settings = current_app.config.get("ECHOCHAT_SETTINGS") or {}
        dsn = get_db_connection_string(runtime_settings if isinstance(runtime_settings, dict) else settings)

        payload = {
            "app": {
                "settings_file": settings_file_path,
                "config_file_default_name": CONFIG_FILE,
            },
            "db": {
                "configured_dsn": redact_postgres_dsn(dsn),
                "identity": _safe_db_identity(),
                "schema_version": _safe_schema_state(),
            },
            "settings": _scrub(runtime_settings),
        }
        return jsonify(payload)

    @app.get("/admin/docs/online-sound-packs")
    @require_permission("admin:settings")
    def _admin_doc_online_sound_packs():
        docs_dir = Path(__file__).resolve().parent / "docs"
        text = (docs_dir / "ONLINE_SOUND_PACKS.md").read_text(encoding="utf-8")
        resp = current_app.response_class(text, mimetype="text/markdown; charset=utf-8")
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    @app.get("/admin/docs/online-chat-sound-sources")
    @require_permission("admin:settings")
    def _admin_doc_online_chat_sound_sources():
        docs_dir = Path(__file__).resolve().parent / "docs"
        text = (docs_dir / "ONLINE_CHAT_SOUND_SOURCES.md").read_text(encoding="utf-8")
        resp = current_app.response_class(text, mimetype="text/markdown; charset=utf-8")
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp


    def _get_user_id(username: str) -> int | None:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s);", (username,))
            row = cur.fetchone()
        return row[0] if row else None

    def _canonical_user_or_error(value: str):
        """Return (stored_username, user_id, error_response) for admin account actions.

        Admin tools accept usernames from routes, forms, and legacy UI actions.
        Those inputs can differ in case from the stored account name.  Any action
        that writes/deletes account-owned rows must resolve the target once and
        then use that canonical spelling everywhere, otherwise ``alice`` can
        update the account row for ``Alice`` but leave exact-match rows such as
        messages, quotas, sanctions, reset tokens, or live sessions behind.
        """
        raw = str(value or "").strip()
        if not raw:
            return None, None, _admin_json_response({"ok": False, "error": "Username required"}, 400)
        if len(raw) > 64 or any(ord(ch) < 32 for ch in raw):
            return None, None, _admin_json_response({"ok": False, "error": "Invalid username"}, 400)
        try:
            conn = get_db()
            stored = canonical_username(conn, raw)
            if not stored:
                return None, None, _admin_json_response({"ok": False, "error": "User not found"}, 404)
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s LIMIT 1;", (stored,))
                row = cur.fetchone()
            if not row:
                return None, None, _admin_json_response({"ok": False, "error": "User not found"}, 404)
            return stored, int(row[0]), None
        except Exception as exc:
            return None, None, _admin_operation_error("resolve_user", exc, ok_style=True)

    def _ensure_admin_profile_runtime_schema() -> None:
        """Keep profile reports/badges admin routes safe on upgraded databases."""
        ensure_profile_post_engagement_schema()

    def _normalize_role_name(role_name: str) -> str:
        role_name = (role_name or "").strip().lower()
        aliases = {
            "user": "viewer",
            "member": "viewer",
            "basic": "viewer",
        }
        return aliases.get(role_name, role_name)

    def _effective_admin_expr(user_alias: str = "u") -> str:
        return f"""(
            COALESCE({user_alias}.is_admin, FALSE)
            OR EXISTS (
                SELECT 1
                  FROM user_roles ur
                  JOIN role_permissions rp ON rp.role_id = ur.role_id
                  JOIN permissions p ON p.id = rp.permission_id
                 WHERE ur.user_id = {user_alias}.id
                   AND p.name IN ('admin:basic')
            )
        )"""

    def _effective_admin_exists_sql(user_alias: str = "u") -> str:
        """Compatibility helper used by admin search/badge regression guards."""
        return _effective_admin_expr(user_alias)

    _PROTECTED_ROLES = {"admin", "moderator", "viewer"}
    _PERMISSION_DESCRIPTIONS = {
        "admin:basic": "Access the admin panel and general operations",
        "admin:settings": "Read and change server settings, media, file, torrent, and anti-abuse controls",
        "admin:audit": "Read admin audit, analytics, and moderation overview data",
        "admin:test_lab": "Launch and run Admin Test Lab diagnostics",
        "admin:create_user": "Create end-user accounts from the admin panel",
        "admin:delete_user": "Delete user accounts and related data",
        "admin:set_recovery_pin": "Set or reset a user's Recovery PIN",
        "admin:set_user_status": "Change account status, suspension, and visibility states",
        "admin:set_user_quota": "Change per-user quota limits",
        "admin:revoke_2fa": "Revoke a user's two-factor setup",
        "admin:broadcast": "Send a server-wide broadcast",
        "admin:assign_role": "Assign non-privileged roles to users",
        "admin:manage_roles": "Create, delete, and edit roles / permissions and privileged assignments",
        "admin:ban_ip": "Ban IP addresses and related sessions",
        "admin:reset_password": "Reset a user password",
        "admin:logout_user": "Force a user to sign out",
        "moderation:mute_user": "Mute users temporarily",
        "moderation:kick_user": "Kick users from rooms",
        "moderation:ban_room": "Ban a user from a room",
        "moderation:suspend_user": "Suspend user access",
        "moderation:shadowban": "Shadowban a user",
        "room:lock": "Lock or unlock rooms",
        "room:readonly": "Toggle room read-only mode",
        "room:clear": "Clear room chat history",
        "room:delete": "Delete rooms permanently",
        "profile:moderate": "Moderate profile posts, comments, reports, warnings, and badges",
        "user:delete_self": "Allow self-delete workflow",
        "user:edit_profile": "Edit profile information",
    }

    _DANGEROUS_PERMISSIONS = {
        "admin:basic", "admin:settings", "admin:test_lab", "admin:create_user", "admin:delete_user",
        "admin:set_recovery_pin", "admin:set_user_status", "admin:revoke_2fa", "admin:broadcast",
        "admin:manage_roles", "admin:ban_ip", "admin:reset_password", "admin:logout_user",
        "moderation:suspend_user", "moderation:shadowban", "room:clear", "room:delete", "profile:moderate",
    }

    _PRIVILEGE_ESCALATION_PERMISSIONS = {
        "admin:basic",
        "admin:settings",
        "admin:audit",
        "admin:test_lab",
        "admin:create_user",
        "admin:delete_user",
        "admin:set_recovery_pin",
        "admin:set_user_status",
        "admin:set_user_quota",
        "admin:revoke_2fa",
        "admin:broadcast",
        "admin:assign_role",
        "admin:manage_roles",
        "admin:ban_ip",
        "admin:reset_password",
        "admin:logout_user",
        "moderation:suspend_user",
        "moderation:shadowban",
        "room:delete",
    }
    _ADMIN_ROLE_MINIMUM_PERMISSIONS = {"admin:basic", "admin:settings", "admin:manage_roles"}
    _ROLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
    _PERMISSION_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[a-z][a-z0-9_-]{0,63}$")

    def _valid_role_name(role_name: str) -> bool:
        return bool(_ROLE_NAME_RE.fullmatch(str(role_name or "")))

    def _valid_permission_name(permission: str) -> bool:
        return bool(_PERMISSION_NAME_RE.fullmatch(str(permission or "")))

    def _actor_has_permission(permission: str) -> bool:
        """Check the current actor's live RBAC permissions without trusting session flags."""
        try:
            return str(permission or "") in set(get_user_permissions(_actor()))
        except Exception:
            return False

    def _require_actor_permission(permission: str, *, action: str | None = None):
        """Return a 403 response unless the current actor has ``permission``."""
        required = str(permission or "").strip()
        if required and _actor_has_permission(required):
            return None
        return jsonify({
            "ok": False,
            "error": "Permission denied",
            "required": required,
            "action": action or required,
        }), 403

    def _target_has_privileged_admin_permissions(username: str) -> bool:
        """Return True when a target account has admin/sensitive RBAC power."""
        target = str(username or "").strip()
        if not target:
            return False
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(u.is_admin, FALSE)
                       OR EXISTS (
                            SELECT 1
                              FROM user_roles ur
                              JOIN role_permissions rp ON rp.role_id = ur.role_id
                              JOIN permissions p ON p.id = rp.permission_id
                             WHERE ur.user_id = u.id
                               AND p.name = ANY(%s)
                       )
                  FROM users u
                 WHERE LOWER(u.username) = LOWER(%s);
                """,
                (list(_PRIVILEGE_ESCALATION_PERMISSIONS), target),
            )
            row = cur.fetchone()
        return bool(row and row[0])

    def _deny_privileged_target_without_admin(username: str, action: str):
        """Prevent lower-trust admins/moderators from acting on privileged accounts.

        A custom role may have one narrow admin permission such as password reset
        or forced logout.  That must not automatically permit changing an owner,
        full admin, or any account carrying privilege-escalation permissions.
        Privileged targets require role-management authority.
        """
        try:
            privileged = _target_has_privileged_admin_permissions(username)
        except Exception as exc:
            try:
                log_audit_event(_actor(), "privileged_target_check_failed", username, f"action={action}; error={type(exc).__name__}")
            except Exception:
                pass
            return _admin_json_response({
                "ok": False,
                "error": "privileged_target_check_failed",
                "message": f"Cannot {action} this account because the server could not verify whether it is privileged. The action was blocked safely.",
                "target": username,
                "required_check": "admin:manage_roles_target_guard",
            }, 500)
        if privileged and not _actor_has_permission("admin:manage_roles"):
            return _admin_json_response({
                "ok": False,
                "error": "privileged_target_requires_role_manager",
                "message": f"Cannot {action} an admin or privileged account without role-management access.",
                "target": username,
                "required": "admin:manage_roles",
            }, 403)
        return None

    def _bounded_int_from_form(name: str, default: int, minimum: int, maximum: int):
        raw = request.form.get(name)
        if raw is None:
            return int(default), None
        try:
            value = int(str(raw).strip())
        except Exception:
            return None, _admin_json_response({"ok": False, "error": f"{name} must be an integer"}, 400)
        if value < minimum or value > maximum:
            return None, _admin_json_response({"ok": False, "error": f"{name} must be between {minimum} and {maximum}"}, 400)
        return int(value), None

    def _admin_form_bool_or_error(name: str, default: bool = False):
        raw = request.form.get(name)
        if raw is None or str(raw).strip() == "":
            return bool(default), None
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True, None
        if text in {"0", "false", "no", "off", "disabled"}:
            return False, None
        return None, _admin_json_response({"ok": False, "error": f"{name} must be a boolean"}, 400)

    def _normalized_ip_or_error(value: str):
        raw = str(value or "").strip().strip("[]")
        if not raw:
            return None, _admin_json_response({"ok": False, "error": "Missing IP"}, 400)
        try:
            return str(ipaddress.ip_address(raw)), None
        except Exception:
            return None, _admin_json_response({"ok": False, "error": "Invalid IP address"}, 400)

    def _admin_reauth_rate_limited_response(actor: str):
        raw_limit = settings.get("rate_limit_admin_reauth") or "5@900"
        limit, window = parse_rate_limit_value(raw_limit, default_limit=5, default_window=900)
        key = f"admin_reauth:{str(actor or '-').lower()}:{get_request_ip()}"
        ok, retry_after = simple_rate_limit(key, limit=limit, window_sec=window)
        if ok:
            return None
        try:
            log_audit_event(actor, "admin_reauth_rate_limited", actor, f"retry_after={int(retry_after or 0)}")
        except Exception:
            pass
        return _admin_json_response({
            "ok": False,
            "error": "admin_reauth_rate_limited",
            "retry_after": int(max(1, retry_after or 1)),
        }, 429)

    def _is_self_target(username: str) -> bool:
        return str(username or "").strip().lower() == str(_actor() or "").strip().lower()

    def _deny_self_target(action: str):
        return jsonify({
            "ok": False,
            "error": "self_target_forbidden",
            "message": f"Cannot {action} your own admin account from the admin panel.",
        }), 403

    def _role_has_privilege_escalation_permissions(cur, role_name: str) -> bool:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM roles r
                  JOIN role_permissions rp ON rp.role_id = r.id
                  JOIN permissions p ON p.id = rp.permission_id
                 WHERE r.name = %s
                   AND p.name = ANY(%s)
            );
            """,
            (str(role_name or "").strip().lower(), list(_PRIVILEGE_ESCALATION_PERMISSIONS)),
        )
        return bool((cur.fetchone() or [False])[0])

    def _protected_role_change_requires_admin(role_name: str, permission: str | None = None) -> bool:
        role = str(role_name or "").strip().lower()
        perm = str(permission or "").strip().lower()
        return role in _PROTECTED_ROLES or perm in _PRIVILEGE_ESCALATION_PERMISSIONS

    def _permission_category(name: str) -> str:
        raw = str(name or "").strip().lower()
        prefix = raw.split(":", 1)[0] if raw else ""
        return {
            "admin": "Admin Core",
            "moderation": "Moderation",
            "room": "Rooms",
            "profile": "Profile Safety",
            "user": "Users",
        }.get(prefix, "Other")

    def _permission_meta(name: str) -> dict:
        perm = str(name or "").strip()
        return {
            "name": perm,
            "category": _permission_category(perm),
            "description": _PERMISSION_DESCRIPTIONS.get(perm, "Custom or project-specific permission"),
            "dangerous": perm in _DANGEROUS_PERMISSIONS,
        }

    def _admin_reason(raw, default: str = "Admin action", *, max_len: int = 240) -> str:
        """Return a bounded, single-line admin reason for storage/audit JSON."""
        text = str(raw if raw is not None else default).strip()
        if not text:
            text = str(default or "Admin action")
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
        return text[: max(1, int(max_len or 240))]

    def _normalized_room_or_error(value: str):
        room = str(value or "").strip()
        if not room:
            return None, _admin_json_response({"ok": False, "error": "Missing room"}, 400)
        if len(room) > 160:
            return None, _admin_json_response({"ok": False, "error": "Room name is too long"}, 400)
        if any(ord(ch) < 32 for ch in room):
            return None, _admin_json_response({"ok": False, "error": "Invalid room name"}, 400)
        return room, None

    def _canonical_room_or_error(value: str, *, require_existing: bool = True):
        """Return the stored room name for admin room/moderation actions.

        Admin tools must not create policy or sanction rows for a wrong-case room
        alias (for example ``general`` when the stored room is ``General``).  Room
        joins, sends, file checks, and admin list output all rely on one canonical
        room string, so privileged room actions resolve against ``chat_rooms``
        case-insensitively before writing lock/read-only/slowmode/ban state.
        """
        room, err = _normalized_room_or_error(value)
        if err is not None:
            return None, err
        if not require_existing:
            return room, None
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name
                      FROM chat_rooms
                     WHERE LOWER(name) = LOWER(%s)
                     ORDER BY CASE WHEN name = %s THEN 0 ELSE 1 END, name
                     LIMIT 1;
                    """,
                    (room, room),
                )
                row = cur.fetchone()
        except Exception as exc:
            return None, _admin_operation_error("resolve_room", exc, ok_style=True)
        if not row or not row[0]:
            return None, _admin_json_response({"ok": False, "error": "Room not found", "room": room}, 404)
        return str(row[0]).strip(), None

    def _delete_casefold_room_policy_rows(cur, table: str, room: str) -> None:
        """Remove wrong-case legacy policy rows before/while changing a room policy."""
        if table not in {"room_locks", "room_readonly", "room_slowmode"}:
            raise ValueError("unsupported room policy table")
        cur.execute(f"DELETE FROM {table} WHERE LOWER(room)=LOWER(%s) AND room <> %s;", (room, room))

    def _revoke_sessions_for_ip(ip: str, actor: str) -> dict:
        """Revoke active auth sessions/tokens seen from a banned IP address."""
        normalized = str(ip or "").strip()
        if not normalized:
            return {"revoked_sessions": 0, "revoked_tokens": 0, "affected_users": []}
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT username, session_id
                  FROM auth_sessions
                 WHERE ip_address = %s
                   AND revoked_at IS NULL;
                """,
                (normalized,),
            )
            session_rows = cur.fetchall() or []
            affected_users = sorted({str(row[0] or "").strip() for row in session_rows if row and row[0]})
            session_ids = [str(row[1] or "").strip() for row in session_rows if row and row[1]]
            cur.execute(
                """
                UPDATE auth_sessions
                   SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                       revoked_reason = %s
                 WHERE ip_address = %s
                   AND revoked_at IS NULL;
                """,
                ("ip_banned", normalized),
            )
            revoked_sessions = int(cur.rowcount or 0)
            if session_ids:
                cur.execute(
                    """
                    UPDATE auth_tokens
                       SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                     WHERE revoked_at IS NULL
                       AND (ip_address = %s OR session_id = ANY(%s));
                    """,
                    (normalized, session_ids),
                )
            else:
                cur.execute(
                    """
                    UPDATE auth_tokens
                       SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                     WHERE revoked_at IS NULL
                       AND ip_address = %s;
                    """,
                    (normalized,),
                )
            revoked_tokens = int(cur.rowcount or 0)
        return {
            "revoked_sessions": revoked_sessions,
            "revoked_tokens": revoked_tokens,
            "affected_users": affected_users,
        }

    _INCIDENT_PRESETS = {
        "soft_lockdown": {
            "room_slowmode_default_sec": 5,
            "max_mentions_per_message": 2,
            "friend_req_rate_limit": "2@60",
            "room_create_rate_limit": "2@300",
        },
        "hard_lockdown": {
            "allow_user_create_rooms": False,
            "giphy_enabled": False,
            "p2p_file_enabled": False,
            "room_slowmode_default_sec": 15,
            "max_mentions_per_message": 1,
            "friend_req_rate_limit": "1@300",
            "room_create_rate_limit": "1@600",
        },
        "raid_mode": {
            "allow_user_create_rooms": False,
            "giphy_enabled": False,
            "p2p_file_enabled": False,
            "room_slowmode_default_sec": 20,
            "max_mentions_per_message": 0,
            "max_links_per_message": 1,
            "max_magnets_per_message": 0,
            "friend_req_rate_limit": "1@600",
            "room_create_rate_limit": "1@900",
        },
        "silent_observe": {},
    }

    def _incident_snapshot() -> dict:
        mode_enabled = bool(settings.get("incident_mode_enabled", False))
        mode_name = str(settings.get("incident_mode_name") or "off")
        patch = settings.get("incident_mode_patch") or {}
        if not isinstance(patch, dict):
            patch = {}
        return {
            "enabled": mode_enabled,
            "mode": mode_name,
            "updated_at": settings.get("incident_mode_updated_at"),
            "updated_by": settings.get("incident_mode_updated_by"),
            "patch": patch,
        }

    def _apply_incident_mode_patch(mode_name: str, actor: str, *, persist: bool = False) -> dict:
        mode_key = str(mode_name or "").strip().lower()
        if mode_key not in _INCIDENT_PRESETS:
            raise ValueError("Unknown incident mode preset")
        patch = dict(_INCIDENT_PRESETS.get(mode_key) or {})
        runtime_patch = dict(patch)
        runtime_patch.update({
            "incident_mode_enabled": mode_key != "silent_observe",
            "incident_mode_name": mode_key,
            "incident_mode_patch": patch,
            "incident_mode_updated_at": _utcnow().isoformat(),
            "incident_mode_updated_by": actor or "system",
        })
        settings.update(runtime_patch)
        persisted = False
        if persist:
            persisted = bool(_persist_settings_patch(runtime_patch))
        try:
            log_audit_event(actor, "incident_mode_apply", mode_key, json.dumps({"patch": patch, "persisted": bool(persisted)}))
        except Exception:
            pass
        snapshot = _incident_snapshot()
        snapshot["persisted"] = bool(persisted)
        return snapshot

    def _disable_incident_mode(actor: str, *, persist: bool = False) -> dict:
        runtime_patch = {
            "incident_mode_enabled": False,
            "incident_mode_name": "off",
            "incident_mode_patch": {},
            "incident_mode_updated_at": _utcnow().isoformat(),
            "incident_mode_updated_by": actor or "system",
        }
        settings.update(runtime_patch)
        persisted = False
        if persist:
            persisted = bool(_persist_settings_patch(runtime_patch))
        try:
            log_audit_event(actor, "incident_mode_disable", "off", json.dumps({"persisted": bool(persisted)}))
        except Exception:
            pass
        snapshot = _incident_snapshot()
        snapshot["persisted"] = bool(persisted)
        return snapshot

    def _connected_usernames() -> list[str]:
        """Best-effort list of currently connected usernames."""
        if _state_connected_usernames is None:
            return []
        try:
            return list(_state_connected_usernames())
        except Exception:
            return []

    def _user_sids(username: str) -> list[str]:
        if _state_user_sids is None:
            return []
        try:
            return list(_state_user_sids(username))
        except Exception:
            return []

    def _room_policy_snapshot(room: str) -> dict:
        """Read current room policy flags from the DB (best-effort)."""
        room = (room or '').strip()
        locked = False
        readonly = False
        slowmode_seconds = 0
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT locked
                      FROM room_locks
                     WHERE LOWER(room) = LOWER(%s)
                     ORDER BY CASE WHEN room = %s THEN 0 ELSE 1 END
                     LIMIT 1;
                ''', (room, room))
                row = cur.fetchone()
                if row is not None:
                    locked = bool(row[0])
        except Exception:
            pass
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT readonly
                      FROM room_readonly
                     WHERE LOWER(room) = LOWER(%s)
                     ORDER BY CASE WHEN room = %s THEN 0 ELSE 1 END
                     LIMIT 1;
                ''', (room, room))
                row = cur.fetchone()
                if row is not None:
                    readonly = bool(row[0])
        except Exception:
            pass
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT seconds
                      FROM room_slowmode
                     WHERE LOWER(room) = LOWER(%s)
                     ORDER BY CASE WHEN room = %s THEN 0 ELSE 1 END
                     LIMIT 1;
                ''', (room, room))
                row = cur.fetchone()
                if row is not None and row[0] is not None:
                    slowmode_seconds = int(row[0])
        except Exception:
            pass

        return {
            'room': room,
            'locked': locked,
            'readonly': readonly,
            'slowmode_seconds': max(0, int(slowmode_seconds or 0)),
            'ts': datetime.now(timezone.utc).isoformat(),
        }

    def _policy_for_user(username: str, policy: dict) -> dict:
        """Compute can_send flags for a specific user given a policy snapshot."""
        perms = set()
        try:
            perms = set(get_user_permissions(username))
        except Exception:
            perms = set()

        bypass_lock = ('admin:basic' in perms) or ('room:lock' in perms)
        bypass_ro = ('admin:basic' in perms) or ('room:readonly' in perms)

        locked = bool(policy.get('locked'))
        readonly = bool(policy.get('readonly'))

        can_send = (not locked or bypass_lock) and (not readonly or bypass_ro)
        block_reason = None
        if not can_send:
            if readonly and not bypass_ro:
                block_reason = 'read_only'
            elif locked and not bypass_lock:
                block_reason = 'locked'
            else:
                block_reason = 'blocked'

        return {
            'can_send': bool(can_send),
            'can_override_lock': bool(bypass_lock),
            'can_override_readonly': bool(bypass_ro),
            'block_reason': block_reason,
        }

    def _emit_room_policy(room: str, actor: str | None = None) -> None:
        """Push current room policy to every connected member (per-user can_send)."""
        if not socketio or _state_connected_room_targets is None:
            return
        policy = _room_policy_snapshot(room)
        if actor:
            policy['set_by'] = actor

        try:
            targets = list(_state_connected_room_targets(room))
        except Exception:
            targets = []

        for sid, uname in targets:
            if not uname:
                continue
            payload = dict(policy)
            payload.update(_policy_for_user(uname, policy))
            try:
                socketio.emit('room_policy_state', payload, to=sid)
            except Exception:
                pass

    def _disconnect_user(username: str) -> int:
        """Hard-disconnect all active Socket.IO sessions for a user. Returns count."""
        if not socketio:
            return 0
        sids = _user_sids(username)
        n = 0
        for sid in sids:
            try:
                socketio.server.disconnect(sid)  # namespace '/'
                n += 1
            except Exception:
                pass
        return n

    def _revoke_and_disconnect_user_sessions(
        username: str,
        *,
        reason: str,
        actor: str | None = None,
        action: str = "role_changed",
        revoke_reason: str = "role_changed",
    ) -> int:
        """Best-effort force sign-out used when role changes invalidate live auth state."""
        username = str(username or "").strip()
        if not username:
            return 0

        payload = {
            "username": username,
            "reason": str(reason or "Your permissions changed. Please sign in again."),
            "by": str(actor or "system"),
            "action": str(action or "role_changed"),
        }
        try:
            if socketio is not None:
                for sid in _user_sids(username):
                    try:
                        socketio.emit("force_logout", payload, to=sid)
                        socketio.emit("admin_force_logout", payload, to=sid)
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            revoke_all_sessions_for_user(username, reason=revoke_reason)
        except Exception:
            pass

        return _disconnect_user(username)

    def _kick_user_from_room(username: str, room: str) -> int:
        """Force the user to leave a room (server-side). Returns number of sids affected."""
        if not socketio:
            return 0
        sids = _user_sids(username)
        affected = 0
        for sid in sids:
            try:
                socketio.server.leave_room(sid, room)
                affected += 1
            except Exception:
                continue
            # Best-effort: clear room pointer in the in-memory registry.
            if _state_update_connected_room is not None:
                try:
                    _state_update_connected_room(sid, None)
                except Exception:
                    pass
        return affected

    def _actor() -> str:
        """Return the best-effort acting username for audit logs.

        Important: Some admin endpoints may be allowed via the session-based
        admin override, which can bypass JWT verification. In those cases,
        calling get_jwt_identity() directly will raise. We therefore:
          1) optionally verify a JWT (if present) to populate context,
          2) use JWT identity if available,
          3) fall back to session username.
        """
        try:
            verify_jwt_in_request(optional=True)
            u = get_jwt_identity()
            if u:
                return str(u)
        except Exception:
            pass
        return str(session.get("username") or "unknown")

    def _current_auth_session_id() -> str | None:
        """Return the current auth-session id (JWT sid preferred)."""
        try:
            verify_jwt_in_request(optional=True)
            claims = get_jwt() or {}
            sid = str(claims.get("sid") or "").strip()
            if sid:
                return sid
        except Exception:
            pass
        try:
            sid = str(session.get("auth_session_id") or "").strip()
            if sid:
                return sid
        except Exception:
            pass
        return None

    def _resolve_idle_logout_seconds() -> float | None:
        idle_hours = settings.get("idle_logout_hours", 8)
        try:
            idle_hours = float(idle_hours) if idle_hours is not None else 8.0
        except Exception:
            idle_hours = 8.0
        return (idle_hours * 3600.0) if idle_hours and idle_hours > 0 else None

    def _admin_session_failure_response(error: str):
        reason = str(error or "session_revoked").strip() or "session_revoked"
        resp = _admin_json_response({"ok": False, "error": reason}, 401)
        try:
            unset_jwt_cookies(resp)
        except Exception:
            pass
        _clear_admin_reauth_state()
        try:
            session.clear()
        except Exception:
            pass
        return resp

    def _require_live_admin_session(*, touch_activity: bool = False, allow_missing_jwt: bool = False):
        try:
            verify_jwt_in_request(optional=allow_missing_jwt)
        except Exception:
            if allow_missing_jwt:
                return None, None, None
            return None, None, _admin_session_failure_response("unauthorized")

        claims = get_jwt() or {}
        sid = str(claims.get("sid") or "").strip()
        username = str(get_jwt_identity() or "").strip().lower()

        if not username or not sid:
            if allow_missing_jwt:
                return None, None, None
            return None, None, _admin_session_failure_response("no_session")

        try:
            state = get_auth_session_state(sid)
        except Exception:
            return None, None, _admin_session_failure_response("session_check_failed")

        if state is None or state.get("revoked_at") is not None:
            return None, None, _admin_session_failure_response("session_revoked")

        max_idle_seconds = _resolve_idle_logout_seconds()
        if max_idle_seconds is not None:
            last_activity = state.get("last_activity")
            if last_activity is not None:
                now = datetime.now(timezone.utc)
                idle_for = (now - last_activity).total_seconds()
                if idle_for > max_idle_seconds:
                    try:
                        revoke_auth_session(sid, reason="idle_timeout")
                    except Exception:
                        pass
                    return None, None, _admin_session_failure_response("idle_timeout")

        try:
            if touch_activity:
                touch_auth_session_activity(sid)
        except Exception:
            return None, None, _admin_session_failure_response("session_touch_failed")

        session["auth_session_id"] = sid
        return sid, state, None

    def _admin_tool_path_requires_live_session(path: str) -> bool:
        path = str(path or "")
        return (
            path == "/api/debug/config"
            or path == "/admin"
            or path.startswith("/admin/")
            or path == "/api/admin"
            or path.startswith("/api/admin/")
        )

    def _admin_route_has_declared_permission_gate(view_func) -> bool:
        """True when an admin view is protected by an explicit RBAC decorator.

        ``require_permission`` and ``require_admin`` mark their wrappers.  This
        runtime tripwire prevents a future /admin or /api/admin route from being
        added without an obvious permission gate.  The tokenized Test Lab routes
        are allowed because they call _admin_testlab_require_admin_or_404()
        internally after validating the random link token.
        """
        return bool(getattr(view_func, "_echochat_admin_route_gate", False))

    _ADMIN_ROUTE_INTERNAL_GATE_ENDPOINTS = {
        "admin_test_lab_legacy_page",
        "admin_test_lab_legacy_action",
        "admin_test_lab_page",
        "admin_test_lab_readiness",
        "admin_test_lab_run",
        "admin_test_lab_live_user_flow",
        "admin_test_lab_autosplit_cleanup",
    }

    @app.before_request
    def _enforce_live_admin_tool_session():
        if not _admin_tool_path_requires_live_session(request.path):
            return None
        _sid, _state, rejection = _require_live_admin_session(touch_activity=True, allow_missing_jwt=True)
        if rejection is not None:
            return rejection
        return None

    @app.before_request
    def _enforce_admin_route_permission_gate():
        if not _admin_tool_path_requires_live_session(request.path):
            return None
        endpoint = str(request.endpoint or "")
        if not endpoint:
            return None
        view_func = current_app.view_functions.get(endpoint)
        if view_func is not None and _admin_route_has_declared_permission_gate(view_func):
            return None
        if endpoint in _ADMIN_ROUTE_INTERNAL_GATE_ENDPOINTS:
            return None
        logging.error("Blocked admin route without declared RBAC gate: endpoint=%s path=%s", endpoint, request.path)
        return jsonify({"ok": False, "error": "admin_route_missing_permission_gate", "endpoint": endpoint}), 403

    def _fresh_admin_auth_window_seconds() -> int:
        try:
            raw = settings.get("admin_fresh_auth_window_seconds", 28800)
            window = int(raw if raw is not None else 28800)
        except Exception:
            window = 28800
        if window < 0:
            window = 0
        if window > 86400:
            window = 86400
        return window

    def _admin_reauth_once_per_session_enabled() -> bool:
        """Return whether one password confirmation unlocks the current admin login.

        This keeps the safety gate tied to the current auth-session id, but avoids
        repeatedly prompting the same admin during one normal admin-panel session.
        Set admin_reauth_once_per_session=false to restore the timed-window mode.
        """
        raw = settings.get("admin_reauth_once_per_session", True)
        if isinstance(raw, str):
            return raw.strip().lower() not in {"0", "false", "no", "off", "timed", "window"}
        return bool(raw)

    def _clear_admin_reauth_state() -> None:
        for key in ("admin_reauth_user", "admin_reauth_sid", "admin_reauth_at"):
            try:
                session.pop(key, None)
            except Exception:
                pass

    def _mark_admin_reauthenticated(actor: str, sid: str | None) -> int:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        session["admin_reauth_user"] = str(actor or "")
        session["admin_reauth_sid"] = str(sid or "")
        session["admin_reauth_at"] = now_ts
        session.modified = True
        return now_ts

    def _admin_reauth_status(actor: str | None = None) -> dict:
        actor = str(actor or _actor() or "")
        sid = str(_current_auth_session_id() or "")
        window = _fresh_admin_auth_window_seconds()
        once_per_session = _admin_reauth_once_per_session_enabled()
        if window <= 0:
            return {
                "ok": True,
                "required": False,
                "reason": "disabled",
                "window_seconds": 0,
                "remaining_seconds": None,
                "confirmed_at": None,
                "sid": sid or None,
                "once_per_session": bool(once_per_session),
            }

        stored_user = str(session.get("admin_reauth_user") or "")
        stored_sid = str(session.get("admin_reauth_sid") or "")
        stored_at = session.get("admin_reauth_at")

        if not actor or not sid:
            _clear_admin_reauth_state()
            return {
                "ok": False,
                "required": True,
                "reason": "missing_identity",
                "window_seconds": window,
                "remaining_seconds": 0,
                "confirmed_at": None,
                "sid": sid or None,
                "once_per_session": bool(once_per_session),
            }

        if stored_user != actor or stored_sid != sid:
            if stored_user or stored_sid or stored_at:
                _clear_admin_reauth_state()
            return {
                "ok": False,
                "required": True,
                "reason": "missing",
                "window_seconds": window,
                "remaining_seconds": 0,
                "confirmed_at": None,
                "sid": sid,
                "once_per_session": bool(once_per_session),
            }

        try:
            confirmed_at = int(stored_at)
        except Exception:
            _clear_admin_reauth_state()
            return {
                "ok": False,
                "required": True,
                "reason": "missing",
                "window_seconds": window,
                "remaining_seconds": 0,
                "confirmed_at": None,
                "sid": sid,
                "once_per_session": bool(once_per_session),
            }

        now_ts = int(datetime.now(timezone.utc).timestamp())
        age = max(0, now_ts - confirmed_at)
        if not once_per_session and age > window:
            _clear_admin_reauth_state()
            return {
                "ok": False,
                "required": True,
                "reason": "expired",
                "window_seconds": window,
                "remaining_seconds": 0,
                "confirmed_at": confirmed_at,
                "sid": sid,
                "once_per_session": False,
            }

        return {
            "ok": True,
            "required": False,
            "reason": "session_fresh" if once_per_session else "fresh",
            "window_seconds": 0 if once_per_session else window,
            "remaining_seconds": None if once_per_session else max(0, window - age),
            "confirmed_at": confirmed_at,
            "sid": sid,
            "once_per_session": bool(once_per_session),
        }

    def _admin_reauth_required_response(status: dict):
        return _admin_json_response(
            {
                "ok": False,
                "error": "Recent admin password confirmation required",
                "code": "admin_reauth_required",
                "reauth_required": True,
                "reason": status.get("reason") or "missing",
                "window_seconds": int(status.get("window_seconds") or 0),
                "remaining_seconds": int(status.get("remaining_seconds") or 0),
                "once_per_session": bool(status.get("once_per_session")),
                "sid": status.get("sid"),
            },
            428,
        )

    def require_recent_admin_auth(func):
        """Require a recent password confirmation for high-risk admin writes."""

        @wraps(func)
        def wrapper(*args, **kwargs):
            actor = _actor()
            status = _admin_reauth_status(actor)
            if status.get("ok"):
                return func(*args, **kwargs)
            return _admin_reauth_required_response(status)

        return wrapper

    @app.route("/admin/auth/status", methods=["GET"])
    @require_permission("admin:basic")
    def admin_auth_status():
        actor = _actor()
        status = _admin_reauth_status(actor)
        return _admin_json_response(
            {
                "ok": True,
                "actor": actor,
                "reauth_required": bool(status.get("required")),
                "reason": status.get("reason"),
                "window_seconds": int(status.get("window_seconds") or 0),
                "remaining_seconds": status.get("remaining_seconds"),
                "confirmed_at": status.get("confirmed_at"),
                "once_per_session": bool(status.get("once_per_session")),
                "sid": status.get("sid"),
            }
        )

    @app.route("/admin/auth/confirm", methods=["POST"])
    @require_permission("admin:basic")
    def admin_auth_confirm():
        actor = _actor()
        sid = _current_auth_session_id()
        raw_password = None
        try:
            raw_password = request.form.get("current_password")
        except Exception:
            raw_password = None
        if raw_password is None:
            try:
                raw_password = (request.get_json(silent=True) or {}).get("current_password")
            except Exception:
                raw_password = None
        raw_password = str(raw_password or "")
        if not raw_password:
            return _admin_json_response({"ok": False, "error": "Missing current_password"}, 400)
        rate_limited = _admin_reauth_rate_limited_response(actor)
        if rate_limited is not None:
            return rate_limited

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT password FROM users WHERE LOWER(username) = LOWER(%s);", (actor,))
                row = cur.fetchone()
            if not row or not row[0]:
                _clear_admin_reauth_state()
                return _admin_json_response({"ok": False, "error": "User not found"}, 404)

            ok, upgraded_hash = verify_password_and_upgrade(raw_password, row[0])
            if not ok:
                _clear_admin_reauth_state()
                try:
                    log_audit_event(actor, "admin_reauth_failed", actor, f"sid={sid or '-'}")
                except Exception:
                    pass
                return _admin_json_response({"ok": False, "error": "Password confirmation failed"}, 403)

            if upgraded_hash:
                try:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET password = %s WHERE LOWER(username) = LOWER(%s);", (upgraded_hash, actor))
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

            confirmed_at = _mark_admin_reauthenticated(actor, sid)
            try:
                log_audit_event(actor, "admin_reauth_ok", actor, f"sid={sid or '-'}")
            except Exception:
                pass

            status = _admin_reauth_status(actor)
            return _admin_json_response(
                {
                    "ok": True,
                    "actor": actor,
                    "confirmed_at": confirmed_at,
                    "window_seconds": int(status.get("window_seconds") or 0),
                    "remaining_seconds": status.get("remaining_seconds"),
                    "once_per_session": bool(status.get("once_per_session")),
                    "sid": status.get("sid") or sid,
                }
            )
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return _admin_operation_error("admin_reauth_confirm", exc, ok_style=True)

    # ── Snapshot / stats ───────────────────────────────────────────
    @app.route("/admin/stats")
    @require_permission("admin:basic")
    def admin_stats():
        """Lightweight operational stats for the injected admin panel."""
        registered = 0
        online_db = 0
        rooms = 0
        pg_version = None
        db_error = None
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users;")
                registered = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM users WHERE online = TRUE;")
                online_db = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM chat_rooms;")
                rooms = int(cur.fetchone()[0])

                # Optional: Postgres version string (for diagnostics).
                try:
                    cur.execute("SHOW server_version;")
                    pg_version = (cur.fetchone() or [None])[0]
                except Exception:
                    pg_version = None
        except Exception:
            logging.exception("[ADMIN] stats database snapshot failed")
            db_error = "unavailable"

        # Prefer live Socket.IO roster if available.
        live = _connected_usernames()
        online_live = len(live) if live else online_db

        # Voice snapshot (best-effort).
        voice_rooms = 0
        voice_total_users = 0
        voice_by_room = {}
        if VOICE_ROOMS_LOCK is not None:
            try:
                with VOICE_ROOMS_LOCK:
                    voice_rooms = len(VOICE_ROOMS or {})
                    for room, users in (VOICE_ROOMS or {}).items():
                        c = len(users or [])
                        voice_total_users += c
                        voice_by_room[str(room)] = c
            except Exception:
                voice_rooms = 0
                voice_total_users = 0
                voice_by_room = {}

        uptime_seconds = max(0, int((_utcnow() - STARTED_AT).total_seconds()))

        last_preflight = current_app.config.get("ECHOCHAT_LAST_PREFLIGHT") or {}

        return _admin_json_response(
            {
                # Back-compat keys
                "registered_users": registered,
                "online_users": online_live,
                "online_usernames": live,
                "rooms": rooms,
                "server_time": _utcnow().isoformat(),

                # Extra ops detail
                "uptime_seconds": uptime_seconds,
                "postgres_version": pg_version,
                "db_error": db_error,
                "connected_sessions": int(len(live) or 0),
                "voice_rooms": voice_rooms,
                "voice_total_users": voice_total_users,
                "voice_by_room": voice_by_room,
                "last_preflight_overall": last_preflight.get("overall"),
                "last_preflight_time": last_preflight.get("timestamp"),
                "last_preflight_counts": last_preflight.get("counts") or {},
                "settings_snapshot": {
                    "voice_enabled": echo_voice_bool(settings, "voice_enabled", True),
                    "voice_max_room_peers": echo_voice_room_limit(settings),
                    "p2p_file_enabled": bool(settings.get("p2p_file_enabled", True)),
                    "giphy_enabled": bool(settings.get("giphy_enabled", True)),
                    "health_endpoint_enabled": bool(settings.get("enable_health_check_endpoint", False)),
                    "av_mode": str(settings.get("av_mode") or ("echo" if echo_voice_bool(settings, "webcam_enabled", True) else "standard")),
                    "webcam_enabled": echo_voice_bool(settings, "webcam_enabled", True),
                    "webcam_quality": echo_webcam_quality(settings),
                    "webcam_codec_strategy": str(settings.get("webcam_codec_strategy") or "prefer-compatible"),
                    "webcam_approval_mode": webcam_policy(settings).get("webcam_approval_mode"),
                    "webcam_max_viewers": webcam_policy(settings).get("webcam_max_viewers"),
                    "allow_user_create_rooms": bool(settings.get("allow_user_create_rooms", True)),
                    "autoscale_rooms_enabled": bool(settings.get("autoscale_rooms_enabled", True)),
                    "autoscale_room_capacity": max(2, min(int(settings.get("autoscale_room_capacity", 30) or 30), 5000)),
                    "autoscale_room_idle_minutes": max(1, min(int(settings.get("autoscale_room_idle_minutes", 30) or 30), 10080)),
                },
                "incident": _incident_snapshot(),
            }
        )


    @app.route("/admin/diagnostics")
    @require_permission("admin:audit")
    @require_recent_admin_auth
    def admin_diagnostics():
        runtime_ctx = {
            "async_mode": current_app.config.get("ECHOCHAT_SOCKETIO_ASYNC_MODE"),
            "ws_enabled": current_app.config.get("ECHOCHAT_WS_ENABLED"),
            "message_queue": current_app.config.get("ECHOCHAT_SOCKETIO_MESSAGE_QUEUE"),
        }
        startup_snapshot = current_app.config.get("ECHOCHAT_STARTUP_PREFLIGHT")
        current = run_preflight(
            settings,
            settings_file=current_app.config.get("ECHOCHAT_SETTINGS_FILE"),
            init_db_pool_if_needed=False,
            runtime_context=runtime_ctx,
        )
        current_app.config["ECHOCHAT_LAST_PREFLIGHT"] = current
        return _admin_json_response(
            {
                "ok": True,
                "current": _admin_sanitize_preflight_snapshot(current),
                "startup": _admin_sanitize_preflight_snapshot(startup_snapshot),
                "db_identity": "available" if _safe_db_identity() else "unavailable",
                "schema_state": _safe_schema_state(),
                "redacted": True,
            }
        )

    # ── Security dashboard ─────────────────────────────────────
    @app.route("/admin/security/status", methods=["GET", "POST"])
    @require_permission("admin:basic")
    def admin_security_status():
        """Summarize encryption, secret, Test Lab, and privacy-retention posture."""
        if request.method == "POST":
            status = _admin_reauth_status(_actor())
            if not status.get("ok"):
                return _admin_reauth_required_response(status)

        retention_run = None
        # legacy guard string: apply_privacy_retention(settings, limit=2500)
        profile_migration_run = None
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            action = str(payload.get("action") or "privacy_retention").strip().lower()
            raw_limit = payload.get("limit")
            default_limit = 100000 if action in {"finish_security_setup", "finish_security_checklist"} else 2500
            limit, limit_error = _coerce_admin_action_limit(raw_limit, default_limit=default_limit, maximum=100000)
            if limit_error:
                return _admin_json_response({"ok": False, "error": limit_error}, 400)
            try:
                if action in {"privacy_retention", "run_retention"}:
                    retention_run = apply_privacy_retention(settings, limit=limit)
                elif action in {"create_security_backup", "backup_security_fields"}:
                    profile_migration_run = create_security_backup("manual", settings, limit=limit)
                elif action in {"restore_latest_security_backup", "restore_security_backup"}:
                    profile_migration_run = restore_security_backup(payload.get("filename"), settings)
                elif action in {"encrypt_plaintext_profile_fields", "bulk_encrypt_profile_fields"}:
                    backup = create_security_backup("before-profile-field-encryption", settings, limit=limit)
                    if not backup.get("ok"):
                        profile_migration_run = {"ok": False, "mode": "encrypt_plaintext", "error": backup.get("error") or "Security backup failed", "backup": backup}
                    else:
                        profile_migration_run = encrypt_plaintext_profile_fields(settings, limit=limit)
                        profile_migration_run["backup"] = backup
                elif action in {"rotate_profile_field_key", "rotate_profile_fields"}:
                    backup = create_security_backup("before-profile-field-key-rotation", settings, limit=limit)
                    if not backup.get("ok"):
                        profile_migration_run = {"ok": False, "mode": "rotate", "error": backup.get("error") or "Security backup failed", "backup": backup}
                    else:
                        profile_migration_run = rotate_profile_field_envelopes(settings, limit=limit)
                        profile_migration_run["backup"] = backup
                elif action in {"encrypt_plaintext_emails", "bulk_encrypt_emails"}:
                    backup = create_security_backup("before-email-encryption", settings, limit=limit)
                    if not backup.get("ok"):
                        profile_migration_run = {"ok": False, "mode": "encrypt_plaintext_emails", "error": backup.get("error") or "Security backup failed", "backup": backup}
                    else:
                        profile_migration_run = encrypt_plaintext_emails(settings, limit=limit)
                        profile_migration_run["backup"] = backup
                elif action in {"finish_security_setup", "finish_security_checklist"}:
                    backup = create_security_backup("finish-security-setup", settings, limit=limit)
                    steps = [{"key": "security_backup", "label": "Create encrypted security backup", "result": backup}]
                    profile_migration_run = {
                        "ok": bool(backup.get("ok")),
                        "mode": "finish_security_setup",
                        "updated_users": 0,
                        "updated_fields": 0,
                        "steps": steps,
                        "backup": backup,
                    }
                    if not backup.get("ok"):
                        profile_migration_run["error"] = backup.get("error") or "Security backup failed; no rewrite actions were run"
                    else:
                        profile_run = encrypt_plaintext_profile_fields(settings, limit=limit)
                        steps.append({"key": "encrypt_profile_fields", "label": "Encrypt old phone/address/location rows", "result": profile_run})
                        email_run = encrypt_plaintext_emails(settings, limit=limit)
                        steps.append({"key": "encrypt_emails", "label": "Encrypt old email rows", "result": email_run})
                        retention_run = apply_privacy_retention(settings, limit=limit)
                        steps.append({"key": "privacy_retention", "label": "Run IP/user-agent privacy retention", "result": retention_run})
                        for run in (profile_run, email_run):
                            profile_migration_run["updated_users"] += int(run.get("updated_users") or 0)
                            profile_migration_run["updated_fields"] += int(run.get("updated_fields") or 0)
                        failed = [st for st in steps if isinstance(st.get("result"), dict) and st["result"].get("ok") is False]
                        if failed:
                            profile_migration_run["ok"] = False
                            profile_migration_run["error"] = "; ".join(str(st["result"].get("error") or st.get("label")) for st in failed)
                else:
                    return _admin_json_response({"ok": False, "error": "Unknown security action"}, 400)
            except Exception as exc:
                logging.exception("[ADMIN] security status action failed: %s", action)
                if action in {"privacy_retention", "run_retention"}:
                    retention_run = {"ok": False, "error": "Security operation failed"}
                else:
                    profile_migration_run = {"ok": False, "error": "Security operation failed"}

        warnings = []
        checks = []

        def add_check(key: str, label: str, ok: bool, summary: str, *, level: str | None = None):
            lvl = level or ("ok" if ok else "warn")
            checks.append({"key": key, "label": label, "ok": bool(ok), "level": lvl, "summary": summary})
            if not ok:
                warnings.append({"key": key, "label": label, "summary": summary})

        dm_required = bool(settings.get("require_dm_e2ee", True))
        dm_plain_fallback = bool(settings.get("allow_plaintext_dm_fallback", False))
        group_required = bool(settings.get("require_group_e2ee", True))
        private_room_required = bool(settings.get("require_private_room_e2ee", True))
        all_room_required = bool(settings.get("require_room_e2ee", False))
        profile_encrypt = bool(settings.get("encrypt_sensitive_profile_fields", True))
        email_encrypt = bool(settings.get("encrypt_email_at_rest", True))
        profile_key = bool(sensitive_field_key_available(settings))
        email_field_key = bool(email_field_key_available(settings))
        email_hash_key = bool(email_hash_key_available(settings))
        secrets_persist = bool(persist_secrets_enabled(settings))
        backup_encrypt = bool(security_backup_encryption_enabled(settings))
        backup_key = bool(security_backup_key_available(settings))

        add_check("dm_e2ee", "DM E2EE required", dm_required and not dm_plain_fallback, "Private messages fail closed unless encrypted.")
        add_check("group_e2ee", "Group E2EE required", group_required, "Group-chat plaintext messages are blocked server-side.")
        add_check("private_room_e2ee", "Private-room E2EE required", private_room_required, "Invite-only/private custom rooms block plaintext room messages.")
        add_check("all_room_e2ee", "All-room E2EE strict mode", all_room_required, "Public rooms are plaintext by default for moderation/search. Strict mode requires encrypted envelopes in every room.", level=("ok" if all_room_required else "warn"))
        previous_profile_keys = bool(sensitive_field_previous_keys_available(settings))
        add_check("profile_field_key", "Profile-field encryption key", (not profile_encrypt) or profile_key, "Set ECHOCHAT_PROFILE_FIELD_KEY or generated stable secrets for phone/address/location encryption.")
        add_check("email_at_rest", "Email encrypted at rest", (not email_encrypt) or (email_field_key and email_hash_key), "Set ECHOCHAT_EMAIL_FIELD_KEY and ECHOCHAT_EMAIL_HASH_KEY before encrypting stored emails.")
        add_check("security_backup_encryption", "Encrypted security backups", (not backup_encrypt) or backup_key, "Set ECHOCHAT_SECURITY_BACKUP_KEY or generated stable crypto keys; new security backups are .json.enc by default.")
        add_check("profile_field_previous_keys", "Profile-field previous keys", True, "During rotation, put old keys in ECHOCHAT_PROFILE_FIELD_PREVIOUS_KEYS until the rotation tool rewrites old envelopes.", level=("warn" if previous_profile_keys else "ok"))
        add_check("secret_persistence", "Config secret persistence", not secrets_persist, "Production should keep secrets in env/secret manager, not server_config.json.", level=("ok" if not secrets_persist else "warn"))
        add_check("testlab_random", "Test Lab randomized links", True, "Predictable Test Lab pages stay dark; random URLs are session-bound and short-lived.")
        add_check("testlab_referrer", "Test Lab no-referrer policy", True, "Tokenized Test Lab pages send Referrer-Policy: no-referrer.")
        add_check("testlab_log_redaction", "Test Lab token log redaction", True, "Werkzeug/audit logs redact /admin/test_lab/<token> URL segments.")

        encrypted_counts = {"users_total": 0, "phone_encrypted": 0, "address_encrypted": 0, "location_encrypted": 0}
        try:
            encrypted_counts = profile_field_encryption_counts(settings)
        except Exception as exc:
            encrypted_counts["error"] = str(exc)
        email_counts = {"users_total": 0, "email_plaintext": 0, "email_encrypted": 0, "email_hash_present": 0}
        try:
            email_counts = email_encryption_counts(settings)
        except Exception as exc:
            email_counts["error"] = str(exc)
        backups = []
        try:
            backups = list_security_backups(settings, limit=5)
        except Exception:
            backups = []

        all_room_impact = public_room_e2ee_impact_report(settings)
        if all_room_required:
            add_check("all_room_moderation_impact", "All-room E2EE moderation/search impact", False, all_room_impact.get("summary") or "Public-room body moderation/search is limited by strict E2EE.", level="warn")
        else:
            add_check("all_room_moderation_impact", "All-room E2EE moderation/search impact", True, "Public-room plaintext moderation/search impact is avoided while all-room strict mode remains off.")

        try:
            retention = privacy_retention_counts(settings)
        except Exception as exc:
            retention = {"enabled": False, "error": str(exc)}

        try:
            retention_enabled = bool(retention.get("enabled")) and int(retention.get("ip_user_agent_retention_days") or 0) > 0
        except Exception:
            retention_enabled = False
        add_check("privacy_retention", "IP/UA privacy retention", retention_enabled, "Old session/token/password-reset IP and user-agent values are hash-retained.")
        try:
            profile_plain_left = int(encrypted_counts.get("phone_plaintext") or 0) + int(encrypted_counts.get("address_plaintext") or 0) + int(encrypted_counts.get("location_plaintext") or 0)
        except Exception:
            profile_plain_left = 0
        try:
            email_plain_left = int(email_counts.get("email_plaintext") or 0) + int(email_counts.get("email_hash_missing") or 0)
        except Exception:
            email_plain_left = 0
        latest_backup_encrypted = bool(backups and backups[0].get("encrypted"))
        finish_ready = (profile_plain_left == 0 and email_plain_left == 0 and retention_enabled and ((not backup_encrypt) or latest_backup_encrypted))
        add_check(
            "finish_security_setup",
            "Finish Security Setup checklist",
            finish_ready,
            "One-click action creates an encrypted backup, encrypts old profile/email rows, and runs privacy retention.",
            level=("ok" if finish_ready else "warn"),
        )

        return _admin_json_response({
            "ok": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall": "warn" if warnings else "ok",
            "warnings": warnings,
            "checks": checks,
            "settings": {
                "require_dm_e2ee": dm_required,
                "allow_plaintext_dm_fallback": dm_plain_fallback,
                "require_group_e2ee": group_required,
                "require_private_room_e2ee": private_room_required,
                "require_room_e2ee": all_room_required,
                "encrypt_sensitive_profile_fields": profile_encrypt,
                "encrypt_email_at_rest": email_encrypt,
                "profile_field_key_available": profile_key,
                "profile_field_previous_keys_available": previous_profile_keys,
                "email_field_key_available": email_field_key,
                "email_hash_key_available": email_hash_key,
                "security_backup_encryption_enabled": backup_encrypt,
                "security_backup_key_available": backup_key,
                "persist_secrets_enabled": secrets_persist,
            },
            "encrypted_profile_counts": encrypted_counts,
            "encrypted_email_counts": email_counts,
            "security_backups": backups,
            "profile_migration_run": profile_migration_run,
            "all_room_e2ee_impact": all_room_impact,
            "privacy_retention": retention,
            "retention_run": retention_run,
            "security_setup_checklist": {
                "profile_plaintext_fields_remaining": profile_plain_left,
                "email_plaintext_or_hash_missing_remaining": email_plain_left,
                "latest_backup_encrypted": latest_backup_encrypted,
                "ready": finish_ready,
            },
        })

    # ── Runtime settings (admin GUI) ──────────────────────────────
    def _settings_path() -> Path:
        """Return the live settings JSON path (best-effort)."""
        p = (current_app.config.get("ECHOCHAT_SETTINGS_FILE") or CONFIG_FILE) if current_app else CONFIG_FILE
        return Path(str(p))

    _settings_persist_report: dict[str, object] = {}

    def _json_equivalent(a, b) -> bool:
        try:
            return json.dumps(a, sort_keys=True, separators=(",", ":"), ensure_ascii=False) == json.dumps(b, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            return a == b

    def _settings_persistence_meta(raw_patch: dict, safe_patch: dict, *, persisted: bool, error: str | None = None) -> dict:
        raw_patch = dict(raw_patch or {})
        safe_patch = dict(safe_patch or {})
        runtime_only = sorted(k for k in raw_patch.keys() if k not in safe_patch)
        redacted_nested = sorted(k for k in raw_patch.keys() if k in safe_patch and not _json_equivalent(raw_patch.get(k), safe_patch.get(k)))
        meta = {
            "persisted": bool(persisted),
            "secret_persistence_enabled": bool(persist_secrets_enabled(settings)),
            "persisted_keys": sorted(safe_patch.keys()) if persisted else [],
            "runtime_only_keys": runtime_only,
            "redacted_nested_keys": redacted_nested,
        }
        if runtime_only or redacted_nested:
            meta["note"] = "Some secret or credential fields were applied to runtime but intentionally omitted or redacted from server_config.json unless secret persistence is explicitly enabled."
        if error:
            meta["error"] = str(error)[:160]
        return meta

    def _last_settings_persistence_meta() -> dict:
        return dict(_settings_persist_report or {})

    def _persist_settings_patch(patch: dict) -> bool:
        """Persist a small patch into the settings JSON without clobbering other keys.

        The return value stays boolean for older callers, while
        ``_last_settings_persistence_meta()`` exposes which keys were actually
        written and which secret/nested credential values were runtime-only.
        """
        nonlocal _settings_persist_report
        raw_patch = dict(patch or {})
        try:
            safe_patch = scrub_patch_for_persist(raw_patch, settings)
            if not safe_patch:
                # Likely only secret keys were supplied while persistence is disabled.
                _settings_persist_report = _settings_persistence_meta(raw_patch, safe_patch, persisted=False)
                return False
            path = _settings_path()
            existing = {}
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8") or "{}")
                    if not isinstance(existing, dict):
                        existing = {}
                except Exception:
                    # Back up invalid settings file rather than overwriting it silently.
                    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                    try:
                        bad = path.with_suffix(path.suffix + f".bad-{ts}")
                        path.rename(bad)
                    except Exception:
                        pass
                    existing = {}

            merged = dict(existing)
            merged.update(safe_patch or {})
            path.parent.mkdir(parents=True, exist_ok=True)
            # Re-evaluate the persistence policy against the merged settings,
            # not the old file. This prevents a dev→production save from
            # keeping secrets merely because the previous config still allowed
            # secret persistence.
            path.write_text(json.dumps(scrub_secrets_for_persist(merged), indent=2), encoding="utf-8")
            _settings_persist_report = _settings_persistence_meta(raw_patch, safe_patch, persisted=True)
            return True
        except Exception as exc:
            _settings_persist_report = _settings_persistence_meta(raw_patch, {}, persisted=False, error=exc.__class__.__name__)
            return False

    def _enforce_voice_room_limit(max_peers: int) -> dict:
        """If max_peers > 0, randomly disconnect extra voice users to satisfy the limit."""
        result = {"kicked": 0, "kicked_by_room": {}}
        if not socketio or VOICE_ROOMS_LOCK is None:
            return result
        if int(max_peers) <= 0:
            return result

        kicked: list[tuple[str, str]] = []  # (room, username)
        rosters_after: dict[str, list[str]] = {}

        with VOICE_ROOMS_LOCK:
            for room, users in list((VOICE_ROOMS or {}).items()):
                if not users:
                    continue
                if len(users) <= max_peers:
                    continue
                excess = len(users) - max_peers
                drop = random.sample(list(users), k=excess)
                for u in drop:
                    try:
                        users.discard(u)
                    except Exception:
                        pass
                    kicked.append((room, u))
                # Clean empty rooms
                if not users:
                    try:
                        del VOICE_ROOMS[room]
                    except Exception:
                        pass
                else:
                    rosters_after[room] = sorted(users)

        # Notify clients outside the lock.
        for room, u in kicked:
            result["kicked_by_room"].setdefault(room, []).append(u)
            # Tell the user(s) they were removed from voice.
            try:
                for sid in _user_sids(u):
                    socketio.emit(
                        "voice_room_forced_leave",
                        {"room": room, "reason": "voice_limit_reduced", "limit": max_peers},
                        to=sid,
                    )
            except Exception:
                pass
            # Tell everyone in the chat room that this user left voice.
            try:
                socketio.emit("voice_room_user_left", {"room": room, "username": u}, room=room)
            except Exception:
                pass

        # Broadcast updated rosters for rooms we modified.
        for room, roster in rosters_after.items():
            try:
                socketio.emit("voice_room_roster", {"room": room, "users": roster, "limit": max_peers}, room=room)
            except Exception:
                pass

        result["kicked"] = len(kicked)
        return result



    # ── Echo media request payload helper ─────────────────────────────
    def _request_payload() -> dict:
        data = {}
        try:
            if request.is_json:
                data.update(request.get_json(silent=True) or {})
        except Exception:
            pass
        try:
            data.update(request.form.to_dict(flat=True) or {})
        except Exception:
            pass
        return data

    # ── Echo media controls are handled through /admin/settings/media.

    @app.route("/admin/settings/voice", methods=["GET"])
    @require_permission("admin:settings")
    def admin_get_voice_settings():
        """Return current voice settings for the injected admin panel."""
        cfg = echo_voice_client_config(settings)
        return _admin_json_response(
            {
                "ok": True,
                "voice_enabled": echo_voice_bool(settings, "voice_enabled", True),
                "voice_max_room_peers": echo_voice_room_limit(settings),
                "voice_audio_quality": echo_voice_audio_quality(settings),
                "voice_auto_quality": echo_voice_bool(settings, "voice_auto_quality", True),
                "voice_noise_cancellation": echo_voice_bool(settings, "voice_noise_cancellation", True),
                "voice_echo_cancellation": echo_voice_bool(settings, "voice_echo_cancellation", True),
                "voice_auto_gain_control": echo_voice_bool(settings, "voice_auto_gain_control", True),
                "voice_default_push_to_talk": echo_voice_bool(settings, "voice_default_push_to_talk", True),
                "client_config": cfg,
            }
        )

    @app.route("/admin/settings/voice", methods=["POST"])
    @require_permission("admin:settings")
    @require_recent_admin_auth
    def admin_set_voice_settings():
        """Update room voice settings, quality, noise processing, and talk mode defaults."""

        actor = _actor()
        json_payload = None
        try:
            json_payload = request.get_json(silent=True) or {}
        except Exception:
            json_payload = {}

        def _incoming(name, default=None):
            try:
                if name in request.form:
                    return request.form.get(name)
            except Exception:
                pass
            return json_payload.get(name, default)

        def _parse_bool(name, default):
            raw = _incoming(name, default)
            if isinstance(raw, bool):
                return raw
            if raw is None:
                return bool(default)
            text = str(raw).strip().lower()
            if text in {"1", "true", "yes", "on", "enabled"}:
                return True
            if text in {"0", "false", "no", "off", "disabled"}:
                return False
            return bool(default)

        raw_limit = _incoming("voice_max_room_peers")
        try:
            if raw_limit is None or str(raw_limit).strip() == "":
                new_limit = echo_voice_room_limit(settings)
            else:
                new_limit = int(str(raw_limit).strip())
        except Exception:
            return _admin_json_response({"ok": False, "error": "voice_max_room_peers must be an integer"}, 400)

        # Clamp: blank defaults to 100; explicit 0 => unlimited; positive values cap the room.
        if new_limit < 0:
            new_limit = 0
        if new_limit > 500:
            return _admin_json_response({"ok": False, "error": "voice_max_room_peers too large (max 500 or 0 for unlimited)"}, 400)

        quality = str(_incoming("voice_audio_quality", echo_voice_audio_quality(settings)) or "balanced").strip().lower()
        if quality not in {"low", "balanced", "high"}:
            return _admin_json_response({"ok": False, "error": "voice_audio_quality must be low, balanced, or high"}, 400)

        patch = {
            "voice_enabled": _parse_bool("voice_enabled", echo_voice_bool(settings, "voice_enabled", True)),
            "voice_max_room_peers": new_limit,
            "voice_audio_quality": quality,
            "voice_auto_quality": _parse_bool("voice_auto_quality", echo_voice_bool(settings, "voice_auto_quality", True)),
            "voice_noise_cancellation": _parse_bool("voice_noise_cancellation", echo_voice_bool(settings, "voice_noise_cancellation", True)),
            "voice_echo_cancellation": _parse_bool("voice_echo_cancellation", echo_voice_bool(settings, "voice_echo_cancellation", True)),
            "voice_auto_gain_control": _parse_bool("voice_auto_gain_control", echo_voice_bool(settings, "voice_auto_gain_control", True)),
            "voice_default_push_to_talk": _parse_bool("voice_default_push_to_talk", echo_voice_bool(settings, "voice_default_push_to_talk", True)),
        }

        settings.update(patch)
        persisted = _persist_settings_patch(patch)

        # Enforce room-cap changes immediately for active voice rooms.
        enforcement = _enforce_voice_room_limit(new_limit)

        try:
            log_audit_event(
                actor,
                "set_voice_settings",
                "*",
                f"voice_max_room_peers={new_limit} quality={quality} auto_quality={patch['voice_auto_quality']} noise={patch['voice_noise_cancellation']} ptt_default={patch['voice_default_push_to_talk']} persisted={persisted} kicked={enforcement.get('kicked', 0)}",
            )
        except Exception:
            pass

        return _admin_json_response(
            {
                "ok": True,
                "voice_enabled": patch["voice_enabled"],
                "voice_max_room_peers": new_limit,
                "voice_audio_quality": quality,
                "voice_auto_quality": patch["voice_auto_quality"],
                "voice_noise_cancellation": patch["voice_noise_cancellation"],
                "voice_echo_cancellation": patch["voice_echo_cancellation"],
                "voice_auto_gain_control": patch["voice_auto_gain_control"],
                "voice_default_push_to_talk": patch["voice_default_push_to_talk"],
                "client_config": echo_voice_client_config(settings),
                "persisted": bool(persisted),
                "persistence": _last_settings_persistence_meta(),
                "kicked": int(enforcement.get("kicked", 0) or 0),
                "kicked_by_room": enforcement.get("kicked_by_room", {}),
            }
        )


    # ── Settings: WebRTC ICE / STUN / TURN (persisted + runtime) ────────
    @app.route("/admin/settings/ice", methods=["GET"])
    @require_permission("admin:settings")
    def admin_get_ice_settings():
        """Return redacted STUN/TURN settings and connectivity summary."""
        p2p_servers = p2p_ice_servers(settings)
        voice_servers = voice_ice_servers(settings)
        return _admin_json_response(
            {
                "ok": True,
                "p2p_ice_servers": redact_ice_servers(p2p_servers),
                "voice_ice_servers": redact_ice_servers(voice_servers),
                "summary": ice_server_summary(settings),
                "help": "STUN works for many LAN tests; TURN is recommended for real internet, cellular, or strict firewall webcam/P2P tests.",
            }
        )

    @app.route("/admin/settings/ice", methods=["POST"])
    @require_permission("admin:settings")
    @require_recent_admin_auth
    def admin_set_ice_settings():
        """Update WebRTC ICE server lists for P2P files, voice, and webcam."""
        actor = _actor()
        data = _request_payload()
        p2p_raw = data.get("p2p_ice_servers") or data.get("p2p_ice_urls") or data.get("ice_servers")
        voice_raw = data.get("voice_ice_servers") or data.get("voice_ice_urls")
        turn_username = str(data.get("turn_username") or "").strip()
        turn_credential = str(data.get("turn_credential") or data.get("turn_password") or "").strip()

        patch: dict[str, object] = {}
        if p2p_raw is not None and str(p2p_raw).strip() != "":
            parsed = parse_ice_servers_text(p2p_raw)
            if not parsed:
                return _admin_json_response({"ok": False, "error": "p2p_ice_servers must contain at least one stun:, stuns:, turn:, or turns: URL"}, 400)
            patch["p2p_ice_servers"] = apply_turn_credentials(parsed, turn_username, turn_credential, keep_existing=True)

        if voice_raw is not None:
            if str(voice_raw).strip() == "":
                patch["voice_ice_servers"] = []
            else:
                parsed = parse_ice_servers_text(voice_raw)
                if not parsed:
                    return _admin_json_response({"ok": False, "error": "voice_ice_servers must be blank or contain valid STUN/TURN URLs"}, 400)
                patch["voice_ice_servers"] = apply_turn_credentials(parsed, turn_username, turn_credential, keep_existing=True)

        if not patch:
            return _admin_json_response({"ok": False, "error": "provide p2p_ice_servers and optionally voice_ice_servers"}, 400)

        validation_settings = dict(settings)
        validation_settings.update(patch)
        ice_errors = turn_credential_errors(validation_settings)
        if ice_errors:
            return _admin_json_response({"ok": False, "error": ice_errors[0], "errors": ice_errors}, 400)

        # Do not write legacy ICE alias keys. Runtime still reads them for old configs,
        # but the admin panel now persists only the current canonical keys.
        settings.update(patch)
        persisted = _persist_settings_patch(patch)
        try:
            summary = ice_server_summary(settings)
            log_audit_event(
                actor,
                "set_webrtc_ice_settings",
                "*",
                f"p2p_count={summary.get('p2p_count')} voice_count={summary.get('voice_count')} turn={summary.get('turn_configured')} persisted={persisted}",
            )
        except Exception:
            pass

        p2p_servers = p2p_ice_servers(settings)
        voice_servers = voice_ice_servers(settings)
        return _admin_json_response(
            {
                "ok": True,
                "p2p_ice_servers": redact_ice_servers(p2p_servers),
                "voice_ice_servers": redact_ice_servers(voice_servers),
                "summary": ice_server_summary(settings),
                "persisted": bool(persisted),
                "persistence": _last_settings_persistence_meta(),
            }
        )


    # ── Settings: Echo Media / webcam (persisted + runtime) ─────────────
    @app.route("/admin/settings/media", methods=["GET"])
    @require_permission("admin:settings")
    def admin_get_media_settings():
        """Return non-secret Echo built-in WebRTC webcam/media settings."""
        decision = resolve_av_mode(settings)
        policy = webcam_policy(settings)
        return _admin_json_response(
            {
                "ok": True,
                "av_mode": str(settings.get("av_mode") or decision.get("requested_mode") or "echo"),
                "active_mode": str(decision.get("mode") or "echo"),
                "voice_enabled": echo_voice_bool(settings, "voice_enabled", True),
                "webcam_enabled": echo_voice_bool(settings, "webcam_enabled", True),
                "echo_webcam_enabled": echo_voice_bool(settings, "echo_webcam_enabled", echo_voice_bool(settings, "webcam_enabled", True)),
                "webcam_quality": echo_webcam_quality(settings),
                "webcam_quality_profiles": ECHO_WEBCAM_QUALITY_PROFILES,
                "webcam_codec_strategy": str(settings.get("webcam_codec_strategy") or "prefer-compatible"),
                "webcam_transport": "echo-webrtc-mesh",
                "webcam_approval_mode": policy.get("webcam_approval_mode"),
                "webcam_max_viewers": policy.get("webcam_max_viewers"),
                "default_media_policy": policy.get("default_media_policy"),
                "server_enforced_webcam_permissions": bool(policy.get("server_enforced_webcam_permissions")),
                "client_config": {**echo_voice_client_config(settings), **client_av_config(settings)},
                "webrtc_ice_summary": ice_server_summary(settings),
                "decision": decision,
            }
        )

    @app.route("/admin/settings/media", methods=["POST"])
    @require_permission("admin:settings")
    @require_recent_admin_auth
    def admin_set_media_settings():
        """Update Echo built-in WebRTC webcam defaults and media policy."""
        actor = _actor()
        data = _request_payload()

        mode = str(data.get("av_mode") or data.get("mode") or settings.get("av_mode") or "echo").strip().lower().replace("-", "_")
        mode_aliases = {
            "webrtc": "echo",
            "built_in": "echo",
            "built-in": "echo",
            "builtin": "echo",
            "echo": "echo",
            "standard": "standard",
            "voice_only": "standard",
        }
        mode = mode_aliases.get(mode)
        if mode not in {"echo", "standard"}:
            return _admin_json_response({"ok": False, "error": "mode must be echo or standard"}, 400)

        quality = str(data.get("webcam_quality") or data.get("echo_webcam_quality") or echo_webcam_quality(settings)).strip().lower()
        if quality not in ECHO_WEBCAM_QUALITY_PROFILES:
            return _admin_json_response({"ok": False, "error": "webcam_quality must be low, balanced, or high"}, 400)

        codec = str(data.get("webcam_codec_strategy") or settings.get("webcam_codec_strategy") or "prefer-compatible").strip().lower().replace("_", "-")
        codec_aliases = {
            "auto": "prefer-compatible",
            "efficient": "prefer-efficient",
            "prefer-efficient": "prefer-efficient",
            "compat": "prefer-compatible",
            "compatible": "prefer-compatible",
            "prefer-compatible": "prefer-compatible",
            "quality": "prefer-quality",
            "prefer-quality": "prefer-quality",
        }
        codec = codec_aliases.get(codec, "prefer-compatible")

        raw_policy = str(data.get("webcam_approval_mode") or "owner_approval").strip().lower().replace("-", "_")
        policy_aliases = {"ask": "owner_approval", "approval": "owner_approval", "owner": "owner_approval", "owner_approval": "owner_approval", "open": "open", "public": "open", "disabled": "disabled", "blocked": "disabled", "off": "disabled"}
        webcam_approval_mode = policy_aliases.get(raw_policy, "owner_approval")

        try:
            max_viewers = int(str(data.get("webcam_max_viewers", settings.get("webcam_max_viewers", 0)) or "0").strip() or "0")
        except Exception:
            return _admin_json_response({"ok": False, "error": "webcam_max_viewers must be an integer"}, 400)
        if max_viewers < 0:
            max_viewers = 0
        if max_viewers > 500:
            return _admin_json_response({"ok": False, "error": "webcam_max_viewers max is 500 or 0 for unlimited"}, 400)

        raw_default = str(data.get("default_media_policy") or "user_choice").strip().lower().replace("-", "_")
        default_aliases = {"manual": "user_choice", "user": "user_choice", "user_choice": "user_choice", "voice": "voice_first", "voice_only": "voice_first", "voice_first": "voice_first", "webcam": "webcam_first", "camera": "webcam_first", "webcam_first": "webcam_first", "camera_first": "webcam_first", "both": "both_first", "both_first": "both_first"}
        default_media_policy = default_aliases.get(raw_default, "user_choice")

        if "webcam_enabled" in data:
            webcam_enabled = echo_voice_bool({"webcam_enabled": data.get("webcam_enabled")}, "webcam_enabled", True)
        else:
            webcam_enabled = echo_voice_bool(settings, "webcam_enabled", mode == "echo")

        # Webcam is now an Echo built-in WebRTC feature.  If the admin checks
        # "allow room webcams" while the old voice-only/standard mode is
        # still selected, honor the webcam intent by switching to Echo mode
        # instead of silently saving a disabled camera configuration.
        if webcam_enabled and mode == "standard":
            mode = "echo"

        patch = {
            "av_mode": mode,
            "webcam_enabled": bool(webcam_enabled),
            "echo_webcam_enabled": bool(webcam_enabled),
            "webcam_quality": quality,
            "echo_webcam_quality": quality,
            "webcam_codec_strategy": codec,
            "webcam_approval_mode": webcam_approval_mode,
            "webcam_max_viewers": max_viewers,
            "default_media_policy": default_media_policy,
        }

        settings.update(patch)
        persisted = _persist_settings_patch(patch)
        decision = resolve_av_mode(settings)
        try:
            log_audit_event(actor, "set_echo_media_settings", "*", f"mode={mode} webcam={patch['webcam_enabled']} quality={quality} codec={codec} policy={webcam_approval_mode} max_viewers={max_viewers} persisted={persisted}")
        except Exception:
            pass
        return jsonify(
            {
                "ok": True,
                "persisted": bool(persisted),
                "persistence": _last_settings_persistence_meta(),
                "av_mode": mode,
                "active_mode": decision.get("mode"),
                "webcam_enabled": patch["webcam_enabled"],
                "webcam_quality": quality,
                "webcam_codec_strategy": codec,
                "webcam_approval_mode": webcam_approval_mode,
                "webcam_max_viewers": max_viewers,
                "default_media_policy": default_media_policy,
                "client_config": {**echo_voice_client_config(settings), **client_av_config(settings)},
                "webrtc_ice_summary": ice_server_summary(settings),
                "decision": decision,
            }
        )


    # ── Settings: GIFs (GIPHY) (persisted + runtime) ───────────────────
    def _has_giphy_key() -> bool:
        try:
            v = (os.getenv("ECHOCHAT_GIPHY_API_KEY") or os.getenv("GIPHY_API_KEY") or str(settings.get("giphy_api_key") or "")).strip()
            if v:
                return True
            base_dir = Path(__file__).resolve().parent
            candidates = [
                Path.cwd() / ".giphy_api_key",
                Path.cwd() / "giphy_api_key.txt",
                base_dir / ".giphy_api_key",
                base_dir / "giphy_api_key.txt",
            ]
            for p in candidates:
                try:
                    if p.exists() and p.read_text(encoding="utf-8").strip():
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    @app.route("/admin/settings/gifs", methods=["GET", "POST"])
    @require_permission("admin:settings")
    def admin_settings_gifs():
        """Read/patch GIF settings.

        Security note:
          - GET does NOT return the API key, only whether it is set.
          - POST can set/replace the key (persists into settings JSON).
        """
        if request.method != "GET":
            status = _admin_reauth_status(_actor())
            if not status.get("ok"):
                return _admin_reauth_required_response(status)
        if request.method == "GET":
            return _admin_json_response(
                {
                    "ok": True,
                    "giphy_enabled": bool(settings.get("giphy_enabled", True)),
                    "giphy_rating": str(settings.get("giphy_rating", "pg-13") or "pg-13"),
                    "giphy_lang": str(settings.get("giphy_lang", "en") or "en"),
                    "giphy_default_limit": int(settings.get("giphy_default_limit", 24) or 24),
                    "has_key": bool(_has_giphy_key()),
                }
            )

        actor = _actor()
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return _admin_json_response({"ok": False, "error": "Invalid JSON"}, 400)

        patch = {}

        if "giphy_enabled" in payload:
            v = payload.get("giphy_enabled")
            patch["giphy_enabled"] = bool(v) if isinstance(v, bool) else str(v).strip().lower() in {"1", "true", "yes", "on"}

        if "giphy_rating" in payload:
            rating = str(payload.get("giphy_rating") or "pg-13").strip().lower()[:20] or "pg-13"
            if rating not in {"y", "g", "pg", "pg-13", "r"}:
                return _admin_json_response({"ok": False, "error": "giphy_rating must be y, g, pg, pg-13, or r"}, 400)
            patch["giphy_rating"] = rating

        if "giphy_lang" in payload:
            lang = str(payload.get("giphy_lang") or "en").strip().lower()[:10] or "en"
            if not re.match(r"^[a-z]{2}(?:-[a-z0-9]{2,8})?$", lang):
                return _admin_json_response({"ok": False, "error": "giphy_lang must be a short locale code like en or en-us"}, 400)
            patch["giphy_lang"] = lang

        if "giphy_default_limit" in payload:
            try:
                lim = int(payload.get("giphy_default_limit") or 24)
            except Exception:
                return _admin_json_response({"ok": False, "error": "giphy_default_limit must be an integer"}, 400)
            patch["giphy_default_limit"] = max(1, min(lim, 48))

        if "giphy_api_key" in payload:
            # Allow blanking the key, but reject control characters and huge values.
            giphy_key = str(payload.get("giphy_api_key") or "").strip()
            if len(giphy_key) > 256 or any(ord(ch) < 32 for ch in giphy_key):
                return _admin_json_response({"ok": False, "error": "giphy_api_key is too long or contains invalid characters"}, 400)
            patch["giphy_api_key"] = giphy_key

        # Apply runtime
        for k, v in patch.items():
            settings[k] = v

        persisted = _persist_settings_patch(patch)

        try:
            # Don't log the raw key.
            safe_meta = ",".join([k for k in patch.keys()])
            log_audit_event(actor, "set_gif_settings", "*", f"keys={safe_meta} persisted={persisted}")
        except Exception:
            pass

        return _admin_json_response(
            {
                "ok": True,
                "persisted": bool(persisted),
                "persistence": _last_settings_persistence_meta(),
                "giphy_enabled": bool(settings.get("giphy_enabled", True)),
                "giphy_rating": str(settings.get("giphy_rating", "pg-13") or "pg-13"),
                "giphy_lang": str(settings.get("giphy_lang", "en") or "en"),
                "giphy_default_limit": int(settings.get("giphy_default_limit", 24) or 24),
                "has_key": bool(_has_giphy_key()),
            }
        )


    @app.route("/admin/users")
    @require_permission("admin:basic")
    def admin_list_users():
        """Legacy lightweight user lookup.

        This endpoint intentionally refuses unfiltered browsing unless the caller
        opts in with browse=1. Large installs can have 100k+ accounts, so admin UI
        code should use /admin/user_search with pagination instead of asking for a
        server-wide user dump.
        """
        prefix = (request.args.get("prefix") or "").strip()
        browse = (request.args.get("browse") or "0").strip().lower() in {"1", "true", "yes", "on"}
        try:
            limit = int(request.args.get("limit") or 50)
        except Exception:
            limit = 50
        limit = max(1, min(limit, 100))

        if not prefix and not browse:
            return jsonify(
                {
                    "users": [],
                    "limit": limit,
                    "has_more": False,
                    "requires_query": True,
                    "message": "Enter a username prefix or pass browse=1 for a paged sample.",
                }
            )

        conn = get_db()
        with conn.cursor() as cur:
            effective_admin_sql = _effective_admin_exists_sql("u")
            effective_admin_expr = effective_admin_sql
            effective_status_expr = effective_account_status_sql("u")
            if prefix:
                cur.execute(
                    f"""
                    SELECT u.username, u.is_admin, {effective_admin_expr} AS effective_is_admin,
                           {effective_status_expr} AS effective_status, u.status AS raw_status,
                           u.online, u.last_seen, u.presence_status, u.custom_status
                      FROM users u
                     WHERE u.username ILIKE %s
                     ORDER BY LOWER(u.username) ASC, u.id ASC
                     LIMIT %s;
                    """,
                    (prefix + "%", limit + 1),
                )
            else:
                cur.execute(
                    f"""
                    SELECT u.username, u.is_admin, {effective_admin_expr} AS effective_is_admin,
                           {effective_status_expr} AS effective_status, u.status AS raw_status,
                           u.online, u.last_seen, u.presence_status, u.custom_status
                      FROM users u
                     ORDER BY LOWER(u.username) ASC, u.id ASC
                     LIMIT %s;
                    """,
                    (limit + 1,),
                )
            rows = cur.fetchall() or []

        has_more = len(rows) > limit
        rows = rows[:limit]
        users = []
        for r in rows:
            users.append(
                {
                    "username": r[0],
                    "is_admin": bool(r[1]),
                    "effective_is_admin": bool(r[2]),
                    "status": r[3],
                    "effective_status": r[3],
                    "raw_status": r[4],
                    "online": bool(r[5]),
                    "last_seen": r[6].isoformat() if r[6] else None,
                    "presence_status": r[7],
                    "custom_status": r[8],
                }
            )

        return jsonify({"users": users, "limit": limit, "has_more": has_more, "requires_query": False})

    # ── Enhanced user search + detail (admin GUI) ─────────────────
    @app.route("/admin/user_search")
    @require_permission("admin:basic")
    def admin_user_search():
        """Search users by username/email/id with server-side pagination.

        This route is intentionally schema-tolerant. Some upgraded installs may
        not have every newer optional users column yet, but admin username search
        must still work.
        """

        q = (request.args.get("q") or "").strip()
        if len(q) > 96:
            return _admin_json_response({"ok": False, "error": "Search is too long", "max_length": 96}, 400)
        mode = (request.args.get("mode") or "contains").strip().lower()
        if mode not in {"contains", "prefix", "exact", "email", "id"}:
            mode = "contains"
        online_only = (request.args.get("online") or "0").strip().lower() in {"1", "true", "yes", "on"}
        admins_only = (request.args.get("admins") or "0").strip().lower() in {"1", "true", "yes", "on"}
        status = (request.args.get("status") or "any").strip().lower()
        if status not in {"any", "active", "suspended", "deactivated", "shadowbanned"}:
            status = "any"

        try:
            limit = int(request.args.get("limit") or 50)
        except Exception:
            limit = 50
        limit = max(1, min(limit, 100))

        try:
            page = int(request.args.get("page") or 1)
        except Exception:
            page = 1
        page = max(1, min(page, 10000))
        offset = (page - 1) * limit

        has_filter = bool(online_only or admins_only or status != "any")
        if not q and not has_filter:
            return _admin_json_response(
                {
                    "ok": True,
                    "users": [],
                    "q": q,
                    "mode": mode,
                    "limit": limit,
                    "page": page,
                    "returned": 0,
                    "has_more": False,
                    "next_page": None,
                    "requires_query": True,
                    "message": "Search by username/email/id or enable a filter before loading users.",
                }
            )

        conn = get_db()

        try:
            with conn.cursor() as cur:
                user_cols = _admin_table_columns(cur, "users")
                has_user_roles = _admin_table_exists(cur, "user_roles")
                has_role_permissions = _admin_table_exists(cur, "role_permissions")
                has_permissions = _admin_table_exists(cur, "permissions")
                has_sanctions = _admin_table_exists(cur, "user_sanctions")
        except Exception:
            user_cols = set()
            has_user_roles = has_role_permissions = has_permissions = has_sanctions = False

        def has_col(name: str) -> bool:
            return (not user_cols) or name in user_cols

        email_col = has_col("email")
        email_hash_col = has_col("email_hash")
        email_encrypted_col = has_col("email_encrypted")
        status_col = has_col("status")
        is_admin_col = has_col("is_admin")
        online_col = has_col("online")
        last_seen_col = has_col("last_seen")
        created_at_col = has_col("created_at")
        presence_status_col = has_col("presence_status")
        custom_status_col = has_col("custom_status")
        two_factor_col = has_col("two_factor_enabled")

        where = []
        params = []

        if has_user_roles and has_role_permissions and has_permissions and is_admin_col:
            effective_admin_sql = _effective_admin_exists_sql("u")
        elif is_admin_col:
            effective_admin_sql = "COALESCE(u.is_admin, FALSE)"
        else:
            effective_admin_sql = "FALSE"

        if status_col and has_sanctions:
            effective_status_sql = effective_account_status_sql("u")
        elif status_col:
            effective_status_sql = "LOWER(COALESCE(u.status, 'active'))"
        else:
            effective_status_sql = "'active'"

        if online_only and online_col:
            where.append("u.online = TRUE")
        if admins_only:
            where.append(f"{effective_admin_sql} = TRUE")
        if status in {"active", "suspended", "deactivated", "shadowbanned"}:
            where.append(f"{effective_status_sql} = %s")
            params.append(status)

        if q:
            q_hash = hash_email(q, settings) if email_hash_col else ""
            if mode == "id" and q.isdigit() and has_col("id"):
                where.append("u.id = %s")
                params.append(int(q))
            else:
                search_clauses = []
                search_params = []
                if mode == "exact":
                    search_clauses.append("u.username = %s")
                    search_params.append(q)
                    if email_col:
                        search_clauses.append("LOWER(u.email) = LOWER(%s)")
                        search_params.append(q)
                    if email_hash_col and q_hash:
                        search_clauses.append("u.email_hash = %s")
                        search_params.append(q_hash)
                elif mode == "prefix":
                    pattern = _admin_like_pattern(q, prefix=True)
                    search_clauses.append("u.username ILIKE %s ESCAPE '\\\\'")
                    search_params.append(pattern)
                    if email_col:
                        search_clauses.append("u.email ILIKE %s ESCAPE '\\\\'")
                        search_params.append(pattern)
                    if email_hash_col and q_hash:
                        search_clauses.append("u.email_hash = %s")
                        search_params.append(q_hash)
                elif mode == "email":
                    if email_col:
                        search_clauses.append("u.email ILIKE %s ESCAPE '\\\\'")
                        search_params.append(_admin_like_pattern(q))
                    if email_hash_col and q_hash:
                        search_clauses.append("u.email_hash = %s")
                        search_params.append(q_hash)
                    # If an upgraded DB does not have email lookup columns yet,
                    # fall back to username search instead of failing the panel.
                    if not search_clauses:
                        search_clauses.append("u.username ILIKE %s ESCAPE '\\\\'")
                        search_params.append(_admin_like_pattern(q))
                else:  # contains
                    pattern = _admin_like_pattern(q)
                    search_clauses.append("u.username ILIKE %s ESCAPE '\\\\'")
                    search_params.append(pattern)
                    if email_col:
                        search_clauses.append("u.email ILIKE %s ESCAPE '\\\\'")
                        search_params.append(pattern)
                    if email_hash_col and q_hash:
                        search_clauses.append("u.email_hash = %s")
                        search_params.append(q_hash)

                if search_clauses:
                    where.append("(" + " OR ".join(search_clauses) + ")")
                    params.extend(search_params)

        email_select = "u.email" if email_col else "NULL::text"
        email_encrypted_select = "u.email_encrypted" if email_encrypted_col else "NULL::text"
        is_admin_select = "COALESCE(u.is_admin, FALSE)" if is_admin_col else "FALSE"
        raw_status_select = "u.status" if status_col else "'active'"
        online_select = "COALESCE(u.online, FALSE)" if online_col else "FALSE"
        last_seen_select = "u.last_seen" if last_seen_col else "NULL::timestamptz"
        created_at_select = "u.created_at" if created_at_col else "NULL::timestamptz"
        presence_status_select = "u.presence_status" if presence_status_col else "NULL::text"
        custom_status_select = "u.custom_status" if custom_status_col else "NULL::text"
        two_factor_select = "COALESCE(u.two_factor_enabled, FALSE)" if two_factor_col else "FALSE"

        sql = (
            f"SELECT u.id, u.username, {email_select} AS email, {email_encrypted_select} AS email_encrypted, "
            f"{is_admin_select} AS is_admin, {effective_admin_sql} AS effective_is_admin, "
            f"{effective_status_sql} AS effective_status, {raw_status_select} AS raw_status, "
            f"{online_select} AS online, {last_seen_select} AS last_seen, {created_at_select} AS created_at, "
            f"{presence_status_select} AS presence_status, {custom_status_select} AS custom_status, "
            f"{two_factor_select} AS two_factor_enabled "
            "FROM users u "
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY online DESC, LOWER(u.username) ASC, u.id ASC LIMIT %s OFFSET %s;"
        params.extend([limit + 1, offset])

        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall() or []
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logging.exception("[ADMIN] enhanced user search failed; falling back to username-only search")
            # Last-resort fallback: do not leave the admin GUI unusable if a
            # migration/schema drift breaks an optional field expression.
            fallback_where = []
            fallback_params = []
            if q:
                if mode == "exact":
                    fallback_where.append("u.username = %s")
                    fallback_params.append(q)
                elif mode == "prefix":
                    fallback_where.append("u.username ILIKE %s ESCAPE '\\\\'")
                    fallback_params.append(_admin_like_pattern(q, prefix=True))
                elif mode == "id" and q.isdigit():
                    fallback_where.append("u.id = %s")
                    fallback_params.append(int(q))
                else:
                    fallback_where.append("u.username ILIKE %s ESCAPE '\\\\'")
                    fallback_params.append(_admin_like_pattern(q))
            if online_only and online_col:
                fallback_where.append("COALESCE(u.online, FALSE) = TRUE")
            if admins_only and is_admin_col:
                fallback_where.append("COALESCE(u.is_admin, FALSE) = TRUE")
            fallback_email_select = email_select
            fallback_email_encrypted_select = email_encrypted_select
            fallback_is_admin_select = is_admin_select
            fallback_status_select = raw_status_select
            fallback_online_select = online_select
            fallback_last_seen_select = last_seen_select
            fallback_created_at_select = created_at_select
            fallback_sql = (
                f"SELECT u.id, u.username, {fallback_email_select} AS email, {fallback_email_encrypted_select} AS email_encrypted, "
                f"{fallback_is_admin_select} AS is_admin, {fallback_is_admin_select} AS effective_is_admin, "
                f"{fallback_status_select} AS effective_status, {fallback_status_select} AS raw_status, "
                f"{fallback_online_select} AS online, {fallback_last_seen_select} AS last_seen, {fallback_created_at_select} AS created_at, "
                "NULL::text AS presence_status, NULL::text AS custom_status, FALSE AS two_factor_enabled "
                "FROM users u "
            )
            if fallback_where:
                fallback_sql += " WHERE " + " AND ".join(fallback_where)
            fallback_sql += " ORDER BY online DESC, LOWER(u.username) ASC, u.id ASC LIMIT %s OFFSET %s;"
            fallback_params.extend([limit + 1, offset])
            with conn.cursor() as cur:
                cur.execute(fallback_sql, tuple(fallback_params))
                rows = cur.fetchall() or []

        has_more = len(rows) > limit
        rows = rows[:limit]
        out = []
        for r in rows:
            out.append(
                {
                    "id": int(r[0]),
                    "username": r[1],
                    "email": display_email(r[2], r[3], settings),
                    "is_admin": bool(r[4]),
                    "effective_is_admin": bool(r[5]),
                    "status": r[6],
                    "effective_status": r[6],
                    "raw_status": r[7],
                    "online": bool(r[8]),
                    "last_seen": r[9].isoformat() if r[9] else None,
                    "created_at": r[10].isoformat() if r[10] else None,
                    "presence_status": r[11],
                    "custom_status": r[12],
                    "two_factor_enabled": bool(r[13]),
                }
            )

        return _admin_json_response(
            {
                "ok": True,
                "users": out,
                "q": q,
                "mode": mode,
                "limit": limit,
                "page": page,
                "returned": len(out),
                "has_more": has_more,
                "next_page": page + 1 if has_more else None,
                "requires_query": False,
                "schema_tolerant": True,
            }
        )

    @app.route("/admin/user_detail/<username>")
    @require_permission("admin:basic")
    def admin_user_detail(username: str):
        """Return an enriched user snapshot for admin UX (no secrets)."""
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err

        conn = get_db()
        with conn.cursor() as cur:
            effective_admin_expr = _effective_admin_expr("u")
            effective_status_expr = effective_account_status_sql("u")
            cur.execute(
                f"""
                SELECT u.id, u.username, u.email, u.email_encrypted, u.is_admin,
                       {effective_admin_expr} AS effective_is_admin,
                       {effective_status_expr} AS effective_status, u.status AS raw_status,
                       u.online, u.last_seen, u.created_at,
                       u.presence_status, u.custom_status, u.two_factor_enabled
                  FROM users u
                 WHERE LOWER(u.username) = LOWER(%s);
                """,
                (username,),
            )
            u = cur.fetchone()
            if not u:
                return _admin_json_response({"ok": False, "error": "not_found"}, 404)

            user_id = int(u[0])

            # Roles
            roles = []
            try:
                cur.execute(
                    """
                    SELECT r.name
                      FROM user_roles ur
                      JOIN roles r ON r.id = ur.role_id
                     WHERE ur.user_id = %s
                     ORDER BY LOWER(r.name);
                    """,
                    (user_id,),
                )
                roles = [r[0] for r in (cur.fetchall() or [])]
            except Exception:
                roles = []

            # Sanctions
            sanctions = []
            try:
                cur.execute(
                    """
                    SELECT sanction_type, reason, created_at, expires_at
                      FROM user_sanctions
                     WHERE LOWER(username) = LOWER(%s)
                     ORDER BY created_at DESC
                     LIMIT 25;
                    """,
                    (username,),
                )
                for s in (cur.fetchall() or []):
                    sanctions.append(
                        {
                            "type": s[0],
                            "reason": s[1],
                            "created_at": s[2].isoformat() if s[2] else None,
                            "expires_at": s[3].isoformat() if s[3] else None,
                        }
                    )
            except Exception:
                sanctions = []

            # Quota
            quota = None
            try:
                cur.execute(
                    "SELECT messages_per_hour, updated_at FROM user_quotas WHERE LOWER(username) = LOWER(%s);",
                    (username,),
                )
                qrow = cur.fetchone()
                if qrow:
                    quota = {
                        "messages_per_hour": int(qrow[0]),
                        "updated_at": qrow[1].isoformat() if qrow[1] else None,
                    }
            except Exception:
                quota = None

            # Lightweight relationship counts
            counts = {"friends": 0, "groups": 0}
            try:
                # Keep admin-side friend totals aligned with the live social UI.
                # Accepted rows in friend_requests are the canonical source of
                # truth; the friends table is a bidirectional helper table and
                # counting its rows can double-count relationships.
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT
                        CASE
                            WHEN from_user = %s THEN to_user
                            ELSE from_user
                        END
                    )
                      FROM friend_requests
                     WHERE (from_user = %s OR to_user = %s)
                       AND request_status = 'accepted';
                    """,
                    (username, username, username),
                )
                counts["friends"] = int((cur.fetchone() or [0])[0] or 0)
            except Exception:
                pass
            try:
                cur.execute("SELECT COUNT(*) FROM group_members WHERE user_id = %s;", (user_id,))
                counts["groups"] = int((cur.fetchone() or [0])[0] or 0)
            except Exception:
                pass

        connected_session_count = len(_user_sids(username))
        return _admin_json_response(
            {
                "ok": True,
                "user": {
                    "id": user_id,
                    "username": u[1],
                    "email": display_email(u[2], u[3], settings),
                    "is_admin": bool(u[4]),
                    "effective_is_admin": bool(u[5]),
                    "status": u[6],
                    "effective_status": u[6],
                    "raw_status": u[7],
                    "online": bool(u[8]),
                    "last_seen": u[9].isoformat() if u[9] else None,
                    "created_at": u[10].isoformat() if u[10] else None,
                    "presence_status": u[11],
                    "custom_status": u[12],
                    "two_factor_enabled": bool(u[13]),
                },
                "roles": roles,
                "sanctions": sanctions,
                "quota": quota,
                "counts": counts,
                "connected_session_count": connected_session_count,
            }
        )

    @app.route("/admin/users/<path:username>/activity_timeline")
    @require_permission("admin:audit")
    def admin_user_activity_timeline(username: str):
        """Return a merged, newest-first user activity timeline for admins.

        The endpoint intentionally uses metadata summaries instead of dumping
        complete message bodies. Admins can see behavior patterns without the
        timeline becoming a raw chat transcript.
        """
        username, user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        try:
            limit = max(1, min(200, int(request.args.get("limit") or 80)))
        except Exception:
            limit = 80
        try:
            days = max(1, min(365, int(request.args.get("days") or 30)))
        except Exception:
            days = 30

        since = _utcnow() - timedelta(days=days)
        events: list[dict] = []

        def recover_query_error():
            try:
                conn.rollback()
            except Exception:
                pass

        def iso(value):
            if hasattr(value, "isoformat"):
                return value.isoformat()
            return str(value or "")

        def add_event(ts, category: str, action: str, summary: str, details: str = "", source: str = ""):
            if not ts:
                return
            events.append({
                "timestamp": iso(ts),
                "category": str(category or "activity"),
                "action": _admin_safe_audit_text(action or "activity", max_len=120),
                "summary": _admin_safe_audit_text(summary, max_len=280),
                "details": _admin_safe_audit_text(details, max_len=500),
                "source": _admin_safe_audit_text(source, max_len=80),
            })

        # Live Socket.IO presence snapshot first, because it is not stored in DB.
        try:
            if _state_connected_sessions_snapshot is not None:
                snapshot = _state_connected_sessions_snapshot() or {}
                for sid, sess in dict(snapshot).items():
                    if not isinstance(sess, dict) or str(sess.get("username") or "") != username:
                        continue
                    room = str(sess.get("room") or "")
                    summary = f"Live session is connected{(' in ' + room) if room else ''}"
                    details = "live Socket.IO session active"
                    add_event(_utcnow(), "live", "live_session", summary, details, "socket_state")
        except Exception:
            pass

        conn = get_db()
        with conn.cursor() as cur:
            # Account sessions.
            try:
                cur.execute(
                    """
                    SELECT created_at, last_activity_at, revoked_at, revoked_reason, ip_address, user_agent
                      FROM auth_sessions
                     WHERE LOWER(username) = LOWER(%s)
                       AND COALESCE(last_activity_at, created_at) >= %s
                     ORDER BY COALESCE(last_activity_at, created_at) DESC
                     LIMIT %s;
                    """,
                    (username, since, limit),
                )
                for created_at, last_activity_at, revoked_at, revoked_reason, ip_address, user_agent in (cur.fetchall() or []):
                    if last_activity_at:
                        add_event(last_activity_at, "session", "session_activity", "Auth session activity", f"ip={ip_address or '-'} ua={(user_agent or '')[:180]}", "auth_sessions")
                    else:
                        add_event(created_at, "session", "login_session", "Auth session created", f"ip={ip_address or '-'} ua={(user_agent or '')[:180]}", "auth_sessions")
                    if revoked_at:
                        add_event(revoked_at, "session", "session_revoked", "Auth session revoked", revoked_reason or "", "auth_sessions")
            except Exception:
                recover_query_error()

            # Audit events where the user acted, was targeted, or appears in composite targets like username@room.
            try:
                like = _admin_like_pattern(username)
                cur.execute(
                    """
                    SELECT actor, action, target, timestamp, details
                      FROM audit_log
                     WHERE timestamp >= %s
                       AND (actor = %s OR target = %s OR target ILIKE %s ESCAPE '\\' OR details ILIKE %s ESCAPE '\\')
                     ORDER BY timestamp DESC
                     LIMIT %s;
                    """,
                    (since, username, username, like, like, limit),
                )
                for actor, action, target, ts, details in (cur.fetchall() or []):
                    direction = "acted" if str(actor or "") == username else "targeted"
                    summary = f"{actor or 'system'} {action or 'activity'}"
                    if target:
                        summary += f" → {target}"
                    add_event(ts, "audit", str(action or "audit"), _admin_safe_audit_text(summary, max_len=280), _admin_safe_audit_text(details or direction, max_len=500), "audit_log")
            except Exception:
                recover_query_error()

            # Message metadata only.
            try:
                cur.execute(
                    """
                    SELECT timestamp, room, receiver, is_encrypted, is_edited
                      FROM messages
                     WHERE sender = %s
                       AND timestamp >= %s
                     ORDER BY timestamp DESC
                     LIMIT %s;
                    """,
                    (username, since, min(limit, 80)),
                )
                for ts, room, receiver, is_encrypted, is_edited in (cur.fetchall() or []):
                    if receiver:
                        summary = f"Private message sent to {receiver}"
                    else:
                        summary = f"Room message sent in {room or 'unknown room'}"
                    bits = []
                    if is_encrypted:
                        bits.append("encrypted")
                    if is_edited:
                        bits.append("edited")
                    add_event(ts, "message", "message_sent", summary, ", ".join(bits), "messages")
            except Exception:
                recover_query_error()

            # Group membership.
            try:
                cur.execute(
                    """
                    SELECT gm.joined_at, g.group_name, gm.role
                      FROM group_members gm
                      JOIN groups g ON g.id = gm.group_id
                     WHERE gm.user_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                       AND gm.joined_at >= %s
                     ORDER BY gm.joined_at DESC
                     LIMIT %s;
                    """,
                    (username, since, min(limit, 60)),
                )
                for ts, group_name, role in (cur.fetchall() or []):
                    add_event(ts, "group", "group_join", f"Joined group {group_name}", f"role={role or 'member'}", "group_members")
            except Exception:
                recover_query_error()

            # Sanctions.
            try:
                cur.execute(
                    """
                    SELECT created_at, sanction_type, reason, expires_at
                      FROM user_sanctions
                     WHERE LOWER(username) = LOWER(%s)
                       AND created_at >= %s
                     ORDER BY created_at DESC
                     LIMIT %s;
                    """,
                    (username, since, min(limit, 60)),
                )
                for ts, sanction_type, reason, expires_at in (cur.fetchall() or []):
                    detail = reason or ""
                    if expires_at:
                        detail += (" • " if detail else "") + f"expires {iso(expires_at)}"
                    add_event(ts, "moderation", f"sanction_{sanction_type}", f"Sanction: {sanction_type}", detail, "user_sanctions")
            except Exception:
                recover_query_error()

            # Profile social activity.
            try:
                cur.execute(
                    """
                    SELECT created_at, id, visibility, deleted_at
                      FROM profile_posts
                     WHERE author_username = %s
                       AND created_at >= %s
                     ORDER BY created_at DESC
                     LIMIT %s;
                    """,
                    (username, since, min(limit, 80)),
                )
                for ts, post_id, visibility, deleted_at in (cur.fetchall() or []):
                    add_event(ts, "profile", "profile_post_create", f"Created profile post #{post_id}", f"visibility={visibility or 'friends'}" + (" • deleted" if deleted_at else ""), "profile_posts")
            except Exception:
                recover_query_error()
            try:
                cur.execute(
                    """
                    SELECT created_at, id, post_id, deleted_at
                      FROM profile_post_comments
                     WHERE author_username = %s
                       AND created_at >= %s
                     ORDER BY created_at DESC
                     LIMIT %s;
                    """,
                    (username, since, min(limit, 80)),
                )
                for ts, comment_id, post_id, deleted_at in (cur.fetchall() or []):
                    add_event(ts, "profile", "profile_comment_create", f"Commented on profile post #{post_id}", f"comment #{comment_id}" + (" • deleted" if deleted_at else ""), "profile_post_comments")
            except Exception:
                recover_query_error()
            try:
                cur.execute(
                    """
                    SELECT created_at, post_id, reaction
                      FROM profile_post_reactions
                     WHERE LOWER(username) = LOWER(%s)
                       AND created_at >= %s
                     ORDER BY created_at DESC
                     LIMIT %s;
                    """,
                    (username, since, min(limit, 80)),
                )
                for ts, post_id, reaction in (cur.fetchall() or []):
                    add_event(ts, "profile", "profile_reaction", f"Reacted to profile post #{post_id}", f"reaction={reaction or 'like'}", "profile_post_reactions")
            except Exception:
                recover_query_error()
            try:
                cur.execute(
                    """
                    SELECT created_at, id, post_id, comment_id, target_username, reason, status
                      FROM profile_post_reports
                     WHERE (reporter_username = %s OR target_username = %s)
                       AND created_at >= %s
                     ORDER BY created_at DESC
                     LIMIT %s;
                    """,
                    (username, username, since, min(limit, 80)),
                )
                for ts, report_id, post_id, comment_id, target, reason, status in (cur.fetchall() or []):
                    if str(target or "") == username:
                        summary = f"Profile report against user #{report_id}"
                    else:
                        summary = f"Filed profile report #{report_id}"
                    target_label = f"comment #{comment_id}" if comment_id else f"post #{post_id}"
                    add_event(ts, "report", "profile_report", summary, f"{target_label} • reason={reason or 'other'} • status={status or 'open'}", "profile_post_reports")
            except Exception:
                recover_query_error()

        events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
        trimmed = events[:limit]
        return _admin_json_response({
            "ok": True,
            "username": username,
            "days": days,
            "limit": limit,
            "events": trimmed,
            "count": len(trimmed),
            "has_more_unmerged": len(events) > len(trimmed),
            "generated_at": _utcnow().isoformat(),
        })


    # ── Rooms snapshot for admin panel ───────────────────────────
    @app.route("/admin/rooms/list")
    @require_permission("admin:basic")
    def admin_rooms_list():
        # Live online counts are derived from Socket.IO session state.
        # Important: chat_rooms.member_count can drift if users have multiple tabs,
        # stale sockets, or disconnect events don’t fire in the expected order.
        # The admin panel should therefore display *online* (deduped by username)
        # rather than the persisted counter.
        live_counts: dict[str, int] = {}
        live_counts_available = False
        if _state_live_room_counts is not None:
            try:
                live_counts = {str(room_name): int(count or 0) for room_name, count in dict(_state_live_room_counts()).items()}
                live_counts_available = True
            except Exception:
                live_counts = {}
                live_counts_available = False

        public_idle_ttl_minutes = _coerce_idle_minutes_from_settings(settings, "custom_room_idle_minutes", "custom_room_idle_hours", 3)
        private_idle_ttl_minutes = _coerce_idle_minutes_from_settings(
            settings,
            "custom_private_room_idle_minutes",
            "custom_private_room_idle_hours",
            max(1, public_idle_ttl_minutes // 60),
        )
        try:
            janitor_interval_seconds = max(10, min(int(settings.get("janitor_interval_seconds", 60)), 3600))
        except Exception:
            janitor_interval_seconds = 60

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.name, r.member_count,
                       COALESCE(l.locked, FALSE) AS locked,
                       COALESCE(ro.readonly, FALSE) AS readonly,
                       COALESCE(sm.seconds, 0) AS slowmode_sec,
                       (cr.name IS NOT NULL) AS is_custom,
                       COALESCE(cr.is_private, FALSE) AS is_private,
                       cr.category, cr.subcategory,
                       cr.created_by,
                       cr.created_at,
                       cr.last_active_at,
                       COALESCE(r.room_kind, 'manual') AS room_kind
                  FROM chat_rooms r
             LEFT JOIN room_locks l ON l.room = r.name
             LEFT JOIN room_readonly ro ON ro.room = r.name
             LEFT JOIN room_slowmode sm ON sm.room = r.name
             LEFT JOIN custom_rooms cr ON cr.name = r.name
                 ORDER BY LOWER(r.name) ASC;
                """
            )
            rows = cur.fetchall() or []

        rooms = []
        now_utc = _utcnow()
        for rr in rows:
            room_name = rr[0]
            live = int(live_counts.get(str(room_name), 0) or 0) if live_counts_available else 0
            is_custom = bool(rr[5])
            is_private = bool(rr[6]) if is_custom else False
            persisted_member_count = int(rr[1] or 0)
            occupancy_count = int(live if live_counts_available else persisted_member_count)
            room_kind = str(rr[12] or "manual").strip().lower()
            cleanup_managed = bool(is_custom and room_kind == "custom")
            activity_at = rr[11] or rr[10]
            activity_age_seconds = None
            idle_ttl_minutes = None
            expires_in_seconds = None
            deletion_state = None
            if cleanup_managed:
                idle_ttl_minutes = int(private_idle_ttl_minutes if is_private else public_idle_ttl_minutes)
                deletion_state = "occupied" if occupancy_count > 0 else "counting_down"
                if activity_at is not None:
                    try:
                        if getattr(activity_at, "tzinfo", None) is None:
                            activity_at = activity_at.replace(tzinfo=timezone.utc)
                        activity_age_seconds = max(0, int((now_utc - activity_at).total_seconds()))
                        expires_in_seconds = max(0, int(idle_ttl_minutes * 60) - activity_age_seconds)
                        if occupancy_count <= 0 and expires_in_seconds <= 0:
                            deletion_state = "eligible_now"
                    except Exception:
                        activity_age_seconds = None
                        expires_in_seconds = None
            rooms.append(
                {
                    "name": room_name,
                    # Persisted counter (kept for diagnostics/back-compat)
                    "member_count": persisted_member_count,
                    "persisted_member_count": persisted_member_count,
                    # Cleanup/display occupancy source.  When live counts are available,
                    # this ignores stale DB counters so an empty custom room keeps counting down.
                    "cleanup_occupancy_count": occupancy_count,
                    # Live online (deduped by username)
                    "online_count": int(live or 0),
                    "live_counts_available": bool(live_counts_available),
                    "locked": bool(rr[2]),
                    "readonly": bool(rr[3]),
                    "slowmode_sec": int(rr[4] or 0),
                    "is_custom": is_custom,
                    "is_private": is_private,
                    "category": rr[7],
                    "subcategory": rr[8],
                    "created_by": rr[9],
                    "created_at": rr[10].isoformat() if rr[10] else None,
                    "last_active_at": rr[11].isoformat() if rr[11] else None,
                    "room_kind": room_kind,
                    "cleanup_managed": cleanup_managed,
                    "activity_at": activity_at.isoformat() if activity_at else None,
                    "idle_ttl_minutes": idle_ttl_minutes,
                    "activity_age_seconds": activity_age_seconds,
                    "expires_in_seconds": expires_in_seconds,
                    "deletion_state": deletion_state,
                }
            )

        # Prefer sorting by live online count (more meaningful in practice).
        try:
            rooms.sort(key=lambda r: (int(r.get("online_count") or 0), str(r.get("name") or "").lower()), reverse=True)
        except Exception:
            pass

        janitor_status = {}
        if janitor_status_snapshot is not None:
            try:
                janitor_status = janitor_status_snapshot()
            except Exception:
                janitor_status = {}

        return jsonify({
            "rooms": rooms,
            "ts": now_utc.isoformat(),
            "custom_room_idle_minutes": public_idle_ttl_minutes,
            "custom_private_room_idle_minutes": private_idle_ttl_minutes,
            "janitor_interval_seconds": janitor_interval_seconds,
            "janitor_status": janitor_status,
        })

    # ── Room radio station editor (admin) ─────────────────────────
    @app.route("/admin/room_radio/catalog", methods=["GET"])
    @require_permission("admin:settings")
    def admin_room_radio_catalog():
        """Return editable official room radio station presets."""
        catalog = _read_room_catalog_raw()
        rooms = []
        for ref in _iter_catalog_room_refs(catalog):
            summary = _radio_room_admin_summary(ref)
            if summary.get("radio_enabled"):
                rooms.append(summary)
        rooms.sort(key=lambda r: (str(r.get("category") or "").lower(), str(r.get("subcategory") or "").lower(), str(r.get("name") or "").lower()))
        return _admin_json_response({
            "ok": True,
            "rooms": rooms,
            "count": len(rooms),
            "max_stations_per_room": 16,
            "catalog_path": str(_room_catalog_path().name),
        })

    @app.route("/admin/room_radio/<path:room_name>/stations", methods=["POST"])
    @require_permission("admin:settings")
    @require_recent_admin_auth
    def admin_room_radio_update_stations(room_name):
        """Replace the station list for one official catalog room."""
        room_name = str(room_name or "").strip()
        if not room_name:
            return jsonify({"ok": False, "error": "Missing room name"}), 400
        payload = request.get_json(silent=True) or {}
        stations, err = _normalize_admin_station_payload(payload.get("stations"))
        if err:
            return jsonify({"ok": False, "error": err}), 400

        with _ROOM_CATALOG_WRITE_LOCK:
            catalog = _read_room_catalog_raw()
            target = None
            for ref in _iter_catalog_room_refs(catalog):
                if str(ref.get("name") or "").strip().lower() == room_name.lower():
                    target = ref
                    break
            if target is None:
                return jsonify({"ok": False, "error": "Official catalog room not found"}), 404

            cats = catalog.get("categories") or []
            cat = cats[target["category_index"]]
            sub = (cat.get("subcategories") or [])[target["subcategory_index"]]
            rooms = sub.get("rooms") or []
            existing = rooms[target["room_index"]]
            if isinstance(existing, dict):
                updated = dict(existing)
                updated["name"] = str(updated.get("name") or target.get("name") or room_name).strip()
            else:
                updated = {"name": str(target.get("name") or room_name).strip()}

            features = updated.get("features") if isinstance(updated.get("features"), list) else []
            feature_set = []
            seen_features = set()
            for flag in features:
                clean = str(flag or "").strip()
                if clean and clean not in seen_features:
                    seen_features.add(clean)
                    feature_set.append(clean)
            if stations and "room_radio" not in seen_features:
                feature_set.append("room_radio")
            updated["features"] = feature_set
            updated["stations"] = stations
            rooms[target["room_index"]] = updated
            sub["rooms"] = rooms
            cat["subcategories"][target["subcategory_index"]] = sub
            catalog["categories"][target["category_index"]] = cat
            _write_room_catalog_raw(catalog)

        try:
            log_audit_event(_actor(), "room_radio_stations_update", room_name, f"stations={len(stations)}")
        except Exception:
            pass
        return _admin_json_response({
            "ok": True,
            "room": updated.get("name") or room_name,
            "category": target.get("category") or "",
            "subcategory": target.get("subcategory") or "",
            "stations": stations,
            "station_count": len(stations),
        })

    # ── Delete custom room (admin) ────────────────────────────────
    @app.route("/admin/rooms/delete/<path:room>", methods=["POST"])
    @require_permission("room:delete")
    @require_recent_admin_auth
    def admin_room_delete(room):
        """Delete a *custom* room immediately (and force users out).

        Safety: official rooms (from chat_rooms.json) are not deletable through this endpoint.
        """
        room, room_error = _canonical_room_or_error(room)
        if room_error is not None:
            return room_error

        actor = _actor()
        reason = (request.form.get("reason") or "").strip()

        # Verify it's a custom room first (prevents accidentally deleting core rooms).
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT cr.is_private, COALESCE(r.room_kind, 'manual') AS room_kind
                      FROM custom_rooms cr
                 LEFT JOIN chat_rooms r ON r.name = cr.name
                     WHERE cr.name=%s;
                    """,
                    (room,),
                )
                row = cur.fetchone()
        except Exception:
            row = None

        if not row:
            return jsonify({"ok": False, "error": "not_custom", "message": "Only custom rooms can be deleted."}), 400

        room_kind = str(row[1] or "manual").strip().lower()
        if room_kind and room_kind != "custom":
            return jsonify({"ok": False, "error": "protected_room_kind", "message": "Permanent rooms cannot be deleted here."}), 400

        # Force-leave any connected users in this room (and voice).
        forced_leave = 0
        forced_voice_leave = 0

        # Voice first (so clients don't emit voice_room_leave when we force-leave the room UI).
        voice_users = []
        if VOICE_ROOMS_LOCK is not None:
            try:
                with VOICE_ROOMS_LOCK:
                    voice_users = sorted(list((VOICE_ROOMS or {}).get(room) or set()))
                    if room in (VOICE_ROOMS or {}):
                        try:
                            del VOICE_ROOMS[room]
                        except Exception:
                            pass
            except Exception:
                voice_users = []

        if socketio and voice_users:
            for uname in voice_users:
                for sid in _user_sids(uname):
                    try:
                        socketio.emit(
                            "voice_room_forced_leave",
                            {"room": room, "reason": "Room deleted", "limit": None},
                            to=sid,
                        )
                        forced_voice_leave += 1
                    except Exception:
                        pass

        # Now force-leave the text room.
        sids_in_room = []
        if _state_connected_room_targets is not None:
            try:
                sids_in_room = list(_state_connected_room_targets(room))
            except Exception:
                sids_in_room = []

        if socketio and sids_in_room:
            for sid, _uname in sids_in_room:
                try:
                    socketio.emit(
                        "room_forced_leave",
                        {"room": room, "reason": "Room deleted"},
                        to=sid,
                    )
                except Exception:
                    pass
                try:
                    socketio.server.leave_room(sid, room)
                except Exception:
                    pass
                forced_leave += 1

        # Update in-memory registry so we don't show ghost membership.
        if _state_update_connected_room is not None and sids_in_room:
            try:
                for sid, _uname in sids_in_room:
                    _state_update_connected_room(sid, None)
            except Exception:
                pass

        # Delete persisted state
        try:
            with conn.cursor() as cur:
                cleanup_stats = delete_custom_room_persisted_state(cur, [room])
                cur.execute("DELETE FROM custom_rooms WHERE name=%s;", (room,))
                cur.execute("DELETE FROM chat_rooms WHERE name=%s AND room_kind='custom';", (room,))
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return _admin_operation_error("room_delete", e, ok_style=True)

        # Audit + notify
        try:
            log_audit_event(actor, "room_delete", target=room, details=(reason or ""))
        except Exception:
            pass

        if socketio:
            try:
                socketio.emit("rooms_changed", {"deleted": room, "reason": "deleted_by_admin", "by": actor})
            except Exception:
                pass

        return _admin_json_response({
            "ok": True,
            "room": room,
            "forced_leave": forced_leave,
            "forced_voice_leave": forced_voice_leave,
            "cleanup_stats": cleanup_stats,
        })

    # ── Settings: general (persisted + runtime) ───────────────────
    @app.route("/admin/settings/general", methods=["GET", "POST"])
    @require_permission("admin:settings")
    def admin_settings_general():
        """Read/patch a safe subset of settings from the admin panel.

        Note: Some settings take effect immediately (server-side checks), while
        some require client reload or server restart. The UI should annotate.
        """
        if request.method != "GET":
            status = _admin_reauth_status(_actor())
            if not status.get("ok"):
                return _admin_reauth_required_response(status)

        allow_keys = {
            # feature flags
            "voice_enabled": "bool",
            "p2p_file_enabled": "bool",
            "giphy_enabled": "bool",
            "disable_file_transfer_globally": "bool",
            "disable_dm_files_globally": "bool",
            "disable_group_files_globally": "bool",
            "torrent_upload_enabled": "bool",
            "torrent_scrape_enabled": "bool",
            "torrent_public_fallback_scrape_enabled": "bool",
            "torrent_dht_scrape_enabled": "bool",
            "require_dm_e2ee": "bool",
            "allow_plaintext_dm_fallback": "bool",
            "require_group_e2ee": "bool",
            "require_private_room_e2ee": "bool",
            "require_room_e2ee": "bool",
            "encrypt_sensitive_profile_fields": "bool",
            "encrypt_email_at_rest": "bool",
            "encrypt_security_backups": "bool",
            "privacy_retention_enabled": "bool",
            "privacy_ip_user_agent_retention_days": "int",
            "privacy_audit_detail_retention_days": "int",
            "all_room_e2ee_impact_acknowledged": "bool",
            # room cleanup / session presence
            "allow_legacy_plaintext_room_history": "bool",
            "allow_legacy_plaintext_history": "bool",
            "allow_legacy_numeric_group_history": "bool",
            "disable_legacy_group_file_upload": "bool",
            "custom_room_idle_minutes": "int",
            "presence_idle_minutes": "int",
            "presence_offline_minutes": "int",
            "custom_room_idle_hours": "int",
            "custom_private_room_idle_minutes": "int",
            "custom_private_room_idle_hours": "int",
            "janitor_interval_seconds": "int",
            "cleanup_expired_auth_enabled": "bool",
            "cleanup_orphan_auth_enabled": "bool",
            "auth_token_retention_days": "int",
            "revoked_session_retention_days": "int",
            "password_reset_token_retention_days": "int",
            "orphan_auth_retention_days": "int",
            "auth_cleanup_batch_limit": "int",
            "privacy_retention_batch_limit": "int",
            "cleanup_revoked_private_files_enabled": "bool",
            "cleanup_orphan_private_file_blobs_enabled": "bool",
            "revoked_private_file_retention_days": "int",
            "orphan_private_file_grace_minutes": "int",
            "private_file_cleanup_batch_limit": "int",

            # public room autosplit / overflow shard controls
            "autoscale_rooms_enabled": "bool",
            "autoscale_room_capacity": "int",
            "autoscale_room_idle_minutes": "int",

            # limits
            "max_message_length": "int",
            "max_attachment_size": "int",
            "max_dm_file_bytes": "int",
            "max_group_upload_bytes": "int",
            "max_group_file_bytes": "int",
            "max_torrent_upload_bytes": "int",
            "max_user_file_storage_bytes": "int",
            "max_user_torrent_storage_bytes": "int",
            "max_torrent_total_size_bytes": "int",
            "torrent_dht_scrape_timeout_sec": "float",
            "torrent_dht_scrape_max_queries": "int",
            "torrent_public_fallback_trackers": "liststr",
            "group_msg_rate_limit": "int",
            "group_msg_rate_window_sec": "int",
            "antiabuse_exempt_staff": "bool",
            # client message text motion
            "chat_text_animation": "str",
            "dm_text_animation": "str",
            "group_text_animation": "str",
            # client notification sound defaults
            "sound_notifications_default": "bool",
            "sound_pack_load_local_builtins": "bool",
            "sound_pack_external_urls": "liststr",
            "sound_pack_default": "str",
            "sound_theme_default": "str",
            "sound_event_dm": "str",
            "sound_event_room_message": "str",
            "sound_event_group_message": "str",
            "sound_event_room_invite": "str",
            "sound_event_group_invite": "str",
            "sound_event_friend_request": "str",
            "sound_event_room_join": "str",
            "sound_event_file": "str",
            "sound_event_error": "str",
            # code-based emoticon catalog / asset source
            "emoticons_enabled": "bool",
            "emoticons_local_enabled": "bool",
            "emoticons_external_enabled": "bool",
            "emoticons_asset_mode": "str",
            "emoticons_local_root": "rawstr",
            "emoticons_external_asset_base_url": "rawstr",
            "emoticons_animation_stop_ms": "rawstr",
            "emoticons_boot_preload_enabled": "bool",
            "emoticons_boot_preload_limit": "rawstr",
            "emoticons_boot_preload_concurrency": "rawstr",
            "emoticons_catalog_cache_seconds": "rawstr",
            "emoticons_custom_entries": "jsonlist",
            # client sender labels / message grouping
            "room_show_sender_every_message": "bool",
            "dm_show_sender_every_message": "bool",
            "group_show_sender_every_message": "bool",
        }

        def _sanitize_torrent_public_fallback_trackers(value):
            defaults = [
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://open.stealth.si:80/announce",
                "udp://tracker.torrent.eu.org:451/announce",
                "udp://tracker.moeking.me:6969/announce",
                "https://tracker2.ctix.cn:443/announce",
                "https://tracker.tamersunion.org:443/announce",
            ]
            raw = value
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = [line.strip() for line in raw.replace(',', '\n').splitlines() if line.strip()]
            candidates = raw if isinstance(raw, list) else defaults
            out = []
            for item in candidates:
                text = str(item or "").strip()
                if not text or text in out:
                    continue
                parsed = urlparse(text)
                if parsed.scheme not in {"udp", "http", "https"}:
                    continue
                if parsed.username or parsed.password:
                    continue
                if any(ch.isspace() for ch in text):
                    continue
                out.append(text)
                if len(out) >= 12:
                    break
            return out or defaults

        if request.method == "GET":
            out = {}
            for k in allow_keys.keys():
                out[k] = settings.get(k)

            # Mirror legacy/current aliases so the admin UI sees the effective values.
            if out.get("max_group_upload_bytes") is None and settings.get("max_group_file_bytes") is not None:
                out["max_group_upload_bytes"] = settings.get("max_group_file_bytes")
            if out.get("max_group_file_bytes") is None and settings.get("max_group_upload_bytes") is not None:
                out["max_group_file_bytes"] = settings.get("max_group_upload_bytes")

            if out.get("allow_legacy_plaintext_room_history") is None and settings.get("allow_legacy_plaintext_history") is not None:
                out["allow_legacy_plaintext_room_history"] = settings.get("allow_legacy_plaintext_history")
            if out.get("allow_legacy_plaintext_history") is None and settings.get("allow_legacy_plaintext_room_history") is not None:
                out["allow_legacy_plaintext_history"] = settings.get("allow_legacy_plaintext_room_history")

            if out.get("custom_room_idle_minutes") is None and settings.get("custom_room_idle_hours") is not None:
                try:
                    out["custom_room_idle_minutes"] = int(settings.get("custom_room_idle_hours")) * 60
                except Exception:
                    pass
            if out.get("custom_room_idle_hours") is None and settings.get("custom_room_idle_minutes") is not None:
                try:
                    out["custom_room_idle_hours"] = max(1, (int(settings.get("custom_room_idle_minutes")) + 59) // 60)
                except Exception:
                    pass

            if out.get("custom_private_room_idle_minutes") is None and settings.get("custom_private_room_idle_hours") is not None:
                try:
                    out["custom_private_room_idle_minutes"] = int(settings.get("custom_private_room_idle_hours")) * 60
                except Exception:
                    pass
            if out.get("custom_private_room_idle_hours") is None and settings.get("custom_private_room_idle_minutes") is not None:
                try:
                    out["custom_private_room_idle_hours"] = max(1, (int(settings.get("custom_private_room_idle_minutes")) + 59) // 60)
                except Exception:
                    pass

            autoscale_defaults = {
                "autoscale_rooms_enabled": True,
                "autoscale_room_capacity": 30,
                "autoscale_room_idle_minutes": 30,
            }
            for key, default_value in autoscale_defaults.items():
                if out.get(key) is None:
                    out[key] = default_value

            dm_e2ee_defaults = {
                "require_dm_e2ee": True,
                "allow_plaintext_dm_fallback": False,
                "require_group_e2ee": True,
                "allow_legacy_numeric_group_history": False,
                "disable_legacy_group_file_upload": True,
                "require_private_room_e2ee": True,
                "require_room_e2ee": False,
                "encrypt_sensitive_profile_fields": True,
                "encrypt_email_at_rest": True,
                "encrypt_security_backups": True,
                "privacy_retention_enabled": True,
                "privacy_ip_user_agent_retention_days": 30,
                "privacy_audit_detail_retention_days": 90,
                "all_room_e2ee_impact_acknowledged": False,
                "cleanup_expired_auth_enabled": True,
                "cleanup_orphan_auth_enabled": True,
                "auth_token_retention_days": 30,
                "revoked_session_retention_days": 30,
                "password_reset_token_retention_days": 7,
                "orphan_auth_retention_days": 1,
                "auth_cleanup_batch_limit": 500,
                "privacy_retention_batch_limit": 500,
            }
            for key, default_value in dm_e2ee_defaults.items():
                if out.get(key) is None:
                    out[key] = default_value
            if bool(out.get("require_dm_e2ee", True)):
                out["allow_plaintext_dm_fallback"] = False

            torrent_defaults = {
                "torrent_upload_enabled": True,
                "torrent_scrape_enabled": False,
                "torrent_public_fallback_scrape_enabled": True,
                "torrent_dht_scrape_enabled": True,
                "torrent_dht_scrape_timeout_sec": 0.9,
                "torrent_dht_scrape_max_queries": 24,
                "torrent_public_fallback_trackers": [
                    "udp://tracker.opentrackr.org:1337/announce",
                    "udp://open.stealth.si:80/announce",
                    "udp://tracker.torrent.eu.org:451/announce",
                    "udp://tracker.moeking.me:6969/announce",
                    "https://tracker2.ctix.cn:443/announce",
                    "https://tracker.tamersunion.org:443/announce",
                ],
            }
            for key, default_value in torrent_defaults.items():
                if out.get(key) is None:
                    out[key] = default_value
            out["torrent_public_fallback_trackers"] = _sanitize_torrent_public_fallback_trackers(out.get("torrent_public_fallback_trackers"))

            sound_defaults = {
                "sound_notifications_default": True,
                "sound_pack_load_local_builtins": True,
                "sound_pack_external_urls": [],
                "sound_pack_default": "echo_modern_generated",
                "sound_theme_default": "soft_chime",
                "sound_event_dm": "mellow_pluck",
                "sound_event_room_message": "soft_chime",
                "sound_event_group_message": "sonar_ping",
                "sound_event_room_invite": "doorbell_duo",
                "sound_event_group_invite": "doorbell_duo",
                "sound_event_friend_request": "success_twinkle",
                "sound_event_room_join": "page_flip",
                "sound_event_file": "digital_drop",
                "sound_event_error": "warning_pulse",
            }
            for key, default_value in sound_defaults.items():
                if out.get(key) is None:
                    out[key] = default_value

            emoticon_defaults = {
                "emoticons_enabled": True,
                "emoticons_local_enabled": True,
                "emoticons_external_enabled": True,
                "emoticons_asset_mode": "local_first",
                "emoticons_local_root": "emoticons",
                "emoticons_external_asset_base_url": "https://github.com/chinhodado/ym_emo_fb",
                "emoticons_animation_stop_ms": 4500,
                "emoticons_boot_preload_enabled": True,
                "emoticons_boot_preload_limit": 180,
                "emoticons_boot_preload_concurrency": 4,
                "emoticons_catalog_cache_seconds": 86400,
                "emoticons_custom_entries": [],
            }
            for key, default_value in emoticon_defaults.items():
                if out.get(key) is None:
                    out[key] = default_value

            out["sound_pack_load_local_builtins"] = sound_pack_local_builtins_enabled(out.get("sound_pack_load_local_builtins"), default=True)
            out["sound_pack_external_urls"] = sanitize_sound_pack_external_urls(out.get("sound_pack_external_urls"))
            out["sound_pack_default"] = normalize_sound_pack_identifier(out.get("sound_pack_default"), "echo_modern_generated")
            for key, default_theme in {
                "sound_theme_default": "soft_chime",
                "sound_event_dm": "mellow_pluck",
                "sound_event_room_message": "soft_chime",
                "sound_event_group_message": "sonar_ping",
                "sound_event_room_invite": "doorbell_duo",
                "sound_event_group_invite": "doorbell_duo",
                "sound_event_friend_request": "success_twinkle",
                "sound_event_room_join": "page_flip",
                "sound_event_file": "digital_drop",
                "sound_event_error": "warning_pulse",
            }.items():
                value = normalize_sound_pack_identifier(out.get(key), default_theme)
                out[key] = "soft_chime" if value in {"classic_beep", "beep", "computer_beep"} else value

            return _admin_json_response({"ok": True, "settings": out})

        actor = _actor()
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return _admin_json_response({"ok": False, "error": "Invalid JSON"}, 400)

        patch = {}
        for k, typ in allow_keys.items():
            if k not in payload:
                continue
            v = payload.get(k)
            try:
                if typ == "bool":
                    if isinstance(v, bool):
                        patch[k] = v
                    else:
                        patch[k] = str(v).strip().lower() in {"1", "true", "yes", "on"}
                elif typ == "str":
                    patch[k] = str(v).strip().lower()
                elif typ == "rawstr":
                    patch[k] = str(v).strip()
                elif typ == "jsonlist":
                    if isinstance(v, str):
                        try:
                            parsed_json = json.loads(v)
                        except Exception:
                            parsed_json = []
                        patch[k] = parsed_json if isinstance(parsed_json, list) else []
                    elif isinstance(v, list):
                        patch[k] = v
                    else:
                        patch[k] = []
                elif typ == "liststr":
                    if isinstance(v, str):
                        try:
                            parsed_list = json.loads(v)
                        except Exception:
                            parsed_list = [line.strip() for line in v.replace(',', '\n').splitlines() if line.strip()]
                        patch[k] = parsed_list
                    elif isinstance(v, (list, tuple, set)):
                        patch[k] = [str(item).strip() for item in v if str(item).strip()]
                    else:
                        patch[k] = []
                elif typ == "float":
                    patch[k] = float(v)
                else:
                    patch[k] = int(v)
            except Exception:
                return _admin_json_response({"ok": False, "error": f"Invalid value for {k}"}, 400)

        # Normalize and sanity-check a few.
        if patch.get("require_dm_e2ee") is True:
            patch["allow_plaintext_dm_fallback"] = False
        if patch.get("allow_plaintext_dm_fallback") is True and patch.get("require_dm_e2ee") is not False:
            patch["require_dm_e2ee"] = False

        if "max_message_length" in patch:
            patch["max_message_length"] = max(50, min(int(patch["max_message_length"]), 20000))
        if "presence_idle_minutes" in patch:
            patch["presence_idle_minutes"] = max(0, min(int(patch["presence_idle_minutes"]), 1440))
        if "presence_offline_minutes" in patch:
            patch["presence_offline_minutes"] = max(0, min(int(patch["presence_offline_minutes"]), 1440))
        if "custom_room_idle_minutes" in patch:
            patch["custom_room_idle_minutes"] = max(1, min(int(patch["custom_room_idle_minutes"]), 24 * 60 * 365))
            patch["custom_room_idle_hours"] = max(1, (int(patch["custom_room_idle_minutes"]) + 59) // 60)
        elif "custom_room_idle_hours" in patch:
            patch["custom_room_idle_hours"] = max(1, min(int(patch["custom_room_idle_hours"]), 24 * 365))
            patch["custom_room_idle_minutes"] = int(patch["custom_room_idle_hours"]) * 60

        if "custom_private_room_idle_minutes" in patch:
            patch["custom_private_room_idle_minutes"] = max(1, min(int(patch["custom_private_room_idle_minutes"]), 24 * 60 * 365))
            patch["custom_private_room_idle_hours"] = max(1, (int(patch["custom_private_room_idle_minutes"]) + 59) // 60)
        elif "custom_private_room_idle_hours" in patch:
            patch["custom_private_room_idle_hours"] = max(1, min(int(patch["custom_private_room_idle_hours"]), 24 * 365))
            patch["custom_private_room_idle_minutes"] = int(patch["custom_private_room_idle_hours"]) * 60

        if "allow_legacy_plaintext_room_history" in patch and "allow_legacy_plaintext_history" not in patch:
            patch["allow_legacy_plaintext_history"] = bool(patch["allow_legacy_plaintext_room_history"])
        elif "allow_legacy_plaintext_history" in patch and "allow_legacy_plaintext_room_history" not in patch:
            patch["allow_legacy_plaintext_room_history"] = bool(patch["allow_legacy_plaintext_history"])

        if "janitor_interval_seconds" in patch:
            patch["janitor_interval_seconds"] = max(10, min(int(patch["janitor_interval_seconds"]), 3600))
        if "privacy_ip_user_agent_retention_days" in patch:
            patch["privacy_ip_user_agent_retention_days"] = max(0, min(int(patch["privacy_ip_user_agent_retention_days"]), 3650))
        if "privacy_audit_detail_retention_days" in patch:
            patch["privacy_audit_detail_retention_days"] = max(0, min(int(patch["privacy_audit_detail_retention_days"]), 3650))
        for key, default_value in (
            ("auth_token_retention_days", 30),
            ("revoked_session_retention_days", 30),
            ("password_reset_token_retention_days", 7),
        ):
            if key in patch:
                patch[key] = max(1, min(int(patch[key]), 3650))
        if "orphan_auth_retention_days" in patch:
            patch["orphan_auth_retention_days"] = max(0, min(int(patch["orphan_auth_retention_days"]), 3650))
        if "revoked_private_file_retention_days" in patch:
            patch["revoked_private_file_retention_days"] = max(1, min(int(patch["revoked_private_file_retention_days"]), 3650))
        if "orphan_private_file_grace_minutes" in patch:
            patch["orphan_private_file_grace_minutes"] = max(5, min(int(patch["orphan_private_file_grace_minutes"]), 24 * 60 * 30))
        for key in ("auth_cleanup_batch_limit", "privacy_retention_batch_limit", "private_file_cleanup_batch_limit"):
            if key in patch:
                patch[key] = max(1, min(int(patch[key]), 10000))

        if patch.get("require_room_e2ee") is True and not bool(settings.get("require_room_e2ee", False)):
            ack = bool(payload.get("confirm_all_room_e2ee_impact") or patch.get("all_room_e2ee_impact_acknowledged"))
            if not ack:
                return _admin_json_response({"ok": False, "error": "All-room E2EE strict mode requires impact acknowledgement", "all_room_e2ee_impact": public_room_e2ee_impact_report(settings)}, 409)
            patch["all_room_e2ee_impact_acknowledged"] = True

        if "autoscale_room_capacity" in patch:
            patch["autoscale_room_capacity"] = max(2, min(int(patch["autoscale_room_capacity"]), 5000))
        if "autoscale_room_idle_minutes" in patch:
            patch["autoscale_room_idle_minutes"] = max(1, min(int(patch["autoscale_room_idle_minutes"]), 10080))

        for key in ("max_attachment_size", "max_dm_file_bytes"):
            if key in patch:
                patch[key] = max(1024 * 256, min(int(patch[key]), 1024 * 1024 * 1024))  # 256KB..1GB

        if "max_group_upload_bytes" in patch or "max_group_file_bytes" in patch:
            resolved_group_bytes = patch.get("max_group_upload_bytes", patch.get("max_group_file_bytes"))
            resolved_group_bytes = max(1024 * 256, min(int(resolved_group_bytes), 1024 * 1024 * 1024))
            patch["max_group_upload_bytes"] = resolved_group_bytes
            patch["max_group_file_bytes"] = resolved_group_bytes

        if "max_torrent_upload_bytes" in patch:
            patch["max_torrent_upload_bytes"] = max(1024, min(int(patch["max_torrent_upload_bytes"]), 5 * 1024 * 1024))
        if "max_user_file_storage_bytes" in patch:
            patch["max_user_file_storage_bytes"] = max(0, min(int(patch["max_user_file_storage_bytes"]), 1024 * 1024 * 1024 * 1024))
        if "max_user_torrent_storage_bytes" in patch:
            patch["max_user_torrent_storage_bytes"] = max(0, min(int(patch["max_user_torrent_storage_bytes"]), 1024 * 1024 * 1024 * 1024))
        if "max_torrent_total_size_bytes" in patch:
            patch["max_torrent_total_size_bytes"] = max(0, min(int(patch["max_torrent_total_size_bytes"]), 1024 * 1024 * 1024 * 1024 * 1024))
        if "torrent_dht_scrape_timeout_sec" in patch:
            patch["torrent_dht_scrape_timeout_sec"] = max(0.2, min(float(patch["torrent_dht_scrape_timeout_sec"]), 3.0))
        if "torrent_dht_scrape_max_queries" in patch:
            patch["torrent_dht_scrape_max_queries"] = max(0, min(int(patch["torrent_dht_scrape_max_queries"]), 96))
        if "torrent_public_fallback_trackers" in patch:
            clean_trackers = _sanitize_torrent_public_fallback_trackers(patch.get("torrent_public_fallback_trackers"))
            raw_count = len(patch.get("torrent_public_fallback_trackers") or []) if isinstance(patch.get("torrent_public_fallback_trackers"), (list, tuple, set)) else 0
            if raw_count and not clean_trackers:
                return _admin_json_response({"ok": False, "error": "Public fallback trackers must be udp/http/https announce URLs without embedded credentials"}, 400)
            patch["torrent_public_fallback_trackers"] = clean_trackers
        if "group_msg_rate_limit" in patch:
            patch["group_msg_rate_limit"] = max(5, min(int(patch["group_msg_rate_limit"]), 10000))
        if "group_msg_rate_window_sec" in patch:
            patch["group_msg_rate_window_sec"] = max(10, min(int(patch["group_msg_rate_window_sec"]), 3600))

        allowed_text_animations = {"none", "fade", "rise", "slide", "scale"}
        for key in ("chat_text_animation", "dm_text_animation", "group_text_animation"):
            if key in patch and patch[key] not in allowed_text_animations:
                return _admin_json_response({"ok": False, "error": f"Invalid value for {key}"}, 400)

        if "sound_pack_external_urls" in patch:
            clean_urls = sanitize_sound_pack_external_urls(patch.get("sound_pack_external_urls"))
            raw_count = len(patch.get("sound_pack_external_urls") or []) if isinstance(patch.get("sound_pack_external_urls"), (list, tuple, set)) else len(str(patch.get("sound_pack_external_urls") or "").splitlines())
            if raw_count and not clean_urls:
                return _admin_json_response({"ok": False, "error": "Online sound-pack URLs must be HTTPS .js URLs, one per line"}, 400)
            patch["sound_pack_external_urls"] = clean_urls

        if "sound_pack_default" in patch:
            patch["sound_pack_default"] = normalize_sound_pack_identifier(patch.get("sound_pack_default"), "echo_modern_generated")

        if "emoticons_asset_mode" in patch:
            mode = str(patch.get("emoticons_asset_mode") or "local_first").strip().lower().replace("-", "_")
            patch["emoticons_asset_mode"] = mode if mode in {"local_first", "external_first"} else "local_first"
        if "emoticons_local_root" in patch:
            root = str(patch.get("emoticons_local_root") or "emoticons").strip().replace("\\", "/")
            if not root or root.startswith("/") or ".." in root.split("/"):
                root = "emoticons"
            patch["emoticons_local_root"] = root
        if "emoticons_external_asset_base_url" in patch:
            raw_url = str(patch.get("emoticons_external_asset_base_url") or "").strip()
            if raw_url:
                parsed = urlparse(raw_url)
                if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
                    return _admin_json_response({"ok": False, "error": "Emoticon external image base must be an http/https URL without embedded credentials"}, 400)
                raw_url = raw_url.rstrip("/")
            patch["emoticons_external_asset_base_url"] = raw_url
        if "emoticons_animation_stop_ms" in patch:
            try:
                patch["emoticons_animation_stop_ms"] = max(0, min(60000, int(str(patch.get("emoticons_animation_stop_ms") or "4500").strip())))
            except Exception:
                patch["emoticons_animation_stop_ms"] = 4500
        if "emoticons_boot_preload_limit" in patch:
            try:
                patch["emoticons_boot_preload_limit"] = max(0, min(240, int(str(patch.get("emoticons_boot_preload_limit") or "180").strip())))
            except Exception:
                patch["emoticons_boot_preload_limit"] = 180
        if "emoticons_boot_preload_concurrency" in patch:
            try:
                patch["emoticons_boot_preload_concurrency"] = max(1, min(8, int(str(patch.get("emoticons_boot_preload_concurrency") or "4").strip())))
            except Exception:
                patch["emoticons_boot_preload_concurrency"] = 4
        if "emoticons_catalog_cache_seconds" in patch:
            try:
                patch["emoticons_catalog_cache_seconds"] = max(0, min(31536000, int(str(patch.get("emoticons_catalog_cache_seconds") or "86400").strip())))
            except Exception:
                patch["emoticons_catalog_cache_seconds"] = 86400
        if "emoticons_custom_entries" in patch:
            clean_custom = []
            seen_names = set()
            seen_codes = set()
            for item in patch.get("emoticons_custom_entries") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip().lower()
                code = str(item.get("code") or "").strip()
                if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", name or "", re.I):
                    continue
                if not code or len(code) > 32 or any(ch in code for ch in "\r\n\t"):
                    continue
                if name in seen_names or code.lower() in seen_codes:
                    continue
                seen_names.add(name)
                seen_codes.add(code.lower())
                entry = {"name": name, "code": code}
                for field in ("label", "category", "filename"):
                    if item.get(field) is not None:
                        entry[field] = str(item.get(field) or "").strip()[:300]
                if item.get("url") is not None:
                    custom_url = str(item.get("url") or "").strip()[:500]
                    if custom_url:
                        parsed_custom_url = urlparse(custom_url)
                        if parsed_custom_url.scheme not in {"https", "http"} or not parsed_custom_url.netloc or parsed_custom_url.username or parsed_custom_url.password:
                            continue
                        custom_url = custom_url.rstrip("/")
                    entry["url"] = custom_url
                clean_custom.append(entry)
                if len(clean_custom) >= 250:
                    break
            patch["emoticons_custom_entries"] = clean_custom

        sound_defaults = {
            "sound_theme_default": "soft_chime",
            "sound_event_dm": "mellow_pluck",
            "sound_event_room_message": "soft_chime",
            "sound_event_group_message": "sonar_ping",
            "sound_event_room_invite": "doorbell_duo",
            "sound_event_group_invite": "doorbell_duo",
            "sound_event_friend_request": "success_twinkle",
            "sound_event_room_join": "page_flip",
            "sound_event_file": "digital_drop",
            "sound_event_error": "warning_pulse",
        }
        for key, default_theme in sound_defaults.items():
            if key not in patch:
                continue
            value = normalize_sound_pack_identifier(patch.get(key), default_theme)
            if value in {"classic_beep", "beep", "computer_beep"}:
                value = "soft_chime"
            patch[key] = value

        if not patch:
            return _admin_json_response({"ok": False, "error": "No changes"}, 400)

        # Apply to runtime dict and persist to settings file.
        for k, v in patch.items():
            settings[k] = v
        persisted = _persist_settings_patch(patch)

        try:
            safe_patch_meta = {k: ("[redacted]" if k in (_last_settings_persistence_meta().get("runtime_only_keys") or []) else v) for k, v in patch.items()}
            log_audit_event(actor, "set_general_settings", "*", json.dumps({"patch": safe_patch_meta, "persisted": bool(persisted), "persistence": _last_settings_persistence_meta()}))
        except Exception:
            pass

        return _admin_json_response({"ok": True, "persisted": bool(persisted), "persistence": _last_settings_persistence_meta(), "patch": patch})

    # ── Settings: anti-abuse (persisted + runtime) ───────────────────
    @app.route("/admin/settings/antiabuse", methods=["GET", "POST"])
    @require_permission("admin:settings")
    def admin_settings_antiabuse():
        """Read/patch anti-abuse settings.

        These take effect immediately for Socket.IO handlers.
        """
        if request.method != "GET":
            status = _admin_reauth_status(_actor())
            if not status.get("ok"):
                return _admin_reauth_required_response(status)

        allow_keys = {
            # burst limits
            "room_msg_rate_limit": "str",
            "room_msg_rate_window_sec": "int",
            "dm_msg_rate_limit": "str",
            "dm_msg_rate_window_sec": "int",
            "enable_room_typing_indicators": "bool",
            "enable_dm_typing_indicators": "bool",
            "enable_group_typing_indicators": "bool",
            "dm_typing_rate_limit": "str",
            "dm_typing_rate_window_sec": "int",
            "group_typing_rate_limit": "str",
            "group_typing_rate_window_sec": "int",
            "file_offer_rate_limit": "str",
            "file_offer_rate_window_sec": "int",
            "room_gif_rate_limit": "str",
            "room_gif_rate_window_sec": "int",
            "room_torrent_rate_limit": "str",
            "room_torrent_rate_window_sec": "int",
            "room_typing_rate_limit": "str",
            "room_typing_rate_window_sec": "int",
            "room_reaction_rate_limit": "str",
            "room_reaction_rate_window_sec": "int",
            "room_media_action_rate_limit": "str",
            "room_media_action_rate_window_sec": "int",
            "room_media_presence_rate_limit": "str",
            "room_media_presence_rate_window_sec": "int",
            "room_catalog_rate_limit": "str",
            "room_catalog_rate_window_sec": "int",
            "room_counts_rate_limit": "str",
            "room_counts_rate_window_sec": "int",
            "wave_user_rate_limit": "str",
            "wave_user_rate_window_sec": "int",
            "poll_vote_rate_limit": "str",
            "poll_vote_rate_window_sec": "int",
            "room_control_rate_limit": "str",
            "room_control_rate_window_sec": "int",
            # slowmode
            "room_slowmode_default_sec": "int",
            # auto-mute
            "antiabuse_strikes_before_mute": "int",
            "antiabuse_strike_window_sec": "int",
            "antiabuse_auto_mute_minutes": "int",
            # join/create/friendreq flood control
            "room_join_rate_limit": "str",
            "room_join_rate_window_sec": "int",
            "room_switch_cooldown_sec": "int",
            "room_create_rate_limit": "str",
            "room_create_rate_window_sec": "int",
            "allow_user_create_rooms": "bool",
            "max_room_name_length": "int",
            "block_custom_room_terms_enabled": "bool",
            "blocked_custom_room_terms": "str",
            "block_registration_terms_enabled": "bool",
            "blocked_registration_terms": "str",
            "friend_req_rate_limit": "str",
            "friend_req_rate_window_sec": "int",
            "friend_req_unique_targets_max": "int",
            "friend_req_unique_targets_window_sec": "int",
            # plaintext heuristics
            "max_links_per_message": "int",
            "max_magnets_per_message": "int",
            "max_mentions_per_message": "int",
            "dup_msg_window_sec": "int",
            "dup_msg_max": "int",
            "dup_msg_min_length": "int",
            "dup_msg_normalize": "bool",
        }

        if request.method == "GET":
            out = {k: settings.get(k) for k in allow_keys.keys()}
            out["enable_room_typing_indicators"] = bool(settings.get("enable_room_typing_indicators", False))
            out["enable_dm_typing_indicators"] = bool(settings.get("enable_dm_typing_indicators", True))
            out["enable_group_typing_indicators"] = bool(settings.get("enable_group_typing_indicators", True))
            out["dm_typing_rate_limit"] = str(settings.get("dm_typing_rate_limit") or "30@10")
            out["dm_typing_rate_window_sec"] = int(settings.get("dm_typing_rate_window_sec") or 10)
            out["group_typing_rate_limit"] = str(settings.get("group_typing_rate_limit") or "30@10")
            out["group_typing_rate_window_sec"] = int(settings.get("group_typing_rate_window_sec") or 10)
            return _admin_json_response({"ok": True, "settings": out})

        actor = _actor()
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return _admin_json_response({"ok": False, "error": "Invalid JSON"}, 400)

        patch = {}
        for k, typ in allow_keys.items():
            if k not in payload:
                continue
            v = payload.get(k)
            try:
                if typ == "bool":
                    if isinstance(v, bool):
                        patch[k] = v
                    else:
                        patch[k] = str(v).strip().lower() in {"1", "true", "yes", "on"}
                elif typ == "int":
                    patch[k] = int(v)
                else:  # str
                    s = str(v).strip()
                    # Rate limit strings should be short and printable
                    if len(s) > 64:
                        return _admin_json_response({"ok": False, "error": f"Value too long for {k}"}, 400)
                    if any(ord(c) < 32 for c in s):
                        return _admin_json_response({"ok": False, "error": f"Invalid characters for {k}"}, 400)
                    patch[k] = s
            except Exception:
                return _admin_json_response({"ok": False, "error": f"Invalid value for {k}"}, 400)

        # Bounds/sanity
        def _normalize_admin_rate_limit(key: str, value) -> str | None:
            text = str(value or "").strip().lower()
            if not text:
                return None
            window_key = key.replace("_rate_limit", "_rate_window_sec")
            try:
                default_window = int(patch.get(window_key) or settings.get(window_key) or 60)
            except Exception:
                default_window = 60
            if re.fullmatch(r"\d+", text):
                limit = int(text)
                window = max(1, min(default_window, 86400))
            else:
                if not re.fullmatch(r"\d+\s*@\s*\d+|\d+\s*(?:per\s*)?(?:second|sec|minute|min|hour|day)s?|\d+\s*/\s*(?:sec|second|min|minute|hour|day)s?", text):
                    return None
                limit, window = parse_rate_limit_value(text, default_limit=0, default_window=0)
            if limit <= 0 or window <= 0 or limit > 100000 or window > 86400:
                return None
            return f"{int(limit)}@{int(window)}"

        for rate_key in [k for k in patch.keys() if k.endswith("_rate_limit")]:
            normalized_rate = _normalize_admin_rate_limit(rate_key, patch.get(rate_key))
            if not normalized_rate:
                return _admin_json_response({"ok": False, "error": f"Invalid rate-limit value for {rate_key}; use forms like 20@10, 10 per minute, or 10/min"}, 400)
            patch[rate_key] = normalized_rate

        def clamp_int(key: str, lo: int, hi: int):
            if key in patch:
                patch[key] = max(lo, min(int(patch[key]), hi))

        # windows
        for w in (
            "room_msg_rate_window_sec",
            "dm_msg_rate_window_sec",
            "dm_typing_rate_window_sec",
            "group_typing_rate_window_sec",
            "file_offer_rate_window_sec",
            "room_gif_rate_window_sec",
            "room_torrent_rate_window_sec",
            "room_typing_rate_window_sec",
            "room_reaction_rate_window_sec",
            "room_media_action_rate_window_sec",
            "room_media_presence_rate_window_sec",
            "room_catalog_rate_window_sec",
            "room_counts_rate_window_sec",
            "wave_user_rate_window_sec",
            "room_control_rate_window_sec",
            "room_join_rate_window_sec",
            "room_create_rate_window_sec",
            "friend_req_rate_window_sec",
            "friend_req_unique_targets_window_sec",
        ):
            clamp_int(w, 1, 3600)

        clamp_int("room_slowmode_default_sec", 0, 3600)
        clamp_int("room_switch_cooldown_sec", 0, 30)
        clamp_int("antiabuse_strikes_before_mute", 1, 100)
        clamp_int("antiabuse_strike_window_sec", 5, 600)
        clamp_int("antiabuse_auto_mute_minutes", 1, 1440)
        clamp_int("max_room_name_length", 8, 128)

        clamp_int("max_links_per_message", 0, 100)
        clamp_int("max_magnets_per_message", 0, 50)
        clamp_int("max_mentions_per_message", 0, 100)
        clamp_int("dup_msg_window_sec", 0, 300)
        clamp_int("dup_msg_max", 1, 50)
        clamp_int("dup_msg_min_length", 1, 1000)

        if not patch:
            return _admin_json_response({"ok": False, "error": "No changes"}, 400)

        for k, v in patch.items():
            settings[k] = v

        persisted = _persist_settings_patch(patch)

        try:
            log_audit_event(actor, "set_antiabuse_settings", "*", json.dumps({"patch_keys": sorted(patch.keys()), "persisted": bool(persisted), "persistence": _last_settings_persistence_meta()}))
        except Exception:
            pass

        return _admin_json_response({"ok": True, "persisted": bool(persisted), "persistence": _last_settings_persistence_meta(), "patch": patch})

    # ── Audit log viewer ──────────────────────────────────────────
    @app.route("/admin/audit/recent")
    @require_permission("admin:audit")
    def admin_audit_recent():
        q = (request.args.get("q") or "").strip()
        actor = (request.args.get("actor") or "").strip()
        action = (request.args.get("action") or "").strip()
        target = (request.args.get("target") or "").strip()
        for field_name, value in {"q": q, "actor": actor, "action": action, "target": target}.items():
            if len(value) > 96:
                return _admin_json_response({"ok": False, "error": f"{field_name} is too long", "max_length": 96}, 400)
        try:
            limit = int(request.args.get("limit") or 50)
        except Exception:
            limit = 50
        limit = max(1, min(limit, 200))

        clauses = []
        params = []
        if q:
            clauses.append("(actor ILIKE %s ESCAPE '\\' OR action ILIKE %s ESCAPE '\\' OR COALESCE(target,'') ILIKE %s ESCAPE '\\' OR COALESCE(details,'') ILIKE %s ESCAPE '\\')")
            like_q = _admin_like_pattern(q, max_len=96)
            params.extend([like_q, like_q, like_q, like_q])
        if actor:
            clauses.append("actor ILIKE %s ESCAPE '\\'")
            params.append(_admin_like_pattern(actor, max_len=96))
        if action:
            clauses.append("action ILIKE %s ESCAPE '\\'")
            params.append(_admin_like_pattern(action, max_len=96))
        if target:
            clauses.append("COALESCE(target,'') ILIKE %s ESCAPE '\\'")
            params.append(_admin_like_pattern(target, max_len=96))

        sql = "SELECT actor, action, target, timestamp, details FROM audit_log"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT %s;"
        params.append(limit)

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall() or []
        except Exception as e:
            return _admin_operation_error("admin_audit_recent", e, ok_style=True)

        out = []
        for r in rows:
            out.append(_admin_safe_audit_event(r[0], r[1], r[2], r[3], r[4]))
        return _admin_json_response({"ok": True, "events": out, "q": q, "actor": actor, "action": action, "target": target, "limit": limit})

    @app.route("/admin/analytics/overview")
    @require_permission("admin:audit")
    def admin_analytics_overview():
        now = _utcnow()
        window_24h = now - timedelta(hours=24)
        window_7d = now - timedelta(days=7)
        summary = {
            "audit_events_24h": 0,
            "sanctions_24h": 0,
            "incidents_7d": 0,
            "room_actions_24h": 0,
            "active_sanctions": 0,
        }
        hourly = {}
        daily = {}
        action_types = []
        top_targets = []
        top_actors = []
        room_activity = []
        conn = get_db()
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT COUNT(*)
                      FROM audit_log
                     WHERE timestamp >= %s;
                    """,
                    (window_24h,),
                )
                summary["audit_events_24h"] = int((cur.fetchone() or [0])[0] or 0)
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
            try:
                cur.execute(
                    """
                    SELECT COUNT(*)
                      FROM user_sanctions
                     WHERE created_at >= %s;
                    """,
                    (window_24h,),
                )
                summary["sanctions_24h"] = int((cur.fetchone() or [0])[0] or 0)
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
            try:
                cur.execute(
                    """
                    SELECT COUNT(*)
                      FROM user_sanctions
                     WHERE expires_at IS NULL OR expires_at > NOW();
                    """
                )
                summary["active_sanctions"] = int((cur.fetchone() or [0])[0] or 0)
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
            try:
                cur.execute(
                    """
                    SELECT COUNT(*)
                      FROM audit_log
                     WHERE timestamp >= %s
                       AND (
                            action IN ('incident_mode_apply', 'incident_mode_disable')
                            OR action ILIKE 'incident_mode_%'
                       );
                    """,
                    (window_7d,),
                )
                summary["incidents_7d"] = int((cur.fetchone() or [0])[0] or 0)
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
            try:
                cur.execute(
                    """
                    SELECT COUNT(*)
                      FROM audit_log
                     WHERE timestamp >= %s
                       AND action IN (
                            'lock_room','unlock_room','set_room_readonly','set_room_slowmode',
                            'clear_room','delete_room','kick_from_room','ban_from_room'
                       );
                    """,
                    (window_24h,),
                )
                summary["room_actions_24h"] = int((cur.fetchone() or [0])[0] or 0)
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
            try:
                cur.execute(
                    """
                    SELECT DATE_TRUNC('hour', timestamp) AS bucket, COUNT(*)
                      FROM audit_log
                     WHERE timestamp >= %s
                     GROUP BY 1
                     ORDER BY 1 ASC;
                    """,
                    (window_24h,),
                )
                hourly = {
                    row[0].replace(tzinfo=timezone.utc).isoformat() if getattr(row[0], 'tzinfo', None) is None else row[0].astimezone(timezone.utc).isoformat(): int(row[1] or 0)
                    for row in (cur.fetchall() or [])
                    if row and row[0] is not None
                }
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
                hourly = {}
            try:
                cur.execute(
                    """
                    SELECT DATE_TRUNC('day', created_at) AS bucket, COUNT(*)
                      FROM user_sanctions
                     WHERE created_at >= %s
                     GROUP BY 1
                     ORDER BY 1 ASC;
                    """,
                    (window_7d,),
                )
                daily = {
                    row[0].replace(tzinfo=timezone.utc).isoformat() if getattr(row[0], 'tzinfo', None) is None else row[0].astimezone(timezone.utc).isoformat(): int(row[1] or 0)
                    for row in (cur.fetchall() or [])
                    if row and row[0] is not None
                }
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
                daily = {}
            try:
                cur.execute(
                    """
                    SELECT action, COUNT(*)
                      FROM audit_log
                     WHERE timestamp >= %s
                       AND action IS NOT NULL
                     GROUP BY action
                     ORDER BY COUNT(*) DESC, action ASC
                     LIMIT 8;
                    """,
                    (window_7d,),
                )
                action_types = [
                    {"action": row[0], "count": int(row[1] or 0)}
                    for row in (cur.fetchall() or [])
                ]
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
                action_types = []
            try:
                cur.execute(
                    """
                    SELECT COALESCE(NULLIF(target, ''), '') AS tgt, COUNT(*)
                      FROM audit_log
                     WHERE timestamp >= %s
                     GROUP BY tgt
                     ORDER BY COUNT(*) DESC, tgt ASC
                     LIMIT 48;
                    """,
                    (window_7d,),
                )
                top_targets = _analytics_bucketed_top_targets(cur.fetchall() or [], limit=6)
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
                top_targets = []
            try:
                cur.execute(
                    """
                    SELECT COALESCE(NULLIF(actor, ''), '(system)') AS actor_name, COUNT(*)
                      FROM audit_log
                     WHERE timestamp >= %s
                     GROUP BY actor_name
                     ORDER BY COUNT(*) DESC, actor_name ASC
                     LIMIT 6;
                    """,
                    (window_7d,),
                )
                top_actors = [
                    {"label": row[0], "count": int(row[1] or 0)}
                    for row in (cur.fetchall() or [])
                ]
            except Exception:
                _admin_recover_query_error(conn, "admin_analytics_overview")
                top_actors = []

        try:
            live_counts = {}
            if _state_live_room_counts is not None:
                live_counts = {str(name): int(count or 0) for name, count in dict(_state_live_room_counts()).items()}
            room_activity = [
                {"label": str(name), "count": int(count or 0)}
                for name, count in sorted(live_counts.items(), key=lambda kv: (-int(kv[1] or 0), str(kv[0])))[:6]
            ]
        except Exception:
            room_activity = []

        hourly_rows = []
        base_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
        for idx in range(24):
            bucket = base_hour + timedelta(hours=idx)
            key = bucket.astimezone(timezone.utc).isoformat()
            hourly_rows.append({
                "bucket": key,
                "label": bucket.strftime("%H:%M"),
                "count": int(hourly.get(key, 0) or 0),
            })

        daily_rows = []
        base_day = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
        for idx in range(7):
            bucket = base_day + timedelta(days=idx)
            key = bucket.astimezone(timezone.utc).isoformat()
            daily_rows.append({
                "bucket": key,
                "label": bucket.strftime("%a"),
                "count": int(daily.get(key, 0) or 0),
            })

        return _admin_json_response(
            {
                "ok": True,
                "summary": summary,
                "hourly_audit": hourly_rows,
                "daily_sanctions": daily_rows,
                "actions_7d": action_types,
                "top_targets_7d": top_targets,
                "top_actors_7d": top_actors,
                "top_rooms_live": room_activity,
                "generated_at": now.isoformat(),
            }
        )

    @app.route("/admin/moderation/overview")
    @require_permission("admin:audit")
    def admin_moderation_overview():
        summary = {}
        active = []
        recent = []
        conn = get_db()
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT sanction_type, COUNT(*)
                      FROM user_sanctions
                     WHERE expires_at IS NULL OR expires_at > NOW()
                     GROUP BY sanction_type
                     ORDER BY sanction_type ASC;
                    """
                )
                summary = {str(row[0]): int(row[1] or 0) for row in (cur.fetchall() or [])}
            except Exception:
                _admin_recover_query_error(conn, "admin_moderation_overview.summary")
                summary = {}
            try:
                cur.execute(
                    """
                    SELECT username, sanction_type, reason, created_at, expires_at
                      FROM user_sanctions
                     WHERE expires_at IS NULL OR expires_at > NOW()
                     ORDER BY created_at DESC
                     LIMIT 20;
                    """
                )
                active = [
                    {
                        "username": row[0],
                        "sanction_type": row[1],
                        "reason": row[2],
                        "created_at": row[3].isoformat() if row[3] else None,
                        "expires_at": row[4].isoformat() if row[4] else None,
                    }
                    for row in (cur.fetchall() or [])
                ]
            except Exception:
                _admin_recover_query_error(conn, "admin_moderation_overview.active")
                active = []
            try:
                cur.execute(
                    """
                    SELECT actor, action, target, timestamp, details
                      FROM audit_log
                     WHERE action IN ('mute_user','suspend_user','shadowban_user','ban_from_room','kick_from_room','assign_role','force_logout','delete_user')
                        OR action ILIKE 'incident_mode_%'
                     ORDER BY timestamp DESC
                     LIMIT 25;
                    """
                )
                recent = [
                    _admin_safe_audit_event(row[0], row[1], row[2], row[3], row[4])
                    for row in (cur.fetchall() or [])
                ]
            except Exception:
                _admin_recover_query_error(conn, "admin_moderation_overview.recent")
                recent = []

        suggestions = []
        incident = _incident_snapshot()
        if incident.get("enabled"):
            suggestions.append("Incident mode is active. Review active sanctions and room slowmode values before relaxing controls.")
        if not summary.get("mute") and not summary.get("suspend"):
            suggestions.append("No active mute or suspend sanctions right now. This is a good time to review policy defaults and thresholds.")
        if bool(settings.get("allow_user_create_rooms", True)):
            suggestions.append("Users can still create rooms. Consider disabling that during spikes or abuse waves.")
        return _admin_json_response({"ok": True, "summary": summary, "active_sanctions": active, "recent_actions": recent, "incident": incident, "suggestions": suggestions})

    @app.route("/admin/incident_mode")
    @require_permission("admin:basic")
    def admin_incident_mode():
        return _admin_json_response({"ok": True, "incident": _incident_snapshot()})

    @app.route("/admin/incident_mode/presets")
    @require_permission("admin:basic")
    def admin_incident_presets():
        return _admin_json_response({"ok": True, "presets": _INCIDENT_PRESETS, "active": _incident_snapshot()})

    @app.route("/admin/incident_mode/apply", methods=["POST"])
    @require_permission("admin:settings")
    @require_recent_admin_auth
    def admin_incident_mode_apply():
        actor_name = _actor()
        payload = request.get_json(silent=True) if request.is_json else {}
        mode = re.sub(r'[^a-z0-9_:-]', '', str((payload or {}).get("mode") if request.is_json else request.form.get("mode") or "").strip().lower())[:32]
        persist_raw = request.form.get("persist") if not request.is_json else (payload or {}).get("persist")
        persist = str(persist_raw or "0").lower() in {"1", "true", "yes", "on"}
        try:
            snapshot = _apply_incident_mode_patch(mode, actor_name, persist=persist)
        except ValueError as exc:
            return _admin_json_response({"ok": False, "error": str(exc)}, 400)
        return _admin_json_response({"ok": True, "incident": snapshot})

    @app.route("/admin/incident_mode/disable", methods=["POST"])
    @require_permission("admin:settings")
    @require_recent_admin_auth
    def admin_incident_mode_disable():
        actor_name = _actor()
        payload = request.get_json(silent=True) if request.is_json else {}
        persist_raw = request.form.get("persist") if not request.is_json else (payload or {}).get("persist")
        persist = str(persist_raw or "0").lower() in {"1", "true", "yes", "on"}
        snapshot = _disable_incident_mode(actor_name, persist=persist)
        return _admin_json_response({"ok": True, "incident": snapshot})

    @app.route("/admin/create_user", methods=["POST"])
    @require_permission("admin:create_user")
    @require_recent_admin_auth
    def admin_create_user():
        """Create a user (with RSA keys) from the admin panel."""
        actor = _actor()
        username = normalize_registration_username(request.form.get("username") or "")
        password = request.form.get("password", "")
        email, email_err = _admin_normalize_account_email(request.form.get("email") or "")
        recovery_pin = (request.form.get("recovery_pin") or "").strip()
        is_admin_flag = (request.form.get("is_admin") or "0").strip() in {"1", "true", "yes", "on"}
        if is_admin_flag and not _actor_has_permission("admin:manage_roles"):
            return _admin_json_response({
                "ok": False,
                "error": "creating_admin_requires_role_manager",
                "required": "admin:manage_roles",
            }, 403)

        ok_username, username_err, _blocked_term = validate_registration_username(username, settings=settings)
        if ok_username:
            ok_username, username_style_err = validate_account_username_style(username)
            username_err = username_style_err
        if not ok_username:
            return _admin_json_response({"ok": False, "error": username_err or "Invalid username"}, 400)
        ok_password, password_err = validate_account_password(
            password,
            username=username,
            email=email,
            server_name=settings.get("server_name"),
        )
        if not ok_password:
            return _admin_json_response({"ok": False, "error": password_err or "Password does not meet account rules"}, 400)
        if email_err:
            return _admin_json_response({"ok": False, "error": email_err}, 400)
        ok_pin, pin_err = validate_recovery_pin(recovery_pin)
        if not ok_pin:
            return _admin_json_response({"ok": False, "error": pin_err or recovery_pin_policy_summary()}, 400)

        conn = get_db()
        if user_exists(conn, username):
            return _admin_json_response({"ok": False, "error": "User already exists"}, 409)
        if email and email_in_use(conn, email, settings=settings):
            return _admin_json_response({"ok": False, "error": "Email already in use"}, 409)

        try:
            create_user_with_keys(
                conn,
                username=username,
                raw_password=password,
                password_hash=hash_password(password),
                email=email,
                is_admin=bool(is_admin_flag),
                recovery_pin_hash=hash_password(recovery_pin),
                recovery_pin_set_at=datetime.now(timezone.utc),
                commit=False,
            )

            # Ensure RBAC role assignment. Admin users get the seeded 'admin' role; others get 'viewer'.
            role_name = "admin" if is_admin_flag else "viewer"
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s);", (username,))
                user_row = cur.fetchone()
                cur.execute("SELECT id FROM roles WHERE name = %s;", (role_name,))
                role_row = cur.fetchone()
                if not user_row or not role_row:
                    raise RuntimeError("Admin-created account role assignment failed")
                cur.execute(
                    """
                    INSERT INTO user_roles (user_id, role_id)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id, role_id) DO NOTHING;
                    """,
                    (user_row[0], role_row[0]),
                )
            conn.commit()

            log_audit_event(actor, "create_user", username, f"role={role_name}")
            return _admin_json_response({"ok": True, "status": "created", "user": username, "role": role_name})
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            msg = str(e)
            low = msg.lower()
            if "unique" in low or "duplicate" in low:
                if "users_email_unique_ci" in low or "lower(email" in low or "email" in low:
                    return _admin_json_response({"ok": False, "error": "Email already in use"}, 409)
                return _admin_json_response({"ok": False, "error": "User already exists"}, 409)
            return _admin_operation_error("create_user", e)

    
    @app.route("/admin/set_recovery_pin", methods=["POST"])
    @require_permission("admin:set_recovery_pin")
    @require_recent_admin_auth
    def admin_set_recovery_pin():
        """Admin: set/reset a user's 4-to-8 digit Recovery PIN."""
        actor = _actor()
        username = (request.form.get("username") or "").strip()
        pin = (request.form.get("recovery_pin") or "").strip()

        if _is_self_target(username):
            return _deny_self_target("set the recovery PIN for")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "set the recovery PIN for")
        if denied is not None:
            return denied
        ok_pin, pin_err = validate_recovery_pin(pin)
        if not ok_pin:
            return _admin_json_response({"ok": False, "error": pin_err or recovery_pin_policy_summary()}, 400)

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                       SET recovery_pin_hash = %s,
                           recovery_pin_set_at = CURRENT_TIMESTAMP,
                           recovery_failed_attempts = 0,
                           recovery_locked_until = NULL
                     WHERE LOWER(username) = LOWER(%s);
                    """,
                    (hash_password(pin), username),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    return _admin_json_response({"ok": False, "error": "User not found"}, 404)
            conn.commit()
            log_audit_event(actor, "set_recovery_pin", username, "admin_reset")
            return _admin_json_response({"ok": True, "status": "ok", "user": username})
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return _admin_operation_error("admin_write", e)

# ── User lifecycle ──────────────────────────────────────────────
    @app.route("/admin/delete_user/<username>", methods=["POST"])
    @require_permission("admin:delete_user")
    @require_recent_admin_auth
    def delete_user(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("delete")
        username, user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "delete")
        if denied is not None:
            return denied

        # Delete must first invalidate sessions/tokens. Otherwise a stale refresh
        # token can keep trying to resurrect a now-deleted account until its cookie
        # naturally expires.
        try:
            revocation = revoke_all_sessions_and_tokens_for_user(username, reason="admin_delete_user")
        except Exception as exc:
            return _admin_operation_error("delete_user_revoke", exc, status=500, ok_style=True)

        conn = get_db()
        owned_custom_rooms: list[str] = []
        try:
            with conn.cursor() as cur:
                # Messages / DMs
                cur.execute(
                    "DELETE FROM messages WHERE LOWER(sender) = LOWER(%s) OR LOWER(receiver) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM offline_messages WHERE LOWER(sender) = LOWER(%s) OR LOWER(receiver) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM pending_messages WHERE LOWER(sender_username) = LOWER(%s) OR LOWER(receiver_username) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM private_messages WHERE LOWER(sender) = LOWER(%s) OR LOWER(recipient) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM dm_files WHERE LOWER(sender) = LOWER(%s) OR LOWER(receiver) = LOWER(%s);",
                    (username, username),
                )
                cur.execute("DELETE FROM message_reactions WHERE LOWER(username) = LOWER(%s);", (username,))
                cur.execute("DELETE FROM message_reads WHERE LOWER(username) = LOWER(%s);", (username,))

                # Social graph
                cur.execute(
                    "DELETE FROM friends WHERE user_id = %s OR friend_id = %s;",
                    (user_id, user_id),
                )
                cur.execute(
                    "DELETE FROM friend_requests WHERE LOWER(from_user) = LOWER(%s) OR LOWER(to_user) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM blocks WHERE LOWER(blocker) = LOWER(%s) OR LOWER(blocked) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM blocked_users WHERE user_id = %s OR blocked_id = %s;",
                    (user_id, user_id),
                )

                # Groups
                cur.execute("DELETE FROM group_members WHERE user_id = %s;", (user_id,))
                cur.execute(
                    "DELETE FROM group_invites WHERE LOWER(from_user) = LOWER(%s) OR LOWER(to_user) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM group_mutes WHERE LOWER(username) = LOWER(%s);",
                    (username,),
                )
                cur.execute("DELETE FROM group_files WHERE LOWER(sender) = LOWER(%s);", (username,))

                # Room invites / custom room access.  These tables store user names
                # as text, not user_id FKs, so account deletion must clear both
                # invites sent by the account and grants held by the account.
                # Owned custom rooms are deleted with their persisted room state;
                # otherwise a later re-created account with the same username could
                # inherit creator/owner powers through custom_rooms.created_by.
                cur.execute(
                    "SELECT name FROM custom_rooms WHERE LOWER(created_by) = LOWER(%s);",
                    (username,),
                )
                owned_custom_rooms = [str(row[0]) for row in (cur.fetchall() or []) if row and row[0]]
                if owned_custom_rooms:
                    delete_custom_room_persisted_state(cur, owned_custom_rooms)
                    cur.execute("DELETE FROM custom_rooms WHERE name = ANY(%s);", (owned_custom_rooms,))
                    cur.execute("DELETE FROM chat_rooms WHERE name = ANY(%s) AND room_kind='custom';", (owned_custom_rooms,))
                cur.execute(
                    "DELETE FROM room_invites WHERE LOWER(invited_user) = LOWER(%s) OR LOWER(invited_by) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM custom_room_invites WHERE LOWER(invited_user) = LOWER(%s) OR LOWER(invited_by) = LOWER(%s);",
                    (username, username),
                )
                cur.execute(
                    "DELETE FROM custom_room_members WHERE LOWER(member_user) = LOWER(%s) OR LOWER(invited_by) = LOWER(%s);",
                    (username, username),
                )

                # Profile/social surface owned by username text.
                cur.execute("DELETE FROM profile_post_reports WHERE LOWER(reporter_username) = LOWER(%s) OR LOWER(target_username) = LOWER(%s);", (username, username))
                cur.execute("DELETE FROM profile_post_comments WHERE LOWER(author_username) = LOWER(%s);", (username,))
                cur.execute("DELETE FROM profile_post_reactions WHERE LOWER(username) = LOWER(%s);", (username,))
                cur.execute("DELETE FROM profile_posts WHERE LOWER(author_username) = LOWER(%s);", (username,))
                cur.execute("DELETE FROM user_profile_badges WHERE LOWER(username) = LOWER(%s);", (username,))
                cur.execute("DELETE FROM user_profile_notification_settings WHERE LOWER(username) = LOWER(%s);", (username,))
                cur.execute("DELETE FROM user_recent_rooms WHERE LOWER(username) = LOWER(%s);", (username,))

                # Moderation
                cur.execute("DELETE FROM user_sanctions WHERE LOWER(username) = LOWER(%s);", (username,))

                # Settings/notifications
                cur.execute("DELETE FROM chat_settings WHERE user_id = %s;", (user_id,))
                cur.execute("DELETE FROM notifications WHERE user_id = %s;", (user_id,))
                cur.execute("DELETE FROM user_quotas WHERE LOWER(username) = LOWER(%s);", (username,))
                cur.execute("DELETE FROM password_reset_tokens WHERE LOWER(username) = LOWER(%s);", (username,))

                # Auth rows have already been revoked above.  Remove them too so a
                # deleted account leaves no active-session/token or auth-history rows
                # keyed to a now-nonexistent username.
                cur.execute("DELETE FROM auth_tokens WHERE LOWER(username) = LOWER(%s);", (username,))
                cur.execute("DELETE FROM auth_sessions WHERE LOWER(username) = LOWER(%s);", (username,))

                # RBAC (user_roles is FK'd to users; explicit delete is fine)
                cur.execute("DELETE FROM user_roles WHERE user_id = %s;", (user_id,))

                # Finally user
                cur.execute("DELETE FROM users WHERE id = %s;", (user_id,))

            conn.commit()
            forced_room_sessions = 0
            if owned_custom_rooms and socketio is not None and _state_connected_room_targets is not None:
                for deleted_room in owned_custom_rooms:
                    try:
                        targets = list(_state_connected_room_targets(deleted_room))
                    except Exception:
                        targets = []
                    for sid, _uname in targets:
                        try:
                            socketio.emit(
                                "room_forced_leave",
                                {"room": deleted_room, "reason": "Room owner account deleted"},
                                to=sid,
                            )
                        except Exception:
                            pass
                        try:
                            socketio.server.leave_room(sid, deleted_room)
                        except Exception:
                            pass
                        if _state_update_connected_room is not None:
                            try:
                                _state_update_connected_room(sid, None)
                            except Exception:
                                pass
                        forced_room_sessions += 1
                    try:
                        socketio.emit("rooms_changed", {"deleted": deleted_room, "reason": "owner_account_deleted", "by": actor})
                    except Exception:
                        pass
            disconnected = _disconnect_user(username)
            log_audit_event(actor, "delete_user", username, "Full account deleted")
            return _admin_json_response({
                "ok": True,
                "status": "deleted",
                "user": username,
                "revoked_sessions": int(revocation.get("revoked_sessions", 0)),
                "revoked_tokens": int(revocation.get("revoked_tokens", 0)),
                "disconnected_sessions": disconnected,
                "deleted_owned_custom_rooms": owned_custom_rooms,
                "forced_room_sessions": int(forced_room_sessions or 0),
            })
        except Exception as e:
            conn.rollback()
            return _admin_operation_error("admin_write", e)

    @app.route("/admin/suspend_user/<username>", methods=["POST"])
    @require_permission("moderation:suspend_user")
    @require_recent_admin_auth
    def suspend_user(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("suspend")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "suspend")
        if denied is not None:
            return denied
        minutes, err = _bounded_int_from_form("minutes", 60, 1, 60 * 24 * 365)
        if err is not None:
            return err
        reason = request.form.get("reason", "Suspended by admin")

        try:
            add_sanction(username, "ban", reason, minutes, actor=actor)
            revoked_sessions = _revoke_and_disconnect_user_sessions(
                username,
                reason="Your account was suspended by an admin.",
                actor=actor,
                action="account_suspended",
                revoke_reason="account_suspended",
            )
            log_audit_event(actor, "suspend_user", username, f"{minutes} min suspension")
            return _admin_json_response({"ok": True, "status": "suspended", "effective_status": get_effective_account_status(username), "user": username, "duration": minutes, "revoked_sessions": revoked_sessions})
        except Exception as e:
            return _admin_operation_error("admin_write", e)

    @app.route("/admin/deactivate_user/<username>", methods=["POST"])
    @require_permission("moderation:suspend_user")
    @require_recent_admin_auth
    def deactivate_user(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("deactivate")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "deactivate")
        if denied is not None:
            return denied
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET status = 'deactivated', online = FALSE WHERE LOWER(username) = LOWER(%s);",
                    (username,),
                )
            conn.commit()
            revoked_sessions = _revoke_and_disconnect_user_sessions(
                username,
                reason="Your account was deactivated by an admin.",
                actor=actor,
                action="account_deactivated",
                revoke_reason="account_deactivated",
            )
            log_audit_event(actor, "deactivate_user", username, "Soft deactivation")
            return _admin_json_response({"ok": True, "status": "deactivated", "effective_status": get_effective_account_status(username), "user": username, "revoked_sessions": revoked_sessions})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error("admin_write", e)

    @app.route("/admin/force_logout/<username>", methods=["POST"])
    @require_permission("admin:logout_user")
    @require_recent_admin_auth
    def force_logout(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("force logout")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "force logout")
        if denied is not None:
            return denied
        reason = (request.form.get("reason") or "Logged out by an admin").strip()
        log_audit_event(actor, "force_logout", username, reason)

        # Revoke first. Force logout must fail closed: if DB revocation fails, do
        # not claim success or merely disconnect a browser that can reconnect with
        # still-valid refresh tokens.
        try:
            revocation = revoke_all_sessions_and_tokens_for_user(username, reason="admin_force_logout")
        except Exception as exc:
            return _admin_operation_error("force_logout_revoke", exc, status=500, ok_style=True)

        # Tell the client WHY, so it can show the login screen message. Then hard
        # disconnect live Socket.IO sessions so stale tabs are removed promptly.
        payload = {
            "username": username,
            "reason": reason,
            "by": actor,
            "action": "force_logout",
            "code": "admin_force_logout",
        }
        emitted_sessions = 0
        try:
            if socketio is not None:
                for sid in _user_sids(username):
                    try:
                        socketio.emit("force_logout", payload, to=sid)
                        # Back-compat for older clients
                        socketio.emit("admin_force_logout", payload, to=sid)
                        emitted_sessions += 1
                    except Exception:
                        pass
        except Exception:
            pass

        disconnected = _disconnect_user(username)

        return _admin_json_response(
            {
                "ok": True,
                "status": "logout_requested",
                "user": username,
                "reason": reason,
                "revoked_sessions": int(revocation.get("revoked_sessions", 0)),
                "revoked_tokens": int(revocation.get("revoked_tokens", 0)),
                "emitted_sessions": emitted_sessions,
                "disconnected_sessions": disconnected,
                "tokens_revoked": True,
            }
        )


    @app.route("/admin/ban_ip", methods=["POST"])
    @require_permission("admin:ban_ip")
    @require_recent_admin_auth
    def ban_ip():
        actor = _actor()
        ip, ip_error = _normalized_ip_or_error(request.form.get("ip"))
        if ip_error is not None:
            return ip_error
        current_ip = get_request_ip()
        if current_ip and ip == current_ip:
            return _admin_json_response({"ok": False, "error": "self_ip_ban_forbidden", "message": "Cannot ban your current admin IP from the admin panel."}, 403)
        reason = _admin_reason(request.form.get("reason"), "Manual IP ban")

        conn = get_db()
        try:
            add_ip_sanction(ip, reason, actor=actor)
            revocation = _revoke_sessions_for_ip(ip, actor)
            conn.commit()
            disconnected_sockets = 0
            try:
                if socketio is not None:
                    payload = {
                        "reason": "Your session was closed because its IP address was banned.",
                        "by": actor,
                        "action": "ip_banned",
                        "code": "ip_banned",
                    }
                    for uname in list((revocation or {}).get("affected_users") or []):
                        for sid in _user_sids(uname):
                            try:
                                socketio.emit("force_logout", dict(payload, username=uname), to=sid)
                                socketio.emit("admin_force_logout", dict(payload, username=uname), to=sid)
                            except Exception:
                                pass
                            try:
                                socketio.server.disconnect(sid)
                                disconnected_sockets += 1
                            except Exception:
                                pass
            except Exception:
                pass
            log_audit_event(actor, "ban_ip", ip, reason)
            return _admin_json_response({
                "ok": True,
                "status": "ip_banned",
                "ip": ip,
                "revoked_sessions": int((revocation or {}).get("revoked_sessions") or 0),
                "revoked_tokens": int((revocation or {}).get("revoked_tokens") or 0),
                "affected_users": list((revocation or {}).get("affected_users") or []),
                "disconnected_sockets": disconnected_sockets,
            })
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return _admin_operation_error("ban_ip", e, ok_style=True)

    @app.route("/admin/unban_ip", methods=["POST"])
    @require_permission("admin:ban_ip")
    @require_recent_admin_auth
    def unban_ip():
        actor = _actor()
        ip, ip_error = _normalized_ip_or_error(request.form.get("ip"))
        if ip_error is not None:
            return ip_error
        reason = _admin_reason(request.form.get("reason"), "IP ban cleared by admin")
        try:
            cleared = expire_ip_sanctions(ip, actor=actor, reason=reason)
        except Exception as exc:
            return _admin_operation_error("unban_ip", exc, ok_style=True)
        log_audit_event(actor, "unban_ip", ip, f"cleared={cleared}; reason={reason}")
        return _admin_json_response({
            "ok": True,
            "status": "ip_unbanned",
            "ip": ip,
            "cleared": int(cleared or 0),
        })

    @app.route("/admin/reset_password/<username>", methods=["POST"])
    @require_permission("admin:reset_password")
    @require_recent_admin_auth
    def admin_reset_password(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("reset the password for")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "reset the password for")
        if denied is not None:
            return denied
        new_pw = request.form.get("new_password", "")
        if not new_pw:
            return _admin_json_response({"ok": False, "error": "Missing password"}, 400)
        target_email = None
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT email, email_encrypted FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1;", (username,))
                email_row = cur.fetchone()
            if email_row:
                target_email = display_email(email_row[0], email_row[1], settings) or None
        except Exception:
            logging.warning("Could not load target email for admin reset password policy context: %s", username, exc_info=True)
        ok_password, password_err = validate_account_password(
            new_pw,
            username=username,
            email=target_email,
            server_name=settings.get("server_name"),
        )
        if not ok_password:
            return _admin_json_response({"ok": False, "error": password_err or "Password does not meet account rules"}, 400)

        conn = get_db()
        try:
            # Password-derived encryption means we must rotate the user's E2EE keypair
            # when an admin resets their password. Otherwise login succeeds but private-message key setup fails.
            new_public, new_enc_priv = generate_user_keypair_for_password(new_pw)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                       SET password = %s,
                           public_key = %s,
                           encrypted_private_key = %s,
                           auth_version = COALESCE(auth_version, 0) + 1,
                           password_changed_at = CURRENT_TIMESTAMP,
                           auth_changed_at = CURRENT_TIMESTAMP
                     WHERE LOWER(username) = LOWER(%s);
                    """,
                    (hash_password(new_pw), new_public, new_enc_priv, username),
                )
                cur.execute(
                    "UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP WHERE LOWER(username) = LOWER(%s) AND used_at IS NULL;",
                    (username,),
                )

                cur.execute(
                    """
                    UPDATE auth_sessions
                       SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                           revoked_reason = COALESCE(revoked_reason, 'admin_password_reset')
                     WHERE LOWER(username)=LOWER(%s) AND revoked_at IS NULL;
                    """,
                    (username,),
                )
                revoked_sessions = cur.rowcount
                cur.execute(
                    "UPDATE auth_tokens SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP) WHERE LOWER(username)=LOWER(%s) AND revoked_at IS NULL;",
                    (username,),
                )
                revoked_tokens = cur.rowcount

            revocation = {"revoked_sessions": int(revoked_sessions or 0), "revoked_tokens": int(revoked_tokens or 0)}
            conn.commit()

            # Best-effort live disconnect + client-visible reason.
            if socketio is not None:
                payload = {
                    "username": username,
                    "reason": "Your password was reset by an admin. Please log in again.",
                    "by": actor,
                    "action": "password_reset",
                }
                try:
                    for sid in _user_sids(username):
                        try:
                            socketio.emit("force_logout", payload, to=sid)
                            socketio.emit("admin_force_logout", payload, to=sid)  # back-compat
                        except Exception:
                            pass
                except Exception:
                    pass

            # Drop any active Socket.IO sessions.
            try:
                _disconnect_user(username)
            except Exception:
                pass
            log_audit_event(actor, "reset_password", username, "Admin reset password")
            return _admin_json_response({"ok": True, "status": "reset", "user": username, "revoked_sessions": int(revocation.get("revoked_sessions", 0)), "revoked_tokens": int(revocation.get("revoked_tokens", 0))})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error("admin_write", e)

    @app.route("/admin/view_logins/<username>")
    @require_permission("admin:basic")
    def view_logins(username):
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT last_seen FROM users WHERE LOWER(username) = LOWER(%s);", (username,))
            row = cur.fetchone()
        return jsonify({"username": username, "last_seen": row[0] if row else None})

    # ── RBAC ────────────────────────────────────────────────────────
    @app.route("/admin/assign_role/<username>", methods=["POST"])
    @require_permission("admin:assign_role")
    @require_recent_admin_auth
    def assign_role(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("change roles for")
        username, user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        requested_role_name = (request.form.get("role") or "").strip().lower()
        role_name = _normalize_role_name(requested_role_name)
        if not role_name:
            return _admin_json_response({"ok": False, "error": "Missing role name"}, 400)
        if not _valid_role_name(role_name):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)

        target_denied = _deny_privileged_target_without_admin(username, "change roles for")
        if target_denied is not None:
            return target_denied

        core_roles = ("admin", "moderator", "viewer")

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM roles WHERE name = %s;", (role_name,))
            role = cur.fetchone()
            if not role:
                return _admin_json_response({"ok": False, "error": "Role does not exist"}, 404)
            if _role_has_privilege_escalation_permissions(cur, role_name):
                denied = _require_actor_permission("admin:manage_roles", action="assign privileged role")
                if denied is not None:
                    return denied

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM user_roles ur
                      JOIN role_permissions rp ON rp.role_id = ur.role_id
                      JOIN permissions p ON p.id = rp.permission_id
                     WHERE ur.user_id = %s
                       AND p.name IN ('admin:basic')
                );
                """,
                (user_id,),
            )
            previous_effective_admin = bool((cur.fetchone() or [False])[0])

            if role_name in core_roles:
                cur.execute(
                    """
                    DELETE FROM user_roles
                     WHERE user_id = %s
                       AND role_id IN (
                            SELECT id FROM roles WHERE name IN ('admin', 'moderator', 'viewer')
                       );
                    """,
                    (user_id,),
                )

            cur.execute(
                """
                INSERT INTO user_roles (user_id, role_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, role_id) DO NOTHING;
                """,
                (user_id, role[0]),
            )

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM user_roles ur
                      JOIN role_permissions rp ON rp.role_id = ur.role_id
                      JOIN permissions p ON p.id = rp.permission_id
                     WHERE ur.user_id = %s
                       AND p.name IN ('admin:basic')
                );
                """,
                (user_id,),
            )
            effective_admin = bool((cur.fetchone() or [False])[0])
            cur.execute("UPDATE users SET is_admin = %s WHERE id = %s;", (effective_admin, user_id))

        conn.commit()
        admin_state_changed = bool(previous_effective_admin) != bool(effective_admin)
        revoked_sessions = 0
        if admin_state_changed:
            revoked_sessions = _revoke_and_disconnect_user_sessions(
                username,
                reason="Your role changed. Please sign in again.",
                actor=actor,
                action="role_changed",
                revoke_reason="role_changed",
            )
        log_audit_event(actor, "assign_role", username, f"Role: {role_name} (requested: {requested_role_name or role_name})")
        return _admin_json_response({
            "ok": True,
            "status": "role_assigned",
            "role": role_name,
            "requested_role": requested_role_name or role_name,
            "effective_is_admin": effective_admin,
            "previous_effective_is_admin": previous_effective_admin,
            "admin_state_changed": admin_state_changed,
            "revoked_sessions": int(revoked_sessions or 0),
            "replaced_core_role": role_name in core_roles,
        })

    # ── Sanctions by type ───────────────────────────────────────────
    @app.route("/admin/mute_user/<username>", methods=["POST"])
    @require_permission("moderation:mute_user")
    @require_recent_admin_auth
    def mute_user_admin(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("mute")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "mute")
        if denied is not None:
            return denied
        minutes, err = _bounded_int_from_form("minutes", 30, 1, 60 * 24 * 30)
        if err is not None:
            return err
        reason = _admin_reason(request.form.get("reason"), "Muted by admin")

        try:
            expires_at = add_sanction(username, "mute", reason, minutes, actor=actor)
            log_audit_event(actor, "mute_user", username, f"{minutes} min mute")
            return _admin_json_response({"ok": True, "status": "muted", "user": username, "minutes": minutes, "expires_at": expires_at.isoformat() if expires_at else None})
        except Exception as e:
            return _admin_operation_error("mute_user", e, ok_style=True)

    @app.route("/admin/kick_from_room", methods=["POST"])
    @require_permission("moderation:kick_user")
    @require_recent_admin_auth
    def kick_from_room():
        actor = _actor()
        username = (request.form.get("username") or "").strip()
        room, room_error = _canonical_room_or_error(request.form.get("room"))
        if not username:
            return _admin_json_response({"ok": False, "error": "Missing username"}, 400)
        if room_error is not None:
            return room_error
        if _is_self_target(username):
            return _deny_self_target("kick")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "kick")
        if denied is not None:
            return denied

        log_audit_event(actor, "kick_from_room", f"{username}@{room}", "Manual kick issued")
        affected = 0
        try:
            affected = _kick_user_from_room(username, room)
        except Exception:
            affected = 0

        # Real-time UX: tell the target client(s) to close/leave the room immediately.
        try:
            if socketio:
                for sid in _user_sids(username):
                    socketio.emit("room_forced_leave", {"room": room, "reason": "kicked", "by": actor}, to=sid)
                # Room-wide heads-up for UIs
                socketio.emit("admin_kick", {"username": username, "room": room, "by": actor}, room=room)
                socketio.emit("notification", f"👢 {actor} kicked {username} from {room}", to=room)
        except Exception:
            pass

        return _admin_json_response({"ok": True, "status": "kick_requested", "user": username, "room": room, "affected_sessions": affected})


    @app.route("/admin/ban_from_room", methods=["POST"])
    @require_permission("moderation:ban_room")
    @require_recent_admin_auth
    def ban_from_room():
        actor = _actor()
        username = (request.form.get("username") or "").strip()
        room, room_error = _canonical_room_or_error(request.form.get("room"))
        reason = _admin_reason(request.form.get("reason"), "Banned from room")
        if not username:
            return _admin_json_response({"ok": False, "error": "Missing username"}, 400)
        if room_error is not None:
            return room_error
        if _is_self_target(username):
            return _deny_self_target("ban from a room")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "ban from a room")
        if denied is not None:
            return denied

        try:
            add_sanction(username, f"room_ban:{room}", reason, None, actor=actor)
        except Exception as e:
            return _admin_operation_error("ban_from_room", e, ok_style=True)

        affected = 0
        # Real-time UX: if the user is currently in the room, kick them out now.
        try:
            affected = _kick_user_from_room(username, room)
        except Exception:
            affected = 0

        try:
            if socketio:
                for sid in _user_sids(username):
                    socketio.emit("room_forced_leave", {"room": room, "reason": "banned", "by": actor}, to=sid)
                socketio.emit("notification", f"⛔ {actor} banned {username} from {room}", to=room)
        except Exception:
            pass

        log_audit_event(actor, "ban_from_room", f"{username}@{room}", reason)
        return _admin_json_response({"ok": True, "status": "room_banned", "user": username, "room": room, "affected_sessions": affected})


    @app.route("/admin/shadowban_user/<username>", methods=["POST"])
    @require_permission("moderation:shadowban")
    @require_recent_admin_auth
    def shadowban_user(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("shadowban")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "shadowban")
        if denied is not None:
            return denied
        reason = _admin_reason(request.form.get("reason"), "Shadowban issued")
        try:
            add_sanction(username, "shadowban", reason, None, actor=actor)
            log_audit_event(actor, "shadowban", username, reason)
            return _admin_json_response({"ok": True, "status": "shadowbanned", "effective_status": get_effective_account_status(username), "user": username})
        except Exception as e:
            return _admin_operation_error("shadowban_user", e, ok_style=True)



    def _clear_user_sanctions_response(username: str, sanction_type: str | None, *, action: str, reason: str = "cleared by admin"):
        """Expire active sanctions while preserving moderation history rows."""
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target(action)
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, action)
        if denied is not None:
            return denied
        try:
            cleared = expire_sanctions(username, sanction_type or "*", actor=actor, reason=reason)
        except Exception as exc:
            return _admin_operation_error(action.replace(" ", "_"), exc, ok_style=True)
        log_audit_event(actor, action.replace(" ", "_"), username, f"type={sanction_type or '*'}; cleared={cleared}; reason={reason}")
        return _admin_json_response({
            "ok": True,
            "status": "sanctions_cleared",
            "user": username,
            "sanction_type": sanction_type or "*",
            "cleared": int(cleared or 0),
            "effective_status": get_effective_account_status(username),
        })

    @app.route("/admin/unmute_user/<username>", methods=["POST"])
    @require_permission("moderation:mute_user")
    @require_recent_admin_auth
    def unmute_user_admin(username):
        reason = _admin_reason(request.form.get("reason"), "Unmuted by admin")
        return _clear_user_sanctions_response(username, "mute", action="unmute_user", reason=reason)

    @app.route("/admin/unsuspend_user/<username>", methods=["POST"])
    @require_permission("moderation:suspend_user")
    @require_recent_admin_auth
    def unsuspend_user(username):
        reason = _admin_reason(request.form.get("reason"), "Suspension cleared by admin")
        return _clear_user_sanctions_response(username, "ban", action="unsuspend_user", reason=reason)

    @app.route("/admin/unshadowban_user/<username>", methods=["POST"])
    @require_permission("moderation:shadowban")
    @require_recent_admin_auth
    def unshadowban_user(username):
        reason = _admin_reason(request.form.get("reason"), "Shadowban cleared by admin")
        return _clear_user_sanctions_response(username, "shadowban", action="unshadowban_user", reason=reason)

    @app.route("/admin/clear_user_sanctions/<username>", methods=["POST"])
    @require_permission("admin:manage_roles")
    @require_recent_admin_auth
    def clear_user_sanctions(username):
        sanction_type = (request.form.get("sanction_type") or request.form.get("type") or "*").strip() or "*"
        reason = _admin_reason(request.form.get("reason"), "All matching sanctions cleared by role manager")
        return _clear_user_sanctions_response(username, sanction_type, action="clear_user_sanctions", reason=reason)

    @app.route("/admin/unban_from_room", methods=["POST"])
    @require_permission("moderation:ban_room")
    @require_recent_admin_auth
    def unban_from_room():
        username = (request.form.get("username") or "").strip()
        room, room_error = _canonical_room_or_error(request.form.get("room"))
        if not username:
            return _admin_json_response({"ok": False, "error": "Missing username"}, 400)
        if room_error is not None:
            return room_error
        reason = _admin_reason(request.form.get("reason"), "Room ban cleared by admin")
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("unban from a room")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "unban from a room")
        if denied is not None:
            return denied
        sanction_type = f"room_ban:{room}"
        try:
            cleared = expire_sanctions(username, sanction_type, actor=actor, reason=reason)
        except Exception as exc:
            return _admin_operation_error("unban_from_room", exc, ok_style=True)
        log_audit_event(actor, "unban_from_room", f"{username}@{room}", f"cleared={cleared}; reason={reason}")
        return _admin_json_response({"ok": True, "status": "room_unbanned", "user": username, "room": room, "cleared": int(cleared or 0)})

    # ── Room controls ───────────────────────────────────────────────
    @app.route("/admin/lock_room/<room>", methods=["POST"])
    @require_permission("room:lock")
    @require_recent_admin_auth
    def lock_room(room):
        actor = _actor()
        room, room_error = _canonical_room_or_error(room)
        if room_error is not None:
            return room_error
        conn = get_db()
        with conn.cursor() as cur:
            _delete_casefold_room_policy_rows(cur, "room_locks", room)
            cur.execute(
                """
                INSERT INTO room_locks (room, locked, locked_by)
                VALUES (%s, TRUE, %s)
                ON CONFLICT (room) DO UPDATE SET locked = EXCLUDED.locked, locked_by = EXCLUDED.locked_by, locked_at = NOW();
                """,
                (room, actor),
            )
        conn.commit()
        log_audit_event(actor, "lock_room", room, "Room locked")
        try:
            _emit_room_policy(room, actor)
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "locked", "room": room})

    @app.route("/admin/unlock_room/<room>", methods=["POST"])
    @require_permission("room:lock")
    @require_recent_admin_auth
    def unlock_room(room):
        actor = _actor()
        room, room_error = _canonical_room_or_error(room)
        if room_error is not None:
            return room_error
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM room_locks WHERE LOWER(room) = LOWER(%s);", (room,))
        conn.commit()
        log_audit_event(actor, "unlock_room", room, "Room unlocked")
        try:
            _emit_room_policy(room, actor)
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "unlocked", "room": room})

    @app.route("/admin/clear_room/<room>", methods=["POST"])
    @require_permission("room:clear")
    @require_recent_admin_auth
    def clear_room(room):
        actor = _actor()
        room, room_error = _canonical_room_or_error(room)
        if room_error is not None:
            return room_error
        conn = get_db()
        deleted_messages = 0
        try:
            with conn.cursor() as cur:
                # Explicitly remove dependent rows first for legacy databases that
                # may have been created before ON DELETE CASCADE constraints existed.
                for sql in (
                    "DELETE FROM message_reactions WHERE message_id IN (SELECT id FROM messages WHERE room = %s);",
                    "DELETE FROM message_reads WHERE message_id IN (SELECT id FROM messages WHERE room = %s);",
                    "DELETE FROM file_attachments WHERE message_id IN (SELECT id FROM messages WHERE room = %s);",
                ):
                    try:
                        cur.execute(sql, (room,))
                    except Exception:
                        # Optional legacy tables should not block the room clear.
                        pass
                cur.execute("DELETE FROM messages WHERE room = %s RETURNING id;", (room,))
                deleted_messages = len(cur.fetchall() or [])
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return _admin_operation_error("clear_room", e, ok_style=True)
        log_audit_event(actor, "clear_room", room, f"deleted_messages={deleted_messages}")
        try:
            if socketio:
                socketio.emit("room_cleared", {"room": room, "by": actor, "deleted_messages": deleted_messages}, to=room)
                socketio.emit("notification", f"{actor} cleared this room", to=room)
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "cleared", "room": room, "deleted_messages": deleted_messages})

    @app.route("/admin/set_room_readonly/<room>", methods=["POST"])
    @require_permission("room:readonly")
    @require_recent_admin_auth
    def set_room_readonly(room):
        actor = _actor()
        room, room_error = _canonical_room_or_error(room)
        if room_error is not None:
            return room_error
        mode, mode_error = _admin_form_bool_or_error("readonly", True)
        if mode_error is not None:
            return mode_error
        conn = get_db()
        with conn.cursor() as cur:
            _delete_casefold_room_policy_rows(cur, "room_readonly", room)
            cur.execute(
                """
                INSERT INTO room_readonly (room, readonly, set_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (room) DO UPDATE SET readonly = EXCLUDED.readonly, set_by = EXCLUDED.set_by, set_at = NOW();
                """,
                (room, bool(mode), actor),
            )
        conn.commit()
        log_audit_event(actor, "set_readonly", room, f"Read-only: {mode}")
        try:
            _emit_room_policy(room, actor)
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "readonly_set", "room": room, "readonly": bool(mode)})


    @app.route("/admin/set_room_slowmode/<room>", methods=["POST"])
    @require_permission("room:readonly")
    @require_recent_admin_auth
    def set_room_slowmode(room):
        """Set per-room slowmode (seconds between messages per user).

        Form fields:
          - seconds: integer >= 0 (0 disables)
        """
        actor = _actor()
        room, room_error = _canonical_room_or_error(room)
        if room_error is not None:
            return room_error
        raw = request.form.get("seconds") or request.form.get("slowmode") or "0"
        try:
            seconds = int(str(raw).strip())
        except Exception:
            return _admin_json_response({"ok": False, "error": "seconds must be an integer"}, 400)
        if seconds < 0 or seconds > 3600:
            return _admin_json_response({"ok": False, "error": "seconds must be between 0 and 3600"}, 400)

        conn = get_db()
        with conn.cursor() as cur:
            _delete_casefold_room_policy_rows(cur, "room_slowmode", room)
            cur.execute(
                """
                INSERT INTO room_slowmode (room, seconds, set_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (room) DO UPDATE
                    SET seconds = EXCLUDED.seconds,
                        set_by = EXCLUDED.set_by,
                        set_at = NOW();
                """,
                (room, seconds, actor),
            )
        conn.commit()

        # Keep Socket.IO message enforcement in sync immediately. Without this, the
        # old process-local slowmode cache could keep enforcing stale seconds.
        try:
            if _state_set_room_slowmode_cache is not None:
                _state_set_room_slowmode_cache(room, seconds)
        except Exception:
            pass

        log_audit_event(actor, "set_slowmode", room, f"seconds={seconds}")

        # Best-effort push of state to the room for live UIs
        try:
            if socketio:
                socketio.emit("slowmode_state", {"room": room, "seconds": seconds, "set_by": actor}, room=room)
                if seconds:
                    socketio.emit("notification", f"{actor} set slowmode to {seconds}s", to=room)
                else:
                    socketio.emit("notification", f"{actor} disabled slowmode", to=room)
        except Exception:
            pass

        try:
            _emit_room_policy(room, actor)
        except Exception:
            pass

        return _admin_json_response({"ok": True, "status": "slowmode_set", "room": room, "seconds": seconds})

    # ── Broadcast ───────────────────────────────────────────────────
    @app.route("/admin/global_broadcast", methods=["POST"])
    @require_permission("admin:broadcast")
    @require_recent_admin_auth
    def global_broadcast():
        actor = _actor()
        message = _admin_reason(request.form.get("message"), "", max_len=1000)
        if not message:
            return _admin_json_response({"ok": False, "error": "Missing message"}, 400)
        if not socketio:
            return _admin_json_response({"ok": False, "error": "SocketIO not available"}, 500)

        delivery_estimate = _global_broadcast_delivery_estimate()
        announcement_id = f"ga-{int(_utcnow().timestamp() * 1000)}-{random.randint(1000, 9999)}"
        payload = {
            "id": announcement_id,
            "message": message,
            "actor": actor,
            "created_at": _utcnow().isoformat(),
            "delivery_estimate": delivery_estimate,
        }

        try:
            # Flask-SocketIO server-originated emits already broadcast to all clients;
            # newer python-socketio releases reject the old broadcast keyword.
            socketio.emit("global_announcement", payload)
        except Exception as e:
            return _admin_operation_error("global_broadcast", e, ok_style=True)
        log_audit_event(
            actor,
            "broadcast",
            "*",
            f"{message[:100]} | sessions={delivery_estimate['sessions']} users={delivery_estimate['users']}",
        )
        return _admin_json_response({
            "ok": True,
            "status": "broadcast_sent",
            "delivered": delivery_estimate["sessions"],
            "delivered_users": delivery_estimate["users"],
            "delivery_estimate": delivery_estimate,
            "announcement_id": announcement_id,
        })

    # ── Account flags ───────────────────────────────────────────────
    @app.route("/admin/revoke_2fa/<username>", methods=["POST"])
    @require_permission("admin:revoke_2fa")
    @require_recent_admin_auth
    def revoke_2fa(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("revoke 2FA for")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "revoke 2FA for")
        if denied is not None:
            return denied
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                       SET two_factor_secret = NULL,
                           two_factor_enabled = FALSE,
                           auth_version = COALESCE(auth_version, 0) + 1,
                           auth_changed_at = CURRENT_TIMESTAMP
                     WHERE LOWER(username) = LOWER(%s);
                    """,
                    (username,),
                )
                cur.execute(
                    """
                    UPDATE auth_sessions
                       SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                           revoked_reason = COALESCE(revoked_reason, 'admin_revoke_2fa')
                     WHERE LOWER(username)=LOWER(%s) AND revoked_at IS NULL;
                    """,
                    (username,),
                )
                revoked_sessions = cur.rowcount
                cur.execute(
                    "UPDATE auth_tokens SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP) WHERE LOWER(username)=LOWER(%s) AND revoked_at IS NULL;",
                    (username,),
                )
                revoked_tokens = cur.rowcount
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return _admin_operation_error("revoke_2fa", e, ok_style=True)

        # 2FA reset changes the trust state of the account.  Existing browser and
        # Socket.IO sessions must be forced through a fresh login instead of being
        # allowed to continue until the next HTTP token check.
        emitted_sessions = 0
        try:
            if socketio is not None:
                payload = {
                    "username": username,
                    "reason": "Your two-factor setup was reset by an admin. Please log in again.",
                    "by": actor,
                    "action": "2fa_revoked",
                    "code": "admin_revoke_2fa",
                }
                for sid in _user_sids(username):
                    try:
                        socketio.emit("force_logout", payload, to=sid)
                        socketio.emit("admin_force_logout", payload, to=sid)
                        emitted_sessions += 1
                    except Exception:
                        pass
        except Exception:
            pass
        disconnected = _disconnect_user(username)
        log_audit_event(actor, "revoke_2fa", username, "2FA revoked")
        return _admin_json_response({
            "ok": True,
            "status": "2fa_revoked",
            "user": username,
            "revoked_sessions": int(revoked_sessions or 0),
            "revoked_tokens": int(revoked_tokens or 0),
            "emitted_sessions": int(emitted_sessions or 0),
            "disconnected_sessions": int(disconnected or 0),
        })

    @app.route("/admin/set_user_quota/<username>", methods=["POST"])
    @require_permission("admin:set_user_quota")
    @require_recent_admin_auth
    def set_user_quota(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("change quota for")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "change quota for")
        if denied is not None:
            return denied
        limit, err = _bounded_int_from_form("messages_per_hour", 60, 0, 100000)
        if err is not None:
            return err
        conn = get_db()
        try:
            with conn.cursor() as cur:
                if int(limit) == 0:
                    cur.execute("DELETE FROM user_quotas WHERE LOWER(username) = LOWER(%s);", (username,))
                    status = "quota_cleared"
                else:
                    # Older builds could write duplicate quota rows that differed
                    # only by username case.  Collapse those stale rows before the
                    # exact-case primary-key upsert so the canonical row is the only
                    # row future lookups can see.
                    cur.execute(
                        "DELETE FROM user_quotas WHERE LOWER(username) = LOWER(%s) AND username <> %s;",
                        (username, username),
                    )
                    cur.execute(
                        """
                        INSERT INTO user_quotas (username, messages_per_hour)
                        VALUES (%s, %s)
                        ON CONFLICT (username) DO UPDATE SET messages_per_hour = EXCLUDED.messages_per_hour, updated_at = NOW();
                        """,
                        (username, limit),
                    )
                    status = "quota_set"
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return _admin_operation_error("set_user_quota", e, ok_style=True)
        log_audit_event(actor, "set_quota", username, f"{limit} msg/hr")
        return _admin_json_response({"ok": True, "status": status, "limit": limit, "user": username})

    @app.route("/admin/set_user_status/<username>", methods=["POST"])
    @require_permission("admin:set_user_status")
    @require_recent_admin_auth
    def set_user_status(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("override status for")
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        denied = _deny_privileged_target_without_admin(username, "override status for")
        if denied is not None:
            return denied
        raw_presence = (
            request.form.get("presence_status")
            or request.form.get("presence")
            or request.form.get("status")
            or "online"
        ).strip().lower()
        presence_aliases = {"available": "online", "default": "online", "dnd": "busy", "do_not_disturb": "busy", "offline": "invisible"}
        presence = presence_aliases.get(raw_presence, raw_presence)
        if presence not in {"online", "away", "busy", "invisible"}:
            return _admin_json_response({"ok": False, "error": "Invalid presence_status"}, 400)

        custom_status_raw = request.form.get("custom_status")
        if custom_status_raw is None:
            custom_status_raw = request.form.get("customStatus")
        if custom_status_raw is None:
            custom_status_raw = request.form.get("custom")
        custom_status = _admin_reason(custom_status_raw, "", max_len=128) if custom_status_raw is not None else ""
        if len(custom_status) > 128:
            return _admin_json_response({"ok": False, "error": "Status too long"}, 400)
        custom_value = custom_status or None

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET presence_status = %s, custom_status = %s WHERE LOWER(username) = LOWER(%s);",
                (presence, custom_value, username),
            )
        conn.commit()
        log_audit_event(actor, "override_status", username, f"presence={presence}; custom={custom_status}")
        try:
            if socketio:
                for sid in _user_sids(username):
                    socketio.emit("my_presence", {"presence": presence, "custom_status": custom_status}, to=sid)
                try:
                    from database import get_friends_for_user
                    visible_online = bool(_user_sids(username)) and presence != "invisible"
                    friend_payload = {
                        "username": username,
                        "online": bool(visible_online),
                        "presence": presence if visible_online else "offline",
                        "custom_status": custom_value if visible_online else None,
                    }
                    for friend in get_friends_for_user(username) or []:
                        for sid in _user_sids(friend):
                            socketio.emit("friend_presence_update", friend_payload, to=sid)
                except Exception:
                    pass
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "status_set", "presence_status": presence, "custom_status": custom_status, "user": username})

    # ── Role/permission management ──────────────────────────────────
    @app.route("/admin/role/create", methods=["POST"])
    @require_permission("admin:manage_roles")
    @require_recent_admin_auth
    def create_role():
        name = _normalize_role_name((request.form.get("name") or "").strip().lower())
        if not name:
            return _admin_json_response({"ok": False, "error": "Missing role name"}, 400)
        if not _valid_role_name(name):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)
        if name in _PROTECTED_ROLES:
            return _admin_json_response({"ok": False, "error": "Protected role already exists"}, 403)

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO roles (name) VALUES (%s) ON CONFLICT (name) DO NOTHING;", (name,))
            created = cur.rowcount
        conn.commit()
        if not created:
            return _admin_json_response({"ok": False, "error": "Role already exists"}, 409)
        try:
            log_audit_event(_actor(), "create_role", name, "")
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "role_created", "name": name})

    @app.route("/admin/role/delete", methods=["POST"])
    @require_permission("admin:manage_roles")
    @require_recent_admin_auth
    def delete_role():
        name = _normalize_role_name((request.form.get("name") or "").strip().lower())
        if not name:
            return _admin_json_response({"ok": False, "error": "Missing role name"}, 400)
        if not _valid_role_name(name):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)
        if name in _PROTECTED_ROLES:
            return _admin_json_response({"ok": False, "error": "Protected role cannot be deleted"}, 403)

        conn = get_db()
        with conn.cursor() as cur:
            if _role_has_privilege_escalation_permissions(cur, name):
                denied = _require_actor_permission("admin:manage_roles", action="delete privileged role")
                if denied is not None:
                    return denied
            cur.execute("DELETE FROM roles WHERE name = %s;", (name,))
            deleted = cur.rowcount
        conn.commit()
        if not deleted:
            return _admin_json_response({"ok": False, "error": "Role not found"}, 404)
        try:
            log_audit_event(_actor(), "delete_role", name, "")
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "role_deleted", "name": name})

    @app.route("/admin/role/add_permission", methods=["POST"])
    @require_permission("admin:manage_roles")
    @require_recent_admin_auth
    def add_permission_to_role():
        role = _normalize_role_name((request.form.get("role") or "").strip().lower())
        perm = (request.form.get("permission") or "").strip().lower()
        if not role or not perm:
            return _admin_json_response({"ok": False, "error": "Missing role or permission"}, 400)
        if not _valid_role_name(role):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)
        if not _valid_permission_name(perm):
            return _admin_json_response({"ok": False, "error": "Invalid permission name"}, 400)
        if _protected_role_change_requires_admin(role, perm):
            denied = _require_actor_permission("admin:manage_roles", action="add protected permission")
            if denied is not None:
                return denied

        conn = get_db()
        with conn.cursor() as cur:
            # Ensure permission exists
            cur.execute("INSERT INTO permissions (name) VALUES (%s) ON CONFLICT (name) DO NOTHING;", (perm,))
            cur.execute("SELECT id FROM permissions WHERE name = %s;", (perm,))
            perm_id = cur.fetchone()[0]

            cur.execute("SELECT id FROM roles WHERE name = %s;", (role,))
            role_id_row = cur.fetchone()
            if not role_id_row:
                return _admin_json_response({"ok": False, "error": "Role not found"}, 404)
            role_id = role_id_row[0]

            cur.execute(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (%s, %s)
                ON CONFLICT (role_id, permission_id) DO NOTHING;
                """,
                (role_id, perm_id),
            )
        conn.commit()
        try:
            log_audit_event(_actor(), "add_role_permission", role, perm)
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "permission_added", "role": role, "permission": perm})

    @app.route("/admin/role/remove_permission", methods=["POST"])
    @require_permission("admin:manage_roles")
    @require_recent_admin_auth
    def remove_permission_from_role():
        role = _normalize_role_name((request.form.get("role") or "").strip().lower())
        perm = (request.form.get("permission") or "").strip().lower()
        if not role or not perm:
            return _admin_json_response({"ok": False, "error": "Missing role or permission"}, 400)
        if not _valid_role_name(role):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)
        if not _valid_permission_name(perm):
            return _admin_json_response({"ok": False, "error": "Invalid permission name"}, 400)
        if role == "admin" and perm in _ADMIN_ROLE_MINIMUM_PERMISSIONS:
            return _admin_json_response({"ok": False, "error": "Cannot remove critical permissions from the protected admin role"}, 403)
        if _protected_role_change_requires_admin(role, perm):
            denied = _require_actor_permission("admin:manage_roles", action="remove protected permission")
            if denied is not None:
                return denied

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM permissions WHERE name = %s;", (perm,))
            perm_id_row = cur.fetchone()
            cur.execute("SELECT id FROM roles WHERE name = %s;", (role,))
            role_id_row = cur.fetchone()
            if not perm_id_row or not role_id_row:
                return _admin_json_response({"ok": False, "error": "Role or permission not found"}, 404)
            perm_id = perm_id_row[0]
            role_id = role_id_row[0]
            cur.execute(
                "DELETE FROM role_permissions WHERE role_id = %s AND permission_id = %s;",
                (role_id, perm_id),
            )
            removed = cur.rowcount
        conn.commit()
        if not removed:
            return _admin_json_response({"ok": False, "error": "Permission mapping not found"}, 404)
        try:
            log_audit_event(_actor(), "remove_role_permission", role, perm)
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "permission_removed", "role": role, "permission": perm})

    @app.route("/admin/role/<role_name>/permissions", methods=["GET"])
    @require_permission("admin:manage_roles")
    def list_role_permissions(role_name):
        role_name = _normalize_role_name(role_name)
        if not _valid_role_name(role_name):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.name
                  FROM role_permissions rp
                  JOIN permissions p ON rp.permission_id = p.id
                  JOIN roles r ON rp.role_id = r.id
                 WHERE r.name = %s
                 ORDER BY p.name;
                """,
                (role_name,),
            )
            results = [row[0] for row in cur.fetchall()]
        return _admin_json_response({"ok": True, "role": role_name, "permissions": results})

    @app.route("/admin/user/<username>/permissions", methods=["GET"])
    @require_permission("admin:manage_roles")
    def list_user_permissions(username):
        from permissions import get_user_permissions

        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        perms = get_user_permissions(username)
        return _admin_json_response({"ok": True, "username": username, "permissions": sorted(list(perms))})

    @app.route("/admin/roles", methods=["GET"])
    @require_permission("admin:manage_roles")
    def list_roles():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.name,
                       COUNT(DISTINCT ur.user_id) AS member_count,
                       COUNT(DISTINCT rp.permission_id) AS permission_count
                  FROM roles r
             LEFT JOIN user_roles ur ON ur.role_id = r.id
             LEFT JOIN role_permissions rp ON rp.role_id = r.id
              GROUP BY r.id, r.name
              ORDER BY LOWER(r.name);
                """
            )
            rows = cur.fetchall() or []
        roles = [
            {
                "name": row[0],
                "member_count": int(row[1] or 0),
                "permission_count": int(row[2] or 0),
                "protected": str(row[0] or "").lower() in _PROTECTED_ROLES,
            }
            for row in rows
        ]
        return _admin_json_response({"ok": True, "roles": roles})

    @app.route("/admin/permissions", methods=["GET"])
    @require_permission("admin:manage_roles")
    def list_permissions():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.name, COUNT(DISTINCT rp.role_id) AS assigned_roles
                  FROM permissions p
             LEFT JOIN role_permissions rp ON rp.permission_id = p.id
              GROUP BY p.id, p.name
              ORDER BY LOWER(p.name);
                """
            )
            rows = cur.fetchall() or []
        perms = []
        for row in rows:
            meta = _permission_meta(row[0])
            meta["assigned_roles"] = int(row[1] or 0)
            perms.append(meta)
        return _admin_json_response({"ok": True, "permissions": perms})

    @app.route("/admin/role/<role_name>/members", methods=["GET"])
    @require_permission("admin:manage_roles")
    def list_role_members(role_name):
        role_name = _normalize_role_name(role_name)
        if not _valid_role_name(role_name):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.username
                  FROM user_roles ur
                  JOIN roles r ON r.id = ur.role_id
                  JOIN users u ON u.id = ur.user_id
                 WHERE r.name = %s
                 ORDER BY LOWER(u.username);
                """,
                (role_name,),
            )
            members = [row[0] for row in (cur.fetchall() or [])]
        return _admin_json_response({"ok": True, "role": role_name, "members": members})

    @app.route("/admin/user/<username>/roles", methods=["GET"])
    @require_permission("admin:manage_roles")
    def list_user_roles(username):
        username, _target_user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.name
                  FROM user_roles ur
                  JOIN roles r ON r.id = ur.role_id
                  JOIN users u ON u.id = ur.user_id
                 WHERE LOWER(u.username) = LOWER(%s)
                 ORDER BY LOWER(r.name);
                """,
                (username,),
            )
            roles = [row[0] for row in (cur.fetchall() or [])]
        return _admin_json_response({"ok": True, "username": username, "roles": roles})

    @app.route("/admin/role/clone", methods=["POST"])
    @require_permission("admin:manage_roles")
    @require_recent_admin_auth
    def clone_role():
        src = _normalize_role_name((request.form.get("source") or "").strip().lower())
        dst = _normalize_role_name((request.form.get("name") or "").strip().lower())
        if not src or not dst:
            return _admin_json_response({"ok": False, "error": "Missing source or destination role"}, 400)
        if not _valid_role_name(src) or not _valid_role_name(dst):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)
        if src == dst:
            return _admin_json_response({"ok": False, "error": "Destination role must be different"}, 400)

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM roles WHERE name = %s;", (src,))
            src_row = cur.fetchone()
            if not src_row:
                return _admin_json_response({"ok": False, "error": "Source role not found"}, 404)
            if _role_has_privilege_escalation_permissions(cur, src):
                denied = _require_actor_permission("admin:manage_roles", action="clone privileged role")
                if denied is not None:
                    return denied
            cur.execute("INSERT INTO roles (name) VALUES (%s) ON CONFLICT (name) DO NOTHING;", (dst,))
            if cur.rowcount == 0:
                return _admin_json_response({"ok": False, "error": "Role already exists"}, 409)
            cur.execute("SELECT id FROM roles WHERE name = %s;", (dst,))
            dst_row = cur.fetchone()
            cur.execute(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT %s, permission_id
                  FROM role_permissions
                 WHERE role_id = %s
                ON CONFLICT (role_id, permission_id) DO NOTHING;
                """,
                (dst_row[0], src_row[0]),
            )
        conn.commit()
        try:
            log_audit_event(_actor(), "clone_role", dst, f"source={src}")
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "role_cloned", "source": src, "name": dst})

    @app.route("/admin/user/<username>/remove_role", methods=["POST"])
    @require_permission("admin:assign_role")
    @require_recent_admin_auth
    def remove_role_from_user(username):
        actor = _actor()
        if _is_self_target(username):
            return _deny_self_target("remove roles from")
        username, user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err
        requested_role_name = (request.form.get("role") or "").strip().lower()
        role_name = _normalize_role_name(requested_role_name)
        if not role_name:
            return _admin_json_response({"ok": False, "error": "Missing role name"}, 400)
        if not _valid_role_name(role_name):
            return _admin_json_response({"ok": False, "error": "Invalid role name"}, 400)

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM user_roles ur
                      JOIN role_permissions rp ON rp.role_id = ur.role_id
                      JOIN permissions p ON p.id = rp.permission_id
                     WHERE ur.user_id = %s
                       AND p.name IN ('admin:basic')
                );
                """,
                (user_id,),
            )
            previous_effective_admin = bool((cur.fetchone() or [False])[0])
            if previous_effective_admin or _role_has_privilege_escalation_permissions(cur, role_name):
                denied = _require_actor_permission("admin:manage_roles", action="remove privileged role")
                if denied is not None:
                    return denied
            cur.execute(
                """
                DELETE FROM user_roles
                 WHERE user_id = %s
                   AND role_id IN (SELECT id FROM roles WHERE name = %s);
                """,
                (user_id, role_name),
            )
            removed = cur.rowcount
            if removed <= 0:
                return _admin_json_response({"ok": False, "error": "Role mapping not found"}, 404)
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM user_roles ur
                      JOIN role_permissions rp ON rp.role_id = ur.role_id
                      JOIN permissions p ON p.id = rp.permission_id
                     WHERE ur.user_id = %s
                       AND p.name IN ('admin:basic')
                );
                """,
                (user_id,),
            )
            effective_admin = bool((cur.fetchone() or [False])[0])
            cur.execute("UPDATE users SET is_admin = %s WHERE id = %s;", (effective_admin, user_id))
        conn.commit()
        admin_state_changed = bool(previous_effective_admin) != bool(effective_admin)
        revoked_sessions = 0
        if admin_state_changed:
            revoked_sessions = _revoke_and_disconnect_user_sessions(
                username,
                reason="Your role changed. Please sign in again.",
                actor=actor,
                action="role_removed",
                revoke_reason="role_changed",
            )
        try:
            log_audit_event(actor, "remove_role", username, f"Role: {role_name}")
        except Exception:
            pass
        return _admin_json_response({"ok": True, "status": "role_removed", "username": username, "role": role_name, "effective_is_admin": effective_admin, "revoked_sessions": int(revoked_sessions or 0)})

    @app.route("/admin/permission/explain", methods=["GET"])
    @require_permission("admin:manage_roles")
    def explain_permission():
        username = (request.args.get("username") or "").strip()
        permission = (request.args.get("permission") or "").strip().lower()
        if not username or not permission:
            return _admin_json_response({"ok": False, "error": "username and permission are required"}, 400)
        if not _valid_permission_name(permission):
            return _admin_json_response({"ok": False, "error": "Invalid permission name"}, 400)
        username, user_id, target_err = _canonical_user_or_error(username)
        if target_err is not None:
            return target_err

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.name
                  FROM user_roles ur
                  JOIN roles r ON r.id = ur.role_id
                  JOIN role_permissions rp ON rp.role_id = r.id
                  JOIN permissions p ON p.id = rp.permission_id
                 WHERE ur.user_id = %s
                   AND p.name = %s
                 ORDER BY LOWER(r.name);
                """,
                (user_id, permission),
            )
            matched_roles = [row[0] for row in (cur.fetchall() or [])]
        effective_perms = set(get_user_permissions(username))
        meta = _permission_meta(permission)
        return _admin_json_response({
            "ok": True,
            "username": username,
            "permission": permission,
            "allowed": permission in effective_perms,
            "matched_roles": matched_roles,
            "effective_permissions": sorted(list(effective_perms)),
            "meta": meta,
            "explanation": f"{username} {'has' if permission in effective_perms else 'does not have'} {permission}.",
        })


    def _admin_testlab_result(name: str, ok: bool, *, details=None, category: str = "general") -> dict:
        return {
            "name": str(name or "").strip(),
            "ok": bool(ok),
            "category": str(category or "general"),
            "details": details,
        }

    def _admin_testlab_clear_realtime_rate_limits() -> dict:
        """Clear synthetic Test Lab realtime limiter buckets after mutating runs.

        The Test Lab creates many local Socket.IO clients quickly.  Those
        synthetic connects share the same loopback/LAN IP bucket as real browser
        tabs, so clearing the in-process Socket.IO buckets prevents the chat UI
        from getting stuck on "not connected / reconnecting" after tests.
        """
        try:
            removed = simple_rate_limit_clear(prefixes=("socketio:connect:", "socketio:event:"))
        except Exception:
            removed = 0
        return {"ok": True, "removed_buckets": int(removed or 0)}

    def _admin_testlab_set_cookie(client, key: str, value: str, *, domain: str = "localhost") -> None:
        try:
            client.set_cookie(key=key, value=value, domain=domain)
            return
        except TypeError:
            pass
        try:
            client.set_cookie(domain, key, value)
        except Exception:
            pass

    def _admin_testlab_get_cookie(client, key: str):
        try:
            cookie = client.get_cookie(key)
            return getattr(cookie, "value", None) if cookie else None
        except Exception:
            return None

    def _admin_testlab_forward_request_cookies(client) -> None:
        for ck, val in (request.cookies or {}).items():
            try:
                _admin_testlab_set_cookie(client, ck, val)
            except Exception:
                continue

    def _admin_testlab_base_url() -> str:
        try:
            base = str(request.host_url or "").strip()
        except Exception:
            base = ""
        return base or "http://localhost/"

    def _admin_testlab_get(client, path: str, **kwargs):
        kwargs.setdefault("follow_redirects", False)
        kwargs.setdefault("base_url", _admin_testlab_base_url())
        return client.get(path, **kwargs)

    def _admin_testlab_post(client, path: str, **kwargs):
        kwargs.setdefault("follow_redirects", False)
        kwargs.setdefault("base_url", _admin_testlab_base_url())
        return client.post(path, **kwargs)

    def _admin_testlab_delete(client, path: str, **kwargs):
        kwargs.setdefault("follow_redirects", False)
        kwargs.setdefault("base_url", _admin_testlab_base_url())
        return client.delete(path, **kwargs)

    def _admin_testlab_patch(client, path: str, **kwargs):
        kwargs.setdefault("follow_redirects", False)
        kwargs.setdefault("base_url", _admin_testlab_base_url())
        return client.patch(path, **kwargs)

    def _admin_testlab_auth_headers(client, path: str = "/") -> dict:
        csrf = _admin_testlab_get_cookie(client, "csrf_access_token") or _admin_testlab_get_cookie(client, "csrf_refresh_token") or ""
        base = _admin_testlab_base_url().rstrip("/")
        rel = str(path or "/")
        if not rel.startswith("/"):
            rel = "/" + rel
        headers = {
            "Origin": base,
            "Referer": f"{base}{rel}",
            "Sec-Fetch-Site": "same-origin",
            "Accept": "application/json",
        }
        if csrf:
            headers["X-CSRF-TOKEN"] = csrf
        return headers

    def _admin_testlab_create_auth_context(username: str) -> dict:
        from flask_jwt_extended import create_access_token, create_refresh_token
        from flask_jwt_extended.utils import decode_token
        from flask_jwt_extended import set_access_cookies, set_refresh_cookies
        from routes_auth import create_auth_session, store_auth_token

        ua = "EchoChatAdminTestLab/1.0"
        ip = "127.0.0.1"
        sid = create_auth_session(username=username, user_agent=ua, ip_address=ip)
        access_token = create_access_token(identity=username, additional_claims={"sid": sid})
        refresh_token = create_refresh_token(identity=username, additional_claims={"sid": sid})
        try:
            access_decoded = decode_token(access_token, allow_expired=False)
            refresh_decoded = decode_token(refresh_token, allow_expired=False)
            from datetime import datetime, timezone as _timezone
            aexp = access_decoded.get("exp")
            rexp = refresh_decoded.get("exp")
            store_auth_token(
                jti=access_decoded.get("jti"),
                username=username,
                token_type="access",
                expires_at=(datetime.fromtimestamp(aexp, tz=_timezone.utc) if isinstance(aexp, (int, float)) else None),
                session_id=sid,
                user_agent=ua,
                ip_address=ip,
            )
            store_auth_token(
                jti=refresh_decoded.get("jti"),
                username=username,
                token_type="refresh",
                expires_at=(datetime.fromtimestamp(rexp, tz=_timezone.utc) if isinstance(rexp, (int, float)) else None),
                session_id=sid,
                user_agent=ua,
                ip_address=ip,
            )
        except Exception as exc:
            try:
                revoke_auth_session(sid, reason="admin_testlab_auth_setup_failed")
            except Exception:
                pass
            raise RuntimeError("Admin Test Lab could not persist auth tokens; refusing to issue unusable cookies.") from exc

        resp = make_response("")
        set_access_cookies(resp, access_token)
        set_refresh_cookies(resp, refresh_token)
        cookie_headers = resp.headers.getlist("Set-Cookie")
        cookies = {}
        for header in cookie_headers:
            try:
                first = str(header).split(";", 1)[0]
                k, v = first.split("=", 1)
                cookies[k] = v
            except Exception:
                continue
        return {"sid": sid, "cookies": cookies}

    def _admin_testlab_seed_client_auth(client, username: str) -> dict:
        auth = _admin_testlab_create_auth_context(username)
        for ck, val in (auth.get("cookies") or {}).items():
            _admin_testlab_set_cookie(client, ck, val)
        return auth

    def _admin_testlab_response_json(resp):
        try:
            return resp.get_json(silent=True)
        except Exception:
            return None

    def _admin_testlab_response_preview(resp, *, limit: int = 300) -> str:
        try:
            data = resp.get_data(as_text=True)
        except Exception:
            return ""
        data = str(data or "").strip()
        if len(data) > limit:
            return data[:limit] + "..."
        return data

    def _admin_testlab_exec_optional(cur, sql: str, params=()) -> bool:
        """Run optional cleanup SQL without poisoning the PostgreSQL transaction.

        PostgreSQL marks a whole transaction as failed after any statement error.
        Test Lab cleanup touches tables that may not exist in older upgraded DBs,
        so each optional statement gets a SAVEPOINT.
        """
        sp_name = f"ec_tl_{random.randint(100000, 999999)}"
        try:
            cur.execute(f"SAVEPOINT {sp_name};")
            cur.execute(sql, params)
            cur.execute(f"RELEASE SAVEPOINT {sp_name};")
            return True
        except Exception:
            try:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                cur.execute(f"RELEASE SAVEPOINT {sp_name};")
            except Exception:
                pass
            return False

    def _admin_testlab_cleanup_room(room_name: str) -> None:
        """Delete a temporary Test Lab room and its related room-scoped rows.

        This helper intentionally lives outside the full-suite function because
        manual autosplit cleanup runs later through a separate admin route.  Older
        versions kept the room cleanup helper inside the suite, so the manual
        cleanup route could disconnect clients and delete users but silently fail
        to remove autosplit shard rooms such as Introductions (2).
        """
        room_name = str(room_name or "").strip()
        if not room_name:
            return
        conn = get_db()
        try:
            with conn.cursor() as cur:
                _admin_testlab_exec_optional(cur, "DELETE FROM room_invites WHERE room_name=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM custom_room_invites WHERE room_name=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM custom_room_members WHERE room_name=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM messages WHERE room=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM room_locks WHERE room=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM room_readonly WHERE room=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM room_slowmode WHERE room=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM room_message_expiry WHERE room=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM custom_rooms WHERE name=%s;", (room_name,))
                _admin_testlab_exec_optional(cur, "DELETE FROM chat_rooms WHERE name=%s;", (room_name,))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    def _admin_testlab_room_has_only_testlab_messages(cur, room_name: str) -> bool:
        """Return true only when a shard room has no real user chat history.

        Stale cleanup may need to remove autosplit shards left after a server
        restart.  Those shards can contain Test Lab marker messages, so checking
        for an empty message table is not enough.  This helper keeps cleanup safe:
        it refuses to delete a room that contains any non-Test-Lab sender/message.
        """
        room_name = str(room_name or "").strip()
        if not room_name:
            return False
        try:
            cur.execute(
                """
                SELECT COUNT(*)
                  FROM messages
                 WHERE room=%s
                   AND NOT (
                        sender LIKE 'zz_load_%%'
                        OR sender LIKE 'zz_test_a_%%'
                        OR sender LIKE 'zz_test_b_%%'
                        OR sender LIKE 'zz_flow_%%'
                        OR message LIKE '🧪 Test Lab visible load marker:%%'
                        OR message LIKE '[admin-testlab seed]%%'
                   );
                """,
                (room_name,),
            )
            row = cur.fetchone()
            non_testlab_messages = int((row or [0])[0] or 0)
            return non_testlab_messages == 0
        except Exception:
            return False

    def _admin_testlab_cleanup_username(username: str) -> None:
        username = str(username or "").strip()
        if not username:
            return
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s);", (username,))
                row = cur.fetchone()
                user_id = row[0] if row else None

                # Username-addressed rows.
                _admin_testlab_exec_optional(cur, "DELETE FROM room_invites WHERE invited_user=%s OR invited_by=%s;", (username, username))
                _admin_testlab_exec_optional(cur, "DELETE FROM custom_room_invites WHERE invited_user=%s OR invited_by=%s;", (username, username))
                _admin_testlab_exec_optional(cur, "DELETE FROM offline_messages WHERE sender=%s OR receiver=%s;", (username, username))
                _admin_testlab_exec_optional(cur, "DELETE FROM messages WHERE sender=%s OR receiver=%s;", (username, username))
                _admin_testlab_exec_optional(cur, "DELETE FROM private_messages WHERE sender=%s OR recipient=%s;", (username, username))
                _admin_testlab_exec_optional(cur, "DELETE FROM friend_requests WHERE from_user=%s OR to_user=%s;", (username, username))
                _admin_testlab_exec_optional(cur, "DELETE FROM group_invites WHERE from_user=%s OR to_user=%s;", (username, username))
                _admin_testlab_exec_optional(cur, "DELETE FROM group_mutes WHERE LOWER(username)=LOWER(%s);", (username,))
                _admin_testlab_exec_optional(cur, "DELETE FROM user_profiles WHERE LOWER(username)=LOWER(%s);", (username,))
                _admin_testlab_exec_optional(cur, "DELETE FROM user_sanctions WHERE LOWER(username)=LOWER(%s);", (username,))
                _admin_testlab_exec_optional(cur, "DELETE FROM auth_tokens WHERE LOWER(username)=LOWER(%s);", (username,))
                _admin_testlab_exec_optional(cur, "DELETE FROM auth_sessions WHERE LOWER(username)=LOWER(%s);", (username,))
                _admin_testlab_exec_optional(cur, "DELETE FROM dm_files WHERE sender=%s OR receiver=%s;", (username, username))
                _admin_testlab_exec_optional(cur, "DELETE FROM group_files WHERE sender=%s;", (username,))

                if user_id is not None:
                    # ID-addressed rows.
                    _admin_testlab_exec_optional(cur, "DELETE FROM friends WHERE user_id=%s OR friend_id=%s;", (user_id, user_id))
                    _admin_testlab_exec_optional(cur, "DELETE FROM blocks WHERE blocker=%s OR blocked=%s;", (username, username))
                    _admin_testlab_exec_optional(cur, "DELETE FROM blocked_users WHERE user_id=%s OR blocked_id=%s;", (user_id, user_id))
                    _admin_testlab_exec_optional(cur, "DELETE FROM group_members WHERE user_id=%s;", (user_id,))
                    _admin_testlab_exec_optional(cur, "DELETE FROM chat_settings WHERE user_id=%s;", (user_id,))
                    _admin_testlab_exec_optional(cur, "DELETE FROM notifications WHERE user_id=%s;", (user_id,))
                    _admin_testlab_exec_optional(cur, "DELETE FROM user_roles WHERE user_id=%s;", (user_id,))
                    cur.execute("DELETE FROM users WHERE id=%s;", (user_id,))
                else:
                    cur.execute("DELETE FROM users WHERE LOWER(username)=LOWER(%s);", (username,))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    def _admin_testlab_create_user_direct(username: str, password: str, email: str, *, role_name: str = "viewer") -> bool:
        username = str(username or "").strip()
        password = str(password or "")
        email = str(email or "").strip().lower() or None
        role_name = str(role_name or "viewer").strip().lower() or "viewer"
        if not username or not password or not email:
            return False
        conn = get_db()
        try:
            _admin_testlab_cleanup_username(username)
            create_user_with_keys(
                conn,
                username=username,
                raw_password=password,
                password_hash=hash_password(password),
                email=email,
                is_admin=False,
                recovery_pin_hash=hash_password("1234"),
                recovery_pin_set_at=datetime.now(timezone.utc),
                commit=False,
            )
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s);", (username,))
                user_row = cur.fetchone()
                cur.execute("SELECT id FROM roles WHERE name=%s;", (role_name,))
                role_row = cur.fetchone()
                if user_row and role_row:
                    cur.execute(
                        """
                        INSERT INTO user_roles (user_id, role_id)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id, role_id) DO NOTHING;
                        """,
                        (user_row[0], role_row[0]),
                    )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False

    def _admin_testlab_cleanup_stale_artifacts() -> dict:
        """Remove leftovers from failed prior Test Lab runs.

        Only hard-coded Test Lab prefixes are touched; real rooms/users are not targeted.
        """
        cleaned = {"users": 0, "rooms": 0}
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username FROM users
                     WHERE username LIKE 'zz_load_%'
                        OR username LIKE 'zz_test_a_%'
                        OR username LIKE 'zz_test_b_%'
                        OR username LIKE 'zz_flow_%';
                    """
                )
                stale_users = [str(r[0]) for r in (cur.fetchall() or [])]
                cur.execute(
                    """
                    SELECT name FROM chat_rooms
                     WHERE name LIKE 'zz_test_room_%'
                        OR name LIKE 'zz_test_invite_%'
                        OR name LIKE 'zz_custom_%'
                        OR name LIKE 'zz_flow_group_%';
                    """
                )
                stale_rooms = [str(r[0]) for r in (cur.fetchall() or [])]
                cur.execute(
                    """
                    SELECT name FROM chat_rooms
                     WHERE name LIKE 'Introductions (%)'
                        OR name LIKE 'Teen Talk (%)';
                    """
                )
                autosplit_candidates = [str(r[0]) for r in (cur.fetchall() or [])]
                for room_name in autosplit_candidates:
                    if _admin_testlab_room_has_only_testlab_messages(cur, room_name):
                        stale_rooms.append(room_name)
            for username in stale_users:
                _admin_testlab_cleanup_username(username)
                cleaned["users"] += 1
            for room_name in stale_rooms:
                try:
                    _admin_testlab_cleanup_room(room_name)
                    cleaned["rooms"] += 1
                except Exception:
                    pass
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        return cleaned

    def _admin_testlab_cleanup_active_autosplit(load_id: str | None = None, *, reason: str = "manual") -> dict:
        """Disconnect and delete a visible/manual autosplit load run.

        Manual visible runs keep Socket.IO test clients in a module-level registry so
        the admin can open the real Introductions room and see the temporary users
        before cleanup. This helper is intentionally server-side and admin-only via
        the route that calls it.
        """
        now = time.time()
        selected_id = str(load_id or "").strip()
        with _TESTLAB_ACTIVE_LOADS_LOCK:
            if selected_id and selected_id not in {"latest", "all", "expired"}:
                selected = [selected_id] if selected_id in _TESTLAB_ACTIVE_LOADS else []
            elif selected_id == "expired":
                selected = [lid for lid, info in _TESTLAB_ACTIVE_LOADS.items() if float(info.get("expires_at") or 0) <= now]
            elif selected_id == "all":
                selected = list(_TESTLAB_ACTIVE_LOADS.keys())
            else:
                selected = []
                if _TESTLAB_ACTIVE_LOADS:
                    selected = [max(_TESTLAB_ACTIVE_LOADS, key=lambda lid: float(_TESTLAB_ACTIVE_LOADS[lid].get("started_at") or 0))]
            loads = [(lid, _TESTLAB_ACTIVE_LOADS.pop(lid, None)) for lid in selected]

        cleaned = {"ok": True, "reason": reason, "loads": [], "load_count": 0, "users": 0, "rooms": 0, "clients": 0}
        for lid, info in loads:
            if not info:
                continue
            timer = info.get("timer")
            try:
                if timer is not None and hasattr(timer, "cancel"):
                    timer.cancel()
            except Exception:
                pass
            clients = list(info.get("clients") or [])
            users = list(info.get("users") or [])
            rooms = list(info.get("rooms") or [])
            base_room = str(info.get("base_room") or "Introductions")
            created_base = bool(info.get("created_base"))
            for sc in clients:
                try:
                    sc.disconnect()
                    cleaned["clients"] += 1
                except Exception:
                    pass
            for username in users:
                try:
                    _admin_testlab_cleanup_username(username)
                    cleaned["users"] += 1
                except Exception:
                    pass
            for room_name in rooms:
                try:
                    if room_name == base_room and not created_base:
                        continue
                    _admin_testlab_cleanup_room(room_name)
                    cleaned["rooms"] += 1
                except Exception:
                    pass
            cleaned["loads"].append({
                "load_id": lid,
                "base_room": base_room,
                "users": len(users),
                "rooms": rooms,
                "distribution": info.get("distribution") or {},
                "age_seconds": max(0, int(now - float(info.get("started_at") or now))),
            })
        cleaned["load_count"] = len(cleaned["loads"])
        # Cleaning visible/manual loads should also release the synthetic
        # Socket.IO connect buckets those test clients filled.
        try:
            cleaned["rate_limit_cleanup"] = _admin_testlab_clear_realtime_rate_limits()
        except Exception:
            cleaned["rate_limit_cleanup"] = {"ok": False}
        return cleaned

    def _admin_testlab_cleanup_expired_autosplit_loads() -> dict:
        return _admin_testlab_cleanup_active_autosplit("expired", reason="expired_safety_timeout")

    def _settings_snapshot() -> dict:
        try:
            runtime = current_app.config.get("ECHOCHAT_SETTINGS") or {}
        except Exception:
            runtime = {}
        snap = dict(settings or {})
        snap.update(dict(runtime or {}))
        return snap

    def _safe_int_setting(cfg: dict, key: str, default: int = 0) -> int:
        try:
            return int(cfg.get(key, default) or default)
        except Exception:
            return int(default)

    def _effective_setting(cfg: dict, key: str, default=None):
        """Return the runtime-effective value for settings that older configs may omit.

        Old server_config.json files are intentionally sparse. The application code
        uses safe defaults for newer hardening settings, so Test Lab must evaluate
        the same effective values instead of treating missing/None as disabled.
        """
        try:
            value = (cfg or {}).get(key, None)
        except Exception:
            value = None
        return default if value is None or value == "" else value

    def _effective_int_setting(cfg: dict, key: str, default: int = 0) -> int:
        return _safe_int_setting({key: _effective_setting(cfg, key, default)}, key, default)

    def _effective_bool_setting(cfg: dict, key: str, default: bool = False) -> bool:
        value = _effective_setting(cfg, key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "none", "null", ""}:
            return False
        return bool(default)

    def _result_category_summary(rows: list[dict]) -> dict:
        out: dict[str, dict[str, int]] = {}
        for row in rows or []:
            cat = str((row or {}).get("category") or "general")
            bucket = out.setdefault(cat, {"passed": 0, "failed": 0, "total": 0})
            bucket["total"] += 1
            if bool((row or {}).get("ok")):
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1
        return out

    def _admin_testlab_scalar(query: str, params=(), default=None):
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
            return row[0] if row else default
        except Exception:
            return default

    def _admin_testlab_readiness_results(record):
        cfg = _settings_snapshot()
        hosting_mode = "unknown"
        try:
            from public_beta_readiness import build_public_beta_readiness, infer_hosting_mode
            readiness = build_public_beta_readiness(cfg, settings_file=CONFIG_FILE, repo_root=Path(__file__).resolve().parent)
            hosting_mode = str(readiness.get("mode") or infer_hosting_mode(cfg) or "unknown")
            details = {
                "overall": readiness.get("overall"),
                "mode": hosting_mode,
                "pass_count": readiness.get("pass_count", 0),
                "warn_count": readiness.get("warn_count", 0),
                "fail_count": readiness.get("fail_count", 0),
                "failed_checks": [
                    {"code": item.get("code"), "title": item.get("title"), "fix": item.get("fix")}
                    for item in (readiness.get("items") or [])
                    if item.get("level") == "fail"
                ][:10],
                "note": "Failures block only when this server is configured for public_beta/production hosting.",
            }
            enforced = hosting_mode == "public_beta" or bool(cfg.get("production_mode"))
            record("public beta readiness snapshot", (int(readiness.get("fail_count") or 0) == 0) if enforced else True, details=details, category="deployment")
        except Exception as exc:
            record("public beta readiness snapshot", False, details={"error": str(exc)}, category="deployment")

        try:
            from redis_socketio_readiness import build_redis_socketio_report
            redis_report = build_redis_socketio_report(cfg, live_check=False)
            enforced = hosting_mode == "public_beta" or bool(cfg.get("production_mode"))
            record(
                "Redis and Socket.IO topology snapshot",
                (int(redis_report.get("fail_count") or 0) == 0) if enforced else True,
                details={
                    "overall": redis_report.get("overall"),
                    "mode": redis_report.get("mode"),
                    "workers": redis_report.get("production_workers"),
                    "worker_class": redis_report.get("worker_class"),
                    "async_mode": redis_report.get("async_mode"),
                    "transports": redis_report.get("socketio_transports"),
                    "rate_limit_storage_uri": redis_report.get("rate_limit_storage_uri"),
                    "socketio_message_queue": redis_report.get("socketio_message_queue"),
                    "pass_count": redis_report.get("pass_count", 0),
                    "warn_count": redis_report.get("warn_count", 0),
                    "fail_count": redis_report.get("fail_count", 0),
                },
                category="deployment",
            )
        except Exception as exc:
            record("Redis and Socket.IO topology snapshot", False, details={"error": str(exc)}, category="deployment")

        try:
            from deployment_wizard import build_deployment_plan
            plan = build_deployment_plan(cfg, settings_file=CONFIG_FILE, repo_root=Path(__file__).resolve().parent)
            record(
                "deployment plan generator",
                bool(plan.get("status")),
                details={
                    "status": plan.get("status"),
                    "public_url": plan.get("public_url"),
                    "workers": plan.get("production_workers"),
                    "worker_class": plan.get("worker_class"),
                    "async_mode": plan.get("async_mode"),
                    "step_count": len(plan.get("steps") or []),
                },
                category="deployment",
            )
        except Exception as exc:
            record("deployment plan generator", False, details={"error": str(exc)}, category="deployment")

        public_or_prod = hosting_mode == "public_beta" or bool(cfg.get("production_mode")) or str(cfg.get("run_mode") or "").lower() == "production"
        cookie_secure = bool(cfg.get("cookie_secure"))
        same_site = str(cfg.get("cookie_samesite") or "Lax")
        record(
            "secure cookie posture",
            (cookie_secure and same_site.lower() in {"lax", "strict", "none"}) if public_or_prod else True,
            details={
                "public_or_production": public_or_prod,
                "cookie_secure": cookie_secure,
                "cookie_samesite": same_site,
                "allow_insecure_lan_cookie_fallback": bool(cfg.get("allow_insecure_lan_cookie_fallback")),
            },
            category="security",
        )

        file_quota = _effective_int_setting(cfg, "max_user_file_storage_bytes", 250 * 1024 * 1024)
        torrent_quota = _effective_int_setting(cfg, "max_user_torrent_storage_bytes", 25 * 1024 * 1024)
        torrent_payload = _effective_int_setting(cfg, "max_torrent_total_size_bytes", 1024 * 1024 * 1024 * 1024)
        max_dm_file = _effective_int_setting(cfg, "max_dm_file_bytes", 10 * 1024 * 1024)
        max_group_file = _effective_int_setting(cfg, "max_group_file_bytes", _effective_int_setting(cfg, "max_group_upload_bytes", max_dm_file))
        record(
            "file and torrent quota controls",
            file_quota > 0 and torrent_quota > 0 and torrent_payload > 0 and max_dm_file > 0 and max_group_file > 0,
            details={
                "max_user_file_storage_bytes": file_quota,
                "max_user_torrent_storage_bytes": torrent_quota,
                "max_torrent_total_size_bytes": torrent_payload,
                "max_dm_file_bytes": max_dm_file,
                "max_group_file_bytes": max_group_file,
                "disable_file_transfer_globally": _effective_bool_setting(cfg, "disable_file_transfer_globally", False),
                "disable_dm_files_globally": _effective_bool_setting(cfg, "disable_dm_files_globally", False),
                "disable_group_files_globally": _effective_bool_setting(cfg, "disable_group_files_globally", False),
                "torrent_upload_enabled": _effective_bool_setting(cfg, "torrent_upload_enabled", True),
                "torrent_scrape_enabled": _effective_bool_setting(cfg, "torrent_scrape_enabled", False),
            },
            category="files",
        )

        admin_settings_perm = bool(_admin_testlab_scalar("SELECT 1 FROM permissions WHERE name=%s;", ("admin:settings",), default=0))
        admin_basic_perm = bool(_admin_testlab_scalar("SELECT 1 FROM permissions WHERE name=%s;", ("admin:basic",), default=0))
        admin_role_has_settings = bool(_admin_testlab_scalar(
            """
            SELECT 1
              FROM roles r
              JOIN role_permissions rp ON rp.role_id = r.id
              JOIN permissions p ON p.id = rp.permission_id
             WHERE r.name=%s AND p.name=%s;
            """,
            ("admin", "admin:settings"),
            default=0,
        ))
        admin_role_has_basic = bool(_admin_testlab_scalar(
            """
            SELECT 1
              FROM roles r
              JOIN role_permissions rp ON rp.role_id = r.id
              JOIN permissions p ON p.id = rp.permission_id
             WHERE r.name=%s AND p.name=%s;
            """,
            ("admin", "admin:basic"),
            default=0,
        ))
        record(
            "admin permission seeds and role mapping",
            admin_basic_perm and admin_settings_perm and admin_role_has_basic and admin_role_has_settings,
            details={
                "permission_seed_admin_basic": admin_basic_perm,
                "permission_seed_admin_settings": admin_settings_perm,
                "admin_role_has_admin_basic": admin_role_has_basic,
                "admin_role_has_admin_settings": admin_role_has_settings,
                "fix": "Run the beta.85 migration or restart after upgrade if admin settings still return 403.",
            },
            category="admin",
        )

        socket_payload_limit = _effective_int_setting(cfg, "socketio_event_max_payload_bytes", 64 * 1024)
        socket_event_limit = str(_effective_setting(cfg, "socketio_event_rate_limit", "180 per minute") or "").strip()
        socket_connect_limit = str(_effective_setting(cfg, "socketio_connect_rate_limit", "30 per minute") or "").strip()
        record(
            "Socket.IO abuse controls configured",
            socket_payload_limit > 0 and socket_payload_limit <= 131072 and bool(socket_event_limit) and bool(socket_connect_limit),
            details={
                "socketio_event_max_payload_bytes": socket_payload_limit,
                "socketio_event_rate_limit": socket_event_limit,
                "socketio_connect_rate_limit": socket_connect_limit,
                "socketio_max_sessions_per_user": _effective_int_setting(cfg, "socketio_max_sessions_per_user", 8),
                "socketio_max_sessions_per_auth_session": _effective_int_setting(cfg, "socketio_max_sessions_per_auth_session", 4),
            },
            category="realtime",
        )

        try:
            db_identity = _safe_db_identity()
            schema_state = _safe_schema_state()
            record("database identity and schema available", not bool(db_identity.get("error")) and str(schema_state or "").lower() not in {"", "unknown"}, details={"database": db_identity, "schema_state": schema_state}, category="database")
            try:
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT 1
                          FROM information_schema.columns
                         WHERE table_schema='public'
                           AND table_name='custom_room_members'
                           AND column_name='role'
                         LIMIT 1;
                        """
                    )
                    role_col_present = cur.fetchone() is not None
                record("custom-room role column available", bool(role_col_present), details={"table": "custom_room_members", "column": "role"}, category="database")
            except Exception as exc:
                record("custom-room role column available", False, details={"error": str(exc)}, category="database")
        except Exception as exc:
            record("database identity and schema available", False, details={"error": str(exc)}, category="database")

    def _admin_testlab_run_suite(include_mutating_settings: bool = True, current_password: str | None = None, include_autosplit_load: bool = True, autosplit_hold_seconds: int = 20, autosplit_wait_for_admin: bool = False) -> dict:
        from flask_socketio import SocketIOTestClient
        from database import get_friends_for_user

        actor = _actor()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        uname1 = f"zz_test_a_{stamp}"[-28:]
        uname2 = f"zz_test_b_{stamp}"[-28:]
        pwd1 = f"Zz!{stamp}aA1234"
        pwd2 = f"Zz!{stamp}bB1234"
        email1 = f"{uname1}@example.test"
        email2 = f"{uname2}@example.test"
        room_main = f"zz_test_room_{stamp}"[-40:]
        room_invite = f"zz_test_invite_{stamp}"[-40:]
        custom_room = f"zz_custom_{stamp}"[-40:]
        private_room = f"zz_private_{stamp}"[-40:]
        group_name = f"zz_test_group_{stamp}"[-48:]
        created_users = []
        created_rooms = []
        created_groups = []
        load_test_rooms = []
        load_test_users = []
        load_test_file_ids = []
        results = []
        summary = {"passed": 0, "failed": 0, "skipped": 0}

        def record(name: str, ok: bool, *, details=None, category: str = "general"):
            results.append(_admin_testlab_result(name, ok, details=details, category=category))
            if ok:
                summary["passed"] += 1
            else:
                summary["failed"] += 1

        def scalar(query: str, params=(), default=None):
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
            if not row:
                return default
            return row[0]

        def _cleanup_testlab_room(room_name: str) -> None:
            # Keep the old local name for readability in the suite, but delegate
            # to the shared helper used by the manual autosplit cleanup route.
            _admin_testlab_cleanup_room(room_name)

        def _cleanup_private_file_rows(file_refs: list[tuple[str, str]]) -> None:
            for kind, file_id in list(file_refs or []):
                kind = str(kind or "").strip()
                file_id = str(file_id or "").strip()
                if not file_id:
                    continue
                try:
                    conn = get_db()
                    with conn.cursor() as cur:
                        if kind == "dm":
                            cur.execute("SELECT storage_path FROM dm_files WHERE file_id=%s;", (file_id,))
                            row = cur.fetchone()
                            cur.execute("DELETE FROM dm_files WHERE file_id=%s;", (file_id,))
                        else:
                            cur.execute("SELECT storage_path FROM group_files WHERE file_id=%s;", (file_id,))
                            row = cur.fetchone()
                            cur.execute("DELETE FROM group_files WHERE file_id=%s;", (file_id,))
                    conn.commit()
                    if row and row[0]:
                        try:
                            os.remove(str(row[0]))
                        except Exception:
                            pass
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

        def first_catalog_path() -> tuple[str, str]:
            try:
                from routes_chat import _read_room_catalog
                catalog = _read_room_catalog() or {}
                for cat in (catalog.get("categories") or []):
                    cname = str((cat or {}).get("name") or "").strip()
                    for sub in ((cat or {}).get("subcategories") or []):
                        sname = str((sub or {}).get("name") or "").strip()
                        if cname and sname:
                            return cname, sname
            except Exception:
                pass
            return "Rooms", "All"

        admin_client = app.test_client()
        _admin_testlab_forward_request_cookies(admin_client)
        admin_headers = _admin_testlab_auth_headers(admin_client, "/admin/test_lab/run")

        status_resp = _admin_testlab_get(admin_client, "/admin/auth/status")
        status_json = _admin_testlab_response_json(status_resp) or {}
        record("admin auth status reachable", status_resp.status_code == 200 and bool(status_json.get("ok")), details=status_json, category="admin")
        needs_reauth = bool(status_json.get("reauth_required")) or not bool((status_json.get("actor") or "").strip())
        current_password = str(current_password or "").strip()
        # The full suite is always mutating: it creates/deletes temporary users,
        # rooms, groups, file rows, invites, and socket state even when the
        # reversible settings-write checkbox is off.  Therefore recent admin
        # confirmation is required for the full suite itself, not only for the
        # optional settings write checks.
        if needs_reauth:
            if current_password:
                confirm_resp = _admin_testlab_post(admin_client, 
                    "/admin/auth/confirm",
                    json={"current_password": current_password},
                    headers=_admin_testlab_auth_headers(admin_client, "/admin/auth/confirm"),
                )
                confirm_json = _admin_testlab_response_json(confirm_resp) or {}
                confirm_ok = confirm_resp.status_code == 200 and bool(confirm_json.get("ok"))
                record("admin password confirmation", confirm_ok, details=confirm_json, category="admin")
                if confirm_ok:
                    admin_headers = _admin_testlab_auth_headers(admin_client, "/admin/test_lab/run")
                else:
                    return {
                        "ok": False,
                        "error": "recent_admin_auth_required",
                        "message": "Admin password confirmation failed. Provide the current admin password to run mutating tests.",
                        "coverage": ["admin"],
                        "category_summary": _result_category_summary(results),
                        "results": results,
                        "summary": summary,
                    }
            else:
                return {
                    "ok": False,
                    "error": "recent_admin_auth_required",
                        "message": "Recent admin authentication is required before running live mutating Test Lab checks. Enter the current admin password on the Test Lab page.",
                        "coverage": ["admin"],
                        "category_summary": _result_category_summary(results),
                    "results": results,
                    "summary": summary,
                }

        _admin_testlab_readiness_results(record)

        try:
            expired_cleanup = _admin_testlab_cleanup_expired_autosplit_loads()
            if int(expired_cleanup.get("load_count") or 0):
                record("expired visible autosplit cleanup", True, details=expired_cleanup, category="cleanup")
        except Exception as exc:
            record("expired visible autosplit cleanup", False, details={"error": str(exc)}, category="cleanup")

        try:
            stale_cleanup = _admin_testlab_cleanup_stale_artifacts()
            if int(stale_cleanup.get("users") or 0) or int(stale_cleanup.get("rooms") or 0):
                record("stale Test Lab artifact cleanup", True, details=stale_cleanup, category="cleanup")
        except Exception as exc:
            record("stale Test Lab artifact cleanup", False, details={"error": str(exc)}, category="cleanup")

        try:
            # Public/browser route smoke checks. These catch broken templates before the deeper
            # authenticated suite creates temporary users or rooms.
            for path, name, allowed in [
                ("/login", "login page", {200}),
                ("/register", "register page", {200}),
                ("/forgot-password", "forgot password page", {200}),
                ("/api/room_catalog", "public room catalog", {200}),
            ]:
                resp = _admin_testlab_get(admin_client, path)
                record(name, resp.status_code in allowed, details={"status_code": resp.status_code}, category="auth-pages")

            # Admin read endpoints
            for path, name in [
                ("/admin/stats", "admin stats"),
                ("/admin/diagnostics", "admin diagnostics"),
                ("/admin/users?limit=5", "admin users list"),
                ("/admin/roles", "admin roles"),
                ("/admin/permissions", "admin permissions"),
                ("/admin/settings/voice", "voice settings read"),
                ("/admin/settings/general", "general settings read"),
                ("/admin/settings/antiabuse", "antiabuse settings read"),
                ("/admin/settings/gifs", "gif settings read"),
            ]:
                resp = _admin_testlab_get(admin_client, path)
                record(name, resp.status_code == 200, details=_admin_testlab_response_json(resp), category="admin")

            # Reversible settings writes
            if include_mutating_settings:
                voice_before = _admin_testlab_response_json(_admin_testlab_get(admin_client, "/admin/settings/voice")) or {}
                new_voice_limit = int(voice_before.get("voice_max_room_peers") or 0) + 1
                resp = _admin_testlab_post(admin_client, "/admin/settings/voice", json={"voice_max_room_peers": new_voice_limit}, headers=admin_headers)
                ok = resp.status_code == 200 and int((_admin_testlab_response_json(resp) or {}).get("voice_max_room_peers") or -999) == new_voice_limit
                record("voice settings write/revert", ok, details=_admin_testlab_response_json(resp), category="settings")
                _admin_testlab_post(admin_client, "/admin/settings/voice", json={"voice_max_room_peers": int(voice_before.get("voice_max_room_peers") or 0)}, headers=admin_headers)

                general_before = ((_admin_testlab_response_json(_admin_testlab_get(admin_client, "/admin/settings/general")) or {}).get("settings") or {})
                general_new = int(general_before.get("max_message_length") or 1000) + 1
                resp = _admin_testlab_post(admin_client, "/admin/settings/general", json={"max_message_length": general_new}, headers=admin_headers)
                ok = resp.status_code == 200 and int(((_admin_testlab_response_json(resp) or {}).get("patch") or {}).get("max_message_length") or -1) == general_new
                record("general settings write/revert", ok, details=_admin_testlab_response_json(resp), category="settings")
                _admin_testlab_post(admin_client, "/admin/settings/general", json={"max_message_length": int(general_before.get("max_message_length") or 1000)}, headers=admin_headers)

                anti_before = ((_admin_testlab_response_json(_admin_testlab_get(admin_client, "/admin/settings/antiabuse")) or {}).get("settings") or {})
                anti_new = int(anti_before.get("max_links_per_message") or 5) + 1
                resp = _admin_testlab_post(admin_client, "/admin/settings/antiabuse", json={"max_links_per_message": anti_new}, headers=admin_headers)
                ok = resp.status_code == 200 and int(((_admin_testlab_response_json(resp) or {}).get("patch") or {}).get("max_links_per_message") or -1) == anti_new
                record("antiabuse settings write/revert", ok, details=_admin_testlab_response_json(resp), category="settings")
                _admin_testlab_post(admin_client, "/admin/settings/antiabuse", json={"max_links_per_message": int(anti_before.get("max_links_per_message") or 5)}, headers=admin_headers)

                gif_before = _admin_testlab_response_json(_admin_testlab_get(admin_client, "/admin/settings/gifs")) or {}
                gif_new = int(gif_before.get("giphy_default_limit") or 24) + 1
                resp = _admin_testlab_post(admin_client, "/admin/settings/gifs", json={"giphy_default_limit": gif_new}, headers=admin_headers)
                ok = resp.status_code == 200 and int((_admin_testlab_response_json(resp) or {}).get("giphy_default_limit") or -1) == min(max(gif_new, 1), 48)
                record("gif settings write/revert", ok, details=_admin_testlab_response_json(resp), category="settings")
                _admin_testlab_post(admin_client, "/admin/settings/gifs", json={"giphy_default_limit": int(gif_before.get("giphy_default_limit") or 24)}, headers=admin_headers)

            # Admin creates users
            for username, password, email in [(uname1, pwd1, email1), (uname2, pwd2, email2)]:
                resp = _admin_testlab_post(admin_client, 
                    "/admin/create_user",
                    data={"username": username, "password": password, "email": email, "recovery_pin": "1234", "is_admin": "0"},
                    headers=admin_headers,
                )
                ok = resp.status_code == 200 and (_admin_testlab_response_json(resp) or {}).get("status") == "created"
                record(f"create user {username}", ok, details=_admin_testlab_response_json(resp), category="admin-actions")
                if ok:
                    created_users.append(username)

            # Admin role/status actions against test users
            if uname2 in created_users:
                resp = _admin_testlab_post(admin_client, f"/admin/assign_role/{uname2}", data={"role": "viewer"}, headers=admin_headers)
                record("assign viewer role to test user", resp.status_code == 200 and (_admin_testlab_response_json(resp) or {}).get("status") == "role_assigned", details=_admin_testlab_response_json(resp), category="admin-actions")

                resp = _admin_testlab_post(admin_client, f"/admin/set_user_status/{uname2}", data={"status": "busy"}, headers=admin_headers)
                status_json = _admin_testlab_response_json(resp) or {}
                persisted_presence = scalar("SELECT presence_status FROM users WHERE LOWER(username)=LOWER(%s);", (uname2,), default=None)
                persisted_custom = scalar("SELECT custom_status FROM users WHERE LOWER(username)=LOWER(%s);", (uname2,), default=None)
                record(
                    "set test user status",
                    resp.status_code == 200
                    and status_json.get("status") == "status_set"
                    and str(status_json.get("presence_status") or "") == "busy"
                    and str(persisted_presence or "") == "busy",
                    details={**status_json, "persisted_presence_status": persisted_presence, "persisted_custom_status": persisted_custom},
                    category="admin-actions",
                )

                resp = _admin_testlab_post(admin_client, f"/admin/set_user_quota/{uname2}", data={"messages_per_hour": "250"}, headers=admin_headers)
                quota_json = _admin_testlab_response_json(resp) or {}
                quota_value = scalar("SELECT messages_per_hour FROM user_quotas WHERE LOWER(username)=LOWER(%s);", (uname2,), default=None)
                record("set test user quota", resp.status_code == 200 and quota_json.get("status") == "quota_set" and int(quota_value or -1) == 250, details={**quota_json, "persisted_messages_per_hour": quota_value}, category="admin-actions")

                resp = _admin_testlab_post(admin_client, f"/admin/set_recovery_pin", data={"username": uname2, "recovery_pin": "5678"}, headers=admin_headers)
                pin_json = _admin_testlab_response_json(resp) or {}
                pin_present = bool(scalar("SELECT recovery_pin_hash IS NOT NULL FROM users WHERE LOWER(username)=LOWER(%s);", (uname2,), default=False))
                record("set recovery pin for test user", resp.status_code == 200 and pin_json.get("status") == "ok" and pin_present, details={**pin_json, "recovery_pin_present": pin_present}, category="admin-actions")

            # Admin creates custom rooms and tests room controls
            room_category, room_subcategory = first_catalog_path()
            for room in (room_main, room_invite):
                resp = _admin_testlab_post(admin_client, "/api/custom_rooms", json={"name": room, "category": room_category, "subcategory": room_subcategory, "is_private": False}, headers=admin_headers)
                room_json = _admin_testlab_response_json(resp) or {}
                in_custom_rooms = bool(scalar("SELECT 1 FROM custom_rooms WHERE name=%s;", (room,), default=0))
                ok = resp.status_code in (200, 201) and room_json.get("room") == room and in_custom_rooms
                record(f"create room {room}", ok, details={**room_json, "category": room_category, "subcategory": room_subcategory, "in_custom_rooms": in_custom_rooms}, category="admin-actions")
                if ok:
                    created_rooms.append(room)
            if room_main in created_rooms:
                resp = _admin_testlab_post(admin_client, f"/admin/set_room_readonly/{room_main}", data={"readonly": "1"}, headers=admin_headers)
                readonly_json = _admin_testlab_response_json(resp) or {}
                readonly_value = bool(scalar("SELECT readonly FROM room_readonly WHERE room=%s;", (room_main,), default=False))
                record("set room readonly", resp.status_code == 200 and readonly_json.get("status") == "readonly_set" and readonly_value is True, details={**readonly_json, "persisted_readonly": readonly_value}, category="admin-actions")
                _admin_testlab_post(admin_client, f"/admin/set_room_readonly/{room_main}", data={"readonly": "0"}, headers=admin_headers)

                resp = _admin_testlab_post(admin_client, f"/admin/set_room_slowmode/{room_main}", data={"seconds": "2"}, headers=admin_headers)
                slowmode_json = _admin_testlab_response_json(resp) or {}
                slowmode_value = scalar("SELECT seconds FROM room_slowmode WHERE room=%s;", (room_main,), default=0)
                record("set room slowmode", resp.status_code == 200 and slowmode_json.get("status") == "slowmode_set" and int(slowmode_value or 0) == 2, details={**slowmode_json, "persisted_seconds": slowmode_value}, category="admin-actions")
                _admin_testlab_post(admin_client, f"/admin/set_room_slowmode/{room_main}", data={"seconds": "0"}, headers=admin_headers)

            # Authenticated user clients
            user1_http = app.test_client()
            user2_http = app.test_client()
            _admin_testlab_seed_client_auth(user1_http, uname1)
            _admin_testlab_seed_client_auth(user2_http, uname2)

            # Authenticated room-browser and custom-room API checks.
            cat, subcat = first_catalog_path()
            rooms_resp = _admin_testlab_get(user1_http, "/api/rooms", headers=_admin_testlab_auth_headers(user1_http, "/api/rooms"))
            rooms_json = _admin_testlab_response_json(rooms_resp) or {}
            record("room browser rooms API", rooms_resp.status_code == 200 and isinstance(rooms_json.get("rooms"), list), details=rooms_json, category="room-browser")

            custom_before_resp = _admin_testlab_get(user1_http, "/api/custom_rooms", query_string={"category": cat, "subcategory": subcat}, headers=_admin_testlab_auth_headers(user1_http, "/api/custom_rooms"))
            custom_before_json = _admin_testlab_response_json(custom_before_resp) or {}
            record("custom rooms list API", custom_before_resp.status_code == 200 and isinstance(custom_before_json.get("rooms"), list), details={"category": cat, "subcategory": subcat, **custom_before_json}, category="custom-rooms")

            custom_create_resp = _admin_testlab_post(
                user1_http,
                "/api/custom_rooms",
                json={"name": custom_room, "category": cat, "subcategory": subcat, "is_private": False, "is_18_plus": False, "is_nsfw": False},
                headers=_admin_testlab_auth_headers(user1_http, "/api/custom_rooms"),
            )
            custom_create_json = _admin_testlab_response_json(custom_create_resp) or {}
            custom_created_ok = custom_create_resp.status_code == 201 and custom_create_json.get("status") == "ok" and custom_create_json.get("auto_join") is True
            record("create custom room with auto-enter metadata", custom_created_ok, details=custom_create_json, category="custom-rooms")
            if custom_created_ok:
                created_rooms.append(custom_room)

            custom_after_resp = _admin_testlab_get(user1_http, "/api/custom_rooms", query_string={"category": cat, "subcategory": subcat}, headers=_admin_testlab_auth_headers(user1_http, "/api/custom_rooms"))
            custom_after_json = _admin_testlab_response_json(custom_after_resp) or {}
            custom_listed = any((it or {}).get("name") == custom_room for it in (custom_after_json.get("rooms") or []))
            record("custom room appears in browser list", custom_after_resp.status_code == 200 and custom_listed, details=custom_after_json, category="custom-rooms")

            private_create_resp = _admin_testlab_post(
                user1_http,
                "/api/custom_rooms",
                json={"name": private_room, "category": cat, "subcategory": subcat, "is_private": True, "is_18_plus": False, "is_nsfw": False},
                headers=_admin_testlab_auth_headers(user1_http, "/api/custom_rooms"),
            )
            private_create_json = _admin_testlab_response_json(private_create_resp) or {}
            private_created_ok = private_create_resp.status_code == 201 and private_create_json.get("status") == "ok" and private_create_json.get("is_private") is True
            if private_created_ok:
                created_rooms.append(private_room)
            owner_role = scalar("SELECT role FROM custom_room_members WHERE room_name=%s AND member_user=%s;", (private_room, uname1), default=None)
            record(
                "private room creator gets room owner role",
                private_created_ok and str(owner_role or "") == "owner",
                details={**private_create_json, "persisted_owner_role": owner_role},
                category="private-rooms",
            )

            case_room = private_room.swapcase()
            case_user = uname1.upper()
            owner_role_casefold = scalar(
                "SELECT role FROM custom_room_members WHERE LOWER(room_name)=LOWER(%s) AND LOWER(member_user)=LOWER(%s);",
                (case_room, case_user),
                default=None,
            )
            try:
                from database import get_custom_room_user_role, can_user_moderate_custom_room
                owner_helper_role = get_custom_room_user_role(case_room, case_user)
                owner_helper_can_moderate = bool(can_user_moderate_custom_room(case_room, case_user))
            except Exception as exc:
                owner_helper_role = None
                owner_helper_can_moderate = False
                owner_helper_error = str(exc)[:180]
            else:
                owner_helper_error = None
            record(
                "private room creator owner role survives case drift",
                private_created_ok and str(owner_role_casefold or "") == "owner" and owner_helper_role == "owner" and owner_helper_can_moderate,
                details={
                    "case_room": case_room,
                    "case_user": case_user,
                    "casefold_persisted_owner_role": owner_role_casefold,
                    "helper_owner_role": owner_helper_role,
                    "helper_can_moderate": owner_helper_can_moderate,
                    "helper_error": owner_helper_error,
                },
                category="private-rooms",
            )

            private_owner_list_resp = _admin_testlab_get(user1_http, "/api/custom_rooms", query_string={"category": cat, "subcategory": subcat}, headers=_admin_testlab_auth_headers(user1_http, "/api/custom_rooms"))
            private_owner_list_json = _admin_testlab_response_json(private_owner_list_resp) or {}
            private_user_list_resp = _admin_testlab_get(user2_http, "/api/custom_rooms", query_string={"category": cat, "subcategory": subcat}, headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms"))
            private_user_list_json = _admin_testlab_response_json(private_user_list_resp) or {}
            owner_sees_private = any((it or {}).get("name") == private_room for it in (private_owner_list_json.get("rooms") or []))
            user2_sees_private = any((it or {}).get("name") == private_room for it in (private_user_list_json.get("rooms") or []))
            record(
                "private room visible only to creator before invite",
                private_created_ok and private_owner_list_resp.status_code == 200 and private_user_list_resp.status_code == 200 and owner_sees_private and not user2_sees_private,
                details={"owner_listed": owner_sees_private, "uninvited_listed": user2_sees_private, "owner_rooms_count": len(private_owner_list_json.get("rooms") or []), "uninvited_rooms_count": len(private_user_list_json.get("rooms") or [])},
                category="private-rooms",
            )

            private_owner_rooms_resp = _admin_testlab_get(user1_http, "/api/rooms", headers=_admin_testlab_auth_headers(user1_http, "/api/rooms"))
            private_owner_rooms_json = _admin_testlab_response_json(private_owner_rooms_resp) or {}
            private_uninvited_rooms_resp = _admin_testlab_get(user2_http, "/api/rooms", headers=_admin_testlab_auth_headers(user2_http, "/api/rooms"))
            private_uninvited_rooms_json = _admin_testlab_response_json(private_uninvited_rooms_resp) or {}
            owner_global_sees_private = any((it or {}).get("name") == private_room for it in (private_owner_rooms_json.get("rooms") or []))
            user2_global_sees_private = any((it or {}).get("name") == private_room for it in (private_uninvited_rooms_json.get("rooms") or []))
            record(
                "private room visible to creator in global room API only",
                private_created_ok and private_owner_rooms_resp.status_code == 200 and private_uninvited_rooms_resp.status_code == 200 and owner_global_sees_private and not user2_global_sees_private,
                details={"owner_global_listed": owner_global_sees_private, "uninvited_global_listed": user2_global_sees_private},
                category="private-rooms",
            )

            fake_accept_resp = _admin_testlab_post(user2_http, "/api/custom_rooms/invites/accept", json={"room": private_room}, headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms/invites/accept"))
            fake_accept_json = _admin_testlab_response_json(fake_accept_resp) or {}
            record(
                "private room fake invite accept is rejected",
                private_created_ok and fake_accept_resp.status_code == 403 and str(fake_accept_json.get("error") or "").strip(),
                details={"status_code": fake_accept_resp.status_code, **fake_accept_json},
                category="private-rooms",
            )

            sock1 = socketio.test_client(app, flask_test_client=user1_http)
            sock2 = socketio.test_client(app, flask_test_client=user2_http)
            record("user1 socket connected", sock1.is_connected(), category="realtime")
            record("user2 socket connected", sock2.is_connected(), category="realtime")

            if private_room in created_rooms:
                try:
                    sock1.get_received(); sock2.get_received()
                    sock1.emit("get_rooms", {})
                    sock2.emit("get_rooms", {})
                    sock1_rooms_packets = sock1.get_received()
                    sock2_rooms_packets = sock2.get_received()
                except Exception:
                    sock1_rooms_packets = []
                    sock2_rooms_packets = []

                def _testlab_socket_room_names(packets):
                    names = []
                    for pkt in packets or []:
                        if pkt.get("name") != "room_list":
                            continue
                        args = pkt.get("args") or []
                        payload = args[0] if args else {}
                        for item in (payload.get("rooms") or []):
                            name = (item or {}).get("name")
                            if name:
                                names.append(str(name))
                    return names

                owner_socket_rooms = _testlab_socket_room_names(sock1_rooms_packets)
                uninvited_socket_rooms = _testlab_socket_room_names(sock2_rooms_packets)
                record(
                    "private room visible to creator in socket room list only",
                    private_room in owner_socket_rooms and private_room not in uninvited_socket_rooms,
                    details={"owner_socket_listed": private_room in owner_socket_rooms, "uninvited_socket_listed": private_room in uninvited_socket_rooms},
                    category="private-rooms",
                )

                blocked_join = sock2.emit("join", {"room": private_room}, callback=True)
                record(
                    "private room direct join blocked before invite",
                    not bool((blocked_join or {}).get("success")) and (blocked_join or {}).get("error") == "invite_required",
                    details=blocked_join,
                    category="private-rooms",
                )
                owner_join = sock1.emit("join", {"room": private_room}, callback=True)
                record("private room owner can join", bool((owner_join or {}).get("success")) and (owner_join or {}).get("my_room_role") == "owner", details=owner_join, category="private-rooms")

                sock1.get_received(); sock2.get_received()
                private_invite = sock1.emit("send_message", {"room": private_room, "message": f"/invite {uname2}"}, callback=True)
                recv_private_invite = sock2.get_received()
                saw_private_invite = any(pkt.get("name") == "custom_room_invite" for pkt in recv_private_invite)
                invite_row_present = bool(scalar("SELECT 1 FROM custom_room_invites WHERE room_name=%s AND invited_user=%s;", (private_room, uname2), default=0))
                record(
                    "private room owner invite grants pending access",
                    bool((private_invite or {}).get("success")) and saw_private_invite and invite_row_present,
                    details={"reply": private_invite, "received": recv_private_invite, "invite_row_present": invite_row_present},
                    category="private-rooms",
                )

                listed_invites_resp = _admin_testlab_get(user2_http, "/api/custom_rooms/invites", headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms/invites"))
                listed_invites_json = _admin_testlab_response_json(listed_invites_resp) or {}
                listed_custom_invite = any((it or {}).get("room") == private_room and (it or {}).get("kind") == "custom_private" for it in (listed_invites_json.get("invites") or []))
                record(
                    "private room invite list shows pending invite",
                    listed_invites_resp.status_code == 200 and listed_custom_invite,
                    details={"status_code": listed_invites_resp.status_code, **listed_invites_json},
                    category="private-rooms",
                )

                decline_private_resp = _admin_testlab_post(user2_http, "/api/custom_rooms/invites/decline", json={"room": private_room}, headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms/invites/decline"))
                decline_private_json = _admin_testlab_response_json(decline_private_resp) or {}
                invite_after_decline = bool(scalar("SELECT 1 FROM custom_room_invites WHERE room_name=%s AND invited_user=%s;", (private_room, uname2), default=0))
                record(
                    "private room invite decline removes pending invite",
                    decline_private_resp.status_code == 200 and decline_private_json.get("deleted") == 1 and not invite_after_decline,
                    details={"status_code": decline_private_resp.status_code, "invite_after_decline": invite_after_decline, **decline_private_json},
                    category="private-rooms",
                )

                sock1.get_received(); sock2.get_received()
                private_invite = sock1.emit("send_message", {"room": private_room, "message": f"/invite {uname2}"}, callback=True)
                recv_private_invite = sock2.get_received()
                invite_row_present = bool(scalar("SELECT 1 FROM custom_room_invites WHERE room_name=%s AND invited_user=%s;", (private_room, uname2), default=0))
                record(
                    "private room invite resend works after decline",
                    bool((private_invite or {}).get("success")) and invite_row_present,
                    details={"reply": private_invite, "received": recv_private_invite, "invite_row_present": invite_row_present},
                    category="private-rooms",
                )

                pending_invite_join = sock2.emit("join", {"room": private_room}, callback=True)
                record(
                    "private room pending invite cannot direct join until accepted",
                    not bool((pending_invite_join or {}).get("success")) and (pending_invite_join or {}).get("error") == "invite_required",
                    details=pending_invite_join,
                    category="private-rooms",
                )

                accept_private_resp = _admin_testlab_post(user2_http, "/api/custom_rooms/invites/accept", json={"room": private_room}, headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms/invites/accept"))
                accept_private_json = _admin_testlab_response_json(accept_private_resp) or {}
                private_member_role = scalar("SELECT role FROM custom_room_members WHERE room_name=%s AND member_user=%s;", (private_room, uname2), default=None)
                record(
                    "private room invite accept persists member access",
                    accept_private_resp.status_code == 200 and accept_private_json.get("status") == "ok" and str(private_member_role or "") == "member",
                    details={**accept_private_json, "persisted_member_role": private_member_role},
                    category="private-rooms",
                )

                invited_join = sock2.emit("join", {"room": private_room}, callback=True)
                record("private room invited user can join", bool((invited_join or {}).get("success")), details=invited_join, category="private-rooms")

                # F099: room-scoped moderator roles are durable, normalized by
                # helpers, and limited to this room.  Store mixed case on purpose
                # so the check catches exact-case role comparisons.
                moderator_set_error = None
                try:
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE custom_room_members
                               SET role='MoDeRaToR'
                             WHERE LOWER(room_name)=LOWER(%s)
                               AND LOWER(member_user)=LOWER(%s);
                            """,
                            (private_room, uname2),
                        )
                    conn.commit()
                except Exception as exc:
                    moderator_set_error = str(exc)[:180]
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                moderator_persisted_role = scalar("SELECT role FROM custom_room_members WHERE LOWER(room_name)=LOWER(%s) AND LOWER(member_user)=LOWER(%s);", (private_room, uname2), default=None)
                try:
                    from database import get_custom_room_user_role, can_user_moderate_custom_room
                    moderator_helper_role = get_custom_room_user_role(private_room.swapcase(), uname2.upper())
                    moderator_helper_can_moderate = bool(can_user_moderate_custom_room(private_room.swapcase(), uname2.upper()))
                except Exception as exc:
                    moderator_helper_role = None
                    moderator_helper_can_moderate = False
                    moderator_helper_error = str(exc)[:180]
                else:
                    moderator_helper_error = None
                record(
                    "private room moderator role persists case-insensitively",
                    str(moderator_persisted_role or "").lower() == "moderator" and moderator_helper_role == "moderator" and moderator_helper_can_moderate,
                    details={
                        "persisted_role": moderator_persisted_role,
                        "helper_role": moderator_helper_role,
                        "helper_can_moderate": moderator_helper_can_moderate,
                        "set_error": moderator_set_error,
                        "helper_error": moderator_helper_error,
                    },
                    category="private-rooms",
                )

                moderator_join = sock2.emit("join", {"room": private_room}, callback=True)
                record(
                    "private room moderator receives room-scoped policy",
                    bool((moderator_join or {}).get("success")) and (moderator_join or {}).get("my_room_role") == "moderator" and bool((moderator_join or {}).get("can_room_moderate")),
                    details=moderator_join,
                    category="private-rooms",
                )

                moderator_kick_owner = sock2.emit("room_kick_user", {"room": private_room, "username": uname1}, callback=True)
                owner_still_has_access = scalar("SELECT 1 FROM custom_room_members WHERE LOWER(room_name)=LOWER(%s) AND LOWER(member_user)=LOWER(%s);", (private_room, uname1), default=0)
                record(
                    "private room moderator cannot kick owner",
                    not bool((moderator_kick_owner or {}).get("success")) and "owner" in str((moderator_kick_owner or {}).get("error") or "").lower() and bool(owner_still_has_access),
                    details={"reply": moderator_kick_owner, "owner_still_has_access": bool(owner_still_has_access)},
                    category="private-rooms",
                )

                owner_self_kick = sock1.emit("room_kick_user", {"room": private_room, "username": uname1.swapcase()}, callback=True)
                owner_still_after_self_kick = scalar("SELECT 1 FROM custom_room_members WHERE LOWER(room_name)=LOWER(%s) AND LOWER(member_user)=LOWER(%s);", (private_room, uname1), default=0)
                record(
                    "private room owner cannot kick self",
                    not bool((owner_self_kick or {}).get("success")) and "yourself" in str((owner_self_kick or {}).get("error") or "").lower() and bool(owner_still_after_self_kick),
                    details={"reply": owner_self_kick, "owner_still_has_access": bool(owner_still_after_self_kick)},
                    category="private-rooms",
                )

                absent_room_target = f"{uname2}_not_in_room"
                absent_kick = sock1.emit("room_kick_user", {"room": private_room, "username": absent_room_target}, callback=True)
                record(
                    "private room kick rejects non-room target before access revoke",
                    not bool((absent_kick or {}).get("success")) and "not in that room" in str((absent_kick or {}).get("error") or "").lower(),
                    details={"reply": absent_kick, "target": absent_room_target},
                    category="private-rooms",
                )

                # F104: durable private-room member management is owner-only and
                # separate from the live kick endpoint.  It must work even when
                # the target's access row is removed through the REST manager.
                member_list_resp = _admin_testlab_get(user1_http, "/api/custom_rooms/members", query_string={"room": private_room}, headers=_admin_testlab_auth_headers(user1_http, "/api/custom_rooms/members"))
                member_list_json = _admin_testlab_response_json(member_list_resp) or {}
                manager_members = member_list_json.get("members") or []
                manager_saw_owner = any(str((it or {}).get("username") or "").lower() == uname1.lower() and (it or {}).get("role") == "owner" for it in manager_members)
                manager_saw_member = any(str((it or {}).get("username") or "").lower() == uname2.lower() for it in manager_members)
                record(
                    "private room owner member manager lists access",
                    member_list_resp.status_code == 200 and manager_saw_owner and manager_saw_member,
                    details={"status": member_list_resp.status_code, "saw_owner": manager_saw_owner, "saw_member": manager_saw_member, "body": member_list_json},
                    category="private-rooms",
                )

                moderator_member_list_resp = _admin_testlab_get(user2_http, "/api/custom_rooms/members", query_string={"room": private_room}, headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms/members"))
                moderator_member_list_json = _admin_testlab_response_json(moderator_member_list_resp) or {}
                record(
                    "private room moderator cannot use member manager",
                    moderator_member_list_resp.status_code == 403 and "owner" in str(moderator_member_list_json.get("error") or "").lower(),
                    details={"status": moderator_member_list_resp.status_code, "body": moderator_member_list_json},
                    category="private-rooms",
                )

                manager_revoke_resp = _admin_testlab_post(user1_http, "/api/custom_rooms/members/revoke", json={"room": private_room.swapcase(), "username": uname2.upper()}, headers=_admin_testlab_auth_headers(user1_http, "/api/custom_rooms/members/revoke"))
                manager_revoke_json = _admin_testlab_response_json(manager_revoke_resp) or {}
                access_after_manager_revoke = scalar("SELECT 1 FROM custom_room_members WHERE LOWER(room_name)=LOWER(%s) AND LOWER(member_user)=LOWER(%s);", (private_room, uname2), default=0)
                invite_after_manager_revoke = scalar("SELECT 1 FROM custom_room_invites WHERE LOWER(room_name)=LOWER(%s) AND LOWER(invited_user)=LOWER(%s);", (private_room, uname2), default=0)
                record(
                    "private room owner member manager revokes access",
                    manager_revoke_resp.status_code == 200 and bool(manager_revoke_json.get("revoked")) and not bool(access_after_manager_revoke) and not bool(invite_after_manager_revoke),
                    details={"status": manager_revoke_resp.status_code, "body": manager_revoke_json, "access_after": bool(access_after_manager_revoke), "invite_after": bool(invite_after_manager_revoke)},
                    category="private-rooms",
                )

                after_manager_revoke_join = sock2.emit("join", {"room": private_room}, callback=True)
                record(
                    "private room member manager revoked user cannot rejoin",
                    not bool((after_manager_revoke_join or {}).get("success")) and (after_manager_revoke_join or {}).get("error") == "invite_required",
                    details=after_manager_revoke_join,
                    category="private-rooms",
                )

                # Restore invited access for the remaining private-room checks.
                restore_private_invite = sock1.emit("send_message", {"room": private_room, "message": f"/invite {uname2}"}, callback=True)
                restore_accept_resp = _admin_testlab_post(user2_http, "/api/custom_rooms/invites/accept", json={"room": private_room}, headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms/invites/accept"))
                restore_accept_json = _admin_testlab_response_json(restore_accept_resp) or {}
                restore_join = sock2.emit("join", {"room": private_room}, callback=True)
                record(
                    "private room member manager re-invite restores access",
                    bool((restore_private_invite or {}).get("success")) and restore_accept_resp.status_code == 200 and bool((restore_join or {}).get("success")),
                    details={"invite": restore_private_invite, "accept_status": restore_accept_resp.status_code, "accept": restore_accept_json, "join": restore_join},
                    category="private-rooms",
                )

                # F096: after accept, the pending invite is gone. Visibility and
                # entry must now come from custom_room_members so refresh/relogin
                # style flows still show and enter the private room.
                accepted_list_resp = _admin_testlab_get(user2_http, "/api/custom_rooms", query_string={"category": cat, "subcategory": subcat}, headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms"))
                accepted_list_json = _admin_testlab_response_json(accepted_list_resp) or {}
                accepted_member_listed = any((it or {}).get("name") == private_room for it in (accepted_list_json.get("rooms") or []))
                accepted_global_resp = _admin_testlab_get(user2_http, "/api/rooms", headers=_admin_testlab_auth_headers(user2_http, "/api/rooms"))
                accepted_global_json = _admin_testlab_response_json(accepted_global_resp) or {}
                accepted_global_listed = any((it or {}).get("name") == private_room for it in (accepted_global_json.get("rooms") or []))
                record(
                    "private room accepted member remains visible after refresh",
                    accepted_list_resp.status_code == 200 and accepted_global_resp.status_code == 200 and accepted_member_listed and accepted_global_listed,
                    details={"category_listed": accepted_member_listed, "global_listed": accepted_global_listed, "category_status": accepted_list_resp.status_code, "global_status": accepted_global_resp.status_code},
                    category="private-rooms",
                )

                sock2_fresh = socketio.test_client(app, flask_test_client=user2_http)
                fresh_join = sock2_fresh.emit("join", {"room": private_room}, callback=True) if sock2_fresh.is_connected() else {"success": False, "error": "fresh_socket_not_connected"}
                record(
                    "private room accepted member can rejoin from fresh socket",
                    bool((fresh_join or {}).get("success")),
                    details=fresh_join,
                    category="private-rooms",
                )
                try:
                    sock2_fresh.disconnect()
                except Exception:
                    pass

                reinvite_existing = sock1.emit("send_message", {"room": private_room, "message": f"/invite {uname2}"}, callback=True)
                record(
                    "private room accepted member is not re-invited",
                    not bool((reinvite_existing or {}).get("success")) and "already has access" in str((reinvite_existing or {}).get("error") or ""),
                    details=reinvite_existing,
                    category="private-rooms",
                )

                kick_reply = sock1.emit("room_kick_user", {"room": private_room, "username": uname2}, callback=True)
                access_after_kick = scalar("SELECT 1 FROM custom_room_members WHERE LOWER(room_name)=LOWER(%s) AND LOWER(member_user)=LOWER(%s);", (private_room, uname2), default=0)
                invite_after_kick = scalar("SELECT 1 FROM custom_room_invites WHERE LOWER(room_name)=LOWER(%s) AND LOWER(invited_user)=LOWER(%s);", (private_room, uname2), default=0)
                record(
                    "private room owner kick revokes access",
                    bool((kick_reply or {}).get("success")) and not bool(access_after_kick) and not bool(invite_after_kick),
                    details={"reply": kick_reply, "access_after_kick": bool(access_after_kick), "invite_after_kick": bool(invite_after_kick)},
                    category="private-rooms",
                )

                after_kick_list_resp = _admin_testlab_get(user2_http, "/api/custom_rooms", query_string={"category": cat, "subcategory": subcat}, headers=_admin_testlab_auth_headers(user2_http, "/api/custom_rooms"))
                after_kick_list_json = _admin_testlab_response_json(after_kick_list_resp) or {}
                after_kick_listed = any((it or {}).get("name") == private_room for it in (after_kick_list_json.get("rooms") or []))
                after_kick_global_resp = _admin_testlab_get(user2_http, "/api/rooms", headers=_admin_testlab_auth_headers(user2_http, "/api/rooms"))
                after_kick_global_json = _admin_testlab_response_json(after_kick_global_resp) or {}
                after_kick_global_listed = any((it or {}).get("name") == private_room for it in (after_kick_global_json.get("rooms") or []))
                record(
                    "private room kicked user loses REST visibility",
                    after_kick_list_resp.status_code == 200 and after_kick_global_resp.status_code == 200 and not after_kick_listed and not after_kick_global_listed,
                    details={"category_listed": after_kick_listed, "global_listed": after_kick_global_listed, "category_status": after_kick_list_resp.status_code, "global_status": after_kick_global_resp.status_code},
                    category="private-rooms",
                )

                history_after_kick = sock2.emit("get_room_history", {"room": private_room}, callback=True)
                record(
                    "private room kicked user cannot fetch room history",
                    not bool((history_after_kick or {}).get("success")) and str((history_after_kick or {}).get("error") or "") in {"invite_required", "Not in that room"},
                    details=history_after_kick,
                    category="private-rooms",
                )

                rejoin_after_kick = sock2.emit("join", {"room": private_room}, callback=True)
                record(
                    "private room kicked user cannot rejoin",
                    not bool((rejoin_after_kick or {}).get("success")) and (rejoin_after_kick or {}).get("error") == "invite_required",
                    details=rejoin_after_kick,
                    category="private-rooms",
                )

            if custom_room in created_rooms:
                custom_sock = socketio.test_client(app, flask_test_client=user1_http)
                custom_join = custom_sock.emit("join", {"room": custom_room}, callback=True)
                record("custom room socket join", bool((custom_join or {}).get("success")), details=custom_join, category="custom-rooms")
                try:
                    custom_sock.disconnect()
                except Exception:
                    pass

            # Friend request + accept
            sock1.get_received(); sock2.get_received()
            reply = sock1.emit("send_friend_request", {"to_username": uname2}, callback=True)
            ok = bool((reply or {}).get("success"))
            recv2 = sock2.get_received()
            saw_friend_req = any(pkt.get("name") in {"friend_request", "pending_friend_requests"} for pkt in recv2)
            record("user1 sends friend request to user2", ok and saw_friend_req, details={"reply": reply, "received": recv2}, category="social")

            reply = sock2.emit("accept_friend_request", {"from_user": uname1}, callback=True)
            friends1 = get_friends_for_user(uname1)
            friends2 = get_friends_for_user(uname2)
            ok = bool((reply or {}).get("success")) and (uname2 in friends1) and (uname1 in friends2)
            record("user2 accepts friend request", ok, details={"reply": reply, "friends1": friends1, "friends2": friends2}, category="social")

            # Room join + room chat both ways
            if room_main in created_rooms:
                r1 = sock1.emit("join", {"room": room_main}, callback=True)
                r2 = sock2.emit("join", {"room": room_main}, callback=True)
                ok = bool((r1 or {}).get("success")) and bool((r2 or {}).get("success"))
                record("both users join main test room", ok, details={"user1": r1, "user2": r2}, category="realtime")

                sock1.get_received(); sock2.get_received()
                msg1 = f"hello from {uname1}"
                rmsg1 = sock1.emit("send_message", {"room": room_main, "message": msg1}, callback=True)
                recv2 = sock2.get_received()
                saw = any(pkt.get("name") == "chat_message" and any(isinstance(arg, dict) and arg.get("message") == msg1 for arg in (pkt.get("args") or [])) for pkt in recv2)
                record("room message user1 -> user2", bool((rmsg1 or {}).get("success")) and saw, details={"reply": rmsg1, "received": recv2}, category="realtime")

                typing_reply = sock1.emit("typing", {"room": room_main}, callback=True)
                stop_typing_reply = sock1.emit("stop_typing", {"room": room_main}, callback=True)
                record("room typing indicators", bool((typing_reply or {}).get("success")) and bool((stop_typing_reply or {}).get("success")), details={"typing": typing_reply, "stop_typing": stop_typing_reply}, category="realtime")

                message_id = (rmsg1 or {}).get("message_id")
                sock1.get_received(); sock2.get_received()
                react_reply = sock2.emit("react_to_message", {"room": room_main, "message_id": message_id, "emoji": "👍"}, callback=True) if message_id else {"success": False, "error": "missing_message_id"}
                recv1_react = sock1.get_received()
                saw_reaction = any(pkt.get("name") == "message_reactions" for pkt in recv1_react)
                record("room message reaction", bool((react_reply or {}).get("success")) and saw_reaction, details={"reply": react_reply, "received": recv1_react}, category="reactions")

                sock1.get_received(); sock2.get_received()
                msg2 = f"reply from {uname2}"
                rmsg2 = sock2.emit("send_message", {"room": room_main, "message": msg2}, callback=True)
                recv1 = sock1.get_received()
                saw = any(pkt.get("name") == "chat_message" and any(isinstance(arg, dict) and arg.get("message") == msg2 for arg in (pkt.get("args") or [])) for pkt in recv1)
                record("room message user2 -> user1", bool((rmsg2 or {}).get("success")) and saw, details={"reply": rmsg2, "received": recv1}, category="realtime")

                seed_error = None
                seeded_count = 0
                try:
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO messages (sender, room, message, is_encrypted)
                            VALUES (%s, %s, %s, FALSE), (%s, %s, %s, FALSE);
                            """,
                            (
                                uname1,
                                room_main,
                                f"[admin-testlab seed] {uname1}",
                                uname2,
                                room_main,
                                f"[admin-testlab seed] {uname2}",
                            ),
                        )
                    conn.commit()
                    seeded_count = 2
                except Exception as exc:
                    seed_error = str(exc)
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                before_clear = int(scalar("SELECT COUNT(*) FROM messages WHERE room=%s;", (room_main,), default=0) or 0)
                clear_resp = _admin_testlab_post(admin_client, f"/admin/clear_room/{room_main}", headers=admin_headers)
                clear_json = _admin_testlab_response_json(clear_resp) or {}
                after_clear = int(scalar("SELECT COUNT(*) FROM messages WHERE room=%s;", (room_main,), default=0) or 0)
                record(
                    "clear room",
                    clear_resp.status_code == 200
                    and clear_json.get("status") == "cleared"
                    and seed_error is None
                    and before_clear >= seeded_count >= 2
                    and after_clear == 0,
                    details={**clear_json, "seeded_count": seeded_count, "seed_error": seed_error, "before_count": before_clear, "after_count": after_clear},
                    category="admin-actions",
                )

            # DM both ways
            sock1.get_received(); sock2.get_received()
            dmr1 = sock1.emit("send_direct_message", {"to": uname2, "cipher": _admin_testlab_valid_dm_cipher("admin-testlab-dm-from-a")}, callback=True)
            recv2 = sock2.get_received()
            saw = any(pkt.get("name") == "private_message" and any(isinstance(arg, dict) and arg.get("sender") == uname1 for arg in (pkt.get("args") or [])) for pkt in recv2)
            record("direct message user1 -> user2", bool((dmr1 or {}).get("success")) and saw, details={"reply": dmr1, "received": recv2}, category="social")

            sock1.get_received(); sock2.get_received()
            dmr2 = sock2.emit("send_direct_message", {"to": uname1, "cipher": _admin_testlab_valid_dm_cipher("admin-testlab-dm-from-b")}, callback=True)
            recv1 = sock1.get_received()
            saw = any(pkt.get("name") == "private_message" and any(isinstance(arg, dict) and arg.get("sender") == uname2 for arg in (pkt.get("args") or [])) for pkt in recv1)
            record("direct message user2 -> user1", bool((dmr2 or {}).get("success")) and saw, details={"reply": dmr2, "received": recv1}, category="social")

            # PM P2P file-transfer signaling path. This cannot prove browser NAT
            # traversal from Flask's test client, but it does prove the EchoChat
            # server creates the direct-transfer session, relays offer/answer/ICE
            # between two logged-in users, and cleans up the session afterward.
            p2p_transfer_id = f"p2p_diag_{stamp}"[-64:]
            p2p_meta = {"name": "admin-testlab-p2p.txt", "mime": "text/plain", "size": 29}
            p2p_offer = {"type": "offer", "sdp": "v=0\r\na=group:BUNDLE data\r\n"}
            p2p_answer = {"type": "answer", "sdp": "v=0\r\na=group:BUNDLE data\r\n"}
            p2p_candidate_a = {"candidate": "candidate:1 1 UDP 2122252543 192.0.2.10 5000 typ host", "sdpMid": "0", "sdpMLineIndex": 0}
            p2p_candidate_b = {"candidate": "candidate:2 1 UDP 2122252542 192.0.2.11 5001 typ host", "sdpMid": "0", "sdpMLineIndex": 0}

            try:
                from realtime.state import P2P_FILE_SESSIONS, P2P_FILE_SESSIONS_LOCK
            except Exception:
                P2P_FILE_SESSIONS = None
                P2P_FILE_SESSIONS_LOCK = None

            def _saw_p2p_packet(packets, event_name: str, transfer_id: str, sender: str) -> bool:
                for pkt in packets or []:
                    if pkt.get("name") != event_name:
                        continue
                    for arg in (pkt.get("args") or []):
                        if isinstance(arg, dict) and arg.get("transfer_id") == transfer_id and arg.get("sender") == sender:
                            return True
                return False

            sock1.get_received(); sock2.get_received()
            p2p_offer_reply = sock1.emit(
                "p2p_file_offer",
                {"to": uname2, "transfer_id": p2p_transfer_id, "offer": p2p_offer, "meta": p2p_meta},
                callback=True,
            )
            p2p_offer_recv = sock2.get_received()
            p2p_session_state = {}
            if P2P_FILE_SESSIONS is not None and P2P_FILE_SESSIONS_LOCK is not None:
                with P2P_FILE_SESSIONS_LOCK:
                    p2p_session_state = dict(P2P_FILE_SESSIONS.get(p2p_transfer_id) or {})
            record(
                "p2p file offer signal user1 -> user2",
                bool((p2p_offer_reply or {}).get("success"))
                and bool((p2p_offer_reply or {}).get("delivered"))
                and _saw_p2p_packet(p2p_offer_recv, "p2p_file_offer", p2p_transfer_id, uname1)
                and p2p_session_state.get("state") == "offered",
                details={"reply": p2p_offer_reply, "received": p2p_offer_recv, "session": p2p_session_state},
                category="p2p",
            )

            sock1.get_received(); sock2.get_received()
            p2p_ice_a_reply = sock1.emit(
                "p2p_file_ice",
                {"to": uname2, "transfer_id": p2p_transfer_id, "candidate": p2p_candidate_a},
                callback=True,
            )
            p2p_ice_a_recv = sock2.get_received()
            record(
                "p2p ICE signal user1 -> user2",
                bool((p2p_ice_a_reply or {}).get("success"))
                and bool((p2p_ice_a_reply or {}).get("delivered"))
                and _saw_p2p_packet(p2p_ice_a_recv, "p2p_file_ice", p2p_transfer_id, uname1),
                details={"reply": p2p_ice_a_reply, "received": p2p_ice_a_recv},
                category="p2p",
            )

            sock1.get_received(); sock2.get_received()
            p2p_answer_reply = sock2.emit(
                "p2p_file_answer",
                {"to": uname1, "transfer_id": p2p_transfer_id, "answer": p2p_answer},
                callback=True,
            )
            p2p_answer_recv = sock1.get_received()
            p2p_session_state = {}
            if P2P_FILE_SESSIONS is not None and P2P_FILE_SESSIONS_LOCK is not None:
                with P2P_FILE_SESSIONS_LOCK:
                    p2p_session_state = dict(P2P_FILE_SESSIONS.get(p2p_transfer_id) or {})
            record(
                "p2p file answer signal user2 -> user1",
                bool((p2p_answer_reply or {}).get("success"))
                and bool((p2p_answer_reply or {}).get("delivered"))
                and _saw_p2p_packet(p2p_answer_recv, "p2p_file_answer", p2p_transfer_id, uname2)
                and p2p_session_state.get("state") == "accepted",
                details={"reply": p2p_answer_reply, "received": p2p_answer_recv, "session": p2p_session_state},
                category="p2p",
            )

            sock1.get_received(); sock2.get_received()
            p2p_ice_b_reply = sock2.emit(
                "p2p_file_ice",
                {"to": uname1, "transfer_id": p2p_transfer_id, "candidate": p2p_candidate_b},
                callback=True,
            )
            p2p_ice_b_recv = sock1.get_received()
            record(
                "p2p ICE signal user2 -> user1",
                bool((p2p_ice_b_reply or {}).get("success"))
                and bool((p2p_ice_b_reply or {}).get("delivered"))
                and _saw_p2p_packet(p2p_ice_b_recv, "p2p_file_ice", p2p_transfer_id, uname2),
                details={"reply": p2p_ice_b_reply, "received": p2p_ice_b_recv},
                category="p2p",
            )

            sock1.get_received(); sock2.get_received()
            p2p_decline_reply = sock1.emit(
                "p2p_file_decline",
                {"to": uname2, "transfer_id": p2p_transfer_id, "reason": "Admin Test Lab P2P diagnostic complete"},
                callback=True,
            )
            p2p_decline_recv = sock2.get_received()
            p2p_session_exists_after_decline = False
            if P2P_FILE_SESSIONS is not None and P2P_FILE_SESSIONS_LOCK is not None:
                with P2P_FILE_SESSIONS_LOCK:
                    p2p_session_exists_after_decline = p2p_transfer_id in P2P_FILE_SESSIONS
            record(
                "p2p file decline cleans up session",
                bool((p2p_decline_reply or {}).get("success"))
                and bool((p2p_decline_reply or {}).get("delivered"))
                and _saw_p2p_packet(p2p_decline_recv, "p2p_file_decline", p2p_transfer_id, uname1)
                and not p2p_session_exists_after_decline,
                details={"reply": p2p_decline_reply, "received": p2p_decline_recv, "session_exists_after_decline": p2p_session_exists_after_decline},
                category="p2p",
            )

            # Encrypted DM file upload/meta/blob path.
            try:
                import io
                dm_upload_resp = _admin_testlab_post(
                    user1_http,
                    "/api/dm_files/upload",
                    data={
                        "to": uname2,
                        "iv_b64": "MTIzNDU2Nzg5MDEy",
                        "ek_to_b64": "d3JhcHBlZC10by1rZXk=",
                        "ek_from_b64": "d3JhcHBlZC1mcm9tLWtleQ==",
                        "sha256": "0" * 64,
                        "original_name": "admin-testlab-dm.txt",
                        "mime_type": "text/plain",
                        "file": (io.BytesIO(b"encrypted-dm-testlab-payload"), "admin-testlab-dm.txt"),
                    },
                    content_type="multipart/form-data",
                    headers=_admin_testlab_auth_headers(user1_http, "/api/dm_files/upload"),
                )
                dm_upload_json = _admin_testlab_response_json(dm_upload_resp) or {}
                dm_file_id = str(dm_upload_json.get("file_id") or "")
                if dm_file_id:
                    load_test_file_ids.append(("dm", dm_file_id))
                dm_meta_resp = _admin_testlab_get(user2_http, f"/api/dm_files/{dm_file_id}/meta", headers=_admin_testlab_auth_headers(user2_http, f"/api/dm_files/{dm_file_id}/meta")) if dm_file_id else None
                dm_blob_resp = _admin_testlab_get(user2_http, f"/api/dm_files/{dm_file_id}/blob", headers=_admin_testlab_auth_headers(user2_http, f"/api/dm_files/{dm_file_id}/blob")) if dm_file_id else None
                record(
                    "encrypted DM file upload/meta/blob",
                    dm_upload_resp.status_code == 200
                    and bool(dm_upload_json.get("success"))
                    and dm_meta_resp is not None and dm_meta_resp.status_code == 200
                    and dm_blob_resp is not None and dm_blob_resp.status_code == 200,
                    details={
                        "upload_status": dm_upload_resp.status_code,
                        "upload": dm_upload_json,
                        "meta_status": getattr(dm_meta_resp, "status_code", None),
                        "meta": _admin_testlab_response_json(dm_meta_resp) if dm_meta_resp is not None else None,
                        "blob_status": getattr(dm_blob_resp, "status_code", None),
                    },
                    category="files",
                )
            except Exception as exc:
                record("encrypted DM file upload/meta/blob", False, details={"error": str(exc)}, category="files")

            # Group create/invite/accept/realtime flow.
            group_create_resp = _admin_testlab_post(
                user1_http,
                "/api/groups",
                json={"name": group_name, "description": "Admin Test Lab temporary group"},
                headers=_admin_testlab_auth_headers(user1_http, "/api/groups"),
            )
            group_create_json = _admin_testlab_response_json(group_create_resp) or {}
            group_id = group_create_json.get("group_id")
            group_ok = group_create_resp.status_code == 201 and group_create_json.get("status") == "created" and group_id
            record("create group", bool(group_ok), details=group_create_json, category="groups")
            if group_ok:
                try:
                    group_id = int(group_id)
                    created_groups.append(group_id)
                except Exception:
                    group_id = None

            if group_id:
                group_invite_resp = _admin_testlab_post(
                    user1_http,
                    f"/api/groups/{group_id}/invite",
                    json={"to_user": uname2},
                    headers=_admin_testlab_auth_headers(user1_http, f"/api/groups/{group_id}/invite"),
                )
                group_invite_json = _admin_testlab_response_json(group_invite_resp) or {}
                record("invite user2 to group", group_invite_resp.status_code in {200, 201} and group_invite_json.get("status") in {"invited", "already_member"}, details=group_invite_json, category="groups")

                group_invites_resp = _admin_testlab_get(user2_http, "/api/groups/invites", headers=_admin_testlab_auth_headers(user2_http, "/api/groups/invites"))
                group_invites_json = _admin_testlab_response_json(group_invites_resp) or {}
                group_invite_listed = any(int((it or {}).get("group_id") or -1) == int(group_id) for it in (group_invites_json.get("invites") or []))
                record("group invite appears for user2", group_invites_resp.status_code == 200 and group_invite_listed, details=group_invites_json, category="groups")

                group_accept_resp = _admin_testlab_post(user2_http, f"/api/groups/{group_id}/accept", headers=_admin_testlab_auth_headers(user2_http, f"/api/groups/{group_id}/accept"))
                group_accept_json = _admin_testlab_response_json(group_accept_resp) or {}
                record("user2 accepts group invite", group_accept_resp.status_code == 200 and group_accept_json.get("status") == "joined", details=group_accept_json, category="groups")

                gj1 = sock1.emit("join_group_chat", {"group_id": group_id}, callback=True)
                gj2 = sock2.emit("join_group_chat", {"group_id": group_id}, callback=True)
                record("both users join group socket", bool((gj1 or {}).get("success")) and bool((gj2 or {}).get("success")), details={"user1": gj1, "user2": gj2}, category="groups")

                sock1.get_received(); sock2.get_received()
                gmsg = sock1.emit("group_message", {"group_id": group_id, "message": "ECP1:group-from-a"}, callback=True)
                grec2 = sock2.get_received()
                gsaw = any(pkt.get("name") == "group_message" and any(isinstance(arg, dict) and arg.get("sender") == uname1 for arg in (pkt.get("args") or [])) for pkt in grec2)
                record("group message user1 -> user2", bool((gmsg or {}).get("success")) and gsaw, details={"reply": gmsg, "received": grec2}, category="groups")

                ghist = sock2.emit("get_group_history", {"group_id": group_id, "limit": 10}, callback=True)
                gmembers = sock2.emit("get_group_members", {"group_id": group_id}, callback=True)
                record("group history and members", bool((ghist or {}).get("success")) and bool((gmembers or {}).get("success")) and uname1 in (gmembers or {}).get("members", []) and uname2 in (gmembers or {}).get("members", []), details={"history": ghist, "members": gmembers}, category="groups")

                # Group owner/management actions.
                group_patch_resp = _admin_testlab_patch(
                    user1_http,
                    f"/api/groups/{group_id}",
                    json={"name": group_name + " updated", "description": "Admin Test Lab updated description"},
                    headers=_admin_testlab_auth_headers(user1_http, f"/api/groups/{group_id}"),
                )
                group_patch_json = _admin_testlab_response_json(group_patch_resp) or {}
                record("group settings update", group_patch_resp.status_code == 200 and group_patch_json.get("status") in {"updated", "ok"}, details=group_patch_json, category="groups")

                role_resp = _admin_testlab_post(
                    user1_http,
                    f"/api/groups/{group_id}/set_role",
                    json={"username": uname2, "role": "moderator"},
                    headers=_admin_testlab_auth_headers(user1_http, f"/api/groups/{group_id}/set_role"),
                )
                role_json = _admin_testlab_response_json(role_resp) or {}
                role_value = scalar(
                    """
                    SELECT gm.role
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                     WHERE gm.group_id=%s AND u.username=%s;
                    """,
                    (int(group_id), uname2),
                    default=None,
                )
                record(
                    "group member role change",
                    role_resp.status_code == 200 and str(role_value or "") == "moderator",
                    details={
                        **role_json,
                        "status_code": role_resp.status_code,
                        "persisted_role": role_value,
                        "response_preview": _admin_testlab_response_preview(role_resp),
                    },
                    category="groups",
                )

                mute_resp = _admin_testlab_post(
                    user1_http,
                    f"/api/groups/{group_id}/mute",
                    json={"username": uname2, "minutes": 1},
                    headers=_admin_testlab_auth_headers(user1_http, f"/api/groups/{group_id}/mute"),
                )
                mute_json = _admin_testlab_response_json(mute_resp) or {}
                mutes_resp = _admin_testlab_get(user1_http, f"/api/groups/{group_id}/mutes", headers=_admin_testlab_auth_headers(user1_http, f"/api/groups/{group_id}/mutes"))
                mutes_json = _admin_testlab_response_json(mutes_resp) or {}
                mute_listed = any(str((m or {}).get("username") or "") == uname2 for m in (mutes_json.get("mutes") or []))
                record("group mute listing", mute_resp.status_code == 200 and mutes_resp.status_code == 200 and mute_listed, details={"mute": mute_json, "mutes": mutes_json}, category="groups")

                blocked_gmsg = sock2.emit("group_message", {"group_id": group_id, "message": "ECP1:muted-should-fail"}, callback=True)
                record("group muted member blocked from sending", not bool((blocked_gmsg or {}).get("success")), details=blocked_gmsg, category="groups")

                unmute_resp = _admin_testlab_post(
                    user1_http,
                    f"/api/groups/{group_id}/unmute",
                    json={"username": uname2},
                    headers=_admin_testlab_auth_headers(user1_http, f"/api/groups/{group_id}/unmute"),
                )
                unmute_json = _admin_testlab_response_json(unmute_resp) or {}
                record("group unmute", unmute_resp.status_code == 200 and unmute_json.get("status") in {"unmuted", "ok"}, details=unmute_json, category="groups")

                try:
                    import io
                    ek_map = {uname1: "d3JhcHBlZC1ncm91cC1rZXktMQ==", uname2: "d3JhcHBlZC1ncm91cC1rZXktMg=="}
                    group_file_resp = _admin_testlab_post(
                        user1_http,
                        "/api/group_files/upload",
                        data={
                            "group_id": str(group_id),
                            "iv_b64": "MTIzNDU2Nzg5MDEy",
                            "ek_map_json": json.dumps(ek_map),
                            "sha256": "1" * 64,
                            "original_name": "admin-testlab-group.txt",
                            "mime_type": "text/plain",
                            "file": (io.BytesIO(b"encrypted-group-testlab-payload"), "admin-testlab-group.txt"),
                        },
                        content_type="multipart/form-data",
                        headers=_admin_testlab_auth_headers(user1_http, "/api/group_files/upload"),
                    )
                    group_file_json = _admin_testlab_response_json(group_file_resp) or {}
                    group_file_id = str(group_file_json.get("file_id") or "")
                    if group_file_id:
                        load_test_file_ids.append(("group", group_file_id))
                    group_meta_resp = _admin_testlab_get(user2_http, f"/api/group_files/{group_file_id}/meta", headers=_admin_testlab_auth_headers(user2_http, f"/api/group_files/{group_file_id}/meta")) if group_file_id else None
                    group_blob_resp = _admin_testlab_get(user2_http, f"/api/group_files/{group_file_id}/blob", headers=_admin_testlab_auth_headers(user2_http, f"/api/group_files/{group_file_id}/blob")) if group_file_id else None
                    record(
                        "encrypted group file upload/meta/blob",
                        group_file_resp.status_code == 200
                        and bool(group_file_json.get("success"))
                        and group_meta_resp is not None and group_meta_resp.status_code == 200
                        and group_blob_resp is not None and group_blob_resp.status_code == 200,
                        details={
                            "upload_status": group_file_resp.status_code,
                            "upload": group_file_json,
                            "meta_status": getattr(group_meta_resp, "status_code", None),
                            "meta": _admin_testlab_response_json(group_meta_resp) if group_meta_resp is not None else None,
                            "blob_status": getattr(group_blob_resp, "status_code", None),
                        },
                        category="files",
                    )
                except Exception as exc:
                    record("encrypted group file upload/meta/blob", False, details={"error": str(exc)}, category="files")

            # Room invite flow both directions using dedicated invite room
            if room_invite in created_rooms:
                jr1 = sock1.emit("join", {"room": room_invite}, callback=True)
                record("user1 joins invite room", bool((jr1 or {}).get("success")), details=jr1, category="invite")

                sock1.get_received(); sock2.get_received()
                inv1 = sock1.emit("send_message", {"room": room_invite, "message": f"/invite {uname2}"}, callback=True)
                recv2 = sock2.get_received()
                saw_inv = any(pkt.get("name") in {"room_invite", "custom_room_invite"} for pkt in recv2)
                invites_resp = _admin_testlab_get(user2_http, "/api/rooms/invites", headers=_admin_testlab_auth_headers(user2_http, "/api/rooms/invites"))
                invites_json = _admin_testlab_response_json(invites_resp) or {}
                listed = any((it or {}).get("room") == room_invite for it in (invites_json.get("invites") or []))
                record("user1 invites user2 to room", bool((inv1 or {}).get("success")) and saw_inv and listed, details={"reply": inv1, "received": recv2, "invites": invites_json}, category="invite")

                accept_resp = _admin_testlab_post(user2_http, "/api/rooms/invites/accept", json={"room": room_invite}, headers=_admin_testlab_auth_headers(user2_http, "/api/rooms/invites/accept"))
                accept_json = _admin_testlab_response_json(accept_resp) or {}
                record("user2 accepts room invite", accept_resp.status_code == 200 and accept_json.get("status") == "ok" and int(accept_json.get("deleted") or 0) >= 1, details=accept_json, category="invite")
                jr2 = sock2.emit("join", {"room": room_invite}, callback=True)
                record("user2 joins invited room", bool((jr2 or {}).get("success")), details=jr2, category="invite")

                # reverse direction
                sock1.get_received(); sock2.get_received()
                inv2 = sock2.emit("send_message", {"room": room_invite, "message": f"/invite {uname1}"}, callback=True)
                recv1 = sock1.get_received()
                saw_inv_back = any(pkt.get("name") in {"room_invite", "custom_room_invite"} for pkt in recv1)
                record("user2 invites user1 back", bool((inv2 or {}).get("success")) and saw_inv_back, details={"reply": inv2, "received": recv1}, category="invite")

            # 10-user autosplit / sub-room load check.
            # This specifically verifies: when many users request Introductions and
            # the configured room capacity is below 10, the server routes overflow
            # users into Introductions (2).
            if include_autosplit_load:
                base_load_room = "Introductions"
                load_count = 10
                forced_capacity = 5
                try:
                    visible_hold_seconds = max(0, min(int(autosplit_hold_seconds or 0), _TESTLAB_MAX_MANUAL_HOLD_SECONDS if autosplit_wait_for_admin else 120))
                except Exception:
                    visible_hold_seconds = 300 if autosplit_wait_for_admin else 20
                if autosplit_wait_for_admin and visible_hold_seconds <= 0:
                    visible_hold_seconds = 300
                original_autoscale = {
                    "autoscale_rooms_enabled": settings.get("autoscale_rooms_enabled"),
                    "autoscale_room_capacity": settings.get("autoscale_room_capacity"),
                    "socketio_connect_rate_limit": settings.get("socketio_connect_rate_limit"),
                    "socketio_max_sessions_per_user": settings.get("socketio_max_sessions_per_user"),
                    "socketio_max_sessions_per_auth_session": settings.get("socketio_max_sessions_per_auth_session"),
                    "admin_rate_limit_write": settings.get("admin_rate_limit_write"),
                    "api_rate_limit_write_guard": settings.get("api_rate_limit_write_guard"),
                }
                load_clients = []
                manual_active_load_id = ""
                manual_keep_active = False
                load_created_base = False
                existing_load_rooms = set()
                joined_rooms: list[str] = []
                join_errors: list[dict] = []
                try:
                    try:
                        conn = get_db()
                        with conn.cursor() as cur:
                            cur.execute("SELECT name FROM chat_rooms WHERE name=%s OR name LIKE %s;", (base_load_room, base_load_room + " (%)"))
                            existing_load_rooms = {str(r[0]) for r in (cur.fetchall() or [])}
                    except Exception:
                        existing_load_rooms = set()

                    if not bool(scalar("SELECT 1 FROM chat_rooms WHERE name=%s;", (base_load_room,), default=0)):
                        create_room_if_missing(base_load_room, room_kind="manual")
                        load_created_base = True
                        load_test_rooms.append(base_load_room)

                    settings["autoscale_rooms_enabled"] = True
                    settings["autoscale_room_capacity"] = forced_capacity
                    # This load test is testing room splitting, not connect-storm blocking.
                    settings["socketio_connect_rate_limit"] = "240 per minute"
                    settings["socketio_max_sessions_per_user"] = max(int(settings.get("socketio_max_sessions_per_user") or 8), 8)
                    settings["socketio_max_sessions_per_auth_session"] = max(int(settings.get("socketio_max_sessions_per_auth_session") or 4), 4)
                    # The load test should test room autosplitting, not admin/API write throttles.
                    settings["admin_rate_limit_write"] = "1000 per minute"
                    settings["api_rate_limit_write_guard"] = "1000 per minute"

                    for idx in range(load_count):
                        username = f"zz_load_{idx:03d}_{stamp}"[-28:]
                        password = f"Zz!Load{idx:03d}{stamp}"[-32:] + "aA1!"
                        email = f"{username}@example.test"
                        if _admin_testlab_create_user_direct(username, password, email, role_name="viewer"):
                            load_test_users.append(username)
                        else:
                            join_errors.append({"phase": "create_user", "user": username, "status_code": None, "body": {"error": "direct_create_failed"}})

                    for username in list(load_test_users):
                        c = app.test_client()
                        _admin_testlab_seed_client_auth(c, username)
                        sc = socketio.test_client(app, flask_test_client=c)
                        load_clients.append(sc)
                        if not sc.is_connected():
                            join_errors.append({"phase": "socket_connect", "user": username, "connected": False})
                            continue
                        reply = sc.emit("join", {"room": base_load_room}, callback=True)
                        if bool((reply or {}).get("success")):
                            joined_rooms.append(str((reply or {}).get("room") or ""))
                        else:
                            join_errors.append({"phase": "join", "user": username, "reply": reply})

                    try:
                        live_counts = dict(_state_live_room_counts() or {}) if _state_live_room_counts else {}
                    except Exception:
                        live_counts = {}

                    try:
                        conn = get_db()
                        with conn.cursor() as cur:
                            cur.execute("SELECT name FROM chat_rooms WHERE name=%s OR name LIKE %s ORDER BY name;", (base_load_room, base_load_room + " (%)"))
                            all_load_rooms = [str(r[0]) for r in (cur.fetchall() or [])]
                    except Exception:
                        all_load_rooms = sorted(set(joined_rooms))

                    created_during = [r for r in all_load_rooms if r not in existing_load_rooms]
                    for room_name in created_during:
                        if room_name not in load_test_rooms:
                            load_test_rooms.append(room_name)

                    distribution = {room: joined_rooms.count(room) for room in sorted(set(joined_rooms))}
                    expected_room_count = (load_count + forced_capacity - 1) // forced_capacity
                    overflow_rooms = [r for r in distribution if r != base_load_room]
                    capacity_ok = bool(distribution) and all(0 < int(count) <= forced_capacity for count in distribution.values())
                    split_ok = len(distribution) >= expected_room_count and any(r == f"{base_load_room} (2)" for r in distribution)

                    visible_messages: list[dict] = []
                    visible_rosters: dict[str, list[str]] = {}
                    try:
                        if _state_room_users is not None:
                            visible_rosters = {room: list(_state_room_users(room) or []) for room in sorted(distribution)}
                    except Exception:
                        visible_rosters = {}

                    # Make the load test visible to a real browser that is already watching
                    # Introductions.  Earlier versions joined and cleaned up so quickly that
                    # the test could pass while an admin never saw the temporary users.
                    if (visible_hold_seconds > 0 or autosplit_wait_for_admin) and socketio is not None and distribution:
                        try:
                            refreshed_counts = dict(_state_live_room_counts() or {}) if _state_live_room_counts else live_counts
                        except Exception:
                            refreshed_counts = live_counts
                        try:
                            socketio.emit("room_counts", {"counts": refreshed_counts, "ts": time.time()})
                        except Exception:
                            pass
                        for room_name, count in sorted(distribution.items()):
                            try:
                                users_for_room = list(_state_room_users(room_name) or []) if _state_room_users else []
                                if users_for_room:
                                    socketio.emit("room_users", {"room": room_name, "users": users_for_room}, room=room_name)
                            except Exception:
                                pass
                            try:
                                socketio.emit(
                                    "notification",
                                    {
                                        "room": room_name,
                                        "message": f"🧪 Test Lab: {count} temporary load users are in {room_name}. " + ("They will stay until you click cleanup or the safety timeout expires." if autosplit_wait_for_admin else f"Holding them visible for {visible_hold_seconds}s."),
                                    },
                                    room=room_name,
                                )
                            except Exception:
                                pass

                        # Send one ordinary room message per autosplit shard so the admin can
                        # plainly see the test in the live chat stream, not only in the JSON.
                        sent_marker_rooms: set[str] = set()
                        for idx, room_name in enumerate(joined_rooms):
                            if room_name in sent_marker_rooms:
                                continue
                            if idx >= len(load_clients):
                                continue
                            try:
                                reply = load_clients[idx].emit(
                                    "send_message",
                                    {
                                        "room": room_name,
                                        "message": f"🧪 Test Lab visible load marker: {distribution.get(room_name, 0)} temporary users joined {room_name}." + (" Waiting for admin cleanup." if autosplit_wait_for_admin else ""),
                                    },
                                    callback=True,
                                )
                                visible_messages.append({"room": room_name, "reply": reply})
                            except Exception as exc:
                                visible_messages.append({"room": room_name, "error": str(exc)[:160]})
                            sent_marker_rooms.add(room_name)

                        if autosplit_wait_for_admin:
                            manual_active_load_id = uuid.uuid4().hex[:12]
                            safety_seconds = max(10, min(int(visible_hold_seconds or 300), _TESTLAB_MAX_MANUAL_HOLD_SECONDS))
                            cleanup_timer = Timer(safety_seconds, lambda lid=manual_active_load_id: _admin_testlab_cleanup_active_autosplit(lid, reason="safety_timeout"))
                            cleanup_timer.daemon = True
                            with _TESTLAB_ACTIVE_LOADS_LOCK:
                                _TESTLAB_ACTIVE_LOADS[manual_active_load_id] = {
                                    "load_id": manual_active_load_id,
                                    "clients": list(load_clients),
                                    "users": list(load_test_users),
                                    "rooms": list(load_test_rooms),
                                    "base_room": base_load_room,
                                    "created_base": bool(load_created_base),
                                    "distribution": dict(distribution),
                                    "started_at": time.time(),
                                    "expires_at": time.time() + safety_seconds,
                                    "timer": cleanup_timer,
                                }
                            cleanup_timer.start()
                            manual_keep_active = True
                            # Ownership moved to the active-load registry; the suite finalizers
                            # will clear these local lists after recording the result so cleanup
                            # only happens when the admin clicks the cleanup button.
                        elif visible_hold_seconds > 0:
                            deadline = time.monotonic() + float(visible_hold_seconds)
                            while time.monotonic() < deadline:
                                remaining = deadline - time.monotonic()
                                try:
                                    socketio.sleep(min(1.0, max(0.05, remaining)))
                                except Exception:
                                    time.sleep(min(1.0, max(0.05, remaining)))

                    record(
                        "10-user Introductions autosplit subrooms",
                        len(load_test_users) == load_count
                        and len(joined_rooms) == load_count
                        and not join_errors
                        and capacity_ok
                        and split_ok,
                        details={
                            "requested_room": base_load_room,
                            "users_requested": load_count,
                            "users_created": len(load_test_users),
                            "users_joined": len(joined_rooms),
                            "forced_capacity": forced_capacity,
                            "expected_min_rooms": expected_room_count,
                            "rooms_used": sorted(distribution),
                            "distribution": distribution,
                            "live_counts_subset": {room: live_counts.get(room, 0) for room in sorted(distribution)},
                            "visible_hold_seconds": visible_hold_seconds,
                            "wait_for_admin_cleanup": bool(autosplit_wait_for_admin),
                            "active_load_id": manual_active_load_id,
                            "cleanup_endpoint": "/admin/test_lab/autosplit_cleanup" if manual_active_load_id else "",
                            "visible_marker_messages": visible_messages,
                            "visible_rosters_sample": {room: (users[:8] if isinstance(users, list) else []) for room, users in visible_rosters.items()},
                            "created_rooms": created_during,
                            "errors": join_errors[:10],
                        },
                        category="load-autosplit",
                    )
                    if manual_keep_active:
                        load_clients = []
                        load_test_users = []
                        load_test_rooms = []
                finally:
                    if not manual_keep_active:
                        for sc in load_clients:
                            try:
                                sc.disconnect()
                            except Exception:
                                pass
                    for key, value in original_autoscale.items():
                        if value is None:
                            try:
                                settings.pop(key, None)
                            except Exception:
                                pass
                        else:
                            settings[key] = value
                    if not manual_keep_active:
                        for username in list(load_test_users):
                            _admin_testlab_cleanup_username(username)
                        # Delete only rooms this test created. If a real Introductions already
                        # existed before the test, preserve it.
                        for room_name in list(load_test_rooms):
                            if room_name == base_load_room and not load_created_base:
                                continue
                            _cleanup_testlab_room(room_name)

            # Clean disconnect
            try:
                sock1.disconnect()
            except Exception:
                pass
            try:
                sock2.disconnect()
            except Exception:
                pass

            # Group cleanup through the normal owner API.
            for group_id in list(created_groups):
                resp = _admin_testlab_delete(user1_http, f"/api/groups/{int(group_id)}", headers=_admin_testlab_auth_headers(user1_http, f"/api/groups/{int(group_id)}"))
                group_delete_json = _admin_testlab_response_json(resp) or {}
                group_still_exists = bool(scalar("SELECT 1 FROM groups WHERE id=%s;", (int(group_id),), default=0))
                record(f"delete group {group_id}", resp.status_code == 200 and group_delete_json.get("status") == "deleted" and not group_still_exists, details={**group_delete_json, "exists_after_delete": group_still_exists}, category="cleanup")

            try:
                _cleanup_private_file_rows(load_test_file_ids)
            except Exception:
                pass

            # User cleanup: use direct cleanup so Test Lab cleanup is not itself blocked by admin throttles.
            for room in list(created_rooms):
                _cleanup_testlab_room(room)
                room_still_exists = bool(scalar("SELECT 1 FROM chat_rooms WHERE name=%s;", (room,), default=0)) or bool(scalar("SELECT 1 FROM custom_rooms WHERE name=%s;", (room,), default=0))
                record(f"delete room {room}", not room_still_exists, details={"status": "deleted" if not room_still_exists else "still_exists", "exists_after_delete": room_still_exists}, category="cleanup")
            for username in list(created_users):
                _admin_testlab_cleanup_username(username)
                user_still_exists = bool(scalar("SELECT 1 FROM users WHERE LOWER(username)=LOWER(%s);", (username,), default=0))
                record(f"delete user {username}", not user_still_exists, details={"status": "deleted" if not user_still_exists else "still_exists", "exists_after_delete": user_still_exists}, category="cleanup")
        finally:
            # Last-resort cleanup if admin endpoints failed.
            try:
                _cleanup_private_file_rows(load_test_file_ids)
            except Exception:
                pass
            for username in list(load_test_users):
                try:
                    _admin_testlab_cleanup_username(username)
                except Exception:
                    pass
            for room_name in list(load_test_rooms):
                try:
                    _cleanup_testlab_room(room_name)
                except Exception:
                    pass
            for group_id in list(created_groups):
                try:
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM messages WHERE room=%s;", (f"g:{int(group_id)}",))
                        cur.execute("DELETE FROM group_invites WHERE group_id=%s;", (int(group_id),))
                        cur.execute("DELETE FROM group_mutes WHERE group_id=%s;", (int(group_id),))
                        cur.execute("DELETE FROM group_members WHERE group_id=%s;", (int(group_id),))
                        cur.execute("DELETE FROM groups WHERE id=%s;", (int(group_id),))
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
            for room in (room_main, room_invite, custom_room, private_room):
                try:
                    _cleanup_testlab_room(room)
                except Exception:
                    pass
            for username in (uname1, uname2):
                try:
                    _admin_testlab_cleanup_username(username)
                except Exception:
                    pass

        ok = summary["failed"] == 0
        return {
            "ok": ok,
            "actor": actor,
            "include_mutating_settings": bool(include_mutating_settings),
            "include_autosplit_load": bool(include_autosplit_load),
            "autosplit_hold_seconds": max(0, min(int(autosplit_hold_seconds or 0), _TESTLAB_MAX_MANUAL_HOLD_SECONDS if autosplit_wait_for_admin else 120)),
            "autosplit_wait_for_admin": bool(autosplit_wait_for_admin),
            "coverage": [
                "deployment",
                "security",
                "database",
                "files",
                "auth-pages",
                "admin",
                "settings",
                "admin-actions",
                "room-browser",
                "custom-rooms",
                "private-rooms",
                "realtime",
                "reactions",
                "social",
                "p2p",
                "groups",
                "invite",
                "load-autosplit",
                "cleanup",
            ],
            "active_autosplit_load_id": next((str(((r.get("details") or {}).get("active_load_id") or "")) for r in results if r.get("category") == "load-autosplit" and ((r.get("details") or {}).get("active_load_id"))), ""),
            "artifacts": {"users": [uname1, uname2], "load_users": len(load_test_users), "rooms": [room_main, room_invite, custom_room, private_room], "load_rooms": load_test_rooms, "groups": created_groups},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "category_summary": _result_category_summary(results),
            "summary": summary,
            "results": results,
        }


    def _admin_testlab_run_live_user_flow(current_password: str | None = None) -> dict:
        """Run a compact end-user journey through the live HTTP + Socket.IO stack.

        This is separate from the large full suite on purpose.  It mirrors the
        manual smoke test an admin performs after an upgrade: open chat as a
        normal user, load rooms, click Join immediately, send a room message,
        switch rooms, use PM/friends, then create/open a group.  Temporary
        artifacts use the zz_flow_* prefix and are cleaned up before returning.
        """
        from database import get_friends_for_user

        actor = _actor()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        uname1 = f"zz_flow_a_{stamp}"[-28:]
        uname2 = f"zz_flow_b_{stamp}"[-28:]
        pwd1 = f"Flow!{stamp}aA1234"
        pwd2 = f"Flow!{stamp}bB1234"
        email1 = f"{uname1}@example.test"
        email2 = f"{uname2}@example.test"
        group_name = f"zz_flow_group_{stamp}"[-48:]
        created_users: list[str] = []
        created_groups: list[int] = []
        results: list[dict] = []
        summary = {"passed": 0, "failed": 0, "skipped": 0}
        sock1 = None
        sock2 = None

        def record(name: str, ok: bool, *, details=None, category: str = "live-user-flow"):
            results.append(_admin_testlab_result(name, ok, details=details, category=category))
            if ok:
                summary["passed"] += 1
            else:
                summary["failed"] += 1

        def scalar(query: str, params=(), default=None):
            try:
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    row = cur.fetchone()
                if not row:
                    return default
                return row[0]
            except Exception:
                return default

        def cleanup_group(group_id) -> None:
            try:
                gid = int(group_id)
            except Exception:
                return
            try:
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM messages WHERE room=%s;", (f"g:{gid}",))
                    cur.execute("DELETE FROM group_invites WHERE group_id=%s;", (gid,))
                    cur.execute("DELETE FROM group_mutes WHERE group_id=%s;", (gid,))
                    cur.execute("DELETE FROM group_members WHERE group_id=%s;", (gid,))
                    cur.execute("DELETE FROM groups WHERE id=%s;", (gid,))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        def pick_room(rooms: list[dict], avoid: str | None = None) -> str:
            avoid = str(avoid or "").strip()
            names = [str((r or {}).get("name") or "").strip() for r in (rooms or [])]
            names = [n for n in names if n and n != avoid]
            for preferred in ("Introductions", "Lobby", "Random", "Support"):
                if preferred in names:
                    return preferred
            return names[0] if names else "Introductions"

        admin_client = app.test_client()
        _admin_testlab_forward_request_cookies(admin_client)
        status_resp = _admin_testlab_get(admin_client, "/admin/auth/status")
        status_json = _admin_testlab_response_json(status_resp) or {}
        record("admin auth status reachable", status_resp.status_code == 200 and bool(status_json.get("ok")), details=status_json, category="admin")
        needs_reauth = bool(status_json.get("reauth_required")) or not bool((status_json.get("actor") or "").strip())
        current_password = str(current_password or "").strip()
        if needs_reauth:
            if current_password:
                confirm_resp = _admin_testlab_post(
                    admin_client,
                    "/admin/auth/confirm",
                    json={"current_password": current_password},
                    headers=_admin_testlab_auth_headers(admin_client, "/admin/auth/confirm"),
                )
                confirm_json = _admin_testlab_response_json(confirm_resp) or {}
                confirm_ok = confirm_resp.status_code == 200 and bool(confirm_json.get("ok"))
                record("admin password confirmation", confirm_ok, details=confirm_json, category="admin")
                if not confirm_ok:
                    return {
                        "ok": False,
                        "error": "recent_admin_auth_required",
                        "message": "Admin password confirmation failed. Provide the current admin password to run the Live User Flow Test.",
                        "mode": "live-user-flow",
                        "coverage": ["admin", "live-user-flow"],
                        "category_summary": _result_category_summary(results),
                        "results": results,
                        "summary": summary,
                    }
            else:
                return {
                    "ok": False,
                    "error": "recent_admin_auth_required",
                    "message": "Recent admin authentication is required before running the Live User Flow Test. Enter the current admin password on the Test Lab page.",
                    "mode": "live-user-flow",
                    "coverage": ["admin", "live-user-flow"],
                    "category_summary": _result_category_summary(results),
                    "results": results,
                    "summary": summary,
                }

        if socketio is None:
            record("Socket.IO test client available", False, details={"error": "socketio object was not provided to register_admin_tools"})
        else:
            record("Socket.IO test client available", True, details={"available": True})

        try:
            try:
                _admin_testlab_cleanup_stale_artifacts()
            except Exception:
                pass

            for username, password, email in ((uname1, pwd1, email1), (uname2, pwd2, email2)):
                created = _admin_testlab_create_user_direct(username, password, email, role_name="viewer")
                exists = bool(scalar("SELECT 1 FROM users WHERE LOWER(username)=LOWER(%s);", (username,), default=0))
                record(f"create normal test user {username}", created and exists, details={"created": created, "exists": exists})
                if exists:
                    created_users.append(username)

            user1_http = app.test_client()
            user2_http = app.test_client()
            auth1 = _admin_testlab_seed_client_auth(user1_http, uname1)
            auth2 = _admin_testlab_seed_client_auth(user2_http, uname2)
            record("seed authenticated user sessions", bool(auth1.get("sid")) and bool(auth2.get("sid")), details={"user1_sid": bool(auth1.get("sid")), "user2_sid": bool(auth2.get("sid"))})

            chat_resp = _admin_testlab_get(user1_http, "/chat", headers=_admin_testlab_auth_headers(user1_http, "/chat"))
            chat_text = ""
            try:
                chat_text = chat_resp.get_data(as_text=True)[:2000]
            except Exception:
                chat_text = ""
            record(
                "login session opens chat shell",
                chat_resp.status_code == 200 and ("Chat Rooms" in chat_text or "chat" in chat_text.lower()),
                details={"status_code": chat_resp.status_code, "contains_chat_rooms_text": "Chat Rooms" in chat_text},
            )

            rooms_resp = _admin_testlab_get(user1_http, "/api/rooms", headers=_admin_testlab_auth_headers(user1_http, "/api/rooms"))
            rooms_json = _admin_testlab_response_json(rooms_resp) or {}
            rooms = rooms_json.get("rooms") if isinstance(rooms_json, dict) else []
            room_list_ok = rooms_resp.status_code == 200 and isinstance(rooms, list) and len(rooms) > 0
            requested_room = pick_room(rooms if isinstance(rooms, list) else [])
            switch_target = pick_room(rooms if isinstance(rooms, list) else [], avoid=requested_room)
            record(
                "room list loads for normal user",
                room_list_ok,
                details={"status_code": rooms_resp.status_code, "room_count": len(rooms or []), "first_join_target": requested_room, "switch_target": switch_target},
            )

            if socketio is not None:
                sock1 = socketio.test_client(app, flask_test_client=user1_http)
                sock2 = socketio.test_client(app, flask_test_client=user2_http)
                record("normal user socket connects after login", sock1.is_connected(), details={"user": uname1})
                record("second normal user socket connects after login", sock2.is_connected(), details={"user": uname2})

                join1 = sock1.emit("join", {"room": requested_room}, callback=True) if sock1.is_connected() else {"success": False, "error": "not_connected"}
                actual_room = str((join1 or {}).get("room") or requested_room).strip()
                record(
                    "click Join enters requested or autosplit room",
                    bool((join1 or {}).get("success")) and bool(actual_room),
                    details={"requested_room": requested_room, "actual_room": actual_room, "reply": join1},
                )

                join2 = sock2.emit("join", {"room": actual_room}, callback=True) if sock2.is_connected() else {"success": False, "error": "not_connected"}
                record("second user joins same room for message visibility", bool((join2 or {}).get("success")), details={"room": actual_room, "reply": join2})

                try:
                    sock1.get_received(); sock2.get_received()
                except Exception:
                    pass
                msg = f"live-flow room message from {uname1}"
                send_reply = sock1.emit("send_message", {"room": actual_room, "message": msg}, callback=True) if actual_room else {"success": False, "error": "missing_room"}
                received_by_2 = sock2.get_received() if sock2 is not None else []
                saw_message = any(
                    pkt.get("name") == "chat_message"
                    and any(isinstance(arg, dict) and arg.get("message") == msg for arg in (pkt.get("args") or []))
                    for pkt in (received_by_2 or [])
                )
                record("send room message reaches another user", bool((send_reply or {}).get("success")) and saw_message, details={"reply": send_reply, "received": received_by_2})

                leave_reply = sock1.emit("leave", {"room": actual_room}, callback=True) if actual_room else {"success": False, "error": "missing_room"}
                switch_reply = sock1.emit("join", {"room": switch_target}, callback=True) if switch_target else {"success": False, "error": "missing_switch_target"}
                switched_room = str((switch_reply or {}).get("room") or "").strip()
                record(
                    "switch rooms after first join",
                    bool((leave_reply or {}).get("success")) and bool((switch_reply or {}).get("success")) and bool(switched_room),
                    details={"left_room": actual_room, "leave_reply": leave_reply, "requested_switch_room": switch_target, "actual_switch_room": switched_room, "switch_reply": switch_reply},
                )

                sock1.get_received(); sock2.get_received()
                friend_reply = sock1.emit("send_friend_request", {"to_username": uname2}, callback=True)
                friend_received = sock2.get_received()
                friend_notice = any(pkt.get("name") in {"friend_request", "pending_friend_requests"} for pkt in (friend_received or []))
                record("send friend request during live user flow", bool((friend_reply or {}).get("success")) and friend_notice, details={"reply": friend_reply, "received": friend_received})

                accept_reply = sock2.emit("accept_friend_request", {"from_user": uname1}, callback=True)
                friends1 = get_friends_for_user(uname1)
                friends2 = get_friends_for_user(uname2)
                record("accept friend request during live user flow", bool((accept_reply or {}).get("success")) and uname2 in friends1 and uname1 in friends2, details={"reply": accept_reply, "friends1": friends1, "friends2": friends2})

                sock1.get_received(); sock2.get_received()
                dm_reply = sock1.emit("send_direct_message", {"to": uname2, "cipher": _admin_testlab_valid_dm_cipher("live-flow-dm")}, callback=True)
                dm_received = sock2.get_received()
                saw_dm = any(
                    pkt.get("name") == "private_message"
                    and any(isinstance(arg, dict) and arg.get("sender") == uname1 for arg in (pkt.get("args") or []))
                    for pkt in (dm_received or [])
                )
                record("private message reaches friend", bool((dm_reply or {}).get("success")) and saw_dm, details={"reply": dm_reply, "received": dm_received})

                group_create_resp = _admin_testlab_post(
                    user1_http,
                    "/api/groups",
                    json={"name": group_name, "description": "Live User Flow temporary group"},
                    headers=_admin_testlab_auth_headers(user1_http, "/api/groups"),
                )
                group_create_json = _admin_testlab_response_json(group_create_resp) or {}
                group_id = group_create_json.get("group_id")
                group_ok = group_create_resp.status_code == 201 and group_create_json.get("status") == "created" and group_id
                record("create group from normal user flow", bool(group_ok), details=group_create_json)
                if group_ok:
                    try:
                        group_id = int(group_id)
                        created_groups.append(group_id)
                    except Exception:
                        group_id = None

                if group_id:
                    invite_resp = _admin_testlab_post(
                        user1_http,
                        f"/api/groups/{int(group_id)}/invite",
                        json={"to_user": uname2},
                        headers=_admin_testlab_auth_headers(user1_http, f"/api/groups/{int(group_id)}/invite"),
                    )
                    invite_json = _admin_testlab_response_json(invite_resp) or {}
                    record("invite friend to group from live flow", invite_resp.status_code in {200, 201} and invite_json.get("status") in {"invited", "already_member"}, details=invite_json)

                    accept_group_resp = _admin_testlab_post(user2_http, f"/api/groups/{int(group_id)}/accept", headers=_admin_testlab_auth_headers(user2_http, f"/api/groups/{int(group_id)}/accept"))
                    accept_group_json = _admin_testlab_response_json(accept_group_resp) or {}
                    record("friend accepts group invite from live flow", accept_group_resp.status_code == 200 and accept_group_json.get("status") == "joined", details=accept_group_json)

                    gj1 = sock1.emit("join_group_chat", {"group_id": int(group_id)}, callback=True)
                    gj2 = sock2.emit("join_group_chat", {"group_id": int(group_id)}, callback=True)
                    record("both users open group chat", bool((gj1 or {}).get("success")) and bool((gj2 or {}).get("success")), details={"user1": gj1, "user2": gj2})

                    sock1.get_received(); sock2.get_received()
                    gmsg_reply = sock1.emit("group_message", {"group_id": int(group_id), "message": "ECP1:live-flow-group-message"}, callback=True)
                    group_received = sock2.get_received()
                    saw_group_msg = any(
                        pkt.get("name") == "group_message"
                        and any(isinstance(arg, dict) and arg.get("sender") == uname1 for arg in (pkt.get("args") or []))
                        for pkt in (group_received or [])
                    )
                    record("group message reaches invitee", bool((gmsg_reply or {}).get("success")) and saw_group_msg, details={"reply": gmsg_reply, "received": group_received})
        except Exception as exc:
            record("live user flow raised exception", False, details={"error": str(exc)})
        finally:
            for sc in (sock1, sock2):
                try:
                    if sc is not None:
                        sc.disconnect()
                except Exception:
                    pass
            for gid in list(created_groups):
                cleanup_group(gid)
            for username in list(created_users):
                try:
                    _admin_testlab_cleanup_username(username)
                except Exception:
                    pass

        ok = summary["failed"] == 0
        return {
            "ok": ok,
            "actor": actor,
            "mode": "live-user-flow",
            "coverage": ["auth-pages", "room-browser", "realtime", "social", "groups", "cleanup", "live-user-flow"],
            "artifacts": {"users": [uname1, uname2], "groups": created_groups},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "category_summary": _result_category_summary(results),
            "summary": summary,
            "results": results,
        }

    @app.route('/admin/test_lab', methods=['GET'])
    @app.route('/admin/test-lab', methods=['GET'])
    def admin_test_lab_legacy_page():
        # Keep the predictable page dark. Admins must use the panel launcher,
        # which mints a random, short-lived, session-bound URL.
        abort(404)

    @app.route('/admin/test_lab/readiness', methods=['POST'])
    @app.route('/admin/test-lab/readiness', methods=['POST'])
    @app.route('/admin/test_lab/run', methods=['POST'])
    @app.route('/admin/test-lab/run', methods=['POST'])
    @app.route('/admin/test_lab/live_user_flow', methods=['POST'])
    @app.route('/admin/test-lab/live_user_flow', methods=['POST'])
    @app.route('/admin/test_lab/autosplit_cleanup', methods=['POST'])
    @app.route('/admin/test-lab/autosplit_cleanup', methods=['POST'])
    def admin_test_lab_legacy_action():
        abort(404)

    @app.route('/admin/test_lab/link', methods=['POST'])
    @app.route('/admin/test-lab/link', methods=['POST'])
    @require_permission('admin:test_lab')
    @require_recent_admin_auth
    def admin_test_lab_link():
        token, expires_at = _admin_testlab_issue_link()
        return _admin_json_response({
            "ok": True,
            "url": url_for('admin_test_lab_page', token=token),
            "expires_at": expires_at,
            "expires_in_seconds": max(0, expires_at - int(time.time())),
            "referrer_policy": "no-referrer",
        }), 200

    @app.route('/admin/test_lab/<token>', methods=['GET'])
    @app.route('/admin/test-lab/<token>', methods=['GET'])
    def admin_test_lab_page(token: str):
        _admin_testlab_require_link_or_404(token)
        _admin_testlab_require_admin_or_404()
        html = render_template('admin_test_lab.html', app_version=current_app.config.get('APP_VERSION', ''), server_name=str(settings.get('server_name') or 'Echo-Chat').strip() or 'Echo-Chat')
        return _admin_no_store_html_response(html)

    @app.route('/admin/test_lab/<token>/readiness', methods=['POST'])
    @app.route('/admin/test-lab/<token>/readiness', methods=['POST'])
    def admin_test_lab_readiness(token: str):
        _admin_testlab_require_link_or_404(token)
        _admin_testlab_require_admin_or_404()
        actor = _actor()
        results = []
        summary = {"passed": 0, "failed": 0, "skipped": 0}

        def record(name: str, ok: bool, *, details=None, category: str = "general"):
            results.append(_admin_testlab_result(name, ok, details=details, category=category))
            if ok:
                summary["passed"] += 1
            else:
                summary["failed"] += 1

        _admin_testlab_readiness_results(record)
        payload = {
            "ok": summary["failed"] == 0,
            "actor": actor,
            "mode": "readiness-only",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "coverage": ["deployment", "security", "database", "admin", "files", "realtime"],
            "summary": summary,
            "category_summary": _result_category_summary(results),
            "results": results,
        }
        return _admin_json_response(payload), 200

    @app.route('/admin/test_lab/<token>/run', methods=['POST'])
    @app.route('/admin/test-lab/<token>/run', methods=['POST'])
    def admin_test_lab_run(token: str):
        _admin_testlab_require_link_or_404(token)
        _admin_testlab_require_admin_or_404()
        payload = request.get_json(silent=True) or {}
        include_mutating_settings = bool(payload.get('include_mutating_settings', True))
        include_autosplit_load = bool(payload.get('include_autosplit_load', True))
        autosplit_wait_for_admin = bool(payload.get('autosplit_wait_for_admin', False))
        try:
            # Normal full-suite runs should not hold fake users in the live server.
            # Manual wait mode still defaults to five minutes so the admin can look.
            autosplit_hold_seconds = int(payload.get('autosplit_hold_seconds') or (300 if autosplit_wait_for_admin else 0))
        except Exception:
            autosplit_hold_seconds = 300 if autosplit_wait_for_admin else 0
        current_password = str(payload.get('current_password') or '').strip()
        pre_cleanup = _admin_testlab_cleanup_active_autosplit("all", reason="pre_run_reset")
        try:
            suite = _admin_testlab_run_suite(
                include_mutating_settings=include_mutating_settings,
                current_password=current_password,
                include_autosplit_load=include_autosplit_load,
                autosplit_hold_seconds=autosplit_hold_seconds,
                autosplit_wait_for_admin=autosplit_wait_for_admin,
            )
        finally:
            # Clear synthetic test-client buckets even if a test fails part-way.
            post_rate_cleanup = _admin_testlab_clear_realtime_rate_limits()
        try:
            suite["pre_run_cleanup"] = pre_cleanup
            suite["post_run_rate_limit_cleanup"] = post_rate_cleanup
        except Exception:
            pass
        status = 428 if suite.get('error') == 'recent_admin_auth_required' else 200
        # Completed suites, including suites with failed checks, intentionally return HTTP 200.
        return _admin_json_response(suite, status), status


    @app.route('/admin/test_lab/<token>/live_user_flow', methods=['POST'])
    @app.route('/admin/test-lab/<token>/live_user_flow', methods=['POST'])
    def admin_test_lab_live_user_flow(token: str):
        _admin_testlab_require_link_or_404(token)
        _admin_testlab_require_admin_or_404()
        payload = request.get_json(silent=True) or {}
        current_password = str(payload.get('current_password') or '').strip()
        try:
            suite = _admin_testlab_run_live_user_flow(current_password=current_password)
        finally:
            rate_cleanup = _admin_testlab_clear_realtime_rate_limits()
        try:
            suite["post_run_rate_limit_cleanup"] = rate_cleanup
        except Exception:
            pass
        status = 428 if suite.get('error') == 'recent_admin_auth_required' else 200
        return _admin_json_response(suite, status), status

    @app.route('/admin/test_lab/<token>/autosplit_cleanup', methods=['POST'])
    @app.route('/admin/test-lab/<token>/autosplit_cleanup', methods=['POST'])
    def admin_test_lab_autosplit_cleanup(token: str):
        _admin_testlab_require_link_or_404(token)
        _admin_testlab_require_admin_or_404()
        payload = request.get_json(silent=True) or {}
        load_id, load_error = _admin_testlab_normalize_cleanup_load_id(payload.get('load_id'))
        if load_error:
            return _admin_json_response({"ok": False, "error": load_error}, 400), 400
        return _admin_json_response(_admin_testlab_cleanup_active_autosplit(load_id, reason='admin_requested')), 200


    # ── Profile safety helpers ───────────────────────────────
    def _profile_notification_pref_enabled(username: str, column_name: str, default: bool = True) -> bool:
        username = str(username or '').strip()
        column_name = str(column_name or '').strip()
        allowed = {'notify_admin_notices', 'notify_report_updates'}
        if not username or column_name not in allowed:
            return bool(default)
        conn = get_db()
        try:
            # Schema is managed by startup migrations; this helper only reads.
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.user_profile_notification_settings');")
                if not (cur.fetchone() or [None])[0]:
                    raise RuntimeError("Profile notification settings table is missing; run `python main.py --migrate`.")
                cur.execute("SELECT " + column_name + " FROM user_profile_notification_settings WHERE LOWER(username) = LOWER(%s) LIMIT 1;", (username,))
                row = cur.fetchone()
            return bool(row[0]) if row else bool(default)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return bool(default)


    def _profile_admin_notice(username: str, message: str, payload_extra: dict | None = None) -> int:
        username = str(username or '').strip()
        message = str(message or '').strip()[:500]
        if not username or not message:
            return 0
        if not _profile_notification_pref_enabled(username, 'notify_admin_notices', True):
            return 0
        payload = {'type': 'profile_post_warning', 'message': message, 'created_at': ''}
        if isinstance(payload_extra, dict):
            payload.update(payload_extra)
        notif_id = 0
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notifications (user_id, notification, type)
                    SELECT id, %s, 'profile_post_warning'
                      FROM users
                     WHERE LOWER(username) = LOWER(%s)
                    RETURNING id, timestamp;
                    """,
                    (json.dumps(payload, separators=(',', ':')), username),
                )
                row = cur.fetchone()
            conn.commit()
            if row:
                notif_id = int(row[0] or 0)
                payload['id'] = notif_id
                payload['created_at'] = row[1].isoformat() if hasattr(row[1], 'isoformat') else str(row[1] or '')
                if socketio is not None and _state_user_sids is not None:
                    for sid in _state_user_sids(username):
                        try:
                            socketio.emit('profile_post_notification', payload, to=sid)
                        except Exception:
                            pass
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        return notif_id


    def _profile_report_update_notice(username: str, message: str, payload_extra: dict | None = None) -> int:
        username = str(username or '').strip()
        message = str(message or '').strip()[:500]
        if not username or not message:
            return 0
        if not _profile_notification_pref_enabled(username, 'notify_report_updates', True):
            return 0
        payload = {'type': 'profile_post_report_update', 'message': message, 'created_at': ''}
        if isinstance(payload_extra, dict):
            payload.update(payload_extra)
        notif_id = 0
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notifications (user_id, notification, type)
                    SELECT id, %s, 'profile_post_report_update'
                      FROM users
                     WHERE LOWER(username) = LOWER(%s)
                    RETURNING id, timestamp;
                    """,
                    (json.dumps(payload, separators=(',', ':')), username),
                )
                row = cur.fetchone()
            conn.commit()
            if row:
                notif_id = int(row[0] or 0)
                payload['id'] = notif_id
                payload['created_at'] = row[1].isoformat() if hasattr(row[1], 'isoformat') else str(row[1] or '')
                if socketio is not None and _state_user_sids is not None:
                    for sid in _state_user_sids(username):
                        try:
                            socketio.emit('profile_post_notification', payload, to=sid)
                        except Exception:
                            pass
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        return notif_id


    def _profile_report_dict(r):
        return {
            'id': int(r[0]),
            'reporter_username': str(r[1] or ''),
            'post_id': int(r[2] or 0),
            'comment_id': int(r[3] or 0),
            'target_username': str(r[4] or ''),
            'reason': str(r[5] or 'other'),
            'details': str(r[6] or ''),
            'status': str(r[7] or 'open'),
            'reviewed_by': str(r[8] or ''),
            'reviewed_at': r[9].isoformat() if hasattr(r[9], 'isoformat') else str(r[9] or ''),
            'action_taken': str(r[10] or ''),
            'created_at': r[11].isoformat() if hasattr(r[11], 'isoformat') else str(r[11] or ''),
            'updated_at': r[12].isoformat() if hasattr(r[12], 'isoformat') else str(r[12] or ''),
            'post_author': str(r[13] or ''),
            'post_body': str(r[14] or ''),
            'post_deleted_at': r[15].isoformat() if hasattr(r[15], 'isoformat') else str(r[15] or ''),
            'comment_author': str(r[16] or ''),
            'comment_body': str(r[17] or ''),
            'comment_deleted_at': r[18].isoformat() if hasattr(r[18], 'isoformat') else str(r[18] or ''),
        }


    def _admin_profile_limit(raw, default: int = 50, maximum: int = 200) -> int:
        try:
            value = int(raw or default)
        except Exception:
            value = int(default)
        return max(1, min(value, int(maximum or 200)))

    def _admin_profile_status(raw: str, allowed: set[str], default: str) -> str:
        status_value = str(raw or default).strip().lower()[:24]
        return status_value if status_value in allowed else default

    def _admin_profile_search(raw: str, max_len: int = 100) -> tuple[str, str]:
        q_value = str(raw or '').strip()[:max(1, int(max_len or 100))]
        return q_value, _admin_like_pattern(q_value, max_len=max_len)

    # ── Profile post moderation ───────────────────────────────
    @app.route('/admin/profile_posts', methods=['GET'])
    @require_permission('profile:moderate')
    def admin_profile_posts():
        _ensure_admin_profile_runtime_schema()
        q, like = _admin_profile_search(request.args.get('q') or request.args.get('query') or '', max_len=100)
        status = _admin_profile_status(request.args.get('status') or 'active', {'active', 'deleted', 'all'}, 'active')
        limit = _admin_profile_limit(request.args.get('limit'), default=50, maximum=200)

        where = []
        params = []
        if status == 'deleted':
            where.append('p.deleted_at IS NOT NULL')
        elif status == 'all':
            pass
        else:
            where.append('p.deleted_at IS NULL')
        if q:
            where.append("(p.author_username ILIKE %s ESCAPE '\\\\' OR p.body ILIKE %s ESCAPE '\\\\' OR p.link_url ILIKE %s ESCAPE '\\\\')")
            params.extend([like, like, like])
        where_sql = 'WHERE ' + ' AND '.join(where) if where else ''
        params.append(limit)

        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT p.id, p.author_username, p.body, p.visibility, p.image_url, p.gif_url, p.link_url,
                           p.is_pinned, p.is_featured, p.created_at, p.updated_at, p.edited_at, COALESCE(p.edit_count, 0),
                           p.deleted_at, p.moderated_by, p.moderated_reason, p.moderated_at,
                           COALESCE((SELECT COUNT(*) FROM profile_post_reactions r WHERE r.post_id = p.id AND r.reaction = 'like'), 0) AS reaction_count,
                           COALESCE((SELECT COUNT(*) FROM profile_post_comments c WHERE c.post_id = p.id AND c.deleted_at IS NULL), 0) AS comment_count
                      FROM profile_posts p
                      {where_sql}
                     ORDER BY p.created_at DESC, p.id DESC
                     LIMIT %s;
                    """,
                    tuple(params),
                )
                rows = cur.fetchall() or []
            posts = []
            for r in rows:
                posts.append({
                    'id': int(r[0]),
                    'author_username': str(r[1] or ''),
                    'body': str(r[2] or ''),
                    'visibility': str(r[3] or 'friends'),
                    'image_url': str(r[4] or ''),
                    'gif_url': str(r[5] or ''),
                    'link_url': str(r[6] or ''),
                    'is_pinned': bool(r[7]),
                    'is_featured': bool(r[8]),
                    'created_at': r[9].isoformat() if hasattr(r[9], 'isoformat') else str(r[9] or ''),
                    'updated_at': r[10].isoformat() if hasattr(r[10], 'isoformat') else str(r[10] or ''),
                    'edited_at': r[11].isoformat() if hasattr(r[11], 'isoformat') else str(r[11] or ''),
                    'edit_count': int(r[12] or 0),
                    'deleted_at': r[13].isoformat() if hasattr(r[13], 'isoformat') else str(r[13] or ''),
                    'moderated_by': str(r[14] or ''),
                    'moderated_reason': str(r[15] or ''),
                    'moderated_at': r[16].isoformat() if hasattr(r[16], 'isoformat') else str(r[16] or ''),
                    'reaction_count': int(r[17] or 0),
                    'comment_count': int(r[18] or 0),
                })
            return _admin_json_response({'ok': True, 'posts': posts, 'status': status, 'query': q, 'limit': limit})
        except Exception as e:
            return _admin_operation_error('admin_profile_posts', e)


    @app.route('/admin/profile_posts/<int:post_id>/comments', methods=['GET'])
    @require_permission('profile:moderate')
    def admin_profile_post_comments(post_id):
        _ensure_admin_profile_runtime_schema()
        limit = _admin_profile_limit(request.args.get('limit'), default=50, maximum=200)
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id, c.post_id, c.author_username, c.body, c.created_at, c.updated_at,
                           c.deleted_at, c.deleted_by, c.deleted_reason
                      FROM profile_post_comments c
                     WHERE c.post_id = %s
                     ORDER BY c.created_at DESC, c.id DESC
                     LIMIT %s;
                    """,
                    (int(post_id), limit),
                )
                rows = cur.fetchall() or []
            comments = []
            for r in rows:
                comments.append({
                    'id': int(r[0]),
                    'post_id': int(r[1]),
                    'author_username': str(r[2] or ''),
                    'body': str(r[3] or ''),
                    'created_at': r[4].isoformat() if hasattr(r[4], 'isoformat') else str(r[4] or ''),
                    'updated_at': r[5].isoformat() if hasattr(r[5], 'isoformat') else str(r[5] or ''),
                    'deleted_at': r[6].isoformat() if hasattr(r[6], 'isoformat') else str(r[6] or ''),
                    'deleted_by': str(r[7] or ''),
                    'deleted_reason': str(r[8] or ''),
                })
            return _admin_json_response({'ok': True, 'comments': comments, 'post_id': int(post_id), 'limit': limit})
        except Exception as e:
            return _admin_operation_error('admin_profile_post_comments', e)


    @app.route('/admin/profile_posts/<int:post_id>/delete', methods=['POST'])
    @require_permission('profile:moderate')
    @require_recent_admin_auth
    def admin_delete_profile_post(post_id):
        actor = _actor()
        reason = str(request.form.get('reason') or 'Removed by admin').strip()[:500]
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE profile_posts
                       SET deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
                           updated_at = CURRENT_TIMESTAMP,
                           is_pinned = FALSE,
                           is_featured = FALSE,
                           moderated_by = %s,
                           moderated_reason = %s,
                           moderated_at = CURRENT_TIMESTAMP
                     WHERE id = %s
                    RETURNING author_username;
                    """,
                    (actor, reason, int(post_id)),
                )
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found_or_already_reviewed'}, 404)
            conn.commit()
            log_audit_event(actor, 'admin_profile_post_delete', str(row[0] or ''), f'post_id={int(post_id)} reason={reason}')
            _profile_admin_notice(str(row[0] or ''), f'An admin removed your profile post: {reason}', {'post_id': int(post_id), 'action': 'removed_post'})
            return _admin_json_response({'ok': True, 'status': 'deleted', 'post_id': int(post_id), 'author_username': str(row[0] or '')})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error('admin_profile_post_delete', e)


    @app.route('/admin/profile_posts/<int:post_id>/restore', methods=['POST'])
    @require_permission('profile:moderate')
    @require_recent_admin_auth
    def admin_restore_profile_post(post_id):
        actor = _actor()
        reason = str(request.form.get('reason') or 'Restored by admin').strip()[:500]
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE profile_posts
                       SET deleted_at = NULL,
                           updated_at = CURRENT_TIMESTAMP,
                           moderated_by = %s,
                           moderated_reason = %s,
                           moderated_at = CURRENT_TIMESTAMP
                     WHERE id = %s
                    RETURNING author_username;
                    """,
                    (actor, reason, int(post_id)),
                )
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found'}, 404)
            conn.commit()
            log_audit_event(actor, 'admin_profile_post_restore', str(row[0] or ''), f'post_id={int(post_id)} reason={reason}')
            _profile_admin_notice(str(row[0] or ''), f'An admin restored your profile post: {reason}', {'post_id': int(post_id), 'action': 'restored_post'})
            return _admin_json_response({'ok': True, 'status': 'restored', 'post_id': int(post_id), 'author_username': str(row[0] or '')})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error('admin_profile_post_restore', e)


    @app.route('/admin/profile_posts/<int:post_id>/comments/<int:comment_id>/delete', methods=['POST'])
    @require_permission('profile:moderate')
    @require_recent_admin_auth
    def admin_delete_profile_post_comment(post_id, comment_id):
        actor = _actor()
        reason = str(request.form.get('reason') or 'Removed by admin').strip()[:500]
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE profile_post_comments
                       SET deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
                           updated_at = CURRENT_TIMESTAMP,
                           deleted_by = %s,
                           deleted_reason = %s
                     WHERE id = %s
                       AND post_id = %s
                    RETURNING author_username;
                    """,
                    (actor, reason, int(comment_id), int(post_id)),
                )
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found'}, 404)
            conn.commit()
            log_audit_event(actor, 'admin_profile_comment_delete', str(row[0] or ''), f'post_id={int(post_id)} comment_id={int(comment_id)} reason={reason}')
            _profile_admin_notice(str(row[0] or ''), f'An admin removed your profile comment: {reason}', {'post_id': int(post_id), 'comment_id': int(comment_id), 'action': 'removed_comment'})
            return _admin_json_response({'ok': True, 'status': 'comment_deleted', 'post_id': int(post_id), 'comment_id': int(comment_id)})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error('admin_profile_comment_delete', e)


    # ── Profile report queue ───────────────────────────────
    @app.route('/admin/profile_reports', methods=['GET'])
    @require_permission('profile:moderate')
    def admin_profile_reports():
        _ensure_admin_profile_runtime_schema()
        q, like = _admin_profile_search(request.args.get('q') or request.args.get('query') or '', max_len=100)
        status = _admin_profile_status(request.args.get('status') or 'open', {'open', 'actioned', 'dismissed', 'all'}, 'open')
        limit = _admin_profile_limit(request.args.get('limit'), default=50, maximum=200)
        where = []
        params = []
        if status and status != 'all':
            where.append('r.status = %s')
            params.append(status)
        if q:
            where.append("(r.reporter_username ILIKE %s ESCAPE '\\\\' OR r.target_username ILIKE %s ESCAPE '\\\\' OR r.reason ILIKE %s ESCAPE '\\\\' OR r.details ILIKE %s ESCAPE '\\\\' OR p.body ILIKE %s ESCAPE '\\\\' OR c.body ILIKE %s ESCAPE '\\\\')")
            params.extend([like, like, like, like, like, like])
        where_sql = 'WHERE ' + ' AND '.join(where) if where else ''
        params.append(limit)
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT r.id, r.reporter_username, r.post_id, COALESCE(r.comment_id, 0), r.target_username,
                           r.reason, r.details, r.status, r.reviewed_by, r.reviewed_at, r.action_taken,
                           r.created_at, r.updated_at, p.author_username, p.body, p.deleted_at,
                           c.author_username, c.body, c.deleted_at
                      FROM profile_post_reports r
                      JOIN profile_posts p ON p.id = r.post_id
                 LEFT JOIN profile_post_comments c ON c.id = r.comment_id
                      {where_sql}
                     ORDER BY CASE WHEN r.status = 'open' THEN 0 ELSE 1 END, r.created_at DESC, r.id DESC
                     LIMIT %s;
                    """,
                    tuple(params),
                )
                rows = cur.fetchall() or []
            return _admin_json_response({'ok': True, 'reports': [_profile_report_dict(r) for r in rows], 'status': status, 'query': q, 'limit': limit})
        except Exception as e:
            return _admin_operation_error('admin_profile_reports', e)


    @app.route('/admin/profile_reports/<int:report_id>/dismiss', methods=['POST'])
    @require_permission('profile:moderate')
    @require_recent_admin_auth
    def admin_dismiss_profile_report(report_id):
        _ensure_admin_profile_runtime_schema()
        actor = _actor()
        note = str(request.form.get('reason') or request.form.get('note') or 'Dismissed by admin').strip()[:500]
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE profile_post_reports
                       SET status = 'dismissed', reviewed_by = %s, reviewed_at = CURRENT_TIMESTAMP,
                           action_taken = %s, updated_at = CURRENT_TIMESTAMP
                     WHERE id = %s
                       AND status = 'open'
                    RETURNING reporter_username, target_username, post_id, COALESCE(comment_id, 0);
                    """,
                    (actor, note, int(report_id)),
                )
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found_or_already_reviewed'}, 404)
            conn.commit()
            reporter = str(row[0] or '')
            target = str(row[1] or '')
            post_id = int(row[2] or 0)
            comment_id = int(row[3] or 0)
            _profile_report_update_notice(reporter, 'Your profile report was reviewed and dismissed.', {
                'report_id': int(report_id), 'post_id': post_id, 'comment_id': comment_id, 'status': 'dismissed',
            })
            log_audit_event(actor, 'admin_profile_report_dismiss', target, f'report_id={int(report_id)} post_id={post_id}')
            return _admin_json_response({'ok': True, 'status': 'dismissed', 'report_id': int(report_id)})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error('admin_profile_report_dismiss', e)


    @app.route('/admin/profile_reports/<int:report_id>/warn', methods=['POST'])
    @require_permission('profile:moderate')
    @require_recent_admin_auth
    def admin_warn_profile_report_target(report_id):
        _ensure_admin_profile_runtime_schema()
        actor = _actor()
        reason = str(request.form.get('reason') or 'Profile content warning').strip()[:500]
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT reporter_username, target_username, post_id, COALESCE(comment_id, 0) FROM profile_post_reports WHERE id = %s AND status = 'open' LIMIT 1 FOR UPDATE;", (int(report_id),))
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found_or_already_reviewed'}, 404)
                reporter = str(row[0] or '')
                target = str(row[1] or '')
                post_id = int(row[2] or 0)
                comment_id = int(row[3] or 0)
                cur.execute(
                    """
                    UPDATE profile_post_reports
                       SET status = 'actioned', reviewed_by = %s, reviewed_at = CURRENT_TIMESTAMP,
                           action_taken = %s, updated_at = CURRENT_TIMESTAMP
                     WHERE id = %s
                       AND status = 'open';
                    """,
                    (actor, f'warned: {reason}', int(report_id)),
                )
                if getattr(cur, 'rowcount', 0) != 1:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found_or_already_reviewed'}, 404)
            conn.commit()
            _profile_admin_notice(target, f'Admin warning about profile content: {reason}', {'post_id': post_id, 'comment_id': comment_id})
            _profile_report_update_notice(reporter, 'Your profile report was reviewed and an admin warning was issued.', {
                'report_id': int(report_id), 'post_id': post_id, 'comment_id': comment_id, 'status': 'actioned',
            })
            log_audit_event(actor, 'admin_profile_report_warn', target, f'report_id={int(report_id)} reason={reason}')
            return _admin_json_response({'ok': True, 'status': 'warned', 'report_id': int(report_id), 'target_username': target})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error('admin_profile_report_warn', e)


    @app.route('/admin/profile_reports/<int:report_id>/delete_content', methods=['POST'])
    @require_permission('profile:moderate')
    @require_recent_admin_auth
    def admin_delete_profile_report_content(report_id):
        _ensure_admin_profile_runtime_schema()
        actor = _actor()
        reason = str(request.form.get('reason') or 'Removed after user report').strip()[:500]
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT reporter_username, target_username, post_id, COALESCE(comment_id, 0) FROM profile_post_reports WHERE id = %s AND status = 'open' LIMIT 1 FOR UPDATE;", (int(report_id),))
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found_or_already_reviewed'}, 404)
                reporter = str(row[0] or '')
                target = str(row[1] or '')
                post_id = int(row[2] or 0)
                comment_id = int(row[3] or 0)
                if comment_id > 0:
                    cur.execute(
                        """
                        UPDATE profile_post_comments
                           SET deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
                               updated_at = CURRENT_TIMESTAMP,
                               deleted_by = %s,
                               deleted_reason = %s
                         WHERE id = %s AND post_id = %s;
                        """,
                        (actor, reason, comment_id, post_id),
                    )
                    action = 'removed_comment'
                else:
                    cur.execute(
                        """
                        UPDATE profile_posts
                           SET deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
                               updated_at = CURRENT_TIMESTAMP,
                               is_pinned = FALSE,
                               is_featured = FALSE,
                               moderated_by = %s,
                               moderated_reason = %s,
                               moderated_at = CURRENT_TIMESTAMP
                         WHERE id = %s;
                        """,
                        (actor, reason, post_id),
                    )
                    action = 'removed_post'
                cur.execute(
                    """
                    UPDATE profile_post_reports
                       SET status = 'actioned', reviewed_by = %s, reviewed_at = CURRENT_TIMESTAMP,
                           action_taken = %s, updated_at = CURRENT_TIMESTAMP
                     WHERE id = %s
                       AND status = 'open';
                    """,
                    (actor, f'{action}: {reason}', int(report_id)),
                )
                if getattr(cur, 'rowcount', 0) != 1:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found_or_already_reviewed'}, 404)
            conn.commit()
            _profile_admin_notice(target, f'An admin removed reported profile content: {reason}', {
                'post_id': post_id, 'comment_id': comment_id, 'report_id': int(report_id), 'action': action,
            })
            _profile_report_update_notice(reporter, 'Your profile report was reviewed and the reported profile content was removed.', {
                'report_id': int(report_id), 'post_id': post_id, 'comment_id': comment_id, 'status': 'actioned', 'action': action,
            })
            log_audit_event(actor, 'admin_profile_report_delete_content', target, f'report_id={int(report_id)} post_id={post_id} comment_id={comment_id} reason={reason}')
            return _admin_json_response({'ok': True, 'status': action, 'report_id': int(report_id), 'post_id': post_id, 'comment_id': comment_id})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error('admin_profile_report_delete_content', e)


    # ── Profile badge management ───────────────────────────────
    def _normalize_profile_badge_key(raw: str) -> str:
        value = re.sub(r'[^a-z0-9_:-]', '', str(raw or '').strip().lower().replace(' ', '_'))[:40]
        return value


    @app.route('/admin/profile_badges/<path:username>', methods=['GET'])
    @require_permission('profile:moderate')
    def admin_profile_badges(username):
        _ensure_admin_profile_runtime_schema()
        username = str(username or '').strip()[:64]
        if not username:
            return jsonify({'ok': False, 'error': 'missing_username'}), 400
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute('SELECT username FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;', (username,))
                urow = cur.fetchone()
                if not urow:
                    return _admin_json_response({'ok': False, 'error': 'not_found'}, 404)
                canonical = str(urow[0])
                cur.execute(
                    """
                    SELECT badge_key, label, assigned_by, reason, created_at
                      FROM user_profile_badges
                     WHERE LOWER(username) = LOWER(%s)
                     ORDER BY created_at DESC, id DESC;
                    """,
                    (canonical,),
                )
                badges = [
                    {
                        'key': str(r[0] or ''),
                        'badge_key': str(r[0] or ''),
                        'label': str(r[1] or ''),
                        'assigned_by': str(r[2] or ''),
                        'reason': str(r[3] or ''),
                        'created_at': r[4].isoformat() if hasattr(r[4], 'isoformat') else str(r[4] or ''),
                    }
                    for r in (cur.fetchall() or [])
                ]
            return jsonify({'ok': True, 'username': canonical, 'badges': badges})
        except Exception as e:
            return _admin_operation_error('admin_profile_badges', e)


    @app.route('/admin/profile_badges/<path:username>', methods=['POST'])
    @require_permission('profile:moderate')
    @require_recent_admin_auth
    def admin_assign_profile_badge(username):
        _ensure_admin_profile_runtime_schema()
        actor = _actor()
        username = str(username or '').strip()[:64]
        key = _normalize_profile_badge_key(request.form.get('badge_key') or request.form.get('key') or '')
        label = str(request.form.get('label') or key.replace('_', ' ').title()).strip()[:40]
        reason = str(request.form.get('reason') or 'Assigned by admin').strip()[:300]
        if not username or not key or not label:
            return jsonify({'ok': False, 'error': 'missing_badge'}), 400
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT username FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;', (username,))
                urow = cur.fetchone()
                if not urow:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found'}, 404)
                canonical = str(urow[0])
                cur.execute(
                    """
                    INSERT INTO user_profile_badges (username, badge_key, label, assigned_by, reason)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (username, badge_key)
                    DO UPDATE SET label = EXCLUDED.label, assigned_by = EXCLUDED.assigned_by, reason = EXCLUDED.reason, created_at = CURRENT_TIMESTAMP
                    RETURNING badge_key, label;
                    """,
                    (canonical, key, label, actor, reason),
                )
                row = cur.fetchone()
            conn.commit()
            log_audit_event(actor, 'admin_profile_badge_assign', canonical, f'badge={key} reason={reason}')
            return jsonify({'ok': True, 'username': canonical, 'badge': {'key': str(row[0] or key), 'label': str(row[1] or label)}})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error('admin_profile_badge_assign', e)


    @app.route('/admin/profile_badges/<path:username>/<path:badge_key>/delete', methods=['POST'])
    @require_permission('profile:moderate')
    @require_recent_admin_auth
    def admin_remove_profile_badge(username, badge_key):
        _ensure_admin_profile_runtime_schema()
        actor = _actor()
        username = str(username or '').strip()[:64]
        key = _normalize_profile_badge_key(badge_key)
        reason = str(request.form.get('reason') or 'Removed by admin').strip()[:300]
        if not username or not key:
            return jsonify({'ok': False, 'error': 'missing_badge'}), 400
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT username FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;', (username,))
                urow = cur.fetchone()
                if not urow:
                    conn.rollback()
                    return _admin_json_response({'ok': False, 'error': 'not_found'}, 404)
                canonical = str(urow[0])
                cur.execute('DELETE FROM user_profile_badges WHERE LOWER(username) = LOWER(%s) AND badge_key = %s;', (canonical, key))
            conn.commit()
            log_audit_event(actor, 'admin_profile_badge_remove', canonical, f'badge={key} reason={reason}')
            return jsonify({'ok': True, 'username': canonical, 'badge_key': key})
        except Exception as e:
            conn.rollback()
            return _admin_operation_error('admin_profile_badge_remove', e)


    # ── Diagnostics: list admin routes (admin only) ───────────
    @app.route('/admin/_routes', methods=['GET'])
    @require_permission('admin:basic')
    def admin_list_routes():
        routes = []
        try:
            for r in app.url_map.iter_rules():
                if r.rule.startswith('/admin') or r.rule.startswith('/api/admin'):
                    methods = sorted([m for m in (r.methods or set()) if m not in {'HEAD','OPTIONS'}])
                    routes.append({'rule': r.rule, 'methods': methods})
        except Exception:
            pass
        routes.sort(key=lambda x: x['rule'])
        return jsonify({'ok': True, 'routes': routes})

    # ── Alias rules to eliminate admin 404s across UI/server versions ─
    # We automatically mirror any newly-registered /admin/* routes from
    # this module under /api/admin/* as well, and add a few extra
    # compatibility paths for room controls.
    def _ecap_add_alias(rule_src: str, rule_dst: str, endpoint: str):
        try:
            existing = {r.rule for r in app.url_map.iter_rules()}
            if rule_dst in existing:
                return
            vf = app.view_functions.get(endpoint)
            if not vf:
                return
            # Try to reuse method set from the source rule
            methods = None
            defaults = None
            for r in app.url_map.iter_rules():
                if r.rule == rule_src and r.endpoint == endpoint:
                    methods = sorted([m for m in (r.methods or set()) if m not in {'HEAD','OPTIONS'}])
                    defaults = r.defaults
                    break
            if not methods:
                for r in app.url_map.iter_rules():
                    if r.endpoint == endpoint:
                        methods = sorted([m for m in (r.methods or set()) if m not in {'HEAD','OPTIONS'}])
                        break
            if not methods:
                # Unknown admin aliases fail closed to POST-only. Never introduce
                # GET access to mutating admin handlers by compatibility fallback.
                methods = ['POST']
            app.add_url_rule(
                rule_dst,
                endpoint=f'ecap_alias_{endpoint}_{abs(hash(rule_dst))}',
                view_func=vf,
                methods=methods,
                defaults=defaults,
            )
        except Exception:
            return

    # Determine which endpoints were added by this register() call
    _ecap_post_endpoints = set(app.view_functions.keys())
    _ecap_new_endpoints = _ecap_post_endpoints - _ecap_pre_endpoints

    # 1) Mirror /admin/* -> /api/admin/* for all new endpoints
    try:
        for r in list(app.url_map.iter_rules()):
            if r.endpoint not in _ecap_new_endpoints:
                continue
            if not r.rule.startswith('/admin/'):
                continue
            dst = '/api' + r.rule
            _ecap_add_alias(r.rule, dst, r.endpoint)
    except Exception:
        pass

    # 2) Extra compatibility for room controls (common alternate URL shapes)
    _room_aliases = [
        ('/admin/lock_room/<room>', '/admin/rooms/lock/<path:room>', 'lock_room'),
        ('/admin/unlock_room/<room>', '/admin/rooms/unlock/<path:room>', 'unlock_room'),
        ('/admin/set_room_readonly/<room>', '/admin/rooms/readonly/<path:room>', 'set_room_readonly'),
        ('/admin/set_room_readonly/<room>', '/admin/rooms/read_only/<path:room>', 'set_room_readonly'),
        ('/admin/set_room_slowmode/<room>', '/admin/rooms/slowmode/<path:room>', 'set_room_slowmode'),
        ('/admin/clear_room/<room>', '/admin/rooms/clear/<path:room>', 'clear_room'),
        ('/admin/rooms/delete/<path:room>', '/admin/rooms/delete/<path:room>', 'admin_room_delete'),
        ('/admin/delete_room/<room>', '/admin/rooms/delete/<path:room>', 'admin_room_delete'),
    ]
    for src, dst, ep in _room_aliases:
        _ecap_add_alias(src, dst, ep)
        _ecap_add_alias(src, '/api' + dst, ep)
