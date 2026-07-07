# rate_limiter_unavailable
#!/usr/bin/env python3
"""routes_main.py

General (non-auth) routes.

Changes in this update:
  - Removed duplicate /chat and /api/rooms routes (single /chat lives in routes_auth.py;
    rooms API lives in routes_chat.py).
  - Removed JSON-backed room endpoints (PostgreSQL is the single source of truth).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
import ipaddress
import urllib.parse
import urllib.request
import socket
import ssl
import mimetypes
import math
import hashlib
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

from flask import jsonify, request, send_file, redirect, abort, make_response
from flask_jwt_extended import get_jwt_identity, get_jwt, jwt_required, verify_jwt_in_request, unset_jwt_cookies
from werkzeug.utils import secure_filename

from database import get_db, get_friends_for_user, get_auth_session_state, revoke_auth_session, touch_auth_session_activity, get_custom_room_meta, can_user_access_custom_room, ensure_users_profile_columns, ensure_profile_post_engagement_schema
from security import log_audit_event, parse_rate_limit_value, simple_rate_limit, get_request_ip, safe_existing_file_under, sanitize_user_visible_text, apply_safe_download_headers
from moderation import is_user_sanctioned
from emoticon_catalog import emoticon_catalog, local_emoticon_root, local_emoticon_roots
from permissions import get_user_permissions
from realtime.state import shared_state_summary
from health_status import build_health_payload, normalize_public_probe_path
from sensitive_fields_crypto import decrypt_sensitive_field


def register_main_routes(app, settings, socketio):
    upload_folder = os.path.join(app.static_folder or "www", "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    profile_avatar_folder = os.path.join(upload_folder, "profile_avatars")
    os.makedirs(profile_avatar_folder, exist_ok=True)
    profile_banner_folder = os.path.join(upload_folder, "profile_banners")
    os.makedirs(profile_banner_folder, exist_ok=True)
    profile_post_folder = os.path.join(upload_folder, "profile_posts")
    os.makedirs(profile_post_folder, exist_ok=True)
    # Server-hosted code-based emoticon assets. Admins can place files such as
    # emoticons/1.gif in the project root; the catalog uses matching names.
    try:
        os.makedirs(local_emoticon_root(settings), exist_ok=True)
        for _emo_root in local_emoticon_roots(settings):
            if _emo_root.name == "emoticons" and str(_emo_root).endswith("/emoticons"):
                _emo_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    max_profile_avatar_bytes = int(settings.get("max_profile_avatar_bytes") or (5 * 1024 * 1024))
    max_profile_banner_bytes = int(settings.get("max_profile_banner_bytes") or (8 * 1024 * 1024))
    max_profile_post_image_bytes = int(settings.get("max_profile_post_image_bytes") or (8 * 1024 * 1024))
    allow_svg_avatars = bool(settings.get("allow_svg_avatars", False))
    # Retired compatibility route. Keep /upload registered so old clients get a
    # clear 410 response, but do not allow config/env toggles to re-enable the
    # old public upload path.
    enable_legacy_public_uploads = False
    max_legacy_public_upload_bytes = int(settings.get("max_legacy_public_upload_bytes") or max(max_profile_avatar_bytes, 10 * 1024 * 1024))
    legacy_public_upload_folder = os.path.join(upload_folder, "legacy_public")
    os.makedirs(legacy_public_upload_folder, exist_ok=True)

    _profile_runtime_schema_checked = {"ok": False}

    def _ensure_profile_runtime_schema() -> None:
        """Patch profile-related tables/columns before profile API queries.

        This keeps upgraded servers from throwing 500s when the browser hits a
        profile endpoint before the admin has run the newest migration. The
        tracked migration remains the canonical install path; this helper is a
        defensive runtime guard for legacy/local development databases.
        """
        if _profile_runtime_schema_checked.get("ok"):
            return
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT to_regclass('public.profile_posts'),
                           to_regclass('public.user_profile_notification_settings'),
                           to_regclass('public.notifications');
                    """
                )
                row = cur.fetchone() or (None, None, None)
                if not all(row):
                    raise RuntimeError(
                        "Profile schema is missing required tables. Run `python main.py --migrate` before serving requests."
                    )
                cur.execute(
                    """
                    SELECT 1
                      FROM information_schema.columns
                     WHERE table_schema='public'
                       AND table_name='users'
                       AND column_name='profile_post_default_visibility'
                     LIMIT 1;
                    """
                )
                if cur.fetchone() is None:
                    raise RuntimeError(
                        "Profile schema is missing users.profile_post_default_visibility. Run `python main.py --migrate`."
                    )
            _profile_runtime_schema_checked["ok"] = True
        except Exception:
            logging.exception("Profile runtime schema check failed")
            raise


    _PROFILE_NOTIFICATION_DEFAULTS = {
        "notify_likes": True,
        "notify_comments": True,
        "notify_admin_notices": True,
        "notify_report_updates": True,
        "notify_profile_views": False,
        "notify_friend_posts": True,
    }


    def _ensure_profile_notification_settings_schema() -> None:
        """Ensure profile notification tables through the migration-backed schema helper.

        Inline route-level CREATE/ALTER statements were removed so normal HTTP
        requests do not race through DDL in multi-instance deployments.
        """
        ensure_profile_post_engagement_schema()



    def _coerce_profile_notification_bool(value, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "y"}:
            return True
        if text in {"0", "false", "no", "off", "n"}:
            return False
        return bool(default)


    def _emoticon_catalog_cache_seconds() -> int:
        try:
            return max(0, min(31536000, int(settings.get("emoticons_catalog_cache_seconds", 86400) or 0)))
        except Exception:
            return 86400


    def _safe_emoticon_file_path(root, name: str) -> Path | None:
        """Return a pathlib Path for safe existing emoticon assets.

        The shared safe_existing_file_under() helper intentionally returns a
        string because older download routes pass that value straight to
        send_file().  The emoticon routes need pathlib methods too, so convert
        the safe result here before checking/serving it.
        """
        safe_path = safe_existing_file_under(root, name)
        if not safe_path:
            return None
        candidate = Path(safe_path)
        return candidate if candidate.is_file() else None


    @app.get("/api/emoticons/catalog")
    def api_emoticons_catalog():
        """Return the neutral code-based emoticon catalog for the chat GUI.

        The chat client asks for this URL with the application version in the
        query string.  Returning cacheable JSON avoids a guaranteed server hit
        every time a user opens /chat, while the ETag keeps normal reloads
        safe and cheap if the browser decides to revalidate.
        """
        payload = {"success": True, **emoticon_catalog(settings)}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        etag = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        cache_seconds = _emoticon_catalog_cache_seconds()

        if request.if_none_match.contains(etag):
            resp = make_response("", 304)
        else:
            resp = make_response(canonical + "\n")
            resp.mimetype = "application/json"
        resp.set_etag(etag)
        if cache_seconds > 0:
            resp.headers["Cache-Control"] = f"private, max-age={cache_seconds}, stale-while-revalidate={cache_seconds}"
        else:
            resp.headers["Cache-Control"] = "no-cache, max-age=0"
        return resp


    @app.get("/api/emoticons/selftest")
    def api_emoticons_selftest():
        """Tiny non-sensitive asset/codes health check for the picker."""
        payload = emoticon_catalog(settings)
        entries = payload.get("entries") or []
        first = next((e for e in entries if ":)" in (e.get("codes") or [])), None)
        laugh = next((e for e in entries if ":))" in (e.get("codes") or [])), None)
        roots = local_emoticon_roots(settings)

        def _exists(name: str) -> list[str]:
            hits = []
            for root in roots:
                path = _safe_emoticon_file_path(root, name)
                if path:
                    hits.append(str(path))
            return hits

        checks = {
            "catalog_enabled": bool(payload.get("enabled")),
            "entries": int(payload.get("count") or len(entries)),
            "code_count": int(payload.get("code_count") or 0),
            "asset_mode": payload.get("asset_mode"),
            "external_enabled": bool(payload.get("external_enabled")),
            "external_asset_base_url": payload.get("external_asset_base_url"),
            "local_roots_checked": [str(root) for root in roots],
            "smile_src": (first or {}).get("src"),
            "laugh_src": (laugh or {}).get("src"),
            "smile_file_hits": _exists("1.gif"),
            "laugh_file_hits": _exists("21.gif"),
            "thumbup_file_hits": _exists("113.gif"),
        }
        ok = all([
            checks["catalog_enabled"],
            checks["entries"] > 0,
            bool(checks["smile_file_hits"]),
            bool(checks["laugh_file_hits"]),
        ]) or bool(checks["external_enabled"])
        resp = jsonify({"success": ok, "checks": checks})
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp


    @app.get("/emoticons/<path:filename>")
    def serve_local_emoticon(filename: str):
        """Serve local emoticon images from safe project roots."""
        if not bool(settings.get("emoticons_enabled", True)) or not bool(settings.get("emoticons_local_enabled", True)):
            abort(404)
        safe_name = str(filename or "").strip().replace("\\", "/")
        if "/" in safe_name or safe_name.startswith("."):
            abort(404)
        ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if ext not in {"gif", "webp", "png", "jpg", "jpeg"}:
            abort(404)
        for root in local_emoticon_roots(settings):
            path = _safe_emoticon_file_path(root, safe_name)
            if path:
                resp = make_response(send_file(path, mimetype=mimetypes.guess_type(str(path))[0] or "application/octet-stream", conditional=True))
                if request.args.get("v"):
                    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                else:
                    resp.headers["Cache-Control"] = "public, max-age=604800"
                resp.headers["X-Content-Type-Options"] = "nosniff"
                resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
                return resp
        abort(404)


    def _profile_notification_settings_for(username: str) -> dict:
        username = str(username or "").strip()
        settings_payload = dict(_PROFILE_NOTIFICATION_DEFAULTS)
        if not username:
            return settings_payload
        _ensure_profile_notification_settings_schema()
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_profile_notification_settings (username)
                VALUES (%s)
                ON CONFLICT (username) DO NOTHING;
                """,
                (username,),
            )
            cur.execute(
                """
                SELECT notify_likes, notify_comments, notify_admin_notices,
                       notify_report_updates, notify_profile_views, notify_friend_posts
                  FROM user_profile_notification_settings
                 WHERE username = %s
                 LIMIT 1;
                """,
                (username,),
            )
            row = cur.fetchone()
        conn.commit()
        if row:
            for idx, key in enumerate(_PROFILE_NOTIFICATION_DEFAULTS.keys()):
                settings_payload[key] = bool(row[idx])
        return settings_payload


    def _profile_notification_enabled(username: str, kind: str) -> bool:
        kind = str(kind or "").strip().lower()
        settings_payload = _profile_notification_settings_for(username)
        if kind == "profile_post_like":
            return bool(settings_payload.get("notify_likes", True))
        if kind == "profile_post_comment":
            return bool(settings_payload.get("notify_comments", True))
        if kind in {"profile_post_warning", "profile_post_notice", "profile_admin_notice"}:
            return bool(settings_payload.get("notify_admin_notices", True))
        if "report" in kind:
            return bool(settings_payload.get("notify_report_updates", True))
        if "view" in kind:
            return bool(settings_payload.get("notify_profile_views", False))
        if "friend" in kind:
            return bool(settings_payload.get("notify_friend_posts", True))
        return True

    def _private_file_bool_setting(name: str, default: bool = False) -> bool:
        value = settings.get(name, default)
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled", ""}:
            return False
        return bool(default)

    def _private_file_int_setting(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
        try:
            value = int(settings.get(name, default))
        except Exception:
            value = int(default)
        value = max(int(minimum), value)
        if maximum is not None:
            value = min(int(maximum), value)
        return value

    # Encrypted DM file storage (NOT publicly served)
    dm_upload_root = str(Path(settings.get("dm_upload_root") or os.path.join(os.getcwd(), "uploads", "dm_files")).expanduser().resolve())
    os.makedirs(dm_upload_root, exist_ok=True)
    max_dm_file_bytes = _private_file_int_setting("max_dm_file_bytes", 10 * 1024 * 1024, minimum=1, maximum=512 * 1024 * 1024)
    # Encrypted Group file storage (NOT publicly served)
    group_upload_root = str(Path(settings.get("group_upload_root") or os.path.join(os.getcwd(), "uploads", "group_files")).expanduser().resolve())
    os.makedirs(group_upload_root, exist_ok=True)
    max_group_file_bytes = _private_file_int_setting("max_group_upload_bytes", settings.get("max_group_file_bytes", max_dm_file_bytes), minimum=1, maximum=1024 * 1024 * 1024)
    disable_dm_files_globally = (
        _private_file_bool_setting("disable_dm_files_globally", False)
        or _private_file_bool_setting("disable_file_transfer_globally", False)
    )
    disable_group_files_globally = (
        _private_file_bool_setting("disable_group_files_globally", False)
        or _private_file_bool_setting("disable_file_transfer_globally", False)
    )
    max_user_file_storage_bytes = _private_file_int_setting("max_user_file_storage_bytes", 250 * 1024 * 1024, minimum=0, maximum=1024 * 1024 * 1024 * 1024)
    max_user_torrent_storage_bytes = _private_file_int_setting("max_user_torrent_storage_bytes", 25 * 1024 * 1024, minimum=0, maximum=1024 * 1024 * 1024)
    max_torrent_total_size_bytes = _private_file_int_setting("max_torrent_total_size_bytes", 1024 * 1024 * 1024 * 1024, minimum=0, maximum=1024 * 1024 * 1024 * 1024 * 1024)

    # ------------------------------------------------------------------
    # Local helper: Flask-Limiter decorator (no-op if Limiter is not active)
    # ------------------------------------------------------------------
    _limiter = (app.extensions.get("limiter")
               or app.extensions.get("flask_limiter")
               or app.extensions.get("flask-limiter"))

    def _limit(rule: str):
        """Decorate a route with a rate limit if Limiter is initialized."""
        if _limiter is not None:
            try:
                return _limiter.limit(rule)
            except Exception:
                # If Limiter is misconfigured, fail open rather than breaking boot.
                pass
        def _decorator(fn):
            return fn
        return _decorator

    def _route_rate_limit_guard(scope: str, cfg_value, *, default_limit: int, default_window: int, user: str | None = None):
        ident = str(user or get_request_ip(request) or request.remote_addr or 'anon').strip() or 'anon'
        limit, window = parse_rate_limit_value(cfg_value, default_limit=default_limit, default_window=default_window)
        ok, retry = simple_rate_limit(f'route:{scope}:{ident}', limit=limit, window_sec=window)
        if ok:
            return None
        return _no_store_json({"success": False, "error": "Rate limited", "retry_after": retry}, 429)

    def _safe_int_setting(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
        try:
            value = int(settings.get(name, default))
        except Exception:
            value = int(default)
        if minimum is not None:
            value = max(int(minimum), value)
        if maximum is not None:
            value = min(int(maximum), value)
        return value

    def _safe_float_setting(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
        try:
            value = float(settings.get(name, default))
        except Exception:
            value = float(default)
        if minimum is not None:
            value = max(float(minimum), value)
        if maximum is not None:
            value = min(float(maximum), value)
        return value

    def _no_store_json(payload: dict, status: int = 200):
        resp = jsonify(payload)
        resp.status_code = int(status)
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    def _private_file_json(payload: dict, status: int = 200):
        """No-store JSON helper for encrypted file upload/meta failures and results."""
        return _no_store_json(payload, status)


    def _valid_private_file_id(file_id: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-f]{32}", str(file_id or "")))

    def _base64ish(value: str, *, max_len: int = 32768) -> bool:
        value = str(value or "").strip()
        if not value or len(value) > max_len:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9+/=_-]+", value))

    def _sanitize_private_file_name(raw: str | None, *, default: str = "file.bin", max_len: int = 180) -> str:
        safe = secure_filename(str(raw or "").strip()) or default
        if len(safe) <= max_len:
            return safe
        stem, ext = os.path.splitext(safe)
        ext = ext[:24]
        keep = max(1, int(max_len) - len(ext))
        return (stem[:keep] + ext) or default

    def _sanitize_private_file_mime(raw: str | None) -> str:
        text = str(raw or "application/octet-stream").strip().lower()
        if not text or len(text) > 128 or "\r" in text or "\n" in text:
            return "application/octet-stream"
        if not re.fullmatch(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}", text):
            return "application/octet-stream"
        return text

    def _sanitize_private_file_sha256(raw: str | None) -> str | None:
        text = str(raw or "").strip().lower()
        if not text:
            return None
        return text if re.fullmatch(r"[0-9a-f]{64}", text) else None

    def _private_file_upload_denial(username: str, *, send_context: bool = True) -> tuple[dict, int] | None:
        """Central account-sanction gate for all private/file-transfer uploads.

        The dedicated ``upload`` sanction must apply to the newer ciphertext-only
        DM/group file APIs too, not just legacy uploads/profile media/torrents.
        ``send_context`` also applies normal chat send sanctions for DM/group
        file cards because the upload creates a user-visible file message.
        """
        actor = str(username or "").strip()
        if not actor:
            return ({"success": False, "error": "Invalid user"}, 403)
        if is_user_sanctioned(actor, "ban"):
            return ({"success": False, "error": "You are banned."}, 403)
        if is_user_sanctioned(actor, "upload"):
            return ({"success": False, "error": "Uploads are disabled for this account"}, 403)
        if send_context and is_user_sanctioned(actor, "mute"):
            return ({"success": False, "error": "You are muted."}, 403)
        return None

    def _same_username(a: str | None, b: str | None) -> bool:
        return str(a or "").strip().lower() == str(b or "").strip().lower() and bool(str(a or "").strip())

    def _dm_file_key_for_user(sender: str, receiver: str, ek_to_b64: str, ek_from_b64: str, username: str) -> str | None:
        """Return the wrapped DM file key for this participant, or None.

        Blob access now uses this too, so malformed/corrupt DM file metadata
        cannot serve ciphertext to a participant who lacks a valid wrapped key.
        Comparisons are case-insensitive to match the rest of the auth/RBAC work.
        """
        if _same_username(username, receiver):
            return str(ek_to_b64).strip() if _base64ish(str(ek_to_b64), max_len=32768) else None
        if _same_username(username, sender):
            return str(ek_from_b64).strip() if _base64ish(str(ek_from_b64), max_len=32768) else None
        return None

    def _participants_blocked(a: str, b: str) -> bool:
        if _same_username(a, b):
            return False
        return bool(_either_blocked(a, b))

    def _current_private_file_storage_bytes(username: str) -> int:
        """Best-effort per-sender private-file storage total for quota checks."""
        username = str(username or "").strip()
        if not username:
            return 0
        total = 0
        conn = get_db()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "SELECT COALESCE(SUM(file_size), 0) FROM dm_files WHERE sender=%s AND COALESCE(revoked, FALSE)=FALSE;",
                        (username,),
                    )
                    total += int((cur.fetchone() or [0])[0] or 0)
                except Exception:
                    pass
                try:
                    cur.execute(
                        "SELECT COALESCE(SUM(file_size), 0) FROM group_files WHERE sender=%s AND COALESCE(revoked, FALSE)=FALSE;",
                        (username,),
                    )
                    total += int((cur.fetchone() or [0])[0] or 0)
                except Exception:
                    pass
        except Exception:
            logging.exception("[UPLOAD] failed to compute private file quota for %s", username)
        return max(0, total)

    def _private_file_quota_response(username: str, new_size: int):
        if max_user_file_storage_bytes <= 0:
            return None
        current = _current_private_file_storage_bytes(username)
        if current + int(new_size or 0) <= max_user_file_storage_bytes:
            return None
        try:
            log_audit_event(username, "file_quota_denied", username, f"current={current} attempted={new_size} limit={max_user_file_storage_bytes}")
        except Exception:
            pass
        return _private_file_json({
            "success": False,
            "error": "Storage quota exceeded",
            "limit": max_user_file_storage_bytes,
            "used": current,
        }, 413)

    def _current_torrent_storage_bytes(username: str) -> int:
        username = str(username or "").strip().lower()
        if not username:
            return 0
        total = 0
        try:
            for meta_path in Path(torrents_root).glob("*.meta.json"):
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if str(data.get("owner") or "").strip().lower() == username:
                    total += int(data.get("size") or 0)
        except Exception:
            logging.exception("[TORRENT] failed to compute torrent quota for %s", username)
        return max(0, total)

    def _torrent_quota_response(username: str, new_size: int):
        if max_user_torrent_storage_bytes <= 0:
            return None
        current = _current_torrent_storage_bytes(username)
        if current + int(new_size or 0) <= max_user_torrent_storage_bytes:
            return None
        try:
            log_audit_event(username, "torrent_quota_denied", username, f"current={current} attempted={new_size} limit={max_user_torrent_storage_bytes}")
        except Exception:
            pass
        return _no_store_json({
            "success": False,
            "error": "Torrent storage quota exceeded",
            "limit": max_user_torrent_storage_bytes,
            "used": current,
        }, 413)

    def _save_filestorage_limited(file_storage, storage_path: str, max_bytes: int, *, chunk_size: int = 1024 * 1024) -> int:
        """Stream an uploaded file to disk and abort once max_bytes is exceeded.

        Werkzeug/Flask request limits prevent very large whole requests, but this
        helper keeps endpoint-specific limits honest even when Content-Length is
        missing or a reverse proxy streams the body. It also avoids reading entire
        uploads into memory.
        """
        max_bytes = max(1, int(max_bytes or 1))
        total = 0
        try:
            with open(storage_path, "wb") as out:
                stream = getattr(file_storage, "stream", None) or file_storage
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("file_too_large")
                    out.write(chunk)
            return total
        except Exception:
            try:
                if os.path.exists(storage_path):
                    os.remove(storage_path)
            except Exception:
                pass
            raise

    def _resolve_idle_logout_seconds() -> float | None:
        idle_hours = settings.get("idle_logout_hours", 8)
        try:
            idle_hours = float(idle_hours) if idle_hours is not None else 8.0
        except Exception:
            idle_hours = 8.0
        return (idle_hours * 3600.0) if idle_hours and idle_hours > 0 else None

    def _main_session_failure_response(error: str):
        reason = str(error or "session_revoked").strip() or "session_revoked"
        resp = _no_store_json({"success": False, "error": reason}, 401)
        try:
            unset_jwt_cookies(resp)
        except Exception:
            pass
        return resp, 401

    def _require_live_main_session(*, touch_activity: bool = False, allow_missing_jwt: bool = False):
        try:
            verify_jwt_in_request(optional=allow_missing_jwt)
        except Exception:
            if allow_missing_jwt:
                return None, None, None
            return None, None, _main_session_failure_response("unauthorized")

        claims = get_jwt() or {}
        sid = str(claims.get("sid") or "").strip()
        username = str(get_jwt_identity() or "").strip().lower()

        if not username or not sid:
            if allow_missing_jwt:
                return None, None, None
            return None, None, _main_session_failure_response("no_session")

        try:
            state = get_auth_session_state(sid)
        except Exception:
            return None, None, _main_session_failure_response("session_check_failed")

        if state is None or state.get("revoked_at") is not None:
            return None, None, _main_session_failure_response("session_revoked")

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
                    return None, None, _main_session_failure_response("idle_timeout")

        try:
            if touch_activity:
                touch_auth_session_activity(sid)
        except Exception:
            return None, None, _main_session_failure_response("session_touch_failed")

        return sid, state, None

    def _main_path_requires_live_session(path: str) -> bool:
        path = str(path or "")
        return (
            path == "/upload"
            or path == "/api/friends"
            or path.startswith("/api/profile/")
            or path.startswith("/media/profile-posts/")
            or path.startswith("/api/gifs/")
            or path.startswith("/api/dm_files/")
            or path.startswith("/api/group_files/")
            or path.startswith("/api/torrents/")
            or path == "/api/torrent/scrape"
        )

    @app.before_request
    def _enforce_live_main_route_session():
        if not _main_path_requires_live_session(request.path):
            return None
        _sid, _state, rejection = _require_live_main_session(touch_activity=True, allow_missing_jwt=True)
        if rejection is not None:
            return rejection
        return None


    # ------------------------------------------------------------------
    # Torrent scrape (swarm stats) tuning
    #
    # Tracker scrapes can be slow/unreliable; we keep the endpoint fast and
    # cache results briefly to avoid repeated outbound requests.
    # ------------------------------------------------------------------
    _TORRENT_SCRAPE_CACHE: dict[str, tuple[float, int | None, int | None, int | None, str, int]] = {}
    _TORRENT_SCRAPE_CACHE_TTL = _safe_float_setting("torrent_scrape_cache_ttl_sec", 120.0, minimum=0.0, maximum=3600.0)
    _TORRENT_SCRAPE_MAX_TRIES = _safe_int_setting("torrent_scrape_max_tries", 3, minimum=0, maximum=6)
    _TORRENT_SCRAPE_MAX_TRACKERS = _safe_int_setting("torrent_scrape_max_trackers", 6, minimum=0, maximum=12)
    _TORRENT_SCRAPE_HTTP_TIMEOUT = _safe_float_setting("torrent_scrape_http_timeout_sec", 1.5, minimum=0.2, maximum=10.0)
    _TORRENT_SCRAPE_UDP_TIMEOUT = _safe_float_setting("torrent_scrape_udp_timeout_sec", 1.5, minimum=0.2, maximum=10.0)
    _TORRENT_PUBLIC_FALLBACK_TRACKERS = [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://tracker.moeking.me:6969/announce",
        "https://tracker2.ctix.cn:443/announce",
        "https://tracker.tamersunion.org:443/announce",
    ]
    _TORRENT_DHT_BOOTSTRAP_NODES = [
        ("router.bittorrent.com", 6881),
        ("router.utorrent.com", 6881),
        ("dht.transmissionbt.com", 6881),
        ("dht.aelitis.com", 6881),
    ]
    _TORRENT_DHT_TIMEOUT = _safe_float_setting("torrent_dht_scrape_timeout_sec", 0.9, minimum=0.2, maximum=5.0)
    _TORRENT_DHT_MAX_QUERIES = _safe_int_setting("torrent_dht_scrape_max_queries", 24, minimum=0, maximum=96)
    # User-supplied tracker scraping remains admin controlled.  Built-in public
    # fallback trackers and optional DHT scrape can still be used for trackerless
    # torrents so the room card can restore the seed/leecher style display the
    # chat had before the trackerless warning pass.
    _TORRENT_SCRAPE_ENABLED = bool(settings.get("torrent_scrape_enabled", False))

    def _as_bool_setting(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled", ""}:
            return False
        return default

    def _torrent_scrape_enabled() -> bool:
        # Read the live settings dict so Admin Panel changes take effect without
        # a server restart.  _TORRENT_SCRAPE_ENABLED remains as a startup
        # snapshot/back-compat marker for older guard tests.
        return _as_bool_setting(settings.get("torrent_scrape_enabled", False), False)

    def _torrent_public_fallback_scrape_enabled() -> bool:
        # Safe-by-default path: scrape only Echo-Chat's built-in public tracker
        # list when a .torrent has no announce URLs.  This avoids arbitrary
        # user-supplied outbound URLs while still letting trackerless torrents
        # show seed/leecher counts when public trackers know the swarm.
        return _as_bool_setting(settings.get("torrent_public_fallback_scrape_enabled", True), True)

    def _torrent_dht_scrape_enabled() -> bool:
        # DHT scrape is best-effort and approximate.  It is used only as a
        # fallback when tracker scrape returns nothing or no trackers are present.
        return _as_bool_setting(settings.get("torrent_dht_scrape_enabled", True), True)

    def _configured_public_fallback_trackers() -> list[str]:
        raw = settings.get("torrent_public_fallback_trackers")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = [x.strip() for x in raw.splitlines() if x.strip()]
        candidates = raw if isinstance(raw, list) else _TORRENT_PUBLIC_FALLBACK_TRACKERS
        out: list[str] = []
        for item in candidates:
            text = str(item or "").strip()
            if not text or text in out:
                continue
            parsed = urllib.parse.urlparse(text)
            if parsed.scheme in {"udp", "http", "https"} and not parsed.username and not parsed.password:
                out.append(text)
            if len(out) >= 12:
                break
        return out or list(_TORRENT_PUBLIC_FALLBACK_TRACKERS)

    def _is_public_fallback_tracker_list(trackers: list[str]) -> bool:
        allowed = {str(x).strip() for x in _configured_public_fallback_trackers()}
        supplied = [str(x or "").strip() for x in (trackers or []) if str(x or "").strip()]
        return bool(supplied) and all(x in allowed for x in supplied)

    def _clean_torrent_tracker_list(trackers, *, limit: int = 12) -> list[str]:
        out: list[str] = []
        for item in trackers if isinstance(trackers, list) else []:
            text = str(item or "").strip()
            if not text or text in out:
                continue
            parsed = urllib.parse.urlparse(text)
            if parsed.scheme not in {"udp", "http", "https"}:
                continue
            if parsed.username or parsed.password:
                continue
            out.append(text)
            if len(out) >= limit:
                break
        return out

    def _torrent_tracker_cache_fingerprint(trackers) -> str:
        clean = _clean_torrent_tracker_list(trackers, limit=12)
        material = "\n".join(clean).encode("utf-8", "ignore")
        return hashlib.sha256(material).hexdigest()[:16]

    _TORRENT_UPLOAD_ENABLED = _as_bool_setting(settings.get("torrent_upload_enabled", True), True)
    _TORRENT_MAX_FILE_BYTES = _safe_int_setting("max_torrent_upload_bytes", 1_000_000, minimum=1024, maximum=5_000_000)

    def _torrent_upload_enabled() -> bool:
        # Read live settings so Admin Panel changes take effect immediately and
        # string values like "false" from hand-edited JSON are not treated as on.
        return _as_bool_setting(settings.get("torrent_upload_enabled", _TORRENT_UPLOAD_ENABLED), bool(_TORRENT_UPLOAD_ENABLED))
    # Retired compatibility escape hatch. Old token-only torrent metadata is no
    # longer downloadable because it has no room/owner scope to enforce.
    _ALLOW_LEGACY_TORRENT_DOWNLOAD_WITHOUT_METADATA = False

    def _either_blocked(a: str, b: str) -> bool:
        a = str(a or "").strip()
        b = str(b or "").strip()
        if not a or not b:
            return False
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                  FROM blocks
                 WHERE (LOWER(blocker) = LOWER(%s) AND LOWER(blocked) = LOWER(%s))
                    OR (LOWER(blocker) = LOWER(%s) AND LOWER(blocked) = LOWER(%s))
                 LIMIT 1;
                """,
                (a, b, b, a),
            )
            return cur.fetchone() is not None


    def _resolve_canonical_username(raw_username: str | None) -> str | None:
        username = str(raw_username or "").strip()
        if not username:
            return None
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;", (username,))
            row = cur.fetchone()
            return str(row[0]) if row and row[0] else None


    def _profile_write_denial(username: str, *, action: str = "write") -> tuple[bool, str]:
        """Return (denied, message) for profile write/visible-activity actions."""
        if is_user_sanctioned(username, "ban"):
            return True, "Profile changes are disabled for this account"
        if action in {"post", "edit", "comment", "reaction", "pin", "feature", "media"} and is_user_sanctioned(username, "mute"):
            return True, "Profile posting is disabled for this account"
        if action in {"avatar", "banner", "media"} and is_user_sanctioned(username, "upload"):
            return True, "Uploads are disabled for this account"
        return False, ""

    def _profile_write_denial_response(username: str, *, action: str = "write", status: int = 403):
        denied, message = _profile_write_denial(username, action=action)
        if denied:
            return jsonify({"success": False, "error": message}), int(status)
        return None

    def _profile_payload_for_user(username: str):
        _ensure_profile_runtime_schema()
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT username, bio, avatar_url, custom_status,
                       presence_status, online, last_seen, created_at, status,
                       relationship_status, relationship_visibility, age, age_visibility,
                       location_text, location_visibility, interests, favorite_music,
                       favorite_movies, favorite_games, website_url,
                       banner_url, profile_accent, share_recent_rooms, recent_rooms_visibility,
                       profile_post_default_visibility
                  FROM users
                 WHERE username = %s
                 LIMIT 1;
                """,
                (username,),
            )
            row = cur.fetchone()
        if not row:
            return None
        (uname, bio, avatar_url, custom_status, presence_status, online, last_seen, created_at, status,
         relationship_status, relationship_visibility, age, age_visibility,
         location_text, location_visibility, interests, favorite_music, favorite_movies, favorite_games,
         website_url, banner_url, profile_accent, share_recent_rooms, recent_rooms_visibility,
         profile_post_default_visibility) = row
        return {
            "username": uname,
            "bio": bio or "",
            "avatar_url": avatar_url or "",
            "banner_url": banner_url or "",
            "profile_accent": profile_accent or "",
            "custom_status": custom_status or "",
            "presence": presence_status or ("online" if bool(online) else "offline"),
            "online": bool(online),
            "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else (str(last_seen) if last_seen else None),
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else (str(created_at) if created_at else None),
            "account_status": status or "active",
            "relationship_status": relationship_status or "",
            "relationship_visibility": relationship_visibility or "friends",
            "age": int(age) if age is not None else None,
            "age_visibility": age_visibility or "friends",
            "location_text": decrypt_sensitive_field(location_text or "", settings, field_name="users.location_text"),
            "location_visibility": location_visibility or "friends",
            "interests": interests or "",
            "favorite_music": favorite_music or "",
            "favorite_movies": favorite_movies or "",
            "favorite_games": favorite_games or "",
            "website_url": website_url or "",
            "share_recent_rooms": bool(share_recent_rooms),
            "recent_rooms_visibility": recent_rooms_visibility or "friends",
            "profile_post_default_visibility": profile_post_default_visibility or "friends",
            "badges": [],
            "recent_rooms": [],
            "recent_rooms_count": 0,
            "mutual_friends": [],
            "mutual_friends_count": 0,
            "mutual_groups": [],
            "mutual_groups_count": 0,
            "mutual_rooms": [],
            "mutual_rooms_count": 0,
            "is_friend": False,
            "blocked_by_me": False,
            "blocks_me": False,
        }


    def _is_accepted_friendship(a: str, b: str) -> bool:
        if not a or not b or str(a).lower() == str(b).lower():
            return True
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                  FROM friend_requests
                 WHERE ((from_user = %s AND to_user = %s)
                     OR (from_user = %s AND to_user = %s))
                   AND request_status = 'accepted'
                 LIMIT 1;
                """,
                (a, b, b, a),
            )
            return cur.fetchone() is not None


    def _users_share_live_room(a: str, b: str) -> bool:
        a = str(a or "").strip()
        b = str(b or "").strip()
        if not a or not b or a == b:
            return bool(a and b and a == b)
        try:
            from realtime.state import get_connected_session, user_sids
            rooms_a = set()
            for sid in user_sids(a):
                sess = get_connected_session(sid) or {}
                room = str(sess.get("room") or "").strip()
                if room:
                    rooms_a.add(room)
            if not rooms_a:
                return False
            for sid in user_sids(b):
                sess = get_connected_session(sid) or {}
                room = str(sess.get("room") or "").strip()
                if room and room in rooms_a:
                    return True
        except Exception:
            return False
        return False


    def _profile_visibility_allows(viewer: str, target: str, visibility: str, *, is_self: bool, is_friend: bool) -> bool:
        v = str(visibility or "friends").strip().lower()
        if v in {"only_me", "me", "private", "nobody"}:
            v = "private"
        if v in {"room", "room_member", "room_members", "room_members_only"}:
            v = "room_members"
        if is_self:
            return True
        if v == "everyone":
            return True
        if v == "friends":
            return bool(is_friend)
        if v == "room_members":
            return _users_share_live_room(viewer, target)
        return False


    def _get_profile_mutual_friend_data(viewer: str, target: str, limit: int = 6) -> dict:
        viewer = str(viewer or "").strip()
        target = str(target or "").strip()
        if not viewer or not target or viewer.lower() == target.lower():
            return {"count": 0, "usernames": []}
        wanted = max(1, min(int(limit or 6), 12))
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH viewer_friends AS (
                    SELECT CASE WHEN LOWER(from_user) = LOWER(%s) THEN to_user ELSE from_user END AS username
                      FROM friend_requests
                     WHERE request_status = 'accepted'
                       AND (LOWER(%s) = LOWER(from_user) OR LOWER(%s) = LOWER(to_user))
                ),
                target_friends AS (
                    SELECT CASE WHEN LOWER(from_user) = LOWER(%s) THEN to_user ELSE from_user END AS username
                      FROM friend_requests
                     WHERE request_status = 'accepted'
                       AND (LOWER(%s) = LOWER(from_user) OR LOWER(%s) = LOWER(to_user))
                )
                SELECT vf.username
                  FROM viewer_friends vf
                  JOIN target_friends tf ON LOWER(tf.username) = LOWER(vf.username)
                 WHERE LOWER(vf.username) <> LOWER(%s)
                   AND LOWER(vf.username) <> LOWER(%s)
                 ORDER BY LOWER(vf.username)
                 LIMIT %s;
                """,
                (viewer, viewer, viewer, target, target, target, viewer, target, wanted),
            )
            usernames = [str(r[0]) for r in (cur.fetchall() or []) if r and r[0]]
            cur.execute(
                """
                WITH viewer_friends AS (
                    SELECT CASE WHEN LOWER(from_user) = LOWER(%s) THEN to_user ELSE from_user END AS username
                      FROM friend_requests
                     WHERE request_status = 'accepted'
                       AND (LOWER(%s) = LOWER(from_user) OR LOWER(%s) = LOWER(to_user))
                ),
                target_friends AS (
                    SELECT CASE WHEN LOWER(from_user) = LOWER(%s) THEN to_user ELSE from_user END AS username
                      FROM friend_requests
                     WHERE request_status = 'accepted'
                       AND (LOWER(%s) = LOWER(from_user) OR LOWER(%s) = LOWER(to_user))
                )
                SELECT COUNT(*)
                  FROM (
                    SELECT vf.username
                      FROM viewer_friends vf
                      JOIN target_friends tf ON LOWER(tf.username) = LOWER(vf.username)
                     WHERE LOWER(vf.username) <> LOWER(%s)
                       AND LOWER(vf.username) <> LOWER(%s)
                  ) mutuals;
                """,
                (viewer, viewer, viewer, target, target, target, viewer, target),
            )
            row = cur.fetchone()
        return {"count": int(row[0] or 0) if row else 0, "usernames": usernames}


    def _get_profile_mutual_group_data(viewer: str, target: str, limit: int = 6) -> dict:
        viewer = str(viewer or "").strip()
        target = str(target or "").strip()
        if not viewer or not target or viewer.lower() == target.lower():
            return {"count": 0, "groups": []}
        wanted = max(1, min(int(limit or 6), 12))
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH viewer_groups AS (
                    SELECT gm.group_id, g.group_name
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                      JOIN groups g ON g.id = gm.group_id
                     WHERE LOWER(u.username) = LOWER(%s)
                ),
                target_groups AS (
                    SELECT gm.group_id
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                     WHERE LOWER(u.username) = LOWER(%s)
                )
                SELECT vg.group_name
                  FROM viewer_groups vg
                  JOIN target_groups tg ON tg.group_id = vg.group_id
                 ORDER BY LOWER(vg.group_name)
                 LIMIT %s;
                """,
                (viewer, target, wanted),
            )
            groups = [str(r[0]) for r in (cur.fetchall() or []) if r and r[0]]
            cur.execute(
                """
                WITH viewer_groups AS (
                    SELECT gm.group_id
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                     WHERE LOWER(u.username) = LOWER(%s)
                ),
                target_groups AS (
                    SELECT gm.group_id
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                     WHERE LOWER(u.username) = LOWER(%s)
                )
                SELECT COUNT(*)
                  FROM (
                    SELECT vg.group_id
                      FROM viewer_groups vg
                      JOIN target_groups tg ON tg.group_id = vg.group_id
                  ) mutual_groups;
                """,
                (viewer, target),
            )
            row = cur.fetchone()
        return {"count": int(row[0] or 0) if row else 0, "groups": groups}


    def _get_profile_mutual_room_data(viewer: str, target: str, limit: int = 6) -> dict:
        viewer = str(viewer or "").strip()
        target = str(target or "").strip()
        if not viewer or not target or viewer.lower() == target.lower():
            return {"count": 0, "rooms": []}
        shared_rooms: set[str] = set()
        wanted = max(1, min(int(limit or 6), 12))
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH viewer_rooms AS (
                    SELECT crm.room_name
                      FROM custom_room_members crm
                     WHERE LOWER(crm.member_user) = LOWER(%s)
                ),
                target_rooms AS (
                    SELECT crm.room_name
                      FROM custom_room_members crm
                     WHERE LOWER(crm.member_user) = LOWER(%s)
                )
                SELECT vr.room_name
                  FROM viewer_rooms vr
                  JOIN target_rooms tr ON LOWER(tr.room_name) = LOWER(vr.room_name)
                 ORDER BY LOWER(vr.room_name);
                """,
                (viewer, target),
            )
            for row in (cur.fetchall() or []):
                if row and row[0]:
                    shared_rooms.add(str(row[0]))
        try:
            from realtime.state import get_connected_session, user_sids
            viewer_live_rooms = set()
            target_live_rooms = set()
            for sid in user_sids(viewer):
                sess = get_connected_session(sid) or {}
                room = str(sess.get("room") or "").strip()
                if room:
                    viewer_live_rooms.add(room)
            for sid in user_sids(target):
                sess = get_connected_session(sid) or {}
                room = str(sess.get("room") or "").strip()
                if room:
                    target_live_rooms.add(room)
            shared_rooms.update(viewer_live_rooms & target_live_rooms)
        except Exception:
            pass
        ordered_rooms = sorted(shared_rooms, key=lambda value: value.lower())
        return {"count": len(ordered_rooms), "rooms": ordered_rooms[:wanted]}


    def _profile_recent_room_visible_to_viewer(room_name: str, viewer: str, target: str) -> bool:
        clean_room = str(room_name or "").strip()
        clean_viewer = str(viewer or "").strip()
        clean_target = str(target or "").strip()
        if not clean_room:
            return False
        if clean_viewer and clean_target and clean_viewer.lower() == clean_target.lower():
            return True
        try:
            meta = get_custom_room_meta(clean_room)
        except Exception:
            return False
        if not meta:
            return True
        if not bool(meta.get("is_private")):
            return True
        try:
            return bool(can_user_access_custom_room(clean_room, clean_viewer))
        except Exception:
            return False


    def _get_profile_recent_room_share_data(target: str, limit: int = 3, viewer: str | None = None) -> dict:
        target = str(target or "").strip()
        viewer = str(viewer or "").strip()
        if not target:
            return {"count": 0, "rooms": []}
        try:
            wanted = max(1, min(int(limit or 3), 3))
        except Exception:
            wanted = 3
        live_rooms: list[str] = []
        live_seen: set[str] = set()
        try:
            from realtime.state import get_connected_session, user_sids
            for sid in user_sids(target):
                sess = get_connected_session(sid) or {}
                room = str(sess.get("room") or "").strip()
                if room and room not in live_seen:
                    live_rooms.append(room)
                    live_seen.add(room)
        except Exception:
            live_rooms = []
            live_seen = set()
        db_rooms: list[str] = []
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT room_name, MAX(joined_at) AS last_joined
                  FROM user_recent_rooms
                 WHERE LOWER(username) = LOWER(%s)
                 GROUP BY room_name
                 ORDER BY MAX(joined_at) DESC, LOWER(room_name) ASC
                 LIMIT %s;
                """,
                (target, max(wanted * 4, 12)),
            )
            for row in (cur.fetchall() or []):
                room_name = str((row or [None])[0] or "").strip()
                if room_name:
                    db_rooms.append(room_name)
        current_first = [room for room in db_rooms if room in live_seen]
        for room in live_rooms:
            if room not in current_first:
                current_first.append(room)
        seen = set(current_first)
        trailing = [room for room in db_rooms if room not in seen]
        ordered = current_first + trailing
        visible_ordered = [
            room_name
            for room_name in ordered
            if _profile_recent_room_visible_to_viewer(room_name, viewer, target)
        ]
        rooms = [{"name": room_name, "is_current": room_name in live_seen} for room_name in visible_ordered[:wanted]]
        return {"count": len(visible_ordered), "rooms": rooms}


    def _profile_static_and_assigned_badges(username: str, profile: dict | None = None) -> list[dict]:
        username = str(username or "").strip()
        profile = profile or {}
        badges: list[dict] = []
        seen: set[str] = set()

        def add(key: str, label: str, kind: str = "system") -> None:
            k = re.sub(r"[^a-z0-9_:-]", "", str(key or "").strip().lower().replace(" ", "_"))[:40]
            text = str(label or "").strip()[:40]
            if not k or not text or k in seen:
                return
            seen.add(k)
            badges.append({"key": k, "label": text, "kind": kind})

        try:
            perms = set(get_user_permissions(username))
        except Exception:
            perms = set()
        if "admin:settings" in perms or "admin:basic" in perms:
            add("admin", "Admin")
        if "moderation:suspend_user" in perms or "moderation:mute_user" in perms:
            add("moderator", "Moderator")
        if bool(profile.get("online")):
            add("online", "Online")
        if str(profile.get("account_status") or "active") != "active":
            add("limited", "Limited", "warning")
        if profile.get("avatar_url") and profile.get("bio"):
            add("profile_complete", "Profile complete")
        try:
            created_raw = profile.get("created_at")
            created_dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00")) if created_raw else None
            if created_dt and (datetime.now(timezone.utc) - created_dt).days <= 30:
                add("new_member", "New member")
        except Exception:
            pass

        if username:
            try:
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT badge_key, label
                          FROM user_profile_badges
                         WHERE username = %s
                         ORDER BY created_at DESC, id DESC
                         LIMIT 20;
                        """,
                        (username,),
                    )
                    for key, label in cur.fetchall() or []:
                        add(str(key or ""), str(label or ""), "assigned")
            except Exception:
                pass
        return badges


    def _profile_payload_for_viewer(viewer: str, target: str):
        profile = _profile_payload_for_user(target)
        if not profile:
            return None

        uname = str(profile.get("username") or target)
        is_self = str(viewer or "").lower() == uname.lower()
        blocked_by_me = False
        blocks_me = False
        if not is_self:
            try:
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM blocks WHERE LOWER(blocker) = LOWER(%s) AND LOWER(blocked) = LOWER(%s) LIMIT 1;", (viewer, uname))
                    blocked_by_me = cur.fetchone() is not None
                    cur.execute("SELECT 1 FROM blocks WHERE LOWER(blocker) = LOWER(%s) AND LOWER(blocked) = LOWER(%s) LIMIT 1;", (uname, viewer))
                    blocks_me = cur.fetchone() is not None
            except Exception:
                blocked_by_me = False
                blocks_me = False

        try:
            is_friend = _is_accepted_friendship(viewer, uname)
        except Exception:
            is_friend = False
        if is_self:
            is_friend = False

        can_share_profile_context = bool(is_self or (not blocked_by_me and not blocks_me))
        if can_share_profile_context:
            try:
                recent = _get_profile_recent_room_share_data(uname, limit=3, viewer=viewer)
                profile["recent_rooms"] = list(recent.get("rooms") or [])
                profile["recent_rooms_count"] = int(recent.get("count") or 0)
            except Exception:
                profile["recent_rooms"] = []
                profile["recent_rooms_count"] = 0
        if can_share_profile_context and not is_self:
            try:
                mutual_friends = _get_profile_mutual_friend_data(viewer, uname, limit=6)
                profile["mutual_friends"] = list(mutual_friends.get("usernames") or [])
                profile["mutual_friends_count"] = int(mutual_friends.get("count") or 0)
            except Exception:
                profile["mutual_friends"] = []
                profile["mutual_friends_count"] = 0
            try:
                mutual_groups = _get_profile_mutual_group_data(viewer, uname, limit=6)
                profile["mutual_groups"] = list(mutual_groups.get("groups") or [])
                profile["mutual_groups_count"] = int(mutual_groups.get("count") or 0)
            except Exception:
                profile["mutual_groups"] = []
                profile["mutual_groups_count"] = 0
            try:
                mutual_rooms = _get_profile_mutual_room_data(viewer, uname, limit=6)
                profile["mutual_rooms"] = list(mutual_rooms.get("rooms") or [])
                profile["mutual_rooms_count"] = int(mutual_rooms.get("count") or 0)
            except Exception:
                profile["mutual_rooms"] = []
                profile["mutual_rooms_count"] = 0

        def can_view(visibility: str) -> bool:
            return _profile_visibility_allows(viewer, uname, visibility, is_self=is_self, is_friend=bool(is_friend))

        relationship_visible = can_view(profile.get("relationship_visibility"))
        age_visible = can_view(profile.get("age_visibility"))
        location_visible = can_view(profile.get("location_visibility"))
        recent_rooms_visible = bool(profile.get("share_recent_rooms")) and can_view(profile.get("recent_rooms_visibility"))

        if not relationship_visible:
            profile["relationship_status"] = ""
        if not age_visible:
            profile["age"] = None
        if not location_visible:
            profile["location_text"] = ""
        if not recent_rooms_visible:
            profile["recent_rooms"] = []
            profile["recent_rooms_count"] = 0

        profile["is_self"] = bool(is_self)
        profile["is_friend"] = bool(is_friend)
        profile["blocked_by_me"] = bool(blocked_by_me)
        profile["blocks_me"] = bool(blocks_me)
        profile["can_view_relationship"] = bool(relationship_visible)
        profile["can_view_age"] = bool(age_visible)
        profile["can_view_location"] = bool(location_visible)
        profile["can_view_recent_rooms"] = bool(recent_rooms_visible)
        profile["badges"] = _profile_static_and_assigned_badges(uname, profile)
        return profile


    def _profile_api_json(payload: dict, status: int = 200):
        resp = jsonify(payload)
        resp.status_code = int(status)
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp


    @app.get("/api/profile/<path:raw_username>")
    @jwt_required()
    def api_profile_view(raw_username: str):
        viewer = get_jwt_identity()
        target = _resolve_canonical_username(raw_username)
        if not target:
            return _profile_api_json({"success": False, "error": "not_found"}, 404)
        profile = _profile_payload_for_viewer(viewer, target)
        if not profile:
            return _profile_api_json({"success": False, "error": "not_found"}, 404)
        return _profile_api_json({"success": True, "profile": profile})


    _AVATAR_PRESET_STYLES = {"persona", "bot", "pixel", "shapes", "animal", "alien", "retro", "politics"}

    def _avatar_ints(style: str, seed: str, count: int = 32):
        raw = hashlib.sha256(f"{style}:{seed}".encode("utf-8", "ignore")).digest()
        ints = list(raw)
        while len(ints) < count:
            raw = hashlib.sha256(raw).digest()
            ints.extend(raw)
        return ints[:count]

    def _avatar_color(h: int, s: int, l: int) -> str:
        h = max(0, min(359, int(h)))
        s = max(0, min(100, int(s)))
        l = max(0, min(100, int(l)))
        return f"hsl({h} {s}% {l}%)"

    def _render_avatar_preset_svg(style: str, seed: str) -> str:
        style = str(style or "").strip().lower()
        seed = str(seed or "").strip()[:128] or "echo"
        nums = _avatar_ints(style, seed, 48)
        hue = nums[0] * 360 // 255
        bg1 = _avatar_color(hue, 58 + (nums[1] % 20), 62 + (nums[2] % 12))
        bg2 = _avatar_color((hue + 36 + (nums[3] % 90)) % 360, 62 + (nums[4] % 18), 36 + (nums[5] % 18))
        frame = _avatar_color((hue + 180) % 360, 18, 22)

        if style == "persona":
            skin = _avatar_color(18 + (nums[6] % 30), 58 + (nums[7] % 20), 62 + (nums[8] % 16))
            hair = _avatar_color((hue + nums[9]) % 360, 18 + (nums[10] % 58), 16 + (nums[11] % 18))
            shirt = _avatar_color((hue + 210 + nums[12]) % 360, 55 + (nums[13] % 24), 42 + (nums[14] % 16))
            eye = _avatar_color((hue + 200) % 360, 20 + (nums[15] % 30), 18 + (nums[16] % 14))
            mouth_y = 71 + (nums[17] % 4)
            return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar preset">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg1}"/>
      <stop offset="100%" stop-color="{bg2}"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#bg)"/>
  <circle cx="64" cy="49" r="26" fill="{skin}"/>
  <path d="M35 47c0-16 12-29 29-29s29 13 29 29v3H35z" fill="{hair}"/>
  <path d="M29 118c4-23 18-36 35-36 18 0 32 13 35 36" fill="{shirt}"/>
  <rect x="36" y="86" width="56" height="28" rx="14" fill="{shirt}"/>
  <circle cx="55" cy="50" r="3.3" fill="{eye}"/>
  <circle cx="73" cy="50" r="3.3" fill="{eye}"/>
  <path d="M54 {mouth_y}c5 5 15 5 20 0" fill="none" stroke="{eye}" stroke-width="3" stroke-linecap="round"/>
  <rect x="0.5" y="0.5" width="127" height="127" rx="24" fill="none" stroke="{frame}" opacity="0.35"/>
</svg>'''

        if style == "bot":
            shell = _avatar_color((hue + 170) % 360, 42 + (nums[6] % 25), 70 + (nums[7] % 10))
            panel = _avatar_color((hue + 200) % 360, 45 + (nums[8] % 18), 28 + (nums[9] % 14))
            accent = _avatar_color((hue + 40) % 360, 85, 58)
            return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar preset">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg1}"/>
      <stop offset="100%" stop-color="{bg2}"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#bg)"/>
  <rect x="28" y="28" width="72" height="54" rx="16" fill="{shell}"/>
  <rect x="37" y="38" width="54" height="28" rx="10" fill="{panel}"/>
  <circle cx="52" cy="52" r="7" fill="{accent}"/>
  <circle cx="76" cy="52" r="7" fill="{accent}"/>
  <rect x="48" y="19" width="32" height="9" rx="4.5" fill="{shell}"/>
  <rect x="60" y="10" width="8" height="12" rx="4" fill="{shell}"/>
  <circle cx="64" cy="10" r="5" fill="{accent}"/>
  <rect x="44" y="90" width="40" height="22" rx="11" fill="{panel}"/>
  <path d="M46 75h36" stroke="{panel}" stroke-width="5" stroke-linecap="round"/>
  <path d="M54 102h20" stroke="{accent}" stroke-width="5" stroke-linecap="round"/>
  <rect x="0.5" y="0.5" width="127" height="127" rx="24" fill="none" stroke="{frame}" opacity="0.35"/>
</svg>'''

        if style == "pixel":
            fg1 = _avatar_color((hue + 18) % 360, 72, 24 + (nums[6] % 20))
            fg2 = _avatar_color((hue + 180) % 360, 68, 44 + (nums[7] % 18))
            cells = []
            idx = 8
            size = 11
            offset = 20
            for y in range(8):
                for x in range(4):
                    n = nums[idx % len(nums)]
                    idx += 1
                    if n % 5 in (0, 1, 2):
                        color = fg1 if (n % 2 == 0) else fg2
                        x0 = offset + x * size
                        y0 = offset + y * size
                        cells.append(f'<rect x="{x0}" y="{y0}" width="{size}" height="{size}" rx="2" fill="{color}"/>')
                        mirror_x = offset + (7 - x) * size
                        if mirror_x != x0:
                            cells.append(f'<rect x="{mirror_x}" y="{y0}" width="{size}" height="{size}" rx="2" fill="{color}"/>')
            return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar preset">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg1}"/>
      <stop offset="100%" stop-color="{bg2}"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#bg)"/>
  <rect x="16" y="16" width="96" height="96" rx="18" fill="rgba(255,255,255,0.12)"/>
  {''.join(cells)}
  <rect x="0.5" y="0.5" width="127" height="127" rx="24" fill="none" stroke="{frame}" opacity="0.35"/>
</svg>'''

        if style == "animal":
            fur = _avatar_color((hue + 12) % 360, 44 + (nums[6] % 18), 54 + (nums[7] % 14))
            inner_ear = _avatar_color((hue + 330) % 360, 58, 78)
            snout = _avatar_color(28 + (nums[8] % 18), 44, 78)
            eye = _avatar_color((hue + 200) % 360, 18, 16)
            whisker = _avatar_color((hue + 210) % 360, 18, 92)
            return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar preset">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg1}"/>
      <stop offset="100%" stop-color="{bg2}"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#bg)"/>
  <path d="M26 47 42 22l14 24" fill="{fur}"/>
  <path d="M102 47 86 22 72 47" fill="{fur}"/>
  <path d="M33 45 42 30l8 15" fill="{inner_ear}" opacity="0.9"/>
  <path d="M95 45 86 30l-8 15" fill="{inner_ear}" opacity="0.9"/>
  <circle cx="64" cy="66" r="34" fill="{fur}"/>
  <ellipse cx="64" cy="79" rx="19" ry="14" fill="{snout}"/>
  <circle cx="51" cy="62" r="4.2" fill="{eye}"/>
  <circle cx="77" cy="62" r="4.2" fill="{eye}"/>
  <path d="M60 77c2 3 6 3 8 0" stroke="{eye}" stroke-width="3" fill="none" stroke-linecap="round"/>
  <circle cx="64" cy="72" r="4" fill="{eye}"/>
  <path d="M46 77h-13M46 82H31M82 77h13M82 82h15" stroke="{whisker}" stroke-width="2.5" stroke-linecap="round" opacity="0.92"/>
  <rect x="0.5" y="0.5" width="127" height="127" rx="24" fill="none" stroke="{frame}" opacity="0.35"/>
</svg>'''

        if style == "alien":
            skin = _avatar_color((hue + 95) % 360, 56 + (nums[6] % 20), 56 + (nums[7] % 10))
            eye = _avatar_color((hue + 220) % 360, 20, 10)
            glow = _avatar_color((hue + 170) % 360, 86, 70)
            suit = _avatar_color((hue + 280) % 360, 42, 28)
            return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar preset">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg1}"/>
      <stop offset="100%" stop-color="{bg2}"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#bg)"/>
  <path d="M52 28c0-6 5-11 12-11s12 5 12 11" stroke="{glow}" stroke-width="4" fill="none" stroke-linecap="round"/>
  <circle cx="52" cy="27" r="4" fill="{glow}"/>
  <circle cx="76" cy="27" r="4" fill="{glow}"/>
  <path d="M64 24v-8" stroke="{glow}" stroke-width="4" stroke-linecap="round"/>
  <circle cx="64" cy="13" r="5" fill="{glow}"/>
  <ellipse cx="64" cy="59" rx="30" ry="36" fill="{skin}"/>
  <ellipse cx="51" cy="56" rx="9" ry="15" fill="{eye}"/>
  <ellipse cx="77" cy="56" rx="9" ry="15" fill="{eye}"/>
  <circle cx="51" cy="55" r="2.2" fill="{glow}"/>
  <circle cx="77" cy="55" r="2.2" fill="{glow}"/>
  <path d="M53 78c5 4 17 4 22 0" stroke="{eye}" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path d="M33 114c5-17 16-28 31-28s26 11 31 28" fill="{suit}"/>
  <rect x="40" y="87" width="48" height="26" rx="13" fill="{suit}"/>
  <circle cx="64" cy="96" r="6" fill="{glow}"/>
  <rect x="0.5" y="0.5" width="127" height="127" rx="24" fill="none" stroke="{frame}" opacity="0.35"/>
</svg>'''

        if style == "retro":
            frame_inner = _avatar_color((hue + 50) % 360, 72, 24)
            fg1 = _avatar_color((hue + 18) % 360, 78, 38)
            fg2 = _avatar_color((hue + 180) % 360, 80, 70)
            pixels = []
            idx = 8
            size = 8
            pattern = [
                '00111100',
                '01111110',
                '11111111',
                '11100111',
                '11111111',
                '10111101',
                '00111100',
                '00100100',
            ]
            for y, row in enumerate(pattern):
                for x, bit in enumerate(row):
                    if bit != '1':
                        continue
                    n = nums[idx % len(nums)]
                    idx += 1
                    color = fg1 if (x + y + n) % 3 else fg2
                    x0 = 32 + x * size
                    y0 = 28 + y * size
                    pixels.append(f'<rect x="{x0}" y="{y0}" width="{size}" height="{size}" fill="{color}"/>')
            return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar preset">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg1}"/>
      <stop offset="100%" stop-color="{bg2}"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#bg)"/>
  <rect x="24" y="20" width="80" height="88" rx="10" fill="{frame_inner}" opacity="0.92"/>
  <rect x="28" y="24" width="72" height="80" rx="8" fill="rgba(255,255,255,0.08)"/>
  {''.join(pixels)}
  <path d="M42 103h44" stroke="{fg2}" stroke-width="4" stroke-linecap="round" opacity="0.8"/>
  <rect x="0.5" y="0.5" width="127" height="127" rx="24" fill="none" stroke="{frame}" opacity="0.35"/>
</svg>'''

        if style == "politics":
            suit = _avatar_color((hue + 220) % 360, 32, 22)
            skin = _avatar_color(18 + (nums[6] % 24), 54, 72)
            tie = _avatar_color((hue + 355) % 360, 78, 56)
            flag_red = _avatar_color(355, 74, 56)
            flag_blue = _avatar_color(220, 66, 34)
            star = _avatar_color(48, 96, 80)
            mic = _avatar_color(210, 10, 80)
            return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar preset">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg1}"/>
      <stop offset="100%" stop-color="{bg2}"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#bg)"/>
  <rect x="18" y="18" width="92" height="34" rx="10" fill="{flag_blue}" opacity="0.95"/>
  <path d="M18 41h92" stroke="{flag_red}" stroke-width="8" opacity="0.96"/>
  <path d="M18 29h92" stroke="white" stroke-width="6" opacity="0.9"/>
  <path d="M18 53h92" stroke="white" stroke-width="6" opacity="0.9"/>
  <path d="M44 23l2.8 5.7 6.2.9-4.5 4.4 1 6.2L44 37l-5.5 3 1-6.2-4.5-4.4 6.2-.9Z" fill="{star}"/>
  <circle cx="64" cy="67" r="20" fill="{skin}"/>
  <path d="M35 117c4-18 15-29 29-29s25 11 29 29" fill="{suit}"/>
  <rect x="39" y="88" width="50" height="28" rx="14" fill="{suit}"/>
  <path d="M64 86l-9 10h18Z" fill="white"/>
  <path d="M64 92l-6 18h12Z" fill="{tie}"/>
  <rect x="88" y="65" width="6" height="23" rx="3" fill="{mic}"/>
  <circle cx="91" cy="62" r="8" fill="{mic}"/>
  <rect x="0.5" y="0.5" width="127" height="127" rx="24" fill="none" stroke="{frame}" opacity="0.35"/>
</svg>'''

        ring = _avatar_color((hue + 300) % 360, 72, 82)
        blob = _avatar_color((hue + 110) % 360, 66, 48)
        blob2 = _avatar_color((hue + 250) % 360, 68, 56)
        initials = ''.join(ch for ch in seed.upper() if ch.isalnum())[:2] or 'EC'
        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="avatar preset">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg1}"/>
      <stop offset="100%" stop-color="{bg2}"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#bg)"/>
  <circle cx="48" cy="50" r="28" fill="{blob}" opacity="0.92"/>
  <circle cx="82" cy="76" r="22" fill="{blob2}" opacity="0.9"/>
  <circle cx="80" cy="42" r="17" fill="{ring}" opacity="0.85"/>
  <text x="64" y="73" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="28" font-weight="700" fill="white">{initials}</text>
  <rect x="0.5" y="0.5" width="127" height="127" rx="24" fill="none" stroke="{frame}" opacity="0.35"/>
</svg>'''



    @app.route("/")
    def index():
        # Root should land on the real login entrypoint, not a legacy splash page
        # and not a chat bootstrap hop.
        return redirect("/login")

    # Health check is optional and should be safe for unauthenticated probes.
    if settings.get("enable_health_check_endpoint", False):
        endpoint = normalize_public_probe_path(settings.get("health_check_endpoint"), "/health")
        settings["health_check_endpoint"] = endpoint

        @app.route(endpoint, methods=["GET", "HEAD"])
        def health_check():
            payload, status_code = build_health_payload(get_db, shared_state_summary)
            resp = jsonify(payload)
            resp.status_code = status_code
            # Avoid stale proxy/browser health state after DB restarts or maintenance.
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp

    
    # ------------------------------------------------------------------
    # GIPHY GIF search (server-side proxy; keeps API key off the client)
    # ------------------------------------------------------------------
    _GIPHY_CACHE: dict[tuple[str, ...], tuple[float, list[dict]]] = {}
    _GIPHY_CACHE_TTL = _safe_float_setting("giphy_cache_ttl_sec", 45.0, minimum=0.0, maximum=3600.0)

    def _read_giphy_key_file() -> str | None:
        """Best-effort read of a local key file for GIPHY.

        Allows keeping the API key out of server_config.json by placing it in:
          - .giphy_api_key
          - giphy_api_key.txt
        (either in the project root / CWD, or next to this module).
        """
        try:
            base_dir = Path(__file__).resolve().parent
            candidates = [
                Path.cwd() / ".giphy_api_key",
                Path.cwd() / "giphy_api_key.txt",
                base_dir / ".giphy_api_key",
                base_dir / "giphy_api_key.txt",
            ]
            for p in candidates:
                try:
                    if p.exists():
                        v = p.read_text(encoding="utf-8").strip()
                        if v:
                            return v
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _get_giphy_key() -> str | None:
        # Prefer env var; optionally allow config file value or a local key file.
        return (
            os.getenv("ECHOCHAT_GIPHY_API_KEY")
            or os.getenv("GIPHY_API_KEY")
            or settings.get("giphy_api_key")
            or _read_giphy_key_file()
            or ""
        ).strip() or None

    def _giphy_enabled_or_error():
        if not bool(settings.get("giphy_enabled", True)):
            return _no_store_json({"success": False, "error": "GIF search disabled"}, 403)
        api_key = _get_giphy_key()
        if not api_key:
            return _no_store_json({"success": False, "error": "GIPHY_API_KEY not set"}, 500)
        return api_key

    def _giphy_limit_from_request() -> int:
        default_limit = _safe_int_setting("giphy_default_limit", 24, minimum=1, maximum=48)
        try:
            limit = int(request.args.get("limit") or default_limit)
        except Exception:
            limit = default_limit
        return max(1, min(limit, 48))

    def _giphy_rating_setting() -> str:
        rating = str(settings.get("giphy_rating", "pg-13") or "pg-13").strip().lower()
        return rating if rating in {"g", "pg", "pg-13", "r"} else "pg-13"

    def _giphy_lang_setting() -> str:
        lang = str(settings.get("giphy_lang", "en") or "en").strip().lower()[:10]
        return lang if re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2})?", lang) else "en"

    def _giphy_request(endpoint: str, *, cache_key: tuple[str, ...], params: dict) -> tuple[list[dict] | None, str | None]:
        now = time.time()
        hit = _GIPHY_CACHE.get(cache_key)
        if hit and (now - hit[0]) < _GIPHY_CACHE_TTL:
            return hit[1], None

        try:
            resp = requests.get(endpoint, params=params, timeout=6)
            resp.raise_for_status()
            payload = resp.json() or {}
        except Exception as e:
            logging.warning("GIPHY request failed (%s): %s", endpoint, e)
            return None, "GIF request failed"

        raw_items = payload.get("data") or []
        if isinstance(raw_items, dict):
            raw_items = [raw_items]

        out: list[dict] = []
        for item in raw_items:
            try:
                images = (item.get("images") or {})
                fixed = (images.get("fixed_width") or {})
                preview = (images.get("fixed_width_small") or fixed)
                still = (images.get("fixed_width_still") or {})
                url = (fixed.get("url") or item.get("image_url") or "").strip()
                pv = (preview.get("url") or still.get("url") or url).strip()
                if not url:
                    continue
                out.append(
                    {
                        "id": item.get("id"),
                        "title": item.get("title") or item.get("slug") or "",
                        "url": url,
                        "preview": pv or url,
                    }
                )
            except Exception:
                continue

        _GIPHY_CACHE[cache_key] = (now, out)
        return out, None

    @app.route("/api/gifs/search", methods=["GET"])
    @_limit(settings.get("rate_limit_gif_search") or "120 per minute")
    @jwt_required()
    def api_gifs_search():
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("giphy_search", settings.get("rate_limit_gif_search") or "120 per minute", default_limit=120, default_window=60, user=user)
        if guard is not None:
            return guard
        api_key_or_error = _giphy_enabled_or_error()
        if not isinstance(api_key_or_error, str):
            return api_key_or_error
        api_key = api_key_or_error

        q = (request.args.get("q") or "").strip()
        if not q:
            return _no_store_json({"success": True, "data": []})

        q = q[:120]
        limit = _giphy_limit_from_request()
        rating = _giphy_rating_setting()
        lang = _giphy_lang_setting()

        out, err = _giphy_request(
            "https://api.giphy.com/v1/gifs/search",
            cache_key=("search", q.lower(), str(limit), rating, lang),
            params={
                "api_key": api_key,
                "q": q,
                "limit": limit,
                "rating": rating,
                "lang": lang,
            },
        )
        if err:
            return _no_store_json({"success": False, "error": err}, 502)
        return _no_store_json({"success": True, "data": out or []})

    @app.route("/api/gifs/trending", methods=["GET"])
    @_limit(settings.get("rate_limit_gif_search") or "120 per minute")
    @jwt_required()
    def api_gifs_trending():
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("giphy_trending", settings.get("rate_limit_gif_search") or "120 per minute", default_limit=120, default_window=60, user=user)
        if guard is not None:
            return guard
        api_key_or_error = _giphy_enabled_or_error()
        if not isinstance(api_key_or_error, str):
            return api_key_or_error
        api_key = api_key_or_error

        limit = _giphy_limit_from_request()
        rating = _giphy_rating_setting()
        lang = _giphy_lang_setting()

        out, err = _giphy_request(
            "https://api.giphy.com/v1/gifs/trending",
            cache_key=("trending", str(limit), rating, lang),
            params={
                "api_key": api_key,
                "limit": limit,
                "rating": rating,
                "lang": lang,
            },
        )
        if err:
            return _no_store_json({"success": False, "error": err}, 502)
        return _no_store_json({"success": True, "data": out or []})

    def _sniff_image_type(data: bytes) -> tuple[str | None, str | None]:
        sample = bytes(data[:4096])
        stripped = sample.lstrip()
        if sample.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png", "image/png"
        if sample[:3] == b"\xff\xd8\xff":
            return ".jpg", "image/jpeg"
        if sample.startswith((b"GIF87a", b"GIF89a")):
            return ".gif", "image/gif"
        if sample.startswith(b"RIFF") and sample[8:12] == b"WEBP":
            return ".webp", "image/webp"
        if sample.startswith(b"BM"):
            return ".bmp", "image/bmp"
        if sample[:4] == b"\x00\x00\x01\x00":
            return ".ico", "image/x-icon"
        low = stripped[:256].lower()
        if low.startswith(b"<?xml") or low.startswith(b"<svg"):
            return ".svg", "image/svg+xml"
        return None, None


    def _safe_public_upload_ext(filename: str) -> str:
        ext = os.path.splitext(secure_filename(filename or ""))[1].lower()
        blocked = {
            ".html", ".htm", ".xhtml", ".svg", ".xml", ".js", ".mjs", ".css", ".json",
            ".php", ".phtml", ".py", ".pyc", ".sh", ".bash", ".zsh", ".pl", ".cgi",
            ".rb", ".jar", ".war", ".exe", ".dll", ".msi", ".bat", ".cmd", ".ps1", ".com", ".hta"
        }
        if not ext or ext in blocked:
            return ".bin"
        return ext


    _SAFE_AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg"}

    def _secure_download_name(name: str, default: str = "download.bin") -> str:
        safe = secure_filename(str(name or "").strip())
        return safe or default

    def _apply_private_download_headers(resp, *, csp: str = "sandbox; default-src 'none';"):
        return apply_safe_download_headers(resp, csp=csp, private=True)

    def _apply_avatar_response_headers(resp, *, cache_seconds: int = 604800):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = f"public, max-age={int(cache_seconds)}, immutable"
        resp.headers.setdefault("Content-Security-Policy", "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline';")
        return resp

    def _resolve_avatar_path(filename: str):
        safe_name = secure_filename(filename or "")
        if not safe_name or safe_name != filename:
            return None
        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in _SAFE_AVATAR_EXTS:
            return None
        candidate = (Path(profile_avatar_folder) / safe_name).resolve()
        try:
            candidate.relative_to(Path(profile_avatar_folder).resolve())
        except Exception:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def _avatar_fallback_username_from_filename(filename: str) -> str:
        """Best-effort username for stale /media/avatars/<file> database rows.

        Uploaded avatar filenames are normally <username>-<unix>-<token>.<ext>.
        If the physical file was deleted or copied without media, returning a
        generated SVG fallback prevents endless browser 404 spam while still
        making the missing local media obvious in response headers.
        """
        safe_name = secure_filename(filename or "")
        stem = Path(safe_name or "avatar").stem
        parts = stem.rsplit('-', 2)
        if len(parts) == 3 and parts[0]:
            stem = parts[0]
        stem = re.sub(r"[^A-Za-z0-9_. -]+", "", stem).strip()
        return stem[:64] or "user"

    def _missing_avatar_fallback_response(filename: str):
        username_hint = _avatar_fallback_username_from_filename(filename)
        svg = _render_avatar_preset_svg("initials", username_hint)
        resp = app.response_class(svg, mimetype="image/svg+xml")
        resp.headers["X-EchoChat-Avatar-Fallback"] = "missing-local-avatar"
        resp.headers["X-EchoChat-Missing-Avatar"] = secure_filename(filename or "")[:160]
        _apply_avatar_response_headers(resp, cache_seconds=300)
        return resp


    def _resolve_banner_path(filename: str):
        safe_name = secure_filename(filename or "")
        if not safe_name or safe_name != filename:
            return None
        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}:
            return None
        candidate = (Path(profile_banner_folder) / safe_name).resolve()
        try:
            candidate.relative_to(Path(profile_banner_folder).resolve())
        except Exception:
            return None
        if not candidate.is_file():
            return None
        return candidate


    def _local_avatar_media_belongs_to_user(filename: str, owner: str | None) -> bool:
        owner = str(owner or "").strip()
        if not owner:
            return False
        safe_owner = secure_filename(owner) or "user"
        safe_name = secure_filename(filename or "")
        return bool(safe_name and safe_name == filename and safe_name.startswith(f"{safe_owner}-"))


    def _local_banner_media_belongs_to_user(filename: str, owner: str | None) -> bool:
        owner = str(owner or "").strip()
        if not owner:
            return False
        safe_owner = secure_filename(owner) or "user"
        safe_name = secure_filename(filename or "")
        return bool(safe_name and safe_name == filename and safe_name.startswith(f"{safe_owner}-"))


    def _delete_local_avatar_media(url: str | None, *, owner: str | None = None) -> None:
        value = str(url or "").strip()
        prefixes = ("/media/avatars/", "/static/uploads/profile_avatars/")
        if not value.startswith(prefixes):
            return
        filename = os.path.basename(value)
        if owner and not _local_avatar_media_belongs_to_user(filename, owner):
            return
        avatar_path = _resolve_avatar_path(filename)
        if avatar_path is not None and avatar_path.is_file():
            avatar_path.unlink()


    def _delete_local_banner_media(url: str | None, *, owner: str | None = None) -> None:
        value = str(url or "").strip()
        prefixes = ("/media/profile-banners/", "/static/uploads/profile_banners/")
        if not value.startswith(prefixes):
            return
        filename = os.path.basename(value)
        if owner and not _local_banner_media_belongs_to_user(filename, owner):
            return
        banner_path = _resolve_banner_path(filename)
        if banner_path is not None and banner_path.is_file():
            banner_path.unlink()


    def _resolve_profile_post_path(filename: str):
        safe_name = secure_filename(filename or "")
        if not safe_name or safe_name != filename:
            return None
        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}:
            return None
        candidate = (Path(profile_post_folder) / safe_name).resolve()
        try:
            candidate.relative_to(Path(profile_post_folder).resolve())
        except Exception:
            return None
        if not candidate.is_file():
            return None
        return candidate


    def _profile_post_media_belongs_to_user(filename: str, owner: str | None) -> bool:
        owner = str(owner or "").strip()
        if not owner:
            return False
        safe_owner = secure_filename(owner) or "user"
        safe_name = secure_filename(filename or "")
        return bool(safe_name and safe_name.startswith(f"{safe_owner}-"))


    def _sanitize_profile_post_media_url(raw: str | None, *, owner: str | None = None) -> str | None:
        value = str(raw or "").strip()
        if not value:
            return ""
        if value.startswith("/media/profile-posts/"):
            filename = value.rsplit("/", 1)[-1]
            if _resolve_profile_post_path(filename) is None:
                return None
            # Local profile-post uploads are writable media.  Only let a post point
            # at media generated for the same account, otherwise one user could
            # attach another user's local file and later delete it through edit/delete.
            if owner and not _profile_post_media_belongs_to_user(filename, owner):
                return None
            return value
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            return None
        return value


    def _sanitize_profile_post_link(raw: str | None) -> str | None:
        value = str(raw or "").strip()
        if not value:
            return ""
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            return None
        return value


    def _normalize_profile_post_visibility(raw: str | None) -> str:
        value = str(raw or "friends").strip().lower()
        if value in {"everyone", "friends", "private", "room_members"}:
            return value
        if value in {"room", "room_member", "room_members_only", "roommates"}:
            return "room_members"
        if value in {"only_me", "me", "nobody", "private_only", "onlyme"}:
            return "private"
        return "friends"


    def _first_url_from_text(raw: str | None) -> str:
        text = str(raw or "")
        match = re.search(r"https?://[^\s<>\"]+", text)
        return match.group(0).strip() if match else ""


    def _users_are_friends(a: str, b: str) -> bool:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                  FROM friend_requests
                 WHERE ((from_user = %s AND to_user = %s)
                     OR (from_user = %s AND to_user = %s))
                   AND request_status = 'accepted'
                 LIMIT 1;
                """,
                (a, b, b, a),
            )
            return cur.fetchone() is not None


    def _profile_dt(value) -> str:
        return value.isoformat() if getattr(value, 'isoformat', None) else str(value or "")


    def _serialize_profile_post(row) -> dict:
        return {
            "id": int(row[0]),
            "author_username": str(row[1] or ""),
            "body": str(row[2] or ""),
            "visibility": str(row[3] or "friends"),
            "image_url": str(row[4] or ""),
            "gif_url": str(row[5] or ""),
            "link_url": str(row[6] or ""),
            "is_pinned": bool(row[7]),
            "is_featured": bool(row[8]),
            "created_at": _profile_dt(row[9]),
            "updated_at": _profile_dt(row[10]),
            "edited_at": _profile_dt(row[11]) if len(row) > 11 else "",
            "edit_count": int((row[12] if len(row) > 12 else 0) or 0),
            "moderated_by": str((row[13] if len(row) > 13 else "") or ""),
            "moderated_reason": str((row[14] if len(row) > 14 else "") or ""),
            "moderated_at": _profile_dt(row[15]) if len(row) > 15 else "",
            "reaction_count": 0,
            "viewer_reacted": False,
            "comment_count": 0,
            "comments_preview": [],
        }


    def _serialize_profile_comment(row, *, viewer: str, post_author: str) -> dict:
        author = str(row[2] or "")
        return {
            "id": int(row[0]),
            "post_id": int(row[1]),
            "author_username": author,
            "body": str(row[3] or ""),
            "created_at": row[4].isoformat() if getattr(row[4], 'isoformat', None) else str(row[4] or ""),
            "updated_at": row[5].isoformat() if getattr(row[5], 'isoformat', None) else str(row[5] or ""),
            "can_delete": bool(viewer and (viewer == author or viewer == post_author)),
        }


    def _current_user_profile_admin(username: str) -> bool:
        try:
            perms = set(get_user_permissions(str(username or "")))
        except Exception:
            perms = set()
        return "admin:basic" in perms or "moderation:suspend_user" in perms or "admin:settings" in perms


    def _profile_post_select_columns() -> str:
        return """
            id, author_username, body, visibility, image_url, gif_url, link_url,
            is_pinned, is_featured, created_at, updated_at, edited_at, edit_count,
            moderated_by, moderated_reason, moderated_at
        """


    def _sanitize_profile_post_body(raw: str | None, *, max_len: int = 1800) -> str:
        return sanitize_user_visible_text(raw, max_len=max_len, keep_newlines=True)


    def _emit_profile_notification(username: str, payload: dict) -> bool:
        username = str(username or "").strip()
        if not username or socketio is None:
            return False
        try:
            from realtime.state import user_sids
            sids = list(user_sids(username))
        except Exception:
            sids = []
        for sid in sids:
            try:
                socketio.emit("profile_post_notification", payload, to=sid)
            except Exception:
                pass
        return bool(sids)


    def _create_profile_post_notification(recipient: str, actor: str, notification_type: str, post_id: int, *, comment_id: int | None = None) -> dict | None:
        recipient = str(recipient or "").strip()
        actor = str(actor or "").strip()
        kind = str(notification_type or "").strip()
        if not recipient or not actor or recipient == actor or not kind:
            return None
        if not _profile_notification_enabled(recipient, kind):
            return None
        message = f"{actor} liked your profile post" if kind == "profile_post_like" else f"{actor} commented on your profile post"
        payload = {
            "id": 0,
            "type": kind,
            "actor": actor,
            "post_id": int(post_id),
            "comment_id": int(comment_id or 0),
            "message": message,
            "created_at": "",
        }
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notifications (user_id, notification, type)
                    SELECT id, %s, %s
                      FROM users
                     WHERE username = %s
                    RETURNING id, timestamp;
                    """,
                    (json.dumps(payload, separators=(",", ":")), kind, recipient),
                )
                row = cur.fetchone()
            conn.commit()
            if row:
                payload["id"] = int(row[0] or 0)
                payload["created_at"] = _profile_dt(row[1])
                _emit_profile_notification(recipient, payload)
                return payload
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logging.warning("Profile notification skipped: %s", exc)
        return None


    def _create_profile_system_notification(recipient: str, kind: str, message: str, payload_extra: dict | None = None) -> dict | None:
        recipient = str(recipient or "").strip()
        kind = str(kind or "profile_post_notice").strip() or "profile_post_notice"
        message = str(message or "").strip()[:500]
        if not recipient or not message:
            return None
        if not _profile_notification_enabled(recipient, kind):
            return None
        payload = {"id": 0, "type": kind, "message": message, "created_at": ""}
        if isinstance(payload_extra, dict):
            payload.update(payload_extra)
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notifications (user_id, notification, type)
                    SELECT id, %s, %s
                      FROM users
                     WHERE username = %s
                    RETURNING id, timestamp;
                    """,
                    (json.dumps(payload, separators=(",", ":")), kind, recipient),
                )
                row = cur.fetchone()
            conn.commit()
            if row:
                payload["id"] = int(row[0] or 0)
                payload["created_at"] = _profile_dt(row[1])
                _emit_profile_notification(recipient, payload)
                return payload
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logging.warning("Profile system notification skipped: %s", exc)
        return None


    def _serialize_profile_notification(row) -> dict:
        raw = str(row[1] or "")
        parsed = {}
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"message": raw}
        if not isinstance(parsed, dict):
            parsed = {"message": raw}
        parsed["id"] = int(row[0])
        parsed["type"] = str(row[2] or parsed.get("type") or "profile_post")
        parsed["is_read"] = bool(row[3])
        parsed["created_at"] = _profile_dt(row[4])
        return parsed


    def _profile_notification_visible_to_user(username: str, item: dict) -> bool:
        actor = str((item or {}).get("actor") or "").strip()
        username = str(username or "").strip()
        if not actor or not username or actor.lower() == username.lower():
            return True
        try:
            return not _either_blocked(username, actor)
        except Exception:
            # Fail closed for blocked-pair notification visibility.
            return False


    def _profile_post_placeholders(values: list[int]) -> str:
        return ",".join(["%s"] * len(values))


    def _hydrate_profile_post_engagement(posts: list[dict], viewer: str) -> list[dict]:
        """Attach like/comment counts and a small comment preview to post payloads."""
        ids = [int(p.get("id") or 0) for p in posts if int(p.get("id") or 0) > 0]
        if not ids:
            return posts
        post_by_id = {int(p.get("id")): p for p in posts if int(p.get("id") or 0) > 0}
        placeholders = _profile_post_placeholders(ids)
        conn = get_db()
        with conn.cursor() as cur:
            try:
                cur.execute(
                    f"""
                    SELECT post_id, COUNT(*)
                      FROM profile_post_reactions
                     WHERE reaction = 'like'
                       AND post_id IN ({placeholders})
                     GROUP BY post_id;
                    """,
                    tuple(ids),
                )
                for post_id, count in cur.fetchall() or []:
                    if int(post_id) in post_by_id:
                        post_by_id[int(post_id)]["reaction_count"] = int(count or 0)

                if viewer:
                    cur.execute(
                        f"""
                        SELECT post_id
                          FROM profile_post_reactions
                         WHERE username = %s
                           AND reaction = 'like'
                           AND post_id IN ({placeholders});
                        """,
                        tuple([viewer] + ids),
                    )
                    reacted = {int(row[0]) for row in (cur.fetchall() or [])}
                    for post_id in reacted:
                        if post_id in post_by_id:
                            post_by_id[post_id]["viewer_reacted"] = True

                cur.execute(
                    f"""
                    SELECT post_id, COUNT(*)
                      FROM profile_post_comments
                     WHERE deleted_at IS NULL
                       AND post_id IN ({placeholders})
                     GROUP BY post_id;
                    """,
                    tuple(ids),
                )
                for post_id, count in cur.fetchall() or []:
                    if int(post_id) in post_by_id:
                        post_by_id[int(post_id)]["comment_count"] = int(count or 0)

                cur.execute(
                    f"""
                    SELECT id, post_id, author_username, body, created_at, updated_at
                      FROM (
                            SELECT id, post_id, author_username, body, created_at, updated_at,
                                   ROW_NUMBER() OVER (PARTITION BY post_id ORDER BY created_at DESC, id DESC) AS rn
                              FROM profile_post_comments
                             WHERE deleted_at IS NULL
                               AND post_id IN ({placeholders})
                           ) latest
                     WHERE rn <= 3
                     ORDER BY post_id ASC, created_at ASC, id ASC;
                    """,
                    tuple(ids),
                )
                for row in cur.fetchall() or []:
                    post_id = int(row[1])
                    post = post_by_id.get(post_id)
                    if not post:
                        continue
                    post.setdefault("comments_preview", []).append(_serialize_profile_comment(row, viewer=viewer, post_author=str(post.get("author_username") or "")))
            except Exception as exc:
                # Legacy DBs that have not run the new migration should still be able
                # to load profiles; the migration/preflight path will create tables.
                logging.warning("Profile post engagement hydrate skipped: %s", exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
        return posts


    def _get_visible_profile_post_for_viewer(post_id: int, viewer: str):
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, author_username, visibility
                  FROM profile_posts
                 WHERE id = %s
                   AND deleted_at IS NULL
                 LIMIT 1;
                """,
                (int(post_id),),
            )
            row = cur.fetchone()
        if not row:
            return None, False, False, False
        author = str(row[1] or "")
        visibility = str(row[2] or "friends")
        is_self = bool(viewer and viewer == author)
        if not is_self and _either_blocked(viewer, author):
            return None, False, False, False
        is_friend = is_self or _users_are_friends(viewer, author)
        can_view = _profile_visibility_allows(viewer, author, visibility, is_self=is_self, is_friend=bool(is_friend))
        return {"id": int(row[0]), "author_username": author, "visibility": visibility}, bool(can_view), bool(is_self), bool(is_friend)


    def _profile_post_media_visible_to_viewer(filename: str, viewer: str) -> bool:
        """Require profile-post media to belong to the viewer or a post the viewer can see."""
        safe_name = secure_filename(filename or "")
        if not safe_name or safe_name != filename:
            return False
        if viewer and _profile_post_media_belongs_to_user(safe_name, viewer):
            return True
        media_url = f"/media/profile-posts/{safe_name}"
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                      FROM profile_posts
                     WHERE deleted_at IS NULL
                       AND (image_url = %s OR gif_url = %s)
                     ORDER BY created_at DESC, id DESC
                     LIMIT 12;
                    """,
                    (media_url, media_url),
                )
                rows = cur.fetchall() or []
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        for row in rows:
            try:
                _post, can_view, _is_self, _is_friend = _get_visible_profile_post_for_viewer(int(row[0]), viewer)
                if can_view:
                    return True
            except Exception:
                continue
        return False

    def _delete_local_profile_post_media(url: str | None, *, owner: str | None = None) -> None:
        value = str(url or "").strip()
        if not value.startswith("/media/profile-posts/"):
            return
        filename = value.rsplit("/", 1)[-1]
        if owner and not _profile_post_media_belongs_to_user(filename, owner):
            logging.warning("Skipped profile-post media delete for non-owned local file: owner=%s file=%s", owner, filename)
            return
        path = _resolve_profile_post_path(filename)
        if path is None:
            return
        try:
            os.remove(path)
        except Exception:
            pass


    @app.get("/media/profile-posts/<path:filename>")
    @jwt_required()
    def serve_profile_post_media(filename: str):
        viewer = get_jwt_identity()
        media_path = _resolve_profile_post_path(filename)
        if media_path is None:
            return jsonify({"error": "not_found"}), 404
        if not _profile_post_media_visible_to_viewer(filename, viewer):
            return jsonify({"error": "not_found"}), 404
        try:
            with open(media_path, "rb") as fh:
                header = fh.read(4096)
        except Exception:
            return jsonify({"error": "not_found"}), 404
        sniffed_ext, sniffed_mime = _sniff_image_type(header)
        if sniffed_ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}:
            return jsonify({"error": "not_found"}), 404
        resp = send_file(
            str(media_path),
            mimetype=sniffed_mime or "application/octet-stream",
            as_attachment=False,
            conditional=True,
            download_name=_secure_download_name(media_path.name, default="profile-post.bin"),
        )
        _apply_avatar_response_headers(resp)
        return resp


    @app.route("/api/profile/post_image_upload", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_post_image_upload") or "20 per hour")
    @jwt_required()
    def upload_profile_post_image():
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_image_upload", settings.get("rate_limit_profile_post_image_upload") or "20 per hour", default_limit=20, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="media")
        if denied is not None:
            return denied

        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"success": False, "error": "Missing file"}), 400

        content_length = request.content_length or 0
        if content_length and int(content_length) > max_profile_post_image_bytes + (256 * 1024):
            return jsonify({"success": False, "error": f"Image too large (max {max_profile_post_image_bytes} bytes)"}), 413

        original_name = secure_filename(f.filename) or "profile-post"
        tmp_bytes = f.read(max_profile_post_image_bytes + 1)
        if len(tmp_bytes) > max_profile_post_image_bytes:
            return jsonify({"success": False, "error": f"Image too large (max {max_profile_post_image_bytes} bytes)"}), 413

        sniffed_ext, _sniffed_mime = _sniff_image_type(tmp_bytes)
        if sniffed_ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}:
            return jsonify({"success": False, "error": "Profile posts only support PNG, JPG, GIF, WEBP, BMP, or ICO images"}), 400

        safe_user = secure_filename(user) or "user"
        ext = ".jpg" if sniffed_ext == ".jpeg" else sniffed_ext
        file_name = f"{safe_user}-{int(time.time())}-{secrets.token_hex(4)}{ext}"
        dest_path = os.path.join(profile_post_folder, file_name)
        try:
            with open(dest_path, "wb") as out:
                out.write(tmp_bytes)
        except Exception as e:
            logging.error("[UPLOAD ERROR] profile post image save failed: %s", e)
            return jsonify({"success": False, "error": "Failed to save image"}), 500

        image_url = f"/media/profile-posts/{file_name}"
        log_audit_event(user, "profile_post_image_upload", user, file_name)
        return jsonify({"success": True, "image_url": image_url})


    @app.get("/api/profile/posts")
    @jwt_required()
    def api_profile_posts():
        _ensure_profile_runtime_schema()
        viewer = get_jwt_identity()
        target = str(request.args.get("username") or viewer or "").strip()
        if not target:
            return jsonify({"success": False, "error": "missing_username"}), 400
        try:
            limit = int(request.args.get("limit") or 30)
        except Exception:
            limit = 30
        try:
            offset = int(request.args.get("offset") or 0)
        except Exception:
            offset = 0
        limit = max(1, min(80, limit))
        offset = max(0, min(5000, offset))

        is_self = viewer == target
        if not is_self and _either_blocked(viewer, target):
            return _profile_api_json({
                "success": True,
                "posts": [],
                "featured": [],
                "photos": [],
                "hidden": True,
                "limit": limit,
                "offset": offset,
                "total_count": 0,
                "has_more": False,
            })
        is_friend = is_self or _users_are_friends(viewer, target)

        where_parts = ["author_username = %s", "deleted_at IS NULL"]
        params = [target]
        if not is_self:
            shares_live_room = _users_share_live_room(viewer, target)
            if is_friend and shares_live_room:
                where_parts.append("visibility IN ('everyone', 'friends', 'room_members')")
            elif is_friend:
                where_parts.append("visibility IN ('everyone', 'friends')")
            elif shares_live_room:
                where_parts.append("visibility IN ('everyone', 'room_members')")
            else:
                where_parts.append("visibility = 'everyone'")

        where_sql = ' AND '.join(where_parts)
        sql = f"""
            SELECT id, author_username, body, visibility, image_url, gif_url, link_url,
                   is_pinned, is_featured, created_at, updated_at, edited_at, edit_count,
                   moderated_by, moderated_reason, moderated_at
              FROM profile_posts
             WHERE {where_sql}
             ORDER BY is_pinned DESC, created_at DESC, id DESC
             LIMIT %s OFFSET %s;
        """

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM profile_posts WHERE {where_sql};", tuple(params))
            total_count = int((cur.fetchone() or [0])[0] or 0)
            cur.execute(sql, tuple(params + [limit, offset]))
            rows = cur.fetchall() or []
        posts = _hydrate_profile_post_engagement([_serialize_profile_post(r) for r in rows], viewer)
        featured = [p for p in posts if p.get("is_featured")][:8]
        photos = [p for p in posts if p.get("image_url") or p.get("gif_url")][:24]
        return _profile_api_json({
            "success": True,
            "posts": posts,
            "featured": featured,
            "photos": photos,
            "is_self": is_self,
            "is_friend": bool(is_friend),
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "has_more": offset + len(posts) < total_count,
        })




    @app.get("/api/profile/gallery")
    @jwt_required()
    def api_profile_gallery():
        """Return the visible profile media gallery for one user.

        The gallery is derived from profile posts, so post visibility, blocks,
        deleted/moderated rows, and shared-room privacy remain the source of
        truth. This endpoint exists so the Photos tab can page/filter media
        without forcing the normal post feed to load every profile post.
        """
        _ensure_profile_runtime_schema()
        viewer = get_jwt_identity()
        target = str(request.args.get("username") or viewer or "").strip()
        if not target:
            return jsonify({"success": False, "error": "missing_username"}), 400
        try:
            limit = int(request.args.get("limit") or 72)
        except Exception:
            limit = 72
        try:
            offset = int(request.args.get("offset") or 0)
        except Exception:
            offset = 0
        limit = max(1, min(120, limit))
        offset = max(0, min(5000, offset))
        gallery_type = str(request.args.get("type") or request.args.get("filter") or "all").strip().lower()
        if gallery_type not in {"all", "photos", "gifs", "featured"}:
            gallery_type = "all"

        is_self = viewer == target
        if not is_self and _either_blocked(viewer, target):
            return _profile_api_json({
                "success": True,
                "items": [],
                "counts": {"all": 0, "photos": 0, "gifs": 0, "featured": 0},
                "hidden": True,
                "type": gallery_type,
                "limit": limit,
                "offset": offset,
                "has_more": False,
            })

        is_friend = is_self or _users_are_friends(viewer, target)
        where_parts = ["author_username = %s", "deleted_at IS NULL"]
        params = [target]
        if not is_self:
            shares_live_room = _users_share_live_room(viewer, target)
            if is_friend and shares_live_room:
                where_parts.append("visibility IN ('everyone', 'friends', 'room_members')")
            elif is_friend:
                where_parts.append("visibility IN ('everyone', 'friends')")
            elif shares_live_room:
                where_parts.append("visibility IN ('everyone', 'room_members')")
            else:
                where_parts.append("visibility = 'everyone'")

        media_any = "((image_url IS NOT NULL AND BTRIM(image_url) <> '') OR (gif_url IS NOT NULL AND BTRIM(gif_url) <> ''))"
        media_photo = "(image_url IS NOT NULL AND BTRIM(image_url) <> '')"
        media_gif = "(gif_url IS NOT NULL AND BTRIM(gif_url) <> '')"
        filter_sql = media_any
        if gallery_type == "photos":
            filter_sql = media_photo
        elif gallery_type == "gifs":
            filter_sql = media_gif
        elif gallery_type == "featured":
            filter_sql = f"(is_featured = TRUE AND {media_any})"

        base_where = " AND ".join(where_parts)
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE {media_any}) AS all_count,
                    COUNT(*) FILTER (WHERE {media_photo}) AS photo_count,
                    COUNT(*) FILTER (WHERE {media_gif}) AS gif_count,
                    COUNT(*) FILTER (WHERE is_featured = TRUE AND {media_any}) AS featured_count
                  FROM profile_posts
                 WHERE {base_where};
                """,
                tuple(params),
            )
            count_row = cur.fetchone() or (0, 0, 0, 0)
            cur.execute(
                f"""
                SELECT {_profile_post_select_columns()}
                  FROM profile_posts
                 WHERE {base_where}
                   AND {filter_sql}
                 ORDER BY is_featured DESC, created_at DESC, id DESC
                 LIMIT %s OFFSET %s;
                """,
                tuple(params + [limit, offset]),
            )
            rows = cur.fetchall() or []

        posts = _hydrate_profile_post_engagement([_serialize_profile_post(r) for r in rows], viewer)
        items = []
        for post in posts:
            image_url = str(post.get("image_url") or "").strip()
            gif_url = str(post.get("gif_url") or "").strip()
            media_url = image_url or gif_url
            if not media_url:
                continue
            body = str(post.get("body") or "").strip()
            items.append({
                "id": int(post.get("id") or 0),
                "post_id": int(post.get("id") or 0),
                "author_username": str(post.get("author_username") or ""),
                "media_url": media_url,
                "media_type": "gif" if gif_url else "photo",
                "image_url": image_url,
                "gif_url": gif_url,
                "body_excerpt": (body[:117] + "…") if len(body) > 120 else body,
                "visibility": str(post.get("visibility") or "friends"),
                "is_featured": bool(post.get("is_featured")),
                "created_at": str(post.get("created_at") or ""),
                "reaction_count": int(post.get("reaction_count") or 0),
                "comment_count": int(post.get("comment_count") or 0),
                "viewer_reacted": bool(post.get("viewer_reacted")),
                "can_manage": bool(is_self),
            })

        counts = {
            "all": int(count_row[0] or 0),
            "photos": int(count_row[1] or 0),
            "gifs": int(count_row[2] or 0),
            "featured": int(count_row[3] or 0),
        }
        return _profile_api_json({
            "success": True,
            "items": items,
            "counts": counts,
            "type": gallery_type,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < counts.get(gallery_type if gallery_type in counts else "all", 0),
            "is_self": bool(is_self),
            "is_friend": bool(is_friend),
        })


    @app.route("/api/profile/posts", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_post_create") or "30 per hour")
    @jwt_required()
    def create_profile_post():
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_create", settings.get("rate_limit_profile_post_create") or "30 per hour", default_limit=30, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="post")
        if denied is not None:
            return denied

        payload = request.get_json(silent=True) or {}
        body = _sanitize_profile_post_body(payload.get("body"), max_len=1800)
        raw_visibility = payload.get("visibility")
        if raw_visibility in {None, ""}:
            try:
                conn_default = get_db()
                with conn_default.cursor() as cur_default:
                    cur_default.execute("SELECT profile_post_default_visibility FROM users WHERE username = %s LIMIT 1;", (user,))
                    row_default = cur_default.fetchone()
                    raw_visibility = row_default[0] if row_default else "friends"
            except Exception:
                raw_visibility = "friends"
        visibility = _normalize_profile_post_visibility(raw_visibility)
        image_url = _sanitize_profile_post_media_url(payload.get("image_url") or payload.get("imageUrl"), owner=user)
        gif_url = _sanitize_profile_post_media_url(payload.get("gif_url") or payload.get("gifUrl"), owner=user)
        link_url = _sanitize_profile_post_link(payload.get("link_url") or payload.get("linkUrl") or _first_url_from_text(body))
        pin_post = bool(payload.get("pin_post") or payload.get("pinPost"))
        feature_post = bool(payload.get("feature_post") or payload.get("featurePost"))

        if image_url is None or gif_url is None or link_url is None:
            return jsonify({"success": False, "error": "Invalid image, GIF, or link URL"}), 400
        if not body and not image_url and not gif_url and not link_url:
            return jsonify({"success": False, "error": "Write something or add a GIF, image, or link"}), 400

        conn = get_db()
        with conn.cursor() as cur:
            if pin_post:
                cur.execute("UPDATE profile_posts SET is_pinned = FALSE WHERE author_username = %s;", (user,))
            cur.execute(
                """
                INSERT INTO profile_posts (author_username, body, visibility, image_url, gif_url, link_url, is_pinned, is_featured)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, author_username, body, visibility, image_url, gif_url, link_url, is_pinned, is_featured, created_at, updated_at;
                """,
                (user, body or None, visibility, image_url or None, gif_url or None, link_url or None, bool(pin_post), bool(feature_post)),
            )
            row = cur.fetchone()
        conn.commit()
        log_audit_event(user, "profile_post_create", user, f"visibility={visibility}")
        return jsonify({"success": True, "post": _serialize_profile_post(row)})


    @app.route("/api/profile/posts/<int:post_id>", methods=["PUT", "PATCH"])
    @_limit(settings.get("rate_limit_profile_post_edit") or "40 per hour")
    @jwt_required()
    def edit_profile_post(post_id: int):
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_edit", settings.get("rate_limit_profile_post_edit") or "40 per hour", default_limit=40, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="edit")
        if denied is not None:
            return denied

        payload = request.get_json(silent=True) or {}
        body = _sanitize_profile_post_body(payload.get("body"), max_len=1800)
        visibility = _normalize_profile_post_visibility(payload.get("visibility"))
        image_url = _sanitize_profile_post_media_url(payload.get("image_url") or payload.get("imageUrl"), owner=user)
        gif_url = _sanitize_profile_post_media_url(payload.get("gif_url") or payload.get("gifUrl"), owner=user)
        link_url = _sanitize_profile_post_link(payload.get("link_url") or payload.get("linkUrl") or _first_url_from_text(body))
        pin_post = bool(payload.get("is_pinned", payload.get("pin_post", False)))
        feature_post = bool(payload.get("is_featured", payload.get("feature_post", False)))

        if image_url is None or gif_url is None or link_url is None:
            return jsonify({"success": False, "error": "Invalid image, GIF, or link URL"}), 400
        if not body and not image_url and not gif_url and not link_url:
            return jsonify({"success": False, "error": "Post needs text, an image/GIF, or a link"}), 400

        conn = get_db()
        old_image_url = ""
        old_gif_url = ""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT author_username, image_url, gif_url
                  FROM profile_posts
                 WHERE id = %s
                   AND deleted_at IS NULL
                 LIMIT 1;
                """,
                (int(post_id),),
            )
            existing = cur.fetchone()
            if not existing or str(existing[0] or "") != user:
                return jsonify({"success": False, "error": "not_found"}), 404
            old_image_url = str(existing[1] or "")
            old_gif_url = str(existing[2] or "")
            if pin_post:
                cur.execute("UPDATE profile_posts SET is_pinned = FALSE WHERE author_username = %s AND id <> %s;", (user, int(post_id)))
            cur.execute(
                """
                UPDATE profile_posts
                   SET body = %s,
                       visibility = %s,
                       image_url = %s,
                       gif_url = %s,
                       link_url = %s,
                       is_pinned = %s,
                       is_featured = %s,
                       updated_at = CURRENT_TIMESTAMP,
                       edited_at = CURRENT_TIMESTAMP,
                       edit_count = COALESCE(edit_count, 0) + 1
                 WHERE id = %s
                RETURNING id, author_username, body, visibility, image_url, gif_url, link_url,
                          is_pinned, is_featured, created_at, updated_at, edited_at, edit_count,
                          moderated_by, moderated_reason, moderated_at;
                """,
                (body or None, visibility, image_url or None, gif_url or None, link_url or None, bool(pin_post), bool(feature_post), int(post_id)),
            )
            row = cur.fetchone()
        conn.commit()
        if old_image_url and old_image_url != image_url:
            _delete_local_profile_post_media(old_image_url, owner=user)
        if old_gif_url and old_gif_url != gif_url:
            _delete_local_profile_post_media(old_gif_url, owner=user)
        log_audit_event(user, "profile_post_edit", user, f"post_id={int(post_id)} visibility={visibility}")
        post_payload = _hydrate_profile_post_engagement([_serialize_profile_post(row)], user)[0] if row else None
        return jsonify({"success": True, "post": post_payload})


    @app.get("/api/profile/notification_settings")
    @jwt_required()
    def get_profile_notification_settings():
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_notification_settings", settings.get("rate_limit_profile_notification_settings") or "120 per hour", default_limit=120, default_window=3600, user=user)
        if guard is not None:
            return guard
        return _profile_api_json({"success": True, "settings": _profile_notification_settings_for(user)})


    @app.route("/api/profile/notification_settings", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_notification_settings") or "120 per hour")
    @jwt_required()
    def save_profile_notification_settings():
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_notification_settings", settings.get("rate_limit_profile_notification_settings") or "120 per hour", default_limit=120, default_window=3600, user=user)
        if guard is not None:
            return guard
        payload = request.get_json(silent=True) or {}
        values = {
            key: _coerce_profile_notification_bool(payload.get(key), default)
            for key, default in _PROFILE_NOTIFICATION_DEFAULTS.items()
        }
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_profile_notification_settings (
                    username, notify_likes, notify_comments, notify_admin_notices,
                    notify_report_updates, notify_profile_views, notify_friend_posts, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (username) DO UPDATE SET
                    notify_likes = EXCLUDED.notify_likes,
                    notify_comments = EXCLUDED.notify_comments,
                    notify_admin_notices = EXCLUDED.notify_admin_notices,
                    notify_report_updates = EXCLUDED.notify_report_updates,
                    notify_profile_views = EXCLUDED.notify_profile_views,
                    notify_friend_posts = EXCLUDED.notify_friend_posts,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (
                    user,
                    values["notify_likes"],
                    values["notify_comments"],
                    values["notify_admin_notices"],
                    values["notify_report_updates"],
                    values["notify_profile_views"],
                    values["notify_friend_posts"],
                ),
            )
        conn.commit()
        log_audit_event(user, "profile_notification_settings_update", user, json.dumps(values, separators=(",", ":")))
        return _profile_api_json({"success": True, "settings": values})


    @app.get("/api/profile/notifications")
    @jwt_required()
    def list_profile_post_notifications():
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_notifications", settings.get("rate_limit_profile_notifications") or "240 per hour", default_limit=240, default_window=3600, user=user)
        if guard is not None:
            return guard
        unread_only = str(request.args.get("unread_only") or "0").strip().lower() in {"1", "true", "yes", "on"}
        try:
            limit = max(1, min(100, int(request.args.get("limit") or 25)))
        except Exception:
            limit = 25
        where = "n.user_id = u.id AND u.username = %s AND n.type LIKE 'profile_post_%%'"
        params = [user]
        if unread_only:
            where += " AND COALESCE(n.is_read, FALSE) = FALSE"
        # Fetch extra rows because blocked-pair notifications are filtered after
        # JSON parsing. The returned list still honors the requested limit.
        fetch_limit = max(limit, min(500, limit * 5))
        params.append(fetch_limit)
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT n.id, n.notification, n.type, COALESCE(n.is_read, FALSE), n.timestamp
                  FROM notifications n
                  JOIN users u ON u.id = n.user_id
                 WHERE {where}
                 ORDER BY n.timestamp DESC, n.id DESC
                 LIMIT %s;
                """,
                tuple(params),
            )
            raw_rows = cur.fetchall() or []
            cur.execute(
                """
                SELECT n.id, n.notification, n.type, COALESCE(n.is_read, FALSE), n.timestamp
                  FROM notifications n
                  JOIN users u ON u.id = n.user_id
                 WHERE n.user_id = u.id
                   AND u.username = %s
                   AND n.type LIKE 'profile_post_%%'
                   AND COALESCE(n.is_read, FALSE) = FALSE
                 ORDER BY n.timestamp DESC, n.id DESC
                 LIMIT 5000;
                """,
                (user,),
            )
            unread_rows = cur.fetchall() or []
        items = []
        for row in raw_rows:
            item = _serialize_profile_notification(row)
            if _profile_notification_visible_to_user(user, item):
                items.append(item)
            if len(items) >= limit:
                break
        unread_count = sum(1 for row in unread_rows if _profile_notification_visible_to_user(user, _serialize_profile_notification(row)))
        return _profile_api_json({"success": True, "notifications": items, "unread_count": unread_count})


    @app.route("/api/profile/notifications/read", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_notifications_read") or "120 per hour")
    @jwt_required()
    def mark_profile_post_notifications_read():
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_notifications_read", settings.get("rate_limit_profile_notifications_read") or "120 per hour", default_limit=120, default_window=3600, user=user)
        if guard is not None:
            return guard
        payload = request.get_json(silent=True) or {}
        mark_all = bool(payload.get("all"))
        ids = []
        if not mark_all:
            raw_ids = payload.get("ids") or []
            if not isinstance(raw_ids, list):
                raw_ids = [raw_ids]
            for item in raw_ids:
                try:
                    iid = int(item)
                    if iid > 0 and iid not in ids:
                        ids.append(iid)
                except Exception:
                    pass
            ids = ids[:100]
            if not ids:
                return _profile_api_json({"success": False, "error": "ids_required"}, 400)
        conn = get_db()
        updated_count = 0
        unread_count = 0
        with conn.cursor() as cur:
            if mark_all:
                cur.execute(
                    """
                    UPDATE notifications n
                       SET is_read = TRUE
                      FROM users u
                     WHERE n.user_id = u.id
                       AND u.username = %s
                       AND n.type LIKE 'profile_post_%%'
                       AND COALESCE(n.is_read, FALSE) = FALSE;
                    """,
                    (user,),
                )
            else:
                cur.execute(
                    f"""
                    UPDATE notifications n
                       SET is_read = TRUE
                      FROM users u
                     WHERE n.user_id = u.id
                       AND u.username = %s
                       AND n.type LIKE 'profile_post_%%'
                       AND COALESCE(n.is_read, FALSE) = FALSE
                       AND n.id IN ({_profile_post_placeholders(ids)});
                    """,
                    tuple([user] + ids),
                )
            updated_count = int(getattr(cur, "rowcount", 0) or 0)
            cur.execute(
                """
                SELECT COUNT(*)
                  FROM notifications n
                  JOIN users u ON u.id = n.user_id
                 WHERE u.username = %s
                   AND n.type LIKE 'profile_post_%%'
                   AND COALESCE(n.is_read, FALSE) = FALSE;
                """,
                (user,),
            )
            unread_count = int((cur.fetchone() or [0])[0] or 0)
        conn.commit()
        return _profile_api_json({"success": True, "updated_count": updated_count, "unread_count": unread_count, "ids": ids if not mark_all else []})


    @app.route("/api/profile/posts/<int:post_id>/react", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_post_react") or "120 per hour")
    @jwt_required()
    def react_profile_post(post_id: int):
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_react", settings.get("rate_limit_profile_post_react") or "120 per hour", default_limit=120, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="reaction")
        if denied is not None:
            return denied
        post, can_view, _is_self, _is_friend = _get_visible_profile_post_for_viewer(int(post_id), user)
        if not post or not can_view:
            return jsonify({"success": False, "error": "not_found"}), 404
        payload = request.get_json(silent=True) or {}
        requested_state = payload.get("state", None)
        conn = get_db()
        did_create_like = False
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM profile_post_reactions WHERE post_id = %s AND username = %s AND reaction = 'like' LIMIT 1;",
                (int(post_id), user),
            )
            already = cur.fetchone() is not None
            state = (not already) if requested_state is None else bool(requested_state)
            if state:
                cur.execute(
                    """
                    INSERT INTO profile_post_reactions (post_id, username, reaction)
                    VALUES (%s, %s, 'like')
                    ON CONFLICT (post_id, username, reaction) DO NOTHING;
                    """,
                    (int(post_id), user),
                )
                did_create_like = int(getattr(cur, "rowcount", 0) or 0) > 0
            else:
                cur.execute(
                    "DELETE FROM profile_post_reactions WHERE post_id = %s AND username = %s AND reaction = 'like';",
                    (int(post_id), user),
                )
            cur.execute("SELECT COUNT(*) FROM profile_post_reactions WHERE post_id = %s AND reaction = 'like';", (int(post_id),))
            count = int((cur.fetchone() or [0])[0] or 0)
        conn.commit()
        if bool(state) and did_create_like:
            _create_profile_post_notification(post["author_username"], user, "profile_post_like", int(post_id))
        log_audit_event(user, "profile_post_react", post["author_username"], f"post_id={int(post_id)} state={bool(state)}")
        return jsonify({"success": True, "viewer_reacted": bool(state), "reaction_count": count})


    @app.route("/api/profile/posts/<int:post_id>/comments", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_post_comment") or "60 per hour")
    @jwt_required()
    def create_profile_post_comment(post_id: int):
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_comment", settings.get("rate_limit_profile_post_comment") or "60 per hour", default_limit=60, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="comment")
        if denied is not None:
            return denied
        post, can_view, _is_self, _is_friend = _get_visible_profile_post_for_viewer(int(post_id), user)
        if not post or not can_view:
            return jsonify({"success": False, "error": "not_found"}), 404
        payload = request.get_json(silent=True) or {}
        body = _sanitize_profile_post_body(payload.get("body"), max_len=700)
        if not body:
            return jsonify({"success": False, "error": "Write a comment first"}), 400

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO profile_post_comments (post_id, author_username, body)
                VALUES (%s, %s, %s)
                RETURNING id, post_id, author_username, body, created_at, updated_at;
                """,
                (int(post_id), user, body),
            )
            row = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM profile_post_comments WHERE post_id = %s AND deleted_at IS NULL;", (int(post_id),))
            count = int((cur.fetchone() or [0])[0] or 0)
        conn.commit()
        comment_payload = _serialize_profile_comment(row, viewer=user, post_author=post["author_username"])
        _create_profile_post_notification(post["author_username"], user, "profile_post_comment", int(post_id), comment_id=comment_payload.get("id"))
        log_audit_event(user, "profile_post_comment", post["author_username"], f"post_id={int(post_id)}")
        return jsonify({"success": True, "comment": comment_payload, "comment_count": count})


    @app.route("/api/profile/posts/<int:post_id>/comments/<int:comment_id>", methods=["DELETE"])
    @_limit(settings.get("rate_limit_profile_post_comment_delete") or "80 per hour")
    @jwt_required()
    def delete_profile_post_comment(post_id: int, comment_id: int):
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_comment_delete", settings.get("rate_limit_profile_post_comment_delete") or "80 per hour", default_limit=80, default_window=3600, user=user)
        if guard is not None:
            return guard
        post, can_view, is_self, _is_friend = _get_visible_profile_post_for_viewer(int(post_id), user)
        if not post or not can_view:
            return jsonify({"success": False, "error": "not_found"}), 404

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT author_username
                  FROM profile_post_comments
                 WHERE id = %s
                   AND post_id = %s
                   AND deleted_at IS NULL
                 LIMIT 1;
                """,
                (int(comment_id), int(post_id)),
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"success": False, "error": "not_found"}), 404
            comment_author = str(row[0] or "")
            if not (is_self or comment_author == user):
                return jsonify({"success": False, "error": "not_allowed"}), 403
            cur.execute(
                """
                UPDATE profile_post_comments
                   SET deleted_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP,
                       deleted_by = %s,
                       deleted_reason = %s
                 WHERE id = %s
                   AND post_id = %s;
                """,
                (user, "Deleted by profile owner or comment author", int(comment_id), int(post_id)),
            )
            cur.execute("SELECT COUNT(*) FROM profile_post_comments WHERE post_id = %s AND deleted_at IS NULL;", (int(post_id),))
            count = int((cur.fetchone() or [0])[0] or 0)
        conn.commit()
        log_audit_event(user, "profile_post_comment_delete", post["author_username"], f"post_id={int(post_id)} comment_id={int(comment_id)}")
        return jsonify({"success": True, "comment_count": count})


    def _normalize_profile_report_reason(raw: str | None) -> str:
        value = re.sub(r"[^a-z0-9_-]", "", str(raw or "other").strip().lower().replace(" ", "_"))[:40]
        return value if value in {"spam", "harassment", "hate", "sexual", "violence", "impersonation", "scam", "privacy", "other"} else "other"


    @app.route("/api/profile/posts/<int:post_id>/report", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_post_report") or "20 per hour")
    @jwt_required()
    def report_profile_post(post_id: int):
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_report", settings.get("rate_limit_profile_post_report") or "20 per hour", default_limit=20, default_window=3600, user=user)
        if guard is not None:
            return guard
        post, can_view, _is_self, _is_friend = _get_visible_profile_post_for_viewer(int(post_id), user)
        if not post or not can_view:
            return jsonify({"success": False, "error": "not_found"}), 404

        payload = request.get_json(silent=True) or {}
        reason = _normalize_profile_report_reason(payload.get("reason"))
        details = _sanitize_profile_post_body(payload.get("details") or payload.get("body"), max_len=700)
        try:
            comment_id = int(payload.get("comment_id") or payload.get("commentId") or 0)
        except Exception:
            comment_id = 0
        target_username = str(post.get("author_username") or "")
        conn = get_db()
        with conn.cursor() as cur:
            if comment_id > 0:
                cur.execute(
                    """
                    SELECT author_username
                      FROM profile_post_comments
                     WHERE id = %s
                       AND post_id = %s
                       AND deleted_at IS NULL
                     LIMIT 1;
                    """,
                    (int(comment_id), int(post_id)),
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({"success": False, "error": "comment_not_found"}), 404
                target_username = str(row[0] or target_username)
            if target_username == user:
                return jsonify({"success": False, "error": "cannot_report_own_content"}), 400
            cur.execute(
                """
                SELECT id
                  FROM profile_post_reports
                 WHERE reporter_username = %s
                   AND post_id = %s
                   AND COALESCE(comment_id, 0) = %s
                   AND status = 'open'
                 LIMIT 1;
                """,
                (user, int(post_id), int(comment_id or 0)),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE profile_post_reports
                       SET reason = %s, details = %s, updated_at = CURRENT_TIMESTAMP
                     WHERE id = %s
                    RETURNING id;
                    """,
                    (reason, details or None, int(existing[0])),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO profile_post_reports (reporter_username, post_id, comment_id, target_username, reason, details)
                    VALUES (%s, %s, NULLIF(%s, 0), %s, %s, %s)
                    RETURNING id;
                    """,
                    (user, int(post_id), int(comment_id or 0), target_username, reason, details or None),
                )
            report_row = cur.fetchone()
        conn.commit()
        report_id = int((report_row or [0])[0] or 0)
        log_audit_event(user, "profile_post_report", target_username, f"post_id={int(post_id)} comment_id={int(comment_id or 0)} reason={reason}")
        return jsonify({"success": True, "report_id": report_id, "status": "open"})


    @app.route("/api/profile/posts/<int:post_id>/pin", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_post_pin") or "60 per hour")
    @jwt_required()
    def pin_profile_post(post_id: int):
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_pin", settings.get("rate_limit_profile_post_pin") or "60 per hour", default_limit=60, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="pin")
        if denied is not None:
            return denied
        payload = request.get_json(silent=True) or {}
        state = bool(payload.get("state", True))
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT author_username FROM profile_posts WHERE id = %s AND deleted_at IS NULL LIMIT 1;", (int(post_id),))
            row = cur.fetchone()
            if not row or str(row[0]) != user:
                return jsonify({"success": False, "error": "not_found"}), 404
            if state:
                cur.execute("UPDATE profile_posts SET is_pinned = FALSE WHERE author_username = %s;", (user,))
            cur.execute("UPDATE profile_posts SET is_pinned = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s;", (bool(state), int(post_id)))
        conn.commit()
        log_audit_event(user, "profile_post_pin", user, f"post_id={int(post_id)} state={bool(state)}")
        return jsonify({"success": True, "is_pinned": bool(state)})


    @app.route("/api/profile/posts/<int:post_id>/feature", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_post_feature") or "60 per hour")
    @jwt_required()
    def feature_profile_post(post_id: int):
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_feature", settings.get("rate_limit_profile_post_feature") or "60 per hour", default_limit=60, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="feature")
        if denied is not None:
            return denied
        payload = request.get_json(silent=True) or {}
        state = bool(payload.get("state", True))
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT author_username FROM profile_posts WHERE id = %s AND deleted_at IS NULL LIMIT 1;", (int(post_id),))
            row = cur.fetchone()
            if not row or str(row[0]) != user:
                return jsonify({"success": False, "error": "not_found"}), 404
            cur.execute("UPDATE profile_posts SET is_featured = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s;", (bool(state), int(post_id)))
        conn.commit()
        log_audit_event(user, "profile_post_feature", user, f"post_id={int(post_id)} state={bool(state)}")
        return jsonify({"success": True, "is_featured": bool(state)})


    @app.route("/api/profile/posts/<int:post_id>", methods=["DELETE"])
    @_limit(settings.get("rate_limit_profile_post_delete") or "40 per hour")
    @jwt_required()
    def delete_profile_post(post_id: int):
        _ensure_profile_runtime_schema()
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("profile_post_delete", settings.get("rate_limit_profile_post_delete") or "40 per hour", default_limit=40, default_window=3600, user=user)
        if guard is not None:
            return guard
        conn = get_db()
        image_url = ""
        with conn.cursor() as cur:
            cur.execute("SELECT author_username, image_url, gif_url FROM profile_posts WHERE id = %s AND deleted_at IS NULL LIMIT 1;", (int(post_id),))
            row = cur.fetchone()
            if not row or str(row[0]) != user:
                return jsonify({"success": False, "error": "not_found"}), 404
            image_url = str(row[1] or "")
            gif_url = str(row[2] or "")
            cur.execute("UPDATE profile_posts SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, is_pinned = FALSE, is_featured = FALSE WHERE id = %s;", (int(post_id),))
        conn.commit()
        _delete_local_profile_post_media(image_url, owner=user)
        _delete_local_profile_post_media(gif_url, owner=user)
        log_audit_event(user, "profile_post_delete", user, f"post_id={int(post_id)}")
        return jsonify({"success": True})


    @app.route("/upload", methods=["POST"])
    @_limit(settings.get("rate_limit_upload") or "20 per minute")
    @jwt_required()
    def upload_file():
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("legacy_upload", settings.get("rate_limit_upload") or "20 per minute", default_limit=20, default_window=60, user=user)
        if guard is not None:
            return guard
        if not enable_legacy_public_uploads:
            return jsonify({
                "error": "Legacy public uploads are disabled",
                "hint": "Use /api/dm_files/upload or /api/group_files/upload instead"
            }), 410

        if is_user_sanctioned(user, "upload"):
            return jsonify({"error": "Uploads are disabled for this account"}), 403

        if "file" not in request.files or "to" not in request.form:
            return jsonify({"error": "Missing file or recipient"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No selected file"}), 400

        receiver_raw = (request.form.get("to") or "").strip()
        if not receiver_raw:
            return jsonify({"error": "Missing recipient"}), 400
        receiver = _resolve_canonical_username(receiver_raw)
        if not receiver:
            return jsonify({"error": "Recipient not found"}), 404
        if receiver == user:
            return jsonify({"error": "Cannot send file to yourself"}), 400
        if _either_blocked(user, receiver):
            return jsonify({"error": "You cannot send files to this user."}), 403

        try:
            if request.content_length and int(request.content_length) > (max_legacy_public_upload_bytes + 256_000):
                return jsonify({"error": f"File too large (max {max_legacy_public_upload_bytes} bytes)"}), 413
        except Exception:
            pass

        original_name = secure_filename(file.filename) or "upload.bin"
        safe_ext = _safe_public_upload_ext(original_name)
        stored_name = f"{int(time.time())}-{secrets.token_hex(8)}{safe_ext}"
        filepath = os.path.join(legacy_public_upload_folder, stored_name)
        try:
            _save_filestorage_limited(file, filepath, max_legacy_public_upload_bytes)
        except ValueError:
            return jsonify({"error": f"File too large (max {max_legacy_public_upload_bytes} bytes)"}), 413
        except Exception as exc:
            logging.error("[UPLOAD ERROR] legacy upload save failed: %s", exc)
            return jsonify({"error": "Upload failed"}), 500

        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE username = %s LIMIT 1;", (receiver,))
                if cur.fetchone() is None:
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
                    return jsonify({"error": "Recipient not found"}), 404

                cur.execute(
                    """
                    INSERT INTO messages (sender, receiver, message, is_encrypted)
                    VALUES (%s, %s, %s, FALSE)
                    RETURNING id;
                    """,
                    (user, receiver, f"[file] {original_name}"),
                )
                message_id = cur.fetchone()[0]

                cur.execute(
                    """
                    INSERT INTO file_attachments (message_id, file_path, file_type, file_size)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (
                        message_id,
                        f"/static/uploads/legacy_public/{stored_name}",
                        (file.content_type or "application/octet-stream"),
                        os.path.getsize(filepath),
                    ),
                )
            conn.commit()
        except Exception as e:
            logging.error("[DB ERROR] Failed to save uploaded file record: %s", e)
            try:
                os.remove(filepath)
            except Exception:
                pass
            return jsonify({"error": "Database failure"}), 500

        log_audit_event(user, "legacy_public_file_upload", receiver, original_name)
        return jsonify({"status": "uploaded", "file": original_name})


    @app.get("/avatar-preset.svg")
    def avatar_preset_svg():
        style = str(request.args.get("style") or "persona").strip().lower()
        seed = str(request.args.get("seed") or "echo").strip()
        if style not in _AVATAR_PRESET_STYLES:
            return jsonify({"error": "invalid_style"}), 404
        if not seed or len(seed) > 128:
            return jsonify({"error": "invalid_seed"}), 400
        svg = _render_avatar_preset_svg(style, seed)
        resp = app.response_class(svg, mimetype="image/svg+xml")
        _apply_avatar_response_headers(resp, cache_seconds=86400)
        return resp


    @app.get("/media/avatars/<path:filename>")
    def serve_uploaded_avatar(filename: str):
        avatar_path = _resolve_avatar_path(filename)
        if avatar_path is None:
            return _missing_avatar_fallback_response(filename)

        try:
            with open(avatar_path, "rb") as fh:
                header = fh.read(4096)
        except Exception:
            return _missing_avatar_fallback_response(filename)

        sniffed_ext, sniffed_mime = _sniff_image_type(header)
        if not sniffed_ext or sniffed_ext not in _SAFE_AVATAR_EXTS:
            return _missing_avatar_fallback_response(filename)
        if sniffed_ext == ".svg" and not allow_svg_avatars:
            return _missing_avatar_fallback_response(filename)

        resp = send_file(
            str(avatar_path),
            mimetype=sniffed_mime or "application/octet-stream",
            as_attachment=False,
            conditional=True,
            download_name=_secure_download_name(avatar_path.name, default="avatar.bin"),
        )
        _apply_avatar_response_headers(resp)
        return resp


    @app.get("/media/profile-banners/<path:filename>")
    def serve_uploaded_profile_banner(filename: str):
        banner_path = _resolve_banner_path(filename)
        if banner_path is None:
            return jsonify({"error": "not_found"}), 404

        try:
            with open(banner_path, "rb") as fh:
                header = fh.read(4096)
        except Exception:
            return jsonify({"error": "not_found"}), 404

        sniffed_ext, sniffed_mime = _sniff_image_type(header)
        if not sniffed_ext or sniffed_ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}:
            return jsonify({"error": "not_found"}), 404

        resp = send_file(
            str(banner_path),
            mimetype=sniffed_mime or "application/octet-stream",
            as_attachment=False,
            conditional=True,
            download_name=_secure_download_name(banner_path.name, default="profile-banner.bin"),
        )
        _apply_avatar_response_headers(resp)
        return resp


    @app.route("/api/profile/avatar_upload", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_avatar_upload") or "10 per hour")
    @jwt_required()
    def upload_profile_avatar():
        user = get_jwt_identity()
        _ensure_profile_runtime_schema()
        guard = _route_rate_limit_guard("profile_avatar_upload", settings.get("rate_limit_profile_avatar_upload") or "10 per hour", default_limit=10, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="avatar")
        if denied is not None:
            return denied

        try:
            os.makedirs(profile_avatar_folder, exist_ok=True)
        except Exception as e:
            logging.error("[UPLOAD ERROR] profile avatar folder unavailable: %s", e)
            return jsonify({"success": False, "error": "Avatar upload folder is not writable"}), 500

        f = request.files.get("file")
        if not f:
            return jsonify({"success": False, "error": "Missing file"}), 400
        if not f.filename:
            return jsonify({"success": False, "error": "Missing filename"}), 400

        content_length = request.content_length or 0
        if content_length and int(content_length) > max_profile_avatar_bytes + (256 * 1024):
            return jsonify({"success": False, "error": f"Avatar too large (max {max_profile_avatar_bytes} bytes)"}), 413

        original_name = secure_filename(f.filename) or "avatar"
        tmp_bytes = f.read(max_profile_avatar_bytes + 1)
        if len(tmp_bytes) > max_profile_avatar_bytes:
            return jsonify({"success": False, "error": f"Avatar too large (max {max_profile_avatar_bytes} bytes)"}), 413

        sniffed_ext, sniffed_mime = _sniff_image_type(tmp_bytes)
        if not sniffed_ext:
            return jsonify({"success": False, "error": "Avatar must be a real image file"}), 400
        if sniffed_ext == ".svg" and not allow_svg_avatars:
            return jsonify({"success": False, "error": "SVG avatars are disabled for security; use PNG, JPG, JPEG, GIF, or WEBP"}), 400

        allowed_exts = {".png", ".jpg", ".gif", ".webp", ".bmp", ".ico"}
        if allow_svg_avatars:
            allowed_exts.add(".svg")
        if sniffed_ext not in allowed_exts:
            return jsonify({"success": False, "error": "Avatar format is not allowed"}), 400

        safe_user = secure_filename(user) or "user"
        ext = ".jpg" if sniffed_ext == ".jpeg" else sniffed_ext
        file_name = f"{safe_user}-{int(time.time())}-{secrets.token_hex(4)}{ext}"
        dest_path = os.path.join(profile_avatar_folder, file_name)

        try:
            with open(dest_path, "wb") as out:
                out.write(tmp_bytes)
        except Exception as e:
            logging.error("[UPLOAD ERROR] profile avatar save failed: %s", e)
            return jsonify({"success": False, "error": "Failed to save avatar"}), 500

        avatar_url = f"/media/avatars/{file_name}"

        old_avatar_url = None
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT avatar_url FROM users WHERE username = %s LIMIT 1;", (user,))
                row = cur.fetchone()
                old_avatar_url = row[0] if row else None
                cur.execute(
                    "UPDATE users SET avatar_url = %s WHERE username = %s;",
                    (avatar_url, user),
                )
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                os.remove(dest_path)
            except Exception:
                pass
            logging.error("[DB ERROR] profile avatar update failed: %s", e)
            return jsonify({"success": False, "error": "Database error"}), 500

        try:
            if str(old_avatar_url or "") != avatar_url:
                _delete_local_avatar_media(old_avatar_url, owner=user)
        except Exception:
            pass

        profile = _profile_payload_for_user(user)
        log_audit_event(user, "avatar_upload", user, file_name)
        return jsonify({"success": True, "avatar_url": avatar_url, "profile": profile})


    @app.route("/api/profile/banner_upload", methods=["POST"])
    @_limit(settings.get("rate_limit_profile_banner_upload") or "10 per hour")
    @jwt_required()
    def upload_profile_banner():
        user = get_jwt_identity()
        _ensure_profile_runtime_schema()
        guard = _route_rate_limit_guard("profile_banner_upload", settings.get("rate_limit_profile_banner_upload") or "10 per hour", default_limit=10, default_window=3600, user=user)
        if guard is not None:
            return guard
        denied = _profile_write_denial_response(user, action="banner")
        if denied is not None:
            return denied

        f = request.files.get("file")
        if not f:
            return jsonify({"success": False, "error": "Missing file"}), 400
        if not f.filename:
            return jsonify({"success": False, "error": "Missing filename"}), 400

        content_length = request.content_length or 0
        if content_length and int(content_length) > max_profile_banner_bytes + (256 * 1024):
            return jsonify({"success": False, "error": f"Banner too large (max {max_profile_banner_bytes} bytes)"}), 413

        tmp_bytes = f.read(max_profile_banner_bytes + 1)
        if len(tmp_bytes) > max_profile_banner_bytes:
            return jsonify({"success": False, "error": f"Banner too large (max {max_profile_banner_bytes} bytes)"}), 413

        sniffed_ext, _sniffed_mime = _sniff_image_type(tmp_bytes)
        if sniffed_ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}:
            return jsonify({"success": False, "error": "Banner must be PNG, JPG, GIF, WEBP, BMP, or ICO"}), 400

        safe_user = secure_filename(user) or "user"
        ext = ".jpg" if sniffed_ext == ".jpeg" else sniffed_ext
        file_name = f"{safe_user}-{int(time.time())}-{secrets.token_hex(4)}{ext}"
        dest_path = os.path.join(profile_banner_folder, file_name)

        try:
            with open(dest_path, "wb") as out:
                out.write(tmp_bytes)
        except Exception as e:
            logging.error("[UPLOAD ERROR] profile banner save failed: %s", e)
            return jsonify({"success": False, "error": "Failed to save banner"}), 500

        banner_url = f"/media/profile-banners/{file_name}"

        old_banner_url = None
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT banner_url FROM users WHERE username = %s LIMIT 1;", (user,))
                row = cur.fetchone()
                old_banner_url = row[0] if row else None
                cur.execute(
                    "UPDATE users SET banner_url = %s WHERE username = %s;",
                    (banner_url, user),
                )
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                os.remove(dest_path)
            except Exception:
                pass
            logging.error("[DB ERROR] profile banner update failed: %s", e)
            return jsonify({"success": False, "error": "Database error"}), 500

        try:
            if str(old_banner_url or "") != banner_url:
                _delete_local_banner_media(old_banner_url, owner=user)
        except Exception:
            pass

        profile = _profile_payload_for_user(user)
        log_audit_event(user, "banner_upload", user, file_name)
        return jsonify({"success": True, "banner_url": banner_url, "profile": profile})


    # ------------------------------------------------------------------
    # Encrypted DM file transfers (ciphertext-only)
    # ------------------------------------------------------------------
    @app.route("/api/dm_files/upload", methods=["POST"])
    @_limit(settings.get("rate_limit_dm_file_upload") or "10 per minute")
    @jwt_required()
    def upload_dm_file_ciphertext():
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("dm_file_upload", settings.get("rate_limit_dm_file_upload") or "10 per minute", default_limit=10, default_window=60, user=user)
        if guard is not None:
            return guard
        if disable_dm_files_globally:
            return _private_file_json({"success": False, "error": "File sharing is disabled"}, 403)
        """Upload an encrypted DM file blob.

        Client sends multipart/form-data:
          - to (recipient username)
          - file (ciphertext blob)
          - iv_b64
          - ek_to_b64
          - ek_from_b64
          - sha256 (optional; plaintext hash, client-provided)
          - original_name (optional; fallback to file.filename)
          - mime_type (optional; fallback to file.content_type)
        """
        # Basic multipart validation
        if "file" not in request.files:
            return _private_file_json({"success": False, "error": "Missing file"}, 400)

        to_user_raw = (request.form.get("to") or "").strip()
        if not to_user_raw:
            return _private_file_json({"success": False, "error": "Missing recipient"}, 400)
        to_user = _resolve_canonical_username(to_user_raw)
        if not to_user:
            return _private_file_json({"success": False, "error": "Recipient not found"}, 404)
        if to_user == user:
            return _private_file_json({"success": False, "error": "Cannot send file to yourself"}, 400)

        # Match Socket.IO DM policy and the dedicated upload-sanction policy.
        denial = _private_file_upload_denial(user, send_context=True)
        if denial is not None:
            payload, status = denial
            return _private_file_json(payload, status)

        # Block policy: either direction blocks file sends.
        if _either_blocked(user, to_user):
            return _private_file_json({"success": False, "error": "You cannot send files to this user."}, 403)

        iv_b64 = (request.form.get("iv_b64") or "").strip()
        ek_to_b64 = (request.form.get("ek_to_b64") or "").strip()
        ek_from_b64 = (request.form.get("ek_from_b64") or "").strip()
        if not iv_b64 or not ek_to_b64 or not ek_from_b64:
            return _private_file_json({"success": False, "error": "Missing encryption envelope fields"}, 400)
        if not (_base64ish(iv_b64, max_len=4096) and _base64ish(ek_to_b64) and _base64ish(ek_from_b64)):
            return _private_file_json({"success": False, "error": "Invalid encryption envelope fields"}, 400)

        # Lightweight upload size guard. request.content_length includes form overhead,
        # so allow a small cushion.
        try:
            if request.content_length and request.content_length > (max_dm_file_bytes + 256_000):
                return _private_file_json({"success": False, "error": f"File too large (max {max_dm_file_bytes} bytes)"}, 413)
        except Exception:
            pass

        f = request.files["file"]
        if not f or f.filename == "":
            return _private_file_json({"success": False, "error": "Empty filename"}, 400)

        original_name = _sanitize_private_file_name((request.form.get("original_name") or "").strip() or f.filename)
        mime_type = _sanitize_private_file_mime((request.form.get("mime_type") or "").strip() or f.content_type)
        sha256 = _sanitize_private_file_sha256(request.form.get("sha256"))

        # Store ciphertext to disk
        file_id = os.urandom(16).hex()
        storage_path = os.path.join(dm_upload_root, f"{file_id}.bin")
        try:
            size = int(_save_filestorage_limited(f, storage_path, max_dm_file_bytes))
        except ValueError:
            return _private_file_json({"success": False, "error": f"File too large (max {max_dm_file_bytes} bytes)"}, 413)
        except Exception as e:
            logging.error("[UPLOAD ERROR] dm_files save failed: %s", e)
            return _private_file_json({"success": False, "error": "Upload failed"}, 500)

        quota_denied = _private_file_quota_response(user, size)
        if quota_denied is not None:
            try:
                os.remove(storage_path)
            except Exception:
                pass
            return quota_denied

        # Persist metadata
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO dm_files (
                        file_id, sender, receiver, original_name, mime_type,
                        file_size, sha256, storage_path, iv_b64, ek_to_b64, ek_from_b64
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
                    """,
                    (
                        file_id,
                        user,
                        to_user,
                        original_name,
                        mime_type,
                        size,
                        sha256,
                        storage_path,
                        iv_b64,
                        ek_to_b64,
                        ek_from_b64,
                    ),
                )
            conn.commit()
        except Exception as e:
            logging.error("[DB ERROR] dm_files insert failed: %s", e)
            try:
                os.remove(storage_path)
            except Exception:
                pass
            return _private_file_json({"success": False, "error": "Database failure"}, 500)

        log_audit_event(user, "dm_file_upload", to_user, original_name)
        return _private_file_json({
            "success": True,
            "file_id": file_id,
            "name": original_name,
            "mime": mime_type,
            "size": size,
        })


    def _get_dm_file_row(file_id: str):
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sender, receiver, original_name, mime_type, file_size, sha256,
                       storage_path, iv_b64, ek_to_b64, ek_from_b64, revoked
                  FROM dm_files
                 WHERE file_id = %s;
                """,
                (file_id,),
            )
            row = cur.fetchone()
        return row


    @app.route("/api/dm_files/<file_id>/meta", methods=["GET"])
    @_limit(settings.get("rate_limit_dm_file_meta") or "240 per minute")
    @jwt_required()
    def dm_file_meta(file_id: str):
        user = get_jwt_identity()
        if not _valid_private_file_id(file_id):
            return _private_file_json({"success": False, "error": "Not found"}, 404)
        row = _get_dm_file_row(file_id)
        if not row:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        sender, receiver, original_name, mime_type, file_size, sha256, storage_path, iv_b64, ek_to_b64, ek_from_b64, revoked = row
        if revoked:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        ek_b64 = _dm_file_key_for_user(sender, receiver, ek_to_b64, ek_from_b64, user)
        if not ek_b64:
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)

        # Block policy applies to stored encrypted DM files too. If either side
        # blocks the other after upload, the server stops serving metadata so
        # stale file cards cannot be used as a post-block communication path.
        if _participants_blocked(sender, receiver):
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)

        return _private_file_json({
            "success": True,
            "file_id": file_id,
            "name": original_name,
            "mime": mime_type,
            "size": int(file_size),
            "sha256": sha256,
            "iv_b64": iv_b64,
            "ek_b64": ek_b64,
        })


    @app.route("/api/dm_files/<file_id>/blob", methods=["GET"])
    @_limit(settings.get("rate_limit_dm_file_blob") or "240 per minute")
    @jwt_required()
    def dm_file_blob(file_id: str):
        user = get_jwt_identity()
        if not _valid_private_file_id(file_id):
            return _private_file_json({"success": False, "error": "Not found"}, 404)
        row = _get_dm_file_row(file_id)
        if not row:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        sender, receiver, original_name, mime_type, file_size, sha256, storage_path, iv_b64, ek_to_b64, ek_from_b64, revoked = row
        if revoked:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        if not _dm_file_key_for_user(sender, receiver, ek_to_b64, ek_from_b64, user):
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)

        # Block policy applies to stored encrypted DM files too.
        if _participants_blocked(sender, receiver):
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)

        safe_storage_path = safe_existing_file_under(dm_upload_root, storage_path)
        if not safe_storage_path:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        # Send ciphertext blob. Client will decrypt locally.
        resp = send_file(
            safe_storage_path,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=_secure_download_name(f"{file_id}.bin"),
            conditional=True,
        )
        return _apply_private_download_headers(resp)

    

    
    # ------------------------------------------------------------------
    # Encrypted Group file routes (ciphertext-only; server cannot decrypt)
    # ------------------------------------------------------------------
    def _is_group_member_username(group_id: int, username: str) -> bool:
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                     WHERE gm.group_id = %s AND LOWER(u.username) = LOWER(%s)
                     LIMIT 1;
                    """,
                    (group_id, username),
                )
                return cur.fetchone() is not None
        except Exception:
            return False

    def _get_group_member_usernames(group_id: int) -> list[str]:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.username
                  FROM group_members gm
                  JOIN users u ON u.id = gm.user_id
                 WHERE gm.group_id = %s
                 ORDER BY u.username;
                """,
                (group_id,),
            )
            return [r[0] for r in cur.fetchall()]

    def _visible_group_file_recipients(sender: str, members: list[str]) -> list[str]:
        """Return current group members that are not blocked with the sender.

        Group file encryption must not require or expose wrapped keys for users
        who have blocked the sender or who the sender has blocked.  The sender
        is always retained so they can decrypt their own upload.
        """
        actor = str(sender or "").strip()
        visible: list[str] = []
        seen: set[str] = set()
        for member in members or []:
            name = str(member or "").strip()
            key = name.lower()
            if not name or key in seen:
                continue
            if actor and key != actor.lower() and _either_blocked(actor, name):
                continue
            visible.append(name)
            seen.add(key)
        if actor and actor.lower() not in seen:
            visible.append(actor)
        return visible

    def _canonicalize_group_file_key_map(raw_map: dict, members: list[str]) -> tuple[dict[str, str] | None, str | None]:
        """Return a canonical username -> wrapped key map for current members only."""
        if not isinstance(raw_map, dict) or not raw_map:
            return None, "Bad key map"
        member_lookup = {str(u).strip().lower(): str(u).strip() for u in (members or []) if str(u).strip()}
        out: dict[str, str] = {}
        for raw_user, raw_key in raw_map.items():
            canonical = member_lookup.get(str(raw_user or "").strip().lower())
            if not canonical:
                return None, "Key map includes non-members"
            if canonical in out:
                return None, "Duplicate group key entry"
            wrapped = str(raw_key or "").strip()
            if not _base64ish(wrapped, max_len=32768):
                return None, "Invalid group key envelope"
            out[canonical] = wrapped
        if len(out) > max(len(member_lookup) + 5, 25):
            return None, "Too many group key entries"
        missing = [u for u in members if u not in out]
        if missing:
            return None, "Missing keys for some group members"
        return out, None

    def _group_file_key_for_user(ek_map_json: str, username: str) -> str | None:
        try:
            ek_map = json.loads(ek_map_json or "{}")
        except Exception:
            return None
        if not isinstance(ek_map, dict):
            return None
        direct = ek_map.get(username)
        if direct and _base64ish(str(direct), max_len=32768):
            return str(direct)
        username_lc = str(username or "").strip().lower()
        for raw_user, raw_key in ek_map.items():
            if str(raw_user or "").strip().lower() == username_lc and _base64ish(str(raw_key), max_len=32768):
                return str(raw_key)
        return None

    def _is_group_muted(group_id: int, username: str) -> bool:
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM group_mutes WHERE group_id=%s AND LOWER(username)=LOWER(%s) LIMIT 1;",
                    (group_id, username),
                )
                return cur.fetchone() is not None
        except Exception:
            return False

    def _get_group_file_row(file_id: str):
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT group_id, sender, original_name, mime_type, file_size, sha256,
                       storage_path, iv_b64, ek_map_json, revoked
                  FROM group_files
                 WHERE file_id = %s;
                """,
                (file_id,),
            )
            return cur.fetchone()

    @app.route("/api/group_files/upload", methods=["POST"])
    @_limit(settings.get("rate_limit_group_file_upload") or "10 per minute")
    @jwt_required()
    def upload_group_file_ciphertext():
        user = get_jwt_identity()
        guard = _route_rate_limit_guard("group_file_upload", settings.get("rate_limit_group_file_upload") or "10 per minute", default_limit=10, default_window=60, user=user)
        if guard is not None:
            return guard
        """Upload an encrypted Group file blob.

        Client sends multipart/form-data:
          - group_id (int)
          - file (ciphertext blob)
          - iv_b64
          - ek_map_json (JSON map: username -> wrapped AES key b64)
          - sha256 (optional; plaintext hash, client-provided)
          - original_name (optional; fallback to file.filename)
          - mime_type (optional; fallback to file.content_type)
        """
        if disable_group_files_globally:
            return _private_file_json({"success": False, "error": "File sharing is disabled"}, 403)

        if "file" not in request.files:
            return _private_file_json({"success": False, "error": "Missing file"}, 400)

        try:
            group_id = int((request.form.get("group_id") or "").strip())
        except Exception:
            return _private_file_json({"success": False, "error": "Missing group_id"}, 400)

        # Sanctions: banned/upload-restricted users cannot upload; muted users cannot create visible file cards.
        denial = _private_file_upload_denial(user, send_context=True)
        if denial is not None:
            payload, status = denial
            return _private_file_json(payload, status)
        if _is_group_muted(group_id, user):
            return _private_file_json({"success": False, "error": "You are muted in this group."}, 403)

        if not _is_group_member_username(group_id, user):
            # No group existence leak
            return _private_file_json({"success": False}, 403)

        iv_b64 = (request.form.get("iv_b64") or "").strip()
        ek_map_json = (request.form.get("ek_map_json") or "").strip()
        if not iv_b64 or not ek_map_json:
            return _private_file_json({"success": False, "error": "Missing encryption envelope fields"}, 400)
        if not _base64ish(iv_b64, max_len=4096) or len(ek_map_json) > 262144:
            return _private_file_json({"success": False, "error": "Invalid encryption envelope fields"}, 400)

        # Parse key map
        try:
            ek_map = json.loads(ek_map_json)
            if not isinstance(ek_map, dict) or not ek_map:
                raise ValueError("bad map")
        except Exception:
            return _private_file_json({"success": False, "error": "Bad ek_map_json"}, 400)

        # Enforce: must include keys for all current members (ciphertext-only guarantee)
        try:
            members = _get_group_member_usernames(group_id)
        except Exception:
            members = []
        if not members:
            return _private_file_json({"success": False, "error": "Group not found"}, 404)
        visible_members = _visible_group_file_recipients(user, members)
        ek_map, ek_error = _canonicalize_group_file_key_map(ek_map, visible_members)
        if ek_error == "Too many group key entries":
            return _private_file_json({"success": False, "error": ek_error}, 400)
        if ek_error:
            return _private_file_json({"success": False, "error": ek_error}, 400)
        if not ek_map or not _group_file_key_for_user(json.dumps(ek_map), user):
            return _private_file_json({"success": False, "error": "Missing sender key"}, 400)

        # Size guard (includes multipart overhead, allow cushion)
        try:
            if request.content_length and request.content_length > (max_group_file_bytes + 256_000):
                return _private_file_json({"success": False, "error": f"File too large (max {max_group_file_bytes} bytes)"}, 413)
        except Exception:
            pass

        f = request.files["file"]
        if not f or f.filename == "":
            return _private_file_json({"success": False, "error": "Empty filename"}, 400)

        original_name = _sanitize_private_file_name((request.form.get("original_name") or "").strip() or f.filename)
        mime_type = _sanitize_private_file_mime((request.form.get("mime_type") or "").strip() or f.content_type)
        sha256 = _sanitize_private_file_sha256(request.form.get("sha256"))

        file_id = os.urandom(16).hex()
        storage_path = os.path.join(group_upload_root, f"{file_id}.bin")
        try:
            size = int(_save_filestorage_limited(f, storage_path, max_group_file_bytes))
        except ValueError:
            return _private_file_json({"success": False, "error": f"File too large (max {max_group_file_bytes} bytes)"}, 413)
        except Exception as e:
            logging.error("[UPLOAD ERROR] group_files save failed: %s", e)
            return _private_file_json({"success": False, "error": "Upload failed"}, 500)

        quota_denied = _private_file_quota_response(user, size)
        if quota_denied is not None:
            try:
                os.remove(storage_path)
            except Exception:
                pass
            return quota_denied

        # Persist metadata
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_files (
                        file_id, group_id, sender, original_name, mime_type,
                        file_size, sha256, storage_path, iv_b64, ek_map_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
                    """,
                    (
                        file_id,
                        group_id,
                        user,
                        original_name,
                        mime_type,
                        size,
                        sha256,
                        storage_path,
                        iv_b64,
                        json.dumps(ek_map),
                    ),
                )
            conn.commit()
        except Exception as e:
            logging.error("[DB ERROR] group_files insert failed: %s", e)
            try:
                os.remove(storage_path)
            except Exception:
                pass
            return _private_file_json({"success": False, "error": "Database failure"}, 500)

        log_audit_event(user, "group_file_upload", str(group_id), original_name)
        return _private_file_json({
            "success": True,
            "group_id": group_id,
            "file_id": file_id,
            "name": original_name,
            "mime": mime_type,
            "size": size,
            "sha256": sha256,
        })

    @app.route("/api/group_files/<file_id>/meta", methods=["GET"])
    @_limit(settings.get("rate_limit_group_file_meta") or "240 per minute")
    @jwt_required()
    def group_file_meta(file_id: str):
        user = get_jwt_identity()
        if not _valid_private_file_id(file_id):
            return _private_file_json({"success": False, "error": "Not found"}, 404)
        row = _get_group_file_row(file_id)
        if not row:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        group_id, sender, original_name, mime_type, file_size, sha256, storage_path, iv_b64, ek_map_json, revoked = row
        if revoked:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        if not _is_group_member_username(int(group_id), user):
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)
        if str(user or "").strip().lower() != str(sender or "").strip().lower() and _either_blocked(user, sender):
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)

        ek_b64 = _group_file_key_for_user(ek_map_json, user)
        if not ek_b64:
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)

        return _private_file_json({
            "success": True,
            "file_id": file_id,
            "group_id": int(group_id),
            "sender": sender,
            "name": original_name,
            "mime": mime_type,
            "size": int(file_size),
            "sha256": sha256,
            "iv_b64": iv_b64,
            "ek_b64": ek_b64,
        })

    @app.route("/api/group_files/<file_id>/blob", methods=["GET"])
    @_limit(settings.get("rate_limit_group_file_blob") or "240 per minute")
    @jwt_required()
    def group_file_blob(file_id: str):
        user = get_jwt_identity()
        if not _valid_private_file_id(file_id):
            return _private_file_json({"success": False, "error": "Not found"}, 404)
        row = _get_group_file_row(file_id)
        if not row:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        group_id, sender, original_name, mime_type, file_size, sha256, storage_path, iv_b64, ek_map_json, revoked = row
        if revoked:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        if not _is_group_member_username(int(group_id), user):
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)
        if str(user or "").strip().lower() != str(sender or "").strip().lower() and _either_blocked(user, sender):
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)

        # Blob downloads require the same per-user encrypted key as metadata.
        # This prevents former/non-keyed group members from collecting ciphertext
        # even though the server still cannot decrypt the file.
        if not _group_file_key_for_user(ek_map_json, user):
            return _private_file_json({"success": False, "error": "Forbidden"}, 403)

        safe_storage_path = safe_existing_file_under(group_upload_root, storage_path)
        if not safe_storage_path:
            return _private_file_json({"success": False, "error": "Not found"}, 404)

        # Send ciphertext blob. Client will decrypt locally.
        resp = send_file(
            safe_storage_path,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=_secure_download_name(f"{file_id}.bin"),
            conditional=True,
        )
        return _apply_private_download_headers(resp)

# ───────────────────────────────────────────────────────────────────────────
    # Torrent helpers (room sharing + tracker scrape)
    # ───────────────────────────────────────────────────────────────────────────
    torrents_root = str(Path(settings.get("torrents_root") or os.path.join(os.getcwd(), "uploads", "torrents")).expanduser().resolve())
    os.makedirs(torrents_root, exist_ok=True)

    def _bdecode(data: bytes, idx: int = 0, *, _depth: int = 0):
        """Minimal bounded bencode decoder for torrent files and scrape responses."""
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("bencode: bytes required")
        if _depth > 64:
            raise ValueError("bencode: nesting too deep")
        if idx < 0 or idx >= len(data):
            raise ValueError("bencode: out of range")

        c = data[idx:idx + 1]
        if c == b"i":
            end = data.index(b"e", idx)
            raw = data[idx + 1:end]
            if not raw or raw in {b"-", b"+"}:
                raise ValueError("bencode: invalid integer")
            if raw.startswith(b"+") or (raw.startswith(b"0") and raw != b"0") or (raw.startswith(b"-0")):
                raise ValueError("bencode: non-canonical integer")
            return int(raw), end + 1

        if c == b"l":
            idx += 1
            out = []
            while True:
                if idx >= len(data):
                    raise ValueError("bencode: unterminated list")
                if data[idx:idx + 1] == b"e":
                    return out, idx + 1
                v, idx = _bdecode(data, idx, _depth=_depth + 1)
                out.append(v)
                if len(out) > 100_000:
                    raise ValueError("bencode: too many list items")

        if c == b"d":
            idx += 1
            out = {}
            last_key = None
            while True:
                if idx >= len(data):
                    raise ValueError("bencode: unterminated dict")
                if data[idx:idx + 1] == b"e":
                    return out, idx + 1
                k, idx = _bdecode(data, idx, _depth=_depth + 1)
                if not isinstance(k, bytes):
                    raise ValueError("bencode: dict key must be bytes")
                # Torrent dictionaries are normally sorted.  Do not require this
                # for compatibility, but reject runaway dictionaries.
                last_key = k if last_key is None else k
                v, idx = _bdecode(data, idx, _depth=_depth + 1)
                out[k] = v
                if len(out) > 50_000:
                    raise ValueError("bencode: too many dict items")

        # bytes: <len>:<payload>
        if not (b"0" <= c <= b"9"):
            raise ValueError("bencode: invalid token")
        colon = data.index(b":", idx)
        raw_len = data[idx:colon]
        if not raw_len:
            raise ValueError("bencode: missing byte length")
        if raw_len.startswith(b"0") and raw_len != b"0":
            raise ValueError("bencode: non-canonical byte length")
        ln = int(raw_len)
        if ln < 0 or ln > 10_000_000:
            raise ValueError("bencode: byte string too large")
        payload_start = colon + 1
        payload_end = payload_start + ln
        if payload_end > len(data):
            raise ValueError("bencode: byte string out of range")
        return bytes(data[payload_start:payload_end]), payload_end


    def _bdecode_exact(data: bytes):
        value, idx = _bdecode(data, 0)
        if idx != len(data):
            raise ValueError("bencode: trailing bytes")
        return value


    def _bencode(value) -> bytes:
        """Small bencode encoder for KRPC/DHT packets."""
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int):
            return b"i" + str(value).encode("ascii") + b"e"
        if isinstance(value, str):
            value = value.encode("utf-8")
        if isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
            return str(len(raw)).encode("ascii") + b":" + raw
        if isinstance(value, (list, tuple)):
            return b"l" + b"".join(_bencode(v) for v in value) + b"e"
        if isinstance(value, dict):
            items = []
            for k, v in value.items():
                key = k.encode("utf-8") if isinstance(k, str) else bytes(k)
                items.append((key, v))
            return b"d" + b"".join(_bencode(k) + _bencode(v) for k, v in sorted(items, key=lambda kv: kv[0])) + b"e"
        raise TypeError(f"cannot bencode {type(value)!r}")


    def _compact_nodes_to_tuples(raw: bytes) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        if not isinstance(raw, (bytes, bytearray)):
            return out
        data = bytes(raw)
        for i in range(0, min(len(data), 26 * 64), 26):
            chunk = data[i:i + 26]
            if len(chunk) != 26:
                continue
            ip_raw = chunk[20:24]
            port_raw = chunk[24:26]
            try:
                ip = ipaddress.ip_address(ip_raw)
            except Exception:
                continue
            if not _is_public_ip_for_outbound(ip):
                continue
            port = int.from_bytes(port_raw, "big")
            if 0 < port <= 65535:
                out.append((str(ip), port))
        return out


    def _bloom_estimate(raw: bytes) -> int | None:
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != 256:
            return None
        data = bytes(raw)
        m = 256 * 8
        k = 2
        zeros = 0
        for b in data:
            zeros += 8 - int(b).bit_count()
        c = min(m - 1, max(1, zeros))
        try:
            return max(0, int(round(math.log(c / m) / (k * math.log(1 - 1 / m)))))
        except Exception:
            return None


    def _dht_krpc_query(sock, addr: tuple[str, int], query: bytes, args: dict, node_id: bytes) -> dict | None:
        try:
            tx = secrets.token_bytes(2)
            pkt = _bencode({b"t": tx, b"y": b"q", b"q": query, b"a": {b"id": node_id, **args}})
            sock.sendto(pkt, addr)
            while True:
                raw, _ = sock.recvfrom(2048)
                try:
                    decoded, _ = _bdecode(raw, 0)
                except Exception:
                    continue
                if not isinstance(decoded, dict) or decoded.get(b"t") != tx or decoded.get(b"y") != b"r":
                    continue
                r = decoded.get(b"r")
                return r if isinstance(r, dict) else None
        except Exception:
            return None


    def _dht_scrape_swarm_counts(infohex: str) -> dict[str, object]:
        """Best-effort BEP 5/BEP 33 DHT scrape for trackerless torrents.

        This is deliberately bounded: it queries a few bootstrap/nearby DHT nodes,
        unions BEP-33 seed/peer bloom filters when available, and falls back to a
        seen-peer count from get_peers values.  DHT data is approximate, but it is
        the only way to recover swarm-style numbers for trackerless torrents
        without running a full torrent client in the chat server.
        """
        if not _torrent_dht_scrape_enabled():
            return {"success": False, "status": "dht_disabled", "error": "DHT scrape is disabled.", "dht_queries": 0}
        infohex = str(infohex or "").strip().lower()
        if len(infohex) != 40:
            return {"success": False, "status": "invalid_infohash", "error": "Invalid infohash", "dht_queries": 0}
        try:
            infohash = bytes.fromhex(infohex)
        except ValueError:
            return {"success": False, "status": "invalid_infohash", "error": "Invalid infohash", "dht_queries": 0}

        node_id = secrets.token_bytes(20)
        seed_bloom = bytearray(256)
        peer_bloom = bytearray(256)
        bloom_seen = False
        peer_values: set[bytes] = set()
        seen_nodes: set[tuple[str, int]] = set()
        queue: list[tuple[str, int]] = []

        for host, port in _TORRENT_DHT_BOOTSTRAP_NODES:
            try:
                for ip in _resolve_public_tracker_host(host, port=port):
                    item = (ip, int(port))
                    if item not in seen_nodes:
                        queue.append(item)
                        seen_nodes.add(item)
            except Exception:
                continue

        queries = 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(max(0.2, min(_TORRENT_DHT_TIMEOUT, 3.0)))
        started = time.time()
        try:
            while queue and queries < _TORRENT_DHT_MAX_QUERIES and (time.time() - started) < 8.0:
                addr = queue.pop(0)
                queries += 1
                r = _dht_krpc_query(sock, addr, b"get_peers", {b"info_hash": infohash, b"scrape": 1}, node_id)
                if not r:
                    continue
                bf_seed = r.get(b"BFsd")
                bf_peer = r.get(b"BFpe")
                if isinstance(bf_seed, bytes) and len(bf_seed) == 256:
                    bloom_seen = True
                    for idx, b in enumerate(bf_seed):
                        seed_bloom[idx] |= b
                if isinstance(bf_peer, bytes) and len(bf_peer) == 256:
                    bloom_seen = True
                    for idx, b in enumerate(bf_peer):
                        peer_bloom[idx] |= b

                vals = r.get(b"values")
                if isinstance(vals, list):
                    for val in vals[:128]:
                        if isinstance(val, bytes) and len(val) in {6, 18}:
                            peer_values.add(val)

                nodes = r.get(b"nodes")
                for item in _compact_nodes_to_tuples(nodes if isinstance(nodes, bytes) else b""):
                    if item not in seen_nodes and len(queue) < 128:
                        queue.append(item)
                        seen_nodes.add(item)

            seeds = _bloom_estimate(bytes(seed_bloom)) if bloom_seen else None
            leechers = _bloom_estimate(bytes(peer_bloom)) if bloom_seen else None
            if seeds is not None or leechers is not None:
                return {
                    "success": True,
                    "seeds": seeds,
                    "leechers": leechers,
                    "completed": None,
                    "status": "dht_estimate",
                    "error": "",
                    "dht_queries": queries,
                    "dht_peers_seen": len(peer_values),
                }
            if peer_values:
                # BEP-5 get_peers values are peers without seed/leecher split.
                # For the room UI, present them as leechers/peers and keep seeds at 0
                # rather than leaving the card blank.
                return {
                    "success": True,
                    "seeds": 0,
                    "leechers": len(peer_values),
                    "completed": None,
                    "status": "dht_peers",
                    "error": "",
                    "dht_queries": queries,
                    "dht_peers_seen": len(peer_values),
                }
            return {
                "success": False,
                "seeds": None,
                "leechers": None,
                "completed": None,
                "status": "dht_no_response",
                "error": "DHT did not return seed/peer data quickly.",
                "dht_queries": queries,
                "dht_peers_seen": 0,
            }
        finally:
            try:
                sock.close()
            except Exception:
                pass


    def _extract_torrent_info_span(data: bytes) -> tuple[dict, bytes]:
        """Return (info_dict, exact_bencoded_info_bytes) from a .torrent file."""
        if not data.startswith(b"d"):
            raise ValueError("torrent: top-level value must be a dictionary")
        idx = 1
        while True:
            if idx >= len(data):
                raise ValueError("torrent: unterminated top-level dictionary")
            if data[idx:idx + 1] == b"e":
                break
            key, idx = _bdecode(data, idx)
            if not isinstance(key, bytes):
                raise ValueError("torrent: invalid top-level key")
            value_start = idx
            value, idx = _bdecode(data, idx)
            if key == b"info":
                if not isinstance(value, dict):
                    raise ValueError("torrent: info dictionary missing")
                return value, bytes(data[value_start:idx])
        raise ValueError("torrent: info dictionary missing")


    def _torrent_text(value, *, max_len: int = 255) -> str:
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", "replace")
            except Exception:
                value = value.decode("latin-1", "replace")
        text = str(value or "").replace("\x00", "").strip()
        return text[:max_len]


    def _collect_torrent_trackers(root: dict) -> list[str]:
        trackers: list[str] = []

        def add(raw) -> None:
            text = _torrent_text(raw, max_len=2048)
            if not text or len(trackers) >= 32:
                return
            parsed = urllib.parse.urlparse(text)
            if parsed.scheme not in {"udp", "http", "https"}:
                return
            if parsed.username or parsed.password:
                return
            if text not in trackers:
                trackers.append(text)

        add(root.get(b"announce"))
        announce_list = root.get(b"announce-list")
        if isinstance(announce_list, list):
            for tier in announce_list:
                if isinstance(tier, list):
                    for item in tier:
                        add(item)
                else:
                    add(tier)
        return trackers


    def _effective_torrent_trackers(trackers: list[str]) -> tuple[list[str], str, int]:
        """Return trackers to use for swarm lookup.

        If the uploaded torrent is trackerless, restore the old practical
        behavior by attaching a bounded built-in public tracker list for scrape
        and magnet sharing.  This gives Echo-Chat another way to show the
        familiar Seeds/Leechers fields instead of hiding them behind a
        trackerless warning.
        """
        clean: list[str] = []
        for tr in trackers if isinstance(trackers, list) else []:
            text = str(tr or "").strip()
            if text and text not in clean:
                clean.append(text)
            if len(clean) >= 25:
                break
        if clean:
            return clean, "torrent", len(clean)
        fallback = _configured_public_fallback_trackers() if _torrent_public_fallback_scrape_enabled() else []
        return fallback, "public_fallback", 0


    def _collect_torrent_web_seeds(root: dict) -> list[str]:
        """Return safe display-only web seed URLs from BEP 19/legacy fields.

        Web seeds can help clients download data, but they are not BitTorrent
        tracker peers and do not expose seed/leecher counts.  Keeping this
        metadata lets the room card explain trackerless Arch-style torrents
        instead of showing a mysterious "?" forever.
        """
        web_seeds: list[str] = []

        def add(raw) -> None:
            text = _torrent_text(raw, max_len=2048)
            if not text or len(web_seeds) >= 512:
                return
            parsed = urllib.parse.urlparse(text)
            if parsed.scheme not in {"http", "https"}:
                return
            if parsed.username or parsed.password:
                return
            if text not in web_seeds:
                web_seeds.append(text)

        for key in (b"url-list", b"url-list.utf-8", b"httpseeds"):
            raw = root.get(key)
            if isinstance(raw, list):
                for item in raw:
                    add(item)
            else:
                add(raw)
        return web_seeds


    def _torrent_total_size(info: dict) -> int:
        length = info.get(b"length")
        if isinstance(length, int) and length >= 0:
            return length
        files = info.get(b"files")
        total = 0
        if isinstance(files, list):
            for item in files[:100_000]:
                if isinstance(item, dict) and isinstance(item.get(b"length"), int) and item.get(b"length") >= 0:
                    total += int(item.get(b"length"))
        return int(total)


    def _validate_torrent_paths(info: dict) -> None:
        """Reject path traversal/control characters in multi-file torrent paths."""
        files = info.get(b"files")
        if not isinstance(files, list):
            return
        if len(files) > 100_000:
            raise ValueError("torrent: too many files")
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("torrent: invalid file entry")
            path_items = item.get(b"path.utf-8") or item.get(b"path") or []
            if not isinstance(path_items, list) or not path_items:
                raise ValueError("torrent: file path missing")
            for part in path_items:
                text = _torrent_text(part, max_len=512)
                if not text or text in {".", ".."} or "/" in text or "\\" in text or any(ord(ch) < 32 for ch in text):
                    raise ValueError("torrent: unsafe file path")


    def _parse_torrent_upload(raw: bytes, original_name: str) -> dict[str, object]:
        """Validate a .torrent file and return safe display metadata."""
        if not raw or len(raw) > _TORRENT_MAX_FILE_BYTES:
            raise ValueError("torrent: invalid size")
        root = _bdecode_exact(raw)
        if not isinstance(root, dict):
            raise ValueError("torrent: invalid top-level value")
        info, info_span = _extract_torrent_info_span(raw)
        if not info_span:
            raise ValueError("torrent: info dictionary missing")
        _validate_torrent_paths(info)
        name = _torrent_text(info.get(b"name.utf-8") or info.get(b"name") or original_name, max_len=255)
        if not name:
            name = original_name or "download.torrent"
        if name in {".", ".."} or "/" in name or "\\" in name or any(ord(ch) < 32 for ch in name):
            raise ValueError("torrent: unsafe name")
        total_size = _torrent_total_size(info)
        if total_size <= 0:
            raise ValueError("torrent: empty payload")
        if max_torrent_total_size_bytes > 0 and total_size > max_torrent_total_size_bytes:
            raise ValueError("torrent: payload too large")
        piece_length = info.get(b"piece length")
        pieces = info.get(b"pieces")
        if not isinstance(piece_length, int) or piece_length <= 0:
            raise ValueError("torrent: piece length missing")
        if not isinstance(pieces, bytes) or len(pieces) == 0 or len(pieces) % 20 != 0:
            raise ValueError("torrent: pieces field invalid")
        creation_date = ""
        try:
            raw_creation = root.get(b"creation date")
            if isinstance(raw_creation, int) and raw_creation > 0:
                creation_date = datetime.fromtimestamp(raw_creation, tz=timezone.utc).isoformat()
        except Exception:
            creation_date = ""
        declared_trackers = _collect_torrent_trackers(root)
        effective_trackers, tracker_source, declared_tracker_count = _effective_torrent_trackers(declared_trackers)
        web_seeds = _collect_torrent_web_seeds(root)
        return {
            "display_name": name,
            "infohash_hex": hashlib.sha1(info_span).hexdigest(),
            "total_size": total_size,
            "trackers": effective_trackers,
            "tracker_count": len(effective_trackers),
            "declared_trackers": declared_trackers,
            "declared_tracker_count": declared_tracker_count,
            "tracker_source": tracker_source,
            "using_public_fallback_trackers": tracker_source == "public_fallback",
            "web_seeds": web_seeds,
            "web_seed_count": len(web_seeds),
            "comment": _torrent_text(root.get(b"comment.utf-8") or root.get(b"comment"), max_len=500),
            "created_by": _torrent_text(root.get(b"created by"), max_len=255),
            "creation_date": creation_date,
        }


    def _torrent_metadata_path(torrent_id: str) -> str:
        return os.path.join(torrents_root, f"{torrent_id}.meta.json")


    def _write_torrent_metadata(torrent_id: str, metadata: dict[str, object]) -> None:
        temp_path = _torrent_metadata_path(torrent_id) + ".tmp"
        final_path = _torrent_metadata_path(torrent_id)
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, ensure_ascii=False, sort_keys=True)
        os.replace(temp_path, final_path)


    def _read_torrent_metadata(torrent_id: str) -> dict[str, object] | None:
        path = safe_existing_file_under(torrents_root, _torrent_metadata_path(torrent_id))
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except Exception:
            logging.exception("[TORRENT] failed to read metadata for %s", torrent_id)
            return None


    def _room_exists(room: str) -> bool:
        room = str(room or "").strip()
        if not room or len(room) > 128:
            return False
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM chat_rooms WHERE name=%s LIMIT 1;", (room,))
            return cur.fetchone() is not None


    def _can_user_access_torrent_metadata(user: str, meta: dict[str, object] | None) -> bool:
        if not meta:
            return _ALLOW_LEGACY_TORRENT_DOWNLOAD_WITHOUT_METADATA
        owner = str(meta.get("owner") or "").strip()
        if owner and str(user or "").strip().lower() == owner.lower():
            return True
        scope = str(meta.get("scope") or "owner").strip().lower()
        scope_id = str(meta.get("scope_id") or "").strip()
        if scope in {"owner", "private"}:
            return False
        if scope == "room":
            if not scope_id:
                return False
            try:
                custom_meta = get_custom_room_meta(scope_id)
            except Exception:
                custom_meta = None
            if custom_meta:
                try:
                    return bool(can_user_access_custom_room(scope_id, user))
                except Exception:
                    return False
            try:
                return _room_exists(scope_id)
            except Exception:
                return False
        if scope == "global":
            # Global torrent scope was an old compatibility path. New uploads use
            # explicit owner or room scope only.
            return False
        return False


    def _room_write_policy_denial(user: str, room: str) -> tuple[str, int] | None:
        """Return a room-moderation denial for room-scoped HTTP writes.

        Socket room messages already enforce bans/locks/readonly/slowmode.
        Room-scoped HTTP writes, such as torrent cards, must not bypass the
        same moderation state just because they arrive through Flask routes.
        """
        clean_room = str(room or "").strip()
        actor = str(user or "").strip()
        if not clean_room or not actor:
            return ("Invalid room scope", 400)
        if is_user_sanctioned(actor, f"room_ban:{clean_room}"):
            return ("You are banned from this room", 403)
        perms = set(get_user_permissions(actor) or set())
        locked = False
        readonly = False
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT locked FROM room_locks WHERE room = %s LIMIT 1;", (clean_room,))
                row = cur.fetchone()
                locked = bool(row and row[0])
                cur.execute("SELECT readonly FROM room_readonly WHERE room = %s LIMIT 1;", (clean_room,))
                row = cur.fetchone()
                readonly = bool(row and row[0])
        except Exception:
            # Fail closed for write actions if the moderation policy cannot be read.
            return ("Room moderation state unavailable", 503)
        if locked and not ({"admin:basic", "room:lock"} & perms):
            return ("Room is locked", 403)
        if readonly and not ({"admin:basic", "room:readonly"} & perms):
            return ("Room is read-only", 403)
        return None

    def _requested_torrent_scope(user: str) -> tuple[str, str]:
        scope = str(request.form.get("scope") or request.form.get("scope_type") or "owner").strip().lower()
        if scope not in {"owner", "room"}:
            scope = "owner"
        if scope == "room":
            room = str(request.form.get("room") or request.form.get("scope_id") or "").strip()
            if not room or len(room) > 128:
                raise ValueError("Invalid room scope")
            custom_meta = None
            try:
                custom_meta = get_custom_room_meta(room)
            except Exception:
                custom_meta = None
            if custom_meta:
                if not can_user_access_custom_room(room, user):
                    raise PermissionError("No access to room")
            elif not _room_exists(room):
                raise PermissionError("No access to room")
            moderation_denial = _room_write_policy_denial(user, room)
            if moderation_denial is not None:
                raise PermissionError(moderation_denial[0])
            return "room", room
        return "owner", str(user or "")


    def _is_public_ip_for_outbound(ip: ipaddress._BaseAddress) -> bool:
        """Return True only for globally routable tracker destinations.

        This protects server-side torrent tracker scraping from reaching localhost,
        RFC1918/LAN addresses, link-local/cloud metadata ranges, multicast, and
        other non-public networks when a user supplies tracker URLs.
        """
        return bool(getattr(ip, "is_global", False) and not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ))

    def _resolve_public_tracker_host(host: str, *, port: int | None = None) -> list[str]:
        """Resolve a tracker hostname and return safe public IP strings.

        Hostname-only checks are not enough for SSRF protection because a public-
        looking hostname can resolve to 127.0.0.1, a LAN address, or a cloud
        metadata address. Treat DNS failures and mixed safe/unsafe results as
        unsafe to fail closed.
        """
        host = (host or "").strip().strip("[]").lower()
        if not host or len(host) > 253:
            return []
        if host in ("localhost",) or host.endswith(".local"):
            return []
        try:
            ip = ipaddress.ip_address(host)
            return [str(ip)] if _is_public_ip_for_outbound(ip) else []
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(host, port or None, type=socket.SOCK_STREAM)
        except Exception:
            return []
        ips: list[str] = []
        unsafe_seen = False
        for info in infos:
            sockaddr = info[4]
            raw_ip = sockaddr[0] if sockaddr else ""
            try:
                ip = ipaddress.ip_address(str(raw_ip).split("%", 1)[0])
            except ValueError:
                unsafe_seen = True
                continue
            if not _is_public_ip_for_outbound(ip):
                unsafe_seen = True
            elif str(ip) not in ips:
                ips.append(str(ip))
        if unsafe_seen:
            return []
        return ips

    def _is_local_host(host: str) -> bool:
        """Compatibility wrapper: True means unsafe for outbound scraping."""
        return not bool(_resolve_public_tracker_host(host))

    def _safe_http_tracker_candidates(url: str) -> list[dict[str, object]]:
        """Return pre-resolved safe HTTP(S) tracker endpoints for scrape.

        Tracker URLs are user-supplied, so SSRF protection must survive DNS
        rebinding.  This function resolves the hostname once, rejects any
        private/local/mixed answers, and returns the validated public IP that
        the fetcher must connect to.  The original hostname is preserved only
        for the HTTP Host header and HTTPS SNI.
        """
        out: list[dict[str, object]] = []
        try:
            raw = str(url or "").strip()
            if not raw or len(raw) > 2048:
                return []
            p = urllib.parse.urlparse(raw)
            if p.scheme not in ("http", "https"):
                return []
            if p.username or p.password:
                return []
            host = (p.hostname or "").strip().lower()
            port = int(p.port or (443 if p.scheme == "https" else 80))
            if port <= 0 or port > 65535:
                return []
            resolved = _resolve_public_tracker_host(host, port=port)
            if not resolved:
                return []
            resolved_ip = resolved[0]

            # Baseline: keep original path, drop query/fragment.
            path = p.path or "/"
            if len(path) > 1024 or not path.startswith("/"):
                return []

            def _add(candidate_path: str) -> None:
                if not candidate_path or len(candidate_path) > 1024 or not candidate_path.startswith("/"):
                    return
                display_url = urllib.parse.urlunparse((p.scheme, f"{host}:{port}", candidate_path, "", "", ""))
                item = {
                    "scheme": p.scheme,
                    "host": host,
                    "port": port,
                    "path": candidate_path,
                    "resolved_ip": resolved_ip,
                    "display_url": display_url,
                }
                if all(existing.get("path") != candidate_path for existing in out):
                    out.append(item)

            _add(path)

            scrape_path = None
            if path.endswith("/announce"):
                scrape_path = path[:-len("/announce")] + "/scrape"
            elif path.endswith("/announce/"):
                scrape_path = path[:-len("/announce/")] + "/scrape/"
            elif path.endswith("announce.php"):
                scrape_path = path[:-len("announce.php")] + "scrape.php"
            elif "/announce" in path:
                # last segment replacement
                left, _ = path.rsplit("/announce", 1)
                scrape_path = left + "/scrape"

            if scrape_path:
                _add(scrape_path)

        except Exception:
            return []
        return out


    def _http_tracker_scrape_request(candidate: dict[str, object], query: str) -> bytes | None:
        """Fetch a tracker scrape response by connecting to the validated IP.

        urllib/requests would resolve the hostname again after validation, which
        re-opens the DNS-rebinding SSRF window.  This tiny client connects to
        candidate["resolved_ip"] directly while keeping the original host for
        Host/SNI.  It reads at most a small bounded response body.
        """
        try:
            scheme = str(candidate.get("scheme") or "").lower()
            host = str(candidate.get("host") or "").strip().lower()
            resolved_ip = str(candidate.get("resolved_ip") or "").strip()
            port = int(candidate.get("port") or (443 if scheme == "https" else 80))
            path = str(candidate.get("path") or "/")
            if scheme not in {"http", "https"} or not host or not resolved_ip:
                return None
            if port <= 0 or port > 65535 or not path.startswith("/"):
                return None
            # Defense in depth: reject a candidate if the resolved IP was somehow
            # mutated after _safe_http_tracker_candidates returned it.
            if not _is_public_ip_for_outbound(ipaddress.ip_address(resolved_ip)):
                return None

            target = path + ("?" + query if query else "")
            host_header = host if (scheme == "https" and port == 443) or (scheme == "http" and port == 80) else f"{host}:{port}"
            req = (
                f"GET {target} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "User-Agent: Echo-Chat/1.0\r\n"
                "Accept: application/octet-stream,*/*;q=0.8\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii", "ignore")

            raw_sock = socket.create_connection((resolved_ip, port), timeout=_TORRENT_SCRAPE_HTTP_TIMEOUT)
            sock = raw_sock
            try:
                if scheme == "https":
                    context = ssl.create_default_context()
                    sock = context.wrap_socket(raw_sock, server_hostname=host)
                sock.settimeout(_TORRENT_SCRAPE_HTTP_TIMEOUT)
                sock.sendall(req)
                chunks: list[bytes] = []
                total = 0
                limit = 220_000
                while total < limit:
                    chunk = sock.recv(min(65536, limit - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                raw = b"".join(chunks)
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            header, sep, body = raw.partition(b"\r\n\r\n")
            if not sep:
                return None
            status = header.split(b"\r\n", 1)[0]
            if not (status.startswith(b"HTTP/1.1 200") or status.startswith(b"HTTP/1.0 200")):
                return None
            return body[:200_000]
        except Exception:
            return None

    def _safe_udp_tracker(url: str) -> tuple[str, int] | None:
        try:
            raw = str(url or "").strip()
            if not raw or len(raw) > 2048:
                return None
            p = urllib.parse.urlparse(raw)
            if p.scheme != "udp":
                return None
            if p.username or p.password:
                return None
            host = (p.hostname or "").strip().lower()
            port = int(p.port or 0)
            if port <= 0 or port > 65535:
                return None
            resolved = _resolve_public_tracker_host(host, port=port)
            if not resolved:
                return None
            # Use the resolved public IP for UDP so socket.sendto does not trigger
            # another hostname resolution after validation.
            return resolved[0], port
        except Exception:
            return None

    def _udp_tracker_scrape(host: str, port: int, infohash: bytes) -> tuple[int | None, int | None, int | None]:
        """BEP 15: UDP tracker connect + scrape. Returns (seeders, leechers, completed)."""
        import os
        import socket
        import struct

        # connect request
        conn_id = 0x41727101980
        trans_id = int.from_bytes(os.urandom(4), "big")
        pkt = struct.pack(">QLL", conn_id, 0, trans_id)

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(_TORRENT_SCRAPE_UDP_TIMEOUT)
        try:
            s.sendto(pkt, (host, port))
            resp, _ = s.recvfrom(2048)
            if len(resp) < 16:
                return None, None, None
            action, r_trans, r_conn = struct.unpack(">LLQ", resp[:16])
            if action != 0 or r_trans != trans_id:
                return None, None, None

            # scrape request (action=2)
            trans_id2 = int.from_bytes(os.urandom(4), "big")
            pkt2 = struct.pack(">QLL", r_conn, 2, trans_id2) + infohash
            s.sendto(pkt2, (host, port))
            resp2, _ = s.recvfrom(2048)
            if len(resp2) < 20:
                return None, None, None
            action2, r_trans2 = struct.unpack(">LL", resp2[:8])
            if action2 != 2 or r_trans2 != trans_id2:
                return None, None, None
            seeders, completed, leechers = struct.unpack(">LLL", resp2[8:20])
            return int(seeders), int(leechers), int(completed)
        finally:
            try:
                s.close()
            except Exception:
                pass




    def _scrape_torrent_swarm_counts(infohex: str, trackers: list[str], *, force_refresh: bool = False) -> dict[str, object]:
        """Best-effort tracker scrape for seeds/leechers/completed.

        Returns a JSON-friendly dict with explicit status fields so the room
        card can show *why* numbers are unknown instead of just rendering "?".
        """
        infohex = str(infohex or "").strip().lower()
        trackers_in = _clean_torrent_tracker_list(trackers, limit=25)
        tracker_count = len(trackers_in)
        tracker_source = "torrent"
        if tracker_count <= 0 and _torrent_public_fallback_scrape_enabled():
            trackers_in = _configured_public_fallback_trackers()
            tracker_count = len(trackers_in)
            tracker_source = "public_fallback"
        if len(infohex) != 40:
            return {
                "success": False,
                "seeds": None,
                "leechers": None,
                "completed": None,
                "cached": False,
                "trackers_tried": 0,
                "tracker_count": tracker_count,
                "status": "invalid_infohash",
                "error": "Invalid infohash",
            }
        try:
            infohash = bytes.fromhex(infohex)
        except ValueError:
            return {
                "success": False,
                "seeds": None,
                "leechers": None,
                "completed": None,
                "cached": False,
                "trackers_tried": 0,
                "tracker_count": tracker_count,
                "status": "invalid_infohash",
                "error": "Invalid infohash",
            }

        if tracker_count <= 0:
            dht = _dht_scrape_swarm_counts(infohex) if _torrent_dht_scrape_enabled() else {}
            return {
                "success": bool(dht.get("success")),
                "seeds": dht.get("seeds"),
                "leechers": dht.get("leechers"),
                "completed": dht.get("completed"),
                "cached": False,
                "trackers_tried": 0,
                "tracker_count": 0,
                "tracker_source": "none",
                "status": dht.get("status") or "no_trackers",
                "error": dht.get("error") or "No trackers were present and DHT did not return swarm data.",
                "dht_queries": dht.get("dht_queries", 0),
                "dht_peers_seen": dht.get("dht_peers_seen", 0),
            }

        now = time.time()
        cache_fingerprint = _torrent_tracker_cache_fingerprint(trackers_in)
        cache_key = f"{infohex}:{tracker_source}:{tracker_count}:{cache_fingerprint}"
        cached = _TORRENT_SCRAPE_CACHE.get(cache_key)
        if (not force_refresh) and cached and (now - cached[0]) <= _TORRENT_SCRAPE_CACHE_TTL:
            _, seeds, leechers, completed, cached_source, cached_tried = cached
            if any(v is not None for v in (seeds, leechers, completed)):
                return {
                    "success": True,
                    "seeds": seeds,
                    "leechers": leechers,
                    "completed": completed,
                    "cached": True,
                    "trackers_tried": cached_tried,
                    "tracker_count": tracker_count,
                    "tracker_source": cached_source or tracker_source,
                    "status": "cached",
                    "error": "",
                }

        seeds = leechers = completed = None
        tried = 0

        for tr in (trackers_in[:_TORRENT_SCRAPE_MAX_TRACKERS] if isinstance(trackers_in, list) else []):
            if not isinstance(tr, str):
                continue
            if tried >= _TORRENT_SCRAPE_MAX_TRIES:
                break
            tr = tr.strip()
            if not tr:
                continue

            udp = _safe_udp_tracker(tr)
            if udp:
                tried += 1
                try:
                    s, l, d = _udp_tracker_scrape(udp[0], udp[1], infohash)
                    if isinstance(s, int):
                        seeds = max(seeds or 0, s)
                    if isinstance(l, int):
                        leechers = max(leechers or 0, l)
                    if isinstance(d, int):
                        completed = max(completed or 0, d)
                except Exception as exc:
                    logging.debug("[TORRENT] UDP scrape failed: %s", exc)
                continue

            cands = _safe_http_tracker_candidates(tr)
            if not cands:
                continue

            for safe in cands[:2]:
                if tried >= _TORRENT_SCRAPE_MAX_TRIES:
                    break
                tried += 1
                try:
                    q = "info_hash=" + urllib.parse.quote_from_bytes(infohash, safe="")
                    raw = _http_tracker_scrape_request(safe, q)
                    if not raw:
                        continue
                    decoded, _ = _bdecode(raw, 0)
                    files = decoded.get(b"files") if isinstance(decoded, dict) else None
                    stats = files.get(infohash) if isinstance(files, dict) else None
                    if isinstance(stats, dict):
                        c = stats.get(b"complete")
                        ic = stats.get(b"incomplete")
                        dl = stats.get(b"downloaded")
                        if isinstance(c, int):
                            seeds = max(seeds or 0, c)
                        if isinstance(ic, int):
                            leechers = max(leechers or 0, ic)
                        if isinstance(dl, int):
                            completed = max(completed or 0, dl)
                except Exception as exc:
                    logging.debug("[TORRENT] HTTP scrape failed: %s", exc)
                    continue

        have_numbers = any(v is not None for v in (seeds, leechers, completed))
        dht_result: dict[str, object] = {}
        if not have_numbers and _torrent_dht_scrape_enabled():
            dht_result = _dht_scrape_swarm_counts(infohex)
            if dht_result.get("success"):
                seeds = dht_result.get("seeds")
                leechers = dht_result.get("leechers")
                completed = dht_result.get("completed")
                have_numbers = any(v is not None for v in (seeds, leechers, completed))

        # Cache only after a real tracker try or a successful non-empty DHT result.
        # Older builds cached the first DHT/partial result by infohash only; that made
        # the Refresh button keep returning "cached" instead of trying trackers again.
        if have_numbers and (tried > 0 or int(dht_result.get("dht_queries") or 0) > 0 or int(dht_result.get("dht_peers_seen") or 0) > 0):
            _TORRENT_SCRAPE_CACHE[cache_key] = (
                now,
                seeds if isinstance(seeds, int) else None,
                leechers if isinstance(leechers, int) else None,
                completed if isinstance(completed, int) else None,
                tracker_source,
                tried,
            )

        if have_numbers and dht_result.get("success"):
            status = str(dht_result.get("status") or "dht_estimate")
            error = ""
        elif have_numbers:
            status = "fallback_refreshed" if tracker_source == "public_fallback" else "refreshed"
            error = ""
        else:
            status = str(dht_result.get("status") or ("no_tracker_response" if tried else "no_usable_trackers"))
            error = str(dht_result.get("error") or ("No tracker or DHT node returned swarm stats." if tried else "No usable public tracker URLs were available."))
        return {
            "success": True,
            "seeds": seeds,
            "leechers": leechers,
            "completed": completed,
            "cached": False,
            "trackers_tried": tried,
            "tracker_count": tracker_count,
            "tracker_source": tracker_source,
            "status": status,
            "error": error,
            "dht_queries": dht_result.get("dht_queries", 0),
            "dht_peers_seen": dht_result.get("dht_peers_seen", 0),
        }

    @app.route("/api/torrents/upload", methods=["POST"])
    @_limit(settings.get("rate_limit_torrent_upload") or "5 per minute")
    @jwt_required()
    def torrents_upload():
        user = get_jwt_identity()
        if not _torrent_upload_enabled():
            return _no_store_json({"success": False, "error": "Torrent uploads are disabled by the server administrator."}, 403)
        guard = _route_rate_limit_guard("torrent_upload", settings.get("rate_limit_torrent_upload") or "5 per minute", default_limit=5, default_window=60, user=user)
        if guard is not None:
            return guard
        denial = _private_file_upload_denial(user, send_context=True)
        if denial is not None:
            payload, status = denial
            return _no_store_json(payload, status)

        f = request.files.get("file")
        if not f or not getattr(f, "filename", ""):
            return jsonify({"success": False, "error": "No file"}), 400

        orig = secure_filename(f.filename) or "download.torrent"
        if not orig.lower().endswith(".torrent"):
            return jsonify({"success": False, "error": "Only .torrent files are allowed"}), 400

        if request.content_length and int(request.content_length) > (_TORRENT_MAX_FILE_BYTES + 256_000):
            return jsonify({"success": False, "error": f"Torrent too large (max {_TORRENT_MAX_FILE_BYTES} bytes)"}), 413

        try:
            raw = f.stream.read(_TORRENT_MAX_FILE_BYTES + 1)
        except Exception:
            return jsonify({"success": False, "error": "Could not read torrent file"}), 400
        if len(raw) <= 0:
            return jsonify({"success": False, "error": "Torrent file is empty"}), 400
        if len(raw) > _TORRENT_MAX_FILE_BYTES:
            return jsonify({"success": False, "error": f"Torrent too large (max {_TORRENT_MAX_FILE_BYTES} bytes)"}), 413

        try:
            parsed_meta = _parse_torrent_upload(raw, orig)
        except Exception as exc:
            logging.info("[TORRENT] rejected invalid upload by %s: %s", user, exc)
            return jsonify({"success": False, "error": "Invalid .torrent file"}), 400

        quota_denied = _torrent_quota_response(user, len(raw))
        if quota_denied is not None:
            return quota_denied

        try:
            scope, scope_id = _requested_torrent_scope(user)
        except PermissionError as exc:
            detail = str(exc) or "You do not have access to that torrent scope"
            return jsonify({"success": False, "error": detail}), 403
        except ValueError:
            return jsonify({"success": False, "error": "Invalid torrent scope"}), 400

        parsed_trackers = list(parsed_meta.get("trackers") or [])
        parsed_web_seeds = list(parsed_meta.get("web_seeds") or [])
        tracker_source = str(parsed_meta.get("tracker_source") or "torrent")
        defer_swarm_lookup = _as_bool_setting(
            request.form.get("defer_swarm")
            or request.form.get("defer_swarm_lookup")
            or request.form.get("fast_room_post"),
            False,
        )
        # Public fallback trackers are controlled by Echo-Chat's bounded built-in
        # list, so allow those lookups even when arbitrary user-supplied tracker
        # scraping is off.  Room posting can explicitly defer lookup so the card
        # appears immediately and then refreshes seeds/leechers asynchronously.
        allow_initial_lookup = (not defer_swarm_lookup) and (_torrent_scrape_enabled() or tracker_source == "public_fallback")
        if allow_initial_lookup:
            initial_swarm = _scrape_torrent_swarm_counts(
                str(parsed_meta.get("infohash_hex") or ""),
                parsed_trackers,
            )
            initial_swarm["web_seed_count"] = len(parsed_web_seeds)
        else:
            initial_swarm = {
                "seeds": None,
                "leechers": None,
                "completed": None,
                "status": "pending" if defer_swarm_lookup else "disabled",
                "error": "" if defer_swarm_lookup else "Tracker scraping is disabled by the server administrator.",
                "deferred": bool(defer_swarm_lookup),
                "trackers_tried": 0,
                "tracker_count": len(parsed_trackers),
                "tracker_source": tracker_source,
                "web_seed_count": len(parsed_web_seeds),
            }

        tid = secrets.token_urlsafe(18)
        stored = f"{tid}__{orig}"
        path = os.path.join(torrents_root, stored)
        try:
            with open(path, "wb") as out:
                out.write(raw)
            metadata = {
                "torrent_id": tid,
                "owner": str(user or ""),
                "scope": scope,
                "scope_id": scope_id,
                "original_name": orig,
                "stored_name": stored,
                "size": len(raw),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "swarm_last": initial_swarm,
                **parsed_meta,
            }
            _write_torrent_metadata(tid, metadata)
        except Exception as exc:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            logging.error("[TORRENT] failed to save upload metadata: %s", exc)
            return jsonify({"success": False, "error": "Could not save torrent"}), 500

        log_audit_event(user, "torrent_upload", target=tid, details=f"{orig} ({len(raw)}) scope={scope}:{scope_id}")
        return jsonify({
            "success": True,
            "torrent_id": tid,
            "name": parsed_meta.get("display_name") or orig,
            "file_name": orig,
            "size": len(raw),
            "infohash_hex": parsed_meta.get("infohash_hex"),
            "infohash": parsed_meta.get("infohash_hex"),
            "total_size": parsed_meta.get("total_size"),
            "trackers": parsed_meta.get("trackers") or [],
            "tracker_count": parsed_meta.get("tracker_count") or len(parsed_meta.get("trackers") or []),
            "declared_tracker_count": parsed_meta.get("declared_tracker_count") or 0,
            "tracker_source": parsed_meta.get("tracker_source") or "torrent",
            "using_public_fallback_trackers": bool(parsed_meta.get("using_public_fallback_trackers")),
            "web_seeds": parsed_meta.get("web_seeds") or [],
            "web_seed_count": parsed_meta.get("web_seed_count") or len(parsed_meta.get("web_seeds") or []),
            "comment": parsed_meta.get("comment") or "",
            "created_by": parsed_meta.get("created_by") or "",
            "creation_date": parsed_meta.get("creation_date") or "",
            "scope": scope,
            "scope_id": scope_id,
            "seeds": initial_swarm.get("seeds"),
            "leechers": initial_swarm.get("leechers"),
            "completed": initial_swarm.get("completed"),
            "scrape_status": initial_swarm.get("status") or "",
            "scrape_error": initial_swarm.get("error") or "",
            "swarm_deferred": bool(initial_swarm.get("deferred")),
            "trackers_tried": initial_swarm.get("trackers_tried") or 0,
            "tracker_count": initial_swarm.get("tracker_count") or len(parsed_meta.get("trackers") or []),
            "tracker_source": initial_swarm.get("tracker_source") or parsed_meta.get("tracker_source") or "torrent",
            "dht_queries": initial_swarm.get("dht_queries") or 0,
            "dht_peers_seen": initial_swarm.get("dht_peers_seen") or 0,
            "torrent_scrape_enabled": bool(_torrent_scrape_enabled() or parsed_meta.get("tracker_source") == "public_fallback"),
            "download_url": f"/api/torrents/{tid}/download",
        })


    @app.route("/api/torrents/<torrent_id>/download", methods=["GET"])
    @_limit(settings.get("rate_limit_torrent_download") or "120 per minute")
    @jwt_required()
    def torrents_download(torrent_id: str):
        user = get_jwt_identity()
        torrent_id = str(torrent_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", torrent_id):
            return jsonify({"success": False, "error": "Not found"}), 404

        meta = _read_torrent_metadata(torrent_id)
        if not _can_user_access_torrent_metadata(user, meta):
            log_audit_event(user, "torrent_download_denied", target=torrent_id, details="scope_denied_or_missing_metadata")
            return jsonify({"success": False, "error": "Not found"}), 404

        # Find file on disk. Prefer metadata, but keep a prefix fallback for older files
        # only when allow_legacy_torrent_download_without_metadata=true.
        found = str((meta or {}).get("stored_name") or "").strip()
        if found and not found.startswith(f"{torrent_id}__"):
            return jsonify({"success": False, "error": "Not found"}), 404
        if not found:
            prefix = f"{torrent_id}__"
            for name in os.listdir(torrents_root):
                if name.startswith(prefix):
                    found = name
                    break
        if not found:
            return jsonify({"success": False, "error": "Not found"}), 404

        path = safe_existing_file_under(torrents_root, os.path.join(torrents_root, found))
        if not path:
            return jsonify({"success": False, "error": "Not found"}), 404
        dl_name = str((meta or {}).get("original_name") or (found.split("__", 1)[1] if "__" in found else f"{torrent_id}.torrent"))

        log_audit_event(user, "torrent_download", target=torrent_id, details=dl_name)
        resp = send_file(
            path,
            mimetype="application/x-bittorrent",
            as_attachment=True,
            download_name=_secure_download_name(dl_name, default=f"{torrent_id}.torrent"),
            conditional=True,
        )
        return _apply_private_download_headers(resp, csp="sandbox; default-src 'none';")


    @app.route("/api/torrent/scrape", methods=["POST"])
    @_limit(settings.get("rate_limit_torrent_scrape") or "30 per minute")
    @jwt_required()
    def torrent_scrape():
        user = get_jwt_identity()
        # Legacy guard marker for older audits: if not _TORRENT_SCRAPE_ENABLED:
        # Legacy disabled response marker: return jsonify({"success": False, "error": "Torrent tracker scraping is disabled."}), 403
        # Runtime must read the live settings dict so Admin Panel changes take
        # effect without restarting.
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return _no_store_json({"success": False, "error": "Invalid request"}, 400)
        raw_trackers = data.get("trackers") or []
        if raw_trackers and not isinstance(raw_trackers, list):
            return _no_store_json({"success": False, "error": "Invalid tracker list"}, 400)
        supplied_trackers = _clean_torrent_tracker_list(raw_trackers, limit=12)
        disabled_tracker_count = len(supplied_trackers)
        # Allow bounded built-in fallback tracker/DHT lookups even when arbitrary
        # user-supplied tracker scraping is disabled.  This is what restores
        # seeds/leechers for trackerless torrents without letting users make the
        # server contact any tracker URL they type.
        fallback_only = disabled_tracker_count <= 0 or (_torrent_public_fallback_scrape_enabled() and _is_public_fallback_tracker_list(supplied_trackers))
        if not _torrent_scrape_enabled() and not fallback_only:
            return _no_store_json({
                "success": False,
                "error": "Tracker scraping is disabled. Admin can enable torrent_scrape_enabled under Limits and uploads.",
                "status": "disabled",
                "seeds": None,
                "leechers": None,
                "completed": None,
                "trackers_tried": 0,
                "tracker_count": disabled_tracker_count,
            }, 403)
        guard = _route_rate_limit_guard("torrent_scrape", settings.get("rate_limit_torrent_scrape") or "30 per minute", default_limit=30, default_window=60, user=user)
        if guard is not None:
            return guard
        infohex = str(data.get("infohash_hex") or "").strip().lower()
        if len(infohex) != 40:
            return _no_store_json({"success": False, "error": "Invalid infohash"}, 400)
        try:
            bytes.fromhex(infohex)
        except ValueError:
            return _no_store_json({"success": False, "error": "Invalid infohash"}, 400)

        force_refresh = _as_bool_setting(data.get("force_refresh") or data.get("bypass_cache") or data.get("no_cache"), False)
        result = _scrape_torrent_swarm_counts(infohex, supplied_trackers, force_refresh=force_refresh)
        status = 200 if result.get("success") else 400
        return _no_store_json(result, status)


    @app.route("/api/friends")
    @jwt_required()
    def api_friends():
        user = get_jwt_identity()
        try:
            friend_usernames = list(dict.fromkeys(get_friends_for_user(user) or []))
            if not friend_usernames:
                return jsonify({"friends": []})

            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, online, presence_status, custom_status, last_seen, avatar_url
                      FROM users
                     WHERE username = ANY(%s)
                     ORDER BY username;
                    """,
                    (friend_usernames,),
                )
                rows = cur.fetchall()

            results = []
            for uname, online, presence_status, custom_status, last_seen, avatar_url in rows:
                pres = str(presence_status or "online").strip().lower()
                if pres == "available":
                    pres = "online"
                visible_online = bool(online) and pres != "invisible"
                visible_presence = "offline" if not visible_online else pres
                results.append(
                    {
                        "username": uname,
                        "online": visible_online,
                        "presence": visible_presence,
                        "custom_status": custom_status if visible_online else None,
                        "last_seen": last_seen.isoformat() if last_seen else None,
                        "avatar_url": avatar_url or "",
                    }
                )
        except Exception as e:
            logging.error("[DB ERROR] Failed to fetch friends: %s", e)
            results = []

        return jsonify({"friends": results})
