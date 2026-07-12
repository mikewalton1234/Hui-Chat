#!/usr/bin/env python3
"""Static file upload/attachment/transfer authorization checks for Hui Chat.

This doctor verifies the server-side S10 file invariants without requiring a
live PostgreSQL database or browser session:

  - modern encrypted DM/group file uploads honor the dedicated upload sanction;
  - torrent uploads and P2P file signaling honor the same upload restriction;
  - DM file metadata and blob downloads require a valid per-participant wrapped key;
  - group file metadata and blob downloads require membership plus a valid per-user key;
  - legacy group attachment upload is disabled by default and, if explicitly re-enabled,
    still applies sanctions/membership and scoped attachment lookup;
  - private blobs/torrents use forced download semantics and private download headers.
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
    groups_py = _read("routes_groups.py")
    rt_files_py = _read("realtime/files.py")

    # Shared helper/gate expectations.
    if "def _private_file_upload_denial" not in main_py:
        failures.append("routes_main.py must centralize private/upload sanction checks")
    else:
        upload_gate = _function_body("routes_main.py", "_private_file_upload_denial")
        for token in ['is_user_sanctioned(actor, "ban")', 'is_user_sanctioned(actor, "upload")', 'is_user_sanctioned(actor, "mute")']:
            if token not in upload_gate:
                failures.append(f"private upload sanction helper missing {token}")

    if "def _dm_file_key_for_user" not in main_py:
        failures.append("DM file access must use a per-participant wrapped-key helper")
    else:
        key_helper = _function_body("routes_main.py", "_dm_file_key_for_user")
        if "_same_username" not in key_helper or "_base64ish" not in key_helper:
            failures.append("DM file key helper must use case-insensitive username matching and validate wrapped keys")

    dm_upload = _function_body("routes_main.py", "upload_dm_file_ciphertext")
    if "disable_dm_files_globally" not in dm_upload:
        failures.append("DM file upload must honor the global DM/file-transfer disable setting")
    if "_private_file_upload_denial" not in dm_upload:
        failures.append("DM file upload must honor ban/upload/mute sanctions")
    if "_either_blocked" not in dm_upload:
        failures.append("DM file upload must honor block policy")
    if "_private_file_quota_response" not in dm_upload:
        failures.append("DM file upload must enforce per-user file quota")
    if "safe_existing_file_under" in dm_upload:
        failures.append("DM file upload should not serve files; safe_existing_file_under belongs on download paths")

    dm_meta = _function_body("routes_main.py", "dm_file_meta")
    dm_blob = _function_body("routes_main.py", "dm_file_blob")
    for name, body in [("dm_file_meta", dm_meta), ("dm_file_blob", dm_blob)]:
        if "_dm_file_key_for_user" not in body:
            failures.append(f"{name} must require a valid participant wrapped key")
        if "_participants_blocked" not in body:
            failures.append(f"{name} must honor post-upload block policy")
        if "user != sender and user != receiver" in body:
            failures.append(f"{name} must not rely on exact-case participant comparisons")
    if "safe_existing_file_under(dm_upload_root" not in dm_blob:
        failures.append("DM blob download must resolve storage path under dm_upload_root")
    if "as_attachment=True" not in dm_blob or "_apply_private_download_headers" not in dm_blob:
        failures.append("DM blob download must be forced attachment with private headers")

    group_upload = _function_body("routes_main.py", "upload_group_file_ciphertext")
    if "disable_group_files_globally" not in group_upload:
        failures.append("group file upload must honor the global group/file-transfer disable setting")
    if "_private_file_upload_denial" not in group_upload:
        failures.append("group file upload must honor ban/upload/mute sanctions")
    if "_is_group_member_username" not in group_upload:
        failures.append("group file upload must verify current group membership")
    if "_canonicalize_group_file_key_map" not in group_upload or "_visible_group_file_recipients" not in group_upload:
        failures.append("group file upload must canonicalize wrapped keys for visible current members")
    if "_private_file_quota_response" not in group_upload:
        failures.append("group file upload must enforce per-user file quota")

    group_meta = _function_body("routes_main.py", "group_file_meta")
    group_blob = _function_body("routes_main.py", "group_file_blob")
    for name, body in [("group_file_meta", group_meta), ("group_file_blob", group_blob)]:
        if "_is_group_member_username" not in body:
            failures.append(f"{name} must verify current group membership")
        if "_group_file_key_for_user" not in body:
            failures.append(f"{name} must require a per-user wrapped key")
        if "_either_blocked" not in body:
            failures.append(f"{name} must honor post-upload sender block policy")
    if "safe_existing_file_under(group_upload_root" not in group_blob:
        failures.append("group blob download must resolve storage path under group_upload_root")
    if "as_attachment=True" not in group_blob or "_apply_private_download_headers" not in group_blob:
        failures.append("group blob download must be forced attachment with private headers")

    torrent_upload = _function_body("routes_main.py", "torrents_upload")
    if "_torrent_upload_enabled" not in torrent_upload:
        failures.append("torrent upload must honor torrent_upload_enabled")
    if "_private_file_upload_denial" not in torrent_upload:
        failures.append("torrent upload must honor ban/upload/mute sanctions")
    if "_requested_torrent_scope" not in torrent_upload:
        failures.append("torrent upload must authorize owner/room scope before saving metadata")
    torrent_download = _function_body("routes_main.py", "torrents_download")
    if "_can_user_access_torrent_metadata" not in torrent_download:
        failures.append("torrent download must authorize metadata scope")
    if "safe_existing_file_under(torrents_root" not in torrent_download:
        failures.append("torrent download must resolve files under torrents_root")
    if "as_attachment=True" not in torrent_download or "_apply_private_download_headers" not in torrent_download:
        failures.append("torrent download must be forced attachment with private headers")

    # Legacy group attachment route must stay deprecated/disabled and safe if re-enabled.
    if 'settings.get("disable_legacy_group_file_upload", True)' not in groups_py:
        failures.append("legacy group attachment upload must be disabled by default")
    legacy_upload = _function_body("routes_groups.py", "group_file_upload")
    if "legacy_group_file_upload_disabled" not in legacy_upload:
        failures.append("legacy group attachment upload route must honor disable switch")
    if "_legacy_group_upload_denial" not in legacy_upload:
        failures.append("legacy group attachment upload must honor ban/upload/mute/group-mute sanctions if re-enabled")
    if "_is_member" not in legacy_upload:
        failures.append("legacy group attachment upload must verify current membership")
    load_attachment = _function_body("routes_groups.py", "_load_attachment_for_group")
    if "_group_history_room_values" not in load_attachment or "m.room = ANY(%s)" not in load_attachment:
        failures.append("legacy group attachment lookup must use scoped group room keys")
    if "safe_existing_file_under(upload_root" not in load_attachment:
        failures.append("legacy group attachment lookup must resolve files under upload_root")
    legacy_blob = _function_body("routes_groups.py", "group_attachment_blob")
    if "as_attachment=True" not in legacy_blob or "apply_safe_download_headers" not in legacy_blob:
        failures.append("legacy group attachment blob must be forced attachment with safe private headers")

    if "def _p2p_upload_sanction_denial" not in rt_files_py or "def _p2p_participants_upload_allowed" not in rt_files_py:
        failures.append("P2P file signaling must centralize upload-sanction checks for both participants")

    p2p_offer = _function_body("realtime/files.py", "handle_p2p_file_offer")
    if "_p2p_disabled" not in p2p_offer:
        failures.append("P2P file offer must honor global P2P/file-transfer disable settings")
    if '_p2p_upload_sanction_denial(sender, role="sender")' not in p2p_offer:
        failures.append("P2P file offer must honor sender upload sanctions")
    if '_p2p_upload_sanction_denial(to, role="receiver")' not in p2p_offer:
        failures.append("P2P file offer must reject upload-sanctioned receivers")
    if "_either_blocked" not in p2p_offer or "_require_not_sanctioned" not in p2p_offer:
        failures.append("P2P file offer must honor DM sanctions and block policy")
    if "max_dm_file_bytes" not in p2p_offer:
        failures.append("P2P file offer metadata size must use the DM file size ceiling")

    for name in ("handle_p2p_file_answer", "handle_p2p_file_ice"):
        body = _function_body("realtime/files.py", name)
        if "_p2p_participants_upload_allowed(sender, to)" not in body:
            failures.append(f"{name} must block upload-sanctioned P2P participants")
        if "_drop_p2p_file_session_for_pair(transfer_id, sender, to)" not in body:
            failures.append(f"{name} must drop active P2P session when participant authorization fails")

    if failures:
        print("❌ File auth doctor failed")
        for f in failures:
            print(f" - {f}")
        return 1

    print("✅ File auth doctor passed")
    print("   checks: upload sanctions, DM/group wrapped-key gates, scoped legacy attachments, strict P2P/torrent authorization, forced private downloads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
