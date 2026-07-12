#!/usr/bin/env python3
"""Hui Chat config doctor.

Runs the same preflight checks as startup, but defaults to a no-database mode so
an operator can validate JSON, secrets/cookies, upload paths, Socket.IO settings,
media mode, and health endpoint choices before PostgreSQL is reachable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask

from constants import server_display_name
from main import apply_env_overrides, load_settings
from preflight import format_preflight_report, run_preflight


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hui Chat configuration doctor")
    parser.add_argument("--config", default="server_config.json", help="path to server config JSON")
    parser.add_argument("--include-db", action="store_true", help="also run the live PostgreSQL/database check")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg_path = Path(args.config)
    settings = load_settings(cfg_path)
    apply_env_overrides(settings)
    app = Flask(__name__)
    with app.app_context():
        result = run_preflight(
            settings,
            settings_file=cfg_path,
            init_db_pool_if_needed=bool(args.include_db),
            include_database=bool(args.include_db),
        )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Config doctor report for {server_display_name(settings)}")
        print(format_preflight_report(result))
        if not args.include_db:
            print("\nDatabase check skipped. Re-run with --include-db after PostgreSQL/env secrets are ready.")
    return 2 if result.get("overall") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
