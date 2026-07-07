"""Socket.IO handlers: voice.

Auto-split from the legacy monolithic socket_handlers.py.
"""

import json
import re
import time
import uuid
import threading
from collections import deque

from flask import request, current_app
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
    touch_custom_room_activity,
    consume_room_invites,
    set_room_message_expiry,
    get_room_message_expiry,
)
from security import log_audit_event
from permissions import check_user_permission
from moderation import is_user_sanctioned, mute_user
from echo_voice_protocol import echo_voice_bool, echo_voice_room_capacity, echo_voice_room_limit

from realtime.state import *

def register(socketio, settings, ctx):
    """Register Socket.IO event handlers for this module."""
    # Make helper functions from socket_handlers available as module globals
    globals().update(ctx.__dict__)


    def _voice_feature_enabled() -> bool:
        """Return whether room/DM voice signaling is enabled by admin settings."""
        return echo_voice_bool(settings, "voice_enabled", True)

    def _voice_disabled_payload() -> dict:
        return {"success": False, "error": "Voice is disabled by admin policy", "error_code": "voice_disabled"}



    def _voice_setting_int(*names, default=1):
        for name in names:
            try:
                val = settings.get(name)
                if val is not None and val != "":
                    return max(1, int(val))
            except Exception:
                pass
        return int(default)

    def _voice_socketio_queue_configured() -> bool:
        try:
            queue = str(current_app.config.get("ECHOCHAT_SOCKETIO_MESSAGE_QUEUE") or "").strip()
            if queue:
                return True
        except Exception:
            pass
        return bool(str(settings.get("socketio_message_queue") or settings.get("message_queue") or "").strip())

    def _voice_scaled_topology_active() -> bool:
        instances = _voice_setting_int("production_instance_count", "production_instances", "instance_count", default=1)
        workers = _voice_setting_int("production_workers", "workers", "worker_count", default=1)
        return instances > 1 or workers > 1

    def _voice_topology_guard_payload() -> dict | None:
        """Block voice in unsafe scaled topology instead of lying with 'offline'."""
        if not _voice_scaled_topology_active():
            return None
        queue_ok = _voice_socketio_queue_configured()
        shared_ok = False
        try:
            shared_ok = bool(shared_state_enabled())
        except Exception:
            shared_ok = False
        if queue_ok and shared_ok:
            return None
        return {
            "success": False,
            "delivered": False,
            "error": "Voice realtime is not safe for multi-worker mode yet. Set socketio_message_queue and shared_state_redis_url, or run one instance/worker for voice.",
            "error_code": "voice_realtime_topology_unsafe",
            "voice_realtime": {
                "scaled_topology": True,
                "socketio_message_queue": bool(queue_ok),
                "shared_state_redis": bool(shared_ok),
            },
        }

    def _voice_delivery_snapshot(username: str) -> dict:
        username = str(username or "").strip()
        sids = []
        try:
            sids = list(_user_sids(username)) if username else []
        except Exception:
            sids = []
        presence = {}
        try:
            presence = _get_user_presence_row(username) if username else {}
        except Exception:
            presence = {}
        shared = {}
        try:
            shared = shared_state_summary()
        except Exception:
            shared = {"enabled": False}
        return {
            "username": username,
            "sid_count": len(sids),
            "sids": sids[:6],
            "db_online": bool((presence or {}).get("online")),
            "presence_status": str((presence or {}).get("presence_status") or ""),
            "shared_state_enabled": bool(shared.get("enabled")),
            "socketio_message_queue": _voice_socketio_queue_configured(),
            "scaled_topology": _voice_scaled_topology_active(),
        }

    def _voice_delivery_error(target: str, snapshot: dict | None = None) -> tuple[str, str]:
        snap = snapshot or _voice_delivery_snapshot(target)
        if snap.get("db_online") and not snap.get("sid_count"):
            return (
                "voice_realtime_unavailable",
                "User appears online, but their realtime voice connection is unavailable. Have them refresh and try again.",
            )
        if snap.get("scaled_topology") and (not snap.get("socketio_message_queue") or not snap.get("shared_state_enabled")):
            return (
                "voice_realtime_topology_unsafe",
                "Voice needs Redis realtime queue/shared state in multi-worker mode. Run one worker or enable Redis voice realtime.",
            )
        return (
            "voice_target_not_connected",
            "User is not connected to realtime right now.",
        )

    def _voice_log_delivery(event_name: str, sender: str, target: str, call_id: str, delivered: bool, *, reason: str = "") -> dict:
        sender_snap = _voice_delivery_snapshot(sender)
        target_snap = _voice_delivery_snapshot(target)
        payload = {
            "event": event_name,
            "sender": sender_snap,
            "target": target_snap,
            "call_id": str(call_id or ""),
            "delivered": bool(delivered),
            "reason": reason,
        }
        if not delivered:
            try:
                current_app.logger.warning(
                    "Voice realtime delivery failed event=%s sender=%s target=%s call_id=%s reason=%s sender_sids=%s target_sids=%s target_db_online=%s target_presence=%s shared_state=%s queue=%s scaled=%s",
                    event_name,
                    sender,
                    target,
                    call_id,
                    reason,
                    sender_snap.get("sid_count"),
                    target_snap.get("sid_count"),
                    target_snap.get("db_online"),
                    target_snap.get("presence_status"),
                    target_snap.get("shared_state_enabled"),
                    target_snap.get("socketio_message_queue"),
                    target_snap.get("scaled_topology"),
                )
            except Exception:
                pass
        return payload

    def _voice_emit_to_user(sender: str, target: str, event_name: str, payload: dict, call_id: str):
        delivered = _emit_to_user(target, event_name, payload)
        diag = _voice_log_delivery(event_name, sender, target, call_id, delivered)
        return delivered, diag

    def _voice_not_delivered_payload(target: str, diagnostic: dict | None = None) -> dict:
        snap = ((diagnostic or {}).get("target") or None)
        code, message = _voice_delivery_error(target, snap)
        return {"success": True, "delivered": False, "error": message, "error_code": code, "voice_realtime": diagnostic or {}}

    def _voice_dm_ttl(state: str = "active") -> float:
        if str(state or "") == "invited":
            return max(float(settings.get("voice_dm_invite_ttl_seconds", 90) or 90), 60)
        return max(float(settings.get("voice_dm_active_ttl_seconds", 3600) or 3600), 120)


    def _event_bool(value, default: bool = False) -> bool:
        """Parse Socket.IO media booleans safely.

        Browser events normally send native booleans, but reconnect/replay tools,
        older clients, and hand-written tests can send strings. Python's raw
        bool("false") is True, which is dangerous for webcam/voice status and
        approval events.
        """
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return bool(default)
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "allow", "allowed"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "deny", "denied"}:
            return False
        return bool(default)

    def _group_voice_id(room: str) -> int | None:
        """Return group id for EchoChat group voice rooms like group_123."""
        m = re.match(r"^group_(\d+)$", str(room or "").strip())
        if not m:
            return None
        try:
            gid = int(m.group(1))
        except Exception:
            return None
        return gid if gid > 0 else None

    def _can_use_voice_room(username: str, sid: str, room: str) -> tuple[bool, str | None, int | None]:
        """Authorize normal room voice or private group-window voice.

        Normal room voice still requires the socket's active room to match.
        Group voice uses the existing group Socket.IO room name (group_<id>)
        and requires current group membership so users cannot join arbitrary
        group voice rooms by guessing ids.
        """
        room = str(room or "").strip()
        if not room:
            return False, "Missing room", None
        current_room = get_connected_room(sid)
        if current_room == room:
            try:
                meta = get_custom_room_meta(room)
            except Exception:
                meta = None
            if meta and meta.get("is_private"):
                try:
                    if not can_user_join_custom_room(room, username):
                        return False, "Private room invite must be accepted first", None
                except Exception:
                    return False, "Private room invite required", None
            return True, None, None
        group_id = _group_voice_id(room)
        if group_id is None:
            return False, "Not in that room", None
        try:
            user_id = _get_user_id_by_username(username)
        except Exception:
            user_id = None
        try:
            allowed = bool(user_id and _is_group_member(group_id, user_id))
        except Exception:
            allowed = False
        if not allowed:
            return False, "Not in that group", group_id
        return True, None, group_id

    @socketio.on("voice_room_join")
    @jwt_required()
    def handle_voice_room_join(data):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "voice_room_join", data, default_max_bytes=8192, default_limit=60, default_window=60)
        if guard:
            return guard
        sid = request.sid
        room = (data or {}).get("room")
        viewer_only = _event_bool((data or {}).get("viewer_only"), False)
        audio_requested = _event_bool((data or {}).get("audio"), not viewer_only)
        voice_on = bool(audio_requested and not viewer_only)

        if not room:
            return {"success": False, "error": "Missing room"}
        if voice_on and not _voice_feature_enabled():
            return _voice_disabled_payload()
        if voice_on:
            topology_guard = _voice_topology_guard_payload()
            if topology_guard is not None:
                return topology_guard

        ok, err = _require_not_sanctioned(username, action="voice")
        if not ok:
            return {"success": False, "error": err}

        # Only allow voice join for the active room, or for a group voice room
        # where the socket user is a current member.
        ok_room, room_err, group_voice_id = _can_use_voice_room(username, sid, room)
        if not ok_room:
            return {"success": False, "error": room_err or "Not in that room"}
        if group_voice_id is not None:
            # Group chat windows use the same Socket.IO room for text + voice
            # signaling. Joining here is idempotent and makes group voice work
            # even if the client clicks Voice immediately after opening the group.
            join_room(room)

        ok2, err2, roster = _voice_room_add(room, username)
        capacity = echo_voice_room_capacity(len(roster), settings)
        limit = int(capacity.get("limit") or 0)
        if not ok2:
            payload = {
                "success": False,
                "error": err2 or "Voice join denied",
                "error_code": "voice_room_full" if capacity.get("full") else "voice_join_denied",
                "full": bool(capacity.get("full")),
                "users": roster,
                "limit": limit,
                "current": int(capacity.get("current") or len(roster)),
                "capacity": capacity,
            }
            if capacity.get("full"):
                try:
                    emit("voice_room_full", {"room": room, "limit": limit, "current": int(capacity.get("current") or len(roster)), "capacity": capacity}, to=sid)
                except Exception:
                    pass
            return payload

        # Push roster to the joiner; notify others.  Viewer-only webcam watchers
        # join the signaling mesh but must not be advertised as speaking/voice-on.
        capacity = echo_voice_room_capacity(len(roster), settings)
        media_status_update(room, username, voice_on=voice_on)
        media_map = media_status_for_room(room)
        emit("voice_room_roster", {"room": room, "users": roster, "limit": int(capacity.get("limit") or 0), "capacity": capacity, "media_status": media_map}, to=sid)
        emit("voice_room_user_joined", {"room": room, "username": username, "voice_on": voice_on, "viewer_only": viewer_only, "capacity": capacity}, room=room, include_self=False)
        emit("voice_media_status", {"room": room, "username": username, "voice_on": voice_on, "webcam_on": media_map.get(str(username), {}).get("webcam_on", False)}, room=room)
        try:
            touch_custom_room_activity(room)
        except Exception:
            pass
        return {"success": True, "users": roster, "limit": limit, "voice_on": voice_on, "viewer_only": viewer_only}


    @socketio.on("voice_room_leave")
    @jwt_required()
    def handle_voice_room_leave(data):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "voice_room_leave", data, default_max_bytes=4096, default_limit=90, default_window=60)
        if guard:
            return guard
        sid = request.sid
        room = (data or {}).get("room") or get_connected_room(sid)
        if not room:
            return {"success": True}
        removed = False
        try:
            removed = _voice_room_remove(room, username)
        except Exception:
            removed = False
        if removed:
            owner_had_cam_state = False
            try:
                owner_had_cam_state = _webcam_owner_has_state(room, username)
            except Exception:
                owner_had_cam_state = False
            try:
                viewers, pending = _webcam_clear_owner(room, username)
                for viewer in set(viewers) | set(pending):
                    try:
                        _emit_to_user(viewer, "webcam_view_kick", {"room": room, "owner": username, "reason": "owner_left", "server_enforced": True})
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                for owner in _webcam_remove_viewer_from_room(room, username):
                    try:
                        _emit_to_user(owner, "webcam_viewing", {"room": room, "viewer": username, "viewing": False, "reason": "viewer_left", "server_enforced": True})
                    except Exception:
                        pass
            except Exception:
                pass
            st = media_status_update(room, username, voice_on=False, webcam_on=False if owner_had_cam_state else False)
            emit("voice_room_user_left", {"room": room, "username": username}, room=room)
            emit("voice_media_status", {"room": room, "username": username, "voice_on": st.get("voice_on", False), "webcam_on": st.get("webcam_on", False)}, room=room)
        try:
            touch_custom_room_activity(room)
        except Exception:
            pass
        roster = _voice_room_users(room)
        capacity = echo_voice_room_capacity(len(roster), settings)
        emit("voice_room_roster", {"room": room, "users": roster, "limit": int(capacity.get("limit") or 0), "capacity": capacity}, to=sid)
        return {"success": True}



    def _voice_signal_guard(username: str, event_name: str):
        """Bound WebRTC/voice signaling bursts per user and event family."""
        lim, win = _parse_rate_limit(settings.get("voice_signal_rate_limit"), default_limit=240, default_window=60)
        try:
            win = int(settings.get("voice_signal_rate_window_sec") or win)
        except Exception:
            pass
        okrl, retry = _rl(f"voice_sig:{event_name}:{username}", lim, win)
        if not okrl:
            return {"success": False, "error": "Rate limited", "retry_after": retry}
        return None

    # Echo webcam viewer policy helpers. EchoChat keeps the policy/approval
    # source of truth here; the camera owner browser mirrors approved viewers
    # into Echo webcam's track-subscription permissions so unapproved viewers
    # do not receive camera tracks.
    def _webcam_policy():
        # Compatibility policy for old webcam-control events.  The active media
        # engine is Echo built-in WebRTC; this local policy avoids any external
        # media-server dependency.
        enabled = echo_voice_bool(settings, "webcam_enabled", echo_voice_bool(settings, "echo_webcam_enabled", True))
        raw_mode = str(settings.get("webcam_approval_mode") or settings.get("webcam_approval_mode") or "owner_approval").strip().lower().replace("-", "_")
        if not enabled or raw_mode in {"disabled", "blocked", "off"}:
            approval_mode = "disabled"
        elif raw_mode in {"owner", "owner_approval", "ask", "request", "request_required"}:
            approval_mode = "owner_approval"
        else:
            approval_mode = "open"
        try:
            max_viewers = int(settings.get("webcam_max_viewers", settings.get("webcam_max_viewers", 0)) or 0)
        except Exception:
            max_viewers = 0
        return {
            "webcam_approval_mode": approval_mode,
            "webcam_max_viewers": max(0, min(500, max_viewers)),
            "default_media_policy": str(settings.get("webcam_default_media_policy") or "user_choice"),
            "server_enforced_webcam_permissions": False,
        }

    def _webcam_room_state(room: str) -> dict:
        room = str(room or "").strip()
        with WEBCAM_PERMISSIONS_LOCK:
            return WEBCAM_PERMISSIONS.setdefault(room, {})

    def _webcam_owner_state(room: str, owner: str) -> dict:
        owner = str(owner or "").strip()
        with WEBCAM_PERMISSIONS_LOCK:
            room_map = WEBCAM_PERMISSIONS.setdefault(str(room or "").strip(), {})
            return room_map.setdefault(owner, {"camera_on": False, "pending": set(), "approved": set(), "viewing": set()})

    def _webcam_snapshot(room: str, owner: str) -> dict:
        with WEBCAM_PERMISSIONS_LOCK:
            st = (WEBCAM_PERMISSIONS.get(str(room or "").strip()) or {}).get(str(owner or "").strip()) or {}
            return {
                "camera_on": bool(st.get("camera_on")),
                "pending": sorted(list(st.get("pending") or set())),
                "approved": sorted(list(st.get("approved") or set())),
                "viewing": sorted(list(st.get("viewing") or set())),
            }

    def _webcam_public_snapshot(room: str, owner: str) -> dict:
        """Return non-secret webcam viewer state for owner/client UI updates."""
        snap = _webcam_snapshot(room, owner)
        viewers = sorted(set(snap.get("viewing") or []))
        return {
            "camera_on": bool(snap.get("camera_on")),
            "viewers": viewers,
            "viewer_count": len(viewers),
            "pending_viewers": sorted(set(snap.get("pending") or [])),
            "approved_viewers": sorted(set(snap.get("approved") or [])),
        }

    def _webcam_viewer_limit_reached(room: str, owner: str, viewer: str, policy: dict | None = None) -> bool:
        policy = policy or _webcam_policy()
        max_viewers = int(policy.get("webcam_max_viewers") or 0)
        if max_viewers <= 0:
            return False
        snap = _webcam_snapshot(room, owner)
        approved = set(snap.get("approved") or [])
        viewing = set(snap.get("viewing") or [])
        existing = approved | viewing
        if viewer in existing:
            return False
        return len(existing) >= max_viewers

    def _webcam_clear_owner(room: str, owner: str) -> tuple[list[str], list[str]]:
        with WEBCAM_PERMISSIONS_LOCK:
            room_map = WEBCAM_PERMISSIONS.get(str(room or "").strip()) or {}
            st = room_map.pop(str(owner or "").strip(), None) or {}
            if not room_map:
                WEBCAM_PERMISSIONS.pop(str(room or "").strip(), None)
        viewers = sorted(set(st.get("approved") or set()) | set(st.get("viewing") or set()))
        pending = sorted(set(st.get("pending") or set()))
        return viewers, pending

    def _webcam_owner_has_state(room: str, owner: str) -> bool:
        with WEBCAM_PERMISSIONS_LOCK:
            return str(owner or "").strip() in (WEBCAM_PERMISSIONS.get(str(room or "").strip()) or {})

    def _webcam_remove_viewer_from_room(room: str, viewer: str) -> list[str]:
        """Remove a leaving viewer from every camera permission set in a room."""
        room = str(room or "").strip()
        viewer = str(viewer or "").strip()
        if not room or not viewer:
            return []
        changed: list[str] = []
        with WEBCAM_PERMISSIONS_LOCK:
            room_map = WEBCAM_PERMISSIONS.get(room) or {}
            for owner, st in list(room_map.items()):
                touched = False
                for key in ("pending", "approved", "viewing"):
                    bucket = st.setdefault(key, set())
                    if viewer in bucket:
                        bucket.discard(viewer)
                        touched = True
                if touched:
                    changed.append(str(owner))
            # Keep active owner camera states, but drop empty inactive entries.
            for owner, st in list(room_map.items()):
                if not st.get("camera_on") and not (st.get("pending") or st.get("approved") or st.get("viewing")):
                    room_map.pop(owner, None)
            if not room_map:
                WEBCAM_PERMISSIONS.pop(room, None)
        return sorted(set(changed))

    def _webcam_room_member(room: str, username: str) -> bool:
        """Fail-closed live-room target check for Echo webcam controls."""
        room = str(room or "").strip()
        username = str(username or "").strip()
        if not room or not username:
            return False
        try:
            live = {str(u or "").strip().lower() for u in room_users(room)}
            return username.lower() in live
        except Exception:
            # Webcam permissions are privacy-sensitive.  If shared presence state
            # is unavailable, do not relay owner/viewer approval/kick/viewing
            # events to a guessed target.
            return False

    def _webcam_require_target_in_room(room: str, username: str, label: str) -> tuple[bool, dict | None]:
        """Return a consistent error when a webcam target is not live in room."""
        if _webcam_room_member(room, username):
            return True, None
        return False, {"success": False, "error": f"{label} not in room", "error_code": "webcam_target_not_in_room"}

    # Echo webcam webcam view request controls. These events are deliberately
    # server-relayed through the existing EchoChat room membership layer so a
    # camera owner can approve/deny/kick viewers in the GUI.
    @socketio.on("webcam_status")
    @jwt_required()
    def handle_webcam_status(data):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "webcam_status", data, default_max_bytes=8192, default_limit=120, default_window=60)
        if guard:
            return guard
        sid = request.sid
        room = (data or {}).get("room")
        camera_on = _event_bool((data or {}).get("camera_on"), False)
        if not room or get_connected_room(sid) != room:
            return {"success": False, "error": "Not in that room"}
        policy = _webcam_policy()
        if policy.get("webcam_approval_mode") == "disabled" and camera_on:
            return {"success": False, "error": "Webcam is disabled by admin policy", "policy": policy}
        if camera_on:
            st = _webcam_owner_state(room, username)
            with WEBCAM_PERMISSIONS_LOCK:
                st["camera_on"] = True
        else:
            viewers, pending = _webcam_clear_owner(room, username)
            for viewer in set(viewers) | set(pending):
                try:
                    _emit_to_user(viewer, "webcam_view_kick", {"room": room, "owner": username, "reason": "camera_off", "server_enforced": True})
                except Exception:
                    pass
        st = media_status_update(room, username, webcam_on=camera_on)
        view_state = _webcam_public_snapshot(room, username)
        emit("webcam_status", {"room": room, "owner": username, "camera_on": camera_on, "policy": policy, **view_state}, room=room, include_self=False)
        emit("voice_media_status", {"room": room, "username": username, "voice_on": st.get("voice_on", False), "webcam_on": st.get("webcam_on", False), **view_state}, room=room)
        return {"success": True, "policy": policy, "server_enforced": bool(policy.get("server_enforced_webcam_permissions")), **view_state}

    @socketio.on("voice_media_status")
    @jwt_required()
    def handle_voice_media_status(data):
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "voice_media_status", data, default_max_bytes=8192, default_limit=120, default_window=60)
        if guard:
            return guard
        sid = request.sid
        room = (data or {}).get("room") or get_connected_room(sid)
        ok_room, room_err, group_voice_id = _can_use_voice_room(username, sid, room)
        if not ok_room:
            return {"success": False, "error": room_err or "Not in that room"}
        if group_voice_id is not None:
            join_room(room)
        patch = {}
        if "voice_on" in (data or {}):
            patch["voice_on"] = _event_bool((data or {}).get("voice_on"), False)
        if "webcam_on" in (data or {}):
            policy = _webcam_policy()
            cam_on = _event_bool((data or {}).get("webcam_on"), False)
            if policy.get("webcam_approval_mode") == "disabled" and cam_on:
                return {"success": False, "error": "Webcam is disabled by admin policy", "policy": policy}
            if cam_on:
                cam_state = _webcam_owner_state(room, username)
                with WEBCAM_PERMISSIONS_LOCK:
                    cam_state["camera_on"] = True
            else:
                viewers, pending = _webcam_clear_owner(room, username)
                for viewer in set(viewers) | set(pending):
                    try:
                        _emit_to_user(viewer, "webcam_view_kick", {"room": room, "owner": username, "reason": "camera_off", "server_enforced": True})
                    except Exception:
                        pass
            patch["webcam_on"] = cam_on
        if not patch:
            return {"success": False, "error": "No media status fields supplied"}
        st = media_status_update(room, username, **patch)
        view_state = _webcam_public_snapshot(room, username) if "webcam_on" in patch else {}
        emit("voice_media_status", {"room": room, "username": username, "voice_on": st.get("voice_on", False), "webcam_on": st.get("webcam_on", False), **view_state}, room=room)
        return {"success": True, "status": st, **view_state}

    @socketio.on("webcam_view_request")
    @jwt_required()
    def handle_webcam_view_request(data):
        viewer = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(viewer, "webcam_view_request", data, default_max_bytes=65536, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        sid = request.sid
        room = (data or {}).get("room")
        owner = str((data or {}).get("owner") or "").strip()
        if not room or get_connected_room(sid) != room:
            return {"success": False, "error": "Not in that room"}
        if not owner or owner == viewer:
            return {"success": False, "error": "Invalid owner"}
        ok_target, target_error = _webcam_require_target_in_room(room, owner, "Owner")
        if not ok_target:
            return target_error
        policy = _webcam_policy()
        mode = str(policy.get("webcam_approval_mode") or "owner_approval")
        if mode == "disabled":
            return {"success": False, "error": "Webcam viewing is disabled by admin policy", "policy": policy}
        st = _webcam_owner_state(room, owner)
        with WEBCAM_PERMISSIONS_LOCK:
            if not bool(st.get("camera_on")):
                return {"success": False, "error": "Owner camera is off", "policy": policy}
            if viewer in set(st.get("approved") or set()):
                allowed_existing = True
            else:
                allowed_existing = False
        if _webcam_viewer_limit_reached(room, owner, viewer, policy):
            return {"success": False, "error": "Webcam viewer limit reached", "policy": policy}
        if mode == "open" or allowed_existing:
            with WEBCAM_PERMISSIONS_LOCK:
                st = _webcam_owner_state(room, owner)
                st.setdefault("approved", set()).add(viewer)
                st.setdefault("pending", set()).discard(viewer)
            _emit_to_user(owner, "webcam_viewing", {"room": room, "viewer": viewer, "viewing": True, "server_enforced": False})
            delivered = _emit_to_user(viewer, "webcam_view_response", {"room": room, "owner": owner, "allowed": True, "auto_allowed": True, "policy": policy})
            return {"success": True, "delivered": bool(delivered), "allowed": True, "auto_allowed": True, "policy": policy}

        with WEBCAM_PERMISSIONS_LOCK:
            st = _webcam_owner_state(room, owner)
            st.setdefault("pending", set()).add(viewer)
        delivered = _emit_to_user(owner, "webcam_view_request", {"room": room, "viewer": viewer, "policy": policy, "server_enforced": True})
        return {"success": bool(delivered), "delivered": bool(delivered), "policy": policy, "server_enforced": True}

    @socketio.on("webcam_view_response")
    @jwt_required()
    def handle_webcam_view_response(data):
        owner = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(owner, "webcam_view_response", data, default_max_bytes=65536, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        sid = request.sid
        room = (data or {}).get("room")
        viewer = str((data or {}).get("viewer") or "").strip()
        allowed = _event_bool((data or {}).get("allowed"), False)
        if not room or get_connected_room(sid) != room:
            return {"success": False, "error": "Not in that room"}
        if not viewer or viewer == owner:
            return {"success": False, "error": "Invalid viewer"}
        ok_target, target_error = _webcam_require_target_in_room(room, viewer, "Viewer")
        if not ok_target:
            return target_error
        policy = _webcam_policy()
        if policy.get("webcam_approval_mode") == "disabled":
            allowed = False
        if allowed:
            snap = _webcam_snapshot(room, owner)
            if not bool(snap.get("camera_on")):
                return {"success": False, "error": "Owner camera is off", "policy": policy}
        if allowed and _webcam_viewer_limit_reached(room, owner, viewer, policy):
            return {"success": False, "error": "Webcam viewer limit reached", "policy": policy}
        with WEBCAM_PERMISSIONS_LOCK:
            st = _webcam_owner_state(room, owner)
            st.setdefault("pending", set()).discard(viewer)
            if allowed:
                st.setdefault("approved", set()).add(viewer)
            else:
                st.setdefault("approved", set()).discard(viewer)
                st.setdefault("viewing", set()).discard(viewer)
        view_state = _webcam_public_snapshot(room, owner)
        delivered = _emit_to_user(viewer, "webcam_view_response", {"room": room, "owner": owner, "allowed": allowed, "policy": policy, "server_enforced": True, **view_state})
        return {"success": bool(delivered), "delivered": bool(delivered), "allowed": bool(allowed), "policy": policy, "server_enforced": True, **view_state}

    @socketio.on("webcam_view_kick")
    @jwt_required()
    def handle_webcam_view_kick(data):
        owner = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(owner, "webcam_view_kick", data, default_max_bytes=65536, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        sid = request.sid
        room = (data or {}).get("room")
        viewer = str((data or {}).get("viewer") or "").strip()
        if not room or get_connected_room(sid) != room:
            return {"success": False, "error": "Not in that room"}
        if not viewer or viewer == owner:
            return {"success": False, "error": "Invalid viewer"}
        ok_target, target_error = _webcam_require_target_in_room(room, viewer, "Viewer")
        if not ok_target:
            return target_error
        policy = _webcam_policy()
        with WEBCAM_PERMISSIONS_LOCK:
            st = _webcam_owner_state(room, owner)
            st.setdefault("approved", set()).discard(viewer)
            st.setdefault("pending", set()).discard(viewer)
            st.setdefault("viewing", set()).discard(viewer)
        view_state = _webcam_public_snapshot(room, owner)
        delivered = _emit_to_user(viewer, "webcam_view_kick", {"room": room, "owner": owner, "policy": policy, "server_enforced": True, **view_state})
        return {"success": bool(delivered), "delivered": bool(delivered), "policy": policy, "server_enforced": True, **view_state}

    @socketio.on("webcam_viewing")
    @jwt_required()
    def handle_webcam_viewing(data):
        viewer = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(viewer, "webcam_viewing", data, default_max_bytes=65536, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        sid = request.sid
        room = (data or {}).get("room")
        owner = str((data or {}).get("owner") or "").strip()
        viewing = _event_bool((data or {}).get("viewing"), False)
        if not room or get_connected_room(sid) != room:
            return {"success": False, "error": "Not in that room"}
        if not owner or owner == viewer:
            return {"success": False, "error": "Invalid owner"}
        ok_target, target_error = _webcam_require_target_in_room(room, owner, "Owner")
        if not ok_target:
            return target_error
        policy = _webcam_policy()
        with WEBCAM_PERMISSIONS_LOCK:
            st = _webcam_owner_state(room, owner)
            approved = viewer in set(st.get("approved") or set()) or policy.get("webcam_approval_mode") == "open"
            if viewing and not approved:
                return {"success": False, "error": "Viewer is not approved for that camera", "policy": policy, "server_enforced": True}
            if viewing:
                st.setdefault("viewing", set()).add(viewer)
            else:
                st.setdefault("viewing", set()).discard(viewer)
        view_state = _webcam_public_snapshot(room, owner)
        delivered = _emit_to_user(owner, "webcam_viewing", {"room": room, "viewer": viewer, "viewing": viewing, "policy": policy, "server_enforced": True, **view_state})
        return {"success": bool(delivered), "delivered": bool(delivered), "policy": policy, "server_enforced": True, **view_state}

    @socketio.on("voice_room_offer")
    @jwt_required()
    def handle_voice_room_offer(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_room_offer", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        if not _voice_feature_enabled():
            return _voice_disabled_payload()
        topology_guard = _voice_topology_guard_payload()
        if topology_guard is not None:
            return topology_guard
        rl_resp = _voice_signal_guard(sender, "room_offer")
        if rl_resp is not None:
            return rl_resp
        sid = request.sid
        room = (data or {}).get("room")
        to = (data or {}).get("to")
        offer = (data or {}).get("offer")
        ice_restart = _event_bool((data or {}).get("ice_restart"), False)
        if not room or not to or not offer:
            return {"success": False, "error": "Missing fields"}
        # Sender must be authorized for this room/group voice namespace and in voice.
        ok_room, room_err, group_voice_id = _can_use_voice_room(sender, sid, room)
        if not ok_room:
            return {"success": False, "error": room_err or "Not in that room"}
        if group_voice_id is not None:
            join_room(room)
        if sender not in set(_voice_room_users(room)):
            return {"success": False, "error": "Not in voice"}
        if to not in set(_voice_room_users(room)):
            return {"success": False, "error": "Recipient not in voice"}
        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_room_offer", {"room": room, "sender": sender, "offer": offer, "ice_restart": ice_restart}, f"room:{room}:{sender}:{to}")
        if not delivered:
            return _voice_not_delivered_payload(to, diagnostic)
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}


    @socketio.on("voice_room_answer")
    @jwt_required()
    def handle_voice_room_answer(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_room_answer", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        if not _voice_feature_enabled():
            return _voice_disabled_payload()
        topology_guard = _voice_topology_guard_payload()
        if topology_guard is not None:
            return topology_guard
        rl_resp = _voice_signal_guard(sender, "room_answer")
        if rl_resp is not None:
            return rl_resp
        sid = request.sid
        room = (data or {}).get("room")
        to = (data or {}).get("to")
        answer = (data or {}).get("answer")
        if not room or not to or not answer:
            return {"success": False, "error": "Missing fields"}
        ok_room, room_err, group_voice_id = _can_use_voice_room(sender, sid, room)
        if not ok_room:
            return {"success": False, "error": room_err or "Not in that room"}
        if group_voice_id is not None:
            join_room(room)
        if sender not in set(_voice_room_users(room)):
            return {"success": False, "error": "Not in voice"}
        if to not in set(_voice_room_users(room)):
            return {"success": False, "error": "Recipient not in voice"}
        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_room_answer", {"room": room, "sender": sender, "answer": answer}, f"room:{room}:{sender}:{to}")
        if not delivered:
            return _voice_not_delivered_payload(to, diagnostic)
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}


    @socketio.on("voice_room_ice")
    @jwt_required()
    def handle_voice_room_ice(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_room_ice", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        if not _voice_feature_enabled():
            return _voice_disabled_payload()
        topology_guard = _voice_topology_guard_payload()
        if topology_guard is not None:
            return topology_guard
        rl_resp = _voice_signal_guard(sender, "room_ice")
        if rl_resp is not None:
            return rl_resp
        sid = request.sid
        room = (data or {}).get("room")
        to = (data or {}).get("to")
        candidate = (data or {}).get("candidate")
        if not room or not to or not candidate:
            return {"success": False, "error": "Missing fields"}
        ok_room, room_err, group_voice_id = _can_use_voice_room(sender, sid, room)
        if not ok_room:
            return {"success": False, "error": room_err or "Not in that room"}
        if group_voice_id is not None:
            join_room(room)
        if sender not in set(_voice_room_users(room)):
            return {"success": False, "error": "Not in voice"}
        if to not in set(_voice_room_users(room)):
            return {"success": False, "error": "Recipient not in voice"}
        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_room_ice", {"room": room, "sender": sender, "candidate": candidate}, f"room:{room}:{sender}:{to}")
        if not delivered:
            return _voice_not_delivered_payload(to, diagnostic)
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}


    # 1:1 voice calls (DM-like)
    # Server tracks call session state to prevent spoofed signaling.

    @socketio.on("voice_dm_invite")
    @jwt_required()
    def handle_voice_dm_invite(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_dm_invite", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        if not _voice_feature_enabled():
            return _voice_disabled_payload()
        topology_guard = _voice_topology_guard_payload()
        if topology_guard is not None:
            return topology_guard
        sid = request.sid
        raw_to = (data or {}).get("to")
        to = _resolve_canonical_username(raw_to)
        call_id = (data or {}).get("call_id")

        if not to or not call_id:
            return {"success": False, "error": "Missing fields"}

        if not _valid_id(call_id):
            return {"success": False, "error": "Invalid call_id"}

        ok, err = _require_not_sanctioned(sender, action="voice")
        if not ok:
            return {"success": False, "error": err}

        if str(to or "").strip().lower() == str(sender or "").strip().lower():
            return {"success": False, "error": "Cannot call yourself"}

        if _either_blocked(sender, to):
            return {"success": False, "error": "Direct message blocked"}

        # basic cooldown per socket
        now = time.time()
        cooldown = float(settings.get("voice_invite_cooldown_seconds", 2) or 2)
        last = VOICE_INVITE_LAST.get(sid, 0.0)
        if cooldown > 0 and (now - last) < cooldown:
            return {"success": False, "error": "Too many invites"}
        VOICE_INVITE_LAST[sid] = now

        _cleanup_voice_dm_sessions()

        existing = voice_dm_session_get(call_id)
        if existing:
            state = str(existing.get("state") or "")
            if state in {"invited", "active"}:
                return {"success": False, "error": "call_id already in use"}
            # allow overwrite only if stale state got here somehow

        # Prevent duplicate per-pair DM calls. Without this, repeated clicks or
        # opposite-direction call glare can create two sessions for the same
        # users and leave one browser stuck in Calling/Incoming. This check now
        # uses shared Redis state when configured, so another worker can see it.
        for other_id, other in list(voice_dm_session_items()):
            if other_id == call_id:
                continue
            state = str(other.get("state") or "")
            if state not in {"invited", "active"}:
                continue
            if {other.get("caller"), other.get("callee")} == {sender, to}:
                return {"success": False, "error": "Call already pending or active"}

        voice_dm_session_set(call_id, {
            "caller": sender,
            "callee": to,
            "state": "invited",
            "created": now,
            "updated": now,
        }, ttl_seconds=_voice_dm_ttl("invited"))

        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_dm_invite", {"sender": sender, "call_id": call_id}, call_id)
        if not delivered:
            sess = voice_dm_session_get(call_id)
            if sess and sess.get("caller") == sender and sess.get("callee") == to:
                voice_dm_session_delete(call_id)
        log_audit_event(sender, "voice_dm_invite", target=to)
        if not delivered:
            return _voice_not_delivered_payload(to, diagnostic)
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}


    @socketio.on("voice_dm_accept")
    @jwt_required()
    def handle_voice_dm_accept(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_dm_accept", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        if not _voice_feature_enabled():
            return _voice_disabled_payload()
        topology_guard = _voice_topology_guard_payload()
        if topology_guard is not None:
            return topology_guard
        raw_to = (data or {}).get("to")
        to = _resolve_canonical_username(raw_to)
        call_id = (data or {}).get("call_id")

        if not to or not call_id:
            return {"success": False, "error": "Missing fields"}

        if not _valid_id(call_id):
            return {"success": False, "error": "Invalid call_id"}

        ok, err = _require_not_sanctioned(sender, action="voice")
        if not ok:
            return {"success": False, "error": err}

        if _either_blocked(sender, to):
            return {"success": False, "error": "Direct message blocked"}

        _cleanup_voice_dm_sessions()

        sess = voice_dm_session_get(call_id)
        if not sess:
            return {"success": False, "error": "Unknown/expired call"}
        if sess.get("callee") != sender or sess.get("caller") != to:
            return {"success": False, "error": "Not a participant"}
        if str(sess.get("state") or "") != "invited":
            return {"success": False, "error": "Call not in invited state"}
        sess["state"] = "active"
        sess["updated"] = time.time()
        voice_dm_session_set(call_id, sess, ttl_seconds=_voice_dm_ttl("active"))

        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_dm_accept", {"sender": sender, "call_id": call_id}, call_id)
        if not delivered:
            sess = voice_dm_session_get(call_id)
            if sess and {sess.get("caller"), sess.get("callee")} == {sender, to}:
                voice_dm_session_delete(call_id)
        log_audit_event(sender, "voice_dm_accept", target=to)
        if not delivered:
            return _voice_not_delivered_payload(to, diagnostic)
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}


    @socketio.on("voice_dm_decline")
    @jwt_required()
    def handle_voice_dm_decline(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_dm_decline", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        raw_to = (data or {}).get("to")
        to = _resolve_canonical_username(raw_to)
        call_id = (data or {}).get("call_id")
        reason = (data or {}).get("reason") or "Declined"

        if not to or not call_id:
            return {"success": False, "error": "Missing fields"}

        if not _valid_id(call_id):
            return {"success": False, "error": "Invalid call_id"}

        ok, err = _require_not_sanctioned(sender, action="voice")
        if not ok:
            return {"success": False, "error": err}

        # Decline must remain allowed even if a block happens after the invite;
        # otherwise the caller can be left ringing until timeout.

        _cleanup_voice_dm_sessions()

        sess = voice_dm_session_get(call_id)
        if not sess:
            return {"success": False, "error": "Unknown/expired call"}
        if sess.get("callee") != sender or sess.get("caller") != to:
            return {"success": False, "error": "Not a participant"}
        voice_dm_session_delete(call_id)

        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_dm_decline", {"sender": sender, "call_id": call_id, "reason": reason}, call_id)
        log_audit_event(sender, "voice_dm_decline", target=to)
        if not delivered:
            payload = _voice_not_delivered_payload(to, diagnostic)
            payload["success"] = True
            return payload
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}


    @socketio.on("voice_dm_end")
    @jwt_required()
    def handle_voice_dm_end(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_dm_end", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        raw_to = (data or {}).get("to")
        to = _resolve_canonical_username(raw_to)
        call_id = (data or {}).get("call_id")
        reason = (data or {}).get("reason") or "Ended"

        if not to or not call_id:
            return {"success": False, "error": "Missing fields"}

        if not _valid_id(call_id):
            return {"success": False, "error": "Invalid call_id"}

        ok, err = _require_not_sanctioned(sender, action="voice")
        if not ok:
            return {"success": False, "error": err}

        # Ending an existing call is always allowed for participants, even if a
        # block was added mid-call. This keeps both browsers from getting stuck.

        _cleanup_voice_dm_sessions()

        sess = voice_dm_session_get(call_id)
        if not sess:
            # allow idempotent end
            sess_ok = True
        else:
            if {sess.get("caller"), sess.get("callee")} != {sender, to}:
                return {"success": False, "error": "Not a participant"}
            voice_dm_session_delete(call_id)
            sess_ok = True

        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_dm_end", {"sender": sender, "call_id": call_id, "reason": reason}, call_id)
        log_audit_event(sender, "voice_dm_end", target=to)
        if not delivered:
            payload = _voice_not_delivered_payload(to, diagnostic)
            payload["session"] = sess_ok
            return payload
        return {"success": True, "delivered": True, "session": sess_ok, "voice_realtime": diagnostic}


    @socketio.on("voice_dm_offer")
    @jwt_required()
    def handle_voice_dm_offer(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_dm_offer", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        if not _voice_feature_enabled():
            return _voice_disabled_payload()
        topology_guard = _voice_topology_guard_payload()
        if topology_guard is not None:
            return topology_guard
        rl_resp = _voice_signal_guard(sender, "dm_offer")
        if rl_resp is not None:
            return rl_resp
        raw_to = (data or {}).get("to")
        to = _resolve_canonical_username(raw_to)
        call_id = (data or {}).get("call_id")
        offer = (data or {}).get("offer")
        ice_restart = _event_bool((data or {}).get("ice_restart"), False)

        if not to or not call_id or not offer:
            return {"success": False, "error": "Missing fields"}

        if not _valid_id(call_id):
            return {"success": False, "error": "Invalid call_id"}

        ok, err = _require_not_sanctioned(sender, action="voice")
        if not ok:
            return {"success": False, "error": err}

        if _either_blocked(sender, to):
            return {"success": False, "error": "Direct message blocked"}

        _, err_resp = _voice_dm_require_active(sender, to, call_id)
        if err_resp:
            return err_resp

        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_dm_offer", {"sender": sender, "call_id": call_id, "offer": offer, "ice_restart": ice_restart}, call_id)
        if not delivered:
            return _voice_not_delivered_payload(to, diagnostic)
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}


    @socketio.on("voice_dm_answer")
    @jwt_required()
    def handle_voice_dm_answer(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_dm_answer", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        if not _voice_feature_enabled():
            return _voice_disabled_payload()
        topology_guard = _voice_topology_guard_payload()
        if topology_guard is not None:
            return topology_guard
        rl_resp = _voice_signal_guard(sender, "dm_answer")
        if rl_resp is not None:
            return rl_resp
        raw_to = (data or {}).get("to")
        to = _resolve_canonical_username(raw_to)
        call_id = (data or {}).get("call_id")
        answer = (data or {}).get("answer")

        if not to or not call_id or not answer:
            return {"success": False, "error": "Missing fields"}

        if not _valid_id(call_id):
            return {"success": False, "error": "Invalid call_id"}

        ok, err = _require_not_sanctioned(sender, action="voice")
        if not ok:
            return {"success": False, "error": err}

        if _either_blocked(sender, to):
            return {"success": False, "error": "Direct message blocked"}

        _, err_resp = _voice_dm_require_active(sender, to, call_id)
        if err_resp:
            return err_resp

        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_dm_answer", {"sender": sender, "call_id": call_id, "answer": answer}, call_id)
        if not delivered:
            return _voice_not_delivered_payload(to, diagnostic)
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}


    @socketio.on("voice_dm_ice")
    @jwt_required()
    def handle_voice_dm_ice(data):
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "voice_dm_ice", data, default_max_bytes=131072, default_limit=180, default_window=60)
        if guard is not None:
            return guard
        if not _voice_feature_enabled():
            return _voice_disabled_payload()
        topology_guard = _voice_topology_guard_payload()
        if topology_guard is not None:
            return topology_guard
        rl_resp = _voice_signal_guard(sender, "dm_ice")
        if rl_resp is not None:
            return rl_resp
        raw_to = (data or {}).get("to")
        to = _resolve_canonical_username(raw_to)
        call_id = (data or {}).get("call_id")
        candidate = (data or {}).get("candidate")

        if not to or not call_id or not candidate:
            return {"success": False, "error": "Missing fields"}

        if not _valid_id(call_id):
            return {"success": False, "error": "Invalid call_id"}

        ok, err = _require_not_sanctioned(sender, action="voice")
        if not ok:
            return {"success": False, "error": err}

        if _either_blocked(sender, to):
            return {"success": False, "error": "Direct message blocked"}

        _, err_resp = _voice_dm_require_active(sender, to, call_id)
        if err_resp:
            return err_resp

        delivered, diagnostic = _voice_emit_to_user(sender, to, "voice_dm_ice", {"sender": sender, "call_id": call_id, "candidate": candidate}, call_id)
        if not delivered:
            return _voice_not_delivered_payload(to, diagnostic)
        return {"success": True, "delivered": True, "voice_realtime": diagnostic}




