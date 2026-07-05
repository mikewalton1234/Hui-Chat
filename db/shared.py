#!/usr/bin/env python3
"""Shared DB module state and migration metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import os

from psycopg2.pool import ThreadedConnectionPool

JSON_ROOMS_PATH = os.path.join(Path(__file__).resolve().parent.parent, "chat_rooms.json")
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA_META_TABLE = "echochat_schema_meta"


@dataclass(frozen=True)
class MigrationSpec:
    version: str
    name: str
    kind: str
    checksum: str
    upgrade: Callable
    source_path: Path


_POOL: ThreadedConnectionPool | None = None
_DSN: str | None = None
_POOL_CONFIGURED: bool = False
_POOL_INIT_ERROR: str | None = None
_ALLOW_DIRECT_FALLBACK: bool = False
_DB_POOL_MAX: int | None = None
_DB_POOL_MIN: int | None = None
