#!/usr/bin/env python3
"""Safe, cross-process branding configuration for Hui-Chat.

Only the small branding subset is read from the shared settings JSON. This lets
all Gunicorn instances see an admin branding change on the next page render
without trusting arbitrary paths or reloading the whole server configuration.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

BRANDING_DEFAULTS: dict[str, Any] = {
    "branding_logo_enabled": True,
    "branding_loading_screen_enabled": True,
    "branding_loading_screen_mode": "animation",
    "branding_logo_asset": "images/hui-chat-logo.svg",
    "branding_loading_asset": "images/hui-chat-logo-loading.svg",
    "branding_asset_revision": "1",
}

BRANDING_KEYS = frozenset(BRANDING_DEFAULTS)
_ALLOWED_ASSET_PREFIXES = ("images/", "uploads/branding/")


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def normalize_branding_asset(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().replace("\\", "/").lstrip("/")
    try:
        path = PurePosixPath(raw)
    except Exception:
        return fallback
    if not raw or path.is_absolute() or ".." in path.parts:
        return fallback
    normalized = path.as_posix()
    if not normalized.startswith(_ALLOWED_ASSET_PREFIXES):
        return fallback
    if any(not (ch.isalnum() or ch in "._-/") for ch in normalized):
        return fallback
    return normalized


def _read_shared_branding(settings_file: str | Path | None) -> dict[str, Any]:
    if not settings_file:
        return {}
    try:
        path = Path(str(settings_file))
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(payload, dict):
            return {}
        return {key: payload.get(key) for key in BRANDING_KEYS if key in payload}
    except Exception:
        return {}


def effective_branding_settings(settings: dict[str, Any] | None, settings_file: str | Path | None = None) -> dict[str, Any]:
    effective = dict(BRANDING_DEFAULTS)
    if isinstance(settings, dict):
        effective.update({key: settings.get(key) for key in BRANDING_KEYS if key in settings})
    # File values win so every scaled instance sees the last persisted admin save.
    effective.update(_read_shared_branding(settings_file))

    effective["branding_logo_enabled"] = _as_bool(
        effective.get("branding_logo_enabled"), True
    )
    effective["branding_loading_screen_enabled"] = _as_bool(
        effective.get("branding_loading_screen_enabled"), True
    )
    mode = str(effective.get("branding_loading_screen_mode") or "animation").strip().lower()
    effective["branding_loading_screen_mode"] = mode if mode in {"animation", "text"} else "animation"
    effective["branding_logo_asset"] = normalize_branding_asset(
        effective.get("branding_logo_asset"), BRANDING_DEFAULTS["branding_logo_asset"]
    )
    effective["branding_loading_asset"] = normalize_branding_asset(
        effective.get("branding_loading_asset"), BRANDING_DEFAULTS["branding_loading_asset"]
    )
    revision = str(effective.get("branding_asset_revision") or "1").strip()
    revision = "".join(ch for ch in revision if ch.isalnum() or ch in "._-")
    effective["branding_asset_revision"] = revision[:80] or "1"
    return effective


def branding_template_context(settings: dict[str, Any] | None, settings_file: str | Path | None = None) -> dict[str, Any]:
    effective = effective_branding_settings(settings, settings_file)
    revision = effective["branding_asset_revision"]
    logo_asset = effective["branding_logo_asset"]
    loading_asset = effective["branding_loading_asset"]
    return {
        "logo_enabled": bool(effective["branding_logo_enabled"]),
        "loading_screen_enabled": bool(effective["branding_loading_screen_enabled"]),
        "loading_screen_mode": effective["branding_loading_screen_mode"],
        "logo_asset": logo_asset,
        "loading_asset": loading_asset,
        "logo_url": f"/static/{logo_asset}?v={revision}",
        "loading_url": f"/static/{loading_asset}?v={revision}",
        "asset_revision": revision,
    }
