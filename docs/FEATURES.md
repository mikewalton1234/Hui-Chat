Version: **0.11.0-beta.317**

## F272-F276 mobile QA

Mobile room browsing, active-room chat controls, room-user drawer, Hub sections, and PM/group sheets are covered by beta.305 guards.

- Rich messaging helpers verified in beta.284: self-hosted emoji picker, server-proxied GIF search/trending, inline GIF rendering, text-animation controls, and classic sender-label display.

- Systemd env templates now include correct SMS 2FA timeout env naming plus Twilio/TURN production placeholders.
- Production/public mode now keeps DB/API/SMTP/Twilio/TURN secrets out of `server_config.json` by default unless `ECHOCHAT_PERSIST_SECRETS=1` is set.
## 0.11.0-beta.138 - randomized Admin Test Lab link

- Changed Admin Test Lab from a predictable page URL to a randomized, short-lived, admin-session-bound URL minted by the admin panel launcher.
- Predictable `/admin/test_lab` and `/admin/test-lab` page requests now return 404 instead of rendering the Test Lab.
- Predictable Test Lab action endpoints now return 404; the page uses token-scoped `/admin/test_lab/<token>/...` actions.
- Updated the Admin Panel **Open Test Lab** button to request `/admin/test_lab/link` and open the generated link in a new tab.
- Updated CLI/live feature runner and regression guards for the randomized Test Lab flow.
- No database migration required.

## 0.11.0-beta.137 - continue room radio playback after skip

- Preserved local room-radio playback intent when `/skip` or the skip button advances to the next configured station.
- Reloaded the embedded player with autoplay intent after a skip-driven station switch so listeners continue on the next station instead of being left on a stopped/stale player.
- Kept v136 hard-stop behavior intact: leaving, switching rooms, muting locally, or voice-ducking still stops/suspends playback instead of restarting it.
- Added regression guards for skip-continue playback state, bundled chat output, and server payload metadata.
- No database migration required.

## 0.11.0-beta.137 - stop room radio when leaving/switching rooms

- Added a hard local radio stop path that blanks and replaces the embedded station iframe so provider audio cannot keep playing after a user leaves a room.
- Stopped room radio immediately on Leave, forced removal, and room switches.
- Reused the same hard-stop path when a room does not support radio or the local radio policy suspends playback.
- Added regression guards for leave/switch/forced-leave radio teardown behavior.

## 0.11.0-beta.135 - remove end-user DM unlock controls

- Removed the end-user Settings > Privacy & unlock section.
- Removed the manual Unlock DMs / Lock DMs controls and the private-message unlock modal from the chat UI.
- Kept private messages automatic: the browser tab uses the login password captured during sign-in to make encrypted private messages ready.
- Added a settings-tab fallback so old saved preferences pointing at the removed privacy tab safely open Chat & GIFs instead of leaving Settings blank.
- Updated private-message error copy to tell users to sign out and sign back in if private messages are not ready.
- Added regression guards for the removed UI controls and bundled chat output.
- No database migration required.

## 0.11.0-beta.134 - admin room-radio station editor

- Added an admin-only room-radio station editor inside the Rooms admin panel.
- Admins can select a radio room, add stations, remove stations, reorder stations, edit labels/providers, and save HTTPS source/embed URLs without hand-editing chat_rooms.json.
- Added RBAC, CSRF/fresh-admin-auth, payload validation, duplicate URL protection, atomic catalog writes, and audit logging for radio station updates.
- Added regression guards for the new admin routes, UI controls, station validation, and full feature runner coverage.
- No database migration required.


## 0.11.0-beta.133 - radio station placement and compact player

- Expanded room radio with correctly placed news/talk, sports, comedy, gospel/Christian, oldies/classic hits, and Indiana local station presets.
- Room radio now defaults to a compact mini-player with a Full player toggle so chat output stays visible.
- Added regression coverage for station placement and compact player layout.

## 0.11.0-beta.132 - room radio station expansion

- Expanded the shared room-radio station catalog across Listening Rooms, Genre Listening, and Music File Sharing rooms.
- Raised room-catalog station exposure from 8 to 16 so the frontend can actually show the deeper station list.
- Added mobile horizontal scrolling for station buttons so many stations do not crowd the chat screen.
- Added regression guards for expanded station counts, complete HTTPS embed/page URLs, deduped station lists, frontend catalog limits, and mobile station-button scrolling.
- No database migration required.

## 0.11.0-beta.131 - production readiness and end-user mobile bug hunt

- Added PUBLIC_URL env aliases for documented deployment env files.
- Normalized health endpoint env values so `healthz` becomes `/healthz`.
- Added public-beta production startup blocking for failed deployment readiness checks.
- Hardened public beta readiness around Cookie SameSite, proxy hop count, PostgreSQL DSN scheme, shared-state Redis defaults, and health endpoint paths.
- Fixed mobile room Users drawer discoverability by adding a visible close button inside the users sheet.

## 0.11.0-beta.130 mobile release-candidate audit

- Audited the full phone path: Rooms, Chat, Hub, private messages, group chat, profile, login, register, and settings.
- Bottom Chat navigation is disabled until a room is open.
- Profile windows use only profile-specific mobile navigation, not the generic PM/group action strip.
- Profile Edit enables only for owner-editable profile surfaces.

## 0.11.0-beta.129 mobile profile/avatar/gallery polish

- Phone-style profile window navigation.
- Mobile edit shortcuts for avatar, banner, bio, intro, favorites, and privacy defaults.
- Single-column mobile profile cards, post composer, profile gallery, and DiceBear/avatar grids.
- Keyboard-aware profile editing layout.

## Mobile PM / group / auth / settings polish — v0.11.0-beta.128

- Phone PM and group chats use full-screen sheet behavior instead of tiny floating desktop windows.
- PM/group tools are hidden until the user taps **More** or **Tools**.
- Group users open as a mobile drawer.
- Register uses the shared auth theme with sectioned signup steps.
- Mobile Settings has horizontal section chips, a scrollable active panel, and sticky Save/Close actions.

## Mobile Hub navigation polish — v0.11.0-beta.127

- Phone users navigate the Hub through **Friends / Alerts / Groups / Me** sections.
- The desktop Hub menu bar and dock tabs are hidden on phones.
- Friends and Groups use larger list rows and search controls.
- Alerts render as full-width phone cards instead of side bubbles.
- Me centralizes profile, settings, add-friend, new-PM, blocked-users, and help shortcuts.

## Mobile room browser redesign — v0.11.0-beta.125

- Phone users browse rooms through **Browse / Official / Custom** steps.
- The Categories panel no longer stacks above all room panels on phones.
- Selecting a category/subcategory advances to Official Rooms automatically.
- Custom rooms are a dedicated mobile step.
- Mobile room rows and controls use larger tap targets for easier one-handed navigation.

## Browser release gate and Socket.IO admin cleanup — v0.11.0-beta.124

- Admin Test Lab includes a Browser release gate checklist for Chrome/Firefox media and reconnect checks.
- Legacy Socket.IO admin placeholders fail closed instead of returning false success.
- Real delete-user and role-assignment actions stay on canonical HTTP Admin Panel routes.

## Admin Test Lab group role fix — v0.11.0-beta.123

- Group role changes now return success after persistence even if best-effort realtime refresh has a problem.
- Admin Test Lab role-change diagnostics now include `status_code`, `persisted_role`, and a response preview.

# Features

Version: **0.11.0-beta.299**

## Voice / webcam hard separation — v0.11.0-beta.123

- Webcam and Voice are independent room controls.
- Webcam-only joins do not request a microphone, mark voice as desired, or save voice reconnect flags.
- Reconnect restore only brings microphone voice back when the explicit Voice flag is present.
- Turning Voice off while Webcam remains on removes audio senders and stops the mic while preserving the camera.

## Group chat invite/role hardening — v0.11.0-beta.121

- Group invites use case-insensitive recipient matching for listing, accept/join, decline, and revoke flows.
- Accepting a stale invite re-checks block state against the inviter before membership is granted.
- Role changes, ownership transfer, and metadata edits push group-list refresh events to every current member.
- Open group windows update their title when group metadata changes.
- Group mute/unmute normalizes legacy mixed-case mute rows.

## Admin Test Lab reconnect recovery — v0.11.0-beta.120

- Normal full-suite runs clean up autosplit users automatically instead of holding fake users connected by default.
- Manual autosplit wait mode remains available, but must be explicitly enabled.
- Full-suite runs pre-clean previous visible autosplit loads before starting.
- Test Lab clears synthetic Socket.IO connect/event limiter buckets after full-suite, live-user-flow, and autosplit-cleanup routes.

## File sharing and P2P transfer hardening — v0.11.0-beta.119

- Private-message P2P transfer sessions are cleaned immediately when a block appears after an offer.
- Active P2P transfer IDs cannot be reused while a transfer is in progress.
- Encrypted group file uploads canonicalize member key maps and group file blobs require a wrapped key for the requesting user.
- Deprecated legacy group upload honors global/group file-disable switches.

## Core chat

- official room browsing, category/subcategory navigation, and autosplit overflow shard visibility
- custom rooms
- direct messages with block-aware live send, offline fetch, E2EE key discovery, and encrypted-only by default policy
- group chat with invite, role, metadata-refresh, and block-aware stale-invite protections
- invite flows for rooms and groups with block-aware invite denial
- missed message and alert surfaces with blocked-pair cleanup

## Social

- friends
- friend requests
- block and unblock flows with canonical username/block-enforcement cleanup
- presence states
- custom profile surfaces

## Profile UI

- profile viewing
- embedded profile editing
- avatar and banner media support
- profile comments/posts related UI surfaces in the current browser client
- profile gallery/photos tab with filters and load-more paging
- profile timeline posts feed with load-more paging

## Rich messaging

- emoji picker
- GIF search/picker
- attachments and transfer-related flows with encrypted DM file access blocked after either side blocks the other
- torrent helper surfaces
- reactions in room chat

## Voice and realtime

- room voice signaling
- DM voice signaling
- Socket.IO-based realtime UI updates
- built-in Echo WebRTC voice/webcam controls

## Security and account

- account login and refresh sessions
- account security UI
- password reset
- optional SMS 2FA with Twilio Verify
- role-based admin gating
- rate limiting and anti-abuse controls

## Operations and admin

- interactive setup wizard
- tracked migrations
- preflight checks
- admin tools and settings surfaces
- profile report moderation outcomes with user notifications
- janitor/cleanup controls

## Current product-state note

Feature breadth is high, but release discipline is still catching up. Documentation refresh and test cleanup are part of the current stabilization phase.
- Mobile room chat polish: back-to-Rooms action, users sheet, More tools composer, and Latest jump button.
