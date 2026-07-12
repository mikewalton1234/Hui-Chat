#!/usr/bin/env python3
"""Static checks for admin reauth once-per-session and deep recheck hotfix."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def fail(message: str) -> None:
    print(f"❌ {message}")
    sys.exit(1)

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        fail(f"{rel} missing {token!r}")

def main() -> None:
    routes = read("routes_admin_tools.py")
    admin = read("admin_panel_inject.py")
    settings = read("settings.example.json")
    server = read("server_config.example.json")
    setup = read("interactive_setup.py")
    security = read("docs/SECURITY.md")
    notes = read("UI08_ADMIN_REAUTH_ONCE_SESSION_HOTFIX_NOTES.md")
    checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta374.md")

    for token in [
        "def _admin_reauth_once_per_session_enabled",
        "admin_reauth_once_per_session",
        "settings.get(\"admin_reauth_once_per_session\", True)",
        "session_fresh",
        "once_per_session",
        "\"remaining_seconds\": None",
        "not once_per_session and age > window",
        "admin_fresh_auth_window_seconds",  # fallback remains available
    ]:
        require(routes, token, "routes_admin_tools.py")

    for token in [
        "v8: UI08 admin reauth deep recheck",
        "Confirm your password once to unlock admin actions for this login session.",
        "oncePerSession",
        "Admin actions unlocked for this login session",
        "admin password confirmed for current login session",
        "Admin reauth deep race guards loaded",
    ]:
        require(admin, token, "admin_panel_inject.py")

    for rel, text in [("settings.example.json", settings), ("server_config.example.json", server), ("interactive_setup.py", setup)]:
        require(text, "admin_reauth_once_per_session", rel)
        require(text, "28800", rel)

    for token in [
        "admin_reauth_once_per_session=true",
        "current login session",
        "rate_limit_admin_reauth",
    ]:
        require(security, token, "docs/SECURITY.md")

    for token in ["0.11.0-beta.373", "one confirmation", "current login/auth session", "admin_reauth_once_per_session"]:
        require(notes, token, "UI08_ADMIN_REAUTH_ONCE_SESSION_HOTFIX_NOTES.md")

    for token in [
        "Current version: **0.11.0-beta.374**",
        "UI08 admin reauth deep recheck",
        "one password confirmation per login session",
        "Hui-Chat-v0.11.0-beta.374-admin-reauth-deep-recheck.zip",
        "UI10 — Mobile/responsive pass",
    ]:
        require(checklist, token, "Hui-Chat_Front-End_UI_Audit_Checklist_beta374.md")

    print("✅ Admin reauth once-per-session/deep recheck doctor passed")

if __name__ == "__main__":
    main()
