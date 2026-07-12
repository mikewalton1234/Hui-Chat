#!/usr/bin/env python3
"""Static checks for UI07 profile UI deep recheck hardening."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        print(f"❌ {rel} missing {token!r}")
        sys.exit(1)

def main() -> None:
    profile = read("static/js/chat_parts/0034_profile_window.js")
    css = read("static/css/chat.css")
    notes = read("UI07_PROFILE_DEEP_RECHECK_NOTES.md")
    checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta368.md")
    for token in [
        "function _profileRevokeObjectUrl",
        "function _profileSafeObjectUrl",
        "function _profileValidateOptionalLink",
        "avatarPreviewObjectUrl",
        "bannerPreviewObjectUrl",
        "cleanupPreviewObjectUrls",
        "ecProfileValidateImageFile(file, 'avatar')",
        "ecProfileValidateImageFile(file, 'banner')",
        "const loadSeq = (Number(win.__ecProfileLoadSeq || 0) || 0) + 1",
        "if (isProfileLoadStale()) return",
        "log.__ecProfileLoadSeq = loadSeq",
        "await _loadProfilePosts(log, u, p, { loadSeq })",
        "_loadProfileGallery(log, u, p, 'all', { loadSeq })",
        "const limitedEditBody = await _profileLimitPostEmoticons",
        "const linkCheck = _profileValidateOptionalLink(link.value, 'profile post link')",
        "const linkCheck = _profileValidateOptionalLink(state.linkUrl || '', 'profile post link')",
        "openProfileWindow(u, { fitMode: String(win?.dataset?.profileOpenMode || 'public') })",
    ]:
        require(profile, token, "0034_profile_window.js")
    for token in [
        "UI07 deep profile recheck",
        ".ecProfileLightboxDialog",
        ".ecProfileOwnerEditorDialog",
        ".ecProfilePostEditPane",
    ]:
        require(css, token, "static/css/chat.css")
    for token in ["0.11.0-beta.368", "object URL", "stale profile", "edit-post"]:
        require(notes, token, "UI07_PROFILE_DEEP_RECHECK_NOTES.md")
    require(checklist, "UI07 deep recheck", "Hui-Chat_Front-End_UI_Audit_Checklist_beta368.md")
    print("✅ UI07 profile UI deep doctor passed")

if __name__ == "__main__":
    main()
