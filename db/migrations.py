#!/usr/bin/env python3
"""Tracked migration discovery and execution helpers."""

from __future__ import annotations

import hashlib
import importlib.util
import logging
from pathlib import Path

import psycopg2

from constants import get_db_connection_string, sanitize_postgres_dsn
from db import shared
from db.shared import MIGRATIONS_DIR, SCHEMA_META_TABLE, MigrationSpec


# A small, explicit compatibility set for beta builds where a migration file
# was corrected after local databases had already recorded a checksum. Applied
# migrations remain immutable in normal cases; follow-up fixes must use a new
# migration version.
_COMPATIBLE_CHECKSUMS: dict[str, set[str]] = {
    "0001_baseline": {
        "8df2cd4b10ceef27ae7b6916ffcba3fa70ce2c8e9be3c5a12222dbf172815214",
        "b98a22deaeaff9aefa121659a67fdbf940709b5067ec924e974f6edd6812f4e9",
    },
    "0002_users_security_columns": {
        "95309e77f7b43919d654864e63230fb25339b8c8bfeaff0b3a7f464e065622f0",
        "2728e26d1ac852438d118f6b1b97042f9f915ce07520237a2210e18e42f0cc45",
    },
    "0003_users_profile_presence_columns": {
        "5eb00956e1a7515774c75187a3514b5c6fedd41181d09e405e10ec7f00c9ddf9",
        "37e07ac5d4c21a6c788f05d5e8a1759870e434801bb7947f57ae0968cc478c37",
    },
    "0004_users_account_status_column": {
        "4e680a9579537f911b8907e80ba6a1146fd0ddde2fd0d1423e9248bcbde845d5",
        "9d5c415ad7d02da0cb12aa0a5fdc27df33a6e8f0b2b0977179a4054fc17c5634",
    },
    "0005_users_extended_profile_fields": {
        "7a2f9f793a48f28ffd4810303eb892c216e560c1b9e7389467308c625c2d521d",
        "4b3198b6120bd71e27e67ffd885a1ffda62165c2d707f7a1324336ecd0e61484",
    },
    "0006_users_profile_favorites_fields": {
        "bfbce9368b8e65193809e9659e72e7c2da7893f4bfcf5c63e918148072e3db5d",
        "b3e389e9517c3b6c8d50e57413328e627d08980bf0655a5d288e61c5f290a0ef",
    },
    "0007_users_recent_room_sharing": {
        "d2562cc9348bbb55b0da565da936c80d3d69df03644aa3cbb4e5ee84465dcc7e",
        "05a27281da3b52d9e88423cda00ae61059ffce4e435caac04d732b62094a226e",
    },
    "0008_profile_posts_and_featured": {
        "be1898b49ece4ee088f662a1302e4d0fedc8fee739a4388aa0fb86f5e4bc3dce",
        "1ce6282c2052731f3e2b1690181771acb10e8c72f48753e08db6381db7787bc4",
    },
    "0009_admin_settings_permission_backfill": {
        "1a672305cdb4b3770289fa828c753bbd9b2b2daf7096d55af70a3c7866b57b3c",
        "8ae3d768b1abf4b38fb418603f9a97862171d134af97ca8c8f68d12acac924e8",
    },
    "0010_profile_post_engagement": {
        "4b2adffd503605d71eabf6dac77b3b3072ffe810cc436798e3ff0c829853d840",
        "93496db6586797a928344cfc808d36dbe7cbf85d822e8343856747b15c5e457f",
    },
    "0011_profile_post_management": {
        "4d7bbf0d3d3800e7e5798e2b644a22c4e66246126e9aaa70097cde5af14f4491",
        "16847e8d22f0f20c6c42427affc79b02aa1357759b81d0aaac762880017762c2",
    },
    "0012_profile_safety_privacy_badges": {
        "80c9c38c453fae407d6fb2fbae2f1d0781141b572de7259755132cee53acb945",
        "dd6a801b46a8494c04db22845c4a3cf21ee03ee6537290ac0f6d3423e1297356",
        "6af0728a882c36cbab0185b096941650947e68290a360e07b22236769c2706e1",
    },
    "0013_profile_runtime_schema_repair": {
        "c5132be15d825febe5ee97aac75dc08c201deea42f53357e78fe4d4a71cf622d",
        "c554023b9518ae84a65e21e65d891a233566caf9e1f6b2823eb7b248ddaa89b2",
    },
    "0014_user_activity_timeline_notification_settings": {
        "22e223cd4901de94afb5d7a60e19801897fd18ab4aef1178f76cb3d7c409ea25",
        "427e300239b08f26f817fc77c28a84c6846434ec71f35ca196a3d2f30ebfbabc",
    },
    "0015_users_email_encrypted_columns": {
        "c789372b51000e76b828e8e37f5678822253f307b391694df2fc11e68cd0efd3",
        "e72c3679a6c55487024da25e79928266adb52357e6c5182076405c022b8c18da",
    },
    "0016_custom_room_members_role": {
        "d090727bdc2a8fc0d6e58a0155fa54d18af6bf9494d67a1175b6d27e26e9d687",
        "997f442528bbfa29184ea594ad7a1281ddb980d61dfab6c3c50bfb3492eb0a48",
    },
}

_MIGRATION_LOCK_KEY = (50291, 31403)


def _checksums_compatible(version: str, db_checksum: str | None, file_checksum: str | None) -> bool:
    if not db_checksum or not file_checksum:
        return False
    if db_checksum == file_checksum:
        return True
    allowed = _COMPATIBLE_CHECKSUMS.get(str(version), set())
    return str(db_checksum) in allowed and str(file_checksum) in allowed


def _ensure_schema_meta_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_META_TABLE} (
                version     TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                kind        TEXT NOT NULL DEFAULT 'python',
                checksum    TEXT NOT NULL,
                applied_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                success     BOOLEAN NOT NULL DEFAULT TRUE,
                notes       TEXT
            );
            """
        )
        # Setup-created legacy beta tables may have weaker nullable columns.
        cur.execute(f"UPDATE {SCHEMA_META_TABLE} SET name=version WHERE name IS NULL OR BTRIM(name)='';")
        cur.execute(f"UPDATE {SCHEMA_META_TABLE} SET kind='python' WHERE kind IS NULL OR BTRIM(kind)='';")
        cur.execute(f"UPDATE {SCHEMA_META_TABLE} SET checksum='legacy-unknown-' || version WHERE checksum IS NULL OR BTRIM(checksum)='';")
        cur.execute(f"UPDATE {SCHEMA_META_TABLE} SET success=TRUE WHERE success IS NULL;")
        cur.execute(f"ALTER TABLE {SCHEMA_META_TABLE} ALTER COLUMN name SET NOT NULL;")
        cur.execute(f"ALTER TABLE {SCHEMA_META_TABLE} ALTER COLUMN kind SET DEFAULT 'python';")
        cur.execute(f"ALTER TABLE {SCHEMA_META_TABLE} ALTER COLUMN kind SET NOT NULL;")
        cur.execute(f"ALTER TABLE {SCHEMA_META_TABLE} ALTER COLUMN checksum SET NOT NULL;")
        cur.execute(f"ALTER TABLE {SCHEMA_META_TABLE} ALTER COLUMN success SET DEFAULT TRUE;")
        cur.execute(f"ALTER TABLE {SCHEMA_META_TABLE} ALTER COLUMN success SET NOT NULL;")
    conn.commit()


def _checksum_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_python_migration(path: Path) -> MigrationSpec:
    spec = importlib.util.spec_from_file_location(f"hui_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load migration module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    version = str(getattr(module, 'VERSION', '')).strip()
    name = str(getattr(module, 'NAME', path.stem)).strip()
    kind = str(getattr(module, 'KIND', 'python')).strip() or 'python'
    upgrade = getattr(module, 'upgrade', None)
    if not version:
        raise RuntimeError(f"Migration {path.name} is missing VERSION")
    if not callable(upgrade):
        raise RuntimeError(f"Migration {path.name} is missing callable upgrade(conn)")
    return MigrationSpec(
        version=version,
        name=name,
        kind=kind,
        checksum=_checksum_path(path),
        upgrade=upgrade,
        source_path=path,
    )


def _available_migration_specs() -> list[MigrationSpec]:
    migrations: list[MigrationSpec] = []
    if MIGRATIONS_DIR.exists():
        for path in MIGRATIONS_DIR.glob('m*.py'):
            if path.name == '__init__.py':
                continue
            migrations.append(_load_python_migration(path))
    migrations.sort(key=lambda m: m.version)
    return migrations


def list_available_migrations() -> list[dict]:
    return [
        {
            'version': m.version,
            'name': m.name,
            'kind': m.kind,
            'checksum': m.checksum,
            'path': str(m.source_path.relative_to(Path(__file__).resolve().parent.parent)),
        }
        for m in _available_migration_specs()
    ]


def _get_applied_migration_rows(conn) -> dict[str, dict]:
    _ensure_schema_meta_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT version, checksum, applied_at, success, notes FROM {SCHEMA_META_TABLE};"
        )
        rows = cur.fetchall() or []
    out = {}
    for version, checksum, applied_at, success, notes in rows:
        out[str(version)] = {
            'checksum': str(checksum),
            'applied_at': applied_at,
            'success': bool(success),
            'notes': notes,
        }
    return out


def _migration_dsn() -> str:
    return str(sanitize_postgres_dsn(shared._DSN or get_db_connection_string()))


def _record_failed_migration(conn, migration: MigrationSpec, exc: BaseException) -> None:
    try:
        conn.rollback()
    except Exception:
        pass
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA_META_TABLE} (version, name, kind, checksum, success, notes)
                VALUES (%s, %s, %s, %s, FALSE, %s)
                ON CONFLICT (version) DO UPDATE
                   SET name = EXCLUDED.name,
                       kind = EXCLUDED.kind,
                       checksum = EXCLUDED.checksum,
                       success = FALSE,
                       applied_at = CURRENT_TIMESTAMP,
                       notes = EXCLUDED.notes;
                """,
                (
                    migration.version,
                    migration.name,
                    migration.kind,
                    migration.checksum,
                    f"FAILED from {migration.source_path.name}: {type(exc).__name__}: {exc}",
                ),
            )
        conn.commit()
    except Exception as record_exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.warning("Could not record failed migration %s: %s", migration.version, record_exc)


def apply_migrations() -> dict:
    """Apply all pending migrations in version order.

    Uses a dedicated PostgreSQL session instead of a pooled request connection.
    Closing that session guarantees the session-level advisory lock is released,
    even if a migration fails.  Migration rows with success=false are considered
    dirty and block startup until an admin repairs the database.
    """
    conn = psycopg2.connect(_migration_dsn())
    applied_versions: list[str] = []
    skipped_versions: list[str] = []
    available = _available_migration_specs()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s, %s);", _MIGRATION_LOCK_KEY)
        logging.info("Acquired Hui Chat migration advisory lock")

        applied = _get_applied_migration_rows(conn)
        for migration in available:
            prior = applied.get(migration.version)
            if prior:
                if not prior.get('success', True):
                    raise RuntimeError(
                        f"Migration {migration.version} previously failed and is marked dirty. "
                        "Restore from backup or repair the schema, then clear/re-run this migration intentionally."
                    )
                if not _checksums_compatible(migration.version, prior.get('checksum'), migration.checksum):
                    raise RuntimeError(
                        f"Migration checksum mismatch for {migration.version}: "
                        f"db={prior.get('checksum')} file={migration.checksum}"
                    )
                skipped_versions.append(migration.version)
                continue

            logging.info("Applying migration %s (%s)", migration.version, migration.name)
            try:
                migration.upgrade(conn)
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {SCHEMA_META_TABLE} (version, name, kind, checksum, success, notes)
                        VALUES (%s, %s, %s, %s, TRUE, %s)
                        ON CONFLICT (version) DO UPDATE
                           SET name = EXCLUDED.name,
                               kind = EXCLUDED.kind,
                               checksum = EXCLUDED.checksum,
                               success = TRUE,
                               applied_at = CURRENT_TIMESTAMP,
                               notes = EXCLUDED.notes;
                        """,
                        (
                            migration.version,
                            migration.name,
                            migration.kind,
                            migration.checksum,
                            f"Applied from {migration.source_path.name}",
                        ),
                    )
                conn.commit()
            except Exception as exc:
                _record_failed_migration(conn, migration, exc)
                raise
            applied_versions.append(migration.version)

        return {
            'applied': applied_versions,
            'skipped': skipped_versions,
            'available': [m.version for m in available],
            'latest': available[-1].version if available else None,
        }
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s, %s);", _MIGRATION_LOCK_KEY)
            logging.info("Released Hui Chat migration advisory lock")
        except Exception as exc:
            logging.warning("Could not release migration advisory lock cleanly; closing dedicated session: %s", exc)
        conn.close()
