"""Small .env loader for Echo-Chat entrypoints.

Echo-Chat is normally launched with `python main.py`, `gunicorn wsgi:app`, or a
standalone janitor process. Those paths do not go through Flask's CLI dotenv
loader, so this helper loads a project `.env` file early without making
python-dotenv a hard runtime dependency.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Iterable


def _candidate_env_files() -> Iterable[Path]:
    explicit = os.environ.get("ECHOCHAT_ENV_FILE") or os.environ.get("ENV_FILE")
    if explicit:
        yield Path(explicit).expanduser()
    yield Path(__file__).resolve().parent / ".env"


def _strip_optional_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _load_simple_dotenv(path: Path, *, override: bool = False) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or any(ch.isspace() for ch in key):
            continue
        value = value.strip()
        # shlex handles simple inline comments without destroying quoted # chars.
        try:
            parts = shlex.split(value, comments=True, posix=True)
            value = " ".join(parts) if parts else ""
        except Exception:
            value = _strip_optional_quotes(value.split(" #", 1)[0])
        if override or key not in os.environ:
            os.environ[key] = value


def load_project_dotenv(*, override: bool = False) -> Path | None:
    """Load the first configured/project .env file if it exists.

    Existing environment variables win by default so systemd, shell exports, and
    container secrets remain authoritative.  We also record which variables came
    from the project .env file so interactive terminal-only controls, such as
    setup UI mode, cannot get stuck forever because of an old persisted .env key.
    """
    before_keys = set(os.environ.keys())
    for path in _candidate_env_files():
        try:
            if not path.exists() or not path.is_file():
                continue
            try:
                from dotenv import load_dotenv  # type: ignore
            except Exception:
                _load_simple_dotenv(path, override=override)
            else:
                load_dotenv(dotenv_path=path, override=override)
            loaded_keys = sorted(set(os.environ.keys()) - before_keys)
            if loaded_keys:
                os.environ.setdefault("ECHOCHAT_DOTENV_KEYS", ",".join(loaded_keys))
                os.environ.setdefault("ECHOCHAT_DOTENV_FILE", str(path))
            return path
        except Exception:
            # Startup must never fail only because an optional .env file was
            # malformed. The downstream config/readiness checks will still catch
            # missing required values.
            return None
    return None
