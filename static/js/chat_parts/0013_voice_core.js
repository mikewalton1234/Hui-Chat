
const VOICE_UI_BUSY = (window.VOICE_UI_BUSY instanceof Set) ? window.VOICE_UI_BUSY : new Set();
window.VOICE_UI_BUSY = VOICE_UI_BUSY;

function voiceActionKey(name, target = "") {
  return `${String(name || "voice").trim().toLowerCase()}::${String(target || "").trim().toLowerCase()}`;
}

function voiceActionBusy(name, target = "") {
  return VOICE_UI_BUSY.has(voiceActionKey(name, target));
}

function voiceSetActionBusy(name, target = "", busy = false) {
  const key = voiceActionKey(name, target);
  if (busy) VOICE_UI_BUSY.add(key);
  else VOICE_UI_BUSY.delete(key);
  try { voiceRefreshBusyUi(); } catch {}
}

async function voiceWithBusy(name, target = "", fn) {
  if (voiceActionBusy(name, target)) return { success: false, busy: true, error: "voice_action_busy" };
  voiceSetActionBusy(name, target, true);
  try { return await fn(); }
  finally { voiceSetActionBusy(name, target, false); }
}

function voiceSetBtnBusy(btn, busy, label) {
  if (!btn) return;
  if (busy) {
    if (!btn.dataset.ecVoiceBusyOriginalText) btn.dataset.ecVoiceBusyOriginalText = btn.textContent || "";
    if (btn.dataset.ecVoiceBusyOriginalDisabled === undefined) {
      btn.dataset.ecVoiceBusyOriginalDisabled = btn.disabled ? "1" : "0";
    }
    if (label) btn.textContent = label;
    btn.classList.add("isBusy");
    btn.setAttribute("aria-busy", "true");
    btn.disabled = true;
  } else {
    btn.classList.remove("isBusy");
    btn.removeAttribute("aria-busy");
    if (btn.dataset.ecVoiceBusyOriginalDisabled !== undefined) {
      btn.disabled = btn.dataset.ecVoiceBusyOriginalDisabled === "1";
    }
    if (btn.dataset.ecVoiceBusyOriginalText && !label) btn.textContent = btn.dataset.ecVoiceBusyOriginalText;
    delete btn.dataset.ecVoiceBusyOriginalText;
    delete btn.dataset.ecVoiceBusyOriginalDisabled;
  }
}

function voiceRefreshBusyUi() {
  try {
    const room = String(UIState.currentRoom || UIState.roomEmbedRoom || "");
    const roomVoiceBusy = voiceActionBusy("room", room) || voiceActionBusy("room-voice", room);
    voiceSetBtnBusy($("btnRoomEmbedVoice"), roomVoiceBusy, "🎤 Voice…");
    const camBusy = (typeof huiMediaIsBusy === "function") && huiMediaIsBusy("cam", room);
    voiceSetBtnBusy($("btnRoomEmbedCam"), !!camBusy, "📷 Webcam…");
  } catch {}
  try {
    UIState.windows.forEach((win) => {
      if (!win || !win._ym) return;
      if (win.dataset.kind === "pm") {
        const peer = String(win.dataset.peer || win.dataset.windowTitle || "").trim();
        const busy = voiceActionBusy("dm", peer) || voiceActionBusy("dm-call", peer) || voiceActionBusy("dm-accept", peer) || voiceActionBusy("dm-end", peer);
        [win._ym.voiceBtn, win._ym.voiceBtnCall, win._ym.voiceBtnHang, win._ym.voiceBtnAccept, win._ym.voiceBtnDecline].forEach((btn) => voiceSetBtnBusy(btn, busy, btn === win._ym.voiceBtn ? "🎤…" : null));
      }
      if (win.dataset.kind === "group") {
        const gid = String(win.dataset.groupId || "").trim();
        const busy = voiceActionBusy("group", gid);
        voiceSetBtnBusy(win._ym.groupVoiceBtn, busy, "🎤 Voice…");
      }
    });
  } catch {}
}
function voiceSecureContextOk() {
  // getUserMedia requires a secure context (HTTPS) except on localhost.
  const h = (location && location.hostname) || "";
  const localhost = (h === "localhost" || h === "127.0.0.1" || h === "::1");
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && (window.isSecureContext || localhost));
}

function voiceSecureContextHelp() {
  const h = (location && location.hostname) || "";
  const scheme = (location && location.protocol) || "";
  const localhost = (h === "localhost" || h === "127.0.0.1" || h === "::1");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (scheme === "http:" && !localhost) {
      return "Voice is blocked by the browser on LAN HTTP. Use http://localhost on this computer, or set up HTTPS with Caddy/Nginx before testing from another device.";
    }
    return "This browser does not expose microphone access here. Check browser permissions, HTTPS, and device settings.";
  }
  if (!window.isSecureContext && !localhost) {
    return "Voice requires HTTPS when opened from a LAN IP or domain. Localhost is allowed for same-computer testing.";
  }
  return "Voice could not access the microphone. Check browser permission and that the mic is not already locked by another app.";
}

function voiceQualityProfile(name = VOICE_AUDIO_QUALITY) {
  const key = String(name || "balanced").toLowerCase();
  return VOICE_QUALITY_PROFILES[key] || VOICE_QUALITY_PROFILES.balanced || { sample_rate: 24000, max_bitrate: 40000, label: "Balanced" };
}

function voiceAudioConstraints() {
  const profile = voiceQualityProfile();
  const sampleRate = Number(profile.sample_rate || VOICE_AUDIO_SAMPLE_RATE || 24000);
  return {
    echoCancellation: !!VOICE_ECHO_CANCELLATION,
    noiseSuppression: !!VOICE_NOISE_CANCELLATION,
    autoGainControl: !!VOICE_AUTO_GAIN_CONTROL,
    channelCount: 1,
    sampleRate: sampleRate > 0 ? { ideal: sampleRate } : undefined,
  };
}

async function voiceEnsureMic() {
  if (VOICE_STATE.micStream) return VOICE_STATE.micStream;
  if (!VOICE_ENABLED) throw new Error("Voice chat disabled");
  if (!voiceSecureContextOk()) {
    throw new Error(voiceSecureContextHelp());
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: false,
      audio: voiceAudioConstraints(),
    });
  } catch (err) {
    const msg = err && (err.name || err.message) ? `${err.name || "MediaError"}: ${err.message || voiceSecureContextHelp()}` : voiceSecureContextHelp();
    throw new Error(msg);
  }
  VOICE_STATE.micStream = stream;
  // If the user yanks permissions mid-call, cleanup.
  stream.getTracks().forEach(t => {
    t.addEventListener("ended", () => {
      // Best-effort: end all calls/room voice
      voiceEndAll("Mic stopped");
    });
  });
  voiceApplyTalkMode({ silent: true });
  return stream;
}

function voiceRoomAudioActive() {
  try {
    if (!VOICE_STATE.room.joined || !VOICE_STATE.room.name) return false;
    return !!(VOICE_STATE.room.wantRoomVoice || (VOICE_STATE.micStream && !VOICE_STATE.room.viewerOnly));
  } catch {
    return false;
  }
}

function voiceAnyActive() {
  const activeDm = Array.from(VOICE_STATE.dmCalls.values()).some(c => c && c.state && c.state !== "incoming");
  const activeRoom = voiceRoomAudioActive();
  let activeEnhanced = false;
  try {
    if (ecMediaModeReady()) {
      const m = ecMediaStateSnapshot();
      activeEnhanced = !!(m.voiceDesired || m.micEnabled);
    }
  } catch {}
  return !!(activeDm || activeRoom || activeEnhanced);
}

function voiceRemoveRoomAudioSenders() {
  try {
    if (!VOICE_STATE.room || !VOICE_STATE.room.peers) return;
    VOICE_STATE.room.peers.forEach((obj) => {
      if (!obj || !obj.pc || !obj.pc.getSenders) return;
      obj.pc.getSenders().forEach((sender) => {
        try {
          if (sender && sender.track && sender.track.kind === "audio") obj.pc.removeTrack(sender);
        } catch {}
      });
    });
  } catch {}
}

function voiceStopMicOnly() {
  if (VOICE_STATE.micStream) {
    try { VOICE_STATE.micStream.getTracks().forEach(t => t.stop()); } catch {}
  }
  VOICE_STATE.micStream = null;
  VOICE_STATE.micMuted = false;
  VOICE_STATE.talkHeld = false;
  voiceRefreshTalkControls();
}

function voiceMaybeStopMic() {
  const anyDm = (VOICE_STATE.dmCalls.size > 0);
  const anyRoomAudio = voiceRoomAudioActive();
  if (anyDm || anyRoomAudio) return;
  voiceStopMicOnly();
  voiceStopAllAutoQualityMonitors();
}

function voiceSetMute(muted) {
  VOICE_STATE.micMuted = !!muted;
  try {
    const s = VOICE_STATE.micStream;
    if (s) s.getAudioTracks().forEach(t => (t.enabled = !muted));
  } catch {}
  voiceRefreshTalkControls();
  try { updateAllGroupVoiceButtons(); } catch {}
}

function voiceGetHandsFree() {
  try {
    const saved = Settings.get("voiceHandsFree", null);
    if (saved !== null && saved !== undefined) return !!saved;
  } catch {}
  return !VOICE_DEFAULT_PUSH_TO_TALK;
}

function voiceSetHandsFree(on, opts = {}) {
  VOICE_STATE.handsFree = !!on;
  try { Settings.set("voiceHandsFree", !!on); } catch {}
  if (!opts.noApply) voiceApplyTalkMode({ silent: !!opts.silent });
  voiceRefreshTalkControls();
}

function voiceApplyTalkMode(opts = {}) {
  VOICE_STATE.handsFree = voiceGetHandsFree();
  const shouldMute = voiceAnyActive() && !VOICE_STATE.handsFree && !VOICE_STATE.talkHeld;
  const shouldUnmute = voiceAnyActive() && (VOICE_STATE.handsFree || VOICE_STATE.talkHeld);
  if (shouldMute) voiceSetMute(true);
  else if (shouldUnmute) voiceSetMute(false);
  if (!opts.silent && voiceAnyActive()) {
    toast(VOICE_STATE.handsFree ? "🎤 Hands-free mic is on" : "🔇 Push-to-talk mode is on", "info", 2400);
  }
  voiceRefreshTalkControls();
}

function voiceTalkDown(ev) {
  try { ev && ev.preventDefault && ev.preventDefault(); } catch {}
  if (VOICE_STATE.handsFree) return;
  if (!voiceAnyActive()) {
    toast("🎤 Join voice or start a voice call first", "warn", 2400);
    return;
  }
  VOICE_STATE.talkHeld = true;
  voiceSetMute(false);
  voiceRefreshTalkControls();
}

function voiceTalkUp(ev) {
  try { ev && ev.preventDefault && ev.preventDefault(); } catch {}
  if (VOICE_STATE.handsFree) return;
  VOICE_STATE.talkHeld = false;
  if (voiceAnyActive()) voiceSetMute(true);
  voiceRefreshTalkControls();
}

function voiceWireTalkButton(button, checkbox) {
  if (!button || button._huiVoiceTalkWired) {
    if (checkbox && !checkbox._huiVoiceHandsFreeWired) voiceWireHandsFreeCheckbox(checkbox);
    voiceRefreshTalkControls();
    return;
  }
  button._huiVoiceTalkWired = true;
  button.type = "button";
  button.addEventListener("pointerdown", voiceTalkDown);
  button.addEventListener("pointerup", voiceTalkUp);
  button.addEventListener("pointercancel", voiceTalkUp);
  button.addEventListener("mouseleave", voiceTalkUp);
  button.addEventListener("touchend", voiceTalkUp, { passive: false });
  button.addEventListener("keydown", (ev) => {
    if (ev.code === "Space" || ev.code === "Enter") voiceTalkDown(ev);
  });
  button.addEventListener("keyup", (ev) => {
    if (ev.code === "Space" || ev.code === "Enter") voiceTalkUp(ev);
  });
  if (checkbox) voiceWireHandsFreeCheckbox(checkbox);
  voiceRefreshTalkControls();
}

function voiceWireHandsFreeCheckbox(checkbox) {
  if (!checkbox || checkbox._huiVoiceHandsFreeWired) return;
  checkbox._huiVoiceHandsFreeWired = true;
  checkbox.checked = voiceGetHandsFree();
  checkbox.addEventListener("change", () => voiceSetHandsFree(!!checkbox.checked));
}

function voiceRefreshTalkControls() {
  const handsFree = voiceGetHandsFree();
  VOICE_STATE.handsFree = handsFree;
  const active = voiceAnyActive();
  const buttons = [];
  const roomBtn = $("btnRoomEmbedVoiceTalk");
  if (roomBtn) buttons.push(roomBtn);
  try {
    UIState.windows.forEach((win) => {
      if (win && win._ym) {
        if (win._ym.voiceBtnTalk) buttons.push(win._ym.voiceBtnTalk);
        if (win._ym.groupTalkBtn) buttons.push(win._ym.groupTalkBtn);
      }
    });
  } catch {}
  buttons.forEach((btn) => {
    btn.classList.toggle("active", !!VOICE_STATE.talkHeld && !handsFree);
    btn.disabled = !!handsFree || !active;
    btn.textContent = handsFree ? "Hands-free" : (VOICE_STATE.talkHeld ? "Talking…" : "Hold Talk");
    btn.title = handsFree ? "Hands-free is enabled; your mic stays open while voice is active" : "Hold to talk; release to mute again";
  });
  const checks = [];
  const roomChk = $("chkRoomEmbedHandsFree");
  if (roomChk) checks.push(roomChk);
  try {
    UIState.windows.forEach((win) => {
      if (win && win._ym) {
        if (win._ym.voiceHandsFree) checks.push(win._ym.voiceHandsFree);
        if (win._ym.groupHandsFree) checks.push(win._ym.groupHandsFree);
      }
    });
  } catch {}
  checks.forEach((chk) => { chk.checked = !!handsFree; });
}

function voiceWireRoomTalkControls() {
  voiceWireTalkButton($("btnRoomEmbedVoiceTalk"), $("chkRoomEmbedHandsFree"));
}

function voiceMediaRoomKey(room = UIState.currentRoom) {
  return String(room || "").trim();
}

function voiceMediaNormalizeStatus(status = {}) {
  return {
    voice_on: !!(status.voice_on || status.voice || status.mic_on || status.micEnabled),
    webcam_on: !!(status.webcam_on || status.webcam || status.camera_on || status.camEnabled),
  };
}

function voiceMediaMapForRoom(room = UIState.currentRoom) {
  const key = voiceMediaRoomKey(room);
  if (!key) return new Map();
  if (!VOICE_STATE.mediaByRoom) VOICE_STATE.mediaByRoom = new Map();
  if (!VOICE_STATE.mediaByRoom.has(key)) VOICE_STATE.mediaByRoom.set(key, new Map());
  return VOICE_STATE.mediaByRoom.get(key);
}

function voiceSetMediaStatus(room, username, status = {}) {
  room = voiceMediaRoomKey(room);
  username = String(username || "").trim();
  if (!room || !username) return;
  const map = voiceMediaMapForRoom(room);
  const prev = voiceMediaNormalizeStatus(map.get(username) || {});
  const next = { ...prev };
  if (Object.prototype.hasOwnProperty.call(status, "voice_on")) next.voice_on = !!status.voice_on;
  if (Object.prototype.hasOwnProperty.call(status, "webcam_on")) next.webcam_on = !!status.webcam_on;
  if (!next.voice_on && !next.webcam_on) map.delete(username);
  else map.set(username, next);
  voiceRefreshRoomUserMediaIcons(room);
}

function voiceSetRoomMediaMap(room, mediaStatus = {}) {
  room = voiceMediaRoomKey(room);
  if (!room || !mediaStatus || typeof mediaStatus !== "object") return;
  const map = voiceMediaMapForRoom(room);
  Object.entries(mediaStatus).forEach(([username, st]) => {
    const next = voiceMediaNormalizeStatus(st || {});
    if (!next.voice_on && !next.webcam_on) map.delete(String(username || ""));
    else map.set(String(username || ""), next);
  });
  voiceRefreshRoomUserMediaIcons(room);
}

function voiceStatusForUser(username, room = UIState.currentRoom) {
  const map = voiceMediaMapForRoom(room);
  const status = voiceMediaNormalizeStatus(map.get(String(username || "").trim()) || {});
  if (String(username || "").trim() === String(currentUser || "").trim()) {
    try {
      if (VOICE_STATE.room.joined && VOICE_STATE.room.name === room && !VOICE_STATE.room.viewerOnly) status.voice_on = true;
      if (ecMediaModeReady()) {
        const media = ecMediaStateSnapshot();
        if (String(media.huiRoom || "") === String(room || "")) {
          if (media.micEnabled || media.voiceDesired) status.voice_on = true;
          if (media.camEnabled || media.camDesired) status.webcam_on = true;
        }
      }
    } catch {}
  }
  return status;
}

function voiceMediaIconLabel(status = {}) {
  const st = voiceMediaNormalizeStatus(status);
  if (st.voice_on && st.webcam_on) return { icon: "🎧📹", label: "Voice and webcam active" };
  if (st.voice_on) return { icon: "🎧", label: "Voice active" };
  if (st.webcam_on) return { icon: "📹", label: "Webcam active" };
  return { icon: "", label: "" };
}

function voiceMediaIconNode(username, room = UIState.currentRoom) {
  const st = voiceStatusForUser(username, room);
  const info = voiceMediaIconLabel(st);
  const span = document.createElement("span");
  span.className = "ec-media-status-icon";
  span.dataset.mediaUser = String(username || "");
  if (info.icon) {
    span.textContent = info.icon;
    span.title = info.label;
    span.setAttribute("aria-label", info.label);
  } else {
    span.classList.add("empty");
    span.setAttribute("aria-hidden", "true");
  }
  return span;
}

function voiceRefreshRoomUserMediaIcons(room = UIState.currentRoom) {
  room = voiceMediaRoomKey(room);
  if (!room || String(room) !== String(UIState.currentRoom || "")) return;
  const ul = $("userList");
  if (!ul) return;
  ul.querySelectorAll("li[data-name]").forEach((li) => {
    const username = li.dataset.name || "";
    let slot = li.querySelector(".ec-media-status-icon");
    const next = voiceMediaIconNode(username, room);
    if (slot) slot.replaceWith(next);
    else {
      const left = li.querySelector(".liLeft") || li;
      left.appendChild(next);
    }
  });
}

function voicePublishLocalMediaStatus(room = UIState.currentRoom, patch = {}) {
  room = voiceMediaRoomKey(room);
  if (!room || !socket) return;
  let status = { ...patch };
  if (!Object.prototype.hasOwnProperty.call(status, "voice_on")) {
    status.voice_on = !!(VOICE_STATE.room.joined && VOICE_STATE.room.name === room && !VOICE_STATE.room.viewerOnly);
  }
  try {
    if (ecMediaModeReady()) {
      const media = ecMediaStateSnapshot();
      if (String(media.huiRoom || "") === room) {
        if (!Object.prototype.hasOwnProperty.call(patch, "voice_on")) status.voice_on = !!(media.micEnabled || media.voiceDesired);
        if (!Object.prototype.hasOwnProperty.call(patch, "webcam_on")) status.webcam_on = !!(media.camEnabled || media.camDesired);
      }
    }
  } catch {}
  voiceSetMediaStatus(room, currentUser, status);
  try { socket.emit("voice_media_status", { room, ...status }, () => {}); } catch {}
}

function voiceUpdateLocalMediaStatus(room = UIState.currentRoom, patch = {}) {
  return voicePublishLocalMediaStatus(room, patch);
}

function voiceWireWindowTalkControls(win) {
  if (!win || !win._ym) return;
  voiceWireTalkButton(win._ym.voiceBtnTalk || win._ym.groupTalkBtn, win._ym.voiceHandsFree || win._ym.groupHandsFree);
}

function voiceTargetBitrateForProfile(profileName = VOICE_AUDIO_QUALITY) {
  const profile = voiceQualityProfile(profileName);
  const n = Number(profile.max_bitrate || VOICE_AUDIO_MAX_BITRATE || 40000);
  return Number.isFinite(n) && n > 0 ? n : 40000;
}

function voiceApplySenderQuality(pc, bitrate = voiceTargetBitrateForProfile()) {
  try {
    if (!pc || !pc.getSenders) return;
    pc.getSenders().forEach((sender) => {
      if (!sender || !sender.track || sender.track.kind !== "audio" || !sender.getParameters) return;
      const params = sender.getParameters() || {};
      if (!params.encodings || !params.encodings.length) params.encodings = [{}];
      params.encodings[0].maxBitrate = bitrate;
      if (sender.setParameters) sender.setParameters(params).catch(() => {});
    });
  } catch {}
}

function voiceStartAutoQualityMonitor(key, pc) {
  if (!VOICE_AUTO_QUALITY || !pc || VOICE_STATE.autoQualityTimers.has(key)) return;
  let current = voiceTargetBitrateForProfile();
  const timer = setInterval(async () => {
    try {
      if (!pc || pc.connectionState === "closed") throw new Error("closed");
      const stats = await pc.getStats();
      let rtt = 0;
      let lost = 0;
      stats.forEach((st) => {
        if (st.type === "candidate-pair" && st.currentRoundTripTime) rtt = Math.max(rtt, Number(st.currentRoundTripTime) || 0);
        if (st.type === "outbound-rtp" && st.kind === "audio") lost += Number(st.packetsLost || 0);
      });
      const highPressure = rtt > 0.35 || lost > 10;
      const target = highPressure ? 24000 : voiceTargetBitrateForProfile();
      if (target !== current) {
        current = target;
        voiceApplySenderQuality(pc, current);
      }
    } catch {
      clearInterval(timer);
      VOICE_STATE.autoQualityTimers.delete(key);
    }
  }, 8000);
  VOICE_STATE.autoQualityTimers.set(key, timer);
}

function voiceStopAutoQualityMonitor(key) {
  const timer = VOICE_STATE.autoQualityTimers.get(key);
  if (timer) clearInterval(timer);
  VOICE_STATE.autoQualityTimers.delete(key);
}

function voiceStopAllAutoQualityMonitors() {
  for (const key of Array.from(VOICE_STATE.autoQualityTimers.keys())) voiceStopAutoQualityMonitor(key);
}

const VOICE_ROOM_ICE_QUEUE_LIMIT = 64;
const VOICE_ICE_RESTART_MAX_ATTEMPTS = 3;

function voicePeerConnectionClosed(pc) {
  const cs = String((pc && pc.connectionState) || "").toLowerCase();
  const ics = String((pc && pc.iceConnectionState) || "").toLowerCase();
  return !pc || cs === "closed" || ics === "closed";
}

async function voiceTryIceRestart(pc) {
  if (!pc || voicePeerConnectionClosed(pc)) return false;
  if (pc._huiIceRestarting) return true;
  const attempts = Number(pc._huiIceRestartAttempts || 0);
  if (attempts >= VOICE_ICE_RESTART_MAX_ATTEMPTS) return false;
  pc._huiIceRestartAttempts = attempts + 1;
  pc._huiIceRestarting = true;
  try {
    if (typeof pc.restartIce === "function") pc.restartIce();
    if (typeof pc._huiIceRestartOffer === "function") {
      const ok = await pc._huiIceRestartOffer();
      return ok !== false;
    }
    return true;
  } catch (err) {
    try { console.warn("ICE restart failed", err); } catch {}
    return false;
  } finally {
    setTimeout(() => { try { pc._huiIceRestarting = false; } catch {} }, 1200);
  }
}

function voiceMakePc() {
  const pc = new RTCPeerConnection({
    iceServers: VOICE_ICE_SERVERS,
    bundlePolicy: "max-bundle",
    rtcpMuxPolicy: "require",
    iceCandidatePoolSize: 2,
  });
  pc._huiIceRestartAttempts = 0;
  pc._huiIceRestarting = false;
  // Best-effort resilience: try ICE restart on failure. Use additive listeners
  // so DM/room-specific handlers do not overwrite the restart safety net.
  let restartTimer = null;
  const scheduleRestart = () => {
    try {
      if (!pc.restartIce || restartTimer || voicePeerConnectionClosed(pc)) return;
      restartTimer = setTimeout(() => {
        restartTimer = null;
        voiceTryIceRestart(pc).catch(() => {});
      }, 600);
    } catch {}
  };
  pc.addEventListener("iceconnectionstatechange", () => {
    try {
      if (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed") pc._huiIceRestartAttempts = 0;
      if (pc.iceConnectionState === "failed") scheduleRestart();
    } catch {}
  });
  pc.addEventListener("connectionstatechange", () => {
    try {
      if (pc.connectionState === "connected") pc._huiIceRestartAttempts = 0;
      if (pc.connectionState === "failed") scheduleRestart();
    } catch {}
  });
  return pc;
}

function voiceRoomQueueIce(peer, candidate) {
  if (!peer || !candidate) return;
  if (!VOICE_STATE.room.iceQueues) VOICE_STATE.room.iceQueues = new Map();
  const q = VOICE_STATE.room.iceQueues.get(peer) || [];
  if (q.length >= VOICE_ROOM_ICE_QUEUE_LIMIT) q.shift();
  q.push(candidate);
  VOICE_STATE.room.iceQueues.set(peer, q);
}

async function voiceRoomFlushIce(peer, pc) {
  if (!peer || !pc || !VOICE_STATE.room.iceQueues) return;
  const q = VOICE_STATE.room.iceQueues.get(peer) || [];
  VOICE_STATE.room.iceQueues.delete(peer);
  for (const c of q) {
    try { await pc.addIceCandidate(new RTCIceCandidate(c)); } catch {}
  }
}

function voiceAttachRemoteAudio(key, stream) {
  // Create (or replace) a hidden <audio> element so remote audio plays.
  const id = `ec-voice-audio-${key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement("audio");
    el.id = id;
    el.autoplay = true;
    el.playsInline = true;
    el.style.display = "none";
    document.body.appendChild(el);
  }
  try { el.srcObject = stream; } catch {}
  return el;
}

function voiceDmUi(peer, patch = {}) {
  const win = ecGetPmWindow(peer);
  if (!win || !win._ym) return;

  const bar = win._ym.voiceBar;
  const status = win._ym.voiceStatus;
  const bCall = win._ym.voiceBtnCall;
  const bHang = win._ym.voiceBtnHang;
  const bMute = win._ym.voiceBtnMute;
  const bAcc = win._ym.voiceBtnAccept;
  const bDec = win._ym.voiceBtnDecline;
  const bTalk = win._ym.voiceBtnTalk || null;
  const handsFree = win._ym.voiceHandsFree || null;
  const bCam = win._ym.voiceBtnCam || null;

  if (bar) {
    bar.classList.remove("hidden");
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Private voice controls");
  }
  if (patch.hideBar && bar) bar.classList.add("hidden");

  if (status) {
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    if (patch.statusText !== undefined) status.textContent = patch.statusText;
  }

  const mode = patch.mode || null; // idle|calling|incoming|active
  if (mode) {
    if (bCall) bCall.style.display = (mode === "idle") ? "" : "none";
    if (bHang) bHang.style.display = (mode === "calling" || mode === "active") ? "" : "none";
    if (bMute) bMute.style.display = (mode === "active") ? "" : "none";
    if (bAcc) bAcc.style.display = (mode === "incoming") ? "" : "none";
    if (bDec) bDec.style.display = (mode === "incoming") ? "" : "none";
    if (bTalk) bTalk.style.display = (mode === "active") ? "" : "none";
    if (handsFree && handsFree.parentElement) handsFree.parentElement.style.display = (mode === "active") ? "" : "none";
  }
  if (bTalk) voiceWireTalkButton(bTalk, handsFree);
  if (bMute && patch.muteLabel) bMute.textContent = patch.muteLabel;
  if (bCam) bCam.style.display = "none";
  if (bCam && patch.camLabel) bCam.textContent = patch.camLabel;
  const peerLabel = String(peer || "");
  if (bCall) bCall.setAttribute("aria-label", `Start voice call with ${peerLabel}`);
  if (bHang) bHang.setAttribute("aria-label", `Hang up voice call with ${peerLabel}`);
  if (bMute) bMute.setAttribute("aria-label", VOICE_STATE.micMuted ? "Unmute microphone" : "Mute microphone");
  if (bAcc) bAcc.setAttribute("aria-label", `Accept voice call from ${peerLabel}`);
  if (bDec) bDec.setAttribute("aria-label", `Decline voice call from ${peerLabel}`);
  try { voiceRefreshBusyUi(); } catch {}

  // Voice button state
  try { voiceUpdateDmVoiceButton(peer); } catch (e) {}
  try { voiceRefreshTalkControls(); } catch (e) {}
}

function voiceUpdateDmVoiceButton(peer) {
  const win = ecGetPmWindow(peer);
  if (!win || !win._ym || !win._ym.voiceBtn) return;
  const call = VOICE_STATE.dmCalls.get(peer);
  if (!call) {
    win._ym.voiceBtn.textContent = "🎤";
    win._ym.voiceBtn.title = "Voice chat — click to call (hands‑free)";
    win._ym.voiceBtn.setAttribute("aria-pressed", "false");
    win._ym.voiceBtn.setAttribute("aria-label", `Start voice call with ${peer}`);
    return;
  }
  if (call.state === "incoming") {
    win._ym.voiceBtn.textContent = "📞";
    win._ym.voiceBtn.title = "Incoming voice — click to accept • Decline button in bar";
    win._ym.voiceBtn.setAttribute("aria-pressed", "false");
    win._ym.voiceBtn.setAttribute("aria-label", `Accept incoming voice call from ${peer}`);
    return;
  }
  if (VOICE_STATE.micMuted) {
    win._ym.voiceBtn.textContent = "🔇";
    win._ym.voiceBtn.title = "Voice is on (muted) — click to hang up • right‑click to unmute";
    win._ym.voiceBtn.setAttribute("aria-pressed", "true");
    win._ym.voiceBtn.setAttribute("aria-label", `Voice call with ${peer} is muted; click to hang up`);
  } else {
    win._ym.voiceBtn.textContent = "📞";
    win._ym.voiceBtn.title = "Voice is on — click to hang up • right‑click to mute";
    win._ym.voiceBtn.setAttribute("aria-pressed", "true");
    win._ym.voiceBtn.setAttribute("aria-label", `Voice call with ${peer} is active; click to hang up`);
  }
  try { voiceRefreshBusyUi(); } catch {}
}

function voiceShowRoomFull(room, payload = {}) {
  const current = Number(payload.current || (payload.capacity && payload.capacity.current) || 0) || 0;
  const limit = Number(payload.limit || (payload.capacity && payload.capacity.limit) || VOICE_MAX_ROOM_PEERS) || 0;
  const limitText = limit > 0 ? String(limit) : "unlimited";
  const msg = limit > 0
    ? `Voice is full in ${room || "this room"}. ${current}/${limitText} users are already connected.`
    : `Voice is full in ${room || "this room"}. Try again in a moment.`;
  if (typeof ecConfirm === "function") {
    ecConfirm(msg, { title: "Voice room full", confirmLabel: "OK", hideCancel: true });
  } else if (typeof toast === "function") {
    toast(`🎤 ${msg}`, "warn", 6500);
  }
}

function voiceRoomUi(patch = {}) {
  const bar = $("roomEmbedVoiceBar");
  const status = $("roomEmbedVoiceStatus");
  const bJoin = $("btnRoomEmbedVoiceJoin");
  const bLeave = $("btnRoomEmbedVoiceLeave");
  const bMute = $("btnRoomEmbedVoiceMute");
  const bTalk = $("btnRoomEmbedVoiceTalk");
  const handsFree = $("chkRoomEmbedHandsFree");
  if (bar) {
    if (patch.show === true) bar.classList.remove("hidden");
    if (patch.show === false) bar.classList.add("hidden");
  }
  if (status) {
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    if (patch.statusText !== undefined) status.textContent = patch.statusText;
  }
  if (bJoin) bJoin.style.display = patch.joinVisible === false ? "none" : "";
  if (bLeave) bLeave.style.display = patch.leaveVisible === false ? "none" : "";
  if (bMute) bMute.style.display = patch.muteVisible === false ? "none" : "";
  if (bMute && patch.muteLabel) bMute.textContent = patch.muteLabel;
  if (bTalk) voiceWireTalkButton(bTalk, handsFree);
  // Keep the main room media buttons in sync with state.
  try { voiceUpdateRoomVoiceButton(); } catch (e) {}
  try { voiceUpdateRoomCamButton(); } catch (e) {}
}

function voiceUpdateRoomVoiceButton() {
  const btn = $("btnRoomEmbedVoice");
  if (!btn) return;

  if (ecMediaModeReady()) {
    const media = ecMediaStateSnapshot();
    btn.style.display = "";
    const active = !!(media.voiceDesired || media.micEnabled);
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
    btn.setAttribute("aria-label", active ? "Turn off room voice" : "Turn on room voice");
    if (!active) {
      btn.textContent = "🎤 Voice";
      btn.title = "Turn on microphone voice for this room";
    } else if (!media.micEnabled) {
      btn.textContent = "🎤 Voice…";
      btn.title = "Voice is requested and connecting";
    } else {
      btn.textContent = "📞 Voice";
      btn.title = "Voice is on — click to turn voice off";
    }
    try { voiceUpdateRoomCamButton(); } catch {}
    return;
  }

  // Legacy P2P voice mode. A webcam-only signaling join must not light up voice.
  const active = voiceRoomAudioActive();
  btn.style.display = "";
  if (!active) {
    btn.textContent = "🎤 Voice";
    btn.title = "Voice chat (room) — click to join (hands-free)";
    btn.classList.remove("active");
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-label", "Turn on room voice");
    try { voiceUpdateRoomCamButton(); } catch {}
    return;
  }
  btn.classList.add("active");
  btn.setAttribute("aria-pressed", "true");
  btn.setAttribute("aria-label", "Turn off room voice");
  if (VOICE_STATE.micMuted) {
    btn.textContent = "🔇 Voice";
    btn.title = "Voice is on (muted) — click to disable voice • right-click to unmute";
  } else {
    btn.textContent = "📞 Voice";
    btn.title = "Voice is on — click to disable voice • right-click to mute";
  }
  try { voiceUpdateRoomCamButton(); } catch {}
}

function voiceUpdateRoomCamButton() {
  const btn = $("btnRoomEmbedCam");
  if (!btn) return;
  if (!ecMediaModeReady()) {
    btn.classList.add("hidden");
    btn.classList.remove("active");
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-label", "Webcam is unavailable");
    return;
  }
  btn.classList.remove("hidden");
  const webcamOk = (typeof ecMediaWebcamAvailable === "function") ? ecMediaWebcamAvailable() : true;
  btn.disabled = !webcamOk;
  btn.classList.toggle("disabled", !webcamOk);
  if (!webcamOk) {
    btn.classList.remove("active");
    btn.textContent = "📷 Webcam";
    btn.title = (typeof ecMediaWebcamUnavailableReason === "function") ? ecMediaWebcamUnavailableReason() : "Webcam is not available.";
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-label", btn.title || "Webcam is unavailable");
    return;
  }
  const media = ecMediaStateSnapshot();
  const active = !!(media.camDesired || media.camEnabled);
  btn.classList.toggle("active", active);
  btn.setAttribute("aria-pressed", active ? "true" : "false");
  btn.setAttribute("aria-label", active ? "Turn off your webcam" : "Turn on your webcam");
  if (!active) {
    btn.textContent = "📷 Webcam";
    btn.title = "Turn on your webcam for this room";
  } else if (!media.camEnabled) {
    btn.textContent = "📷 Webcam…";
    btn.title = "Webcam is requested and connecting";
  } else {
    btn.textContent = "📹 Cam On";
    btn.title = "Your webcam is on — click to turn webcam off";
  }
}
