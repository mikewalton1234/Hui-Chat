# WebRTC Diagnostics

Echo-Chat includes a logged-in browser diagnostics page at `/webrtc-diagnostics`.
Open it from **Admin Panel → Echo Media → Open WebRTC diagnostics** or visit the path directly after signing in.

The page tests facts that only the browser can know:

- secure-context status: HTTPS, localhost, or 127.0.0.1
- Permissions-Policy support for camera and microphone
- WebRTC API availability
- camera capture with a 640×360 compatible constraint set
- optional microphone capture
- normal ICE/data-channel loopback
- selected ICE candidate type, such as host, srflx, prflx, or relay
- TURN relay-only loopback when a TURN server is configured

## How to read the result

- `host` candidate: usually LAN/same-computer testing.
- `srflx` candidate: STUN found a public/reflexive address.
- `relay` candidate: media/data is going through TURN. This is the important result for strict NAT, cellular, hotel Wi-Fi, and corporate networks.
- Camera failures normally mean browser permission denied, insecure HTTP from a LAN IP/domain, an OS-level camera block, or a webcam already locked by another app.

## Important server header

Echo webcam requires the browser to be allowed to use camera capture. The default Permissions-Policy is now:

```text
geolocation=(), camera=(self), microphone=(self)
```

If you override `permissions_policy` in config, keep `camera=(self)` and `microphone=(self)` or webcam/voice can fail even when the JavaScript is correct.
