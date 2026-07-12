// buildDuplicateMessageHints compatibility hook
// ───────────────────────────────────────────────────────────────────────────────
// Group history pagination (Load older)
// ───────────────────────────────────────────────────────────────────────────────
const GROUP_HISTORY_PAGE_SIZE = 200;

function groupMsgId(m) {
  const v = (m && (m.message_id ?? m.messageId ?? m.id)) ?? null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function groupHistState(win) {
  if (!win) return { oldestId: null, loading: false, done: true };
  if (!win._groupHist) win._groupHist = { oldestId: null, loading: false, done: false };
  return win._groupHist;
}

function groupSeenIds(win) {
  if (!win) return new Set();
  if (!win._groupSeenMessageIds) win._groupSeenMessageIds = new Set();
  return win._groupSeenMessageIds;
}

function rememberGroupMessageId(win, messageId) {
  const id = groupMsgId({ message_id: messageId });
  if (id === null) return false;
  groupSeenIds(win).add(id);
  return true;
}

function hasSeenGroupMessageId(win, messageId) {
  const id = groupMsgId({ message_id: messageId });
  if (id === null) return false;
  return groupSeenIds(win).has(id);
}

function ecGroupCurrentUserKey() {
  return String(currentUser || '').trim().toLowerCase();
}

function ecGroupSameUser(a, b) {
  const aa = String(a || '').trim().toLowerCase();
  const bb = String(b || '').trim().toLowerCase();
  return !!aa && !!bb && aa === bb;
}


// ───────────────────────────────────────────────────────────────────────────────
// PM/group typing indicators — lightweight presence only, never message content.
// ───────────────────────────────────────────────────────────────────────────────
const EC_CONVO_TYPING_TIMEOUT_MS = 6500;
const EC_CONVO_TYPING_SEND_THROTTLE_MS = 2200;
const EC_CONVO_TYPING_STATE = new Map();

function ecTypingFeatureEnabled(surface) {
  const cfg = window.HUI_CFG || {};
  if (surface === 'pm' || surface === 'dm' || surface === 'direct') {
    return (typeof ecConfigBool === 'function') ? ecConfigBool(cfg.enable_dm_typing_indicators, true) : cfg.enable_dm_typing_indicators !== false;
  }
  if (surface === 'group') {
    return (typeof ecConfigBool === 'function') ? ecConfigBool(cfg.enable_group_typing_indicators, true) : cfg.enable_group_typing_indicators !== false;
  }
  return false;
}

function ecConversationTypingKey(surface, conversationId, username) {
  return `${String(surface || '').trim()}:${String(conversationId || '').trim().toLowerCase()}\x1f${String(username || '').trim().toLowerCase()}`;
}

function ecTypingCurrentUsername() {
  return String(window.CURRENT_USER || window.USERNAME || currentUser || '').trim();
}

function ecTypingSameUser(a, b) {
  const aa = String(a || '').trim().toLowerCase();
  const bb = String(b || '').trim().toLowerCase();
  return !!aa && !!bb && aa === bb;
}

function ecTypingVisibleName(name) {
  const text = String(name || '').trim();
  return text ? text.slice(0, 48) : 'Someone';
}

function ecFormatConversationTypingUsers(users, surface) {
  const all = Array.from(users || []).filter(Boolean).map(ecTypingVisibleName);
  const names = all.slice(0, 3);
  if (!names.length) return '';
  if (surface === 'pm' || surface === 'dm' || surface === 'direct') return `${names[0]} is typing…`;
  if (all.length === 1) return `${names[0]} is typing…`;
  if (all.length === 2) return `${names[0]} and ${names[1]} are typing…`;
  if (all.length === 3) return `${names[0]}, ${names[1]}, and ${names[2]} are typing…`;
  return `${names[0]}, ${names[1]}, and ${all.length - 2} others are typing…`;
}

function ecEnsureConversationTypingIndicator(win, surface) {
  if (!win?._ym?.log) return null;
  if (win._ym.typingIndicator && win._ym.typingIndicator.isConnected) return win._ym.typingIndicator;
  const node = document.createElement('div');
  node.className = `ymTypingIndicator ymTypingIndicator--${surface} hidden`;
  node.setAttribute('aria-live', 'polite');
  node.setAttribute('role', 'status');
  node.textContent = '';
  const log = win._ym.log;
  const parent = log.parentElement || win.querySelector?.('.ym-body') || win;
  try { parent.insertBefore(node, log.nextSibling); }
  catch { parent.appendChild(node); }
  win._ym.typingIndicator = node;
  return node;
}

function ecTypingWindowFor(surface, conversationId) {
  if (surface === 'pm' || surface === 'dm' || surface === 'direct') {
    const peer = ecPmPeerName(conversationId);
    return peer ? UIState.windows.get(ecPmWindowId(peer)) : null;
  }
  if (surface === 'group') {
    const gid = Number(conversationId || 0);
    return gid ? UIState.windows.get('group:' + String(gid)) : null;
  }
  return null;
}

function ecRenderConversationTyping(surface, conversationId) {
  if (!ecTypingFeatureEnabled(surface)) return;
  const conv = String(conversationId || '').trim();
  if (!conv) return;
  const win = ecTypingWindowFor(surface, conv);
  if (!win) return;
  const now = Date.now();
  const active = new Set();
  const me = ecTypingCurrentUsername();
  for (const [key, entry] of EC_CONVO_TYPING_STATE.entries()) {
    if (!entry || entry.surface !== surface || String(entry.conversationId) !== conv) continue;
    if (entry.expiresAt <= now) {
      if (entry.timer) clearTimeout(entry.timer);
      EC_CONVO_TYPING_STATE.delete(key);
      continue;
    }
    if (entry.username && !ecTypingSameUser(entry.username, me)) active.add(entry.username);
  }
  const label = ecFormatConversationTypingUsers(active, surface);
  const node = ecEnsureConversationTypingIndicator(win, surface);
  if (!node) return;
  node.textContent = label;
  node.classList.toggle('hidden', !label);
}

function ecSetConversationTyping(surface, conversationId, username, isTyping, expiresInSec) {
  if (!ecTypingFeatureEnabled(surface)) return;
  const conv = String(conversationId || '').trim();
  const user = String(username || '').trim();
  if (!conv || !user || ecTypingSameUser(user, ecTypingCurrentUsername())) return;
  const key = ecConversationTypingKey(surface, conv, user);
  if (isTyping) {
    const ttlMs = Math.max(1500, Math.min(15000, Number(expiresInSec || 5) * 1000 + 1000));
    const previous = EC_CONVO_TYPING_STATE.get(key);
    if (previous?.timer) clearTimeout(previous.timer);
    const timer = setTimeout(() => {
      EC_CONVO_TYPING_STATE.delete(key);
      ecRenderConversationTyping(surface, conv);
    }, ttlMs);
    EC_CONVO_TYPING_STATE.set(key, { surface, conversationId: conv, username: user, expiresAt: Date.now() + ttlMs, timer });
  } else {
    const previous = EC_CONVO_TYPING_STATE.get(key);
    if (previous?.timer) clearTimeout(previous.timer);
    EC_CONVO_TYPING_STATE.delete(key);
  }
  ecRenderConversationTyping(surface, conv);
}

function ecClearConversationTypingForWindow(win) {
  const surface = String(win?.dataset?.kind || '').trim() === 'group' ? 'group' : 'pm';
  const conv = surface === 'group' ? String(win?.dataset?.groupId || '').trim() : ecPmPeerName(win?.dataset?.pmPeer || '');
  if (!conv) return;
  for (const [key, entry] of EC_CONVO_TYPING_STATE.entries()) {
    if (entry?.surface === surface && String(entry.conversationId) === String(conv)) {
      if (entry.timer) clearTimeout(entry.timer);
      EC_CONVO_TYPING_STATE.delete(key);
    }
  }
  const node = win?._ym?.typingIndicator;
  if (node) {
    node.textContent = '';
    node.classList.add('hidden');
  }
}

function ecTypingEmit(eventName, payload, onAck = null) {
  if (!socket?.connected) return false;
  try {
    socket.emit(eventName, payload || {}, (res) => {
      try { if (typeof onAck === 'function') onAck(res || {}); } catch {}
    });
    return true;
  } catch {
    return false;
  }
}

function ecConversationTypingStop(input, opts = {}) {
  if (!input) return;
  const surface = String(input._ecTypingSurface || '').trim();
  if (!surface) return;
  if (input._ecTypingStopTimer) {
    clearTimeout(input._ecTypingStopTimer);
    input._ecTypingStopTimer = null;
  }
  const wasTyping = !!input._ecTypingActive;
  input._ecTypingActive = false;
  input._ecTypingLastSent = 0;
  if (!wasTyping && !opts.force) return;
  if (surface === 'pm') {
    const peer = ecPmPeerName(input._ecTypingPeer || '');
    if (peer && ecTypingFeatureEnabled('pm')) ecTypingEmit('direct_stop_typing', { to: peer });
  } else if (surface === 'group') {
    const gid = Number(input._ecTypingGroupId || 0);
    if (gid && ecTypingFeatureEnabled('group')) ecTypingEmit('group_stop_typing', { group_id: gid });
  }
}


function ecConversationTypingStart(input) {
  if (!input) return false;
  const surface = String(input._ecTypingSurface || '').trim();
  if (!surface || !ecTypingFeatureEnabled(surface)) return false;
  if (!input.value || !input.value.trim()) {
    ecConversationTypingStop(input, { force: true });
    return false;
  }
  const now = Date.now();
  if (input._ecTypingActive && input._ecTypingLastSent && (now - input._ecTypingLastSent) <= EC_CONVO_TYPING_SEND_THROTTLE_MS) {
    return true;
  }

  const onAck = (res = {}) => {
    const err = String(res?.error || '').toLowerCase();
    if (res && res.success === false && (err.includes('blocked') || err.includes('self_dm_disabled') || err.includes('not found') || err.includes('denied'))) {
      input._ecTypingActive = false;
      input._ecTypingLastSent = 0;
      if (input._ecTypingStopTimer) {
        clearTimeout(input._ecTypingStopTimer);
        input._ecTypingStopTimer = null;
      }
    }
  };

  let emitted = false;
  if (surface === 'pm') {
    const peer = ecPmPeerName(input._ecTypingPeer || '');
    if (peer) emitted = ecTypingEmit('direct_typing', { to: peer }, onAck);
  } else if (surface === 'group') {
    const gid = Number(input._ecTypingGroupId || 0);
    if (gid) emitted = ecTypingEmit('group_typing', { group_id: gid }, onAck);
  }

  if (!emitted) {
    input._ecTypingActive = false;
    input._ecTypingLastSent = 0;
    return false;
  }
  input._ecTypingActive = true;
  input._ecTypingLastSent = now;
  return true;
}

function ecConversationTypingArmStopTimer(input) {
  if (!input) return;
  if (input._ecTypingStopTimer) clearTimeout(input._ecTypingStopTimer);
  input._ecTypingStopTimer = setTimeout(() => ecConversationTypingStop(input), EC_CONVO_TYPING_TIMEOUT_MS - 1500);
}

function ecStopAllConversationTyping(opts = {}) {
  try {
    if (!UIState?.windows || typeof UIState.windows.values !== 'function') return;
    for (const win of UIState.windows.values()) {
      const input = win?._ym?.input;
      if (input?._ecTypingSurface) ecConversationTypingStop(input, { force: opts.force !== false });
    }
  } catch {}
}

function ecClearAllConversationTypingIndicators() {
  try {
    for (const [_key, entry] of EC_CONVO_TYPING_STATE.entries()) {
      if (entry?.timer) clearTimeout(entry.timer);
    }
    EC_CONVO_TYPING_STATE.clear();
    if (UIState?.windows && typeof UIState.windows.values === 'function') {
      for (const win of UIState.windows.values()) {
        const node = win?._ym?.typingIndicator;
        if (node) {
          node.textContent = '';
          node.classList.add('hidden');
        }
      }
    }
  } catch {}
}

try {
  if (!window.__ecConvoTypingLifecycleBound) {
    window.__ecConvoTypingLifecycleBound = true;
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) ecStopAllConversationTyping({ force: true });
    });
    window.addEventListener('pagehide', () => ecStopAllConversationTyping({ force: true }));
    socket.on('disconnect', () => {
      ecStopAllConversationTyping({ force: true });
      ecClearAllConversationTypingIndicators();
    });
    socket.on('connect', () => {
      try {
        if (!UIState?.windows || typeof UIState.windows.values !== 'function') return;
        for (const win of UIState.windows.values()) {
          const input = win?._ym?.input;
          if (input?._ecTypingSurface && input.value && input.value.trim()) {
            input._ecTypingActive = false;
            input._ecTypingLastSent = 0;
            ecConversationTypingStart(input);
            ecConversationTypingArmStopTimer(input);
          }
        }
      } catch {}
    });
  }
} catch {}

function ecBindConversationTypingInput(win, surface, conversationId) {
  const input = win?._ym?.input;
  if (!input || !ecTypingFeatureEnabled(surface)) return;
  const conv = String(conversationId || '').trim();
  if (!conv) return;
  input._ecTypingSurface = surface;
  if (surface === 'pm') input._ecTypingPeer = conv;
  if (surface === 'group') input._ecTypingGroupId = Number(conv || 0);
  ecEnsureConversationTypingIndicator(win, surface);
  try { setTimeout(() => ecRenderConversationTyping(surface, conv), 0); } catch {}
  if (input._ecConversationTypingBound) return;
  input._ecConversationTypingBound = true;
  input.addEventListener('input', () => {
    if (!input.value || !input.value.trim()) {
      ecConversationTypingStop(input, { force: true });
      return;
    }
    ecConversationTypingStart(input);
    ecConversationTypingArmStopTimer(input);
  });
  input.addEventListener('focus', () => {
    if (input.value && input.value.trim()) {
      input._ecTypingActive = false;
      input._ecTypingLastSent = 0;
      ecConversationTypingStart(input);
      ecConversationTypingArmStopTimer(input);
    }
  });
  input.addEventListener('compositionend', () => {
    if (input.value && input.value.trim()) {
      ecConversationTypingStart(input);
      ecConversationTypingArmStopTimer(input);
    }
  });
  input.addEventListener('blur', () => ecConversationTypingStop(input));
  try { registerWindowCleanup(win, () => { ecConversationTypingStop(input, { force: true }); ecClearConversationTypingForWindow(win); }); } catch {}
}

function ecBindDirectTypingInput(win, peer) {
  ecBindConversationTypingInput(win, 'pm', ecPmPeerName(peer));
}

try {
  window.ecPmTypingDebugState = () => Array.from(EC_CONVO_TYPING_STATE.values()).map((entry) => ({
    surface: entry.surface,
    conversationId: entry.conversationId,
    username: entry.username,
    expiresInMs: Math.max(0, Math.round((entry.expiresAt || 0) - Date.now())),
  }));
} catch {}

function ecBindGroupTypingInput(win, groupId) {
  const gid = Number(groupId || 0);
  if (gid) ecBindConversationTypingInput(win, 'group', String(gid));
}

socket.on('direct_typing', (payload = {}) => {
  if (!payload) return;
  const from = ecPmPeerName(payload.from || payload.username || payload.sender || '');
  if (!from) return;
  ecSetConversationTyping('pm', from, from, payload.typing !== false, payload.expires_in);
});

socket.on('direct_stop_typing', (payload = {}) => {
  if (!payload) return;
  const from = ecPmPeerName(payload.from || payload.username || payload.sender || '');
  if (!from) return;
  ecSetConversationTyping('pm', from, from, false, 0);
});

socket.on('group_typing', (payload = {}) => {
  if (!payload) return;
  const gid = Number(payload.group_id || payload.groupId || 0);
  const username = String(payload.username || payload.sender || '').trim();
  if (!gid || !username) return;
  ecSetConversationTyping('group', String(gid), username, payload.typing !== false, payload.expires_in);
});

socket.on('group_stop_typing', (payload = {}) => {
  if (!payload) return;
  const gid = Number(payload.group_id || payload.groupId || 0);
  const username = String(payload.username || payload.sender || '').trim();
  if (!gid || !username) return;
  ecSetConversationTyping('group', String(gid), username, false, 0);
});

function ecGroupCipherFingerprint(value) {
  const text = String(value ?? '');
  if (!text) return '';
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}

function ecGroupRenderedKeys(win) {
  if (!win) return new Set();
  if (!win._groupRenderedKeys) win._groupRenderedKeys = new Set();
  return win._groupRenderedKeys;
}

function ecRememberGroupRenderKey(win, key) {
  if (!win || !key) return true;
  const set = ecGroupRenderedKeys(win);
  if (set.has(key)) return false;
  set.add(key);
  if (set.size > 800) {
    try {
      const keys = Array.from(set);
      win._groupRenderedKeys = new Set(keys.slice(-500));
    } catch {}
  }
  return true;
}

function ecGroupRenderKey(groupId, m = {}, render = {}) {
  const mid = groupMsgId(m);
  if (mid !== null) return `group:${Number(groupId || 0)}:id:${mid}`;
  const sender = String(m?.sender || '').trim().toLowerCase();
  const ts = String(m?.timestamp || m?.ts || render?.ts || 'no-ts');
  const cipher = String(m?.cipher || (String(m?.message || '').startsWith(GROUP_ENVELOPE_PREFIX) ? m.message : '') || '');
  const fp = cipher ? ecGroupCipherFingerprint(cipher) : ecGroupCipherFingerprint(String(render?.text ?? m?.message ?? ''));
  return fp ? `group:${Number(groupId || 0)}:${sender}:${ts}:${fp}` : '';
}

function ecGroupEncryptionState() {
  if (typeof HAS_WEBCRYPTO !== 'undefined' && !HAS_WEBCRYPTO) {
    return { kind: 'error', label: 'Group E2EE unavailable', detail: 'Use HTTPS, localhost, or 127.0.0.1 so group messages can decrypt.' };
  }
  if (window.myPrivateCryptoKey) {
    return { kind: 'ok', label: 'Group E2EE ready', detail: 'Encrypted group messages can decrypt in this tab.' };
  }
  return { kind: 'warn', label: 'Group E2EE locked', detail: 'Unlock your private key to read encrypted group history.' };
}

function updateGroupWindowStatus(win, groupId, opts = {}) {
  const status = win?._ym?.groupStatus;
  if (!status) return;
  const gid = Number(groupId || win?.dataset?.groupId || 0);
  const enc = ecGroupEncryptionState();
  const lockedCount = Math.max(0, Number(opts.lockedCount ?? win.dataset.groupLockedCount ?? 0) || 0);
  const memberCount = Math.max(0, Number(opts.memberCount ?? win.dataset.groupMemberCount ?? 0) || 0);
  const unread = Math.max(0, Number(opts.unreadCount ?? UIState.groupUnreadCounts?.get?.(gid) ?? UIState.groupUnreadCounts?.get?.(String(gid)) ?? 0) || 0);
  const role = currentGroupRole(gid);
  const roleLabel = groupMemberRoleLabel(role);

  status.className = `ym-dmStatus ym-groupStatus ym-groupStatus--${enc.kind}`;
  ecClearNode(status);

  const textWrap = document.createElement('div');
  textWrap.className = 'ym-dmStatusText ym-groupStatusText';
  const main = document.createElement('div');
  main.className = 'ym-dmStatusMain ym-groupStatusMain';
  main.textContent = lockedCount > 0 ? `${enc.label} · ${lockedCount} locked` : enc.label;
  const meta = document.createElement('div');
  meta.className = 'ym-dmStatusMeta ym-groupStatusMeta';
  const parts = [];
  if (unread > 0) parts.push(`${unread} unread`);
  parts.push(`${memberCount} member${memberCount === 1 ? '' : 's'}`);
  parts.push(roleLabel);
  parts.push(enc.detail);
  meta.textContent = parts.join(' · ');
  textWrap.appendChild(main);
  textWrap.appendChild(meta);
  status.appendChild(textWrap);

  const actions = document.createElement('div');
  actions.className = 'ym-dmStatusActions ym-groupStatusActions';
  if (enc.kind !== 'ok') {
    const unlock = document.createElement('button');
    unlock.type = 'button';
    unlock.className = 'ym-dmStatusBtn ym-groupStatusBtn';
    unlock.textContent = 'Unlock';
    unlock.title = 'Unlock private key and reload encrypted group history';
    unlock.onclick = async () => {
      try {
        unlock.disabled = true;
        if (typeof ensurePrivateKeyUnlocked === 'function') await ensurePrivateKeyUnlocked();
        await reloadGroupLatestHistory(win, gid, { toast: true });
      } catch (e) {
        toast(`🔒 Unlock failed: ${e?.message || e}`, 'error');
      } finally {
        unlock.disabled = false;
        updateGroupWindowStatus(win, gid);
      }
    };
    actions.appendChild(unlock);
  }
  const refresh = document.createElement('button');
  refresh.type = 'button';
  refresh.className = 'ym-dmStatusBtn ym-groupStatusBtn';
  refresh.textContent = 'Refresh';
  refresh.title = 'Refresh group members and read state';
  refresh.onclick = async () => {
    try {
      refresh.disabled = true;
      await refreshGroupMemberRoster(gid, win, { toast: false });
      try { refreshMyGroups(); } catch {}
      updateGroupWindowStatus(win, gid);
    } finally {
      refresh.disabled = false;
    }
  };
  actions.appendChild(refresh);
  status.appendChild(actions);
}

async function groupMessageToRender(m = {}) {
  const isEnc = !!m?.is_encrypted || m?.is_encrypted === 1 || m?.is_encrypted === true || !!m?.cipher;
  const cipher = (m && typeof m.cipher === 'string') ? m.cipher : null;
  let text = String(m?.message ?? '');
  const candidate = cipher || text;
  const result = {
    text,
    encrypted: isEnc || (typeof candidate === 'string' && candidate.startsWith(GROUP_ENVELOPE_PREFIX)),
    decrypted: false,
    locked: false,
    error: false,
    hidden: !!(m?.hidden_legacy || m?.hidden_invalid_cipher),
    readSafe: false,
  };

  if (candidate && typeof candidate === 'string' && candidate.startsWith(GROUP_ENVELOPE_PREFIX)) {
    if (HAS_WEBCRYPTO && window.myPrivateCryptoKey) {
      try {
        result.text = await decryptGroupEnvelope(window.myPrivateCryptoKey, candidate);
        result.decrypted = true;
        result.readSafe = true;
      } catch (e) {
        console.error(e);
        result.text = '🔒 Encrypted message could not be decrypted';
        result.error = true;
        result.readSafe = false;
      }
    } else {
      result.text = '🔒 Encrypted message (unlock to read)';
      result.locked = true;
      result.readSafe = false;
    }
  } else if (result.hidden) {
    result.readSafe = false;
  } else if (isEnc && !cipher) {
    result.text = '🔒 Encrypted message';
    result.error = true;
    result.readSafe = false;
  } else {
    result.readSafe = true;
  }
  return result;
}

async function groupHistoryItemToText(m) {
  return (await groupMessageToRender(m)).text;
}

function parseGroupPayload(plaintext) {
  try {
    if (typeof parseDmPayload === 'function') return parseDmPayload(plaintext);
  } catch {}
  let parsed = null;
  if (typeof plaintext === 'string') {
    const s = plaintext.trim();
    if (s.startsWith('{') && s.endsWith('}')) {
      try { parsed = JSON.parse(s); } catch { parsed = null; }
    }
  } else if (plaintext && typeof plaintext === 'object') {
    parsed = plaintext;
  }
  if (parsed && typeof parsed === 'object') {
    const ec = String(parsed._ec || parsed.kind || parsed.type || '').trim().toLowerCase();
    if (ec === 'file' && parsed.file_id) return { kind: 'file', ...parsed };
    if (ec === 'torrent' || parsed.magnet || parsed.infohash || parsed.infohash_hex) return { kind: 'torrent', t: parsed.t || parsed };
  }
  return { kind: 'text', text: String(plaintext ?? '') };
}

function appendGroupRenderedMessage(win, groupId, sender, render, raw = {}, { mode = 'bottom' } = {}) {
  const gid = Number(groupId || 0);
  const ts = raw?.timestamp || raw?.ts || render?.ts || Date.now();
  const messageId = groupMsgId(raw);
  const direction = ecGroupSameUser(sender, currentUser) ? 'out' : 'in';
  const payload = parseGroupPayload(render?.text ?? '');
  if (payload && payload.kind === 'file') {
    if (!payload.group_id && gid) payload.group_id = gid;
    if (mode === 'top') {
      return { payload, kind: 'file' };
    }
    appendDmPayload(win, `${sender}:`, payload, { peer: gid ? `group:${gid}` : null, direction, ts, messageId, cipher: raw?.cipher || '' });
    return { payload, kind: 'file' };
  }
  if (payload && payload.kind === 'torrent') {
    if (mode === 'top') return { payload, kind: 'torrent' };
    appendDmPayload(win, `${sender}:`, payload, { peer: gid ? `group:${gid}` : null, direction, ts, messageId, cipher: raw?.cipher || '' });
    return { payload, kind: 'torrent' };
  }
  const text = (payload && payload.kind === 'text') ? payload.text : (render?.text ?? '');
  if (mode !== 'top') appendLine(win, `${sender}:`, text, { ts, context: 'group' });
  return { payload: { kind: 'text', text }, kind: parseGifMarker(text) ? 'gif' : 'text' };
}

async function reloadGroupLatestHistory(win, groupId, opts = {}) {
  const gid = Number(groupId || 0);
  if (!win?._ym?.log || !gid) return;
  const res = (typeof ecEmitAck === 'function')
    ? await ecEmitAck('get_group_history', { group_id: gid, limit: GROUP_HISTORY_PAGE_SIZE }, 8500, { connectBannerText: '🔌 Reconnecting before reloading group history…', bannerDelayMs: 1200 })
    : await new Promise((resolve) => socket.emit('get_group_history', { group_id: gid, limit: GROUP_HISTORY_PAGE_SIZE }, (r) => resolve(r || { success: false })));
  if (!res?.success) throw new Error(res?.error || 'Could not reload group history');
  resetChatLogState(win._ym.log);
  ecClearNode(win._ym.log);
  win._groupSeenMessageIds = new Set();
  win._groupRenderedKeys = new Set();
  win.dataset.groupLockedCount = '0';
  await appendGroupHistory(win, Array.isArray(res.history) ? res.history : []);
  const st = groupHistState(win);
  st.done = (Array.isArray(res.history) ? res.history.length : 0) < GROUP_HISTORY_PAGE_SIZE;
  updateOldestId(win, res.history || []);
  updateGroupOlderUI(win);
  if (opts.toast) toast('🔄 Group history reloaded', 'ok', 1600);
}


function groupWindowIsVisible(win) {
  try {
    return !!win && !win.classList.contains('hidden') && !!document.body?.contains(win);
  } catch {
    return false;
  }
}

function shouldNotifyGroupMessage(win, groupId, sender) {
  const from = String(sender || '').trim();
  const me = String(currentUser || '').trim().toLowerCase();
  if (!from || (me && from.toLowerCase() === me)) return false;
  // If the group conversation is the active/top PM-group window, the rendered
  // line itself is the notification. Visible-but-background windows still get
  // unread attention so messages are not silently hidden behind another sheet.
  if (typeof ecIsGroupConversationActive === 'function' && ecIsGroupConversationActive(win)) return false;
  if (groupWindowIsVisible(win) && typeof ecIsConversationWindowActive === 'function' && ecIsConversationWindowActive(win)) return false;
  return true;
}


function ecIsGroupConversationActive(win) {
  try {
    if (typeof ecIsConversationWindowActive === 'function') return ecIsConversationWindowActive(win);
    const focused = (typeof ecIsWindowActivelyFocused === 'function')
      ? ecIsWindowActivelyFocused()
      : (document.visibilityState === 'visible' && (!document.hasFocus || document.hasFocus()));
    return !!(groupWindowIsVisible(win) && focused);
  } catch {
    return false;
  }
}

function bumpGroupUnreadCache(groupId, amount = 1) {
  const gid = Number(groupId || 0);
  if (!gid) return;
  const cur = Math.max(0, Number(UIState.groupUnreadCounts?.get?.(gid) ?? UIState.groupUnreadCounts?.get?.(String(gid)) ?? 0) || 0);
  updateGroupUnreadCache(gid, cur + Math.max(1, Number(amount || 1) || 1));
}

function updateGroupUnreadCache(groupId, unreadCount) {
  const gid = Number(groupId || 0);
  const count = Math.max(0, Number(unreadCount || 0) || 0);
  if (!gid) return;
  try {
    if (!UIState.groupUnreadCounts) UIState.groupUnreadCounts = new Map();
    UIState.groupUnreadCounts.set(gid, count);
    UIState.groupUnreadCounts.set(String(gid), count);
    try { updateGroupWindowStatus(UIState.windows.get('group:' + String(gid)), gid, { unreadCount: count }); } catch {}
    if (Array.isArray(UIState.myGroups)) {
      const idx = UIState.myGroups.findIndex((g) => Number(g?.id || g?.group_id || 0) === gid);
      if (idx >= 0) UIState.myGroups[idx] = { ...UIState.myGroups[idx], unread_count: count, unread: count };
    }
    try { refreshMyGroups(); } catch {}
    try { updateDockSummaryCounts(); } catch {}
  } catch {}
}

function markGroupMessagesRead(groupId, messageIds) {
  const gid = Number(groupId || 0);
  if (!gid) return;
  const ids = [];
  const seen = new Set();
  (Array.isArray(messageIds) ? messageIds : [messageIds]).forEach((raw) => {
    const mid = groupMsgId({ message_id: raw });
    if (mid === null || seen.has(mid)) return;
    seen.add(mid);
    ids.push(mid);
  });
  if (!ids.length) return;
  try {
    // Legacy single-message equivalent: socket.emit('mark_group_read', { group_id: gid, message_id: mid }
    socket.emit('mark_group_read', { group_id: gid, message_ids: ids }, (res) => {
      if (res?.success && typeof res.unread_count !== 'undefined') updateGroupUnreadCache(gid, res.unread_count);
      try { updateGroupWindowStatus(UIState.windows.get('group:' + String(gid)), gid); } catch {}
    });
  } catch {}
}

function markVisibleGroupMessageRead(groupId, messageId) {
  markGroupMessagesRead(groupId, [messageId]);
}

async function ecJoinGroupChatAck(groupId) {
  const gid = Number(groupId || 0);
  if (!gid) return { success: false, error: 'bad_group_id' };
  return (typeof ecEmitAck === 'function')
    ? await ecEmitAck('join_group_chat', { group_id: gid }, 8500, { connectBannerText: '🔌 Reconnecting before opening group chat…' })
    : await new Promise((resolve) => socket.emit('join_group_chat', { group_id: gid }, (res) => resolve(res || { success: false })));
}

async function ecLeaveGroupChatAck(groupId) {
  const gid = Number(groupId || 0);
  if (!gid) return { success: false, error: 'bad_group_id' };
  return (typeof ecEmitAck === 'function')
    ? await ecEmitAck('leave_group_chat', { group_id: gid }, 3500, { connectBannerText: '🔌 Reconnecting before leaving group chat…', bannerDelayMs: 1200 })
    : await new Promise((resolve) => socket.emit('leave_group_chat', { group_id: gid }, (res) => resolve(res || { success: false })));
}

function updateGroupOlderUI(win) {
  const st = groupHistState(win);
  const btn = win?._ym?.groupOlderBtn;
  const hint = win?._ym?.groupOlderHint;
  if (!btn) return;
  btn.disabled = !!st.loading || !!st.done || !st.oldestId;
  if (hint) hint.textContent = st.loading ? "Loading…" : (st.done ? "No more" : "Older");
}

function ensureGroupHistoryToolbar(win, groupId) {
  if (!win || !win._ym?.log) return;
  if (win._ym.groupOlderBtn) return;

  const body = win.querySelector('.ym-body');
  if (!body) return;

  const bar = document.createElement('div');
  bar.className = 'ym-toolbar ym-groupToolbar';

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ym-toolBtn';
  btn.title = 'Load older messages';
  btn.textContent = '⬆';

  const hint = document.createElement('span');
  hint.className = 'ym-toolHint';
  hint.textContent = 'Older';

  bar.appendChild(btn);
  bar.appendChild(hint);

  const parent = win._ym.log?.parentElement || body;
  parent.insertBefore(bar, win._ym.log);

  win._ym.groupOlderBtn = btn;
  win._ym.groupOlderHint = hint;
  btn.onclick = () => loadOlderGroupHistory(win, groupId);

  updateGroupOlderUI(win);
}


async function appendGroupHistory(win, hist) {
  const log = win?._ym?.log;
  if (!log) return;
  const id = String(win?._ym?.id || "");
  const gid = id.startsWith("group:") ? Number(id.split(":")[1]) : Number(win?.dataset?.groupId || 0) || null;
  const renderedIds = [];
  let lockedCount = 0;

  for (const m of (hist || [])) {
    const messageId = groupMsgId(m);
    if (messageId !== null && hasSeenGroupMessageId(win, messageId)) continue;
    const render = await groupMessageToRender(m);
    const renderKey = ecGroupRenderKey(gid, m, render);
    if (renderKey && !ecRememberGroupRenderKey(win, renderKey)) continue;
    if (messageId !== null) rememberGroupMessageId(win, messageId);

    const sender = String(m?.sender || "?");
    appendGroupRenderedMessage(win, gid, sender, render, m);
    if (render.locked || render.error || render.hidden) lockedCount += 1;
    if (render.readSafe && messageId !== null) renderedIds.push(messageId);
  }
  if (lockedCount) win.dataset.groupLockedCount = String((Number(win.dataset.groupLockedCount || 0) || 0) + lockedCount);
  if (gid && renderedIds.length) markGroupMessagesRead(gid, renderedIds);
  updateGroupWindowStatus(win, gid);
  scheduleScrollLogToBottom(log);
}

async function insertGroupHistoryAtTop(win, hist) {
  const log = win?._ym?.log;
  if (!log) return;

  const id = String(win?._ym?.id || "");
  const gid = id.startsWith("group:") ? Number(id.split(":")[1]) : Number(win?.dataset?.groupId || 0) || null;
  const renderedIds = [];
  let lockedCount = 0;

  const beforeH = log.scrollHeight;
  const beforeTop = log.scrollTop;

  const temp = document.createElement("div");
  for (const m of (hist || [])) {
    const messageId = groupMsgId(m);
    if (messageId !== null && hasSeenGroupMessageId(win, messageId)) continue;
    const render = await groupMessageToRender(m);
    const renderKey = ecGroupRenderKey(gid, m, render);
    if (renderKey && !ecRememberGroupRenderKey(win, renderKey)) continue;
    if (messageId !== null) rememberGroupMessageId(win, messageId);

    const sender = String(m?.sender || "?");
    const ts = m?.timestamp || m?.ts || null;
    const direction = ecGroupSameUser(sender, currentUser) ? "out" : "in";
    const payloadInfo = appendGroupRenderedMessage(win, gid, sender, render, m, { mode: "top" });
    const payload = payloadInfo?.payload || { kind: "text", text: render.text };

    if (payload && payload.kind === "file") {
      if (!payload.group_id && gid) payload.group_id = gid;
      appendGenericMessageItem(temp, `${sender}:`, buildFileCardElement(payload, { peer: gid ? `group:${gid}` : null, direction }), { ts, kind: "file", context: "group" });
    } else if (payload && payload.kind === "torrent") {
      appendGenericMessageItem(temp, `${sender}:`, buildTorrentCard(payload.t || payload), { ts, kind: "torrent", context: "group" });
    } else {
      const text = String(payload?.text ?? render.text ?? "");
      appendGenericMessageItem(temp, `${sender}:`, buildTextMessageBody(text), { ts, kind: parseGifMarker(text) ? "gif" : "text", context: "group" });
    }

    if (render.locked || render.error || render.hidden) lockedCount += 1;
    if (render.readSafe && messageId !== null) renderedIds.push(messageId);
  }

  const first = log.firstElementChild;
  const incomingLastDate = temp._ecChatUi?.lastDateKey || null;
  if (incomingLastDate && first?.classList?.contains("ec-dateSep") && first.dataset?.dateKey === incomingLastDate) {
    try { first.remove(); } catch {}
  }

  while (temp.firstChild) {
    log.insertBefore(temp.firstChild, log.firstChild);
  }

  if (lockedCount) win.dataset.groupLockedCount = String((Number(win.dataset.groupLockedCount || 0) || 0) + lockedCount);
  if (gid && renderedIds.length) markGroupMessagesRead(gid, renderedIds);
  updateGroupWindowStatus(win, gid);
  const afterH = log.scrollHeight;
  log.scrollTop = beforeTop + (afterH - beforeH);
}

function updateOldestId(win, hist) {
  const st = groupHistState(win);
  const ids = (hist || []).map(groupMsgId).filter((x) => x !== null);
  if (ids.length) {
    const minId = Math.min(...ids);
    st.oldestId = (st.oldestId === null || st.oldestId === undefined) ? minId : Math.min(st.oldestId, minId);
  }
}

function loadOlderGroupHistory(win, groupId) {
  const st = groupHistState(win);
  if (st.loading || st.done) return;
  if (!st.oldestId) {
    st.done = true;
    updateGroupOlderUI(win);
    return;
  }

  st.loading = true;
  updateGroupOlderUI(win);

  const historyRequest = (typeof ecEmitAck === 'function')
    ? ecEmitAck('get_group_history', { group_id: Number(groupId), before_id: st.oldestId, limit: GROUP_HISTORY_PAGE_SIZE }, 8500, { connectBannerText: '🔌 Reconnecting before loading group history…', bannerDelayMs: 1200 })
    : new Promise((resolve) => socket.emit('get_group_history', { group_id: Number(groupId), before_id: st.oldestId, limit: GROUP_HISTORY_PAGE_SIZE }, (res) => resolve(res || { success: false })));

  historyRequest.then(async (res) => {
    st.loading = false;
    if (!res?.success) {
      updateGroupOlderUI(win);
      toast('❌ Could not load older messages', 'error');
      return;
    }

    const hist = Array.isArray(res.history) ? res.history : [];
    if (!hist.length) {
      st.done = true;
      updateGroupOlderUI(win);
      return;
    }

    await insertGroupHistoryAtTop(win, hist);
    updateOldestId(win, hist);
    if (hist.length < GROUP_HISTORY_PAGE_SIZE) st.done = true;
    updateGroupOlderUI(win);
  }).catch(() => {
    st.loading = false;
    updateGroupOlderUI(win);
    toast('❌ Could not load older messages', 'error');
  });
}


function normalizeGroupMemberDetails(rawMembers, fallbackMembers = []) {
  const rows = [];
  const seen = new Set();
  const validRole = (role) => {
    const r = String(role || 'member').trim().toLowerCase();
    return ['owner', 'admin', 'moderator', 'member'].includes(r) ? r : 'member';
  };
  const pushRow = (username, role = 'member', extra = {}) => {
    const name = String(username || '').trim();
    if (!name) return;
    const key = name.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    const normalizedRole = validRole(role);
    rows.push({
      username: name,
      role: normalizedRole,
      role_label: extra.role_label || groupMemberRoleLabel(normalizedRole),
      role_rank: Number.isFinite(Number(extra.role_rank)) ? Number(extra.role_rank) : groupRoleRank(normalizedRole),
      capabilities: extra.capabilities || null,
      is_self: Boolean(extra.is_self),
      joined_at: extra.joined_at || '',
    });
  };

  (Array.isArray(rawMembers) ? rawMembers : []).forEach((row) => {
    if (typeof row === 'string') pushRow(row, 'member');
    else if (row && typeof row === 'object') pushRow(row.username || row.name || row.user, row.role || 'member', row);
  });
  (Array.isArray(fallbackMembers) ? fallbackMembers : []).forEach((row) => {
    if (typeof row === 'string') pushRow(row, 'member');
    else if (row && typeof row === 'object') pushRow(row.username || row.name || row.user, row.role || 'member', row);
  });

  const rank = { owner: 0, admin: 1, moderator: 2, member: 3 };
  rows.sort((a, b) => {
    const ar = rank[a.role] ?? 9;
    const br = rank[b.role] ?? 9;
    if (ar !== br) return ar - br;
    return a.username.localeCompare(b.username, undefined, { sensitivity: 'base' });
  });
  return rows;
}

function rememberGroupMembersFromResponse(groupId, res = {}) {
  const gid = Number(groupId || 0);
  if (!gid) return [];
  const details = normalizeGroupMemberDetails(res?.member_details, res?.members);
  const names = details.map((m) => m.username);
  UIState.groupMembers.set(gid, names);
  UIState.groupMemberDetails.set(gid, details);
  if (typeof res?.unread_count !== 'undefined') updateGroupUnreadCache(gid, res.unread_count);
  try { updateGroupWindowStatus(UIState.windows.get('group:' + String(gid)), gid, { memberCount: details.length }); } catch {}
  try {
    if (Array.isArray(UIState.myGroups)) {
      const idx = UIState.myGroups.findIndex((g) => Number(g?.id || g?.group_id || 0) === gid);
      if (idx >= 0) {
        UIState.myGroups[idx] = {
          ...UIState.myGroups[idx],
          role: res?.role || res?.current_role || UIState.myGroups[idx].role,
          role_label: res?.role_label || UIState.myGroups[idx].role_label,
          role_rank: Number.isFinite(Number(res?.role_rank)) ? Number(res.role_rank) : UIState.myGroups[idx].role_rank,
          capabilities: res?.capabilities || res?.current_capabilities || UIState.myGroups[idx].capabilities,
          member_count: Number(res?.total || details.length || UIState.myGroups[idx].member_count || 0) || 0,
        };
      }
    }
  } catch {}
  return details;
}

function groupMemberPresence(username) {
  const exact = UIState.presence?.get?.(username);
  let p = exact;
  if (!p && UIState.presence && typeof UIState.presence.forEach === 'function') {
    const wanted = String(username || '').toLowerCase();
    UIState.presence.forEach((value, key) => {
      if (!p && String(key || '').toLowerCase() === wanted) p = value;
    });
  }
  const presence = String(p?.presence || (p?.online ? 'online' : 'offline')).toLowerCase();
  if (!p?.online || presence === 'offline' || presence === 'invisible') return { className: 'offline', label: 'Offline' };
  if (presence === 'busy') return { className: 'busy', label: 'Busy' };
  if (presence === 'away') return { className: 'away', label: 'Away' };
  return { className: 'online', label: 'Online' };
}

function groupMemberRoleLabel(role) {
  switch (String(role || 'member').toLowerCase()) {
    case 'owner': return 'Owner';
    case 'admin': return 'Admin';
    case 'moderator': return 'Moderator';
    default: return 'Member';
  }
}


const EC_GROUP_ROLE_RANK = { member: 0, moderator: 1, admin: 2, owner: 3 };

function groupRoleRank(role) {
  return EC_GROUP_ROLE_RANK[String(role || 'member').toLowerCase()] ?? 0;
}

function groupMemberDetailFor(groupId, username) {
  const gid = Number(groupId || 0);
  const wanted = String(username || '').trim().toLowerCase();
  if (!gid || !wanted) return null;
  const details = normalizeGroupMemberDetails(UIState.groupMemberDetails?.get?.(gid), UIState.groupMembers?.get?.(gid));
  return details.find((m) => String(m.username || '').toLowerCase() === wanted) || null;
}

function groupMetaFromCache(groupId, title = '') {
  const gid = Number(groupId || 0);
  const cached = (Array.isArray(UIState.myGroups) ? UIState.myGroups : []).find((g) => Number(g?.id || 0) === gid) || null;
  const titleName = String(title || '').replace(/\s*\(#\d+\)\s*$/, '').replace(/^Group\s+—\s+/, '').trim();
  const groupName = String(cached?.group_name || titleName || (gid ? `Group #${gid}` : 'Group')).trim();
  const groupDescription = String(cached?.group_description || '').trim();
  const cachedRole = String(cached?.role || '').trim().toLowerCase();
  return {
    group_id: gid,
    group_name: groupName,
    group_description: groupDescription,
    role: cachedRole || '',
  };
}

function currentGroupRole(groupId) {
  const gid = Number(groupId || 0);
  const cached = groupMetaFromCache(gid)?.role;
  return cached || groupMemberDetailFor(gid, currentUser)?.role || 'member';
}

function currentGroupCanModerate(groupId, targetUsername, minRole = 'moderator') {
  const me = currentGroupRole(groupId);
  const mine = groupRoleRank(me);
  const min = groupRoleRank(minRole);
  if (mine < min) return false;
  const target = groupMemberDetailFor(groupId, targetUsername);
  if (!target) return false;
  return mine > groupRoleRank(target.role);
}

function groupVoiceUserIsActive(groupId, username) {
  const gid = Number(groupId || 0);
  const name = String(username || '').trim();
  if (!gid || !name || typeof voiceMediaMapForRoom !== 'function') return false;
  try {
    const st = voiceMediaMapForRoom(groupVoiceRoomName(gid))?.get?.(name);
    return !!(st && st.voice_on);
  } catch {
    return false;
  }
}

function refreshGroupVoiceIndicatorsForRoom(room) {
  const gid = groupVoiceRoomIdFromName(room);
  if (!gid) return;
  const win = UIState.windows.get('group:' + String(gid));
  if (win) renderGroupMemberRoster(win, gid);
}

function groupDisplayError(e, fallback = 'Group action failed') {
  return String(e?.message || e?.error || e || fallback || 'Group action failed');
}

async function groupApiPost(groupId, path, body = {}) {
  const gid = Number(groupId || 0);
  if (!gid) throw new Error('Missing group id');
  return apiJson(`/api/groups/${encodeURIComponent(gid)}${path}`, {
    method: 'POST',
    body: JSON.stringify(body || {}),
  });
}

function groupMutedSetFor(groupId) {
  const gid = Number(groupId || 0);
  return UIState.groupMutedMembers?.get?.(gid) || new Set();
}

function groupMemberIsMuted(groupId, username) {
  const name = String(username || '').trim().toLowerCase();
  if (!name) return false;
  try { return groupMutedSetFor(groupId).has(name); } catch { return false; }
}

async function groupRefreshMutes(groupId, opts = {}) {
  const gid = Number(groupId || 0);
  if (!gid) return { success: false, mutes: [] };
  try {
    const res = await apiJson(`/api/groups/${encodeURIComponent(gid)}/mutes`, { method: 'GET' });
    const rows = Array.isArray(res?.mutes) ? res.mutes : [];
    const names = new Set(rows.map((m) => String(m?.username || '').trim().toLowerCase()).filter(Boolean));
    UIState.groupMutedMembers?.set?.(gid, names);
    if (!opts.silent && rows.length) toast(`🔇 ${rows.length} muted member${rows.length === 1 ? '' : 's'} in this group`, 'info');
    return { success: true, mutes: rows };
  } catch (e) {
    if (!opts.silent) toast(`❌ Could not load group mute list: ${groupDisplayError(e)}`, 'error');
    return { success: false, error: groupDisplayError(e), mutes: [] };
  }
}

async function groupRefreshAfterAction(groupId) {
  const gid = Number(groupId || 0);
  if (!gid) return;
  try {
    const win = UIState.windows.get('group:' + String(gid));
    await refreshGroupMemberRoster(gid, win || null);
  } catch {}
  try { await groupRefreshMutes(gid, { silent: true }); } catch {}
  try { renderGroupSettingsMembers(gid); renderGroupSettingsMutes(gid); } catch {}
  try { refreshMyGroups(); } catch {}
}

async function groupMuteMember(groupId, username) {
  const u = String(username || '').trim();
  if (!u) return;
  try {
    await groupApiPost(groupId, '/mute', { username: u });
    toast(`🔇 Muted ${u} in this group`, 'ok');
    await groupRefreshAfterAction(groupId);
  } catch (e) {
    toast(`❌ Mute failed: ${groupDisplayError(e)}`, 'error');
  }
}

async function groupUnmuteMember(groupId, username) {
  const u = String(username || '').trim();
  if (!u) return;
  try {
    await groupApiPost(groupId, '/unmute', { username: u });
    toast(`🎤 Unmuted ${u} in this group`, 'ok');
    await groupRefreshAfterAction(groupId);
  } catch (e) {
    toast(`❌ Unmute failed: ${groupDisplayError(e)}`, 'error');
  }
}

async function groupKickMember(groupId, username) {
  const u = String(username || '').trim();
  if (!u) return;
  const ok = await ecConfirm(`Remove ${u} from this group?`, {
    title: `Kick ${u}?`,
    confirmLabel: 'Remove from group',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  try {
    await groupApiPost(groupId, '/kick', { username: u });
    toast(`👢 Removed ${u} from the group`, 'ok');
    await groupRefreshAfterAction(groupId);
  } catch (e) {
    toast(`❌ Kick failed: ${groupDisplayError(e)}`, 'error');
  }
}

async function groupSetMemberRole(groupId, username, role) {
  const u = String(username || '').trim();
  const r = String(role || '').trim().toLowerCase();
  if (!u || !r) return;
  const label = groupMemberRoleLabel(r);
  const ok = await ecConfirm(`Change ${u}'s group role to ${label}?`, {
    title: `Set ${u} as ${label}?`,
    confirmLabel: 'Change role',
    focusCancel: true,
  });
  if (!ok) return;
  try {
    await groupApiPost(groupId, '/set_role', { username: u, role: r });
    toast(`✅ ${u} is now ${label}`, 'ok');
    await groupRefreshAfterAction(groupId);
  } catch (e) {
    toast(`❌ Role change failed: ${groupDisplayError(e)}`, 'error');
  }
}

async function groupTransferOwnership(groupId, username) {
  const u = String(username || '').trim();
  if (!u) return;
  const ok = await ecConfirm(`Transfer ownership of this group to ${u}? You will become an admin.`, {
    title: 'Transfer group ownership?',
    confirmLabel: 'Transfer ownership',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  try {
    await groupApiPost(groupId, '/transfer_ownership', { username: u });
    toast(`👑 ${u} is now the group owner`, 'ok');
    await groupRefreshAfterAction(groupId);
  } catch (e) {
    toast(`❌ Ownership transfer failed: ${groupDisplayError(e)}`, 'error');
  }
}

async function groupKickMemberFromVoice(groupId, username) {
  const u = String(username || '').trim();
  if (!u) return;
  try {
    await groupApiPost(groupId, '/voice/kick', { username: u });
    toast(`🎤 Disconnected ${u} from group voice`, 'ok');
    refreshGroupVoiceIndicatorsForRoom(groupVoiceRoomName(groupId));
  } catch (e) {
    toast(`❌ Voice disconnect failed: ${groupDisplayError(e)}`, 'error');
  }
}

async function inviteSelectedUserToCurrentRoom(username) {
  const u = String(username || '').trim();
  const room = String(UIState.roomEmbedRoom || UIState.currentRoom || '').trim();
  if (!u) return;
  if (!room) return toast('Join a room first, then invite someone.', 'warn');
  try {
    await apiJson('/api/rooms/invite', { method: 'POST', body: JSON.stringify({ room, invitee: u }) });
    toast(`✅ Invited ${u} to ${room}`, 'ok');
  } catch (e) {
    toast(`❌ Room invite failed: ${groupDisplayError(e)}`, 'error');
  }
}

function ecSetGroupWindowTitle(win, groupId, groupName) {
  const gid = Number(groupId || 0);
  const cleanName = String(groupName || '').trim() || `#${gid}`;
  const nextTitle = `Group — ${cleanName} (#${gid})`;
  if (!win || !gid) return nextTitle;
  if (win._ym?.titleEl) {
    win._ym.titleEl.textContent = nextTitle;
    win._ym.titleEl.title = nextTitle;
  }
  win.setAttribute('aria-label', nextTitle);
  win.dataset.windowTitle = nextTitle;
  win.dataset.windowFullTitle = nextTitle;
  try {
    const taskBtn = UIState.minimized?.get?.('group:' + String(gid));
    if (taskBtn) taskBtn.textContent = nextTitle;
  } catch {}
  return nextTitle;
}

async function groupUpdateMetadata(groupId, name, description = '', opts = {}) {
  const gid = Number(groupId || 0);
  const cleanName = String(name || '').trim();
  const cleanDescription = String(description || '').trim();
  if (!gid) return { success: false, error: 'Missing group id' };
  if (!cleanName) return { success: false, error: 'Group name required' };
  if (cleanName.length > 64) return { success: false, error: 'Group name too long (max 64)' };
  if (cleanDescription.length > 512) return { success: false, error: 'Description too long (max 512)' };

  await apiJson(`/api/groups/${encodeURIComponent(gid)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name: cleanName, description: cleanDescription }),
  });

  const win = UIState.windows.get('group:' + String(gid));
  if (win) ecSetGroupWindowTitle(win, gid, cleanName);
  try {
    const idx = Array.isArray(UIState.myGroups) ? UIState.myGroups.findIndex((g) => Number(g?.id || 0) === gid) : -1;
    if (idx >= 0) {
      UIState.myGroups[idx] = { ...UIState.myGroups[idx], group_name: cleanName, group_description: cleanDescription };
    }
  } catch {}
  if (!opts.silent) toast('✅ Group settings saved', 'ok');
  try { refreshMyGroups(); } catch {}
  return { success: true };
}

async function groupRenameDescription(groupId, title = '') {
  const gid = Number(groupId || 0);
  if (!gid) return;
  const meta = groupMetaFromCache(gid, title);
  const role = currentGroupRole(gid);
  if (groupRoleRank(role) < groupRoleRank('admin')) {
    toast('Only group admins and the owner can rename or edit the description.', 'warn');
    return;
  }
  const name = await ecPrompt('New group name:', meta.group_name, {
    title: 'Rename group',
    inputLabel: 'Group name',
    confirmLabel: 'Save',
    maxLength: 64,
  });
  if (name === null) return;
  const desc = await ecPrompt('Group description:', meta.group_description || '', {
    title: 'Group description',
    inputLabel: 'Description',
    confirmLabel: 'Save',
    maxLength: 512,
  });
  if (desc === null) return;
  try {
    const res = await groupUpdateMetadata(gid, name, desc);
    if (!res?.success) toast(`❌ Save failed: ${res?.error || 'Invalid group settings'}`, 'error');
  } catch (e) {
    toast(`❌ Save failed: ${groupDisplayError(e)}`, 'error');
  }
}

async function groupLeaveFromSettings(groupId, title = '') {
  const gid = Number(groupId || 0);
  if (!gid) return;
  const ok = await ecConfirm(`Leave ${title || 'this group'}?`, {
    title: 'Leave group?',
    confirmLabel: 'Leave group',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  try {
    const res = await groupApiPost(gid, '/leave', {});
    forceLeaveGroupUI(gid, res?.status === 'deleted' ? 'deleted' : 'left');
  } catch (e) {
    toast(`❌ Leave failed: ${groupDisplayError(e)}`, 'error');
  }
}

async function groupDeleteFromSettings(groupId, title = '') {
  const gid = Number(groupId || 0);
  if (!gid) return;
  const ok = await ecConfirm(`Delete ${title || 'this group'} for everyone? This cannot be undone.`, {
    title: 'Delete group?',
    confirmLabel: 'Delete group',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  try {
    await apiJson(`/api/groups/${encodeURIComponent(gid)}`, { method: 'DELETE' });
    forceLeaveGroupUI(gid, 'deleted');
  } catch (e) {
    toast(`❌ Delete failed: ${groupDisplayError(e)}`, 'error');
  }
}

let EC_GROUP_SETTINGS_MODAL = null;
let EC_GROUP_SETTINGS_ACTIVE = null;

function groupSettingsDetailSnapshot(modal = EC_GROUP_SETTINGS_MODAL) {
  if (!modal) return { name: '', description: '' };
  return {
    name: String(modal.querySelector('#ecGroupSettingsName')?.value || '').trim(),
    description: String(modal.querySelector('#ecGroupSettingsDescription')?.value || '').trim(),
  };
}

function updateGroupSettingsSaveState() {
  const modal = EC_GROUP_SETTINGS_MODAL;
  const active = EC_GROUP_SETTINGS_ACTIVE;
  if (!modal || !active) return;
  const saveBtn = modal.querySelector('#ecGroupSettingsSave');
  const dirtyEl = modal.querySelector('#ecGroupSettingsDirty');
  const nameCount = modal.querySelector('#ecGroupSettingsNameCount');
  const descCount = modal.querySelector('#ecGroupSettingsDescriptionCount');
  const snap = groupSettingsDetailSnapshot(modal);
  if (nameCount) nameCount.textContent = `${snap.name.length} / 64`;
  if (descCount) descCount.textContent = `${snap.description.length} / 512`;
  const changed = snap.name !== String(active.originalName || '') || snap.description !== String(active.originalDescription || '');
  const valid = !!snap.name && snap.name.length <= 64 && snap.description.length <= 512;
  if (dirtyEl) {
    dirtyEl.textContent = changed ? 'Unsaved changes' : 'Saved';
    dirtyEl.classList.toggle('dirty', changed);
  }
  if (saveBtn) {
    saveBtn.disabled = !active.canEditMeta || !changed || !valid;
    saveBtn.textContent = changed ? 'Save changes' : 'No changes';
  }
}

function groupSettingsStats(groupId) {
  const gid = Number(groupId || 0);
  const details = normalizeGroupMemberDetails(UIState.groupMemberDetails?.get?.(gid), UIState.groupMembers?.get?.(gid));
  let online = 0;
  let voice = 0;
  details.forEach((member) => {
    const username = String(member.username || '').trim();
    const presence = groupMemberPresence(username);
    if (presence.className !== 'offline') online += 1;
    if (groupVoiceUserIsActive(gid, username)) voice += 1;
  });
  return { total: details.length, online, voice, details };
}

function updateGroupSettingsStats(groupId) {
  const modal = EC_GROUP_SETTINGS_MODAL;
  if (!modal) return;
  const stats = groupSettingsStats(groupId);
  const memberEl = modal.querySelector('#ecGroupSettingsMemberCount');
  const onlineEl = modal.querySelector('#ecGroupSettingsOnlineCount');
  const voiceEl = modal.querySelector('#ecGroupSettingsVoiceCount');
  if (memberEl) memberEl.textContent = `${stats.total} member${stats.total === 1 ? '' : 's'}`;
  if (onlineEl) onlineEl.textContent = `${stats.online} online`;
  if (voiceEl) voiceEl.textContent = `${stats.voice} in voice`;
  return stats;
}

function renderGroupSettingsMembers(groupId) {
  const modal = EC_GROUP_SETTINGS_MODAL;
  if (!modal) return;
  const gid = Number(groupId || 0);
  const list = modal.querySelector('#ecGroupSettingsMembersList');
  const summary = modal.querySelector('#ecGroupSettingsMembersSummary');
  if (!list) return;
  ecClearNode(list);
  const stats = updateGroupSettingsStats(gid) || groupSettingsStats(gid);
  const q = String(modal.querySelector('#ecGroupSettingsMemberSearch')?.value || '').trim().toLowerCase();
  const matches = q ? stats.details.filter((m) => `${m.username} ${m.role}`.toLowerCase().includes(q)) : stats.details;
  if (summary) {
    summary.textContent = q
      ? `${matches.length} matching member${matches.length === 1 ? '' : 's'} of ${stats.total}`
      : `${stats.total} group member${stats.total === 1 ? '' : 's'} shown by role`;
  }
  if (!matches.length) {
    list.appendChild(ecCreateEl('li', { className: 'ecGroupSettingsMemberEmpty', text: q ? 'No matching members.' : 'No group members loaded yet.' }));
    return;
  }
  matches.slice(0, 80).forEach((member) => {
    const username = String(member.username || '').trim();
    if (!username) return;
    const roleLabel = groupMemberRoleLabel(member.role);
    const presence = groupMemberPresence(username);
    const voiceActive = groupVoiceUserIsActive(gid, username);
    const mutedInGroup = groupMemberIsMuted(gid, username);
    const isSelf = String(username).toLowerCase() === String(currentUser || '').toLowerCase();
    const li = ecCreateEl('li', { className: `ecGroupSettingsMember ${presence.className}` });
    const left = ecCreateEl('button', { className: 'ecGroupSettingsMemberMain', type: 'button', attrs: { title: isSelf ? 'This is you' : `Open private chat with ${username}` } }, [
      ecCreateEl('span', { className: `presenceDot ${presence.className}` }),
      ecCreateEl('span', { className: 'ecGroupSettingsMemberName', text: username }),
      ecCreateEl('span', { className: 'ecGroupSettingsMemberMeta', text: `${roleLabel} · ${presence.label}${voiceActive ? ' · Voice' : ''}${mutedInGroup ? ' · Muted' : ''}` }),
    ]);
    left.disabled = isSelf;
    left.addEventListener('click', () => { if (!isSelf) openPrivateChat(username); });
    const role = ecCreateEl('span', { className: `ecGroupSettingsRoleBadge role-${String(member.role || 'member').toLowerCase()}`, text: isSelf ? 'You' : roleLabel });
    li.appendChild(left);
    li.appendChild(role);
    list.appendChild(li);
  });
  if (matches.length > 80) {
    list.appendChild(ecCreateEl('li', { className: 'ecGroupSettingsMemberEmpty', text: `Showing first 80 of ${matches.length}. Use search to narrow the list.` }));
  }
}

function renderGroupSettingsMutes(groupId) {
  const modal = EC_GROUP_SETTINGS_MODAL;
  if (!modal) return;
  const gid = Number(groupId || 0);
  const panel = modal.querySelector('#ecGroupSettingsMutedPanel');
  const list = modal.querySelector('#ecGroupSettingsMutedList');
  const summary = modal.querySelector('#ecGroupSettingsMutedSummary');
  if (!panel || !list) return;
  const canModerate = groupRoleRank(currentGroupRole(gid)) >= groupRoleRank('moderator');
  panel.classList.toggle('hidden', !canModerate);
  ecClearNode(list);
  if (!canModerate) return;
  const muted = Array.from(groupMutedSetFor(gid));
  if (summary) summary.textContent = muted.length ? `${muted.length} muted member${muted.length === 1 ? '' : 's'}` : 'No muted members.';
  if (!muted.length) {
    list.appendChild(ecCreateEl('li', { className: 'ecGroupSettingsMemberEmpty', text: 'Nobody is muted in this group.' }));
    return;
  }
  muted.slice(0, 80).forEach((lowerName) => {
    const detail = groupMemberDetailFor(gid, lowerName);
    const username = String(detail?.username || lowerName || '').trim();
    const li = ecCreateEl('li', { className: 'ecGroupSettingsMember muted' });
    li.appendChild(ecCreateEl('span', { className: 'ecGroupSettingsMemberName', text: username }));
    const btn = ecCreateEl('button', { className: 'ghostBtn smallBtn', type: 'button', text: 'Unmute' });
    btn.addEventListener('click', () => groupUnmuteMember(gid, username));
    li.appendChild(btn);
    list.appendChild(li);
  });
}

function runGroupSettingsCommandChip(command) {
  const active = EC_GROUP_SETTINGS_ACTIVE;
  const modal = EC_GROUP_SETTINGS_MODAL;
  if (!active?.groupId || !modal) return;
  const cmd = String(command || '').trim().toLowerCase();
  if (cmd === 'invite') {
    modal.querySelector('#ecGroupSettingsInviteInput')?.focus?.();
    return;
  }
  if (cmd === 'rename') {
    modal.querySelector('#ecGroupSettingsName')?.focus?.();
    return;
  }
  if (cmd === 'desc') {
    modal.querySelector('#ecGroupSettingsDescription')?.focus?.();
    return;
  }
  if (cmd === 'voice') {
    modal.querySelector('#ecGroupSettingsVoice')?.click?.();
    return;
  }
  if (cmd === 'users') {
    modal.querySelector('#ecGroupSettingsRefresh')?.click?.();
  }
}

function closeGroupSettingsModal() {
  if (!EC_GROUP_SETTINGS_MODAL) return;
  const active = EC_GROUP_SETTINGS_ACTIVE;
  EC_GROUP_SETTINGS_ACTIVE = null;
  EC_GROUP_SETTINGS_MODAL.classList.add('hidden');
  try { active?.restoreFocus?.focus?.(); } catch {}
}

function ensureGroupSettingsModal() {
  if (EC_GROUP_SETTINGS_MODAL) return EC_GROUP_SETTINGS_MODAL;

  const modal = ecCreateEl('div', {
    id: 'ecGroupSettingsModal',
    className: 'modal hidden ecGroupSettingsModal',
    role: 'dialog',
    ariaModal: 'true',
    attrs: { 'aria-labelledby': 'ecGroupSettingsTitle' },
  });
  const card = ecCreateEl('div', { className: 'modalCard ecGroupSettingsCard' });

  const closeBtn = ecCreateEl('button', { id: 'ecGroupSettingsClose', className: 'iconBtn', type: 'button', text: '×', attrs: { 'aria-label': 'Close group settings' } });
  card.appendChild(ecCreateEl('div', { className: 'modalHead ecGroupSettingsHead' }, [
    ecCreateEl('div', {}, [
      ecCreateEl('div', { id: 'ecGroupSettingsTitle', className: 'modalTitle', text: 'Group settings' }),
      ecCreateEl('div', { id: 'ecGroupSettingsSubTitle', className: 'ecGroupSettingsSubTitle', text: 'Manage this group without typing actions.' }),
    ]),
    closeBtn,
  ]));

  const nameInput = ecCreateEl('input', { id: 'ecGroupSettingsName', className: 'modalInput ecGroupSettingsInput', type: 'text', autocomplete: 'off', attrs: { maxlength: '64' } });
  const descInput = ecCreateEl('textarea', { id: 'ecGroupSettingsDescription', className: 'modalInput ecGroupSettingsTextarea', attrs: { maxlength: '512', rows: '4' } });
  const inviteInput = ecCreateEl('input', { id: 'ecGroupSettingsInviteInput', className: 'modalInput ecGroupSettingsInviteInput', type: 'text', autocomplete: 'off', attrs: { maxlength: '80', placeholder: 'username' } });
  const memberSearch = ecCreateEl('input', { id: 'ecGroupSettingsMemberSearch', className: 'modalInput ecGroupSettingsMemberSearch', type: 'search', autocomplete: 'off', attrs: { maxlength: '80', placeholder: 'Filter members…' } });
  const rolePill = ecCreateEl('span', { id: 'ecGroupSettingsRole', className: 'ecGroupSettingsRole', text: 'Member' });
  const permissionHint = ecCreateEl('div', { id: 'ecGroupSettingsPermissionHint', className: 'ecGroupSettingsHint', text: '' });

  const body = ecCreateEl('div', { className: 'modalBody ecGroupSettingsBody' }, [
    ecCreateEl('div', { className: 'ecGroupSettingsHero' }, [
      ecCreateEl('div', {}, [
        ecCreateEl('div', { className: 'ecGroupSettingsLabel', text: 'Current role' }),
        rolePill,
      ]),
      ecCreateEl('div', { id: 'ecGroupSettingsGroupId', className: 'ecGroupSettingsId', text: '#0' }),
    ]),
    ecCreateEl('div', { id: 'ecGroupSettingsStats', className: 'ecGroupSettingsStats', attrs: { 'aria-label': 'Group status summary' } }, [
      ecCreateEl('span', { id: 'ecGroupSettingsMemberCount', className: 'ecGroupSettingsStatPill', text: '0 members' }),
      ecCreateEl('span', { id: 'ecGroupSettingsOnlineCount', className: 'ecGroupSettingsStatPill', text: '0 online' }),
      ecCreateEl('span', { id: 'ecGroupSettingsVoiceCount', className: 'ecGroupSettingsStatPill', text: '0 in voice' }),
    ]),
    ecCreateEl('div', { className: 'ecGroupSettingsPanel' }, [
      ecCreateEl('div', { className: 'ecGroupSettingsPanelHead' }, [
        ecCreateEl('div', { className: 'ecGroupSettingsPanelTitle', text: 'Details' }),
        ecCreateEl('div', { id: 'ecGroupSettingsDirty', className: 'ecGroupSettingsDirty', text: 'Saved' }),
      ]),
      ecCreateEl('div', { className: 'ecGroupSettingsGrid' }, [
        ecCreateEl('div', { className: 'ecGroupSettingsLabelRow' }, [
          ecCreateEl('label', { className: 'fieldLabel', attrs: { for: 'ecGroupSettingsName' }, text: 'Group name' }),
          ecCreateEl('span', { id: 'ecGroupSettingsNameCount', className: 'ecGroupSettingsCounter', text: '0 / 64' }),
        ]),
        nameInput,
        ecCreateEl('div', { className: 'ecGroupSettingsLabelRow' }, [
          ecCreateEl('label', { className: 'fieldLabel', attrs: { for: 'ecGroupSettingsDescription' }, text: 'Description' }),
          ecCreateEl('span', { id: 'ecGroupSettingsDescriptionCount', className: 'ecGroupSettingsCounter', text: '0 / 512' }),
        ]),
        descInput,
        permissionHint,
      ]),
    ]),
    ecCreateEl('div', { className: 'ecGroupSettingsPanel' }, [
      ecCreateEl('div', { className: 'ecGroupSettingsPanelHead' }, [
        ecCreateEl('div', { className: 'ecGroupSettingsPanelTitle', text: 'Invite' }),
        ecCreateEl('div', { className: 'ecGroupSettingsHint', text: 'Same as /invite username' }),
      ]),
      ecCreateEl('div', { className: 'ecGroupSettingsInviteRow' }, [
        inviteInput,
        ecCreateEl('button', { id: 'ecGroupSettingsInviteSend', className: 'primaryBtn', type: 'button', text: 'Send invite' }),
      ]),
    ]),
    ecCreateEl('div', { className: 'ecGroupSettingsPanel' }, [
      ecCreateEl('div', { className: 'ecGroupSettingsPanelHead' }, [
        ecCreateEl('div', { className: 'ecGroupSettingsPanelTitle', text: 'Members' }),
        ecCreateEl('button', { id: 'ecGroupSettingsRefresh', className: 'ghostBtn smallBtn', type: 'button', text: '↻ Refresh' }),
      ]),
      memberSearch,
      ecCreateEl('div', { id: 'ecGroupSettingsMembersSummary', className: 'ecGroupSettingsHint', text: 'Loading members…' }),
      ecCreateEl('ul', { id: 'ecGroupSettingsMembersList', className: 'ecGroupSettingsMembersList', attrs: { 'aria-label': 'Group members' } }),
    ]),
    ecCreateEl('div', { id: 'ecGroupSettingsMutedPanel', className: 'ecGroupSettingsPanel hidden' }, [
      ecCreateEl('div', { className: 'ecGroupSettingsPanelHead' }, [
        ecCreateEl('div', { className: 'ecGroupSettingsPanelTitle', text: 'Muted members' }),
        ecCreateEl('button', { id: 'ecGroupSettingsMutedRefresh', className: 'ghostBtn smallBtn', type: 'button', text: '↻ Refresh' }),
      ]),
      ecCreateEl('div', { id: 'ecGroupSettingsMutedSummary', className: 'ecGroupSettingsHint', text: 'Loading mute list…' }),
      ecCreateEl('ul', { id: 'ecGroupSettingsMutedList', className: 'ecGroupSettingsMembersList', attrs: { 'aria-label': 'Muted group members' } }),
    ]),
    ecCreateEl('div', { className: 'ecGroupSettingsActions', attrs: { 'aria-label': 'Group quick actions' } }, [
      ecCreateEl('button', { id: 'ecGroupSettingsInvite', className: 'ghostBtn', type: 'button', text: '➕ Invite user' }),
      ecCreateEl('button', { id: 'ecGroupSettingsVoice', className: 'ghostBtn', type: 'button', text: '🎤 Toggle voice' }),
      ecCreateEl('button', { id: 'ecGroupSettingsLeave', className: 'ghostBtn dangerText', type: 'button', text: 'Leave group' }),
      ecCreateEl('button', { id: 'ecGroupSettingsDelete', className: 'ghostBtn dangerText', type: 'button', text: 'Delete group' }),
    ]),
    ecCreateEl('div', { className: 'ecGroupSettingsCommandHint' }, [
      ecCreateEl('span', { text: 'Text commands still work: ' }),
      ecCreateEl('button', { className: 'ecGroupCommandChip', type: 'button', text: '/invite', attrs: { 'data-command': 'invite' } }),
      ecCreateEl('button', { className: 'ecGroupCommandChip', type: 'button', text: '/voice', attrs: { 'data-command': 'voice' } }),
      ecCreateEl('button', { className: 'ecGroupCommandChip', type: 'button', text: '/users', attrs: { 'data-command': 'users' } }),
      ecCreateEl('button', { className: 'ecGroupCommandChip', type: 'button', text: '/rename', attrs: { 'data-command': 'rename' } }),
      ecCreateEl('button', { className: 'ecGroupCommandChip', type: 'button', text: '/desc', attrs: { 'data-command': 'desc' } }),
    ]),
  ]);
  card.appendChild(body);
  card.appendChild(ecCreateEl('div', { className: 'modalFoot confirmModalFoot ecGroupSettingsFoot' }, [
    ecCreateEl('button', { id: 'ecGroupSettingsCancel', className: 'ghostBtn', type: 'button', text: 'Cancel' }),
    ecCreateEl('button', { id: 'ecGroupSettingsSave', className: 'primaryBtn', type: 'button', text: 'Save changes' }),
  ]));

  modal.appendChild(card);
  modal.addEventListener('mousedown', (ev) => {
    if (ev.target === modal) closeGroupSettingsModal();
  });
  closeBtn.addEventListener('click', closeGroupSettingsModal);
  card.querySelector('#ecGroupSettingsCancel')?.addEventListener('click', closeGroupSettingsModal);
  card.querySelector('#ecGroupSettingsSave')?.addEventListener('click', async () => {
    const active = EC_GROUP_SETTINGS_ACTIVE;
    if (!active?.groupId) return;
    const saveBtn = card.querySelector('#ecGroupSettingsSave');
    try {
      if (saveBtn) saveBtn.disabled = true;
      const res = await groupUpdateMetadata(active.groupId, nameInput.value, descInput.value);
      if (res?.success) {
        active.originalName = String(nameInput.value || '').trim();
        active.originalDescription = String(descInput.value || '').trim();
        updateGroupSettingsSaveState();
        closeGroupSettingsModal();
      }
      else toast(`❌ Save failed: ${res?.error || 'Invalid group settings'}`, 'error');
    } catch (e) {
      toast(`❌ Save failed: ${groupDisplayError(e)}`, 'error');
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  });
  card.querySelector('#ecGroupSettingsInvite')?.addEventListener('click', async () => {
    const active = EC_GROUP_SETTINGS_ACTIVE;
    if (!active?.groupId) return;
    const input = card.querySelector('#ecGroupSettingsInviteInput');
    if (input) {
      input.focus();
      return;
    }
    await inviteToGroupFromWindow(active.groupId, active.title || '');
  });
  const sendInlineInvite = async () => {
    const active = EC_GROUP_SETTINGS_ACTIVE;
    const input = card.querySelector('#ecGroupSettingsInviteInput');
    if (!active?.groupId || !input) return;
    const target = String(input.value || '').trim();
    if (!target) {
      input.focus();
      toast('Type a username to invite.', 'warn');
      return;
    }
    const btn = card.querySelector('#ecGroupSettingsInviteSend');
    try {
      if (btn) btn.disabled = true;
      const res = await groupInviteUsername(active.groupId, target);
      if (res?.success) input.value = '';
    } finally {
      if (btn) btn.disabled = false;
    }
  };
  card.querySelector('#ecGroupSettingsInviteSend')?.addEventListener('click', sendInlineInvite);
  card.querySelector('#ecGroupSettingsInviteInput')?.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    sendInlineInvite();
  });
  nameInput.addEventListener('input', updateGroupSettingsSaveState);
  descInput.addEventListener('input', updateGroupSettingsSaveState);
  memberSearch.addEventListener('input', () => renderGroupSettingsMembers(EC_GROUP_SETTINGS_ACTIVE?.groupId));
  card.querySelectorAll('.ecGroupCommandChip').forEach((btn) => {
    btn.addEventListener('click', () => runGroupSettingsCommandChip(btn.dataset.command));
  });
  card.querySelector('#ecGroupSettingsVoice')?.addEventListener('click', async () => {
    const active = EC_GROUP_SETTINGS_ACTIVE;
    if (!active?.groupId) return;
    const win = UIState.windows.get('group:' + String(active.groupId));
    await toggleGroupVoice(active.groupId, win);
    updateGroupSettingsVoiceButton(active.groupId);
  });
  card.querySelector('#ecGroupSettingsRefresh')?.addEventListener('click', async () => {
    const active = EC_GROUP_SETTINGS_ACTIVE;
    if (!active?.groupId) return;
    const win = UIState.windows.get('group:' + String(active.groupId));
    await refreshGroupMemberRoster(active.groupId, win, { toast: true });
    await groupRefreshMutes(active.groupId, { silent: true });
    renderGroupSettingsMembers(active.groupId);
    renderGroupSettingsMutes(active.groupId);
  });
  card.querySelector('#ecGroupSettingsMutedRefresh')?.addEventListener('click', async () => {
    const active = EC_GROUP_SETTINGS_ACTIVE;
    if (!active?.groupId) return;
    await groupRefreshMutes(active.groupId, { silent: false });
    renderGroupSettingsMembers(active.groupId);
    renderGroupSettingsMutes(active.groupId);
  });
  card.querySelector('#ecGroupSettingsLeave')?.addEventListener('click', async () => {
    const active = EC_GROUP_SETTINGS_ACTIVE;
    if (!active?.groupId) return;
    closeGroupSettingsModal();
    await groupLeaveFromSettings(active.groupId, active.title || '');
  });
  card.querySelector('#ecGroupSettingsDelete')?.addEventListener('click', async () => {
    const active = EC_GROUP_SETTINGS_ACTIVE;
    if (!active?.groupId) return;
    closeGroupSettingsModal();
    await groupDeleteFromSettings(active.groupId, active.title || '');
  });

  document.addEventListener('keydown', (ev) => {
    if (!EC_GROUP_SETTINGS_ACTIVE || !EC_GROUP_SETTINGS_MODAL || EC_GROUP_SETTINGS_MODAL.classList.contains('hidden')) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      closeGroupSettingsModal();
    }
  });

  (document.body || document.documentElement).appendChild(modal);
  EC_GROUP_SETTINGS_MODAL = modal;
  return modal;
}

function updateGroupSettingsVoiceButton(groupId) {
  const modal = EC_GROUP_SETTINGS_MODAL;
  if (!modal || modal.classList.contains('hidden')) return;
  const btn = modal.querySelector('#ecGroupSettingsVoice');
  if (!btn) return;
  const active = groupVoiceIsActive(groupId);
  btn.textContent = active ? '📞 Leave voice' : '🎤 Start voice';
  btn.classList.toggle('active', active);
  btn.disabled = !VOICE_ENABLED;
  if (!VOICE_ENABLED) btn.textContent = '🎤 Voice disabled';
}

async function openGroupSettings(groupId, title = '') {
  const gid = Number(groupId || 0);
  if (!gid) return;
  const modal = ensureGroupSettingsModal();
  const meta = groupMetaFromCache(gid, title);
  const role = currentGroupRole(gid);
  const roleLabel = groupMemberRoleLabel(role);
  const canEditMeta = groupRoleRank(role) >= groupRoleRank('admin');
  const isOwner = role === 'owner';

  const titleEl = modal.querySelector('#ecGroupSettingsTitle');
  const subTitleEl = modal.querySelector('#ecGroupSettingsSubTitle');
  const idEl = modal.querySelector('#ecGroupSettingsGroupId');
  const roleEl = modal.querySelector('#ecGroupSettingsRole');
  const nameInput = modal.querySelector('#ecGroupSettingsName');
  const descInput = modal.querySelector('#ecGroupSettingsDescription');
  const hint = modal.querySelector('#ecGroupSettingsPermissionHint');
  const saveBtn = modal.querySelector('#ecGroupSettingsSave');
  const inviteBtn = modal.querySelector('#ecGroupSettingsInvite');
  const inviteSendBtn = modal.querySelector('#ecGroupSettingsInviteSend');
  const inviteInput = modal.querySelector('#ecGroupSettingsInviteInput');
  const memberSearch = modal.querySelector('#ecGroupSettingsMemberSearch');
  const deleteBtn = modal.querySelector('#ecGroupSettingsDelete');

  if (titleEl) titleEl.textContent = 'Group settings';
  if (subTitleEl) subTitleEl.textContent = `${meta.group_name} · ${roleLabel}`;
  if (idEl) idEl.textContent = `#${gid}`;
  if (roleEl) roleEl.textContent = roleLabel;
  if (nameInput) {
    nameInput.value = meta.group_name;
    nameInput.disabled = !canEditMeta;
  }
  if (descInput) {
    descInput.value = meta.group_description;
    descInput.disabled = !canEditMeta;
  }
  if (hint) {
    hint.textContent = canEditMeta
      ? 'Admins and the owner can update the group name and description.'
      : 'Only group admins and the owner can update the group name and description. You can still use invite, voice, refresh, or leave if allowed.';
  }
  const canInvite = groupRoleRank(role) >= groupRoleRank('moderator');
  if (inviteBtn) inviteBtn.disabled = !canInvite;
  if (inviteSendBtn) inviteSendBtn.disabled = !canInvite;
  if (inviteInput) {
    inviteInput.value = '';
    inviteInput.disabled = !canInvite;
    inviteInput.placeholder = canInvite ? 'username' : 'Moderators, admins, and owners can invite';
  }
  if (memberSearch) memberSearch.value = '';
  if (deleteBtn) deleteBtn.classList.toggle('hidden', !isOwner);
  updateGroupSettingsVoiceButton(gid);

  EC_GROUP_SETTINGS_ACTIVE = {
    groupId: gid,
    title: title || meta.group_name,
    originalName: meta.group_name,
    originalDescription: meta.group_description,
    canEditMeta,
    canInvite,
    restoreFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null,
  };
  updateGroupSettingsSaveState();
  renderGroupSettingsMembers(gid);
  renderGroupSettingsMutes(gid);
  if (groupRoleRank(role) >= groupRoleRank('moderator')) {
    groupRefreshMutes(gid, { silent: true }).then(() => {
      renderGroupSettingsMembers(gid);
      renderGroupSettingsMutes(gid);
    }).catch(() => {});
  }
  modal.classList.remove('hidden');
  window.setTimeout(() => {
    try {
      if (canEditMeta) nameInput?.focus?.();
      else modal.querySelector('#ecGroupSettingsInvite:not(:disabled), #ecGroupSettingsVoice:not(:disabled), #ecGroupSettingsCancel')?.focus?.();
    } catch {}
  }, 0);
}

async function groupInviteUsername(groupId, username) {
  const gid = Number(groupId || 0);
  const targetUser = String(username || '').trim().replace(/^@/, '');
  if (!gid || !targetUser) return { success: false, error: 'Usage: /invite <username>' };
  try {
    const res = await apiJson(`/api/groups/${encodeURIComponent(gid)}/invite`, {
      method: 'POST',
      body: JSON.stringify({ to_user: targetUser }),
    });
    if (res?.status === 'already_member') {
      toast(`ℹ️ ${targetUser} is already in this group`, 'info');
    } else {
      toast(`✅ Invite sent to ${targetUser}`, 'ok');
    }
    return { success: true, command: 'invite' };
  } catch (e) {
    const msg = groupDisplayError(e);
    toast(`❌ Invite failed: ${msg}`, 'error');
    return { success: false, command: 'invite', error: msg };
  }
}

async function runGroupTextCommand(groupId, plaintext, ctx = {}) {
  const gid = Number(groupId || 0);
  const raw = (typeof plaintext === 'string') ? plaintext : String(plaintext ?? '');
  const t = raw.trim();
  if (!gid || !t.startsWith('/')) return { handled: false };
  const lower = t.toLowerCase();
  const win = ctx?.win || UIState.windows.get('group:' + String(gid));
  const title = ctx?.title || win?.dataset?.windowTitle || '';
  const meta = groupMetaFromCache(gid, title);

  if (/^\/invite(\s|$)/i.test(t)) {
    const rest = t.replace(/^\/invite\s*/i, '').trim();
    const u = ((rest.split(/\s+/)[0] || '').trim()).replace(/^@/, '');
    if (!u) return { handled: true, success: false, command: 'invite', error: 'Usage: /invite <username>' };
    const res = await groupInviteUsername(gid, u);
    return { handled: true, ...res };
  }
  if (lower === '/settings' || lower === '/group settings') {
    await openGroupSettings(gid, title);
    return { handled: true, success: true, command: 'settings' };
  }
  if (lower === '/voice') {
    await toggleGroupVoice(gid, win);
    return { handled: true, success: true, command: 'voice' };
  }
  if (lower === '/users' || lower === '/members') {
    await refreshGroupMemberRoster(gid, win, { toast: true });
    return { handled: true, success: true, command: 'members' };
  }
  if (lower === '/leave') {
    await groupLeaveFromSettings(gid, meta.group_name);
    return { handled: true, success: true, command: 'leave' };
  }
  if (/^\/rename\s+/i.test(t)) {
    if (groupRoleRank(currentGroupRole(gid)) < groupRoleRank('admin')) {
      return { handled: true, success: false, command: 'rename', error: 'Only group admins and the owner can rename this group.' };
    }
    const name = t.replace(/^\/rename\s+/i, '').trim();
    const res = await groupUpdateMetadata(gid, name, meta.group_description);
    return { handled: true, command: 'rename', success: !!res?.success, error: res?.error };
  }
  if (/^\/(desc|description)\s+/i.test(t)) {
    if (groupRoleRank(currentGroupRole(gid)) < groupRoleRank('admin')) {
      return { handled: true, success: false, command: 'description', error: 'Only group admins and the owner can edit the description.' };
    }
    const desc = t.replace(/^\/(desc|description)\s+/i, '').trim();
    const res = await groupUpdateMetadata(gid, meta.group_name, desc);
    return { handled: true, command: 'description', success: !!res?.success, error: res?.error };
  }
  if (lower === '/grouphelp' || lower === '/help group') {
    toast('Group commands: /invite username, /settings, /voice, /users, /leave, /rename name, /desc description', 'info', 6500);
    return { handled: true, success: true, command: 'help' };
  }
  return { handled: false };
}

function renderGroupMemberRoster(win, groupId, opts = {}) {
  if (!win?._ym?.groupMembersList) return;
  const gid = Number(groupId || 0);
  const ul = win._ym.groupMembersList;
  const countEl = win._ym.groupMembersCount;
  ecClearNode(ul);

  const details = normalizeGroupMemberDetails(UIState.groupMemberDetails?.get?.(gid), UIState.groupMembers?.get?.(gid));
  if (countEl) countEl.textContent = String(details.length || 0);
  win.dataset.groupMemberCount = String(details.length || 0);
  updateGroupWindowStatus(win, gid, { memberCount: details.length });
  try {
    const usersBtn = win.querySelector('.ym-mobileWindowUsersBtn');
    if (usersBtn) {
      usersBtn.textContent = details.length ? `Users (${details.length})` : 'Users';
      usersBtn.setAttribute('aria-label', details.length ? `Show ${details.length} group users` : 'Show group users');
    }
  } catch {}

  if (!details.length) {
    const text = opts.loading ? 'Loading group users…' : 'No group users found';
    ul.appendChild(ecRoomSidebarEmptyRow(text, { muted: true }));
    return;
  }

  details.forEach((member) => {
    const username = String(member.username || '').trim();
    if (!username) return;
    const roleLabel = groupMemberRoleLabel(member.role);
    const presence = groupMemberPresence(username);
    const voiceActive = groupVoiceUserIsActive(gid, username);
    const mutedInGroup = groupMemberIsMuted(gid, username);
    const isSelf = String(username).toLowerCase() === String(currentUser || '').toLowerCase();

    const li = document.createElement('li');
    li.className = 'ym-groupMemberItem';
    li.dataset.name = username;
    li.dataset.role = String(member.role || 'member').toLowerCase();
    li.dataset.presence = String(presence.key || presence.label || '').toLowerCase();
    li.dataset.groupId = String(gid);
    li.dataset.search = `${username} ${roleLabel} ${presence.label} group member`;

    const left = document.createElement('div');
    left.className = 'liLeft';
    try {
      createDockIdentity(left, {
        name: username,
        presenceClass: presence.className,
        meta: `${roleLabel} · ${presence.label}${voiceActive ? ' · Voice' : ''}`,
        chip: isSelf ? 'You' : (voiceActive ? 'Voice' : (roleLabel !== 'Member' ? roleLabel : '')),
        avatarUrl: UIState.presence?.get?.(username)?.avatar_url || '',
      });
    } catch {
      left.appendChild(ecRoomSidebarLeft(presence.className, username, { descText: ` · ${roleLabel}${voiceActive ? ' · Voice' : ''}` }));
    }

    const actions = document.createElement('div');
    actions.className = 'liActions';
    const pmBtn = document.createElement('button');
    pmBtn.className = 'iconBtn';
    pmBtn.textContent = '💬';
    pmBtn.title = isSelf ? 'This is you' : `Private message ${username}`;
    pmBtn.disabled = isSelf;
    pmBtn.onclick = (ev) => {
      ev.stopPropagation();
      if (!isSelf) openPrivateChat(username);
    };
    actions.appendChild(pmBtn);

    const moreBtn = document.createElement('button');
    moreBtn.className = 'iconBtn ym-groupMemberMoreBtn';
    moreBtn.textContent = '⋯';
    moreBtn.title = `Group actions for ${username}`;
    moreBtn.setAttribute('aria-label', `Group actions for ${username}`);
    moreBtn.onclick = (ev) => {
      ev.stopPropagation();
      selectBuddyRow(username, 'group', li);
      if (typeof showUserContextMenu === 'function') {
        showUserContextMenu(ev, username, { source: 'group', group_id: gid });
      }
    };
    actions.appendChild(moreBtn);

    li.appendChild(left);
    li.appendChild(actions);
    li.onclick = () => selectBuddyRow(username, 'group', li);
    li.ondblclick = () => { if (!isSelf) openPrivateChat(username); };
    li.oncontextmenu = (ev) => {
      selectBuddyRow(username, 'group', li);
      showUserContextMenu(ev, username, { source: 'group', group_id: gid });
    };

    ul.appendChild(li);
  });
}

function refreshGroupMemberRoster(groupId, win = null, opts = {}) {
  const gid = Number(groupId || 0);
  const targetWin = win || UIState.windows.get('group:' + String(gid));
  if (!gid) return Promise.resolve([]);
  if (targetWin) renderGroupMemberRoster(targetWin, gid, { loading: true });
  return new Promise((resolve) => {
    socket.emit('get_group_members', { group_id: gid }, (res) => {
      if (res?.success) {
        const details = rememberGroupMembersFromResponse(gid, res);
        if (targetWin) renderGroupMemberRoster(targetWin, gid);
        if (EC_GROUP_SETTINGS_ACTIVE?.groupId === gid) renderGroupSettingsMembers(gid);
        if (opts.toast) toast(`👥 Refreshed ${details.length} group user${details.length === 1 ? '' : 's'}`, 'ok');
        resolve(details);
        return;
      }
      if (targetWin) renderGroupMemberRoster(targetWin, gid);
      if (opts.toast) toast(`❌ Could not refresh group users`, 'error');
      resolve([]);
    });
  });
}

function wireGroupMemberRoster(win, groupId) {
  if (!win?._ym) return;
  const gid = Number(groupId || 0);
  win.dataset.groupId = String(gid || '');
  if (win._ym.groupMembersRefreshBtn) {
    win._ym.groupMembersRefreshBtn.onclick = (ev) => {
      ev.preventDefault();
      refreshGroupMemberRoster(gid, win, { toast: true });
    };
  }
  if (win._ym.groupMembersCloseBtn) {
    win._ym.groupMembersCloseBtn.onclick = (ev) => {
      ev.preventDefault();
      win.classList.remove('is-mobile-group-members-open');
      const usersBtn = win.querySelector('.ym-mobileWindowUsersBtn');
      if (usersBtn) usersBtn.setAttribute('aria-expanded', 'false');
      const usersPanel = win.querySelector('.ym-groupMembersPanel');
      if (usersPanel) usersPanel.setAttribute('aria-hidden', 'true');
    };
  }
  renderGroupMemberRoster(win, gid, { loading: true });
  registerWindowCleanup(win, () => {
    if (win._groupMemberRefreshTimer) {
      clearInterval(win._groupMemberRefreshTimer);
      win._groupMemberRefreshTimer = null;
    }
  });
  if (!win._groupMemberRefreshTimer) {
    win._groupMemberRefreshTimer = setInterval(() => {
      if (!document.body?.contains(win) || win.classList.contains('hidden')) return;
      refreshGroupMemberRoster(gid, win).catch(() => {});
    }, 60000);
  }
}


function groupVoiceRoomName(groupId) {
  const gid = Number(groupId || 0);
  return gid ? `group_${gid}` : '';
}

function groupVoiceRoomIdFromName(room) {
  const m = String(room || '').match(/^group_(\d+)$/);
  return m ? Number(m[1] || 0) : 0;
}

function groupVoiceIsActive(groupId) {
  const room = groupVoiceRoomName(groupId);
  return !!(room && VOICE_STATE?.room?.joined && VOICE_STATE?.room?.name === room);
}

function updateGroupVoiceButton(groupId, win = null) {
  const gid = Number(groupId || 0);
  const targetWin = win || UIState.windows.get('group:' + String(gid));
  if (!targetWin?._ym?.groupVoiceBtn) return;
  const btn = targetWin._ym.groupVoiceBtn;
  const hint = targetWin._ym.groupVoiceHint;
  const active = groupVoiceIsActive(gid);
  const busy = (typeof voiceActionBusy === 'function') && voiceActionBusy('group', String(gid));
  btn.disabled = !VOICE_ENABLED || !!busy;
  btn.classList.toggle('active', active);
  btn.classList.toggle('isBusy', !!busy);
  btn.setAttribute('aria-busy', busy ? 'true' : 'false');
  btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  if (!VOICE_ENABLED) {
    btn.textContent = busy ? '🎤 Voice…' : '🎤 Voice';
    btn.title = 'Voice is disabled on this server';
    btn.setAttribute('aria-label', 'Group voice is disabled');
    if (hint) hint.textContent = 'Voice off';
    return;
  }
  if (!active) {
    btn.textContent = busy ? '🎤 Voice…' : '🎤 Voice';
    btn.title = busy ? 'Group voice is connecting…' : 'Enable voice for this group';
    btn.setAttribute('aria-label', 'Enable group voice');
    if (hint) hint.textContent = 'Voice';
    return;
  }
  if (VOICE_STATE?.micMuted) {
    btn.textContent = busy ? '🔇 Muted…' : '🔇 Muted';
    btn.title = 'Group voice is on but muted — click to leave, right-click to unmute';
    btn.setAttribute('aria-label', 'Group voice is muted; click to leave');
    if (hint) hint.textContent = 'Muted';
  } else {
    btn.textContent = busy ? '📞 Voice…' : '📞 Voice on';
    btn.title = 'Group voice is on — click to leave, right-click to mute';
    btn.setAttribute('aria-label', 'Group voice is on; click to leave');
    if (hint) hint.textContent = 'Voice on';
  }
}

function updateAllGroupVoiceButtons() {
  try {
    UIState.windows.forEach((win) => {
      if (!win || win.dataset.kind !== 'group') return;
      const gid = Number(win.dataset.groupId || String(win.dataset.winId || '').replace(/^group:/, '') || 0);
      if (gid) updateGroupVoiceButton(gid, win);
    });
  } catch {}
}

async function inviteToGroupFromWindow(groupId, groupTitle = '') {
  const gid = Number(groupId || 0);
  if (!gid) return;
  const u = await ecPrompt('Invite which username?', '', {
    title: 'Invite user to group',
    inputLabel: 'Username',
    confirmLabel: 'Send invite',
    maxLength: 80,
    placeholder: 'username',
  });
  const targetUser = String(u || '').trim();
  if (!targetUser) return;
  await groupInviteUsername(gid, targetUser);
}

async function toggleGroupVoice(groupId, win = null) {
  const gid = Number(groupId || 0);
  const room = groupVoiceRoomName(gid);
  if (!gid || !room) return;
  if (typeof voiceActionBusy === 'function' && voiceActionBusy('group', String(gid))) return;
  if (typeof voiceWithBusy === 'function') return voiceWithBusy('group', String(gid), async () => toggleGroupVoiceUnlocked(gid, win));
  return toggleGroupVoiceUnlocked(gid, win);
}

async function toggleGroupVoiceUnlocked(groupId, win = null) {
  const gid = Number(groupId || 0);
  const room = groupVoiceRoomName(gid);
  if (!gid || !room) return;
  try {
    if (!VOICE_ENABLED) return toast('🎤 Voice is disabled on this server', 'warn');
    if (groupVoiceIsActive(gid)) {
      VOICE_STATE.room.wantRoomVoice = false;
      voiceLeaveRoom('Group voice disabled', true);
      updateAllGroupVoiceButtons();
      return;
    }
    // Group voice is intentionally voice-only.  Do not route this through the
    // room webcam/media toggle, because that can carry an active room camera
    // into the group.  The underlying WebRTC voice mesh is the same one the
    // room voice button uses.
    VOICE_STATE.room.wantRoomVoice = true;
    voiceSetMute(false);
    const res = await voiceJoinRoom(room, { silent: true, audio: true });
    if (!res?.success) {
      if (res?.error_code === 'voice_room_full') voiceShowRoomFull(room, res);
      else toast(`❌ ${res?.error || 'Group voice join failed'}`, 'error');
    } else {
      toast('🎤 Group voice connected', 'ok', 1800);
    }
    updateGroupVoiceButton(gid, win);
  } catch (e) {
    console.error(e);
    toast(`❌ Group voice error: ${e?.message || e}`, 'error');
  } finally {
    updateAllGroupVoiceButtons();
  }
}

function wireGroupWindowActions(win, groupId, title = '') {
  const gid = Number(groupId || 0);
  if (!win?._ym || !gid) return;
  if (win._ym.groupInviteBtn) {
    win._ym.groupInviteBtn.onclick = (ev) => {
      ev.preventDefault();
      inviteToGroupFromWindow(gid, title);
    };
  }
  if (win._ym.groupSettingsBtn) {
    win._ym.groupSettingsBtn.onclick = async (ev) => {
      ev.preventDefault();
      await openGroupSettings(gid, title || win?.dataset?.windowTitle || '');
    };
  }
  if (win._ym.groupVoiceBtn) {
    win._ym.groupVoiceBtn.onclick = async (ev) => {
      ev.preventDefault();
      await toggleGroupVoice(gid, win);
    };
    win._ym.groupVoiceBtn.oncontextmenu = (ev) => {
      ev.preventDefault();
      if (!groupVoiceIsActive(gid)) return false;
      const muted = !VOICE_STATE.micMuted;
      voiceSetMute(muted);
      updateAllGroupVoiceButtons();
      toast(muted ? '🔇 Mic muted' : '🎤 Mic unmuted', 'info');
      return false;
    };
  }
  updateGroupVoiceButton(gid, win);
}

function openGroupWindow(groupId, title) {
  const id = "group:" + groupId;
  const existing = UIState.windows.get(id);
  if (existing) {
    existing.classList.remove("hidden");
    if (String(title || '').trim()) ecSetGroupWindowTitle(existing, groupId, title);
    bringToFront(existing);
    refreshGroupMemberRoster(groupId, existing).catch(() => {});
    wireGroupWindowActions(existing, groupId, title);
    try { ecBindGroupTypingInput(existing, groupId); } catch {}
    updateGroupWindowStatus(existing, groupId);
    return existing;
  }
  const win = createWindow({ id, title: `Group — ${title} (#${groupId})`, kind: "group" });
  if (!win) return;
  ecSetGroupWindowTitle(win, groupId, title);
  wireGroupMemberRoster(win, groupId);
  wireGroupWindowActions(win, groupId, title);
  try { ecBindGroupTypingInput(win, groupId); } catch {}
  updateGroupWindowStatus(win, groupId);

  // Group: add history toolbar + paging state
  ensureGroupHistoryToolbar(win, groupId);
  const _gst = groupHistState(win);
  _gst.loading = false;
  _gst.done = false;
  updateGroupOlderUI(win);

  try { voiceWireWindowTalkControls(win); } catch (e) {}

  // Join Socket.IO room for group chat, with reconnect-aware ACK handling.
  ecJoinGroupChatAck(groupId).then((res) => {
    if (res?.success) {
      win._groupChatJoined = true;
      if (res?.group_name) ecSetGroupWindowTitle(win, groupId, res.group_name);
      rememberGroupMembersFromResponse(groupId, res);
      renderGroupMemberRoster(win, groupId);
      updateGroupWindowStatus(win, groupId, { unreadCount: res?.unread_count });

      // Render history (ciphertext-only safe). If history exists, replace the
      // default "Window opened" line to avoid clutter.
      const hist = Array.isArray(res.history) ? res.history : [];
      if (win._ym?.log && hist.length) {
        resetChatLogState(win._ym.log);
        (async () => {
          await appendGroupHistory(win, hist);
          const st = groupHistState(win);
          st.done = false;
          updateOldestId(win, hist);
          if (hist.length < GROUP_HISTORY_PAGE_SIZE) st.done = true;
          updateGroupOlderUI(win);
          appendLine(win, "System:", "Joined group chat.", { ts: res?.joined_at || Date.now() });
        })();
      } else {
        const st = groupHistState(win);
        st.oldestId = null;
        st.done = true;
        updateGroupOlderUI(win);
        appendLine(win, "System:", "Joined group chat.", { ts: res?.joined_at || Date.now() });
      }
      return;
    }
    const reason = res?.error || 'Could not join group chat';
    appendLine(win, 'System:', `Could not join group chat: ${reason}`, { ts: Date.now() });
    toast(`❌ ${reason}`, 'error');
  }).catch((e) => {
    const reason = e?.message || 'Could not join group chat';
    appendLine(win, 'System:', `Could not join group chat: ${reason}`, { ts: Date.now() });
    toast(`❌ ${reason}`, 'error');
  });

  win._ym.send.onclick = () => {
    const input = win._ym?.input || null;
    const sendBtn = win._ym?.send || null;
    const msg = input?.value?.trim() || '';
    if (!msg) return;

    const optimistic = (typeof ecComposerBeginOptimisticSend === 'function')
      ? ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })
      : null;
    sendGroupTo(groupId, msg, { win, title: title || win?.dataset?.windowTitle || '' }).then((res) => {
      if (res?.success) {
        try { ecConversationTypingStop(input, { force: true }); } catch {}
        optimistic?.commit?.();
      } else {
        optimistic?.restore?.(res?.error || "Group send failed");
        toast(`❌ ${res?.error || "Group send failed"}`, "error");
      }
    }).catch((e) => {
      optimistic?.restore?.(e?.message || 'Group send failed');
      console.error(e);
      toast(`❌ Group send failed: ${e?.message || e}`, "error");
    });
  };

  // Group GIF button (send without polluting the input field)
  if (win._ym?.gifBtn) {
    win._ym.gifBtn.onclick = () => {
      openGifPicker((url) => {
        const clean = url;
          const msg = `gif:${clean}`;
        sendGroupTo(groupId, msg).then((res) => {
          if (res?.success) {
          } else {
            toast(`❌ ${res?.error || "Group GIF send failed"}`, "error");
          }
        }).catch((e) => {
          console.error(e);
          toast(`❌ Group GIF send failed: ${e?.message || e}`, "error");
        });
      });
    };
  }

  // Group file button (E2EE + server ciphertext storage)
  if (win._ym?.fileBtn && win._ym?.fileInput) {
    win._ym.fileBtn.onclick = () => win._ym.fileInput.click();
    win._ym.fileInput.onchange = async () => {
      try {
        const f = win._ym.fileInput.files?.[0];
        win._ym.fileInput.value = "";
        if (!f) return;

        const payload = await sendGroupFileTo(groupId, f, { win });
        if (payload) {
          // The server echoes the encrypted group-file payload back with a real
          // message_id.  Do not append locally here or the sender sees a duplicate card.
          updateGroupWindowStatus(win, groupId);
        }
      } catch (e) {
        console.error(e);
        toast(`❌ Group file send failed: ${e?.message || e}`, "error");
      }
    };
  }

  bringToFront(win);
  return win;
}

function forceLeaveGroupUI(groupId, why = "removed") {
  const gid = String(groupId || "").trim();
  if (!gid) return;
  try { UIState.groupMembers.delete(Number(gid)); } catch {}
  try { UIState.groupMemberDetails.delete(Number(gid)); } catch {}
  try {
    const id = "group:" + gid;
    if (UIState.windows.has(id)) closeWindow(id);
  } catch {}
  try { refreshMyGroups(); } catch {}
  const reason = String(why || "removed").toLowerCase();
  if (reason === "kicked") toast(`👢 Removed from group #${gid}`, "warn", 4200);
  else if (reason === "deleted") toast(`🗑️ Group #${gid} was deleted`, "warn", 4200);
  else if (reason === "left") toast(`👋 Left group #${gid}`, "info", 3200);
}

socket.on("group_forced_leave", (payload = {}) => {
  try {
    const groupId = payload?.group_id;
    if (!groupId) return;
    forceLeaveGroupUI(groupId, payload?.reason || "removed");
  } catch (e) {
    console.warn("group_forced_leave handler failed", e);
  }
});

function applyGroupMetadataUpdateFromEvent(groupId, payload = {}) {
  const gid = Number(groupId || 0);
  if (!gid) return;
  const nextName = String(payload?.name || '').trim();
  if (!nextName) return;
  try {
    const idx = Array.isArray(UIState.myGroups) ? UIState.myGroups.findIndex((g) => Number(g?.id || 0) === gid) : -1;
    if (idx >= 0) UIState.myGroups[idx] = { ...UIState.myGroups[idx], group_name: nextName };
  } catch {}
  const win = UIState.windows.get('group:' + String(gid));
  if (win) ecSetGroupWindowTitle(win, gid, nextName);
  if (EC_GROUP_SETTINGS_ACTIVE?.groupId === gid) {
    try {
      const modal = EC_GROUP_SETTINGS_MODAL;
      const subtitle = modal?.querySelector?.('#ecGroupSettingsSubTitle');
      if (subtitle) subtitle.textContent = `${nextName} · ${groupMemberRoleLabel(currentGroupRole(gid))}`;
      EC_GROUP_SETTINGS_ACTIVE.title = nextName;
      EC_GROUP_SETTINGS_ACTIVE.originalName = nextName;
    } catch {}
  }
}

socket.on("group_members_changed", (payload = {}) => {
  try {
    const groupId = Number(payload?.group_id || 0);
    if (!groupId) return;
    if (String(payload?.reason || '') === 'metadata_updated') {
      applyGroupMetadataUpdateFromEvent(groupId, payload);
    }
    const win = UIState.windows.get("group:" + String(groupId));
    if (win) refreshGroupMemberRoster(groupId, win).catch(() => {});
    try { refreshMyGroups(); } catch {}
  } catch (e) {
    console.warn("group_members_changed handler failed", e);
  }
});

socket.on("group_message", async (payload) => {
  if (!payload) return;
  const group_id = Number(payload.group_id || 0);
  const sender = payload.sender;
  const win = UIState.windows.get("group:" + String(group_id));

  if (!win) {
    // Closed/minimized group windows still need unread/sidebar refresh and a
    // single attention notification. Rendering will occur when the user opens it.
    try { bumpGroupUnreadCache(group_id, 1); } catch {}
    try { refreshMyGroups(); } catch {}
    if (shouldNotifyGroupMessage(null, group_id, sender)) {
      const kind = String(payload?.message_kind || '').toLowerCase();
      const summary = kind === 'file' ? '📎 Group file' : kind === 'torrent' ? '🧲 Group torrent' : 'New group message';
      const dedupeKey = `groupmsg:${String(group_id)}:${String(sender || '').toLowerCase()}:${String(payload?.message_id || ecGroupCipherFingerprint(payload?.cipher || payload?.message || ''))}`;
      toast(`👥 ${sender} in group #${group_id}`, "info", 3500, { event: "group_message", dedupeKey });
      maybeBrowserNotify("Group message", `${sender}: ${summary}`, { dedupeKey });
    }
    return;
  }

  const messageId = groupMsgId(payload);
  if (messageId !== null && hasSeenGroupMessageId(win, messageId)) return;
  const render = await groupMessageToRender(payload);
  const renderKey = ecGroupRenderKey(group_id, payload, render);
  if (renderKey && !ecRememberGroupRenderKey(win, renderKey)) return;
  if (messageId !== null) rememberGroupMessageId(win, messageId);

  try { ecSetConversationTyping('group', String(group_id), sender, false, 0); } catch {}
  const rendered = appendGroupRenderedMessage(win, group_id, sender, render, payload);
  if (render.locked || render.error || render.hidden) {
    win.dataset.groupLockedCount = String((Number(win.dataset.groupLockedCount || 0) || 0) + 1);
  }
  const groupIsActive = ecIsGroupConversationActive(win);
  if (render.readSafe && messageId !== null && groupIsActive) {
    markVisibleGroupMessageRead(group_id, messageId);
  } else if (!groupIsActive && sender && !ecGroupSameUser(sender, currentUser)) {
    bumpGroupUnreadCache(group_id, 1);
  }
  updateGroupWindowStatus(win, group_id);

  if (shouldNotifyGroupMessage(win, group_id, sender)) {
    const parsed = rendered?.payload || parseGroupPayload(render.text);
    const notifText = (parsed && parsed.kind === "file")
      ? `📎 ${parsed?.name || "file"}`
      : (parsed && parsed.kind === "torrent")
        ? "🧲 Torrent"
        : `${render.text}`;
    const dedupeKey = `groupmsg:${String(group_id)}:${String(sender || '').toLowerCase()}:${String(messageId || ecGroupCipherFingerprint(payload?.cipher || notifText))}`;
    toast(`👥 ${sender} in group #${group_id}`, "info", 3500, { event: "group_message", dedupeKey });
    maybeBrowserNotify("Group message", `${sender}: ${notifText}`, { dedupeKey });
  }
});


// ───────────────────────────────────────────────────────────────────────────────
// DMs (E2EE) — floating windows
// ───────────────────────────────────────────────────────────────────────────────
const EC_PM_FULL_TITLE_USERNAME_MAX = 14;

function ecPrivateMessageWindowTitle(username) {
  const name = String(username || "").trim().replace(/\s+/g, " ") || "user";
  const prefix = name.length <= EC_PM_FULL_TITLE_USERNAME_MAX ? "Private message" : "PM";
  return `${prefix} — ${name}`;
}

function ecPrivateMessageWindowFullTitle(username) {
  const name = String(username || "").trim().replace(/\s+/g, " ") || "user";
  return `Private message — ${name}`;
}

function ecUpdatePrivateMessageWindowTitle(win, username) {
  if (!win || !win._ym || !win._ym.titleEl) return;
  const title = ecPrivateMessageWindowTitle(username);
  const fullTitle = ecPrivateMessageWindowFullTitle(username);
  win._ym.titleEl.textContent = title;
  win._ym.titleEl.title = fullTitle;
  win.setAttribute("aria-label", fullTitle);
  win.dataset.windowTitle = title;
  win.dataset.windowFullTitle = fullTitle;
}

function ecDmCanonicalKey(peer) {
  try {
    if (typeof ecPmPeerKey === "function") return ecPmPeerKey(peer);
  } catch {}
  return String(peer || "").trim().toLowerCase();
}

function ecDmKeysEqual(a, b) {
  const ka = ecDmCanonicalKey(a);
  const kb = ecDmCanonicalKey(b);
  return !!ka && !!kb && ka === kb;
}

function ecDmPendingOfflineCount(peer) {
  const key = ecDmCanonicalKey(peer);
  if (!key || !UIState?.pendingOfflineDm || typeof UIState.pendingOfflineDm.entries !== "function") return 0;
  let total = 0;
  try {
    for (const [name, arr] of UIState.pendingOfflineDm.entries()) {
      if (ecDmKeysEqual(name, peer)) total += Array.isArray(arr) ? arr.length : 0;
    }
  } catch {}
  return total;
}

function ecDmMissedCount(peer) {
  const list = (typeof ecGetCombinedMissedPmItems === 'function')
    ? ecGetCombinedMissedPmItems()
    : (Array.isArray(UIState?.missedPmSummary) ? UIState.missedPmSummary : []);
  let total = 0;
  for (const it of list) {
    if (it && ecDmKeysEqual(it.sender, peer)) total += Number(it.count ?? 0) || 0;
  }
  return total;
}

function ecDmIsConsuming(peer) {
  try {
    if (UIState?.consumingOfflinePeers instanceof Set) {
      for (const name of UIState.consumingOfflinePeers.values()) {
        if (ecDmKeysEqual(name, peer)) return true;
      }
    }
  } catch {}
  return false;
}

function ecDmEncryptionState() {
  if (typeof HAS_WEBCRYPTO !== "undefined" && !HAS_WEBCRYPTO) {
    return {
      kind: "error",
      label: "E2EE unavailable",
      detail: "Use HTTPS, localhost, or 127.0.0.1 so private messages can decrypt."
    };
  }
  if (window.myPrivateCryptoKey) {
    return { kind: "ok", label: "E2EE ready", detail: "Private messages can decrypt in this tab." };
  }
  if (window.ENCRYPTED_PRIV_KEY) {
    return { kind: "warn", label: "E2EE locked", detail: "Unlock private messages to read missed encrypted messages." };
  }
  if (typeof DM_PLAINTEXT_COMPAT_ALLOWED !== "undefined" && DM_PLAINTEXT_COMPAT_ALLOWED) {
    return { kind: "warn", label: "Legacy PM compatibility", detail: "Encrypted PM key is missing; plaintext fallback may be used only where allowed." };
  }
  return { kind: "warn", label: "E2EE key missing", detail: "Sign out and sign back in if private messages do not unlock." };
}

function ecMakeDmStatusButton(label, title, onclick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "miniBtn ym-dmStatusBtn";
  btn.textContent = label;
  if (title) btn.title = title;
  btn.onclick = onclick;
  return btn;
}

function ecUpdateDmStatus(win, peer = null) {
  if (!win || !win._ym?.dmStatus) return;
  const statusEl = win._ym.dmStatus;
  const target = ecPmPeerName(peer || win.dataset?.pmPeer || "");
  const state = ecDmEncryptionState();
  const missed = ecDmMissedCount(target);
  const queued = ecDmPendingOfflineCount(target);
  const consuming = ecDmIsConsuming(target);

  statusEl.className = `ym-dmStatus ym-dmStatus--${state.kind}${consuming ? " ym-dmStatus--busy" : ""}`;
  ecClearNode(statusEl);

  const main = document.createElement("div");
  main.className = "ym-dmStatusMain";
  main.textContent = `🔒 ${state.label}`;

  const meta = document.createElement("div");
  meta.className = "ym-dmStatusMeta";
  const parts = [state.detail];
  if (missed > 0) parts.push(`${missed} missed`);
  if (queued > 0) parts.push(`${queued} saved locally`);
  if (consuming) parts.push("loading missed messages");
  meta.textContent = parts.filter(Boolean).join(" • ");

  const textWrap = document.createElement("div");
  textWrap.className = "ym-dmStatusText";
  textWrap.appendChild(main);
  textWrap.appendChild(meta);
  statusEl.appendChild(textWrap);

  const actions = document.createElement("div");
  actions.className = "ym-dmStatusActions";

  if (target && (missed > 0 || queued > 0 || (state.kind !== "ok" && window.ENCRYPTED_PRIV_KEY))) {
    const label = (missed > 0 || queued > 0) ? "Load missed" : "Unlock";
    actions.appendChild(ecMakeDmStatusButton(label, "Unlock and retry missed private messages", async () => {
      const btn = actions.querySelector("button");
      try {
        if (btn) {
          btn.disabled = true;
          btn.textContent = "Loading…";
        }
        try { await consumeOfflinePmsForPeer(target, { promptUnlock: true, quiet: false }); } catch {}
        if (window.myPrivateCryptoKey) {
          try { await flushPendingOfflineDm(target); } catch {}
        } else if (typeof ensurePrivateKeyUnlocked === "function") {
          try { await ensurePrivateKeyUnlocked(); } catch {}
          if (window.myPrivateCryptoKey) {
            try { await flushPendingOfflineDm(target); } catch {}
          }
        }
      } finally {
        ecUpdateDmStatus(win, target);
      }
    }));
  }

  if (actions.childNodes.length) statusEl.appendChild(actions);
}

function ecUpdateAllOpenDmStatuses() {
  try {
    if (!UIState?.windows || typeof UIState.windows.values !== "function") return;
    for (const win of UIState.windows.values()) {
      if (String(win?.dataset?.kind || "") !== "dm") continue;
      ecUpdateDmStatus(win, win?.dataset?.pmPeer || "");
    }
  } catch {}
}

function openPrivateChat(username, opts = {}) {
  const peer = ecPmPeerName(username);
  if (!peer) return null;

  const currentUserName = ecPmPeerName(window.CURRENT_USER || window.USERNAME || '');
  if (peer && currentUserName && ecSamePmPeer(peer, currentUserName) && !opts?.allowSelf) {
    toast("ℹ️ You cannot open a private message window to yourself.", "info");
    return null;
  }

  const consumeOffline = opts?.consumeOffline !== false;
  const consumePromptUnlock = !!opts?.promptUnlock;
  const consumeQuiet = opts?.quiet !== false;

  const id = ecPmWindowId(peer);
  const existed = UIState.windows.has(id);
  const win = createWindow({ id, title: ecPrivateMessageWindowTitle(peer), kind: "dm" });
  if (!win) return null;
  win.dataset.pmPeer = peer;
  win.dataset.pmPeerKey = ecPmPeerKey(peer);
  win.dataset.mobileSheet = "pm";
  ecUpdatePrivateMessageWindowTitle(win, peer);
  if (opts?.clearLiveUnread !== false) {
    try { ecClearLivePmUnread(peer); } catch {}
  }

  // Load local history (if enabled) once per window.
  ensureDmHistoryRendered(win, peer);
  try { ecBindDirectTypingInput(win, peer); } catch {}
  ecUpdateDmStatus(win, peer);

  if (!existed) {
    win._ym.send.onclick = async () => {
      const input = win._ym?.input || null;
      const sendBtn = win._ym?.send || null;
      const msg = input?.value?.trim() || '';
      if (!msg) return;

      const optimistic = (typeof ecComposerBeginOptimisticSend === 'function')
        ? ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })
        : null;

      try {
        // Magnet paste → render as torrent card in chat
        if (typeof isMagnetText === "function" && isMagnetText(msg)) {
          const meta = await sendTorrentMagnetShare(peer, msg, { win });
          if (meta) {
            addPmHistory(peer, "out", `🧲 Magnet: ${meta.name || meta.infohash}`);
            try { ecConversationTypingStop(input, { force: true }); } catch {}
            optimistic?.commit?.();
            ecUpdateDmStatus(win, peer);
          } else {
            optimistic?.restore?.('Magnet send failed');
          }
          return;
        }

        let sendText = msg;
        if (typeof ecLimitOutgoingChatEmoticons === "function") {
          const limited = await ecLimitOutgoingChatEmoticons(sendText, { surface: "pm" });
          sendText = String(limited?.text ?? sendText).trim();
        }
        if (!sendText) {
          optimistic?.restore?.('Message empty after emoticon filter');
          toast("Message empty after emoticon filter", "info", 3500);
          return;
        }

        const ok = await sendPrivateTo(peer, sendText);
        if (ok) {
          appendLine(win, "You:", sendText);
          addPmHistory(peer, "out", sendText);
          try { ecConversationTypingStop(input, { force: true }); } catch {}
          optimistic?.commit?.();
          ecUpdateDmStatus(win, peer);
        } else {
          optimistic?.restore?.('PM send failed');
        }
      } catch (e) {
        optimistic?.restore?.(e?.message || 'Message send failed');
        console.error(e);
        toast("❌ Message send failed", "error");
      }
    };

    // DM GIF button (send without touching the composer input)
    if (win._ym?.gifBtn) {
      win._ym.gifBtn.onclick = () => {
        openGifPicker(async (url) => {
          const clean = url;
          const msg = `gif:${clean}`;
          try {
            const ok = await sendPrivateTo(peer, msg);
            if (ok) {
              appendLine(win, "You:", msg);
              addPmHistory(peer, "out", msg);
              ecUpdateDmStatus(win, peer);
            } else {
              toast("❌ GIF send failed", "error");
            }
          } catch (e) {
            console.error(e);
            toast(`❌ GIF send failed: ${e?.message || e}`, "error");
          }
        });
      };
    }

    // File share (encrypted upload) button between log + compose
    if (win._ym.fileBtn && win._ym.fileInput) {
      win._ym.fileBtn.onclick = () => win._ym.fileInput.click();
      win._ym.fileInput.onchange = async () => {
        const f = win._ym.fileInput.files && win._ym.fileInput.files[0];
        // Reset selection immediately so reselecting the same file triggers change
        win._ym.fileInput.value = "";
        if (!f) return;

        try {
          if (isTorrentName(f.name)) {
            toast(`🧲 Sharing torrent ${f.name}…`, "info", 1600);
            await sendTorrentShare(peer, f, { win });
            addPmHistory(peer, "out", `🧲 Torrent: ${f.name}`);
            ecUpdateDmStatus(win, peer);
            toast(`✅ Torrent shared with ${peer}`, "ok");
            return;
          }

          toast(`⬆️ Uploading ${f.name}…`, "info", 1600);
          const payload = await sendDmFileTo(peer, f, { win });
          if (payload) {
            appendDmPayload(win, "You:", payload, { peer, direction: "out" });
            addPmHistory(peer, "out", `📎 ${payload.name} (${humanBytes(payload.size)})`);
            ecUpdateDmStatus(win, peer);
            toast(`✅ Sent file to ${peer}`, "ok");
          }
        } catch (e) {
          console.error(e);
          toast(`❌ File send failed: ${e?.message || e}`, "error");
        }
      };
    }

    // Voice controls
    if (win._ym.voiceBtn) {
      // Start hidden by default
      voiceDmUi(peer, { statusText: "Not connected", mode: "idle", hideBar: true });

      win._ym.voiceBtn.onclick = () => voiceToggleDmMain(peer);
      win._ym.voiceBtn.oncontextmenu = (ev) => {
        try {
          ev.preventDefault();
          if (!VOICE_STATE.micStream) return false;
          const muted = !VOICE_STATE.micMuted;
          voiceSetMute(muted);
          voiceDmUi(peer, { muteLabel: muted ? "Unmute" : "Mute" });
          voiceUpdateDmVoiceButton(peer);
          toast(muted ? "🔇 Mic muted" : "🎤 Mic unmuted", "info");
        } catch (e) {}
        return false;
      };

      win._ym.voiceBtnCall && (win._ym.voiceBtnCall.onclick = () => voiceStartDmCall(peer));
      win._ym.voiceBtnHang && (win._ym.voiceBtnHang.onclick = () => voiceHangupDm(peer, "Ended", true));
      win._ym.voiceBtnMute && (win._ym.voiceBtnMute.onclick = () => voiceToggleMuteDm(peer));
      win._ym.voiceBtnAccept && (win._ym.voiceBtnAccept.onclick = () => voiceAcceptDmCall(peer));
      win._ym.voiceBtnDecline && (win._ym.voiceBtnDecline.onclick = () => voiceDeclineDmCall(peer, "Declined"));
      [win._ym.voiceBtnCall, win._ym.voiceBtnHang, win._ym.voiceBtnMute, win._ym.voiceBtnAccept, win._ym.voiceBtnDecline].forEach((btn) => { if (btn) btn.type = 'button'; });
      try { voiceWireWindowTalkControls(win); } catch (e) {}
    }
  }

  bringToFront(win);

  // If this DM window is open, the missed-messages sidebar should not keep showing this peer.
  // Consume any offline queue for this peer ONLY when it makes sense:
  // - Only if we actually have missed messages for this peer
  // - Only if we can decrypt now (key already unlocked) OR the caller explicitly wants to prompt
  // This avoids a common failure mode where the app "peeks" while locked, hides the missed entry,
  // and then the bubble reappears after refresh because nothing was actually ACKed.
  if (consumeOffline) {
    try {
      // Always check once per DM window open, even if the missed summary arrives later.
      // This prevents a race where the user opens the DM before we received missed_pm_summary,
      // which would otherwise skip consumption and keep the bubble stuck.
      if (!win._ym.__offlineChecked) {
        win._ym.__offlineChecked = true;

        // Consume server-side; ciphertext is queued locally if private messages are not ready.
        consumeOfflinePmsForPeer(peer, { promptUnlock: consumePromptUnlock, quiet: consumeQuiet })
          .finally(() => ecUpdateDmStatus(win, peer));

        // Optional hint (once) when locked and we did not prompt.
        if (!window.myPrivateCryptoKey && !consumePromptUnlock) {
          try {
            if (!win._ym.__missedHintShown) {
              win._ym.__missedHintShown = true;
              appendLine(win, "System:", `📨 Missed messages from ${peer} are saved until private messages unlock. Use the Load missed button in the status strip, or sign out and sign back in if they do not appear.`, "system");
              ecUpdateDmStatus(win, peer);
            }
          } catch {}
        }
      }
    } catch {}
  }

  return win;
}
// ───────────────────────────────────────────────────────────────────────────────
async function sendPrivateTo(to, plaintext) {
  const allowPlain = DM_PLAINTEXT_COMPAT_ALLOWED;
  let outgoingPlaintext = String(plaintext ?? "");
  if (typeof ecLimitCodeEmoticonsInText === "function") {
    // Silent guard for callers that did not pass through the visible composer path.
    const limited = ecLimitCodeEmoticonsInText(outgoingPlaintext);
    outgoingPlaintext = String(limited?.text ?? outgoingPlaintext);
  }
  const targetUser = ecPmPeerName(to);
  const currentUser = ecPmPeerName(window.CURRENT_USER || window.USERNAME || '');

  if (!targetUser) {
    toast("❌ Missing PM recipient", "error");
    return false;
  }

  if (targetUser && currentUser && ecSamePmPeer(targetUser, currentUser)) {
    toast("ℹ️ You cannot send a private message to yourself.", "info");
    return false;
  }

  if (typeof ecIsBlockedPrivateMessageSender === "function" && ecIsBlockedPrivateMessageSender(targetUser)) {
    toast(`⛔ PM blocked between you and ${targetUser}`, "error");
    try { socket.emit("get_missed_pm_summary"); } catch {}
    return false;
  }

  const emitDmAck = (payload, timeoutMs = 8000) => {
    if (typeof ecEmitAck === "function") {
      return ecEmitAck("send_direct_message", payload, timeoutMs, {
        connectBannerText: "🔌 Reconnecting before sending PM…",
      }).then((res) => (res && typeof res === "object") ? res : { success: false, error: "No response from server" });
    }
    return new Promise((resolve) => {
      let done = false;
      const timer = setTimeout(() => {
        if (done) return;
        done = true;
        resolve({ success: false, error: "Socket ACK timeout" });
      }, timeoutMs);

      try {
        socket.emit("send_direct_message", payload, (res) => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          resolve((res && typeof res === "object") ? res : { success: false, error: "No response from server" });
        });
      } catch (e) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve({ success: false, error: String(e?.message || e || "Socket emit failed") });
      }
    });
  };

  const describeDmError = (res, fallbackUser) => {
    const raw = String(res?.error || res?.message || "PM failed");
    const low = raw.toLowerCase();
    if (low.includes("cannot dm yourself") || low.includes("self_dm_disabled")) {
      return "ℹ️ You cannot send a private message to yourself.";
    }
    if (low.includes("blocked")) {
      return `⛔ PM blocked between you and ${fallbackUser}`;
    }
    if (low.includes("user_not_found") || low.includes("invalid_username") || low.includes("username_required")) {
      return `❌ PM user not found: ${fallbackUser}`;
    }
    if (low.includes("target_not_active")) {
      return `⛔ ${fallbackUser} cannot receive private messages right now.`;
    }
    if (low.includes("rate limit")) {
      return `⏳ ${raw}`;
    }
    if (low.includes("quota exceeded")) {
      return `⏳ ${raw}`;
    }
    if (low.includes("socket ack timeout") || low.includes("no response from server")) {
      return "⚠️ PM server did not respond in time.";
    }
    if (low.includes("dm_requires_e2ee")) {
      return "🔒 This server requires encrypted private messages.";
    }
    if (low.includes("plaintext_dm_disabled")) {
      return "🔒 Plaintext DM fallback is disabled on this server.";
    }
    if (low.includes("missing recipient or message")) {
      return "❌ Missing PM recipient or message.";
    }
    return `❌ PM to ${fallbackUser} failed: ${raw}`;
  };

  // If WebCrypto isn't available (non-HTTPS/non-localhost), optionally fall back to plaintext wrapper.
  if (!HAS_WEBCRYPTO) {
    if (allowPlain) {
      try {
        const cipher = wrapPlainDm(outgoingPlaintext);
        const ok = await new Promise((resolve) => {
          emitDmAck({ to: targetUser, cipher }).then((res) => resolve(res));
        });
        if (ok?.success) {
          toast("⚠️ Sent without E2EE (compat mode)", "warn", 2600);
          return true;
        }
        toast(describeDmError(ok, targetUser), "error");
      } catch (e) {
        console.error(e);
      }
    }
    toast("🔒 Private messages require HTTPS or http://localhost.", "warn");
    return false;
  }

  // Normal E2EE path (hybrid RSA-OAEP + AES-GCM envelope)
  try {
    // IMPORTANT: do not rely on a long-lived cached pubkey for DMs.
    // Keys can rotate (e.g., after password reset), and stale caches cause 1-way "could not decrypt".
    const rsaPubKey = await getUserRsaPublicKey(targetUser, { forceRefresh: true });

    const encoder = new TextEncoder();
    const msgBytes = encoder.encode(String(outgoingPlaintext ?? ""));

    const aesKey = await window.crypto.subtle.generateKey(
      { name: "AES-GCM", length: 256 },
      true,
      ["encrypt", "decrypt"]
    );
    const iv = window.crypto.getRandomValues(new Uint8Array(12));
    const ctBuffer = await window.crypto.subtle.encrypt({ name: "AES-GCM", iv }, aesKey, msgBytes);
    const rawAesKey = await window.crypto.subtle.exportKey("raw", aesKey);
    const wrappedKey = await window.crypto.subtle.encrypt({ name: "RSA-OAEP" }, rsaPubKey, rawAesKey);

    const envelope = {
      v: 1,
      alg: "RSA-OAEP+AES-GCM",
      ek: b64FromBytes(new Uint8Array(wrappedKey)),
      iv: b64FromBytes(iv),
      ct: b64FromBytes(new Uint8Array(ctBuffer))
    };

    const cipher = PM_ENVELOPE_PREFIX + btoa(JSON.stringify(envelope));

    const ok = await new Promise((resolve) => {
      emitDmAck({ to: targetUser, cipher }).then((res) => resolve(res));
    });

    if (!ok?.success) {
      toast(describeDmError(ok, targetUser), ok?.error && String(ok.error).toLowerCase().includes("cannot dm yourself") ? "info" : "error");
      return false;
    }
    if (ok?.queued_offline === true) {
      toast(`📬 ${targetUser} is offline. PM saved for later delivery.`, "info", 2600, { event: "dm", dedupeKey: `pm-queued:${targetUser}` });
    }
    return true;
  } catch (e) {
    console.error(e);
    const encErr = String(e?.message || e || "").toLowerCase();
    if (encErr.includes("blocked")) {
      toast(describeDmError({ error: "blocked" }, targetUser), "error");
      return false;
    }
    if (encErr.includes("user_not_found") || encErr.includes("target_not_active") || encErr.includes("invalid_username") || encErr.includes("username_required")) {
      toast(describeDmError({ error: e?.message || e }, targetUser), "error");
      return false;
    }

    // Compatibility: peer may lack keys (or server refused /get_public_key). Optionally fall back.
    if (allowPlain) {
      try {
        const cipher = wrapPlainDm(outgoingPlaintext);
        const ok = await new Promise((resolve) => {
          emitDmAck({ to: targetUser, cipher }).then((res) => resolve(res));
        });
        if (ok?.success) {
          toast("⚠️ Sent without E2EE (peer missing keys)", "warn", 2600);
          return true;
        }
        toast(describeDmError(ok, targetUser), "error");
      } catch (e2) {
        console.error(e2);
      }
    }

    toast("❌ Failed to encrypt or send PM", "error");
    return false;
  }
}
