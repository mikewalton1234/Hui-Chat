async function restoreLastRoomAndVoice() {
  if (EC_RESTORE_IN_PROGRESS) return;
  EC_RESTORE_IN_PROGRESS = true;
  try {
    let lastRoom = "";
    try {
      lastRoom = String(UIState?.currentRoom || sessionStorage.getItem("hui_last_room") || "").trim();
    } catch (e) {
      lastRoom = String(UIState?.currentRoom || "").trim();
    }

    const voiceWanted = (() => {
      // Only restore microphone voice when the user explicitly enabled Voice.
      // Webcam-only joins may still store hui_voice_room for signaling, but
      // must not restore as voice after a reconnect.
      try { return sessionStorage.getItem("hui_voice_desired") === "1"; } catch (e) { return false; }
    })();
    const voiceRoom = (() => {
      try { return String(sessionStorage.getItem("hui_voice_room") || "").trim(); } catch (e) { return ""; }
    })();

    if (!lastRoom) return;

    const res = await joinRoom(lastRoom, { silent: true, restore: true });
    const restoredRoom = String(res?.room || lastRoom || "").trim();
    if (res?.success && restoredRoom && restoredRoom !== lastRoom) {
      try { sessionStorage.setItem("hui_last_room", restoredRoom); } catch (e) {}
    }
    if (res?.success && restoredRoom && ecMediaModeReady()) {
      try { await ecMediaSwitchRoomIfDesired(restoredRoom); } catch (e) {}
    } else if (res?.success && restoredRoom && voiceWanted && (!voiceRoom || voiceRoom === lastRoom || voiceRoom === restoredRoom)) {
      // Reset local voice state on reconnect so we re-announce to the server and rebuild peers.
      try {
        if (VOICE_STATE?.room?.joined && VOICE_STATE.room.name === restoredRoom) {
          voiceLeaveRoom("Reconnecting", false, { keepDesired: true, silent: true });
        }
      } catch (e) {}
      await voiceJoinRoom(restoredRoom, { silent: true, restore: true });
    }
  } catch (e) {
    console.warn("restoreLastRoomAndVoice failed", e);
  } finally {
    EC_RESTORE_IN_PROGRESS = false;
  }
}


function ecLiveDataBootstrapRescue(reason = '') {
  // One more pass after connect/auth recovery.  This fixes the visible symptom
  // where the socket connects but an early event/ack is dropped, leaving Rooms,
  // Friends, Groups, and invite panels empty until a manual refresh.
  window.setTimeout(() => {
    try { getRooms({ timeoutMs: 7000, reason: reason || 'bootstrap_rescue' }); } catch (e) { try { console.warn('[Hui Chat] rescue getRooms failed', e); } catch {} }
    try { getFriends({ timeoutMs: 7000, reason: reason || 'bootstrap_rescue' }); } catch (e) { try { console.warn('[Hui Chat] rescue getFriends failed', e); } catch {} }
    try { getPendingFriendRequests(); } catch (_) {}
    try { getBlockedUsers(); } catch (_) {}
    try { refreshMyGroups(); } catch (_) {}
    try { refreshGroupInvites(); } catch (_) {}
    try { refreshRoomInvites(); } catch (_) {}
    try { refreshCustomRoomInvites(); } catch (_) {}
  }, 2200);
}
window.ecLiveDataBootstrapRescue = ecLiveDataBootstrapRescue;

socket.on("connect", () => {
  EC_RECONNECT_IN_PROGRESS = false;
  EC_SERVER_DISCONNECT_RETRIES = 0;
  const first = !EC_HAS_EVER_CONNECTED;
  EC_HAS_EVER_CONNECTED = true;

  const now = Date.now();
  const doFullBootstrap = first || (now - EC_LAST_CONNECT_BOOTSTRAP_AT > 15_000);
  if (doFullBootstrap) EC_LAST_CONNECT_BOOTSTRAP_AT = now;

  if (first) {
    toast("✅ Connected", "ok");
  } else {
    const downFor = EC_DISCONNECTED_AT ? (Date.now() - EC_DISCONNECTED_AT) : 0;
    const bannerWasVisible = !!(EC_CONN_BANNER && !EC_CONN_BANNER.classList.contains("hidden"));
    if ((downFor >= EC_RECONNECT_TOAST_THRESHOLD_MS || bannerWasVisible) && Date.now() - EC_LAST_RECONNECT_TOAST_AT > 5000) {
      EC_LAST_RECONNECT_TOAST_AT = Date.now();
      toast("🔁 Reconnected", "ok", 2600);
    }
  }

  hideConnBanner();

  // Full bootstrap can be expensive (multiple HTTP fetches). Only do it on the
  // first connection, or if we haven't done one recently.
  if (doFullBootstrap) {
    getRooms();
    // Pull invite list so users see invitations even if they were offline when invited.
    refreshCustomRoomInvites();
    refreshRoomInvites();
  }

  // Live room counts for room browser badges (instant updates vs polling)
  try {
    socket.emit("get_room_counts", null, (res) => {
      try { if (res && res.counts) rbApplyRoomCounts(res.counts); } catch {}
    });
  } catch {}

  if (doFullBootstrap) {
    getFriends();
    getPendingFriendRequests();
    getBlockedUsers();
    refreshMyProfileInHub().catch(() => renderMyHubIdentity({ username: currentUser }));
  }

  // Missed (offline) PM summary on login
  MISSED_SUMMARY_TOAST_ARMED = first;
  socket.emit("get_missed_pm_summary");

  // Presence (server addition)
  ensurePresencePolling();
  requestPresenceRefresh("connect", { force: true });
  if (doFullBootstrap) {
    refreshMyGroups();
    refreshGroupInvites();
    try { ecLiveDataBootstrapRescue(first ? 'first_connect' : 'reconnect'); } catch (_) {}
  }

  // Re-join the remembered room after transient reconnects and after a fresh
  // browser reload. Without the first-connect restore the left pane could show
  // the old room while the server had no live Socket.IO room membership, which
  // made the users panel say "No users" even though the user was looking at the room.
  try {
    const lastRoom = String(sessionStorage.getItem("hui_last_room") || "").trim();
    const lastAt = Number(sessionStorage.getItem("hui_last_room_set_at") || 0) || 0;
    const isRecent = !lastAt || (Date.now() - lastAt) < 24 * 60 * 60 * 1000;
    if ((!first || (!UIState.currentRoom && lastRoom && isRecent))) restoreLastRoomAndVoice();
  } catch (e) {
    if (!first) restoreLastRoomAndVoice();
  }
});

socket.on("disconnect", (reason) => {
  // Transient disconnects happen (server restarts, Wi‑Fi blips, sleep/wake).
  // Keep the user in-app; only redirect on real auth failure or explicit logout.
  if (AUTH_RECOVERY_IN_PROGRESS) return;
  if (reason === "io client disconnect") return;

  EC_DISCONNECTED_AT = Date.now();
  const r = String(reason || "disconnect");
  setConnBannerSoon("disconnected", `🔌 Disconnected (${r}) — reconnecting…`);

  // If the *server* explicitly disconnected us, uncontrolled reconnect loops can hammer
  // the server (and flood logs). Do a single delayed attempt, then require manual retry.
  if (r === "io server disconnect") {
    EC_SERVER_DISCONNECT_RETRIES += 1;
    if (EC_SERVER_DISCONNECT_RETRIES <= 1) {
      setTimeout(() => tryReconnectNow("io_server_disconnect"), 2000);
    } else {
      EC_RECONNECT_IN_PROGRESS = false;
      setConnBannerNow("disconnected", `🔌 Disconnected (${r}) — click Retry to reconnect`, { showRetry: true });
    }
  } else {
    // For transient disconnects, Socket.IO handles exponential backoff.
    EC_RECONNECT_IN_PROGRESS = false;
  }
});

// Missed PM summary from server
socket.on("missed_pm_summary", ({ items, total, generated_at } = {}) => {
  const list = normalizeMissedPmSummaryItems(items);
  try { if (typeof ecMissedDebug === 'function') ecMissedDebug('socket.missed_pm_summary.received', { rawItems: items, total, generated_at, normalized: list }); } catch {}

  // IMPORTANT: Do not hide missed entries just because a DM window is open.
  // If private messages are not ready, auto-consuming would only "peek" and can make the UI look cleared
  // while the server still has undelivered messages (they then reappear after refresh).
  UIState.missedPmSummary = list;
  renderMissedPmList(list);
  try { ecUpdateAllOpenDmStatuses(); } catch {}
  if (!list.length) closeDockRailPanelIfEmpty('missed');


  // If the user already has a DM window open for a sender with missed messages,
  // auto-consume immediately. This fixes a race where the DM is opened before the
  // missed summary arrives, leaving the bubble stuck until manual interaction.
  try {
    for (const it of list) {
      const sender = it?.sender;
      const count = Number(it?.count ?? 0) || 0;
      if (!sender || count <= 0) continue;
      const win = UIState.windows ? ecGetPmWindow(sender) : null;
      const activelyReading = win && (typeof ecIsConversationWindowActive === 'function') && ecIsConversationWindowActive(win);
      const autoOpenedFromIncoming = !!(win && win._ym && Number(win._ym.__incomingMissedAutoDrainAt || 0) > 0);
      if (activelyReading || autoOpenedFromIncoming) {
        consumeOfflinePmsForPeer(sender, { promptUnlock: false, quiet: true })
          .finally(() => { try { ecUpdateAllOpenDmStatuses(); } catch {} });
      }
    }
  } catch {}


  const listTotal = list.reduce((acc, it) => acc + (Number(it?.count ?? 0) || 0), 0);
  const serverTotal = Number(total);
  // beta.390: do not let a stale/zero server total hide a non-empty front-end list.
  // beta.322 counted directly from the normalized list, which is the behavior users saw working.
  const summaryTotal = Math.max(0, Number.isFinite(serverTotal) ? serverTotal : 0, listTotal);
  try { if (typeof ecMissedDebug === 'function') ecMissedDebug('socket.missed_pm_summary.rendered', { listTotal, serverTotal, summaryTotal, list }); } catch {}
  try {
    if (typeof ecMaybePopupMissedPmSummary === 'function') {
      ecMaybePopupMissedPmSummary(list, { total: summaryTotal, reason: 'server_summary' });
    } else if (MISSED_SUMMARY_TOAST_ARMED && UIState.prefs.missedToast && summaryTotal > 0) {
      toast(`📨 You have ${summaryTotal} missed PM(s)`, "info", 3500, { event: "dm" });
      maybeBrowserNotify("Missed private messages", `You have ${summaryTotal} missed PM(s).`);
    }
  } catch {}
  MISSED_SUMMARY_TOAST_ARMED = false;
});

// Friend request ping
socket.on("friend_request", ({ from }) => {
  const who = String(from || '').replace(/\s+/g, ' ').trim();
  if (who && !ecUserSetHasName(UIState.blockedSet, who) && !ecUserSetHasName(UIState.friendSet, who)) {
    const existing = ecCanonicalUsernameList(UIState.pendingRequests || []);
    if (!existing.some((name) => ecNormalizeUsernameKey(name) === ecNormalizeUsernameKey(who))) {
      UIState.pendingRequests = existing.concat([who]);
      try { renderPendingFriendRequestsInto($('pendingRequestsList'), UIState.pendingRequests); } catch {}
      try { renderPendingFriendRequestsInto($('railPendingRequestsList'), UIState.pendingRequests); } catch {}
      try { updateDockSummaryCounts(); } catch {}
    }
  }
  toast(`🎉 Friend request from ${who || from}`, "info", 3500, { event: "friend_request" });
  maybeBrowserNotify("Friend request", `From: ${who || from}`);
  getPendingFriendRequests();
});

// Friend request accepted (requester side)
socket.on("friend_request_accepted", ({ by }) => {
  const who = String(by || "").trim();
  toast(`✅ ${who || "A user"} accepted your friend request`, "ok", 5000, { event: "friend_request" });
  maybeBrowserNotify("Friend request accepted", who ? `${who} accepted your request` : "Accepted");
  getPendingFriendRequests();
  getFriends();
});

// Friend request rejected (requester side)
socket.on("friend_request_rejected", ({ by }) => {
  const who = String(by || "").trim();
  toast(`ℹ️ ${who || "A user"} rejected your friend request`, "info", 4500, { event: "friend_request" });
  getPendingFriendRequests();
});

function _inviteKey(room, by, kind = "room") {
  return `invite:${String(kind || "room").toLowerCase()}:${String(room || "").toLowerCase()}:${String(by || "").toLowerCase()}`;
}
