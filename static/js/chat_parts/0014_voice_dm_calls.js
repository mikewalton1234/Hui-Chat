const VOICE_DM_INVITE_TIMEOUT_MS = 85000;
const VOICE_DM_CONNECT_TIMEOUT_MS = 45000;
const VOICE_DM_ICE_QUEUE_LIMIT = 64;

function voiceDmClearTimer(call) {
  try {
    if (call && call._timeout) clearTimeout(call._timeout);
  } catch {}
  if (call) call._timeout = null;
}

function voiceDmScheduleTimeout(peer, call, phase = "connect") {
  if (!call) return;
  voiceDmClearTimer(call);
  const ms = phase === "incoming" || phase === "outgoing" ? VOICE_DM_INVITE_TIMEOUT_MS : VOICE_DM_CONNECT_TIMEOUT_MS;
  call._timeout = setTimeout(() => {
    const current = VOICE_STATE.dmCalls.get(peer);
    if (!current || current.call_id !== call.call_id || current.state === "active" || current.ending) return;
    const reason = phase === "incoming" ? "Missed call" : (phase === "outgoing" ? "No answer" : "Connection timed out");
    try {
      const evt = phase === "incoming" ? "voice_dm_decline" : "voice_dm_end";
      socket.emit(evt, { to: peer, call_id: call.call_id, reason }, () => {});
    } catch {}
    voiceDmCleanup(peer, reason);
    if (phase === "outgoing") toast(`🎤 No answer from ${peer}`, "warn");
  }, ms);
}

function voiceDmQueueIce(call, candidate) {
  if (!call || !candidate) return;
  if (!Array.isArray(call.pendingIce)) call.pendingIce = [];
  if (call.pendingIce.length >= VOICE_DM_ICE_QUEUE_LIMIT) call.pendingIce.shift();
  call.pendingIce.push(candidate);
}

async function voiceDmFlushIce(call) {
  if (!call || !call.pc || !call.pc.remoteDescription || !call.pc.remoteDescription.type) return;
  const queued = Array.isArray(call.pendingIce) ? call.pendingIce.splice(0) : [];
  for (const cand of queued) {
    try { await call.pc.addIceCandidate(new RTCIceCandidate(cand)); } catch (e) { console.warn("DM ICE candidate rejected", e); }
  }
}

function voiceDmSetConnected(peer, call, muteLabel) {
  if (!call || call.ending) return;
  call.state = "active";
  voiceDmClearTimer(call);
  voiceDmUi(peer, { statusText: "Connected", mode: "active", muteLabel: muteLabel || (VOICE_STATE.micMuted ? "Unmute" : "Mute") });
  voiceApplyTalkMode({ silent: true });
}

function voiceDmWirePeerConnection(peer, call) {
  if (!call || !call.pc) return;
  const pc = call.pc;
  pc._huiIceRestartOffer = async () => {
    const current = VOICE_STATE.dmCalls.get(peer);
    if (!current || current.call_id !== call.call_id || call.ending || !call.pc) return false;
    if (pc.signalingState !== "stable") return false;
    pc._huiMakingOffer = true;
    try {
      const offer = await pc.createOffer({ offerToReceiveAudio: true, iceRestart: true });
      await pc.setLocalDescription(offer);
      const ack = await new Promise((resolve) => socket.emit("voice_dm_offer", { to: peer, call_id: call.call_id, offer: pc.localDescription, ice_restart: true }, resolve));
      return !!(ack && ack.success);
    } finally {
      pc._huiMakingOffer = false;
    }
  };
  pc.ontrack = (ev) => {
    const st = ev.streams && ev.streams[0];
    if (st) call.remoteEl = voiceAttachRemoteAudio(`dm-${peer}`, st);
  };
  pc.onicecandidate = (ev) => {
    if (ev.candidate) socket.emit("voice_dm_ice", { to: peer, call_id: call.call_id, candidate: ev.candidate });
  };
  pc.onconnectionstatechange = () => {
    const current = VOICE_STATE.dmCalls.get(peer);
    if (!current || current.call_id !== call.call_id || call.ending) return;
    const state = String(pc.connectionState || "");
    if (state === "connected") {
      voiceDmSetConnected(peer, call);
    } else if (state === "disconnected") {
      voiceDmUi(peer, { statusText: "Reconnecting…", mode: "active" });
    } else if (state === "failed") {
      voiceDmUi(peer, { statusText: "Reconnecting…", mode: "active" });
      voiceTryIceRestart(pc).then((ok) => { if (!ok) voiceHangupDm(peer, "Connection failed", true); }).catch(() => voiceHangupDm(peer, "Connection failed", true));
    }
  };
  pc.oniceconnectionstatechange = () => {
    const current = VOICE_STATE.dmCalls.get(peer);
    if (!current || current.call_id !== call.call_id || call.ending) return;
    if (pc.iceConnectionState === "failed") {
      voiceDmUi(peer, { statusText: "Reconnecting…", mode: "active" });
      voiceTryIceRestart(pc).then((ok) => { if (!ok) voiceHangupDm(peer, "Connection failed", true); }).catch(() => voiceHangupDm(peer, "Connection failed", true));
    }
  };
}

function voiceDmCleanup(peer, reason = "") {
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call) {
    voiceDmUi(peer, { statusText: reason ? `Ended: ${reason}` : "Not connected", mode: "idle", hideBar: false });
    try { voiceUpdateDmVoiceButton(peer); } catch (e) {}
    return;
  }
  call.ending = true;
  voiceDmClearTimer(call);
  try { voiceStopAutoQualityMonitor(`dm-${peer}`); } catch {}
  try { call.pc?.close(); } catch {}
  try {
    if (call.remoteEl) {
      call.remoteEl.srcObject = null;
      call.remoteEl.remove();
    }
  } catch {}
  call.pendingIce = [];
  VOICE_STATE.dmCalls.delete(peer);
  voiceDmUi(peer, { statusText: reason ? `Ended: ${reason}` : "Not connected", mode: "idle", hideBar: false });
  try { voiceUpdateDmVoiceButton(peer); } catch (e) {}
  voiceMaybeStopMic();
}

async function voiceStartDmCall(peer) {
  peer = String(peer || "").trim();
  if (!peer) return;
  if (typeof voiceActionBusy === "function" && voiceActionBusy("dm", peer)) return;
  if (typeof voiceWithBusy === "function") return voiceWithBusy("dm", peer, async () => voiceStartDmCallUnlocked(peer));
  return voiceStartDmCallUnlocked(peer);
}

async function voiceStartDmCallUnlocked(peer) {
  if (!VOICE_ENABLED) return toast("🎤 Voice is disabled on this server", "warn");
  // Ensure DM window exists
  openPrivateChat(peer);

  if (VOICE_STATE.dmCalls.has(peer)) {
    return toast("🎤 Voice call already active", "warn");
  }

  const call_id = crypto?.randomUUID ? crypto.randomUUID() : (Date.now() + "-" + Math.random());
  const call = { call_id, peer, pc: null, remoteEl: null, state: "calling", muted: false, isCaller: true, pendingIce: [], ending: false, _timeout: null };
  VOICE_STATE.dmCalls.set(peer, call);
  voiceSetMute(false);
  voiceDmUi(peer, { statusText: "Calling…", mode: "calling" });
  voiceDmScheduleTimeout(peer, call, "outgoing");
  try { voiceUpdateDmVoiceButton(peer); } catch (e) {}

  try {
    await voiceEnsureMic();
    const pc = voiceMakePc();
    call.pc = pc;
    const stream = VOICE_STATE.micStream;
    stream.getTracks().forEach(t => pc.addTrack(t, stream));
    voiceApplySenderQuality(pc);
    voiceStartAutoQualityMonitor(`dm-${peer}`, pc);
    voiceApplyTalkMode({ silent: true });
    voiceDmWirePeerConnection(peer, call);

    // Send invite first (lets receiver accept/decline)
    const inv = await new Promise((resolve) => socket.emit("voice_dm_invite", { to: peer, call_id }, resolve));
    if (!inv?.success || !inv?.delivered) {
      const msg = inv?.error || "User realtime connection unavailable. Ask them to refresh and try again.";
      voiceDmCleanup(peer, msg);
      if (inv?.voice_realtime) console.warn("Voice invite realtime diagnostic", inv.voice_realtime);
      return toast(`❌ ${msg}`, "error");
    }

    // Wait for accept event to start offer (see socket.on handlers)
  } catch (e) {
    console.error(e);
    voiceDmCleanup(peer, e?.message || "Voice call failed");
    toast(`❌ Voice call failed: ${e?.message || e}`, "error");
  }
}

async function voiceAcceptDmCall(peer) {
  peer = String(peer || "").trim();
  if (!peer) return;
  if (typeof voiceActionBusy === "function" && voiceActionBusy("dm", peer)) return;
  if (typeof voiceWithBusy === "function") return voiceWithBusy("dm", peer, async () => voiceAcceptDmCallUnlocked(peer));
  return voiceAcceptDmCallUnlocked(peer);
}

async function voiceAcceptDmCallUnlocked(peer) {
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call || call.state !== "incoming") return;
  try {
    voiceSetMute(false);
    call.state = "connecting";
    voiceDmUi(peer, { statusText: "Connecting…", mode: "active", muteLabel: "Mute" });
    voiceDmScheduleTimeout(peer, call, "connect");
    try { voiceUpdateDmVoiceButton(peer); } catch (e) {}
    await voiceEnsureMic();
    const pc = voiceMakePc();
    call.pc = pc;
    const stream = VOICE_STATE.micStream;
    stream.getTracks().forEach(t => pc.addTrack(t, stream));
    voiceApplySenderQuality(pc);
    voiceStartAutoQualityMonitor(`dm-${peer}`, pc);
    voiceApplyTalkMode({ silent: true });
    voiceDmWirePeerConnection(peer, call);

    const ack = await new Promise((resolve) => socket.emit("voice_dm_accept", { to: peer, call_id: call.call_id }, resolve));
    if (!ack?.success || !ack?.delivered) {
      const msg = ack?.error || "Caller realtime connection unavailable. Ask them to refresh and try again.";
      voiceDmCleanup(peer, msg);
      if (ack?.voice_realtime) console.warn("Voice accept realtime diagnostic", ack.voice_realtime);
      toast(`❌ ${msg}`, "error");
      return;
    }
    voiceDmUi(peer, { statusText: "Connecting…", mode: "active", muteLabel: "Mute" });
    voiceApplyTalkMode({ silent: true });
  } catch (e) {
    console.error(e);
    try { socket.emit("voice_dm_decline", { to: peer, call_id: call.call_id, reason: "Accept failed" }, () => {}); } catch {}
    voiceDmCleanup(peer, e?.message || "Accept failed");
    toast(`❌ Voice accept failed: ${e?.message || e}`, "error");
  }
}

function voiceDeclineDmCall(peer, reason = "Declined") {
  peer = String(peer || "").trim();
  if (!peer) return;
  const run = () => {
    const call = VOICE_STATE.dmCalls.get(peer);
    if (!call) return;
    call.ending = true;
    socket.emit("voice_dm_decline", { to: peer, call_id: call.call_id, reason }, () => {});
    voiceDmCleanup(peer, reason);
  };
  if (typeof voiceActionBusy === "function" && voiceActionBusy("dm", peer)) return;
  if (typeof voiceSetActionBusy === "function") {
    voiceSetActionBusy("dm", peer, true);
    try { run(); } finally { setTimeout(() => voiceSetActionBusy("dm", peer, false), 250); }
    return;
  }
  run();
}

async function voiceToggleDmMain(peer) {
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call) {
    voiceSetMute(false);
    return await voiceStartDmCall(peer);
  }
  if (call.state === "incoming") {
    voiceSetMute(false);
    return await voiceAcceptDmCall(peer);
  }
  return voiceHangupDm(peer, "Ended", true);
}

function voiceHangupDm(peer, reason = "Ended", notifyPeer = true) {
  peer = String(peer || "").trim();
  if (!peer) return;
  const run = () => {
    const call = VOICE_STATE.dmCalls.get(peer);
    if (!call) return;
    call.ending = true;
    if (notifyPeer) socket.emit("voice_dm_end", { to: peer, call_id: call.call_id, reason }, () => {});
    voiceDmCleanup(peer, reason);
  };
  if (typeof voiceActionBusy === "function" && voiceActionBusy("dm", peer)) return;
  if (typeof voiceSetActionBusy === "function") {
    voiceSetActionBusy("dm", peer, true);
    try { run(); } finally { setTimeout(() => voiceSetActionBusy("dm", peer, false), 250); }
    return;
  }
  run();
}

function voiceToggleMuteDm(peer) {
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call) return;
  const muted = !VOICE_STATE.micMuted;
  voiceSetMute(muted);
  call.muted = muted;
  voiceDmUi(peer, { muteLabel: muted ? "Unmute" : "Mute" });
  try { voiceUpdateDmVoiceButton(peer); } catch (e) {}
}

async function voiceJoinRoom(room, opts) {
  opts = opts || {};
  const silent = !!opts.silent;
  const restore = !!opts.restore;
  const viewerOnly = !!opts.viewerOnly;
  const needAudio = opts.audio !== false;
  const voiceDesired = !!(needAudio && !viewerOnly);

  if (!VOICE_ENABLED) {
    if (!silent && !restore) toast("🎤 Voice is disabled on this server", "warn");
    return { success: false, error: "voice_disabled" };
  }
  if (!room) {
    if (!silent && !restore) toast("⚠️ Join a room first", "warn");
    return { success: false, error: "missing_room" };
  }
  if (VOICE_STATE.room.joined && VOICE_STATE.room.name === room) {
    if (voiceDesired) {
      // Upgrade an existing webcam/view-only signaling join into real voice.
      if (!VOICE_STATE.micStream) await voiceEnsureMic();
      VOICE_STATE.room.wantRoomVoice = true;
      VOICE_STATE.room.viewerOnly = false;
      try {
        VOICE_STATE.room.peers.forEach((obj) => {
          if (!obj || !obj.pc || !VOICE_STATE.micStream) return;
          const hasAudio = obj.pc.getSenders && obj.pc.getSenders().some(s => s && s.track && s.track.kind === "audio");
          if (!hasAudio) VOICE_STATE.micStream.getTracks().forEach(t => obj.pc.addTrack(t, VOICE_STATE.micStream));
          voiceApplySenderQuality(obj.pc);
        });
      } catch (e) {}
      voiceSetMute(false);
      voiceApplyTalkMode({ silent: true });
      try { sessionStorage.setItem("hui_voice_desired", "1"); } catch (e) {}
      try { voiceUpdateLocalMediaStatus(room, { voice_on: true }); } catch (e) {}
    } else {
      // Webcam-only and viewer-only joins keep the signaling mesh without turning
      // on room voice or setting reconnect voice preferences.
      if (!VOICE_STATE.room.wantRoomVoice) {
        VOICE_STATE.room.viewerOnly = !!viewerOnly;
        try { sessionStorage.removeItem("hui_voice_desired"); } catch (e) {}
      }
      try { voiceUpdateLocalMediaStatus(room, { voice_on: !!VOICE_STATE.room.wantRoomVoice }); } catch (e) {}
    }
    voiceUpdateRoomVoiceButton();
    return { success: true, already: true, voice_on: !!VOICE_STATE.room.wantRoomVoice, viewer_only: !!VOICE_STATE.room.viewerOnly };
  }

  // Bug-hunt fix: joining media for a different room must first tear down the
  // previous room peer mesh and tell the server.  Otherwise the browser can keep
  // stale PeerConnections and the server can keep the user in the old voice room.
  if (VOICE_STATE.room.joined && VOICE_STATE.room.name && VOICE_STATE.room.name !== room) {
    try { voiceLeaveRoom("Switching rooms", true, { silent: true }); } catch (e) {}
  }

  try {
    if (voiceDesired) await voiceEnsureMic();
    VOICE_STATE.room.name = room;
    VOICE_STATE.room.joined = true;
    VOICE_STATE.room.wantRoomVoice = voiceDesired;
    VOICE_STATE.room.viewerOnly = !!viewerOnly;
    VOICE_STATE.room.peers.clear();
    try { VOICE_STATE.room.iceQueues.clear(); } catch (e) {}
    if (voiceDesired) {
      voiceSetMute(false);
      voiceApplyTalkMode({ silent: true });
    } else {
      // Do not mark camera-only room media as voice.  Keep any existing mic off.
      voiceSetMute(true);
    }
    if (voiceDesired) voiceRoomUi({ show: true, statusText: "Joining…", joinVisible: false, leaveVisible: false, muteVisible: false, muteLabel: "Mute" });

    const ack = await new Promise((resolve) => socket.emit("voice_room_join", { room, viewer_only: viewerOnly, audio: needAudio }, resolve));
    if (!ack?.success) {
      VOICE_STATE.room.joined = false;
      VOICE_STATE.room.name = null;
      VOICE_STATE.room.viewerOnly = false;
      const isFull = ack?.error_code === "voice_room_full" || ack?.full === true;
      if (voiceDesired) {
        voiceMaybeStopMic();
        voiceRoomUi({ show: true, statusText: isFull ? "Voice is full" : (ack?.error || "Voice join failed") });
      } else voiceRoomUi({ show: false });
      if (isFull) {
        if (!silent && !restore) voiceShowRoomFull(room, ack);
      } else if (!silent && !restore) {
        toast(`❌ ${ack?.error || "Voice join failed"}`, "error");
      }
      return { success: false, error: ack?.error || (isFull ? "voice_room_full" : "voice_join_failed"), error_code: ack?.error_code || (isFull ? "voice_room_full" : "voice_join_failed") };
    }

    // Persist for reconnect restore (per-tab).
    try {
      sessionStorage.setItem("hui_voice_room", String(room));
      sessionStorage.setItem("hui_voice_room_joined", "1");
      if (voiceDesired) sessionStorage.setItem("hui_voice_desired", "1");
      else sessionStorage.removeItem("hui_voice_desired");
    } catch (e) {}

    const roster = Array.isArray(ack.users) ? ack.users : [];
    const limN = (ack && ack.limit !== undefined && ack.limit !== null) ? Number(ack.limit) : VOICE_MAX_ROOM_PEERS;
    const limText = (Number.isFinite(limN) && limN > 0) ? String(limN) : "∞";
    if (voiceDesired) voiceRoomUi({ show: true, statusText: `Voice connected (${roster.length}/${limText})`, joinVisible: false, leaveVisible: false, muteVisible: false });
    else voiceRoomUi({ show: false });
    if (voiceDesired) voiceApplyTalkMode({ silent: true });
    voiceUpdateRoomVoiceButton();

    // Ensure peers
    for (const p of roster) {
      if (!p || p === currentUser) continue;
      voiceRoomEnsurePeer(room, p);
    }

    return { success: true, users: roster, limit: limN, voice_on: voiceDesired, viewer_only: viewerOnly };
  } catch (e) {
    console.error(e);
    if (!silent && !restore) toast(`❌ Voice room failed: ${e?.message || e}`, "error");
    voiceLeaveRoom("Error", false);
    return { success: false, error: e?.message || String(e) };
  }
}
