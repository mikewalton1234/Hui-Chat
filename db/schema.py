#!/usr/bin/env python3
"""Schema/bootstrap helpers extracted from the legacy database facade."""

from __future__ import annotations

import logging

import psycopg2
from psycopg2.extras import RealDictCursor

from db.core import get_db, _acquire_conn, _release_conn
from db.rooms import _official_room_names_from_json, load_rooms_from_json

def _log_table_owner_mismatch(conn, table_name: str) -> None:
    """Log actionable guidance when the connected DB user can't ALTER a table.

    Most common cause: tables were created by a different Postgres role (owner),
    but the DSN in server_config.json uses another role.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database();")
            current_user, current_db = cur.fetchone()
            cur.execute(
                """
                SELECT tableowner
                  FROM pg_tables
                 WHERE schemaname = 'public'
                   AND tablename = %s;
                """,
                (table_name,),
            )
            row = cur.fetchone()
            owner = row[0] if row else None

        logging.error(
            "DB migration blocked: connected as '%s' but public.%s is owned by '%s'.",
            current_user,
            table_name,
            owner,
        )
        logging.error("Fix (run as a superuser / postgres):")
        logging.error(
            "  sudo -u postgres psql -d %s -c \"ALTER TABLE public.%s OWNER TO %s;\"",
            current_db,
            table_name,
            current_user,
        )
        if owner and owner != current_user:
            logging.error(
                "Optional: reassign everything owned by '%s' to '%s':",
                owner,
                current_user,
            )
            logging.error(
                "  sudo -u postgres psql -d %s -c \"REASSIGN OWNED BY %s TO %s;\"",
                current_db,
                owner,
                current_user,
            )
    except Exception as exc:
        logging.error("Could not inspect table ownership for troubleshooting: %s", exc)





# ----------------------------------------------------------------------
# Column / table patch helpers
# ----------------------------------------------------------------------


def _schema_conn(existing=None):
    """Return an existing migration/setup connection or the request DB connection."""
    return existing if existing is not None else get_db()


def _commit_schema_conn(conn, commit: bool) -> None:
    """Commit only when the caller did not pass a migration-managed connection."""
    if commit:
        conn.commit()


def ensure_online_column(conn=None, *, commit: bool = True):
    """
    Add an 'online BOOLEAN DEFAULT FALSE' column to users if it does not exist.
    """
    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'users'
               AND column_name = 'online';
            """
        )
        if cur.fetchone() is None:
            logging.warning("Adding users.online column")
            cur.execute(
                "ALTER TABLE users ADD COLUMN online BOOLEAN DEFAULT FALSE;"
            )
            _commit_schema_conn(conn, commit)


def ensure_presence_columns(conn=None, *, commit: bool = True):
    """Ensure presence-related columns exist on users.

    - presence_status: user's chosen availability state (online/away/busy/invisible)
    - custom_status: optional short text (enforced in app logic)

    These are separate from users.online (transport-level connectedness) and users.status
    (account/admin state).
    """
    conn = _schema_conn(conn)
    try:
        with conn.cursor() as cur:
            # presence_status
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'users'
                   AND column_name = 'presence_status';
                """
            )
            if cur.fetchone() is None:
                logging.warning("Adding users.presence_status column")
                cur.execute(
                    "ALTER TABLE users ADD COLUMN presence_status TEXT NOT NULL DEFAULT 'online';"
                )

            # custom_status (older DBs may not have it)
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'users'
                   AND column_name = 'custom_status';
                """
            )
            if cur.fetchone() is None:
                logging.warning("Adding users.custom_status column")
                cur.execute(
                    "ALTER TABLE users ADD COLUMN custom_status TEXT;"
                )

        _commit_schema_conn(conn, commit)
    except psycopg2.errors.InsufficientPrivilege:
        conn.rollback()
        _log_table_owner_mismatch(conn, "users")
        raise


def ensure_users_profile_columns(conn=None, *, commit: bool = True) -> None:
    """Ensure newer profile/presence-history columns exist on users.

    These columns were added after some setup/bootstrap paths already existed,
    so legacy or setup-created databases can otherwise miss them even though
    runtime queries expect them. Keep this idempotent.
    """

    conn = _schema_conn(conn)
    try:
        with conn.cursor() as cur:
            def _ensure_user_col(col: str, ddl: str) -> None:
                cur.execute(
                    """
                    SELECT column_name
                      FROM information_schema.columns
                     WHERE table_schema = 'public'
                       AND table_name = 'users'
                       AND column_name = %s;
                    """,
                    (col,),
                )
                if cur.fetchone() is None:
                    logging.warning("Adding users.%s column", col)
                    cur.execute(ddl)

            _ensure_user_col("last_seen", "ALTER TABLE users ADD COLUMN last_seen TIMESTAMP WITH TIME ZONE;")
            _ensure_user_col("status", "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active';")
            _ensure_user_col("bio", "ALTER TABLE users ADD COLUMN bio TEXT;")
            _ensure_user_col("relationship_status", "ALTER TABLE users ADD COLUMN relationship_status TEXT;")
            _ensure_user_col("relationship_visibility", "ALTER TABLE users ADD COLUMN relationship_visibility TEXT NOT NULL DEFAULT 'friends';")
            _ensure_user_col("age_visibility", "ALTER TABLE users ADD COLUMN age_visibility TEXT NOT NULL DEFAULT 'friends';")
            _ensure_user_col("location_text", "ALTER TABLE users ADD COLUMN location_text TEXT;")
            _ensure_user_col("location_visibility", "ALTER TABLE users ADD COLUMN location_visibility TEXT NOT NULL DEFAULT 'friends';")
            _ensure_user_col("interests", "ALTER TABLE users ADD COLUMN interests TEXT;")
            _ensure_user_col("favorite_music", "ALTER TABLE users ADD COLUMN favorite_music TEXT;")
            _ensure_user_col("favorite_movies", "ALTER TABLE users ADD COLUMN favorite_movies TEXT;")
            _ensure_user_col("favorite_games", "ALTER TABLE users ADD COLUMN favorite_games TEXT;")
            _ensure_user_col("website_url", "ALTER TABLE users ADD COLUMN website_url TEXT;")
            _ensure_user_col("banner_url", "ALTER TABLE users ADD COLUMN banner_url TEXT;")
            _ensure_user_col("profile_accent", "ALTER TABLE users ADD COLUMN profile_accent TEXT;")
            _ensure_user_col("share_recent_rooms", "ALTER TABLE users ADD COLUMN share_recent_rooms BOOLEAN NOT NULL DEFAULT FALSE;")
            _ensure_user_col("recent_rooms_visibility", "ALTER TABLE users ADD COLUMN recent_rooms_visibility TEXT NOT NULL DEFAULT 'friends';")
            _ensure_user_col("profile_post_default_visibility", "ALTER TABLE users ADD COLUMN profile_post_default_visibility TEXT NOT NULL DEFAULT 'friends';")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_recent_rooms (
                    id         SERIAL PRIMARY KEY,
                    username   TEXT NOT NULL,
                    room_name  TEXT NOT NULL,
                    joined_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_recent_rooms_username_joined ON user_recent_rooms(username, joined_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_recent_rooms_username_room ON user_recent_rooms(username, room_name);")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_posts (
                    id              SERIAL PRIMARY KEY,
                    author_username TEXT NOT NULL,
                    body            TEXT,
                    visibility      TEXT NOT NULL DEFAULT 'friends',
                    image_url       TEXT,
                    gif_url         TEXT,
                    link_url        TEXT,
                    is_pinned       BOOLEAN NOT NULL DEFAULT FALSE,
                    is_featured     BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    deleted_at      TIMESTAMP WITH TIME ZONE,
                    edited_at       TIMESTAMP WITH TIME ZONE,
                    edit_count      INTEGER NOT NULL DEFAULT 0,
                    moderated_by    TEXT,
                    moderated_reason TEXT,
                    moderated_at    TIMESTAMP WITH TIME ZONE
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_created ON profile_posts(author_username, created_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_featured ON profile_posts(author_username, is_featured, created_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_pinned ON profile_posts(author_username, is_pinned, created_at DESC);")
            _ensure_profile_post_engagement_tables(cur)

            cur.execute("UPDATE users SET status = 'active' WHERE status IS NULL OR BTRIM(status) = '';")
            cur.execute("UPDATE users SET relationship_visibility = 'friends' WHERE relationship_visibility IS NULL OR BTRIM(relationship_visibility) = '';")
            cur.execute("UPDATE users SET age_visibility = 'friends' WHERE age_visibility IS NULL OR BTRIM(age_visibility) = '';")
            cur.execute("UPDATE users SET location_visibility = 'friends' WHERE location_visibility IS NULL OR BTRIM(location_visibility) = '';")
            cur.execute("UPDATE users SET recent_rooms_visibility = 'friends' WHERE recent_rooms_visibility IS NULL OR BTRIM(recent_rooms_visibility) = '';")
            cur.execute("UPDATE users SET profile_post_default_visibility = 'friends' WHERE profile_post_default_visibility IS NULL OR BTRIM(profile_post_default_visibility) = '';")
            cur.execute("UPDATE users SET share_recent_rooms = FALSE WHERE share_recent_rooms IS NULL;")

        _commit_schema_conn(conn, commit)
    except psycopg2.errors.InsufficientPrivilege:
        conn.rollback()
        _log_table_owner_mismatch(conn, "users")
        raise


def _ensure_profile_post_engagement_tables(cur) -> None:
    """Create profile post editing, engagement, notification, and moderation tables/indexes.

    Kept as a shared helper so fresh installs, legacy schema patching, and
    tracked migrations all create the same profile-post structure.
    """
    profile_post_columns = {
        "edited_at": "ALTER TABLE profile_posts ADD COLUMN edited_at TIMESTAMP WITH TIME ZONE;",
        "edit_count": "ALTER TABLE profile_posts ADD COLUMN edit_count INTEGER NOT NULL DEFAULT 0;",
        "moderated_by": "ALTER TABLE profile_posts ADD COLUMN moderated_by TEXT;",
        "moderated_reason": "ALTER TABLE profile_posts ADD COLUMN moderated_reason TEXT;",
        "moderated_at": "ALTER TABLE profile_posts ADD COLUMN moderated_at TIMESTAMP WITH TIME ZONE;",
    }
    cur.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'profile_posts';
        """
    )
    existing_profile_post_columns = {str(row[0]) for row in (cur.fetchall() or [])}
    for column_name, ddl in profile_post_columns.items():
        if column_name not in existing_profile_post_columns:
            cur.execute(ddl)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_post_reactions (
            post_id    INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
            username   TEXT NOT NULL,
            reaction   TEXT NOT NULL DEFAULT 'like',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (post_id, username, reaction)
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_post_reactions_user ON profile_post_reactions(username, created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_post_reactions_post ON profile_post_reactions(post_id, reaction);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_post_comments (
            id              SERIAL PRIMARY KEY,
            post_id         INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
            author_username TEXT NOT NULL,
            body            TEXT NOT NULL,
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            deleted_at      TIMESTAMP WITH TIME ZONE,
            deleted_by      TEXT,
            deleted_reason  TEXT
        );
        """
    )
    cur.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'profile_post_comments';
        """
    )
    existing_comment_columns = {str(row[0]) for row in (cur.fetchall() or [])}
    if "deleted_by" not in existing_comment_columns:
        cur.execute("ALTER TABLE profile_post_comments ADD COLUMN deleted_by TEXT;")
    if "deleted_reason" not in existing_comment_columns:
        cur.execute("ALTER TABLE profile_post_comments ADD COLUMN deleted_reason TEXT;")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_post_comments_post_created ON profile_post_comments(post_id, created_at DESC, id DESC) WHERE deleted_at IS NULL;")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_post_comments_author_created ON profile_post_comments(author_username, created_at DESC) WHERE deleted_at IS NULL;")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_moderation ON profile_posts(deleted_at, moderated_at, updated_at DESC);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_post_reports (
            id                SERIAL PRIMARY KEY,
            reporter_username TEXT NOT NULL,
            post_id           INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
            comment_id        INTEGER REFERENCES profile_post_comments(id) ON DELETE SET NULL,
            target_username   TEXT NOT NULL,
            reason            TEXT NOT NULL DEFAULT 'other',
            details           TEXT,
            status            TEXT NOT NULL DEFAULT 'open',
            reviewed_by       TEXT,
            reviewed_at       TIMESTAMP WITH TIME ZONE,
            action_taken      TEXT,
            created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_post_reports_status_created ON profile_post_reports(status, created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_post_reports_post_created ON profile_post_reports(post_id, created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_post_reports_target_created ON profile_post_reports(target_username, created_at DESC);")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_profile_post_reports_open_reporter_target ON profile_post_reports(reporter_username, post_id, COALESCE(comment_id, 0)) WHERE status = 'open';")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile_badges (
            id          SERIAL PRIMARY KEY,
            username    TEXT NOT NULL,
            badge_key   TEXT NOT NULL,
            label       TEXT NOT NULL,
            assigned_by TEXT,
            reason      TEXT,
            created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(username, badge_key)
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_profile_badges_username ON user_profile_badges(username, created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_profile_badges_key ON user_profile_badges(badge_key);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile_notification_settings (
            username              TEXT PRIMARY KEY,
            notify_likes          BOOLEAN NOT NULL DEFAULT TRUE,
            notify_comments       BOOLEAN NOT NULL DEFAULT TRUE,
            notify_admin_notices  BOOLEAN NOT NULL DEFAULT TRUE,
            notify_report_updates BOOLEAN NOT NULL DEFAULT TRUE,
            notify_profile_views  BOOLEAN NOT NULL DEFAULT FALSE,
            notify_friend_posts   BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'user_profile_notification_settings';
        """
    )
    existing_notification_setting_columns = {str(row[0]) for row in (cur.fetchall() or [])}
    notification_setting_columns = {
        "notify_likes": "ALTER TABLE user_profile_notification_settings ADD COLUMN notify_likes BOOLEAN NOT NULL DEFAULT TRUE;",
        "notify_comments": "ALTER TABLE user_profile_notification_settings ADD COLUMN notify_comments BOOLEAN NOT NULL DEFAULT TRUE;",
        "notify_admin_notices": "ALTER TABLE user_profile_notification_settings ADD COLUMN notify_admin_notices BOOLEAN NOT NULL DEFAULT TRUE;",
        "notify_report_updates": "ALTER TABLE user_profile_notification_settings ADD COLUMN notify_report_updates BOOLEAN NOT NULL DEFAULT TRUE;",
        "notify_profile_views": "ALTER TABLE user_profile_notification_settings ADD COLUMN notify_profile_views BOOLEAN NOT NULL DEFAULT FALSE;",
        "notify_friend_posts": "ALTER TABLE user_profile_notification_settings ADD COLUMN notify_friend_posts BOOLEAN NOT NULL DEFAULT TRUE;",
        "updated_at": "ALTER TABLE user_profile_notification_settings ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;",
    }
    for column_name, ddl in notification_setting_columns.items():
        if column_name not in existing_notification_setting_columns:
            cur.execute(ddl)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_notification_settings_updated ON user_profile_notification_settings(updated_at DESC);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id            SERIAL PRIMARY KEY,
            user_id       INTEGER NOT NULL,
            notification  TEXT NOT NULL,
            type          TEXT,
            is_read       BOOLEAN DEFAULT FALSE,
            timestamp     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'notifications';
        """
    )
    existing_notification_columns = {str(row[0]) for row in (cur.fetchall() or [])}
    notification_columns = {
        "type": "ALTER TABLE notifications ADD COLUMN type TEXT;",
        "is_read": "ALTER TABLE notifications ADD COLUMN is_read BOOLEAN DEFAULT FALSE;",
        "timestamp": "ALTER TABLE notifications ADD COLUMN timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
    }
    for column_name, ddl in notification_columns.items():
        if column_name not in existing_notification_columns:
            cur.execute(ddl)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_notifications_user_unread ON notifications(user_id, is_read, timestamp DESC) WHERE type LIKE 'profile_post_%';")


def ensure_profile_post_engagement_schema(conn=None, *, commit: bool = True) -> None:
    """Ensure profile post likes/reactions and comments exist."""
    conn = _schema_conn(conn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_posts (
                    id              SERIAL PRIMARY KEY,
                    author_username TEXT NOT NULL,
                    body            TEXT,
                    visibility      TEXT NOT NULL DEFAULT 'friends',
                    image_url       TEXT,
                    gif_url         TEXT,
                    link_url        TEXT,
                    is_pinned       BOOLEAN NOT NULL DEFAULT FALSE,
                    is_featured     BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    deleted_at      TIMESTAMP WITH TIME ZONE,
                    edited_at       TIMESTAMP WITH TIME ZONE,
                    edit_count      INTEGER NOT NULL DEFAULT 0,
                    moderated_by    TEXT,
                    moderated_reason TEXT,
                    moderated_at    TIMESTAMP WITH TIME ZONE
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_created ON profile_posts(author_username, created_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_featured ON profile_posts(author_username, is_featured, created_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_pinned ON profile_posts(author_username, is_pinned, created_at DESC);")
            _ensure_profile_post_engagement_tables(cur)
        _commit_schema_conn(conn, commit)
    except psycopg2.errors.InsufficientPrivilege:
        conn.rollback()
        _log_table_owner_mismatch(conn, "profile_posts")
        raise


def ensure_chat_rooms_table(conn=None, *, commit: bool = True):
    """
    Create chat_rooms table and ensure the metadata columns we rely on exist.
    """
    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_rooms (
                id            SERIAL PRIMARY KEY,
                name          TEXT UNIQUE NOT NULL,
                created_by    TEXT NOT NULL DEFAULT 'system',
                member_count  INTEGER DEFAULT 0,
                room_kind     TEXT NOT NULL DEFAULT 'manual',
                created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_active_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'chat_rooms'
               AND column_name = 'member_count';
            """
        )
        if cur.fetchone() is None:
            logging.warning("Adding chat_rooms.member_count column")
            cur.execute("ALTER TABLE chat_rooms ADD COLUMN member_count INTEGER DEFAULT 0;")

        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'chat_rooms'
               AND column_name = 'room_kind';
            """
        )
        if cur.fetchone() is None:
            logging.warning("Adding chat_rooms.room_kind column")
            cur.execute("ALTER TABLE chat_rooms ADD COLUMN room_kind TEXT NOT NULL DEFAULT 'manual';")

        # Track last time the room was non-empty (used for autoscaled room cleanup)
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'chat_rooms'
               AND column_name = 'last_active_at';
            """
        )
        if cur.fetchone() is None:
            logging.warning("Adding chat_rooms.last_active_at column")
            cur.execute(
                "ALTER TABLE chat_rooms ADD COLUMN last_active_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;"
            )
            try:
                cur.execute("UPDATE chat_rooms SET last_active_at = created_at WHERE last_active_at IS NULL;")
            except Exception:
                pass
    _commit_schema_conn(conn, commit)


def sync_chat_room_kinds(conn=None, *, commit: bool = True):
    """Backfill room_kind so official/custom/manual rooms can be managed safely."""
    official_names_lower = [name.lower() for name in _official_room_names_from_json()]

    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE chat_rooms SET room_kind='manual' WHERE room_kind IS NULL OR BTRIM(room_kind)='';")
        cur.execute("UPDATE chat_rooms SET room_kind='autoscaler' WHERE created_by='autoscaler';")
        cur.execute(
            """
            UPDATE chat_rooms r
               SET room_kind='custom'
              FROM custom_rooms cr
             WHERE cr.name = r.name;
            """
        )
        if official_names_lower:
            cur.execute(
                """
                UPDATE chat_rooms r
                   SET room_kind='official',
                       created_by='system'
                 WHERE LOWER(r.name) = ANY(%s)
                   AND NOT EXISTS (SELECT 1 FROM custom_rooms cr WHERE cr.name = r.name);
                """,
                (official_names_lower,),
            )
    _commit_schema_conn(conn, commit)


def ensure_users_key_columns(conn=None, *, commit: bool = True):
    """
    If the 'users' table already exists but lacks:
      • a column 'password' (only 'password_hash' exists), rename password_hash → password,
      • columns 'public_key' and 'encrypted_private_key', add them now.
    """
    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        # 1) Rename password_hash → password if needed
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'users'
               AND column_name = 'password_hash';
            """
        )
        if cur.fetchone() is not None:
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'users'
                   AND column_name = 'password';
                """
            )
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE users RENAME COLUMN password_hash TO password;")

        # 2) Add public_key if missing
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'users'
               AND column_name = 'public_key';
            """
        )
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE users ADD COLUMN public_key TEXT;")

        # 3) Add encrypted_private_key if missing
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'users'
               AND column_name = 'encrypted_private_key';
            """
        )
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE users ADD COLUMN encrypted_private_key TEXT;")

    _commit_schema_conn(conn, commit)


def ensure_users_security_columns(conn=None, *, commit: bool = True) -> None:
    """Ensure users security/profile columns needed by auth flows exist.

    Older EchoChat databases can predate SMS 2FA and some profile fields. Keep
    this idempotent so startup migrations and setup-created databases can
    converge before login/account-security queries run.
    """

    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        def _ensure_user_col(col: str, ddl: str) -> None:
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'users'
                   AND column_name = %s;
                """,
                (col,),
            )
            if cur.fetchone() is None:
                logging.warning("Adding users.%s column", col)
                cur.execute(ddl)

        _ensure_user_col("phone", "ALTER TABLE users ADD COLUMN phone TEXT;")
        _ensure_user_col("email_hash", "ALTER TABLE users ADD COLUMN email_hash TEXT;")
        _ensure_user_col("email_encrypted", "ALTER TABLE users ADD COLUMN email_encrypted TEXT;")
        try:
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_hash_unique ON users (email_hash) WHERE email_hash IS NOT NULL AND BTRIM(email_hash) <> '';" )
        except Exception as e:
            logging.warning("Could not create users_email_hash_unique index (continuing): %s", e)
        _ensure_user_col(
            "two_factor_enabled",
            "ALTER TABLE users ADD COLUMN two_factor_enabled BOOLEAN NOT NULL DEFAULT FALSE;",
        )
        _ensure_user_col("two_factor_secret", "ALTER TABLE users ADD COLUMN two_factor_secret TEXT;")
        _ensure_user_col("avatar_url", "ALTER TABLE users ADD COLUMN avatar_url TEXT;")
        _ensure_user_col(
            "created_at",
            "ALTER TABLE users ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
        )

    _commit_schema_conn(conn, commit)


def ensure_account_recovery_schema(conn=None, *, commit: bool = True) -> None:
    """Ensure account recovery fields and tables exist.

    This adds low-entropy recovery support (4-digit PIN) in a *safe* way:
      - the PIN is stored hashed (PBKDF2 via security.hash_password)
      - failed attempts are tracked and can be locked out

    It also creates a password_reset_tokens table for high-entropy, single-use,
    expiring reset tokens.

    Safe to call repeatedly.
    """

    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        # ── users recovery columns ───────────────────────────────────────
        def _ensure_user_col(col: str, ddl: str) -> None:
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'users'
                   AND column_name = %s;
                """,
                (col,),
            )
            if cur.fetchone() is None:
                logging.warning("Adding users.%s column", col)
                cur.execute(ddl)

        _ensure_user_col("recovery_pin_hash", "ALTER TABLE users ADD COLUMN recovery_pin_hash TEXT;")
        _ensure_user_col(
            "recovery_pin_set_at",
            "ALTER TABLE users ADD COLUMN recovery_pin_set_at TIMESTAMP WITH TIME ZONE;",
        )
        _ensure_user_col(
            "recovery_failed_attempts",
            "ALTER TABLE users ADD COLUMN recovery_failed_attempts INTEGER NOT NULL DEFAULT 0;",
        )
        _ensure_user_col(
            "recovery_locked_until",
            "ALTER TABLE users ADD COLUMN recovery_locked_until TIMESTAMP WITH TIME ZONE;",
        )

        # Optional but useful: a case-insensitive unique index for email.
        #
        # If an existing DB has duplicates, this will fail.
        # IMPORTANT: In PostgreSQL, a failed statement aborts the whole transaction
        # until it is rolled back. Use a SAVEPOINT so we can continue cleanly.
        try:
            cur.execute(
                """
                SELECT COUNT(*)
                  FROM (
                        SELECT LOWER(email) AS e
                          FROM users
                         WHERE email IS NOT NULL AND BTRIM(email) <> ''
                         GROUP BY LOWER(email)
                        HAVING COUNT(*) > 1
                       ) d;
                """
            )
            dup_cnt = int((cur.fetchone() or [0])[0])
        except Exception:
            dup_cnt = 0

        if dup_cnt > 0:
            logging.warning(
                "Email uniqueness index not created: found %s duplicate email(s). ",
                dup_cnt,
            )
            logging.warning(
                "Fix duplicates then restart. Helpful script: tools/dedupe_duplicate_emails.py (use --dry-run first)."
            )
        else:
            try:
                cur.execute("SAVEPOINT sp_users_email_unique_ci;")
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique_ci
                    ON users (LOWER(email))
                    WHERE email IS NOT NULL AND BTRIM(email) <> '';
                    """
                )
                cur.execute("RELEASE SAVEPOINT sp_users_email_unique_ci;")
            except Exception as e:
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_users_email_unique_ci;")
                    cur.execute("RELEASE SAVEPOINT sp_users_email_unique_ci;")
                except Exception:
                    # If rollback-to-savepoint fails for any reason, we fall back
                    # to continuing and letting the outer commit/rollback handle it.
                    pass
                logging.warning("Could not create users_email_unique_ci index (continuing): %s", e)

        try:
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_hash_unique ON users (email_hash) WHERE email_hash IS NOT NULL AND BTRIM(email_hash) <> '';" )
        except Exception as e:
            logging.warning("Could not create users_email_hash_unique index (continuing): %s", e)

        # ── password_reset_tokens table ──────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id          SERIAL PRIMARY KEY,
                username    TEXT NOT NULL,
                token_hash  TEXT UNIQUE NOT NULL,
                created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,
                used_at     TIMESTAMP WITH TIME ZONE,
                request_ip  TEXT,
                user_agent  TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS prt_username_created_idx
            ON password_reset_tokens (username, created_at);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS prt_expires_used_idx
            ON password_reset_tokens (expires_at, used_at);
            """
        )

    _commit_schema_conn(conn, commit)


def ensure_auth_session_schema(conn=None, *, commit: bool = True) -> None:
    """Ensure auth session tracking schema exists.

    - Adds auth_sessions table (one row per device/session)
    - Adds auth_tokens.session_id column (ties tokens to a session)
    Safe to call repeatedly.
    """
    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        # auth_sessions table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id  TEXT PRIMARY KEY,
                username    TEXT NOT NULL,
                created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP WITH TIME ZONE,
                last_activity_at TIMESTAMP WITH TIME ZONE,
                revoked_at  TIMESTAMP WITH TIME ZONE,
                revoked_reason TEXT,
                user_agent  TEXT,
                ip_address  TEXT,
                auth_version INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_username
            ON auth_sessions(username);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_revoked
            ON auth_sessions(revoked_at);
            """
        )

        # auth_sessions.last_activity_at column (used for idle logout)
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'auth_sessions'
               AND column_name = 'last_activity_at';
            """
        )
        if cur.fetchone() is None:
            logging.warning("Adding auth_sessions.last_activity_at column")
            cur.execute("ALTER TABLE auth_sessions ADD COLUMN last_activity_at TIMESTAMPTZ NULL;")
        # Backfill nulls (older DBs) to avoid treating existing sessions as instantly idle
        cur.execute(
            """
            UPDATE auth_sessions
               SET last_activity_at = COALESCE(last_seen_at, created_at, NOW())
             WHERE last_activity_at IS NULL;
            """
        )

        cur.execute("ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS auth_version INTEGER NOT NULL DEFAULT 0;")
        cur.execute("SELECT to_regclass('public.auth_tokens');")
        _auth_tokens_exists = bool((cur.fetchone() or [None])[0])
        if _auth_tokens_exists:
            cur.execute("ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS auth_version INTEGER NOT NULL DEFAULT 0;")

        # auth_tokens.session_id column
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'auth_tokens'
               AND column_name = 'session_id';
            """
        )
        if _auth_tokens_exists and cur.fetchone() is None:
            logging.warning("Adding auth_tokens.session_id column")
            cur.execute("ALTER TABLE auth_tokens ADD COLUMN session_id TEXT;")

        if _auth_tokens_exists:
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_session
                ON auth_tokens(session_id);
                """
            )
    _commit_schema_conn(conn, commit)


def ensure_user_verified_column(conn=None, *, commit: bool = True):
    """Ensure users.is_verified exists.

    EchoChat doesn't yet have an explicit email verification workflow, but the
    room browser requires a server-side "verified" gate for creating custom rooms.
    We default existing users to TRUE for backward compatibility.
    """
    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name='users'
               AND column_name='is_verified';
            """
        )
        if cur.fetchone() is None:
            logging.warning("Adding users.is_verified column")
            cur.execute("ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT TRUE;")
    _commit_schema_conn(conn, commit)


def ensure_custom_rooms_schema(conn=None, *, commit: bool = True):
    """Create/patch schema for custom rooms + private invites."""
    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_rooms (
                name           TEXT PRIMARY KEY,
                category       TEXT NOT NULL,
                subcategory    TEXT NOT NULL,
                created_by     TEXT NOT NULL,
                is_private     BOOLEAN NOT NULL DEFAULT FALSE,
                is_18_plus     BOOLEAN NOT NULL DEFAULT FALSE,
                is_nsfw        BOOLEAN NOT NULL DEFAULT FALSE,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_custom_rooms_cat
            ON custom_rooms(category, subcategory);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_custom_rooms_last_active
            ON custom_rooms(last_active_at);
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_room_invites (
                id           SERIAL PRIMARY KEY,
                room_name    TEXT NOT NULL,
                invited_user TEXT NOT NULL,
                invited_by   TEXT NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(room_name, invited_user)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_custom_room_invites_user
            ON custom_room_invites(invited_user);
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_room_members (
                id           SERIAL PRIMARY KEY,
                room_name    TEXT NOT NULL,
                member_user  TEXT NOT NULL,
                invited_by   TEXT,
                role         TEXT NOT NULL DEFAULT 'member',
                joined_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(room_name, member_user)
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE custom_room_members
            ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'member';
            """
        )
        cur.execute(
            """
            UPDATE custom_room_members
               SET role = 'member'
             WHERE role IS NULL OR TRIM(role) = '';
            """
        )
        cur.execute(
            """
            UPDATE custom_room_members m
               SET role = 'owner'
              FROM custom_rooms cr
             WHERE LOWER(BTRIM(cr.name)) = LOWER(BTRIM(m.room_name))
               AND LOWER(BTRIM(cr.created_by)) = LOWER(BTRIM(m.member_user))
               AND LOWER(COALESCE(m.role, '')) <> 'owner';
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_custom_room_members_user
            ON custom_room_members(member_user);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_custom_room_members_room
            ON custom_room_members(room_name);
            """
        )

        # Generic room invite notifications (for public/official rooms)
        # NOTE: these invites do *not* grant access control; they are used for UX only.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS room_invites (
                id           SERIAL PRIMARY KEY,
                room_name    TEXT NOT NULL,
                invited_user TEXT NOT NULL,
                invited_by   TEXT NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(room_name, invited_user)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_room_invites_user
            ON room_invites(invited_user);
            """
        )
    _commit_schema_conn(conn, commit)


def ensure_room_message_expiry_schema(conn=None, *, commit: bool = True) -> None:
    """Create/patch schema for per-room message expiry + supporting indexes."""
    conn = _schema_conn(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS room_message_expiry (
                room           TEXT PRIMARY KEY,
                expiry_seconds INTEGER NOT NULL,
                set_by         TEXT,
                set_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # History lookups (room history) should be fast.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room, id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_ts ON messages(room, timestamp);")

        # Reaction fanout aggregates should be fast.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_message_reactions_message_id ON message_reactions(message_id);")
    _commit_schema_conn(conn, commit)






# ----------------------------------------------------------------------
# Full schema creation (all tables adapted for PostgreSQL)
# ----------------------------------------------------------------------


def _create_full_schema(conn=None, *, commit: bool = True):
    """
    Create all tables from the original SQLite schema, adapted for PostgreSQL.
    Uses SERIAL for auto-increment IDs, TIMESTAMP WITH TIME ZONE for date columns, and ON CONFLICT where needed.
    """
    own_conn = conn is None
    from_pool = False
    if own_conn:
        conn, from_pool = _acquire_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                /* ── Core user & messaging tables ─────────────────────────── */
                CREATE TABLE IF NOT EXISTS users (
                    id                    SERIAL PRIMARY KEY,
                    username              TEXT UNIQUE NOT NULL,
                    password              TEXT NOT NULL,
                    email                 TEXT,
                    email_hash            TEXT,
                    email_encrypted       TEXT,
                    phone                 TEXT,
                    address               TEXT,
                    age                   INTEGER,
                    age_visibility        TEXT NOT NULL DEFAULT 'friends',
                    is_admin              BOOLEAN NOT NULL DEFAULT FALSE,
                    last_seen             TIMESTAMP WITH TIME ZONE,
                    status                TEXT DEFAULT 'active',
                    relationship_status   TEXT,
                    relationship_visibility TEXT NOT NULL DEFAULT 'friends',
                    location_text         TEXT,
                    location_visibility   TEXT NOT NULL DEFAULT 'friends',
                    interests             TEXT,
                    favorite_music        TEXT,
                    favorite_movies       TEXT,
                    favorite_games        TEXT,
                    website_url           TEXT,
                    banner_url            TEXT,
                    profile_accent        TEXT,
                    share_recent_rooms    BOOLEAN NOT NULL DEFAULT FALSE,
                    recent_rooms_visibility TEXT NOT NULL DEFAULT 'friends',
                    profile_post_default_visibility TEXT NOT NULL DEFAULT 'friends',
                    presence_status       TEXT NOT NULL DEFAULT 'online',
                    two_factor_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
                    two_factor_secret     TEXT,
                    custom_status         TEXT,
                    recovery_pin_hash     TEXT,
                    recovery_pin_set_at   TIMESTAMP WITH TIME ZONE,
                    recovery_failed_attempts INTEGER NOT NULL DEFAULT 0,
                    recovery_locked_until TIMESTAMP WITH TIME ZONE,
                    bio                   TEXT,
                    avatar_url            TEXT,
                    public_key            TEXT,
                    encrypted_private_key TEXT,
                    online                BOOLEAN DEFAULT FALSE,
                    is_verified           BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at            TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    auth_version          INTEGER NOT NULL DEFAULT 0,
                    password_changed_at   TIMESTAMP WITH TIME ZONE,
                    auth_changed_at       TIMESTAMP WITH TIME ZONE
                );

                CREATE TABLE IF NOT EXISTS user_recent_rooms (
                    id         SERIAL PRIMARY KEY,
                    username   TEXT NOT NULL,
                    room_name  TEXT NOT NULL,
                    joined_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_user_recent_rooms_username_joined ON user_recent_rooms(username, joined_at DESC);
                CREATE INDEX IF NOT EXISTS idx_user_recent_rooms_username_room ON user_recent_rooms(username, room_name);

                CREATE TABLE IF NOT EXISTS profile_posts (
                    id              SERIAL PRIMARY KEY,
                    author_username TEXT NOT NULL,
                    body            TEXT,
                    visibility      TEXT NOT NULL DEFAULT 'friends',
                    image_url       TEXT,
                    gif_url         TEXT,
                    link_url        TEXT,
                    is_pinned       BOOLEAN NOT NULL DEFAULT FALSE,
                    is_featured     BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    deleted_at      TIMESTAMP WITH TIME ZONE,
                    edited_at       TIMESTAMP WITH TIME ZONE,
                    edit_count      INTEGER NOT NULL DEFAULT 0,
                    moderated_by    TEXT,
                    moderated_reason TEXT,
                    moderated_at    TIMESTAMP WITH TIME ZONE
                );
                CREATE INDEX IF NOT EXISTS idx_profile_posts_author_created ON profile_posts(author_username, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_profile_posts_author_featured ON profile_posts(author_username, is_featured, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_profile_posts_author_pinned ON profile_posts(author_username, is_pinned, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_profile_posts_moderation ON profile_posts(deleted_at, moderated_at, updated_at DESC);

                CREATE TABLE IF NOT EXISTS profile_post_reactions (
                    post_id    INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
                    username   TEXT NOT NULL,
                    reaction   TEXT NOT NULL DEFAULT 'like',
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (post_id, username, reaction)
                );
                CREATE INDEX IF NOT EXISTS idx_profile_post_reactions_user ON profile_post_reactions(username, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_profile_post_reactions_post ON profile_post_reactions(post_id, reaction);

                CREATE TABLE IF NOT EXISTS profile_post_comments (
                    id              SERIAL PRIMARY KEY,
                    post_id         INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
                    author_username TEXT NOT NULL,
                    body            TEXT NOT NULL,
                    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    deleted_at      TIMESTAMP WITH TIME ZONE,
                    deleted_by      TEXT,
                    deleted_reason  TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_profile_post_comments_post_created ON profile_post_comments(post_id, created_at DESC, id DESC) WHERE deleted_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_profile_post_comments_author_created ON profile_post_comments(author_username, created_at DESC) WHERE deleted_at IS NULL;

                CREATE TABLE IF NOT EXISTS profile_post_reports (
                    id                SERIAL PRIMARY KEY,
                    reporter_username TEXT NOT NULL,
                    post_id           INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
                    comment_id        INTEGER REFERENCES profile_post_comments(id) ON DELETE SET NULL,
                    target_username   TEXT NOT NULL,
                    reason            TEXT NOT NULL DEFAULT 'other',
                    details           TEXT,
                    status            TEXT NOT NULL DEFAULT 'open',
                    reviewed_by       TEXT,
                    reviewed_at       TIMESTAMP WITH TIME ZONE,
                    action_taken      TEXT,
                    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_profile_post_reports_status_created ON profile_post_reports(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_profile_post_reports_post_created ON profile_post_reports(post_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_profile_post_reports_target_created ON profile_post_reports(target_username, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_profile_post_reports_open_reporter_target ON profile_post_reports(reporter_username, post_id, COALESCE(comment_id, 0)) WHERE status = 'open';

                CREATE TABLE IF NOT EXISTS user_profile_badges (
                    id          SERIAL PRIMARY KEY,
                    username    TEXT NOT NULL,
                    badge_key   TEXT NOT NULL,
                    label       TEXT NOT NULL,
                    assigned_by TEXT,
                    reason      TEXT,
                    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(username, badge_key)
                );
                CREATE INDEX IF NOT EXISTS idx_user_profile_badges_username ON user_profile_badges(username, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_user_profile_badges_key ON user_profile_badges(badge_key);

                CREATE TABLE IF NOT EXISTS user_profile_notification_settings (
                    username              TEXT PRIMARY KEY,
                    notify_likes          BOOLEAN NOT NULL DEFAULT TRUE,
                    notify_comments       BOOLEAN NOT NULL DEFAULT TRUE,
                    notify_admin_notices  BOOLEAN NOT NULL DEFAULT TRUE,
                    notify_report_updates BOOLEAN NOT NULL DEFAULT TRUE,
                    notify_profile_views  BOOLEAN NOT NULL DEFAULT FALSE,
                    notify_friend_posts   BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_profile_notification_settings_updated ON user_profile_notification_settings(updated_at DESC);

                /* ── Account recovery (password reset tokens) ─────────── */
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id          SERIAL PRIMARY KEY,
                    username    TEXT NOT NULL,
                    token_hash  TEXT UNIQUE NOT NULL,
                    created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,
                    used_at     TIMESTAMP WITH TIME ZONE,
                    request_ip  TEXT,
                    user_agent  TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id            SERIAL PRIMARY KEY,
                    room          TEXT,
                    sender        TEXT NOT NULL,
                    receiver      TEXT,
                    message       TEXT NOT NULL,
                    timestamp     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    is_encrypted  BOOLEAN DEFAULT FALSE,
                    is_read       BOOLEAN DEFAULT FALSE,
                    is_edited     BOOLEAN DEFAULT FALSE,
                    is_deleted    BOOLEAN DEFAULT FALSE
                );

                CREATE TABLE IF NOT EXISTS offline_messages (
                    id            SERIAL PRIMARY KEY,
                    sender        TEXT NOT NULL,
                    receiver      TEXT NOT NULL,
                    message       TEXT NOT NULL,
                    timestamp     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    delivered     BOOLEAN DEFAULT FALSE
                );

                CREATE TABLE IF NOT EXISTS pending_messages (
                    id                  SERIAL PRIMARY KEY,
                    receiver_username   TEXT NOT NULL,
                    sender_username     TEXT NOT NULL,
                    message             TEXT NOT NULL,
                    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS private_messages (
                    id            SERIAL PRIMARY KEY,
                    sender        TEXT NOT NULL,
                    recipient     TEXT NOT NULL,
                    message       TEXT NOT NULL,
                    timestamp     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                /* ── Social tables ─────────────────────────────────────────── */
                CREATE TABLE IF NOT EXISTS friend_requests (
                    id              SERIAL PRIMARY KEY,
                    from_user       TEXT NOT NULL,
                    to_user         TEXT NOT NULL,
                    request_status  TEXT DEFAULT 'pending',
                    timestamp       TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS friends (
                    id            SERIAL PRIMARY KEY,
                    user_id       INTEGER NOT NULL,
                    friend_id     INTEGER NOT NULL,
                    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, friend_id)
                );

                CREATE TABLE IF NOT EXISTS blocked_users (
                    id            SERIAL PRIMARY KEY,
                    user_id       INTEGER NOT NULL,
                    blocked_id    INTEGER NOT NULL,
                    UNIQUE(user_id, blocked_id)
                );

                CREATE TABLE IF NOT EXISTS blocks (
                    id           SERIAL PRIMARY KEY,
                    blocker      TEXT NOT NULL,
                    blocked      TEXT NOT NULL,
                    timestamp    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                /* ── Encrypted DM file transfers (ciphertext-only) ─────────── */
                /*
                   The server never decrypts DM files.

                   Client uploads an AES-GCM ciphertext blob plus:
                     - iv_b64
                     - ek_to_b64   (AES key wrapped to recipient RSA-OAEP key)
                     - ek_from_b64 (AES key wrapped to sender RSA-OAEP key)

                   A DM "file message" is sent separately (as encrypted JSON)
                   containing file_id + display metadata.
                */
                CREATE TABLE IF NOT EXISTS dm_files (
                    file_id        TEXT PRIMARY KEY,
                    sender         TEXT NOT NULL,
                    receiver       TEXT NOT NULL,
                    original_name  TEXT NOT NULL,
                    mime_type      TEXT,
                    file_size      INTEGER NOT NULL,
                    sha256         TEXT,
                    storage_path   TEXT NOT NULL,
                    iv_b64         TEXT NOT NULL,
                    ek_to_b64      TEXT NOT NULL,
                    ek_from_b64    TEXT NOT NULL,
                    revoked        BOOLEAN DEFAULT FALSE,
                    uploaded_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_dm_files_receiver ON dm_files(receiver);
                CREATE INDEX IF NOT EXISTS idx_dm_files_sender   ON dm_files(sender);

                
                

/* ── Attachments & reactions ───────────────────────────────── */
                CREATE TABLE IF NOT EXISTS file_attachments (
                    id            SERIAL PRIMARY KEY,
                    message_id    INTEGER,
                    file_path     TEXT NOT NULL,
                    file_type     TEXT,
                    file_size     INTEGER,
                    uploaded_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS message_reactions (
                    id            SERIAL PRIMARY KEY,
                    message_id    INTEGER NOT NULL,
                    username      TEXT NOT NULL,
                    emoji         TEXT NOT NULL,
                    reacted_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(message_id, username, emoji),
                    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS message_reads (
                    id            SERIAL PRIMARY KEY,
                    message_id    INTEGER NOT NULL,
                    username      TEXT NOT NULL,
                    read_at       TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(message_id, username),
                    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
                );

                /* ── Notifications & settings ─────────────────────────────── */
                CREATE TABLE IF NOT EXISTS notifications (
                    id            SERIAL PRIMARY KEY,
                    user_id       INTEGER NOT NULL,
                    notification  TEXT NOT NULL,
                    type          TEXT,
                    is_read       BOOLEAN DEFAULT FALSE,
                    timestamp     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_profile_notifications_user_unread ON notifications(user_id, is_read, timestamp DESC) WHERE type LIKE 'profile_post_%';

                CREATE TABLE IF NOT EXISTS chat_settings (
                    id              SERIAL PRIMARY KEY,
                    user_id         INTEGER NOT NULL,
                    setting_name    TEXT NOT NULL,
                    setting_value   TEXT,
                    UNIQUE(user_id, setting_name)
                );

                /* ── Group / room tables ───────────────────────────────────── */
                CREATE TABLE IF NOT EXISTS groups (
                    id                SERIAL PRIMARY KEY,
                    group_name        TEXT NOT NULL,
                    group_description TEXT,
                    created_by        INTEGER NOT NULL,
                    created_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS group_members (
                    id            SERIAL PRIMARY KEY,
                    group_id      INTEGER NOT NULL,
                    user_id       INTEGER NOT NULL,
                    role          TEXT DEFAULT 'member',
                    joined_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(group_id, user_id),
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS group_mutes (
                    group_id    INTEGER NOT NULL,
                    username    TEXT NOT NULL,
                    muted_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, username)
                );

                CREATE TABLE IF NOT EXISTS group_invites (
                    id          SERIAL PRIMARY KEY,
                    group_id    INTEGER NOT NULL,
                    from_user   TEXT NOT NULL,
                    to_user     TEXT NOT NULL,
                    sent_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    status      TEXT DEFAULT 'pending',
                    UNIQUE(group_id, to_user),
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS group_pins (
                    group_id    INTEGER PRIMARY KEY,
                    pinned_by   TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    pinned_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                );

                

                /* ── Encrypted Group file storage (NOT publicly served) ────── */
                CREATE TABLE IF NOT EXISTS group_files (
                    file_id        TEXT PRIMARY KEY,
                    group_id       INTEGER NOT NULL,
                    sender         TEXT NOT NULL,
                    original_name  TEXT NOT NULL,
                    mime_type      TEXT,
                    file_size      BIGINT NOT NULL,
                    sha256         TEXT,
                    storage_path   TEXT NOT NULL,
                    iv_b64         TEXT NOT NULL,
                    ek_map_json    TEXT NOT NULL,
                    revoked        BOOLEAN DEFAULT FALSE,
                    uploaded_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_group_files_group  ON group_files(group_id);
                CREATE INDEX IF NOT EXISTS idx_group_files_sender ON group_files(sender);

                /* ── Moderation & audit ───────────────────────────────────── */
                CREATE TABLE IF NOT EXISTS user_sanctions (
                    id            SERIAL PRIMARY KEY,
                    username      TEXT NOT NULL,
                    sanction_type TEXT NOT NULL,
                    reason        TEXT,
                    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    expires_at    TIMESTAMP WITH TIME ZONE
                );

                CREATE INDEX IF NOT EXISTS idx_user_sanctions_user_type_active
                    ON user_sanctions (LOWER(username), sanction_type, expires_at, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_user_sanctions_type_active
                    ON user_sanctions (sanction_type, expires_at, created_at DESC);

                CREATE TABLE IF NOT EXISTS audit_log (
                    id            SERIAL PRIMARY KEY,
                    actor         TEXT NOT NULL,
                    action        TEXT NOT NULL,
                    target        TEXT,
                    timestamp     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    details       TEXT
                );

                /* ── RBAC tables ──────────────────────────────────────────── */
                CREATE TABLE IF NOT EXISTS roles (
                    id      SERIAL PRIMARY KEY,
                    name    TEXT UNIQUE NOT NULL
                );

                CREATE TABLE IF NOT EXISTS permissions (
                    id      SERIAL PRIMARY KEY,
                    name    TEXT UNIQUE NOT NULL
                );

                CREATE TABLE IF NOT EXISTS role_permissions (
                    role_id       INTEGER NOT NULL,
                    permission_id INTEGER NOT NULL,
                    PRIMARY KEY (role_id, permission_id),
                    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
                    FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id       INTEGER NOT NULL,
                    role_id       INTEGER NOT NULL,
                    PRIMARY KEY (user_id, role_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
                );

                /* ── Admin / moderation helper tables ─────────────────── */
                CREATE TABLE IF NOT EXISTS room_locks (
                    room       TEXT PRIMARY KEY,
                    locked     BOOLEAN NOT NULL DEFAULT TRUE,
                    locked_by  TEXT,
                    locked_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    reason     TEXT
                );

                CREATE TABLE IF NOT EXISTS room_readonly (
                    room      TEXT PRIMARY KEY,
                    readonly  BOOLEAN NOT NULL DEFAULT FALSE,
                    set_by    TEXT,
                    set_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS room_slowmode (
                    room      TEXT PRIMARY KEY,
                    seconds   INTEGER NOT NULL DEFAULT 0,
                    set_by    TEXT,
                    set_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_quotas (
                    username           TEXT PRIMARY KEY,
                    messages_per_hour  INTEGER NOT NULL DEFAULT 60,
                    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                /* ── Auth token store (refresh rotation + revocation) ────── */
                /*
                   We persist *issued* JWT JTIs so we can:
                    - rotate refresh tokens on every refresh (single-use refresh)
                    - detect refresh token replay/reuse
                    - revoke tokens on logout / password change / admin action

                   Notes:
                    - Access tokens are short-lived but still stored so logout can
                      revoke them immediately.
                    - A refresh token is considered ACTIVE only if:
                        revoked_at IS NULL AND replaced_by IS NULL
                */
                
                /* ── Auth sessions (device/session tracking) ─────────── */
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_id  TEXT PRIMARY KEY,
                    username    TEXT NOT NULL,
                    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
	                    last_seen_at TIMESTAMP WITH TIME ZONE,
	                    last_activity_at TIMESTAMP WITH TIME ZONE,
                    revoked_at  TIMESTAMP WITH TIME ZONE,
                    revoked_reason TEXT,
                    user_agent  TEXT,
                    ip_address  TEXT,
                    auth_version INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_username ON auth_sessions(username);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_revoked  ON auth_sessions(revoked_at);

CREATE TABLE IF NOT EXISTS auth_tokens (
                    jti         TEXT PRIMARY KEY,
                    username    TEXT NOT NULL,
                    session_id  TEXT,
                    token_type  TEXT NOT NULL,
                    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at  TIMESTAMP WITH TIME ZONE,
                    revoked_at  TIMESTAMP WITH TIME ZONE,
                    replaced_by TEXT,
                    last_used_at TIMESTAMP WITH TIME ZONE,
                    user_agent  TEXT,
                    ip_address  TEXT,
                    auth_version INTEGER NOT NULL DEFAULT 0
                );

                /*
                   Back-compat / partial-schema safety:
                   If an older DB already has an auth_tokens table without
                   the new session_id column, CREATE TABLE IF NOT EXISTS is a
                   no-op. Ensure the column exists before creating indexes or
                   writing tokens.
                */
                ALTER TABLE auth_tokens
                    ADD COLUMN IF NOT EXISTS session_id TEXT;

                CREATE INDEX IF NOT EXISTS idx_auth_tokens_username ON auth_tokens(username);
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_expires  ON auth_tokens(expires_at);
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_session  ON auth_tokens(session_id);

                /* chat_rooms handled in ensure_chat_rooms_table() */
                """
            )
        _commit_schema_conn(conn, commit)
    finally:
        if own_conn:
            _release_conn(conn, from_pool)


def _seed_roles_permissions(conn=None, *, commit: bool = True):
    """
    Insert default roles and permissions, then map them.
    """
    own_conn = conn is None
    from_pool = False
    if own_conn:
        conn, from_pool = _acquire_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            roles = ["admin", "moderator", "viewer"]
            perms = [
                "admin:basic", "admin:settings", "admin:audit", "admin:test_lab",
                "admin:create_user", "admin:delete_user", "admin:set_recovery_pin",
                "admin:set_user_status", "admin:set_user_quota", "admin:revoke_2fa",
                "admin:broadcast", "admin:assign_role", "admin:manage_roles",
                "admin:ban_ip", "admin:reset_password", "admin:logout_user",
                "moderation:mute_user", "moderation:kick_user", "moderation:ban_room",
                "moderation:suspend_user", "moderation:shadowban",
                "room:lock", "room:readonly", "room:clear", "room:delete",
                "profile:moderate",
                "user:delete_self", "user:edit_profile"
            ]

            for r in roles:
                cur.execute(
                    "INSERT INTO roles (name) VALUES (%s) ON CONFLICT (name) DO NOTHING;",
                    (r,)
                )

            for p in perms:
                cur.execute(
                    "INSERT INTO permissions (name) VALUES (%s) ON CONFLICT (name) DO NOTHING;",
                    (p,)
                )

            cur.execute("SELECT id, name FROM roles;")
            role_map = {row["name"]: row["id"] for row in cur.fetchall()}

            cur.execute("SELECT id, name FROM permissions;")
            perm_map = {row["name"]: row["id"] for row in cur.fetchall()}

            def map_role_perm(role_name: str, perm_name: str):
                cur.execute(
                    """
                    INSERT INTO role_permissions (role_id, permission_id)
                    VALUES (%s, %s)
                    ON CONFLICT (role_id, permission_id) DO NOTHING;
                    """,
                    (role_map[role_name], perm_map[perm_name])
                )

            for p in perms:
                map_role_perm("admin", p)

            for p in ("moderation:mute_user", "moderation:kick_user",
                      "moderation:ban_room", "room:readonly", "room:clear",
                      "profile:moderate"):
                map_role_perm("moderator", p)

            map_role_perm("viewer", "user:edit_profile")

        _commit_schema_conn(conn, commit)
    finally:
        if own_conn:
            _release_conn(conn, from_pool)


# ----------------------------------------------------------------------
# Database initialization sequence
# ----------------------------------------------------------------------


def _legacy_bootstrap_schema(conn=None, *, commit: bool = True):
    """Create or patch the current schema using the pre-migrations bootstrap.

    This remains the baseline implementation for migration 0001 so existing
    databases and fresh databases can converge on a tracked schema version.
    """
    conn = _schema_conn(conn)
    # 1) Create all tables if missing
    _create_full_schema(conn, commit=False)

    # 2) Patch any missing columns/tables on the same migration/setup connection
    ensure_online_column(conn, commit=False)
    ensure_presence_columns(conn, commit=False)
    ensure_users_profile_columns(conn, commit=False)
    ensure_custom_rooms_schema(conn, commit=False)
    ensure_chat_rooms_table(conn, commit=False)
    ensure_users_key_columns(conn, commit=False)
    ensure_users_security_columns(conn, commit=False)
    ensure_user_verified_column(conn, commit=False)
    ensure_account_recovery_schema(conn, commit=False)
    ensure_auth_session_schema(conn, commit=False)
    ensure_room_message_expiry_schema(conn, commit=False)

    # 3) RBAC seeding and room preload
    _seed_roles_permissions(conn, commit=False)
    sync_chat_room_kinds(conn, commit=False)
    load_rooms_from_json(conn, commit=False)
    _commit_schema_conn(conn, commit)




# ----------------------------------------------------------------------
# Auth token store helpers (refresh rotation + revocation)
# ----------------------------------------------------------------------



# ----------------------------------------------------------------------
# Public helper queries
# ----------------------------------------------------------------------
