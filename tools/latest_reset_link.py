#!/usr/bin/env python3
"""Print the latest locally mirrored Hui Chat password reset link.

This reads logs/reset_links.log by default. Hui Chat writes this file only for
localhost/LAN reset requests unless password_reset_spool_allow_remote is enabled.
It exists for development and deliverability troubleshooting when SMTP accepts a
message but the inbox provider hides, delays, blocks, or quarantines it.

Examples:
  python tools/latest_reset_link.py
  python tools/latest_reset_link.py --email you@example.com
  python tools/latest_reset_link.py --user admin --show-all
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_line(line: str) -> dict[str, str]:
    out: dict[str, str] = {"raw": line.rstrip("\n")}
    parts = line.rstrip("\n").split("\t")
    if parts:
        out["timestamp"] = parts[0]
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Show latest locally mirrored password reset link.")
    ap.add_argument("--file", default="logs/reset_links.log", help="Reset-link mirror file")
    ap.add_argument("--email", default="", help="Filter by recipient email")
    ap.add_argument("--user", default="", help="Filter by username")
    ap.add_argument("--show-all", action="store_true", help="Show all matching entries instead of only the newest")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"❌ No reset-link mirror file found: {path}")
        print("   Submit /forgot-password from localhost/LAN once, then run this again.")
        return 1

    wanted_email = args.email.strip().lower()
    wanted_user = args.user.strip().lower()

    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        item = parse_line(line)
        if wanted_email and item.get("email", "").lower() != wanted_email:
            continue
        if wanted_user and item.get("user", "").lower() != wanted_user:
            continue
        if not item.get("url"):
            continue
        entries.append(item)

    if not entries:
        print("❌ No matching reset links found in the mirror file.")
        return 1

    selected = entries if args.show_all else [entries[-1]]
    for idx, item in enumerate(selected):
        print(f"timestamp: {item.get('timestamp', '')}")
        print(f"user:      {item.get('user', '')}")
        print(f"email:     {item.get('email', '')}")
        print(f"smtp:      {item.get('smtp', '')}")
        print(f"reason:    {item.get('reason', '')}")
        print(f"url:       {item.get('url', '')}")
        if idx != len(selected) - 1:
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
