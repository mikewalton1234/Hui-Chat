# rate_limiter_unavailable
#!/usr/bin/env python3
"""
routes_auth.py

Authentication and user‐management routes, updated for PostgreSQL.
All references to 'password_hash' have been replaced with 'password'
to match an existing users(password) column.
"""

import logging
import hashlib
import secrets
import os
import urllib.parse
import re
from datetime import datetime, timezone, timedelta

from flask import (
    request,
    redirect,
    render_template,
    session,
    jsonify,
    url_for,
    make_response,
    g,
)
from flask_jwt_extended import (
    jwt_required,
    get_jwt,
    get_jwt_identity,
    create_access_token,
    create_refresh_token,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from flask_jwt_extended.utils import decode_token
from flask_wtf.csrf import generate_csrf, validate_csrf
from wtforms.validators import ValidationError
from cryptography.hazmat.primitives import serialization

from constants import (
    KEY_FILE, APP_VERSION, DEFAULT_SERVER_NAME, detect_mobile_client,
    get_chat_script_parts, get_chat_script_urls, get_sound_pack_script_urls,
    normalize_sound_pack_identifier, sanitize_sound_pack_external_urls, sound_pack_script_src,
    sound_pack_local_builtins_enabled,
)
from echo_voice_protocol import echo_voice_bool, echo_voice_client_config, echo_voice_room_limit
from webrtc_ice_config import ice_server_summary, p2p_ice_servers, voice_ice_servers
from database import get_db
from database import (
    create_user_with_keys,
    canonical_username,
    find_user_by_username_ci,
    create_login_session_and_tokens,
    rotate_refresh_and_store_access_token,
    store_auth_token_in_conn,
    apply_auth_risk_event,
    get_auth_version,
    get_public_key_for_username,
    get_encrypted_private_key_for_username,
    ensure_user_has_keys,
    generate_user_keypair_for_password,
    user_exists,
    email_in_use,

    # Token store
    store_auth_token,
    rotate_refresh_token,
    is_refresh_token_active,
    get_refresh_token_meta,
    revoke_auth_token,
    revoke_all_tokens_for_user,

    # Session Truth (device/session tracking)
    create_auth_session,
    touch_auth_session,
    touch_auth_session_activity,
    is_auth_session_active,
    get_auth_session_state,
    get_session_id_for_token,
    attach_session_to_token,
    revoke_auth_session,
    revoke_other_sessions_for_user,
    revoke_all_sessions_for_user,
    list_auth_sessions,
    _decrypt_private_key_blob,
    _encrypt_private_key_v2,
)
from security import hash_password, verify_password, verify_password_and_upgrade, log_audit_event, get_request_ip, parse_rate_limit_value, request_has_valid_double_submit_csrf, simple_rate_limit, is_local_request, is_localish_request
from encryption import load_or_generate_key
from admin_panel_inject import inject_admin_panel
from permissions import check_user_permission, get_user_permissions
from emailer import send_email
from registration_name_policy import normalize_registration_username, validate_registration_username, validate_registration_username_format, username_policy_metadata
from account_creation_policy import (
    PASSWORD_MIN_LENGTH,
    PASSWORD_MAX_LENGTH,
    PASSWORD_RECOMMENDED_LENGTH,
    password_policy_summary,
    password_policy_metadata,
    validate_account_password,
    validate_account_username_style,
    validate_recovery_pin,
    recovery_pin_policy_summary,
    recovery_pin_lock_settings,
    password_reset_limit_settings,
)
from media_mode import client_av_config
from profile_defaults import build_default_avatar_url
from sensitive_fields_crypto import encrypt_sensitive_field, decrypt_sensitive_field
from email_at_rest import display_email, submitted_email_matches
from sms_2fa_config import effective_twilio_settings, twilio_ready
from account_status import account_can_authenticate, account_status_allows_auth, account_status_error_code, account_status_reason, get_effective_account_status
from moderation import get_active_ip_sanction_detail, is_ip_sanctioned

try:
    from realtime.state import auth_session_sids as _state_auth_session_sids, user_sids as _state_user_sids
except Exception:  # pragma: no cover
    _state_auth_session_sids = None
    _state_user_sids = None


def register_auth_routes(app, settings, limiter=None):

    def _build_default_avatar_url(username: str) -> str:
        return build_default_avatar_url(username)
    def _limit(rule, **kwargs):
        """Apply Flask-Limiter rule if available."""
        if limiter is None:
            return lambda f: f
        try:
            return limiter.limit(rule, **kwargs)
        except Exception:
            return lambda f: f

    def _socketio_instance():
        try:
            return app.config.get("ECHOCHAT_SOCKETIO")
        except Exception:
            return None

    def _auth_limit_response(message: str, *, template: str | None = None, status: int = 429, **context):
        retry_after = context.pop('retry_after', None)
        headers = {}
        if retry_after is not None:
            try:
                headers['Retry-After'] = str(int(max(1, float(retry_after))))
            except Exception:
                headers['Retry-After'] = '1'
        if template:
            # The login page has a LAN/HTTP CSRF fallback for accidentally enabled
            # Secure cookies. Keep rate-limit rerenders on that same path instead
            # of returning a fresh form whose hidden token no longer matches the
            # fallback cookie. Other auth templates can use the normal renderer.
            if template == "login.html":
                return _render_login(error=context.pop("error", message), **context), status, headers
            if template == "forgot_password.html":
                return _render_forgot(error=context.pop("error", message), status=status, **context), status, headers
            if template == "reset_password.html":
                return _render_reset(error=context.pop("error", message), status=status, **context), status, headers
            context.setdefault("app_version", APP_VERSION)
            return render_template(template, **context), status, headers
        return jsonify({"ok": False, "error": "rate_limited", "message": message}), status, headers

    def _enforce_named_rate_limit(scope: str, identifier: str, cfg_value, *, default_limit: int, default_window: int) -> tuple[bool, float]:
        ident = str(identifier or '').strip().lower()
        if not ident:
            return True, 0.0
        lim, win = parse_rate_limit_value(cfg_value, default_limit=default_limit, default_window=default_window)
        return simple_rate_limit(f'{scope}:{ident}', limit=lim, window_sec=win)

    def _refresh_rotation_grace_seconds() -> int:
        """Return a safe refresh-token race/replay grace window in seconds."""
        try:
            seconds = int(float(settings.get("refresh_rotation_grace_seconds", 10)))
        except Exception:
            seconds = 10
        return max(0, min(seconds, 300))

    def _auth_json_response(payload: dict, status: int = 200, *, clear_cookies: bool = False):
        """Build a non-cacheable JSON response for auth/session truth endpoints."""
        resp = jsonify(payload)
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        if clear_cookies:
            unset_jwt_cookies(resp)
        if int(status) == 200:
            return resp
        return resp, int(status)

    def _refresh_json_response(payload: dict, status: int = 200, *, clear_cookies: bool = False):
        """Build a non-cacheable JSON response for /token/refresh."""
        return _auth_json_response(payload, status=status, clear_cookies=clear_cookies)

    def _active_ip_ban_message(ip: str | None) -> str:
        """Human-readable reason for current request IP-ban enforcement."""
        reason = None
        expires_at = None
        try:
            reason, expires_at = get_active_ip_sanction_detail(ip)
        except Exception:
            reason, expires_at = None, None
        msg = "This connection is blocked by an active IP ban."
        if reason:
            msg += f" Reason: {reason}"
        if expires_at:
            try:
                msg += f" Until: {expires_at.isoformat()}"
            except Exception:
                pass
        return msg

    def _current_request_ip_banned() -> tuple[bool, str | None, str]:
        ip = get_request_ip() or None
        try:
            if ip and is_ip_sanctioned(ip):
                return True, ip, _active_ip_ban_message(ip)
        except Exception:
            # Do not fail open on auth boundaries if the active IP-ban check errors.
            logging.warning("IP-ban enforcement check failed during auth", exc_info=True)
            return True, ip, "This connection could not be verified for sign-in. Please contact an admin."
        return False, ip, ""

    def _log_ip_ban_block(action: str, ip: str | None, detail: str = "") -> None:
        try:
            log_audit_event("system", action, ip or "unknown", detail or "blocked by active IP ban")
        except Exception:
            pass

    def _chat_ip_ban_redirect(ip: str | None, message: str):
        _log_ip_ban_block("chat_ip_ban_blocked", ip)
        resp = redirect(f"/login?reason={urllib.parse.quote('ip_banned')}")
        try:
            unset_jwt_cookies(resp)
        except Exception:
            pass
        try:
            session.clear()
        except Exception:
            pass
        return resp

    def _account_status_auth_allowed(username: str) -> tuple[bool, str | None, str, str]:
        """Return whether an account may use auth/session features right now."""
        return account_can_authenticate(username)

    def _account_status_json_failure(username: str, *, clear_cookies: bool = True):
        allowed, status, code, reason = _account_status_auth_allowed(username)
        if allowed:
            return None
        return _auth_json_response(
            {"ok": False, "error": code, "account_status": status, "message": reason},
            status=403 if status in {"suspended", "deactivated"} else 401,
            clear_cookies=clear_cookies,
        )

    def _username_availability_json(payload: dict, status: int = 200):
        """Build a non-cacheable JSON response for live username checks."""
        return _auth_json_response(payload, status=status)

    def _valid_public_key_pem(public_pem: str) -> bool:
        """Return True only for a small, parseable PEM SubjectPublicKeyInfo blob."""
        pem = str(public_pem or "").strip()
        if not pem or len(pem) > 8192:
            return False
        if not (pem.startswith("-----BEGIN PUBLIC KEY-----") and pem.endswith("-----END PUBLIC KEY-----")):
            return False
        try:
            serialization.load_pem_public_key(pem.encode("utf-8"))
            return True
        except Exception:
            return False

    def _public_key_json(payload: dict, status: int = 200):
        """Build a non-cacheable JSON response for E2EE public-key discovery."""
        return _auth_json_response(payload, status=status)

    def _as_aware_utc(value):
        """Return datetimes as timezone-aware UTC for safe PostgreSQL comparisons."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value

    def _safe_password_reset_base_url() -> str:
        """Resolve the public URL used in emailed reset links.

        Avoid trusting arbitrary Host headers for internet requests. In production,
        set public_base_url/ECHOCHAT_PUBLIC_BASE_URL. For local dev, deriving from
        request.host_url is acceptable so localhost/LAN testing still works.
        """
        configured = str(settings.get("public_base_url") or "").strip().rstrip("/")
        if configured:
            parsed = urllib.parse.urlparse(configured)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                return configured
            logging.error("Invalid public_base_url for password reset links: %r", configured)
            return ""
        if is_localish_request():
            return request.host_url.rstrip("/")
        logging.error("Password reset email blocked: public_base_url is not set for a non-local request.")
        return ""


    LOGIN_CSRF_FALLBACK_COOKIE = "echochat_login_csrf"
    REGISTER_CSRF_FALLBACK_COOKIE = "echochat_register_csrf"
    FORGOT_CSRF_FALLBACK_COOKIE = "echochat_forgot_csrf"
    RESET_CSRF_FALLBACK_COOKIE = "echochat_reset_csrf"
    ENABLE_2FA_CSRF_FALLBACK_COOKIE = "echochat_enable_2fa_csrf"

    _EMAIL_RE = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+$", re.IGNORECASE)
    _DUMMY_LOGIN_HASH = hash_password("EchoChat dummy login timing password v1")
    _DUMMY_RECOVERY_PIN_HASH = hash_password("000000")

    def _dummy_password_verify(candidate: str) -> None:
        try:
            verify_password_and_upgrade(candidate or "", _DUMMY_LOGIN_HASH)
        except Exception:
            pass

    def _dummy_recovery_pin_verify(candidate: str) -> None:
        try:
            verify_password_and_upgrade(candidate or "", _DUMMY_RECOVERY_PIN_HASH)
        except Exception:
            pass


    def _csrf_time_limit_seconds() -> int | None:
        raw = app.config.get("WTF_CSRF_TIME_LIMIT", 3600)
        if raw is None:
            return None
        try:
            return max(60, int(raw))
        except Exception:
            return 3600

    def _signed_csrf_token_is_well_formed(token: str) -> bool:
        """Validate a Flask-WTF signed CSRF token without requiring session state.

        This is deliberately narrower than normal Flask-WTF validation and is
        only used for the local/LAN HTTP fallback below. Normal login POSTs still
        use validate_csrf(), which checks both the signed token and the Flask
        session value.
        """
        token = str(token or "").strip()
        if not token:
            return False
        try:
            from itsdangerous import URLSafeTimedSerializer

            secret_key = app.config.get("WTF_CSRF_SECRET_KEY") or app.config.get("SECRET_KEY")
            if not secret_key:
                return False
            serializer = URLSafeTimedSerializer(secret_key, salt="wtf-csrf-token")
            return bool(serializer.loads(token, max_age=_csrf_time_limit_seconds()))
        except Exception:
            return False

    def _login_csrf_fallback_valid(submitted_token: str) -> bool:
        """Accept login CSRF when Secure session cookies were enabled on LAN HTTP.

        Browsers correctly refuse Secure Flask session cookies over plain HTTP,
        which otherwise makes the login form fail before JWT cookies can be set.
        For local/private HTTP only, require the submitted signed CSRF token to
        exactly match an HttpOnly fallback cookie and verify the token signature.
        """
        if not _lan_http_cookie_fallback_allowed():
            return False
        submitted = str(submitted_token or "").strip()
        cookie_token = str(request.cookies.get(LOGIN_CSRF_FALLBACK_COOKIE) or "").strip()
        if not submitted or not cookie_token:
            return False
        if not secrets.compare_digest(submitted, cookie_token):
            return False
        return _signed_csrf_token_is_well_formed(submitted)

    def _set_login_csrf_fallback_cookie(resp, token: str | None):
        if _lan_http_cookie_fallback_allowed() and token:
            max_age = _csrf_time_limit_seconds() or 3600
            resp.set_cookie(
                LOGIN_CSRF_FALLBACK_COOKIE,
                str(token),
                max_age=max_age,
                secure=False,
                httponly=True,
                samesite="Lax",
                path="/login",
            )
        else:
            resp.delete_cookie(LOGIN_CSRF_FALLBACK_COOKIE, path="/login")
        return resp

    def _render_login(error: str | None = None, **extra):
        try:
            login_csrf_token = generate_csrf()
        except Exception:
            login_csrf_token = ""
        ctx = {
            "error": error,
            "two_factor_pending": False,
            "two_factor_phone_mask": "",
            "two_factor_hint": "",
            "prefill_username": "",
            "app_version": APP_VERSION,
            "login_csrf_token": login_csrf_token,
        }
        ctx.update(extra or {})
        resp = make_response(render_template("login.html", **ctx))
        return _set_login_csrf_fallback_cookie(resp, ctx.get("login_csrf_token"))

    def _server_challenges(kind: str) -> dict:
        key = f"ECHOCHAT_{kind.upper()}_CHALLENGES"
        store = app.config.get(key)
        if not isinstance(store, dict):
            store = {}
            app.config[key] = store
        return store

    def _save_server_challenge(kind: str, username: str, phone: str) -> str:
        challenge_id = secrets.token_urlsafe(24)
        _server_challenges(kind)[challenge_id] = {
            "username": str(username or "").strip(),
            "phone": _normalize_phone_e164(phone or ""),
            "started_at": datetime.now(timezone.utc).timestamp(),
            "auth_version": get_auth_version(username),
        }
        return challenge_id

    def _load_server_challenge(kind: str, challenge_id: str) -> dict | None:
        if not challenge_id:
            return None
        item = _server_challenges(kind).get(str(challenge_id))
        return dict(item) if isinstance(item, dict) else None

    def _pop_server_challenge(kind: str, challenge_id: str | None) -> None:
        if challenge_id:
            _server_challenges(kind).pop(str(challenge_id), None)

    def _login_2fa_account_still_matches(username: str, phone: str, auth_version: int | None = None) -> bool:
        """Return true when the pending SMS login challenge still matches the account."""
        username = str(username or "").strip()
        expected_phone = _normalize_phone_e164(phone or "")
        if not username or not expected_phone:
            return False
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT username, phone, two_factor_enabled, COALESCE(auth_version, 0) FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1;",
                    (username,),
                )
                row = cur.fetchone()
        except Exception as exc:
            logging.error("DB error checking pending SMS 2FA login state for %s: %s", username, exc)
            return False
        if not row or not bool(row[2]):
            return False
        if auth_version is not None:
            try:
                if int(auth_version) != int(row[3] or 0):
                    return False
            except Exception:
                return False
        allowed, _status, _code, _reason = _account_status_auth_allowed(str(row[0] or username))
        if not allowed:
            return False
        current_phone = _normalize_phone_e164(decrypt_sensitive_field(row[1] or "", settings, field_name="users.phone"))
        return bool(current_phone and secrets.compare_digest(current_phone, expected_phone))


    def _forgot_csrf_fallback_valid(submitted_token: str) -> bool:
        """Accept forgot-password CSRF only for the same LAN HTTP fallback as login/register.

        This keeps password-reset recovery usable during localhost/LAN testing when
        Secure Flask session cookies were accidentally enabled on plain HTTP. Normal
        POSTs still use Flask-WTF session-bound CSRF validation first.
        """
        if not _lan_http_cookie_fallback_allowed():
            return False
        submitted = str(submitted_token or "").strip()
        cookie_token = str(request.cookies.get(FORGOT_CSRF_FALLBACK_COOKIE) or "").strip()
        if not submitted or not cookie_token:
            return False
        if not secrets.compare_digest(submitted, cookie_token):
            return False
        return _signed_csrf_token_is_well_formed(submitted)

    def _set_forgot_csrf_fallback_cookie(resp, token: str | None):
        if _lan_http_cookie_fallback_allowed() and token:
            max_age = _csrf_time_limit_seconds() or 3600
            resp.set_cookie(
                FORGOT_CSRF_FALLBACK_COOKIE,
                str(token),
                max_age=max_age,
                secure=False,
                httponly=True,
                samesite="Lax",
                path="/forgot-password",
            )
        else:
            resp.delete_cookie(FORGOT_CSRF_FALLBACK_COOKIE, path="/forgot-password")
        return resp

    def _forgot_form_values(**values) -> dict:
        """Return safe forgot-password values to re-display after failed submit."""
        allowed = {"email", "username"}
        return {key: str(values.get(key) or "")[:254] for key in allowed}

    def _render_forgot(
        error: str | None = None,
        *,
        message: str | None = None,
        status: int = 200,
        values: dict | None = None,
    ):
        try:
            forgot_csrf_token = generate_csrf()
        except Exception:
            forgot_csrf_token = ""
        username_policy = username_policy_metadata(settings)
        ctx = {
            "error": error,
            "message": message,
            "app_version": APP_VERSION,
            "forgot_csrf_token": forgot_csrf_token,
            "forgot_values": _forgot_form_values(**(values or {})),
            "username_policy": username_policy.get("summary"),
            "username_min_length": username_policy.get("min_length"),
            "username_max_length": username_policy.get("max_length"),
            "username_html_pattern": username_policy.get("html_pattern"),
            "username_title": username_policy.get("title"),
            "recovery_pin_policy": recovery_pin_policy_summary(),
        }
        resp = make_response(render_template("forgot_password.html", **ctx), int(status))
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return _set_forgot_csrf_fallback_cookie(resp, ctx.get("forgot_csrf_token"))

    def _reset_csrf_fallback_valid(submitted_token: str) -> bool:
        """Accept reset-password CSRF only for the same LAN HTTP fallback as login/register.

        Reset links are often tested from localhost/LAN before HTTPS is configured.
        If an admin accidentally enabled Secure Flask session cookies on plain HTTP,
        fall back to a path-scoped HttpOnly CSRF mirror while still requiring a
        signed Flask-WTF token. Normal POSTs use validate_csrf() first.
        """
        if not _lan_http_cookie_fallback_allowed():
            return False
        submitted = str(submitted_token or "").strip()
        cookie_token = str(request.cookies.get(RESET_CSRF_FALLBACK_COOKIE) or "").strip()
        if not submitted or not cookie_token:
            return False
        if not secrets.compare_digest(submitted, cookie_token):
            return False
        return _signed_csrf_token_is_well_formed(submitted)

    def _set_reset_csrf_fallback_cookie(resp, token: str | None):
        if _lan_http_cookie_fallback_allowed() and token:
            max_age = _csrf_time_limit_seconds() or 3600
            resp.set_cookie(
                RESET_CSRF_FALLBACK_COOKIE,
                str(token),
                max_age=max_age,
                secure=False,
                httponly=True,
                samesite="Lax",
                path="/reset-password",
            )
        else:
            resp.delete_cookie(RESET_CSRF_FALLBACK_COOKIE, path="/reset-password")
        return resp

    def _render_reset(
        error: str | None = None,
        *,
        message: str | None = None,
        status: int = 200,
        require_pin: bool = True,
        reset_username: str | None = None,
        reset_complete: bool = False,
        login_redirect_url: str | None = None,
        login_redirect_seconds: int = 3,
    ):
        try:
            reset_csrf_token = "" if reset_complete else generate_csrf()
        except Exception:
            reset_csrf_token = ""
        policy = password_policy_metadata()
        ctx = {
            "error": error,
            "message": message,
            "app_version": APP_VERSION,
            "reset_csrf_token": reset_csrf_token,
            "require_pin": bool(require_pin),
            "reset_username": reset_username,
            "reset_complete": bool(reset_complete),
            "login_redirect_url": login_redirect_url,
            "login_redirect_seconds": int(login_redirect_seconds or 3),
            "password_min_length": policy.get("min_length", PASSWORD_MIN_LENGTH),
            "password_max_length": policy.get("max_length", PASSWORD_MAX_LENGTH),
            "password_recommended_length": policy.get("recommended_length", PASSWORD_RECOMMENDED_LENGTH),
            "password_common_weak": policy.get("common_weak", []),
        }
        resp = make_response(render_template("reset_password.html", **ctx), int(status))
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return _set_reset_csrf_fallback_cookie(resp, ctx.get("reset_csrf_token"))

    def _register_csrf_fallback_valid(submitted_token: str) -> bool:
        """Accept register CSRF only for the same LAN HTTP fallback as login.

        This prevents the register page from failing before account creation when
        an admin accidentally enabled Secure session cookies while still testing
        on plain-http localhost/LAN. Normal registration still uses Flask-WTF's
        session-bound CSRF validation first.
        """
        if not _lan_http_cookie_fallback_allowed():
            return False
        submitted = str(submitted_token or "").strip()
        cookie_token = str(request.cookies.get(REGISTER_CSRF_FALLBACK_COOKIE) or "").strip()
        if not submitted or not cookie_token:
            return False
        if not secrets.compare_digest(submitted, cookie_token):
            return False
        return _signed_csrf_token_is_well_formed(submitted)

    def _set_register_csrf_fallback_cookie(resp, token: str | None):
        if _lan_http_cookie_fallback_allowed() and token:
            max_age = _csrf_time_limit_seconds() or 3600
            resp.set_cookie(
                REGISTER_CSRF_FALLBACK_COOKIE,
                str(token),
                max_age=max_age,
                secure=False,
                httponly=True,
                samesite="Lax",
                path="/register",
            )
        else:
            resp.delete_cookie(REGISTER_CSRF_FALLBACK_COOKIE, path="/register")
        return resp

    def _register_form_values(**values) -> dict:
        """Return safe values to re-display after a failed register submit."""
        allowed = {"username", "email", "phone", "age"}
        return {key: str(values.get(key) or "")[:254] for key in allowed}

    def _render_register(error: str | None = None, *, status: int = 200, values: dict | None = None):
        try:
            register_csrf_token = generate_csrf()
        except Exception:
            register_csrf_token = ""
        username_policy = username_policy_metadata(settings)
        password_policy = password_policy_metadata()
        ctx = {
            "error": error,
            "app_version": APP_VERSION,
            "register_csrf_token": register_csrf_token,
            "register_values": _register_form_values(**(values or {})),
            "username_policy": username_policy.get("summary"),
            "username_min_length": username_policy.get("min_length"),
            "username_max_length": username_policy.get("max_length"),
            "username_html_pattern": username_policy.get("html_pattern"),
            "username_title": username_policy.get("title"),
            "password_policy": password_policy.get("summary"),
            "password_min_length": password_policy.get("min_length"),
            "password_max_length": password_policy.get("max_length"),
            "password_recommended_length": password_policy.get("recommended_length"),
            "password_common_weak": password_policy.get("common_weak"),
        }
        resp = make_response(render_template("register.html", **ctx), int(status))
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return _set_register_csrf_fallback_cookie(resp, ctx.get("register_csrf_token"))

    def _normalize_registration_email(raw_email: str) -> tuple[str, str | None]:
        email = str(raw_email or "").strip().lower()
        if not email:
            return "", "Email is required."
        if len(email) > 254 or any(ord(ch) < 32 for ch in email):
            return "", "Email is invalid."
        if email.count("@") != 1:
            return "", "Email is invalid."
        local, domain = email.split("@", 1)
        if not local or not domain or len(local) > 64:
            return "", "Email is invalid."
        if local.startswith(".") or local.endswith(".") or ".." in local:
            return "", "Email is invalid."
        if not _EMAIL_RE.fullmatch(email):
            return "", "Email is invalid."
        labels = domain.split(".")
        if any((not label or label.startswith("-") or label.endswith("-")) for label in labels):
            return "", "Email is invalid."
        return email, None

    def _stale_login_form_response():
        """Recover cleanly from stale login CSRF after a server restart or old tab submit.

        A browser can keep an old /login form open while the development server is
        stopped/restarted.  Submitting that stale form used to show a raw
        "Invalid CSRF token" page.  Clear transient auth state, issue a fresh GET,
        and let the user sign in from a new form instead.
        """
        try:
            session.clear()
        except Exception:
            pass
        resp = make_response(redirect("/login?reason=login_form_expired"))
        try:
            unset_jwt_cookies(resp)
        except Exception:
            pass
        return resp

    def _normalize_phone_e164(raw: str) -> str:
        val = str(raw or "").strip()
        if not val:
            return ""
        compact = re.sub(r"[^0-9+]", "", val)
        if compact.startswith("00"):
            compact = "+" + compact[2:]
        if not compact.startswith("+"):
            return ""
        digits = compact[1:]
        if not digits.isdigit():
            return ""
        if len(digits) < 8 or len(digits) > 15:
            return ""
        return "+" + digits

    def _mask_phone(phone: str) -> str:
        p = _normalize_phone_e164(phone)
        if not p:
            return ""
        digits = p[1:]
        if len(digits) <= 4:
            return p
        return f"+{'*' * max(0, len(digits) - 4)}{digits[-4:]}"

    def _twilio_verify_ready() -> bool:
        return twilio_ready(settings)

    def _twilio_verify_client():
        from twilio.rest import Client

        cfg = effective_twilio_settings(settings)
        account_sid = str(cfg.get("twilio_account_sid") or "").strip()
        auth_token = str(cfg.get("twilio_auth_token") or "").strip()
        service_sid = str(cfg.get("twilio_verify_service_sid") or "").strip()
        if not (account_sid and auth_token and service_sid):
            raise RuntimeError("Twilio Verify is not configured")
        return Client(account_sid, auth_token), service_sid

    def _send_sms_2fa_code(phone: str) -> tuple[bool, str]:
        phone = _normalize_phone_e164(phone)
        if not phone:
            return False, "Phone number must be in international format like +15551234567."
        if not _twilio_verify_ready():
            return False, "SMS 2FA is disabled or not configured on this server."
        try:
            client, service_sid = _twilio_verify_client()
            verification = client.verify.v2.services(service_sid).verifications.create(
                channel=str(effective_twilio_settings(settings).get("two_factor_sms_channel") or "sms").strip() or "sms",
                to=phone,
            )
            status = str(getattr(verification, "status", "") or "").strip().lower()
            if status == "pending":
                return True, "Code sent"
            return False, f"Verification send failed ({status or 'unknown'})."
        except Exception as exc:
            logging.error("Twilio Verify send failed: %s", exc)
            return False, "Could not send the verification text message. Check Twilio Verify settings and logs."

    def _check_sms_2fa_code(phone: str, code: str) -> tuple[bool, str]:
        phone = _normalize_phone_e164(phone)
        code = str(code or "").strip()
        if not phone:
            return False, "Missing phone number for 2FA check."
        if not code or not code.isdigit() or len(code) < 4 or len(code) > 10:
            return False, "Enter the code from the text message."
        if not _twilio_verify_ready():
            return False, "SMS 2FA is disabled or not configured on this server."
        try:
            client, service_sid = _twilio_verify_client()
            check = client.verify.v2.services(service_sid).verification_checks.create(to=phone, code=code)
            status = str(getattr(check, "status", "") or "").strip().lower()
            if status == "approved":
                return True, "approved"
            return False, "That verification code was not accepted."
        except Exception as exc:
            logging.error("Twilio Verify check failed: %s", exc)
            return False, "Could not verify that code right now. Check Twilio Verify settings and logs."

    def _clear_pending_login_2fa() -> None:
        _pop_server_challenge("login_2fa", session.pop("pending_2fa_challenge_id", None))
        session.pop("pending_2fa_username", None)
        session.pop("pending_2fa_phone", None)
        session.pop("pending_2fa_started_at", None)

    def _clear_pending_enable_2fa() -> None:
        _pop_server_challenge("enable_2fa", session.pop("enable_2fa_challenge_id", None))
        session.pop("enable_2fa_phone", None)
        session.pop("enable_2fa_user", None)
        session.pop("enable_2fa_started_at", None)

    def _enable_2fa_csrf_fallback_valid(submitted_token: str) -> bool:
        """Accept SMS-2FA management CSRF only for local/LAN HTTP testing fallback."""
        if not _lan_http_cookie_fallback_allowed():
            return False
        submitted = str(submitted_token or "").strip()
        cookie_token = str(request.cookies.get(ENABLE_2FA_CSRF_FALLBACK_COOKIE) or "").strip()
        if not submitted or not cookie_token:
            return False
        if not secrets.compare_digest(submitted, cookie_token):
            return False
        return _signed_csrf_token_is_well_formed(submitted)

    def _set_enable_2fa_csrf_fallback_cookie(resp, token: str | None):
        if _lan_http_cookie_fallback_allowed() and token:
            max_age = _csrf_time_limit_seconds() or 3600
            resp.set_cookie(
                ENABLE_2FA_CSRF_FALLBACK_COOKIE,
                str(token),
                max_age=max_age,
                secure=False,
                httponly=True,
                samesite="Lax",
                path="/enable-2fa",
            )
        else:
            resp.delete_cookie(ENABLE_2FA_CSRF_FALLBACK_COOKIE, path="/enable-2fa")
        return resp

    def _render_enable_2fa(
        *,
        enabled: bool,
        saved_phone: str = "",
        pending_phone: str = "",
        pending_active: bool = False,
        message: str | None = None,
        error: str | None = None,
        status: int = 200,
    ):
        try:
            enable_2fa_csrf_token = generate_csrf()
        except Exception:
            enable_2fa_csrf_token = ""
        resp = make_response(render_template(
            "two_factor_sms.html",
            enabled=bool(enabled),
            saved_phone=saved_phone,
            pending_phone_mask=_mask_phone(pending_phone),
            pending_active=bool(pending_active),
            message=message,
            error=error,
            twilio_ready=_twilio_verify_ready(),
            app_version=APP_VERSION,
            enable_2fa_csrf_token=enable_2fa_csrf_token,
            two_factor_timeout_seconds=int(effective_twilio_settings(settings).get("two_factor_login_timeout_seconds") or 600),
        ), int(status))
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return _set_enable_2fa_csrf_fallback_cookie(resp, enable_2fa_csrf_token)

    def _effective_admin_for_user(username: str) -> bool:
        """Resolve live RBAC admin state for login/bootstrap flows."""
        try:
            return bool(
                check_user_permission(username, "admin:basic")
            )
        except Exception:
            return False

    def _ensure_baseline_viewer_role(username: str) -> None:
        """Self-heal the baseline viewer role for accounts missing it."""
        username = str(username or "").strip()
        if not username:
            return
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;", (username,))
            user_row = cur.fetchone()
            cur.execute("SELECT id FROM roles WHERE name = 'viewer' LIMIT 1;")
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

    def _issue_replacement_auth_cookies(username: str, sid: str, *, user_agent: str | None, ip_address: str | None, redirect_to: str | None = None):
        """Mint and persist fresh cookies for the current auth session after an auth-risk event."""
        access_token = create_access_token(identity=username, additional_claims={"sid": sid})
        refresh_token = create_refresh_token(identity=username, additional_claims={"sid": sid})
        conn = get_db()
        try:
            a = decode_token(access_token, allow_expired=False)
            r = decode_token(refresh_token, allow_expired=False)
            store_auth_token_in_conn(
                conn,
                jti=a.get("jti"),
                username=username,
                token_type="access",
                expires_at=(datetime.fromtimestamp(a.get("exp"), tz=timezone.utc) if isinstance(a.get("exp"), (int, float)) else None),
                session_id=sid,
                user_agent=user_agent,
                ip_address=ip_address,
            )
            store_auth_token_in_conn(
                conn,
                jti=r.get("jti"),
                username=username,
                token_type="refresh",
                expires_at=(datetime.fromtimestamp(r.get("exp"), tz=timezone.utc) if isinstance(r.get("exp"), (int, float)) else None),
                session_id=sid,
                user_agent=user_agent,
                ip_address=ip_address,
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        resp = make_response(redirect(redirect_to) if redirect_to else jsonify({"ok": True}))
        return _set_auth_cookies_for_response(resp, access_token, refresh_token)

    def _clear_user_transient_presence(username: str) -> None:
        """Clear one-session presence text whenever a user signs in/out."""
        username = str(username or "").strip()
        if not username:
            return
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET presence_status = 'online', custom_status = NULL WHERE LOWER(username) = LOWER(%s);",
                    (username,),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    def _effective_request_scheme() -> str:
        """Return the best-effort browser-facing request scheme."""
        forwarded = str(request.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        if forwarded in {"http", "https"}:
            return forwarded
        return "https" if request.is_secure else "http"

    def _public_base_url_is_https() -> bool:
        raw = str(settings.get("public_base_url") or "").strip().lower()
        return raw.startswith("https://")

    def _lan_http_cookie_fallback_allowed() -> bool:
        """Allow LAN/mobile testing to work when secure cookies were enabled too early.

        Phones usually open the dev server with a LAN IP such as
        http://192.168.x.x:5000.  If cookie_secure/https was enabled while the
        page is still plain HTTP, mobile browsers correctly refuse the Secure
        auth cookies and /chat immediately redirects back to /login.

        This fallback only applies to local/private clients over plain HTTP, and
        it is disabled automatically for public HTTPS deployments.
        """
        if not bool(settings.get("allow_insecure_lan_cookie_fallback", True)):
            return False
        if not bool(settings.get("cookie_secure", False) or settings.get("https", False)):
            return False
        if _public_base_url_is_https():
            return False
        if _effective_request_scheme() == "https":
            return False
        try:
            return bool(is_localish_request(request))
        except Exception:
            return False

    def _safe_lan_cookie_samesite() -> str:
        samesite = str(settings.get("cookie_samesite") or "Lax").strip() or "Lax"
        # SameSite=None without Secure is rejected by modern browsers.  When we
        # intentionally relax Secure for LAN HTTP testing, use Lax so login
        # cookies survive on phones and tablets.
        if samesite.lower() == "none":
            return "Lax"
        if samesite.lower() not in {"lax", "strict"}:
            return "Lax"
        return "Strict" if samesite.lower() == "strict" else "Lax"

    def _set_lan_http_jwt_cookies(resp, access_token: str, refresh_token: str) -> None:
        """Re-set JWT cookies without Secure for local phone/LAN HTTP testing."""
        try:
            access_claims = decode_token(access_token, allow_expired=False)
            refresh_claims = decode_token(refresh_token, allow_expired=False)
        except Exception:
            access_claims, refresh_claims = {}, {}

        samesite = _safe_lan_cookie_samesite()
        access_max_age = max(60, int(settings.get("access_token_minutes", 30) or 30) * 60)
        refresh_max_age = max(3600, int(settings.get("refresh_token_days", 7) or 7) * 24 * 60 * 60)

        resp.set_cookie(
            app.config.get("JWT_ACCESS_COOKIE_NAME", "echochat_access"),
            access_token,
            max_age=access_max_age,
            secure=False,
            httponly=True,
            samesite=samesite,
            path=app.config.get("JWT_ACCESS_COOKIE_PATH", "/"),
        )
        resp.set_cookie(
            app.config.get("JWT_REFRESH_COOKIE_NAME", "echochat_refresh"),
            refresh_token,
            max_age=refresh_max_age,
            secure=False,
            httponly=True,
            samesite=samesite,
            path=app.config.get("JWT_REFRESH_COOKIE_PATH", "/token/refresh"),
        )

        if bool(app.config.get("JWT_COOKIE_CSRF_PROTECT", True)):
            access_csrf = access_claims.get("csrf")
            refresh_csrf = refresh_claims.get("csrf")
            if access_csrf:
                resp.set_cookie(
                    app.config.get("JWT_ACCESS_CSRF_COOKIE_NAME", "csrf_access_token"),
                    access_csrf,
                    max_age=access_max_age,
                    secure=False,
                    httponly=False,
                    samesite=samesite,
                    path=app.config.get("JWT_ACCESS_CSRF_COOKIE_PATH", "/"),
                )
            if refresh_csrf:
                resp.set_cookie(
                    app.config.get("JWT_REFRESH_CSRF_COOKIE_NAME", "csrf_refresh_token"),
                    refresh_csrf,
                    max_age=refresh_max_age,
                    secure=False,
                    httponly=False,
                    samesite=samesite,
                    path=app.config.get("JWT_REFRESH_CSRF_COOKIE_PATH", "/"),
                )

    def _set_auth_cookies_for_response(resp, access_token: str, refresh_token: str):
        """Set auth cookies, with a safe LAN/mobile HTTP fallback when needed."""
        set_access_cookies(resp, access_token)
        set_refresh_cookies(resp, refresh_token)
        if _lan_http_cookie_fallback_allowed():
            _set_lan_http_jwt_cookies(resp, access_token, refresh_token)
        return resp

    def _finalize_login_success(username: str):
        allowed, account_status, code, reason = _account_status_auth_allowed(username)
        if not allowed:
            msg = "Invalid username or password" if not is_localish_request() else (reason or "This account cannot sign in right now.")
            return _render_login(error=msg)
        is_admin = _effective_admin_for_user(username)
        ua = request.headers.get("User-Agent")
        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        if ip_banned:
            _clear_pending_login_2fa()
            try:
                log_audit_event("anon", "login_ip_ban_blocked", banned_ip, f"user={username}")
            except Exception:
                pass
            return _render_login(error=ip_ban_message)
        ip = banned_ip or get_request_ip() or None

        # Custom presence text is intentionally transient. Start each login clean.
        _clear_user_transient_presence(username)

        # Create tokens first, then persist session + token JTIs atomically before cookies are sent.
        temp_access = create_access_token(identity=username, additional_claims={"sid": "pending"})
        temp_refresh = create_refresh_token(identity=username, additional_claims={"sid": "pending"})
        try:
            a = decode_token(temp_access, allow_expired=False)
            r = decode_token(temp_refresh, allow_expired=False)
            a_exp = a.get("exp")
            r_exp = r.get("exp")
            # Create the DB session first so the final JWTs can carry its sid.
            sid = None
            # Generate final tokens after session creation inside the DB helper sequence.
            from db.core import get_db as _login_get_db
            login_conn = _login_get_db()
            try:
                from database import create_auth_session_in_conn as _create_auth_session_in_conn
                sid = _create_auth_session_in_conn(login_conn, username, user_agent=ua, ip_address=ip)
                access_token = create_access_token(identity=username, additional_claims={"sid": sid})
                refresh_token = create_refresh_token(identity=username, additional_claims={"sid": sid})
                a = decode_token(access_token, allow_expired=False)
                r = decode_token(refresh_token, allow_expired=False)
                store_auth_token_in_conn(
                    login_conn,
                    jti=a.get("jti"),
                    username=username,
                    token_type="access",
                    expires_at=(datetime.fromtimestamp(a.get("exp"), tz=timezone.utc) if isinstance(a.get("exp"), (int, float)) else None),
                    session_id=sid,
                    user_agent=ua,
                    ip_address=ip,
                )
                store_auth_token_in_conn(
                    login_conn,
                    jti=r.get("jti"),
                    username=username,
                    token_type="refresh",
                    expires_at=(datetime.fromtimestamp(r.get("exp"), tz=timezone.utc) if isinstance(r.get("exp"), (int, float)) else None),
                    session_id=sid,
                    user_agent=ua,
                    ip_address=ip,
                )
                login_conn.commit()
            except Exception:
                try:
                    login_conn.rollback()
                except Exception:
                    pass
                raise
        except Exception as exc:
            logging.error("Login token/session persistence failed for %s: %s", username, exc)
            _clear_pending_login_2fa()
            return _render_login(error="Could not start a secure session. Please try again.")

        # Privilege transition: clear transient Flask session data before marking authenticated.
        session.clear()
        session.update({"username": username, "is_admin": bool(is_admin), "auth_session_id": sid})

        try:
            _ensure_baseline_viewer_role(username)
        except Exception:
            logging.warning("Could not self-heal baseline viewer role for %s", username, exc_info=True)

        resp = make_response(redirect("/chat"))
        resp.delete_cookie(LOGIN_CSRF_FALLBACK_COOKIE, path="/login")
        return _set_auth_cookies_for_response(resp, access_token, refresh_token)


    def _socket_sids_for_user(username: str, auth_session_ids: set[str] | None = None) -> list[str]:
        username = str(username or "").strip().lower()
        if not username:
            return []
        auth_session_ids = {str(x or "").strip() for x in (auth_session_ids or set()) if str(x or "").strip()}
        out: list[str] = []
        seen: set[str] = set()
        try:
            if auth_session_ids and _state_auth_session_sids is not None:
                for auth_sid in auth_session_ids:
                    for sock_sid in list(_state_auth_session_sids(username, auth_sid) or []):
                        if sock_sid and sock_sid not in seen:
                            seen.add(sock_sid)
                            out.append(sock_sid)
                return out
            if _state_user_sids is not None:
                for sock_sid in list(_state_user_sids(username) or []):
                    if sock_sid and sock_sid not in seen:
                        seen.add(sock_sid)
                        out.append(sock_sid)
        except Exception:
            return []
        return out

    def _force_logout_live_sessions(
        username: str,
        reason: str,
        *,
        auth_session_ids: set[str] | None = None,
        exclude_auth_session_ids: set[str] | None = None,
        action: str = "session_revoked",
        code: str = "session_revoked",
    ) -> int:
        username = str(username or "").strip().lower()
        if not username:
            return 0
        socketio = _socketio_instance()
        if socketio is None:
            return 0

        targets: list[str] = []
        excluded = {str(x or "").strip() for x in (exclude_auth_session_ids or set()) if str(x or "").strip()}
        for sock_sid in _socket_sids_for_user(username, auth_session_ids=auth_session_ids):
            skip = False
            if excluded and _state_auth_session_sids is not None:
                for excluded_sid in excluded:
                    try:
                        if sock_sid in list(_state_auth_session_sids(username, excluded_sid) or []):
                            skip = True
                            break
                    except Exception:
                        continue
            if not skip:
                targets.append(sock_sid)

        payload = {
            "username": username,
            "reason": str(reason or "Signed out"),
            "action": str(action or "session_revoked"),
            "code": str(code or "session_revoked"),
        }
        delivered = 0
        for sock_sid in targets:
            try:
                socketio.emit("force_logout", payload, to=sock_sid)
                socketio.emit("admin_force_logout", payload, to=sock_sid)
                delivered += 1
            except Exception:
                pass
            try:
                socketio.server.disconnect(sock_sid)
            except Exception:
                pass
        return delivered


    def _resolve_idle_logout_seconds() -> float | None:
        idle_hours = settings.get("idle_logout_hours", 8)
        try:
            idle_hours = float(idle_hours) if idle_hours is not None else 8.0
        except Exception:
            idle_hours = 8.0
        return (idle_hours * 3600.0) if idle_hours and idle_hours > 0 else None


    def _session_failure_response(error: str, *, redirect_on_failure: bool = False):
        reason = str(error or "session_revoked").strip() or "session_revoked"
        if redirect_on_failure:
            resp = make_response(redirect(f"/login?reason={urllib.parse.quote(reason)}"))
        else:
            resp = jsonify({"ok": False, "error": reason})
        try:
            unset_jwt_cookies(resp)
        except Exception:
            pass
        session.clear()
        if not redirect_on_failure:
            resp.headers["Cache-Control"] = "no-store, max-age=0"
        if redirect_on_failure:
            return resp
        return resp, 401


    def _require_active_session(
        username: str | None = None,
        *,
        redirect_on_failure: bool = False,
        touch_seen: bool = False,
        touch_activity: bool = False,
    ):
        claims = get_jwt() or {}
        sid = str(claims.get("sid") or "").strip()
        username = str(username or get_jwt_identity() or "").strip().lower()
        if not sid:
            return None, None, _session_failure_response("no_session", redirect_on_failure=redirect_on_failure)

        try:
            state = get_auth_session_state(sid)
        except Exception:
            return None, None, _session_failure_response("session_check_failed", redirect_on_failure=redirect_on_failure)

        if state is None or state.get("revoked_at") is not None:
            if username:
                try:
                    _force_logout_live_sessions(
                        username,
                        "Your session was revoked. Please sign in again.",
                        auth_session_ids={sid},
                        action="session_revoked",
                        code="session_revoked",
                    )
                except Exception:
                    pass
            return None, None, _session_failure_response("session_revoked", redirect_on_failure=redirect_on_failure)

        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        if ip_banned:
            try:
                revoke_auth_session(sid, reason="ip_banned")
            except Exception:
                pass
            if username:
                try:
                    _force_logout_live_sessions(
                        username,
                        ip_ban_message,
                        auth_session_ids={sid},
                        action="ip_banned",
                        code="ip_banned",
                    )
                except Exception:
                    pass
            try:
                log_audit_event("system", "session_ip_ban_blocked", banned_ip, f"user={username}; sid={sid}")
            except Exception:
                pass
            return None, None, _session_failure_response("ip_banned", redirect_on_failure=redirect_on_failure)

        allowed, account_status, status_code, status_reason = _account_status_auth_allowed(username)
        if not allowed:
            try:
                revoke_auth_session(sid, reason=status_code or "account_not_active")
            except Exception:
                pass
            if username:
                try:
                    _force_logout_live_sessions(
                        username,
                        status_reason,
                        auth_session_ids={sid},
                        action=status_code or "account_not_active",
                        code=status_code or "account_not_active",
                    )
                except Exception:
                    pass
            return None, None, _session_failure_response(status_code or "account_not_active", redirect_on_failure=redirect_on_failure)

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
                    if username:
                        try:
                            _force_logout_live_sessions(
                                username,
                                "You were signed out due to inactivity.",
                                auth_session_ids={sid},
                                action="idle_timeout",
                                code="idle_timeout",
                            )
                        except Exception:
                            pass
                    return None, None, _session_failure_response("idle_timeout", redirect_on_failure=redirect_on_failure)

        try:
            if touch_activity:
                touch_auth_session_activity(sid)
            elif touch_seen:
                touch_auth_session(sid)
        except Exception:
            return None, None, _session_failure_response("session_touch_failed", redirect_on_failure=redirect_on_failure)

        session["auth_session_id"] = sid
        return sid, state, None

    # NOTE: Password reset tokens are stored server-side in PostgreSQL.

    @app.route("/chat")
    def chat_page():
        """Render the chat UI.

        NOTE: We intentionally do **not** protect this HTML route with
        @jwt_required(), because access tokens are short-lived. If the user
        refreshes their browser after the access token expires, we still want to
        return the page so the client can call /token/refresh using the refresh
        token cookie.
        """

        # Auth gating:
        # - Prefer access cookie when present (even if expired).
        # - If access cookie is missing/corrupt, serve a tiny bootstrap page that
        #   attempts /token/refresh and then reloads /chat.
        #
        # NOTE: The refresh token cookie is path-restricted to /token/refresh, so
        # /chat cannot see it. However the CSRF cookie (csrf_refresh_token) is
        # available on '/', so we use it as a signal that a refresh cookie likely
        # exists.
        # IP-ban hard gate: do not render the chat shell for a banned IP.
        # Sockets and /token/refresh also enforce this, but blocking here avoids
        # bootstrapping the full app for a connection that is already sanctioned.
        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        if ip_banned:
            return _chat_ip_ban_redirect(banned_ip, ip_ban_message)

        access_cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "echochat_access")
        access_token = request.cookies.get(access_cookie_name)
        refresh_csrf_cookie = request.cookies.get("csrf_refresh_token")
        server_name = str(settings.get("server_name") or DEFAULT_SERVER_NAME).strip() or DEFAULT_SERVER_NAME
        client_device = detect_mobile_client(request.headers.get("User-Agent"), request.headers.get("Sec-CH-UA-Mobile"))

        def _chat_bootstrap_response():
            return make_response(
                render_template(
                    "chat_bootstrap.html",
                    app_version=APP_VERSION,
                    server_name=server_name,
                )
            )

        if not access_token:
            if refresh_csrf_cookie:
                return _chat_bootstrap_response()
            return redirect("/login")

        try:
            access_decoded = decode_token(access_token, allow_expired=True)
            username = access_decoded.get("sub")
            sid = access_decoded.get("sid")
            exp_ts = access_decoded.get("exp")
        except Exception:
            if refresh_csrf_cookie:
                return _chat_bootstrap_response()
            return redirect("/login")

        try:
            access_is_expired = bool(exp_ts) and datetime.now(timezone.utc).timestamp() >= float(exp_ts)
        except Exception:
            access_is_expired = False

        # If the short-lived access token is already expired, do not render the full
        # chat app yet. Serve the tiny bootstrap page first so the browser refreshes
        # auth before the chat runtime, polling, room-catalog fetches, and activity pings start.
        if access_is_expired:
            if refresh_csrf_cookie:
                return _chat_bootstrap_response()
            return redirect("/login")

        if not username:
            if refresh_csrf_cookie:
                return _chat_bootstrap_response()
            return redirect("/login")

        # Require a Session Truth sid. If missing (legacy/partial cookies), try
        # refresh-based recovery.
        if not sid:
            if refresh_csrf_cookie:
                return _chat_bootstrap_response()
            return redirect("/login")

        # Session Truth: sid must still be active.
        try:
            if not is_auth_session_active(sid, username=username):
                if refresh_csrf_cookie:
                    return _chat_bootstrap_response()
                return redirect("/login")
        except Exception:
            if refresh_csrf_cookie:
                return _chat_bootstrap_response()
            return redirect("/login")

        allowed, account_status, status_code, status_reason = _account_status_auth_allowed(username)
        if not allowed:
            try:
                revoke_auth_session(sid, reason=status_code or "account_not_active")
            except Exception:
                pass
            if refresh_csrf_cookie:
                return _chat_bootstrap_response()
            return redirect(f"/login?reason={urllib.parse.quote(status_code or 'account_not_active')}")

        # Look up user info + encrypted key in PostgreSQL
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT encrypted_private_key FROM users WHERE LOWER(username) = LOWER(%s);",
                    (username,),
                )
                row = cur.fetchone()
        except Exception as e:
            logging.error("DB error in chat_page: %s", e)
            return redirect("/login")

        if not row:
            return redirect("/login")
        encrypted_priv = row[0] if (row and row[0]) else None

        # Admin UI injection must follow the same live source-of-truth as backend
        # guards. Do not trust session["is_admin"] or legacy DB flags here
        # because they can remain stale after a role change until the browser
        # refreshes. Only users with live RBAC admin permissions should receive
        # the injected admin panel or template-level admin state.
        rbac_admin = False
        try:
            rbac_admin = bool(
                check_user_permission(username, "admin:basic")
            )
        except Exception:
            rbac_admin = False

        is_admin = bool(rbac_admin)
        try:
            session["is_admin"] = bool(is_admin)
            if not is_admin:
                session["is_admin"] = False
        except Exception:
            pass

        # For client-side UX (e.g., room policy banners), expose the user's effective RBAC permissions.
        try:
            user_perms = sorted(get_user_permissions(username))
        except Exception:
            user_perms = []

        # Fetch all rooms, ordered case‐insensitive
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, member_count FROM chat_rooms ORDER BY LOWER(name);"
                )
                rooms_data = cur.fetchall()
        except Exception as e:
            logging.error("DB error fetching rooms: %s", e)
            rooms_data = []

        
        # Overlay live room counts from active Socket.IO sessions (prevents stale DB drift)
        live_counts = {}
        try:
            from realtime.state import live_room_counts
            live_counts = dict(live_room_counts())
        except Exception:
            live_counts = {}
        rooms = [
            {"name": r[0], "member_count": int(live_counts.get(r[0], 0) or 0)}
            for r in rooms_data
        ]

        # Client-side feature/config flags (small + non-secret).
        # The goal is to keep client limits in sync with server settings.
        # Idle logout window (seconds). 0 disables.
        idle_hours = settings.get("idle_logout_hours", 8)
        try:
            idle_hours = float(idle_hours) if idle_hours is not None else 8.0
        except Exception:
            idle_hours = 8.0
        idle_logout_seconds = int(idle_hours * 3600) if idle_hours and idle_hours > 0 else 0

        def _client_text_animation(key, default):
            mode = str(settings.get(key) or default or "none").strip().lower()
            return mode if mode in {"none", "fade", "rise", "slide", "scale"} else str(default or "none")

        def _client_sound_pack(default="echo_modern_generated"):
            # Server sends only a safe ID; online sound packs may define custom IDs.
            return normalize_sound_pack_identifier(settings.get("sound_pack_default"), default)

        def _client_sound_theme(key, default="soft_chime"):
            value = normalize_sound_pack_identifier(settings.get(key), default or "soft_chime")
            if value in {"classic_beep", "beep", "computer_beep"}:
                value = "soft_chime"
            return value

        def _client_sound_event_map():
            defaults = {
                "dm": "mellow_pluck",
                "room_message": "soft_chime",
                "group_message": "sonar_ping",
                "room_invite": "doorbell_duo",
                "group_invite": "doorbell_duo",
                "friend_request": "success_twinkle",
                "room_join": "page_flip",
                "file": "digital_drop",
                "error": "warning_pulse",
            }
            return {event: _client_sound_theme(f"sound_event_{event}", default) for event, default in defaults.items()}

        def _client_bool_setting(key, default=False):
            raw = settings.get(key, default)
            if isinstance(raw, bool):
                return raw
            if raw is None:
                return bool(default)
            if isinstance(raw, (int, float)):
                return bool(raw)
            text = str(raw).strip().lower()
            if text in {"1", "true", "yes", "on", "enabled"}:
                return True
            if text in {"0", "false", "no", "off", "disabled", ""}:
                return False
            return bool(default)

        def _client_int_setting(key, default, *, minimum=0, maximum=None):
            try:
                value = int(settings.get(key, default))
            except Exception:
                value = int(default)
            value = max(int(minimum), value)
            if maximum is not None:
                value = min(int(maximum), value)
            return value

        def _client_torrent_fallback_trackers():
            defaults = [
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://open.stealth.si:80/announce",
                "udp://tracker.torrent.eu.org:451/announce",
                "udp://tracker.moeking.me:6969/announce",
                "https://tracker2.ctix.cn:443/announce",
                "https://tracker.tamersunion.org:443/announce",
            ]
            raw = settings.get("torrent_public_fallback_trackers")
            if isinstance(raw, str):
                try:
                    import json as _json
                    raw = _json.loads(raw)
                except Exception:
                    raw = [x.strip() for x in raw.splitlines() if x.strip()]
            candidates = raw if isinstance(raw, list) else defaults
            out = []
            for item in candidates:
                text = str(item or "").strip()
                if not text or text in out:
                    continue
                parsed = urllib.parse.urlparse(text)
                if parsed.scheme in {"udp", "http", "https"} and not parsed.username and not parsed.password:
                    out.append(text)
                if len(out) >= 12:
                    break
            return out or defaults

        client_cfg = {
            "server_name": str(settings.get("server_name") or DEFAULT_SERVER_NAME).strip() or DEFAULT_SERVER_NAME,
            "chat_text_animation": _client_text_animation("chat_text_animation", "none"),
            "dm_text_animation": _client_text_animation("dm_text_animation", "rise"),
            "group_text_animation": _client_text_animation("group_text_animation", "rise"),
            "room_show_sender_every_message": bool(settings.get("room_show_sender_every_message", False)),
            "dm_show_sender_every_message": bool(settings.get("dm_show_sender_every_message", False)),
            "group_show_sender_every_message": bool(settings.get("group_show_sender_every_message", False)),
            "sound_notifications_default": bool(settings.get("sound_notifications_default", True)),
            "sound_pack_default": _client_sound_pack("echo_modern_generated"),
            "sound_pack_external_urls": sanitize_sound_pack_external_urls(settings.get("sound_pack_external_urls")),
            "sound_pack_load_local_builtins": sound_pack_local_builtins_enabled(settings.get("sound_pack_load_local_builtins", True), default=True),
            "sound_theme_default": _client_sound_theme("sound_theme_default", "soft_chime"),
            "default_sound_theme": _client_sound_theme("sound_theme_default", "soft_chime"),
            "sound_event_themes": _client_sound_event_map(),
            "idle_logout_seconds": idle_logout_seconds,
            "presence_idle_minutes": max(0, int(settings.get("presence_idle_minutes", 15) or 0)),
            "presence_offline_minutes": max(0, int(settings.get("presence_offline_minutes", 0) or 0)),
            "max_dm_file_bytes": _client_int_setting("max_dm_file_bytes", 10 * 1024 * 1024, minimum=1, maximum=512 * 1024 * 1024),
            "max_group_file_bytes": _client_int_setting("max_group_upload_bytes", settings.get("max_group_file_bytes", settings.get("max_dm_file_bytes", 10 * 1024 * 1024)), minimum=1, maximum=1024 * 1024 * 1024),
            "max_group_upload_bytes": _client_int_setting("max_group_upload_bytes", settings.get("max_group_file_bytes", settings.get("max_dm_file_bytes", 10 * 1024 * 1024)), minimum=1, maximum=1024 * 1024 * 1024),
            "max_torrent_upload_bytes": _client_int_setting("max_torrent_upload_bytes", 1_000_000, minimum=1024, maximum=5_000_000),
            "allow_plaintext_dm_fallback": bool(settings.get("allow_plaintext_dm_fallback", False)),
            "require_dm_e2ee": bool(settings.get("require_dm_e2ee", True)),
            "require_group_e2ee": bool(settings.get("require_group_e2ee", True)),
            "require_private_room_e2ee": bool(settings.get("require_private_room_e2ee", True)),
            "require_room_e2ee": bool(settings.get("require_room_e2ee", False)),
            "max_emoticons_per_message": _client_int_setting("max_emoticons_per_message", 15, minimum=0, maximum=100),
            "emoticons_boot_preload_enabled": _client_bool_setting("emoticons_boot_preload_enabled", True),
            "emoticons_boot_preload_limit": _client_int_setting("emoticons_boot_preload_limit", 180, minimum=0, maximum=240),
            "emoticons_boot_preload_concurrency": _client_int_setting("emoticons_boot_preload_concurrency", 4, minimum=1, maximum=8),
            "enable_room_typing_indicators": _client_bool_setting("enable_room_typing_indicators", False),
            "enable_dm_typing_indicators": _client_bool_setting("enable_dm_typing_indicators", True),
            "enable_group_typing_indicators": _client_bool_setting("enable_group_typing_indicators", True),
            "disable_file_transfer_globally": _client_bool_setting("disable_file_transfer_globally", False),
            "disable_dm_files_globally": _client_bool_setting("disable_dm_files_globally", False) or _client_bool_setting("disable_file_transfer_globally", False),
            "disable_group_files_globally": _client_bool_setting("disable_group_files_globally", False) or _client_bool_setting("disable_file_transfer_globally", False),
            "p2p_file_enabled": _client_bool_setting("p2p_file_enabled", True) and not _client_bool_setting("disable_file_transfer_globally", False),
            "torrent_upload_enabled": _client_bool_setting("torrent_upload_enabled", True),
            "torrent_scrape_enabled": _client_bool_setting("torrent_scrape_enabled", False),
            "torrent_public_fallback_scrape_enabled": _client_bool_setting("torrent_public_fallback_scrape_enabled", True),
            "torrent_public_fallback_trackers": _client_torrent_fallback_trackers(),
            "torrent_dht_scrape_enabled": _client_bool_setting("torrent_dht_scrape_enabled", True),
            "torrent_scrape_disabled_reason": "Server setting torrent_scrape_enabled=false. Admin can enable tracker scraping under Admin Panel → Limits and uploads." if not _client_bool_setting("torrent_scrape_enabled", False) else "",
            "p2p_chunk_bytes": int(settings.get("p2p_file_chunk_bytes", 64 * 1024)),
            "p2p_handshake_timeout_ms": int(settings.get("p2p_file_handshake_timeout_ms", 7000)),
            "p2p_transfer_timeout_ms": int(settings.get("p2p_file_transfer_timeout_ms", 60000)),
            "p2p_ice_servers": p2p_ice_servers(settings),
            "webrtc_ice_summary": ice_server_summary(settings),

            # Voice chat (WebRTC audio)
            # Uses the same ICE server list as P2P file transfers by default.
            "voice_enabled": echo_voice_bool(settings, "voice_enabled", True),
            # Missing/blank defaults to 100; an explicit 0 still means unlimited.
            "voice_max_room_peers": echo_voice_room_limit(settings),
            **echo_voice_client_config(settings),
            "voice_ice_servers": voice_ice_servers(settings),

            # Profile media upload hints. These mirror server-side enforcement for UX only.
            "allow_svg_avatars": bool(settings.get("allow_svg_avatars", False)),
            "max_profile_avatar_bytes": int(settings.get("max_profile_avatar_bytes") or (5 * 1024 * 1024)),
            "max_profile_banner_bytes": int(settings.get("max_profile_banner_bytes") or (8 * 1024 * 1024)),
            "max_profile_post_image_bytes": int(settings.get("max_profile_post_image_bytes") or (8 * 1024 * 1024)),

            # Auth/session
            "idle_logout_seconds": idle_logout_seconds,

            # Server-owned A/V mode decision. The browser uses Echo's built-in
            # WebRTC media engine for room voice/webcam controls.
            **client_av_config(settings),

            # Mobile/front-end shell hints. These are not trusted security inputs;
            # the browser re-checks viewport/pointer state before enabling phone layout.
            "device_profile": client_device.get("profile", "desktop"),
            "mobile_device_hint": bool(client_device.get("is_mobile")),
            "phone_device_hint": bool(client_device.get("is_phone")),
            "tablet_device_hint": bool(client_device.get("is_tablet")),

            # Socket transport preference. When true, the browser will prefer WebSockets
            # (far fewer requests than long-polling).
            "ws_enabled": bool(app.config.get("ECHOCHAT_WS_ENABLED", False)),
            "socketio_transports": list(app.config.get("ECHOCHAT_SOCKETIO_TRANSPORTS", ["websocket", "polling"])),
            "socketio_websocket_only": bool(app.config.get("ECHOCHAT_SOCKETIO_WEBSOCKET_ONLY", False)),
        }

        chat_script_urls = get_chat_script_urls()
        sound_pack_script_urls = [sound_pack_script_src(url, APP_VERSION) for url in get_sound_pack_script_urls(settings)]
        html = render_template(
            "chat.html",
            username=username,
            is_admin=is_admin,
            rooms=rooms,
            encrypted_private_key=encrypted_priv,
            csrf_token=session.get("csrf_token"),
            client_cfg=client_cfg,
            client_device=client_device,
            user_perms=user_perms,
            app_version=APP_VERSION,
            chat_script_parts=get_chat_script_parts(),
            chat_script_urls=chat_script_urls,
            sound_pack_script_urls=sound_pack_script_urls,

        )

        # Admin UI is injected server-side to keep it out of static end-user assets.
        if is_admin:
            html = inject_admin_panel(
                html,
                csp_nonce=getattr(g, "echochat_csp_nonce", None),
            )

        resp = make_response(html)
        return resp

    @app.route("/token/refresh", methods=["POST"])
    @_limit(settings.get("rate_limit_refresh") or "30 per minute")
    @jwt_required(refresh=True)
    def token_refresh():
        """Rotate refresh token + mint a new access token (session-aware).

        Security:
          - Refresh tokens are single-use (rotated on every refresh)
          - Reuse of an already-rotated refresh token is treated as replay
          - Refresh tokens are bound to an auth session (device/session tracking)
        """

        username = get_jwt_identity()
        claims = get_jwt()
        old_refresh_jti = claims.get("jti")

        effective_status = get_effective_account_status(username)
        if not account_status_allows_auth(effective_status):
            status_code = account_status_error_code(effective_status)
            status_reason = account_status_reason(effective_status)
            try:
                revoke_all_sessions_for_user(username, reason=status_code)
            except Exception:
                pass
            try:
                _force_logout_live_sessions(
                    username,
                    status_reason,
                    action=status_code,
                    code=status_code,
                )
            except Exception:
                pass
            return _refresh_json_response(
                {"ok": False, "error": status_code, "account_status": effective_status, "message": status_reason},
                403,
                clear_cookies=True,
            )

        ua = request.headers.get("User-Agent")
        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        ip = banned_ip or get_request_ip() or None
        if ip_banned:
            try:
                revoke_all_sessions_for_user(username, reason="ip_banned")
            except Exception:
                pass
            try:
                _force_logout_live_sessions(
                    username,
                    ip_ban_message,
                    action="ip_banned",
                    code="ip_banned",
                )
            except Exception:
                pass
            try:
                log_audit_event("system", "refresh_ip_ban_blocked", ip, f"user={username}")
            except Exception:
                pass
            return _refresh_json_response({"ok": False, "error": "ip_banned", "message": ip_ban_message}, 403, clear_cookies=True)

        # Determine refresh token state (handles replay vs race conditions).
        meta = get_refresh_token_meta(username, old_refresh_jti)
        if not meta:
            return _refresh_json_response({"ok": False, "error": "refresh_unknown"}, 401, clear_cookies=True)

        revoked_at, replaced_by, expires_at, last_used_at, meta_sid = meta
        if revoked_at is not None:
            return _refresh_json_response({"ok": False, "error": "refresh_revoked"}, 401, clear_cookies=True)

        # Session Truth: sid must be consistent between JWT claim and DB row.
        sid_claim = claims.get("sid")
        if sid_claim and meta_sid and sid_claim != meta_sid:
            try:
                revoke_all_sessions_for_user(username, reason="sid_mismatch")
            except Exception:
                pass
            try:
                _force_logout_live_sessions(
                    username,
                    "Your session was invalidated. Please sign in again.",
                    action="sid_mismatch",
                    code="sid_mismatch",
                )
            except Exception:
                pass
            return _refresh_json_response({"ok": False, "error": "sid_mismatch"}, 401, clear_cookies=True)

        sid = sid_claim or meta_sid

        # Legacy refresh tokens (pre-session tracking): create + bind a session on first refresh.
        if not sid:
            try:
                sid = create_auth_session(username=username, user_agent=ua, ip_address=ip)
                attach_session_to_token(username=username, jti=old_refresh_jti, session_id=sid)
            except Exception:
                return _refresh_json_response({"ok": False, "error": "session_create_failed"}, 401, clear_cookies=True)

        if sid:
            session["auth_session_id"] = sid

        if sid:
            ok_refresh_rl, refresh_retry = _enforce_named_rate_limit(
                'refresh:sid',
                f'{sid}:{ip or "unknown"}',
                settings.get('rate_limit_refresh_session') or '20@60',
                default_limit=20,
                default_window=60,
            )
            if not ok_refresh_rl:
                return _auth_limit_response('Refresh rate limited', retry_after=refresh_retry)

        # Session must be active (and enforce idle logout)
        try:
            max_idle_seconds = _resolve_idle_logout_seconds()

            state = get_auth_session_state(sid)
            if state is None or state.get("revoked_at") is not None:
                try:
                    _force_logout_live_sessions(
                        username,
                        "Your session was revoked. Please sign in again.",
                        auth_session_ids={sid},
                        action="session_revoked",
                        code="session_revoked",
                    )
                except Exception:
                    pass
                return _refresh_json_response({"ok": False, "error": "session_revoked"}, 401, clear_cookies=True)

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
                        try:
                            _force_logout_live_sessions(
                                username,
                                "You were signed out due to inactivity.",
                                auth_session_ids={sid},
                                action="idle_timeout",
                                code="idle_timeout",
                            )
                        except Exception:
                            pass
                        return _refresh_json_response({"ok": False, "error": "idle_timeout"}, 401, clear_cookies=True)

            # Touch *seen* time (does not extend idle window)
            touch_auth_session(sid)
        except Exception:
            return _refresh_json_response({"ok": False, "error": "session_check_failed"}, 401, clear_cookies=True)

        # If the refresh token was already rotated, it might be:
        #  - a legitimate race (two refresh attempts close together)
        #  - a stolen-token replay
        if replaced_by is not None:
            now = datetime.now(timezone.utc)
            grace = _refresh_rotation_grace_seconds()
            if last_used_at and (now - last_used_at).total_seconds() <= grace:
                # Graceful response: don't modify cookies; client should retry.
                return _refresh_json_response({"ok": False, "error": "stale_refresh"}, 409)

            # Outside grace window -> treat as replay and hard-kill sessions.
            try:
                revoke_all_sessions_for_user(username, reason="refresh_token_reuse")
            except Exception:
                pass
            try:
                _force_logout_live_sessions(
                    username,
                    "Your session was invalidated after refresh token reuse was detected. Please sign in again.",
                    action="refresh_token_reuse",
                    code="refresh_token_reuse",
                )
            except Exception:
                pass
            return _refresh_json_response({"ok": False, "error": "refresh_token_reuse"}, 401, clear_cookies=True)

        # Mint new tokens (bind to the same session)
        new_access = create_access_token(identity=username, additional_claims={"sid": sid})
        new_refresh = create_refresh_token(identity=username, additional_claims={"sid": sid})

        # Extract JTIs/exp for storage
        access_decoded = decode_token(new_access, allow_expired=False)
        refresh_decoded = decode_token(new_refresh, allow_expired=False)
        new_access_jti = access_decoded.get("jti")
        new_refresh_jti = refresh_decoded.get("jti")

        # Convert exp (unix seconds) -> aware UTC timestamp
        access_exp = access_decoded.get("exp")
        refresh_exp = refresh_decoded.get("exp")
        access_expires_at = (
            datetime.fromtimestamp(access_exp, tz=timezone.utc) if isinstance(access_exp, (int, float)) else None
        )
        refresh_expires_at = (
            datetime.fromtimestamp(refresh_exp, tz=timezone.utc) if isinstance(refresh_exp, (int, float)) else None
        )

        # Atomic rotation: replace old refresh, insert new refresh, and persist new access token together.
        try:
            rotated = rotate_refresh_and_store_access_token(
                username=username,
                old_jti=old_refresh_jti,
                new_refresh_jti=new_refresh_jti,
                new_refresh_expires_at=refresh_expires_at,
                new_access_jti=new_access_jti,
                new_access_expires_at=access_expires_at,
                session_id=sid,
                user_agent=ua,
                ip_address=ip,
            )
        except Exception:
            logging.exception("Refresh rotation persistence failed for %s", username)
            return _refresh_json_response({"ok": False, "error": "refresh_persist_failed"}, 401, clear_cookies=True)
        if not rotated:
            # Likely race: another refresh already rotated this token.
            # Do NOT unset cookies (a parallel successful refresh might have
            # already set a new refresh cookie).
            return _refresh_json_response({"ok": False, "error": "stale_refresh"}, 409)

        resp = _refresh_json_response({"ok": True})
        return _set_auth_cookies_for_response(resp, new_access, new_refresh)


    @app.route("/api/activity", methods=["POST"])
    @_limit(settings.get("rate_limit_activity") or "120 per minute")
    @jwt_required()
    def api_activity():
        """Client-side activity ping used for idle logout."""
        user = get_jwt_identity()
        sid, _state, rejection = _require_active_session(user, touch_activity=True)
        if rejection:
            return rejection
        return _auth_json_response({"ok": True})

    @app.route("/login", methods=["GET", "POST"])
    @_limit(settings.get("rate_limit_login") or "10 per minute", methods=["POST"])
    def login():
        if request.method == "GET":
            if request.args.get("cancel_2fa"):
                _clear_pending_login_2fa()
                return _render_login(error=None)
            login_challenge = _load_server_challenge("login_2fa", session.get("pending_2fa_challenge_id")) or {}
            pending_username = str(login_challenge.get("username") or session.get("pending_2fa_username") or "").strip().lower()
            pending_phone = _normalize_phone_e164(login_challenge.get("phone") or "")
            pending_started_at = float(login_challenge.get("started_at") or 0.0)
            max_age = int(effective_twilio_settings(settings).get("two_factor_login_timeout_seconds") or 600)
            if pending_username and pending_phone and pending_started_at:
                if (datetime.now(timezone.utc).timestamp() - pending_started_at) <= max_age:
                    return _render_login(
                        error=None,
                        two_factor_pending=True,
                        two_factor_phone_mask=_mask_phone(pending_phone),
                        prefill_username=pending_username,
                    )
                _clear_pending_login_2fa()
            return _render_login(error=None)

        submitted_csrf = request.form.get("csrf_token")
        try:
            validate_csrf(submitted_csrf)
        except ValidationError:
            if not _login_csrf_fallback_valid(submitted_csrf):
                return _stale_login_form_response()

        stage = (request.form.get("stage") or "password").strip().lower()

        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        if ip_banned:
            _clear_pending_login_2fa()
            try:
                log_audit_event("anon", "login_ip_ban_blocked", banned_ip, f"stage={stage}")
            except Exception:
                pass
            return _render_login(error=ip_ban_message)

        if stage == "2fa_cancel":
            _clear_pending_login_2fa()
            return _render_login(error=None)

        if stage in {"2fa_sms", "2fa_resend"}:
            login_challenge = _load_server_challenge("login_2fa", session.get("pending_2fa_challenge_id")) or {}
            username = str(login_challenge.get("username") or session.get("pending_2fa_username") or "").strip().lower()
            phone = _normalize_phone_e164(login_challenge.get("phone") or "")
            started_at = float(login_challenge.get("started_at") or 0.0)
            challenge_auth_version = login_challenge.get("auth_version")
            max_age = int(effective_twilio_settings(settings).get("two_factor_login_timeout_seconds") or 600)
            if not username or not phone or not started_at:
                _clear_pending_login_2fa()
                return _render_login(error="Your 2FA login step expired. Please sign in again.")
            if (datetime.now(timezone.utc).timestamp() - started_at) > max_age:
                _clear_pending_login_2fa()
                return _render_login(error="Your 2FA code expired. Please sign in again.")

            ok_2fa_user, retry_2fa_user = _enforce_named_rate_limit(
                'login:2fa:user',
                username,
                settings.get('rate_limit_login_2fa_check') or '10@600',
                default_limit=10,
                default_window=600,
            )
            if not ok_2fa_user:
                return _auth_limit_response(
                    'Too many 2FA code attempts. Try again later.',
                    template='login.html',
                    error='Too many 2FA code attempts. Try again later.',
                    retry_after=retry_2fa_user,
                    two_factor_pending=True,
                    two_factor_phone_mask=_mask_phone(phone),
                    prefill_username=username,
                )

            if stage == "2fa_resend":
                ok_2fa_resend, retry_2fa_resend = _enforce_named_rate_limit(
                    "login:2fa:resend",
                    username,
                    settings.get("rate_limit_login_2fa_resend") or "3@300",
                    default_limit=3,
                    default_window=300,
                )
                if not ok_2fa_resend:
                    return _auth_limit_response(
                        "Too many 2FA resend requests. Try again later.",
                        template="login.html",
                        error="Too many 2FA resend requests. Try again later.",
                        retry_after=retry_2fa_resend,
                        two_factor_pending=True,
                        two_factor_phone_mask=_mask_phone(phone),
                        prefill_username=username,
                    )
                ok_send, msg = _send_sms_2fa_code(phone)
                if ok_send:
                    cid = _save_server_challenge("login_2fa", username, phone)
                    session["pending_2fa_challenge_id"] = cid
                    session["pending_2fa_username"] = username
                    return _render_login(
                        error=None,
                        two_factor_pending=True,
                        two_factor_phone_mask=_mask_phone(phone),
                        two_factor_hint="We sent a fresh verification code.",
                        prefill_username=username,
                    )
                return _render_login(
                    error=msg,
                    two_factor_pending=True,
                    two_factor_phone_mask=_mask_phone(phone),
                    prefill_username=username,
                )

            code = (request.form.get("two_factor_code") or "").strip()
            ok_check, msg = _check_sms_2fa_code(phone, code)
            if not ok_check:
                return _render_login(
                    error=msg,
                    two_factor_pending=True,
                    two_factor_phone_mask=_mask_phone(phone),
                    prefill_username=username,
                )
            if not _login_2fa_account_still_matches(username, phone, challenge_auth_version):
                _clear_pending_login_2fa()
                return _render_login(error="Your 2FA settings changed. Please sign in again.")
            _clear_pending_login_2fa()
            return _finalize_login_success(username)

        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not password:
            return _render_login(error="Username and password required")

        ok_login_user, retry_login_user = _enforce_named_rate_limit(
            'login:user',
            username,
            settings.get('rate_limit_login_username') or '20@300',
            default_limit=20,
            default_window=300,
        )
        if not ok_login_user:
            return _auth_limit_response('Too many login attempts for that username. Try again later.', template='login.html', error='Too many login attempts for that username. Try again later.', retry_after=retry_login_user)

        client_ip = get_request_ip() or 'unknown'
        ok_login_ip_user, retry_login_ip_user = _enforce_named_rate_limit(
            'login:ip_user',
            f'{client_ip}:{username}',
            settings.get('rate_limit_login_ip_username') or '10@300',
            default_limit=10,
            default_window=300,
        )
        if not ok_login_ip_user:
            return _auth_limit_response('Too many login attempts from this connection. Try again later.', template='login.html', error='Too many login attempts from this connection. Try again later.', retry_after=retry_login_ip_user)

        canonical_username = username
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name
                      FROM information_schema.columns
                     WHERE table_schema = 'public'
                       AND table_name = 'users'
                       AND column_name = ANY(%s);
                    """,
                    (["two_factor_enabled", "phone"],),
                )
                present_cols = {str(r[0]) for r in (cur.fetchall() or [])}
                two_factor_expr = "two_factor_enabled" if "two_factor_enabled" in present_cols else "FALSE AS two_factor_enabled"
                phone_expr = "phone" if "phone" in present_cols else "NULL::TEXT AS phone"
                cur.execute(
                    f"SELECT username, password, {two_factor_expr}, {phone_expr}, status FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1;",
                    (username,),
                )
                row = cur.fetchone()
        except Exception as e:
            logging.error("DB error in login lookup: %s", e)
            row = None

        ok, upgraded_hash = (False, None)
        if row:
            canonical_username = str(row[0] or username)
            ok, upgraded_hash = verify_password_and_upgrade(password, row[1])
        else:
            _dummy_password_verify(password)

        if not ok:
            _clear_pending_login_2fa()
            return _render_login(error="Invalid username or password")

        allowed, account_status, status_code, status_reason = _account_status_auth_allowed(canonical_username)
        if not allowed:
            _clear_pending_login_2fa()
            logging.info("Login blocked for account status user=%s status=%s code=%s", canonical_username, account_status, status_code)
            msg = (status_reason or "This account cannot sign in right now.") if is_localish_request() else "Invalid username or password"
            return _render_login(error=msg)

        if upgraded_hash:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET password = %s WHERE LOWER(username) = LOWER(%s);",
                        (upgraded_hash, canonical_username),
                    )
                conn.commit()
            except Exception as e:
                logging.warning("Could not upgrade password hash for %s: %s", canonical_username, e)

        two_factor_enabled = bool(row[2])
        phone = _normalize_phone_e164(decrypt_sensitive_field(row[3] or "", settings, field_name="users.phone"))

        try:
            ensure_user_has_keys(conn, canonical_username, password)
        except Exception as e:
            logging.error("Failed to ensure user keys for %s: %s", canonical_username, e)

        if two_factor_enabled:
            if not _twilio_verify_ready():
                return _render_login(error="2FA is enabled on this account, but SMS 2FA is not configured on the server.")
            if not phone:
                return _render_login(error="2FA is enabled on this account, but no valid phone number is saved for it.")
            ok_send, msg = _send_sms_2fa_code(phone)
            if not ok_send:
                return _render_login(error=msg)
            cid = _save_server_challenge("login_2fa", canonical_username, phone)
            session["pending_2fa_challenge_id"] = cid
            session["pending_2fa_username"] = canonical_username
            return _render_login(
                error=None,
                two_factor_pending=True,
                two_factor_phone_mask=_mask_phone(phone),
                two_factor_hint="We sent a verification code to your phone.",
                prefill_username=canonical_username,
            )

        _clear_pending_login_2fa()
        return _finalize_login_success(canonical_username)

    def _logout_csrf_token_for_form() -> str:
        """Return the readable JWT CSRF cookie that can protect a manual logout POST."""
        for cookie_name in (
            app.config.get("JWT_ACCESS_CSRF_COOKIE_NAME", "csrf_access_token"),
            app.config.get("JWT_REFRESH_CSRF_COOKIE_NAME", "csrf_refresh_token"),
            "csrf_access_token",
            "csrf_refresh_token",
        ):
            val = str(request.cookies.get(cookie_name) or "").strip()
            if val:
                return val
        return ""

    def _logout_wants_html() -> bool:
        try:
            if request.args.get("redirect") == "1":
                return True
            best = request.accept_mimetypes.best_match(["text/html", "application/json"])
            return best == "text/html" and request.accept_mimetypes["text/html"] >= request.accept_mimetypes["application/json"]
        except Exception:
            return False

    def _render_logout_confirm(error: str | None = None, *, status: int = 200):
        """Render a safe GET logout page that performs the real logout with POST."""
        token = _logout_csrf_token_for_form()
        server_name = str(settings.get("server_name") or DEFAULT_SERVER_NAME).strip() or DEFAULT_SERVER_NAME
        resp = make_response(
            render_template(
                "logout.html",
                app_version=APP_VERSION,
                server_name=server_name,
                error=error,
                logout_csrf_token=token,
                csrf_required=bool(settings.get("require_logout_csrf", True)),
            ),
            status,
        )
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    def _clear_auth_cookies_and_session(resp):
        try:
            unset_jwt_cookies(resp)
        except Exception:
            pass
        try:
            resp.delete_cookie(LOGIN_CSRF_FALLBACK_COOKIE, path="/login")
        except Exception:
            pass
        try:
            session.clear()
        except Exception:
            pass
        return resp

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        """Revoke the current access/refresh tokens (if present) and clear cookies."""

        if request.method == "GET" and not bool(settings.get("enable_legacy_get_logout", False)):
            return _render_logout_confirm()

        if request.method == "POST" and bool(settings.get("require_logout_csrf", True)):
            if not request_has_valid_double_submit_csrf(request):
                if _logout_wants_html():
                    return _render_logout_confirm(
                        "Could not verify that logout request. Please refresh this page and try again.",
                        status=400,
                    )
                return jsonify({"ok": False, "error": "invalid_csrf"}), 400

        access_cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "echochat_access")
        refresh_cookie_name = app.config.get("JWT_REFRESH_COOKIE_NAME", "echochat_refresh")

        access_token = request.cookies.get(access_cookie_name)
        refresh_token = request.cookies.get(refresh_cookie_name)
        token_username = None

        # Best-effort: revoke the session (preferred), otherwise revoke JTIs.
        sid = None
        try:
            if refresh_token:
                r = decode_token(refresh_token, allow_expired=True)
                sid = r.get("sid")
                token_username = (r.get("sub") or token_username)
        except Exception:
            sid = None

        if not sid:
            try:
                if access_token:
                    a = decode_token(access_token, allow_expired=True)
                    sid = a.get("sid") or sid
                    token_username = (a.get("sub") or token_username)
            except Exception:
                pass

        if sid:
            try:
                revoke_auth_session(sid, reason="logout")
            except Exception:
                pass
            try:
                if token_username:
                    _clear_user_transient_presence(token_username)
            except Exception:
                pass
            try:
                if token_username:
                    _force_logout_live_sessions(
                        token_username,
                        "You were signed out.",
                        auth_session_ids={sid},
                        action="logout",
                        code="logout",
                    )
            except Exception:
                pass
        else:
            try:
                if access_token:
                    a = decode_token(access_token, allow_expired=True)
                    revoke_auth_token(a.get("jti"))
                    token_username = (a.get("sub") or token_username)
            except Exception:
                pass
            try:
                if refresh_token:
                    r = decode_token(refresh_token, allow_expired=True)
                    revoke_auth_token(r.get("jti"))
                    token_username = (r.get("sub") or token_username)
            except Exception:
                pass
            try:
                if token_username:
                    _clear_user_transient_presence(token_username)
            except Exception:
                pass

        # GET only reaches this point when legacy GET logout is explicitly enabled.
        # Normal GET remains non-mutating and renders a POST confirmation page.
        if _logout_wants_html() or request.method == "GET":
            resp = make_response(redirect("/login?reason=logged_out"))
        else:
            resp = jsonify({"ok": True, "msg": "Logout successful"})

        return _clear_auth_cookies_and_session(resp)


    # ------------------------------------------------------------------
    # Session Truth APIs (optional client/admin UI can call these)
    # ------------------------------------------------------------------
    @app.route("/auth/ping", methods=["GET"])
    @_limit(settings.get("rate_limit_auth_ping") or "120 per minute")
    @jwt_required()
    def auth_ping():
        """Lightweight auth/session check that does not extend the idle window."""
        user = get_jwt_identity()
        sid, _state, rejection = _require_active_session(user, touch_seen=True)
        if rejection:
            return rejection
        return _auth_json_response({"ok": True, "user": user, "session_active": True})

    def _public_auth_session_payload(session_row: dict, *, current_sid: str | None = None) -> dict:
        """Return a browser-safe auth-session row for the user's own UI/API."""
        sess_id = str((session_row or {}).get("session_id") or "").strip()
        user_agent = str((session_row or {}).get("user_agent") or "").strip()
        ip_address = str((session_row or {}).get("ip_address") or "").strip()
        revoked_at = (session_row or {}).get("revoked_at")
        return {
            "session_id": sess_id,
            "is_current": bool(current_sid and sess_id == current_sid),
            "is_active": revoked_at is None,
            "created_at": (session_row or {}).get("created_at"),
            "last_seen_at": (session_row or {}).get("last_seen_at"),
            "last_activity_at": (session_row or {}).get("last_activity_at"),
            "device_label": _short_device_label(user_agent),
            "user_agent": user_agent[:240] or "Unknown device",
            "ip_address": ip_address[:96],
        }

    def _short_device_label(ua: str) -> str:
        """Create a compact, non-HTML device label from a User-Agent string."""
        ua = str(ua or "").strip()
        if not ua:
            return "Unknown device"
        for token in ("Firefox/", "Chrome/", "Edg/", "Safari/", "OPR/"):
            if token in ua:
                idx = ua.find(token)
                tail = ua[idx:].split(" ", 1)[0]
                return tail[:72]
        return ua[:72]

    @app.route("/auth/sessions", methods=["GET"])
    @_limit(settings.get("rate_limit_sessions") or "120 per minute")
    @jwt_required()
    def auth_sessions():
        """Return the caller's active sessions without old revoked history."""
        user = get_jwt_identity()
        sid, _state, rejection = _require_active_session(user)
        if rejection:
            return rejection
        try:
            raw_sessions = list_auth_sessions(user, include_revoked=False, limit=50)
        except Exception:
            raw_sessions = []
        sessions = [_public_auth_session_payload(sess, current_sid=sid) for sess in raw_sessions]
        active_count = len(sessions)
        other_active_count = sum(1 for sess in sessions if not sess.get("is_current"))
        return _auth_json_response({
            "ok": True,
            "active_session_count": active_count,
            "other_active_count": other_active_count,
            "sessions": sessions,
        })


    @app.route("/auth/logout-others", methods=["POST"])
    @_limit(settings.get("rate_limit_logout_others") or "10 per minute")
    @jwt_required()
    def auth_logout_others():
        """Revoke every active session except the caller's current session."""
        user = get_jwt_identity()
        sid, _state, rejection = _require_active_session(user)
        if rejection:
            return rejection
        if not sid:
            return _auth_json_response({"ok": False, "error": "current_session_unknown"}, status=409)
        try:
            revoked = revoke_other_sessions_for_user(user, keep_session_id=sid, reason="logout_others")
        except Exception:
            logging.exception("Could not revoke other sessions for %s", user)
            return _auth_json_response({"ok": False, "error": "logout_others_failed"}, status=500)
        if revoked:
            try:
                _force_logout_live_sessions(
                    user,
                    "You were signed out because another session chose log out others.",
                    exclude_auth_session_ids={sid},
                    action="logout_others",
                    code="logout_others",
                )
            except Exception:
                pass
        return _auth_json_response({
            "ok": True,
            "revoked_sessions": revoked,
            "current_session_kept": True,
        })

    @app.route("/auth/logout-all", methods=["POST"])
    @_limit(settings.get("rate_limit_logout_all") or "5 per minute")
    @jwt_required()
    def auth_logout_all():
        """Revoke every active session for the caller, including this browser."""
        user = get_jwt_identity()
        _sid, _state, rejection = _require_active_session(user)
        if rejection:
            return rejection
        try:
            revoked = revoke_all_sessions_for_user(user, reason="logout_all")
        except Exception:
            logging.exception("Could not revoke all sessions for %s", user)
            return _auth_json_response({"ok": False, "error": "logout_all_failed"}, status=500)
        if revoked:
            try:
                _force_logout_live_sessions(
                    user,
                    "You were signed out from all sessions.",
                    action="logout_all",
                    code="logout_all",
                )
            except Exception:
                pass
        resp = _auth_json_response({"ok": True, "revoked_sessions": revoked}, clear_cookies=True)
        session.clear()
        return resp


    @app.route("/api/username_available", methods=["GET"])
    @_limit(settings.get("rate_limit_username_available") or "30 per minute")
    def username_available():
        """Live username availability check used by account-creation forms.

        It mirrors the public registration username policy before checking the
        database so the browser can show useful feedback without waiting for the
        final submit. Registration still repeats these checks server-side.
        """
        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        if ip_banned:
            _log_ip_ban_block("username_available_ip_ban_blocked", banned_ip)
            return _username_availability_json({
                "ok": False,
                "available": False,
                "status": "ip_banned",
                "username": "",
                "message": ip_ban_message,
            }, status=403)

        raw_username = request.args.get("username", "")
        username = normalize_registration_username(raw_username)
        if not username:
            return _username_availability_json({
                "ok": True,
                "available": False,
                "status": "empty",
                "username": "",
                "message": "Enter a username.",
            })

        ok_username, username_err, _blocked_term = validate_registration_username(username, settings=settings)
        if ok_username:
            ok_username, username_style_err = validate_account_username_style(username)
            if not ok_username:
                username_err = username_style_err
        if not ok_username:
            return _username_availability_json({
                "ok": True,
                "available": False,
                "status": "invalid",
                "username": username,
                "message": username_err or "Username not allowed.",
            })

        try:
            conn = get_db()
            taken = bool(user_exists(conn, username))
        except Exception as exc:
            logging.exception("Username availability check failed: %s", exc)
            return _username_availability_json({
                "ok": False,
                "available": False,
                "status": "unknown",
                "username": username,
                "message": "Could not check username right now. Try again in a moment.",
            }, status=503)

        return _username_availability_json({
            "ok": True,
            "available": not taken,
            "status": "available" if not taken else "taken",
            "username": username,
            "message": "Username is available." if not taken else "Username already exists.",
        })


    @app.route("/register", methods=["GET", "POST"])
    @_limit(settings.get("rate_limit_register") or "3 per minute", methods=["POST"])
    def register():
        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        if ip_banned:
            _log_ip_ban_block("register_ip_ban_blocked", banned_ip)
            return _render_register(ip_ban_message, status=403)

        if request.method == "POST":
            submitted_csrf = request.form.get("csrf_token")
            try:
                validate_csrf(submitted_csrf)
            except ValidationError:
                if not _register_csrf_fallback_valid(submitted_csrf):
                    return _render_register("Registration form expired. Please try again.", status=400)

            username = normalize_registration_username(request.form.get("username", ""))
            email, email_err = _normalize_registration_email(request.form.get("email", ""))
            phone = (request.form.get("phone", "") or "").strip()
            recovery_pin = (request.form.get("recovery_pin", "") or "").strip()
            recovery_pin_confirm = (request.form.get("recovery_pin_confirm", "") or "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            age_str = request.form.get("age", "").strip()
            form_values = _register_form_values(username=username, email=email or request.form.get("email", ""), phone=phone, age=age_str)

            if email_err:
                return _render_register(email_err, status=400, values=form_values)
            if not all([username, recovery_pin, recovery_pin_confirm, password, confirm, age_str]):
                return _render_register("All fields are required.", status=400, values=form_values)

            ok_username, username_err, blocked_term = validate_registration_username(username, settings=settings)
            if ok_username:
                ok_username, username_style_err = validate_account_username_style(username)
                username_err = username_style_err
            if not ok_username:
                try:
                    if blocked_term:
                        log_audit_event('anon', 'register_username_blocked', username, f"matched={blocked_term} ip={get_request_ip()}")
                except Exception:
                    pass
                return _render_register(username_err or "Username not allowed.", status=400, values=form_values)

            ok_reg_user, retry_reg_user = _enforce_named_rate_limit(
                'register:user',
                username,
                settings.get('rate_limit_register_username') or '5@3600',
                default_limit=5,
                default_window=3600,
            )
            if not ok_reg_user:
                resp = _render_register('That username has hit the registration rate limit. Try again later.', status=429, values=form_values)
                resp.headers['Retry-After'] = str(int(max(1, retry_reg_user or 1)))
                return resp

            ok_reg_email, retry_reg_email = _enforce_named_rate_limit(
                'register:email',
                email,
                settings.get('rate_limit_register_email') or '5@3600',
                default_limit=5,
                default_window=3600,
            )
            if not ok_reg_email:
                resp = _render_register('That email has hit the registration rate limit. Try again later.', status=429, values=form_values)
                resp.headers['Retry-After'] = str(int(max(1, retry_reg_email or 1)))
                return resp

            ok_password, password_err = validate_account_password(
                password,
                username=username,
                email=email,
                server_name=settings.get("server_name"),
            )
            if not ok_password:
                return _render_register(password_err or "Password does not meet account rules.", status=400, values=form_values)
            if password != confirm:
                return _render_register("Passwords must match.", status=400, values=form_values)
            if recovery_pin != recovery_pin_confirm:
                return _render_register("Recovery PINs must match.", status=400, values=form_values)
            ok_pin, pin_err = validate_recovery_pin(recovery_pin)
            if not ok_pin:
                return _render_register(pin_err or recovery_pin_policy_summary(), status=400, values=form_values)
            try:
                age = int(age_str)
                if age < 0:
                    raise ValueError
            except ValueError:
                return _render_register("Invalid age.", status=400, values=form_values)

            # Optional phone number. If supplied, require international E.164-ish format
            # so SMS 2FA can use it later without ambiguity.
            if phone:
                phone = _normalize_phone_e164(phone)
                form_values["phone"] = phone or form_values.get("phone", "")
                if not phone:
                    return _render_register("Phone must be in international format like +15551234567.", status=400, values=form_values)

            try:
                conn = get_db()

                # Friendly pre-checks for clearer errors (DB still enforces constraints).
                if user_exists(conn, username):
                    return _render_register("Username already exists.", status=409, values=form_values)
                if email_in_use(conn, email, settings=settings):
                    return _render_register("Email already in use.", status=409, values=form_values)

                # Create the account, default avatar, and baseline viewer role in one transaction.
                pwd_hash = hash_password(password)
                pin_hash = hash_password(recovery_pin)
                create_user_with_keys(
                    conn=conn,
                    username=username,
                    raw_password=password,
                    password_hash=pwd_hash,
                    email=email,
                    phone=phone or None,
                    address=None,
                    field_encryption_settings=settings,
                    age=age,
                    is_admin=False,
                    recovery_pin_hash=pin_hash,
                    recovery_pin_set_at=datetime.now(timezone.utc),
                    commit=False,
                )
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;", (username,))
                    user_row = cur.fetchone()
                    cur.execute("SELECT id FROM roles WHERE name = 'viewer' LIMIT 1;")
                    role_row = cur.fetchone()
                    if not user_row or not role_row:
                        raise RuntimeError("baseline viewer role is not available")
                    cur.execute(
                        """
                        INSERT INTO user_roles (user_id, role_id)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id, role_id) DO NOTHING;
                        """,
                        (user_row[0], role_row[0]),
                    )
                conn.commit()
                resp = make_response(redirect("/login?registered=1"))
                resp.delete_cookie(REGISTER_CSRF_FALLBACK_COOKIE, path="/register")
                return resp
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                # Try to provide a deterministic error when a DB uniqueness constraint trips.
                msg = str(e)
                low = msg.lower()
                if "unique" in low or "duplicate" in low:
                    if "users_email_unique_ci" in low or "lower(email" in low or "email" in low:
                        return _render_register("Email already in use.", status=409, values=form_values)
                    return _render_register("Username already exists.", status=409, values=form_values)
                logging.exception("Registration failed")
                return _render_register("Registration failed. Please try again later.", status=500, values=form_values)

        return _render_register()

    @app.route("/forgot-password", methods=["GET", "POST"])
    @_limit(settings.get("rate_limit_forgot_password") or "3 per minute", methods=["POST"])
    def forgot_password():
        """Begin password reset.

        Flow:
          1) User submits email + required username + Recovery PIN
          2) Server verifies the username/email/PIN combination without leaking public lookup details
          3) Server sends a high-entropy, single-use reset link to that email
          4) User must also provide their 4-to-8 digit Recovery PIN to complete the reset

        Security:
          - Always respond generically to avoid account enumeration.
          - Token expires quickly and is single-use.
        """

        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        if ip_banned:
            _log_ip_ban_block("forgot_password_ip_ban_blocked", banned_ip)
            return _render_forgot(ip_ban_message, status=403)

        if request.method == "POST":
            raw_email = request.form.get("email", "") or ""
            raw_username = request.form.get("username", "") or ""
            form_values = _forgot_form_values(email=raw_email, username=raw_username)
            submitted_csrf = request.form.get("csrf_token")
            try:
                validate_csrf(submitted_csrf)
            except ValidationError:
                if not _forgot_csrf_fallback_valid(submitted_csrf):
                    return _render_forgot("Forgot password form expired. Please try again.", status=400, values=form_values)

            email, email_err = _normalize_registration_email(raw_email)
            username_hint = normalize_registration_username(raw_username)
            recovery_pin = (request.form.get("recovery_pin", "") or "").strip()
            if email_err:
                return _render_forgot(email_err, status=400, values=form_values)
            ok_username, username_err = validate_registration_username_format(username_hint, settings=settings)
            if not ok_username:
                return _render_forgot(username_err or "Username required", status=400, values=form_values)
            ok_pin, pin_err = validate_recovery_pin(recovery_pin)
            if not ok_pin:
                return _render_forgot(pin_err or recovery_pin_policy_summary(), status=400, values=form_values)

            ok_forgot_email, retry_forgot_email = _enforce_named_rate_limit(
                'forgot:email',
                email,
                settings.get('rate_limit_forgot_password_email') or '5@3600',
                default_limit=5,
                default_window=3600,
            )
            if not ok_forgot_email:
                return _auth_limit_response('Password reset rate limit reached for that email. Try again later.', template='forgot_password.html', error='Too many reset requests. Try again later.', message=None, values=form_values, retry_after=retry_forgot_email)

            ok_forgot_user, retry_forgot_user = _enforce_named_rate_limit(
                'forgot:user',
                username_hint,
                settings.get('rate_limit_forgot_password_username') or '5@3600',
                default_limit=5,
                default_window=3600,
            )
            if not ok_forgot_user:
                return _auth_limit_response('Password reset rate limit reached for that username. Try again later.', template='forgot_password.html', error='Too many reset requests. Try again later.', message=None, values=form_values, retry_after=retry_forgot_user)

            conn = get_db()
            username = None
            recovery_pin_hash = None
            recovery_failed_attempts = 0
            recovery_locked_until = None
            lookup_note = None
            email_send_info = None
            pin_verified = False

            client_ip = get_request_ip()

            # Lookup (best-effort; response is generic either way). Username is required
            # so password reset never chooses an arbitrary account by shared email.
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT username, email, email_hash, email_encrypted, recovery_pin_hash, recovery_failed_attempts, recovery_locked_until
                          FROM users
                         WHERE LOWER(username) = LOWER(%s)
                         LIMIT 1;
                        """,
                        (username_hint,),
                    )
                    row = cur.fetchone()
                    if row and submitted_email_matches(email, legacy_email=row[1], email_hash_value=row[2], email_encrypted=row[3], settings=settings):
                        username = row[0]
                        recovery_pin_hash = row[4]
                        recovery_failed_attempts = int(row[5] or 0)
                        recovery_locked_until = _as_aware_utc(row[6])
                        lookup_note = "matched_username_email"
            except Exception as e:
                logging.warning("DB error in forgot_password lookup: %s", e)
                username = None
                lookup_note = "db_error"

            require_pin = bool(recovery_pin_hash)

            # Verify the Recovery PIN before issuing any reset email. Public responses
            # stay generic so invalid username/email/PIN combinations cannot enumerate accounts.
            if username and not recovery_pin_hash:
                lookup_note = "missing_recovery_pin"
                username = None
            elif username and recovery_locked_until and recovery_locked_until > datetime.now(timezone.utc):
                lookup_note = "recovery_pin_locked"
                username = None
            elif username and recovery_pin_hash:
                ok_pin, upgraded_pin_hash = verify_password_and_upgrade(recovery_pin, recovery_pin_hash)
                if ok_pin:
                    pin_verified = True
                    try:
                        with conn.cursor() as cur:
                            if upgraded_pin_hash:
                                cur.execute(
                                    """
                                    UPDATE users
                                       SET recovery_pin_hash = %s,
                                           recovery_failed_attempts = 0,
                                           recovery_locked_until = NULL
                                     WHERE LOWER(username) = LOWER(%s);
                                    """,
                                    (upgraded_pin_hash, username),
                                )
                            else:
                                cur.execute(
                                    """
                                    UPDATE users
                                       SET recovery_failed_attempts = 0,
                                           recovery_locked_until = NULL
                                     WHERE LOWER(username) = LOWER(%s);
                                    """,
                                    (username,),
                                )
                        conn.commit()
                    except Exception as e:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        logging.warning("Could not reset recovery PIN counters for %s during forgot-password: %s", username, e)
                else:
                    failed_attempts = recovery_failed_attempts + 1
                    new_locked_until = None
                    max_attempts, lock_min = recovery_pin_lock_settings(settings)
                    if failed_attempts >= max_attempts:
                        new_locked_until = datetime.now(timezone.utc) + timedelta(minutes=lock_min)
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE users
                                   SET recovery_failed_attempts = %s,
                                       recovery_locked_until = %s
                                 WHERE LOWER(username) = LOWER(%s);
                                """,
                                (failed_attempts, new_locked_until, username),
                            )
                        conn.commit()
                    except Exception:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                    lookup_note = "invalid_recovery_pin"
                    username = None

            if not pin_verified:
                _dummy_recovery_pin_verify(recovery_pin)

            # If we found an account and verified its Recovery PIN, generate a token.
            reset_url = None
            reset_token_hash = None
            if username:
                try:
                    now = datetime.now(timezone.utc)
                    # Uses password_reset_max_active_tokens from settings via password_reset_limit_settings().
                    ttl_min, max_daily_reset_requests, max_active_tokens = password_reset_limit_settings(settings)
                    expires_at = now + timedelta(minutes=ttl_min)

                    # Keep reset delivery usable during setup/testing: if older active links
                    # already exist, revoke them and issue one fresh link instead of silently
                    # returning the generic public response. The email/user/PIN rate limits and
                    # daily per-account token budget remain the primary abuse controls. Lock
                    # the verified user row while checking/inserting so concurrent requests
                    # cannot race past the daily or active-token budget.
                    daily_limit_reached = False
                    active_count = max_active_tokens
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT username
                              FROM users
                             WHERE LOWER(username) = LOWER(%s)
                             FOR UPDATE;
                            """,
                            (username,),
                        )
                        if not cur.fetchone():
                            username = None
                            lookup_note = "account_missing_after_pin"
                            conn.rollback()
                        else:
                            cur.execute(
                                """
                                SELECT COUNT(*)
                                  FROM password_reset_tokens
                                 WHERE LOWER(username) = LOWER(%s)
                                   AND created_at > (CURRENT_TIMESTAMP - INTERVAL '24 hours');
                                """,
                                (username,),
                            )
                            daily_count = int((cur.fetchone() or [0])[0])

                            if daily_count >= max_daily_reset_requests:
                                daily_limit_reached = True
                                email_send_info = "daily_limit_reached"
                                conn.rollback()
                                logging.warning(
                                    "Password reset daily limit reached for user=%s count=%s limit=%s",
                                    username,
                                    daily_count,
                                    max_daily_reset_requests,
                                )
                                try:
                                    log_audit_event('anon', 'password_reset_daily_limited', username, f"ip={get_request_ip()} count={daily_count} limit={max_daily_reset_requests}")
                                except Exception:
                                    pass
                            else:
                                cur.execute(
                                    """
                                    SELECT COUNT(*)
                                      FROM password_reset_tokens
                                     WHERE LOWER(username) = LOWER(%s)
                                       AND created_at > (CURRENT_TIMESTAMP - INTERVAL '15 minutes')
                                       AND used_at IS NULL;
                                    """,
                                    (username,),
                                )
                                active_count = int((cur.fetchone() or [0])[0])

                                if active_count >= max_active_tokens:
                                    cur.execute(
                                        """
                                        DELETE FROM password_reset_tokens
                                         WHERE LOWER(username) = LOWER(%s)
                                           AND used_at IS NULL
                                           AND created_at > (CURRENT_TIMESTAMP - INTERVAL '15 minutes');
                                        """,
                                        (username,),
                                    )
                                    replaced_count = int(getattr(cur, "rowcount", 0) or 0)
                                    active_count = 0
                                    logging.info(
                                        "Password reset request replaced %s outstanding active reset token(s) for user=%s before issuing a fresh link.",
                                        replaced_count,
                                        username,
                                    )

                                if active_count < max_active_tokens:
                                    # IMPORTANT: server_host is often 0.0.0.0 (bind-all) which is not a usable link.
                                    # Prefer explicit public_base_url. Only derive from Host for localhost/LAN dev.
                                    base_url = _safe_password_reset_base_url()
                                    if not base_url:
                                        email_send_info = "missing_public_base_url"
                                        conn.rollback()
                                        logging.error("Password reset token was not created because public_base_url is missing or invalid.")
                                    else:
                                        token = secrets.token_urlsafe(32)
                                        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                                        reset_token_hash = token_hash

                                        cur.execute(
                                            """
                                            INSERT INTO password_reset_tokens (username, token_hash, expires_at, request_ip, user_agent)
                                            VALUES (%s, %s, %s, %s, %s);
                                            """,
                                            (
                                                username,
                                                token_hash,
                                                expires_at,
                                                get_request_ip(),
                                                request.headers.get("User-Agent"),
                                            ),
                                        )
                                        conn.commit()

                            if reset_token_hash:
                                try:
                                    log_audit_event('anon', 'password_reset_request', username, f"ip={get_request_ip()} ua={request.headers.get('User-Agent')} note={lookup_note}")
                                except Exception:
                                    pass

                                reset_url = f"{base_url}/reset-password/{token}"
                                pin_note = (
                                    "To complete the reset, you will also need your 4-to-8 digit Recovery PIN.\n\n"
                                    if require_pin
                                    else (
                                        "This account does not have a Recovery PIN set, so the reset link alone is sufficient.\n"
                                        "After logging in, consider setting a Recovery PIN for extra protection.\n\n"
                                    )
                                )

                                body = (
                                    f"You requested a password reset for {settings.get('server_name') or DEFAULT_SERVER_NAME}.\n\n"
                                    f"Username: {username}\n\n"
                                    f"Reset link (expires in {ttl_min} minutes, single-use):\n{reset_url}\n\n"
                                    f"{pin_note}"
                                    "If you did not request this, you can ignore this email."
                                )
                                ok, email_send_info = send_email(settings, to_email=email, subject=f"{settings.get('server_name') or DEFAULT_SERVER_NAME} password reset", body_text=body)
                                if ok:
                                    logging.info(
                                        "Password reset email accepted by SMTP for user=%s email=%s info=%s",
                                        username,
                                        email,
                                        email_send_info,
                                    )

                                # Local dev UX: mirror the latest reset link to a local file so a developer can
                                # complete the reset even when Gmail/Brevo deliverability hides, blocks, delays,
                                # or quarantines the message after SMTP has accepted it. The link is never printed
                                # to stdout/server logs, and the default mirror is restricted to localhost/LAN
                                # requests only. Disable with password_reset_spool_local_copy=false.
                                spool_ok = False
                                spool_reason = None
                                try:
                                    allow_remote = bool(settings.get("password_reset_spool_allow_remote", False))
                                    local_copy_enabled = bool(settings.get("password_reset_spool_local_copy", True))
                                    should_spool = False
                                    if not ok and email_send_info == "not_configured":
                                        should_spool = True
                                        spool_reason = "smtp_not_configured"
                                    elif ok and local_copy_enabled:
                                        should_spool = True
                                        spool_reason = "smtp_accepted_local_copy"

                                    if should_spool:
                                        if allow_remote or is_localish_request():
                                            spool_path = str(settings.get("password_reset_spool_file") or os.path.join("logs", "reset_links.log"))
                                            spool_dir = os.path.dirname(spool_path) or "."
                                            os.makedirs(spool_dir, mode=0o700, exist_ok=True)
                                            try:
                                                os.chmod(spool_dir, 0o700)
                                            except Exception:
                                                pass
                                            fd = os.open(spool_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                                            try:
                                                os.chmod(spool_path, 0o600)
                                            except Exception:
                                                pass
                                            ts = datetime.now(timezone.utc).isoformat()
                                            line = (
                                                f"{ts}\tuser={username}\temail={email}\tip={client_ip}\t"
                                                f"smtp={email_send_info}\treason={spool_reason}\turl={reset_url}\n"
                                            )
                                            with os.fdopen(fd, "a", encoding="utf-8") as f:
                                                f.write(line)
                                            spool_ok = True

                                            # Do not print reset links to stdout/server logs.
                                            # Local dev can read the configured spool file directly.

                                        if spool_ok:
                                            if ok:
                                                logging.warning(
                                                    "Password reset email accepted by SMTP; local reset-link mirror written to %s for deliverability troubleshooting.",
                                                    settings.get("password_reset_spool_file") or os.path.join("logs", "reset_links.log"),
                                                )
                                            else:
                                                logging.warning(
                                                    "Password reset email not sent (SMTP not configured). Reset link spooled to %s",
                                                    settings.get("password_reset_spool_file") or os.path.join("logs", "reset_links.log"),
                                                )
                                        elif not ok and email_send_info == "not_configured":
                                            logging.error(
                                                "Password reset email not sent (SMTP not configured). Spooling disabled for non-local IP %s",
                                                client_ip,
                                            )
                                except Exception as e2:
                                    logging.error("Failed to spool password reset link: %s", e2)

                                # If SMTP was configured but delivery failed, remove the just-created token
                                # so reset-token budget is not consumed by unusable links.
                                if not ok and email_send_info != "not_configured" and reset_token_hash:
                                    try:
                                        with conn.cursor() as cur:
                                            cur.execute(
                                                "DELETE FROM password_reset_tokens WHERE token_hash = %s AND used_at IS NULL;",
                                                (reset_token_hash,),
                                            )
                                        conn.commit()
                                    except Exception:
                                        try:
                                            conn.rollback()
                                        except Exception:
                                            pass
                                        logging.exception("Failed to clean up unsent password reset token")
                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    logging.warning("Failed to create/send reset token: %s", e)

            if not username and is_localish_request():
                logging.info(
                    "Password reset email not sent: no valid username/email/PIN combination matched submitted email=%s username_hint=%s note=%s",
                    email,
                    username_hint or "<none>",
                    lookup_note or "no_match",
                )

            # Avoid account enumeration for public users, but make localhost/LAN setup failures visible
            # so administrators do not see a misleading success message while SMTP is broken.
            msg = "If an account matches that email and mail delivery is available, a reset link will be sent."
            local_mail_failure = False
            local_mail_error = None

            if email_send_info == "daily_limit_reached":
                logging.warning("Password reset email not sent: daily account limit reached.")
            elif email_send_info == "not_configured":
                logging.error("Password reset email not sent: SMTP not configured.")
                local_mail_failure = True
                local_mail_error = "SMTP is not configured."
            elif email_send_info == "missing_public_base_url":
                logging.error("Password reset email not sent: set public_base_url or ECHOCHAT_PUBLIC_BASE_URL.")
                local_mail_failure = True
                local_mail_error = "public_base_url is missing or invalid."
            elif email_send_info in {"invalid_from_localhost", "invalid_from_placeholder"}:
                logging.error("Password reset email not sent: SMTP From address is not a real verified sender for the configured provider.")
                local_mail_failure = True
                local_mail_error = "smtp_from must be a real sender verified in your email provider dashboard."
            elif isinstance(email_send_info, str) and email_send_info.startswith("smtp_error:"):
                logging.warning("Password reset email send failed: %s", email_send_info)
                local_mail_failure = True
                local_mail_error = f"SMTP delivery failed ({email_send_info.removeprefix('smtp_error:')})."

            if local_mail_failure and is_localish_request():
                return _render_forgot(
                    error=f"Password reset email could not be sent: {local_mail_error}",
                    status=503,
                    values=form_values,
                )

            return _render_forgot(message=msg, values=form_values)

        return _render_forgot()

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    @_limit(settings.get("rate_limit_reset_password") or "6 per minute", methods=["POST"])
    def reset_password(token):
        """Complete the reset using token + Recovery PIN."""

        ip_banned, banned_ip, ip_ban_message = _current_request_ip_banned()
        if ip_banned:
            _log_ip_ban_block("reset_password_ip_ban_blocked", banned_ip)
            return _render_reset(ip_ban_message, status=403, require_pin=True)

        raw_token = str(token or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", raw_token):
            return _render_reset("Invalid or expired reset link", status=400, require_pin=True)

        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        conn = get_db()
        now = datetime.now(timezone.utc)

        # Validate token
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, expires_at, used_at
                      FROM password_reset_tokens
                     WHERE token_hash = %s;
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
        except Exception as e:
            logging.warning("DB error loading reset token: %s", e)
            row = None

        if not row:
            return _render_reset("Invalid or expired reset link", status=400, require_pin=True)

        username, expires_at, used_at = row[0], _as_aware_utc(row[1]), _as_aware_utc(row[2])
        reset_username = username
        if used_at is not None or (expires_at and expires_at <= now):
            return _render_reset("Invalid or expired reset link", status=400, require_pin=True, reset_username=reset_username)

        # Determine whether this account requires a Recovery PIN and load the
        # stored email for password-policy context. Password reset should not
        # crash just because the account has encrypted-at-rest email storage.
        require_pin = False
        account_email = None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT recovery_pin_hash, email, email_encrypted FROM users WHERE LOWER(username) = LOWER(%s);", (username,))
                prow = cur.fetchone()
            require_pin = bool(prow and prow[0])
            if prow:
                account_email = display_email(prow[1], prow[2], settings) or None
        except Exception as e:
            logging.warning('DB error checking recovery pin presence: %s', e)
            require_pin = True  # fail-closed

        if request.method == "POST":
            try:
                validate_csrf(request.form.get("csrf_token"))
            except ValidationError:
                if not _reset_csrf_fallback_valid(request.form.get("csrf_token")):
                    return _render_reset("Reset form expired. Please try again.", status=400, require_pin=require_pin, reset_username=reset_username)

            ok_reset_token, retry_reset_token = _enforce_named_rate_limit(
                'reset:token',
                token_hash,
                settings.get('rate_limit_reset_password_token') or '10@900',
                default_limit=10,
                default_window=900,
            )
            if not ok_reset_token:
                return _auth_limit_response('This reset link is temporarily rate limited. Try again later.', template='reset_password.html', error='Too many reset attempts for this link. Try again later.', message=None, require_pin=require_pin, reset_username=reset_username, retry_after=retry_reset_token)

            pin = (request.form.get("recovery_pin") or "").strip()
            pw = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            ok_password, password_err = validate_account_password(pw, username=username, email=account_email, server_name=settings.get("server_name"))
            if not ok_password:
                return _render_reset(password_err or "Password does not meet account rules", status=400, require_pin=require_pin, reset_username=reset_username)
            if pw != confirm:
                return _render_reset("Passwords must match", status=400, require_pin=require_pin, reset_username=reset_username)
            if require_pin:
                ok_pin_format, pin_format_err = validate_recovery_pin(pin)
                if not ok_pin_format:
                    return _render_reset(pin_format_err or recovery_pin_policy_summary(), status=400, require_pin=require_pin, reset_username=reset_username)

            # Load recovery state
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT recovery_pin_hash, recovery_failed_attempts, recovery_locked_until
                          FROM users
                         WHERE LOWER(username) = LOWER(%s);
                        """,
                        (username,),
                    )
                    urow = cur.fetchone()
            except Exception as e:
                logging.warning("DB error loading user recovery state: %s", e)
                urow = None

            if not urow:
                return _render_reset("Account not found", status=400, require_pin=require_pin, reset_username=reset_username)

            if require_pin and not urow[0]:
                # Can't reset with PIN requirement if none is configured.
                return _render_reset("Recovery PIN required but not configured. Contact an admin.", status=400, require_pin=require_pin, reset_username=reset_username)

            stored_pin_hash, failed_attempts, locked_until = urow[0], int(urow[1] or 0), _as_aware_utc(urow[2])
            if require_pin and locked_until and locked_until > now:
                return _render_reset("Too many incorrect PIN attempts. Try again later.", status=429, require_pin=require_pin, reset_username=reset_username)
            # Verify PIN
            if require_pin:
                ok_pin, upgraded_pin_hash = verify_password_and_upgrade(pin, stored_pin_hash)
                if not ok_pin:
                    failed_attempts += 1
                    new_locked_until = None
                    max_attempts, lock_min = recovery_pin_lock_settings(settings)
                    if failed_attempts >= max_attempts:
                        new_locked_until = now + timedelta(minutes=lock_min)
            
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE users
                                   SET recovery_failed_attempts = %s,
                                       recovery_locked_until = %s
                                 WHERE LOWER(username) = LOWER(%s);
                                """,
                                (failed_attempts, new_locked_until, username),
                            )
                        conn.commit()
                    except Exception:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
            
                    return _render_reset("Invalid PIN", status=400, require_pin=require_pin, reset_username=reset_username)
            
                # Optional: upgrade stored PIN hash (legacy -> Argon2id)
                if upgraded_pin_hash:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE users SET recovery_pin_hash = %s WHERE LOWER(username) = LOWER(%s);",
                                (upgraded_pin_hash, username),
                            )
                        conn.commit()
                    except Exception as e:
                        logging.warning("Could not upgrade recovery PIN hash for %s: %s", username, e)
            
            # Success: set password, consume token(s), reset counters, rotate E2EE keys, revoke sessions
            try:
                # Password-derived encryption means we must regenerate encrypted_private_key on reset.
                new_public, new_enc_priv = generate_user_keypair_for_password(pw)

                with conn.cursor() as cur:
                    # Atomically consume the submitted token before changing the
                    # password. This prevents two simultaneous POSTs from using
                    # the same reset link successfully.
                    cur.execute(
                        """
                        UPDATE password_reset_tokens
                           SET used_at = CURRENT_TIMESTAMP
                         WHERE token_hash = %s
                           AND LOWER(username) = LOWER(%s)
                           AND used_at IS NULL
                           AND expires_at > CURRENT_TIMESTAMP;
                        """,
                        (token_hash, username),
                    )
                    if int(getattr(cur, "rowcount", 0) or 0) != 1:
                        conn.rollback()
                        return _render_reset("Invalid or expired reset link", status=400, require_pin=True, reset_username=reset_username)

                    cur.execute(
                        """
                        UPDATE users
                           SET password = %s,
                               public_key = %s,
                               encrypted_private_key = %s,
                               recovery_failed_attempts = 0,
                               recovery_locked_until = NULL,
                               auth_version = COALESCE(auth_version, 0) + 1,
                               password_changed_at = CURRENT_TIMESTAMP,
                               auth_changed_at = CURRENT_TIMESTAMP
                         WHERE LOWER(username) = LOWER(%s);
                        """,
                        (hash_password(pw), new_public, new_enc_priv, username),
                    )
                    # Consume all other outstanding reset tokens for this user.
                    cur.execute(
                        "UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP WHERE LOWER(username)=LOWER(%s) AND used_at IS NULL;",
                        (username,),
                    )
                    cur.execute(
                        """
                        UPDATE auth_sessions
                           SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                               revoked_reason = COALESCE(revoked_reason, 'password_reset')
                         WHERE LOWER(username)=LOWER(%s) AND revoked_at IS NULL;
                        """,
                        (username,),
                    )
                    cur.execute(
                        "UPDATE auth_tokens SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP) WHERE LOWER(username)=LOWER(%s) AND revoked_at IS NULL;",
                        (username,),
                    )
                conn.commit()

                try:
                    log_audit_event('anon', 'password_reset_complete', username, f"token={token_hash[:12]}...")
                except Exception:
                    pass

                try:
                    _force_logout_live_sessions(
                        username,
                        "Your password was changed. Please sign in again.",
                        action="password_reset",
                        code="password_reset",
                    )
                except Exception:
                    pass
            except Exception as e:
                logging.warning('DB error completing reset: %s', e)
                try:
                    conn.rollback()
                except Exception:
                    pass
                return _render_reset('Error resetting password', status=500, require_pin=require_pin, reset_username=reset_username)

            login_redirect_url = url_for('login', message='password_reset_complete')
            resp = _render_reset(
                message='Password reset complete. Sending you back to login...',
                error=None,
                require_pin=False,
                reset_username=reset_username,
                reset_complete=True,
                login_redirect_url=login_redirect_url,
                login_redirect_seconds=3,
            )
            # Clear any existing auth cookies so the browser doesn't try to reuse an old session.
            try:
                unset_jwt_cookies(resp)
            except Exception:
                pass
            return resp
        return _render_reset(message=None, error=None, require_pin=require_pin, reset_username=reset_username)

    @app.route("/account/security", methods=["GET", "POST"])
    @_limit(settings.get("rate_limit_account_security") or "30 per minute", methods=["POST"])
    @jwt_required()
    def account_security():
        user = get_jwt_identity()
        current_sid, _session_state, rejection = _require_active_session(user, redirect_on_failure=True)
        if rejection:
            return rejection

        def _mask_email(email: str) -> str:
            email = str(email or "").strip()
            if not email or "@" not in email:
                return ""
            local, domain = email.split("@", 1)
            if len(local) <= 2:
                local_mask = local[0] + "*" * max(0, len(local) - 1)
            else:
                local_mask = local[:2] + "*" * max(0, len(local) - 2)
            return f"{local_mask}@{domain}"

        def _format_dt(value):
            if not value:
                return "—"
            try:
                if hasattr(value, "astimezone"):
                    value = value.astimezone(timezone.utc)
                return value.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                try:
                    return str(value)
                except Exception:
                    return "—"

        def _short_user_agent(ua: str) -> str:
            ua = str(ua or "").strip()
            if not ua:
                return "Unknown device"
            for token in ("Firefox/", "Chrome/", "Edg/", "Safari/", "OPR/"):
                if token in ua:
                    idx = ua.find(token)
                    tail = ua[idx:].split(" ", 1)[0]
                    return tail[:72]
            return ua[:72]

        def _account_email_for_password_policy() -> str | None:
            try:
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute("SELECT email, email_encrypted FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1;", (user,))
                    row = cur.fetchone()
                if not row:
                    return None
                return display_email(row[0], row[1], settings) or None
            except Exception:
                logging.warning("Could not load account email for password-policy context for %s", user, exc_info=True)
                return None

        def _password_change_form_validation(current_password: str, new_password: str, confirm_password: str) -> str:
            """Validate form-only password-change fields before touching account secrets.

            Full password policy validation happens only after the current password is
            verified, so a wrong-current-password request cannot use this page as a
            password-policy oracle for the account.
            """
            if not current_password:
                return "Enter your current password."
            if not new_password:
                return "Enter a new password."
            if not confirm_password:
                return "Confirm your new password."
            if new_password != confirm_password:
                return "New password and confirmation do not match."
            if current_password == new_password:
                return "Choose a different new password."
            return ""

        def _load_summary():
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT email, email_encrypted, phone, two_factor_enabled, is_verified, created_at
                      FROM users
                     WHERE LOWER(username) = LOWER(%s)
                     LIMIT 1;
                    """,
                    (user,),
                )
                row = cur.fetchone()
            if not row:
                return None
            email, email_encrypted, phone, two_factor_enabled, is_verified, created_at = row
            email = display_email(email, email_encrypted, settings)
            raw_sessions = list_auth_sessions(user) or []
            sessions = []
            active_count = 0
            other_active_count = 0
            for sess in raw_sessions:
                sess_id = str(sess.get("session_id") or "").strip()
                revoked_at = sess.get("revoked_at")
                is_current = bool(current_sid and sess_id == current_sid)
                is_active = revoked_at is None
                if is_active:
                    active_count += 1
                    if not is_current:
                        other_active_count += 1
                sessions.append({
                    **sess,
                    "is_current": is_current,
                    "is_active": is_active,
                    "created_at_display": _format_dt(sess.get("created_at")),
                    "last_seen_at_display": _format_dt(sess.get("last_seen_at")),
                    "last_activity_at_display": _format_dt(sess.get("last_activity_at")),
                    "revoked_at_display": _format_dt(revoked_at),
                    "revoked_reason_display": str(sess.get("revoked_reason") or "").replace("_", " ").strip() or "—",
                    "ip_display": str(sess.get("ip_address") or "").strip()[:96] or "—",
                    "user_agent_display": str(sess.get("user_agent") or "").strip()[:240] or "Unknown device",
                    "device_label": _short_device_label(sess.get("user_agent") or ""),
                })
            return {
                "email_masked": _mask_email(email or ""),
                "phone_masked": _mask_phone(decrypt_sensitive_field(phone or "", settings, field_name="users.phone")),
                "two_factor_enabled": bool(two_factor_enabled),
                "is_verified": bool(is_verified),
                "created_at_display": _format_dt(created_at),
                "sessions": sessions,
                "active_session_count": active_count,
                "other_active_count": other_active_count,
            }

        def _render_account_security(
            *,
            message: str | None = None,
            error: str | None = None,
            status: int = 200,
            summary_override: dict | None = None,
        ):
            current_summary = summary_override if summary_override is not None else _load_summary()
            if not current_summary:
                resp = make_response(render_template(
                    "account_security.html",
                    current_user=user,
                    security_summary={
                        "email_masked": "",
                        "phone_masked": "",
                        "two_factor_enabled": False,
                        "is_verified": False,
                        "created_at_display": "—",
                        "sessions": [],
                        "active_session_count": 0,
                        "other_active_count": 0,
                    },
                    current_sid=current_sid,
                    twilio_ready=_twilio_verify_ready(),
                    sms_2fa_available=bool(effective_twilio_settings(settings).get("enable_two_factor_beta") and effective_twilio_settings(settings).get("enable_sms_two_factor")),
                    message=None,
                    error=error or "Could not load your account security state right now.",
                ), 404 if status == 200 else int(status))
            else:
                resp = make_response(render_template(
                    "account_security.html",
                    current_user=user,
                    security_summary=current_summary,
                    current_sid=current_sid,
                    twilio_ready=_twilio_verify_ready(),
                    sms_2fa_available=bool(effective_twilio_settings(settings).get("enable_two_factor_beta") and effective_twilio_settings(settings).get("enable_sms_two_factor")),
                    message=message,
                    error=error,
                ), int(status))
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp

        summary = _load_summary()
        if not summary:
            return _render_account_security(error="Could not load your account security state right now.", status=404, summary_override=None)

        message = None
        error = None

        if request.method == "POST":
            try:
                validate_csrf(request.form.get("csrf_token"))
            except ValidationError:
                return _render_account_security(error="Account Security form expired. Please try again.", status=400, summary_override=summary)

            action = str(request.form.get("action") or "").strip().lower()
            valid_session_ids = {
                str(s.get("session_id") or "").strip()
                for s in (summary.get("sessions") or [])
                if str(s.get("session_id") or "").strip() and bool(s.get("is_active"))
            }
            valid_actions = {"change_password", "revoke_session", "logout_others", "logout_all"}
            if action not in valid_actions:
                error = "Choose a valid account-security action."

            elif action == "change_password":
                current_password = request.form.get("current_password", "")
                new_password = request.form.get("new_password", "")
                confirm_password = request.form.get("confirm_password", "")
                sign_out_others = str(request.form.get("sign_out_others") or "").strip().lower() in {"1", "true", "on", "yes"}

                validation_error = _password_change_form_validation(current_password, new_password, confirm_password)
                if validation_error:
                    error = validation_error
                else:
                    conn = get_db()
                    new_encrypted_private_key = None
                    try:
                        with conn.cursor() as cur:
                            # Lock the account row while verifying the current password and
                            # updating the wrapped private key. This prevents two concurrent
                            # password-change POSTs from both succeeding with the same old
                            # password and last-writer-wins key wrapping.
                            cur.execute(
                                """
                                SELECT password, encrypted_private_key, email, email_encrypted
                                  FROM users
                                 WHERE LOWER(username) = LOWER(%s)
                                 FOR UPDATE;
                                """,
                                (user,),
                            )
                            row = cur.fetchone()

                            if not row:
                                error = "Could not load your account security state right now."
                            else:
                                stored_hash, encrypted_private_key, email_value, email_encrypted = row
                                ok, _upgraded_hash = verify_password_and_upgrade(current_password, stored_hash)
                                if not ok:
                                    error = "Current password is incorrect."
                                elif not encrypted_private_key:
                                    error = "Your encrypted private key is missing. Use password reset or contact an admin."
                                else:
                                    account_email = display_email(email_value, email_encrypted, settings) or None
                                    ok_password, password_err = validate_account_password(
                                        new_password,
                                        username=user,
                                        email=account_email,
                                        server_name=settings.get("server_name"),
                                    )
                                    if not ok_password:
                                        error = password_err or "New password does not meet account rules."

                            if not error and row:
                                try:
                                    private_key_bytes = _decrypt_private_key_blob(current_password, encrypted_private_key)
                                    new_encrypted_private_key = _encrypt_private_key_v2(private_key_bytes, new_password)
                                except Exception:
                                    logging.exception("Could not re-encrypt private key during password change for %s", user)
                                    error = "Could not re-encrypt your private messages key with the new password."

                            if not error and row and new_encrypted_private_key:
                                cur.execute(
                                    """
                                    UPDATE users
                                       SET password = %s,
                                           encrypted_private_key = %s
                                     WHERE LOWER(username) = LOWER(%s);
                                    """,
                                    (hash_password(new_password), new_encrypted_private_key, user),
                                )
                                # Any old reset link was issued under the previous password.
                                # Consuming unused tokens on manual password change prevents a
                                # stale reset email from being used after the account is secured.
                                cur.execute(
                                    """
                                    UPDATE password_reset_tokens
                                       SET used_at = COALESCE(used_at, CURRENT_TIMESTAMP)
                                     WHERE LOWER(username) = LOWER(%s)
                                       AND used_at IS NULL;
                                    """,
                                    (user,),
                                )

                        if error:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                        else:
                            apply_auth_risk_event(user, "password_change", keep_current_sid=current_sid, revoke_all=False, conn=conn, commit=False)
                            conn.commit()
                    except Exception:
                        logging.exception("Could not store password change for %s", user)
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        error = "Could not update your password right now."

                    if not error:
                        try:
                            log_audit_event(user, "password_change", user, "changed from account security")
                        except Exception:
                            pass
                        try:
                            try:
                                _force_logout_live_sessions(
                                    user,
                                    "Your account password was changed. Please sign in again.",
                                    exclude_auth_session_ids={current_sid} if current_sid else None,
                                    action="password_change",
                                    code="password_change",
                                )
                            except Exception:
                                pass
                            resp = _issue_replacement_auth_cookies(
                                user,
                                current_sid,
                                user_agent=request.headers.get("User-Agent"),
                                ip_address=get_request_ip() or None,
                                redirect_to="/account/security?message=password_changed",
                            )
                            return resp
                        except Exception:
                            logging.exception("Password changed for %s but session/version refresh failed", user)
                            error = "Password changed, but the server could not safely refresh your session. Please sign in again."


            elif action == "revoke_session":
                target_sid = str(request.form.get("session_id") or "").strip()
                if not target_sid or target_sid not in valid_session_ids:
                    error = "That device session could not be found."
                elif current_sid and target_sid == current_sid:
                    try:
                        revoke_auth_session(target_sid, reason="account_security_sign_out")
                    except Exception:
                        pass
                    try:
                        _force_logout_live_sessions(
                            user,
                            "This device was signed out from Account Security.",
                            auth_session_ids={target_sid},
                            action="account_security_sign_out",
                            code="account_security_sign_out",
                        )
                    except Exception:
                        pass
                    resp = make_response(redirect("/login?reason=account_security_sign_out"))
                    resp.headers["Cache-Control"] = "no-store, max-age=0"
                    try:
                        unset_jwt_cookies(resp)
                    except Exception:
                        pass
                    session.clear()
                    return resp
                else:
                    try:
                        revoke_auth_session(target_sid, reason="account_security_sign_out")
                        _force_logout_live_sessions(
                            user,
                            "A device was signed out from Account Security.",
                            auth_session_ids={target_sid},
                            action="account_security_sign_out",
                            code="account_security_sign_out",
                        )
                        message = "That device was signed out."
                    except Exception:
                        logging.exception("Could not revoke session %s for %s", target_sid, user)
                        error = "Could not sign out that device right now."

            elif action == "logout_others":
                if not current_sid:
                    error = "Your current session could not be identified."
                elif int(summary.get("other_active_count") or 0) < 1:
                    message = "No other active sessions were found. This browser stayed signed in."
                else:
                    try:
                        revoked = revoke_other_sessions_for_user(user, keep_session_id=current_sid, reason="account_security_logout_others")
                        if revoked:
                            try:
                                _force_logout_live_sessions(
                                    user,
                                    "Another device chose sign out other sessions.",
                                    exclude_auth_session_ids={current_sid},
                                    action="logout_others",
                                    code="logout_others",
                                )
                            except Exception:
                                pass
                            message = f"Signed out {revoked} other active session(s). This browser stayed signed in."
                        else:
                            message = "No other active sessions were found. This browser stayed signed in."
                    except Exception:
                        logging.exception("Could not revoke other sessions for %s", user)
                        error = "Could not sign out your other sessions right now."

            elif action == "logout_all":
                try:
                    revoked = revoke_all_sessions_for_user(user, reason="account_security_logout_all")
                except Exception:
                    logging.exception("Could not revoke all sessions for %s", user)
                    return _render_account_security(error="Could not sign out all devices right now.", status=500, summary_override=summary)
                if revoked:
                    try:
                        _force_logout_live_sessions(
                            user,
                            "All sessions were signed out from Account Security.",
                            action="logout_all",
                            code="logout_all",
                        )
                    except Exception:
                        pass
                logging.info("Account Security signed out all sessions for %s; revoked_sessions=%s", user, revoked)
                resp = make_response(redirect("/login?reason=all_sessions_signed_out"))
                resp.headers["Cache-Control"] = "no-store, max-age=0"
                try:
                    unset_jwt_cookies(resp)
                except Exception:
                    pass
                session.clear()
                return resp

            summary = _load_summary()

        return _render_account_security(message=message, error=error, summary_override=summary)

    @app.route("/enable-2fa", methods=["GET", "POST"])
    @_limit(settings.get("rate_limit_enable_2fa") or "3 per minute", methods=["POST"])
    @jwt_required()
    def enable_2fa():
        sms_cfg = effective_twilio_settings(settings)
        if not bool(sms_cfg.get("enable_two_factor_beta")):
            resp = make_response("Not found", 404)
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp

        if not bool(sms_cfg.get("enable_sms_two_factor")):
            resp = make_response("SMS 2FA is disabled on this server.", 404)
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp

        user = get_jwt_identity()
        _sid, _session_state, rejection = _require_active_session(user, redirect_on_failure=True)
        if rejection:
            return rejection
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT phone, two_factor_enabled, password FROM users WHERE LOWER(username) = LOWER(%s);",
                    (user,),
                )
                row = cur.fetchone()
        except Exception as e:
            logging.error("DB error in enable_2fa lookup: %s", e)
            resp = make_response("Error", 500)
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp

        if not row:
            resp = make_response("User not found", 404)
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp

        saved_phone = _normalize_phone_e164(decrypt_sensitive_field(row[0] or "", settings, field_name="users.phone"))
        two_factor_enabled = bool(row[1])
        password_hash = row[2]

        enable_challenge = _load_server_challenge("enable_2fa", session.get("enable_2fa_challenge_id")) or {}
        pending_phone = _normalize_phone_e164(enable_challenge.get("phone") or "")
        pending_user = str(enable_challenge.get("username") or session.get("enable_2fa_user") or "").strip().lower()
        pending_started_at = float(enable_challenge.get("started_at") or 0.0)
        pending_auth_version = enable_challenge.get("auth_version")
        pending_active = pending_user == user and pending_phone and pending_started_at
        max_age = int(effective_twilio_settings(settings).get("two_factor_login_timeout_seconds") or 600)
        if pending_active and (datetime.now(timezone.utc).timestamp() - pending_started_at) > max_age:
            _clear_pending_enable_2fa()
            pending_phone = ""
            pending_active = False

        message = None
        error = None

        if request.method == "POST":
            submitted_csrf = request.form.get("csrf_token")
            try:
                validate_csrf(submitted_csrf)
            except ValidationError:
                if not _enable_2fa_csrf_fallback_valid(submitted_csrf):
                    return _render_enable_2fa(
                        enabled=two_factor_enabled,
                        saved_phone=saved_phone,
                        pending_phone=pending_phone,
                        pending_active=bool(pending_active),
                        error="SMS 2FA form expired. Please try again.",
                        status=400,
                    )

            action = (request.form.get("action") or "send").strip().lower()

            if action == "disable":
                current_password = request.form.get("current_password", "")
                ok, upgraded_hash = verify_password_and_upgrade(current_password, password_hash)
                if not ok:
                    error = "Current password is required to disable 2FA."
                else:
                    try:
                        with conn.cursor() as cur:
                            if upgraded_hash:
                                cur.execute("UPDATE users SET password = %s WHERE LOWER(username) = LOWER(%s);", (upgraded_hash, user))
                            cur.execute(
                                "UPDATE users SET two_factor_enabled = FALSE, two_factor_secret = NULL WHERE LOWER(username) = LOWER(%s);",
                                (user,),
                            )
                        apply_auth_risk_event(user, "sms_2fa_disabled", keep_current_sid=_sid, revoke_all=False, conn=conn, commit=False)
                        conn.commit()
                        _clear_pending_enable_2fa()
                        try:
                            _force_logout_live_sessions(
                                user,
                                "SMS 2FA was changed on this account. Please sign in again.",
                                exclude_auth_session_ids={_sid} if _sid else None,
                                action="sms_2fa_disabled",
                                code="sms_2fa_disabled",
                            )
                        except Exception:
                            pass
                        resp = _issue_replacement_auth_cookies(user, _sid, user_agent=request.headers.get("User-Agent"), ip_address=get_request_ip() or None, redirect_to="/account/security?message=2fa_disabled")
                        return resp
                    except Exception as e:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        logging.error("DB error disabling 2FA: %s", e)
                        error = "Could not disable SMS 2FA."

            elif action == "verify":
                ok_enable_verify, retry_enable_verify = _enforce_named_rate_limit(
                    "enable_2fa:verify",
                    user,
                    settings.get("rate_limit_enable_2fa_verify") or "10@600",
                    default_limit=10,
                    default_window=600,
                )
                if not ok_enable_verify:
                    error = "Too many SMS 2FA verification attempts. Try again later."
                code = (request.form.get("code") or "").strip()
                phone = pending_phone
                if error:
                    pass
                elif not phone:
                    error = "Start SMS 2FA setup first."
                elif pending_auth_version is not None and int(pending_auth_version or 0) != int(get_auth_version(user) or 0):
                    _clear_pending_enable_2fa()
                    error = "Your account security state changed. Start SMS 2FA setup again."
                else:
                    ok_check, msg = _check_sms_2fa_code(phone, code)
                    if not ok_check:
                        error = msg
                    else:
                        try:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "UPDATE users SET phone = %s, two_factor_enabled = TRUE, two_factor_secret = NULL WHERE LOWER(username) = LOWER(%s);",
                                    (encrypt_sensitive_field(phone, settings, field_name="users.phone"), user),
                                )
                            apply_auth_risk_event(user, "sms_2fa_enabled", keep_current_sid=_sid, revoke_all=False, conn=conn, commit=False)
                            conn.commit()
                            _clear_pending_enable_2fa()
                            try:
                                _force_logout_live_sessions(
                                    user,
                                    "SMS 2FA was changed on this account. Please sign in again.",
                                    exclude_auth_session_ids={_sid} if _sid else None,
                                    action="sms_2fa_enabled",
                                    code="sms_2fa_enabled",
                                )
                            except Exception:
                                pass
                            resp = _issue_replacement_auth_cookies(user, _sid, user_agent=request.headers.get("User-Agent"), ip_address=get_request_ip() or None, redirect_to="/account/security?message=2fa_enabled")
                            return resp
                        except Exception as e:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            logging.error("DB error enabling SMS 2FA: %s", e)
                            error = "Could not enable SMS 2FA."

            elif action == "resend":
                ok_enable_resend, retry_enable_resend = _enforce_named_rate_limit(
                    "enable_2fa:resend",
                    user,
                    settings.get("rate_limit_enable_2fa_resend") or "3@300",
                    default_limit=3,
                    default_window=300,
                )
                phone = pending_phone
                if not ok_enable_resend:
                    error = "Too many SMS 2FA resend requests. Try again later."
                elif not phone:
                    error = "Start SMS 2FA setup first."
                else:
                    ok_send, msg = _send_sms_2fa_code(phone)
                    if ok_send:
                        cid = _save_server_challenge("enable_2fa", user, phone)
                        session["enable_2fa_challenge_id"] = cid
                        session["enable_2fa_user"] = user
                        message = "We sent a fresh verification code."
                    else:
                        error = msg

            elif action == "send":
                ok_enable_send, retry_enable_send = _enforce_named_rate_limit(
                    "enable_2fa:send",
                    user,
                    settings.get("rate_limit_enable_2fa_send") or "3@300",
                    default_limit=3,
                    default_window=300,
                )
                current_password = request.form.get("current_password", "")
                requested_phone = _normalize_phone_e164(request.form.get("phone") or saved_phone or "")
                if not ok_enable_send:
                    error = "Too many SMS 2FA setup requests. Try again later."
                    ok, upgraded_hash = False, None
                else:
                    ok, upgraded_hash = verify_password_and_upgrade(current_password, password_hash)
                if error:
                    pass
                elif not ok:
                    error = "Current password is required to enable 2FA."
                elif not requested_phone:
                    error = "Enter a phone number in international format like +15551234567."
                elif not _twilio_verify_ready():
                    error = "Twilio Verify is not configured yet on this server."
                else:
                    if upgraded_hash:
                        try:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE users SET password = %s WHERE LOWER(username) = LOWER(%s);", (upgraded_hash, user))
                            conn.commit()
                        except Exception:
                            logging.exception("Could not upgrade password hash while enabling 2FA for %s", user)
                    ok_send, msg = _send_sms_2fa_code(requested_phone)
                    if ok_send:
                        cid = _save_server_challenge("enable_2fa", user, requested_phone)
                        session["enable_2fa_challenge_id"] = cid
                        session["enable_2fa_user"] = user
                        pending_phone = requested_phone
                        pending_active = True
                        message = f"We sent a verification code to {_mask_phone(requested_phone)}."
                    else:
                        error = msg

            else:
                error = "Choose a valid SMS 2FA action."

        return _render_enable_2fa(
            enabled=two_factor_enabled,
            saved_phone=saved_phone,
            pending_phone=pending_phone,
            pending_active=bool(pending_active),
            message=message,
            error=error,
        )

    @app.route("/get_public_key", methods=["GET"])
    @_limit(settings.get("rate_limit_public_key") or "120 per minute")
    @jwt_required()
    def get_public_key():
        requester = str(get_jwt_identity() or "").strip().lower()
        _sid, _state, rejection = _require_active_session(requester, touch_seen=True)
        if rejection:
            return rejection

        target_raw = request.args.get("username", "").strip()
        key_scope = str(request.args.get("scope") or request.args.get("context") or "").strip().lower()
        room_key_scope = key_scope in {"room", "room_chat", "room_e2ee"}
        if not target_raw:
            return _public_key_json({"ok": False, "error": "username_required"}, status=400)
        if len(target_raw) > 64 or any(ch in target_raw for ch in "\r\n\t\x00"):
            return _public_key_json({"ok": False, "error": "invalid_username"}, status=400)

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, public_key FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;",
                (target_raw,),
            )
            row = cur.fetchone()

        if not row:
            return _public_key_json({"ok": False, "error": "user_not_found"}, status=404)

        target, public_pem = row[0], row[1]

        target_status = get_effective_account_status(target)
        if target_status in {"suspended", "deactivated"}:
            return _public_key_json({"ok": False, "error": "target_not_active", "account_status": target_status}, status=403)

        # E2EE key discovery has two block models:
        # - direct/private contexts (PM, files, groups) are hard pairwise blocks;
        #   either direction denies key lookup.
        # - room chat is viewer-side: if I block you, I should not receive your
        #   room messages, but you can still receive mine while we both remain
        #   visible in the room roster.  Therefore room-scope lookup only denies
        #   when the *target* has blocked the requester.
        if requester and str(requester).strip().lower() != str(target).strip().lower():
            with conn.cursor() as cur:
                if room_key_scope:
                    cur.execute(
                        """
                        SELECT 1
                          FROM blocks
                         WHERE LOWER(blocker)=LOWER(%s)
                           AND LOWER(blocked)=LOWER(%s)
                         LIMIT 1;
                        """,
                        (target, requester),
                    )
                else:
                    cur.execute(
                        """
                        SELECT 1
                          FROM blocks
                         WHERE (LOWER(blocker)=LOWER(%s) AND LOWER(blocked)=LOWER(%s))
                            OR (LOWER(blocker)=LOWER(%s) AND LOWER(blocked)=LOWER(%s))
                         LIMIT 1;
                        """,
                        (requester, target, target, requester),
                    )
                if cur.fetchone():
                    return _public_key_json({"ok": False, "error": "blocked"}, status=403)

        if not _valid_public_key_pem(public_pem):
            return _public_key_json({"ok": False, "error": "no_public_key"}, status=409)
        return _public_key_json({"ok": True, "public_key": str(public_pem).strip(), "username": target}, status=200)
