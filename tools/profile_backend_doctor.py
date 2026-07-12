#!/usr/bin/env python3
"""Static profile/avatar/banner/profile-post backend checks for Hui Chat.

This doctor verifies the S11 profile invariants without requiring a live DB:

  - profile-visible write actions honor ban/mute sanctions;
  - profile media/avatar/banner uploads honor ban/upload/mute policy as applicable;
  - local profile-post media is authenticated and visibility-gated;
  - profile-post media ownership checks prevent one user from attaching/deleting another user's upload;
  - profile media routes still sniff/serve only image files through safe path resolvers.
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
    main_py = _read("routes_main.py")
    presence_py = _read("realtime/presence_social.py")

    if 'path.startswith("/media/profile-posts/")' not in main_py:
        failures.append("profile-post media path must participate in live-session enforcement")

    denial = _function_body("routes_main.py", "_profile_write_denial")
    if not denial:
        failures.append("profile writes must use a centralized sanction helper")
    else:
        for token in ['is_user_sanctioned(username, "ban")', 'is_user_sanctioned(username, "mute")', 'is_user_sanctioned(username, "upload")']:
            if token not in denial:
                failures.append(f"profile sanction helper missing {token}")

    media_visible = _function_body("routes_main.py", "_profile_post_media_visible_to_viewer")
    if not media_visible:
        failures.append("profile-post media must have a visibility helper")
    else:
        for token in ["_profile_post_media_belongs_to_user", "_get_visible_profile_post_for_viewer", "image_url = %s OR gif_url = %s"]:
            if token not in media_visible:
                failures.append(f"profile-post media visibility helper missing {token}")

    sanitize_media = _function_body("routes_main.py", "_sanitize_profile_post_media_url")
    if "_profile_post_media_belongs_to_user" not in sanitize_media:
        failures.append("profile-post media URL sanitizer must enforce same-owner local media")

    delete_media = _function_body("routes_main.py", "_delete_local_profile_post_media")
    if "_profile_post_media_belongs_to_user" not in delete_media:
        failures.append("profile-post media delete helper must enforce same-owner local media")

    serve_media = _function_body("routes_main.py", "serve_profile_post_media")
    if not serve_media:
        failures.append("missing profile-post media serving route")
    else:
        route_marker = '@app.get("/media/profile-posts/<path:filename>")'
        route_pos = main_py.find(route_marker)
        func_pos = main_py.find('def serve_profile_post_media', route_pos if route_pos >= 0 else 0)
        route_header = main_py[route_pos:func_pos] if route_pos >= 0 and func_pos >= 0 else ""
        if "@jwt_required()" not in route_header:
            failures.append("profile-post media route must require JWT auth")
        if "_profile_post_media_visible_to_viewer" not in serve_media:
            failures.append("profile-post media route must enforce post visibility")
        if "_resolve_profile_post_path" not in serve_media or "_sniff_image_type" not in serve_media:
            failures.append("profile-post media route must use safe path resolution and image sniffing")

    for name in ("create_profile_post", "edit_profile_post", "react_profile_post", "create_profile_post_comment", "pin_profile_post", "feature_profile_post"):
        body = _function_body("routes_main.py", name)
        if "_profile_write_denial_response" not in body:
            failures.append(f"{name} must honor profile write sanctions")

    upload_expectations = {
        "upload_profile_post_image": 'action="media"',
        "upload_profile_avatar": 'action="avatar"',
        "upload_profile_banner": 'action="banner"',
    }
    for name, action_token in upload_expectations.items():
        body = _function_body("routes_main.py", name)
        if "_profile_write_denial_response" not in body or action_token not in body:
            failures.append(f"{name} must use profile upload sanction helper with {action_token}")
        if "_sniff_image_type" not in body:
            failures.append(f"{name} must sniff uploaded image bytes")

    for name in ("serve_uploaded_avatar", "serve_uploaded_profile_banner"):
        body = _function_body("routes_main.py", name)
        if "_resolve_" not in body or "_sniff_image_type" not in body:
            failures.append(f"{name} must use safe path resolution and image sniffing")
        if "as_attachment=False" not in body:
            failures.append(f"{name} should serve display images inline, not forced downloads")

    set_profile = _function_body("realtime/presence_social.py", "handle_set_my_profile")
    if not set_profile:
        failures.append("Socket.IO set_my_profile handler must be covered by profile backend checks")
    else:
        for token in ["_profile_socket_write_denial", "_profile_local_media_owner_ok", "_profile_local_media_change_requires_upload_permission"]:
            if token not in set_profile:
                failures.append(f"set_my_profile missing {token}")
        if 'action="media"' not in set_profile:
            failures.append("set_my_profile must apply upload-sanction checks before changing local avatar/banner media")

    socket_denial = _function_body("realtime/presence_social.py", "_profile_socket_write_denial")
    if not socket_denial:
        failures.append("Socket.IO profile writes must use a centralized sanction helper")
    else:
        for token in ['is_user_sanctioned(username, "ban")', 'is_user_sanctioned(username, "mute")', 'is_user_sanctioned(username, "upload")']:
            if token not in socket_denial:
                failures.append(f"Socket.IO profile sanction helper missing {token}")

    owner_ok = _function_body("realtime/presence_social.py", "_profile_local_media_owner_ok")
    if not owner_ok or "secure_filename" not in owner_ok or "safe_owner" not in owner_ok or "startswith" not in owner_ok:
        failures.append("set_my_profile must require same-owner local avatar/banner media")

    for helper, body_token in {
        "_delete_local_avatar_media": "_local_avatar_media_belongs_to_user",
        "_delete_local_banner_media": "_local_banner_media_belongs_to_user",
    }.items():
        body = _function_body("routes_main.py", helper)
        if body_token not in body or "owner and" not in body:
            failures.append(f"{helper} must enforce same-owner cleanup before deleting local files")

    avatar_body = _function_body("routes_main.py", "upload_profile_avatar")
    if "_delete_local_avatar_media(old_avatar_url, owner=user)" not in avatar_body:
        failures.append("avatar upload cleanup must delete only old local avatar media owned by the user")
    banner_body = _function_body("routes_main.py", "upload_profile_banner")
    if "_delete_local_banner_media(old_banner_url, owner=user)" not in banner_body:
        failures.append("banner upload cleanup must delete only old local banner media owned by the user")

    if failures:
        print("❌ Profile backend doctor failed")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("✅ Profile backend doctor passed")
    print("   checks: profile write sanctions, upload gates, Socket.IO profile editor, owner-safe avatar/banner cleanup, profile-post media visibility")
    return 0


if __name__ == "__main__":
    sys.exit(main())
