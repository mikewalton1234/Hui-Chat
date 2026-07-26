#!/usr/bin/env python3
"""security.py

Utility functions for password hashing and audit logging.

2026-02-15 hardening:
  - New hashes: Argon2id (argon2-cffi)
  - Back-compat: verify legacy PBKDF2 hashes (salt_hex:hash_b64)
  - Upgrade path: verify_password_and_upgrade() returns a new Argon2id hash

We reuse these helpers for:
  - user passwords (users.password)
  - recovery PIN hashes (users.recovery_pin_hash)
  - admin password hash stored in server_config.json (admin_pass)
"""

from __future__ import annotations

import os
import base64
import hmac
import getpass
import logging
import re
import ipaddress
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional, Tuple

from flask import current_app, has_request_context, request

try:
    import redis as _redis_mod  # type: ignore
except Exception:  # pragma: no cover - optional production dependency
    _redis_mod = None

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

from database import get_db

try:
    from privacy_retention import redact_testlab_token_text
except Exception:  # pragma: no cover - keep security helpers import-safe
    def redact_testlab_token_text(text):
        return str(text or "")

# ────────────────────────────────────────────────────────────
# Audit logging
# ────────────────────────────────────────────────────────────

def log_audit_event(actor: str, action: str, target: str | None = None, details: str | None = None) -> None:
    """Insert an audit log entry into the audit_log table."""
    try:
        target = redact_testlab_token_text(target) if target is not None else None
        details = redact_testlab_token_text(details) if details is not None else None
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log (actor, action, target, details)
                VALUES (%s, %s, %s, %s);
                """,
                (actor, action, target, details),
            )
        conn.commit()
    except Exception as e:
        logging.error("Failed to write audit log (%s, %s, %s, %s): %s", actor, action, target, details, e)


# ────────────────────────────────────────────────────────────
# User-visible text sanitization
# ────────────────────────────────────────────────────────────

_USER_VISIBLE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_USER_VISIBLE_BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def sanitize_user_visible_text(raw, *, max_len: int = 2000, keep_newlines: bool = True) -> str:
    """Normalize text before it is stored or relayed back to browsers.

    This is a defensive server-side guard for public/chat/profile text.  It does
    not replace output escaping in the UI; it removes dangerous control and bidi
    override characters that can hide markup/scripts in logs, moderation tools,
    and legacy clients.  Encrypted ciphertext should not be passed through this
    helper.
    """
    try:
        limit = int(max_len)
    except Exception:
        limit = 2000
    limit = max(1, min(limit, 100000))
    text = str(raw or "")
    if not keep_newlines:
        text = text.replace("\r", " ").replace("\n", " ")
    else:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _USER_VISIBLE_CONTROL_RE.sub("", text)
    text = _USER_VISIBLE_BIDI_RE.sub("", text)
    return text.strip()[:limit]


# ────────────────────────────────────────────────────────────
# Safe download response headers
# ────────────────────────────────────────────────────────────

def apply_safe_download_headers(resp, *, csp: str = "sandbox; default-src 'none';", private: bool = True):
    """Apply defense-in-depth headers to user-supplied download responses.

    Private attachments, encrypted blobs, and uploaded .torrent files must never
    be rendered as active browser content. Routes still decide authorization and
    ``send_file(..., as_attachment=True)`` semantics; this helper centralizes the
    headers that make those downloads non-cacheable, same-origin only, and inert
    if a browser or proxy tries to sniff them.
    """
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers.setdefault("Content-Security-Policy", csp)
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if private:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    else:
        resp.headers.setdefault("Cache-Control", "no-store, max-age=0")
    return resp


# ────────────────────────────────────────────────────────────
# Password hashing utilities
# ────────────────────────────────────────────────────────────

# Legacy PBKDF2 parameters (kept only for verifying old hashes)
_LEGACY_PBKDF2_ITERS = 100_000
_LEGACY_PBKDF2_LEN = 32


def _pbkdf2_legacy(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from password+salt using legacy PBKDF2-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_LEGACY_PBKDF2_LEN,
        salt=salt,
        iterations=_LEGACY_PBKDF2_ITERS,
        backend=default_backend(),
    )
    return kdf.derive(password.encode("utf-8"))


def _is_legacy_pbkdf2_hash(stored_hash: str) -> bool:
    # Expected: <32 hex chars>:<base64...>
    if not stored_hash or ":" not in stored_hash:
        return False
    left, right = stored_hash.split(":", 1)
    if len(left) != 32:
        return False
    try:
        bytes.fromhex(left)
    except Exception:
        return False
    return bool(right)


def _verify_legacy_pbkdf2(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, hashed_b64 = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        new_hash = base64.urlsafe_b64encode(_pbkdf2_legacy(password, salt)).decode("utf-8")
        return hmac.compare_digest(new_hash, hashed_b64)
    except Exception:
        return False


# Argon2id (preferred)
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

    _PWH = PasswordHasher(
        time_cost=3,
        memory_cost=65536,  # KiB (64 MiB)
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )
except Exception:
    PasswordHasher = None  # type: ignore
    _PWH = None


def _is_argon2_hash(stored_hash: str) -> bool:
    return bool(stored_hash) and stored_hash.startswith("$argon2")


def hash_password(password: str) -> str:
    """Hash plaintext password using Argon2id (preferred).

    Falls back to PBKDF2 if argon2-cffi isn't installed.
    """
    if _PWH is None:
        # Fallback (dev only): keep legacy format
        salt = os.urandom(16)
        hashed = base64.urlsafe_b64encode(_pbkdf2_legacy(password, salt)).decode("utf-8")
        return f"{salt.hex()}:{hashed}"
    return _PWH.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash (Argon2id or legacy PBKDF2)."""
    ok, _ = verify_password_and_upgrade(password, stored_hash)
    return ok


def verify_password_and_upgrade(password: str, stored_hash: str) -> Tuple[bool, Optional[str]]:
    """Verify password, and if the stored hash is legacy (or needs rehash),
    return a new Argon2id hash for upgrade.

    Returns: (ok, upgraded_hash_or_None)
    """
    if not stored_hash:
        return False, None

    # Argon2 path
    if _is_argon2_hash(stored_hash) and _PWH is not None:
        try:
            _PWH.verify(stored_hash, password)
            if _PWH.check_needs_rehash(stored_hash):
                return True, _PWH.hash(password)
            return True, None
        except (VerifyMismatchError, VerificationError, InvalidHash):
            return False, None
        except Exception:
            return False, None

    # Legacy PBKDF2 path
    if _is_legacy_pbkdf2_hash(stored_hash):
        ok = _verify_legacy_pbkdf2(password, stored_hash)
        if ok and _PWH is not None:
            return True, _PWH.hash(password)
        return ok, None

    # Unknown format
    return False, None


def get_admin_password(prompt_text: str = "Enter password: ") -> str:
    """Secure prompt for passwords (CLI tools/setup)."""
    return getpass.getpass(prompt_text)


# ────────────────────────────────────────────────────────────
# Small in-process rate limiter (dev-safe; use Redis-backed limiter in prod)
# ────────────────────────────────────────────────────────────
#
# We use this as a centralized guardrail for broad path prefixes (e.g. /admin/*)
# to avoid missing new endpoints accidentally. It is NOT a replacement for
# Flask-Limiter with a shared storage backend in production.

import time
import threading
from collections import deque

_SRL_BUCKETS: dict[str, deque] = {}
_SRL_LOCK = threading.Lock()
_SRL_REDIS_CLIENT = None
_SRL_REDIS_URL: str | None = None
_SRL_REDIS_CONFIG: tuple[float, float, int] | None = None


def _simple_rate_limit_redis_url() -> str:
    if not has_request_context():
        return ''
    try:
        url = str(current_app.config.get('HUI_SIMPLE_RATE_LIMIT_REDIS_URL') or '').strip()
    except Exception:
        url = ''
    return url if url.startswith(('redis://', 'rediss://')) else ''


def _simple_rate_limit_redis_client(url: str):
    global _SRL_REDIS_CLIENT, _SRL_REDIS_URL, _SRL_REDIS_CONFIG
    if not url or _redis_mod is None:
        return None
    with _SRL_LOCK:
        redis_config = (
            float(current_app.config.get("HUI_REDIS_CONNECT_TIMEOUT_SECONDS") or 1.0),
            float(current_app.config.get("HUI_REDIS_SOCKET_TIMEOUT_SECONDS") or 1.0),
            int(current_app.config.get("HUI_REDIS_HEALTH_CHECK_INTERVAL_SECONDS") or 30),
        )
        if _SRL_REDIS_CLIENT is not None and _SRL_REDIS_URL == url and _SRL_REDIS_CONFIG == redis_config:
            return _SRL_REDIS_CLIENT
        try:
            _SRL_REDIS_CLIENT = _redis_mod.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=redis_config[0],
                socket_timeout=redis_config[1],
                health_check_interval=redis_config[2],
            )
            _SRL_REDIS_CLIENT.ping()
            _SRL_REDIS_URL = url
            _SRL_REDIS_CONFIG = redis_config
            return _SRL_REDIS_CLIENT
        except Exception as exc:
            logging.warning('Redis simple rate limiter unavailable; falling back to process-local buckets: %s', exc)
            _SRL_REDIS_CLIENT = None
            _SRL_REDIS_URL = None
            _SRL_REDIS_CONFIG = None
            return None


def _simple_rate_limit_redis(key: str, limit: int, window_sec: int, now: float) -> tuple[bool, float] | None:
    url = _simple_rate_limit_redis_url()
    client = _simple_rate_limit_redis_client(url) if url else None
    if client is None:
        return None
    redis_key = f"hui:srl:{key}"
    member = f"{now:.6f}:{os.getpid()}:{threading.get_ident()}"
    cutoff = now - window_sec
    try:
        pipe = client.pipeline(transaction=True)
        pipe.zremrangebyscore(redis_key, 0, cutoff)
        pipe.zcard(redis_key)
        pipe.zrange(redis_key, 0, 0, withscores=True)
        removed, count, oldest = pipe.execute()
        count = int(count or 0)
        if count >= limit:
            oldest_score = float(oldest[0][1]) if oldest else now
            retry = (oldest_score + window_sec) - now
            client.expire(redis_key, max(1, int(window_sec * 2)))
            return False, max(0.0, float(retry))
        pipe = client.pipeline(transaction=True)
        pipe.zadd(redis_key, {member: now})
        pipe.expire(redis_key, max(1, int(window_sec * 2)))
        pipe.execute()
        return True, 0.0
    except Exception as exc:
        logging.warning('Redis simple rate limiter failed; falling back to process-local bucket for this event: %s', exc)
        return None


def simple_rate_limit(key: str, limit: int, window_sec: int) -> tuple[bool, float]:
    """Sliding-window limiter.

    Returns (ok, retry_after_seconds).  When the Flask app provides a Redis rate
    storage URI, this guardrail uses Redis too so broad admin/API/Socket.IO
    buckets are shared across multiple Hui Chat instances.
    """
    try:
        limit = int(limit)
    except Exception:
        limit = 0
    try:
        window_sec = int(window_sec)
    except Exception:
        window_sec = 0

    if limit <= 0 or window_sec <= 0:
        return True, 0.0

    now = time.time()
    redis_result = _simple_rate_limit_redis(str(key), limit, window_sec, now)
    if redis_result is not None:
        return redis_result

    with _SRL_LOCK:
        dq = _SRL_BUCKETS.get(key)
        if dq is None:
            dq = deque()
            _SRL_BUCKETS[key] = dq
        cutoff = now - window_sec
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry = (dq[0] + window_sec) - now
            return False, max(0.0, float(retry))
        dq.append(now)
        return True, 0.0


def simple_rate_limit_clear(*, prefixes=None, keys=None) -> int:
    """Clear selected in-process simple-rate-limit buckets.

    This is intentionally narrow and is mainly used by Admin Test Lab cleanup.
    Test Lab creates many local Socket.IO test clients in one burst; without a
    cleanup hook those synthetic connect attempts can leave real browser tabs
    stuck in reconnect/rate-limit loops until the sliding window expires.
    """
    prefix_tuple = tuple(str(p or "") for p in (prefixes or []) if str(p or ""))
    key_set = {str(k or "") for k in (keys or []) if str(k or "")}
    removed = 0
    with _SRL_LOCK:
        for key in list(_SRL_BUCKETS.keys()):
            if key in key_set or (prefix_tuple and any(key.startswith(prefix) for prefix in prefix_tuple)):
                _SRL_BUCKETS.pop(key, None)
                removed += 1
    return removed


def parse_rate_limit_value(val, default_limit: int = 0, default_window: int = 0) -> tuple[int, int]:
    """Parse either an int or a human string like '10 per minute' or '30@10'."""
    if val is None:
        return int(default_limit), int(default_window)
    if isinstance(val, (int, float)):
        lim = int(val)
        return (lim if lim > 0 else int(default_limit)), (int(default_window) or 60)
    if isinstance(val, str):
        s = val.strip().lower()
        import re
        m = re.match(r"^(\d+)\s*@\s*(\d+)$", s)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.match(r"^(\d+)\s*(?:per\s*)?(second|sec|minute|min|hour|day)s?$", s)
        if m:
            lim = int(m.group(1))
            unit = m.group(2)
            win = 1 if unit in ('second', 'sec') else 60 if unit in ('minute', 'min') else 3600 if unit == 'hour' else 86400
            return lim, win
        m = re.match(r"^(\d+)\s*/\s*(sec|second|min|minute|hour|day)s?$", s)
        if m:
            lim = int(m.group(1))
            unit = m.group(2)
            win = 1 if unit in ('sec', 'second') else 60 if unit in ('min', 'minute') else 3600 if unit == 'hour' else 86400
            return lim, win
    return int(default_limit), int(default_window)




def safe_existing_file_under(root: str | os.PathLike, candidate: str | os.PathLike) -> str | None:
    """Return a resolved existing file path only when it stays under ``root``.

    Private download endpoints store disk paths in the database.  Re-checking
    those paths at read time prevents a stale/corrupted row, symlink, or legacy
    attachment record from turning a member-only download route into an
    arbitrary local-file read.
    """
    try:
        root_path = Path(root).expanduser().resolve()
        raw_candidate = Path(candidate)
        if not raw_candidate.is_absolute():
            raw_candidate = root_path / raw_candidate
        resolved = raw_candidate.expanduser().resolve()
        resolved.relative_to(root_path)
        if not resolved.is_file():
            return None
        return str(resolved)
    except Exception:
        return None

# ────────────────────────────────────────────────────────────
# Request/network helpers
# ────────────────────────────────────────────────────────────

def _clean_ip(value: str | None) -> str:
    raw = str(value or '').strip().strip('[]')
    if not raw:
        return ''
    if ':' in raw and raw.count(':') == 1 and '.' in raw.split(':', 1)[0] and raw.rsplit(':', 1)[1].isdigit():
        raw = raw.rsplit(':', 1)[0]
    try:
        return str(ipaddress.ip_address(raw))
    except Exception:
        return ''


def _forwarded_for_ip(header_value: str | None) -> str:
    raw = str(header_value or '').strip()
    if not raw:
        return ''
    first = raw.split(',', 1)[0].strip()
    return _clean_ip(first)


def trust_proxy_headers_enabled() -> bool:
    if not has_request_context():
        return False
    try:
        return bool(current_app.config.get('HUI_TRUST_PROXY_HEADERS', False))
    except Exception:
        return False


def get_request_ip(req=None) -> str:
    req = req or request
    # When proxy trust is enabled, server_init.py applies Werkzeug ProxyFix with
    # the configured trusted-hop count.  Use the already-normalized remote_addr
    # instead of re-reading raw X-Forwarded-For/Forwarded headers here; raw client
    # headers are spoofable when a request can reach the app directly.
    direct = _clean_ip(getattr(req, 'remote_addr', None))
    if direct:
        return direct
    if not trust_proxy_headers_enabled():
        forwarded = _forwarded_for_ip(req.headers.get('X-Forwarded-For'))
        if forwarded:
            return forwarded
    return 'unknown'


def is_loopback_ip(ip: str | None) -> bool:
    try:
        return ipaddress.ip_address(str(ip or '').strip()).is_loopback
    except Exception:
        return False


def is_localish_ip(ip: str | None) -> bool:
    try:
        addr = ipaddress.ip_address(str(ip or '').strip())
        return bool(addr.is_loopback or addr.is_private or addr.is_link_local)
    except Exception:
        return False


def is_local_request(req=None) -> bool:
    return is_loopback_ip(get_request_ip(req))


def is_localish_request(req=None) -> bool:
    return is_localish_ip(get_request_ip(req))


def _origin_from_url(value: str | None) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    try:
        parsed = urlparse(raw)
        scheme = (parsed.scheme or '').lower()
        netloc = (parsed.netloc or '').lower()
        if scheme and netloc:
            return f"{scheme}://{netloc}"
    except Exception:
        return ''
    return ''


def request_is_same_origin(req=None, *, allow_missing: bool = True) -> tuple[bool, str]:
    req = req or request
    try:
        host_origin = _origin_from_url(getattr(req, 'host_url', None))
    except Exception:
        host_origin = ''

    sec_fetch_site = str(req.headers.get('Sec-Fetch-Site') or '').strip().lower()
    if sec_fetch_site == 'cross-site':
        return False, 'sec_fetch_site'

    origin = _origin_from_url(req.headers.get('Origin'))
    if origin:
        if host_origin and origin != host_origin:
            return False, 'origin'
        return True, 'origin'

    referer = _origin_from_url(req.headers.get('Referer'))
    if referer:
        if host_origin and referer != host_origin:
            return False, 'referer'
        return True, 'referer'

    return (True, 'missing') if allow_missing else (False, 'missing')


def request_has_valid_double_submit_csrf(
    req=None,
    *,
    header_names: tuple[str, ...] = ('X-CSRF-TOKEN', 'X-CSRFToken'),
    form_names: tuple[str, ...] = ('csrf_token', '_csrf_token', 'csrf'),
    cookie_names: tuple[str, ...] = ('csrf_access_token', 'csrf_refresh_token'),
) -> bool:
    """Validate JWT double-submit CSRF for fetch/XHR and plain HTML forms.

    JavaScript callers send the token in X-CSRF-TOKEN. Browser logout
    confirmations can submit the same readable JWT CSRF cookie value as a
    hidden form field. Cross-site forms cannot read that cookie, so the form
    value still has to match one of the Hui Chat CSRF cookies exactly.
    """
    req = req or request
    submitted_values: list[str] = []
    for name in header_names:
        val = str(req.headers.get(name) or '').strip()
        if val:
            submitted_values.append(val)
            break
    if not submitted_values:
        try:
            form = getattr(req, 'form', None)
            for name in form_names:
                val = str(form.get(name) or '').strip() if form is not None else ''
                if val:
                    submitted_values.append(val)
                    break
        except Exception:
            pass
    if not submitted_values:
        return False
    for submitted in submitted_values:
        for cookie_name in cookie_names:
            cookie_val = str(req.cookies.get(cookie_name) or '').strip()
            if cookie_val and hmac.compare_digest(submitted, cookie_val):
                return True
    return False
