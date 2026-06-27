# Echo-Chat

**Echo-Chat** is a self-hosted chat server for people who want to run their own private chat app.

It includes chat rooms, private messages, group chats, friends, profiles, radio listening, file sharing, torrent/magnet tools, voice chat, webcam sharing, mobile support, and a full admin control panel.

Current build: **0.11.0-beta.309**

## What Makes Echo-Chat Special

Echo-Chat is more than a simple chat room. It is built to feel like a full social chat app that you can host yourself.

Main highlights:

* **Public chat rooms** for open conversations
* **Private and invite-only rooms** for smaller locked spaces
* **Direct messages** between users
* **Group chats** with members, roles, and controls
* **Friends, profiles, avatars, posts, comments, badges, and alerts**
* **End-to-end encrypted private messages**
* **Encrypted file sharing** for private messages and groups
* **Peer-to-peer file transfer support**
* **Torrent and magnet link sharing tools**
* **Room radio** so users can listen while chatting
* **iHeartRadio/API-based radio support** for station-style listening
* **Voice chat** in rooms and private conversations
* **Webcam sharing** with viewer controls
* **Admin control panel** for users, rooms, reports, roles, settings, and security
* **Admin Test Lab** for checking the server before release
* **Mobile-friendly layout** for phones and small screens
* **Setup, testing, release, checksum, and rollback tools** for server owners

## Encryption and Privacy

Echo-Chat includes encryption and privacy features for private communication and file sharing.

Encryption features include:

* **End-to-end encrypted private messages**
* Private-message encryption key discovery
* Ciphertext-only private-message relay behavior
* Encrypted private-message file sharing
* Encrypted group file sharing
* Wrapped-key protection for group file access
* Secure file download checks
* Private download headers
* Block-aware message and file access
* Session revocation and forced logout support
* Password reset and account-security protections
* Privacy-retention cleanup tools

Private messages are designed so the server relays encrypted message content instead of plain readable message text.

Group file sharing uses encryption controls so shared files are protected and only intended group members can access the needed file keys.

Public chat rooms are protected with login security, CSRF protection, rate limits, moderation tools, safe links, XSS guards, room locks, slowmode, and admin controls. Public room messages should only be described as end-to-end encrypted if that mode is later confirmed or added.

## Chat Rooms

Echo-Chat supports different kinds of rooms so a server can have both public and private spaces.

Room features include:

* Public chat rooms
* Custom user-created rooms
* Private rooms
* Invite-only rooms
* Room categories
* Room search
* Room history
* Typing indicators
* Message reactions
* Pinned messages
* Polls
* Clickable links
* Room rosters showing who is inside
* Room locks
* Read-only mode
* Slowmode
* Message cleanup and expiration
* Automatic overflow handling for busy rooms

## Private Messages

Users can message each other directly.

Private message features include:

* Live private messages
* End-to-end encrypted private messaging
* Offline message delivery
* Missed-message summaries
* Private message history
* Safe clickable links
* Block protection
* Encrypted file sharing
* Mobile full-screen private message view

## Group Chats

Echo-Chat includes group chats for smaller private communities.

Group features include:

* Create groups
* Invite users
* Accept, decline, or revoke invites
* Group message history
* Read/unread counts
* Member list
* Owner, admin, moderator, and member roles
* Change member roles
* Transfer group ownership
* Kick members
* Mute members
* Edit group details
* Delete groups
* Block-aware group safety checks
* Encrypted group file sharing
* Mobile group chat view
* Mobile group users drawer

## Friends, Blocks, and Alerts

Echo-Chat includes social tools so users can manage who they talk to.

Social features include:

* Friends list
* Friend requests
* Accept or reject requests
* Remove friends
* Block and unblock users
* Blocked-users list
* Online, away, and offline status
* Friend presence updates
* Alerts and toast notifications
* Missed-message alerts
* Block cleanup across messages, invites, files, and alerts

## Profiles

Users can build a profile inside the chat app.

Profile features include:

* View user profiles
* Edit your own profile
* Avatar builder
* DiceBear avatar support
* Avatar uploads
* Banner uploads
* Bio and intro fields
* Favorites and privacy fields
* Profile posts
* Post images
* Reactions
* Comments
* Profile reports
* Pinned or featured posts
* Profile photo gallery
* Badges

## File Sharing

Echo-Chat includes multiple ways to share files.

File-sharing features include:

* Encrypted file sharing in private messages
* Encrypted file sharing in group chats
* Secure file download checks
* File-size limits
* User quota controls
* Admin file-sharing controls
* Option to disable file sharing globally
* Peer-to-peer file transfer support between browsers

## Torrent and Magnet Tools

Echo-Chat includes torrent and magnet helper tools for chat rooms.

Torrent features include:

* Upload `.torrent` files to rooms
* Display torrent cards in chat
* Copy magnet links
* Copy torrent hashes
* Download `.torrent` files
* Show tracker/scrape information
* Show swarm/peer status when available
* Refresh torrent status
* Support pasted magnet links
* Admin controls for torrent settings
* Quota and file-size protection

Server owners are responsible for how torrent and file-sharing features are configured and moderated.

## Room Radio

Echo-Chat includes room radio so users can listen while chatting.

Room radio features include:

* Radio-enabled chat rooms
* iHeartRadio/API-based radio support
* Station/source buttons
* Embedded room audio player
* Compact mini-player
* Full-player view
* Listener count
* Skip voting
* Continue playback after skipping
* Stop playback when leaving a room
* Stop playback when switching rooms
* Admin station editor
* Admin add/remove/reorder station controls
* HTTPS station/source validation

## Voice and Webcam

Echo-Chat includes built-in voice and webcam features.

Voice and webcam features include:

* Voice chat in rooms
* Private voice calls
* Webcam sharing
* Webcam-only mode without forcing microphone audio
* Webcam viewer requests
* Viewer list
* Viewer kick controls
* Quality fallback for weaker connections
* WebRTC diagnostics
* STUN/TURN setup support for better connections

## Admin Control Panel

Echo-Chat includes a full Admin Panel for server owners and moderators.

Admin features include:

* Server stats
* Diagnostics
* Security dashboard
* User search
* User details and activity
* Create users
* Reset passwords
* Recovery PIN tools
* Suspend, deactivate, delete, or force logout users
* Ban IP addresses
* Manage roles and permissions
* Room moderation tools
* Mute, kick, or ban users from rooms
* Lock, unlock, clear, or slow down rooms
* Send global broadcasts
* Revoke 2FA
* Manage user quotas
* View audit logs
* Review reports
* Moderate profile posts and comments
* Incident mode presets
* Radio station editor
* Torrent setting controls

## Admin Test Lab

Echo-Chat includes an Admin Test Lab to help check the server before release or public use.

Test Lab features include:

* Readiness checks
* Browser checks
* Live user-flow tests
* Room autosplit checks
* Release gate checks
* Exportable test results
* Hidden/randomized test link for admins

## Mobile Support

Echo-Chat has mobile-friendly layouts for phones and small screens.

Mobile features include:

* Mobile room browser
* Mobile chat controls
* Latest-message jump button
* Mobile users drawer
* Mobile friends, alerts, groups, and profile sections
* Full-screen private messages
* Full-screen group chats
* Mobile profile editing
* Mobile settings controls
* Larger tap targets
* Small-screen Admin Panel improvements

## Setup and Server Tools

Echo-Chat includes tools to help server owners set up, check, and release the project.

Server tools include:

* Interactive setup wizard
* Database setup and verification
* Admin account setup
* SMTP/email setup
* Optional SMS 2FA setup
* STUN/TURN setup for WebRTC
* Deployment helper
* Reverse proxy examples
* Systemd service examples
* Dynamic DNS helper
* Config doctor
* Service smoke test
* Log sanity scanner
* Release report exporter
* Package checksum tool
* Upgrade and rollback docs
* First-run handoff guide
* Final QA checklist

## Release and Handoff Tools

Echo-Chat includes tools to help package and verify a release before handing it off or running it publicly.

Release tools include:

* Release integrity checks
* Dependency checks
* Final release smoke checks
* Release report export
* Version archive
* Package checksum generation
* `.sha256` checksum sidecar
* Upgrade guide
* Rollback guide
* Admin handoff guide
* Operator first-run guide

## Tech Stack

Echo-Chat is built with:

* Python
* Flask
* Flask-SocketIO
* PostgreSQL
* HTML
* CSS
* JavaScript
* JWT cookie authentication
* CSRF protection
* WebRTC support
* Optional Redis support
* Optional Twilio Verify support
* Optional STUN/TURN support

## Status

Echo-Chat is currently in **beta**.

The project is being tested feature-by-feature before a full public release.

Recently tested areas include:

* Authentication and account security
* Room behavior
* Private rooms
* Direct messages
* Group chats
* Friends, blocks, profiles, and alerts
* Encrypted file sharing
* Torrent and magnet tools
* WebRTC voice and webcam
* Room radio
* Admin tools
* Security hardening
* Mobile UI
* Release packaging
* Operator handoff

## Security Notes

Do **not** commit private runtime files such as:

* `.env`
* `server_config.json`
* database files
* private keys
* certificates
* uploaded user files
* downloaded user files
* backups
* logs
* production secrets

Use the included setup, config, release, and public-beta checks before exposing a server online.

## Basic Run Flow

Typical local setup:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --setup
python main.py
```

Production or public setup should be reviewed with the included config, release, security, and readiness checks before exposing the server online.

## License

See the `LICENSE` file for the full license text.
