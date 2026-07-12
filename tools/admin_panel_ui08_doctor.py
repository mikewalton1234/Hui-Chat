#!/usr/bin/env python3
"""Static checks for UI08 admin panel UI hardening."""
from __future__ import annotations
from pathlib import Path
import re
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
    notes = read("UI08_ADMIN_PANEL_UI_NOTES.md") + "\n" + read("UI08_ADMIN_PANEL_DEEP_RECHECK_NOTES.md")
    checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta370.md")

    for token in [
        "v6: UI08 deep recheck",
        "const ecapPendingActions = new Set()",
        "function setButtonBusy",
        "async function withAdminAction",
        "aria-busy",
        "ecap-btn.isBusy",
        "role:'tab'",
        "role:'tabpanel'",
        "aria-selected",
        "aria-label':'Refresh admin panel'",
        "let userSearchSeq = 0",
        "let userDetailSeq = 0",
        "let userTimelineSeq = 0",
        "let roomsRefreshSeq = 0",
        "if (seq !== userSearchSeq) return",
        "if (seq !== userDetailSeq",
        "if (seq !== userTimelineSeq",
        "if (seq !== roomsRefreshSeq) return",
        "withAdminAction(cuBtn, 'create-user'",
        "withAdminAction(btnRefresh, 'admin:refresh-all'",
        "withAdminAction(e.currentTarget, 'settings:apply'",
        "withAdminAction(e.currentTarget, 'anti-abuse:apply'",
        "withAdminAction(e.currentTarget, 'roles:create'",
        "withAdminAction(e.currentTarget, 'audit:refresh'",
        "Admin reauth deep race guards loaded",
    ]:
        require(admin, token, "admin_panel_inject.py")

    if admin.count("textContent = safe(msg)") or admin.count("textContent = safe(meta"):
        fail("admin_panel_inject.py still double-escapes toast text")

    for token in ["0.11.0-beta.370", "busy-state", "stale-response", "ARIA", "small-width"]:
        require(notes, token, "UI08_ADMIN_PANEL_UI_NOTES.md")

    for token in ["UI08 — Admin panel UI", "beta.370", "UI09 — Settings modal"]:
        require(checklist, token, "Hui-Chat_Front-End_UI_Audit_Checklist_beta370.md")

    print("✅ UI08 admin panel UI doctor passed")

if __name__ == "__main__":
    main()
