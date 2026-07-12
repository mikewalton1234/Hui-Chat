// Lightweight boot/runtime JavaScript diagnostics. Keep errors recorded for
// browser-console debugging, but do not paint an in-page red warning bar.
window.HUI_JS_ERRORS = Array.isArray(window.HUI_JS_ERRORS) ? window.HUI_JS_ERRORS : [];
function huiChatShowJsHealthWarning(message) {
  try {
    if (window.HUI_CFG && window.HUI_CFG.debug_js_health_overlay === true) {
      console.warn("[Hui Chat JS]", message);
    }
  } catch {}
}
function huiChatRecordJsError(kind, error) {
  try {
    const msg = String((error && (error.message || error.reason || error.toString())) || error || kind || "unknown error");
    window.HUI_JS_ERRORS.push({ kind, message: msg, at: new Date().toISOString() });
    if (window.HUI_JS_ERRORS.length > 20) window.HUI_JS_ERRORS.shift();
    huiChatShowJsHealthWarning(msg);
  } catch {}
}
window.addEventListener("error", (event) => {
  try { huiChatRecordJsError("error", event.error || event.message || "script error"); } catch {}
});
window.addEventListener("unhandledrejection", (event) => {
  try { huiChatRecordJsError("unhandledrejection", event.reason || "unhandled promise rejection"); } catch {}
});
const __socketIoAvailable = typeof window.io === "function";
if (!__socketIoAvailable) {
  huiChatRecordJsError("boot", "Socket.IO client library did not load; continuing with limited HTTP-only GUI bootstrap.");
  window.HUI_SOCKETIO_CLIENT_MISSING = true;
}

function ecCreateSocketFallback(reason = "socketio_client_missing") {
  const handlers = new Map();
  const managerHandlers = new Map();

  function addHandler(store, eventName, fn, once = false) {
    if (typeof fn !== "function") return;
    const key = String(eventName || "");
    const list = store.get(key) || [];
    list.push({ fn, once: !!once });
    store.set(key, list);
  }

  function removeHandler(store, eventName, fn) {
    if (!eventName) {
      store.clear();
      return;
    }
    const key = String(eventName || "");
    if (!store.has(key)) return;
    if (typeof fn !== "function") {
      store.delete(key);
      return;
    }
    const next = (store.get(key) || []).filter((entry) => entry.fn !== fn);
    if (next.length) store.set(key, next);
    else store.delete(key);
  }

  function fire(store, eventName, ...args) {
    const key = String(eventName || "");
    const list = (store.get(key) || []).slice();
    if (!list.length) return;
    const keep = [];
    for (const entry of list) {
      try { entry.fn(...args); } catch (e) { huiChatRecordJsError("socket_fallback_handler", e); }
      if (!entry.once) keep.push(entry);
    }
    if (keep.length) store.set(key, keep);
    else store.delete(key);
  }

  const manager = {
    opts: {},
    on(eventName, fn) { addHandler(managerHandlers, eventName, fn, false); return manager; },
    once(eventName, fn) { addHandler(managerHandlers, eventName, fn, true); return manager; },
    off(eventName, fn) { removeHandler(managerHandlers, eventName, fn); return manager; },
  };

  const sock = {
    connected: false,
    disconnected: true,
    active: false,
    io: manager,
    on(eventName, fn) { addHandler(handlers, eventName, fn, false); return sock; },
    once(eventName, fn) { addHandler(handlers, eventName, fn, true); return sock; },
    off(eventName, fn) { removeHandler(handlers, eventName, fn); return sock; },
    emit(eventName, ...args) {
      const ack = args.length && typeof args[args.length - 1] === "function" ? args[args.length - 1] : null;
      if (ack) {
        setTimeout(() => {
          try { ack({ success: false, error: reason, event: String(eventName || "") }); } catch {}
        }, 0);
      }
      return sock;
    },
    connect() {
      setTimeout(() => {
        const err = new Error("Socket.IO client library is missing");
        err.code = reason;
        fire(handlers, "connect_error", err);
        fire(managerHandlers, "reconnect_error", err);
      }, 0);
      return sock;
    },
    disconnect() { sock.connected = false; sock.disconnected = true; return sock; },
    close() { return sock.disconnect(); },
  };
  return sock;
}

const __wsEnabled = !!(window.HUI_CFG && window.HUI_CFG.ws_enabled);
const __socketTransports = Array.isArray(window.HUI_CFG && window.HUI_CFG.socketio_transports) ? window.HUI_CFG.socketio_transports : (__wsEnabled ? ["websocket", "polling"] : ["polling"]);
const __socketWebsocketOnly = !!(window.HUI_CFG && window.HUI_CFG.socketio_websocket_only);
const HUI_CFG = (window.HUI_CFG && typeof window.HUI_CFG === "object") ? window.HUI_CFG : {};
const SERVER_NAME = String(HUI_CFG.server_name || "Hui Chat").trim() || "Hui Chat";
const SERVER_ADMIN_NAME = `${SERVER_NAME} Admin`;
const serverRoomLabel = () => `${SERVER_NAME} room`;

const socket = __socketIoAvailable ? window.io({
  transports: __socketTransports,
  upgrade: __wsEnabled && !__socketWebsocketOnly,
  rememberUpgrade: __wsEnabled,
  withCredentials: true,

  // Connection resilience:
  // - Keep trying to reconnect on transient network/server restarts.
  // - We only send the user back to /login on *auth* failures (refresh/token invalid),
  //   or explicit server-side logout events.
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 600,
  reconnectionDelayMax: 6000,
  timeout: 20000,

  // Don't auto-connect; we first try to refresh the short-lived access token.
  autoConnect: false
}) : ecCreateSocketFallback("socketio_client_missing");

try {
  window.socket = socket;
  window.HUI_SOCKET = socket;
  window.HUI_SOCKETIO_AVAILABLE = __socketIoAvailable;
} catch {}

// When the server rejects Socket.IO events because the access JWT expired,
// we try a silent refresh + reconnect. This flag suppresses the generic
// "disconnect → redirect to login" path during that recovery.
let AUTH_RECOVERY_IN_PROGRESS = false;

// Auth/session recovery state:
// - When access expires we try a bounded refresh-with-backoff.
// - If that still fails (or we get repeat 401 after refresh), we PAUSE polling
//   and show a banner prompting the user to Retry (manual recovery) or Logout.
let AUTH_EXPIRED = false;
let AUTH_BOOTSTRAP_PENDING = true;
let AUTH_FAIL_REASON = "";
let AUTH_RECOVERY_ATTEMPTS = 0;
let _authRecoveryPromise = null;

// Timers that should stop during auth-expired mode
let EC_TOKEN_KEEPALIVE_TIMER = null;

function enterAuthExpiredState(reason = "auth_required") {
  AUTH_EXPIRED = true;
  AUTH_FAIL_REASON = String(reason || "auth_required");
  AUTH_RECOVERY_ATTEMPTS = 0;

  // Stop any periodic network traffic
  try { if (EC_TOKEN_KEEPALIVE_TIMER) { clearInterval(EC_TOKEN_KEEPALIVE_TIMER); EC_TOKEN_KEEPALIVE_TIMER = null; } } catch {}
  try { if (typeof rbStopPolling === "function") rbStopPolling(); } catch {}

  // Drop socket to prevent reconnect/polling spam
  try { if (socket && socket.connected) socket.disconnect(); } catch {}

  // Show banner with Retry + Logout (no redirect)
  setConnBanner("auth_expired", "🔒 Session expired. Click Retry to re-authenticate.", { spinner: false, showRetry: true });
}

async function refreshAccessTokenWithBackoff(maxAttempts = 3) {
  // De-dupe concurrent recovery attempts
  if (_authRecoveryPromise) return _authRecoveryPromise;

  _authRecoveryPromise = (async () => {
    const delays = [250, 750, 2000]; // ms
    let lastErr = null;

    for (let i = 0; i < Math.max(1, Number(maxAttempts || 1)); i++) {
      AUTH_RECOVERY_ATTEMPTS = i + 1;
      try {
        await refreshAccessToken();
        return true;
      } catch (e) {
        lastErr = e;
        // If offline, don't keep hammering.
        if (navigator && navigator.onLine === false) break;
        const d = delays[Math.min(i, delays.length - 1)];
        await new Promise(r => setTimeout(r, d));
      }
    }
    const msg = (lastErr && (lastErr.message || lastErr.toString())) || "refresh failed";
    throw new Error(msg);
  })();

  try {
    return await _authRecoveryPromise;
  } finally {
    _authRecoveryPromise = null;
  }
}

async function attemptAuthRecoveryFlow(trigger = "auth_required") {
  if (AUTH_RECOVERY_IN_PROGRESS) return false;
  AUTH_RECOVERY_IN_PROGRESS = true;
  try {
    setConnBanner("auth_recovering", `🔐 Restoring session… (attempt ${AUTH_RECOVERY_ATTEMPTS + 1}/3)`, { spinner: true, showRetry: false });
    await refreshAccessTokenWithBackoff(3);

    // If we got here, token refresh succeeded → resume.
    AUTH_EXPIRED = false;
    AUTH_FAIL_REASON = "";
    hideConnBanner();

    // Reconnect socket (best-effort)
    try { if (socket && !socket.connected) socket.connect(); } catch {}

    // Restart room browser polling
    try { if (typeof rbStartPolling === "function") rbStartPolling(); } catch {}

    // Restart keepalive refresh timer
    try {
      if (!EC_TOKEN_KEEPALIVE_TIMER) {
        EC_TOKEN_KEEPALIVE_TIMER = setInterval(() => {
          if (AUTH_EXPIRED) return;
          refreshAccessToken().catch(() => {});
        }, 22 * 60 * 1000);
      }
    } catch {}

    return true;
  } catch (_e) {
    enterAuthExpiredState(trigger);
    return false;
  } finally {
    AUTH_RECOVERY_IN_PROGRESS = false;
  }
}

// Connection status banner (do NOT redirect to /login on transient disconnects)
let EC_HAS_EVER_CONNECTED = false;
let EC_CONN_BANNER = null;
let EC_CONN_STATE = "init";
let EC_CONN_ATTEMPT = 0;
let EC_CONN_LAST_REASON = "";
let EC_RECONNECT_IN_PROGRESS = false;
let EC_SERVER_DISCONNECT_RETRIES = 0;
let EC_LAST_RECONNECT_TOAST_AT = 0;
let EC_DISCONNECTED_AT = 0;
let EC_RECONNECT_BANNER_TIMER = null;
let EC_PENDING_CONN_BANNER = null;
const EC_RECONNECT_BANNER_GRACE_MS = 3200;
const EC_RECONNECT_TOAST_THRESHOLD_MS = 5000;

function clearPendingConnBanner() {
  try { if (EC_RECONNECT_BANNER_TIMER) clearTimeout(EC_RECONNECT_BANNER_TIMER); } catch {}
  EC_RECONNECT_BANNER_TIMER = null;
  EC_PENDING_CONN_BANNER = null;
}

function setConnBannerSoon(state, text, opts = {}, delayMs = EC_RECONNECT_BANNER_GRACE_MS) {
  // Short Socket.IO flaps are common during tab wake, Wi-Fi changes, and token
  // recovery. Delay the visible banner so quick reconnects stay invisible.
  const delay = Math.max(0, Number(delayMs || 0));
  EC_PENDING_CONN_BANNER = { state, text, opts };
  if (EC_RECONNECT_BANNER_TIMER) return;
  EC_RECONNECT_BANNER_TIMER = setTimeout(() => {
    const pending = EC_PENDING_CONN_BANNER;
    EC_RECONNECT_BANNER_TIMER = null;
    EC_PENDING_CONN_BANNER = null;
    if (!pending) return;
    try { if (socket && socket.connected) return; } catch {}
    setConnBanner(pending.state, pending.text, pending.opts || {});
  }, delay);
}

function setConnBannerNow(state, text, opts = {}) {
  clearPendingConnBanner();
  setConnBanner(state, text, opts);
}

// Guard: Socket.IO can briefly flap (Wi‑Fi blips, server restarts). The connect
// handler triggers multiple data fetches; if it runs in a tight loop it can
// hammer the server and exhaust the DB pool. We throttle the "full bootstrap"
// fan-out to keep reconnects cheap.
let EC_LAST_CONNECT_BOOTSTRAP_AT = 0;

function ensureConnBanner() {
  if (EC_CONN_BANNER) return EC_CONN_BANNER;
  const el = ecCreateEl("div", { id: "ecConnBanner", className: "ec-conn hidden" });
  const inner = ecCreateEl("div", { className: "ec-conn-inner" });
  const left = ecCreateEl("div", { className: "ec-conn-left" });
  left.appendChild(ecCreateEl("span", { className: "ec-spinner", ariaHidden: "true" }));
  left.appendChild(ecCreateEl("span", { id: "ecConnText", className: "ec-conn-text", text: "Connecting…" }));
  const right = ecCreateEl("div", { className: "ec-conn-right" });
  right.appendChild(ecCreateEl("button", { id: "ecConnRetry", className: "miniBtn", type: "button", text: "Retry" }));
  right.appendChild(ecCreateEl("button", { id: "ecConnLogout", className: "miniBtn danger", type: "button", title: "Log out", text: "Logout" }));
  inner.appendChild(left);
  inner.appendChild(right);
  el.appendChild(inner);
  (document.body || document.documentElement).appendChild(el);
  EC_CONN_BANNER = el;

  // Buttons
  el.querySelector("#ecConnRetry")?.addEventListener("click", () => {
    // If we are in auth-expired mode, Retry means: attempt token refresh + resume.
    if (typeof AUTH_EXPIRED !== "undefined" && AUTH_EXPIRED) {
      attemptAuthRecoveryFlow("manual_retry").catch(() => {});
      return;
    }
    tryReconnectNow("manual_retry");
  });
  el.querySelector("#ecConnLogout")?.addEventListener("click", () => {
    bestEffortLogoutThenRedirect("user_logout").catch(() => {
      window.location.href = "/login?reason=user_logout";
    });
  });

  return el;
}

function setConnBanner(state, text, { spinner = true, showRetry = true } = {}) {
  EC_CONN_STATE = String(state || "");
  const el = ensureConnBanner();
  const t = el.querySelector("#ecConnText");
  if (t) t.textContent = String(text || "");
  el.classList.remove("hidden");
  el.classList.toggle("no-spinner", !spinner);
  el.classList.toggle("no-retry", !showRetry);
}

function hideConnBanner() {
  clearPendingConnBanner();
  if (!EC_CONN_BANNER) return;
  EC_CONN_BANNER.classList.add("hidden");
  EC_CONN_BANNER.classList.remove("no-spinner", "no-retry");
  EC_CONN_STATE = "connected";
  EC_CONN_ATTEMPT = 0;
  EC_CONN_LAST_REASON = "";
}

function tryReconnectNow(reason = "") {
  // If server disconnected us, Socket.IO won't auto-reconnect until we call connect().
  // If we're offline, just show the banner and wait for the browser "online" event.
  EC_CONN_LAST_REASON = String(reason || EC_CONN_LAST_REASON || "");
  if (navigator && navigator.onLine === false) {
    setConnBannerNow("offline", "📡 Offline — waiting for network…", { spinner: false, showRetry: false });
    return;
  }
  try {
    if (EC_RECONNECT_IN_PROGRESS) return;
    if (socket.connected) return;
    EC_RECONNECT_IN_PROGRESS = true;
    if (reason === "manual_retry") {
      setConnBannerNow("connecting", "🔌 Reconnecting…", { spinner: true, showRetry: true });
    } else {
      setConnBannerSoon("connecting", "🔌 Reconnecting…", { spinner: true, showRetry: true });
    }
    // Small delay prevents tight loops when the server keeps dropping us.
    setTimeout(() => {
      try {
        if (!socket.connected) socket.connect();
      } catch {}
    }, 400);
  } catch {}
}



const EC_SOCKET_READY_TIMEOUT_MS = 6500;
const EC_SOCKET_ACK_DEFAULT_TIMEOUT_MS = 8500;

function ecNormalizeSocketAck(res, fallbackError = "socket_failed") {
  if (res && typeof res === "object") return res;
  if (res === true) return { success: true };
  if (res === false) return { success: false, error: fallbackError };
  return { success: false, error: fallbackError };
}

async function ecWaitForSocketReady(timeoutMs = EC_SOCKET_READY_TIMEOUT_MS, opts = {}) {
  const total = Math.max(500, Number(timeoutMs || EC_SOCKET_READY_TIMEOUT_MS));
  const bannerText = String(opts.bannerText || "🔌 Reconnecting to server…");
  try {
    if (socket && socket.connected) return true;
    if (typeof AUTH_EXPIRED !== "undefined" && AUTH_EXPIRED && typeof attemptAuthRecoveryFlow === "function") {
      await attemptAuthRecoveryFlow(String(opts.trigger || "socket_ready_auth_expired"));
      if (socket && socket.connected) return true;
    }
    if (!socket || typeof socket.connect !== "function" || typeof socket.once !== "function") return false;
  } catch (_e) {
    return false;
  }

  return await new Promise((resolve) => {
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      try { clearTimeout(timer); } catch {}
      try { socket.off("connect", onConnect); } catch {}
      try { socket.off("connect_error", onConnectError); } catch {}
      resolve(!!ok);
    };
    const onConnect = () => finish(true);
    const onConnectError = async (err) => {
      const msg = String((err && (err.message || err.toString())) || "");
      if (/expired|unauthoriz|401/i.test(msg) && typeof attemptAuthRecoveryFlow === "function") {
        try {
          const recovered = await attemptAuthRecoveryFlow(String(opts.trigger || "socket_ready_connect_error"));
          if (recovered) {
            try { if (!socket.connected) socket.connect(); } catch {}
            return;
          }
        } catch (_e) {}
      }
      // Non-auth connection failures may resolve on the next Socket.IO retry.
    };
    const timer = setTimeout(() => finish(!!(socket && socket.connected)), total);
    try {
      socket.once("connect", onConnect);
      socket.on("connect_error", onConnectError);
      if (!socket.connected) socket.connect();
      if (typeof setConnBannerSoon === "function") {
        setConnBannerSoon("connecting", bannerText, { spinner: true, showRetry: true }, Number(opts.bannerDelayMs ?? 700));
      }
    } catch (_e) {
      finish(false);
    }
  });
}

async function ecEmitAck(eventName, payload = {}, timeoutMs = EC_SOCKET_ACK_DEFAULT_TIMEOUT_MS, opts = {}) {
  const eventLabel = String(eventName || "socket_event");
  const total = Math.max(500, Number(timeoutMs || EC_SOCKET_ACK_DEFAULT_TIMEOUT_MS));
  const startedAt = Date.now();
  try {
    if (!socket || !socket.connected) {
      const connected = await ecWaitForSocketReady(Math.min(EC_SOCKET_READY_TIMEOUT_MS, Math.max(750, total - 500)), {
        trigger: `emit_${eventLabel}`,
        bannerText: opts.connectBannerText || "🔌 Reconnecting before sending…",
        bannerDelayMs: opts.bannerDelayMs ?? 700,
      });
      if (!connected) return { success: false, error: "not_connected", event: eventLabel };
    }
  } catch (_e) {
    return { success: false, error: "not_connected", event: eventLabel };
  }

  const elapsed = Date.now() - startedAt;
  const ackTimeout = Math.max(500, total - elapsed);
  return await new Promise((resolve) => {
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      try { clearTimeout(timer); } catch {}
      resolve(ecNormalizeSocketAck(value, "empty_socket_response"));
    };
    const timer = setTimeout(() => finish({ success: false, error: "ack_timeout", event: eventLabel }), ackTimeout);
    try {
      if (!socket || !socket.connected || typeof socket.emit !== "function") {
        finish({ success: false, error: "not_connected", event: eventLabel });
        return;
      }
      socket.emit(eventName, payload, (res) => finish(res || { success: true }));
    } catch (e) {
      finish({ success: false, error: String(e?.message || e || "emit_failed"), event: eventLabel });
    }
  });
}

async function ecEmitBestEffort(eventName, payload = {}, opts = {}) {
  try {
    const connected = socket && socket.connected ? true : await ecWaitForSocketReady(Number(opts.timeoutMs || 2500), {
      trigger: `best_effort_${String(eventName || "event")}`,
      bannerText: opts.connectBannerText || "🔌 Reconnecting…",
      bannerDelayMs: opts.bannerDelayMs ?? 1200,
    });
    if (!connected || !socket || typeof socket.emit !== "function") return false;
    socket.emit(eventName, payload);
    return true;
  } catch (_e) {
    return false;
  }
}

// If the app is opened on the bind-all host (0.0.0.0), browsers do not treat it as a
// secure context. That breaks WebCrypto and PM E2EE even on the local machine.
// Redirect local users to 127.0.0.1 so encrypted PMs work without needing manual URL edits.
if (window.location.protocol === "http:" && ["0.0.0.0", "::", "[::]"].includes(String(window.location.hostname || "").trim())) {
  try {
    const url = new URL(window.location.href);
    url.hostname = "127.0.0.1";
    window.location.replace(url.toString());
  } catch (e) {}
}

const currentUser = window.USERNAME || "guest";
const USER_PERMS = new Set(Array.isArray(window.USER_PERMS) ? window.USER_PERMS.map(String) : []);

// WebCrypto (SubtleCrypto) is only available in a *secure context* (HTTPS or localhost).
const HAS_WEBCRYPTO = !!(window.isSecureContext && window.crypto && window.crypto.subtle);
