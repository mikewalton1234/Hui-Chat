#!/usr/bin/env python3
"""tools/smtp_test.py

Quick SMTP smoke test for Hui Chat config.

Usage:
  source .venv/bin/activate
  python tools/smtp_test.py --config server_config.json --to you@example.com

This script ONLY sends a simple plaintext email (no reset tokens).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# When this file is run as `python tools/smtp_test.py`, Python puts the
# `tools/` directory on sys.path, not the Hui Chat project root. Add the
# project root so imports like `from emailer import send_email` work from
# a normal terminal session.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from emailer import send_email
from constants import server_display_name


def _server_display_name(settings: dict | None = None) -> str:
    return server_display_name(settings)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="server_config.json", help="Path to server_config.json")
    ap.add_argument("--to", required=True, help="Recipient email address")
    ap.add_argument("--subject", default="", help="Email subject. Default: '<server name> SMTP test'.")
    args = ap.parse_args()

    try:
        with open(args.config, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception as e:
        print(f"❌ Could not read config {args.config}: {e}")
        return 2

    server_label = _server_display_name(settings)
    subject = str(args.subject or "").strip() or f"{server_label} SMTP test"
    ok, info = send_email(
        settings,
        to_email=args.to,
        subject=subject,
        body_text=f"{server_label} SMTP test: if you received this, SMTP is working.",
    )

    if ok:
        print("✅ SMTP test email sent")
        return 0

    print(f"❌ SMTP test failed: {info}")
    # Common hints
    if info == "not_configured":
        print("Hint: set smtp_enabled=true and provide smtp_host/smtp_username/smtp_password in server_config.json")
    elif info == "invalid_from_localhost":
        print("Hint: set smtp_from to a real verified sender address; external SMTP providers will not reliably deliver no-reply@localhost")
    elif str(info).startswith("smtp_error:"):
        print("Hint: run: python tools/smtp_doctor.py --config server_config.json --to YOUR_EMAIL --send --try-brevo-modes --write-working-mode")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
