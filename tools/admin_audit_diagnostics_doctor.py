#!/usr/bin/env python3
"""Static audit/analytics/admin diagnostics backend checks for Hui Chat.

This S17 doctor verifies browser-facing admin reporting surfaces without a live
DB.  It checks that diagnostics and user timelines use the audit permission,
that diagnostics are recently re-authenticated and sanitized, that audit rows are
redacted before JSON output, and that optional dashboard queries recover cleanly
after PostgreSQL transaction errors.
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


def _decorator_window(text: str, fn_name: str, chars: int = 320) -> str:
    marker = f"def {fn_name}"
    idx = text.find(marker)
    if idx < 0:
        return ""
    return text[max(0, idx - chars):idx]


def main() -> int:
    failures: list[str] = []
    routes = _read("routes_admin_tools.py")

    for token in [
        "is_local_request as _is_local_request",
        "_ADMIN_AUDIT_SECRET_VALUE_RE",
        "def _admin_safe_audit_text",
        "def _admin_safe_audit_event",
        "def _admin_recover_query_error",
        "def _admin_sanitize_preflight_snapshot",
        "_ADMIN_ABSOLUTE_PATH_RE",
    ]:
        if token not in routes:
            failures.append(f"routes_admin_tools missing audit/diagnostic safety token: {token}")

    diagnostics = _function_body("routes_admin_tools.py", "admin_diagnostics")
    diagnostics_decorators = _decorator_window(routes, "admin_diagnostics")
    if not diagnostics:
        failures.append("missing admin_diagnostics route")
    else:
        for token in ['@require_permission("admin:audit")', "@require_recent_admin_auth"]:
            if token not in diagnostics_decorators:
                failures.append(f"admin_diagnostics missing decorator {token}")
        for token in [
            "_admin_sanitize_preflight_snapshot(current)",
            "_admin_sanitize_preflight_snapshot(startup_snapshot)",
            '"db_identity": "available"',
            '"redacted": True',
        ]:
            if token not in diagnostics:
                failures.append(f"admin_diagnostics missing sanitized-output token: {token}")
        if '"db_identity": _safe_db_identity()' in diagnostics:
            failures.append("admin_diagnostics must not return raw db_identity")

    timeline = _function_body("routes_admin_tools.py", "admin_user_activity_timeline")
    timeline_decorators = _decorator_window(routes, "admin_user_activity_timeline")
    if not timeline:
        failures.append("missing admin_user_activity_timeline route")
    else:
        if '@require_permission("admin:audit")' not in timeline_decorators:
            failures.append("admin_user_activity_timeline must require admin:audit")
        for token in [
            "_canonical_user_or_error(username)",
            "_admin_safe_audit_text(summary",
            "_admin_safe_audit_text(details",
            "recover_query_error()",
        ]:
            if token not in timeline:
                failures.append(f"activity timeline missing safety token: {token}")

    audit_recent = _function_body("routes_admin_tools.py", "admin_audit_recent")
    if not audit_recent:
        failures.append("missing admin_audit_recent route")
    else:
        if '_admin_safe_audit_event(r[0], r[1], r[2], r[3], r[4])' not in audit_recent:
            failures.append("admin_audit_recent must redact audit rows via _admin_safe_audit_event")
        for token in ["len(value) > 96", "limit = max(1, min(limit, 200))", "_admin_like_pattern"]:
            if token not in audit_recent:
                failures.append(f"admin_audit_recent missing query bound token: {token}")

    analytics = _function_body("routes_admin_tools.py", "admin_analytics_overview")
    if not analytics:
        failures.append("missing admin_analytics_overview route")
    else:
        if '@require_permission("admin:audit")' not in _decorator_window(routes, "admin_analytics_overview"):
            failures.append("admin_analytics_overview must require admin:audit")
        if "_admin_recover_query_error(conn, \"admin_analytics_overview\")" not in analytics:
            failures.append("admin_analytics_overview must recover DB transaction state after optional query failures")
        for token in ["window_24h", "window_7d", "_analytics_bucketed_top_targets"]:
            if token not in analytics:
                failures.append(f"admin_analytics_overview missing bounded summary token: {token}")

    moderation = _function_body("routes_admin_tools.py", "admin_moderation_overview")
    if not moderation:
        failures.append("missing admin_moderation_overview route")
    else:
        if '@require_permission("admin:audit")' not in _decorator_window(routes, "admin_moderation_overview"):
            failures.append("admin_moderation_overview must require admin:audit")
        for token in [
            "WHERE expires_at IS NULL OR expires_at > NOW()",
            "_admin_safe_audit_event(row[0], row[1], row[2], row[3], row[4])",
            "_admin_recover_query_error(conn, \"admin_moderation_overview.summary\")",
            "_admin_recover_query_error(conn, \"admin_moderation_overview.active\")",
            "_admin_recover_query_error(conn, \"admin_moderation_overview.recent\")",
        ]:
            if token not in moderation:
                failures.append(f"admin_moderation_overview missing safety token: {token}")

    testlab_link = _function_body("routes_admin_tools.py", "admin_test_lab_link")
    testlab_link_decorators = _decorator_window(routes, "admin_test_lab_link")
    for token in ["@require_permission('admin:test_lab')", "@require_recent_admin_auth"]:
        if token not in testlab_link_decorators:
            failures.append(f"admin_test_lab_link missing decorator {token}")
    if "referrer_policy" not in testlab_link:
        failures.append("admin_test_lab_link should report no-referrer policy")

    if failures:
        print("❌ Admin audit/diagnostics doctor failed")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("✅ Admin audit/diagnostics doctor passed")
    print("   checks: diagnostics gate/reauth, sanitized preflight output, audit redaction, analytics recovery, active-sanction filtering")
    return 0


if __name__ == "__main__":
    sys.exit(main())
