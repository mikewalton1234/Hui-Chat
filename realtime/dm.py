

def _antiabuse_duplicate_checks(*args, **kwargs):
    """Placeholder hook for duplicate fingerprint checks."""
    return True
"""Socket.IO handlers: dm.

Auto-split from the legacy monolithic socket_handlers.py.
"""

import base64
import binascii
import json
import logging
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
    close_db,
    get_custom_room_meta,
    can_user_access_custom_room,
    touch_custom_room_activity,
    consume_room_invites,
    set_room_message_expiry,
    get_room_message_expiry,
)
from security import log_audit_event
from permissions import check_user_permission
from moderation import is_user_sanctioned, mute_user

from realtime.state import *

def register(socketio, settings, ctx):
    """Register Socket.IO event handlers for this module."""
    # Make helper functions from socket_handlers available as module globals
    globals().update(ctx.__dict__)
    _antiabuse_exempt_staff = bool(settings.get("antiabuse_exempt_staff", True))
    def _mark_offline_delivered(cur, username: str, msg_ids: list[int]) -> int:
        """Mark offline_messages.delivered=TRUE for this receiver and IDs.

        Uses an IN (...) list instead of ANY(%s::int[]) to avoid adapter/array-cast edge cases
        where delivered flags can fail to update and missed messages reappear after refresh.
        """
        try:
            ids = [int(x) for x in (msg_ids or []) if int(x) > 0]
        except Exception:
            ids = []
        if not ids:
            return 0
        updated_total = 0
        CHUNK = 500
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i+CHUNK]
            placeholders = ",".join(["%s"] * len(chunk))
            sql = f"UPDATE offline_messages SET delivered = TRUE WHERE LOWER(receiver)=LOWER(%s) AND delivered = FALSE AND id IN ({placeholders});"
            cur.execute(sql, tuple([username] + chunk))
            updated_total += int(cur.rowcount or 0)
        return updated_total

    def _dbg(*a):
        """Debug prints for offline PM delivery/ack. Enable with settings['debug_offline_pms']=True."""
        try:
            if bool(settings.get("debug_offline_pms", False)):
                print(*a)
        except Exception:
            pass

    def _safe_dm_positive_int(value, default: int, *, minimum: int = 1, maximum: int = 500000) -> int:
        """Parse DM size-limit settings without letting bad config break PM sending."""
        try:
            parsed = int(value)
        except Exception:
            parsed = int(default)
        if parsed < int(minimum):
            return int(default)
        if parsed > int(maximum):
            return int(maximum)
        return int(parsed)

    def _dm_b64_field(value, *, min_bytes: int, max_bytes: int) -> bytes | None:
        """Decode one strict standard-base64 envelope field within size bounds."""
        if not isinstance(value, str) or not value:
            return None
        if len(value) > max(16, int(max_bytes) * 2):
            return None
        try:
            raw = base64.b64decode(value.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError):
            return None
        if len(raw) < int(min_bytes) or len(raw) > int(max_bytes):
            return None
        return raw

    def _looks_like_dm_cipher_envelope(value) -> bool:
        """Accept only the EC1 hybrid envelope shape produced by the Echo-Chat browser client.

        This is a server-side relay/storage boundary, not decryption. The server
        validates the non-secret envelope structure so plaintext cannot be shoved
        into the ciphertext field and then relayed/stored as if it were E2EE.
        """
        if not isinstance(value, str) or not value.startswith("EC1:"):
            return False
        encoded = value[len("EC1:"):].strip()
        if not encoded or len(encoded) > 180000:
            return False
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
            if len(raw) > 120000:
                return False
            env = json.loads(raw.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError, TypeError):
            return False
        if not isinstance(env, dict):
            return False
        if env.get("v") != 1 or env.get("alg") != "RSA-OAEP+AES-GCM":
            return False
        # ek is an RSA-OAEP-wrapped AES key. Echo-Chat normally uses 2048-bit
        # account RSA keys (256 bytes), but accept a bounded range for future
        # 3072/4096-bit upgrades without code churn.
        if _dm_b64_field(env.get("ek"), min_bytes=128, max_bytes=512) is None:
            return False
        iv = _dm_b64_field(env.get("iv"), min_bytes=12, max_bytes=12)
        if iv is None:
            return False
        # AES-GCM ciphertext includes the auth tag; even an empty plaintext would
        # produce a 16-byte tag. The browser never sends empty PMs, but this keeps
        # the envelope rule protocol-correct.
        if _dm_b64_field(env.get("ct"), min_bytes=16, max_bytes=120000) is None:
            return False
        return True

    def _offline_pm_wire_item(mid, sender, stored_message, ts, *, require_e2ee: bool, allow_plain: bool) -> tuple[dict | None, bool]:
        """Return a safe wire item and whether the DB row should be quarantined.

        Old builds could leave plaintext rows in offline_messages.message.  In
        strict DM E2EE mode, do not deliver those rows as if they were ciphertext;
        mark them consumed/quarantined so they do not keep creating missed-PM
        alerts.
        """
        try:
            msg_id = int(mid)
        except Exception:
            return None, True
        text = str(stored_message or "").strip()
        if _looks_like_dm_cipher_envelope(text):
            return {"id": msg_id, "sender": sender, "cipher": text, "ts": float(ts) if ts is not None else None, "encrypted": True}, False
        if require_e2ee or not allow_plain:
            return None, True
        # Explicit legacy compatibility only.  Do not label plaintext as cipher.
        return {"id": msg_id, "sender": sender, "message": text, "ts": float(ts) if ts is not None else None, "encrypted": False, "legacy_plaintext": True}, False

    @socketio.on("get_missed_pm_summary")
    @jwt_required()
    def handle_get_missed_pm_summary(data=None):
        data = data if isinstance(data, dict) else {}
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "get_missed_pm_summary", data, default_max_bytes=2048, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        _emit_missed_pm_summary(username, request.sid)
        return {"success": True}

    @socketio.on("fetch_offline_pms")
    @jwt_required()
    def handle_fetch_offline_pms(data):
        data = data if isinstance(data, dict) else {}
        """Fetch offline PMs.

        By default this marks messages as delivered (consumes the queue).
        If the client passes {peek: true}, the server returns messages
        without marking them delivered. The client can later call
        ack_offline_pms with the IDs it successfully processed.
        """
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "fetch_offline_pms", data, default_max_bytes=4096, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        requested_from_user = str(data.get("from_user") or "").strip()
        from_user = _resolve_canonical_username(requested_from_user) if requested_from_user else None
        if requested_from_user and not from_user:
            return {"success": False, "error": "User not found"}
        if from_user and _either_blocked(username, from_user):
            # Blocked-pair fetches are denied and the user's missed-PM summary is
            # refreshed immediately so stale blocked rows cannot remain visible
            # in the dock/alerts UI. The block action itself marks existing
            # undelivered rows delivered, but this covers races and older rows.
            try:
                _emit_missed_pm_summary_to_user(username)
            except Exception:
                pass
            return {"success": False, "error": "Direct message blocked", "messages": [], "blocked": True}
        peek = bool(data.get("peek", False))
        conn = get_db()
        try:
            with conn.cursor() as cur:
                if from_user:
                    cur.execute(
                        """
                        SELECT id, sender, message, EXTRACT(EPOCH FROM timestamp)::float AS ts
                          FROM offline_messages
                         WHERE LOWER(receiver) = LOWER(%s)
                           AND delivered = FALSE
                           AND LOWER(sender) = LOWER(%s)
                           AND NOT EXISTS (
                               SELECT 1 FROM blocks b
                                WHERE (LOWER(b.blocker) = LOWER(offline_messages.receiver) AND LOWER(b.blocked) = LOWER(offline_messages.sender))
                                   OR (LOWER(b.blocker) = LOWER(offline_messages.sender) AND LOWER(b.blocked) = LOWER(offline_messages.receiver))
                           )
                         ORDER BY timestamp ASC;
                        """,
                        (username, from_user),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, sender, message, EXTRACT(EPOCH FROM timestamp)::float AS ts
                          FROM offline_messages
                         WHERE LOWER(receiver) = LOWER(%s)
                           AND delivered = FALSE
                           AND NOT EXISTS (
                               SELECT 1 FROM blocks b
                                WHERE (LOWER(b.blocker) = LOWER(offline_messages.receiver) AND LOWER(b.blocked) = LOWER(offline_messages.sender))
                                   OR (LOWER(b.blocker) = LOWER(offline_messages.sender) AND LOWER(b.blocked) = LOWER(offline_messages.receiver))
                           )
                         ORDER BY timestamp ASC;
                        """,
                        (username,),
                    )
                rows = cur.fetchall() or []

                require_e2ee = bool(settings.get("require_dm_e2ee", True))
                allow_plain = bool(settings.get("allow_plaintext_dm_fallback", False))
                msg_ids = [int(r[0]) for r in rows]
                messages = []
                quarantine_ids = []
                for mid, sender, stored_message, ts in rows:
                    item, quarantine = _offline_pm_wire_item(
                        mid,
                        sender,
                        stored_message,
                        ts,
                        require_e2ee=require_e2ee,
                        allow_plain=allow_plain,
                    )
                    if item is not None:
                        messages.append(item)
                    if quarantine:
                        try:
                            quarantine_ids.append(int(mid))
                        except Exception:
                            pass

                if quarantine_ids:
                    # Consume old non-envelope rows in strict E2EE mode so they
                    # cannot be repeatedly surfaced as missed messages.
                    _mark_offline_delivered(cur, username, quarantine_ids)
                    _dbg(f"[offline_pms] quarantined receiver={username} from={from_user or '*'} ids={len(quarantine_ids)}")

                if msg_ids and not peek:
                    # Mark safe returned rows consumed for this receiver (robust IN-list update).
                    safe_ids = [int(m.get("id")) for m in messages if m.get("id") is not None]
                    upd = _mark_offline_delivered(cur, username, safe_ids) if safe_ids else 0
                    _dbg(f"[offline_pms] consume receiver={username} from={from_user or '*'} ids={len(safe_ids)} updated={upd}")
            conn.commit()

            if not peek:
                try:
                    _emit_missed_pm_summary_to_user(username)
                except Exception:
                    try:
                        _emit_missed_pm_summary(username, request.sid)
                    except Exception:
                        pass

            logging.info(
                "fetch_offline_pms receiver=%s from=%s peek=%s returned=%s",
                username,
                (from_user or "*"),
                peek,
                len(messages),
            )

            return {"success": True, "messages": messages, "peek": peek, "quarantined_legacy": len(quarantine_ids)}
        except Exception as e:
            print(f"[DB ERROR] fetch_offline_pms: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "error": "Failed to fetch offline messages"}
        finally:
            # Ensure pooled DB connections are returned after Socket.IO events.
            try:
                close_db()
            except Exception:
                pass



    @socketio.on("ack_offline_pms")
    @jwt_required()
    def handle_ack_offline_pms(data):
        data = data if isinstance(data, dict) else {}
        """Mark specific offline PM IDs as delivered for the current user.

        Used together with fetch_offline_pms(peek=true) so clients only consume
        messages they successfully decrypted/rendered.
        """
        username = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(username, "ack_offline_pms", data, default_max_bytes=32768, default_limit=120, default_window=60)
        if guard is not None:
            return guard
        ids = data.get("ids") or []
        if not isinstance(ids, (list, tuple)):
            return {"success": False, "error": "bad_ids"}

        msg_ids = []
        for x in ids:
            try:
                msg_ids.append(int(x))
            except Exception:
                continue
        msg_ids = list(dict.fromkeys([i for i in msg_ids if i > 0]))

        requested = len(msg_ids)
        msg_ids = msg_ids[:1000]

        if not msg_ids:
            return {"success": True, "updated": 0, "requested": 0}

        conn = get_db()
        updated = 0
        try:
            with conn.cursor() as cur:
                updated = int(_mark_offline_delivered(cur, username, msg_ids) or 0)
                _dbg(f"[offline_pms] ack receiver={username} ids={len(msg_ids)} updated={updated}")
            conn.commit()
        except Exception as e:
            print(f"[DB ERROR] ack_offline_pms: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "error": "db"}
        finally:
            # Ensure pooled DB connections are returned after Socket.IO events.
            try:
                close_db()
            except Exception:
                pass

        try:
            _emit_missed_pm_summary_to_user(username)
        except Exception:
            try:
                _emit_missed_pm_summary(username, request.sid)
            except Exception:
                pass

        return {"success": True, "updated": updated, "requested": requested}


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

    def _dm_typing_payload(sender: str, to: str, *, typing: bool) -> dict:
        return {
            "from": str(sender or "").strip(),
            "to": str(to or "").strip(),
            "username": str(sender or "").strip(),
            "typing": bool(typing),
            "expires_in": int(TYPING_EXPIRY_SECONDS),
            "ts": time.time(),
        }

    def _direct_typing_rate_ok(sender: str) -> tuple[bool, int | None, bool]:
        return _socket_action_rate_ok(
            sender,
            "dm_typing",
            "dm_typing_rate_limit",
            "dm_typing_rate_window_sec",
            default_limit=30,
            default_window=10,
            strike_reason="dm_typing_rate",
        )

    def _emit_direct_stop_typing(sender: str, to: str) -> bool:
        """Best-effort PM typing cleanup.

        This is intentionally payload-light and contains no message content.  It is
        used both for explicit direct_stop_typing events and after a real PM is
        accepted so the recipient UI never leaves a stale "is typing" line when
        a browser misses the stop packet.
        """
        clean_sender = str(sender or "").strip()
        clean_to = str(to or "").strip()
        if not clean_sender or not clean_to or clean_sender.lower() == clean_to.lower():
            return False
        if not _feature_bool("enable_dm_typing_indicators", True):
            return False
        try:
            return bool(_emit_to_user(clean_to, "direct_stop_typing", _dm_typing_payload(clean_sender, clean_to, typing=False)))
        except Exception:
            return False

    @socketio.on("direct_typing")
    @jwt_required()
    def handle_direct_typing(data):
        data = data if isinstance(data, dict) else {}
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "direct_typing", data, default_max_bytes=4096, default_limit=60, default_window=10)
        if guard is not None:
            return guard
        to = _resolve_canonical_username(data.get("to"))
        if not _feature_bool("enable_dm_typing_indicators", True):
            return {"success": True, "typing": False, "disabled": True}
        if not to:
            return {"success": False, "error": "User not found"}
        if to == sender:
            return {"success": False, "error": "self_dm_disabled"}
        ok, err = _require_not_sanctioned(sender, action="dm")
        if not ok:
            return {"success": False, "error": err or "dm_denied"}
        if _either_blocked(sender, to):
            return {"success": False, "error": "Direct message blocked"}
        okrl, retry, auto_muted = _direct_typing_rate_ok(sender)
        if not okrl:
            return {"success": False, "error": "rate_limited", "retry_after": retry, "auto_muted": auto_muted}
        shadowbanned_sender = False
        try:
            shadowbanned_sender = bool(_is_effectively_shadowbanned(sender))
        except Exception:
            shadowbanned_sender = False
        delivered = False
        if not shadowbanned_sender:
            delivered = bool(_emit_to_user(to, "direct_typing", _dm_typing_payload(sender, to, typing=True)))
        return {"success": True, "to": to, "typing": True, "expires_in": int(TYPING_EXPIRY_SECONDS), "delivered": delivered}

    @socketio.on("direct_stop_typing")
    @jwt_required()
    def handle_direct_stop_typing(data):
        data = data if isinstance(data, dict) else {}
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "direct_stop_typing", data, default_max_bytes=4096, default_limit=90, default_window=10)
        if guard is not None:
            return guard
        to = _resolve_canonical_username(data.get("to"))
        if not _feature_bool("enable_dm_typing_indicators", True):
            return {"success": True, "typing": False, "disabled": True}
        if not to:
            return {"success": False, "error": "User not found"}
        if to == sender:
            return {"success": False, "error": "self_dm_disabled"}
        if _either_blocked(sender, to):
            return {"success": False, "error": "Direct message blocked"}
        # Stop-typing should clear stale receiver UI even when the sender has
        # been typing too fast. The generic socket_event_guard above still limits
        # abusive stop loops; do not apply the stricter start-typing strike here.
        shadowbanned_sender = False
        try:
            shadowbanned_sender = bool(_is_effectively_shadowbanned(sender))
        except Exception:
            shadowbanned_sender = False
        delivered = False
        if not shadowbanned_sender:
            delivered = bool(_emit_direct_stop_typing(sender, to))
        return {"success": True, "to": to, "typing": False, "delivered": delivered}

    @socketio.on("send_direct_message")
    @jwt_required()
    def handle_send_direct_message(data):
        """Send a private message (DM).

        Security model:
          - Normal path: client sends ciphertext-only in data['cipher'] (EC1:... envelope).
          - Compatibility: older clients may send ciphertext in data['message'].
          - Optional plaintext wrapper (ECP1:...) is disabled by default and allowed only for explicit legacy compatibility.

        The server NEVER decrypts DM payloads.
        """

        data = data if isinstance(data, dict) else {}
        to = data.get("to")
        sender = get_jwt_identity()
        rejection = _reject_if_stale_socket_session(touch_activity=True)
        if rejection is not None:
            return rejection
        guard = _socket_event_guard(sender, "send_direct_message", data, default_max_bytes=160000, default_limit=120, default_window=60)
        if guard is not None:
            return guard

        to = _resolve_canonical_username(to)

        cipher = data.get("cipher")
        if not cipher:
            # legacy/compat: allow older clients to send ciphertext in "message"
            cipher = data.get("message")

        if not to or not cipher:
            try:
                current_app.logger.warning("DM rejected: sender=%s reason=missing_or_unknown_recipient to=%r has_cipher=%s", sender, to, bool(cipher))
            except Exception:
                pass
            if not to:
                return {"success": False, "error": "User not found"}
            return {"success": False, "error": "Missing recipient or message"}

        require_e2ee = bool(settings.get("require_dm_e2ee", True))
        allow_plain = bool(settings.get("allow_plaintext_dm_fallback", False))
        plain_prefix = "ECP1:"

        if require_e2ee:
            # Require that the client used the explicit ciphertext field
            if not data.get("cipher"):
                return {"success": False, "error": "dm_requires_e2ee"}
            if isinstance(cipher, str) and cipher.startswith(plain_prefix):
                return {"success": False, "error": "dm_requires_e2ee"}

        if not allow_plain:
            if isinstance(cipher, str) and cipher.startswith(plain_prefix):
                return {"success": False, "error": "plaintext_dm_disabled"}

        if not isinstance(cipher, str) or not cipher.strip():
            return {"success": False, "error": "bad_cipher"}
        cipher = cipher.strip()

        max_len = _safe_dm_positive_int(settings.get("max_dm_cipher_length"), 140000, minimum=256, maximum=500000)
        if len(cipher) > max_len:
            return {"success": False, "error": f"Ciphertext too large (max {max_len})"}

        if require_e2ee and not _looks_like_dm_cipher_envelope(cipher):
            return {"success": False, "error": "bad_cipher_envelope"}
        if cipher.startswith("EC1:") and not _looks_like_dm_cipher_envelope(cipher):
            return {"success": False, "error": "bad_cipher_envelope"}

        ok, err = _require_not_sanctioned(sender, action="dm")
        if not ok:
            return {"success": False, "error": err}

        if to == sender:
            try:
                current_app.logger.info("DM rejected: sender=%s to=%s reason=self_dm_disabled", sender, to)
            except Exception:
                pass
            return {"success": False, "error": "self_dm_disabled"}

        if _either_blocked(sender, to):
            try:
                current_app.logger.info("DM rejected: sender=%s to=%s reason=blocked", sender, to)
            except Exception:
                pass
            return {"success": False, "error": "Direct message blocked"}

        try:
            if _is_effectively_shadowbanned(sender):
                try:
                    current_app.logger.info("DM shadow-dropped: sender=%s to=%s", sender, to)
                except Exception:
                    pass
                return {"success": True, "delivered": True, "shadowbanned": True}
        except Exception:
            pass

        # Anti-abuse: DM burst rate limiting + optional per-user quota
        quota = _get_user_quota_per_hour(sender)
        if quota and int(quota) > 0:
            okq, _raq = _rl(f"quota:{sender}", int(quota), 3600)
            if not okq:
                _abuse_strike(sender, "quota")
                return {"success": False, "error": f"Quota exceeded ({int(quota)}/hour). Try later."}

        lim, win = _parse_rate_limit(settings.get("dm_msg_rate_limit"), default_limit=15, default_window=10)
        try:
            win = int(settings.get("dm_msg_rate_window_sec") or win)
        except Exception:
            pass
        okrl, retry = _rl(f"dmmsg:{sender}", lim, win)
        if not okrl:
            if _abuse_strike(sender, "dm_rate"):
                return {"success": False, "error": "Auto-muted for spamming. Try again later."}
            return {"success": False, "error": f"Rate limited (wait {retry:.1f}s)"}
        # Store first, then relay live.  The row is an encrypted unread/PM
        # delivery record, not plaintext.  The receiver ACKs it only after the
        # PM is actually rendered/read.  This prevents "online but missed" PMs
        # from disappearing when a browser tab is backgrounded, sleeping, or not
        # focused on that private conversation.
        unread_id = _store_offline_pm(sender, to, cipher)
        live_payload = {"sender": sender, "cipher": cipher, "ts": time.time()}
        if unread_id:
            live_payload["id"] = int(unread_id)
            live_payload["message_id"] = int(unread_id)
        delivered = _emit_to_user(to, "private_message", live_payload)
        try:
            _emit_direct_stop_typing(sender, to)
        except Exception:
            pass

        # Do not label a PM as "offline queued" just because Socket.IO could not
        # confirm a live emit. In dev/multi-worker setups a recipient can still
        # be visibly online through the DB/session presence layer while this
        # worker has no local SID for them. Only report queued_offline when the
        # recipient is truly offline or intentionally invisible/appear-offline.
        recipient_presence = None
        recipient_effectively_offline = not bool(delivered)
        try:
            recipient_presence = _get_user_presence_row(to)
            recipient_effectively_offline = (
                not bool(recipient_presence.get("online"))
                or str(recipient_presence.get("presence_status") or "").lower() == "invisible"
            )
        except Exception:
            recipient_effectively_offline = not bool(delivered)
        queued_offline = bool(unread_id and recipient_effectively_offline)
        server_unread = bool(unread_id)
        if server_unread:
            try:
                _emit_missed_pm_summary_to_user(to)
            except Exception:
                pass
        try:
            current_app.logger.info(
                "DM accepted: sender=%s to=%s delivered=%s queued_offline=%s server_unread=%s unread_id=%s",
                sender,
                to,
                delivered,
                queued_offline,
                server_unread,
                unread_id,
            )
        except Exception:
            pass
        return {
            "success": True,
            "delivered": bool(delivered),
            "queued_offline": bool(queued_offline),
            "server_unread": bool(server_unread),
            "recipient_offline": bool(recipient_effectively_offline),
            "message_id": int(unread_id) if unread_id else None,
            "recipient": to,
        }

    # ------------------------------------------------------------------
    # WebRTC P2P file transfer signaling (offer/answer/ICE)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # WebRTC P2P file transfer signaling (offer/answer/ICE)
    # ------------------------------------------------------------------
