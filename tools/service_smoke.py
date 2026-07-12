#!/usr/bin/env python3
"""Run a small live smoke test against a running Hui Chat service."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVICE_SMOKE_ROUTES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("/login", (200,)),
    ("/register", (200,)),
    ("/forgot_password", (200,)),
    ("/chat", (200, 302, 401)),
    ("/api/room_catalog", (200, 302, 401)),
)


@dataclass(frozen=True)
class SmokeResult:
    name: str
    ok: bool
    detail: str = ""
    status_code: int | None = None


def _fetch_status(url: str, timeout: float = 5.0) -> tuple[int | None, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "HuiChatServiceSmoke/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - operator supplied local/service URL smoke test
            return int(response.status), "ok"
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc)
    except Exception as exc:  # pragma: no cover - host/network specific
        return None, str(exc)


def run_live_smoke(base_url: str) -> list[SmokeResult]:
    base = base_url.rstrip("/") + "/"
    results: list[SmokeResult] = []
    for route, allowed in SERVICE_SMOKE_ROUTES:
        url = urljoin(base, route.lstrip("/"))
        status, detail = _fetch_status(url)
        ok = status in allowed
        results.append(SmokeResult(
            name=f"GET {route}",
            ok=ok,
            status_code=status,
            detail=f"status={status}; allowed={allowed}; {detail}",
        ))
    return results


def summarize(results: list[SmokeResult]) -> dict:
    failed = [result for result in results if not result.ok]
    return {"ok": not failed, "passed": len(results) - len(failed), "failed": len(failed), "total": len(results)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hui Chat live service launch smoke test")
    parser.add_argument("--url", default="http://127.0.0.1:5000", help="running service base URL")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = run_live_smoke(args.url)
    payload = {"url": args.url, "summary": summarize(results), "results": [asdict(r) for r in results]}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        summary = payload["summary"]
        print(f"Hui Chat service smoke: {'PASS' if summary['ok'] else 'FAIL'}")
        print(f"Passed {summary['passed']} / {summary['total']} checks")
        for result in results:
            status = "PASS" if result.ok else "FAIL"
            print(f"[{status}] {result.name}: {result.detail[:300]}")
    return 0 if payload["summary"]["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
