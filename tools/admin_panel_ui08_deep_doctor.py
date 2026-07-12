#!/usr/bin/env python3
"""Static checks for UI08 deep admin panel recheck."""
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
    admin = read("admin_panel_inject.py")
    notes = read("UI08_ADMIN_PANEL_DEEP_RECHECK_NOTES.md")
    checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta370.md")

    for token in [
        "v6: UI08 deep recheck",
        "function normalizeHeadersForAdminFetch",
        "function attachFreshAccessCsrf",
        "attachFreshAccessCsrf(options, true)",
        "btn.dataset.ecapOriginalHtml",
        "role:'status'",
        "Dismiss admin notification",
        "ecapDialogTitle_",
        "priorFocus",
        "ArrowRight",
        "section.setAttribute('aria-hidden'",
        "let profilePostModerationSeq = 0",
        "let profileReportsSeq = 0",
        "let profileBadgesSeq = 0",
        "let rolesRefreshSeq = 0",
        "let rolePermissionSeq = 0",
        "seq !== profilePostModerationSeq",
        "seq !== profileReportsSeq",
        "seq !== profileBadgesSeq",
        "seq !== rolesRefreshSeq",
        "seq !== rolePermissionSeq",
        "rooms:${r.name}:clear",
        "rooms:${r.name}:delete",
        "rooms:kick-user",
        "rooms:broadcast",
        "safety:ban-ip",
        "safety:incident-apply",
        "roles:${r.name}:delete",
        "roles:user:${username}:${roleName}:remove",
        "cb.setAttribute('aria-busy', 'true')",
        "Admin reauth deep race guards loaded",
    ]:
        require(admin, token, "admin_panel_inject.py")

    for token in ["0.11.0-beta.370", "CSRF", "keyboard navigation", "stale-response guards", "duplicate-action guards"]:
        require(notes, token, "UI08_ADMIN_PANEL_DEEP_RECHECK_NOTES.md")

    for token in ["Current version: **0.11.0-beta.370**", "UI08 deep recheck", "UI09 — Settings modal", "Hui-Chat-v0.11.0-beta.370-ui08-deep-admin-panel-recheck.zip"]:
        require(checklist, token, "Hui-Chat_Front-End_UI_Audit_Checklist_beta370.md")

    print("✅ UI08 deep admin panel doctor passed")

if __name__ == "__main__":
    main()
