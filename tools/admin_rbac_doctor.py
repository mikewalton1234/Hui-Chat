#!/usr/bin/env python3
"""Admin/RBAC static doctor for Hui Chat.

This checks the server-side admin route surface without connecting to a
PostgreSQL database. It is intentionally conservative and is meant for release
smoke checks:

  - every /admin or /api/admin route must have an RBAC gate, or be one of the
    explicit tokenized/internal Test Lab dark routes;
  - mutating admin routes must require recent admin re-authentication, or have a
    documented manual/internal gate;
  - every permission used by route decorators must be present in the baseline
    seed/migration permission inventory.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADMIN_FILE = ROOT / "routes_admin_tools.py"
PERMISSIONS_FILE = ROOT / "permissions.py"
SCHEMA_FILE = ROOT / "db" / "schema.py"
SETUP_FILE = ROOT / "interactive_setup.py"
MIGRATIONS_DIR = ROOT / "migrations"

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# These endpoint names are deliberately not decorated because they either abort
# 404 or validate a random, session-bound Test Lab token and then call the
# internal permission check before running.
INTERNAL_GATE_ENDPOINTS = {
    "admin_test_lab_legacy_page",
    "admin_test_lab_legacy_action",
    "admin_test_lab_page",
    "admin_test_lab_readiness",
    "admin_test_lab_run",
    "admin_test_lab_live_user_flow",
    "admin_test_lab_autosplit_cleanup",
}

# Combined GET/POST settings routes enforce recent re-auth inside the body so
# safe GET reads can remain lightweight while writes are stepped up.
MANUAL_REAUTH_ENDPOINTS = {
    "admin_auth_confirm",          # this is the password-confirm endpoint itself
    "admin_security_status",       # GET/POST route; POST has in-body step-up
    "admin_settings_gifs",
    "admin_settings_general",
    "admin_settings_antiabuse",
}

ROUTE_RE = re.compile(r"@app\.(?:route|get|post|put|patch|delete)\((?P<args>.*)\)")
PERM_RE = re.compile(r"require_permission\(['\"](?P<perm>[^'\"]+)['\"]\)")
STRING_RE = re.compile(r"['\"]([^'\"]+)['\"]")
METHODS_RE = re.compile(r"methods\s*=\s*\[(?P<methods>[^\]]+)\]")
PERMISSION_LITERAL_RE = re.compile(r"['\"]([a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*)['\"]")


@dataclass(frozen=True)
class RouteInfo:
    endpoint: str
    rule: str
    methods: tuple[str, ...]
    decorators: tuple[str, ...]
    line: int


def _decorator_route(decorator: str) -> tuple[str, tuple[str, ...]] | None:
    stripped = decorator.strip()
    if stripped.startswith("@app.get("):
        args = stripped[len("@app.get(") : -1]
        matches = STRING_RE.findall(args)
        return (matches[0], ("GET",)) if matches else None
    if stripped.startswith("@app.post("):
        args = stripped[len("@app.post(") : -1]
        matches = STRING_RE.findall(args)
        return (matches[0], ("POST",)) if matches else None
    if stripped.startswith("@app.put("):
        args = stripped[len("@app.put(") : -1]
        matches = STRING_RE.findall(args)
        return (matches[0], ("PUT",)) if matches else None
    if stripped.startswith("@app.patch("):
        args = stripped[len("@app.patch(") : -1]
        matches = STRING_RE.findall(args)
        return (matches[0], ("PATCH",)) if matches else None
    if stripped.startswith("@app.delete("):
        args = stripped[len("@app.delete(") : -1]
        matches = STRING_RE.findall(args)
        return (matches[0], ("DELETE",)) if matches else None
    m = ROUTE_RE.match(stripped)
    if not m:
        return None
    args = m.group("args")
    matches = STRING_RE.findall(args)
    if not matches:
        return None
    rule = matches[0]
    mm = METHODS_RE.search(args)
    if not mm:
        return rule, ("GET",)
    methods = tuple(sorted({m.upper() for m in STRING_RE.findall(mm.group("methods"))}))
    return rule, methods or ("GET",)


def parse_admin_routes() -> list[RouteInfo]:
    lines = ADMIN_FILE.read_text(encoding="utf-8").splitlines()
    routes: list[RouteInfo] = []
    decorators: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("@"):
            decorators.append((idx, stripped))
            continue
        if stripped.startswith("def "):
            endpoint = stripped.split("def ", 1)[1].split("(", 1)[0].strip()
            dec_text = tuple(d for _ln, d in decorators)
            for ln, dec in decorators:
                parsed = _decorator_route(dec)
                if not parsed:
                    continue
                rule, methods = parsed
                if rule.startswith("/admin") or rule.startswith("/api/admin"):
                    routes.append(RouteInfo(endpoint=endpoint, rule=rule, methods=methods, decorators=dec_text, line=ln))
            decorators = []
            continue
        if stripped and not stripped.startswith("#"):
            decorators = []
    return routes


def decorator_permissions(text: str) -> set[str]:
    return {m.group("perm") for m in PERM_RE.finditer(text)}


def seeded_permissions() -> set[str]:
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in [SCHEMA_FILE, SETUP_FILE, *sorted(MIGRATIONS_DIR.glob("m*.py"))]
        if path.exists()
    )
    return set(PERMISSION_LITERAL_RE.findall(text))


def main() -> int:
    admin_text = ADMIN_FILE.read_text(encoding="utf-8")
    routes = parse_admin_routes()
    failures: list[str] = []

    for route in routes:
        dec_blob = "\n".join(route.decorators)
        has_gate = "require_admin" in dec_blob or "require_permission" in dec_blob
        if route.endpoint in INTERNAL_GATE_ENDPOINTS:
            has_gate = True
        if not has_gate:
            failures.append(f"missing RBAC gate: {route.rule} -> {route.endpoint} (line {route.line})")

        mutating = bool(MUTATING_METHODS.intersection(route.methods))
        has_recent = "require_recent_admin_auth" in dec_blob
        if route.endpoint in MANUAL_REAUTH_ENDPOINTS:
            # Verify the body still contains the step-up helper, not just the allow-list entry.
            body_marker = f"def {route.endpoint}"
            start = admin_text.find(body_marker)
            next_def = admin_text.find("\n    def ", start + len(body_marker)) if start >= 0 else -1
            body = admin_text[start : next_def if next_def > start else len(admin_text)] if start >= 0 else ""
            if route.endpoint == "admin_auth_confirm":
                has_recent = "verify_password_and_upgrade" in body and "_mark_admin_reauthenticated" in body
            else:
                has_recent = "_admin_reauth_status" in body and "_admin_reauth_required_response" in body
        if route.endpoint in INTERNAL_GATE_ENDPOINTS:
            has_recent = True
        if mutating and not has_recent:
            failures.append(f"missing recent admin re-auth: {route.rule} {route.methods} -> {route.endpoint} (line {route.line})")

    used = decorator_permissions(admin_text)
    available = seeded_permissions()
    missing = sorted(used - available)
    if missing:
        failures.append("permissions used by decorators but not seeded/backfilled: " + ", ".join(missing))

    if failures:
        print("❌ Admin RBAC doctor failed")
        for item in failures:
            print(f" - {item}")
        return 1

    print("✅ Admin RBAC doctor passed")
    print(f"   admin routes checked: {len(routes)}")
    print(f"   permissions used: {len(used)}")
    print(f"   seeded/backfilled permissions seen: {len(available)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
