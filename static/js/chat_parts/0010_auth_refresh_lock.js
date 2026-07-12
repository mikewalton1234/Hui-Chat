// ───────────────────────────────────────────────────────────────────────────────
// Auth helpers (short-lived access token + refresh token in HttpOnly cookies)
// ───────────────────────────────────────────────────────────────────────────────
let _refreshPromise = null;

// Cross-tab refresh coordination
// HuiChat uses rotating refresh tokens. If multiple tabs refresh at once, they
// fight over the cookie and can cause storms (/token/refresh spam, reconnect loops).
// We coordinate refresh attempts across tabs with a best-effort localStorage lock
// and (optionally) BroadcastChannel notifications.

function ecStorageScopeValue(value, fallback = "anonymous") {
  const raw = String(value || fallback || "anonymous").trim().toLowerCase();
  const safe = raw.replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64);
  return safe || String(fallback || "anonymous");
}

function ecStorageScope() {
  const server = ecStorageScopeValue((window.HUI_CFG && window.HUI_CFG.server_name) || "Hui Chat", "hui-chat");
  const user = ecStorageScopeValue(window.USERNAME || "anonymous", "anonymous");
  return `${server}:${user}`;
}

function ecScopedStorageKey(name) {
  const safeName = ecStorageScopeValue(name || "state", "state");
  return `hui:${ecStorageScope()}:${safeName}`;
}

function ecClearLegacyAuthStorageKeys() {
  // Earlier beta builds used origin-global names. Clear them so one account's
  // idle/refresh state cannot influence another account on the same browser.
  try {
    ["hui_refresh_lock", "hui_last_refresh_ts", "hui_last_user_activity_ms"].forEach((key) => {
      try { localStorage.removeItem(key); } catch {}
    });
  } catch {}
}

ecClearLegacyAuthStorageKeys();

const EC_TAB_ID = (() => {
  try {
    const v = sessionStorage.getItem('hui_tab_id');
    if (v) return v;
    const nv = `tab_${Math.random().toString(36).slice(2)}_${Date.now()}`;
    sessionStorage.setItem('hui_tab_id', nv);
    return nv;
  } catch {
    return `tab_${Math.random().toString(36).slice(2)}_${Date.now()}`;
  }
})();

let EC_AUTH_BC = null;
try { EC_AUTH_BC = new BroadcastChannel(ecScopedStorageKey('auth_channel')); } catch { EC_AUTH_BC = null; }

function ecBroadcastAuth(msg) {
  try { EC_AUTH_BC?.postMessage({ ...msg, ts: Date.now(), tab: EC_TAB_ID }); } catch {}
}

function ecGetLastRefreshTs() {
  try { return Number(localStorage.getItem(ecScopedStorageKey('last_refresh_ts')) || 0) || 0; } catch { return 0; }
}

function ecSetLastRefreshTs(ts) {
  try { localStorage.setItem(ecScopedStorageKey('last_refresh_ts'), String(Number(ts) || Date.now())); } catch {}
}

async function ecAcquireRefreshLock(ttlMs = 6500) {
  const now = Date.now();
  const lockKey = ecScopedStorageKey('refresh_lock');
  const mine = { owner: EC_TAB_ID, exp: now + ttlMs };
  try {
    const raw = localStorage.getItem(lockKey);
    if (raw) {
      const cur = JSON.parse(raw);
      if (cur && Number(cur.exp || 0) > now && cur.owner && cur.owner !== EC_TAB_ID) {
        return false;
      }
    }
    localStorage.setItem(lockKey, JSON.stringify(mine));
    const check = JSON.parse(localStorage.getItem(lockKey) || '{}');
    return check && check.owner === EC_TAB_ID;
  } catch {
    // If storage is unavailable, just don't lock.
    return true;
  }
}

function ecReleaseRefreshLock() {
  const lockKey = ecScopedStorageKey('refresh_lock');
  try {
    const raw = localStorage.getItem(lockKey);
    if (!raw) return;
    const cur = JSON.parse(raw);
    if (cur && cur.owner === EC_TAB_ID) {
      localStorage.removeItem(lockKey);
    }
  } catch {}
}

function ecLockIsHeldByOther() {
  const lockKey = ecScopedStorageKey('refresh_lock');
  const now = Date.now();
  try {
    const raw = localStorage.getItem(lockKey);
    if (!raw) return false;
    const cur = JSON.parse(raw);
    return Boolean(cur && Number(cur.exp || 0) > now && cur.owner && cur.owner !== EC_TAB_ID);
  } catch {
    return false;
  }
}

async function ecWaitForOtherTabRefresh(maxWaitMs = 5500) {
  const start = Date.now();
  let done = false;

  return await new Promise((resolve) => {
    const finish = (ok) => {
      if (done) return;
      done = true;
      try { EC_AUTH_BC?.removeEventListener('message', onMsg); } catch {}
      clearInterval(poll);
      resolve(Boolean(ok));
    };

    const onMsg = (ev) => {
      const data = ev?.data;
      if (!data || data.tab === EC_TAB_ID) return;
      if (data.type === 'refreshed') finish(true);
      if (data.type === 'refresh_failed') finish(false);
    };

    try { EC_AUTH_BC?.addEventListener('message', onMsg); } catch {}

    const poll = setInterval(() => {
      if (!ecLockIsHeldByOther()) {
        // Lock cleared. If another tab just refreshed, we should be good.
        if (Date.now() - ecGetLastRefreshTs() < 30000) finish(true);
      }
      if (Date.now() - start > maxWaitMs) finish(false);
    }, 250);
  });
}

let _redirectingToLogin = false;

async function bestEffortLogoutThenRedirect(reason = 'disconnected') {
  // Used for *true* logout conditions (idle timeout, auth required, admin force logout).
  // Transient disconnects should NOT call this.
  if (_redirectingToLogin) return;
  _redirectingToLogin = true;

  try { sessionStorage.setItem('ec_disconnect_reason', String(reason)); } catch {}

  // Stop Socket.IO reconnection spam before navigating away.
  try { socket.io.opts.reconnection = false; } catch {}
  try { socket.disconnect(); } catch {}

  // Best-effort cookie clearing + session revoke. This may fail if the server is down.
  try {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), 1500);
    await fetch('/logout', { method: 'POST', credentials: 'same-origin', signal: controller.signal, headers: (() => { const csrf = getCookie('csrf_access_token') || getCookie('csrf_refresh_token'); return csrf ? { 'X-CSRF-TOKEN': csrf } : {}; })() });
    clearTimeout(t);
  } catch {}

  window.location.href = `/login?reason=${encodeURIComponent(String(reason))}`;
}

// ───────────────────────────────────────────────────────────────────────────────
// Idle logout + activity pings
//
// Server enforces idle logout based on auth session's last_activity_at.
// We update last_activity_at from the browser based on real user interaction.
//
// Config:
//   window.HUI_CFG.idle_logout_seconds (0 disables)
// ───────────────────────────────────────────────────────────────────────────────
