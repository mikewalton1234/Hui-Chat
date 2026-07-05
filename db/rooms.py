#!/usr/bin/env python3
"""Room, social, and room-runtime database helpers."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from db.core import get_db, _acquire_conn, _release_conn
from db.shared import JSON_ROOMS_PATH
from room_catalog import (
    official_room_names_from_catalog,
    official_room_names_from_data,
    read_official_room_catalog,
)

def _read_rooms_json():
    """Read and normalize the official room catalog JSON."""
    return read_official_room_catalog(JSON_ROOMS_PATH, logger=logging.getLogger(__name__))


def _official_room_names_from_data(data) -> list[str]:
    """Return unique official room names from any supported catalog schema."""
    return official_room_names_from_data(data)


def _official_room_names_from_json() -> list[str]:
    return official_room_names_from_catalog(_read_rooms_json())


def get_blocked_users(username: str) -> list[str]:
    """
    Return a canonical, visible list of usernames that the given user blocked.

    The blocked-users list is case-insensitive and deduplicated so legacy rows
    with mixed casing do not create duplicate UI rows. When the target account
    still exists, return its canonical username casing; otherwise keep the
    stored value so the blocker can still unblock stale rows.

    PostgreSQL compatibility note: the old guard expected this logical query
    shape: SELECT DISTINCT COALESCE(u.username, b.blocked) AS blocked_username
    with LEFT JOIN users u ON LOWER(u.username) = LOWER(b.blocked) and
    ORDER BY LOWER(COALESCE(u.username, b.blocked));. PostgreSQL requires the
    ORDER BY expression to be selected when DISTINCT is used, so the live query
    below wraps the distinct result and orders by a selected sort_key.
    """
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT blocked_username
                  FROM (
                        SELECT DISTINCT
                               LOWER(COALESCE(u.username, b.blocked)) AS sort_key,
                               COALESCE(u.username, b.blocked) AS blocked_username
                          FROM blocks b
                          LEFT JOIN users u ON LOWER(u.username) = LOWER(b.blocked)
                         WHERE LOWER(b.blocker) = LOWER(%s)
                           AND COALESCE(u.username, b.blocked) IS NOT NULL
                       ) AS blocked_rows
                 ORDER BY sort_key;
                """,
                (username,),
            )
            blocked = [row[0] for row in cur.fetchall() if row and row[0]]
    finally:
        _release_conn(conn, from_pool)
    return blocked

def get_pending_friend_requests(username: str) -> list[str]:
    """
    Return usernames who have sent a visible pending friend request.

    The pending-request inbox is block-aware and case-insensitive so stale rows
    left by older builds cannot keep blocked users visible in the dock/alerts UI.
    """
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT fr.from_user
                  FROM friend_requests fr
                 WHERE LOWER(fr.to_user) = LOWER(%s)
                   AND fr.request_status = 'pending'
                   AND LOWER(fr.from_user) <> LOWER(%s)
                   AND NOT EXISTS (
                       SELECT 1
                         FROM blocks b
                        WHERE (LOWER(b.blocker) = LOWER(%s) AND LOWER(b.blocked) = LOWER(fr.from_user))
                           OR (LOWER(b.blocker) = LOWER(fr.from_user) AND LOWER(b.blocked) = LOWER(%s))
                   )
                 GROUP BY fr.from_user
                 ORDER BY LOWER(fr.from_user);
                """,
                (username, username, username, username),
            )
            requests = [row[0] for row in cur.fetchall()]
    finally:
        _release_conn(conn, from_pool)
    return requests

def load_rooms_from_json(conn=None, *, commit: bool = True):
    """Sync *official* chat_rooms from chat_rooms.json.

    Behavior:
      - inserts newly added official rooms
      - marks catalog-backed rows as room_kind='official'
      - prunes official rooms removed from the catalog
      - leaves custom/manual/autoscaled rooms untouched
    """
    data = _read_rooms_json()
    room_names = _official_room_names_from_data(data)
    if not room_names:
        logging.warning("Official room catalog has no loadable rooms – skipping preload")
        return

    rooms = [(name, 0, 'system', 'official') for name in room_names]
    official_names_lower = [name.lower() for name in room_names]

    conn = conn if conn is not None else get_db()
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chat_rooms (name, member_count, created_by, room_kind)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE
               SET created_by = EXCLUDED.created_by,
                   room_kind = EXCLUDED.room_kind;
            """,
            rooms,
        )
        cur.execute(
            """
            DELETE FROM chat_rooms r
             WHERE r.room_kind = 'official'
               AND NOT (LOWER(r.name) = ANY(%s))
               AND NOT EXISTS (SELECT 1 FROM custom_rooms cr WHERE cr.name = r.name);
            """,
            (official_names_lower,),
        )
    if commit:
        conn.commit()

def consume_room_invites(room_name: str, username: str) -> None:
    """Delete outstanding *notification* invites for (room_name, username).

    For private custom rooms, the access grant must survive reconnects, so we
    intentionally leave ``custom_room_invites`` intact and instead persist room
    membership separately when the user successfully joins.
    """
    if not room_name or not username:
        return
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM room_invites
                 WHERE LOWER(room_name)=LOWER(%s)
                   AND LOWER(invited_user)=LOWER(%s);
                """,
                (room_name, username),
            )
        conn.commit()
    finally:
        _release_conn(conn, from_pool)



def _normalize_custom_room_role(role: str | None) -> str:
    value = str(role or "member").strip().lower()
    if value in {"owner", "moderator", "member"}:
        return value
    if value in {"mod", "room_moderator", "room-mod"}:
        return "moderator"
    return "member"


def custom_room_role_rank(role: str | None) -> int:
    """Numeric custom-room role order for room-scoped moderation checks."""
    return {"member": 0, "moderator": 1, "owner": 2}.get(_normalize_custom_room_role(role), 0)

def record_custom_room_membership(room_name: str, username: str, invited_by: str | None = None, role: str | None = None) -> None:
    """Persist membership/room-scoped role for a custom room after create/join.

    Private-room members must have either a room role (owner/moderator) or a
    traceable invite grant.  This prevents old/stale member rows from becoming
    a permanent bypass for invite-only rooms.  Room names and creator usernames
    are canonicalized from ``custom_rooms`` before writing role rows so harmless
    casing drift cannot split the creator's room-scoped owner record.
    """
    if not room_name or not username:
        return
    role_value = _normalize_custom_room_role(role)
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            canonical_room = str(room_name or "").strip()
            canonical_user = str(username or "").strip()
            try:
                cur.execute(
                    """
                    SELECT name, created_by
                      FROM custom_rooms
                     WHERE LOWER(name)=LOWER(%s)
                     LIMIT 1;
                    """,
                    (canonical_room,),
                )
                meta_row = cur.fetchone()
                if meta_row:
                    canonical_room = str(meta_row[0] or canonical_room).strip() or canonical_room
                    created_by = str(meta_row[1] or "").strip()
                    if role_value == "owner" and created_by.lower() == canonical_user.lower():
                        canonical_user = created_by or canonical_user
            except Exception:
                canonical_room = str(room_name or "").strip()
                canonical_user = str(username or "").strip()

            invite_grant = invited_by
            if role_value == "member" and not invite_grant:
                try:
                    cur.execute(
                        """
                        SELECT invited_by
                          FROM custom_room_invites
                         WHERE LOWER(room_name)=LOWER(%s)
                           AND LOWER(invited_user)=LOWER(%s)
                         LIMIT 1;
                        """,
                        (canonical_room, canonical_user),
                    )
                    row = cur.fetchone()
                    invite_grant = row[0] if row and row[0] else None
                except Exception:
                    invite_grant = invited_by
            cur.execute(
                """
                INSERT INTO custom_room_members (room_name, member_user, invited_by, role, last_seen_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (room_name, member_user)
                DO UPDATE SET invited_by = COALESCE(EXCLUDED.invited_by, custom_room_members.invited_by),
                              role = CASE
                                  WHEN LOWER(COALESCE(custom_room_members.role, '')) = 'owner' THEN 'owner'
                                  WHEN LOWER(COALESCE(EXCLUDED.role, '')) IN ('owner', 'moderator') THEN LOWER(EXCLUDED.role)
                                  ELSE COALESCE(NULLIF(custom_room_members.role, ''), 'member')
                              END,
                              last_seen_at = NOW();
                """,
                (canonical_room, canonical_user, invite_grant, role_value),
            )
        conn.commit()
    finally:
        _release_conn(conn, from_pool)

def set_room_message_expiry(room: str, expiry_seconds: int, set_by: str | None = None) -> None:
    """Set per-room message expiry. expiry_seconds <= 0 disables expiry for that room."""
    room = (room or '').strip()
    if not room:
        return
    try:
        expiry_seconds = int(expiry_seconds)
    except Exception:
        expiry_seconds = 0

    conn = get_db()
    with conn.cursor() as cur:
        if expiry_seconds <= 0:
            cur.execute("DELETE FROM room_message_expiry WHERE room=%s;", (room,))
        else:
            cur.execute(
                """
                INSERT INTO room_message_expiry (room, expiry_seconds, set_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (room)
                DO UPDATE SET expiry_seconds=EXCLUDED.expiry_seconds,
                              set_by=EXCLUDED.set_by,
                              set_at=NOW();
                """,
                (room, expiry_seconds, set_by),
            )
    conn.commit()

def get_room_message_expiry(room: str) -> int | None:
    room = (room or '').strip()
    if not room:
        return None
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT expiry_seconds FROM room_message_expiry WHERE room=%s;", (room,))
        row = cur.fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except Exception:
        return None

def cleanup_expired_room_messages() -> int:
    """Delete messages older than per-room expiry. Returns number of messages deleted."""
    # Must be callable from janitor thread (no Flask context).
    conn, from_pool = _acquire_conn()
    try:
        deleted = 0
        with conn.cursor() as cur:
            # Use a single set-based delete; Postgres will apply per-row interval.
            cur.execute(
                """
                WITH del AS (
                    DELETE FROM messages m
                     USING room_message_expiry e
                     WHERE m.room = e.room
                       AND e.expiry_seconds > 0
                       AND m.timestamp < (NOW() - (e.expiry_seconds || ' seconds')::interval)
                    RETURNING 1
                )
                SELECT COUNT(*) FROM del;
                """
            )
            row = cur.fetchone()
            deleted = int(row[0] or 0) if row else 0
        if deleted:
            conn.commit()
        else:
            conn.rollback()
        return deleted
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("cleanup_expired_room_messages failed")
        return 0
    finally:
        _release_conn(conn, from_pool)

def is_user_verified(username: str) -> bool:
    if not username:
        return False
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT is_verified FROM users WHERE username=%s;", (username,))
        row = cur.fetchone()
    if not row:
        return False
    return bool(row[0])

def get_custom_room_meta(room_name: str) -> dict | None:
    """Return custom room metadata or None if not a custom room."""
    if not room_name:
        return None
    # Must be callable from Socket.IO and janitor contexts.
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT name, category, subcategory, created_by, is_private, is_18_plus, is_nsfw, created_at, last_active_at
                  FROM custom_rooms
                 WHERE LOWER(name)=LOWER(%s)
                 LIMIT 1;
                """,
                (room_name,),
            )
            row = cur.fetchone()
    finally:
        _release_conn(conn, from_pool)
    if not row:
        return None
    return {
        "name": row[0],
        "category": row[1],
        "subcategory": row[2],
        "created_by": row[3],
        "is_private": bool(row[4]),
        "is_18_plus": bool(row[5]),
        "is_nsfw": bool(row[6]),
        "created_at": row[7],
        "last_active_at": row[8],
    }


def get_custom_room_user_role(room_name: str, username: str) -> str | None:
    """Return the user's room-scoped custom-room role, if any.

    The creator is always treated as ``owner`` even if an older database was
    created before the custom_room_members.role column existed.
    """
    room_name = str(room_name or "").strip()
    username = str(username or "").strip()
    if not room_name or not username:
        return None
    meta = get_custom_room_meta(room_name)
    if not meta:
        return None
    if str(meta.get("created_by") or "").strip().lower() == username.lower():
        return "owner"
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(role, 'member')
                  FROM custom_room_members
                 WHERE LOWER(room_name)=LOWER(%s)
                   AND LOWER(member_user)=LOWER(%s)
                 LIMIT 1;
                """,
                (room_name, username),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _normalize_custom_room_role(row[0])
    finally:
        _release_conn(conn, from_pool)


def can_user_moderate_custom_room(room_name: str, username: str) -> bool:
    """True when the user has room-scoped moderator/owner powers only for this room."""
    role = get_custom_room_user_role(room_name, username)
    return role in {"owner", "moderator"}


def revoke_custom_room_access(room_name: str, username: str) -> int:
    """Remove a user's persisted custom-room access/invite rows.

    This is used when a private-room owner kicks someone: otherwise the user
    could immediately rejoin through their stored membership row.  The room
    creator/owner row is protected.
    """
    room_name = str(room_name or "").strip()
    username = str(username or "").strip()
    if not room_name or not username:
        return 0
    meta = get_custom_room_meta(room_name)
    if meta and str(meta.get("created_by") or "").strip().lower() == username.lower():
        return 0
    conn, from_pool = _acquire_conn()
    deleted = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM custom_room_invites
                 WHERE LOWER(room_name)=LOWER(%s)
                   AND LOWER(invited_user)=LOWER(%s);
                """,
                (room_name, username),
            )
            deleted += int(getattr(cur, "rowcount", 0) or 0)
            cur.execute(
                """
                DELETE FROM custom_room_members
                 WHERE LOWER(room_name)=LOWER(%s)
                   AND LOWER(member_user)=LOWER(%s);
                """,
                (room_name, username),
            )
            deleted += int(getattr(cur, "rowcount", 0) or 0)
        conn.commit()
        return deleted
    finally:
        _release_conn(conn, from_pool)

def _custom_room_member_access_exists(cur, room_name: str, username: str) -> bool:
    """True for accepted/private-room membership rows that grant entry.

    This intentionally does not look at pending invite rows.  A pending invite
    may make an invite-only room visible in the invite UI, but direct Socket.IO
    joins still ignore older/stale rows that cannot prove invitation, so
    older/stale rows cannot let someone enter an invite-only room by guessing.
    room entry must require the creator or an accepted/persisted membership
    grant so guessing a room name plus stale client state cannot bypass the
    invite acceptance step.
    """
    cur.execute(
        """
        SELECT 1
          FROM custom_room_members
         WHERE LOWER(room_name)=LOWER(%s)
           AND LOWER(member_user)=LOWER(%s)
           AND (LOWER(COALESCE(role, '')) IN ('owner', 'moderator') OR invited_by IS NOT NULL)
         LIMIT 1;
        """,
        (room_name, username),
    )
    return cur.fetchone() is not None


def can_user_join_custom_room(room_name: str, username: str) -> bool:
    """Return True if the user may enter the custom room right now.

    Rules:
      - Public custom rooms: anyone.
      - Private custom rooms: creator/owner or an accepted persisted member.
      - Pending invite rows alone are not join access; the invite must first be
        accepted so membership is persisted and fake direct joins stay blocked.
    """
    meta = get_custom_room_meta(room_name)
    if not meta:
        return False
    if not meta.get("is_private"):
        return True
    if username and str(meta.get("created_by") or "").strip().lower() == str(username or "").strip().lower():
        return True
    if not username:
        return False
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            return _custom_room_member_access_exists(cur, room_name, username)
    finally:
        _release_conn(conn, from_pool)


def can_user_access_custom_room(room_name: str, username: str) -> bool:
    """Return True if user can see the custom room.

    Rules:
      - Public custom rooms: anyone.
      - Private custom rooms: owner, accepted room member/moderator, or
        currently pending invitee.

    Visibility deliberately includes pending invite rows so invited users can
    see and accept the room invite.  Join enforcement uses
    can_user_join_custom_room() so a pending invite does not become a direct
    room-entry bypass.
    """
    meta = get_custom_room_meta(room_name)
    if not meta:
        return False
    if not meta.get("is_private"):
        return True
    if username and str(meta.get("created_by") or "").strip().lower() == str(username or "").strip().lower():
        return True
    if not username:
        return False
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            if _custom_room_member_access_exists(cur, room_name, username):
                return True
            cur.execute(
                """
                SELECT 1 FROM custom_room_invites
                 WHERE LOWER(room_name)=LOWER(%s)
                   AND LOWER(invited_user)=LOWER(%s)
                 LIMIT 1;
                """,
                (room_name, username),
            )
            return cur.fetchone() is not None
    finally:
        _release_conn(conn, from_pool)

def touch_custom_room_activity(room_name: str) -> None:
    """Update last_active_at for a custom room (no-op for non-custom)."""
    if not room_name:
        return
    # Must be callable from Socket.IO contexts.
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE custom_rooms
                   SET last_active_at = NOW()
                 WHERE name = %s;
                """,
                (room_name,),
            )
            touched = cur.rowcount
        if touched:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _release_conn(conn, from_pool)


def delete_custom_room_persisted_state(cur, room_names: list[str] | tuple[str, ...]) -> dict[str, int]:
    """Delete DB rows owned by custom rooms that are being removed.

    This intentionally covers more than the custom_rooms/chat_rooms rows.  Room
    messages are the durable history users see after reconnect, while room
    invites, moderation controls, per-room expiry, and recent-room profile rows
    can otherwise leave a deleted custom room visible through side channels.

    The caller owns the transaction.
    """
    names: list[str] = []
    for raw in room_names or []:
        value = str(raw or "").strip()
        if value and value not in names:
            names.append(value)
    if not names:
        return {}

    stats: dict[str, int] = {}

    def _delete(key: str, sql: str, *, optional: bool = False) -> None:
        try:
            cur.execute(sql, (names,))
            stats[key] = int(getattr(cur, "rowcount", 0) or 0)
        except Exception:
            if not optional:
                raise
            stats[key] = 0
            logging.debug("optional custom-room cleanup table missing/unavailable for %s", key, exc_info=True)

    # Delete message children explicitly for upgraded legacy databases that may
    # not have ON DELETE CASCADE constraints even though current installs do.
    _delete(
        "message_reads",
        """
        DELETE FROM message_reads
         WHERE message_id IN (SELECT id FROM messages WHERE room = ANY(%s));
        """,
        optional=True,
    )
    _delete(
        "message_reactions",
        """
        DELETE FROM message_reactions
         WHERE message_id IN (SELECT id FROM messages WHERE room = ANY(%s));
        """,
        optional=True,
    )
    _delete(
        "file_attachments",
        """
        DELETE FROM file_attachments
         WHERE message_id IN (SELECT id FROM messages WHERE room = ANY(%s));
        """,
        optional=True,
    )
    _delete("messages", "DELETE FROM messages WHERE room = ANY(%s);")
    _delete("room_invites", "DELETE FROM room_invites WHERE room_name = ANY(%s);", optional=True)
    _delete("custom_room_invites", "DELETE FROM custom_room_invites WHERE room_name = ANY(%s);")
    _delete("custom_room_members", "DELETE FROM custom_room_members WHERE room_name = ANY(%s);")
    _delete("room_locks", "DELETE FROM room_locks WHERE room = ANY(%s);", optional=True)
    _delete("room_readonly", "DELETE FROM room_readonly WHERE room = ANY(%s);", optional=True)
    _delete("room_slowmode", "DELETE FROM room_slowmode WHERE room = ANY(%s);", optional=True)
    _delete("room_message_expiry", "DELETE FROM room_message_expiry WHERE room = ANY(%s);", optional=True)
    _delete("user_recent_rooms", "DELETE FROM user_recent_rooms WHERE room_name = ANY(%s);", optional=True)
    _delete(
        "room_sanctions",
        """
        DELETE FROM user_sanctions
         WHERE sanction_type = ANY(
            SELECT 'room_ban:' || room_name FROM unnest(%s::text[]) AS room_name(room_name)
         );
        """,
        optional=True,
    )
    return stats

def cleanup_expired_custom_rooms(
    idle_hours: int = 168,
    private_idle_hours: int | None = None,
    idle_minutes: int | None = None,
    private_idle_minutes: int | None = None,
    debug: bool = False,
    live_counts: dict[str, int] | None = None,
) -> int:
    """Delete empty custom rooms that have been inactive beyond their TTL.

    Rooms are eligible if:
      - they exist in custom_rooms
      - they are empty (live Socket.IO room count is 0 when supplied, otherwise chat_rooms.member_count is 0)
      - their last_active_at / created_at is older than NOW() - TTL

    Deletion also purges durable room history/control state for those rooms:
    messages, invites, room locks/read-only/slowmode, per-room expiry settings,
    room-scoped membership, recent-room profile references, and room bans.

    TTL policy:
      - minute-based settings win when provided
      - otherwise hour-based settings are converted to minutes for backwards compatibility
      - public rooms use `idle_minutes` / `idle_hours`
      - private rooms use `private_idle_minutes` / `private_idle_hours` (fallback: public TTL)

    When `debug` is true, this logs the reason each custom room was skipped or deleted.

    `live_counts` is an optional authoritative snapshot from realtime.state.live_room_counts().
    When supplied, it wins over the persisted chat_rooms.member_count counter.  That
    fixes stale DB counters that can otherwise keep an empty custom room stuck in
    "timer paused" forever after a browser crash, tab close, or missed disconnect.

    Returns number of deleted rooms.

    This must be callable outside of Flask request/app context (e.g. janitor).
    """
    try:
        public_ttl_minutes = int(idle_minutes) if idle_minutes is not None else int(idle_hours or 3) * 60
    except Exception:
        public_ttl_minutes = 180

    try:
        private_ttl_minutes = int(private_idle_minutes) if private_idle_minutes is not None else (
            int(private_idle_hours) * 60 if private_idle_hours is not None else public_ttl_minutes
        )
    except Exception:
        private_ttl_minutes = public_ttl_minutes

    public_ttl_minutes = max(1, min(public_ttl_minutes, 24 * 60 * 365))
    private_ttl_minutes = max(1, min(private_ttl_minutes, 24 * 60 * 365))

    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cr.name,
                    cr.is_private,
                    COALESCE(cr.last_active_at, cr.created_at) AS activity_at,
                    COALESCE(r.member_count, 0) AS member_count,
                    EXTRACT(EPOCH FROM (NOW() - COALESCE(cr.last_active_at, cr.created_at))) / 60.0 AS age_minutes,
                    (r.name IS NULL) AS chat_room_missing,
                    COALESCE(r.room_kind, '') AS chat_room_kind
                  FROM custom_rooms cr
                  LEFT JOIN chat_rooms r ON r.name = cr.name
                 ORDER BY cr.name ASC;
                """
            )
            rows = cur.fetchall() or []

            expired_names: list[str] = []
            member_count_repairs: list[tuple[int, str]] = []
            live_counts_snapshot = live_counts if isinstance(live_counts, dict) else None

            for row in rows:
                room_name = row[0]
                is_private = bool(row[1])
                activity_at = row[2]
                persisted_member_count = int(row[3] or 0)
                live_member_count = None
                if live_counts_snapshot is not None:
                    try:
                        live_member_count = max(0, int(live_counts_snapshot.get(str(room_name), 0) or 0))
                    except Exception:
                        live_member_count = 0
                member_count = live_member_count if live_member_count is not None else persisted_member_count
                if live_member_count is not None and live_member_count != persisted_member_count:
                    member_count_repairs.append((live_member_count, room_name))
                age_minutes = float(row[4] or 0.0)
                chat_room_missing = bool(row[5])
                chat_room_kind = str(row[6] or '').strip().lower()
                ttl_minutes = private_ttl_minutes if is_private else public_ttl_minutes

                if chat_room_kind and chat_room_kind != 'custom':
                    if debug:
                        logging.info(
                            "[JANITOR][custom_rooms] skip room=%r private=%s reason=protected_room_kind room_kind=%r ttl_minutes=%s age_minutes=%.2f",
                            room_name, is_private, chat_room_kind, ttl_minutes, age_minutes,
                        )
                    continue

                if member_count > 0:
                    if debug:
                        source = "live" if live_member_count is not None else "persisted"
                        logging.info(
                            "[JANITOR][custom_rooms] skip room=%r private=%s reason=member_count source=%s member_count=%s persisted_member_count=%s ttl_minutes=%s age_minutes=%.2f",
                            room_name, is_private, source, member_count, persisted_member_count, ttl_minutes, age_minutes,
                        )
                    continue

                if age_minutes < ttl_minutes:
                    if debug:
                        logging.info(
                            "[JANITOR][custom_rooms] skip room=%r private=%s reason=not_old_enough ttl_minutes=%s age_minutes=%.2f activity_at=%s chat_room_missing=%s",
                            room_name, is_private, ttl_minutes, age_minutes, activity_at, chat_room_missing,
                        )
                    continue

                expired_names.append(room_name)
                if debug:
                    logging.info(
                        "[JANITOR][custom_rooms] delete room=%r private=%s reason=expired ttl_minutes=%s age_minutes=%.2f activity_at=%s chat_room_missing=%s",
                        room_name, is_private, ttl_minutes, age_minutes, activity_at, chat_room_missing,
                    )

            if member_count_repairs:
                for repaired_count, repaired_room in member_count_repairs:
                    cur.execute(
                        "UPDATE chat_rooms SET member_count = %s WHERE name = %s AND room_kind = 'custom';",
                        (repaired_count, repaired_room),
                    )
                    if debug:
                        logging.info(
                            "[JANITOR][custom_rooms] repaired stale member_count room=%r member_count=%s",
                            repaired_room, repaired_count,
                        )

            if not expired_names:
                if debug:
                    logging.info(
                        "[JANITOR][custom_rooms] no deletions public_ttl_minutes=%s private_ttl_minutes=%s inspected=%s",
                        public_ttl_minutes, private_ttl_minutes, len(rows),
                    )
                conn.commit()
                return 0

            cleanup_stats = delete_custom_room_persisted_state(cur, expired_names)
            if debug:
                logging.info(
                    "[JANITOR][custom_rooms] purged expired custom-room state stats=%s",
                    cleanup_stats,
                )
            cur.execute(
                "DELETE FROM custom_rooms WHERE name = ANY(%s) RETURNING name;",
                (expired_names,),
            )
            deleted_rows = cur.fetchall() or []
            deleted_names = [r[0] for r in deleted_rows]
            if deleted_names:
                cur.execute(
                    "DELETE FROM chat_rooms WHERE name = ANY(%s) AND room_kind = 'custom';",
                    (deleted_names,),
                )
        conn.commit()
        return len(deleted_names)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("cleanup_expired_custom_rooms failed")
        return 0
    finally:
        _release_conn(conn, from_pool)

def get_friends_for_user(username: str) -> list[str]:
    """
    Return a visible, accepted friend list for the given username.

    Accepted rows in friend_requests are the canonical friendship source.  This
    helper filters stale blocked pairs and sorts deterministically so the live
    dock, HTTP API, and admin/test flows all see the same friend set.
    """
    conn = get_db()
    friends: list[str] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH accepted AS (
                SELECT DISTINCT
                    CASE
                        WHEN LOWER(from_user) = LOWER(%s) THEN to_user
                        ELSE from_user
                    END AS friend
                  FROM friend_requests
                 WHERE (LOWER(from_user) = LOWER(%s) OR LOWER(to_user) = LOWER(%s))
                   AND request_status = 'accepted'
            )
            SELECT friend
              FROM accepted a
             WHERE friend IS NOT NULL
               AND LOWER(friend) <> LOWER(%s)
               AND NOT EXISTS (
                   SELECT 1
                     FROM blocks b
                    WHERE (LOWER(b.blocker) = LOWER(%s) AND LOWER(b.blocked) = LOWER(a.friend))
                       OR (LOWER(b.blocker) = LOWER(a.friend) AND LOWER(b.blocked) = LOWER(%s))
               )
             ORDER BY LOWER(friend);
            """,
            (username, username, username, username, username, username),
        )
        for row in cur.fetchall():
            friend_name = row[0]
            if friend_name:
                friends.append(friend_name)
    return friends

def get_all_rooms():
    """Return a list of all chat rooms, ordered by name.

    Uses the global pool when available.
    """
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, member_count FROM chat_rooms ORDER BY name;")
            rows = cur.fetchall()
        return [{"name": row[0], "member_count": int(row[1] or 0), "members": int(row[1] or 0)} for row in rows]
    except Exception as e:
        logging.error("get_all_rooms() failed: %s", str(e))
        return []
    finally:
        _release_conn(conn, from_pool)

def create_room_if_missing(room: str, room_kind: str = "manual"):
    """
    Insert a room with member_count=0 if it does not already exist.

    room_kind is used to distinguish official/custom/manual rooms so JSON sync
    can safely prune only catalog-backed entries.
    """
    conn, from_pool = _acquire_conn()
    room_kind = str(room_kind or "manual").strip().lower()
    if room_kind not in {"manual", "custom", "official", "autoscaler"}:
        room_kind = "manual"
    created_by = "autoscaler" if room_kind == "autoscaler" else "system"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_rooms (name, member_count, created_by, last_active_at, room_kind)
                VALUES (%s, 0, %s, NOW(), %s)
                ON CONFLICT (name) DO NOTHING;
                """,
                (room, created_by, room_kind),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("create_room_if_missing failed (room=%s kind=%s)", room, room_kind)
    finally:
        _release_conn(conn, from_pool)

def create_autoscaled_room_if_missing(room: str, base_room: str) -> bool:
    """Create an autoscaled room shard and report whether it was inserted.

    Marks created_by='autoscaler' and room_kind='autoscaler' so janitor can
    safely delete it when idle. The base_room argument is intentionally kept for
    audit/log context; runtime routing still stores the shard as a normal
    chat_rooms row named like ``Base Room (2)``.
    """
    conn, from_pool = _acquire_conn()
    created = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_rooms (name, member_count, created_by, last_active_at, room_kind)
                VALUES (%s, 0, 'autoscaler', NOW(), 'autoscaler')
                ON CONFLICT (name) DO NOTHING;
                """,
                (room,),
            )
            try:
                created = int(getattr(cur, "rowcount", 0) or 0) > 0
            except Exception:
                created = False
        conn.commit()
        return bool(created)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("create_autoscaled_room_if_missing failed (room=%s base=%s)", room, base_room)
        return False
    finally:
        _release_conn(conn, from_pool)

def increment_room_count(room: str, delta: int):
    """
    Add 'delta' (which may be negative) to member_count for a given room,
    ensuring the result does not go below 0.

    IMPORTANT:
      This helper is called from Socket.IO event handlers, disconnect handlers,
      and background-ish contexts where Flask's request/app context may not be
      present. Therefore it must NOT rely on flask.g / get_db().
    """
    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            if int(delta) > 0:
                cur.execute(
                    """
                    UPDATE chat_rooms
                       SET member_count = GREATEST(member_count + %s, 0),
                           last_active_at = NOW()
                     WHERE name = %s;
                    """,
                    (delta, room),
                )
            else:
                cur.execute(
                    """
                    UPDATE chat_rooms
                       SET member_count = GREATEST(member_count + %s, 0)
                     WHERE name = %s;
                    """,
                    (delta, room),
                )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("increment_room_count failed (room=%s delta=%s)", room, delta)
    finally:
        _release_conn(conn, from_pool)

def cleanup_expired_autoscaled_rooms(idle_minutes: int = 30) -> int:
    """Delete empty autoscaled room shards that have been idle longer than idle_minutes."""
    try:
        idle_minutes = int(idle_minutes)
    except Exception:
        idle_minutes = 30
    idle_minutes = max(1, min(idle_minutes, 24 * 60 * 7))

    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM chat_rooms
                 WHERE created_by = 'autoscaler'
                   AND COALESCE(member_count, 0) = 0
                   AND COALESCE(last_active_at, created_at) < (NOW() - (%s || ' minutes')::interval)
                RETURNING name;
                """,
                (idle_minutes,),
            )
            rows = cur.fetchall() or []
        conn.commit()
        return len(rows)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.exception("cleanup_expired_autoscaled_rooms failed")
        return 0
    finally:
        _release_conn(conn, from_pool)

def dump_tables():
    """
    Print quick row counts for sanity checks.
    """
    conn = get_db()
    with conn.cursor() as cur:
        for tbl in ("users", "chat_rooms", "messages"):
            cur.execute(f"SELECT COUNT(*) FROM {tbl};")
            count = cur.fetchone()[0]
            print(f"{tbl}: {count}")

def seed_rooms_from_file(file_path="chat_rooms.json"):
    """
    Read a JSON file of rooms (each can be either a string or a dict
    containing 'name' and optional 'description'), then INSERT them
    into a legacy 'rooms' table if it exists. Prints status messages.
    """
    if not os.path.exists(file_path):
        print(f"⚠ Room file '{file_path}' not found.")
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            rooms = json.load(f)
    except Exception as e:
        print(f"❌ Failed to parse {file_path}: {e}")
        return

    conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            count = 0
            for room in rooms:
                name = room.get("name") if isinstance(room, dict) else room
                description = room.get("description", "") if isinstance(room, dict) else ""
                cur.execute(
                    """
                    INSERT INTO rooms (name, description)
                    VALUES (%s, %s)
                    ON CONFLICT (name) DO NOTHING;
                    """,
                    (name, description)
                )
                count += 1
        conn.commit()
        print(f"✅ Seeded {count} room(s) from {file_path}")
    finally:
        _release_conn(conn, from_pool)

