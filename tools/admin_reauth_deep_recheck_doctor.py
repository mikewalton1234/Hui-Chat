#!/usr/bin/env python3
"""Static checks for beta.374 admin reauth deep recheck."""
from __future__ import annotations
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_BETA = 374

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def fail(message: str) -> None:
    print(f"❌ {message}")
    sys.exit(1)

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        fail(f"{rel} missing {token!r}")

def beta_number(version: str) -> int:
    match = re.search(r"beta\.(\d+)", version)
    if not match:
        fail(f"VERSION.txt has unexpected beta version: {version!r}")
    return int(match.group(1))

def main() -> None:
    admin = read("admin_panel_inject.py")
    routes = read("routes_admin_tools.py")
    notes = read("UI08_ADMIN_REAUTH_DEEP_RECHECK_NOTES.md")
    checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta374.md")
    version = read("VERSION.txt").strip()

    for token in [
        "v8: UI08 admin reauth deep recheck",
        "let adminReauthStatusPromise = null",
        "let adminReauthSessionCache",
        "function _adminReauthSessionKey",
        "function _markAdminReauthConfirmed",
        "function _clearAdminReauthConfirmed",
        "function _adminReauthCacheMatches",
        "async function ensureAdminReauthAlreadyFresh",
        "fetch('/admin/auth/status'",
        "_adminReauthCacheMatches(meta) || await ensureAdminReauthAlreadyFresh(meta)",
        "_markAdminReauthConfirmed(j)",
        "Admin reauth deep race guards loaded",
    ]:
        require(admin, token, "admin_panel_inject.py")

    for token in [
        '"sid": status.get("sid")',
        '"sid": status.get("sid") or sid',
        '"once_per_session": bool(status.get("once_per_session"))',
        '"reauth_required": bool(status.get("required"))',
    ]:
        require(routes, token, "routes_admin_tools.py")

    for token in [
        "Version: **0.11.0-beta.374**",
        "auth-session id",
        "428 admin_reauth_required",
        "/admin/auth/status",
        "same login session",
    ]:
        require(notes, token, "UI08_ADMIN_REAUTH_DEEP_RECHECK_NOTES.md")

    for token in [
        "Current version: **0.11.0-beta.374**",
        "UI08 admin reauth deep recheck",
        "repeated prompt race/session safety",
        "Hui-Chat-v0.11.0-beta.374-admin-reauth-deep-recheck.zip",
        "UI10 — Mobile/responsive pass",
    ]:
        require(checklist, token, "Hui-Chat_Front-End_UI_Audit_Checklist_beta374.md")

    if beta_number(version) < MIN_BETA:
        fail(f"VERSION.txt is {version!r}, expected beta.{MIN_BETA} or newer")

    print("✅ Admin reauth deep recheck doctor passed")

if __name__ == "__main__":
    main()
