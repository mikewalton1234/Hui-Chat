#!/usr/bin/env python3
"""Static checks for UI07 profile/avatar/banner/post front-end hardening."""
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
    editor = read("static/js/chat_parts/0033b_my_profile_editor.js")
    avatar = read("static/js/chat_parts/0033_avatar_presets_upload.js")
    css = read("static/css/chat.css")
    notes = read("UI07_PROFILE_AVATAR_BANNER_POST_UI_NOTES.md")
    checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta368.md")

    for token in [
        "const EC_PROFILE_POST_ACTION_PENDING = new Set()",
        "const EC_PROFILE_COMMENT_ACTION_PENDING = new Set()",
        "function _profileSetButtonBusy",
        "function _profileLimitPostEmoticons",
        "function _profileWindowKey",
        "const id = 'profile:' + _profileWindowKey(u)",
        "if (root.__ecProfileComposerBusy) return",
        "Add text, a GIF, a photo, or a link before publishing.",
        "function _profileValidateOptionalLink",
        "EC_PROFILE_POST_ACTION_PENDING.add(pendingKey)",
        "EC_PROFILE_COMMENT_ACTION_PENDING.add(pendingKey)",
        "if (visibility) visibility.value = _profileDefaultPostVisibility(profile)",
    ]:
        require(profile, token, "0034_profile_window.js")

    for token in [
        "ecProfileValidateImageFile(file, 'avatar')",
        "ecProfileValidateImageFile(file, 'banner')",
        "ecProfileValidateImageFile(file, 'post_image')",
    ]:
        require(avatar + profile, token, "profile upload validators")

    for token in [
        "const initialPayloadSnapshot = collectProfilePayload()",
        "btnSaveProfileNotifications",
        "DiceBear avatar builder",
    ]:
        require(editor, token, "0033b_my_profile_editor.js")

    for token in [
        "UI07 profile/avatar/banner/post UI hardening",
        ".ecProfileWindow button.isBusy",
        "@media (hover: none), (pointer: coarse)",
        ".ecProfileWindow .ecProfilePostLinkInline",
    ]:
        require(css, token, "static/css/chat.css")

    for token in ["0.11.0-beta.367", "Profile post composer", "busy lock", "Touch/coarse-pointer"]:
        require(notes, token, "UI07_PROFILE_AVATAR_BANNER_POST_UI_NOTES.md")
    require(checklist, "[x] **UI07 — Profile/avatar/banner/post UI**", "Hui-Chat_Front-End_UI_Audit_Checklist_beta367.md")
    print("✅ UI07 profile UI doctor passed")

if __name__ == "__main__":
    main()
