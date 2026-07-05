## 0.11.0-beta.177 - production readiness and end-user mobile bug hunt

- Added PUBLIC_URL env aliases for documented deployment env files.
- Normalized health endpoint env values so `healthz` becomes `/healthz`.
- Added public-beta production startup blocking for failed deployment readiness checks.
- Hardened public beta readiness around Cookie SameSite, proxy hop count, PostgreSQL DSN scheme, shared-state Redis defaults, and health endpoint paths.
- Fixed mobile room Users drawer discoverability by adding a visible close button inside the users sheet.

## 0.11.0-beta.177 mobile release-candidate audit

The mobile path has been audited across Rooms, Chat, Hub, PMs, Groups, Profile, Login, Register, and Settings. The RC guard pass fixes profile windows receiving duplicate mobile nav, disables Chat bottom-nav until a room is open, and keeps public profile Edit controls disabled unless owner edit actions exist.

## 0.11.0-beta.177 profile/avatar/gallery mobile behavior

The mobile GUI now treats profile windows as phone sheets. Users can move between Posts, About, Photos, and Favorites from a top action strip, return to Hub, close the sheet, or open an Edit drawer for Avatar, Banner, Bio, Intro, Favorites, and Privacy. Gallery filters and avatar/DiceBear choices are large touch targets and scroll cleanly on phones.

# Mobile Chat GUI

Echo-Chat now has a phone/tablet shell for the main chat page. It is not a separate phone app. It is the same authenticated `/chat` page with responsive CSS and a small mobile controller.

## Design

Desktop keeps the existing classic layout:

```text
Room area / room browser  |  Hub dock
```

Phones switch to one active panel at a time:

```text
Rooms  |  Chat  |  Hub
```

The bottom navigation appears only on narrow or coarse-pointer layouts.

## Detection

The server gives a non-security hint from User-Agent and `Sec-CH-UA-Mobile`. The browser still uses `matchMedia("(max-width: 820px), (max-width: 1024px) and (pointer: coarse)")` because window size and touch behavior are more reliable for layout than User-Agent alone.

## Assets

- Main CSS remains `/static/css/chat.css`.
- Mobile CSS is `/static/css/mobile.css` and is loaded unconditionally, but all broad layout rules are gated behind the mobile runtime classes so desktop stays untouched.
- Mobile behavior lives in `static/js/chat_parts/0050_mobile_layout.js` and is included in the normal ordered chat bundle.

## Admin/testing notes

Test with:

1. Desktop browser at normal width.
2. Desktop browser narrowed below 820px.
3. Chrome/Firefox responsive device mode.
4. Real phone over LAN or HTTPS beta domain.
5. Room join, leave, PM window, settings modal, emoji picker, and hub tabs.
## Mobile login over LAN

If a phone accepts the username/password but immediately lands back on `/login`, the login probably succeeded but the phone did not keep the auth cookies. This commonly happens when `cookie_secure` or `https` is enabled before the phone is actually using an HTTPS URL.

Echo-Chat now includes a guarded LAN fallback for local testing:

- Normal secure cookie behavior is used first.
- If the request is plain HTTP from a private/local client and the server is not configured with a public HTTPS base URL, Echo-Chat re-sets the JWT and CSRF cookies without `Secure`.
- The fallback can be disabled with `allow_insecure_lan_cookie_fallback=false`.

For public beta testing, use a real HTTPS domain and keep secure cookies enabled. The LAN fallback is only for local phone testing, such as `http://192.168.x.x:5000`.

## v0.11.0-beta.177 mobile fit fixes

- Mobile now hard-fits the main chat shell to the phone viewport and keeps overflow inside the active panel.
- Room rows switch to stacked buttons on phones so Join/Favorite/Invite controls do not squeeze the room name.
- The admin panel collapses into a small top bar on phones unless the admin maximizes it.
- Pending friend request rows now emphasize the requester username and include an accessible `Friend request from <username>` label.



## v0.11.0-beta.177 mobile room chat polish

The mobile chat panel now uses a phone-first layout:

- The active room chat fills the phone viewport below the mobile navigation area.
- The message log scrolls independently, so the whole page should not slide sideways or vertically as a desktop page.
- The composer uses a two-row layout: full-width message input above Emoji, Torrent, GIF, and Send buttons.
- The input uses a 16px font on mobile to avoid browser zoom-on-focus behavior.
- The room user list is hidden by default and opens from a mobile **Users** button as a slide-up sheet.
- The room user count on that button follows the live `roomUsersCount` value.
- Reactions are visible below room messages on mobile because touch devices cannot rely on hover.
- When the mobile keyboard opens, the bottom mobile nav hides so the composer has more room.

## v0.11.0-beta.177 mobile PM/group/auth/settings polish

The mobile polish pass now covers more than room chat:

- Private-message and group-chat windows become phone-style conversation sheets.
- PM/group windows have a compact mobile strip for **← Hub**, **More/Tools**, group **Users**, and **Close**.
- Toolbars are collapsed by default so File/GIF/Voice actions do not crowd the message input.
- Group users open as a drawer instead of staying permanently beside messages.
- Register uses the shared auth theme and is split into Identity, Recovery PIN, and Age/password sections.
- Settings uses phone-style horizontal section chips plus sticky Save/Close actions.

## Release notes

- beta.151: Added STUN/TURN setup/admin helpers for real internet webcam/voice/P2P testing, runtime ICE env overrides, and cleaned new setup output so legacy ICE aliases/public-upload controls are not emitted.
