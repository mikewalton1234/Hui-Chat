# Webcam compatibility and performance

Echo-Chat uses the browser WebRTC stack directly for room voice and webcam. The beta.151 pass focuses on small-room compatibility and reliability without bringing back an external media server.

## Browser requirements

- Camera and microphone capture require HTTPS, `localhost`, or `127.0.0.1`. LAN IP testing over plain HTTP is expected to fail in modern browsers.
- Users must approve camera/microphone permission in the browser. Echo-Chat cannot bypass browser permission prompts.
- Real internet-to-internet webcam sessions need usable ICE servers. STUN is enough for some networks; restrictive NATs usually require a TURN relay.

## Runtime behavior

- The default webcam codec strategy is now `prefer-compatible`. H.264/VP8 are tried before newer codecs so Chrome, Firefox, Edge, and Safari have a safer common path.
- Webcam capture opens with the selected quality, falls back to `balanced`, then `low`, then browser defaults if constraints are rejected. Permission-denied, camera-missing, and camera-busy errors stop immediately with a clearer message.
- Low and balanced profiles reduce capture resolution and FPS before bitrate, which usually looks cleaner and starts faster than forcing a high-resolution camera stream into a tiny bitrate.
- Room WebRTC signaling queues ICE candidates that arrive before `remoteDescription`, then flushes them after offer/answer setup. This avoids losing candidates during fast reconnects or negotiation races.
- Peer connections use `bundlePolicy: max-bundle`, `rtcpMuxPolicy: require`, `iceCandidatePoolSize: 2`, and debounced ICE restart on failed connections.

## Admin guidance

Recommended defaults for public beta:

- Webcam quality: `balanced`
- Codec strategy: `prefer-compatible`
- Approval mode: `owner_approval`
- Max viewers: keep unlimited only for trusted small rooms; set a cap for public rooms.
- ICE servers: configure at least STUN; add TURN before real public testing across different networks.

Echo WebRTC mesh is best for small webcam groups. A room where many users broadcast camera at the same time will multiply upload bandwidth from each publisher. Keep webcam owner-approval on and encourage users to view only streams they actually need.


## STUN/TURN setup added in beta.151

- Setup wizard → **Voice and WebRTC** now includes a guided STUN/TURN helper.
- Admin Panel → **Echo Voice** now has a **STUN/TURN connectivity** card with redacted ICE summaries and a paste-in JSON/URL editor.
- Runtime env overrides support `ECHOCHAT_WEBRTC_ICE_SERVERS_JSON`, `ECHOCHAT_VOICE_ICE_SERVERS_JSON`, and the simpler `ECHOCHAT_TURN_URLS` + username + credential form.
- New configs save only `p2p_ice_servers` and `voice_ice_servers`; old alias keys are still read but no longer emitted by setup.

See `docs/STUN_TURN_SETUP.md` for exact examples.
