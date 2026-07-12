#!/usr/bin/env python3
"""Hui Chat SMTP doctor.

This is safer and more explicit than triggering /forgot-password repeatedly.
It can verify TCP reachability, TLS/STARTTLS/login, send one diagnostic email,
and optionally normalize Brevo settings to the recommended 587 + STARTTLS mode.

Examples:
  source .venv/bin/activate
  python tools/smtp_doctor.py --config server_config.json --to you@example.com --send
  python tools/smtp_doctor.py --config server_config.json --fix-brevo-config
  python tools/smtp_doctor.py --config server_config.json --to you@example.com --send --try-brevo-modes --write-working-mode
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import socket
import ssl
import smtplib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from emailer import describe_smtp_settings, send_email  # noqa: E402
from constants import server_display_name  # noqa: E402

BREVO_HOST = "smtp-relay.brevo.com"
BREVO_MODES = [
    {"name": "Brevo recommended", "smtp_host": BREVO_HOST, "smtp_port": 587, "smtp_use_starttls": True, "smtp_use_ssl": False},
    {"name": "Brevo fallback", "smtp_host": BREVO_HOST, "smtp_port": 2525, "smtp_use_starttls": True, "smtp_use_ssl": False},
    {"name": "Brevo implicit TLS", "smtp_host": BREVO_HOST, "smtp_port": 465, "smtp_use_starttls": False, "smtp_use_ssl": True},
]

def _server_display_name(settings: dict[str, Any] | None = None) -> str:
    return server_display_name(settings)


ENV_OVERRIDES = [
    "HUI_SMTP_ENABLED", "SMTP_ENABLED",
    "HUI_SMTP_HOST", "SMTP_HOST",
    "HUI_SMTP_PORT", "SMTP_PORT",
    "HUI_SMTP_USERNAME", "HUI_SMTP_USER", "SMTP_USERNAME", "SMTP_USER",
    "HUI_SMTP_PASSWORD", "HUI_SMTP_PASS", "SMTP_PASSWORD", "SMTP_PASS",
    "HUI_SMTP_STARTTLS", "SMTP_STARTTLS",
    "HUI_SMTP_SSL", "SMTP_SSL",
    "HUI_SMTP_FROM", "SMTP_FROM",
    "HUI_SMTP_TIMEOUT", "SMTP_TIMEOUT",
]


def env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def smtp_username(settings: dict[str, Any]) -> str:
    return str(
        env_first("HUI_SMTP_USERNAME", "HUI_SMTP_USER", "SMTP_USERNAME", "SMTP_USER")
        or settings.get("smtp_username")
        or settings.get("smtp_user")
        or ""
    ).strip()


def smtp_password(settings: dict[str, Any]) -> str:
    return str(
        env_first("HUI_SMTP_PASSWORD", "HUI_SMTP_PASS", "SMTP_PASSWORD", "SMTP_PASS")
        or settings.get("smtp_password")
        or settings.get("smtp_pass")
        or ""
    )


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return obj


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def normalize_brevo(settings: dict[str, Any]) -> dict[str, Any]:
    fixed = copy.deepcopy(settings)
    fixed["smtp_enabled"] = True
    fixed["smtp_provider"] = "brevo"
    fixed["smtp_host"] = BREVO_HOST
    fixed["smtp_port"] = 587
    fixed["smtp_use_starttls"] = True
    fixed["smtp_use_ssl"] = False
    fixed["smtp_timeout_seconds"] = max(30, int(fixed.get("smtp_timeout_seconds") or 30))
    return fixed


def print_effective(settings: dict[str, Any]) -> None:
    d = describe_smtp_settings(settings)
    print("\nEffective SMTP config (secrets redacted):")
    for key in ["enabled", "provider", "host", "port", "starttls", "use_ssl", "username_set", "password_set", "from_email", "timeout"]:
        print(f"  {key}: {d.get(key)}")
    if d.get("mode_hint"):
        print(f"  hint: {d['mode_hint']}")
    if d.get("from_warning"):
        print(f"  from warning: {d['from_warning']}")
        if d["from_warning"] == "invalid_from_localhost":
            print("  fix: set smtp_from to a real verified Brevo sender, not no-reply@localhost")


def warn_env_overrides() -> None:
    active = [name for name in ENV_OVERRIDES if os.getenv(name)]
    if active:
        print("\n⚠️  SMTP environment overrides are set and will beat server_config.json:")
        for name in active:
            value = "***" if "PASS" in name or "PASSWORD" in name else os.getenv(name)
            print(f"  {name}={value}")


def tcp_probe(host: str, port: int, timeout: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"TCP connection OK to {host}:{port}"
    except Exception as exc:
        return False, f"TCP connection failed to {host}:{port}: {type(exc).__name__}: {exc}"


def handshake_probe(settings: dict[str, Any]) -> tuple[bool, str]:
    d = describe_smtp_settings(settings)
    host = str(d.get("host") or "").strip()
    port = int(d.get("port") or 0)
    timeout = int(d.get("timeout") or 20)
    use_ssl = bool(d.get("use_ssl"))
    starttls = bool(d.get("starttls"))
    username = smtp_username(settings)
    password = smtp_password(settings)

    if not host or not port:
        return False, "SMTP host/port missing"
    if d.get("from_warning") == "invalid_from_localhost":
        return False, "smtp_from uses localhost; Brevo needs a real verified sender address"

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as client:
                client.ehlo()
                if username:
                    if not password:
                        return False, "SMTP username is set but password/key is blank"
                    client.login(username, password)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as client:
                client.ehlo()
                if starttls:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if username:
                    if not password:
                        return False, "SMTP username is set but password/key is blank"
                    client.login(username, password)
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"SMTP login failed: {exc.smtp_code} {exc.smtp_error!r}. For Brevo, use the SMTP key, not the API key."
    except Exception as exc:
        return False, f"SMTP handshake/login failed: {type(exc).__name__}: {exc}"

    mode = "SSL" if use_ssl else "STARTTLS" if starttls else "plain SMTP"
    return True, f"SMTP handshake/login OK over {mode} to {host}:{port}"


def send_test(settings: dict[str, Any], to_addr: str, subject: str) -> tuple[bool, str]:
    now = datetime.now(timezone.utc).isoformat()
    server_label = _server_display_name(settings)
    subject = str(subject or "").strip() or f"{server_label} SMTP doctor test"
    body = (
        f"{server_label} SMTP doctor test.\n\n"
        f"Created at: {now}\n"
        "This is not a password reset email. If you received this, SMTP submission works.\n"
    )
    return send_email(settings, to_email=to_addr, subject=subject, body_text=body)


def overlay(settings: dict[str, Any], mode: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(settings)
    for k, v in mode.items():
        if k != "name":
            out[k] = v
    out["smtp_enabled"] = True
    out["smtp_provider"] = "brevo"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose this server's SMTP/Brevo password-reset email delivery.")
    ap.add_argument("--config", default="server_config.json", help="Path to server_config.json")
    ap.add_argument("--to", help="Recipient for a real test email")
    ap.add_argument("--subject", default="", help="Subject for the real test email. Default: '<server name> SMTP doctor test'.")
    ap.add_argument("--send", action="store_true", help="Send one real diagnostic email")
    ap.add_argument("--try-brevo-modes", action="store_true", help="Try Brevo 587/465/2525 modes until one accepts the test email")
    ap.add_argument("--write-working-mode", action="store_true", help="After --try-brevo-modes succeeds, save the working mode to config")
    ap.add_argument("--fix-brevo-config", action="store_true", help="Rewrite config to Brevo recommended 587 + STARTTLS")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    try:
        settings = load_json(cfg_path)
    except Exception as exc:
        print(f"❌ Could not read {cfg_path}: {exc}")
        return 2

    if args.fix_brevo_config:
        settings = normalize_brevo(settings)
        save_json(cfg_path, settings)
        print(f"✅ Wrote Brevo recommended SMTP mode to {cfg_path}: 587 + STARTTLS")
        print("   Confirm smtp_username, smtp_password/SMTP_PASSWORD, and smtp_from are still correct.")

    warn_env_overrides()
    print_effective(settings)

    d = describe_smtp_settings(settings)
    if not d.get("enabled"):
        print("\n❌ SMTP is disabled. Set smtp_enabled=true or run this with --fix-brevo-config first.")
        return 1

    host = str(d.get("host") or "").strip()
    port = int(d.get("port") or 0)
    timeout = int(d.get("timeout") or 20)
    ok, msg = tcp_probe(host, port, timeout)
    print(f"\n{'✅' if ok else '❌'} {msg}")
    if not ok:
        print("Hint: try port 587 first for Brevo; if blocked by your network/ISP, try 465 or 2525.")
        return 1

    ok, msg = handshake_probe(settings)
    print(f"{'✅' if ok else '❌'} {msg}")
    if not ok and not args.try_brevo_modes:
        return 1

    if args.send and not args.to:
        print("❌ --send requires --to you@example.com")
        return 2

    if args.try_brevo_modes:
        if not args.to:
            print("❌ --try-brevo-modes requires --to because it sends a real test email")
            return 2
        print("\nTrying Brevo send modes, stopping after the first accepted message:")
        for mode in BREVO_MODES:
            candidate = overlay(settings, mode)
            print(f"\nMode: {mode['name']} ({mode['smtp_port']}, {'SSL' if mode['smtp_use_ssl'] else 'STARTTLS'})")
            print_effective(candidate)
            h_ok, h_msg = handshake_probe(candidate)
            print(f"{'✅' if h_ok else '❌'} {h_msg}")
            if not h_ok:
                continue
            s_ok, s_info = send_test(candidate, args.to, f"{args.subject} - {mode['name']}")
            print(f"{'✅' if s_ok else '❌'} SMTP send result: {s_info}")
            if s_ok:
                print("\n✅ Brevo accepted the diagnostic email. Now check Inbox, Spam, and Brevo Transactional logs.")
                if args.write_working_mode:
                    settings = overlay(settings, mode)
                    save_json(cfg_path, settings)
                    print(f"✅ Saved working SMTP mode to {cfg_path}")
                return 0
        print("\n❌ No Brevo mode accepted a diagnostic email.")
        return 1

    if args.send:
        ok, info = send_test(settings, args.to, args.subject)
        print(f"\n{'✅' if ok else '❌'} SMTP send result: {info}")
        if ok:
            print(f"SMTP accepted the test email. If it does not arrive, check Spam and Brevo Transactional logs; this is usually sender/domain/credits/reputation, not {_server_display_name(settings)} code.")
            return 0
        if info == "invalid_from_localhost":
            print(f"Fix: set smtp_from to a real sender verified in Brevo, for example: {_server_display_name(settings)} <you@yourdomain.com>")
        elif info == "not_configured":
            print("Fix: set smtp_enabled=true, smtp_host, smtp_username, and SMTP_PASSWORD/HUI_SMTP_PASSWORD.")
        return 1

    print("\nNo test email sent. Add --send --to your@email.com to send one diagnostic message.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
