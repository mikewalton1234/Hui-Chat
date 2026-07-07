# Beta 433 — Frontend live data bootstrap fix

This patch hardens the initial chat UI data path for rooms, friends, groups, and invites.

## Problem

After setup/database changes, the page could load but appear like the JavaScript was broken: rooms, friends, and other live panels stayed empty. The fragile path was that the UI relied heavily on one early Socket.IO bootstrap pass. If the socket ack was dropped, rate-limited, recovering auth, or pointed at an existing database whose official rooms had not been re-synced, the UI could remain blank until manual refresh/reconnect.

## Fix

- `get_rooms` now returns an acknowledgement payload as well as emitting `room_list`.
- Room loading now has an HTTP fallback through `/api/rooms`.
- Friend loading now has an HTTP fallback through `/api/friends`.
- Friend list normalization accepts both string rows and object rows.
- Connect bootstrap now runs a delayed rescue pass for rooms, friends, groups, invites, pending requests, and block state.
- Runtime DB init re-syncs official rooms from `chat_rooms.json` every startup after migrations. This is idempotent and prevents an existing-but-empty/partial database from making the room UI look broken.

## Intended result

A signed-in user should get rooms, friends, groups, invite state, and presence again after a hard refresh, server restart, setup run, or token refresh race.
