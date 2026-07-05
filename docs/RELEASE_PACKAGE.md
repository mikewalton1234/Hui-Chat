# Echo-Chat Release Package Guide

Version: **0.11.0-beta.386**

This document explains how to build a clean release zip for Echo-Chat without accidentally including local secrets, runtime uploads, logs, local databases, symlink targets, or older release archives.

## Build the release package

From the project root:

```bash
python scripts/build_release_package.py --output-dir dist --label torrent-card-render-fix-ui-audit-start
```

The command writes three artifacts into `dist/`:

```text
Echo-Chat-v<version>-<label>.zip
Echo-Chat-v<version>-<label>.zip.sha256
Echo-Chat-v<version>-<label>.release_manifest.json
```

The `.sha256` file is compatible with:

```bash
cd dist
sha256sum -c Echo-Chat-v<version>-<label>.zip.sha256
```

The generated `release_manifest.json` records the package name, package SHA256, file count, build time, safety exclusions, and post-extract verification commands. It intentionally uses `source_root_name` instead of an absolute source path in `source_root` so local build paths are not leaked into the manifest.

## Files intentionally excluded

The release builder excludes local and runtime-only files by default, including:

```text
server_config.json
settings.json
.env
.env.local
secrets.json
*.key
*.pem
logs/
uploads/
private_uploads/
instance/
*.sqlite
*.sqlite3
*.db
*.zip
*.sha256
```

Safe templates remain included, such as:

```text
server_config.example.json
settings.example.json
.env.example
```

## Deep packaging safety rules

The builder also enforces these release-safety rules:

- Symlinks are excluded, including file symlinks that point outside the project.
- Any path that resolves outside the source root is excluded.
- Every zip member must live under one package root folder.
- Archive paths with absolute prefixes, backslashes, or `..` traversal are rejected.
- Zip timestamps are deterministic.
- Zip file permissions are normalized so random local execute bits on media files do not leak into the release.
- The manifest does not include the absolute local source path.

## Release verification

Before handing a zip to someone else or deploying it yourself, run:

```bash
python tools/release_packaging_doctor.py
python tools/release_packaging_deep_doctor.py
python tools/ui12_final_frontend_release_doctor.py
python tools/config_doctor.py --config server_config.json
python main.py --preflight
```

For a production host, also run the deployment/topology checks used by your install method:

```bash
python main.py --redis-socketio-check
python tools/deployment_ops_doctor.py
python tools/deployment_ops_deep_doctor.py
```

## Why the package builder matters

Do not manually zip your working directory if you have ever started the server from it. A live working directory may contain private uploads, logs, local SQLite files, generated release zips, service env files, symlinks to files outside the project, or secret-bearing configuration. Use `scripts/build_release_package.py` so the release package is repeatable, reviewable, and safe to hand off.
