from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from database import cleanup_expired_custom_rooms, cleanup_expired_room_messages, cleanup_expired_autoscaled_rooms
from runtime_timing import timing_int
from maintenance_cleanup import (
    cleanup_expired_auth_artifacts,
    cleanup_revoked_private_files,
    cleanup_orphan_private_file_blobs,
)

try:
    from privacy_retention import apply_privacy_retention
except Exception:  # pragma: no cover - optional during partial installs
    apply_privacy_retention = None

try:  # optional: lets janitor use the same live room truth as the room browser
    from realtime.state import configure_shared_state, live_room_counts, shared_state_enabled
except Exception:  # pragma: no cover - keep janitor import-safe during partial installs
    configure_shared_state = None
    live_room_counts = None
    shared_state_enabled = None


_JANITOR_LOCK = threading.Lock()
_JANITOR_THREAD: threading.Thread | None = None
_JANITOR_STATUS: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "last_cycle_started_at": None,
    "last_cycle_finished_at": None,
    "last_cycle_ok": None,
    "last_error": None,
    "consecutive_failures": 0,
    "last_cycle": {},
}
_STATUS_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_update(**patch: Any) -> None:
    with _STATUS_LOCK:
        _JANITOR_STATUS.update(patch)


def janitor_status_snapshot() -> dict[str, Any]:
    with _STATUS_LOCK:
        out = dict(_JANITOR_STATUS)
        last_cycle = out.get("last_cycle")
        if isinstance(last_cycle, dict):
            out["last_cycle"] = dict(last_cycle)
        return out


def _coerce_idle_minutes(settings: dict, minute_key: str, hour_key: str, default_hours: int) -> int:
    """Resolve idle TTL in minutes, preferring minute-based settings and falling back to hours."""
    raw_minutes = settings.get(minute_key, None)
    if raw_minutes not in (None, ""):
        try:
            minutes = int(raw_minutes)
        except Exception:
            minutes = default_hours * 60
    else:
        try:
            hours = int(settings.get(hour_key, default_hours))
        except Exception:
            hours = default_hours
        minutes = hours * 60
    return max(1, min(int(minutes), 24 * 60 * 365))


def _coerce_interval_seconds(settings: dict) -> int:
    return timing_int(settings, "janitor_interval_seconds")


def _coerce_limit(settings: dict, key: str, default: int = 500) -> int:
    try:
        value = int(settings.get(key, default))
    except Exception:
        value = default
    return max(1, min(value, 10000))


def configure_janitor_runtime(settings: dict) -> None:
    """Configure optional cross-process runtime state for cleanup decisions."""
    try:
        if configure_shared_state is not None:
            configure_shared_state(settings)
    except Exception:
        logging.exception("[JANITOR] shared-state configuration failed")


def _run_task(cycle: dict[str, Any], key: str, label: str, func, *, log_count: bool = True) -> None:
    started = time.monotonic()
    try:
        result = func()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        result_ok = not (isinstance(result, dict) and result.get("ok") is False)
        if not result_ok:
            cycle["ok"] = False
        task_status = {"ok": result_ok, "result": result, "elapsed_ms": elapsed_ms}
        if isinstance(result, dict) and result.get("error"):
            task_status["error"] = str(result.get("error"))
        cycle["tasks"][key] = task_status
        if log_count:
            if isinstance(result, int) and result:
                logging.info("[JANITOR] %s: %s", label, result)
            elif isinstance(result, dict):
                deleted = result.get("deleted") if isinstance(result.get("deleted"), dict) else None
                updated = result.get("updated") if isinstance(result.get("updated"), dict) else None
                totals = deleted or updated
                if totals and any(int(v or 0) for v in totals.values() if isinstance(v, (int, str))):
                    logging.info("[JANITOR] %s: %s", label, totals)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        cycle["ok"] = False
        cycle["tasks"][key] = {"ok": False, "error": type(exc).__name__, "elapsed_ms": elapsed_ms}
        logging.exception("[JANITOR] %s error", label)


def run_janitor_cycle(settings: dict, *, use_live_counts: bool = True) -> dict[str, Any]:
    """Run one cleanup cycle and return a structured status dict.

    The cycle is deliberately fail-soft: one broken cleanup task is logged and
    recorded but does not prevent the remaining cleanup tasks from running.
    This is useful for a long-lived background thread and for `janitor_runner.py
    --once` smoke checks.
    """
    settings = settings or {}
    cycle: dict[str, Any] = {
        "ok": True,
        "started_at": _utc_now_iso(),
        "finished_at": None,
        "interval_seconds": _coerce_interval_seconds(settings),
        "tasks": {},
    }
    _status_update(last_cycle_started_at=cycle["started_at"])

    idle_minutes = _coerce_idle_minutes(settings, "custom_room_idle_minutes", "custom_room_idle_hours", 3)
    private_idle_minutes = _coerce_idle_minutes(settings, "custom_private_room_idle_minutes", "custom_private_room_idle_hours", max(1, idle_minutes // 60))
    debug_custom_rooms = bool(settings.get("janitor_debug_custom_rooms", False))

    try:
        autoscale_idle_min = int(settings.get("autoscale_room_idle_minutes", 30))
    except Exception:
        autoscale_idle_min = 30
    autoscale_idle_min = max(1, min(autoscale_idle_min, 24 * 60 * 7))

    live_counts_snapshot = None
    try:
        can_use_live_counts = bool(use_live_counts) or bool(shared_state_enabled and shared_state_enabled())
        if can_use_live_counts and live_room_counts is not None:
            live_counts_snapshot = live_room_counts()
    except Exception:
        live_counts_snapshot = None

    _run_task(
        cycle,
        "custom_rooms",
        "deleted idle custom rooms",
        lambda: cleanup_expired_custom_rooms(
            idle_minutes=idle_minutes,
            private_idle_minutes=private_idle_minutes,
            debug=debug_custom_rooms,
            live_counts=live_counts_snapshot,
        ),
    )

    if bool(settings.get("autoscale_rooms_enabled", True)):
        _run_task(
            cycle,
            "autoscaled_rooms",
            "deleted idle autoscaled rooms",
            lambda: cleanup_expired_autoscaled_rooms(idle_minutes=autoscale_idle_min),
        )
    else:
        cycle["tasks"]["autoscaled_rooms"] = {"ok": True, "skipped": "autoscale rooms disabled"}

    _run_task(cycle, "room_messages", "deleted expired room messages", cleanup_expired_room_messages)

    if apply_privacy_retention is not None:
        retention_limit = _coerce_limit(settings, "privacy_retention_batch_limit", 500)
        _run_task(
            cycle,
            "privacy_retention",
            "privacy-retained old IP/UA metadata",
            lambda: apply_privacy_retention(settings, limit=retention_limit),
        )
    else:
        cycle["tasks"]["privacy_retention"] = {"ok": True, "skipped": "privacy retention unavailable"}

    auth_limit = _coerce_limit(settings, "auth_cleanup_batch_limit", 500)
    _run_task(
        cycle,
        "auth_artifacts",
        "deleted stale auth/session/reset-token artifacts",
        lambda: cleanup_expired_auth_artifacts(settings, limit=auth_limit),
    )

    private_file_limit = _coerce_limit(settings, "private_file_cleanup_batch_limit", auth_limit)
    _run_task(
        cycle,
        "revoked_private_files",
        "deleted revoked private file rows/blobs",
        lambda: cleanup_revoked_private_files(settings, limit=private_file_limit),
    )
    _run_task(
        cycle,
        "orphan_private_file_blobs",
        "deleted orphan private file blobs",
        lambda: cleanup_orphan_private_file_blobs(settings, limit=private_file_limit),
    )

    cycle["finished_at"] = _utc_now_iso()
    failed = [k for k, v in cycle["tasks"].items() if isinstance(v, dict) and v.get("ok") is False]
    cycle["ok"] = not failed
    cycle["failed_tasks"] = failed
    with _STATUS_LOCK:
        previous_failures = int(_JANITOR_STATUS.get("consecutive_failures") or 0)
    _status_update(
        last_cycle_finished_at=cycle["finished_at"],
        last_cycle_ok=cycle["ok"],
        last_error=(None if cycle["ok"] else ",".join(failed)),
        consecutive_failures=(0 if cycle["ok"] else previous_failures + 1),
        last_cycle=cycle,
    )
    return cycle


def start_janitor(settings: dict, use_live_counts: bool = True):
    """Start a lightweight background cleanup loop.

    - Removes inactive/empty custom rooms
    - Purges room messages with configured expiry
    - Applies old IP/UA privacy retention
    - Deletes stale revoked/expired auth artifacts

    This keeps the custom-room experience ephemeral without requiring UI polling.
    """

    configure_janitor_runtime(settings)

    def _loop():
        _status_update(running=True, started_at=_utc_now_iso())
        while True:
            interval = _coerce_interval_seconds(settings)
            cycle = run_janitor_cycle(settings, use_live_counts=use_live_counts)
            failures = int(janitor_status_snapshot().get("consecutive_failures") or 0)
            if failures >= 3:
                backoff = max(interval, min(interval * failures, 3600))
                logging.warning("[JANITOR] %s consecutive failed cycles; backing off for %ss", failures, backoff)
                time.sleep(backoff)
            else:
                time.sleep(interval)

    global _JANITOR_THREAD
    with _JANITOR_LOCK:
        if _JANITOR_THREAD is not None and _JANITOR_THREAD.is_alive():
            logging.info("[JANITOR] background cleanup loop already running; reusing existing thread")
            return _JANITOR_THREAD

        t = threading.Thread(target=_loop, name="hui_janitor", daemon=True)
        t.start()
        _JANITOR_THREAD = t
        logging.info("[JANITOR] background cleanup loop started")
        return t
