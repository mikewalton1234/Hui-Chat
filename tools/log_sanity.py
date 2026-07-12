#!/usr/bin/env python3
"""Scan recent Hui Chat logs for startup errors and accidental secret leakage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOG_FATAL_PATTERNS: tuple[str, ...] = (
    "Traceback (most recent call last)",
    "ModuleNotFoundError",
    "ImportError",
    "FATAL",
    "CRITICAL",
    "OperationalError",
    "ProgrammingError",
    "psycopg2.errors",
    "CSRFError",
    "JWTDecodeError",
)

LOG_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"postgres(?:ql)?://[^\s:@]+:[^\s@]+@", re.I),
    re.compile(r"\b(?:SECRET_KEY|HUI_SECRET_KEY|JWT_SECRET|JWT_SECRET_KEY|HUI_JWT_SECRET|HUI_PROFILE_FIELD_KEY|HUI_EMAIL_FIELD_KEY|HUI_EMAIL_HASH_KEY|HUI_SECURITY_BACKUP_KEY|HUI_PRIVACY_HASH_KEY|DATABASE_URL|DB_CONNECTION_STRING)\s*=\s*[^\s]+", re.I),
    re.compile(r"\b(?:smtp_password|twilio_auth_token|turn_credential|giphy_api_key)\b\s*[:=]\s*[^\s,}]+", re.I),
)


@dataclass(frozen=True)
class LogCheck:
    name: str
    ok: bool
    detail: str = ""
    category: str = "log-sanity"


def redact_log_snippet(line: str) -> str:
    redacted = str(line or "")
    redacted = re.sub(r"(postgres(?:ql)?://[^\s:@]+:)[^\s@]+(@)", r"\1***\2", redacted, flags=re.I)
    redacted = re.sub(r"((?:SECRET_KEY|HUI_SECRET_KEY|JWT_SECRET|JWT_SECRET_KEY|HUI_JWT_SECRET|HUI_PROFILE_FIELD_KEY|HUI_EMAIL_FIELD_KEY|HUI_EMAIL_HASH_KEY|HUI_SECURITY_BACKUP_KEY|HUI_PRIVACY_HASH_KEY|DATABASE_URL|DB_CONNECTION_STRING)\s*=\s*)[^\s]+", r"\1***", redacted, flags=re.I)
    redacted = re.sub(r"((?:smtp_password|twilio_auth_token|turn_credential|giphy_api_key)\b\s*[:=]\s*)[^\s,}]+", r"\1***", redacted, flags=re.I)
    return redacted[:500]


def scan_log_sanity(paths: Iterable[Path | str]) -> list[LogCheck]:
    checks: list[LogCheck] = []
    any_existing = False
    fatal_hits: list[str] = []
    secret_hits: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists() or not path.is_file():
            continue
        any_existing = True
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
        except Exception as exc:
            checks.append(LogCheck(f"log readable: {path}", False, str(exc)))
            continue
        start = max(1, len(lines) - 1999)
        for idx, line in enumerate(lines, start=start):
            if any(token in line for token in LOG_FATAL_PATTERNS):
                fatal_hits.append(f"{path}:{idx}: {redact_log_snippet(line)}")
            if any(pattern.search(line) for pattern in LOG_SECRET_PATTERNS):
                secret_hits.append(f"{path}:{idx}: {redact_log_snippet(line)}")
    if not any_existing:
        checks.append(LogCheck("log files optional before first run", True, "no logs found yet"))
    else:
        checks.append(LogCheck("no obvious fatal startup errors in recent logs", not fatal_hits, "; ".join(fatal_hits[:5]) if fatal_hits else "clean"))
        checks.append(LogCheck("no obvious secrets in recent logs", not secret_hits, "; ".join(secret_hits[:5]) if secret_hits else "clean"))
    return checks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hui Chat log sanity scanner")
    parser.add_argument("--log", action="append", default=[], help="log file to scan; may be provided more than once")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = [Path(p) for p in args.log] if args.log else [ROOT / "logs" / "server.log"]
    checks = scan_log_sanity(paths)
    failed = [check for check in checks if not check.ok]
    payload = {"ok": not failed, "passed": len(checks) - len(failed), "failed": len(failed), "checks": [asdict(check) for check in checks]}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Hui Chat log sanity: {'PASS' if payload['ok'] else 'FAIL'}")
        for check in checks:
            status = "PASS" if check.ok else "FAIL"
            print(f"[{status}] {check.name}: {check.detail}")
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
