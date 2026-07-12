// Hui webcam/media engine — built-in WebRTC, no external media server SDK.
// This file intentionally keeps its current manifest filename so upgrades do not
// break stale deployments, but the runtime engine is HuiMedia/WebRTC.
(function () {
  const CAM_PROFILES = (HUI_CFG.webcam_quality_profiles && typeof HUI_CFG.webcam_quality_profiles === "object")
    ? HUI_CFG.webcam_quality_profiles
    : {
        low: { label: "Low data / compatible", width: 320, height: 180, frameRate: 12, max_bitrate: 160000, preferred_codecs: ["H264", "VP8", "VP9"], content_hint: "detail", degradation_preference: "maintain-framerate" },
        balanced: { label: "Balanced / compatible", width: 640, height: 360, frameRate: 18, max_bitrate: 550000, preferred_codecs: ["H264", "VP8", "VP9"], content_hint: "motion", degradation_preference: "balanced" },
        high: { label: "High quality", width: 1280, height: 720, frameRate: 24, max_bitrate: 1500000, preferred_codecs: ["H264", "VP8", "VP9", "AV1"], content_hint: "motion", degradation_preference: "balanced" },
      };

  const HUI_MEDIA = {
    huiRoom: "",
    voiceDesired: false,
    camDesired: false,
    comboDesired: false,
    camEnabled: false,
    micEnabled: false,
    camStream: null,
    panel: null,
    grid: null,
    status: null,
    quality: "balanced",
    codec: "auto",
    remoteTiles: new Map(),
    // viewer side: room::owner webcams this browser explicitly requested/was allowed to view.
    requestedViewers: new Set(),
    // owner side: room::viewer clients approved to receive this user's camera track.
    approvedViewers: new Set(),
    activeViewers: new Map(),
    localTile: null,
    lastCameraError: "",
    lastCameraQuality: "",
    actionLocks: new Set(),
    pendingViewRequests: new Set(),
  };

  function safeToast(message, level = "info", ms) {
    try { if (typeof toast === "function") toast(message, level, ms); } catch {}
  }

  function mediaActionKey(name, room = localRoomName(), peer = "") {
    return `${String(name || "media").trim().toLowerCase()}::${String(room || "").trim().toLowerCase()}::${String(peer || "").trim().toLowerCase()}`;
  }

  function huiMediaIsBusy(name, room = localRoomName(), peer = "") {
    return !!(HUI_MEDIA.actionLocks && HUI_MEDIA.actionLocks.has(mediaActionKey(name, room, peer)));
  }

  function huiMediaSetBusy(name, busy, room = localRoomName(), peer = "") {
    const key = mediaActionKey(name, room, peer);
    if (!HUI_MEDIA.actionLocks) HUI_MEDIA.actionLocks = new Set();
    if (busy) HUI_MEDIA.actionLocks.add(key);
    else HUI_MEDIA.actionLocks.delete(key);
    try { huiCamRefreshUiState(); } catch {}
  }

  async function huiMediaWithBusy(name, room, peer, fn) {
    if (huiMediaIsBusy(name, room, peer)) return { success: false, busy: true, error: "media_action_busy" };
    huiMediaSetBusy(name, true, room, peer);
    try { return await fn(); }
    finally { huiMediaSetBusy(name, false, room, peer); }
  }

  function huiSetButtonBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      if (!btn.dataset.ecBusyOriginalText) btn.dataset.ecBusyOriginalText = btn.textContent || "";
      if (btn.dataset.ecBusyOriginalDisabled === undefined) {
        btn.dataset.ecBusyOriginalDisabled = btn.disabled ? "1" : "0";
      }
      if (label) btn.textContent = label;
      btn.classList.add("isBusy");
      btn.setAttribute("aria-busy", "true");
      btn.disabled = true;
    } else {
      btn.classList.remove("isBusy");
      btn.removeAttribute("aria-busy");
      if (btn.dataset.ecBusyOriginalDisabled !== undefined) {
        btn.disabled = btn.dataset.ecBusyOriginalDisabled === "1";
      }
      if (btn.dataset.ecBusyOriginalText && !label) btn.textContent = btn.dataset.ecBusyOriginalText;
      delete btn.dataset.ecBusyOriginalText;
      delete btn.dataset.ecBusyOriginalDisabled;
    }
  }

  function readSavedQuality() {
    try {
      const v = Settings && Settings.get ? Settings.get("huiWebcamQuality", null) : null;
      if (v && CAM_PROFILES[String(v).toLowerCase()]) return String(v).toLowerCase();
    } catch {}
    const cfg = String((HUI_CFG && HUI_CFG.webcam_quality) || (HUI_CFG && HUI_CFG.hui_webcam_quality) || "balanced").toLowerCase();
    return CAM_PROFILES[cfg] ? cfg : "balanced";
  }

  function saveQuality(name) {
    const q = CAM_PROFILES[String(name || "").toLowerCase()] ? String(name).toLowerCase() : "balanced";
    HUI_MEDIA.quality = q;
    try { Settings && Settings.set && Settings.set("huiWebcamQuality", q); } catch {}
    return q;
  }

  function profile(name = HUI_MEDIA.quality) {
    const key = String(name || "balanced").toLowerCase();
    return CAM_PROFILES[key] || CAM_PROFILES.balanced || CAM_PROFILES.low;
  }

  HUI_MEDIA.quality = readSavedQuality();

  function isLocalhostLikeOrigin() {
    try {
      const h = String(location && location.hostname || "").toLowerCase();
      return h === "localhost" || h === "127.0.0.1" || h === "::1" || h.endsWith(".localhost");
    } catch {
      return false;
    }
  }

  function webcamConfigStatus() {
    const cfg = (HUI_CFG && typeof HUI_CFG === "object") ? HUI_CFG : {};
    const features = (cfg.features && typeof cfg.features === "object") ? cfg.features : {};
    const policy = huiCamPolicy();
    if (policy.webcam_approval_mode === "disabled") {
      return { ok: false, reason: "Webcam is disabled by the server webcam policy." };
    }
    if (cfg.webcam_enabled === false || cfg.hui_webcam_enabled === false) {
      const mode = String(cfg.av_mode || cfg.av_requested_mode || "").toLowerCase();
      const suffix = mode === "standard" ? " Admin → Hui Media is set to Standard voice only." : "";
      return { ok: false, reason: `Webcam is disabled by server settings.${suffix}` };
    }
    if (features.webcam === false) {
      return { ok: false, reason: "Webcam is not enabled for the current server media mode." };
    }
    return { ok: true, reason: "" };
  }

  function browserWebcamStatus() {
    if (!VOICE_ENABLED) return { ok: false, reason: "Voice/media is disabled on this server." };
    if (typeof RTCPeerConnection === "undefined") return { ok: false, reason: "This browser does not support WebRTC peer connections." };
    if (!window.isSecureContext && !isLocalhostLikeOrigin()) {
      return { ok: false, reason: "Webcam requires HTTPS, localhost, or 127.0.0.1." };
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return { ok: false, reason: "This browser/context does not expose camera access. Use HTTPS or localhost, and make sure camera permission is not blocked." };
    }
    return { ok: true, reason: "" };
  }

  function ready() {
    const browser = browserWebcamStatus();
    // Keep the media engine ready for room voice even when webcam is disabled
    // by policy.  Webcam-specific controls call webcamAvailable() below.
    return !!(VOICE_ENABLED && typeof RTCPeerConnection !== "undefined" && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  function webcamUnavailableReason() {
    const cfg = webcamConfigStatus();
    if (!cfg.ok) return cfg.reason;
    const browser = browserWebcamStatus();
    if (!browser.ok) return browser.reason;
    return "Webcam is not available.";
  }

  function webcamAvailable() {
    return !!(webcamConfigStatus().ok && browserWebcamStatus().ok);
  }

  function localRoomName() {
    return String(HUI_MEDIA.huiRoom || (VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.name) || UIState.currentRoom || UIState.roomEmbedRoom || "").trim();
  }

  function updateStatus(text) {
    if (HUI_MEDIA.status) HUI_MEDIA.status.textContent = text || "";
    try { huiCamRefreshDiagnostics(); } catch {}
  }


  function huiCamPolicy() {
    const cfg = (HUI_CFG && typeof HUI_CFG === "object") ? HUI_CFG : {};
    const policy = (cfg.webcam_policy && typeof cfg.webcam_policy === "object") ? cfg.webcam_policy : {};
    const raw = String(cfg.webcam_approval_mode || policy.webcam_approval_mode || "owner_approval").trim().toLowerCase().replace(/-/g, "_");
    const mode = (raw === "open" || raw === "public" || raw === "everyone") ? "open"
      : (raw === "disabled" || raw === "blocked" || raw === "off") ? "disabled"
      : "owner_approval";
    return {
      webcam_approval_mode: mode,
      webcam_max_viewers: Number(cfg.webcam_max_viewers || policy.webcam_max_viewers || 0) || 0,
      default_media_policy: String(cfg.default_media_policy || policy.default_media_policy || "user_choice"),
    };
  }

  function huiCamKey(room, username) {
    return `${String(room || "").trim()}::${String(username || "").trim()}`;
  }

  function huiCamViewerRequested(room, owner) {
    return HUI_MEDIA.requestedViewers.has(huiCamKey(room, owner));
  }

  function huiCamViewerApproved(room, viewer) {
    return HUI_MEDIA.approvedViewers.has(huiCamKey(room, viewer));
  }

  function huiCamSetViewerApproved(room, viewer, approved) {
    const key = huiCamKey(room, viewer);
    if (!String(viewer || "").trim() || !String(room || "").trim()) return false;
    if (approved) HUI_MEDIA.approvedViewers.add(key);
    else HUI_MEDIA.approvedViewers.delete(key);
    return true;
  }

  function huiCamRequestKey(room, viewer) {
    return huiCamKey(room, viewer).toLowerCase();
  }

  function huiCamViewerRoomKey(room) {
    return String(room || localRoomName()).trim();
  }

  function huiCamViewerSetForRoom(room) {
    const key = huiCamViewerRoomKey(room);
    if (!key) return new Set();
    if (!HUI_MEDIA.activeViewers.has(key)) HUI_MEDIA.activeViewers.set(key, new Set());
    return HUI_MEDIA.activeViewers.get(key);
  }

  function huiCamViewerSummary(room) {
    const key = huiCamViewerRoomKey(room);
    const viewers = key ? Array.from(HUI_MEDIA.activeViewers.get(key) || []).filter(Boolean).sort() : [];
    return { viewers, viewerCount: viewers.length };
  }

  function huiCamUpdateLocalViewerInfo(room) {
    if (!HUI_MEDIA.localTile || !HUI_MEDIA.localTile._huiInfo) return;
    const summary = huiCamViewerSummary(room);
    if (!HUI_MEDIA.camDesired && !HUI_MEDIA.camEnabled) {
      HUI_MEDIA.localTile._huiInfo.textContent = "Local camera preview";
      return;
    }
    HUI_MEDIA.localTile._huiInfo.textContent = summary.viewerCount
      ? `Viewing: ${summary.viewers.join(", ")}`
      : "No active webcam viewers";
    try { HUI_MEDIA.localTile.dataset.viewerCount = String(summary.viewerCount); } catch {}
  }

  function huiCamSetActiveViewing(room, viewer, viewing) {
    room = huiCamViewerRoomKey(room);
    viewer = String(viewer || "").trim();
    if (!room || !viewer || viewer === String(currentUser || "").trim()) return huiCamViewerSummary(room);
    const set = huiCamViewerSetForRoom(room);
    if (viewing) set.add(viewer);
    else set.delete(viewer);
    huiCamUpdateLocalViewerInfo(room);
    return huiCamViewerSummary(room);
  }

  function huiCamReplaceActiveViewers(room, viewers) {
    room = huiCamViewerRoomKey(room);
    if (!room) return huiCamViewerSummary(room);
    const set = new Set((Array.isArray(viewers) ? viewers : []).map(v => String(v || "").trim()).filter(v => v && v !== String(currentUser || "").trim()));
    HUI_MEDIA.activeViewers.set(room, set);
    huiCamUpdateLocalViewerInfo(room);
    return huiCamViewerSummary(room);
  }

  function huiCamRenderAlertRequests(opts = {}) {
    try {
      if (typeof renderAlertsInviteListInto === "function") {
        renderAlertsInviteListInto($("railAlertsList"), UIState.groupInvites, UIState.roomInvites, { openRail: true });
      }
    } catch {}
    try { if (typeof updateDockSummaryCounts === "function") updateDockSummaryCounts(); } catch {}
    try {
      if (opts && opts.open && typeof openDockRailPanel === "function") openDockRailPanel("alerts");
    } catch {}
  }

  function huiCamUpsertIncomingRequest(room, viewer, policy = null) {
    room = String(room || localRoomName()).trim();
    viewer = String(viewer || "").trim();
    if (!room || !viewer || viewer === String(currentUser || "").trim()) return null;
    if (!Array.isArray(UIState.webcamRequests)) UIState.webcamRequests = [];
    const key = huiCamRequestKey(room, viewer);
    const existing = UIState.webcamRequests.find((req) => huiCamRequestKey(req && req.room, req && req.viewer) === key);
    const req = existing || { room, viewer, requested_at: new Date().toISOString() };
    req.room = room;
    req.viewer = viewer;
    req.policy = policy || req.policy || null;
    req.requested_at = req.requested_at || new Date().toISOString();
    req.updated_at = new Date().toISOString();
    if (!existing) UIState.webcamRequests.unshift(req);
    huiCamRenderAlertRequests({ open: true });
    return req;
  }

  function huiCamRemoveIncomingRequest(room, viewer) {
    room = String(room || localRoomName()).trim();
    viewer = String(viewer || "").trim();
    if (!Array.isArray(UIState.webcamRequests) || !room || !viewer) return false;
    const key = huiCamRequestKey(room, viewer);
    const before = UIState.webcamRequests.length;
    UIState.webcamRequests = UIState.webcamRequests.filter((req) => huiCamRequestKey(req && req.room, req && req.viewer) !== key);
    if (UIState.webcamRequests.length !== before) {
      huiCamRenderAlertRequests({ open: false });
      return true;
    }
    return false;
  }

  function huiCamClearIncomingRequestsForRoom(room) {
    room = String(room || localRoomName()).trim();
    if (!Array.isArray(UIState.webcamRequests) || !room) return false;
    const before = UIState.webcamRequests.length;
    UIState.webcamRequests = UIState.webcamRequests.filter((req) => String(req && req.room || "") !== room);
    if (UIState.webcamRequests.length !== before) huiCamRenderAlertRequests({ open: false });
    return UIState.webcamRequests.length !== before;
  }

  function huiCamCanSendToPeer(room, peer) {
    if (!HUI_MEDIA.camDesired || !HUI_MEDIA.camEnabled) return false;
    if (!peer || peer === String(currentUser || "")) return false;
    const policy = huiCamPolicy();
    if (policy.webcam_approval_mode === "disabled") return false;
    // Important privacy rule: even "open" mode means anyone may request/join
    // without owner approval; it does NOT mean every room user is auto-subscribed.
    return huiCamViewerApproved(room, peer);
  }

  function huiCamCanReceiveFromPeer(room, owner) {
    if (!owner || owner === String(currentUser || "")) return false;
    const policy = huiCamPolicy();
    if (policy.webcam_approval_mode === "disabled") return false;
    return huiCamViewerRequested(room, owner);
  }

  function ensurePanel() {
    if (HUI_MEDIA.panel && document.body.contains(HUI_MEDIA.panel)) return HUI_MEDIA.panel;

    const panel = document.createElement("div");
    panel.className = "ym-avPanel";
    panel.id = "huiWebcamPanel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Voice and webcam panel");

    const top = document.createElement("div");
    top.className = "ym-avTop";

    const meta = document.createElement("div");
    meta.className = "ym-avMeta";
    const title = document.createElement("div");
    title.className = "ym-avTitle";
    title.textContent = "Webcam";
    const status = document.createElement("div");
    status.className = "ym-avStatus";
    status.textContent = "Built-in WebRTC";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    meta.append(title, status);

    const close = document.createElement("button");
    close.type = "button";
    close.className = "miniBtn";
    close.textContent = "✕";
    close.title = "Close webcam panel";
    close.addEventListener("click", () => panel.classList.add("hidden"));
    top.append(meta, close);

    const deviceRow = document.createElement("div");
    deviceRow.className = "ym-avDeviceRow";
    const qualityField = document.createElement("label");
    qualityField.className = "ym-avDeviceField";
    const qText = document.createElement("span");
    qText.textContent = "Quality";
    const select = document.createElement("select");
    select.className = "ym-avSelect";
    for (const [key, val] of Object.entries(CAM_PROFILES)) {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = val && val.label ? String(val.label) : key;
      select.appendChild(opt);
    }
    select.value = HUI_MEDIA.quality;
    select.addEventListener("change", async () => {
      const q = saveQuality(select.value);
      try { await huiCamApplyQualityToLocalTrack(); } catch {}
      try { huiCamApplyQualityToAllSenders(); } catch {}
      safeToast(`📷 Webcam quality set to ${profile(q).label || q}`, "info", 2200);
    });
    qualityField.append(qText, select);
    deviceRow.appendChild(qualityField);

    const diagnostics = document.createElement("div");
    diagnostics.className = "ym-avDiagnostics";
    diagnostics.setAttribute("aria-live", "polite");
    diagnostics.textContent = "Media diagnostics will appear here.";

    const grid = document.createElement("div");
    grid.className = "ym-avGrid";

    panel.append(top, deviceRow, diagnostics, grid);
    HUI_MEDIA.diagnostics = diagnostics;
    document.body.appendChild(panel);
    HUI_MEDIA.panel = panel;
    HUI_MEDIA.grid = grid;
    HUI_MEDIA.status = status;
    return panel;
  }

  function showPanel() {
    const panel = ensurePanel();
    panel.classList.remove("hidden");
    return panel;
  }

  function huiCamDestroyPanelIfIdle(opts = {}) {
    const force = !!(opts && opts.force);
    const localTileLive = !!(HUI_MEDIA.localTile && document.body.contains(HUI_MEDIA.localTile));
    const remoteTileLive = Array.from(HUI_MEDIA.remoteTiles.values()).some((tile) => tile && document.body.contains(tile));
    const mediaStillVisible = localTileLive || remoteTileLive || !!HUI_MEDIA.camDesired || !!HUI_MEDIA.camEnabled;
    if (!force && mediaStillVisible) return false;
    const panel = HUI_MEDIA.panel;
    if (panel) {
      try { panel.remove(); } catch { try { panel.classList.add("hidden"); } catch {} }
    }
    HUI_MEDIA.panel = null;
    HUI_MEDIA.grid = null;
    HUI_MEDIA.status = null;
    HUI_MEDIA.diagnostics = null;
    return true;
  }

  function makeTile(username, label, local = false) {
    showPanel();
    const tile = document.createElement("div");
    tile.className = "ym-avTile";
    tile.dataset.user = String(username || "");

    const head = document.createElement("div");
    head.className = "ym-avTileHead";
    const name = document.createElement("div");
    name.className = "ym-avTileName";
    name.textContent = label || username || "Webcam";
    const badge = document.createElement("div");
    badge.className = "ym-avTileBadge";
    badge.textContent = local ? "You" : "Peer";
    head.append(name, badge);

    const media = document.createElement("div");
    media.className = "ym-avMedia";
    const video = document.createElement("video");
    video.autoplay = true;
    video.playsInline = true;
    video.muted = !!local;
    media.appendChild(video);

    const controls = document.createElement("div");
    controls.className = "ym-avTileControls";
    const info = document.createElement("div");
    info.className = "ym-avViewers";
    info.textContent = local ? "Local camera preview" : "Room webcam stream";
    controls.appendChild(info);
    const actions = document.createElement("div");
    actions.className = "ym-avTileActions";
    const stopBtn = document.createElement("button");
    stopBtn.type = "button";
    stopBtn.className = local ? "miniBtn danger" : "miniBtn";
    stopBtn.textContent = local ? "Stop camera" : "Stop viewing";
    stopBtn.title = local ? "Turn off your webcam" : "Stop viewing this webcam";
    stopBtn.addEventListener("click", () => {
      try {
        if (local) huiCamDisable("Webcam disabled", { keepRoom: !!HUI_MEDIA.voiceDesired });
        else huiCamStopViewing(username, localRoomName());
      } catch (e) { safeToast(`📷 ${e?.message || e}`, "warn", 4200); }
    });
    actions.appendChild(stopBtn);
    controls.appendChild(actions);
    tile._huiStopBtn = stopBtn;

    tile.append(head, media, controls);
    tile._huiVideo = video;
    tile._huiInfo = info;
    HUI_MEDIA.grid.appendChild(tile);
    return tile;
  }

  function attachLocalPreview(stream) {
    if (!stream) return;
    const tile = HUI_MEDIA.localTile || makeTile(currentUser || "me", `${currentUser || "Me"} camera`, true);
    HUI_MEDIA.localTile = tile;
    try { tile._huiVideo.srcObject = stream; } catch {}
    huiCamUpdateLocalViewerInfo(localRoomName());
    return tile;
  }

  function huiCamAttachRemoteVideo(room, peer, stream) {
    if (!room || localRoomName() && String(room) !== localRoomName()) return null;
    const key = String(peer || "").trim();
    if (!key) return null;
    if (!huiCamCanReceiveFromPeer(room, key)) {
      try { if (stream && stream.getVideoTracks) stream.getVideoTracks().forEach(t => { t.enabled = false; }); } catch {}
      return null;
    }
    let tile = HUI_MEDIA.remoteTiles.get(key);
    if (!tile || !document.body.contains(tile)) {
      tile = makeTile(key, `${key} camera`, false);
      HUI_MEDIA.remoteTiles.set(key, tile);
    }
    try { tile._huiVideo.srcObject = stream; } catch {}
    if (tile._huiInfo) tile._huiInfo.textContent = `Receiving ${key}'s webcam`;
    updateStatus(`Receiving webcam from ${key}`);
    showPanel();
    try { huiCamRefreshDiagnostics(); } catch {}
    return tile;
  }

  function huiCamRemoveRemoteVideo(peer) {
    const key = String(peer || "").trim();
    if (!key) return;
    const tile = HUI_MEDIA.remoteTiles.get(key);
    if (tile) {
      try { tile._huiVideo.srcObject = null; } catch {}
      try { tile.remove(); } catch {}
    }
    HUI_MEDIA.remoteTiles.delete(key);
    try { huiCamRefreshDiagnostics(); } catch {}
    try { huiCamDestroyPanelIfIdle(); } catch {}
  }

  function clearRemoteVideos() {
    for (const key of Array.from(HUI_MEDIA.remoteTiles.keys())) huiCamRemoveRemoteVideo(key);
    try { huiCamDestroyPanelIfIdle(); } catch {}
  }

  function videoConstraints(profileName = HUI_MEDIA.quality) {
    const p = profile(profileName);
    const width = Number(p.width || 640);
    const height = Number(p.height || 360);
    const frameRate = Number(p.frameRate || p.framerate || 18);
    const out = {
      width: width > 0 ? { ideal: width } : undefined,
      height: height > 0 ? { ideal: height } : undefined,
      frameRate: frameRate > 0 ? { ideal: frameRate, max: Math.max(10, frameRate) } : undefined,
      facingMode: { ideal: "user" },
    };
    if (width > 0 && height > 0) out.aspectRatio = { ideal: width / height };
    return out;
  }

  function makeCameraError(name, message) {
    const err = new Error(message || name || "CameraError");
    err.name = name || "CameraError";
    return err;
  }

  async function browserHasVideoInput() {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.enumerateDevices !== "function") return true;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (!Array.isArray(devices) || devices.length === 0) return true;
      return devices.some((device) => device && device.kind === "videoinput");
    } catch {
      // Permission and privacy failures here should not block the normal
      // getUserMedia permission prompt. The real capture attempt below will
      // return the authoritative browser error.
      return true;
    }
  }

  function isFatalCameraError(err) {
    const name = String(err && err.name || "").toLowerCase();
    return name.includes("notallowed")
      || name.includes("permission")
      || name.includes("security")
      || name.includes("notfound")
      || name.includes("notreadable");
  }

  function cameraOpenAttempts() {
    const preferred = String(HUI_MEDIA.quality || "balanced").toLowerCase();
    const order = [];
    [preferred, "balanced", "low"].forEach((q) => {
      if (CAM_PROFILES[q] && !order.includes(q)) order.push(q);
    });
    const attempts = order.map((q) => ({ quality: q, constraints: videoConstraints(q) }));
    attempts.push({ quality: "browser-default", constraints: true });
    return attempts;
  }

  function describeCameraError(err) {
    const name = String(err && err.name || "CameraError");
    const detail = String(err && err.message || "Camera blocked or unavailable");
    if (name === "NotAllowedError" || name === "PermissionDeniedError") return "Camera permission was denied. Allow camera access in the browser address-bar permissions menu, then try Webcam again.";
    if (name === "SecurityError") return "Camera access is blocked by the browser security context. Use HTTPS, localhost, or 127.0.0.1.";
    if (name === "NotFoundError" || name === "DevicesNotFoundError") return "No webcam was found by the browser. Plug in or enable a camera, then try again.";
    if (name === "NotReadableError" || name === "TrackStartError") return "The webcam is busy or blocked by another app. Close other camera apps/tabs, then try again.";
    if (name === "AbortError" || String(detail).toLowerCase().includes("starting videoinput failed")) return "The browser found a webcam, but it could not start it. Close other apps/tabs using the camera, unplug/replug the webcam if needed, then try again.";
    if (name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") return "The webcam does not support the requested quality. Try Low data, or let the browser use its default camera settings.";
    if (name === "Error" && detail) return detail;
    return `${name}: ${detail}`;
  }

  async function ensureCamera() {
    if (HUI_MEDIA.camStream) return HUI_MEDIA.camStream;
    if (!webcamAvailable()) throw new Error(webcamUnavailableReason());
    let lastErr = null;
    const requestedQuality = HUI_MEDIA.quality;
    let chosenQuality = HUI_MEDIA.quality;
    if (!(await browserHasVideoInput())) {
      lastErr = makeCameraError("NotFoundError", "No video input device is available.");
      HUI_MEDIA.lastCameraError = describeCameraError(lastErr);
      throw new Error(HUI_MEDIA.lastCameraError);
    }
    for (const attempt of cameraOpenAttempts()) {
      try {
        updateStatus(attempt.quality === "browser-default" ? "Opening webcam with browser defaults…" : `Opening webcam · ${profile(attempt.quality).label || attempt.quality}…`);
        const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: attempt.constraints });
        if (attempt.quality !== "browser-default" && attempt.quality !== HUI_MEDIA.quality) chosenQuality = saveQuality(attempt.quality);
        else chosenQuality = HUI_MEDIA.quality;
        const track = stream.getVideoTracks()[0];
        if (track) {
          try { track.contentHint = String(profile(chosenQuality).content_hint || "motion"); } catch {}
          track.addEventListener("ended", () => {
            try { huiCamDisable("Camera stopped", { keepRoom: true }); } catch {}
          });
        }
        HUI_MEDIA.lastCameraError = "";
        HUI_MEDIA.lastCameraQuality = attempt.quality === "browser-default" ? "browser-default" : String(chosenQuality || attempt.quality);
        if (attempt.quality !== requestedQuality && attempt.quality !== "browser-default") {
          safeToast(`📷 Requested quality was not supported; using ${profile(attempt.quality).label || attempt.quality}.`, "warn", 3600);
        } else if (attempt.quality === "browser-default") {
          safeToast("📷 Requested webcam constraints failed; using browser default camera settings.", "warn", 3600);
        }
        HUI_MEDIA.camStream = stream;
        HUI_MEDIA.camEnabled = true;
        attachLocalPreview(stream);
        return stream;
      } catch (err) {
        lastErr = err;
        if (isFatalCameraError(err)) break;
      }
    }
    HUI_MEDIA.lastCameraError = describeCameraError(lastErr);
    throw new Error(HUI_MEDIA.lastCameraError || "Camera blocked");
  }

  async function huiCamApplyQualityToLocalTrack() {
    const track = HUI_MEDIA.camStream && HUI_MEDIA.camStream.getVideoTracks && HUI_MEDIA.camStream.getVideoTracks()[0];
    if (!track) return;
    try { track.contentHint = String(profile().content_hint || "motion"); } catch {}
    if (track.applyConstraints) {
      try { await track.applyConstraints(videoConstraints()); }
      catch (err) {
        safeToast(`📷 Could not apply that camera quality: ${describeCameraError(err)}`, "warn", 4200);
      }
    }
  }

  function codecStrategy() {
    const raw = String((HUI_CFG && HUI_CFG.webcam_codec_strategy) || "prefer-compatible").toLowerCase().replace(/_/g, "-");
    if (raw === "prefer-efficient" || raw === "efficient") return "prefer-efficient";
    if (raw === "prefer-quality" || raw === "quality") return "prefer-quality";
    return "prefer-compatible";
  }

  function codecPreferenceList() {
    const p = profile();
    if (Array.isArray(p.preferred_codecs) && p.preferred_codecs.length) return p.preferred_codecs.map(x => String(x).toUpperCase());
    const strategy = codecStrategy();
    if (strategy === "prefer-efficient") return ["VP9", "AV1", "H264", "VP8"];
    if (strategy === "prefer-quality") return ["H264", "VP9", "AV1", "VP8"];
    return ["H264", "VP8", "VP9", "AV1"];
  }

  function sortedVideoCodecs() {
    let codecs = [];
    try {
      const caps = (RTCRtpReceiver.getCapabilities && RTCRtpReceiver.getCapabilities("video")) || null;
      codecs = Array.isArray(caps && caps.codecs) ? caps.codecs.slice() : [];
    } catch {}
    if (!codecs.length) return [];
    const wanted = codecPreferenceList();
    const score = (c) => {
      const mt = String(c.mimeType || "").toUpperCase();
      const idx = wanted.findIndex(w => mt.includes(w));
      return idx < 0 ? 999 : idx;
    };
    return codecs.sort((a, b) => score(a) - score(b));
  }

  function applyCodecPreference(transceiver) {
    if (!transceiver || !transceiver.setCodecPreferences) return;
    const list = sortedVideoCodecs();
    if (!list.length) return;
    try { transceiver.setCodecPreferences(list); } catch {}
  }

  function applySenderParams(sender) {
    if (!sender || !sender.getParameters) return;
    const p = profile();
    const bitrate = Number(p.max_bitrate || p.maxBitrate || 550000);
    const scale = Number(p.scaleResolutionDownBy || p.scale_resolution_down_by || 1);
    try {
      const params = sender.getParameters() || {};
      if (!params.encodings || !params.encodings.length) params.encodings = [{}];
      if (Number.isFinite(bitrate) && bitrate > 0) params.encodings[0].maxBitrate = bitrate;
      if (Number.isFinite(scale) && scale > 1) params.encodings[0].scaleResolutionDownBy = scale;
      params.degradationPreference = String(p.degradationPreference || p.degradation_preference || "balanced");
      if (sender.setParameters) sender.setParameters(params).catch(() => {});
    } catch {}
  }

  function huiCamApplyQualityToAllSenders() {
    try {
      if (!VOICE_STATE || !VOICE_STATE.room || !VOICE_STATE.room.peers) return;
      VOICE_STATE.room.peers.forEach((obj) => {
        if (!obj || !obj.pc) return;
        if (obj.huiVideoTransceiver) applyCodecPreference(obj.huiVideoTransceiver);
        if (obj.huiVideoSender) applySenderParams(obj.huiVideoSender);
      });
    } catch {}
  }

  function attachCameraToPeer(pc, obj, room, peer) {
    const stream = HUI_MEDIA.camStream;
    const track = stream && stream.getVideoTracks && stream.getVideoTracks()[0];
    if (!pc || !obj || !track) return null;
    if (!huiCamCanSendToPeer(room, peer)) return null;
    if (obj.huiVideoSender && obj.huiVideoSender.track === track) {
      applySenderParams(obj.huiVideoSender);
      return obj.huiVideoSender;
    }
    let transceiver = null;
    try {
      if (obj.huiVideoTransceiver && obj.huiVideoTransceiver.sender) {
        transceiver = obj.huiVideoTransceiver;
        obj.huiVideoSender = transceiver.sender;
        if (obj.huiVideoSender.replaceTrack) obj.huiVideoSender.replaceTrack(track).catch(() => {});
        try { transceiver.direction = "sendrecv"; } catch {}
        applyCodecPreference(transceiver);
      } else if (pc.addTransceiver) {
        transceiver = pc.addTransceiver(track, { direction: "sendrecv", streams: [stream] });
        applyCodecPreference(transceiver);
        obj.huiVideoTransceiver = transceiver;
        obj.huiVideoSender = transceiver.sender;
      } else {
        obj.huiVideoSender = pc.addTrack(track, stream);
      }
      applySenderParams(obj.huiVideoSender);
    } catch (e) {
      console.warn("camera attach failed", e);
    }
    return obj.huiVideoSender;
  }

  function attachCameraToApprovedPeers() {
    if (!HUI_MEDIA.camStream || !VOICE_STATE || !VOICE_STATE.room || !VOICE_STATE.room.peers) return;
    const room = localRoomName();
    VOICE_STATE.room.peers.forEach((obj, peer) => {
      if (huiCamCanSendToPeer(room, peer)) attachCameraToPeer(obj && obj.pc, obj, room, peer);
    });
  }

  function removeCameraFromPeer(obj) {
    if (!obj || !obj.pc) return;
    try {
      if (obj.huiVideoTransceiver) {
        if (obj.huiVideoSender && obj.huiVideoSender.replaceTrack) obj.huiVideoSender.replaceTrack(null).catch(() => {});
        try { obj.huiVideoTransceiver.direction = "inactive"; } catch {}
        return;
      }
      if (obj.huiVideoSender) obj.pc.removeTrack(obj.huiVideoSender);
    } catch {}
    obj.huiVideoSender = null;
    obj.huiVideoTransceiver = null;
  }

  function removeCameraFromAllPeers() {
    try {
      if (!VOICE_STATE || !VOICE_STATE.room || !VOICE_STATE.room.peers) return;
      VOICE_STATE.room.peers.forEach((obj) => {
        if (!obj || !obj.pc) return;
        removeCameraFromPeer(obj);
      });
    } catch {}
  }

  function setMicTracksEnabled(on) {
    try {
      const s = VOICE_STATE && VOICE_STATE.micStream;
      if (s) s.getAudioTracks().forEach(t => { t.enabled = !!on; });
    } catch {}
  }

  function huiCamDisable(reason = "Webcam disabled", opts = {}) {
    const room = localRoomName();
    HUI_MEDIA.camDesired = false;
    HUI_MEDIA.comboDesired = false;
    HUI_MEDIA.camEnabled = false;
    HUI_MEDIA.approvedViewers.clear();
    if (room) HUI_MEDIA.activeViewers.delete(room);
    huiCamClearIncomingRequestsForRoom(room);
    removeCameraFromAllPeers();
    if (HUI_MEDIA.camStream) {
      try { HUI_MEDIA.camStream.getTracks().forEach(t => t.stop()); } catch {}
    }
    HUI_MEDIA.camStream = null;
    if (HUI_MEDIA.localTile) {
      try { HUI_MEDIA.localTile._huiVideo.srcObject = null; } catch {}
      try { HUI_MEDIA.localTile.remove(); } catch {}
    }
    HUI_MEDIA.localTile = null;
    try { huiCamUpdateLocalViewerInfo(room); } catch {}
    try { voiceUpdateLocalMediaStatus(localRoomName(), { webcam_on: false, voice_on: !!HUI_MEDIA.voiceDesired }); } catch {}
    try { if (room) socket.emit("webcam_status", { room, camera_on: false }, () => {}); } catch {}
    try { voiceUpdateRoomCamButton(); } catch {}
    updateStatus(reason);
    if (!opts.keepRoom && !HUI_MEDIA.voiceDesired) {
      try { voiceLeaveRoom(reason, true); } catch {}
    }
    try { huiCamRefreshUiState(); } catch {}
    try { huiCamDestroyPanelIfIdle(); } catch {}
  }

  function huiCamStopViewing(owner, room = localRoomName()) {
    owner = String(owner || "").trim();
    room = String(room || localRoomName()).trim();
    if (!owner || !room) return { success: false, error: "missing_owner_or_room" };
    HUI_MEDIA.requestedViewers.delete(huiCamKey(room, owner));
    HUI_MEDIA.pendingViewRequests && HUI_MEDIA.pendingViewRequests.delete(huiCamKey(room, owner));
    huiCamRemoveRemoteVideo(owner);
    try { socket.emit("webcam_viewing", { room, owner, viewing: false }, () => {}); } catch {}
    updateStatus(`Stopped viewing ${owner}'s webcam`);
    return { success: true, owner, room };
  }

  function huiCamRefreshDiagnostics() {
    if (!HUI_MEDIA.diagnostics) return;
    const snap = snapshot();
    const browser = browserWebcamStatus();
    const cfg = webcamConfigStatus();
    const bits = [];
    bits.push(`Room: ${snap.huiRoom || "none"}`);
    bits.push(`Voice: ${snap.voiceDesired ? (snap.micEnabled ? "on" : "connecting") : "off"}`);
    bits.push(`Webcam: ${snap.camDesired ? (snap.camEnabled ? "on" : "connecting") : "off"}`);
    bits.push(`Quality: ${snap.lastCameraQuality || snap.quality || "balanced"}`);
    bits.push(`Viewers: ${snap.viewerCount || 0}`);
    if (!cfg.ok) bits.push(`Policy: ${cfg.reason}`);
    else if (!browser.ok) bits.push(`Browser: ${browser.reason}`);
    else if (snap.lastCameraError) bits.push(`Last camera error: ${snap.lastCameraError}`);
    HUI_MEDIA.diagnostics.textContent = bits.join(" • ");
  }

  function huiCamRefreshUiState() {
    try { voiceUpdateRoomVoiceButton(); } catch {}
    try { voiceUpdateRoomCamButton(); } catch {}
    try { huiCamRefreshDiagnostics(); } catch {}
    const room = localRoomName();
    huiSetButtonBusy($("btnRoomEmbedVoice"), huiMediaIsBusy("voice", room), "🎤 Voice…");
    huiSetButtonBusy($("btnRoomEmbedCam"), huiMediaIsBusy("cam", room), "📷 Webcam…");
    if (HUI_MEDIA.localTile && HUI_MEDIA.localTile._huiStopBtn) {
      HUI_MEDIA.localTile._huiStopBtn.disabled = huiMediaIsBusy("cam", room);
      HUI_MEDIA.localTile._huiStopBtn.classList.toggle("isBusy", huiMediaIsBusy("cam", room));
    }
    HUI_MEDIA.remoteTiles.forEach((tile, owner) => {
      if (!tile || !tile._huiStopBtn) return;
      const busy = huiMediaIsBusy("view", room, owner);
      tile._huiStopBtn.disabled = busy;
      tile._huiStopBtn.classList.toggle("isBusy", busy);
      tile._huiStopBtn.setAttribute("aria-busy", busy ? "true" : "false");
    });
  }

  async function ensureMediaRoom(room, opts = {}) {
    room = String(room || localRoomName()).trim();
    if (!room) throw new Error("Join a room first");
    HUI_MEDIA.huiRoom = room;
    const needAudio = opts.audio !== false;
    if (!VOICE_STATE.room.joined || VOICE_STATE.room.name !== room) {
      const res = await voiceJoinRoom(room, { silent: true, audio: needAudio, viewerOnly: opts.viewerOnly === true || !needAudio });
      if (!res || !res.success) throw new Error(res && res.error ? res.error : "Media room join failed");
    } else if (needAudio && !VOICE_STATE.micStream) {
      await voiceEnsureMic();
      try {
        VOICE_STATE.room.peers.forEach((obj) => {
          if (!obj || !obj.pc || !VOICE_STATE.micStream) return;
          const hasAudio = obj.pc.getSenders && obj.pc.getSenders().some(s => s && s.track && s.track.kind === "audio");
          if (!hasAudio) VOICE_STATE.micStream.getTracks().forEach(t => obj.pc.addTrack(t, VOICE_STATE.micStream));
          voiceApplySenderQuality(obj.pc);
        });
      } catch {}
    }
    return room;
  }

  async function toggleVoiceForRoom(room) {
    room = String(room || localRoomName()).trim();
    if (!room) throw new Error("Join a room first");
    if (huiMediaIsBusy("voice", room)) return { success: false, busy: true };
    return huiMediaWithBusy("voice", room, "", async () => {
    if (HUI_MEDIA.voiceDesired && HUI_MEDIA.huiRoom === room) {
      HUI_MEDIA.voiceDesired = false;
      HUI_MEDIA.micEnabled = false;
      try { VOICE_STATE.room.wantRoomVoice = false; } catch {}
      try { sessionStorage.removeItem("hui_voice_desired"); } catch {}
      try { voiceRemoveRoomAudioSenders(); } catch {}
      setMicTracksEnabled(false);
      try { voiceStopMicOnly(); } catch { try { voiceSetMute(true); } catch {} }
      try { voiceUpdateLocalMediaStatus(room, { voice_on: false, webcam_on: !!HUI_MEDIA.camDesired }); } catch {}
      if (!HUI_MEDIA.camDesired) {
        try { voiceLeaveRoom("Voice disabled", true); } catch {}
      } else {
        safeToast("🔇 Voice disabled; webcam still on", "info", 2200);
      }
      try { voiceUpdateRoomVoiceButton(); } catch {}
      return { success: true, voice: false, webcam: !!HUI_MEDIA.camDesired };
    }
    await ensureMediaRoom(room, { audio: true });
    HUI_MEDIA.voiceDesired = true;
    HUI_MEDIA.micEnabled = true;
    HUI_MEDIA.huiRoom = room;
    setMicTracksEnabled(true);
    try { voiceSetMute(false); voiceApplyTalkMode({ silent: true }); } catch {}
    try { voiceUpdateLocalMediaStatus(room, { voice_on: true, webcam_on: !!HUI_MEDIA.camDesired }); } catch {}
    try { voiceUpdateRoomVoiceButton(); } catch {}
    safeToast("🎤 Voice connected", "info", 1600);
    return { success: true, voice: true, webcam: !!HUI_MEDIA.camDesired };
    });
  }

  async function toggleCamForRoom(room) {
    room = String(room || localRoomName()).trim();
    if (!room) throw new Error("Join a room first");
    if (huiMediaIsBusy("cam", room)) return { success: false, busy: true };
    return huiMediaWithBusy("cam", room, "", async () => {
    if (HUI_MEDIA.camDesired && HUI_MEDIA.huiRoom === room) {
      huiCamDisable("Webcam disabled", { keepRoom: !!HUI_MEDIA.voiceDesired });
      safeToast("📷 Webcam disabled", "info", 1600);
      return { success: true, webcam: false, voice: !!HUI_MEDIA.voiceDesired };
    }
    await ensureMediaRoom(room, { audio: !!HUI_MEDIA.voiceDesired });
    // A webcam-only click must not turn on voice, store voice reconnect flags,
    // or request a microphone. Voice is enabled only by the Voice button.
    if (!HUI_MEDIA.voiceDesired) {
      try { VOICE_STATE.room.wantRoomVoice = false; } catch {}
      try { sessionStorage.removeItem("hui_voice_desired"); } catch {}
      try { voiceUpdateLocalMediaStatus(room, { voice_on: false, webcam_on: !!HUI_MEDIA.camDesired }); } catch {}
    }
    try {
      await ensureCamera();
    } catch (err) {
      const reason = describeCameraError(err);
      HUI_MEDIA.lastCameraError = reason;
      huiCamDisable(reason, { keepRoom: !!HUI_MEDIA.voiceDesired });
      throw new Error(reason);
    }
    HUI_MEDIA.camDesired = true;
    HUI_MEDIA.camEnabled = true;
    HUI_MEDIA.huiRoom = room;
    attachCameraToApprovedPeers();
    try { socket.emit("webcam_status", { room, camera_on: true }, () => {}); } catch {}
    try { voiceUpdateLocalMediaStatus(room, { webcam_on: true, voice_on: !!HUI_MEDIA.voiceDesired }); } catch {}
    try { voiceUpdateRoomCamButton(); } catch {}
    updateStatus(`Webcam on · ${profile().label || HUI_MEDIA.quality}`);
    safeToast("📷 Webcam enabled", "info", 1600);
    return { success: true, webcam: true, voice: !!HUI_MEDIA.voiceDesired };
    });
  }

  async function toggleBothForRoom(room) {
    room = String(room || localRoomName()).trim();
    if (!room) throw new Error("Join a room first");
    if (huiMediaIsBusy("both", room)) return { success: false, busy: true };
    return huiMediaWithBusy("both", room, "", async () => {
    if (HUI_MEDIA.voiceDesired && HUI_MEDIA.camDesired && HUI_MEDIA.huiRoom === room) {
      await leave("Voice/webcam disabled");
      return { success: true, voice: false, webcam: false };
    }
    await ensureMediaRoom(room, { audio: true });
    HUI_MEDIA.voiceDesired = true;
    HUI_MEDIA.micEnabled = true;
    setMicTracksEnabled(true);
    try {
      await ensureCamera();
    } catch (err) {
      const reason = describeCameraError(err);
      HUI_MEDIA.lastCameraError = reason;
      huiCamDisable(reason, { keepRoom: !!HUI_MEDIA.voiceDesired });
      throw new Error(reason);
    }
    HUI_MEDIA.camDesired = true;
    HUI_MEDIA.camEnabled = true;
    HUI_MEDIA.comboDesired = true;
    attachCameraToApprovedPeers();
    try { socket.emit("webcam_status", { room, camera_on: true }, () => {}); } catch {}
    try { voiceUpdateLocalMediaStatus(room, { voice_on: true, webcam_on: true }); } catch {}
    try { voiceUpdateRoomVoiceButton(); voiceUpdateRoomCamButton(); } catch {}
    updateStatus(`Voice + webcam · ${profile().label || HUI_MEDIA.quality}`);
    return { success: true, voice: true, webcam: true };
    });
  }

  async function toggleMic() {
    const room = localRoomName();
    if (huiMediaIsBusy("mic", room)) return { success: false, busy: true };
    return huiMediaWithBusy("mic", room, "", async () => {
    if (!HUI_MEDIA.voiceDesired && !(VOICE_STATE && VOICE_STATE.micStream)) return null;
    const muted = !VOICE_STATE.micMuted;
    try { voiceSetMute(muted); } catch {}
    HUI_MEDIA.micEnabled = !muted;
    safeToast(muted ? "🔇 Mic muted" : "🎤 Mic unmuted", "info", 1600);
    return { success: true, muted };
    });
  }

  async function toggleCam() {
    const room = localRoomName();
    return toggleCamForRoom(room);
  }

  async function switchRoomIfMediaDesired(room) {
    room = String(room || "").trim();
    if (!room) return null;
    const wantVoice = !!HUI_MEDIA.voiceDesired;
    const wantCam = !!HUI_MEDIA.camDesired;
    if (!wantVoice && !wantCam) return null;
    const previousRoom = String(HUI_MEDIA.huiRoom || (VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.name) || "").trim();
    const switchingRooms = !!(previousRoom && previousRoom !== room);
    const oldCam = HUI_MEDIA.camStream;
    if (switchingRooms) {
      // Clear old-room webcam permissions/status before moving the active camera
      // to the new room.  Without this, old-room users can keep seeing stale
      // webcam-on badges and previously approved viewers can be reused.
      try { if (wantCam) socket.emit("webcam_status", { room: previousRoom, camera_on: false }, () => {}); } catch {}
      try { voiceUpdateLocalMediaStatus(previousRoom, { voice_on: false, webcam_on: false }); } catch {}
      try { huiCamClearIncomingRequestsForRoom(previousRoom); } catch {}
      HUI_MEDIA.approvedViewers.clear();
      HUI_MEDIA.requestedViewers.clear();
      clearRemoteVideos();
    }
    await ensureMediaRoom(room, { audio: wantVoice });
    HUI_MEDIA.huiRoom = room;
    if (wantCam && oldCam) {
      attachCameraToApprovedPeers();
      try { socket.emit("webcam_status", { room, camera_on: true }, () => {}); } catch {}
    }
    try { voiceUpdateLocalMediaStatus(room, { voice_on: wantVoice, webcam_on: wantCam }); } catch {}
    return { success: true, room, voice: wantVoice, webcam: wantCam };
  }

  async function leave(reason = "Media disabled", opts = {}) {
    HUI_MEDIA.voiceDesired = !!(opts && opts.preserveDesired && HUI_MEDIA.voiceDesired);
    HUI_MEDIA.micEnabled = false;
    huiCamDisable(reason, { keepRoom: false });
    if (!opts || !opts.preserveDesired) HUI_MEDIA.voiceDesired = false;
    try { voiceLeaveRoom(reason, true, { silent: !!(opts && opts.silent) }); } catch {}
    clearRemoteVideos();
    try { huiCamDestroyPanelIfIdle({ force: true }); } catch {}
    return { success: true };
  }


  function huiSocketAck(event, payload) {
    return new Promise((resolve) => {
      try {
        if (!socket || !socket.emit) return resolve({ success: false, error: "Socket is not connected" });
        socket.emit(event, payload || {}, (ack) => resolve(ack || {}));
      } catch (err) {
        resolve({ success: false, error: err && err.message ? err.message : String(err || "socket_error") });
      }
    });
  }

  function huiCamOwnerHasWebcamOn(owner, room) {
    owner = String(owner || "").trim();
    room = String(room || localRoomName()).trim();
    if (!owner || owner === String(currentUser || "").trim() || !room) return false;
    try {
      if (typeof voiceStatusForUser !== "function") return true;
      const st = voiceStatusForUser(owner, room);
      return !!(st && st.webcam_on);
    } catch {
      return false;
    }
  }

  async function huiJoinMediaRoomForViewing(owner, room) {
    owner = String(owner || "").trim();
    room = String(room || localRoomName()).trim();
    if (!owner || !room) return { success: false, error: "Missing webcam owner or room" };
    const alreadyInRoom = !!(VOICE_STATE.room.joined && VOICE_STATE.room.name === room);
    const preserveVoice = alreadyInRoom && !!(HUI_MEDIA.voiceDesired || VOICE_STATE.room.wantRoomVoice);
    if (!alreadyInRoom) {
      const res = await voiceJoinRoom(room, { silent: true, audio: false, viewerOnly: true });
      if (!res || !res.success) return res || { success: false, error: "Media room join failed" };
    }
    HUI_MEDIA.huiRoom = room;
    HUI_MEDIA.requestedViewers.add(huiCamKey(room, owner));
    if (!preserveVoice) {
      try { VOICE_STATE.room.wantRoomVoice = false; } catch {}
      try { sessionStorage.removeItem("hui_voice_desired"); } catch {}
      try { voiceUpdateLocalMediaStatus(room, { voice_on: false, webcam_on: !!HUI_MEDIA.camDesired }); } catch {}
    }
    try {
      if (!VOICE_STATE.room.peers.has(owner) && typeof voiceRoomEnsurePeer === "function") voiceRoomEnsurePeer(room, owner);
    } catch {}
    showPanel();
    updateStatus(`Opening ${owner}'s webcam…`);
    try { socket.emit("webcam_viewing", { room, owner, viewing: true }, () => {}); } catch {}
    return { success: true, owner, room };
  }

  async function huiRequestRemoteCamFromRoomUser(owner, roomName) {
    owner = String(owner || "").trim();
    const room = String(roomName || localRoomName()).trim();
    if (huiMediaIsBusy("view", room, owner)) return { success: false, busy: true };
    return huiMediaWithBusy("view", room, owner, async () => {
    if (!owner || !room) {
      safeToast("📷 Join a room before viewing webcams.", "warn");
      return { success: false, error: "missing_room_or_owner" };
    }
    if (owner === String(currentUser || "").trim()) {
      safeToast("📷 Use the webcam button to preview your own camera.", "info");
      return { success: false, error: "self_webcam" };
    }
    if (!huiCamOwnerHasWebcamOn(owner, room)) {
      safeToast(`${owner} does not have webcam on right now.`, "warn", 2600);
      return { success: false, error: "webcam_off" };
    }

    safeToast(`📷 Requesting ${owner}'s webcam…`, "info", 1800);
    const ack = await huiSocketAck("webcam_view_request", { room, owner });
    if (!ack || !ack.success) {
      safeToast(`❌ Webcam request failed: ${ack && ack.error ? ack.error : "not delivered"}`, "error", 5000);
      return ack || { success: false, error: "webcam_request_failed" };
    }
    if (ack.allowed || ack.auto_allowed || (ack.policy && ack.policy.webcam_approval_mode === "open")) {
      const opened = await huiJoinMediaRoomForViewing(owner, room);
      if (opened && opened.success) safeToast(`📷 Viewing ${owner}'s webcam`, "ok", 2200);
      return { ...ack, opened: !!(opened && opened.success) };
    }
    safeToast(`📷 Requested ${owner}'s webcam. Waiting for approval.`, "info", 3600);
    return ack;
    });
  }

  async function huiRespondToCamViewRequest(room, viewer, allowed) {
    room = String(room || localRoomName()).trim();
    viewer = String(viewer || "").trim();
    if (!room || !viewer || viewer === String(currentUser || "").trim()) return;
    if (huiMediaIsBusy("respond", room, viewer)) return { success: false, busy: true };
    return huiMediaWithBusy("respond", room, viewer, async () => {
    const ack = await huiSocketAck("webcam_view_response", { room, viewer, allowed: !!allowed });
    if (!ack || !ack.success) {
      safeToast(`❌ Webcam response failed: ${ack && ack.error ? ack.error : "not delivered"}`, "error", 4500);
      return ack;
    }
    huiCamRemoveIncomingRequest(room, viewer);
    huiCamSetViewerApproved(room, viewer, !!allowed);
    if (Array.isArray(ack.viewers)) huiCamReplaceActiveViewers(room, ack.viewers);
    if (allowed) {
      try {
        const obj = VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.peers && VOICE_STATE.room.peers.get(viewer);
        if (obj) attachCameraToPeer(obj.pc, obj, room, viewer);
      } catch {}
    } else {
      try {
        const obj = VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.peers && VOICE_STATE.room.peers.get(viewer);
        if (obj) removeCameraFromPeer(obj);
      } catch {}
    }
    safeToast(allowed ? `📷 Allowed ${viewer} to view your webcam` : `📷 Denied ${viewer}'s webcam request`, allowed ? "ok" : "info", 2600);
    return ack;
    });
  }

  function wireHuiWebcamViewEvents() {
    if (!socket || socket._huiWebcamViewEventsBound) return;
    socket._huiWebcamViewEventsBound = true;

    socket.on("webcam_view_request", (payload = {}) => {
      const room = String(payload.room || localRoomName()).trim();
      const viewer = String(payload.viewer || "").trim();
      if (!room || !viewer || viewer === String(currentUser || "").trim()) return;
      const localCamOn = !!(HUI_MEDIA.camDesired || HUI_MEDIA.camEnabled);
      if (!localCamOn) {
        huiRespondToCamViewRequest(room, viewer, false);
        return;
      }
      huiCamUpsertIncomingRequest(room, viewer, payload.policy || null);
      safeToast(`📷 Webcam request from ${viewer} is waiting in Alerts.`, "info", 5000);
      try { if (typeof maybeBrowserNotify === "function") maybeBrowserNotify("Webcam request", `${viewer} wants to view your webcam in ${room}`); } catch {}
    });

    socket.on("webcam_view_response", async (payload = {}) => {
      const room = String(payload.room || localRoomName()).trim();
      const owner = String(payload.owner || "").trim();
      if (!room || !owner || owner === String(currentUser || "").trim()) return;
      if (Array.isArray(payload.viewers) && owner === String(currentUser || "").trim()) huiCamReplaceActiveViewers(room, payload.viewers);
      if (payload.allowed) {
        const opened = await huiJoinMediaRoomForViewing(owner, room);
        if (opened && opened.success) safeToast(`📷 Viewing ${owner}'s webcam`, "ok", 2200);
      } else {
        HUI_MEDIA.requestedViewers.delete(huiCamKey(room, owner));
        huiCamRemoveRemoteVideo(owner);
        safeToast(`📷 ${owner} denied the webcam request.`, "warn", 3200);
      }
      try { huiCamRefreshUiState(); } catch {}
    });

    socket.on("webcam_view_kick", (payload = {}) => {
      const owner = String(payload.owner || "").trim();
      if (Array.isArray(payload.viewers) && owner === String(currentUser || "").trim()) huiCamReplaceActiveViewers(String(payload.room || localRoomName()).trim(), payload.viewers);
      if (owner) huiCamRemoveRemoteVideo(owner);
      safeToast(owner ? `📷 ${owner} stopped your webcam view.` : "📷 Webcam view stopped.", "info", 3200);
    });


    socket.on("webcam_viewing", (payload = {}) => {
      const room = String(payload.room || localRoomName()).trim();
      const viewer = String(payload.viewer || "").trim();
      const viewing = payload.viewing !== false && String(payload.viewing).toLowerCase() !== "false";
      if (!room || !viewer || viewer === String(currentUser || "").trim()) return;
      if (Array.isArray(payload.viewers)) huiCamReplaceActiveViewers(room, payload.viewers);
      else huiCamSetActiveViewing(room, viewer, viewing);
      if (viewing) huiCamRemoveIncomingRequest(room, viewer);
      huiCamSetViewerApproved(room, viewer, viewing);
      try {
        const obj = VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.peers && VOICE_STATE.room.peers.get(viewer);
        if (viewing) {
          if (!obj && VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.joined && VOICE_STATE.room.name === room && typeof voiceRoomEnsurePeer === "function") voiceRoomEnsurePeer(room, viewer);
          const nextObj = VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.peers && VOICE_STATE.room.peers.get(viewer);
          if (nextObj) attachCameraToPeer(nextObj.pc, nextObj, room, viewer);
        } else if (obj) {
          removeCameraFromPeer(obj);
        }
      } catch {}
    });

    socket.on("webcam_status", (payload = {}) => {
      const owner = String(payload.owner || "").trim();
      const room = String(payload.room || localRoomName()).trim();
      if (Array.isArray(payload.viewers) && owner === String(currentUser || "").trim()) huiCamReplaceActiveViewers(room, payload.viewers);
      if (owner && payload.camera_on === false) {
        HUI_MEDIA.requestedViewers.delete(huiCamKey(room, owner));
        huiCamRemoveRemoteVideo(owner);
        updateStatus(owner === String(currentUser || "").trim() ? "Your webcam is off" : `${owner}'s webcam is off`);
      }
      try { huiCamRefreshUiState(); } catch {}
    });
  }

  function snapshot() {
    return {
      engine: "hui",
      connected: !!(VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.joined),
      huiRoom: localRoomName(),
      voiceDesired: !!HUI_MEDIA.voiceDesired,
      camDesired: !!HUI_MEDIA.camDesired,
      comboDesired: !!HUI_MEDIA.comboDesired,
      micEnabled: !!(HUI_MEDIA.voiceDesired && !VOICE_STATE.micMuted),
      camEnabled: !!HUI_MEDIA.camEnabled,
      quality: HUI_MEDIA.quality,
      lastCameraQuality: HUI_MEDIA.lastCameraQuality || "",
      viewers: huiCamViewerSummary(localRoomName()).viewers,
      viewerCount: huiCamViewerSummary(localRoomName()).viewerCount,
      lastCameraError: HUI_MEDIA.lastCameraError || "",
    };
  }

  async function refreshModeFromServer() {
    let resp = null;
    let j = {};
    try {
      resp = await (typeof fetchWithAuth === "function" ? fetchWithAuth("/api/av/mode") : fetch("/api/av/mode"));
      j = typeof ecReadApiJson === "function" ? await ecReadApiJson(resp) : await resp.json().catch(() => ({}));
      if (resp && !resp.ok && typeof ecApiErrorMessage === "function") {
        throw new Error(ecApiErrorMessage(resp, j, 'Media mode request failed'));
      }
      if (j && j.client_config && j.client_config.webcam_quality) saveQuality(j.client_config.webcam_quality);
      else if (j && j.webcam_quality) saveQuality(j.webcam_quality);
      return j;
    } catch (err) {
      if (typeof ecApiErrorMessage === "function" && resp) {
        console.warn(ecApiErrorMessage(resp, j, 'Media mode request failed'));
      }
      return { ok: true, av_mode: "hui", reason: "offline_client_default", error: err && err.message ? err.message : String(err || "") };
    }
  }

  wireHuiWebcamViewEvents();

  window.huiCamAttachRemoteVideo = huiCamAttachRemoteVideo;
  window.huiCamRemoveRemoteVideo = huiCamRemoveRemoteVideo;
  window.huiCamAttachTrackToPeer = attachCameraToPeer;
  window.huiCamCanReceiveFromPeer = huiCamCanReceiveFromPeer;
  window.huiCamCanSendToPeer = huiCamCanSendToPeer;
  window.huiCamSetViewerApproved = huiCamSetViewerApproved;
  window.huiCamSetActiveViewing = huiCamSetActiveViewing;
  window.huiCamReplaceActiveViewers = huiCamReplaceActiveViewers;
  window.huiCamViewerSummary = huiCamViewerSummary;
  window.huiCamUpsertIncomingRequest = huiCamUpsertIncomingRequest;
  window.huiCamRemoveIncomingRequest = huiCamRemoveIncomingRequest;
  window.huiRespondToCamViewRequest = huiRespondToCamViewRequest;
  window.huiCamApplyQualityToAllSenders = huiCamApplyQualityToAllSenders;
  window.huiCamApplyQualityToLocalTrack = huiCamApplyQualityToLocalTrack;
  window.huiCamDisable = huiCamDisable;
  window.huiCamStopViewing = huiCamStopViewing;
  window.huiCamRefreshUiState = huiCamRefreshUiState;
  window.huiMediaIsBusy = huiMediaIsBusy;
  window.huiRequestRemoteCamFromRoomUser = huiRequestRemoteCamFromRoomUser;

  try {
    if (typeof ecRegisterMediaEngine === "function") {
      ecRegisterMediaEngine({
        id: "hui",
        label: "Hui built-in media",
        ready,
        webcamAvailable,
        webcamUnavailableReason,
        refreshModeFromServer,
        snapshot,
        toggleVoiceForRoom,
        toggleCamForRoom,
        toggleBothForRoom,
        toggleMic,
        toggleCam,
        switchRoomIfMediaDesired,
        leave,
        isConnectedToRoom: (room) => String(localRoomName()) === String(room || "") && !!(VOICE_STATE && VOICE_STATE.room && VOICE_STATE.room.joined),
      }, { active: true });
      if (typeof ecMediaSetActive === "function") ecMediaSetActive("hui");
    }
  } catch (e) {
    console.warn("Hui media engine registration failed", e);
  }
})();
