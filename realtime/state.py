"""Shared runtime state for EchoChat Socket.IO handlers.

This module keeps the legacy in-process registries for compatibility while also
optionally projecting the important presence / room-membership state into Redis
so multiple workers can agree on who is online and which room they are in.
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import quote

try:  # pragma: no cover - optional at import time
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None

# Shared in-memory state
_SEND_HISTORY = {}
CONNECTED_USERS: dict[str, dict] = {}
TYPING_STATUS: dict[str, float] = {}
TYPING_EXPIRY_SECONDS = 5

CONNECTED_USERS_LOCK = threading.Lock()
TYPING_STATUS_LOCK = threading.Lock()

# WebRTC P2P file transfer sessions
P2P_FILE_SESSIONS: dict[str, dict] = {}
# Recently closed/expired transfer IDs are kept briefly so a stale or malicious
# client cannot immediately recycle an ID after cleanup and confuse late ICE,
# answer, or decline events from the previous transfer.
P2P_FILE_RECENT_TRANSFER_IDS: dict[str, float] = {}
P2P_FILE_SESSIONS_LOCK = threading.Lock()

# 1:1 voice call sessions (DM-like)
VOICE_DM_SESSIONS: dict[str, dict] = {}
VOICE_DM_SESSIONS_LOCK = threading.Lock()

# Live room messages and reactions — in-memory only.
#
# Room chat is intentionally live-only, so room reactions must be tied to the
# server-issued live message ids that are currently known to this worker.  Do
# not fall back to the legacy persisted messages table here: that table is
# shared by older/group flows and would let modified clients react to stale or
# forged ids that were never visible in the active room.
ROOM_LIVE_MESSAGES: dict[str, dict] = {}
ROOM_LIVE_MESSAGES_LOCK = threading.Lock()
ROOM_LIVE_MESSAGE_TTL_SECONDS = 6 * 60 * 60
ROOM_LIVE_MESSAGE_MAX = 5000

MESSAGE_REACTIONS: dict[str, dict] = {}
MESSAGE_REACTIONS_LOCK = threading.Lock()

# Live room message pins — in-memory only, keyed by canonical room name.
# Room chat has no persistent history, so pins are intentionally tied to the
# current live-message registry and are dropped when the underlying live
# message expires or leaves the worker cache.
ROOM_PINNED_MESSAGES: dict[str, dict] = {}
ROOM_PINNED_MESSAGES_LOCK = threading.Lock()

# Live room polls are intentionally ephemeral, matching room chat. There is no
# room-history-backed poll persistence here; if a future poll-creation UI seeds
# this registry, vote/get-active flows will validate against it.
ROOM_ACTIVE_POLLS: dict[str, dict] = {}
ROOM_ACTIVE_POLLS_LOCK = threading.Lock()

# Voice chat room roster — in-memory
VOICE_ROOMS: dict[str, set[str]] = {}
VOICE_ROOMS_LOCK = threading.Lock()

# Echo webcam webcam owner/viewer permissions — in-memory. The browser publisher
# applies this state to Echo webcam track-subscription permissions so camera tracks
# are blocked by the Echo webcam server unless EchoChat has approved the viewer.
WEBCAM_PERMISSIONS: dict[str, dict[str, dict[str, object]]] = {}
WEBCAM_PERMISSIONS_LOCK = threading.RLock()

# Room media presence for GUI indicators.  This is intentionally non-secret and
# short-lived/in-process for the active GUI: it lets clients show whether a
# user currently has voice, webcam, or both enabled in the active room.
ROOM_MEDIA_STATUS: dict[str, dict[str, dict[str, bool]]] = {}
ROOM_MEDIA_STATUS_LOCK = threading.RLock()


def media_status_update(room: str, username: str, *, voice_on=None, webcam_on=None) -> dict:
    room = str(room or '').strip()
    username = str(username or '').strip()
    if not room or not username:
        return {}
    with ROOM_MEDIA_STATUS_LOCK:
        per_room = ROOM_MEDIA_STATUS.setdefault(room, {})
        cur = dict(per_room.get(username) or {'voice_on': False, 'webcam_on': False})
        if voice_on is not None:
            cur['voice_on'] = bool(voice_on)
        if webcam_on is not None:
            cur['webcam_on'] = bool(webcam_on)
        if not cur.get('voice_on') and not cur.get('webcam_on'):
            per_room.pop(username, None)
        else:
            per_room[username] = cur
        if not per_room:
            ROOM_MEDIA_STATUS.pop(room, None)
        return {'voice_on': bool(cur.get('voice_on')), 'webcam_on': bool(cur.get('webcam_on'))}


def media_status_for_room(room: str) -> dict[str, dict[str, bool]]:
    room = str(room or '').strip()
    if not room:
        return {}
    with ROOM_MEDIA_STATUS_LOCK:
        return {u: {'voice_on': bool(st.get('voice_on')), 'webcam_on': bool(st.get('webcam_on'))} for u, st in (ROOM_MEDIA_STATUS.get(room) or {}).items()}


def media_status_clear_user(username: str, room: str | None = None) -> None:
    username = str(username or '').strip()
    if not username:
        return
    with ROOM_MEDIA_STATUS_LOCK:
        rooms = [str(room).strip()] if room else list(ROOM_MEDIA_STATUS.keys())
        for r in rooms:
            if not r:
                continue
            per_room = ROOM_MEDIA_STATUS.get(r)
            if not per_room:
                continue
            per_room.pop(username, None)
            if not per_room:
                ROOM_MEDIA_STATUS.pop(r, None)

# Simple anti-spam for voice call invites (per-socket)
VOICE_INVITE_LAST: dict[str, float] = {}  # sid -> epoch

# Default allowed reactions
ALLOWED_REACTION_EMOJIS = {"👍", "👎", "😂", "❤️", "😮", "😢", "😡"}

# Room slowmode cache shared by HTTP admin routes and Socket.IO message handlers.
# Keeping this in realtime.state prevents stale enforcement after an admin changes
# slowmode from the Admin Panel.
ROOM_SLOWMODE_CACHE: dict[str, tuple[int, float]] = {}
ROOM_SLOWMODE_CACHE_LOCK = threading.Lock()

def set_room_slowmode_cache(room: str, seconds: int) -> None:
    """Update the process-local slowmode cache immediately after admin changes."""
    room = str(room or "").strip()
    if not room:
        return
    try:
        sec = max(0, min(int(seconds), 3600))
    except Exception:
        sec = 0
    with ROOM_SLOWMODE_CACHE_LOCK:
        ROOM_SLOWMODE_CACHE[room] = (sec, time.time())

def clear_room_slowmode_cache(room: str | None = None) -> None:
    """Clear one room's slowmode cache entry, or all entries when room is omitted."""
    with ROOM_SLOWMODE_CACHE_LOCK:
        if room is None:
            ROOM_SLOWMODE_CACHE.clear()
        else:
            ROOM_SLOWMODE_CACHE.pop(str(room or "").strip(), None)

_SHARED_STATE_LOCK = threading.Lock()
_SHARED_STATE_CLIENT = None
_SHARED_STATE_URL: str | None = None
_SHARED_STATE_PREFIX = "echochat"
_SHARED_STATE_SESSION_TTL_SECONDS = 300


def _safe_token(value: str) -> str:
    return quote(str(value or ""), safe="")


def _state_key(*parts: str) -> str:
    clean = [_safe_token(p) for p in parts]
    return f"{_SHARED_STATE_PREFIX}:" + ":".join(clean)


def _sid_key(sid: str) -> str:
    return _state_key("presence", "sid", sid)


def _user_sids_key(username: str) -> str:
    return _state_key("presence", "user_sids", username)


def _online_users_key() -> str:
    return _state_key("presence", "online_users")


def _active_rooms_key() -> str:
    return _state_key("rooms", "active")


def _room_users_key(room: str) -> str:
    return _state_key("room", "users", room)


def _room_user_sids_key(room: str, username: str) -> str:
    return _state_key("room", "user_sids", room, username)


def configure_shared_state(settings: dict | None = None) -> bool:
    """Configure optional Redis-backed shared presence/room state."""
    global _SHARED_STATE_CLIENT, _SHARED_STATE_URL, _SHARED_STATE_PREFIX, _SHARED_STATE_SESSION_TTL_SECONDS

    settings = settings or {}
    prefix = (
        os.environ.get("ECHOCHAT_SHARED_STATE_PREFIX")
        or settings.get("shared_state_prefix")
        or "echochat"
    )
    ttl_raw = (
        os.environ.get("ECHOCHAT_SHARED_STATE_SESSION_TTL")
        or settings.get("shared_state_session_ttl_seconds")
        or 300
    )
    try:
        ttl = max(60, int(ttl_raw))
    except Exception:
        ttl = 300

    # Shared realtime state intentionally requires its own explicit Redis URL.
    # Do not silently reuse the Socket.IO pub/sub DB or generic REDIS_URL; Echo-Chat
    # keeps Redis DB 0/1/2 separated for rate limits, Socket.IO, and shared state.
    url = (
        os.environ.get("ECHOCHAT_SHARED_STATE_REDIS_URL")
        or os.environ.get("SHARED_STATE_REDIS_URL")
        or settings.get("shared_state_redis_url")
        or ""
    ).strip()

    with _SHARED_STATE_LOCK:
        _SHARED_STATE_PREFIX = str(prefix or "echochat").strip() or "echochat"
        _SHARED_STATE_SESSION_TTL_SECONDS = ttl

        if not url or redis is None:
            _SHARED_STATE_CLIENT = None
            _SHARED_STATE_URL = None
            return False

        try:
            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
                health_check_interval=30,
            )
            client.ping()
            _SHARED_STATE_CLIENT = client
            _SHARED_STATE_URL = url
            return True
        except Exception:
            _SHARED_STATE_CLIENT = None
            _SHARED_STATE_URL = None
            return False


def shared_state_enabled() -> bool:
    return _SHARED_STATE_CLIENT is not None


def shared_state_summary() -> dict:
    return {
        "enabled": shared_state_enabled(),
        "url": _SHARED_STATE_URL,
        "prefix": _SHARED_STATE_PREFIX,
        "session_ttl_seconds": _SHARED_STATE_SESSION_TTL_SECONDS,
    }


def _redis_client():
    return _SHARED_STATE_CLIENT




# ---------------------------------------------------------------------------
# Shared voice state helpers
# ---------------------------------------------------------------------------
# DM voice and room voice are still safe in one-process development mode, but
# they must not rely only on process-local dictionaries once multiple workers or
# multiple one-worker instances are used.  When shared_state_redis_url is set and
# Redis is reachable, these helpers mirror/read the voice state from Redis so any
# worker can validate calls and rosters consistently.

def _voice_dm_session_key(call_id: str) -> str:
    return _state_key("voice", "dm_session", call_id)


def _voice_dm_sessions_key() -> str:
    return _state_key("voice", "dm_sessions")


def _voice_room_users_key(room: str) -> str:
    return _state_key("voice", "room_users", room)


def _coerce_voice_dm_session(data) -> dict | None:
    if not data:
        return None
    caller = str(data.get("caller") or "").strip()
    callee = str(data.get("callee") or "").strip()
    state = str(data.get("state") or "").strip()
    if not caller or not callee or not state:
        return None
    out = {"caller": caller, "callee": callee, "state": state}
    for key in ("created", "updated"):
        try:
            out[key] = float(data.get(key) or 0)
        except Exception:
            out[key] = 0.0
    return out


def voice_dm_session_get(call_id: str) -> dict | None:
    call_id = str(call_id or "").strip()
    if not call_id:
        return None
    client = _redis_client()
    if client is not None:
        try:
            sess = _coerce_voice_dm_session(client.hgetall(_voice_dm_session_key(call_id)) or {})
            if sess:
                return sess
            try:
                client.srem(_voice_dm_sessions_key(), call_id)
            except Exception:
                pass
            return None
        except Exception:
            return None
    with VOICE_DM_SESSIONS_LOCK:
        sess = VOICE_DM_SESSIONS.get(call_id)
        return dict(sess) if sess else None


def voice_dm_session_set(call_id: str, session: dict, *, ttl_seconds: int | float = 3600) -> bool:
    call_id = str(call_id or "").strip()
    if not call_id:
        return False
    sess = _coerce_voice_dm_session(session or {})
    if not sess:
        return False
    ttl = max(60, int(float(ttl_seconds or 3600)))
    with VOICE_DM_SESSIONS_LOCK:
        VOICE_DM_SESSIONS[call_id] = dict(sess)
    client = _redis_client()
    if client is not None:
        try:
            client.hset(_voice_dm_session_key(call_id), mapping={k: str(v) for k, v in sess.items()})
            client.expire(_voice_dm_session_key(call_id), ttl)
            client.sadd(_voice_dm_sessions_key(), call_id)
            client.expire(_voice_dm_sessions_key(), max(ttl, _SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
        except Exception:
            return False
    return True


def voice_dm_session_delete(call_id: str) -> bool:
    call_id = str(call_id or "").strip()
    if not call_id:
        return False
    removed = False
    with VOICE_DM_SESSIONS_LOCK:
        removed = call_id in VOICE_DM_SESSIONS
        VOICE_DM_SESSIONS.pop(call_id, None)
    client = _redis_client()
    if client is not None:
        try:
            client.delete(_voice_dm_session_key(call_id))
            client.srem(_voice_dm_sessions_key(), call_id)
            removed = True
        except Exception:
            pass
    return removed


def voice_dm_session_items() -> list[tuple[str, dict]]:
    client = _redis_client()
    if client is not None:
        try:
            ids = sorted(list(client.smembers(_voice_dm_sessions_key()) or []))
        except Exception:
            ids = []
        out: list[tuple[str, dict]] = []
        stale: list[str] = []
        for call_id in ids:
            try:
                sess = _coerce_voice_dm_session(client.hgetall(_voice_dm_session_key(call_id)) or {})
            except Exception:
                sess = None
            if sess:
                out.append((call_id, sess))
            else:
                stale.append(call_id)
        if stale:
            try:
                client.srem(_voice_dm_sessions_key(), *stale)
            except Exception:
                pass
        return out
    with VOICE_DM_SESSIONS_LOCK:
        return [(cid, dict(sess)) for cid, sess in VOICE_DM_SESSIONS.items()]


def voice_room_users_shared(room: str) -> list[str]:
    room = str(room or "").strip()
    if not room:
        return []
    client = _redis_client()
    if client is not None:
        try:
            return sorted([str(u).strip() for u in (client.smembers(_voice_room_users_key(room)) or []) if str(u).strip()])
        except Exception:
            return []
    with VOICE_ROOMS_LOCK:
        return sorted(list(VOICE_ROOMS.get(room) or set()))


def voice_room_add_shared(room: str, username: str, *, max_peers: int = 0) -> tuple[bool, str | None, list[str]]:
    room = str(room or "").strip()
    username = str(username or "").strip()
    if not room or not username:
        return False, "Missing room/user", []
    max_peers = max(0, int(max_peers or 0))
    client = _redis_client()
    if client is not None:
        key = _voice_room_users_key(room)
        try:
            users = set(str(u).strip() for u in (client.smembers(key) or []) if str(u).strip())
            if username in users:
                client.expire(key, max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
                return True, None, sorted(users)
            if max_peers > 0 and len(users) >= max_peers:
                return False, "Voice room is full.", sorted(users)
            client.sadd(key, username)
            client.expire(key, max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
            users.add(username)
            with VOICE_ROOMS_LOCK:
                VOICE_ROOMS.setdefault(room, set()).add(username)
            return True, None, sorted(users)
        except Exception:
            return False, "Shared voice roster unavailable.", sorted(list(users)) if 'users' in locals() else []
    with VOICE_ROOMS_LOCK:
        s = VOICE_ROOMS.setdefault(room, set())
        if username in s:
            return True, None, sorted(s)
        if max_peers > 0 and len(s) >= max_peers:
            return False, "Voice room is full.", sorted(s)
        s.add(username)
        return True, None, sorted(s)


def voice_room_remove_shared(room: str, username: str) -> bool:
    room = str(room or "").strip()
    username = str(username or "").strip()
    if not room or not username:
        return False
    removed = False
    with VOICE_ROOMS_LOCK:
        s = VOICE_ROOMS.get(room)
        if s and username in s:
            s.discard(username)
            removed = True
            if not s:
                VOICE_ROOMS.pop(room, None)
    client = _redis_client()
    if client is not None:
        try:
            key = _voice_room_users_key(room)
            client.srem(key, username)
            if int(client.scard(key) or 0) <= 0:
                client.delete(key)
            else:
                client.expire(key, max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
            removed = True
        except Exception:
            pass
    return removed


def _session_from_hash(data) -> dict | None:
    if not data:
        return None
    username = str(data.get("username") or "").strip()
    if not username:
        return None
    room = str(data.get("room") or "").strip() or None
    auth_session_id = str(data.get("auth_session_id") or "").strip() or None
    out = {"username": username, "room": room, "auth_session_id": auth_session_id}
    updated = data.get("updated")
    if updated is not None:
        try:
            out["updated"] = float(updated)
        except Exception:
            pass
    return out


def _cleanup_user_refs(username: str) -> list[str]:
    client = _redis_client()
    if client is None:
        return []
    username = str(username or "").strip()
    if not username:
        return []
    key = _user_sids_key(username)
    try:
        sids = sorted(list(client.smembers(key) or []))
    except Exception:
        return []
    alive: list[str] = []
    stale: list[str] = []
    for sid in sids:
        try:
            if client.exists(_sid_key(sid)):
                alive.append(sid)
            else:
                stale.append(sid)
        except Exception:
            stale.append(sid)
    if stale:
        try:
            client.srem(key, *stale)
        except Exception:
            pass
    if alive:
        try:
            client.sadd(_online_users_key(), username)
            client.expire(key, max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
        except Exception:
            pass
    else:
        try:
            client.srem(_online_users_key(), username)
            client.delete(key)
        except Exception:
            pass
    return alive


def _cleanup_room_refs(room: str, username: str) -> list[str]:
    client = _redis_client()
    if client is None:
        return []
    room = str(room or "").strip()
    username = str(username or "").strip()
    if not room or not username:
        return []

    user_key = _room_user_sids_key(room, username)
    room_users_key = _room_users_key(room)
    try:
        sids = sorted(list(client.smembers(user_key) or []))
    except Exception:
        return []

    alive: list[str] = []
    stale: list[str] = []
    for sid in sids:
        try:
            data = client.hgetall(_sid_key(sid)) or {}
        except Exception:
            data = {}
        sess = _session_from_hash(data)
        if not sess or sess.get("username") != username or (sess.get("room") or "") != room:
            stale.append(sid)
        else:
            alive.append(sid)

    if stale:
        try:
            client.srem(user_key, *stale)
        except Exception:
            pass

    if alive:
        try:
            client.sadd(room_users_key, username)
            client.sadd(_active_rooms_key(), room)
            client.expire(user_key, max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
            client.expire(room_users_key, max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
        except Exception:
            pass
    else:
        try:
            client.delete(user_key)
            client.srem(room_users_key, username)
            if int(client.scard(room_users_key) or 0) <= 0:
                client.delete(room_users_key)
                client.srem(_active_rooms_key(), room)
        except Exception:
            pass
    return alive


def get_connected_session(sid: str) -> dict | None:
    sid = str(sid or "").strip()
    if not sid:
        return None
    with CONNECTED_USERS_LOCK:
        local = CONNECTED_USERS.get(sid)
        if local:
            return dict(local)
    client = _redis_client()
    if client is None:
        return None
    try:
        return _session_from_hash(client.hgetall(_sid_key(sid)) or {})
    except Exception:
        return None


def get_connected_room(sid: str) -> str | None:
    sess = get_connected_session(sid)
    if not sess:
        return None
    return str(sess.get("room") or "").strip() or None


def get_connected_username(sid: str) -> str | None:
    sess = get_connected_session(sid)
    if not sess:
        return None
    return str(sess.get("username") or "").strip() or None


def get_connected_auth_session_id(sid: str) -> str | None:
    sess = get_connected_session(sid)
    if not sess:
        return None
    return str(sess.get("auth_session_id") or "").strip() or None


def upsert_connected_session(
    sid: str,
    username: str,
    room: str | None = None,
    auth_session_id: str | None = None,
) -> dict | None:
    sid = str(sid or "").strip()
    username = str(username or "").strip()
    room = str(room or "").strip() or None
    auth_session_id = str(auth_session_id or "").strip() or None
    if not sid or not username:
        return None

    previous = get_connected_session(sid)
    if auth_session_id is None:
        auth_session_id = str((previous or {}).get("auth_session_id") or "").strip() or None

    with CONNECTED_USERS_LOCK:
        CONNECTED_USERS[sid] = {"username": username, "room": room, "auth_session_id": auth_session_id}

    client = _redis_client()
    if client is None:
        return previous

    try:
        old_username = str((previous or {}).get("username") or "").strip() or None
        old_room = str((previous or {}).get("room") or "").strip() or None

        client.hset(
            _sid_key(sid),
            mapping={
                "username": username,
                "room": room or "",
                "auth_session_id": auth_session_id or "",
                "updated": str(time.time()),
            },
        )
        client.expire(_sid_key(sid), _SHARED_STATE_SESSION_TTL_SECONDS)
        client.sadd(_user_sids_key(username), sid)
        client.expire(_user_sids_key(username), max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
        client.sadd(_online_users_key(), username)

        if old_username and old_username != username:
            try:
                client.srem(_user_sids_key(old_username), sid)
            except Exception:
                pass
            _cleanup_user_refs(old_username)

        if old_room and old_username:
            if old_room != room or old_username != username:
                try:
                    client.srem(_room_user_sids_key(old_room, old_username), sid)
                except Exception:
                    pass
                _cleanup_room_refs(old_room, old_username)

        if room:
            client.sadd(_room_user_sids_key(room, username), sid)
            client.expire(_room_user_sids_key(room, username), max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
            client.sadd(_room_users_key(room), username)
            client.expire(_room_users_key(room), max(_SHARED_STATE_SESSION_TTL_SECONDS * 4, 600))
            client.sadd(_active_rooms_key(), room)
            _cleanup_room_refs(room, username)

        _cleanup_user_refs(username)
    except Exception:
        pass

    return previous


def update_connected_room(sid: str, room: str | None) -> dict | None:
    sid = str(sid or "").strip()
    if not sid:
        return None
    current = get_connected_session(sid)
    username = str((current or {}).get("username") or "").strip()
    auth_session_id = str((current or {}).get("auth_session_id") or "").strip() or None
    if not username:
        return current
    return upsert_connected_session(sid, username, room, auth_session_id=auth_session_id)


def remove_connected_session(sid: str) -> dict | None:
    sid = str(sid or "").strip()
    if not sid:
        return None
    previous = get_connected_session(sid)

    with CONNECTED_USERS_LOCK:
        try:
            if sid in CONNECTED_USERS:
                del CONNECTED_USERS[sid]
        except Exception:
            pass

    client = _redis_client()
    if client is None:
        return previous

    try:
        username = str((previous or {}).get("username") or "").strip() or None
        room = str((previous or {}).get("room") or "").strip() or None
        client.delete(_sid_key(sid))
        if username:
            try:
                client.srem(_user_sids_key(username), sid)
            except Exception:
                pass
            _cleanup_user_refs(username)
        if room and username:
            try:
                client.srem(_room_user_sids_key(room, username), sid)
            except Exception:
                pass
            _cleanup_room_refs(room, username)
    except Exception:
        pass
    return previous


def connected_usernames() -> list[str]:
    client = _redis_client()
    if client is None:
        with CONNECTED_USERS_LOCK:
            return sorted({(u or {}).get("username") for u in CONNECTED_USERS.values() if (u or {}).get("username")})
    try:
        users = sorted(list(client.smembers(_online_users_key()) or []))
    except Exception:
        users = []
    alive: list[str] = []
    for username in users:
        if _cleanup_user_refs(username):
            alive.append(username)
    return sorted(set(alive))


def user_sids(username: str) -> list[str]:
    username = str(username or "").strip()
    if not username:
        return []
    client = _redis_client()
    if client is None:
        with CONNECTED_USERS_LOCK:
            return [sid for sid, u in CONNECTED_USERS.items() if (u or {}).get("username") == username]
    return _cleanup_user_refs(username)


def auth_session_sids(username: str, auth_session_id: str) -> list[str]:
    username = str(username or "").strip()
    auth_session_id = str(auth_session_id or "").strip()
    if not username or not auth_session_id:
        return []
    out: list[str] = []
    for sid in user_sids(username):
        try:
            sess = get_connected_session(sid)
        except Exception:
            sess = None
        if not sess:
            continue
        if str(sess.get("auth_session_id") or "").strip() == auth_session_id:
            out.append(sid)
    return out


def room_users(room: str) -> list[str]:
    room = str(room or "").strip()
    if not room:
        return []
    client = _redis_client()
    if client is None:
        users: set[str] = set()
        with CONNECTED_USERS_LOCK:
            for _sid, sess in CONNECTED_USERS.items():
                try:
                    if str(sess.get("room") or "") != room:
                        continue
                    u = sess.get("username")
                    if u:
                        users.add(str(u))
                except Exception:
                    continue
        return sorted(users)

    try:
        raw_users = sorted(list(client.smembers(_room_users_key(room)) or []))
    except Exception:
        raw_users = []
    alive: list[str] = []
    for username in raw_users:
        if _cleanup_room_refs(room, username):
            alive.append(username)
    return sorted(set(alive))


def connected_room_targets(room: str) -> list[tuple[str, str]]:
    room = str(room or "").strip()
    if not room:
        return []
    targets: list[tuple[str, str]] = []
    for username in room_users(room):
        for sid in user_sids(username):
            sess = get_connected_session(sid)
            if not sess:
                continue
            if str(sess.get("room") or "") != room:
                continue
            targets.append((sid, username))
    return targets


def is_user_in_room(username: str, room: str) -> bool:
    username = str(username or "").strip()
    room = str(room or "").strip()
    if not username or not room:
        return False
    return username in set(room_users(room))


def live_room_counts() -> dict[str, int]:
    client = _redis_client()
    if client is None:
        per_room: dict[str, set[str]] = {}
        with CONNECTED_USERS_LOCK:
            for _sid, sess in CONNECTED_USERS.items():
                try:
                    r = sess.get("room")
                    u = sess.get("username")
                except Exception:
                    continue
                if not r or not u:
                    continue
                per_room.setdefault(str(r), set()).add(str(u))
        return {room: len(users) for room, users in per_room.items()}

    try:
        rooms = sorted(list(client.smembers(_active_rooms_key()) or []))
    except Exception:
        rooms = []
    counts: dict[str, int] = {}
    for room in rooms:
        users = room_users(room)
        if users:
            counts[room] = len(users)
    return counts


def connected_sessions_snapshot() -> dict[str, dict]:
    client = _redis_client()
    if client is None:
        with CONNECTED_USERS_LOCK:
            return {sid: dict(sess) for sid, sess in CONNECTED_USERS.items()}
    snap: dict[str, dict] = {}
    for username in connected_usernames():
        for sid in user_sids(username):
            sess = get_connected_session(sid)
            if sess:
                snap[sid] = dict(sess)
    return snap
