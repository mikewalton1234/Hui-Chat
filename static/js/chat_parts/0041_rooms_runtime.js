// Rooms
// ───────────────────────────────────────────────────────────────────────────────
function ecRoomSidebarLeft(dotClass, nameText, opts = {}) {
  const left = document.createElement('div');
  left.className = 'liLeft';
  const dot = document.createElement('span');
  dot.className = `presDot ${dotClass || 'offline'}`;
  left.appendChild(dot);

  if (opts.avatarText || opts.avatarUrl) {
    const av = document.createElement('span');
    av.className = 'liAvatar';
    if (opts.avatarUrl && typeof renderDockAvatar === 'function') {
      renderDockAvatar(av, nameText, opts.avatarUrl);
    } else {
      av.textContent = String(opts.avatarText || '');
    }
    left.appendChild(av);
  }

  const name = document.createElement('span');
  name.className = opts.muted ? 'liName muted' : 'liName';
  name.textContent = String(nameText || '');

  if (opts.policyText) {
    name.appendChild(document.createTextNode(String(opts.policyText || '')));
  }

  if (opts.descText) {
    name.appendChild(document.createTextNode(' '));
    const desc = document.createElement('span');
    desc.className = 'muted';
    desc.textContent = String(opts.descText || '');
    name.appendChild(desc);
  }

  left.appendChild(name);
  return left;
}

function ecRoomSidebarEmptyRow(message, opts = {}) {
  const li = document.createElement('li');
  li.dataset.name = opts.name || 'none';
  li.appendChild(ecRoomSidebarLeft(opts.dotClass || 'offline', message, {
    avatarText: opts.avatarText || '',
    muted: opts.muted !== false,
  }));
  return li;
}

async function ecInviteUserToCurrentRoom(defaultUser = "") {
  const room = String(UIState.currentRoom || UIState.roomEmbedRoom || "").trim();
  if (!room) return toast("Join a room first, then invite someone.", "warn");
  let invitee = String(defaultUser || "").replace(/^@/, "").trim();
  if (!invitee && typeof ecPrompt === "function") {
    invitee = await ecPrompt(`Invite which username to ${room}?`, "", {
      title: "Invite user to room",
      label: "Username",
      confirmLabel: "Send invite",
      placeholder: "username",
    });
    invitee = String(invitee || "").replace(/^@/, "").trim();
  }
  if (!invitee) return;
  if (invitee === String(currentUser || "").trim()) return toast("You cannot invite yourself.", "warn");
  try {
    const res = await apiJson('/api/rooms/invite', { method: 'POST', body: JSON.stringify({ room, invitee }) });
    toast(`✅ Invited ${invitee} to ${room}`, "ok");
    return res;
  } catch (e) {
    toast(`❌ Invite failed: ${e?.message || e}`, "error", 7000);
    return null;
  }
}

function ecCanRequestUserWebcam(username, room = UIState.currentRoom) {
  username = String(username || "").trim();
  if (!username || username === String(currentUser || "").trim()) return false;
  try {
    if (typeof ecMediaModeReady !== "function" || !ecMediaModeReady()) return false;
    if (typeof voiceStatusForUser !== "function") return true;
    const st = voiceStatusForUser(username, room);
    return !!(st && st.webcam_on);
  } catch {
    return false;
  }
}

function ecMakeRoomUserActionButton({ label, title, className = "iconBtn", disabled = false, onClick }) {
  const btn = document.createElement("button");
  btn.className = className;
  btn.type = "button";
  btn.textContent = label;
  btn.title = title || label;
  btn.setAttribute("aria-label", title || label);
  btn.disabled = !!disabled;
  if (typeof onClick === "function") btn.onclick = onClick;
  return btn;
}

function renderRooms(rooms) {
  const ul = $("roomList");
  if (!ul) return;

  // Allow callers (like room policy updates) to re-render without having to
  // pass the rooms list each time.
  if (Array.isArray(rooms)) UIState.roomsCache = rooms;
  const list = Array.isArray(rooms) ? rooms : (Array.isArray(UIState.roomsCache) ? UIState.roomsCache : []);

  ul.replaceChildren();

  if (!list || list.length === 0) {
    ul.appendChild(ecRoomSidebarEmptyRow('No rooms'));
    return;
  }

  list.forEach(r => {
    const name = (r && (r.name || r.room_id)) || String(r);
    const desc = (r && r.description) ? `(${r.description})` : "";

    const li = document.createElement("li");
    li.dataset.name = name;

    const pol = getRoomPolicy(name);
    const policyIcons = pol ? `${pol.locked ? " 🔒" : ""}${pol.readonly ? " 📝" : ""}${(Number(pol.slowmode_seconds||0) > 0) ? " 🐢" : ""}` : "";

    const left = ecRoomSidebarLeft('offline', name, {
      policyText: policyIcons,
      descText: desc,
    });

    const actions = document.createElement("div");
    actions.className = "liActions";

    const joinBtn = document.createElement("button");
    joinBtn.className = "iconBtn";
    joinBtn.textContent = "🚪";
    joinBtn.title = "Join";
    joinBtn.onclick = () => joinRoom(name);

    actions.appendChild(joinBtn);

    li.appendChild(left);
    li.appendChild(actions);
    li.ondblclick = () => joinRoom(name);

    ul.appendChild(li);
  });
}

function ecNormalizeRoomRows(rows) {
  if (!Array.isArray(rows)) return [];
  const seen = new Set();
  const out = [];
  rows.forEach((row) => {
    const name = String((row && typeof row === 'object') ? (row.name || row.room || row.room_id || '') : row || '').replace(/\s+/g, ' ').trim();
    if (!name) return;
    const key = name.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    if (row && typeof row === 'object') out.push({ ...row, name });
    else out.push({ name });
  });
  return out;
}

async function ecLoadRoomsViaHttpFallback(reason = '') {
  try {
    if (typeof fetchWithAuth !== 'function') return false;
    const resp = await fetchWithAuth('/api/rooms', { method: 'GET' }, { retryOn401: true });
    const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
    if (!resp || !resp.ok) return false;
    const rooms = ecNormalizeRoomRows(Array.isArray(data) ? data : (Array.isArray(data?.rooms) ? data.rooms : []));
    if (rooms.length) {
      renderRooms(rooms);
      try { if (typeof rbRefreshLists === 'function') rbRefreshLists(); } catch {}
      return true;
    }
  } catch (e) {
    try { console.warn('[Hui Chat] room HTTP fallback failed', reason, e); } catch {}
  }
  return false;
}

async function getRooms(opts = {}) {
  // If we have server-rendered initial rooms, show instantly:
  if (Array.isArray(window.INIT_ROOMS) && window.INIT_ROOMS.length > 0) {
    renderRooms(window.INIT_ROOMS);
  }

  const useAck = typeof ecEmitAck === 'function';
  if (useAck) {
    const res = await ecEmitAck('get_rooms', {}, Number(opts.timeoutMs || 6500), { connectBannerText: '🔌 Loading rooms…' });
    if (res && res.success !== false && Array.isArray(res.rooms)) {
      const rows = ecNormalizeRoomRows(res.rooms);
      renderRooms(rows);
      return rows;
    }
    await ecLoadRoomsViaHttpFallback(res?.error || 'socket_ack_failed');
    return [];
  }

  try { socket.emit("get_rooms"); } catch (_) {}
  // Safety net: if the socket event was dropped during reconnect/auth recovery,
  // load the same room list through the REST endpoint so the UI does not stay blank.
  window.setTimeout(() => {
    try {
      const cur = Array.isArray(UIState.roomsCache) ? UIState.roomsCache : [];
      if (!cur.length) ecLoadRoomsViaHttpFallback('socket_no_room_list');
    } catch {}
  }, 1800);
  return [];
}

socket.on("room_list", (data) => {
  if (!data || !Array.isArray(data.rooms)) {
    toast("❌ Failed to fetch rooms", "error");
    return;
  }
  renderRooms(ecNormalizeRoomRows(data.rooms));
});

// Server hint that room inventory changed (autoscaled room created/deleted).
socket.on("rooms_changed", (_payload) => {
  try {
    getRooms();
    if (typeof rbRefreshLists === "function") rbRefreshLists();
  } catch (e) {}
});

socket.on("room_counts", (payload) => {
  try {
    if (payload && payload.counts) rbApplyRoomCounts(payload.counts);
  } catch (e) {}
});

// Live room policy state (locked/read-only/slowmode)
socket.on("room_policy_state", (payload) => {
  try {
    const room = payload?.room;
    if (!room) return;
    upsertRoomPolicy(room, {
      locked: !!payload.locked,
      readonly: !!payload.readonly,
      slowmode_seconds: Number(payload.slowmode_seconds || 0),
      can_send: payload.can_send !== undefined ? !!payload.can_send : true,
      can_override_lock: !!payload.can_override_lock,
      can_override_readonly: !!payload.can_override_readonly,
      block_reason: payload.block_reason || null,
      set_by: payload.set_by || null,
      ts: payload.ts || null,
      is_custom_room: !!payload.is_custom_room,
      is_private_room: !!payload.is_private_room,
      room_owner: payload.room_owner || null,
      my_room_role: payload.my_room_role || null,
      can_room_moderate: !!payload.can_room_moderate,
    });
  } catch (e) {
    console.warn('room_policy_state handler failed', e);
  }
});

socket.on("room_forced_leave", (payload) => {
  try {
    const room = payload?.room;
    if (!room) return;
    const reason = payload?.reason || 'removed';
    forceLeaveRoomUI(room, reason);
  } catch (e) {
    console.warn('room_forced_leave handler failed', e);
  }
});

socket.on("admin_kick", (payload) => {
  try {
    if (!payload) return;
    const room = payload.room;
    const who = payload.username;
    if (who && String(who) === String(currentUser) && room) {
      forceLeaveRoomUI(room, 'kicked');
    } else if (room && who) {
      toast(`👢 ${who} was kicked from ${room}`, 'warn', 4200);
    }
  } catch {}
});

function bestEffortForceLogout(payload) {
  try {
    const who = payload?.username;
    if (who && currentUser && String(who) !== String(currentUser)) return;

    const reason = String(payload?.code || payload?.reason || payload?.message || "session_revoked");
    try { sessionStorage.setItem("hui_logout_reason", String(payload?.reason || payload?.message || "Signed out")); } catch (e) {}
    try { socket.disconnect(); } catch (e) {}
    if (typeof bestEffortLogoutThenRedirect === 'function') {
      bestEffortLogoutThenRedirect(reason).catch(() => {
        try { window.location.href = `/login?reason=${encodeURIComponent(reason)}`; } catch (_e) {}
      });
      return;
    }
    window.location.href = `/login?reason=${encodeURIComponent(reason)}`;
  } catch (e) {
    try { window.location.href = "/login?reason=session_revoked"; } catch (_e) {}
  }
}

socket.on("force_logout", (payload) => bestEffortForceLogout(payload));
socket.on("admin_force_logout", (payload) => bestEffortForceLogout(payload));

socket.on("slowmode_state", (payload) => {
  try {
    const room = payload?.room;
    const sec = Number(payload?.seconds || 0);
    if (room) upsertRoomPolicy(room, { slowmode_seconds: sec });
  } catch {}
});

socket.on("global_announcement", (payload) => {
  const msg = String(payload?.message || '').trim();
  if (!msg) return;
  toast(`📣 ${msg}`, 'info', 6500);
  try {
    const room = String(UIState?.currentRoom || UIState?.roomEmbedRoom || '').trim();
    const view = room && typeof getActiveRoomView === 'function' ? getActiveRoomView(room) : null;
    if (view && typeof appendGlobalAnnouncement === 'function') appendGlobalAnnouncement(view, payload);
  } catch (e) {
    console.warn('global_announcement render failed', e);
  }
  try { maybeBrowserNotify('Server announcement', msg); } catch {}
});


const EC_ROOM_ACK_TIMEOUT_MS = 15000;
const EC_ROOM_JOIN_RECOVERY_TIMEOUT_MS = 6000;
const EC_ROOM_USERS_TIMEOUT_MS = 2500;
const EC_ROOM_SOCKET_CONNECT_WAIT_MS = 6000;

function ecWaitForSocketConnected(timeoutMs = EC_ROOM_SOCKET_CONNECT_WAIT_MS) {
  if (typeof ecWaitForSocketReady === "function") {
    return ecWaitForSocketReady(timeoutMs, {
      trigger: "room_socket_connect",
      bannerText: "🔌 Connecting to room server…",
      bannerDelayMs: 600,
    });
  }
  return Promise.resolve(!!(socket && socket.connected));
}

async function ecSocketEmitAck(eventName, payload, timeoutMs = EC_ROOM_ACK_TIMEOUT_MS) {
  if (typeof ecEmitAck === "function") {
    return await ecEmitAck(eventName, payload, timeoutMs, {
      connectBannerText: "🔌 Connecting to room server…",
      bannerDelayMs: 600,
    });
  }
  return { success: false, error: "not_connected", event: String(eventName || "") };
}

function ecDelay(ms) {
  const n = Math.max(0, Number(ms || 0));
  return new Promise((resolve) => setTimeout(resolve, n));
}

async function ecRecoverRoomJoinAfterAckTimeout(requestedRoom) {
  const requested = String(requestedRoom || "").trim();
  if (!requested) return { success: false, error: "missing_room" };

  // A join can finish on the server but miss the browser's ACK window when
  // Gunicorn/Replit is cold, the DB is waking up, or a room-list broadcast is
  // slow. Probe the server's authoritative Socket.IO room state before telling
  // the user the join failed. This probe is read-only server-side.
  await ecDelay(450);
  const probe = await ecSocketEmitAck("get_join_state", { requested_room: requested }, EC_ROOM_JOIN_RECOVERY_TIMEOUT_MS);
  if (probe && probe.success && String(probe.room || "").trim()) {
    probe.recovered_from_ack_timeout = true;
    return probe;
  }
  return probe || { success: false, error: "ack_timeout" };
}

function ecRoomJoinContext(requestedRoom, joinedRoom, res, opts) {
  opts = opts || {};
  const requested = String(requestedRoom || '').trim();
  const joined = String(joinedRoom || requested || '').trim();
  const row = opts.row || opts.roomRow || opts.contextRow || null;
  let isCustom = false;
  let category = null;
  let subcategory = null;
  let meta = null;

  if (row && String(row.name || '').trim()) {
    isCustom = !!row.isCustom;
    category = row.category || row.meta?.category || null;
    subcategory = row.subcategory || row.meta?.subcategory || null;
    meta = row.meta || null;
  }

  if (res && Object.prototype.hasOwnProperty.call(res, 'is_custom_room')) isCustom = !!res.is_custom_room;
  else if (res && Object.prototype.hasOwnProperty.call(res, 'is_official_room')) isCustom = !res.is_official_room;

  if (res?.category) category = String(res.category);
  if (res?.subcategory) subcategory = String(res.subcategory);
  if (res?.meta && typeof res.meta === 'object') meta = res.meta;

  if (!isCustom && typeof rbFindCatalogRoom === 'function') {
    const officialHit = rbFindCatalogRoom(joined) || rbFindCatalogRoom(requested) || rbFindCatalogRoom(res?.official_room || res?.autosplit_from || '');
    if (officialHit) {
      category = category || officialHit.category || null;
      subcategory = subcategory || officialHit.subcategory || null;
      meta = meta || officialHit.meta || null;
    }
  }

  if (isCustom && typeof rbResolveCustomMeta === 'function') {
    meta = rbResolveCustomMeta(joined, meta) || meta;
    category = category || meta?.category || null;
    subcategory = subcategory || meta?.subcategory || null;
  }

  return {
    name: joined,
    isCustom: !!isCustom,
    category: category || null,
    subcategory: subcategory || null,
    meta: meta || null,
    cnt: (typeof rbMapGetRoomValue === 'function') ? (rbMapGetRoomValue(ROOM_BROWSER?.counts, joined, 0) || 0) : ((ROOM_BROWSER?.counts instanceof Map) ? (ROOM_BROWSER.counts.get(joined) || 0) : 0),
  };
}

function joinRoom(room, opts) {
  opts = opts || {};
  const silent = !!opts.silent;
  const restore = !!opts.restore;
  const requestedRoom = String(room || "").trim();
  const previousRoom = String(UIState.currentRoom || "").trim();

  if (!requestedRoom) return Promise.resolve({ success: false, error: "missing_room" });

  const previousVoiceRoom = (VOICE_STATE?.room?.joined && VOICE_STATE.room.name && VOICE_STATE.room.name !== requestedRoom)
    ? String(VOICE_STATE.room.name)
    : "";

  const joinPayload = { room: requestedRoom };
  if (opts.autoJoinCreatedCustomRoom || opts.auto_join_created_custom_room) {
    joinPayload.auto_join_created_custom_room = true;
  }

  return ecSocketEmitAck("join", joinPayload, EC_ROOM_ACK_TIMEOUT_MS).then(async (initialRes) => {
    let res = initialRes;
    if (res && res.error === "ack_timeout") {
      const recovered = await ecRecoverRoomJoinAfterAckTimeout(requestedRoom);
      if (recovered && recovered.success) {
        res = recovered;
      }
    }

    if (res && res.success) {
      const joinedRoom = String(res?.room || requestedRoom).trim();
      UIState.currentRoom = joinedRoom;
      const roomToJoin = $("roomToJoin");
      if (roomToJoin) roomToJoin.value = joinedRoom;

      // Persist for reconnect/session restore (per-tab). Store the actual room
      // returned by the server, not the originally requested base room. This
      // matters when autosplit sends the user to e.g. "Teen Talk (2)".
      try {
        sessionStorage.setItem("hui_last_room", String(joinedRoom));
        sessionStorage.setItem("hui_last_room_set_at", String(Date.now()));
      } catch (e) {}

      if (!silent && !restore) {
        if (res?.recovered_from_ack_timeout) toast(`🚪 Joined room: ${joinedRoom} — recovered after a slow server ACK`, "ok", 4500);
        else if (joinedRoom !== requestedRoom) toast(`🚪 ${requestedRoom} full — joined: ${joinedRoom}`, "ok");
        else toast(`🚪 Joined room: ${joinedRoom}`, "ok");
      }
      const sameRoomReasserted = !!(res && res.same_room_reasserted);
      openRoomEmbedded(joinedRoom, {
        preserveLog: previousRoom === joinedRoom && (restore || sameRoomReasserted)
      });

      const standardVoiceWanted = !!(VOICE_STATE?.room?.wantRoomVoice) || (() => { try { return sessionStorage.getItem("hui_voice_desired") === "1"; } catch { return false; } })();
      if (previousVoiceRoom && previousVoiceRoom !== joinedRoom && VOICE_STATE?.room?.joined && String(VOICE_STATE.room.name || '') === previousVoiceRoom) {
        voiceLeaveRoom("Switching rooms", true, { keepDesired: true, silent: true });
      }

      // If the user left voice enabled, carry it to the newly joined room until
      // the user explicitly disables voice. Enhanced media handles this with its own
      // desired flags; standard voice uses the legacy P2P voice room.
      try {
        if (ecMediaModeReady()) {
          await ecMediaSwitchRoomIfDesired(joinedRoom);
        } else if (standardVoiceWanted) {
          await voiceJoinRoom(joinedRoom, { silent: true, restore: restore });
        }
      } catch (e) {
        console.warn("voice/webcam room carry-over failed", e);
      }

      // Room history is disabled; each intentional room join starts with a fresh live log.
      // Transient reconnect restores keep the live in-memory log so the user stays where they left off.

      try {
        const joinCtx = ecRoomJoinContext(requestedRoom, joinedRoom, res, opts);
        rbRememberRecentRoom(joinCtx);
        rbClearUnread(joinedRoom);
        rbSetSelectedRow(rbBuildRow(joinedRoom, {
          isCustom: !!joinCtx.isCustom,
          meta: joinCtx.meta,
          category: joinCtx.category,
          subcategory: joinCtx.subcategory,
        }), { syncCategory: false });
        rbRenderRoomLists();
        rbClosePopoutAfterRoomChoice();
      } catch {}
      getUsersInRoom(joinedRoom);
      return res;
    }

    const err = res?.error || "join_failed";
    if (!silent && !restore) {
      if (err === "not_connected") toast("🔌 Not connected yet. Reconnect and try the room again.", "warn", 5000);
      else if (err === "ack_timeout") toast("⏳ Room join did not finish. Check the server connection and try again.", "warn", 6500);
      else toast(`❌ Failed to join room: ${err || requestedRoom}`, "error");
    }
    return res || { success: false, error: err };
  });
}

function leaveRoom() {
  const room = UIState.currentRoom;
  if (!room) return toast("⚠️ Not in a room", "warn");

  try {
    if (typeof roomMediaStopLocalPlayback === 'function') {
      roomMediaStopLocalPlayback(room, { hideRail: true, heartbeat: true });
    }
  } catch {}

  if (VOICE_STATE?.room?.joined && VOICE_STATE.room.name === room) {
    voiceLeaveRoom("Left room", true, { keepDesired: true, silent: true });
  }
  try {
    if (ecMediaIsConnectedToRoom(room)) {
      ecMediaLeave("Left room", { preserveDesired: true, silent: true });
    }
  } catch {}

  ecSocketEmitAck("leave", { room }, EC_ROOM_ACK_TIMEOUT_MS).then((res) => {
    if (res && res.success) {
      toast(`👋 Left room: ${room}`, "warn");
      UIState.currentRoom = null;
      // Clear restore targets when the user intentionally leaves.
      try {
        sessionStorage.removeItem("hui_last_room");
        sessionStorage.removeItem("hui_last_room_set_at");
        sessionStorage.removeItem("hui_voice_room");
        sessionStorage.removeItem("hui_voice_room_joined");
        // Keep hui_voice_desired so the next joined room can restore voice.
        // The Voice button itself clears this when the user disables voice.
      } catch (e) {}
      const roomToJoin = $("roomToJoin");
      if (roomToJoin) roomToJoin.value = "";
      const userList = $("userList");
      if (userList) userList.replaceChildren();
      setRoomUsersCount(0);
      showRoomEmbed(null);
      try { rbRenderRoomLists(); } catch {}

    } else {
      const err = res?.error || "leave_failed";
      if (err === "not_connected") toast("🔌 Not connected. Reconnect before leaving the room server-side.", "warn", 6000);
      else if (err === "ack_timeout") toast("⏳ Leave did not finish. Check the server connection and try again.", "warn", 6500);
      else toast(`❌ Failed to leave room: ${err}`, "error");
    }
  });
}

let _roomUsersWaiters = [];

function ecNormalizeRoomUsersWaitRoom(room) {
  return String(room || "").replace(/\s+/g, " ").trim();
}

function ecRemoveRoomUsersWaiter(waiter) {
  _roomUsersWaiters = _roomUsersWaiters.filter(w => w !== waiter);
  try { clearTimeout(waiter.timer); } catch {}
}

const EC_ROOM_ROSTER_SELF_HEAL_MS = 2500;
let EC_ROOM_ROSTER_SELF_HEAL_LAST = { room: "", at: 0 };

function ecIsActiveRoom(room) {
  return String(room || "").trim() === String(UIState?.currentRoom || "").trim();
}

function ecSetRoomUsersPanelStatus(text) {
  const el = $("roomUsersStatus");
  if (!el) return;
  el.textContent = String(text || "");
}

function ecRenderRoomUsersPlaceholder(text, count = 0) {
  const ul = $("userList");
  if (!ul) return;
  ul.replaceChildren();
  setRoomUsersCount(count);
  ecSetRoomUsersPanelStatus(text);
  ul.appendChild(ecRoomSidebarEmptyRow(text));
}

function ecNormalizeRoomUsersList(users) {
  const out = [];
  const seen = new Set();
  for (const raw of Array.isArray(users) ? users : []) {
    const rawName = (raw && typeof raw === "object")
      ? (raw.username || raw.name || raw.user || raw.friend || '')
      : raw;
    const name = String(rawName || "").replace(/\s+/g, " ").trim();
    if (!name) continue;
    if (raw && typeof raw === "object") {
      try { ecCacheUserProfileAvatar(name, raw, { online: true, presence: 'online' }); } catch {}
    }
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(name);
    if (out.length >= 500) break;
  }
  return out.sort((a, b) => {
    const self = String(currentUser || "").trim().toLowerCase();
    if (self) {
      if (a.toLowerCase() === self) return -1;
      if (b.toLowerCase() === self) return 1;
    }
    return a.localeCompare(b, undefined, { sensitivity: "base" });
  });
}

function ecCacheRoomUsersPayloadProfiles(payload, users = []) {
  if (!payload || typeof payload !== "object") return;
  const profileMap = payload.user_profiles || payload.profiles || payload.profile_map || null;
  if (profileMap && typeof profileMap === "object" && !Array.isArray(profileMap)) {
    Object.entries(profileMap).forEach(([name, profile]) => {
      try { ecCacheUserProfileAvatar(name, profile || {}, { online: true, presence: 'online' }); } catch {}
    });
  }
  const avatars = payload.avatars || payload.avatar_map || payload.avatar_urls || null;
  if (avatars && typeof avatars === "object" && !Array.isArray(avatars)) {
    Object.entries(avatars).forEach(([name, avatarUrl]) => {
      try { ecCacheUserAvatar(name, avatarUrl, { online: true, presence: 'online' }); } catch {}
    });
  }
  if (Array.isArray(payload.users)) {
    payload.users.forEach((row) => {
      if (row && typeof row === "object") {
        const name = row.username || row.name || row.user || '';
        try { ecCacheUserProfileAvatar(name, row, { online: true, presence: 'online' }); } catch {}
      }
    });
  }
  if (Array.isArray(users)) {
    users.forEach((name) => {
      const avatarUrl = ecRoomUserAvatarUrl(name);
      if (avatarUrl) {
        try { ecCacheUserAvatar(name, avatarUrl, { online: true, presence: 'online' }); } catch {}
      }
    });
  }
}

function ecRoomUserAvatarUrl(username) {
  const name = String(username || "").replace(/\s+/g, " ").trim();
  if (!name) return "";
  const selfKey = String(currentUser || "").trim().toLowerCase();
  const userKey = name.toLowerCase();
  try {
    if (selfKey && userKey === selfKey && UIState.myProfile && (UIState.myProfile.avatar_url || UIState.myProfile.avatarUrl)) {
      return String(UIState.myProfile.avatar_url || UIState.myProfile.avatarUrl || '').trim();
    }
  } catch {}
  try {
    const p = typeof ecGetPresenceForUsername === "function" ? ecGetPresenceForUsername(name) : null;
    if (p && typeof p === "object" && (p.avatar_url || p.avatarUrl)) return String(p.avatar_url || p.avatarUrl || '').trim();
  } catch {}
  try {
    if (typeof ecDomAvatarUrlForUsername === "function") return ecDomAvatarUrlForUsername(name) || "";
  } catch {}
  return "";
}

function ecRoomUserAvatarInitial(username) {
  try { if (typeof dockInitials === "function") return dockInitials(username); } catch {}
  try { if (typeof getGroupAvatarInitial === "function") return getGroupAvatarInitial(username); } catch {}
  const s = String(username || "").trim();
  return (s[0] || "?").toUpperCase();
}


function ecRoomUserRelation(username) {
  const u = String(username || "").trim();
  const key = u.toLowerCase();
  const self = String(currentUser || "").trim().toLowerCase();
  if (self && key === self) return "self";
  try {
    if (UIState.blockedSet && UIState.blockedSet.has(u)) return "blocked";
    if (UIState.blockedSet && UIState.blockedSet.has(key)) return "blocked";
  } catch {}
  try {
    if (UIState.friendSet && UIState.friendSet.has(u)) return "friend";
    if (UIState.friendSet && UIState.friendSet.has(key)) return "friend";
  } catch {}
  return "";
}

function ecBindRoomUsersRefreshButton() {
  const btn = $("btnRoomUsersRefresh");
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", () => {
    const room = String(UIState.currentRoom || "").trim();
    if (!room) {
      ecRenderRoomUsersPlaceholder("Join a room to see users", 0);
      return;
    }
    getUsersInRoom(room);
  });
}

function ecScheduleRoomRosterSelfHeal(room, reason) {
  const target = ecNormalizeRoomUsersWaitRoom(room);
  if (!target || !ecIsActiveRoom(target)) return;
  const now = Date.now();
  if (EC_ROOM_ROSTER_SELF_HEAL_LAST.room === target && (now - EC_ROOM_ROSTER_SELF_HEAL_LAST.at) < EC_ROOM_ROSTER_SELF_HEAL_MS) return;
  EC_ROOM_ROSTER_SELF_HEAL_LAST = { room: target, at: now };
  setTimeout(() => {
    if (!ecIsActiveRoom(target)) return;
    try {
      joinRoom(target, { silent: true, restore: true, rosterHeal: true });
    } catch (e) {
      try { getUsersInRoom(target); } catch {}
    }
  }, reason === "not_in_room" ? 150 : 450);
}

function getUsersInRoom(room = UIState.currentRoom) {
  // Server requires an explicit room name.
  room = ecNormalizeRoomUsersWaitRoom(room);
  if (!room) {
    setRoomUsersCount(0);
    const ul = $("userList");
    if (ul) {
      ul.replaceChildren();
      ul.appendChild(ecRoomSidebarEmptyRow('Join a room to see users'));
    }
    return;
  }

  ecSetRoomUsersPanelStatus(`Refreshing users in ${room}…`);
  ecSocketEmitAck("get_users_in_room", { room }, EC_ROOM_USERS_TIMEOUT_MS).then((res) => {
    if (!res) return;
    if (res.success) {
      // The server also emits room_users. Render the ack too so manual refresh
      // feels immediate and stale events cannot leave the panel blank.
      if (UIState.currentRoom === room && Array.isArray(res.users)) {
        ecRenderRoomUsersPayload(res);
      }
      return;
    }
    if (UIState.currentRoom === room) {
      const err = String(res.error || "");
      if (/not in that room/i.test(err)) {
        ecRenderRoomUsersPlaceholder('Re-syncing room membership…', 0);
        ecScheduleRoomRosterSelfHeal(room, "not_in_room");
        return;
      }
      const ul = $("userList");
      if (ul) {
        ul.replaceChildren();
        const text = res.error === "not_connected" ? 'Reconnect to refresh users' : 'Unable to refresh users';
        ul.appendChild(ecRoomSidebarEmptyRow(text));
        ecSetRoomUsersPanelStatus(text);
      }
      setRoomUsersCount(0);
    }
  });
}

// Promise helper used for room E2EE (needs a fresh member list).
function requestRoomUsers(room = UIState.currentRoom, timeoutMs = 1500) {
  return new Promise((resolve, reject) => {
    room = ecNormalizeRoomUsersWaitRoom(room);
    if (!room) return resolve([]);

    const waiter = {
      room,
      resolve,
      reject,
      timer: null,
    };

    waiter.timer = setTimeout(() => {
      ecRemoveRoomUsersWaiter(waiter);
      reject(new Error("room_users timeout"));
    }, Math.max(500, Number(timeoutMs || EC_ROOM_USERS_TIMEOUT_MS)));

    _roomUsersWaiters.push(waiter);

    ecSocketEmitAck("get_users_in_room", { room }, Math.max(500, Number(timeoutMs || EC_ROOM_USERS_TIMEOUT_MS))).then((res) => {
      if (!res || res.success) return;
      ecRemoveRoomUsersWaiter(waiter);
      reject(new Error(res.error || "room_users failed"));
    });
  });
}

function ecRenderRoomUsersPayload(payload) {
  const room = typeof payload === "object" && payload ? ecNormalizeRoomUsersWaitRoom(payload.room || "") : "";
  const rawUsers = Array.isArray(payload) ? ecNormalizeRoomUsersList(payload) : ecNormalizeRoomUsersList(payload?.users);
  const activeRoom = String(UIState.currentRoom || "").trim();
  const payloadRoom = String(room || activeRoom || "").trim();
  const selfName = String(currentUser || "").replace(/\s+/g, " ").trim();
  const selfKey = selfName.toLowerCase();
  const priorUsers = (() => {
    try { return Array.isArray(UIState.roomUsers.get(activeRoom)) ? UIState.roomUsers.get(activeRoom) : []; } catch { return []; }
  })();
  const staleSoloAfterUnblock = !!(
    activeRoom && payloadRoom === activeRoom && selfName &&
    rawUsers.length === 1 && String(rawUsers[0] || '').replace(/\s+/g, ' ').trim().toLowerCase() === selfKey &&
    priorUsers.length > 1 && Number(UIState.roomUnblockRefreshUntil || 0) > Date.now()
  );
  const provisionalSelfHeal = !!(activeRoom && payloadRoom === activeRoom && rawUsers.length === 0 && selfName);
  const users = staleSoloAfterUnblock ? priorUsers : (provisionalSelfHeal ? [selfName] : rawUsers);
  try { ecCacheRoomUsersPayloadProfiles(payload, users); } catch {}

  try {
    const cur = activeRoom;
    if (room) {
      ROOM_BROWSER.roomOccupants.set(room, rawUsers);
      ROOM_BROWSER.roomOccupantsMeta.set(room, Date.now());
    }
    if (cur && (!room || room === cur)) UIState.roomUsers.set(cur, users);
    const waiters = _roomUsersWaiters.slice();
    for (const waiter of waiters) {
      const waiterRoom = ecNormalizeRoomUsersWaitRoom(waiter?.room || "");
      // Legacy array payloads have no room and are allowed to resolve the
      // active-room waiter. Named payloads only resolve matching room requests.
      if (room && waiterRoom && waiterRoom !== room) continue;
      ecRemoveRoomUsersWaiter(waiter);
      try { waiter.resolve(users); } catch {}
    }
  } catch {}
  try {
    if (room && ROOM_BROWSER.selectedRoom === room) {
      rbRenderRoomLists();
    }
  } catch {}

  // Stale named snapshots from a previous room must update caches only; they
  // must not repaint the visible users panel after leave/switch/reconnect races.
  if (room && (!activeRoom || room !== activeRoom)) return;
  if (!room && !activeRoom) {
    ecRenderRoomUsersPlaceholder('Join a room to see users', 0);
    return;
  }

  const ul = $("userList");
  if (!ul) return;
  ul.replaceChildren();
  if (!users.length) {
    setRoomUsersCount(0);
    const payloadRoom = String(room || activeRoom || "").trim();
    if (activeRoom && payloadRoom === activeRoom) {
      ul.appendChild(ecRoomSidebarEmptyRow('Syncing users…'));
      ecSetRoomUsersPanelStatus(`Syncing users in ${activeRoom}…`);
      ecScheduleRoomRosterSelfHeal(activeRoom, "empty_roster");
      return;
    }
    ul.appendChild(ecRoomSidebarEmptyRow('No users'));
    ecSetRoomUsersPanelStatus('No users');
    return;
  }

  setRoomUsersCount(users.length);
  if (staleSoloAfterUnblock) {
    ecSetRoomUsersPanelStatus(`Re-syncing users in ${activeRoom}…`);
    ecScheduleRoomRosterSelfHeal(activeRoom, "unblock");
  } else if (provisionalSelfHeal) {
    ecSetRoomUsersPanelStatus(`Re-syncing users in ${activeRoom}…`);
    ecScheduleRoomRosterSelfHeal(activeRoom, "empty_roster");
  } else {
    ecSetRoomUsersPanelStatus(`${users.length} user${users.length === 1 ? "" : "s"} in ${room || activeRoom}`);
  }

  users.forEach(u => {
    const li = document.createElement("li");
    li.dataset.name = u;
    li.dataset.search = `${u} ${ecRoomUserRelation(u)}`.trim();
    const relation = ecRoomUserRelation(u);
    if (relation) li.classList.add(`is-${relation}`);

    const avatarUrl = ecRoomUserAvatarUrl(u);
    const left = ecRoomSidebarLeft('online', u, {
      avatarText: ecRoomUserAvatarInitial(u),
      avatarUrl
    });
    try {
      if (typeof voiceMediaIconNode === "function") left.appendChild(voiceMediaIconNode(u, room || activeRoom));
    } catch {}
    try { window.ecRefreshMessageAvatarsForUsername?.(u); } catch {}

    const actions = document.createElement("div");
    actions.className = "liActions";

    const webcamActive = (() => {
      try {
        const st = typeof voiceStatusForUser === "function" ? voiceStatusForUser(u, room || UIState.currentRoom) : null;
        return !!(st && st.webcam_on);
      } catch { return false; }
    })();
    const canViewCam = ecCanRequestUserWebcam(u, room || activeRoom);
    const camBtn = ecMakeRoomUserActionButton({
      label: "📹",
      title: webcamActive ? `Request/view ${u}'s webcam` : `${u} does not have webcam on`,
      disabled: !canViewCam,
      onClick: async () => {
        if (typeof huiRequestRemoteCamFromRoomUser === "function") {
          await huiRequestRemoteCamFromRoomUser(u, room || UIState.currentRoom);
        } else {
          toast("📷 Webcam viewing is not ready yet.", "warn");
        }
      },
    });

    const isSelf = relation === "self";
    const chatBtn = ecMakeRoomUserActionButton({
      label: "💬",
      title: isSelf ? "This is you" : `Private message ${u}`,
      disabled: isSelf,
      onClick: () => { if (!isSelf) openPrivateChat(u); },
    });

    actions.appendChild(camBtn);
    actions.appendChild(chatBtn);

    li.appendChild(left);
    li.appendChild(actions);
    li.onclick = () => selectBuddyRow(u, 'room', li);
    li.ondblclick = () => { if (!isSelf) openPrivateChat(u); };

    // Right-click context menu
    li.oncontextmenu = (ev) => {
      selectBuddyRow(u, 'room', li);
      showUserContextMenu(ev, u, { source: "room", room: room || UIState.currentRoom });
    };

    ul.appendChild(li);
  });
}

socket.on("room_users", (payload) => {
  ecRenderRoomUsersPayload(payload);
});

// ───────────────────────────────────────────────────────────────────────────────
// Room policy live state (locked/read-only/slowmode)

function getRoomPolicy(room) {
  if (!room) return null;
  return UIState.roomPolicy.get(String(room)) || null;
}

function policyLabel(policy) {
  if (!policy) return "";
  const parts = [];
  if (policy.locked) parts.push("🔒 Locked");
  if (policy.readonly) parts.push("📝 Read-only");
  const slow = Number(policy.slowmode_seconds || 0);
  if (slow > 0) parts.push(`🐢 Slowmode: ${slow}s`);
  let out = parts.join(" · ");
  if (!policy.can_send && out) out += " · You cannot post";
  if (!policy.can_send && !out) out = "You cannot post";
  return out;
}

function ensureWindowPolicyBanner(winEl) {
  if (!winEl) return null;
  const existing = winEl.querySelector('.ym-policy');
  if (existing) return existing;
  const log = winEl.querySelector('.ym-log');
  if (!log) return null;
  const div = document.createElement('div');
  div.className = 'ym-policy hidden';
  div.setAttribute('aria-live', 'polite');
  div.setAttribute('aria-atomic', 'true');
  log.parentElement.insertBefore(div, log);
  return div;
}

function applyRoomPolicyToView(room, viewEl, policy) {
  if (!room || !viewEl) return;
  const p = policy || getRoomPolicy(room);
  if (!p) {
    const b = viewEl.id === 'roomEmbed' ? $('roomEmbedPolicy') : ensureWindowPolicyBanner(viewEl);
    if (b) b.classList.add('hidden');
    return;
  }

  const banner = viewEl.id === 'roomEmbed' ? $('roomEmbedPolicy') : ensureWindowPolicyBanner(viewEl);
  if (banner) {
    const label = policyLabel(p);
    if (label) {
      banner.textContent = label;
      banner.classList.remove('hidden');
    } else {
      banner.textContent = '';
      banner.classList.add('hidden');
    }
  }

  const canSend = !!p.can_send;
  const ym = viewEl._ym || {};
  const controls = [ym.input, ym.send, ym.gifBtn, ym.emojiBtn, ym.torrentBtn, ym.fileBtn, ...(Array.isArray(ym.formatControls) ? ym.formatControls : [])].filter(Boolean);
  for (const el of controls) {
    try { el.disabled = !canSend; } catch {}
  }
  if (ym.input) {
    const reason = String(p.block_reason || '').toLowerCase();
    if (!canSend) {
      if (reason.includes('read')) ym.input.placeholder = 'Room is read-only';
      else if (reason.includes('lock')) ym.input.placeholder = 'Room is locked';
      else ym.input.placeholder = 'Posting disabled';
    } else {
      ym.input.placeholder = 'Type a message…';
    }
  }
}

function upsertRoomPolicy(room, policy) {
  if (!room || !policy) return;
  const key = String(room);
  const prev = UIState.roomPolicy.get(key) || {};
  const merged = { ...prev, ...policy, room: key };
  UIState.roomPolicy.set(key, merged);

  if (UIState.roomEmbedRoom === key) {
    const pane = $('roomEmbed');
    if (pane) applyRoomPolicyToView(key, pane, merged);
  }

  const win = UIState.windows.get('room:' + key);
  if (win) applyRoomPolicyToView(key, win, merged);

  try { renderRooms(); } catch {}
  try { if (typeof ecRoomModeratorPanelSync === 'function') ecRoomModeratorPanelSync(key); } catch {}
}

function forceLeaveRoomUI(room, why) {
  const r = String(room || '');
  if (!r) return;

  if (UIState.roomEmbedRoom === r) {
    try {
      if (typeof roomMediaStopLocalPlayback === 'function') {
        roomMediaStopLocalPlayback(r, { hideRail: true, heartbeat: true });
      }
    } catch {}
    try { showRoomEmbed(null); } catch {}
    UIState.roomEmbedRoom = null;
  }

  try {
    const id = 'room:' + r;
    if (UIState.windows.has(id)) closeWindow(id);
  } catch {}

  if (UIState.currentRoom === r) {
    // If we were in room voice, leave it as well.
    try {
      if (typeof ecMediaIsConnectedToRoom === "function" && ecMediaIsConnectedToRoom(r)) {
        ecMediaLeave("Removed", { silent: true });
      } else if (VOICE_STATE?.room?.joined && VOICE_STATE.room.name === r) {
        voiceLeaveRoom("Removed", true);
      }
    } catch (e) {} 
    UIState.currentRoom = null;
    // Clear restore targets if we were removed from the active room.
    try {
      sessionStorage.removeItem("hui_last_room");
      sessionStorage.removeItem("hui_last_room_set_at");
      sessionStorage.removeItem("hui_voice_room");
      sessionStorage.removeItem("hui_voice_room_joined");
    } catch (e) {}
    const roomToJoin = $('roomToJoin');
    if (roomToJoin) roomToJoin.value = '';
    const ul = $('userList');
    if (ul) ecClearNode(ul);
    setRoomUsersCount(0);
  }

  toast(`🚫 Removed from ${r}${why ? `: ${why}` : ''}`, 'warn', 5200);
}


const EC_ROOM_TYPING_TIMEOUT_MS = 6500;
const EC_ROOM_TYPING_SEND_THROTTLE_MS = 2200;
const EC_ROOM_TYPING_STATE = new Map();

function ecRoomTypingIndicatorsEnabled() {
  const cfg = window.HUI_CFG || {};
  return (typeof ecConfigBool === 'function') ? ecConfigBool(cfg.enable_room_typing_indicators, false) : cfg.enable_room_typing_indicators === true;
}

function ecRoomTypingKey(room, username) {
  return `${String(room || '').trim()}\x1f${String(username || '').trim().toLowerCase()}`;
}

function ecRoomTypingViews(room) {
  const views = [];
  const r = String(room || '').trim();
  if (!r) return views;
  const embed = getActiveRoomView(r);
  if (embed) views.push(embed);
  const win = UIState.windows?.get?.('room:' + r);
  if (win && !views.includes(win)) views.push(win);
  return views;
}

function ecEnsureRoomTypingIndicator(viewEl) {
  if (!viewEl?._ym?.log) return null;
  if (viewEl._ym.typingIndicator && viewEl._ym.typingIndicator.isConnected) return viewEl._ym.typingIndicator;
  const node = document.createElement('div');
  node.className = 'ecRoomTypingIndicator hidden';
  node.setAttribute('aria-live', 'polite');
  node.textContent = '';
  const log = viewEl._ym.log;
  const parent = log.parentElement || viewEl;
  try { parent.insertBefore(node, log.nextSibling); }
  catch { parent.appendChild(node); }
  viewEl._ym.typingIndicator = node;
  return node;
}

function ecFormatRoomTypingUsers(users) {
  const all = Array.from(users || []).filter(Boolean);
  const names = all.slice(0, 3);
  if (!names.length) return '';
  if (all.length === 1) return `${names[0]} is typing…`;
  if (all.length === 2) return `${names[0]} and ${names[1]} are typing…`;
  if (all.length === 3) return `${names[0]}, ${names[1]}, and ${names[2]} are typing…`;
  return `${names[0]}, ${names[1]}, and ${all.length - 2} others are typing…`;
}

function ecRenderRoomTyping(room) {
  if (!ecRoomTypingIndicatorsEnabled()) return;
  const r = String(room || '').trim();
  if (!r) return;
  const now = Date.now();
  const active = new Set();
  for (const [key, entry] of EC_ROOM_TYPING_STATE.entries()) {
    if (!entry || entry.room !== r) continue;
    if (entry.expiresAt <= now) {
      EC_ROOM_TYPING_STATE.delete(key);
      continue;
    }
    if (entry.username && entry.username !== String(currentUser || '').trim()) active.add(entry.username);
  }
  const label = ecFormatRoomTypingUsers(active);
  for (const view of ecRoomTypingViews(r)) {
    const node = ecEnsureRoomTypingIndicator(view);
    if (!node) continue;
    node.textContent = label;
    node.classList.toggle('hidden', !label);
  }
}

function ecSetRoomTyping(room, username, isTyping, expiresInSec) {
  if (!ecRoomTypingIndicatorsEnabled()) return;
  const r = String(room || '').trim();
  const u = String(username || '').trim();
  if (!r || !u || u === String(currentUser || '').trim()) return;
  const key = ecRoomTypingKey(r, u);
  if (isTyping) {
    const ttlMs = Math.max(1500, Math.min(15000, Number(expiresInSec || 5) * 1000 + 1000));
    const previous = EC_ROOM_TYPING_STATE.get(key);
    if (previous?.timer) clearTimeout(previous.timer);
    const timer = setTimeout(() => {
      EC_ROOM_TYPING_STATE.delete(key);
      ecRenderRoomTyping(r);
    }, ttlMs);
    EC_ROOM_TYPING_STATE.set(key, { room: r, username: u, expiresAt: Date.now() + ttlMs, timer });
  } else {
    const previous = EC_ROOM_TYPING_STATE.get(key);
    if (previous?.timer) clearTimeout(previous.timer);
    EC_ROOM_TYPING_STATE.delete(key);
  }
  ecRenderRoomTyping(r);
}

function ecClearRoomTyping(room, username) {
  ecSetRoomTyping(room, username, false, 0);
}

function ecRoomTypingEmit(eventName, room) {
  if (!ecRoomTypingIndicatorsEnabled()) return;
  const r = String(room || '').trim();
  if (!r || !socket?.connected) return;
  try { socket.emit(eventName, { room: r }, () => {}); } catch {}
}

function ecRoomTypingStop(room, input, opts = {}) {
  const r = String(room || input?._ecTypingRoom || '').trim();
  if (!r) return;
  if (input?._ecTypingStopTimer) {
    clearTimeout(input._ecTypingStopTimer);
    input._ecTypingStopTimer = null;
  }
  const wasTyping = !!input?._ecTypingActive;
  if (input) input._ecTypingActive = false;
  if (wasTyping || opts.force) ecRoomTypingEmit('stop_typing', r);
}

function ecBindRoomTypingInput(room, input) {
  if (!input || !ecRoomTypingIndicatorsEnabled()) return;
  input._ecTypingRoom = String(room || '').trim();
  if (input._ecRoomTypingBound) return;
  input._ecRoomTypingBound = true;
  input.addEventListener('input', () => {
    const r = String(input._ecTypingRoom || '').trim();
    if (!r) return;
    if (!input.value || !input.value.trim()) {
      ecRoomTypingStop(r, input, { force: true });
      return;
    }
    const now = Date.now();
    if (!input._ecTypingActive || !input._ecTypingLastSent || (now - input._ecTypingLastSent) > EC_ROOM_TYPING_SEND_THROTTLE_MS) {
      input._ecTypingActive = true;
      input._ecTypingLastSent = now;
      ecRoomTypingEmit('typing', r);
    }
    if (input._ecTypingStopTimer) clearTimeout(input._ecTypingStopTimer);
    input._ecTypingStopTimer = setTimeout(() => ecRoomTypingStop(r, input), EC_ROOM_TYPING_TIMEOUT_MS - 1500);
  });
  input.addEventListener('blur', () => ecRoomTypingStop(input._ecTypingRoom, input));
}

socket.on('room_typing', (payload) => {
  if (!payload) return;
  if (ecRoomShouldHideBlockedSender(payload.username)) {
    ecClearRoomTyping(payload.room || UIState.currentRoom, payload.username);
    return;
  }
  ecSetRoomTyping(payload.room || UIState.currentRoom, payload.username, payload.typing !== false, payload.expires_in);
});

socket.on('room_stop_typing', (payload) => {
  if (!payload) return;
  if (ecRoomShouldHideBlockedSender(payload.username)) {
    ecClearRoomTyping(payload.room || UIState.currentRoom, payload.username);
    return;
  }
  ecSetRoomTyping(payload.room || UIState.currentRoom, payload.username, false, 0);
});

function openRoomWindow(room) {
  const id = "room:" + room;
  const win = createWindow({ id, title: `Room — ${room}`, kind: "room" });
  if (!win) return;

  ecBindRoomTypingInput(room, win._ym?.input);

  // Replace the default "Send" behavior with room send_message
  win._ym.send.onclick = () => {
    const input = win._ym?.input || null;
    const sendBtn = win._ym?.send || null;
    const msg = input?.value?.trim() || '';
    if (!msg) return;

    // Slash command: /invite <username>
    if (/^\/invite(\s|$)/i.test(msg)) {
      const rest = msg.replace(/^\/invite\s*/i, "").trim();
      const raw = (rest.split(/\s+/)[0] || "").trim();
      const u = raw.replace(/^@/, "");
      if (!u) return toast("Usage: /invite <username>", "info", 6000);
      const optimistic = (typeof ecComposerBeginOptimisticSend === 'function')
        ? ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })
        : null;
      apiJson("/api/rooms/invite", { method: "POST", body: JSON.stringify({ room, invitee: u }) })
        .then(() => {
          toast(`✅ Invited ${u} to ${room}`, "ok");
          ecRoomTypingStop(room, input, { force: true });
          optimistic?.commit?.();
        })
        .catch((e) => {
          optimistic?.restore?.(e?.message || 'Invite failed');
          toast(`❌ ${e.message}`, "error");
        });
      return;
    }

    const optimistic = (typeof ecComposerBeginOptimisticSend === 'function')
      ? ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })
      : null;
    sendRoomTo(room, msg).then((res) => {
      if (res?.success) {
        ecRoomTypingStop(room, input, { force: true });
        // Don't append locally; we wait for server broadcast so we get message_id
        optimistic?.commit?.();
      } else {
        optimistic?.restore?.(res?.error || "Send failed");
        toast(`❌ ${res?.error || "Send failed"}`, "error");
      }
    }).catch((e) => {
      optimistic?.restore?.(e?.message || 'Send failed');
      console.error(e);
      toast(`❌ Send failed: ${e?.message || e}`, "error");
    });
  };

  // Add a one-time hint line
  // Server emits join notifications (e.g., "user has entered room").
  bringToFront(win);
  return win;
}


function ecRoomElementHasVisibleBox(el) {
  try {
    if (!el || !document.body?.contains(el)) return false;
    if (el.hidden || el.classList?.contains?.('hidden')) return false;
    if (el.getAttribute?.('aria-hidden') === 'true') return false;
    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (style && (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0)) return false;
    const rect = typeof el.getBoundingClientRect === 'function' ? el.getBoundingClientRect() : null;
    return !rect || (rect.width > 1 && rect.height > 1);
  } catch {
    return false;
  }
}

function ecIsRoomSurfaceActuallyReadable(view) {
  try {
    const root = document.getElementById('appRoot');
    const embed = document.getElementById('roomEmbed') || view?.el || view;
    const log = view?._ym?.log || document.getElementById('roomEmbedLog');
    if (!ecRoomElementHasVisibleBox(embed) || !ecRoomElementHasVisibleBox(log)) return false;

    // On phone/narrow layout the current room exists in the DOM even when the
    // user is looking at Rooms or Hub.  Only the Chat panel is actively readable.
    if (root?.classList?.contains?.('is-mobile-shell')) {
      if (root.getAttribute('data-mobile-panel') !== 'chat') return false;
      const activeConversation = (typeof ecTopVisibleConversationWindow === 'function') ? ecTopVisibleConversationWindow() : null;
      if (activeConversation && typeof ecIsConversationWindowActive === 'function' && ecIsConversationWindowActive(activeConversation)) return false;
    }

    // The room browser popout intentionally leaves the room pane scaled behind
    // it.  Treat that as covered/underlay, not as a room the user has read.
    const siteArea = document.getElementById('siteArea');
    if (embed.classList?.contains?.('is-underlay')) return false;
    if (siteArea?.classList?.contains?.('room-browser-overlay-open')) return false;
    if (typeof ROOM_BROWSER !== 'undefined' && ROOM_BROWSER?.popoutOpen) return false;

    return true;
  } catch {
    return false;
  }
}

function ecRoomNormalizeUserKey(username) {
  return String(username || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function ecRoomUserSetHasName(setLike, username) {
  const key = ecRoomNormalizeUserKey(username);
  if (!key || !(setLike instanceof Set)) return false;
  if (setLike.has(username) || setLike.has(key)) return true;
  for (const item of setLike.values()) {
    if (ecRoomNormalizeUserKey(item) === key) return true;
  }
  return false;
}

function ecRoomShouldHideBlockedSender(username) {
  const name = String(username || "").replace(/\s+/g, " ").trim();
  const key = ecRoomNormalizeUserKey(name);
  if (!key) return false;
  if (key === "system") return false;
  const selfKey = ecRoomNormalizeUserKey(currentUser || "");
  if (selfKey && key === selfKey) return false;
  try {
    if (typeof ecUserSetHasName === "function") return !!ecUserSetHasName(UIState?.blockedSet, name);
  } catch {}
  try {
    return ecRoomUserSetHasName(UIState?.blockedSet, name);
  } catch {}
  return false;
}

function ecRoomEnvelopeIncludesCurrentUser(cipherStr) {
  const cipher = String(cipherStr || "").trim();
  const selfKey = ecRoomNormalizeUserKey(currentUser || "");
  if (!cipher || !selfKey || typeof ROOM_ENVELOPE_PREFIX === "undefined" || !cipher.startsWith(ROOM_ENVELOPE_PREFIX)) return true;
  try {
    const env = JSON.parse(atob(cipher.slice(ROOM_ENVELOPE_PREFIX.length)));
    const keys = env && env.keys;
    if (!keys || typeof keys !== "object") return true;
    for (const name of Object.keys(keys)) {
      if (ecRoomNormalizeUserKey(name) === selfKey) return true;
    }
    return false;
  } catch {
    // Bad/corrupt envelopes should continue through the normal decrypt error path
    // instead of being silently hidden as a block-filtered message.
    return true;
  }
}

function ecPruneRoomMessagesFromBlockedUser(username) {
  const key = ecRoomNormalizeUserKey(username);
  if (!key) return 0;
  let removed = 0;
  try {
    document.querySelectorAll('.ec-msgGroup--room[data-sender-key]').forEach((group) => {
      if (ecRoomNormalizeUserKey(group?.dataset?.senderKey || '') !== key) return;
      group.remove();
      removed += 1;
    });
    document.querySelectorAll('.roomWindow, #roomEmbed').forEach((viewEl) => {
      const log = viewEl?._ym?.log || viewEl?.querySelector?.('.chatLog, .roomLog, #roomEmbedLog');
      const st = log?._ecChatUi;
      if (st && ecRoomNormalizeUserKey(st.lastGroup?.senderKey || '') === key) st.lastGroup = null;
    });
  } catch {}
  return removed;
}

function ecIsRoomMessageQuietlyVisible(room, view) {
  // Messages for the room the user is actively reading should render in the
  // transcript only. Toasts/browser popups are reserved for away/inactive room
  // attention so active chat does not stack duplicate alerts.
  if (!room || !view) return false;
  try {
    const activeRoom = String(UIState?.currentRoom || UIState?.roomEmbedRoom || '');
    const sameRoom = String(room) === activeRoom;
    const focused = (typeof ecIsWindowActivelyFocused === 'function')
      ? ecIsWindowActivelyFocused()
      : (document.visibilityState === 'visible' && document.hasFocus && document.hasFocus());
    return !!sameRoom && !!focused && ecIsRoomSurfaceActuallyReadable(view);
  } catch {
    return false;
  }
}

// When someone sends a room message:
socket.on("chat_message", async (payload) => {
  if (!payload) return;
  const room = (typeof ecNormalizeRoomNameForMatch === 'function')
    ? ecNormalizeRoomNameForMatch(payload.room || UIState.currentRoom || UIState.roomEmbedRoom)
    : String(payload.room || UIState.currentRoom || UIState.roomEmbedRoom || '').trim();
  if (!room) return;

  const incomingUsername = payload.username || payload.sender || "";
  try {
    if (incomingUsername && (payload.avatar_url || payload.avatarUrl)) {
      ecCacheUserAvatar(incomingUsername, payload.avatar_url || payload.avatarUrl, { online: true, presence: 'online' });
    }
  } catch {}
  if (ecRoomShouldHideBlockedSender(incomingUsername)) {
    ecClearRoomTyping(room, incomingUsername);
    return;
  }

  // If the server intentionally omitted this client from a room envelope
  // (blocked pair / non-recipient), do not render a scary "Encrypted message"
  // placeholder.  From the user's perspective, blocked-user traffic should be
  // invisible rather than unreadable.
  if (payload.cipher && typeof payload.cipher === "string" && payload.cipher.startsWith(ROOM_ENVELOPE_PREFIX)) {
    const selfKey = ecRoomNormalizeUserKey(currentUser || "");
    const senderKey = ecRoomNormalizeUserKey(incomingUsername || "");
    if (selfKey && senderKey && selfKey !== senderKey && !ecRoomEnvelopeIncludesCurrentUser(payload.cipher)) {
      ecClearRoomTyping(room, incomingUsername);
      return;
    }
  }

  // If this is an encrypted room envelope, try to decrypt for display.
  let msgForUi = payload.message;
  if (payload.cipher && typeof payload.cipher === "string" && payload.cipher.startsWith(ROOM_ENVELOPE_PREFIX)) {
    if (HAS_WEBCRYPTO && window.myPrivateCryptoKey) {
      try {
        msgForUi = await decryptRoomEnvelope(window.myPrivateCryptoKey, payload.cipher);
      } catch (e) {
        console.error(e);
        msgForUi = "🔒 Encrypted message";
      }
    } else {
      msgForUi = "🔒 Encrypted message (unlock to read)";
    }
    payload = { ...payload, message: msgForUi, encrypted: true };
  }

  const view = getActiveRoomView(room);
  if (!view) {
    try { console.warn('[Hui Chat] chat_message had no active room view', { room, currentRoom: UIState.currentRoom, roomEmbedRoom: UIState.roomEmbedRoom }); } catch {}
    try { if (room && room !== UIState.currentRoom) { rbBumpUnread(room); rbRenderRoomLists(); } } catch {}
    return;
  }

  appendRoomMessage(view, { ...payload, room });

  const username = payload.username;
  if (username) ecClearRoomTyping(room, username);
  const message = payload.message;
  const quietActiveRoomMessage = ecIsRoomMessageQuietlyVisible(room, view);
  try {
    if (room && username && username !== currentUser) {
      if (quietActiveRoomMessage && room === UIState.currentRoom) rbClearUnread(room);
      else { rbBumpUnread(room); rbRenderRoomLists(); }
    } else if (room && room === UIState.currentRoom && quietActiveRoomMessage) {
      rbClearUnread(room);
    }
  } catch {}
  // If the user is already focused on this room, the transcript itself is the
  // notification. Avoid duplicate in-app toasts and OS popups for active chat.
  if (username && username !== currentUser && !quietActiveRoomMessage) {
    toast(`💬 ${username} in ${room}`, "info", 3500, {
      event: "room_message",
      dedupeKey: `room-msg:${room}:${username}`,
      dedupeMs: 2500,
    });
    maybeBrowserNotify("Room message", `${username}: ${message}`, {
      dedupeKey: `room-msg:${room}:${username}:${message}`,
      dedupeMs: 5000,
    });
  }
});

// Server-side expiry notice for live-only room messages.
socket.on("room_messages_expired", (payload) => {
  const room = payload?.room || UIState.currentRoom;
  const ids = Array.isArray(payload?.message_ids) ? payload.message_ids : [];
  if (!room || !ids.length) return;
  const view = getActiveRoomView(room);
  if (!view || typeof _removeRoomMessage !== "function") return;
  ids.forEach((id) => _removeRoomMessage(view, String(id || ""), payload?.reason || "expired"));
});

// Reaction count updates for a message
socket.on("message_reactions", (payload) => {
  const room = payload?.room || UIState.currentRoom;
  const messageId = payload?.message_id || payload?.messageId || payload?.id;
  const counts = payload?.counts || {};
  if (!room || !messageId) return;

  const view = getActiveRoomView(room);
  if (!view) return;
  if (typeof _storeRoomReactionCounts === "function") _storeRoomReactionCounts(view, messageId, counts);
  const msgEl = _findMsgEl(view, messageId);
  if (!msgEl) return;
  const rx = msgEl.querySelector(".msgReactions");
  _renderReactionPills(rx, (typeof _getRoomReactionCounts === "function") ? _getRoomReactionCounts(view, messageId) : counts);

  // Same signed-in user in another tab should lock to the final reaction too.
  if (payload?.reacted_by && String(payload.reacted_by) === String(currentUser || "")) {
    const safeCounts = (typeof _getRoomReactionCounts === "function") ? _getRoomReactionCounts(view, messageId) : counts;
    const reactedEmoji = Object.keys(safeCounts || {}).find((emoji) => Number(safeCounts[emoji] || 0) > 0);
    _setMyReaction(view, messageId, reactedEmoji || true);
    _lockReactions(view, messageId);
  }
});

socket.on("room_message_pinned", (payload) => {
  const room = payload?.room || UIState.currentRoom;
  const messageId = payload?.message_id || payload?.messageId || payload?.id;
  if (!room || !messageId) return;
  const view = getActiveRoomView(room);
  if (!view) return;
  _setRoomPinnedMessage(view, messageId, payload);
  if (payload?.pinned_by && String(payload.pinned_by) !== String(currentUser || "")) {
    toast(`📌 ${payload.pinned_by} pinned a message in ${room}`, "info", 3500, {
      event: "room_message_pin",
      dedupeKey: `room-pin:${room}:${messageId}`
    });
  }
});

socket.on("room_message_unpinned", (payload) => {
  const room = payload?.room || UIState.currentRoom;
  const messageId = payload?.message_id || payload?.messageId || payload?.id;
  if (!room || !messageId) return;
  const view = getActiveRoomView(room);
  if (!view) return;
  _clearRoomPinnedMessage(view, messageId);
  if (payload?.unpinned_by && String(payload.unpinned_by) !== String(currentUser || "")) {
    toast(`📌 ${payload.unpinned_by} unpinned a message in ${room}`, "info", 3500, {
      event: "room_message_pin",
      dedupeKey: `room-unpin:${room}:${messageId}`
    });
  }
});

// Room notifications (join/leave messages)
const EC_SOCKET_NOTIFICATION_DEDUPE_MS = 5000;
const EC_SOCKET_NOTIFICATION_HISTORY = new Map();
const EC_DEBUG_NOTIFICATION_PATTERNS = [
  /\bis typing\.\.\.$/i,
  /\bstopped typing$/i,
  /\busage stats retrieved\b/i,
  /\baudit logs fetched\b/i,
  /\bserver settings refreshed\b/i,
  /\blisting files in room\b/i,
  /\bactive polls retrieved\b/i,
  /\brequested room navigation shortcuts\b/i,
  /\bvoted in poll\b/i,
  /\bpinned message\b/i,
  /\bunpinned message\b/i,
  /\bedited message\b/i,
  /\bdeleted message\b/i,
  /\bhighlighted message\b/i,
];
function isDebugSocketNotification(msg) {
  const text = String(msg || "").trim();
  if (!text) return false;
  return EC_DEBUG_NOTIFICATION_PATTERNS.some((pattern) => pattern.test(text));
}

function ecSocketNotificationKey(room, message) {
  const normalizer = (typeof ecNormalizeNotificationText === "function")
    ? ecNormalizeNotificationText
    : (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  return `${normalizer(room || "global")}:${normalizer(message)}`;
}


function ecIsRoomPresenceNotification(room, message, kind = "") {
  if (String(kind || "").toLowerCase() === "room_presence") return true;
  const r = (typeof ecNormalizeNotificationText === "function")
    ? ecNormalizeNotificationText(room || UIState?.currentRoom || "")
    : String(room || UIState?.currentRoom || "").replace(/\s+/g, " ").trim().toLowerCase();
  const msg = (typeof ecNormalizeNotificationText === "function")
    ? ecNormalizeNotificationText(message || "")
    : String(message || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (!msg) return false;
  if (r) {
    const escapedRoom = r.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const roomPresencePattern = new RegExp(`^.+\\s+(has\\s+)?(entered|left)\\s+${escapedRoom}\\.?$`, "i");
    if (roomPresencePattern.test(msg)) return true;
  }
  return /^.+\s+(has\s+)?(entered|left)\s+.+\.?$/i.test(msg);
}

function ecShouldSuppressSocketNotification(room, message) {
  if (!message) return true;
  if (isDebugSocketNotification(message)) return true;
  if (typeof ecIsSelfRoomPresenceNotification === "function" && ecIsSelfRoomPresenceNotification(room, message)) return true;

  const key = ecSocketNotificationKey(room, message);
  if (typeof ecShouldAllowRecentNotification === "function") {
    return !ecShouldAllowRecentNotification(EC_SOCKET_NOTIFICATION_HISTORY, key, EC_SOCKET_NOTIFICATION_DEDUPE_MS);
  }

  const now = Date.now();
  const previous = Number(EC_SOCKET_NOTIFICATION_HISTORY.get(key) || 0);
  if (previous && (now - previous) < EC_SOCKET_NOTIFICATION_DEDUPE_MS) return true;
  EC_SOCKET_NOTIFICATION_HISTORY.set(key, now);
  return false;
}

socket.on("notification", (payload) => {
  // payload can be {room, message} or sometimes string
  if (typeof payload === "string") {
    const msg = (payload || "").trim();
    if (ecShouldSuppressSocketNotification(null, msg)) return;
    toast(msg || payload, "info", 3500, { event: "room_join", dedupeKey: `socket:${ecSocketNotificationKey(null, msg)}` });
    return;
  }
  const room = payload?.room || null;
  const message = payload?.message || "";
  const kind = payload?.kind || payload?.type || "";
  if (ecShouldSuppressSocketNotification(room, message)) return;

  const isRoomPresence = ecIsRoomPresenceNotification(room, message, kind);
  if (!isRoomPresence) {
    toast(message, "info", 3500, { event: "room_join", dedupeKey: `socket:${ecSocketNotificationKey(room, message)}` });
  }

  if (room && UIState.currentRoom === room) {
    const view = getActiveRoomView(room);
    if (view && message) appendLine(view, "System:", message);
    if (isRoomPresence && message) {
      try { playUiSound("info", { event: "room_join" }); } catch {}
    }
    // Keep the right-dock "Users in current room" list fresh.
    // Server does not push roster updates except on request.
    getUsersInRoom(room);
  }
});

// ───────────────────────────────────────────────────────────────────────────────

try {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ecBindRoomUsersRefreshButton, { once: true });
  } else {
    ecBindRoomUsersRefreshButton();
  }
} catch {}

// ───────────────────────────────────────────────────────────────────────────────
// Room navigation shortcuts (F078)
// Ctrl+Alt avoids normal text entry and browser back/forward shortcuts.
const EC_ROOM_NAV_SHORTCUTS = Object.freeze({
  focusRoomList: 'Ctrl+Alt+R',
  focusMessage: 'Ctrl+Alt+M',
  focusUsers: 'Ctrl+Alt+U',
  previousRoom: 'Ctrl+Alt+ArrowUp',
  nextRoom: 'Ctrl+Alt+ArrowDown',
});

function ecRoomNavEditableTarget(target) {
  try {
    const el = target;
    if (!el) return false;
    const tag = String(el.tagName || '').toLowerCase();
    return !!(el.isContentEditable || tag === 'input' || tag === 'textarea' || tag === 'select');
  } catch { return false; }
}

function ecRoomNavShortcutMatch(ev, key) {
  if (!ev || !ev.ctrlKey || !ev.altKey || ev.metaKey) return false;
  return String(ev.key || '').toLowerCase() === String(key || '').toLowerCase();
}

function ecRoomNavVisibleRoomNames() {
  const names = [];
  try {
    const listEl = $('roomList');
    if (listEl) {
      listEl.querySelectorAll('li[data-name]').forEach((li) => {
        const name = String(li.dataset?.name || '').trim();
        if (!name || name === 'none') return;
        const style = window.getComputedStyle ? window.getComputedStyle(li) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden')) return;
        if (!names.includes(name)) names.push(name);
      });
    }
  } catch {}
  if (!names.length && Array.isArray(UIState?.roomsCache)) {
    UIState.roomsCache.forEach((row) => {
      const name = String((row && (row.name || row.room_id)) || row || '').trim();
      if (name && !names.includes(name)) names.push(name);
    });
  }
  return names;
}

function ecRoomNavFocusRoomList() {
  const search = $('rbRoomSearch');
  if (search && typeof search.focus === 'function') {
    try { search.focus({ preventScroll: false }); search.select?.(); } catch { search.focus(); }
    toast('Room search focused', 'info', 1800);
    return true;
  }
  const listEl = $('roomList');
  const first = listEl?.querySelector?.('li[data-name]:not([data-name="none"])');
  if (first) {
    try { first.tabIndex = first.tabIndex >= 0 ? first.tabIndex : 0; first.focus({ preventScroll: false }); } catch { first.focus?.(); }
    toast('Room list focused', 'info', 1800);
    return true;
  }
  return false;
}

function ecRoomNavFocusMessageInput() {
  const room = String(UIState?.currentRoom || UIState?.roomEmbedRoom || '').trim();
  const view = room && typeof getActiveRoomView === 'function' ? getActiveRoomView(room) : null;
  const input = view?._ym?.input || $('msgInput');
  if (input && typeof input.focus === 'function') {
    try { input.focus({ preventScroll: false }); } catch { input.focus(); }
    toast('Room message box focused', 'info', 1800);
    return true;
  }
  toast('Join a room first, then use the message shortcut.', 'warn', 2600);
  return false;
}

function ecRoomNavFocusUsersPanel() {
  const users = $('userList');
  if (users && typeof users.focus === 'function') {
    try { users.tabIndex = users.tabIndex >= 0 ? users.tabIndex : 0; users.focus({ preventScroll: false }); } catch { users.focus(); }
    toast('Room users focused', 'info', 1800);
    return true;
  }
  return false;
}

function ecRoomNavJoinOffset(delta) {
  const names = ecRoomNavVisibleRoomNames();
  if (!names.length) {
    toast('No visible rooms to switch to.', 'warn', 2400);
    return false;
  }
  const active = String(UIState?.currentRoom || UIState?.roomEmbedRoom || '').trim();
  let idx = active ? names.indexOf(active) : -1;
  if (idx < 0) idx = delta > 0 ? -1 : 0;
  const next = names[(idx + delta + names.length) % names.length];
  if (!next) return false;
  joinRoom(next, { silent: false });
  toast(`Switching to ${next}`, 'info', 1800);
  return true;
}

function ecHandleRoomNavigationShortcut(ev) {
  if (!ev || !ev.ctrlKey || !ev.altKey || ev.metaKey) return;
  // Let browser/page editing shortcuts work when a modal text field is active,
  // except for our explicit room-message focus shortcut.
  const editable = ecRoomNavEditableTarget(ev.target);
  const key = String(ev.key || '');
  let handled = false;
  if (ecRoomNavShortcutMatch(ev, 'r') && !editable) handled = ecRoomNavFocusRoomList();
  else if (ecRoomNavShortcutMatch(ev, 'm')) handled = ecRoomNavFocusMessageInput();
  else if (ecRoomNavShortcutMatch(ev, 'u') && !editable) handled = ecRoomNavFocusUsersPanel();
  else if (ecRoomNavShortcutMatch(ev, 'ArrowUp') && !editable) handled = ecRoomNavJoinOffset(-1);
  else if (ecRoomNavShortcutMatch(ev, 'ArrowDown') && !editable) handled = ecRoomNavJoinOffset(1);
  if (handled) {
    try { ev.preventDefault(); ev.stopPropagation(); } catch {}
  }
}

function ecBindRoomNavigationShortcuts() {
  if (window.__ecRoomNavigationShortcutsBound) return;
  window.__ecRoomNavigationShortcutsBound = true;
  document.addEventListener('keydown', ecHandleRoomNavigationShortcut, true);
  try {
    if (socket && typeof socket.emit === 'function') {
      socket.emit('room_navigation_shortcuts', { room: String(UIState?.currentRoom || '') }, () => {});
    }
  } catch {}
}

try {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ecBindRoomNavigationShortcuts, { once: true });
  } else {
    ecBindRoomNavigationShortcuts();
  }
} catch {}
