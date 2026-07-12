const _idleCfg = (window.HUI_CFG || {});
const _idleLimitMs = Math.max(0, Number(_idleCfg.idle_logout_seconds || 0)) * 1000;
const _presenceIdleMs = Math.max(0, Number(_idleCfg.presence_idle_minutes || 0)) * 60 * 1000;
const _presenceOfflineMs = Math.max(0, Number(_idleCfg.presence_offline_minutes || 0)) * 60 * 1000;
const _sharedActivityStorageKey = (typeof ecScopedStorageKey === "function") ? ecScopedStorageKey("last_user_activity_ms") : "hui:anonymous:last_user_activity_ms";
let _lastUserInteractionMs = Date.now();
let _lastActivityPingMs = 0;

function _readSharedActivityMs() {
  try {
    const n = Number(localStorage.getItem(_sharedActivityStorageKey) || 0);
    return Number.isFinite(n) && n > 0 ? n : 0;
  } catch (_) {
    return 0;
  }
}

function _writeSharedActivityMs(ts) {
  try { localStorage.setItem(_sharedActivityStorageKey, String(ts)); } catch (_) {}
}

function _effectiveLastUserInteractionMs() {
  return Math.max(_lastUserInteractionMs, _readSharedActivityMs());
}

function _manualPresence() {
  return String(window.__ec_manualPresence || window.__ym_lastPresence || $("meStatus")?.value || "online");
}

function ecBuildRefreshCsrfHeaders() {
  // Refresh rotation can update csrf_refresh_token between attempts, especially
  // when another tab wins the refresh race. Read the cookie immediately before
  // each /token/refresh request instead of reusing a stale header.
  const csrf = getCookie("csrf_refresh_token");
  return csrf ? { "X-CSRF-TOKEN": csrf } : {};
}

function _emitIdlePresence(nextPresence) {
  if (typeof socket === "undefined" || !socket || typeof socket.emit !== "function") return;
  socket.emit("set_my_presence", { presence: nextPresence });
}

function _restorePresenceFromIdleAutomation() {
  const autoAway = !!window.__ec_autoAwayActive;
  const autoOffline = !!window.__ec_autoOfflineActive;
  if (!autoAway && !autoOffline) return;
  if (_manualPresence() !== "online") {
    window.__ec_autoAwayActive = false;
    window.__ec_autoOfflineActive = false;
    return;
  }
  window.__ec_autoAwayActive = false;
  window.__ec_autoOfflineActive = false;
  _emitIdlePresence("online");
}

function _markUserInteraction() {
  if (typeof AUTH_EXPIRED !== "undefined" && AUTH_EXPIRED) return;
  if (typeof AUTH_BOOTSTRAP_PENDING !== "undefined" && AUTH_BOOTSTRAP_PENDING) return;
  _lastUserInteractionMs = Date.now();
  const now = _lastUserInteractionMs;
  _writeSharedActivityMs(now);
  _restorePresenceFromIdleAutomation();
  // Throttle activity pings to at most 1/minute.
  if (now - _lastActivityPingMs < 60_000) return;
  _lastActivityPingMs = now;
  // Best-effort; failures are handled by fetchWithAuth (may refresh/redirect).
  fetchWithAuth("/api/activity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).catch(() => {});
}

// Listen for interaction events (passive where possible).
window.addEventListener("mousemove", _markUserInteraction, { passive: true });
window.addEventListener("mousedown", _markUserInteraction, { passive: true });
window.addEventListener("keydown", _markUserInteraction, { passive: true });
window.addEventListener("scroll", _markUserInteraction, { passive: true });
window.addEventListener("touchstart", _markUserInteraction, { passive: true });
document.addEventListener("visibilitychange", () => {
  if (typeof AUTH_BOOTSTRAP_PENDING !== "undefined" && AUTH_BOOTSTRAP_PENDING) return;
  if (!document.hidden) _markUserInteraction();
});

// Auto-logout when idle window is exceeded.
if (_idleLimitMs > 0) {
  setInterval(() => {
    const idleFor = Date.now() - _effectiveLastUserInteractionMs();
    if (idleFor > _idleLimitMs) {
      bestEffortLogoutThenRedirect("idle_timeout").catch(() => {
        // As a fallback, hard redirect.
        window.location.href = "/login?reason=idle_timeout";
      });
    }
  }, 30_000);
}

async function refreshAccessToken() {
  // De-dupe concurrent refresh attempts.
  if (_refreshPromise) return _refreshPromise;

  _refreshPromise = (async () => {
    // If any tab refreshed very recently, don't churn refresh tokens.
    if (Date.now() - ecGetLastRefreshTs() < 30_000) {
      return true;
    }

    // Cross-tab lock: only one tab should refresh at a time.
    let gotLock = await ecAcquireRefreshLock();
    if (!gotLock) {
      const ok = await ecWaitForOtherTabRefresh(5500);
      if (ok) return true;
      // Last resort: try to grab the lock again; if we still can't, proceed without
      // locking to avoid deadlock.
      gotLock = await ecAcquireRefreshLock();
    }

    let resp;
    try {
      resp = await fetch("/token/refresh", {
        method: "POST",
        credentials: "include",
        headers: ecBuildRefreshCsrfHeaders()
      });
    } catch (e) {
      if (gotLock) ecReleaseRefreshLock();
      ecBroadcastAuth({ type: 'refresh_failed' });
      throw new Error('refresh network error');
    }

    if (!resp.ok) {
      // 409 = stale refresh (another tab/device likely rotated the refresh token).
      // Retry once after a short delay.
      if (resp.status === 409) {
        await new Promise(r => setTimeout(r, 250));
        let resp2;
        try {
          resp2 = await fetch("/token/refresh", {
            method: "POST",
            credentials: "include",
            headers: ecBuildRefreshCsrfHeaders()
          });
        } catch (_e) {
          if (gotLock) ecReleaseRefreshLock();
          ecBroadcastAuth({ type: 'refresh_failed' });
          throw new Error('refresh network error');
        }
        if (resp2.ok) {
          ecSetLastRefreshTs(Date.now());
          ecBroadcastAuth({ type: 'refreshed' });
          if (gotLock) ecReleaseRefreshLock();
          return true;
        }
        if (gotLock) ecReleaseRefreshLock();
        ecBroadcastAuth({ type: 'refresh_failed' });
        throw new Error(`refresh failed (${resp2.status})`);
      }
      // Let caller decide what to do (usually redirect to /login).
      if (gotLock) ecReleaseRefreshLock();
      ecBroadcastAuth({ type: 'refresh_failed' });
      throw new Error(`refresh failed (${resp.status})`);
    }

    ecSetLastRefreshTs(Date.now());
    ecBroadcastAuth({ type: 'refreshed' });
    if (gotLock) ecReleaseRefreshLock();
    return true;
  })();

  try {
    return await _refreshPromise;
  } finally {
    _refreshPromise = null;
  }
}


window.addEventListener("storage", (ev) => {
  if (ev.key !== _sharedActivityStorageKey) return;
  const ts = Number(ev.newValue || 0);
  if (Number.isFinite(ts) && ts > _lastUserInteractionMs) {
    _lastUserInteractionMs = ts;
    _restorePresenceFromIdleAutomation();
  }
});

if (_presenceOfflineMs > 0) {
  setInterval(() => {
    if (typeof AUTH_EXPIRED !== "undefined" && AUTH_EXPIRED) return;
    if (typeof AUTH_BOOTSTRAP_PENDING !== "undefined" && AUTH_BOOTSTRAP_PENDING) return;
    if (window.__ec_autoOfflineActive) return;
    if (_manualPresence() !== "online") return;
    const idleFor = Date.now() - _effectiveLastUserInteractionMs();
    if (idleFor >= _presenceOfflineMs) {
      window.__ec_autoAwayActive = false;
      window.__ec_autoOfflineActive = true;
      _emitIdlePresence("invisible");
    }
  }, 15_000);
}

if (_presenceIdleMs > 0) {
  setInterval(() => {
    if (typeof AUTH_EXPIRED !== "undefined" && AUTH_EXPIRED) return;
    if (typeof AUTH_BOOTSTRAP_PENDING !== "undefined" && AUTH_BOOTSTRAP_PENDING) return;
    if (window.__ec_autoAwayActive || window.__ec_autoOfflineActive) return;
    if (_manualPresence() !== "online") return;
    const idleFor = Date.now() - _effectiveLastUserInteractionMs();
    if (idleFor >= _presenceIdleMs) {
      window.__ec_autoAwayActive = true;
      _emitIdlePresence("away");
    }
  }, 15_000);
}
