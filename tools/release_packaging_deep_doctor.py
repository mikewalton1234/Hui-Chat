#!/usr/bin/env python3
"""Deep S20 release packaging safety doctor.

This check creates temporary probe files inside the working tree, builds a
release package, and verifies that the release builder excludes symlinks,
secret-like files, runtime folders, nested artifacts, and absolute source paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_zip_path(name: str, root_prefix: str) -> bool:
    if name.startswith("/") or "\\" in name:
        return False
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        return False
    return name.startswith(root_prefix)


def _member_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0o777


def main() -> int:
    failures: list[str] = []
    cleanup_paths: list[Path] = []
    symlink_created = False

    with tempfile.TemporaryDirectory(prefix="hui-s20-deep-") as tmp:
        tmp_path = Path(tmp)
        unique = uuid.uuid4().hex
        tokens = {
            "outside": f"RELEASE_DOCTOR_OUTSIDE_{unique}",
            "server_config": f"RELEASE_DOCTOR_SERVER_CONFIG_{unique}",
            "env_local": f"RELEASE_DOCTOR_ENV_LOCAL_{unique}",
            "key": f"RELEASE_DOCTOR_KEY_{unique}",
            "zip": f"RELEASE_DOCTOR_ZIP_{unique}",
            "log": f"RELEASE_DOCTOR_LOG_{unique}",
            "upload": f"RELEASE_DOCTOR_UPLOAD_{unique}",
            "private_upload": f"RELEASE_DOCTOR_PRIVATE_UPLOAD_{unique}",
            "sqlite": f"RELEASE_DOCTOR_SQLITE_{unique}",
        }
        outside_secret = tmp_path / "outside_secret.txt"
        outside_secret.write_text(tokens["outside"] + "\n", encoding="utf-8")

        probes = {
            ROOT / "server_config.json": f'{{"secret":"{tokens["server_config"]}"}}\n',
            ROOT / ".env.local": f'TOKEN={tokens["env_local"]}\n',
            ROOT / "release_doctor_probe.key": tokens["key"] + "\n",
            ROOT / "release_doctor_probe.zip": tokens["zip"] + "\n",
            ROOT / "logs" / "release_doctor_probe.log": tokens["log"] + "\n",
            ROOT / "uploads" / "release_doctor_probe.bin": tokens["upload"] + "\n",
            ROOT / "private_uploads" / "release_doctor_probe.bin": tokens["private_upload"] + "\n",
            ROOT / "instance" / "release_doctor_probe.sqlite": tokens["sqlite"] + "\n",
        }

        try:
            for path, content in probes.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                cleanup_paths.append(path)

            symlink_path = ROOT / "release_doctor_probe_symlink.txt"
            try:
                if symlink_path.exists() or symlink_path.is_symlink():
                    symlink_path.unlink()
                os.symlink(outside_secret, symlink_path)
                symlink_created = True
                cleanup_paths.append(symlink_path)
            except (OSError, NotImplementedError):
                symlink_created = False

            out = tmp_path / "dist"
            cmd = [sys.executable, str(ROOT / "scripts" / "build_release_package.py"), "--output-dir", str(out), "--label", "deep-doctor", "--json"]
            first = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            second = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if first.returncode != 0:
                failures.append(f"first release build failed: {first.stderr.strip() or first.stdout.strip()}")
            if second.returncode != 0:
                failures.append(f"second release build failed: {second.stderr.strip() or second.stdout.strip()}")
            if first.returncode == 0 and second.returncode == 0:
                manifest = json.loads(second.stdout)
                zip_path = out / manifest["package_name"]
                sha_path = out / f"{manifest['package_name']}.sha256"
                manifest_path = out / f"{manifest['package_root']}.release_manifest.json"

                if not zip_path.exists():
                    failures.append("release zip was not created")
                if not sha_path.exists():
                    failures.append("release zip checksum file was not created")
                if not manifest_path.exists():
                    failures.append("release manifest file was not created")

                if zip_path.exists() and sha_path.exists():
                    expected_sha_line = f"{_sha256(zip_path)}  {zip_path.name}"
                    if sha_path.read_text(encoding="utf-8").strip() != expected_sha_line:
                        failures.append("zip sha256 file does not match built zip")

                if "source_root" in manifest:
                    failures.append("manifest leaks absolute source_root")
                policy = manifest.get("safety_policy") or {}
                for flag in (
                    "symlinks_excluded",
                    "outside_root_files_excluded",
                    "absolute_source_path_omitted",
                    "single_package_root_required",
                ):
                    if policy.get(flag) is not True:
                        failures.append(f"manifest safety flag missing/false: {flag}")

                if zip_path.exists():
                    with zipfile.ZipFile(zip_path) as zf:
                        infos = zf.infolist()
                        names = [info.filename for info in infos]
                        combined_payload = b"".join(zf.read(name) for name in names if not name.endswith("/"))

                    root_prefix = manifest["package_root"] + "/"
                    if not all(_safe_zip_path(name, root_prefix) for name in names):
                        failures.append("release zip contains a path outside the single package root")
                    bad_name_tokens = [
                        "server_config.json",
                        ".env.local",
                        "release_doctor_probe.key",
                        "release_doctor_probe.zip",
                        "release_doctor_probe.log",
                        "release_doctor_probe.sqlite",
                        "release_doctor_probe.bin",
                        "release_doctor_probe_symlink.txt",
                    ]
                    for token in bad_name_tokens:
                        if any(name.endswith("/" + token) or token in name for name in names):
                            failures.append(f"release zip included probe artifact by name: {token}")
                    bad_payload_tokens = [value.encode("ascii") for value in tokens.values()]
                    for token in bad_payload_tokens:
                        if token in combined_payload:
                            failures.append(f"release zip included forbidden probe payload: {token.decode('ascii')}")
                    for info in infos:
                        if info.date_time != (2024, 1, 1, 0, 0, 0):
                            failures.append(f"non-deterministic timestamp in release zip: {info.filename}")
                        mode = _member_mode(info)
                        if info.filename.endswith("scripts/run_production.sh"):
                            if mode != 0o755:
                                failures.append("scripts/run_production.sh should have normalized 0755 mode")
                        elif not info.filename.endswith("/") and mode not in {0o644, 0o755}:
                            failures.append(f"unexpected normalized mode {mode:o} for {info.filename}")
                    if symlink_created and manifest.get("excluded_counts", {}).get("symlink", 0) < 1:
                        failures.append("symlink probe was not reported as excluded")

                # The manifest is outside the zip; make sure it does not expose local absolute paths.
                if manifest_path.exists():
                    manifest_text = manifest_path.read_text(encoding="utf-8")
                    if str(ROOT) in manifest_text:
                        failures.append("manifest file contains absolute local source root path")
                    if "source_root_name" not in manifest_text:
                        failures.append("manifest file missing source_root_name")
        finally:
            # Remove files first, then prune empty probe directories if they did not exist before.
            for path in reversed(cleanup_paths):
                try:
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                except Exception:
                    pass
            for dirname in (ROOT / "logs", ROOT / "uploads", ROOT / "private_uploads", ROOT / "instance"):
                try:
                    if dirname.exists() and not any(dirname.iterdir()):
                        dirname.rmdir()
                except Exception:
                    pass
            shutil.rmtree(tmp_path / "dist", ignore_errors=True)

    if failures:
        print("❌ Release packaging deep doctor failed")
        for failure in failures:
            print(f"   - {failure}")
        return 1

    print("✅ Release packaging deep doctor passed")
    print("   checks: symlink exclusion, runtime/secret probe exclusion, single-root archive paths, checksum integrity, manifest path privacy, normalized zip timestamps/modes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
