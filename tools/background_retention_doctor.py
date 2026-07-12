#!/usr/bin/env python3
"""Static background jobs, retention, and cleanup backend checks for Hui Chat.

This S18 doctor verifies cleanup invariants without requiring a live database:

  - janitor exposes a one-cycle runner and status snapshot;
  - cleanup tasks are fail-soft and recorded independently;
  - stale auth/session/password-reset artifacts are cleaned with bounded,
    parameterized, limited SQL;
  - the standalone janitor runner supports --once smoke checks;
  - admin settings expose/clamp the cleanup TTL and batch-limit controls.
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


def main() -> int:
    failures: list[str] = []
    janitor = _read("janitor.py")
    runner = _read("janitor_runner.py")
    cleanup = _read("maintenance_cleanup.py")
    routes = _read("routes_admin_tools.py")

    for token in [
        "def run_janitor_cycle",
        "def janitor_status_snapshot",
        "def configure_janitor_runtime",
        "cleanup_expired_auth_artifacts",
        "privacy_retention_batch_limit",
        "auth_cleanup_batch_limit",
        "consecutive_failures",
        "failed_tasks",
    ]:
        if token not in janitor:
            failures.append(f"janitor.py missing cleanup/status token: {token}")

    run_cycle = _function_body("janitor.py", "run_janitor_cycle")
    for token in [
        "cleanup_expired_custom_rooms",
        "cleanup_expired_autoscaled_rooms",
        "cleanup_expired_room_messages",
        "apply_privacy_retention(settings, limit=retention_limit)",
        "cleanup_expired_auth_artifacts(settings, limit=auth_limit)",
        "cycle[\"tasks\"]",
        "failed = [k for k, v in cycle[\"tasks\"].items()",
    ]:
        if token not in run_cycle:
            failures.append(f"run_janitor_cycle missing task token: {token}")

    run_task = _function_body("janitor.py", "_run_task")
    for token in ["try:", "except Exception as exc", "cycle[\"ok\"] = False", "logging.exception"]:
        if token not in run_task:
            failures.append(f"_run_task must be fail-soft and observable: {token}")

    auth_cleanup = _function_body("maintenance_cleanup.py", "cleanup_expired_auth_artifacts")
    for token in [
        "cleanup_expired_auth_enabled",
        "auth_token_retention_days",
        "revoked_session_retention_days",
        "password_reset_token_retention_days",
        "orphan_auth_retention_days",
        "LIMIT %s",
        "DELETE FROM auth_tokens",
        "DELETE FROM auth_sessions",
        "DELETE FROM password_reset_tokens",
        "NOT EXISTS (",
        "LOWER(u.username) = LOWER",
        "conn.commit()",
        "conn.rollback()",
    ]:
        if token not in auth_cleanup:
            failures.append(f"cleanup_expired_auth_artifacts missing safety token: {token}")
    if "DELETE FROM users" in auth_cleanup:
        failures.append("background auth cleanup must never delete users")

    for token in [
        "--once",
        "--json",
        "run_janitor_cycle(settings, use_live_counts=False)",
        "SystemExit(0 if result.get(\"ok\") else 2)",
    ]:
        if token not in runner:
            failures.append(f"janitor_runner.py missing run-once token: {token}")

    for token in [
        "janitor_status_snapshot",
        '"janitor_status": janitor_status',
        '"cleanup_expired_auth_enabled": "bool"',
        '"cleanup_orphan_auth_enabled": "bool"',
        '"auth_token_retention_days": "int"',
        '"revoked_session_retention_days": "int"',
        '"password_reset_token_retention_days": "int"',
        '"orphan_auth_retention_days": "int"',
        '"auth_cleanup_batch_limit": "int"',
        '"privacy_retention_batch_limit": "int"',
        'for key, default_value in (',
        'patch[key] = max(1, min(int(patch[key]), 3650))',
        'patch["orphan_auth_retention_days"] = max(0, min(int(patch["orphan_auth_retention_days"]), 3650))',
        'patch[key] = max(1, min(int(patch[key]), 10000))',
    ]:
        if token not in routes:
            failures.append(f"routes_admin_tools.py missing cleanup settings/status token: {token}")

    if failures:
        print("❌ Background retention/cleanup doctor failed")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("✅ Background retention/cleanup doctor passed")
    print("   checks: one-cycle janitor, fail-soft tasks, stale auth cleanup, runner --once, admin cleanup settings/status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
