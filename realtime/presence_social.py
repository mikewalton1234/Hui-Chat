# antiabuse_exempt_staff compatibility: settings.get("antiabuse_exempt_staff", True)
"""Socket.IO handlers: presence_social.

Auto-split from the legacy monolithic socket_handlers.py.
"""

import json
import re
import time
import uuid
import threading
import urllib.parse
from datetime import datetime, timezone
from collections import deque

from flask import request
from socket_auth import jwt_required, get_jwt, get_jwt_identity
from flask_socketio import join_room, leave_room, emit, disconnect
from werkzeug.utils import secure_filename

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
    touch_custom_room_activity,
    consume_room_invites,
    set_room_message_expiry,
    get_room_message_expiry,
    is_auth_session_active,
)
from security import log_audit_event
from permissions import check_user_permission
from moderation import is_user_sanctioned, mute_user
from account_status import get_effective_account_status
from sensitive_fields_crypto import encrypt_sensitive_field, decrypt_sensitive_field

from realtime.state import *

def register(socketio, settings, ctx):
    """Register Socket.IO event handlers for this module."""
    # Make helper functions from socket_handlers available as module globals
    globals().update(ctx.__dict__)

    @socketio.on("connect")

    @jwt_required()
    def handle_connect(auth=None):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        sid = request.sid
        claims = get_jwt() or {}
        auth_session_id = str(claims.get("sid") or "").strip() or None

        if not auth_session_id:
            try:
                emit("force_logout", {"username": username, "reason": "Your session is missing realtime auth state. Please sign in again.", "code": "missing_session"}, to=sid)
            except Exception:
                pass
            try:
                disconnect(sid=sid)
            except Exception:
                pass
            return False

        try:
            if not is_auth_session_active(auth_session_id, username=username, max_idle_seconds=None):
                emit("force_logout", {"username": username, "reason": "Your session was revoked. Please sign in again.", "code": "session_revoked"}, to=sid)
                try:
                    disconnect(sid=sid)
                except Exception:
                    pass
                return False
        except Exception:
            emit("notification", "Connection denied", to=sid)
            return False

        # Stop reconnect storms and accidental tab floods before mutating
        # online/presence state or broadcasting room activity.
        try:
            if not _socket_connect_guard(username, auth_session_id, sid):
                return False
        except Exception:
            emit("notification", "Connection denied", to=sid)
            return False

        # If banned/kicked, force the client back to the login screen with a reason.
        if is_user_sanctioned(username, "ban"):
            msg = _format_sanction_message(username, "ban", "You were signed out because you are banned.")
            try:
                emit("force_logout", {"username": username, "reason": msg, "code": "ban"}, to=sid)
            except Exception:
                pass
            try:
                disconnect(sid=sid)
            except Exception:
                pass
            return False

        if is_user_sanctioned(username, "kick"):
            msg = _format_sanction_message(username, "kick", "You were signed out because you were kicked.")
            try:
                emit("force_logout", {"username": username, "reason": msg, "code": "kick"}, to=sid)
            except Exception:
                pass
            try:
                disconnect(sid=sid)
            except Exception:
                pass
            return False

        ok, err = _require_not_sanctioned(username, action="connect")
        if not ok:
            emit("notification", err or "Connection denied", to=sid)
            return False  # drop

        # Track this session first (shared state mirrors this into Redis when configured)
        upsert_connected_session(sid, username, None, auth_session_id=auth_session_id)

        # Mark online on every successful realtime connect. The previous
        # first-session-only update could leave users visually offline when stale
        # session refs made first_session false or when another worker did not share
        # in-memory presence state.
        first_session = (len(_user_sids(username)) == 1)
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                       SET online = TRUE,
                           last_seen = NULL
                     WHERE LOWER(username) = LOWER(%s);
                    """,
                    (username,),
                )
            conn.commit()
        except Exception:
            pass

        # Push user's own presence to their UI
        try:
            emit("my_presence", _self_presence_snapshot(username), to=sid)
        except Exception:
            pass

        # Prime social state immediately on socket connect so the UI does not
        # depend entirely on a later client-side bootstrap fetch.
        try:
            emit("friends_list", get_friends_for_user(username), to=sid)
        except Exception:
            pass
        try:
            emit("pending_friend_requests", get_pending_friend_requests(username), to=sid)
        except Exception:
            pass
        try:
            emit("blocked_users_list", get_blocked_users(username), to=sid)
        except Exception:
            pass
        try:
            friends = get_friends_for_user(username) or []
            emit("friends_presence", [_public_presence_for_user(f) for f in friends], to=sid)
        except Exception:
            pass

        # Viewer-safe presence push to friends (best-effort). Do this on every
        # connect/reconnect so friend docks heal after refreshes, stale DB flags,
        # or multi-worker routing.
        _broadcast_presence_to_friends(username)

        log_audit_event(username, "connected")
        

        # Prime the client with live room counts (for room browser badges).
        try:
            _emit_room_counts_snapshot(to_sid=sid)
        except Exception:
            pass

        # Deliver any queued ciphertext PMs for this user.
        _emit_missed_pm_summary(username, sid)


    @socketio.on("disconnect")
    def handle_disconnect(*args, **kwargs):
        # Socket.IO may pass a reason or sid depending on version.
        reason = args[0] if args else kwargs.get("reason")
        sid = request.sid

        # Snapshot + remove this session safely from shared/local state.
        session = remove_connected_session(sid)
        user = session.get("username") if session else None
        room = session.get("room") if session else None

        if not user:
            print(f"🔌 Disconnect from unknown SID: {sid}")
            return

        log_audit_event(user, "disconnected")

        if room:
            # Flask-SocketIO will clean up the transport room, but leave explicitly
            # for old transports/tests. Do this before deciding whether the USER
            # really left the chat room; multiple tabs may still represent the
            # same person in the same room.
            try:
                leave_room(room)
            except Exception:
                pass

            still_in_same_room = False
            try:
                still_in_same_room = user in set(_live_room_users(room))
            except Exception:
                still_in_same_room = False

            if still_in_same_room:
                # Quiet fast reconnect/tab-close churn: no "disconnected" toast,
                # no member_count decrement, no voice-roster removal. The user is
                # still present in that room through another live socket.
                try:
                    _emit_room_users_snapshot(room)
                    _emit_room_counts_snapshot()
                except Exception:
                    pass
                return

            # Voice roster cleanup (best-effort)
            try:
                if _voice_room_remove(room, user):
                    emit("voice_room_user_left", {"room": room, "username": user}, room=room)
            except Exception:
                pass

            # Maintain member_count (best-effort)
            try:
                increment_room_count(room, -1)
            except Exception:
                pass

            try:
                touch_custom_room_activity(room)
            except Exception:
                pass

            emit("notification", {"room": room, "message": f"{user} disconnected", "kind": "room_presence"}, room=room)

            # Broadcast updated live room counts and room user list.
            try:
                _emit_room_counts_snapshot()
                _emit_room_users_snapshot(room)
            except Exception:
                pass

        # If user still has other live sessions, do NOT flip them offline or end calls.
        if _user_sids(user):
            return

        # Mark offline in DB (best-effort)
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                       SET online = FALSE,
                           last_seen = NOW(),
                           presence_status = 'online',
                           custom_status = NULL
                     WHERE username = %s;
                    """,
                    (user,),
                )
            conn.commit()
        except Exception:
            pass

        # Viewer-safe presence push to friends (best-effort)
        _broadcast_presence_to_friends(user)

        # End any active/invited voice DM sessions when a user goes fully offline (best-effort)
        try:
            _cleanup_voice_dm_sessions()
            notify = []
            for cid, s in list(voice_dm_session_items()):
                if s.get("caller") == user or s.get("callee") == user:
                    other = s.get("callee") if s.get("caller") == user else s.get("caller")
                    state = str(s.get("state") or "")
                    if state in {"active", "invited"}:
                        notify.append((cid, other))
                    voice_dm_session_delete(cid)

            for cid, other in notify:
                _emit_to_user(other, "voice_dm_end", {"sender": user, "call_id": cid, "reason": "peer_disconnected"})
        except Exception:
            pass


    def _lock_friend_pair(cur, username_a: str, username_b: str) -> None:
        """Serialize friend-request mutations for a user pair.

        Friend requests do not have a pair-level unique constraint in legacy
        databases, so concurrent tabs can otherwise create duplicate pending or
        accepted rows. Locking the two user rows is lightweight and keeps
        send/accept/reject decisions based on a stable pair snapshot.
        """
        cur.execute(
            """
            SELECT username
              FROM users
             WHERE LOWER(username) IN (LOWER(%s), LOWER(%s))
             ORDER BY LOWER(username)
             FOR UPDATE;
            """,
            (username_a, username_b),
        )
        cur.fetchall()


    def _friend_pair_blocked_in_tx(cur, username_a: str, username_b: str) -> bool:
        cur.execute(
            """
            SELECT 1
              FROM blocks
             WHERE (LOWER(blocker) = LOWER(%s) AND LOWER(blocked) = LOWER(%s))
                OR (LOWER(blocker) = LOWER(%s) AND LOWER(blocked) = LOWER(%s))
             LIMIT 1;
            """,
            (username_a, username_b, username_b, username_a),
        )
        return cur.fetchone() is not None


    def _emit_friend_request_state(username: str) -> None:
        """Push canonical friend and pending-request snapshots to all user tabs."""
        clean = str(username or "").strip()
        if not clean:
            return
        try:
            _emit_to_user(clean, "friends_list", get_friends_for_user(clean))
        except Exception:
            pass
        try:
            _emit_to_user(clean, "pending_friend_requests", get_pending_friend_requests(clean))
        except Exception:
            pass


    def _emit_blocked_users_state(username: str) -> None:
        """Push the canonical blocked-users snapshot to all of this user's tabs."""
        clean = str(username or "").strip()
        if not clean:
            return
        try:
            _emit_to_user(clean, "blocked_users_list", get_blocked_users(clean))
        except Exception:
            pass


    def _emit_social_pair_unlinked(username_a: str, username_b: str) -> None:
        """Remove stale friend/presence affordances after unfriend/block cleanup."""
        try:
            _emit_to_user(username_a, "friend_presence_update", {"username": username_b, "online": False, "presence": "offline", "custom_status": None, "last_seen": None, "avatar_url": ""})
            _emit_to_user(username_b, "friend_presence_update", {"username": username_a, "online": False, "presence": "offline", "custom_status": None, "last_seen": None, "avatar_url": ""})
        except Exception:
            pass


    def _emit_friend_presence_pair(username_a: str, username_b: str) -> None:
        try:
            _emit_to_user(username_a, "friends_presence", [_public_presence_for_user(username_b)])
            _emit_to_user(username_b, "friends_presence", [_public_presence_for_user(username_a)])
        except Exception:
            pass


    def _social_action_denial(username: str, *, action: str = "social"):
        """Return a callback-safe denial for social/presence writes."""
        if is_user_sanctioned(username, "ban"):
            return {"success": False, "error": "Social actions are disabled for this account"}
        # Muted users should not create new visible social/presence activity, but
        # privacy cleanup actions like block/unblock/reject/remove remain allowed.
        if action in {"friend_request", "accept_friend", "presence"} and is_user_sanctioned(username, "mute"):
            return {"success": False, "error": "Social actions are disabled while muted"}
        return None


    def _pair_live_rooms(username_a: str, username_b: str) -> set[str]:
        rooms: set[str] = set()
        for user in (str(username_a or "").strip(), str(username_b or "").strip()):
            if not user:
                continue
            try:
                sids = list(_user_sids(user))
            except Exception:
                sids = []
            for sid in sids:
                try:
                    sess = get_connected_session(sid)
                except Exception:
                    sess = None
                room = str((sess or {}).get("room") or "").strip()
                if room:
                    rooms.add(room)
        return rooms

    def _emit_pair_room_visibility_refresh(username_a: str, username_b: str, *, reason: str = "privacy") -> None:
        """Refresh room rosters/counts after block or unblock.

        A block changes who is allowed to see whom. An unblock changes that back.
        Both transitions need personalized room_users snapshots; doing it only on
        the client can leave stale one-person rosters that break room E2EE.
        """
        def _send_once() -> set[str]:
            # Re-read live rooms on every pass. During block/unblock, one tab may
            # reassert/rejoin milliseconds after the ACK, so a fixed precomputed
            # room set can miss the room that actually needs the roster refresh.
            live_rooms = _pair_live_rooms(username_a, username_b)
            for room in sorted(live_rooms):
                try:
                    _emit_room_users_snapshot(room)
                except Exception:
                    pass
            try:
                _emit_room_counts_snapshot()
            except Exception:
                pass
            return live_rooms

        rooms = _send_once()
        # A short delayed pass covers browser/server ordering races where the
        # unblock ACK reaches the client before the room reassert/join completes.
        if rooms:
            for delay in (0.35, 1.10, 2.25):
                try:
                    timer = threading.Timer(delay, _send_once)
                    timer.daemon = True
                    timer.start()
                except Exception:
                    pass

    def _emit_unblock_realtime_refresh(blocker: str, blocked: str) -> None:
        """Refresh both browsers after an unblock so room roster/E2EE state unsticks."""
        a = str(blocker or "").strip()
        b = str(blocked or "").strip()
        if not a or not b:
            return
        try:
            _emit_to_user(a, "social_alert_cleanup", {"reason": "unblock", "peer": b})
            _emit_to_user(b, "social_alert_cleanup", {"reason": "peer_unblocked", "peer": a})
        except Exception:
            pass
        _emit_pair_room_visibility_refresh(a, b, reason="unblock")

    def _cleanup_social_pair_realtime_sessions(username_a: str, username_b: str) -> dict:
        """Drop live P2P file/voice sessions between a newly blocked pair."""
        a = str(username_a or "").strip()
        b = str(username_b or "").strip()
        if not a or not b:
            return {"p2p_file_sessions": 0, "voice_dm_sessions": 0}
        pair = {a.lower(), b.lower()}
        dropped_p2p = 0
        dropped_voice = 0

        try:
            with P2P_FILE_SESSIONS_LOCK:
                for transfer_id, sess in list(P2P_FILE_SESSIONS.items()):
                    sess_pair = {str(sess.get("a") or "").strip().lower(), str(sess.get("b") or "").strip().lower()}
                    if sess_pair == pair:
                        try:
                            del P2P_FILE_SESSIONS[transfer_id]
                            dropped_p2p += 1
                            try:
                                _mark_p2p_transfer_id_closed(transfer_id)
                            except Exception:
                                pass
                        except Exception:
                            pass
        except Exception:
            pass

        try:
            for call_id, sess in list(voice_dm_session_items()):
                sess_pair = {str(sess.get("caller") or "").strip().lower(), str(sess.get("callee") or "").strip().lower()}
                if sess_pair == pair:
                    try:
                        voice_dm_session_delete(call_id)
                        dropped_voice += 1
                    except Exception:
                        pass
                    try:
                        _emit_to_user(a, "voice_dm_end", {"sender": b, "call_id": call_id, "reason": "blocked"})
                        _emit_to_user(b, "voice_dm_end", {"sender": a, "call_id": call_id, "reason": "blocked"})
                    except Exception:
                        pass
        except Exception:
            pass

        if dropped_p2p:
            try:
                _emit_to_user(a, "p2p_file_cancelled", {"peer": b, "reason": "blocked"})
                _emit_to_user(b, "p2p_file_cancelled", {"peer": a, "reason": "blocked"})
            except Exception:
                pass
        return {"p2p_file_sessions": int(dropped_p2p), "voice_dm_sessions": int(dropped_voice)}


    @socketio.on("remove_friend")
    @jwt_required()
    def handle_remove_friend(data):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "remove_friend", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard:
            return guard
        denied = _social_action_denial(username, action="remove_friend")
        if denied is not None:
            return denied

        lim, win = _parse_rate_limit(settings.get("social_action_rate_limit"), default_limit=60, default_window=60)
        try:
            win = int(settings.get("social_action_rate_window_sec") or win)
        except Exception:
            pass
        okrl, retry = _rl(f"rmfriend:{username}", lim, win)
        if not okrl:
            return {"success": False, "error": "Rate limited", "retry_after": retry}

        friend = _resolve_canonical_username((data or {}).get("friend"))
        if not friend or friend.lower() == str(username or "").strip().lower():
            return {"success": False, "error": "Invalid friend"}

        removed_requests = 0
        removed_friend_rows = 0
        try:
            conn = get_db()
            with conn.cursor() as cur:
                _lock_friend_pair(cur, username, friend)
                cur.execute(
                    """
                    DELETE FROM friend_requests
                     WHERE ((LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s))
                            OR (LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s)))
                       AND request_status = 'accepted';
                    """,
                    (username, friend, friend, username),
                )
                removed_requests = int(cur.rowcount or 0)

                cur.execute(
                    """
                    DELETE FROM friends
                     WHERE (user_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                        AND friend_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1))
                        OR (user_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                        AND friend_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1));
                    """,
                    (username, friend, friend, username),
                )
                removed_friend_rows = int(cur.rowcount or 0)
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[DB ERROR] Failed to remove friend: {e}")
            return {"success": False, "error": "Database error"}

        affected = int(removed_requests or 0) or int(removed_friend_rows or 0)
        if affected:
            _emit_friend_request_state(username)
            _emit_friend_request_state(friend)
            _emit_social_pair_unlinked(username, friend)
            return {"success": True, "friend": friend, "removed_friendship": bool(removed_requests), "removed_friend_rows": int(removed_friend_rows or 0)}
        _emit_friend_request_state(username)
        return {"success": False, "error": "Friendship not found"}





    @socketio.on("get_pending_friend_requests")
    @jwt_required()
    def handle_get_pending_friend_requests(data=None):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_pending_friend_requests", data, default_max_bytes=2048, default_limit=120, default_window=60)
        if guard:
            return guard
        try:
            pending = get_pending_friend_requests(username)
            emit("pending_friend_requests", pending, to=request.sid)
        except Exception as e:
            print(f"[DB ERROR] get_pending_friend_requests: {e}")
            emit("pending_friend_requests", [], to=request.sid)

        return {"success": True}


    @socketio.on("get_blocked_users")
    @jwt_required()
    def handle_get_blocked_users(data=None):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_blocked_users", data, default_max_bytes=2048, default_limit=120, default_window=60)
        if guard:
            return guard
        try:
            blocked = get_blocked_users(username)
        except Exception as e:
            print(f"[DB ERROR] get_blocked_users: {e}")
            blocked = []

        emit("blocked_users_list", blocked, to=request.sid)
        # Also return for Socket.IO callback (client expects an array).
        return blocked


    def _sanitize_profile_bio(raw):
        if raw is None:
            return ""
        bio = str(raw).replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(bio) > 500:
            bio = bio[:500]
        return bio


    def _sanitize_profile_media_url(raw, *, allow_local=False):
        if raw is None:
            return ""
        url = str(raw).strip()
        if not url:
            return ""
        if len(url) > 1024:
            url = url[:1024]
        if any(ch.isspace() for ch in url):
            return None
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return None

        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return url

        if allow_local and parsed.scheme in {"", None}:
            path = str(parsed.path or "")
            allowed_local_prefixes = (
                "/media/avatars/",
                "/static/uploads/profile_avatars/",
                "/media/profile-banners/",
                "/static/uploads/profile_banners/",
                "/avatar-preset.svg",
            )
            if any(path.startswith(prefix) for prefix in allowed_local_prefixes):
                return url

        return None


    def _sanitize_avatar_url(raw):
        return _sanitize_profile_media_url(raw, allow_local=True)


    def _sanitize_profile_url(raw):
        return _sanitize_profile_media_url(raw, allow_local=False)


    def _profile_local_media_owner_ok(url: str | None, username: str | None, *, kind: str) -> bool:
        """Only allow local avatar/banner media that was generated for this account.

        Uploaded profile avatar/banner filenames are prefixed with secure_filename(username).
        This prevents a user from pointing their profile at another user's local
        media and later causing that file to be deleted by their own upload flow.
        """
        value = str(url or "").strip()
        if not value:
            return True
        try:
            parsed = urllib.parse.urlparse(value)
        except Exception:
            return False
        if parsed.scheme in {"http", "https"}:
            return True
        path = str(parsed.path or "")
        if path == "/avatar-preset.svg":
            return True
        avatar_prefixes = ("/media/avatars/", "/static/uploads/profile_avatars/")
        banner_prefixes = ("/media/profile-banners/", "/static/uploads/profile_banners/")
        prefixes = avatar_prefixes if kind == "avatar" else banner_prefixes
        if not any(path.startswith(prefix) for prefix in prefixes):
            return True
        filename = path.rsplit("/", 1)[-1]
        safe_name = secure_filename(filename or "")
        safe_owner = secure_filename(str(username or "")) or "user"
        return bool(safe_name and safe_name == filename and safe_name.startswith(f"{safe_owner}-"))


    def _profile_local_media_change_requires_upload_permission(old_url: str | None, new_url: str | None) -> bool:
        """Return True when the profile edit points at uploaded local media."""
        old_value = str(old_url or "").strip()
        new_value = str(new_url or "").strip()
        if not new_value or new_value == old_value:
            return False
        try:
            parsed = urllib.parse.urlparse(new_value)
        except Exception:
            return False
        if parsed.scheme in {"http", "https"}:
            return False
        path = str(parsed.path or "")
        upload_prefixes = (
            "/media/avatars/",
            "/static/uploads/profile_avatars/",
            "/media/profile-banners/",
            "/static/uploads/profile_banners/",
        )
        return any(path.startswith(prefix) for prefix in upload_prefixes)


    def _profile_socket_write_denial(username: str, *, action: str = "write"):
        if is_user_sanctioned(username, "ban"):
            return {"success": False, "error": "Profile changes are disabled for this account"}
        # set_my_profile changes visible profile text/media and can push presence/profile updates.
        if action in {"write", "media"} and is_user_sanctioned(username, "mute"):
            return {"success": False, "error": "Profile editing is disabled for this account"}
        if action == "media" and is_user_sanctioned(username, "upload"):
            return {"success": False, "error": "Uploads are disabled for this account"}
        return None


    def _sanitize_profile_text(raw, max_len=120):
        if raw is None:
            return ""
        text_value = str(raw).replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(text_value) > max_len:
            text_value = text_value[:max_len]
        return text_value


    def _sanitize_visibility(raw, default="friends"):
        value = str(raw or default).strip().lower()
        if value in {"only_me", "me", "private"}:
            value = "nobody"
        if value in {"room", "room_member", "room_members_only"}:
            value = "room_members"
        if value not in {"everyone", "friends", "room_members", "nobody"}:
            value = default
        return value


    def _sanitize_profile_post_visibility(raw, default="friends"):
        value = str(raw or default).strip().lower()
        if value in {"only_me", "me", "nobody", "private", "private_only", "onlyme"}:
            value = "private"
        if value in {"room", "room_member", "room_members_only"}:
            value = "room_members"
        if value not in {"everyone", "friends", "room_members", "private"}:
            value = default
        return value


    def _sanitize_profile_age(raw):
        if raw in {None, ""}:
            return None
        try:
            value = int(str(raw).strip())
        except Exception:
            return None
        if value < 1 or value > 120:
            return None
        return value


    def _sanitize_profile_accent(raw):
        value = str(raw or "").strip()
        if not value:
            return ""
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value.lower()
        return ""


    def _sanitize_profile_bool(raw, default=False):
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return bool(default)
        if isinstance(raw, (int, float)):
            return bool(raw)
        value = str(raw).strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off", ""}:
            return False
        return bool(default)


    def _users_share_live_room(a: str, b: str) -> bool:
        a = str(a or "").strip()
        b = str(b or "").strip()
        if not a or not b or a == b:
            return bool(a and b and a == b)
        try:
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


    def _can_view_profile_field(viewer: str, target: str, visibility: str, is_friend: bool) -> bool:
        if viewer == target:
            return True
        visibility = _sanitize_visibility(visibility)
        if visibility == "everyone":
            return True
        if visibility == "friends":
            return bool(is_friend)
        if visibility == "room_members":
            return _users_share_live_room(viewer, target)
        return False


    def _get_mutual_friend_data(viewer: str, target: str, limit: int = 6) -> dict:
        if not viewer or not target or viewer == target:
            return {"count": 0, "usernames": []}
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH viewer_friends AS (
                    SELECT CASE WHEN from_user = %s THEN to_user ELSE from_user END AS username
                      FROM friend_requests
                     WHERE request_status = 'accepted'
                       AND (%s = from_user OR %s = to_user)
                ),
                target_friends AS (
                    SELECT CASE WHEN from_user = %s THEN to_user ELSE from_user END AS username
                      FROM friend_requests
                     WHERE request_status = 'accepted'
                       AND (%s = from_user OR %s = to_user)
                )
                SELECT vf.username
                  FROM viewer_friends vf
                  JOIN target_friends tf ON tf.username = vf.username
                 WHERE vf.username <> %s
                   AND vf.username <> %s
                 ORDER BY LOWER(vf.username)
                 LIMIT %s;
                """,
                (viewer, viewer, viewer, target, target, target, viewer, target, int(limit)),
            )
            usernames = [str(r[0]) for r in (cur.fetchall() or []) if r and r[0]]
            cur.execute(
                """
                WITH viewer_friends AS (
                    SELECT CASE WHEN from_user = %s THEN to_user ELSE from_user END AS username
                      FROM friend_requests
                     WHERE request_status = 'accepted'
                       AND (%s = from_user OR %s = to_user)
                ),
                target_friends AS (
                    SELECT CASE WHEN from_user = %s THEN to_user ELSE from_user END AS username
                      FROM friend_requests
                     WHERE request_status = 'accepted'
                       AND (%s = from_user OR %s = to_user)
                )
                SELECT COUNT(*)
                  FROM (
                    SELECT vf.username
                      FROM viewer_friends vf
                      JOIN target_friends tf ON tf.username = vf.username
                     WHERE vf.username <> %s
                       AND vf.username <> %s
                  ) mutuals;
                """,
                (viewer, viewer, viewer, target, target, target, viewer, target),
            )
            row = cur.fetchone()
        return {"count": int(row[0] or 0) if row else 0, "usernames": usernames}


    def _get_mutual_group_data(viewer: str, target: str, limit: int = 6) -> dict:
        if not viewer or not target or viewer == target:
            return {"count": 0, "groups": []}
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH viewer_groups AS (
                    SELECT gm.group_id, g.group_name
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                      JOIN groups g ON g.id = gm.group_id
                     WHERE u.username = %s
                ),
                target_groups AS (
                    SELECT gm.group_id
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                     WHERE u.username = %s
                )
                SELECT vg.group_name
                  FROM viewer_groups vg
                  JOIN target_groups tg ON tg.group_id = vg.group_id
                 ORDER BY LOWER(vg.group_name)
                 LIMIT %s;
                """,
                (viewer, target, int(limit)),
            )
            groups = [str(r[0]) for r in (cur.fetchall() or []) if r and r[0]]
            cur.execute(
                """
                WITH viewer_groups AS (
                    SELECT gm.group_id
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                     WHERE u.username = %s
                ),
                target_groups AS (
                    SELECT gm.group_id
                      FROM group_members gm
                      JOIN users u ON u.id = gm.user_id
                     WHERE u.username = %s
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


    def _get_mutual_room_data(viewer: str, target: str, limit: int = 6) -> dict:
        if not viewer or not target or viewer == target:
            return {"count": 0, "rooms": []}

        shared_rooms: set[str] = set()
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH viewer_rooms AS (
                    SELECT crm.room_name
                      FROM custom_room_members crm
                     WHERE crm.member_user = %s
                ),
                target_rooms AS (
                    SELECT crm.room_name
                      FROM custom_room_members crm
                     WHERE crm.member_user = %s
                )
                SELECT vr.room_name
                  FROM viewer_rooms vr
                  JOIN target_rooms tr ON tr.room_name = vr.room_name
                 ORDER BY LOWER(vr.room_name);
                """,
                (viewer, target),
            )
            for row in (cur.fetchall() or []):
                if row and row[0]:
                    shared_rooms.add(str(row[0]))

        viewer_live_rooms = set()
        target_live_rooms = set()
        try:
            for sid in user_sids(viewer):
                sess = get_connected_session(sid)
                room = str((sess or {}).get("room") or "").strip()
                if room:
                    viewer_live_rooms.add(room)
            for sid in user_sids(target):
                sess = get_connected_session(sid)
                room = str((sess or {}).get("room") or "").strip()
                if room:
                    target_live_rooms.add(room)
        except Exception:
            viewer_live_rooms = viewer_live_rooms or set()
            target_live_rooms = target_live_rooms or set()

        shared_rooms.update(viewer_live_rooms & target_live_rooms)
        ordered_rooms = sorted(shared_rooms, key=lambda value: value.lower())
        return {"count": len(ordered_rooms), "rooms": ordered_rooms[: max(1, int(limit))]}


    def _recent_room_visible_to_viewer(room_name: str, viewer: str, target: str) -> bool:
        """Hide invite-only custom rooms from profile recent-room sharing.

        A user's profile can share recent rooms with friends/public viewers, but
        that sharing preference must not turn a private custom room into a name
        leak. Private custom rooms are visible here only to the target user or
        to viewers who already have owner/member/pending-invite access.
        """
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
            # Recent rooms are optional profile metadata; fail closed so lookup
            # errors cannot expose invite-only room names.
            return False
        if not meta:
            return True
        if not bool(meta.get("is_private")):
            return True
        try:
            return bool(can_user_access_custom_room(clean_room, clean_viewer))
        except Exception:
            return False


    def _get_recent_room_share_data(target: str, limit: int = 3, viewer: str | None = None) -> dict:
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
            for sid in user_sids(target):
                sess = get_connected_session(sid)
                room = str((sess or {}).get("room") or "").strip()
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
                 WHERE username = %s
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
            if _recent_room_visible_to_viewer(room_name, viewer, target)
        ]
        rooms = []
        for room_name in visible_ordered[:wanted]:
            rooms.append({"name": room_name, "is_current": room_name in live_seen})
        return {"count": len(visible_ordered), "rooms": rooms}


    def _profile_static_and_assigned_badges(username: str, profile: dict | None = None) -> list[dict]:
        username = str(username or "").strip()
        profile = profile or {}
        badges = []
        seen = set()

        def add(key, label, kind="system"):
            k = re.sub(r"[^a-z0-9_:-]", "", str(key or "").strip().lower().replace(" ", "_"))[:40]
            text = str(label or "").strip()[:40]
            if not k or not text or k in seen:
                return
            seen.add(k)
            badges.append({"key": k, "label": text, "kind": kind})

        try:
            if check_user_permission(username, "admin:basic") or check_user_permission(username, "admin:settings"):
                add("admin", "Admin")
            if check_user_permission(username, "moderation:suspend_user") or check_user_permission(username, "moderation:mute_user"):
                add("moderator", "Moderator")
        except Exception:
            pass
        if bool(profile.get("online")):
            add("online", "Online")
        if profile.get("avatar_url") and profile.get("bio"):
            add("profile_complete", "Profile complete")
        try:
            created_raw = profile.get("created_at")
            created_dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00")) if created_raw else None
            if created_dt and (datetime.now(timezone.utc) - created_dt).days <= 30:
                add("new_member", "New member")
        except Exception:
            pass
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


    def _build_profile_payload(viewer: str, row, *, is_friend=False, blocked_by_me=False, blocks_me=False, mutual_friends=None, mutual_groups=None, mutual_rooms=None, recent_rooms=None):
        (
            uname, bio, avatar_url, custom_status, presence_status, online, last_seen, created_at, status,
            relationship_status, relationship_visibility, age, age_visibility,
            location_text, location_visibility, interests, favorite_music, favorite_movies,
            favorite_games, website_url, banner_url, profile_accent, share_recent_rooms, recent_rooms_visibility,
            profile_post_default_visibility,
        ) = row
        public_presence = _public_presence_snapshot(uname)
        can_view_relationship = _can_view_profile_field(viewer, uname, relationship_visibility, bool(is_friend))
        can_view_age = _can_view_profile_field(viewer, uname, age_visibility, bool(is_friend))
        can_view_location = _can_view_profile_field(viewer, uname, location_visibility, bool(is_friend))
        can_view_recent_rooms = bool(share_recent_rooms) and _can_view_profile_field(viewer, uname, recent_rooms_visibility, bool(is_friend))

        payload = {
            "username": uname,
            "bio": bio or "",
            "avatar_url": avatar_url or "",
            "banner_url": banner_url or "",
            "profile_accent": profile_accent or "",
            "custom_status": public_presence.get("custom_status") or custom_status or "",
            "presence": public_presence.get("presence") or (presence_status or ("online" if bool(online) else "offline")),
            "online": bool(public_presence.get("online", online)),
            "last_seen": public_presence.get("last_seen") or (last_seen.isoformat() if hasattr(last_seen, "isoformat") else (str(last_seen) if last_seen else None)),
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else (str(created_at) if created_at else None),
            "account_status": get_effective_account_status(uname) or status or "active",
            "relationship_status": (relationship_status or "") if can_view_relationship else "",
            "relationship_visibility": relationship_visibility or "friends",
            "age": int(age) if can_view_age and age is not None else None,
            "age_visibility": age_visibility or "friends",
            "location_text": decrypt_sensitive_field(location_text or "", settings, field_name="users.location_text") if can_view_location else "",
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
            "recent_rooms": list((recent_rooms or {}).get("rooms") or []) if can_view_recent_rooms else [],
            "recent_rooms_count": int((recent_rooms or {}).get("count") or 0) if can_view_recent_rooms else 0,
            "mutual_friends": list((mutual_friends or {}).get("usernames") or []),
            "mutual_friends_count": int((mutual_friends or {}).get("count") or 0),
            "mutual_groups": list((mutual_groups or {}).get("groups") or []),
            "mutual_groups_count": int((mutual_groups or {}).get("count") or 0),
            "mutual_rooms": list((mutual_rooms or {}).get("rooms") or []),
            "mutual_rooms_count": int((mutual_rooms or {}).get("count") or 0),
            "is_friend": bool(is_friend),
            "blocked_by_me": bool(blocked_by_me),
            "blocks_me": bool(blocks_me),
            "is_self": viewer == uname,
            "can_view_relationship": bool(can_view_relationship),
            "can_view_age": bool(can_view_age),
            "can_view_location": bool(can_view_location),
            "can_view_recent_rooms": bool(can_view_recent_rooms),
        }
        payload["badges"] = _profile_static_and_assigned_badges(uname, payload)
        return payload


    @socketio.on("set_my_profile")
    @jwt_required()
    def handle_set_my_profile(data=None):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "set_my_profile", data, default_max_bytes=32768, default_limit=30, default_window=60)
        if guard:
            return guard
        data = data or {}

        bio = _sanitize_profile_bio(data.get("bio", ""))
        avatar_url = _sanitize_avatar_url(data.get("avatar_url", data.get("avatarUrl", "")))
        banner_url = _sanitize_profile_media_url(data.get("banner_url", data.get("bannerUrl", "")), allow_local=True)
        website_url = _sanitize_profile_url(data.get("website_url", data.get("websiteUrl", "")))
        relationship_status = _sanitize_profile_text(data.get("relationship_status", ""), 40)
        relationship_visibility = _sanitize_visibility(data.get("relationship_visibility", "friends"))
        age = _sanitize_profile_age(data.get("age"))
        age_visibility = _sanitize_visibility(data.get("age_visibility", "friends"))
        location_text = _sanitize_profile_text(data.get("location_text", ""), 80)
        location_visibility = _sanitize_visibility(data.get("location_visibility", "friends"))
        interests = _sanitize_profile_text(data.get("interests", ""), 240)
        favorite_music = _sanitize_profile_text(data.get("favorite_music", data.get("favoriteMusic", "")), 120)
        favorite_movies = _sanitize_profile_text(data.get("favorite_movies", data.get("favoriteMovies", "")), 120)
        favorite_games = _sanitize_profile_text(data.get("favorite_games", data.get("favoriteGames", "")), 120)
        profile_accent = _sanitize_profile_accent(data.get("profile_accent", data.get("profileAccent", "")))
        share_recent_rooms = _sanitize_profile_bool(data.get("share_recent_rooms", data.get("shareRecentRooms", False)))
        recent_rooms_visibility = _sanitize_visibility(data.get("recent_rooms_visibility", data.get("recentRoomsVisibility", "friends")))
        profile_post_default_visibility = _sanitize_profile_post_visibility(data.get("profile_post_default_visibility", data.get("profilePostDefaultVisibility", "friends")), default="friends")

        if avatar_url is None:
            return {"success": False, "error": "Avatar URL must be a direct http/https image URL, a local uploaded avatar, or a built-in avatar"}
        if banner_url is None:
            return {"success": False, "error": "Banner URL must be a direct http/https image URL"}
        if website_url is None:
            return {"success": False, "error": "Website URL must be a direct http/https URL"}

        denied = _profile_socket_write_denial(username, action="write")
        if denied is not None:
            return denied

        if not _profile_local_media_owner_ok(avatar_url, username, kind="avatar"):
            return {"success": False, "error": "Avatar URL must belong to your account"}
        if not _profile_local_media_owner_ok(banner_url, username, kind="banner"):
            return {"success": False, "error": "Banner URL must belong to your account"}

        try:
            conn = get_db()
            old_avatar_url = None
            old_banner_url = None
            with conn.cursor() as cur:
                cur.execute("SELECT avatar_url, banner_url FROM users WHERE username = %s LIMIT 1;", (username,))
                old_media_row = cur.fetchone()
                if old_media_row:
                    old_avatar_url = old_media_row[0]
                    old_banner_url = old_media_row[1]

                media_changed = (
                    _profile_local_media_change_requires_upload_permission(old_avatar_url, avatar_url)
                    or _profile_local_media_change_requires_upload_permission(old_banner_url, banner_url)
                )
                if media_changed:
                    denied = _profile_socket_write_denial(username, action="media")
                    if denied is not None:
                        conn.rollback()
                        return denied

                cur.execute(
                    """
                    UPDATE users
                       SET bio = %s,
                           avatar_url = %s,
                           banner_url = %s,
                           profile_accent = %s,
                           relationship_status = %s,
                           relationship_visibility = %s,
                           age = %s,
                           age_visibility = %s,
                           location_text = %s,
                           location_visibility = %s,
                           interests = %s,
                           favorite_music = %s,
                           favorite_movies = %s,
                           favorite_games = %s,
                           website_url = %s,
                           share_recent_rooms = %s,
                           recent_rooms_visibility = %s,
                           profile_post_default_visibility = %s
                     WHERE username = %s;
                    """,
                    (
                        bio, avatar_url or None, banner_url or None, profile_accent or None,
                        relationship_status or None, relationship_visibility, age, age_visibility,
                        encrypt_sensitive_field(location_text, settings, field_name="users.location_text") if location_text else None, location_visibility, interests or None,
                        favorite_music or None, favorite_movies or None, favorite_games or None,
                        website_url or None, bool(share_recent_rooms), recent_rooms_visibility, profile_post_default_visibility, username,
                    ),
                )
                if not bool(share_recent_rooms):
                    cur.execute("DELETE FROM user_recent_rooms WHERE username = %s;", (username,))
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
            conn.commit()

            if not row:
                return {"success": False, "error": "not_found"}

            recent_rooms = {"count": 0, "rooms": []}
            try:
                recent_rooms = _get_recent_room_share_data(username, limit=3, viewer=username)
            except Exception:
                recent_rooms = {"count": 0, "rooms": []}

            profile = _build_profile_payload(username, row, is_friend=False, blocked_by_me=False, blocks_me=False, recent_rooms=recent_rooms)

            try:
                _emit_to_user(username, "my_profile", profile)
            except Exception:
                try:
                    emit("my_profile", profile, to=request.sid)
                except Exception:
                    pass

            try:
                _broadcast_presence_to_friends(username)
            except Exception:
                pass

            log_audit_event(username, "edit_profile", username, "self_profile_updated")
            return {"success": True, "profile": profile}
        except Exception as e:
            print(f"[DB ERROR] set_my_profile: {e}")
            return {"success": False, "error": "Database error"}


    @socketio.on("get_user_profile")
    @jwt_required()
    def handle_get_user_profile(data=None):
        """Return a profile for a user, with privacy-aware field filtering."""
        viewer = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(viewer, "get_user_profile", data, default_max_bytes=4096, default_limit=120, default_window=60)
        if guard:
            return guard
        target = (data or {}).get("username")
        target = str(target).strip() if target is not None else ""

        if not target:
            return {"success": False, "error": "missing_username"}
        if len(target) > 64 or any(c.isspace() for c in target):
            return {"success": False, "error": "invalid_username"}
        target = _resolve_canonical_username(target)
        if not target:
            return {"success": False, "error": "not_found"}

        try:
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
                    (target,),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "not_found"}

                uname = str(row[0])

                cur.execute(
                    """
                    SELECT 1
                      FROM friend_requests
                     WHERE ((LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s))
                         OR (LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s)))
                       AND request_status = 'accepted'
                     LIMIT 1;
                    """,
                    (viewer, uname, uname, viewer),
                )
                is_friend = cur.fetchone() is not None

                cur.execute(
                    "SELECT 1 FROM blocks WHERE LOWER(blocker) = LOWER(%s) AND LOWER(blocked) = LOWER(%s) LIMIT 1;",
                    (viewer, uname),
                )
                blocked_by_me = cur.fetchone() is not None

                cur.execute(
                    "SELECT 1 FROM blocks WHERE LOWER(blocker) = LOWER(%s) AND LOWER(blocked) = LOWER(%s) LIMIT 1;",
                    (uname, viewer),
                )
                blocks_me = cur.fetchone() is not None

            mutual_friends = {"count": 0, "usernames": []}
            mutual_groups = {"count": 0, "groups": []}
            mutual_rooms = {"count": 0, "rooms": []}
            recent_rooms = {"count": 0, "rooms": []}
            if viewer == uname or (not blocked_by_me and not blocks_me):
                try:
                    recent_rooms = _get_recent_room_share_data(uname, limit=3, viewer=viewer)
                except Exception:
                    recent_rooms = {"count": 0, "rooms": []}
            if viewer != uname and not blocked_by_me and not blocks_me:
                try:
                    mutual_friends = _get_mutual_friend_data(viewer, uname, limit=6)
                except Exception:
                    mutual_friends = {"count": 0, "usernames": []}
                try:
                    mutual_groups = _get_mutual_group_data(viewer, uname, limit=6)
                except Exception:
                    mutual_groups = {"count": 0, "groups": []}
                try:
                    mutual_rooms = _get_mutual_room_data(viewer, uname, limit=6)
                except Exception:
                    mutual_rooms = {"count": 0, "rooms": []}

            profile = _build_profile_payload(
                viewer,
                row,
                is_friend=is_friend,
                blocked_by_me=blocked_by_me,
                blocks_me=blocks_me,
                mutual_friends=mutual_friends,
                mutual_groups=mutual_groups,
                mutual_rooms=mutual_rooms,
                recent_rooms=recent_rooms,
            )
            return {"success": True, "profile": profile}
        except Exception as e:
            print(f"[DB ERROR] get_user_profile: {e}")
            return {"success": False, "error": "db"}


    @socketio.on("accept_friend_request")
    @jwt_required()
    def handle_accept_friend_request(data):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "accept_friend_request", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard:
            return guard
        denied = _social_action_denial(username, action="accept_friend")
        if denied is not None:
            return denied
        from_user = _resolve_canonical_username((data or {}).get("from_user"))

        if not from_user:
            emit("notification", "Invalid friend request to accept", to=request.sid)
            return {"success": False, "error": "Invalid friend request"}

        if from_user.lower() == str(username or "").strip().lower():
            return {"success": False, "error": "Invalid friend request"}

        accepted = False
        already_friends = False
        try:
            conn = get_db()
            with conn.cursor() as cur:
                _lock_friend_pair(cur, username, from_user)

                if _friend_pair_blocked_in_tx(cur, username, from_user):
                    conn.rollback()
                    _emit_friend_request_state(username)
                    return {"success": False, "error": "Blocked"}

                cur.execute(
                    """
                    SELECT 1
                      FROM friend_requests
                     WHERE ((LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s))
                         OR (LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s)))
                       AND request_status = 'accepted'
                     LIMIT 1;
                    """,
                    (from_user, username, username, from_user),
                )
                already_friends = cur.fetchone() is not None

                cur.execute(
                    """
                    UPDATE friend_requests
                       SET request_status = 'accepted'
                     WHERE LOWER(from_user) = LOWER(%s)
                       AND LOWER(to_user) = LOWER(%s)
                       AND request_status = 'pending';
                    """,
                    (from_user, username),
                )
                accepted = cur.rowcount > 0

                if accepted or already_friends:
                    # Remove crossing pending/rejected clutter and make the
                    # bidirectional helper table match canonical accepted truth.
                    cur.execute(
                        """
                        DELETE FROM friend_requests
                         WHERE LOWER(from_user) = LOWER(%s)
                           AND LOWER(to_user) = LOWER(%s)
                           AND request_status IN ('pending', 'rejected');
                        """,
                        (username, from_user),
                    )
                    cur.execute(
                        """
                        INSERT INTO friends (user_id, friend_id)
                        VALUES (
                            (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1),
                            (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                        ),
                        (
                            (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1),
                            (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                        )
                        ON CONFLICT DO NOTHING;
                        """,
                        (from_user, username, username, from_user),
                    )
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[DB ERROR] accept_friend_request: {e}")
            return {"success": False, "error": "Database error"}

        if accepted or already_friends:
            _emit_friend_request_state(username)
            _emit_friend_request_state(from_user)

            # Let the requester know, if they're online.
            if accepted:
                _emit_to_user(from_user, "friend_request_accepted", {"by": username})

            # New friends should immediately see each other's current presence.
            try:
                _emit_to_user(username, "friends_presence", [_public_presence_for_user(from_user)])
                _emit_to_user(from_user, "friends_presence", [_public_presence_for_user(username)])
            except Exception:
                pass

            return {"success": True, "already_friends": bool(already_friends and not accepted)}

        _emit_friend_request_state(username)
        return {"success": False, "error": "Request not found"}




    @socketio.on("block_user")
    @jwt_required()
    def handle_block_user(data):
        blocker = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(blocker, "block_user", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard:
            return guard
        denied = _social_action_denial(blocker, action="block")
        if denied is not None:
            return denied

        lim, win = _parse_rate_limit(settings.get("social_action_rate_limit"), default_limit=60, default_window=60)
        try:
            win = int(settings.get("social_action_rate_window_sec") or win)
        except Exception:
            pass
        okrl, retry = _rl(f"block:{blocker}", lim, win)
        if not okrl:
            return {"success": False, "error": "Rate limited", "retry_after": retry}

        requested_blocked = str((data or {}).get("blocked") or "").strip()
        blocked = _resolve_canonical_username(requested_blocked)

        if not blocked or blocked.lower() == str(blocker or "").strip().lower():
            return {"success": False, "error": "Invalid user"}

        removed_friendship = False
        removed_pending = False
        removed_room_invites = False
        removed_group_invites = False
        removed_offline_pms = False
        removed_profile_notifications = False

        try:
            conn = get_db()
            with conn.cursor() as cur:
                _lock_friend_pair(cur, blocker, blocked)
                # Prevent double-block. Case-insensitive so legacy mixed-case
                # block rows still enforce the user's intent. The pair lock above
                # serializes fast duplicate block clicks across tabs.
                cur.execute(
                    """
                    SELECT 1
                      FROM blocks
                     WHERE LOWER(blocker) = LOWER(%s)
                       AND LOWER(blocked) = LOWER(%s)
                     LIMIT 1;
                    """,
                    (blocker, blocked),
                )
                if cur.fetchone():
                    conn.rollback()
                    return {"success": False, "error": "Already blocked"}

                cur.execute(
                    """
                    DELETE FROM friend_requests
                     WHERE ((LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s))
                            OR (LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s)))
                       AND request_status = 'accepted';
                    """,
                    (blocker, blocked, blocked, blocker),
                )
                removed_friendship = bool(cur.rowcount)

                cur.execute(
                    """
                    DELETE FROM friend_requests
                     WHERE ((LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s))
                            OR (LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s)))
                       AND request_status IN ('pending', 'rejected');
                    """,
                    (blocker, blocked, blocked, blocker),
                )
                removed_pending = bool(cur.rowcount)

                # A block should also clear pending invite surfaces between the
                # pair so the blocked user cannot keep stale actionable invites.
                cur.execute(
                    """
                    DELETE FROM room_invites
                     WHERE (LOWER(invited_by) = LOWER(%s) AND LOWER(invited_user) = LOWER(%s))
                        OR (LOWER(invited_by) = LOWER(%s) AND LOWER(invited_user) = LOWER(%s));
                    """,
                    (blocker, blocked, blocked, blocker),
                )
                removed_room_invites = bool(cur.rowcount)

                cur.execute(
                    """
                    DELETE FROM custom_room_invites
                     WHERE (LOWER(invited_by) = LOWER(%s) AND LOWER(invited_user) = LOWER(%s))
                        OR (LOWER(invited_by) = LOWER(%s) AND LOWER(invited_user) = LOWER(%s));
                    """,
                    (blocker, blocked, blocked, blocker),
                )
                removed_room_invites = bool(removed_room_invites or cur.rowcount)

                cur.execute(
                    """
                    UPDATE group_invites
                       SET status = 'revoked'
                     WHERE status = 'pending'
                       AND ((LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s))
                         OR (LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s)));
                    """,
                    (blocker, blocked, blocked, blocker),
                )
                removed_group_invites = bool(cur.rowcount)

                # Blocking should also clear missed/private-message alert state
                # between the pair. Keep rows for audit/history retention, but mark
                # undelivered ciphertext consumed so blocked users cannot revive
                # stale PM bubbles after refresh.
                cur.execute(
                    """
                    UPDATE offline_messages
                       SET delivered = TRUE
                     WHERE delivered = FALSE
                       AND ((LOWER(sender) = LOWER(%s) AND LOWER(receiver) = LOWER(%s))
                         OR (LOWER(sender) = LOWER(%s) AND LOWER(receiver) = LOWER(%s)));
                    """,
                    (blocker, blocked, blocked, blocker),
                )
                removed_offline_pms = bool(cur.rowcount)

                # Hide persisted profile-post alerts created by the newly
                # blocked pair. Rows are kept for retention/audit, but they stop
                # surfacing as unread notifications after the block.
                profile_notification_ids = []
                cur.execute(
                    """
                    SELECT n.id, u.username, n.notification
                      FROM notifications n
                      JOIN users u ON u.id = n.user_id
                     WHERE n.type LIKE 'profile_post_%%'
                       AND (LOWER(u.username) = LOWER(%s) OR LOWER(u.username) = LOWER(%s));
                    """,
                    (blocker, blocked),
                )
                for row in cur.fetchall() or []:
                    try:
                        notif_id = int(row[0])
                        recipient = str(row[1] or "").strip()
                        payload = json.loads(str(row[2] or "{}"))
                        actor = str((payload or {}).get("actor") or "").strip() if isinstance(payload, dict) else ""
                        if ((recipient.lower() == str(blocker).lower() and actor.lower() == str(blocked).lower())
                                or (recipient.lower() == str(blocked).lower() and actor.lower() == str(blocker).lower())):
                            profile_notification_ids.append(notif_id)
                    except Exception:
                        continue
                if profile_notification_ids:
                    cur.execute(
                        "UPDATE notifications SET is_read = TRUE WHERE id = ANY(%s);",
                        (profile_notification_ids,),
                    )
                    removed_profile_notifications = bool(cur.rowcount)

                cur.execute(
                    """
                    DELETE FROM friends
                     WHERE (user_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                        AND friend_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1))
                        OR (user_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                        AND friend_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1));
                    """,
                    (blocker, blocked, blocked, blocker),
                )

                cur.execute(
                    """
                    INSERT INTO blocks (blocker, blocked)
                    SELECT %s, %s
                     WHERE NOT EXISTS (
                        SELECT 1 FROM blocks
                         WHERE LOWER(blocker) = LOWER(%s)
                           AND LOWER(blocked) = LOWER(%s)
                     );
                    """,
                    (blocker, blocked, blocker, blocked),
                )
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[DB ERROR] block_user: {e}")
            return {"success": False, "error": "Database error"}

        live_cleanup = _cleanup_social_pair_realtime_sessions(blocker, blocked)

        try:
            _emit_blocked_users_state(blocker)
        except Exception:
            pass

        try:
            emit("friends_list", get_friends_for_user(blocker), to=request.sid)
        except Exception:
            pass

        try:
            emit("pending_friend_requests", get_pending_friend_requests(blocker), to=request.sid)
        except Exception:
            pass

        try:
            _emit_to_user(blocked, "friends_list", get_friends_for_user(blocked))
        except Exception:
            pass

        try:
            _emit_to_user(blocked, "pending_friend_requests", get_pending_friend_requests(blocked))
        except Exception:
            pass

        # Refresh all alert surfaces for both sides. Blocks revoke group/room
        # invites and consume missed PM rows; this event also lets each browser
        # immediately drop stale local bubbles before the server refresh returns.
        try:
            _emit_to_user(blocker, "social_alert_cleanup", {"reason": "block", "peer": blocked})
            _emit_to_user(blocked, "social_alert_cleanup", {"reason": "block", "peer": blocker})
        except Exception:
            pass

        try:
            _emit_pair_room_visibility_refresh(blocker, blocked, reason="block")
        except Exception:
            pass

        if removed_group_invites:
            try:
                _emit_to_user(blocker, "groups_refresh", {"reason": "block_cleanup", "peer": blocked})
                _emit_to_user(blocked, "groups_refresh", {"reason": "block_cleanup", "peer": blocker})
            except Exception:
                pass

        try:
            for _sid in _user_sids(blocker):
                _emit_missed_pm_summary(blocker, _sid)
            for _sid in _user_sids(blocked):
                _emit_missed_pm_summary(blocked, _sid)
        except Exception:
            pass

        # Remove stale presence/friend badges from both clients immediately.
        try:
            _emit_to_user(blocker, "friend_presence_update", {"username": blocked, "online": False, "presence": "offline", "custom_status": None, "last_seen": None, "avatar_url": ""})
            _emit_to_user(blocked, "friend_presence_update", {"username": blocker, "online": False, "presence": "offline", "custom_status": None, "last_seen": None, "avatar_url": ""})
        except Exception:
            pass

        return {"success": True, "blocked": blocked, "removed_friendship": bool(removed_friendship), "removed_pending": bool(removed_pending), "removed_room_invites": bool(removed_room_invites), "removed_group_invites": bool(removed_group_invites), "removed_offline_pms": bool(removed_offline_pms), "removed_profile_notifications": bool(removed_profile_notifications), "live_cleanup": live_cleanup}


    @socketio.on("unblock_user")
    @jwt_required()
    def handle_unblock_user(data):
        blocker = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(blocker, "unblock_user", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard:
            return guard
        denied = _social_action_denial(blocker, action="unblock")
        if denied is not None:
            return denied

        lim, win = _parse_rate_limit(settings.get("social_action_rate_limit"), default_limit=60, default_window=60)
        try:
            win = int(settings.get("social_action_rate_window_sec") or win)
        except Exception:
            pass
        okrl, retry = _rl(f"unblock:{blocker}", lim, win)
        if not okrl:
            return {"success": False, "error": "Rate limited", "retry_after": retry}

        blocked = _resolve_canonical_username((data or {}).get("blocked"))

        if not blocked or blocked.lower() == str(blocker or "").strip().lower():
            return {"success": False, "error": "Invalid user"}

        affected = 0
        try:
            conn = get_db()
            with conn.cursor() as cur:
                _lock_friend_pair(cur, blocker, blocked)
                cur.execute(
                    """
                    DELETE FROM blocks
                     WHERE LOWER(blocker) = LOWER(%s)
                       AND LOWER(blocked) = LOWER(%s);
                    """,
                    (blocker, blocked),
                )
                affected = int(cur.rowcount or 0)
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[DB ERROR] unblock_user: {e}")
            return {"success": False, "error": "Database error"}

        if affected:
            _emit_blocked_users_state(blocker)
            # Unblocking can make older incoming pending requests visible again,
            # so refresh the social panels even though it does not recreate a
            # friendship by itself.
            _emit_friend_request_state(blocker)
            _emit_friend_request_state(blocked)
            _emit_unblock_realtime_refresh(blocker, blocked)
            return {"success": True, "blocked": blocked}
        _emit_blocked_users_state(blocker)
        # A stale browser may ask to unblock after the DB row is already gone.
        # Treat the client-side privacy state as clear and refresh live room state
        # instead of leaving the sender with an old E2EE recipient exclusion.
        _emit_unblock_realtime_refresh(blocker, blocked)
        return {"success": False, "error": "Not blocked", "blocked": blocked}


    @socketio.on("get_friends")
    @jwt_required()
    def handle_get_friends(data=None):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_friends", data, default_max_bytes=2048, default_limit=120, default_window=60)
        if guard:
            return guard
        friends = get_friends_for_user(username)
        emit("friends_list", friends, to=request.sid)
        try:
            emit("friends_presence", [_public_presence_for_user(f) for f in (friends or [])], to=request.sid)
        except Exception:
            pass
        return {"friends": friends}


    @socketio.on("get_my_presence")
    @jwt_required()
    def handle_get_my_presence(data=None):
        data = data if isinstance(data, dict) else {}
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_my_presence", data, default_max_bytes=2048, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        snapshot = _self_presence_snapshot(username)
        emit("my_presence", snapshot, to=request.sid)
        return {"success": True, **snapshot}



    @socketio.on("set_my_presence")
    @jwt_required()
    def handle_set_my_presence(data):
        """Update the caller's presence_status and/or custom_status.

        Data:
          presence: online|away|busy|invisible (offline is accepted as appear-offline/invisible)
          custom_status: optional text (<=128; empty clears)
        """
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        data = data if isinstance(data, dict) else {}
        guard = _socket_event_guard(username, "set_my_presence", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard is not None:
            return guard

        # Determine what fields were explicitly provided
        presence_provided = any(k in data for k in ("presence", "status"))
        custom_provided = any(k in data for k in ("custom_status", "customStatus", "custom"))

        presence = _normalize_presence(data.get("presence") if "presence" in data else data.get("status"))
        if presence_provided and not presence:
            return {"success": False, "error": "Invalid presence"}

        raw_custom = None
        if "custom_status" in data:
            raw_custom = data.get("custom_status")
        elif "customStatus" in data:
            raw_custom = data.get("customStatus")
        elif "custom" in data:
            raw_custom = data.get("custom")

        custom_status = _sanitize_custom_status(raw_custom)
        if custom_provided and raw_custom is not None and isinstance(raw_custom, str) and len(raw_custom.strip()) > 128:
            # Note: we clamp, but also tell caller we truncated.
            pass

        if not (presence_provided or custom_provided):
            return {"success": False, "error": "No updates"}

        denied = _social_action_denial(username, action="presence")
        if denied is not None:
            return denied

        try:
            conn = get_db()
            sets = []
            params = []
            if presence_provided:
                sets.append("presence_status = %s")
                params.append(presence)
            if custom_provided:
                sets.append("custom_status = %s")
                params.append(custom_status)  # None clears
            params.append(username)
            with conn.cursor() as cur:
                cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE username = %s;", tuple(params))
            conn.commit()
        except Exception as e:
            return {"success": False, "error": "Database error"}

        # Update caller UI (all sessions)
        try:
            _emit_to_user(username, "my_presence", _self_presence_snapshot(username))
        except Exception:
            pass

        # Push viewer-safe snapshot to friends
        _broadcast_presence_to_friends(username)
        snapshot = _self_presence_snapshot(username)
        return {"success": True, **snapshot}



    @socketio.on("get_friend_presence")
    @jwt_required()
    def handle_get_friend_presence(data=None):
        """Return viewer-safe presence for all friends.

        Emitted payload (array):
          [{username, online, presence, custom_status, last_seen}, ...]

        Notes:
          - If a friend is in "invisible", they appear as offline.
          - custom_status is hidden when offline/invisible.
        """
        data = data if isinstance(data, dict) else {}
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_friend_presence", data, default_max_bytes=2048, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        friends = get_friends_for_user(username) or []
        presence_payload = []

        if friends:
            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT username, online, presence_status, custom_status, last_seen, avatar_url
                          FROM users
                         WHERE username = ANY(%s);
                        """,
                        (friends,),
                    )
                    rows = cur.fetchall() or []

                row_map = {str(r[0]).strip().lower(): r for r in rows}
                for u in friends:
                    r = row_map.get(str(u).strip().lower())
                    if r:
                        presence_payload.append(_public_presence_snapshot_from_row(r[0], r[1], r[2], r[3], r[4], r[5] if len(r) > 5 else None))
                    else:
                        presence_payload.append({"username": u, "online": False, "presence": "offline", "custom_status": None, "last_seen": None, "avatar_url": ""})
            except Exception:
                presence_payload = [{"username": u, "online": False, "presence": "offline", "custom_status": None, "last_seen": None, "avatar_url": ""} for u in friends]

        emit("friends_presence", presence_payload, to=request.sid)
        return {"friends_presence": presence_payload}


    @socketio.on("send_friend_request")
    @jwt_required()
    def handle_send_friend_request(data):
        to_username_raw = (data or {}).get("to_username")
        from_username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(from_username, "send_friend_request", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard:
            return guard
        denied = _social_action_denial(from_username, action="friend_request")
        if denied is not None:
            return denied

        to_username = _resolve_canonical_username(to_username_raw)
        if not to_username:
            return {"success": False, "error": "User not found"}

        if to_username.lower() == str(from_username or "").strip().lower():
            return {"success": False, "error": "Cannot friend yourself"}

        ok, err = _require_not_sanctioned(from_username, action="send")
        if not ok:
            return {"success": False, "error": err}

        if _either_blocked(from_username, to_username):
            return {"success": False, "error": "Blocked"}

        # Anti-abuse: friend request flood control.  Keep this before the DB
        # write path, then recheck block/friendship state inside the pair lock.
        okrl, retry = _friend_req_rate_ok(from_username)
        if not okrl:
            if _abuse_strike(from_username, "friendreq_rate"):
                return {"success": False, "error": "Auto-muted for spamming. Try again later."}
            return {"success": False, "error": f"Rate limited (wait {retry:.1f}s)"}

        okspread, serr = _friend_req_target_spread_ok(from_username, to_username)
        if not okspread:
            return {"success": False, "error": serr or "Too many targets"}

        try:
            conn = get_db()
            with conn.cursor() as cur:
                _lock_friend_pair(cur, from_username, to_username)

                if _friend_pair_blocked_in_tx(cur, from_username, to_username):
                    conn.rollback()
                    return {"success": False, "error": "Blocked"}

                # Canonical friendship check: accepted rows in friend_requests are
                # the real source of truth for the live social UI. The friends
                # table is a denormalized helper table and may contain stale rows
                # if an older remove/unwind path did not clean both directions.
                cur.execute(
                    """
                    SELECT 1
                      FROM friend_requests
                     WHERE ((LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s))
                         OR (LOWER(from_user) = LOWER(%s) AND LOWER(to_user) = LOWER(%s)))
                       AND request_status = 'accepted'
                     LIMIT 1;
                    """,
                    (from_username, to_username, to_username, from_username),
                )
                if cur.fetchone():
                    conn.rollback()
                    _emit_friend_request_state(from_username)
                    return {"success": False, "error": "Already friends", "to_username": to_username}

                # Self-heal stale helper rows for this pair so a prior bad state
                # does not block a legitimate re-request.
                cur.execute(
                    """
                    DELETE FROM friends
                     WHERE (user_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                        AND friend_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1))
                        OR (user_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1)
                        AND friend_id = (SELECT id FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1));
                    """,
                    (from_username, to_username, to_username, from_username),
                )

                # Prevent duplicate pending requests in the same direction.  The
                # user-row pair lock above serializes same-pair tabs/processes so
                # this check remains stable until the insert commits.
                cur.execute(
                    """
                    SELECT 1
                      FROM friend_requests
                     WHERE LOWER(from_user) = LOWER(%s)
                       AND LOWER(to_user) = LOWER(%s)
                       AND request_status = 'pending'
                     LIMIT 1;
                    """,
                    (from_username, to_username),
                )
                if cur.fetchone():
                    conn.rollback()
                    _emit_friend_request_state(to_username)
                    return {"success": False, "error": "Request already pending", "to_username": to_username}

                # If the target already sent the caller a pending request, do not
                # create a second crossing request. Tell the caller to accept the
                # incoming request and refresh their pending snapshot immediately.
                cur.execute(
                    """
                    SELECT 1
                      FROM friend_requests
                     WHERE LOWER(from_user) = LOWER(%s)
                       AND LOWER(to_user) = LOWER(%s)
                       AND request_status = 'pending'
                     LIMIT 1;
                    """,
                    (to_username, from_username),
                )
                if cur.fetchone():
                    conn.rollback()
                    _emit_friend_request_state(from_username)
                    return {
                        "success": False,
                        "error": "That user already sent you a request",
                        "incoming_pending": True,
                        "to_username": to_username,
                    }

                cur.execute(
                    """
                    INSERT INTO friend_requests (from_user, to_user, request_status)
                    VALUES (%s, %s, 'pending');
                    """,
                    (from_username, to_username),
                )
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[DB ERROR] send_friend_request: {e}")
            return {"success": False, "error": "Database error"}

        delivered_live = _emit_to_user(to_username, "friend_request", {"from": from_username})

        # Push canonical snapshots to both users. This makes incoming requests
        # resilient to dropped realtime pings and reconnect timing, because the
        # UI can rebuild from DB truth instantly.
        _emit_friend_request_state(from_username)
        _emit_friend_request_state(to_username)

        try:
            pending_for_target = get_pending_friend_requests(to_username)
        except Exception:
            pending_for_target = None
        if pending_for_target is not None:
            _emit_to_user(to_username, "pending_friend_requests", pending_for_target)

        return {"success": True, "delivered_live": bool(delivered_live)}


    @socketio.on("reject_friend_request")
    @jwt_required()
    def handle_reject_friend_request(data):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "reject_friend_request", data, default_max_bytes=4096, default_limit=60, default_window=60)
        if guard:
            return guard
        denied = _social_action_denial(username, action="reject_friend")
        if denied is not None:
            return denied
        from_user = _resolve_canonical_username((data or {}).get("from_user"))

        if not from_user:
            emit("notification", "Invalid friend request to reject", to=request.sid)
            return {"success": False, "error": "Invalid friend request"}

        if from_user.lower() == str(username or "").strip().lower():
            return {"success": False, "error": "Invalid friend request"}

        affected = 0
        already_rejected = False
        try:
            conn = get_db()
            with conn.cursor() as cur:
                _lock_friend_pair(cur, username, from_user)
                cur.execute(
                    """
                    UPDATE friend_requests
                       SET request_status = 'rejected'
                     WHERE LOWER(from_user) = LOWER(%s)
                       AND LOWER(to_user) = LOWER(%s)
                       AND request_status = 'pending';
                    """,
                    (from_user, username),
                )
                affected = int(cur.rowcount or 0)
                if not affected:
                    cur.execute(
                        """
                        SELECT 1
                          FROM friend_requests
                         WHERE LOWER(from_user) = LOWER(%s)
                           AND LOWER(to_user) = LOWER(%s)
                           AND request_status = 'rejected'
                         LIMIT 1;
                        """,
                        (from_user, username),
                    )
                    already_rejected = cur.fetchone() is not None
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[DB ERROR] reject_friend_request: {e}")
            return {"success": False, "error": "Database error"}

        if affected or already_rejected:
            _emit_friend_request_state(username)
            _emit_friend_request_state(from_user)
            _emit_to_user(from_user, "friend_request_rejected", {"by": username})
            return {"success": True, "already_rejected": bool(already_rejected and not affected)}

        _emit_friend_request_state(username)
        return {"success": False, "error": "Request not found"}
