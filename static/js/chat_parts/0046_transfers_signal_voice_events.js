// WebRTC P2P file transfer signaling (offer/answer/ICE)
// ───────────────────────────────────────────────────────────────────────────────
socket.on("p2p_file_answer", ({ sender, transfer_id, answer }) => {
  const tr = P2P_TRANSFERS.get(transfer_id);
  if (!tr || tr.role !== "sender") return;
  if (!ecSamePmPeer(sender, tr.peer)) return;
  if (tr._answerResolve) tr._answerResolve(answer);
});

socket.on("p2p_file_decline", ({ sender, transfer_id, reason }) => {
  const tr = P2P_TRANSFERS.get(transfer_id);
  if (!tr) return;
  if (!ecSamePmPeer(sender, tr.peer)) return;
  if (tr._answerReject) tr._answerReject(new Error(reason || "Declined"));
  if (tr.ui) tr.ui.setStatus("❌ Declined");
  p2pSafeClose(transfer_id);
});

socket.on("p2p_file_ice", async ({ sender, transfer_id, candidate }) => {
  if (!transfer_id || !candidate) return;
  const tr = P2P_TRANSFERS.get(transfer_id);
  if (tr && !ecSamePmPeer(sender, tr.peer)) return;
  if (!tr || !tr.pc) {
    p2pQueueIceCandidate(transfer_id, candidate);
    return;
  }
  if (!tr.pc.remoteDescription || !tr.pc.remoteDescription.type) {
    p2pQueueIceCandidate(transfer_id, candidate);
    return;
  }
  try {
    await tr.pc.addIceCandidate(new RTCIceCandidate(candidate));
  } catch (e) {
    p2pQueueIceCandidate(transfer_id, candidate);
  }
});

socket.on("p2p_file_offer", async ({ sender, transfer_id, offer, meta }) => {
  try {
    const senderName = ecPmPeerName(sender);
    if (!senderName || !transfer_id || !offer) return;
    if (!P2P_FILE_ENABLED || !window.isSecureContext) {
      try { socket.emit("p2p_file_decline", { to: sender, transfer_id, reason: "P2P disabled" }); } catch {}
      return;
    }
    // If we already have or recently closed a transfer with this id, decline dupes
    // instead of silently leaving the sender waiting on an old/stale card.
    if (P2P_TRANSFERS.has(transfer_id) || p2pTransferIdRecentlyUsed(transfer_id)) {
      try { socket.emit("p2p_file_decline", { to: sender, transfer_id, reason: "Duplicate transfer" }); } catch {}
      return;
    }

    const win = openPrivateChat(senderName) || ecGetPmWindow(senderName);
    if (!win) return;

    const ui = appendP2pTransferUI(win, `${senderName}:`, meta || {}, { mode: "incoming" });
    ui.setStatus("Incoming file offer");

    ui.onAccept(async () => {
      ui.disableActions();
      if (ui?.setBadge) ui.setBadge("P2P");
      ui.setStatus("Accepting…");

      const pc = p2pMakePc();
      let dc = null;

      const tr = {
        role: "receiver",
        peer: senderName,
        transfer_id,
        pc,
        dc: null,
        ui,
        meta: meta || {},
        recv: { expected: 0, got: 0, parts: [], gotDone: false },
        _watchdog: null,
        _watchdogInterval: null,
      };
      P2P_TRANSFERS.set(transfer_id, tr);

      const fail = (msg) => {
        try { ui.setStatus(msg || "⚠️ Transfer failed"); } catch {}
        try { socket.emit("p2p_file_decline", { to: sender, transfer_id, reason: msg || "Failed" }); } catch {}
        p2pSafeClose(transfer_id);
      };

      // ICE + state diagnostics
      pc.onicecandidate = (e) => {
        if (e.candidate) {
          p2pEmitIceCandidate(sender, transfer_id, e.candidate, tr);
        }
      };
      pc.onconnectionstatechange = () => {
        const st = pc.connectionState;
        if (st === "failed") fail("⚠️ Connection failed");
      };
      pc.oniceconnectionstatechange = () => {
        const st = pc.iceConnectionState;
        if (st === "failed") fail("⚠️ ICE failed");
      };

      pc.ondatachannel = (ev) => {
        dc = ev.channel;
        tr.dc = dc;
        dc.binaryType = "arraybuffer";

        dc.onmessage = async (msgEv) => {
          const data = msgEv.data;

          if (typeof data === "string") {
            let obj = null;
            try { obj = JSON.parse(data); } catch {}
            if (!obj || obj.transfer_id !== transfer_id) return;

            if (obj.type === "meta" && obj.meta) {
              tr.meta = obj.meta;
              tr.recv.expected = Number(obj.meta.size || 0) || 0;
              ui.setStatus("Receiving…");
              return;
            }

            if (obj.type === "done") {
              tr.recv.gotDone = true;
              if (tr.recv.expected && tr.recv.got >= tr.recv.expected) {
                await finalizeIncomingP2pFile(sender, transfer_id);
              }
              return;
            }

            return;
          }

          if (data instanceof ArrayBuffer) {
            tr.recv.parts.push(data);
            tr.recv.got += data.byteLength || 0;
            if (tr.recv.expected) ui.setProgress(tr.recv.got / tr.recv.expected);

            if (tr.recv.gotDone && tr.recv.expected && tr.recv.got >= tr.recv.expected) {
              await finalizeIncomingP2pFile(sender, transfer_id);
            }
          }
        };

        dc.onopen = () => ui.setStatus("Receiving…");
        dc.onerror = () => fail("⚠️ DataChannel error");
        dc.onclose = () => {
          // If we didn't finish, treat as failure.
          if (!tr.recv.gotDone || (tr.recv.expected && tr.recv.got < tr.recv.expected)) {
            fail("⚠️ Channel closed");
          }
        };
      };

      // Watchdog: if the sender never completes handshake / never sends data.
      const deadline = Date.now() + (Number(P2P_FILE_HANDSHAKE_TIMEOUT_MS) || 7000) * 3;
      tr._watchdogInterval = setInterval(() => {
        if (!P2P_TRANSFERS.has(transfer_id)) return;
        if (tr.recv.got > 0) return; // activity started
        if (Date.now() > deadline) {
          fail("⏳ Sender not responding");
        }
      }, 600);

      try {
        // Apply offer, flush any ICE that arrived before the receiver clicked Accept,
        // then generate the answer.
        await pc.setRemoteDescription(new RTCSessionDescription(offer));
        await p2pFlushIceQueue(tr);
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);

        const resp = await socketEmitAck("p2p_file_answer", {
          to: sender,
          transfer_id,
          answer: { type: pc.localDescription.type, sdp: pc.localDescription.sdp },
        }).catch(() => null);

        if (!resp || resp.success === false) {
          fail("⚠️ Answer failed");
          return;
        }

        ui.setStatus("Answer sent — waiting for sender…");
      } catch (e) {
        const msg = `❌ Accept failed: ${String(e?.message || e || "error")}`;
        fail(msg);
      }
    });

    ui.onDecline(() => {
      ui.disableActions();
      ui.setStatus("Declined");
      p2pRememberClosedTransferId(transfer_id);
      socket.emit("p2p_file_decline", { to: sender, transfer_id, reason: "Declined" });
      setTimeout(() => ui.remove(), 700);
    });
  } catch (e) {
    console.error("p2p_file_offer handler failed:", e);
  }
});

async function finalizeIncomingP2pFile(sender, transfer_id) {
  const senderName = ecPmPeerName(sender);
  const tr = P2P_TRANSFERS.get(transfer_id);
  if (!tr || tr.role !== "receiver") return;

  const meta = tr.meta || {};
  const parts = tr.recv?.parts || [];
  const blob = new Blob(parts, { type: meta.mime || "application/octet-stream" });

  // Optional integrity check (best-effort)
  try {
    if (meta.sha256) {
      const buf = await blob.arrayBuffer();
      const got = await sha256HexFromArrayBuffer(buf);
      if (got !== meta.sha256) {
        tr.ui.setStatus("⚠️ Hash mismatch");
      }
    }
  } catch {}

  // Show final file card in the PM window
  const win = ecGetPmWindow(senderName);
  if (win) {
    appendDmPayload(win, `${senderName}:`, {
      kind: "file",
      source: "p2p",
      transfer_id,
      name: meta.name || "file",
      size: Number(meta.size || blob.size) || blob.size,
      mime: meta.mime || blob.type || "application/octet-stream",
      sha256: meta.sha256 || null,
      blob,
    }, { peer: senderName, direction: "in" });

    addPmHistory(senderName, "in", `📎 ${meta.name || "file"} (${humanBytes(Number(meta.size || blob.size) || blob.size)})`);
  }

  // ACK back to sender
  try {
    tr.dc && tr.dc.send(JSON.stringify({ type: "ack", transfer_id }));
  } catch {}

  tr.ui.setProgress(1);
  tr.ui.setStatus("✅ Received — click Download");
  setTimeout(() => tr.ui.remove(), 900);

  p2pSafeClose(transfer_id);
}

// ───────────────────────────────────────────────────────────────────────────────
// Voice events (WebRTC audio)
// ───────────────────────────────────────────────────────────────────────────────
socket.on("voice_dm_invite", ({ sender, call_id }) => {
  if (!VOICE_ENABLED) return;
  if (!sender || !call_id) return;
  const existing = VOICE_STATE.dmCalls.get(sender);
  if (existing) {
    // Duplicate invite from the same call can happen after reconnect/retry.
    // Do not overwrite an active/calling PC, or both sides can get stuck.
    if (existing.call_id === call_id) {
      if (existing.state === "incoming") {
        voiceDmUi(sender, { statusText: `Incoming call from ${sender}`, mode: "incoming" });
      }
      return;
    }
    try { socket.emit("voice_dm_decline", { to: sender, call_id, reason: "Busy" }, () => {}); } catch {}
    return;
  }
  openPrivateChat(sender);
  const call = { call_id, peer: sender, pc: null, remoteEl: null, state: "incoming", muted: false, isCaller: false, pendingIce: [], ending: false, _timeout: null };
  VOICE_STATE.dmCalls.set(sender, call);
  voiceDmScheduleTimeout(sender, call, "incoming");
  voiceDmUi(sender, { statusText: `Incoming call from ${sender}`, mode: "incoming" });
  toast(`🎤 Incoming voice call from ${sender}`, "info");
  maybeBrowserNotify("Voice call", `Incoming call from ${sender}`);
});

socket.on("voice_dm_accept", async ({ sender, call_id }) => {
  const peer = sender;
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call || call.call_id !== call_id || !call.isCaller) return;
  try {
    const pc = call.pc;
    if (!pc) {
      voiceDmCleanup(peer, "Call setup failed");
      return;
    }
    call.state = "connecting";
    voiceDmScheduleTimeout(peer, call, "connect");
    voiceDmUi(peer, { statusText: "Connecting…", mode: "calling" });
    const offer = await pc.createOffer({ offerToReceiveAudio: true });
    await pc.setLocalDescription(offer);
    const ack = await new Promise((resolve) => socket.emit("voice_dm_offer", { to: peer, call_id, offer: pc.localDescription }, resolve));
    if (!ack?.success || !ack?.delivered) {
      voiceDmCleanup(peer, ack?.error || "Peer unavailable");
      if (ack?.error) toast(`❌ ${ack.error}`, "error");
    }
  } catch (e) {
    console.error(e);
    voiceDmCleanup(peer, e?.message || "Offer failed");
  }
});

socket.on("voice_dm_decline", ({ sender, call_id, reason }) => {
  const peer = sender;
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call || call.call_id !== call_id) return;
  voiceDmCleanup(peer, reason || "Declined");
  toast(`🎤 Call declined by ${peer}`, "warn");
});

socket.on("voice_dm_end", ({ sender, call_id, reason }) => {
  const peer = sender;
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call || call.call_id !== call_id) return;
  voiceDmCleanup(peer, reason || "Ended");
  toast(`🎤 Call ended (${peer})`, "info");
});

socket.on("voice_dm_offer", async ({ sender, call_id, offer, ice_restart }) => {
  const peer = sender;
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call || call.call_id !== call_id) return;
  try {
    if (!call.pc) {
      // If user accepted, pc exists. If they didn't, auto-decline.
      voiceDeclineDmCall(peer, "Not accepted");
      return;
    }
    const offerCollision = !!(call.pc._huiMakingOffer || call.pc.signalingState !== "stable");
    const polite = !call.isCaller;
    if (!polite && offerCollision && !ice_restart) return;
    if (offerCollision && call.pc.setLocalDescription) {
      try { await call.pc.setLocalDescription({ type: "rollback" }); } catch {}
    }
    await call.pc.setRemoteDescription(new RTCSessionDescription(offer));
    await voiceDmFlushIce(call);
    const answer = await call.pc.createAnswer();
    await call.pc.setLocalDescription(answer);
    const ack = await new Promise((resolve) => socket.emit("voice_dm_answer", { to: peer, call_id, answer: call.pc.localDescription }, resolve));
    if (!ack?.success || !ack?.delivered) {
      voiceDmCleanup(peer, ack?.error || "Peer unavailable");
      if (ack?.error) toast(`❌ ${ack.error}`, "error");
      return;
    }
    voiceDmSetConnected(peer, call, "Mute");
  } catch (e) {
    console.error(e);
    voiceDmCleanup(peer, e?.message || "Offer handling failed");
  }
});

socket.on("voice_dm_answer", async ({ sender, call_id, answer }) => {
  const peer = sender;
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call || call.call_id !== call_id || !call.pc) return;
  try {
    await call.pc.setRemoteDescription(new RTCSessionDescription(answer));
    await voiceDmFlushIce(call);
    voiceDmSetConnected(peer, call, VOICE_STATE.micMuted ? "Unmute" : "Mute");
  } catch (e) {
    console.error(e);
    voiceDmCleanup(peer, e?.message || "Answer failed");
  }
});

socket.on("voice_dm_ice", async ({ sender, call_id, candidate }) => {
  const peer = sender;
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call || call.call_id !== call_id || !candidate) return;
  if (!call.pc || !call.pc.remoteDescription || !call.pc.remoteDescription.type) {
    voiceDmQueueIce(call, candidate);
    return;
  }
  try { await call.pc.addIceCandidate(new RTCIceCandidate(candidate)); } catch (e) { console.warn("DM ICE candidate rejected", e); }
});

// Room voice roster + signaling
socket.on("voice_room_roster", ({ room, users, limit, media_status }) => {
  if (!VOICE_ENABLED) return;
  if (!VOICE_STATE.room.joined || VOICE_STATE.room.name !== room) return;
  const roster = Array.isArray(users) ? users : [];
  const voiceUiActive = !!(VOICE_STATE.room.wantRoomVoice && !VOICE_STATE.room.viewerOnly);
  try {
    if (media_status && typeof voiceSetRoomMediaMap === "function") voiceSetRoomMediaMap(room, media_status);
    roster.forEach((u) => {
      if (!media_status || !Object.prototype.hasOwnProperty.call(media_status, u)) voiceSetMediaStatus(room, u, { voice_on: true });
    });
    if (!voiceUiActive) voiceSetMediaStatus(room, currentUser, { voice_on: false });
  } catch {}
  const limN = (limit !== undefined && limit !== null) ? Number(limit) : VOICE_MAX_ROOM_PEERS;
  const limText = (Number.isFinite(limN) && limN > 0) ? String(limN) : "∞";
  if (voiceUiActive) {
    voiceRoomUi({ show: true, statusText: `Voice connected (${roster.length}/${limText})`, joinVisible: false, leaveVisible: false, muteVisible: false });
    voiceApplyTalkMode({ silent: true });
    voiceUpdateRoomVoiceButton();
  } else {
    voiceRoomUi({ show: false });
    voiceUpdateRoomVoiceButton();
  }
  try { updateAllGroupVoiceButtons(); } catch {}
  try { refreshGroupVoiceIndicatorsForRoom(room); } catch {}
  for (const p of roster) {
    if (!p || p === currentUser) continue;
    voiceRoomEnsurePeer(room, p);
  }
});

socket.on("voice_room_user_joined", ({ room, username, voice_on }) => {
  if (!VOICE_ENABLED) return;
  if (!VOICE_STATE.room.joined || VOICE_STATE.room.name !== room) return;
  if (!username || username === currentUser) return;
  try { voiceSetMediaStatus(room, username, { voice_on: voice_on !== false }); } catch {}
  voiceRoomEnsurePeer(room, username);
  if (VOICE_STATE.room.wantRoomVoice && !VOICE_STATE.room.viewerOnly) voiceRoomUi({ show: true, joinVisible: false, leaveVisible: true, muteVisible: true });
  try { updateAllGroupVoiceButtons(); } catch {}
  try { refreshGroupVoiceIndicatorsForRoom(room); } catch {}
});

socket.on("voice_room_user_left", ({ room, username }) => {
  if (!VOICE_ENABLED) return;
  if (!VOICE_STATE.room.joined || VOICE_STATE.room.name !== room) return;
  if (!username) return;
  try { voiceSetMediaStatus(room, username, { voice_on: false, webcam_on: false }); } catch {}
  try { if (typeof huiCamSetViewerApproved === "function") huiCamSetViewerApproved(room, username, false); } catch {}
  try { if (typeof huiCamRemoveIncomingRequest === "function") huiCamRemoveIncomingRequest(room, username); } catch {}
  try { if (typeof huiCamRemoveRemoteVideo === "function") huiCamRemoveRemoteVideo(username); } catch {}
  const obj = VOICE_STATE.room.peers.get(username);
  if (obj) {
    try { voiceStopAutoQualityMonitor(`room-${room}-${username}`); } catch {}
    try { obj.pc?.close(); } catch {}
    try { obj.remoteEl?.remove(); } catch {}
    try { obj.remoteVideoEl?.remove(); } catch {}
    VOICE_STATE.room.peers.delete(username);
  }
  try { updateAllGroupVoiceButtons(); } catch {}
  try { refreshGroupVoiceIndicatorsForRoom(room); } catch {}
});


socket.on("voice_room_full", ({ room, limit, current, capacity }) => {
  if (!VOICE_ENABLED) return;
  const activeRoom = String(UIState.roomEmbedRoom || UIState.currentRoom || "");
  if (room && activeRoom && String(room) !== activeRoom) return;
  try { voiceShowRoomFull(room || activeRoom, { limit, current, capacity, error_code: "voice_room_full" }); } catch {}
});

// Server can forcibly disconnect users from voice when an admin lowers the room voice limit.
socket.on("voice_room_forced_leave", ({ room, reason, limit }) => {
  if (!VOICE_ENABLED) return;
  if (!VOICE_STATE.room.joined || VOICE_STATE.room.name !== room) return;
  const r = (reason ? String(reason) : "").trim();
  const lim = (limit === undefined || limit === null) ? null : Number(limit);
  const limText = (lim && Number.isFinite(lim) && lim > 0) ? ` (limit=${lim})` : "";
  toast(`🎤 Disconnected from voice${limText}${r ? ": " + r : ""}`, "warn");
  // Server already updated its roster; do not emit voice_room_leave again.
  voiceLeaveRoom(r ? `Disconnected: ${r}` : "Disconnected", false);
  try { updateAllGroupVoiceButtons(); } catch {}
  try { refreshGroupVoiceIndicatorsForRoom(room); } catch {}
});

socket.on("voice_room_offer", async ({ room, sender, offer, ice_restart }) => {
  if (!VOICE_ENABLED) return;
  if (!VOICE_STATE.room.joined || VOICE_STATE.room.name !== room) return;
  const peer = sender;
  if (!peer || peer === currentUser) return;
  voiceRoomEnsurePeer(room, peer);
  const obj = VOICE_STATE.room.peers.get(peer);
  if (!obj || !obj.pc) return;
  const pc = obj.pc;
  try {
    const offerCollision = !!(pc._huiMakingOffer || pc.signalingState !== "stable");
    pc._huiIgnoreOffer = !pc._huiPolite && offerCollision;
    if (pc._huiIgnoreOffer) return;
    if (offerCollision && pc.setLocalDescription) {
      try { await pc.setLocalDescription({ type: "rollback" }); } catch {}
    }
    await pc.setRemoteDescription(new RTCSessionDescription(offer));
    try { await voiceRoomFlushIce(peer, pc); } catch {}
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    socket.emit("voice_room_answer", { room, to: peer, answer: pc.localDescription });
  } catch (e) {
    console.warn("voice/video room offer failed", e);
  }
});

socket.on("voice_room_answer", async ({ room, sender, answer }) => {
  if (!VOICE_ENABLED) return;
  if (!VOICE_STATE.room.joined || VOICE_STATE.room.name !== room) return;
  const peer = sender;
  const obj = VOICE_STATE.room.peers.get(peer);
  if (!obj || !obj.pc) return;
  try {
    await obj.pc.setRemoteDescription(new RTCSessionDescription(answer));
    try { await voiceRoomFlushIce(peer, obj.pc); } catch {}
  } catch {}
});

socket.on("voice_room_ice", async ({ room, sender, candidate }) => {
  if (!VOICE_ENABLED) return;
  if (!VOICE_STATE.room.joined || VOICE_STATE.room.name !== room) return;
  const peer = sender;
  const obj = VOICE_STATE.room.peers.get(peer);
  if (!obj || !obj.pc || !candidate) return;
  try {
    if (obj.pc._huiIgnoreOffer) return;
    if (!obj.pc.remoteDescription) {
      try { voiceRoomQueueIce(peer, candidate); } catch {}
      return;
    }
    await obj.pc.addIceCandidate(new RTCIceCandidate(candidate));
  } catch {
    try { voiceRoomQueueIce(peer, candidate); } catch {}
  }
});


socket.on("voice_media_status", ({ room, username, voice_on, webcam_on }) => {
  try {
    if (!room) return;
    const isCurrentRoom = String(room) === String(UIState.currentRoom || "");
    const gid = typeof groupVoiceRoomIdFromName === 'function' ? groupVoiceRoomIdFromName(room) : 0;
    const hasGroupWindow = gid && UIState.windows?.has?.('group:' + String(gid));
    if (!isCurrentRoom && !hasGroupWindow) return;
    voiceSetMediaStatus(room, username, { voice_on: !!voice_on, webcam_on: !!webcam_on });
    try { refreshGroupVoiceIndicatorsForRoom(room); } catch {}
  } catch {}
});
