// Hui Chat media engine adapter layer
// ───────────────────────────────────────────────────────────────────────────────
// The room UI should call ecMedia* helpers instead of directly depending on a
// specific media backend. Hui built-in WebRTC is the default engine today; this
// registry keeps the UI contract stable for Hui Chat's built-in media
// controls and any future backend that follows the same browser contract.
(function () {
  const engines = new Map();
  const state = { active: "" };

  function noopAsyncResult() { return Promise.resolve(null); }
  function normalizeEngine(engine) {
    if (!engine || typeof engine !== "object") throw new Error("media engine must be an object");
    const id = String(engine.id || engine.name || "").trim().toLowerCase();
    if (!id) throw new Error("media engine id missing");
    return {
      id,
      label: String(engine.label || id),
      ready: typeof engine.ready === "function" ? engine.ready : () => false,
      webcamAvailable: typeof engine.webcamAvailable === "function" ? engine.webcamAvailable : () => false,
      webcamUnavailableReason: typeof engine.webcamUnavailableReason === "function" ? engine.webcamUnavailableReason : () => "Webcam is not available.",
      refreshModeFromServer: typeof engine.refreshModeFromServer === "function" ? engine.refreshModeFromServer : noopAsyncResult,
      snapshot: typeof engine.snapshot === "function" ? engine.snapshot : () => ({}),
      toggleVoiceForRoom: typeof engine.toggleVoiceForRoom === "function" ? engine.toggleVoiceForRoom : noopAsyncResult,
      toggleCamForRoom: typeof engine.toggleCamForRoom === "function" ? engine.toggleCamForRoom : noopAsyncResult,
      toggleBothForRoom: typeof engine.toggleBothForRoom === "function" ? engine.toggleBothForRoom : noopAsyncResult,
      toggleMic: typeof engine.toggleMic === "function" ? engine.toggleMic : noopAsyncResult,
      toggleCam: typeof engine.toggleCam === "function" ? engine.toggleCam : noopAsyncResult,
      switchRoomIfMediaDesired: typeof engine.switchRoomIfMediaDesired === "function" ? engine.switchRoomIfMediaDesired : noopAsyncResult,
      leave: typeof engine.leave === "function" ? engine.leave : noopAsyncResult,
      isConnectedToRoom: typeof engine.isConnectedToRoom === "function" ? engine.isConnectedToRoom : () => false,
    };
  }

  function register(engine, opts = {}) {
    const normalized = normalizeEngine(engine);
    engines.set(normalized.id, normalized);
    if (opts.active || !state.active) state.active = normalized.id;
    return normalized;
  }

  function setActive(id) {
    const key = String(id || "").trim().toLowerCase();
    if (!key) {
      state.active = "";
      return null;
    }
    if (engines.has(key)) {
      state.active = key;
      return engines.get(key);
    }
    state.active = key;
    return null;
  }

  function active() {
    return engines.get(state.active) || null;
  }

  function activeOrNull() {
    const engine = active();
    return engine && engine.ready() ? engine : null;
  }

  window.HuiMedia = {
    register,
    setActive,
    active,
    activeId: () => state.active || "",
    engines: () => Array.from(engines.keys()),
  };

  window.ecRegisterMediaEngine = register;
  window.ecMediaSetActive = setActive;
  window.ecMediaActiveEngine = active;
  window.ecMediaActiveEngineId = () => state.active || "";
  window.ecMediaModeReady = () => !!activeOrNull();
  window.ecMediaWebcamAvailable = () => !!(activeOrNull() && activeOrNull().webcamAvailable());
  window.ecMediaWebcamUnavailableReason = () => {
    const engine = active() || activeOrNull();
    return engine && typeof engine.webcamUnavailableReason === "function" ? engine.webcamUnavailableReason() : "Webcam is not available.";
  };
  window.ecMediaRefreshModeFromServer = async () => {
    const engine = active() || activeOrNull();
    return engine ? engine.refreshModeFromServer() : null;
  };
  window.ecMediaStateSnapshot = () => {
    const engine = active();
    const snap = engine ? engine.snapshot() : {};
    return Object.assign({
      engine: state.active || "none",
      connected: false,
      huiRoom: "",
      voiceDesired: false,
      camDesired: false,
      comboDesired: false,
      micEnabled: false,
      camEnabled: false,
    }, snap || {});
  };
  window.ecMediaToggleVoiceForRoom = async (room) => {
    const engine = activeOrNull();
    if (!engine) throw new Error("Voice is not ready yet");
    return engine.toggleVoiceForRoom(room);
  };
  window.ecMediaToggleCamForRoom = async (room) => {
    const engine = activeOrNull();
    if (!engine) throw new Error("Webcam is not ready yet");
    return engine.toggleCamForRoom(room);
  };
  window.ecMediaToggleBothForRoom = async (room) => {
    const engine = activeOrNull();
    if (!engine) throw new Error("Voice and webcam are not ready yet");
    return engine.toggleBothForRoom(room);
  };
  window.ecMediaToggleMic = async () => {
    const engine = activeOrNull();
    if (!engine) throw new Error("Voice is not ready yet");
    return engine.toggleMic();
  };
  window.ecMediaToggleCam = async () => {
    const engine = activeOrNull();
    if (!engine) throw new Error("Webcam is not ready yet");
    return engine.toggleCam();
  };
  window.ecMediaSwitchRoomIfDesired = async (room) => {
    const engine = activeOrNull();
    return engine ? engine.switchRoomIfMediaDesired(room) : null;
  };
  window.ecMediaLeave = async (reason, opts = {}) => {
    const engine = active();
    return engine ? engine.leave(reason, opts) : null;
  };
  window.ecMediaIsConnectedToRoom = (room) => {
    const engine = active();
    return !!(engine && engine.isConnectedToRoom(room));
  };
})();
