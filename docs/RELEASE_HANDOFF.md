# Echo-Chat Release Handoff

Version: **0.11.0-beta.386**

Use this handoff when you move the package to a server, share it for testing, or archive it as a rollback point.

## Release gate

Run these from the extracted project root before declaring the package ready:

```bash
python tools/release_packaging_doctor.py
python tools/release_packaging_deep_doctor.py
python tools/post_ui12_room_unread_visibility_doctor.py
python tools/post_ui12_deep_recheck_doctor.py
python tools/post_ui12_bug_hunt_doctor.py
python tools/ui12_final_frontend_release_doctor.py
python tools/config_doctor.py --config server_config.json
python main.py --preflight
python main.py --schema-version
python tools/log_sanity.py
```

If you use Redis, multi-instance Socket.IO, or systemd deployment helpers, also run:

```bash
python main.py --redis-socketio-check
python tools/deployment_ops_doctor.py
python tools/deployment_ops_deep_doctor.py
```

If you build a fresh release artifact locally, verify its checksum before moving it:

```bash
cd dist
sha256sum -c Echo-Chat-v0.11.0-beta.386-post-ui12-room-unread-visibility.zip.sha256
```

## Symlink safety

The release builder intentionally excludes symlinks. This prevents a symlink inside the project folder from silently packaging a file outside the source tree, such as a local secret, private upload, backup, or service env file. Do not replace this with a manual zip command unless you have manually inspected symlinks and runtime folders first.

## Rollback point

Before starting the new build on a live server:

1. Save the previous release zip or previous project folder.
2. Back up PostgreSQL with `pg_dump` or a provider snapshot.
3. Back up `/etc/echochat/echochat.env` or whichever protected env file your service uses.
4. Back up the current `server_config.json` if you keep it in the project folder.
5. Record the old `VERSION.txt` value and current service names.

Rollback is safest before users create new content on the upgraded build. If users already sent messages, uploaded files, or changed settings, restoring the old DB backup will remove that newer data.

## Minimum live smoke after start

1. Open `/login` and confirm the visible version is `0.11.0-beta.386`.
2. Log in as the owner/admin.
3. Join a public room, send a disposable message, and confirm it renders.
4. Send one GIF from a fresh GIPHY search, not browser Recents.
5. Open a PM and group chat and verify basic message rendering.
6. Confirm the room composer toolbar order: font, size, bold, italic, underline, color, emoticons, torrent, GIF, voice, webcam, message, Send.
7. Open Admin Panel and verify diagnostics require the right admin permission and recent re-auth.
8. Run Admin Test Lab readiness, full suite, live user flow, browser P2P diagnostics, and complete the Browser release gate for the browsers you support.
9. Check the janitor status snapshot and logs for cleanup failures.

## Do not paste secrets

Do not paste production passwords, SMTP tokens, TURN credentials, JWT secrets, Redis passwords, database DSNs, env files, `.key` files, private upload file names, or local absolute paths from manifests/logs into support chats, bug reports, screenshots, or release notes. Redact them first.

## Package contents to keep with the release

Archive these together:

```text
Echo-Chat-v0.11.0-beta.386-post-ui12-room-unread-visibility.zip
Echo-Chat-v0.11.0-beta.386-post-ui12-room-unread-visibility.zip.sha256
Echo-Chat-v0.11.0-beta.386-post-ui12-room-unread-visibility.release_manifest.json
Echo-Chat_Server-Side_Audit_Checklist_beta352.md
```


## beta.380 optimistic composer send deep recheck

Verify room, PM, and group composers clear immediately after Enter/Send on a slow server. If the server rejects the message, the draft should restore when the field is still empty, or be saved as the last failed draft if the user has already typed a new message.


## beta.382 UI12 final front-end release

Admin Test Lab Browser release gate rows now recompute the visible pass/fail summary from current results. This prevents stale manual-gate failures from keeping the release summary failed after all browser checks are later completed.

## beta.383 post-UI12 bug hunt

When validating missed-message behavior, test this focus-return path: receive a PM or group message while Echo-Chat is unfocused, return to the app, and confirm the top visible PM/group conversation clears its live unread badge without needing a second click on the exact window body. Also verify touch/pointer focus on mobile PM/group sheets clears the active conversation only.

## beta.385 post-UI12 room unread visibility pass

When validating current-room unread behavior, join a room, switch to mobile Rooms/Hub or open the room-browser overlay, then receive a message in the current room from another user. The current room should keep unread attention and notify while the transcript is covered or hidden. It should only suppress duplicate alerts when the Chat panel/current room transcript is actually readable.


## beta.386 private missed messages icon hotfix

When validating private missed-message behavior, receive a PM while the recipient is logged in but not actively reading that PM window. The Missed Messages icon should increment and stay visible until the user opens/loads the missed PM. Also test a fully offline recipient; the missed PM should appear after login. Run `python tools/private_missed_messages_icon_hotfix_doctor.py`.
