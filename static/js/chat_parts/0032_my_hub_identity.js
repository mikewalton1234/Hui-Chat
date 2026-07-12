function ecCurrentUsernameForHub() {
  const candidate = String(window.CURRENT_USER || window.USERNAME || currentUser || '').trim();
  return candidate || 'User';
}

function ecProfileSocketTimeoutMs() {
  const raw = Number(window.HUI_CFG && window.HUI_CFG.profile_socket_ack_timeout_ms);
  return Number.isFinite(raw) && raw >= 500 ? Math.min(raw, 10000) : 3000;
}

function ecSocketAckWithTimeout(eventName, payload, timeoutMs = ecProfileSocketTimeoutMs()) {
  if (typeof ecEmitAck === 'function') {
    return ecEmitAck(eventName, payload || {}, timeoutMs, {
      connectBannerText: '🔌 Reconnecting before profile action…',
      bannerDelayMs: 900,
    });
  }
  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      try { clearTimeout(timer); } catch {}
      resolve(value);
    };
    const timer = setTimeout(() => finish({ success: false, error: 'socket_timeout' }), Math.max(500, Number(timeoutMs || 0) || 3000));
    try {
      if (!socket || !socket.connected || typeof socket.emit !== 'function') {
        finish({ success: false, error: 'socket_not_connected' });
        return;
      }
      socket.emit(eventName, payload || {}, (res) => finish(res || { success: false, error: 'empty_socket_response' }));
    } catch (err) {
      finish({ success: false, error: String(err?.message || err || 'socket_error') });
    }
  });
}

async function fetchUserProfileViaHttp(username, opts = {}) {
  const u = String(username || '').trim();
  if (!u) return { success: false, error: 'missing_username' };
  const timeoutMs = Math.max(800, Number(opts.timeoutMs || 4500) || 4500);
  let controller = null;
  let timeoutId = null;
  try {
    const path = `/api/profile/${encodeURIComponent(u)}`;
    const requestUrl = `${path}${path.includes('?') ? '&' : '?'}_=${Date.now()}`;
    const requestOpts = {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
    };
    if (typeof AbortController === 'function') {
      controller = new AbortController();
      requestOpts.signal = controller.signal;
      timeoutId = setTimeout(() => {
        try { controller.abort(); } catch {}
      }, timeoutMs);
    }

    const res = (typeof fetchWithAuth === 'function')
      ? await fetchWithAuth(requestUrl, requestOpts)
      : await fetch(requestUrl, requestOpts);
    let payload = null;
    try { payload = await res.json(); } catch (_) { payload = null; }
    if (!res.ok) return payload || { success: false, error: `http_${res.status}` };
    return payload || { success: false, error: 'empty_http_response' };
  } catch (err) {
    const isAbort = String(err?.name || '').toLowerCase() === 'aborterror';
    return { success: false, error: isAbort ? 'http_timeout' : String(err?.message || err || 'http_error') };
  } finally {
    try { if (timeoutId) clearTimeout(timeoutId); } catch {}
  }
}

async function fetchUserProfileForUI(username, opts = {}) {
  const u = String(username || ecCurrentUsernameForHub()).trim();
  if (!u) return { success: false, error: 'missing_username' };
  const timeoutMs = Math.max(800, Number(opts.timeoutMs || ecProfileSocketTimeoutMs()) || 3000);

  // Prefer the HTTP profile endpoint for the UI. Socket.IO ack callbacks can be
  // dropped during reconnects/polling churn, which left the profile window stuck
  // on “Loading profile page…”. HTTP gives us a visible request in the server log
  // and a deterministic timeout/error path. Keep the socket path as a fallback.
  const httpResult = await fetchUserProfileViaHttp(u, { timeoutMs: Math.max(timeoutMs, 4500) });
  if (httpResult && httpResult.success && httpResult.profile) return httpResult;

  const socketResult = await ecSocketAckWithTimeout('get_user_profile', { username: u }, Math.min(Math.max(timeoutMs, 1200), 2500));
  if (socketResult && socketResult.success && socketResult.profile) return socketResult;

  return httpResult || socketResult || { success: false, error: 'profile_unavailable' };
}

function renderMyHubIdentity(profile = null) {
  const p = (profile && typeof profile === 'object') ? profile : (UIState.myProfile || {});
  const username = String(p.username || ecCurrentUsernameForHub()).trim() || 'User';

  const nameEl = $('meName');
  if (nameEl) nameEl.textContent = username;

  const titleUserEl = $('dockTitleUser');
  if (titleUserEl) titleUserEl.textContent = username;

  const av = $('meAvatar');
  if (!av) return;
  if (typeof ecClearNode === 'function') ecClearNode(av);
  else av.replaceChildren();

  const avatarUrl = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(p.avatar_url || '', { allowRelative: true, allowExternal: true })
    : String(p.avatar_url || '').trim();
  av.classList.toggle('hasImage', !!avatarUrl);
  if (avatarUrl) {
    const img = document.createElement('img');
    img.src = avatarUrl;
    img.alt = `${username} avatar`;
    img.loading = 'lazy';
    img.referrerPolicy = 'no-referrer';
    img.addEventListener('error', () => {
      av.classList.remove('hasImage');
      if (typeof ecClearNode === 'function') ecClearNode(av);
      else av.replaceChildren();
      av.textContent = dockInitials(username);
    }, { once: true });
    av.appendChild(img);
  } else {
    av.textContent = dockInitials(username);
  }

  // The room transcript may render before the profile cache finishes loading.
  // Once the hub identity has the real avatar, refresh already-rendered message
  // avatars for this user so they do not stay as letter bubbles.
  try { window.ecRefreshMessageAvatarsForUsername?.(username); } catch {}
}

function fetchMyProfile() {
  return new Promise((resolve) => {
    try {
      fetchUserProfileForUI(ecCurrentUsernameForHub()).then((res) => {
        if (res?.success && res?.profile) {
          UIState.myProfile = res.profile;
          resolve(res.profile);
          return;
        }
        resolve(null);
      });
    } catch (_) {
      resolve(null);
    }
  });
}

async function refreshMyProfileInHub() {
  const profile = await fetchMyProfile();
  renderMyHubIdentity(profile);
  return profile;
}

function bindHubProfileControls() {
  const open = (ev) => {
    try { ev?.preventDefault?.(); } catch {}
    openMyProfileEditor();
  };

  $('btnEditMeProfile')?.addEventListener('click', open);
  $('meAvatar')?.addEventListener('click', open);
  $('meName')?.addEventListener('click', open);

  const keyOpen = (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') {
      try { ev.preventDefault(); } catch {}
      openMyProfileEditor();
    }
  };
  $('meAvatar')?.addEventListener('keydown', keyOpen);
  $('meName')?.addEventListener('keydown', keyOpen);
}
