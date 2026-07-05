#!/usr/bin/env python3
"""User key, auth token, and session database helpers."""

from __future__ import annotations

import base64
import logging
import os
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from psycopg2.extras import RealDictCursor

from db.core import get_db
from profile_defaults import build_default_avatar_url
from sensitive_fields_crypto import encrypt_sensitive_field
from email_at_rest import hash_email, prepare_email_storage
from account_status import account_status_allows_auth, get_effective_account_status

_E2EE_KEYBLOB_AAD = b"echochat:keyblob:v2"

def _pbkdf2_key(password: str, salt: bytes, iterations: int = 390_000, length: int = 32) -> bytes:
    """PBKDF2-HMAC-SHA256 key derivation (used for client-compatible key wrapping)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        iterations=iterations,
        backend=default_backend(),
    )
    return kdf.derive(password.encode("utf-8"))

def _encrypt_private_key_v2(private_pem_bytes: bytes, raw_password: str) -> str:
    """Encrypt a PKCS8 private key PEM using PBKDF2->AES-256-GCM (client-compatible).

    Format:
      v2:<salt_b64>:<nonce_b64>:<cipher_b64>

    Where cipher_b64 contains ciphertext||tag (standard AESGCM output).
    """
    salt = os.urandom(16)
    nonce = os.urandom(12)  # AES-GCM recommended nonce size
    key = _pbkdf2_key(raw_password, salt, iterations=390_000, length=32)
    aes = AESGCM(key)
    ct = aes.encrypt(nonce, private_pem_bytes, _E2EE_KEYBLOB_AAD)
    return "v2:" + ":".join([
        base64.b64encode(salt).decode("utf-8"),
        base64.b64encode(nonce).decode("utf-8"),
        base64.b64encode(ct).decode("utf-8"),
    ])

def _decrypt_private_key_blob(raw_password: str, encrypted_blob: str) -> bytes:
    """Decrypt user encrypted_private_key.

    Supports:
      - v2: PBKDF2->AES-256-GCM (v2:<salt>:<nonce>:<cipher>)
      - legacy v1: PBKDF2->XOR (salt:cipher)  (no prefix)
    """
    if not encrypted_blob:
        raise ValueError("empty key blob")

    # v2 (AES-GCM)
    if encrypted_blob.startswith("v2:"):
        parts = encrypted_blob.split(":")
        if len(parts) != 4:
            raise ValueError("invalid v2 key blob format")
        _, salt_b64, nonce_b64, cipher_b64 = parts
        salt = base64.b64decode(salt_b64)
        nonce = base64.b64decode(nonce_b64)
        ct = base64.b64decode(cipher_b64)
        key = _pbkdf2_key(raw_password, salt, iterations=390_000, length=32)
        aes = AESGCM(key)
        return aes.decrypt(nonce, ct, _E2EE_KEYBLOB_AAD)

    # legacy v1 (XOR)
    salt_b64, cipher_b64 = encrypted_blob.split(":", 1)
    salt = base64.b64decode(salt_b64)
    encrypted_priv = base64.b64decode(cipher_b64)

    derived_key = _pbkdf2_key(raw_password, salt, iterations=390_000, length=32)
    key_repeated = derived_key * (len(encrypted_priv) // len(derived_key) + 1)
    plain = bytes(a ^ b for a, b in zip(encrypted_priv, key_repeated))
    return plain

def _generate_and_encrypt_rsa_keypair(raw_password: str):
    """Generate a 2048-bit RSA keypair and encrypt the private key for browser unlock.

    Returns:
      (public_pem_str, encrypted_private_key_str)

    encrypted_private_key_str is versioned:
      - v2:<salt_b64>:<nonce_b64>:<cipher_b64>  (PBKDF2->AES-256-GCM)
      - legacy: <salt_b64>:<cipher_b64>         (PBKDF2->XOR)  [only for back-compat]
    """
    # 1) Generate RSA keypair
    private_key_obj = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key_obj = private_key_obj.public_key()

    # 2) Serialize public key to PEM (UTF-8 text)
    public_pem = public_key_obj.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    # 3) Serialize private key to raw PEM bytes (no encryption)
    private_pem_bytes = private_key_obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # 4) Encrypt for storage (v2)
    encrypted_blob = _encrypt_private_key_v2(private_pem_bytes, raw_password)
    return public_pem, encrypted_blob

def generate_user_keypair_for_password(raw_password: str) -> tuple[str, str]:
    """Generate a fresh RSA keypair and encrypt the private key under raw_password.

    Returns (public_pem, encrypted_private_key_blob).

    NOTE: This is used during password resets because the encrypted private key
    is derived from the user password; without the old password we cannot re-encrypt
    the existing private key.
    """
    return _generate_and_encrypt_rsa_keypair(raw_password)

def ensure_user_has_default_avatar(conn, username: str, *, randomize: bool = True) -> str | None:
    """Store a generated DiceBear avatar for ``username`` when the profile is blank.

    Account creation paths use this immediately after inserting a user. Setup sync
    paths use it for already-existing owner/admin accounts so a migrated database
    does not keep those users visually blank forever. The update is intentionally
    conditional and never overwrites a user-selected or uploaded avatar.

    Returns the generated avatar URL when the users.avatar_url column exists, or
    ``None`` when running against a very old database that has not added profiles
    yet. The caller owns transaction commit/rollback.
    """
    clean_username = str(username or "").strip()
    if not clean_username:
        return None
    if not _users_column_exists(conn, "avatar_url"):
        return None

    default_avatar_url = build_default_avatar_url(clean_username, randomize=randomize)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
               SET avatar_url = %s
             WHERE LOWER(username) = LOWER(%s)
               AND (avatar_url IS NULL OR BTRIM(avatar_url) = '');
            """,
            (default_avatar_url, clean_username),
        )
    return default_avatar_url


def create_user_with_keys(
    conn,
    username: str,
    raw_password: str,
    password_hash: str,
    email: str = None,
    phone: str = None,
    address: str = None,
    age: int = None,
    is_admin: bool = False,
    recovery_pin_hash: str | None = None,
    recovery_pin_set_at: datetime | None = None,
    field_encryption_settings: dict | None = None,
    commit: bool = True,
) -> None:
    """
    Generate an RSA keypair for this user, encrypt the private key under raw_password,
    then INSERT a new row into users(
        username, password, email, phone, address, age, is_admin,
        public_key, encrypted_private_key,
        recovery_pin_hash, recovery_pin_set_at
    ).
    `conn` must be a psycopg2 connection. Raises on any constraint violation.
    """
    public_pem, encrypted_priv_b64 = _generate_and_encrypt_rsa_keypair(raw_password)

    email_to_store, email_hash_to_store, email_encrypted_to_store = prepare_email_storage(email, field_encryption_settings)
    phone_to_store = encrypt_sensitive_field(phone, field_encryption_settings, field_name="users.phone") if phone else None
    address_to_store = encrypt_sensitive_field(address, field_encryption_settings, field_name="users.address") if address else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users
              (username, password, email, email_hash, email_encrypted, phone, address, age, is_admin,
               public_key, encrypted_private_key,
               recovery_pin_hash, recovery_pin_set_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                username,
                password_hash,       # goes into “password” column
                email_to_store,
                email_hash_to_store,
                email_encrypted_to_store,
                phone_to_store,
                address_to_store,
                age,
                is_admin,
                public_pem,
                encrypted_priv_b64,
                recovery_pin_hash,
                recovery_pin_set_at,
            ),
        )

    # Best-effort avatar assignment keeps account creation compatible with
    # older databases that have not added avatar_url yet, while giving every
    # migrated/current new account a stored generated avatar immediately.
    ensure_user_has_default_avatar(conn, username)
    if commit:
        conn.commit()

def get_public_key_for_username(conn, username: str) -> str:
    """
    Return the PEM string of public_key for `username`, or None if user doesn’t exist.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT public_key FROM users WHERE LOWER(username) = LOWER(%s);",
            (username,)
        )
        row = cur.fetchone()
    return row[0] if row else None

def canonical_username(conn, username: str) -> str | None:
    """Return the stored username for a case-insensitive username lookup."""
    clean = str(username or "").strip()
    if not clean:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT username FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1;", (clean,))
        row = cur.fetchone()
    return str(row[0]) if row and row[0] is not None else None


def find_user_by_username_ci(conn, username: str, columns: str = "*"):
    """Return one user row by case-insensitive username, using a controlled column list."""
    clean = str(username or "").strip()
    if not clean:
        return None
    safe_cols = str(columns or "*").strip()
    # This helper is intentionally internal; callers pass static column strings.
    with conn.cursor() as cur:
        cur.execute(f"SELECT {safe_cols} FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1;", (clean,))
        return cur.fetchone()


def user_exists(conn, username: str) -> bool:
    return canonical_username(conn, username) is not None

def _users_column_exists(conn, column_name: str) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'users'
                   AND column_name = %s
                 LIMIT 1;
                """,
                (column_name,),
            )
            return cur.fetchone() is not None
    except Exception:
        return False




def _table_column_exists(conn, table_name: str, column_name: str) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = %s
                   AND column_name = %s
                 LIMIT 1;
                """,
                (table_name, column_name),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def _current_auth_version_for_conn(conn, username: str) -> int:
    if not username or not _users_column_exists(conn, "auth_version"):
        return 0
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(auth_version, 0) FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1;", (username,))
        row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def get_auth_version(username: str) -> int:
    conn = get_db()
    return _current_auth_version_for_conn(conn, username)


def bump_auth_version(conn, username: str) -> int:
    """Increment users.auth_version when present and return the new version."""
    if not username or not _users_column_exists(conn, "auth_version"):
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
               SET auth_version = COALESCE(auth_version, 0) + 1,
                   password_changed_at = CURRENT_TIMESTAMP
             WHERE LOWER(username) = LOWER(%s)
             RETURNING auth_version;
            """,
            (username,),
        )
        row = cur.fetchone()
    return int(row[0] or 0) if row else 0

def email_in_use(conn, email: str, exclude_user_id: int | None = None, settings: dict | None = None) -> bool:
    """Return True if `email` is already in use.

    New encrypted-at-rest rows are matched through users.email_hash. Legacy
    plaintext rows are still checked through LOWER(users.email) until admins run
    the bulk email encryption action.
    """
    if not email:
        return False
    email = str(email).strip().lower()
    if not email:
        return False

    clauses = ["LOWER(email) = LOWER(%s)"]
    params: list = [email]
    if _users_column_exists(conn, "email_hash"):
        clauses.insert(0, "email_hash = %s")
        params.insert(0, hash_email(email, settings))
    sql = "SELECT 1 FROM users WHERE (" + " OR ".join(clauses) + ")"
    if exclude_user_id is not None:
        sql += " AND id <> %s"
        params.append(int(exclude_user_id))
    sql += " LIMIT 1;"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchone() is not None

def ensure_user_has_keys(conn, username: str, raw_password: str) -> bool:
    """Ensure an existing user row has (public_key, encrypted_private_key).

    Returns True if user exists (and now has keys), False if user does not exist.

    Also opportunistically migrates legacy key blobs:
      - legacy v1: salt_b64:cipher_b64 (PBKDF2->XOR)
      - v2: v2:salt_b64:nonce_b64:cipher_b64 (PBKDF2->AES-256-GCM)
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT public_key, encrypted_private_key FROM users WHERE LOWER(username) = LOWER(%s);",
            (username,),
        )
        row = cur.fetchone()

    if not row:
        return False

    public_key, encrypted_priv = row[0], row[1]

    # If keys exist, validate that the blob can be decrypted with the *current* password.
    # Why: admin password resets (or manual password edits) can change the login password
    # without re-wrapping the E2EE private key. That makes login succeed but DM unlock fail.
    #
    # Behavior:
    #   - If decrypt succeeds and blob is legacy v1 -> upgrade to v2.
    #   - If decrypt fails (but login already succeeded) -> rotate E2EE keys to match password.
    if public_key and encrypted_priv:
        blob = str(encrypted_priv)
        try:
            plain = _decrypt_private_key_blob(raw_password, blob)
            # Opportunistic upgrade: v1 XOR -> v2 AES-GCM
            if not blob.startswith("v2:"):
                upgraded = _encrypt_private_key_v2(plain, raw_password)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET encrypted_private_key = %s WHERE LOWER(username) = LOWER(%s);",
                        (upgraded, username),
                    )
                conn.commit()
            return True
        except Exception as e:
            logging.warning(
                "encrypted_private_key mismatch/corruption for %s (will rotate keys): %s",
                username,
                e,
            )
            try:
                public_pem, encrypted_priv_blob = _generate_and_encrypt_rsa_keypair(raw_password)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET public_key = %s, encrypted_private_key = %s WHERE LOWER(username) = LOWER(%s);",
                        (public_pem, encrypted_priv_blob, username),
                    )
                conn.commit()
            except Exception as e2:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logging.error("Failed rotating E2EE keys for %s: %s", username, e2)
            return True

    public_pem, encrypted_priv_blob = _generate_and_encrypt_rsa_keypair(raw_password)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET public_key = %s, encrypted_private_key = %s WHERE LOWER(username) = LOWER(%s);",
            (public_pem, encrypted_priv_blob, username),
        )
    conn.commit()
    return True

def get_encrypted_private_key_for_username(conn, username: str) -> str:
    """
    Return the TEXT value of encrypted_private_key.

    Formats:
      - v2:<salt_b64>:<nonce_b64>:<cipher_b64>   (PBKDF2->AES-256-GCM)
      - legacy: <salt_b64>:<cipher_b64>          (PBKDF2->XOR)
    for `username`, or None if no such user.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT encrypted_private_key FROM users WHERE LOWER(username) = LOWER(%s);",
            (username,)
        )
        row = cur.fetchone()
    return row[0] if row else None

def store_auth_token(
    jti: str,
    username: str,
    token_type: str,
    expires_at: datetime | None,
    session_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Persist an issued JWT's JTI.

    We store both access and refresh tokens. Access tokens are short-lived but
    storing them allows immediate logout revocation.
    """
    if not jti or not username or not token_type:
        return

    conn = get_db()
    store_auth_token_in_conn(
        conn,
        jti=jti,
        username=username,
        token_type=token_type,
        expires_at=expires_at,
        session_id=session_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    conn.commit()



def store_auth_token_in_conn(
    conn,
    *,
    jti: str,
    username: str,
    token_type: str,
    expires_at: datetime | None,
    session_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> None:
    if not jti or not username or not token_type:
        raise ValueError("jti, username, and token_type are required")
    has_auth_version = _table_column_exists(conn, "auth_tokens", "auth_version")
    auth_version = _current_auth_version_for_conn(conn, username) if has_auth_version else None
    with conn.cursor() as cur:
        if has_auth_version:
            cur.execute(
                """
                INSERT INTO auth_tokens (jti, username, session_id, token_type, expires_at, user_agent, ip_address, auth_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (jti) DO NOTHING;
                """,
                (jti, username, session_id, token_type, expires_at, user_agent, ip_address, auth_version),
            )
        else:
            cur.execute(
                """
                INSERT INTO auth_tokens (jti, username, session_id, token_type, expires_at, user_agent, ip_address)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (jti) DO NOTHING;
                """,
                (jti, username, session_id, token_type, expires_at, user_agent, ip_address),
            )
        if int(getattr(cur, "rowcount", 0) or 0) < 1:
            raise RuntimeError("auth token insert did not persist")


def create_auth_session_in_conn(conn, username: str, user_agent: str | None = None, ip_address: str | None = None) -> str:
    if not username:
        raise ValueError("username required")
    sid = uuid.uuid4().hex
    has_auth_version = _table_column_exists(conn, "auth_sessions", "auth_version")
    auth_version = _current_auth_version_for_conn(conn, username) if has_auth_version else None
    with conn.cursor() as cur:
        if has_auth_version:
            cur.execute(
                """
                INSERT INTO auth_sessions (session_id, username, last_seen_at, last_activity_at, user_agent, ip_address, auth_version)
                VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING;
                """,
                (sid, username, user_agent, ip_address, auth_version),
            )
        else:
            cur.execute(
                """
                INSERT INTO auth_sessions (session_id, username, last_seen_at, last_activity_at, user_agent, ip_address)
                VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s, %s)
                ON CONFLICT (session_id) DO NOTHING;
                """,
                (sid, username, user_agent, ip_address),
            )
        if int(getattr(cur, "rowcount", 0) or 0) < 1:
            raise RuntimeError("auth session insert did not persist")
    return sid


def create_login_session_and_tokens(
    username: str,
    *,
    access_jti: str,
    access_expires_at: datetime | None,
    refresh_jti: str,
    refresh_expires_at: datetime | None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> str:
    conn = get_db()
    try:
        sid = create_auth_session_in_conn(conn, username, user_agent=user_agent, ip_address=ip_address)
        store_auth_token_in_conn(conn, jti=access_jti, username=username, token_type="access", expires_at=access_expires_at, session_id=sid, user_agent=user_agent, ip_address=ip_address)
        store_auth_token_in_conn(conn, jti=refresh_jti, username=username, token_type="refresh", expires_at=refresh_expires_at, session_id=sid, user_agent=user_agent, ip_address=ip_address)
        conn.commit()
        return sid
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

def revoke_auth_token(jti: str) -> None:
    """Revoke a specific token by JTI."""
    if not jti:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_tokens
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
             WHERE jti = %s;
            """,
            (jti,),
        )
    conn.commit()

def revoke_all_tokens_for_user(username: str, token_type: str | None = None) -> None:
    """Revoke all tokens for a user (optionally filtered by type)."""
    if not username:
        return
    conn = get_db()
    with conn.cursor() as cur:
        if token_type:
            cur.execute(
                """
                UPDATE auth_tokens
                   SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                 WHERE LOWER(username) = LOWER(%s)
                   AND token_type = %s
                   AND revoked_at IS NULL;
                """,
                (username, token_type),
            )
        else:
            cur.execute(
                """
                UPDATE auth_tokens
                   SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                 WHERE LOWER(username) = LOWER(%s)
                   AND revoked_at IS NULL;
                """,
                (username,),
            )
    conn.commit()

def is_auth_token_revoked(jti: str) -> bool:
    """Return True if an issued token should be treated as revoked.

    IMPORTANT SECURITY BEHAVIOR:
      - If we *don't* have a DB record for a JTI, we treat it as revoked.
      - We also treat server-side expiry as revoked (defense in depth).
      - If the token is associated with a revoked session, it is revoked.
    """
    if not jti:
        return True

    conn = get_db()
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.revoked_at, t.expires_at, t.session_id, s.revoked_at, t.username,
                   COALESCE(t.auth_version, s.auth_version, 0), COALESCE(u.auth_version, 0)
              FROM auth_tokens t
              LEFT JOIN auth_sessions s
                     ON s.session_id = t.session_id
              LEFT JOIN users u
                     ON LOWER(u.username) = LOWER(t.username)
             WHERE t.jti = %s;
            """,
            (jti,),
        )
        row = cur.fetchone()

    # Unknown JTI => reject.
    if not row:
        return True

    revoked_at, expires_at, session_id, session_revoked_at, token_username, token_auth_version, user_auth_version = row
    if token_username:
        try:
            if not account_status_allows_auth(get_effective_account_status(token_username)):
                return True
        except Exception:
            # On DB errors, fail closed for token revocation checks.
            return True
    if revoked_at is not None:
        return True
    try:
        if int(token_auth_version or 0) != int(user_auth_version or 0):
            return True
    except Exception:
        return True
    if expires_at is not None and expires_at <= now:
        return True

    # If the token is session-bound, the session must exist and be active.
    if session_id:
        if session_revoked_at is not None:
            return True
        if session_revoked_at is None:
            # session_revoked_at None could still be because the LEFT JOIN didn't match.
            # Fail closed if session row is missing.
            # (We can't distinguish missing vs active without a separate select, so do one.)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT revoked_at FROM auth_sessions WHERE session_id = %s;",
                    (session_id,),
                )
                srow = cur.fetchone()
            if not srow:
                return True
            if srow[0] is not None:
                return True

    return False

def create_auth_session(username: str, user_agent: str | None = None, ip_address: str | None = None) -> str:
    """Create a new auth session and return session_id."""
    conn = get_db()
    sid = create_auth_session_in_conn(conn, username, user_agent=user_agent, ip_address=ip_address)
    conn.commit()
    return sid

def touch_auth_session(session_id: str) -> None:
    """Update last_seen_at for a session."""
    if not session_id:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_sessions
               SET last_seen_at = CURRENT_TIMESTAMP
             WHERE session_id = %s
               AND revoked_at IS NULL;
            """,
            (session_id,),
        )
    conn.commit()

def touch_auth_session_activity(session_id: str) -> None:
    """Update last_activity_at for a session (used for idle timeout)."""
    if not session_id:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_sessions
               SET last_activity_at = CURRENT_TIMESTAMP
             WHERE session_id = %s
               AND revoked_at IS NULL;
            """,
            (session_id,),
        )
    conn.commit()

def is_auth_session_active(
    session_id: str,
    username: str | None = None,
    max_idle_seconds: float | None = None,
) -> bool:
    """Return True if session exists, is not revoked, and (optionally) not idle."""
    if not session_id:
        return False
    conn = get_db()
    with conn.cursor() as cur:
        if username:
            cur.execute(
                """
                SELECT s.revoked_at,
                       COALESCE(s.last_activity_at, s.last_seen_at, s.created_at) AS last_act,
                       COALESCE(s.auth_version, 0), COALESCE(u.auth_version, 0)
                  FROM auth_sessions s
                  LEFT JOIN users u ON LOWER(u.username) = LOWER(s.username)
                 WHERE s.session_id = %s AND LOWER(s.username) = LOWER(%s);
                """,
                (session_id, username),
            )
        else:
            cur.execute(
                """
                SELECT s.revoked_at,
                       COALESCE(s.last_activity_at, s.last_seen_at, s.created_at) AS last_act,
                       COALESCE(s.auth_version, 0), COALESCE(u.auth_version, 0)
                  FROM auth_sessions s
                  LEFT JOIN users u ON LOWER(u.username) = LOWER(s.username)
                 WHERE s.session_id = %s;
                """,
                (session_id,),
            )
        row = cur.fetchone()
    if not row:
        return False
    revoked_at, last_act = row[0], row[1]
    if revoked_at is not None:
        return False
    try:
        if int(row[2] or 0) != int(row[3] or 0):
            return False
    except Exception:
        return False

    # Idle timeout is enforced on client-activity, not on background refresh/polling.
    if max_idle_seconds and max_idle_seconds > 0 and last_act is not None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        idle_s = (now - last_act).total_seconds()
        if idle_s > max_idle_seconds:
            return False

    return True

def get_auth_session_state(session_id: str):
    """Return minimal session timing info for UX/reason codes."""
    if not session_id:
        return None
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              revoked_at,
              created_at,
              last_seen_at,
              COALESCE(last_activity_at, last_seen_at, created_at) AS last_activity
            FROM auth_sessions
            WHERE session_id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    revoked_at, created_at, last_seen_at, last_activity = row
    return {
        "revoked_at": revoked_at,
        "created_at": created_at,
        "last_seen_at": last_seen_at,
        "last_activity": last_activity,
    }

def get_session_id_for_token(jti: str) -> str | None:
    """Return the session_id bound to a token JTI, if any."""
    if not jti:
        return None
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT session_id FROM auth_tokens WHERE jti = %s;", (jti,))
        row = cur.fetchone()
    return row[0] if row else None

def attach_session_to_token(username: str, jti: str, session_id: str) -> None:
    """Bind an existing token row to a session_id (used for legacy tokens)."""
    if not username or not jti or not session_id:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_tokens
               SET session_id = %s
             WHERE jti = %s
               AND LOWER(username) = LOWER(%s)
               AND session_id IS NULL;
            """,
            (session_id, jti, username),
        )
    conn.commit()



def apply_auth_risk_event(
    username: str,
    event: str,
    *,
    keep_current_sid: str | None = None,
    revoke_all: bool = False,
    conn=None,
    commit: bool = True,
) -> dict[str, int]:
    """Apply a high-risk auth lifecycle event.

    When ``conn`` is supplied with ``commit=False``, the caller can make the
    account change, auth-version bump, and session/token revocation one atomic
    transaction. Password-change timestamps are only updated for password
    events; the broader ``auth_changed_at`` timestamp is used for all auth-risk
    lifecycle events when the column exists.
    """
    username = str(username or "").strip()
    event = str(event or "auth_risk_event").strip() or "auth_risk_event"
    keep_current_sid = str(keep_current_sid or "").strip() or None
    if not username:
        return {"auth_version": 0, "revoked_sessions": 0, "revoked_tokens": 0}
    own_conn = conn is None
    conn = conn or get_db()
    password_events = {"password_change", "password_reset", "admin_password_reset"}
    try:
        with conn.cursor() as cur:
            if _users_column_exists(conn, "auth_version"):
                set_parts = ["auth_version = COALESCE(auth_version, 0) + 1"]
                if _users_column_exists(conn, "auth_changed_at"):
                    set_parts.append("auth_changed_at = CURRENT_TIMESTAMP")
                if event in password_events and _users_column_exists(conn, "password_changed_at"):
                    set_parts.append("password_changed_at = CURRENT_TIMESTAMP")
                cur.execute(
                    f"""
                    UPDATE users
                       SET {', '.join(set_parts)}
                     WHERE LOWER(username) = LOWER(%s)
                     RETURNING auth_version, username;
                    """,
                    (username,),
                )
                row = cur.fetchone()
                new_version = int(row[0] or 0) if row else 0
                stored_username = str(row[1] or username) if row else username
            else:
                new_version = 0
                stored_username = canonical_username(conn, username) or username

            if revoke_all or not keep_current_sid:
                cur.execute(
                    """
                    UPDATE auth_sessions
                       SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                           revoked_reason = COALESCE(revoked_reason, %s)
                     WHERE LOWER(username) = LOWER(%s)
                       AND revoked_at IS NULL;
                    """,
                    (event, stored_username),
                )
                revoked_sessions = cur.rowcount
                cur.execute(
                    """
                    UPDATE auth_tokens
                       SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                     WHERE LOWER(username) = LOWER(%s)
                       AND revoked_at IS NULL;
                    """,
                    (stored_username,),
                )
                revoked_tokens = cur.rowcount
            else:
                cur.execute(
                    """
                    UPDATE auth_sessions
                       SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                           revoked_reason = COALESCE(revoked_reason, %s)
                     WHERE LOWER(username) = LOWER(%s)
                       AND session_id <> %s
                       AND revoked_at IS NULL;
                    """,
                    (event, stored_username, keep_current_sid),
                )
                revoked_sessions = cur.rowcount
                cur.execute(
                    """
                    UPDATE auth_tokens
                       SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                     WHERE LOWER(username) = LOWER(%s)
                       AND session_id IS DISTINCT FROM %s
                       AND revoked_at IS NULL;
                    """,
                    (stored_username, keep_current_sid),
                )
                revoked_tokens = cur.rowcount
                if _table_column_exists(conn, "auth_sessions", "auth_version"):
                    cur.execute(
                        """
                        UPDATE auth_sessions
                           SET auth_version = %s,
                               revoked_at = NULL,
                               revoked_reason = NULL
                         WHERE session_id = %s
                           AND LOWER(username) = LOWER(%s);
                        """,
                        (new_version, keep_current_sid, stored_username),
                    )
        if commit:
            conn.commit()
        return {
            "auth_version": int(new_version or 0),
            "revoked_sessions": int(revoked_sessions or 0),
            "revoked_tokens": int(revoked_tokens or 0),
        }
    except Exception:
        if own_conn or commit:
            try:
                conn.rollback()
            except Exception:
                pass
        raise

def revoke_auth_session(session_id: str, reason: str | None = None) -> None:
    """Revoke a session and all tokens bound to it."""
    if not session_id:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_sessions
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                   revoked_reason = COALESCE(revoked_reason, %s)
             WHERE session_id = %s;
            """,
            (reason, session_id),
        )
        cur.execute(
            """
            UPDATE auth_tokens
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
             WHERE session_id = %s
               AND revoked_at IS NULL;
            """,
            (session_id,),
        )
    conn.commit()

def revoke_other_sessions_for_user(username: str, keep_session_id: str, reason: str | None = "logout_others") -> int:
    """Revoke every other active auth session for a user.

    The current session is preserved by exact session_id match.  Refresh/access
    token rows from other sessions are revoked too, including legacy/unbound token
    rows whose session_id is NULL.  Those unbound token rows cannot be proven to
    belong to the current browser, so logout-other-devices must fail closed on
    them instead of leaving an old refresh token usable.
    """
    username = str(username or "").strip().lower()
    keep_session_id = str(keep_session_id or "").strip()
    if not username or not keep_session_id:
        return 0
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_sessions
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                   revoked_reason = COALESCE(revoked_reason, %s)
             WHERE LOWER(username) = LOWER(%s)
               AND session_id <> %s
               AND revoked_at IS NULL;
            """,
            (reason, username, keep_session_id),
        )
        revoked_sessions = cur.rowcount

        cur.execute(
            """
            UPDATE auth_tokens
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
             WHERE LOWER(username) = LOWER(%s)
               AND session_id IS DISTINCT FROM %s
               AND revoked_at IS NULL;
            """,
            (username, keep_session_id),
        )
    conn.commit()
    return int(revoked_sessions or 0)

def revoke_all_sessions_for_user(username: str, reason: str | None = "logout_all") -> int:
    """Revoke every active session and token for a user.

    Unlike logout-other-devices, this intentionally revokes the caller's current
    session too.  Token cleanup also covers legacy/unbound token rows whose
    session_id is NULL, because after logout-all no refresh/access token for the
    account should remain usable.
    """
    username = str(username or "").strip().lower()
    if not username:
        return 0
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_sessions
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                   revoked_reason = COALESCE(revoked_reason, %s)
             WHERE LOWER(username) = LOWER(%s)
               AND revoked_at IS NULL;
            """,
            (reason, username),
        )
        revoked_sessions = cur.rowcount

        cur.execute(
            """
            UPDATE auth_tokens
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
             WHERE LOWER(username) = LOWER(%s)
               AND revoked_at IS NULL;
            """,
            (username,),
        )
    conn.commit()
    return int(revoked_sessions or 0)


def revoke_all_sessions_and_tokens_for_user(username: str, reason: str | None = "admin_force_logout") -> dict[str, int]:
    """Revoke every active session and token for a user and report both counts.

    This is for admin/server-side force sign-out paths where success must mean
    both browser sessions and all stored access/refresh token rows are no longer
    usable.  Token cleanup intentionally covers legacy/unbound token rows whose
    session_id is NULL.
    """
    username = str(username or "").strip().lower()
    if not username:
        return {"revoked_sessions": 0, "revoked_tokens": 0}
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_sessions
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                   revoked_reason = COALESCE(revoked_reason, %s)
             WHERE LOWER(username) = LOWER(%s)
               AND revoked_at IS NULL;
            """,
            (reason, username),
        )
        revoked_sessions = cur.rowcount

        cur.execute(
            """
            UPDATE auth_tokens
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
             WHERE LOWER(username) = LOWER(%s)
               AND revoked_at IS NULL;
            """,
            (username,),
        )
        revoked_tokens = cur.rowcount
    conn.commit()
    return {
        "revoked_sessions": int(revoked_sessions or 0),
        "revoked_tokens": int(revoked_tokens or 0),
    }

def _coerce_session_list_limit(limit, *, default: int = 50, maximum: int = 100) -> int:
    """Return a safe session-list LIMIT for account/session UIs."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    if value < 1:
        return default
    return min(value, maximum)


def list_auth_sessions(username: str, *, include_revoked: bool = True, limit: int = 50) -> list[dict]:
    """List a user's auth sessions, newest activity first.

    The Account Security page uses this for the current/recent device list.
    The JSON /auth/sessions endpoint passes include_revoked=False so its
    payload remains an active-session list instead of exposing old history.
    """
    if not username:
        return []
    limit_value = _coerce_session_list_limit(limit)
    revoked_filter = "" if include_revoked else "AND revoked_at IS NULL"
    conn = get_db()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT session_id, created_at, last_seen_at, last_activity_at, revoked_at, revoked_reason, user_agent, ip_address
              FROM auth_sessions
             WHERE LOWER(username) = LOWER(%s)
               {revoked_filter}
             ORDER BY COALESCE(last_activity_at, last_seen_at, created_at) DESC
             LIMIT %s;
            """,
            (username, limit_value),
        )
        rows = cur.fetchall() or []
    return [dict(r) for r in rows]

def revoke_all_tokens_global() -> None:
    """Revoke *all* tokens for *all* users (used only when explicitly enabled).

    This can be useful if you want "server restart forces re-login" behavior.
    """
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_tokens
               SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
             WHERE revoked_at IS NULL;
            """
        )
    conn.commit()

def is_refresh_token_active(username: str, jti: str) -> bool:
    """A refresh token is ACTIVE only if it exists and is not revoked/replaced.

    Additionally:
      - if the token is associated to a session_id, that session must be active
        (revoked_at IS NULL).
    """
    if not username or not jti:
        return False
    conn = get_db()
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT revoked_at, replaced_by, expires_at, session_id
              FROM auth_tokens
             WHERE jti = %s
               AND LOWER(username) = LOWER(%s)
               AND token_type = 'refresh';
            """,
            (jti, username),
        )
        row = cur.fetchone()
    if not row:
        return False
    try:
        if not account_status_allows_auth(get_effective_account_status(username)):
            return False
    except Exception:
        return False
    revoked_at, replaced_by, expires_at, session_id = row
    if revoked_at is not None:
        return False
    if replaced_by is not None:
        return False
    if expires_at is not None and expires_at <= now:
        return False

    if session_id:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT revoked_at FROM auth_sessions WHERE session_id = %s AND LOWER(username) = LOWER(%s);",
                (session_id, username),
            )
            srow = cur.fetchone()
        # If we can't find the session row, fail closed (forces re-login).
        if not srow:
            return False
        if srow[0] is not None:
            return False

    return True

def get_refresh_token_meta(username: str, jti: str):
    """Return (revoked_at, replaced_by, expires_at, last_used_at, session_id) or None."""
    if not username or not jti:
        return None
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT revoked_at, replaced_by, expires_at, last_used_at, session_id
              FROM auth_tokens
             WHERE jti = %s
               AND LOWER(username) = LOWER(%s)
               AND token_type = 'refresh';
            """,
            (jti, username),
        )
        row = cur.fetchone()
    return row

def is_refresh_token_usable(username: str, jti: str) -> bool:
    """Refresh token is *usable* if it exists, not explicitly revoked, and unexpired.

    NOTE:
      - This does **not** require replaced_by to be NULL. We intentionally allow
        rotated refresh tokens to reach /token/refresh so the endpoint can
        respond gracefully (race) or hard-kill (replay).
      - If the token is bound to a session_id, the session must be active.
    """
    meta = get_refresh_token_meta(username, jti)
    if not meta:
        return False
    try:
        if not account_status_allows_auth(get_effective_account_status(username)):
            return False
    except Exception:
        return False
    revoked_at, _replaced_by, expires_at, _last_used_at, session_id = meta
    if revoked_at is not None:
        return False
    now = datetime.now(timezone.utc)
    if expires_at is not None and expires_at <= now:
        return False

    if session_id:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT revoked_at FROM auth_sessions WHERE session_id = %s AND LOWER(username) = LOWER(%s);",
                (session_id, username),
            )
            srow = cur.fetchone()
        if not srow:
            return False
        if srow[0] is not None:
            return False

    return True



def rotate_refresh_and_store_access_token(
    *,
    username: str,
    old_jti: str,
    new_refresh_jti: str,
    new_refresh_expires_at: datetime | None,
    new_access_jti: str,
    new_access_expires_at: datetime | None,
    session_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> bool:
    if not username or not old_jti or not new_refresh_jti or not new_access_jti:
        return False
    conn = get_db()
    try:
        with conn.cursor() as cur:
            if session_id is None:
                cur.execute(
                    """
                    SELECT session_id
                      FROM auth_tokens
                     WHERE jti = %s
                       AND LOWER(username) = LOWER(%s)
                       AND token_type = 'refresh';
                    """,
                    (old_jti, username),
                )
                row = cur.fetchone()
                session_id = row[0] if row else None
            cur.execute(
                """
                UPDATE auth_tokens
                   SET replaced_by = %s,
                       last_used_at = CURRENT_TIMESTAMP
                 WHERE jti = %s
                   AND LOWER(username) = LOWER(%s)
                   AND token_type = 'refresh'
                   AND revoked_at IS NULL
                   AND replaced_by IS NULL;
                """,
                (new_refresh_jti, old_jti, username),
            )
            if int(getattr(cur, "rowcount", 0) or 0) != 1:
                conn.rollback()
                return False
        store_auth_token_in_conn(conn, jti=new_refresh_jti, username=username, token_type="refresh", expires_at=new_refresh_expires_at, session_id=session_id, user_agent=user_agent, ip_address=ip_address)
        store_auth_token_in_conn(conn, jti=new_access_jti, username=username, token_type="access", expires_at=new_access_expires_at, session_id=session_id, user_agent=user_agent, ip_address=ip_address)
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

def rotate_refresh_token(
    username: str,
    old_jti: str,
    new_jti: str,
    new_expires_at: datetime | None,
    session_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> bool:
    """Single-use refresh rotation (session-aware).

    Returns True if rotation succeeded, False if the old token was not ACTIVE.

    If session_id is None, we will copy the old token's session_id (if present).
    """
    if not username or not old_jti or not new_jti:
        return False

    conn = get_db()
    with conn.cursor() as cur:
        # Copy session_id from old token unless explicitly provided.
        if session_id is None:
            cur.execute(
                """
                SELECT session_id
                  FROM auth_tokens
                 WHERE jti = %s
                   AND LOWER(username) = LOWER(%s)
                   AND token_type = 'refresh';
                """,
                (old_jti, username),
            )
            row = cur.fetchone()
            session_id = row[0] if row else None

        cur.execute(
            """
            UPDATE auth_tokens
               SET replaced_by = %s,
                   last_used_at = CURRENT_TIMESTAMP
             WHERE jti = %s
               AND LOWER(username) = LOWER(%s)
               AND token_type = 'refresh'
               AND revoked_at IS NULL
               AND replaced_by IS NULL;
            """,
            (new_jti, old_jti, username),
        )
        updated = cur.rowcount

        if updated != 1:
            conn.rollback()
            return False

        store_auth_token_in_conn(
            conn,
            jti=new_jti,
            username=username,
            token_type="refresh",
            expires_at=new_expires_at,
            session_id=session_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    conn.commit()
    return True

def touch_auth_token(jti: str) -> None:
    """Update last_used_at for a token."""
    if not jti:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth_tokens
               SET last_used_at = CURRENT_TIMESTAMP
             WHERE jti = %s;
            """,
            (jti,),
        )
    conn.commit()

