#!/usr/bin/env python3
"""S20 release packaging, checksums, upgrade/rollback, and handoff doctor."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_contains(failures: list[str], label: str, text: str, tokens: list[str]) -> None:
    for token in tokens:
        if token not in text:
            failures.append(f"{label} missing token: {token}")


def _assert_not_contains(failures: list[str], label: str, text: str, tokens: list[str]) -> None:
    for token in tokens:
        if token in text:
            failures.append(f"{label} contains stale/unsafe token: {token}")


def _is_safe_member(name: str, root_prefix: str) -> bool:
    if name.startswith("/") or "\\" in name:
        return False
    pure = PurePosixPath(name)
    if ".." in pure.parts or pure.is_absolute():
        return False
    return name.startswith(root_prefix)


def main() -> int:
    failures: list[str] = []
    version = _read("VERSION.txt").strip()
    if not version.startswith("0.11.0-beta."):
        failures.append(f"VERSION.txt has unexpected beta version format: {version!r}")

    beta_suffix = version.rsplit('.', 1)[-1]
    version_synced_files = ["README.md", "docs/UPGRADE_ROLLBACK.md"]
    frontend_checklist = f"Hui-Chat_Front-End_UI_Audit_Checklist_beta{beta_suffix}.md"
    if (ROOT / frontend_checklist).exists():
        version_synced_files.append(frontend_checklist)

    for rel in version_synced_files:
        path = ROOT / rel
        if not path.exists():
            failures.append(f"missing release version file: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        _assert_contains(failures, rel, text, [version])
        _assert_not_contains(failures, rel, text, ["Current version: **0.11.0-beta.351**", "Version: **0.11.0-beta.351**"])

    server_checklists = sorted(ROOT.glob("Hui-Chat_Server-Side_Audit_Checklist_beta*.md"))
    if not server_checklists:
        failures.append("missing server-side audit checklist archive")
    else:
        latest_server_checklist = server_checklists[-1]
        server_text = latest_server_checklist.read_text(encoding="utf-8")
        _assert_contains(failures, latest_server_checklist.name, server_text, ["S20", "server-side audit"])

    required_files = [
        "scripts/build_release_package.py",
        "tools/release_packaging_deep_doctor.py",
        "docs/RELEASE_HANDOFF.md",
        "docs/RELEASE_PACKAGE.md",
        "S20_PATCH_NOTES.md",
        "S20_DEEP_RECHECK_PATCH_NOTES.md",
    ]
    for rel in required_files:
        if not (ROOT / rel).exists():
            failures.append(f"missing required S20 file: {rel}")

    if (ROOT / "docs/RELEASE_HANDOFF.md").exists():
        handoff = _read("docs/RELEASE_HANDOFF.md")
        _assert_contains(failures, "docs/RELEASE_HANDOFF.md", handoff, [
            "Release gate",
            "sha256sum -c",
            "python tools/release_packaging_doctor.py",
            "python tools/release_packaging_deep_doctor.py",
            "python tools/config_doctor.py --config server_config.json",
            "python main.py --preflight",
            "Rollback point",
            "Do not paste secrets",
            "Symlink safety",
        ])

    if (ROOT / "docs/RELEASE_PACKAGE.md").exists():
        package_doc = _read("docs/RELEASE_PACKAGE.md")
        _assert_contains(failures, "docs/RELEASE_PACKAGE.md", package_doc, [
            "scripts/build_release_package.py",
            "release_manifest.json",
            "server_config.json",
            "private_uploads/",
            ".env.example",
            "Symlinks are excluded",
            "absolute source path",
            "deterministic",
        ])

    if (ROOT / "docs/UPGRADE_ROLLBACK.md").exists():
        upgrade = _read("docs/UPGRADE_ROLLBACK.md")
        _assert_contains(failures, "docs/UPGRADE_ROLLBACK.md", upgrade, [
            "python tools/release_packaging_doctor.py",
            "python tools/release_packaging_deep_doctor.py",
            "sha256sum -c",
            "Rollback limits",
            "Release artifact verification",
        ])

    builder = _read("scripts/build_release_package.py") if (ROOT / "scripts/build_release_package.py").exists() else ""
    _assert_contains(failures, "scripts/build_release_package.py", builder, [
        "EXCLUDED_DIR_NAMES",
        "server_config.json",
        "private_uploads",
        "release_manifest.json",
        "sha256_file",
        "zipfile.ZipFile",
        "path.is_symlink()",
        "source_root_name",
        "DETERMINISTIC_ZIP_DATE",
        "_safe_archive_name",
    ])
    _assert_not_contains(failures, "scripts/build_release_package.py", builder, [
        "shell=True",
        "subprocess.",
        '"source_root": str(ROOT)',
    ])

    # Build a temporary package and inspect it. This proves the packaging tool is
    # safe enough to run from a checkout without accidentally nesting artifacts.
    try:
        with tempfile.TemporaryDirectory(prefix="hui-s20-release-") as tmp:
            out = Path(tmp) / "dist"
            cmd = [sys.executable, str(ROOT / "scripts" / "build_release_package.py"), "--output-dir", str(out), "--label", "doctor", "--json"]
            proc = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if proc.returncode != 0:
                failures.append(f"build_release_package.py failed: {proc.stderr.strip() or proc.stdout.strip()}")
            else:
                manifest = json.loads(proc.stdout)
                zip_path = out / manifest["package_name"]
                sha_path = out / f"{manifest['package_name']}.sha256"
                manifest_path = out / f"{manifest['package_root']}.release_manifest.json"
                for artifact in (zip_path, sha_path, manifest_path):
                    if not artifact.exists():
                        failures.append(f"missing generated artifact: {artifact.name}")
                if zip_path.exists() and manifest.get("package_sha256") != _sha256(zip_path):
                    failures.append("generated manifest package_sha256 does not match zip")
                if "source_root" in manifest:
                    failures.append("generated manifest includes absolute source_root")
                policy = manifest.get("safety_policy") or {}
                for flag in ("symlinks_excluded", "outside_root_files_excluded", "absolute_source_path_omitted", "single_package_root_required"):
                    if policy.get(flag) is not True:
                        failures.append(f"generated manifest safety_policy missing/false: {flag}")
                if sha_path.exists() and zip_path.exists():
                    expected_line = f"{_sha256(zip_path)}  {zip_path.name}"
                    if sha_path.read_text(encoding="utf-8").strip() != expected_line:
                        failures.append("generated .sha256 file content is not sha256sum-compatible")
                if zip_path.exists():
                    with zipfile.ZipFile(zip_path) as zf:
                        infos = zf.infolist()
                        names = [info.filename for info in infos]
                    root_prefix = manifest["package_root"] + "/"
                    for name in names:
                        if not _is_safe_member(name, root_prefix):
                            failures.append(f"release zip contains unsafe member path: {name}")
                    required_inside = [
                        root_prefix + "VERSION.txt",
                        root_prefix + "README.md",
                        root_prefix + "docs/RELEASE_HANDOFF.md",
                        root_prefix + "docs/UPGRADE_ROLLBACK.md",
                        root_prefix + "scripts/build_release_package.py",
                    ]
                    for name in required_inside:
                        if name not in names:
                            failures.append(f"release zip missing expected file: {name}")
                    unsafe_exact_suffixes = (
                        "/server_config.json",
                        "/settings.json",
                        "/secrets.json",
                        "/.env",
                    )
                    unsafe_parts = (
                        "/.git/",
                        "/.venv/",
                        "/venv/",
                        "/__pycache__/",
                        "/logs/",
                        "/uploads/",
                        "/private_uploads/",
                        "/instance/",
                    )
                    for info in infos:
                        name = info.filename
                        if name.endswith((".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".pem", ".key", ".sha256", ".zip")):
                            failures.append(f"release zip includes unsafe artifact: {name}")
                        if any(name.endswith(suffix) for suffix in unsafe_exact_suffixes):
                            failures.append(f"release zip includes local secret/config file: {name}")
                        if any(part in name for part in unsafe_parts):
                            failures.append(f"release zip includes runtime/private directory: {name}")
                        if info.date_time != (2024, 1, 1, 0, 0, 0):
                            failures.append(f"release zip member has non-deterministic timestamp: {name}")
    except Exception as exc:
        failures.append(f"release package smoke raised exception: {type(exc).__name__}: {exc}")

    if failures:
        print("❌ Release packaging doctor failed")
        for failure in failures:
            print(f"   - {failure}")
        return 1

    print("✅ Release packaging doctor passed")
    print("   checks: version sync, release docs, package builder, checksum generation, manifest, secret/runtime exclusions, deterministic zip metadata, upgrade/rollback handoff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
