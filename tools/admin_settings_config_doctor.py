#!/usr/bin/env python3
"""Static admin settings/configuration backend checks for Hui Chat.

This S16 doctor verifies settings/config invariants without a live database:

  - debug config snapshots require settings permission, recent admin re-auth,
    redaction, and local-only-by-default access;
  - settings persistence reports which keys were actually written and which
    secrets/nested credentials were runtime-only;
  - settings writers return persistence metadata and keep recent admin re-auth;
  - secret-bearing settings are not logged raw;
  - external/admin-supplied URLs and rate-limit strings are validated.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _function_body(rel: str, name: str) -> str:
    text = _read(rel)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    return ""


def _decorator_window(text: str, fn_name: str, chars: int = 260) -> str:
    marker = f"def {fn_name}"
    idx = text.find(marker)
    if idx < 0:
        return ""
    return text[max(0, idx - chars):idx]


def main() -> int:
    failures: list[str] = []
    routes = _read("routes_admin_tools.py")
    secrets_policy = _read("secrets_policy.py")

    debug = _function_body("routes_admin_tools.py", "_debug_config")
    if not debug:
        failures.append("missing /api/debug/config handler")
    else:
        debug_decorators = _decorator_window(routes, "_debug_config")
        for token in [
            '@require_permission("admin:settings")',
        ]:
            if token not in debug_decorators:
                failures.append(f"debug config route missing decorator/token: {token}")
        for token in [
            "_admin_reauth_status(_actor())",
            "_admin_reauth_required_response(status)",
            "debug_config_allow_remote",
            "not allow_remote and not _is_local_request()",
            "redact_postgres_dsn(dsn)",
            '"settings": _scrub(runtime_settings)',
        ]:
            if token not in debug:
                failures.append(f"debug config route missing safety token: {token}")

    persist = _function_body("routes_admin_tools.py", "_persist_settings_patch")
    for token in [
        "scrub_patch_for_persist(raw_patch, settings)",
        "scrub_secrets_for_persist(merged)",
        "_settings_persist_report",
        "_settings_persistence_meta(raw_patch, safe_patch, persisted=True)",
        "runtime-only",
    ]:
        if token not in persist:
            failures.append(f"settings persistence helper missing token: {token}")

    meta = _function_body("routes_admin_tools.py", "_settings_persistence_meta")
    for token in [
        "secret_persistence_enabled",
        "persisted_keys",
        "runtime_only_keys",
        "redacted_nested_keys",
        "Some secret or credential fields",
    ]:
        if token not in meta:
            failures.append(f"settings persistence metadata missing token: {token}")

    for fn in [
        "admin_set_voice_settings",
        "admin_set_ice_settings",
        "admin_set_media_settings",
    ]:
        body = _function_body("routes_admin_tools.py", fn)
        if not body:
            failures.append(f"missing settings writer {fn}")
            continue
        decorators = _decorator_window(routes, fn)
        if '@require_permission("admin:settings")' not in decorators:
            failures.append(f"{fn} must require admin:settings")
        if "@require_recent_admin_auth" not in decorators:
            failures.append(f"{fn} must require recent admin reauth")
        for token in ["_persist_settings_patch(patch)", '"persistence": _last_settings_persistence_meta()']:
            if token not in body:
                failures.append(f"{fn} missing {token}")

    for fn in ["admin_settings_gifs", "admin_settings_general", "admin_settings_antiabuse"]:
        body = _function_body("routes_admin_tools.py", fn)
        if not body:
            failures.append(f"missing settings handler {fn}")
            continue
        decorators = _decorator_window(routes, fn)
        if '@require_permission("admin:settings")' not in decorators:
            failures.append(f"{fn} must require admin:settings")
        if "_admin_reauth_status(_actor())" not in body:
            failures.append(f"{fn} must manually require recent admin reauth for non-GET writes")
        for token in ["_persist_settings_patch(patch)", '"persistence": _last_settings_persistence_meta()']:
            if token not in body:
                failures.append(f"{fn} missing {token}")

    gifs = _function_body("routes_admin_tools.py", "admin_settings_gifs")
    for token in [
        "has_key",
        "giphy_rating must be y, g, pg, pg-13, or r",
        "giphy_lang must be a short locale code",
        "giphy_api_key is too long or contains invalid characters",
        "safe_meta = \",\".join([k for k in patch.keys()])",
    ]:
        if token not in gifs:
            failures.append(f"GIF settings route missing safety token: {token}")
    if '"giphy_api_key":' in gifs and "has_key" not in gifs:
        failures.append("GIF settings GET/response must not expose raw giphy_api_key")

    general = _function_body("routes_admin_tools.py", "admin_settings_general")
    for token in [
        "sound_pack_external_urls",
        "sanitize_sound_pack_external_urls",
        "emoticons_local_root",
        "root.startswith(\"/\") or \"..\" in root.split(\"/\")",
        "emoticons_external_asset_base_url",
        "parsed.username or parsed.password",
        "parsed_custom_url.scheme not in {\"https\", \"http\"}",
        "emoticons_custom_entries",
        "safe_patch_meta",
    ]:
        if token not in general:
            failures.append(f"general settings route missing validation/logging token: {token}")

    anti = _function_body("routes_admin_tools.py", "admin_settings_antiabuse")
    for token in [
        "_normalize_admin_rate_limit",
        "parse_rate_limit_value",
        "Invalid rate-limit value",
        "patch_keys"
    ]:
        if token not in anti:
            failures.append(f"anti-abuse settings route missing rate/logging token: {token}")

    for token in [
        "SECRET_SETTING_KEYS",
        "turn_credential",
        "giphy_api_key",
        "scrub_patch_for_persist",
        "NESTED_SECRET_FIELD_NAMES",
        "credential",
    ]:
        if token not in secrets_policy:
            failures.append(f"secrets policy missing expected token: {token}")

    if failures:
        print("❌ Admin settings/config doctor failed")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("✅ Admin settings/config doctor passed")
    print("   checks: debug config gate, settings persistence metadata, secret redaction, URL/rate validation, recent reauth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
