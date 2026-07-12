#!/usr/bin/env python3
"""Fail when a retired full product-name variant reappears in a Hui release.

The retired words are assembled in pieces so this validator does not contain the
forbidden full name itself. The scan covers paths, ordinary text, common encoded
forms, UTF-16 text, binary metadata visible through Latin-1, and nested ZIP
members.
"""

from __future__ import annotations

import html
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
LEFT = "ec" + "ho"
RIGHT = "ch" + "at"
FORBIDDEN = re.compile(
    re.escape(LEFT) + r"(?:[\s._\-\u2010-\u2015/\\]*)" + re.escape(RIGHT),
    re.IGNORECASE,
)
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", "dist"}


def normalized_views(value: str) -> list[str]:
    """Return normalized and commonly decoded forms of a path or text value."""
    views: list[str] = []
    current = str(value or "")
    for _ in range(3):
        candidates = [
            current,
            unicodedata.normalize("NFKC", current),
            html.unescape(current),
            unquote(current),
        ]
        expanded: list[str] = []
        for candidate in candidates:
            candidate = re.sub(
                r"\\u([0-9a-fA-F]{4})",
                lambda m: chr(int(m.group(1), 16)),
                candidate,
            )
            candidate = re.sub(
                r"\\x([0-9a-fA-F]{2})",
                lambda m: chr(int(m.group(1), 16)),
                candidate,
            )
            expanded.append(unicodedata.normalize("NFKC", candidate))
        for candidate in expanded:
            if candidate not in views:
                views.append(candidate)
        next_value = html.unescape(unquote(expanded[-1]))
        if next_value == current:
            break
        current = next_value
    return views


def first_match(value: str) -> str | None:
    for view in normalized_views(value):
        match = FORBIDDEN.search(view)
        if match:
            return match.group(0)
    return None


def decoded_texts(data: bytes) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for encoding in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            value = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        out.append((encoding, value))
    return out


def scan_payload(label: str, data: bytes, failures: list[str]) -> None:
    for encoding, value in decoded_texts(data):
        match = first_match(value)
        if match:
            failures.append(f"{label}: forbidden content via {encoding}: {match!r}")
            return


def scan_zip(path: Path, failures: list[str]) -> None:
    try:
        with ZipFile(path) as archive:
            for info in archive.infolist():
                match = first_match(info.filename)
                if match:
                    failures.append(f"{path.relative_to(ROOT)}::{info.filename}: forbidden path: {match!r}")
                    continue
                if info.is_dir():
                    continue
                try:
                    data = archive.read(info)
                except Exception as exc:  # defensive validation output
                    failures.append(f"{path.relative_to(ROOT)}::{info.filename}: unreadable member: {exc}")
                    continue
                scan_payload(f"{path.relative_to(ROOT)}::{info.filename}", data, failures)
    except BadZipFile as exc:
        failures.append(f"{path.relative_to(ROOT)}: invalid ZIP: {exc}")


def main() -> int:
    failures: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        match = first_match(rel.as_posix())
        if match:
            failures.append(f"{rel}: forbidden path: {match!r}")
        if not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            scan_zip(path, failures)
            continue
        scan_payload(str(rel), path.read_bytes(), failures)

    if failures:
        print("Hui brand-purge doctor failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("Hui brand-purge doctor passed")
    print("   checks: paths, Unicode normalization, encoded text, UTF-16, binary metadata, nested ZIP members")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
