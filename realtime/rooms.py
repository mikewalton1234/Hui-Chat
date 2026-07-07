

def _antiabuse_duplicate_checks(*args, **kwargs):
    """Placeholder hook for duplicate fingerprint checks."""
    return True
"""Socket.IO handlers: rooms.

Auto-split from the legacy monolithic socket_handlers.py.
"""

import base64
import json
import re
import time
import uuid
import threading
from collections import deque


from flask import request
from socket_auth import jwt_required, get_jwt_identity
from flask_socketio import join_room, leave_room, emit, disconnect

from database import (
    get_all_rooms,
    get_friends_for_user,
    create_room_if_missing,
    create_autoscaled_room_if_missing,
    increment_room_count,
    get_pending_friend_requests,
    get_blocked_users,
    get_db,
    get_custom_room_meta,
    can_user_access_custom_room,
    can_user_join_custom_room,
    get_custom_room_user_role,
    can_user_moderate_custom_room,
    custom_room_role_rank,
    revoke_custom_room_access,
    touch_custom_room_activity,
    consume_room_invites,
    record_custom_room_membership,
    set_room_message_expiry,
    get_room_message_expiry,
)
from security import log_audit_event, sanitize_user_visible_text
from permissions import check_user_permission
from moderation import is_user_sanctioned, mute_user
from room_catalog import find_catalog_room_entry, find_catalog_room_location, read_official_room_catalog

from realtime.state import *
from realtime.antiabuse_utils import infer_room_message_kind


def _record_recent_room_join(username: str, room: str, keep: int = 24) -> None:
    username = str(username or "").strip()
    room = str(room or "").strip()
    if not username or not room:
        return
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(share_recent_rooms, FALSE) FROM users WHERE username = %s LIMIT 1;", (username,))
            row = cur.fetchone()
            if not row or not bool(row[0]):
                return
            cur.execute(
                "INSERT INTO user_recent_rooms (username, room_name) VALUES (%s, %s);",
                (username, room),
            )
            cur.execute(
                """
                WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER (PARTITION BY username ORDER BY joined_at DESC, id DESC) AS rn
                      FROM user_recent_rooms
                     WHERE username = %s
                )
                DELETE FROM user_recent_rooms urr
                 USING ranked r
                 WHERE urr.id = r.id
                   AND r.rn > %s;
                """,
                (username, max(6, int(keep or 24))),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass



ROOM_MEDIA_LOCK = threading.Lock()
ROOM_MEDIA_STATE: dict[str, dict] = {}
ROOM_MEDIA_LISTENERS: dict[str, dict[str, float]] = {}
ROOM_MEDIA_SKIP_VOTES: dict[str, set[str]] = {}
ROOM_MEDIA_PRESENCE_TTL_SEC = 35.0


def _read_room_catalog_for_media() -> dict:
    return read_official_room_catalog()


def _catalog_room_entry(room_name: str) -> dict | None:
    return find_catalog_room_entry(_read_room_catalog_for_media(), room_name)


def _room_media_enabled(room_name: str) -> bool:
    entry = _catalog_room_entry(room_name) or {}
    features = entry.get("features") or []
    if any(str(flag or "").strip() in {"music_room", "music_share", "room_radio"} for flag in features):
        return True
    # Admin-edited catalog rooms with a valid station list are radio-capable even
    # if an older JSON row did not carry the room_radio feature flag yet.
    return bool(entry.get("stations"))


def _room_media_normalize_station(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label") or raw.get("name") or "").strip()
    page_url = str(raw.get("page_url") or raw.get("url") or "").strip()
    embed_url = str(raw.get("embed_url") or "").strip()
    provider = str(raw.get("provider") or "").strip()
    if not label and not page_url and not embed_url:
        return None
    return {
        "label": label or "Station",
        "page_url": page_url,
        "embed_url": embed_url,
        "provider": provider,
    }


def _room_media_stations(room_name: str) -> list[dict]:
    entry = _catalog_room_entry(room_name) or {}
    out = []
    for raw in (entry.get("stations") or []):
        clean = _room_media_normalize_station(raw)
        if clean:
            out.append(clean)
    return out[:16]


def _room_media_prune_stale(room_name: str) -> dict[str, float]:
    room = str(room_name or "").strip()
    now = time.time()
    listeners = ROOM_MEDIA_LISTENERS.get(room) or {}
    cleaned = {u: float(ts) for u, ts in listeners.items() if (now - float(ts or 0.0)) <= ROOM_MEDIA_PRESENCE_TTL_SEC}
    if cleaned:
        ROOM_MEDIA_LISTENERS[room] = cleaned
    else:
        ROOM_MEDIA_LISTENERS.pop(room, None)
    votes = ROOM_MEDIA_SKIP_VOTES.get(room) or set()
    active_votes = {u for u in votes if u in cleaned}
    if active_votes:
        ROOM_MEDIA_SKIP_VOTES[room] = active_votes
    else:
        ROOM_MEDIA_SKIP_VOTES.pop(room, None)
    return cleaned


def _room_media_listener_count(room_name: str) -> int:
    return len(_room_media_prune_stale(room_name))


def _room_media_required_votes(listener_count: int) -> int:
    try:
        n = int(listener_count or 0)
    except Exception:
        n = 0
    if n <= 1:
        return 1
    return max(1, n // 2)


def _room_media_touch_listener(room_name: str, username: str, active: bool = True) -> int:
    room = str(room_name or "").strip()
    user = str(username or "").strip()
    if not room or not user:
        return 0
    with ROOM_MEDIA_LOCK:
        if not active:
            listeners = ROOM_MEDIA_LISTENERS.get(room) or {}
            listeners.pop(user, None)
            if listeners:
                ROOM_MEDIA_LISTENERS[room] = listeners
            else:
                ROOM_MEDIA_LISTENERS.pop(room, None)
            votes = ROOM_MEDIA_SKIP_VOTES.get(room) or set()
            votes.discard(user)
            if votes:
                ROOM_MEDIA_SKIP_VOTES[room] = votes
            else:
                ROOM_MEDIA_SKIP_VOTES.pop(room, None)
            return _room_media_listener_count(room)
        listeners = ROOM_MEDIA_LISTENERS.get(room) or {}
        listeners[user] = time.time()
        ROOM_MEDIA_LISTENERS[room] = listeners
        return _room_media_listener_count(room)


def _room_media_payload(room_name: str) -> dict | None:
    room = str(room_name or "").strip()
    if not room or not _room_media_enabled(room):
        return None
    stations = _room_media_stations(room)
    with ROOM_MEDIA_LOCK:
        state = ROOM_MEDIA_STATE.get(room) or {}
        try:
            idx = int(state.get("station_index") or 0) if stations else -1
        except Exception:
            idx = 0 if stations else -1
        if stations and (idx < 0 or idx >= len(stations)):
            idx = 0
        current = stations[idx] if stations and idx >= 0 else None
        if stations and state.get("station_index") != idx:
            ROOM_MEDIA_STATE[room] = {
                "station_index": idx,
                "changed_at": state.get("changed_at") or time.time(),
                "changed_by": state.get("changed_by") or "system",
            }
        listeners = _room_media_prune_stale(room)
        votes = ROOM_MEDIA_SKIP_VOTES.get(room) or set()
        active_voters = sorted([u for u in votes if u in listeners])
        ROOM_MEDIA_SKIP_VOTES[room] = set(active_voters)
        count = len(listeners)
        required = _room_media_required_votes(count)
        station_count = len(stations)
        return {
            "room": room,
            "supported": True,
            "stations": stations,
            "station": current,
            "station_index": idx if current else None,
            "station_count": station_count,
            "can_skip_advance": station_count > 1,
            "listener_count": count,
            "required_votes": required,
            "votes": len(active_voters),
            "voters": active_voters,
            "changed_at": float((ROOM_MEDIA_STATE.get(room) or {}).get("changed_at") or time.time()),
            "changed_by": str((ROOM_MEDIA_STATE.get(room) or {}).get("changed_by") or "system"),
            "change_reason": str((ROOM_MEDIA_STATE.get(room) or {}).get("reason") or "select"),
        }


def _room_media_emit_state(room_name: str, *, to_sid: str | None = None) -> dict | None:
    payload = _room_media_payload(room_name)
    if not payload:
        return None
    if to_sid:
        emit("room_media_state_sync", payload, to=to_sid)
    else:
        emit("room_media_state_sync", payload, to=str(room_name or "").strip())
    return payload


def _room_media_set_station(room_name: str, station_index: int, actor: str, *, reason: str = "select") -> dict | None:
    room = str(room_name or "").strip()
    stations = _room_media_stations(room)
    if not room or not stations:
        return None
    try:
        idx = int(station_index)
    except Exception:
        idx = 0
    idx = max(0, min(len(stations) - 1, idx))
    with ROOM_MEDIA_LOCK:
        ROOM_MEDIA_STATE[room] = {
            "station_index": idx,
            "changed_at": time.time(),
            "changed_by": str(actor or "system"),
            "reason": str(reason or "select"),
        }
        ROOM_MEDIA_SKIP_VOTES[room] = set()
    payload = _room_media_emit_state(room)
    station = stations[idx]
    try:
        emit(
            "chat_message",
            {
                "room": room,
                "message_id": uuid.uuid4().hex,
                "username": "System",
                "message": f"🎵 {actor or 'Someone'} switched the room radio to {station.get('label') or 'a new source'}.",
                "encrypted": False,
                "ts": time.time(),
                "message_kind": "text",
            },
            to=room,
        )
    except Exception:
        pass
    return payload


def _room_media_advance_station(room_name: str, actor: str, *, reason: str = "skip") -> tuple[dict | None, bool]:
    room = str(room_name or "").strip()
    stations = _room_media_stations(room)
    if not room or not stations:
        return None, False
    current = _room_media_payload(room) or {}
    idx = int(current.get("station_index") or 0)
    if len(stations) <= 1:
        with ROOM_MEDIA_LOCK:
            ROOM_MEDIA_SKIP_VOTES[room] = set()
        payload = _room_media_emit_state(room) or current or _room_media_payload(room)
        return payload, False
    next_idx = (idx + 1) % len(stations)
    return _room_media_set_station(room, next_idx, actor, reason=reason), True


def _room_media_vote_skip(room_name: str, username: str) -> tuple[dict | None, bool, str]:
    room = str(room_name or "").strip()
    user = str(username or "").strip()
    if not room or not user or not _room_media_enabled(room):
        return None, False, "disabled"
    if not _room_media_stations(room):
        return _room_media_emit_state(room), False, "no_source"
    with ROOM_MEDIA_LOCK:
        listeners = _room_media_prune_stale(room)
        listeners[user] = time.time()
        ROOM_MEDIA_LISTENERS[room] = listeners
        votes = ROOM_MEDIA_SKIP_VOTES.get(room) or set()
        votes.add(user)
        ROOM_MEDIA_SKIP_VOTES[room] = votes
        count = len(listeners)
        required = _room_media_required_votes(count)
        reached = len(votes) >= required
    if reached:
        payload, advanced = _room_media_advance_station(room, user, reason="vote_skip")
        return payload, advanced, ("switched" if advanced else "no_alternate")
    payload = _room_media_emit_state(room)
    return payload, False, "voted"

def register(socketio, settings, ctx):
    """Register Socket.IO event handlers for this module."""
    # Make helper functions from socket_handlers available as module globals
    globals().update(ctx.__dict__)
    _antiabuse_exempt_staff = bool(settings.get("antiabuse_exempt_staff", True))

    def _feature_bool(key: str, default: bool = False) -> bool:
        val = settings.get(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        text = str(val or "").strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "none"}:
            return False
        return bool(default)

    def _can_override_room_lock(username: str) -> bool:
        return bool(check_user_permission(username, "admin:basic") or check_user_permission(username, "room:lock"))

    def _can_override_room_readonly(username: str) -> bool:
        return bool(check_user_permission(username, "admin:basic") or check_user_permission(username, "room:readonly"))

    def _is_recent_custom_room_create_auto_join(room: str, username: str, data=None) -> bool:
        """Allow the creator's immediate post-create auto-join to bypass switch throttles only."""
        if not bool((data or {}).get("auto_join_created_custom_room")):
            return False
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_user = str(username or "").strip()
        if not clean_room or not clean_user:
            return False
        try:
            meta = get_custom_room_meta(clean_room)
        except Exception:
            meta = None
        if not meta:
            return False
        if str(meta.get("created_by") or "").strip().lower() != clean_user.lower():
            return False
        created_at = meta.get("created_at")
        try:
            created_ts = float(created_at.timestamp())
        except Exception:
            return False
        return bool((time.time() - created_ts) <= 120)

    def _room_policy_payload(room: str, username: str) -> dict:
        """Build live room policy plus room-scoped custom-room moderation state."""
        clean_room = _canonical_room_name(str(room or "").strip())
        policy_available = True
        try:
            locked = _room_locked(clean_room)
        except Exception:
            locked = False
            policy_available = False
        try:
            readonly = _room_readonly(clean_room)
        except Exception:
            readonly = False
            policy_available = False
        try:
            slow = _room_slowmode_seconds(clean_room)
        except Exception:
            slow = 0
            policy_available = False
        bypass_lock = _can_override_room_lock(username)
        bypass_ro = _can_override_room_readonly(username)
        can_send = bool(policy_available) and (not locked or bypass_lock) and (not readonly or bypass_ro)

        block_reason = None
        if not can_send:
            if not policy_available:
                block_reason = "policy_unavailable"
            elif readonly and not bypass_ro:
                block_reason = "read_only"
            elif locked and not bypass_lock:
                block_reason = "locked"
            else:
                block_reason = "blocked"

        room_role = None
        can_room_moderate = False
        room_owner = None
        is_custom_room = False
        is_private_room = False
        try:
            meta = get_custom_room_meta(clean_room)
            if meta:
                is_custom_room = True
                is_private_room = bool(meta.get("is_private"))
                room_owner = str(meta.get("created_by") or "").strip() or None
                room_role = get_custom_room_user_role(clean_room, username)
                can_room_moderate = bool(can_user_moderate_custom_room(clean_room, username))
        except Exception:
            room_role = None
            can_room_moderate = False

        return {
            "room": clean_room,
            "locked": bool(locked),
            "readonly": bool(readonly),
            "slowmode_seconds": int(slow or 0),
            "can_send": bool(can_send),
            "policy_available": bool(policy_available),
            "can_override_lock": bool(bypass_lock),
            "can_override_readonly": bool(bypass_ro),
            "block_reason": block_reason,
            "is_custom_room": bool(is_custom_room),
            "is_private_room": bool(is_private_room),
            "room_owner": room_owner,
            "my_room_role": room_role,
            "can_room_moderate": bool(can_room_moderate),
        }

    def _emit_room_policy_state(room: str, username: str, *, to_sid: str) -> dict:
        payload = _room_policy_payload(room, username)
        emit("room_policy_state", payload, to=to_sid)
        return payload

    def _room_count_is_visible_to_user(room: str, username: str | None) -> bool:
        """Do not leak private custom-room names/counts to callers who cannot see them."""
        clean_room = _canonical_room_name(str(room or "").strip())
        if not clean_room:
            return False
        try:
            denied, _reason = _private_custom_room_visibility_denied(clean_room, username or "")
        except Exception:
            # Counts are metadata. If privacy checks fail, hide the room rather
            # than leaking private-room names or presence.
            denied = True
        return not bool(denied)

    def _visible_live_room_counts(username: str | None = None) -> dict[str, int]:
        counts = {}
        try:
            raw_counts = dict(_live_room_counts() or {})
        except Exception:
            raw_counts = {}
        for room_name, count in raw_counts.items():
            clean_room = _canonical_room_name(str(room_name or "").strip())
            if not clean_room:
                continue
            if not _room_count_is_visible_to_user(clean_room, username):
                continue
            try:
                counts[clean_room] = max(0, int(count or 0))
            except Exception:
                counts[clean_room] = 0
        return counts

    def _room_counts_payload(username: str | None = None) -> dict:
        return {
            "success": True,
            "counts": _visible_live_room_counts(username),
            "visibility": "user" if username else "public",
            "ts": time.time(),
        }

    def _emit_room_counts_snapshot(*, to_sid: str | None = None, username: str | None = None) -> None:
        """Emit privacy-filtered room counts.

        Global broadcasts include only non-private rooms. Direct snapshots include
        private rooms only when that signed-in user can access them.
        """
        target_user = str(username or "").strip() or None
        if to_sid and not target_user:
            try:
                sess = get_connected_session(to_sid)
                target_user = str((sess or {}).get("username") or "").strip() or None
            except Exception:
                target_user = None
        payload = _room_counts_payload(target_user)
        try:
            if to_sid:
                emit("room_counts", payload, to=to_sid)
            else:
                socketio.emit("room_counts", _room_counts_payload(None))
        except Exception:
            pass

    def _safe_room_send_positive_int(value, default: int, *, minimum: int = 1, maximum: int = 500000) -> int:
        """Parse room-send limit settings without letting bad config break chat."""
        try:
            parsed = int(value)
        except Exception:
            parsed = int(default)
        if parsed < int(minimum):
            return int(default)
        if parsed > int(maximum):
            return int(maximum)
        return int(parsed)

    def _b64u_decode_room(raw: str) -> bytes:
        raw = str(raw or "").strip()
        raw += "=" * (-len(raw) % 4)
        return base64.urlsafe_b64decode(raw.encode("ascii"))

    def _room_cipher_envelope_obj(value):
        """Return parsed ECR1 room envelope object or None.

        Room E2EE is client-side, but the server may still safely inspect the
        recipient key names.  That lets it reject stale self-only envelopes after
        unblock instead of delivering a message the other live user cannot read.
        """
        if not isinstance(value, str) or not value.startswith("ECR1:"):
            return None
        payload = value[len("ECR1:"):].strip()
        if not payload or len(payload) > 250000:
            return None
        try:
            decoded = _b64u_decode_room(payload)
            if len(decoded) > 200000:
                return None
            obj = json.loads(decoded.decode("utf-8"))
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    def _looks_like_room_cipher_envelope(value) -> bool:
        """Accept only a structured room E2EE envelope, not just any ECR1 string."""
        obj = _room_cipher_envelope_obj(value)
        if not isinstance(obj, dict):
            return False
        version = obj.get("v", obj.get("version"))
        if str(version) not in {"1", "room-v1"}:
            return False
        alg = str(obj.get("alg") or obj.get("cipher") or "").upper()
        if alg and "AES" not in alg:
            return False
        iv = obj.get("iv") or obj.get("nonce")
        ct = obj.get("ct") or obj.get("ciphertext")
        keys = obj.get("keys") or obj.get("wrapped_keys") or obj.get("recipients")
        if not isinstance(iv, str) or not isinstance(ct, str):
            return False
        try:
            if len(_b64u_decode_room(iv)) not in {12, 16, 24}:
                return False
            if len(_b64u_decode_room(ct)) < 16:
                return False
        except Exception:
            return False
        if not isinstance(keys, dict) or not keys or len(keys) > 500:
            return False
        for k, wrapped in list(keys.items())[:10]:
            if not str(k or "").strip() or not isinstance(wrapped, str) or len(wrapped) < 16:
                return False
        return True

    def _room_cipher_recipient_keys(value) -> set[str]:
        obj = _room_cipher_envelope_obj(value)
        if not isinstance(obj, dict):
            return set()
        keys = obj.get("keys") or obj.get("wrapped_keys") or obj.get("recipients")
        if not isinstance(keys, dict):
            return set()
        out: set[str] = set()
        for raw in keys.keys():
            key = str(raw or "").replace("\u00a0", " ").strip().lower()
            key = re.sub(r"\s+", " ", key)
            if key:
                out.add(key)
        return out

    def _room_live_allowed_recipient_names(room: str, sender: str) -> list[str]:
        """Live users who should receive sender's room message.

        This mirrors the room fan-out model: blocking is viewer-side for room
        chat.  A user receives a sender's room packet unless that viewer has
        blocked the sender.  Sender-blocks-viewer does not remove the viewer
        from room membership or from the sender's room recipient list.
        """
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_sender = str(sender or "").strip()
        if not clean_room or not clean_sender:
            return []
        targets: list[tuple[str, str]] = []
        try:
            if callable(globals().get("_socketio_room_targets")):
                targets.extend(list(_socketio_room_targets(clean_room)))
        except Exception:
            pass
        try:
            targets.extend(list(connected_room_targets(clean_room)))
        except Exception:
            pass
        if not targets:
            targets = [(str(getattr(request, "sid", "") or ""), clean_sender)]

        names: dict[str, str] = {}
        sender_key = re.sub(r"\s+", " ", clean_sender).strip().lower()
        if sender_key:
            names[sender_key] = clean_sender
        block_cache: dict[str, bool] = {}
        for _sid, raw_name in targets:
            name = str(raw_name or "").replace("\u00a0", " ").strip()
            key = re.sub(r"\s+", " ", name).strip().lower()
            if not key or key in names:
                continue
            if sender_key and key != sender_key:
                cache_key = key
                if cache_key not in block_cache:
                    try:
                        block_cache[cache_key] = bool(_is_blocked(name, clean_sender))
                    except Exception:
                        block_cache[cache_key] = True
                if block_cache[cache_key]:
                    continue
            names[key] = name
        return [names[k] for k in sorted(names.keys())]

    def _room_e2ee_recipient_mismatch(room: str, sender: str, cipher: str) -> list[str]:
        """Return live allowed recipients missing from the envelope key map."""
        present = _room_cipher_recipient_keys(cipher)
        if not present:
            return []
        missing = []
        for name in _room_live_allowed_recipient_names(room, sender):
            key = re.sub(r"\s+", " ", str(name or "").replace("\u00a0", " ").strip()).lower()
            if key and key not in present:
                missing.append(name)
        return missing

    def _room_typing_key(room: str, username: str) -> str:
        return f"{_canonical_room_name(str(room or '').strip())}\x1f{str(username or '').strip()}"

    def _room_typing_payload(room: str, username: str, *, typing: bool) -> dict:
        return {
            "room": _canonical_room_name(str(room or "").strip()),
            "username": str(username or "").strip(),
            "typing": bool(typing),
            "expires_in": int(TYPING_EXPIRY_SECONDS),
            "ts": time.time(),
        }

    def _room_public_avatar_url(username: str) -> str:
        clean = str(username or "").strip()
        if not clean:
            return ""
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(avatar_url, '')
                      FROM users
                     WHERE LOWER(username) = LOWER(%s)
                     LIMIT 1;
                    """,
                    (clean,),
                )
                row = cur.fetchone()
            return str(row[0] or "").strip() if row else ""
        except Exception:
            return ""

    def _same_realtime_user(a: str, b: str) -> bool:
        return str(a or "").strip().lower() == str(b or "").strip().lower()

    def _emit_room_chat_message_filtered(room: str, sender: str, payload: dict, *, shadowbanned_sender: bool = False) -> int:
        """Emit a room chat message only to room sockets allowed to see sender.

        Room chat is live-only, but blocking is a per-viewer privacy filter.  The
        sender still sees their own echo.  A recipient is skipped only when that
        recipient has blocked the sender.  Sender-blocks-recipient does not hide
        the sender's room message from the recipient, because block does not
        change room membership or global room visibility.  The sender echo is
        sent directly before registry fan-out so a stale room roster can never
        make a successful send look like it disappeared.
        """
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_sender = str(sender or "").strip()
        if not clean_room or not clean_sender:
            return 0

        delivered = 0
        delivered_sids: set[str] = set()

        def _emit_once(target_sid: str | None) -> bool:
            nonlocal delivered
            sid = str(target_sid or "").strip()
            if not sid or sid in delivered_sids:
                return False
            try:
                emit("chat_message", payload, to=sid)
                delivered_sids.add(sid)
                delivered += 1
                return True
            except Exception:
                return False

        # Always echo to the sending socket first.  This also covers unblock
        # races where shared presence/room state has not caught up yet.
        try:
            _emit_once(request.sid)
        except Exception:
            pass

        if shadowbanned_sender:
            return delivered

        block_cache: dict[str, bool] = {}
        targets: list[tuple[str, str]] = []
        try:
            # Prefer the actual Flask-SocketIO room participants.  The shared
            # roster can lag during block -> unblock / reconnect races, while
            # the transport room is the membership used by normal room emits.
            if callable(globals().get("_socketio_room_targets")):
                targets.extend(list(_socketio_room_targets(clean_room)))
        except Exception:
            pass
        try:
            targets.extend(list(connected_room_targets(clean_room)))
        except Exception:
            pass
        if not targets:
            targets = [(request.sid, clean_sender)]

        seen_target_sids: set[str] = set()
        for target_sid, target_user in targets:
            target_sid = str(target_sid or "").strip()
            if not target_sid or target_sid in seen_target_sids:
                continue
            seen_target_sids.add(target_sid)
            target_name = str(target_user or "").strip()
            if not target_sid or not target_name:
                continue
            allowed = True
            if not _same_realtime_user(clean_sender, target_name):
                cache_key = target_name.lower()
                if cache_key not in block_cache:
                    try:
                        block_cache[cache_key] = bool(_is_blocked(target_name, clean_sender))
                    except Exception:
                        # If block status cannot be confirmed, fail closed for
                        # non-sender recipients instead of leaking a blocked message.
                        block_cache[cache_key] = True
                allowed = not block_cache[cache_key]
            if not allowed:
                continue
            _emit_once(target_sid)
        return delivered

    def _emit_room_signal_filtered(room: str, sender: str, event_name: str, payload: dict, *, skip_sid: str | None = None) -> int:
        """Emit a lightweight room signal only to users allowed to see sender.

        Typing/stop-typing follows the same viewer-side privacy boundary as
        room messages: skip a recipient only when that recipient has blocked
        the sender.  The browser still has a client-side guard, but the server
        should not deliver side-channel packets to a viewer who blocked the
        sender in the first place.
        """
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_sender = str(sender or "").strip()
        clean_event = str(event_name or "").strip()
        if not clean_room or not clean_sender or clean_event not in {"room_typing", "room_stop_typing"}:
            return 0

        skip = str(skip_sid or "").strip()
        delivered = 0
        delivered_sids: set[str] = set()
        targets: list[tuple[str, str]] = []
        try:
            if callable(globals().get("_socketio_room_targets")):
                targets.extend(list(_socketio_room_targets(clean_room)))
        except Exception:
            pass
        try:
            targets.extend(list(connected_room_targets(clean_room)))
        except Exception:
            pass

        block_cache: dict[str, bool] = {}
        for target_sid, target_user in targets:
            sid = str(target_sid or "").strip()
            target_name = str(target_user or "").strip()
            if not sid or sid in delivered_sids or (skip and sid == skip) or not target_name:
                continue
            if _same_realtime_user(clean_sender, target_name):
                continue
            cache_key = target_name.lower()
            if cache_key not in block_cache:
                try:
                    block_cache[cache_key] = bool(_is_blocked(target_name, clean_sender))
                except Exception:
                    block_cache[cache_key] = True
            if block_cache[cache_key]:
                continue
            try:
                emit(clean_event, payload, to=sid)
                delivered_sids.add(sid)
                delivered += 1
            except Exception:
                continue
        return delivered

    def _clear_room_typing(room: str, username: str, *, broadcast: bool = False, skip_sid: str | None = None) -> None:
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_user = str(username or "").strip()
        if not clean_room or not clean_user:
            return
        key = _room_typing_key(clean_room, clean_user)
        with TYPING_STATUS_LOCK:
            TYPING_STATUS.pop(key, None)
            # Remove legacy beta entries keyed only by username so old in-memory
            # state cannot leak a stale indicator after upgrading.
            TYPING_STATUS.pop(clean_user, None)
        if broadcast:
            try:
                _emit_room_signal_filtered(clean_room, clean_user, "room_stop_typing", _room_typing_payload(clean_room, clean_user, typing=False), skip_sid=skip_sid)
            except Exception:
                pass

    def _mark_room_typing(room: str, username: str) -> None:
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_user = str(username or "").strip()
        if not clean_room or not clean_user:
            return
        with TYPING_STATUS_LOCK:
            TYPING_STATUS[_room_typing_key(clean_room, clean_user)] = time.time()

    def _clean_room_reaction_message_id(value) -> str:
        """Accept only server-issued live room message ids."""
        mid = str(value or "").strip()
        if not re.fullmatch(r"[0-9a-f]{32}", mid):
            return ""
        return mid

    def _room_live_message_ttl_seconds(room: str) -> int:
        """Return the live DOM/registry TTL for room messages.

        Room chat is live-only, but message controls such as reactions and pins
        still need a bounded lifetime.  Per-room admin expiry wins when set;
        otherwise the process-wide live-message TTL is used.  Bad hand-edited
        config or DB values must never make messages immortal or crash send.
        """
        clean_room = _canonical_room_name(str(room or "").strip())
        configured = 0
        if clean_room:
            try:
                configured = int(get_room_message_expiry(clean_room) or 0)
            except Exception:
                configured = 0
        if configured <= 0:
            try:
                configured = int(settings.get("room_live_message_ttl_seconds") or ROOM_LIVE_MESSAGE_TTL_SECONDS)
            except Exception:
                configured = int(ROOM_LIVE_MESSAGE_TTL_SECONDS)
        return max(30, min(int(configured), 7 * 24 * 60 * 60))

    def _prune_room_live_messages(now: float | None = None, *, emit_expired: bool = True) -> None:
        now_ts = float(now if now is not None else time.time())
        try:
            fallback_ttl = float(settings.get("room_live_message_ttl_seconds") or ROOM_LIVE_MESSAGE_TTL_SECONDS)
        except Exception:
            fallback_ttl = 21600.0
        fallback_ttl = max(30.0, min(float(fallback_ttl), float(7 * 24 * 60 * 60)))
        try:
            max_messages = max(100, int(settings.get("room_live_message_max") or ROOM_LIVE_MESSAGE_MAX))
        except Exception:
            max_messages = 5000

        expired: list[str] = []
        expired_by_room: dict[str, list[str]] = {}
        with ROOM_LIVE_MESSAGES_LOCK:
            for mid, meta in list(ROOM_LIVE_MESSAGES.items()):
                try:
                    ts = float((meta or {}).get("ts") or 0.0)
                except Exception:
                    ts = 0.0
                try:
                    expires_at = float((meta or {}).get("expires_at") or 0.0)
                except Exception:
                    expires_at = 0.0
                should_expire = bool(expires_at and now_ts >= expires_at) or (not expires_at and (not ts or (now_ts - ts) > fallback_ttl))
                if should_expire:
                    mid_s = str(mid)
                    room_s = _canonical_room_name(str((meta or {}).get("room") or "").strip())
                    expired.append(mid_s)
                    if room_s:
                        expired_by_room.setdefault(room_s, []).append(mid_s)
                    ROOM_LIVE_MESSAGES.pop(mid, None)
            if len(ROOM_LIVE_MESSAGES) > max_messages:
                overflow = sorted(
                    ROOM_LIVE_MESSAGES.items(),
                    key=lambda item: float((item[1] or {}).get("ts") or 0.0),
                )[: max(0, len(ROOM_LIVE_MESSAGES) - max_messages)]
                for mid, meta in overflow:
                    mid_s = str(mid)
                    room_s = _canonical_room_name(str((meta or {}).get("room") or "").strip())
                    expired.append(mid_s)
                    if room_s:
                        expired_by_room.setdefault(room_s, []).append(mid_s)
                    ROOM_LIVE_MESSAGES.pop(mid, None)

        if expired:
            with MESSAGE_REACTIONS_LOCK:
                for mid in expired:
                    MESSAGE_REACTIONS.pop(mid, None)
            with ROOM_PINNED_MESSAGES_LOCK:
                for clean_room, pin in list(ROOM_PINNED_MESSAGES.items()):
                    if str((pin or {}).get("message_id") or "") in expired:
                        ROOM_PINNED_MESSAGES.pop(clean_room, None)
            if emit_expired:
                for clean_room, mids in expired_by_room.items():
                    try:
                        emit(
                            "room_messages_expired",
                            {"room": clean_room, "message_ids": list(dict.fromkeys(mids)), "reason": "expired"},
                            to=clean_room,
                        )
                    except Exception:
                        pass

    def _register_live_room_message(message_id: str, room: str, username: str, *, kind: str = "text", encrypted: bool = False, shadowbanned: bool = False, ttl_seconds: int | None = None, expires_at: float | None = None) -> dict:
        mid = _clean_room_reaction_message_id(message_id)
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_user = str(username or "").strip()
        if not mid or not clean_room or not clean_user:
            return {}
        _prune_room_live_messages()
        now_ts = time.time()
        try:
            ttl = int(ttl_seconds or _room_live_message_ttl_seconds(clean_room))
        except Exception:
            ttl = int(ROOM_LIVE_MESSAGE_TTL_SECONDS)
        ttl = max(30, min(int(ttl), 7 * 24 * 60 * 60))
        exp = float(expires_at or (now_ts + ttl))
        meta = {
            "room": clean_room,
            "username": clean_user,
            "kind": str(kind or "text"),
            "encrypted": bool(encrypted),
            "shadowbanned": bool(shadowbanned),
            "ts": now_ts,
            "ttl_seconds": ttl,
            "expires_at": exp,
        }
        with ROOM_LIVE_MESSAGES_LOCK:
            ROOM_LIVE_MESSAGES[mid] = dict(meta)
        return meta

    def _live_room_message_meta(message_id: str) -> dict | None:
        mid = _clean_room_reaction_message_id(message_id)
        if not mid:
            return None
        _prune_room_live_messages()
        with ROOM_LIVE_MESSAGES_LOCK:
            meta = ROOM_LIVE_MESSAGES.get(mid)
            return dict(meta) if isinstance(meta, dict) else None

    def _room_reaction_counts(message_id: str, room: str) -> dict:
        mid = _clean_room_reaction_message_id(message_id)
        if not mid:
            return {}
        clean_room = _canonical_room_name(str(room or "").strip())
        with MESSAGE_REACTIONS_LOCK:
            entry = MESSAGE_REACTIONS.get(mid) or {}
            if entry.get("room") != clean_room:
                return {}
            rx = entry.get("reactions") or {}
            return {str(emoji): len(users or set()) for emoji, users in rx.items() if len(users or set()) > 0}

    def _room_pin_payload(room: str) -> dict | None:
        clean_room = _canonical_room_name(str(room or "").strip())
        if not clean_room:
            return None
        _prune_room_live_messages()
        with ROOM_PINNED_MESSAGES_LOCK:
            pin = ROOM_PINNED_MESSAGES.get(clean_room)
            return dict(pin) if isinstance(pin, dict) else None

    def _set_room_pin(room: str, message_id: str, pinned_by: str, meta: dict | None = None) -> dict:
        clean_room = _canonical_room_name(str(room or "").strip())
        mid = _clean_room_reaction_message_id(message_id)
        user = str(pinned_by or "").strip()
        if not clean_room or not mid or not user:
            return {}
        payload = {
            "room": clean_room,
            "message_id": mid,
            "pinned_by": user,
            "pinned_at": time.time(),
            "message_author": str((meta or {}).get("username") or "").strip(),
            "message_kind": str((meta or {}).get("kind") or "text"),
            "encrypted": bool((meta or {}).get("encrypted")),
        }
        with ROOM_PINNED_MESSAGES_LOCK:
            ROOM_PINNED_MESSAGES[clean_room] = dict(payload)
        return payload

    def _clear_room_pin(room: str, message_id: str | None = None) -> dict | None:
        clean_room = _canonical_room_name(str(room or "").strip())
        mid = _clean_room_reaction_message_id(message_id) if message_id else ""
        if not clean_room:
            return None
        with ROOM_PINNED_MESSAGES_LOCK:
            pin = ROOM_PINNED_MESSAGES.get(clean_room)
            if mid and (not pin or str((pin or {}).get("message_id") or "") != mid):
                return None
            return ROOM_PINNED_MESSAGES.pop(clean_room, None)


    def _official_join_context(room: str, requested_room: str | None = None) -> dict:
        """Return browser-safe official-room context for a join acknowledgement."""
        actual = _canonical_room_name(str(room or "").strip())
        requested = _canonical_room_name(str(requested_room or actual or "").strip())
        base = actual
        shard = _parse_room_shard(actual)
        autosplit_from = None
        if shard:
            base, _n = shard
            autosplit_from = base
        try:
            catalog = read_official_room_catalog()
            hit = find_catalog_room_location(catalog, base)
        except Exception:
            hit = None
        if not hit:
            return {
                "is_official_room": False,
                "is_custom_room": False,
                "requested_room": requested,
                "autosplit_from": autosplit_from,
            }
        meta = hit.get("meta") or {}
        return {
            "is_official_room": True,
            "is_custom_room": False,
            "requested_room": requested,
            "official_room": str(hit.get("name") or base).strip(),
            "category": str(hit.get("category") or "").strip(),
            "subcategory": str(hit.get("subcategory") or "").strip(),
            "meta": meta if isinstance(meta, dict) else {},
            "autosplit_from": autosplit_from,
            "autosplit": bool(autosplit_from),
        }

    def _join_success_payload(room: str, requested_room: str, username: str, history: list | None = None) -> dict:
        meta = None
        try:
            meta = get_custom_room_meta(room)
        except Exception:
            meta = None
        shard = _parse_room_shard(room)
        payload = {
            "success": True,
            "room": room,
            "requested_room": requested_room,
            "history": history or [],
            "users": _live_room_users(room),
            "is_custom_room": bool(meta),
            "is_private_room": bool(meta and meta.get("is_private")),
            "autosplit_created": False,
            "autosplit_routed": bool(shard and _canonical_room_name(str(requested_room or "").strip()) != _canonical_room_name(str(room or "").strip())),
            "autosplit_base": str(shard[0]).strip() if shard else None,
            "autosplit_shard": int(shard[1]) if shard else None,
        }
        if meta:
            try:
                room_role = get_custom_room_user_role(room, username)
                can_room_moderate = bool(can_user_moderate_custom_room(room, username))
            except Exception:
                room_role = None
                can_room_moderate = False
            payload.update({
                "category": str(meta.get("category") or "").strip(),
                "subcategory": str(meta.get("subcategory") or "").strip(),
                "room_owner": str(meta.get("created_by") or "").strip() or None,
                "my_room_role": room_role,
                "can_room_moderate": bool(can_room_moderate),
                "meta": {
                    "name": room,
                    "created_by": str(meta.get("created_by") or "").strip(),
                    "is_private": bool(meta.get("is_private")),
                    "is_18_plus": bool(meta.get("is_18_plus")),
                    "is_nsfw": bool(meta.get("is_nsfw")),
                    "category": str(meta.get("category") or "").strip(),
                    "subcategory": str(meta.get("subcategory") or "").strip(),
                },
            })
        else:
            payload.update(_official_join_context(room, requested_room))
        return payload

    def _enforce_private_room_access(room: str, username: str, *, sid: str | None = None, notify: bool = True) -> tuple[bool, str]:
        """Fail closed for invite-only custom rooms on every room action.

        Join checks are not enough: a stale socket/session, cached tab, or old
        member row could leave the browser visually inside a private room.  When
        access no longer checks out, remove that socket from the Socket.IO room
        and make the caller re-enter through a valid invite/member grant.
        """
        try:
            denied, reason = _private_custom_room_access_denied(room, username)
        except Exception:
            denied, reason = True, "Private room invite required."
        if not denied:
            return True, ""

        reason = reason or "Private room invite required."
        if sid:
            try:
                if get_connected_room(sid) == room:
                    try:
                        leave_room(room)
                    except Exception:
                        pass
                    try:
                        upsert_connected_session(sid, username, None)
                    except Exception:
                        try:
                            update_connected_room(sid, None)
                        except Exception:
                            pass
                    try:
                        _emit_room_users_snapshot(room)
                        _emit_room_counts_snapshot()
                    except Exception:
                        pass
            except Exception:
                pass
            if notify:
                try:
                    emit("notification", {"room": room, "message": "🔒 " + reason}, to=sid)
                except Exception:
                    pass
        return False, reason

    @socketio.on("get_rooms")
    @jwt_required()
    def handle_get_rooms(data=None):
        """
        Emit the list of all rooms (fetched from DB).
        """
        sid = request.sid
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_rooms", data or {}, default_max_bytes=2048, default_limit=60, default_window=60)
        if guard:
            return guard
        okrl, retry, auto_muted = _socket_action_rate_ok(
            username,
            "get_rooms",
            "room_catalog_rate_limit",
            "room_catalog_rate_window_sec",
            default_limit=30,
            default_window=10,
            strike_reason="room_catalog_rate",
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}
        try:
            rooms = get_all_rooms()
            # Hide invite-only custom rooms from the global Socket.IO room list
            # unless this user is the owner, invited, or already a persisted
            # private-room member. The REST /api/rooms route applies the same
            # visibility rule.
            visible_rooms = []
            for rr in rooms:
                name = str((rr or {}).get("name") or "").strip()
                if not name:
                    continue
                try:
                    denied_private, _reason = _private_custom_room_visibility_denied(name, username)
                except Exception:
                    # Room-list privacy fails closed: if the custom/private
                    # access helper cannot prove visibility, do not include the
                    # row in the caller's global Socket.IO room list.
                    denied_private = True
                if denied_private:
                    continue
                visible_rooms.append(rr)
            rooms = visible_rooms
            try:
                live = _visible_live_room_counts(username)
                for rr in rooms:
                    name = rr.get("name")
                    if not name:
                        continue
                    c = live.get(str(name), 0)
                    rr["member_count"] = int(c or 0)
                    rr["members"] = int(c or 0)
            except Exception:
                pass
            emit("room_list", {"rooms": rooms}, to=sid)
            return {"success": True, "rooms": rooms}

        except Exception as e:
            print("Error in get_rooms:", e)
            emit("room_list", {"rooms": [], "error": str(e)}, to=sid)
            return {"success": False, "rooms": [], "error": str(e)}




    @socketio.on("get_room_counts")
    @jwt_required()
    def handle_get_room_counts(data=None):
        """Return privacy-filtered live room counts for the signed-in user."""
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=False)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_room_counts", data or {}, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard is not None:
            return guard
        okrl, retry, _auto_muted = _socket_action_rate_ok(
            username,
            "room_counts",
            "room_counts_rate_limit",
            "room_counts_rate_window_sec",
            default_limit=60,
            default_window=60,
            strike_reason=None,
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry}
        return _room_counts_payload(username)

    
    @socketio.on("get_room_media_state")
    @jwt_required()
    def handle_get_room_media_state(data=None):
        data = data or {}
        room = str(data.get("room") or get_connected_room(request.sid) or "").strip()
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_room_media_state", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard is not None:
            return guard
        if not room:
            return {"success": False, "error": "missing_room"}
        if get_connected_room(request.sid) != room:
            return {"success": False, "error": "Not in that room"}
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, username, sid=request.sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}
        if not _room_media_enabled(room):
            return {"success": True, "supported": False, "room": room}
        _room_media_touch_listener(room, username, active=True)
        payload = _room_media_payload(room)
        if not payload:
            return {"success": False, "error": "room_media_unavailable"}
        emit("room_media_state_sync", payload, to=request.sid)
        return {"success": True, **payload}

    @socketio.on("room_media_presence")
    @jwt_required()
    def handle_room_media_presence(data=None):
        data = data or {}
        room = str(data.get("room") or get_connected_room(request.sid) or "").strip()
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "room_media_presence", data, default_max_bytes=4096, default_limit=90, default_window=60)
        if guard is not None:
            return guard
        if not room:
            return {"success": False, "error": "missing_room"}
        if get_connected_room(request.sid) != room:
            return {"success": False, "error": "Not in that room"}
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, username, sid=request.sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}
        if not _room_media_enabled(room):
            # Do not dirty the listener/vote maps for ordinary chat rooms. If a
            # room was recently edited from radio back to normal, clear this user's
            # old radio presence so active listener counts converge immediately.
            _room_media_touch_listener(room, username, active=False)
            return {"success": True, "supported": False, "room": room, "active": False, "listener_count": 0}
        okrl, retry, _auto_muted = _socket_action_rate_ok(
            username,
            "room_media_presence",
            "room_media_presence_rate_limit",
            "room_media_presence_rate_window_sec",
            default_limit=30,
            default_window=30,
            strike_reason=None,
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry}
        active = bool(data.get("active", True))
        suppress_inactive_for_other_tabs = False
        if not active:
            try:
                same_user_targets = [
                    target_sid
                    for target_sid, target_user in connected_room_targets(room)
                    if str(target_user or "").strip() == str(username or "").strip()
                ]
                suppress_inactive_for_other_tabs = len(set(same_user_targets)) > 1
            except Exception:
                suppress_inactive_for_other_tabs = False
        count = _room_media_listener_count(room) if suppress_inactive_for_other_tabs else _room_media_touch_listener(room, username, active=active)
        payload = _room_media_payload(room)
        if payload and not suppress_inactive_for_other_tabs:
            emit("room_media_state_sync", payload, to=room)
        return {
            "success": True,
            "room": room,
            "active": active,
            "listener_count": count,
            "user_still_present": bool(suppress_inactive_for_other_tabs),
        }

    @socketio.on("room_media_set_source")
    @jwt_required()
    def handle_room_media_set_source(data=None):
        data = data or {}
        room = str(data.get("room") or get_connected_room(request.sid) or "").strip()
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "room_media_set_source", data, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard
        if not room:
            return {"success": False, "error": "missing_room"}
        if get_connected_room(request.sid) != room:
            return {"success": False, "error": "Not in that room"}
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, username, sid=request.sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}
        if not _room_media_enabled(room):
            return {"success": False, "error": "room_media_disabled"}
        ok, err = _require_not_sanctioned(username, action="send")
        if not ok:
            return {"success": False, "error": err or "send_denied"}
        stations = _room_media_stations(room)
        if not stations:
            return {"success": False, "error": "No radio sources configured"}
        okrl, retry, auto_muted = _socket_action_rate_ok(
            username,
            "room_media_action",
            "room_media_action_rate_limit",
            "room_media_action_rate_window_sec",
            default_limit=10,
            default_window=30,
            strike_reason="room_media_rate",
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}
        station_index = data.get("station_index")
        if station_index is None:
            requested = str((data.get("station") or {}).get("page_url") or (data.get("station") or {}).get("embed_url") or "").strip()
            resolved = 0
            for idx, station in enumerate(stations):
                if requested and requested in {str(station.get("page_url") or ""), str(station.get("embed_url") or "")}:
                    resolved = idx
                    break
            station_index = resolved
        try:
            resolved_index = int(station_index or 0)
        except Exception:
            return {"success": False, "error": "invalid_station_index"}
        payload = _room_media_set_station(room, resolved_index, username, reason="select")
        return {"success": True, **(payload or {})}

    @socketio.on("room_media_vote_skip")
    @jwt_required()
    def handle_room_media_vote_skip(data=None):
        data = data or {}
        room = str(data.get("room") or get_connected_room(request.sid) or "").strip()
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "room_media_vote_skip", data, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard
        if not room:
            return {"success": False, "error": "missing_room"}
        if get_connected_room(request.sid) != room:
            return {"success": False, "error": "Not in that room"}
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, username, sid=request.sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}
        if not _room_media_enabled(room):
            return {"success": False, "error": "room_media_disabled"}
        ok, err = _require_not_sanctioned(username, action="send")
        if not ok:
            return {"success": False, "error": err or "send_denied"}
        okrl, retry, auto_muted = _socket_action_rate_ok(
            username,
            "room_media_action",
            "room_media_action_rate_limit",
            "room_media_action_rate_window_sec",
            default_limit=10,
            default_window=30,
            strike_reason="room_media_rate",
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}
        payload, switched, status = _room_media_vote_skip(room, username)
        try:
            if switched:
                station_label = ((payload or {}).get("station") or {}).get("label") or "the next source"
                emit(
                    "chat_message",
                    {
                        "room": room,
                        "message_id": uuid.uuid4().hex,
                        "username": "System",
                        "message": f"⏭️ Vote skip passed. Switched {room} to {station_label}.",
                        "encrypted": False,
                        "ts": time.time(),
                        "message_kind": "text",
                    },
                    to=room,
                )
            elif status == "no_alternate":
                emit(
                    "chat_message",
                    {
                        "room": room,
                        "message_id": uuid.uuid4().hex,
                        "username": "System",
                        "message": "⏭️ Skip vote reached the threshold, but this room only has one source configured right now.",
                        "encrypted": False,
                        "ts": time.time(),
                        "message_kind": "text",
                    },
                    to=room,
                )
            else:
                required = int((payload or {}).get("required_votes") or 1)
                votes = int((payload or {}).get("votes") or 0)
                emit(
                    "chat_message",
                    {
                        "room": room,
                        "message_id": uuid.uuid4().hex,
                        "username": "System",
                        "message": f"⏭️ {username} voted to skip ({votes}/{required}). Type /skip to vote too.",
                        "encrypted": False,
                        "ts": time.time(),
                        "message_kind": "text",
                    },
                    to=room,
                )
        except Exception:
            pass
        return {"success": True, "switched": bool(switched), "status": status, **(payload or {})}

    
    # ───────────────────────────────────────────────────────────────────────────
    # Private Groups (Socket.IO)
    # Hardened:
    #   - membership enforcement for join/send
    #   - mute enforcement
    #   - message length limits
    #   - persistence to messages table using room key "g:<group_id>" (for unread)
    #   - basic rate limiting
    # ───────────────────────────────────────────────────────────────────────────

    _GROUP_RATE: dict[str, deque] = {}
    _GROUP_RATE_LOCK = threading.Lock()


    @socketio.on("join")
    @jwt_required()
    def handle_join(data):
        room = (data or {}).get("room")
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "join", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard:
            return guard
        sid = request.sid

        if not room:
            emit("notification", {"room": None, "message": "Room name missing"}, to=sid)
            return {"success": False, "error": "missing_room"}

        try:
            okname, nameerr = _validate_room_name(room)
            if not okname:
                emit("notification", {"room": None, "message": nameerr or "Invalid room"}, to=sid)
                return {"success": False, "error": nameerr or "invalid_room"}

            requested_room = (room or "").strip()
            requested_room = _canonical_room_name(requested_room)
            previous_room = get_connected_room(sid)
            auto_join_created_custom_room = _is_recent_custom_room_create_auto_join(requested_room, username, data)

            access_ok, private_reason = _enforce_private_room_access(requested_room, username, sid=sid)
            if not access_ok:
                return {"success": False, "error": "invite_required", "reason": private_reason}

            # Anti-abuse: rate/cooldown checks must run before autosplit may
            # create a new overflow shard. Otherwise a modified client could spam
            # direct requests such as "Introductions (99)" and create rooms before
            # join throttles fire. Same-room reassertions remain exempt so browser
            # restore/roster self-heal does not get blocked.
            if previous_room != requested_room and not auto_join_created_custom_room:
                okj, retry = _join_rate_ok(username)
                if not okj:
                    if _abuse_strike(username, "join_rate"):
                        emit("notification", {"room": requested_room, "message": "🚫 Auto-muted for spam/abuse guard."}, to=sid)
                    else:
                        emit("notification", {"room": requested_room, "message": f"⏳ Join rate limited (wait {retry:.1f}s)"}, to=sid)
                    return {"success": False, "error": "join_rate_limited"}

                if previous_room:
                    ok_switch, switch_retry = _room_switch_cooldown_ok(username, previous_room, requested_room)
                    if not ok_switch:
                        if _abuse_strike(username, "room_switch_cooldown"):
                            emit("notification", {"room": previous_room, "message": "🚫 Auto-muted for room switching spam."}, to=sid)
                        else:
                            emit(
                                "notification",
                                {"room": previous_room, "message": f"⏳ Room switching is briefly limited (wait {switch_retry:.1f}s)."},
                                to=sid,
                            )
                        return {"success": False, "error": "room_switch_cooldown", "retry_after": switch_retry}

            # Autoscale: Lobby full -> Lobby (2) etc.  If this socket is already
            # recorded in the requested room, keep the exact room. Same-room
            # reassertions are used by reconnect/roster self-heal and must not
            # route the user away just because their own presence makes the base
            # room look full.
            if previous_room and previous_room == requested_room:
                room, created_new = previous_room, False
            else:
                room, created_new = _select_autoscaled_room(requested_room)

            # Notify clients that the room list may have changed (new shard)
            if created_new:
                try:
                    socketio.emit("rooms_changed", {"base": requested_room, "created": room})
                except Exception:
                    pass

            existed = _room_exists(room)
            if not existed:
                emit("notification", {"room": room, "message": "🚫 Room does not exist."}, to=sid)
                return {"success": False, "error": "room_not_found"}

            # If this is a custom room, enforce privacy + 18+ rules.
            try:
                meta = get_custom_room_meta(room)
            except Exception:
                meta = None

            if meta:
                if meta.get("is_private"):
                    access_ok, private_reason = _enforce_private_room_access(room, username, sid=sid)
                    if not access_ok:
                        return {"success": False, "error": "invite_required", "reason": private_reason}

                if meta.get("is_18_plus") or meta.get("is_nsfw"):
                    try:
                        conn = get_db()
                        with conn.cursor() as cur:
                            cur.execute("SELECT age FROM users WHERE username=%s;", (username,))
                            row = cur.fetchone()
                        age = int(row[0] or 0) if row else 0
                    except Exception:
                        age = 0
                    if age < 18:
                        emit("notification", {"room": room, "message": "⛔ 18+ room (age restriction)."}, to=sid)
                        return {"success": False, "error": "age_restricted"}

            ok, err = _require_not_sanctioned(username, action="join")
            if not ok:
                emit("notification", {"room": room, "message": err or "Join denied"}, to=sid)
                return {"success": False, "error": err or "join_denied"}

            if is_user_sanctioned(username, f"room_ban:{room}"):
                emit("notification", {"room": room, "message": "⛔ You are banned from this room."}, to=sid)
                return {"success": False, "error": "room_banned"}

            try:
                locked_for_join = bool(_room_locked(room))
            except Exception:
                emit("notification", {"room": room, "message": "🔒 Room policy is temporarily unavailable."}, to=sid)
                return {"success": False, "error": "room_policy_unavailable"}
            if previous_room != room and locked_for_join and not _can_override_room_lock(username):
                emit("notification", {"room": room, "message": "🔒 Room is locked."}, to=sid)
                return {"success": False, "error": "room_locked"}

            # Room history is intentionally disabled.
            def _load_history():
                return []

            # Reassert membership if already in that room. This is intentionally
            # stronger than a no-op: after browser restore, reconnect, Redis TTL
            # refresh, or a missed Socket.IO room join, the client may need the
            # server to join the Socket.IO room again and refresh shared state
            # without incrementing persisted room counts or broadcasting another
            # entered-room notification.
            if previous_room == room:
                try:
                    join_room(room)
                except Exception:
                    pass
                try:
                    upsert_connected_session(sid, username, room)
                except Exception:
                    pass
                try:
                    _record_recent_room_join(username, room)
                except Exception:
                    pass
                try:
                    touch_custom_room_activity(room)
                except Exception:
                    pass

                # Persist private custom-room membership/owner role on reassert
                # too, so stale invite/member repair does not depend on a full
                # leave-and-rejoin cycle.
                try:
                    if meta and str(meta.get("created_by") or "").strip().lower() == str(username or "").strip().lower():
                        record_custom_room_membership(room, username, role="owner")
                    elif meta and meta.get("is_private"):
                        record_custom_room_membership(room, username)
                except Exception:
                    pass

                # Send current policy state (UI toggles).
                try:
                    _emit_room_policy_state(room, username, to_sid=sid)
                except Exception:
                    pass

                # The client may have missed the previous roster broadcast after
                # a tab restore/reconnect. Re-send a direct snapshot even when
                # the server sees this as a same-room join.
                try:
                    _emit_room_users_snapshot(room, to_sid=sid)
                    _emit_room_counts_snapshot(to_sid=sid, username=username)
                except Exception:
                    pass

                try:
                    if _room_media_enabled(room):
                        _room_media_touch_listener(room, username, active=True)
                        _room_media_emit_state(room, to_sid=sid)
                except Exception:
                    pass

                payload = _join_success_payload(room, requested_room, username, _load_history())
                payload["auto_joined_created_custom_room"] = bool(auto_join_created_custom_room)
                payload["same_room_reasserted"] = True
                payload["autosplit_created"] = False
                return payload

            # Leave previous room
            if previous_room:
                try:
                    if _voice_room_remove(previous_room, username):
                        emit(
                            "voice_room_user_left",
                            {"room": previous_room, "username": username},
                            room=previous_room,
                        )
                except Exception:
                    pass

                leave_room(previous_room)
                try:
                    increment_room_count(previous_room, -1)
                except Exception:
                    pass

                try:
                    touch_custom_room_activity(previous_room)
                except Exception:
                    pass
                emit("notification", {"room": previous_room, "message": f"{username} has left {previous_room}."}, room=previous_room)

                # Update in-memory room membership immediately (prevents ghost users/counts).
                try:
                    update_connected_room(sid, None)
                    _emit_room_users_snapshot(previous_room)
                except Exception:
                    pass

            join_room(room)
            try:
                increment_room_count(room, 1)
            except Exception:
                pass

            # Persist private custom-room membership so invitees can rejoin, and
            # keep the creator's room-scoped owner/moderator role attached to this
            # room only.  This does not grant global admin permissions.
            try:
                if meta and str(meta.get("created_by") or "").strip().lower() == str(username or "").strip().lower():
                    record_custom_room_membership(room, username, role="owner")
                elif meta and meta.get("is_private"):
                    record_custom_room_membership(room, username)
            except Exception:
                pass

            # Consume generic room invite notifications so they do not linger.
            try:
                consume_room_invites(room, username)
            except Exception:
                pass

            try:
                touch_custom_room_activity(room)
            except Exception:
                pass

            upsert_connected_session(sid, username, room)
            try:
                _record_recent_room_join(username, room)
            except Exception:
                pass

            # Broadcast updated room user list (keeps the USERS panel accurate).
            try:
                _emit_room_users_snapshot(room)
            except Exception:
                pass

            # Send current room policy + room-scoped custom-room moderation state
            # to the joining user.
            try:
                _emit_room_policy_state(room, username, to_sid=sid)
            except Exception:
                pass

            try:
                if _room_media_enabled(room):
                    _room_media_touch_listener(room, username, active=True)
                    _room_media_emit_state(room, to_sid=sid)
            except Exception:
                pass

            log_audit_event(username, f"joined room {room}")
            emit(
                "notification",
                {"room": room, "message": f"{username} has entered {room}.", "kind": "room_presence"},
                room=room,
                skip_sid=sid,
            )

            # Broadcast updated live room counts for room browser UI
            try:
                _emit_room_counts_snapshot()
            except Exception:
                pass

            payload = _join_success_payload(room, requested_room, username, _load_history())
            payload["auto_joined_created_custom_room"] = bool(auto_join_created_custom_room)
            payload["autosplit_created"] = bool(created_new)
            return payload

        except Exception as e:
            print(f"[ERROR] handle_join: {e}")
            return {"success": False, "error": "server_error"}



    def _room_history_disabled_payload(room: str, *, reason: str = "room_history_disabled") -> dict:
        """Return an explicit, cache-neutral room-history shape without exposing old DB rows."""
        clean_room = _canonical_room_name(str(room or "").strip())
        return {
            "success": True,
            "room": clean_room,
            "history": [],
            "history_enabled": False,
            "history_disabled": True,
            "reason": str(reason or "room_history_disabled"),
            "has_more": False,
            "oldest_id": None,
            "newest_id": None,
        }

    def _room_join_state_matches_request(requested_room: str, actual_room: str) -> bool:
        """Return true when a recovered join state satisfies the user's request.

        The browser may time out waiting for the original join ACK on slow
        Replit/Gunicorn boots even though the server already finished the join.
        This helper lets a follow-up probe accept the exact room or an
        autosplit shard for the same base room without accepting an unrelated
        stale room from an earlier session.
        """
        requested = _canonical_room_name(str(requested_room or "").strip())
        actual = _canonical_room_name(str(actual_room or "").strip())
        if not actual:
            return False
        if not requested or requested == actual:
            return True
        try:
            req_shard = _parse_room_shard(requested)
        except Exception:
            req_shard = None
        try:
            act_shard = _parse_room_shard(actual)
        except Exception:
            act_shard = None
        req_base = str(req_shard[0]).strip() if req_shard else requested
        act_base = str(act_shard[0]).strip() if act_shard else actual
        return bool(req_base and act_base and req_base == act_base)


    @socketio.on("get_join_state")
    @jwt_required()
    def handle_get_join_state(data):
        """Probe the authoritative room state after a lost/late join ACK.

        This is intentionally read-only. It does not join, leave, create rooms,
        or bypass membership checks. It only lets the browser recover when the
        first join event completed server-side but the ACK arrived too late for
        the client timeout.
        """
        data = data or {}
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_join_state", data, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard

        sid = request.sid
        requested_room = _canonical_room_name(str(data.get("requested_room") or data.get("room") or "").strip())
        current_room = _canonical_room_name(str(get_connected_room(sid) or "").strip())
        if not current_room:
            return {"success": False, "error": "no_current_room", "requested_room": requested_room}
        if not _room_join_state_matches_request(requested_room, current_room):
            return {
                "success": False,
                "error": "current_room_mismatch",
                "requested_room": requested_room,
                "current_room": current_room,
            }
        if not _room_exists(current_room):
            return {"success": False, "error": "room_not_found", "requested_room": requested_room, "current_room": current_room}

        access_ok, private_reason = _enforce_private_room_access(current_room, username, sid=sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}

        try:
            _emit_room_users_snapshot(current_room, to_sid=sid)
            _emit_room_counts_snapshot(to_sid=sid, username=username)
        except Exception:
            pass
        try:
            _emit_room_policy_state(current_room, username, to_sid=sid)
        except Exception:
            pass

        payload = _join_success_payload(current_room, requested_room or current_room, username, [])
        payload["same_room_reasserted"] = True
        payload["recovered_from_ack_timeout"] = True
        payload["autosplit_created"] = False
        return payload


    @socketio.on("get_room_history")
    @jwt_required()
    def handle_get_room_history(data):
        """Fetch room history metadata while honoring Echo-Chat's no-room-history policy."""
        data = data or {}
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_room_history", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard is not None:
            return guard

        room = _canonical_room_name(str(data.get("room") or "").strip())
        if not room:
            return {"success": False, "error": "Missing room"}

        sid = request.sid
        current_room = get_connected_room(sid)
        if current_room != room:
            return {"success": False, "error": "Not in that room"}
        access_ok, private_reason = _enforce_private_room_access(room, username, sid=sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}

        # Room chat is live-only by design. Do not read the shared `messages`
        # table here: that table is used by groups/legacy migrations, and
        # returning those rows would leak stale or cross-feature data into rooms.
        return _room_history_disabled_payload(room)

    def _best_effort_leave_socket_room(room: str) -> None:
        """Leave the Socket.IO room even when shared state is already stale."""
        try:
            leave_room(room)
        except Exception:
            pass

    def _room_user_still_present(room: str, username: str) -> bool:
        try:
            return str(username or "").strip() in set(_live_room_users(room))
        except Exception:
            return False

    def _emit_leave_room_refresh(room: str, *, to_sid: str | None = None) -> None:
        try:
            _emit_room_users_snapshot(room, to_sid=to_sid)
        except Exception:
            pass
        try:
            _emit_room_counts_snapshot(to_sid=to_sid)
        except Exception:
            pass

    def _clear_room_media_presence(room: str, username: str, *, broadcast: bool) -> None:
        try:
            _room_media_touch_listener(room, username, active=False)
            if broadcast and _room_media_enabled(room):
                _room_media_emit_state(room)
        except Exception:
            pass

    @socketio.on("leave")
    @jwt_required()
    def handle_leave(data):
        data = data or {}
        room = _canonical_room_name(str(data.get("room") or "").strip())
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "leave", data, default_max_bytes=4096, default_limit=90, default_window=60)
        if guard:
            return guard
        sid = request.sid

        if not room:
            return {"success": False, "error": "Room name missing"}

        current_room = get_connected_room(sid)
        if current_room != room:
            # Idempotent leave: the shared session is already out of this room,
            # but a tab restore/pagehide race can still leave the Socket.IO
            # membership, voice roster, or radio listener map dirty. Clean those
            # best-effort without decrementing counts or broadcasting a fake leave.
            _best_effort_leave_socket_room(room)
            still_in_requested_room = _room_user_still_present(room, username)
            if not still_in_requested_room:
                _clear_room_typing(room, username, broadcast=True, skip_sid=sid)
                _clear_room_media_presence(room, username, broadcast=True)
                try:
                    if _voice_room_remove(room, username):
                        emit("voice_room_user_left", {"room": room, "username": username}, room=room)
                except Exception:
                    pass
            _emit_leave_room_refresh(room, to_sid=sid)
            return {
                "success": True,
                "left_room": False,
                "already_left": True,
                "room": room,
                "current_room": current_room,
            }

        _best_effort_leave_socket_room(room)

        try:
            touch_custom_room_activity(room)
        except Exception:
            pass

        upsert_connected_session(sid, username, None)
        still_in_same_room = _room_user_still_present(room, username)
        if not still_in_same_room:
            _clear_room_typing(room, username, broadcast=True, skip_sid=sid)

        # Only remove shared voice/media presence and decrement durable counts
        # when this username no longer has another live socket in the same room.
        if not still_in_same_room:
            try:
                if _voice_room_remove(room, username):
                    emit("voice_room_user_left", {"room": room, "username": username}, room=room)
            except Exception:
                pass
            try:
                increment_room_count(room, -1)
            except Exception:
                pass
            _clear_room_media_presence(room, username, broadcast=True)
            emit("notification", {"room": room, "message": f"🔌 {username} has left {room}.", "kind": "room_presence"}, to=room)
        else:
            # Another tab/session for this user still represents them in the room.
            # Keep room/user-visible voice/media presence intact; only this
            # Socket.IO transport membership was removed.
            pass

        _emit_leave_room_refresh(room)

        log_audit_event(username, f"left room {room}")
        return {
            "success": True,
            "left_room": True,
            "already_left": False,
            "room": room,
            "user_still_present": bool(still_in_same_room),
        }



    @socketio.on("send_message")
    @jwt_required()
    def handle_send_message(data):
        # Room messages:
        # - Legacy clients send {"room","message"} (plaintext).
        # - New clients may send {"room","cipher","keys"} (ciphertext-only envelope).
        data = data or {}
        room = _canonical_room_name(str(data.get("room") or "").strip())
        cipher = data.get("cipher")
        message = data.get("message")
        keys = data.get("keys") or data.get("key_map") or None
        dup_sig_raw = (data or {}).get("dup_sig_raw")
        dup_sig_norm = (data or {}).get("dup_sig_norm")
        dup_plain_len = (data or {}).get("dup_plain_len")
        message_kind = infer_room_message_kind(
            declared_kind=(data or {}).get("message_kind") or (data or {}).get("content_kind") or (data or {}).get("kind"),
            plaintext=message,
            cipher=cipher,
        )
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "send_message", data, default_max_bytes=131072, default_limit=120, default_window=60)
        if guard is not None:
            return guard

        if not room:
            return {"success": False, "error": "Missing room"}

        require_e2ee = bool(settings.get("require_room_e2ee", False))
        if not require_e2ee and bool(settings.get("require_private_room_e2ee", True)):
            try:
                meta = get_custom_room_meta(room)
                require_e2ee = bool(meta and meta.get("is_private"))
            except Exception:
                require_e2ee = False

        if require_e2ee and not cipher:
            # Allow supported slash commands in plaintext, even when the room requires E2EE for messages.
            if isinstance(message, str) and message.strip().lower().startswith(("/invite", "/skip")):
                pass
            else:
                return {"success": False, "error": "This room requires encrypted messages"}

        if (not cipher) and (message is None):
            return {"success": False, "error": "Missing message"}
        if cipher is None and isinstance(message, str) and not message.strip():
            return {"success": False, "error": "Missing message"}

        ok, err = _require_not_sanctioned(username, action="send")
        if not ok:
            return {"success": False, "error": err}

        sid = request.sid
        current_room = get_connected_room(sid)
        if current_room != room:
            try:
                for target_sid, target_user in list(_socketio_room_targets(room)) if callable(globals().get("_socketio_room_targets")) else []:
                    if str(target_sid or "") == str(sid or "") and str(target_user or "").strip().lower() == str(username or "").strip().lower():
                        current_room = room
                        break
            except Exception:
                pass
        if current_room != room:
            return {"success": False, "error": "Not in that room"}
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}

        access_ok, private_reason = _enforce_private_room_access(room, username, sid=sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}

        # Touch activity only after live membership is confirmed. A forged
        # payload must not keep unrelated custom rooms alive.
        try:
            touch_custom_room_activity(room)
        except Exception:
            pass

        # Slash commands (plaintext only).
        # Server-side safety net: never broadcast control commands into chat history even if a client fails to intercept.
        if cipher is None and isinstance(message, str):
            m = message.strip()
            if m.lower().startswith("/skip"):
                if not _room_media_enabled(room):
                    return {"success": False, "error": "This room does not support vote skip"}
                okrl, retry, auto_muted = _socket_action_rate_ok(
                    username,
                    "room_media_action",
                    "room_media_action_rate_limit",
                    "room_media_action_rate_window_sec",
                    default_limit=10,
                    default_window=30,
                    strike_reason="room_media_rate",
                )
                if not okrl:
                    return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}
                payload, switched, status = _room_media_vote_skip(room, username)
                try:
                    if switched:
                        station_label = ((payload or {}).get("station") or {}).get("label") or "the next source"
                        emit(
                            "chat_message",
                            {
                                "room": room,
                                "message_id": uuid.uuid4().hex,
                                "username": "System",
                                "message": f"⏭️ Vote skip passed. Switched {room} to {station_label}.",
                                "encrypted": False,
                                "ts": time.time(),
                                "message_kind": "text",
                            },
                            to=room,
                        )
                    elif status == "no_alternate":
                        emit(
                            "chat_message",
                            {
                                "room": room,
                                "message_id": uuid.uuid4().hex,
                                "username": "System",
                                "message": "⏭️ Skip vote reached the threshold, but this room only has one source configured right now.",
                                "encrypted": False,
                                "ts": time.time(),
                                "message_kind": "text",
                            },
                            to=room,
                        )
                    else:
                        required = int((payload or {}).get("required_votes") or 1)
                        votes = int((payload or {}).get("votes") or 0)
                        emit(
                            "chat_message",
                            {
                                "room": room,
                                "message_id": uuid.uuid4().hex,
                                "username": "System",
                                "message": f"⏭️ {username} voted to skip ({votes}/{required}). Type /skip to vote too.",
                                "encrypted": False,
                                "ts": time.time(),
                                "message_kind": "text",
                            },
                            to=room,
                        )
                except Exception:
                    pass
                return {"success": True, "command": "skip", "switched": bool(switched), "status": status, **(payload or {})}
            if m.lower().startswith("/invite"):
                parts = m.split()
                if len(parts) < 2:
                    return {"success": False, "error": "Usage: /invite <username>"}
                invitee = parts[1].lstrip("@").strip()
                if not invitee:
                    return {"success": False, "error": "Usage: /invite <username>"}
                if invitee.lower() == str(username or "").strip().lower():
                    return {"success": False, "error": "Cannot invite yourself"}

                invite_lim, invite_win = _parse_rate_limit(
                    settings.get("room_invite_rate_limit"),
                    default_limit=5,
                    default_window=60,
                )
                try:
                    invite_win = int(settings.get("room_invite_rate_window_sec") or invite_win)
                except Exception:
                    pass
                ok_invite_rl, invite_retry = _rl(f"roominvite:{username}", invite_lim, invite_win)
                if not ok_invite_rl:
                    if _abuse_strike(username, "room_invite_rate"):
                        return {"success": False, "error": "Auto-muted for invite spam. Try again later."}
                    return {"success": False, "error": f"Invite rate limited (wait {invite_retry:.1f}s)"}

                # Persist invite (so offline users still see it) + push realtime notification
                kind = "room"
                event = "room_invite"
                delivered = False
                conn = None
                try:
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("SELECT username FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;", (invitee,))
                        _urow = cur.fetchone()
                        if _urow is None:
                            return {"success": False, "error": "User not found"}
                        invitee = str(_urow[0])
                        if invitee.lower() == str(username or "").strip().lower():
                            return {"success": False, "error": "Cannot invite yourself"}
                        if _either_blocked(username, invitee):
                            return {"success": False, "error": "You cannot invite this user"}
                        # If this is a custom room, detect privacy with canonical/case-insensitive lookup.
                        cur.execute("SELECT name, created_by, is_private FROM custom_rooms WHERE LOWER(name)=LOWER(%s) LIMIT 1;", (room,))
                        crow = cur.fetchone()
                        canonical_room = str(crow[0]) if crow else room
                        created_by = str(crow[1] or "") if crow else ""
                        is_private = bool(crow[2]) if crow else False

                        # Ensure the room exists in chat_rooms for join UI.
                        cur.execute("SELECT 1 FROM chat_rooms WHERE LOWER(name)=LOWER(%s) LIMIT 1;", (canonical_room,))
                        if cur.fetchone() is None:
                            if crow:
                                create_room_if_missing(canonical_room, room_kind="custom")
                            else:
                                return {"success": False, "error": "Room not found"}

                        if is_private:
                            # F094/S13: private custom-room invites are pending lifecycle state only;
                            # only the room owner or room-scoped moderators may expand private membership.
                            if not can_user_moderate_custom_room(canonical_room, username):
                                return {"success": False, "error": "Only the room owner or a room moderator can invite users to this private room"}
                            if created_by.strip().lower() == invitee.lower():
                                return {"success": False, "error": "User already has access to this private room"}
                            cur.execute(
                                """
                                SELECT 1
                                  FROM custom_room_members
                                 WHERE LOWER(room_name)=LOWER(%s)
                                   AND LOWER(member_user)=LOWER(%s)
                                   AND (LOWER(COALESCE(role, '')) IN ('owner', 'moderator') OR invited_by IS NOT NULL)
                                 LIMIT 1;
                                """,
                                (canonical_room, invitee),
                            )
                            if cur.fetchone() is not None:
                                return {"success": False, "error": "User already has access to this private room"}
                            cur.execute(
                                """
                                INSERT INTO custom_room_invites (room_name, invited_user, invited_by)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (room_name, invited_user)
                                DO UPDATE SET invited_by = EXCLUDED.invited_by,
                                              created_at = NOW();
                                """,
                                (canonical_room, invitee, username),
                            )
                            kind = "custom_private"
                            event = "custom_room_invite"
                        else:
                            cur.execute(
                                """
                                INSERT INTO room_invites (room_name, invited_user, invited_by)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (room_name, invited_user)
                                DO UPDATE SET invited_by = EXCLUDED.invited_by,
                                              created_at = NOW();
                                """,
                                (canonical_room, invitee, username),
                            )
                    conn.commit()
                    delivered = bool(_emit_to_user(invitee, event, {"room": canonical_room, "by": username, "kind": kind}))
                except Exception as e:
                    try:
                        if conn: conn.rollback()
                    except Exception:
                        pass
                    return {"success": False, "error": str(e)}

                return {"success": True, "command": "invite", "room": canonical_room, "invitee": invitee, "kind": kind, "delivered": delivered}

        # Validate payload size and shape. Bad hand-edited config must not crash room chat.
        if cipher:
            if not isinstance(cipher, str) or not cipher.strip():
                return {"success": False, "error": "bad_cipher"}
            if not _looks_like_room_cipher_envelope(cipher):
                return {"success": False, "error": "bad_cipher_envelope"}
            max_cipher_len = _safe_room_send_positive_int(
                settings.get("max_room_cipher_length"),
                120000,
                minimum=1000,
                maximum=500000,
            )
            if len(cipher) > max_cipher_len:
                return {"success": False, "error": f"Ciphertext too large (max {max_cipher_len})"}

            missing_recipients = _room_e2ee_recipient_mismatch(room, username, cipher)
            if missing_recipients:
                # Do not deliver a ciphertext that omits live unblocked room users.
                # The most common trigger is block -> unblock where the client had
                # a stale one-person roster and encrypted only to itself. Ask the
                # browser to refresh room membership and rebuild the E2EE envelope.
                try:
                    _emit_room_users_snapshot(room)
                    _emit_room_counts_snapshot()
                except Exception:
                    pass
                return {
                    "success": False,
                    "error": "Room roster refreshed; please send again.",
                    "code": "room_roster_stale",
                    "refresh_room_users": True,
                    "missing_recipients": missing_recipients[:10],
                }

            if keys is not None and not isinstance(keys, dict):
                return {"success": False, "error": "bad_keys"}
            if isinstance(keys, dict):
                max_keys = _safe_room_send_positive_int(
                    settings.get("max_room_key_recipients"),
                    120,
                    minimum=1,
                    maximum=500,
                )
                if len(keys) > max_keys:
                    return {"success": False, "error": f"Too many recipients (max {max_keys})"}
        else:
            if not isinstance(message, str):
                return {"success": False, "error": "bad_message"}
            max_len = _safe_room_send_positive_int(
                settings.get("max_message_length", 1000),
                1000,
                minimum=1,
                maximum=50000,
            )
            message = sanitize_user_visible_text(message, max_len=max_len, keep_newlines=True)
            try:
                message, _ec_removed_emoticons = _filter_excess_emoticons(message)
            except Exception:
                _ec_removed_emoticons = 0
            if not message:
                return {"success": False, "error": "Missing message"}
            if len(message) > max_len:
                return {"success": False, "error": f"Message too long (max {max_len})"}

        # Read-only rooms: allow only users with room:readonly/admin override.
        try:
            readonly_for_send = bool(_room_readonly(room))
        except Exception:
            return {"success": False, "error": "room_policy_unavailable"}
        if readonly_for_send and not _can_override_room_readonly(username):
            return {"success": False, "error": "Room is read-only", "code": "room_readonly"}

        # Locked rooms: allow messages only for lock/admin override.
        try:
            locked_for_send = bool(_room_locked(room))
        except Exception:
            return {"success": False, "error": "room_policy_unavailable"}
        if locked_for_send and not _can_override_room_lock(username):
            return {"success": False, "error": "Room is locked", "code": "room_locked"}



        # Anti-abuse: room slowmode + burst rate limiting + optional per-user quota
        slowmode_stamp = None

        # Anti-spam content heuristics (plaintext rooms only)
        if cipher is None:
            okc, cerr = _antiabuse_plaintext_checks(username, room, message)
            if not okc:
                return {"success": False, "error": cerr or "Message blocked"}

        # Optional per-user quota (messages/hour) – only enforced when explicitly configured for the user.
        quota = _get_user_quota_per_hour(username)
        if quota and int(quota) > 0:
            okq, _raq = _rl(f"quota:{username}", int(quota), 3600)
            if not okq:
                _abuse_strike(username, "quota")
                return {"success": False, "error": f"Quota exceeded ({int(quota)}/hour). Try later."}

        # Burst rate limit for room messages
        lim, win = _parse_rate_limit(settings.get("room_msg_rate_limit"), default_limit=20, default_window=10)
        try:
            win = int(settings.get("room_msg_rate_window_sec") or win)
        except Exception:
            pass
        okrl, retry = _rl(f"roommsg:{username}", lim, win)
        if not okrl:
            if _abuse_strike(username, "room_rate"):
                return {"success": False, "error": "Auto-muted for spamming. Try again later."}
            return {"success": False, "error": f"Rate limited (wait {retry:.1f}s)"}

        kind_setting_map = {
            "gif": ("room_gif_rate_limit", "room_gif_rate_window_sec", 6, 20, "room_gif_rate"),
            "torrent": ("room_torrent_rate_limit", "room_torrent_rate_window_sec", 2, 30, "room_torrent_rate"),
            "file": ("room_file_rate_limit", "room_file_rate_window_sec", 2, 30, "room_file_rate"),
        }
        kind_cfg = kind_setting_map.get(message_kind)
        if kind_cfg is not None:
            limit_key, window_key, default_limit, default_window, abuse_reason = kind_cfg
            klim, kwin = _parse_rate_limit(settings.get(limit_key), default_limit=default_limit, default_window=default_window)
            try:
                kwin = int(settings.get(window_key) or kwin)
            except Exception:
                pass
            ok_kind_rl, kind_retry = _rl(f"roommsg:{message_kind}:{username}", klim, kwin)
            if not ok_kind_rl:
                if _abuse_strike(username, abuse_reason):
                    return {"success": False, "error": "Auto-muted for spamming. Try again later."}
                label = "GIF" if message_kind == "gif" else "torrent" if message_kind == "torrent" else message_kind
                return {"success": False, "error": f"{label} rate limited (wait {kind_retry:.1f}s)"}

        # Room slowmode (per user per room)
        try:
            slow = _room_slowmode_seconds(room)
        except Exception:
            return {"success": False, "error": "room_policy_unavailable"}
        if slow > 0:
            now = time.time()
            with _SLOWMODE_LAST_SENT_LOCK:
                last = float(_SLOWMODE_LAST_SENT.get((username, room), 0.0) or 0.0)
            if (now - last) < float(slow):
                remaining = float(slow) - (now - last)
                if _abuse_strike(username, "slowmode"):
                    return {"success": False, "error": "Auto-muted for spamming. Try again later."}
                return {"success": False, "error": f"Slow mode (wait {remaining:.1f}s)"}
            slowmode_stamp = now

        if slowmode_stamp is not None:
            with _SLOWMODE_LAST_SENT_LOCK:
                _SLOWMODE_LAST_SENT[(username, room)] = float(slowmode_stamp)
        # Room messages are intentionally not persisted.
        # Use an ephemeral id for live-only room chat.
        message_id = uuid.uuid4().hex

        shadowbanned_sender = False
        try:
            shadowbanned_sender = bool(_is_effectively_shadowbanned(username))
        except Exception:
            shadowbanned_sender = False
        ttl_seconds = _room_live_message_ttl_seconds(room)
        expires_at = time.time() + ttl_seconds
        live_meta = _register_live_room_message(
            message_id,
            room,
            username,
            kind=message_kind,
            encrypted=bool(cipher),
            shadowbanned=shadowbanned_sender,
            ttl_seconds=ttl_seconds,
            expires_at=expires_at,
        )
        expires_at = float((live_meta or {}).get("expires_at") or expires_at)
        ttl_seconds = int((live_meta or {}).get("ttl_seconds") or ttl_seconds)
        _clear_room_typing(room, username, broadcast=not shadowbanned_sender, skip_sid=request.sid)

        sender_avatar_url = _room_public_avatar_url(username)
        if cipher:
            chat_payload = {
                "room": room,
                "message_id": message_id,
                "username": username,
                "avatar_url": sender_avatar_url,
                # Compatibility text for older clients (does not reveal plaintext).
                "message": "🔒 Encrypted message",
                "encrypted": True,
                "cipher": cipher,
                "keys": keys,
                "ts": time.time(),
                "message_kind": message_kind,
                "ttl_seconds": ttl_seconds,
                "expires_at": expires_at,
            }
        else:
            chat_payload = {
                "room": room,
                "message_id": message_id,
                "username": username,
                "avatar_url": sender_avatar_url,
                "message": message,
                "encrypted": False,
                "ts": time.time(),
                "message_kind": message_kind,
                "ttl_seconds": ttl_seconds,
                "expires_at": expires_at,
            }
        _emit_room_chat_message_filtered(room, username, chat_payload, shadowbanned_sender=shadowbanned_sender)
        return {
            "success": True,
            "room": room,
            "message_id": message_id,
            "message_kind": message_kind,
            "encrypted": bool(cipher),
            "shadowbanned": shadowbanned_sender,
            "ttl_seconds": ttl_seconds,
            "expires_at": expires_at,
        }


    @socketio.on("get_users_in_room")
    @jwt_required()
    def handle_get_users_in_room(data):
        room = _canonical_room_name(str((data or {}).get("room") or "").strip())
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "get_users_in_room", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard:
            return guard

        ok, err = _require_live_room_membership(user, room)
        if not ok:
            return err
        access_ok, private_reason = _enforce_private_room_access(room, user, sid=request.sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}

        # Only reveal the roster for the caller's live room. This prevents
        # arbitrary room-member enumeration via forged client payloads.
        snapshot = _emit_room_users_snapshot(room, to_sid=request.sid) or {"room": room, "users": [], "count": 0}
        return {"success": True, **snapshot}



    @socketio.on("typing")
    @jwt_required()
    def handle_typing(data):
        data = data or {}
        room = _canonical_room_name(str(data.get("room") or "").strip())
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "typing", data, default_max_bytes=4096, default_limit=60, default_window=10)
        if guard is not None:
            return guard
        sid = request.sid

        if not room:
            return {"success": False, "error": "Missing room"}
        if not _feature_bool("enable_room_typing_indicators", False):
            return {"success": True, "room": room, "typing": False, "disabled": True}

        # Only broadcast typing if this socket is actually in the room.
        current_room = get_connected_room(sid)
        if current_room != room:
            return {"success": False, "error": "Not in that room"}
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, user, sid=sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}
        ok, err = _require_not_sanctioned(user, action="send")
        if not ok:
            return {"success": False, "error": err or "send_denied"}

        okrl, retry, auto_muted = _socket_action_rate_ok(
            user,
            "typing",
            "room_typing_rate_limit",
            "room_typing_rate_window_sec",
            default_limit=30,
            default_window=10,
            strike_reason="typing_rate",
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}

        _mark_room_typing(room, user)

        shadowbanned_sender = False
        try:
            shadowbanned_sender = bool(_is_effectively_shadowbanned(user))
        except Exception:
            shadowbanned_sender = False
        if not shadowbanned_sender:
            _emit_room_signal_filtered(room, user, "room_typing", _room_typing_payload(room, user, typing=True), skip_sid=sid)

        return {"success": True, "room": room, "typing": True, "expires_in": int(TYPING_EXPIRY_SECONDS)}


    @socketio.on("stop_typing")
    @jwt_required()
    def handle_stop_typing(data):
        data = data or {}
        room = _canonical_room_name(str(data.get("room") or "").strip())
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "stop_typing", data, default_max_bytes=4096, default_limit=90, default_window=10)
        if guard is not None:
            return guard
        sid = request.sid

        if not room:
            return {"success": False, "error": "Missing room"}
        if not _feature_bool("enable_room_typing_indicators", False):
            return {"success": True, "room": room, "typing": False, "disabled": True}

        current_room = get_connected_room(sid)
        if current_room != room:
            return {"success": False, "error": "Not in that room"}
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, user, sid=sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}

        okrl, retry, auto_muted = _socket_action_rate_ok(
            user,
            "typing",
            "room_typing_rate_limit",
            "room_typing_rate_window_sec",
            default_limit=30,
            default_window=10,
            strike_reason="typing_rate",
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}

        shadowbanned_sender = False
        try:
            shadowbanned_sender = bool(_is_effectively_shadowbanned(user))
        except Exception:
            shadowbanned_sender = False
        _clear_room_typing(room, user, broadcast=not shadowbanned_sender, skip_sid=sid)

        return {"success": True, "room": room, "typing": False}


    @socketio.on("react_to_message")
    @jwt_required()
    def handle_react_to_message(data):
        data = data or {}
        room = _canonical_room_name(str(data.get("room") or "").strip())
        message_id = _clean_room_reaction_message_id(data.get("message_id"))
        emoji = str(data.get("emoji") or data.get("reaction") or "👍").strip()
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "react_to_message", data, default_max_bytes=4096, default_limit=60, default_window=30)
        if guard is not None:
            return guard

        if not room or not message_id:
            return {"success": False, "error": "Missing room or message_id"}

        # Must be in the room to react, and must still be allowed to see it.
        sid = request.sid
        current_room = get_connected_room(sid)
        if current_room != room:
            return {"success": False, "error": "Not in that room"}
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, user, sid=sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}
        ok, err = _require_not_sanctioned(user, action="send")
        if not ok:
            return {"success": False, "error": err or "send_denied"}

        if emoji not in ALLOWED_REACTION_EMOJIS:
            return {"success": False, "error": "Unsupported reaction"}

        meta = _live_room_message_meta(message_id)
        if not meta or meta.get("room") != room:
            return {"success": False, "error": "Message not found in this room"}
        if meta.get("shadowbanned"):
            return {"success": False, "error": "Message not found in this room"}

        okrl, retry, auto_muted = _socket_action_rate_ok(
            user,
            "reaction",
            "room_reaction_rate_limit",
            "room_reaction_rate_window_sec",
            default_limit=12,
            default_window=30,
            strike_reason="reaction_rate",
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}

        shadowbanned_sender = False
        try:
            shadowbanned_sender = bool(_is_effectively_shadowbanned(user))
        except Exception:
            shadowbanned_sender = False
        if shadowbanned_sender:
            return {
                "success": True,
                "room": room,
                "message_id": message_id,
                "counts": _room_reaction_counts(message_id, room),
                "current": emoji,
                "shadowbanned": True,
            }

        with MESSAGE_REACTIONS_LOCK:
            entry = MESSAGE_REACTIONS.get(message_id)
            if not entry:
                entry = {"room": room, "reactions": {}, "by_user": {}, "ts": time.time()}
                MESSAGE_REACTIONS[message_id] = entry

            if entry.get("room") != room:
                return {"success": False, "error": "Message not in this room"}

            rx = entry.setdefault("reactions", {})
            by_user = entry.setdefault("by_user", {})
            existing = by_user.get(user)

            if existing:
                counts = {e: len(u_set or set()) for e, u_set in rx.items() if len(u_set or set()) > 0}
                return {
                    "success": False,
                    "error": "Reaction is final. You cannot change or undo it.",
                    "counts": counts,
                    "current": existing,
                }

            users = rx.setdefault(emoji, set())
            users.add(user)
            by_user[user] = emoji
            entry["ts"] = time.time()
            counts = {e: len(u_set or set()) for e, u_set in rx.items() if len(u_set or set()) > 0}

        emit(
            "message_reactions",
            {"room": room, "message_id": message_id, "counts": counts, "reacted_by": user},
            to=room,
        )
        return {"success": True, "room": room, "message_id": message_id, "counts": counts, "current": emoji, "shadowbanned": False}


    def _require_live_room_membership(user: str, room: str):
        room = str(room or "").strip()
        if not room:
            return False, {"success": False, "error": "Missing room"}

        sid = request.sid
        current_room = get_connected_room(sid)
        if current_room != room:
            try:
                for target_sid, target_user in list(_socketio_room_targets(room)) if callable(globals().get("_socketio_room_targets")) else []:
                    if str(target_sid or "") == str(sid or "") and str(target_user or "").strip().lower() == str(user or "").strip().lower():
                        current_room = room
                        break
            except Exception:
                pass
        if current_room != room:
            return False, {"success": False, "error": "Not in that room"}

        try:
            meta = get_custom_room_meta(room)
        except Exception:
            meta = None

        if meta and meta.get("is_private"):
            try:
                access_ok, private_reason = _enforce_private_room_access(room, user, sid=sid)
                if not access_ok:
                    return False, {"success": False, "error": "invite_required", "reason": private_reason}
            except Exception:
                return False, {"success": False, "error": "No access to that room"}

        return True, None


    def _can_manage_room_message_controls(user: str) -> bool:
        return bool(
            check_user_permission(user, "admin:basic")
            
            or check_user_permission(user, "room:delete")
            or check_user_permission(user, "moderation:kick_user")
        )


    def _room_control_rate_guard(user: str, *, bucket: str, default_limit: int = 12, default_window: int = 30):
        lim, win = _parse_rate_limit(
            settings.get("room_control_rate_limit"),
            default_limit=default_limit,
            default_window=default_window,
        )
        try:
            win = int(settings.get("room_control_rate_window_sec") or win)
        except Exception:
            pass
        return _rl(f"{bucket}:{user}", lim, win)


    def _room_has_connected_target(room: str, target: str) -> bool:
        """True only when the target currently has a live socket in this room.

        The room kick endpoint is intentionally an active-room moderation tool,
        not the private-room member manager.  A forged socket payload should not
        be able to revoke an offline user's durable invite-only membership by
        guessing their username.
        """
        room = str(room or "").strip()
        target_lc = str(target or "").strip().lower()
        if not room or not target_lc:
            return False
        for _sid, user in connected_room_targets(room):
            if str(user or "").strip().lower() == target_lc:
                return True
        return False


    def _can_room_scoped_kick(actor: str, room: str, target: str) -> tuple[bool, str | None, dict | None]:
        actor = str(actor or "").strip()
        room = str(room or "").strip()
        target = str(target or "").strip()
        if not actor or not room or not target:
            return False, "Missing room or username", None
        if actor.lower() == target.lower():
            return False, "You cannot kick yourself", None
        try:
            meta = get_custom_room_meta(room)
        except Exception:
            meta = None
        if not meta:
            return False, "Room owner tools only work in custom rooms", None
        global_kick = bool(check_user_permission(actor, "moderation:kick_user"))
        if global_kick:
            return True, None, meta

        actor_role = get_custom_room_user_role(room, actor)
        if actor_role not in {"owner", "moderator"}:
            return False, "No room moderation permission", meta

        target_role = get_custom_room_user_role(room, target)
        target_is_owner = str(meta.get("created_by") or "").strip().lower() == target.lower() or target_role == "owner"
        if target_is_owner:
            return False, "You cannot kick the room owner", meta

        # Room-scoped roles are hierarchical: owners may kick moderators/members,
        # but room moderators may only kick ordinary members. This prevents a
        # moderator from removing another moderator and taking over the room.
        if custom_room_role_rank(actor_role) <= custom_room_role_rank(target_role):
            return False, "You cannot kick a user with the same or higher room role", meta

        return True, None, meta


    @socketio.on("room_kick_user")
    @jwt_required()
    def handle_room_kick_user(data):
        """Room-scoped kick for custom room owners/moderators.

        This is intentionally narrower than the admin kick route: creating a
        custom/private room grants owner moderation only for that one room, not
        global admin powers.  For private rooms, kicking also revokes persisted
        invite/member access so the user cannot immediately rejoin.
        """
        payload = data or {}
        room = _canonical_room_name(str(payload.get("room") or "").strip())
        target = str(payload.get("username") or payload.get("target") or "").strip()
        actor = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(actor, "room_kick_user", payload, default_max_bytes=8192, default_limit=30, default_window=60)
        if guard is not None:
            return guard

        ok, err = _require_live_room_membership(actor, room)
        if not ok:
            return err
        allowed, reason, meta = _can_room_scoped_kick(actor, room, target)
        if not allowed:
            return {"success": False, "error": reason or "No permission"}
        if not _room_has_connected_target(room, target):
            return {"success": False, "error": "Target is not in that room"}

        okrl, retry = _room_control_rate_guard(actor, bucket="roomctl:kick", default_limit=10, default_window=30)
        if not okrl:
            return {"success": False, "error": f"Rate limited (wait {retry:.1f}s)"}

        affected = 0
        target_lc = target.lower()
        for sid, user in connected_room_targets(room):
            if str(user or "").strip().lower() != target_lc:
                continue
            try:
                socketio.emit("room_forced_leave", {"room": room, "reason": "kicked", "by": actor, "scoped": True}, to=sid)
            except Exception:
                pass
            try:
                socketio.server.leave_room(sid, room)
                affected += 1
            except Exception:
                pass
            try:
                update_connected_room(sid, None)
            except Exception:
                pass

        revoked_access = 0
        try:
            if meta and meta.get("is_private"):
                revoked_access = int(revoke_custom_room_access(room, target) or 0)
        except Exception:
            revoked_access = 0

        try:
            _emit_room_users_snapshot(room)
            _emit_room_counts_snapshot()
        except Exception:
            pass
        try:
            emit("admin_kick", {"username": target, "room": room, "by": actor, "scoped": True}, room=room)
            emit("notification", {"room": room, "message": f"👢 {actor} kicked {target} from {room}.", "kind": "room_moderation"}, room=room)
        except Exception:
            pass
        try:
            log_audit_event(actor, "custom_room_kick", f"{target}@{room}", f"affected={affected} revoked_access={revoked_access}")
        except Exception:
            pass
        return {"success": True, "room": room, "user": target, "affected_sessions": affected, "revoked_access": revoked_access}


    @socketio.on("pin_message")
    @jwt_required()
    def handle_pin_message(data):
        payload = data or {}
        room = _canonical_room_name(str(payload.get("room") or "").strip())
        message_id = _clean_room_reaction_message_id(payload.get("message_id"))
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "pin_message", payload, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard

        if not message_id or not room:
            return {"success": False, "error": "Missing room or message_id"}

        ok, err = _require_live_room_membership(user, room)
        if not ok:
            return err
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}

        if not _can_manage_room_message_controls(user):
            return {"success": False, "error": "No permission"}

        meta = _live_room_message_meta(message_id)
        if not meta or meta.get("room") != room or meta.get("shadowbanned"):
            return {"success": False, "error": "Message not found in this room"}

        okrl, retry = _room_control_rate_guard(user, bucket="roomctl:pin")
        if not okrl:
            return {"success": False, "error": f"Rate limited (wait {retry:.1f}s)"}

        pin = _set_room_pin(room, message_id, user, meta)
        emit("room_message_pinned", pin, to=room)
        return {"success": True, "room": room, "message_id": message_id, "pin": pin}


    @socketio.on("unpin_message")
    @jwt_required()
    def handle_unpin_message(data):
        payload = data or {}
        room = _canonical_room_name(str(payload.get("room") or "").strip())
        message_id = _clean_room_reaction_message_id(payload.get("message_id"))
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "unpin_message", payload, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard

        if not message_id or not room:
            return {"success": False, "error": "Missing room or message_id"}

        ok, err = _require_live_room_membership(user, room)
        if not ok:
            return err
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}

        if not _can_manage_room_message_controls(user):
            return {"success": False, "error": "No permission"}

        existing = _room_pin_payload(room)
        if not existing or str(existing.get("message_id") or "") != message_id:
            return {"success": False, "error": "Message is not pinned"}

        okrl, retry = _room_control_rate_guard(user, bucket="roomctl:unpin")
        if not okrl:
            return {"success": False, "error": f"Rate limited (wait {retry:.1f}s)"}

        removed = _clear_room_pin(room, message_id)
        payload_out = {"room": room, "message_id": message_id, "unpinned_by": user, "unpinned_at": time.time()}
        if removed:
            payload_out["previous_pin"] = removed
        emit("room_message_unpinned", payload_out, to=room)
        return {"success": True, "room": room, "message_id": message_id, "unpinned": True}


    def _room_message_mutation_disabled_response(action: str, room: str = "", message_id: str = "") -> dict:
        """Return the canonical disabled-policy response for retired room-message controls.

        Echo-Chat room chat is intentionally live-only and immutable.  Users,
        moderators, and admins are not allowed to edit, delete, or highlight room
        messages; future clients should treat these events as retired policy
        checks, not supported moderation actions.
        """
        requested = str(action or "").strip().lower()
        clean_action = requested if requested in {"edit", "delete", "highlight"} else "edit"
        nouns = {"edit": "editing", "delete": "deletion", "highlight": "highlighting"}
        noun = nouns[clean_action]
        payload = {
            "success": False,
            "error": f"Room message {noun} is disabled by server policy",
            "code": f"room_message_{clean_action}_disabled",
            "room_message_mutation_disabled": True,
            "room_message_control_disabled": True,
            "action": clean_action,
        }
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_message_id = _clean_room_reaction_message_id(message_id)
        if clean_room:
            payload["room"] = clean_room
        if clean_message_id:
            payload["message_id"] = clean_message_id
        return payload


    @socketio.on("edit_message")
    @jwt_required()
    def handle_edit_message(data):
        payload = data if isinstance(data, dict) else {}
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "edit_message", payload, default_max_bytes=4096, default_limit=20, default_window=60)
        if guard is not None:
            return guard
        return _room_message_mutation_disabled_response(
            "edit",
            payload.get("room"),
            payload.get("message_id"),
        )


    @socketio.on("delete_message")
    @jwt_required()
    def handle_delete_message(data):
        payload = data if isinstance(data, dict) else {}
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "delete_message", payload, default_max_bytes=4096, default_limit=20, default_window=60)
        if guard is not None:
            return guard
        return _room_message_mutation_disabled_response(
            "delete",
            payload.get("room"),
            payload.get("message_id"),
        )


    @socketio.on("highlight_message")
    @jwt_required()
    def handle_highlight_message(data):
        payload = data if isinstance(data, dict) else {}
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "highlight_message", payload, default_max_bytes=4096, default_limit=20, default_window=60)
        if guard is not None:
            return guard
        return _room_message_mutation_disabled_response(
            "highlight",
            payload.get("room"),
            payload.get("message_id"),
        )


    def _clean_wave_target(value) -> str:
        target = str(value or "").strip()
        if not target or len(target) > 96:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,96}", target):
            return ""
        return target

    def _room_target_sids(room: str, target: str) -> list[str]:
        clean_room = _canonical_room_name(str(room or "").strip())
        clean_target = str(target or "").strip().lower()
        if not clean_room or not clean_target:
            return []
        out = []
        try:
            for target_sid, target_user in connected_room_targets(clean_room):
                if str(target_user or "").strip().lower() == clean_target:
                    out.append(target_sid)
        except Exception:
            return []
        return sorted(set(out))

    def _poll_clean_id(value) -> str:
        poll_id = str(value or "").strip()
        if not poll_id or len(poll_id) > 80:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", poll_id):
            return ""
        return poll_id

    def _poll_clean_option(value) -> str:
        option = str(value or "").strip()
        if not option or len(option) > 120:
            return ""
        return option

    def _poll_options(raw) -> list[str]:
        if not isinstance(raw, (list, tuple)):
            return []
        out = []
        for item in raw:
            option = _poll_clean_option(item)
            if option and option not in out:
                out.append(option)
        return out[:24]

    def _poll_is_active(poll: dict | None) -> bool:
        if not isinstance(poll, dict):
            return False
        if poll.get("active") is False or poll.get("closed") is True:
            return False
        try:
            expires_at = float(poll.get("expires_at") or poll.get("expires") or 0.0)
        except Exception:
            expires_at = 0.0
        return not expires_at or expires_at > time.time()

    def _poll_public_payload(poll_id: str, poll: dict) -> dict:
        clean_id = _poll_clean_id(poll_id or poll.get("poll_id") or poll.get("id"))
        options = _poll_options(poll.get("options") or [])
        votes = poll.get("votes") if isinstance(poll.get("votes"), dict) else {}
        counts = {option: len(set(votes.get(option) or [])) for option in options}
        return {
            "poll_id": clean_id,
            "id": clean_id,
            "room": _canonical_room_name(str(poll.get("room") or "").strip()),
            "question": str(poll.get("question") or "").strip()[:300],
            "options": options,
            "counts": counts,
            "active": _poll_is_active(poll),
            "created_by": str(poll.get("created_by") or "").strip()[:96],
            "created_at": float(poll.get("created_at") or 0.0) if str(poll.get("created_at") or "").strip() else None,
            "expires_at": float(poll.get("expires_at") or 0.0) if str(poll.get("expires_at") or "").strip() else None,
        }

    def _poll_find_locked(poll_id: str) -> tuple[str, dict | None]:
        """Return (canonical id, poll dict) from the live-poll registry.

        Preferred shape is ROOM_ACTIVE_POLLS[poll_id] = {room, question, options,
        votes, voters, ...}.  For compatibility with future room-keyed seeders,
        this also accepts ROOM_ACTIVE_POLLS[room][poll_id].
        """
        clean_id = _poll_clean_id(poll_id)
        if not clean_id:
            return "", None
        direct = ROOM_ACTIVE_POLLS.get(clean_id)
        if isinstance(direct, dict) and (direct.get("options") is not None or direct.get("room")):
            return clean_id, direct
        for room_key, value in (ROOM_ACTIVE_POLLS or {}).items():
            if not isinstance(value, dict):
                continue
            nested = value.get(clean_id)
            if isinstance(nested, dict):
                nested.setdefault("room", str(room_key or "").strip())
                return clean_id, nested
        return clean_id, None

    def _polls_for_room(room: str) -> list[dict]:
        clean_room = _canonical_room_name(str(room or "").strip())
        if not clean_room:
            return []
        out = []
        seen = set()
        with ROOM_ACTIVE_POLLS_LOCK:
            for key, value in list((ROOM_ACTIVE_POLLS or {}).items()):
                if not isinstance(value, dict):
                    continue
                if value.get("options") is not None or value.get("room"):
                    poll_id = _poll_clean_id(value.get("poll_id") or value.get("id") or key)
                    poll = value
                    if poll_id and poll_id not in seen and _canonical_room_name(str(poll.get("room") or "").strip()) == clean_room and _poll_is_active(poll):
                        out.append(_poll_public_payload(poll_id, poll))
                        seen.add(poll_id)
                    continue
                # Optional room -> {poll_id: poll} shape.
                if _canonical_room_name(str(key or "").strip()) != clean_room:
                    continue
                for nested_id, nested in value.items():
                    if not isinstance(nested, dict):
                        continue
                    poll_id = _poll_clean_id(nested.get("poll_id") or nested.get("id") or nested_id)
                    if poll_id and poll_id not in seen and _poll_is_active(nested):
                        nested.setdefault("room", clean_room)
                        out.append(_poll_public_payload(poll_id, nested))
                        seen.add(poll_id)
        out.sort(key=lambda item: float(item.get("created_at") or 0.0), reverse=True)
        return out[:20]

    @socketio.on("wave_user")
    @jwt_required()
    def handle_wave_user(data):
        payload = data if isinstance(data, dict) else {}
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "wave_user", payload, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard

        room = _canonical_room_name(str(payload.get("room") or get_connected_room(request.sid) or "").strip())
        target = _clean_wave_target(payload.get("target") or payload.get("username") or payload.get("to"))
        if not room:
            return {"success": False, "error": "missing_room"}
        if not target:
            return {"success": False, "error": "missing_target"}
        if str(sender or "").strip().lower() == target.lower():
            return {"success": False, "error": "cannot_wave_self"}

        ok, err = _require_live_room_membership(sender, room)
        if not ok:
            return err
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, sender, sid=request.sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}

        ok, err = _require_not_sanctioned(sender, action="send")
        if not ok:
            return {"success": False, "error": err}
        if _either_blocked(sender, target):
            return {"success": False, "error": "Blocked"}

        target_sids = _room_target_sids(room, target)
        if not target_sids:
            return {"success": False, "error": "target_not_in_room", "room": room, "target": target}

        okrl, retry, auto_muted = _socket_action_rate_ok(
            sender,
            "wave_user",
            "wave_user_rate_limit",
            "wave_user_rate_window_sec",
            default_limit=10,
            default_window=60,
            strike_reason="wave_rate",
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}

        payload_out = {"room": room, "from": sender, "target": target, "message": f"{sender} 👋 waved at you!", "ts": time.time()}
        try:
            shadowbanned_sender = bool(_is_effectively_shadowbanned(sender))
        except Exception:
            shadowbanned_sender = False
        delivered = 0
        if not shadowbanned_sender:
            for target_sid in target_sids:
                try:
                    emit("room_wave", payload_out, to=target_sid)
                    emit("notification", {"room": room, "message": payload_out["message"], "kind": "room_wave"}, to=target_sid)
                    delivered += 1
                except Exception:
                    pass
        return {"success": True, "room": room, "target": target, "delivered": delivered, "shadowbanned": shadowbanned_sender}


    @socketio.on("vote_poll")
    @jwt_required()
    def handle_vote_poll(data):
        payload = data if isinstance(data, dict) else {}
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "vote_poll", payload, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard

        room = _canonical_room_name(str(payload.get("room") or get_connected_room(request.sid) or "").strip())
        poll_id = _poll_clean_id(payload.get("poll_id") or payload.get("id"))
        option = _poll_clean_option(payload.get("option"))
        if not room:
            return {"success": False, "error": "missing_room"}
        if not poll_id or not option:
            return {"success": False, "error": "missing_poll_or_option"}

        ok, err = _require_live_room_membership(user, room)
        if not ok:
            return err
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, user, sid=request.sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}

        okrl, retry, auto_muted = _socket_action_rate_ok(
            user,
            "vote_poll",
            "poll_vote_rate_limit",
            "poll_vote_rate_window_sec",
            default_limit=20,
            default_window=60,
            strike_reason="poll_vote_rate",
        )
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}

        with ROOM_ACTIVE_POLLS_LOCK:
            canonical_id, poll = _poll_find_locked(poll_id)
            if not poll:
                return {"success": False, "error": "poll_not_found", "room": room, "poll_id": poll_id}
            poll_room = _canonical_room_name(str(poll.get("room") or "").strip())
            if poll_room != room:
                return {"success": False, "error": "poll_not_in_room", "room": room, "poll_id": canonical_id}
            if not _poll_is_active(poll):
                return {"success": False, "error": "poll_closed", "room": room, "poll_id": canonical_id}
            options = _poll_options(poll.get("options") or [])
            matched = next((candidate for candidate in options if candidate.lower() == option.lower()), "")
            if not matched:
                return {"success": False, "error": "invalid_option", "room": room, "poll_id": canonical_id, "options": options}

            try:
                shadowbanned_voter = bool(_is_effectively_shadowbanned(user))
            except Exception:
                shadowbanned_voter = False
            if shadowbanned_voter:
                public = _poll_public_payload(canonical_id, poll)
                return {"success": True, "room": room, "poll_id": canonical_id, "counted": False, "shadowbanned": True, "poll": public}

            votes = poll.setdefault("votes", {})
            if not isinstance(votes, dict):
                votes = {}
                poll["votes"] = votes
            voters = poll.setdefault("voters", {})
            if not isinstance(voters, dict):
                voters = {}
                poll["voters"] = voters
            previous = str(voters.get(user) or "")
            if previous:
                return {"success": False, "error": "vote_is_final", "room": room, "poll_id": canonical_id, "current": previous, "poll": _poll_public_payload(canonical_id, poll)}
            bucket = votes.setdefault(matched, set())
            if not isinstance(bucket, set):
                bucket = set(bucket if isinstance(bucket, (list, tuple)) else [])
                votes[matched] = bucket
            bucket.add(user)
            voters[user] = matched
            poll["updated_at"] = time.time()
            public = _poll_public_payload(canonical_id, poll)

        emit("room_poll_update", {"room": room, "poll": public, "poll_id": canonical_id, "voted_by": user}, to=room)
        return {"success": True, "room": room, "poll_id": canonical_id, "option": matched, "counted": True, "poll": public}


    @socketio.on("get_active_polls")
    @jwt_required()
    def handle_get_active_polls(data=None):
        payload = data if isinstance(data, dict) else {}
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "get_active_polls", payload, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard

        room = _canonical_room_name(str(payload.get("room") or get_connected_room(request.sid) or "").strip())
        if not room:
            return {"success": False, "error": "missing_room"}
        ok, err = _require_live_room_membership(user, room)
        if not ok:
            return err
        if not _room_exists(room):
            return {"success": False, "error": "Room not found"}
        access_ok, private_reason = _enforce_private_room_access(room, user, sid=request.sid)
        if not access_ok:
            return {"success": False, "error": "invite_required", "reason": private_reason}
        polls = _polls_for_room(room)
        return {"success": True, "room": room, "polls": polls, "active_polls": polls, "count": len(polls), "history_enabled": False}


    def _room_navigation_shortcut_payload(room: str = "") -> dict:
        """Return the supported browser room-navigation shortcuts.

        This event is informational only; it does not join, leave, or switch rooms.
        Keeping it server-backed lets the browser/help UI discover the current
        shortcut contract without letting modified clients turn the placeholder
        event into a fake room-navigation success path.
        """
        clean_room = _canonical_room_name(str(room or "").strip())
        return {
            "success": True,
            "room": clean_room or None,
            "shortcuts_enabled": True,
            "mutates_room": False,
            "shortcuts": [
                {"combo": "Ctrl+Alt+R", "action": "focus_room_list", "label": "Focus the room list"},
                {"combo": "Ctrl+Alt+M", "action": "focus_room_message", "label": "Focus the active room message box"},
                {"combo": "Ctrl+Alt+U", "action": "focus_room_users", "label": "Focus the room users panel"},
                {"combo": "Ctrl+Alt+ArrowUp", "action": "previous_room", "label": "Join the previous visible room"},
                {"combo": "Ctrl+Alt+ArrowDown", "action": "next_room", "label": "Join the next visible room"},
            ],
        }

    @socketio.on("room_navigation_shortcuts")
    @jwt_required()
    def handle_room_navigation_shortcuts(data=None):
        payload = data if isinstance(data, dict) else {}
        user = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(user, "room_navigation_shortcuts", payload, default_max_bytes=4096, default_limit=30, default_window=60)
        if guard is not None:
            return guard

        room = _canonical_room_name(str(payload.get("room") or get_connected_room(request.sid) or "").strip())
        if room:
            ok, err = _require_live_room_membership(user, room)
            if not ok:
                return err
            if not _room_exists(room):
                return {"success": False, "error": "Room not found"}
            access_ok, private_reason = _enforce_private_room_access(room, user, sid=request.sid)
            if not access_ok:
                return {"success": False, "error": "invite_required", "reason": private_reason}

        return _room_navigation_shortcut_payload(room)


