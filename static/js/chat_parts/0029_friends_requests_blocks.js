// ───────────────────────────────────────────────────────────────────────────────
// Friends / Requests / Blocks
// ───────────────────────────────────────────────────────────────────────────────

let EC_CONFIRM_MODAL = null;
let EC_CONFIRM_ACTIVE = null;

function ensureCustomConfirmModal() {
  if (EC_CONFIRM_MODAL) return EC_CONFIRM_MODAL;

  const modal = ecCreateEl('div', { id: 'ecConfirmModal', className: 'modal hidden', role: 'dialog', ariaModal: 'true', attrs: { 'aria-labelledby': 'ecConfirmTitle' } });
  const card = ecCreateEl('div', { className: 'modalCard confirmModalCard' });
  card.appendChild(ecCreateEl('div', { className: 'modalHead' }, [
    ecCreateEl('div', { id: 'ecConfirmTitle', className: 'modalTitle', text: 'Confirm action' })
  ]));
  card.appendChild(ecCreateEl('div', { className: 'modalBody' }, [
    ecCreateEl('div', { id: 'ecConfirmMessage', className: 'confirmModalMessage' })
  ]));
  card.appendChild(ecCreateEl('div', { className: 'modalFoot confirmModalFoot' }, [
    ecCreateEl('button', { id: 'ecConfirmCancel', className: 'ghostBtn', type: 'button', text: 'Cancel' }),
    ecCreateEl('button', { id: 'ecConfirmOk', className: 'primaryBtn', type: 'button', text: 'OK' })
  ]));
  modal.appendChild(card);

  modal.addEventListener('mousedown', (ev) => {
    if (ev.target === modal) resolveCustomConfirm(false);
  });

  const cancelBtn = modal.querySelector('#ecConfirmCancel');
  const okBtn = modal.querySelector('#ecConfirmOk');
  cancelBtn?.addEventListener('click', () => resolveCustomConfirm(false));
  okBtn?.addEventListener('click', () => resolveCustomConfirm(true));

  document.addEventListener('keydown', (ev) => {
    if (!EC_CONFIRM_ACTIVE || !EC_CONFIRM_MODAL || EC_CONFIRM_MODAL.classList.contains('hidden')) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      resolveCustomConfirm(false);
      return;
    }
    if (ev.key === 'Enter') {
      const target = ev.target;
      const tag = String(target?.tagName || '').toLowerCase();
      if (tag === 'textarea') return;
      if (tag === 'input' && /^(button|submit|checkbox|radio)$/i.test(String(target?.type || ''))) return;
      ev.preventDefault();
      resolveCustomConfirm(true);
    }
  });

  (document.body || document.documentElement).appendChild(modal);
  EC_CONFIRM_MODAL = modal;
  return modal;
}

function resolveCustomConfirm(result) {
  if (!EC_CONFIRM_ACTIVE || !EC_CONFIRM_MODAL) return;
  const active = EC_CONFIRM_ACTIVE;
  EC_CONFIRM_ACTIVE = null;
  EC_CONFIRM_MODAL.classList.add('hidden');
  EC_CONFIRM_MODAL.dataset.kind = '';
  const okBtn = EC_CONFIRM_MODAL.querySelector('#ecConfirmOk');
  okBtn?.classList.remove('danger');
  try {
    active.restoreFocus?.focus?.();
  } catch {}
  try {
    active.resolve(!!result);
  } catch {}
}

function ecConfirm(message, opts = {}) {
  const modal = ensureCustomConfirmModal();
  if (EC_CONFIRM_ACTIVE) {
    try { EC_CONFIRM_ACTIVE.resolve(false); } catch {}
    EC_CONFIRM_ACTIVE = null;
  }

  const titleEl = modal.querySelector('#ecConfirmTitle');
  const msgEl = modal.querySelector('#ecConfirmMessage');
  const cancelBtn = modal.querySelector('#ecConfirmCancel');
  const okBtn = modal.querySelector('#ecConfirmOk');

  if (titleEl) titleEl.textContent = String(opts.title || 'Confirm action');
  if (msgEl) msgEl.textContent = String(message || '');
  if (cancelBtn) {
    cancelBtn.textContent = String(opts.cancelLabel || 'Cancel');
    cancelBtn.classList.toggle('hidden', !!opts.hideCancel);
  }
  if (okBtn) {
    okBtn.textContent = String(opts.confirmLabel || 'OK');
    okBtn.classList.toggle('danger', !!opts.danger);
  }

  modal.classList.remove('hidden');
  modal.dataset.kind = String(opts.danger ? 'danger' : 'default');

  return new Promise((resolve) => {
    EC_CONFIRM_ACTIVE = {
      resolve,
      restoreFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    };
    window.setTimeout(() => {
      try {
        if (opts.focusCancel && cancelBtn && !cancelBtn.classList.contains('hidden')) cancelBtn.focus();
        else okBtn?.focus();
      } catch {}
    }, 0);
  });
}

window.ecConfirm = ecConfirm;


let EC_PROMPT_MODAL = null;
let EC_PROMPT_ACTIVE = null;

function ensureCustomPromptModal() {
  if (EC_PROMPT_MODAL) return EC_PROMPT_MODAL;

  const modal = ecCreateEl('div', { id: 'ecPromptModal', className: 'modal hidden', role: 'dialog', ariaModal: 'true', attrs: { 'aria-labelledby': 'ecPromptTitle' } });
  const card = ecCreateEl('div', { className: 'modalCard confirmModalCard' });
  card.appendChild(ecCreateEl('div', { className: 'modalHead' }, [
    ecCreateEl('div', { id: 'ecPromptTitle', className: 'modalTitle', text: 'Enter value' })
  ]));
  card.appendChild(ecCreateEl('div', { className: 'modalBody' }, [
    ecCreateEl('div', { id: 'ecPromptMessage', className: 'confirmModalMessage' }),
    ecCreateEl('label', { id: 'ecPromptLabel', className: 'fieldLabel', htmlFor: 'ecPromptInput', text: 'Value' }),
    ecCreateEl('input', { id: 'ecPromptInput', className: 'modalInput', type: 'text', autocomplete: 'off' })
  ]));
  card.appendChild(ecCreateEl('div', { className: 'modalFoot confirmModalFoot' }, [
    ecCreateEl('button', { id: 'ecPromptCancel', className: 'ghostBtn', type: 'button', text: 'Cancel' }),
    ecCreateEl('button', { id: 'ecPromptOk', className: 'primaryBtn', type: 'button', text: 'OK' })
  ]));
  modal.appendChild(card);

  modal.addEventListener('mousedown', (ev) => {
    if (ev.target === modal) resolveCustomPrompt(null);
  });

  const cancelBtn = modal.querySelector('#ecPromptCancel');
  const okBtn = modal.querySelector('#ecPromptOk');
  cancelBtn?.addEventListener('click', () => resolveCustomPrompt(null));
  okBtn?.addEventListener('click', () => {
    const input = modal.querySelector('#ecPromptInput');
    resolveCustomPrompt(input ? String(input.value || '') : '');
  });

  document.addEventListener('keydown', (ev) => {
    if (!EC_PROMPT_ACTIVE || !EC_PROMPT_MODAL || EC_PROMPT_MODAL.classList.contains('hidden')) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      resolveCustomPrompt(null);
      return;
    }
    if (ev.key === 'Enter') {
      ev.preventDefault();
      const input = EC_PROMPT_MODAL.querySelector('#ecPromptInput');
      resolveCustomPrompt(input ? String(input.value || '') : '');
    }
  });

  (document.body || document.documentElement).appendChild(modal);
  EC_PROMPT_MODAL = modal;
  return modal;
}

function resolveCustomPrompt(result) {
  if (!EC_PROMPT_ACTIVE || !EC_PROMPT_MODAL) return;
  const active = EC_PROMPT_ACTIVE;
  EC_PROMPT_ACTIVE = null;
  EC_PROMPT_MODAL.classList.add('hidden');
  const input = EC_PROMPT_MODAL.querySelector('#ecPromptInput');
  if (input) {
    input.value = '';
    input.removeAttribute('maxlength');
    input.removeAttribute('placeholder');
  }
  try {
    active.restoreFocus?.focus?.();
  } catch {}
  try {
    active.resolve(result === null ? null : String(result));
  } catch {}
}

function ecPrompt(message, defaultValue = '', opts = {}) {
  const modal = ensureCustomPromptModal();
  if (EC_PROMPT_ACTIVE) {
    try { EC_PROMPT_ACTIVE.resolve(null); } catch {}
    EC_PROMPT_ACTIVE = null;
  }

  const titleEl = modal.querySelector('#ecPromptTitle');
  const msgEl = modal.querySelector('#ecPromptMessage');
  const labelEl = modal.querySelector('#ecPromptLabel');
  const input = modal.querySelector('#ecPromptInput');
  const cancelBtn = modal.querySelector('#ecPromptCancel');
  const okBtn = modal.querySelector('#ecPromptOk');

  if (titleEl) titleEl.textContent = String(opts.title || 'Enter value');
  if (msgEl) msgEl.textContent = String(message || '');
  if (labelEl) labelEl.textContent = String(opts.inputLabel || 'Value');
  if (cancelBtn) cancelBtn.textContent = String(opts.cancelLabel || 'Cancel');
  if (okBtn) okBtn.textContent = String(opts.confirmLabel || 'OK');
  if (input) {
    input.value = String(defaultValue || '');
    input.type = String(opts.type || 'text');
    if (opts.maxLength) input.maxLength = Number(opts.maxLength);
    else input.removeAttribute('maxlength');
    if (opts.placeholder) input.placeholder = String(opts.placeholder);
    else input.removeAttribute('placeholder');
    input.setAttribute('aria-label', String(opts.inputLabel || opts.title || 'Value'));
  }

  modal.classList.remove('hidden');

  return new Promise((resolve) => {
    EC_PROMPT_ACTIVE = {
      resolve,
      restoreFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    };
    window.setTimeout(() => {
      try {
        input?.focus();
        input?.select?.();
      } catch {}
    }, 0);
  });
}

window.ecPrompt = ecPrompt;

function ecNormalizeFriendRows(rows) {
  const names = ecCanonicalUsernameList(Array.isArray(rows) ? rows : [], { excludeSelf: true, excludeBlocked: true });
  try {
    (Array.isArray(rows) ? rows : []).forEach((row) => {
      if (!row || typeof row !== 'object') return;
      const username = String(row.username || row.name || row.user || row.friend || '').replace(/\s+/g, ' ').trim();
      if (!username) return;
      ecSetPresenceForUsername(username, {
        online: !!row.online,
        presence: row.presence || row.presence_status || (row.online ? 'online' : 'offline'),
        custom_status: row.custom_status || row.customStatus || '',
        last_seen: row.last_seen || row.lastSeen || null,
        avatar_url: row.avatar_url || row.avatarUrl || '',
      });
    });
  } catch (_) {}
  return names;
}

async function ecLoadFriendsViaHttpFallback(reason = '') {
  try {
    if (typeof fetchWithAuth !== 'function') return false;
    const resp = await fetchWithAuth('/api/friends', { method: 'GET' }, { retryOn401: true });
    const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
    if (!resp || !resp.ok) return false;
    const friends = ecNormalizeFriendRows(Array.isArray(data) ? data : (Array.isArray(data?.friends) ? data.friends : []));
    updateFriendsListUI(friends);
    try { socket.emit("get_friend_presence"); } catch (_) {}
    return true;
  } catch (e) {
    try { console.warn('[Hui Chat] friends HTTP fallback failed', reason, e); } catch {}
  }
  return false;
}

async function getFriends(opts = {}) {
  const useAck = typeof ecEmitAck === 'function';
  if (useAck) {
    const res = await ecEmitAck('get_friends', {}, Number(opts.timeoutMs || 6500), { connectBannerText: '🔌 Loading friends…' });
    if (res && res.success !== false && Array.isArray(res.friends)) {
      const friends = ecNormalizeFriendRows(res.friends);
      updateFriendsListUI(friends);
      try { socket.emit("get_friend_presence"); } catch (_) {}
      return friends;
    }
    await ecLoadFriendsViaHttpFallback(res?.error || 'socket_ack_failed');
    return [];
  }

  try {
    socket.emit("get_friends", {}, (res) => {
      if (res && Array.isArray(res.friends)) {
        updateFriendsListUI(ecNormalizeFriendRows(res.friends));
        try { socket.emit("get_friend_presence"); } catch (_) {}
      } else {
        ecLoadFriendsViaHttpFallback('socket_empty_response');
      }
    });
  } catch (_) {
    ecLoadFriendsViaHttpFallback('socket_emit_failed');
  }
  return [];
}

function getPendingFriendRequests() {
  socket.emit("get_pending_friend_requests");
}

function getBlockedUsers() {
  socket.emit("get_blocked_users");
}

const FRIEND_GROUP_DEFAULT_KEY = '__friends__';
const FRIEND_GROUP_DEFAULT_LABEL = 'Friends';
let EC_FRIEND_GROUP_CTX_MENU = null;
let EC_ACTIVE_FRIEND_DRAG = null;
const EC_PENDING_FRIEND_ACTIONS = new Set();
let EC_FRIENDS_RENDER_REFRESH_TIMER = null;
let EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL = 0;

function getFriendGroupStorageKey() {
  return `friendGroups_${String(currentUser || 'guest')}`;
}

function normalizeFriendGroupName(name) {
  return String(name || '').replace(/\s+/g, ' ').trim().slice(0, 40);
}

function scheduleFriendsListRenderRefresh(reason = '') {
  if (EC_FRIENDS_RENDER_REFRESH_TIMER) window.clearTimeout(EC_FRIENDS_RENDER_REFRESH_TIMER);
  EC_FRIENDS_RENDER_REFRESH_TIMER = window.setTimeout(() => {
    EC_FRIENDS_RENDER_REFRESH_TIMER = null;
    try { updateFriendsListUI(UIState.friendsListCache || []); } catch (e) {}
  }, 120);
}

function ecDumpFriendsPresenceState() {
  const friends = Array.isArray(UIState.friendsListCache) ? UIState.friendsListCache.slice() : [];
  const presence = friends.map((friend) => {
    const p = (typeof ecGetPresenceForUsername === 'function') ? ecGetPresenceForUsername(friend) : null;
    return { friend, presence: p || null };
  });
  const dom = [...document.querySelectorAll('#friendsList .friendItem')].map((li) => ({
    name: li.dataset.name || '',
    offlineClass: li.classList.contains('offline'),
    text: li.textContent || ''
  }));
  const out = { friends, presence, dom };
  try { console.table(presence.map((row) => ({ friend: row.friend, online: !!row.presence?.online, presence: row.presence?.presence || 'missing' }))); } catch (_) {}
  console.log('[Hui Chat] friends presence state', out);
  return out;
}
window.ecDumpFriendsPresenceState = ecDumpFriendsPresenceState;

function getFriendAssignmentKey(friend) {
  return ecNormalizeUsernameKey(friend);
}

function getCanonicalFriendNameFromCache(friend) {
  const key = ecNormalizeUsernameKey(friend);
  if (!key) return String(friend || '').trim();
  return (Array.isArray(UIState.friendsListCache) ? UIState.friendsListCache : []).find((name) => ecNormalizeUsernameKey(name) === key) || String(friend || '').trim();
}


function friendGroupStoredNameFromKey(groupKey) {
  return String(groupKey || '') === FRIEND_GROUP_DEFAULT_KEY ? '' : String(groupKey || '');
}

function getFriendGroupingState() {
  if (UIState.friendGroups && typeof UIState.friendGroups === 'object') return UIState.friendGroups;
  const raw = Settings.get(getFriendGroupStorageKey(), null);
  const state = {
    groups: Array.isArray(raw?.groups) ? raw.groups.map(normalizeFriendGroupName).filter(Boolean) : [],
    assignments: (raw && typeof raw.assignments === 'object' && raw.assignments) ? { ...raw.assignments } : {},
    collapsed: (raw && typeof raw.collapsed === 'object' && raw.collapsed) ? { ...raw.collapsed } : {}
  };
  const seen = new Set();
  state.groups = state.groups.filter((name) => {
    const key = name.toLowerCase();
    if (!name || seen.has(key) || key === FRIEND_GROUP_DEFAULT_LABEL.toLowerCase()) return false;
    seen.add(key);
    return true;
  });
  const normalizedAssignments = {};
  Object.keys(state.assignments).forEach((friend) => {
    const friendKey = getFriendAssignmentKey(friend);
    const grp = normalizeFriendGroupName(state.assignments[friend]);
    if (!friendKey || !grp) return;
    const actualGroup = state.groups.find((name) => name.toLowerCase() === grp.toLowerCase()) || '';
    if (!actualGroup) return;
    normalizedAssignments[friendKey] = actualGroup;
  });
  state.assignments = normalizedAssignments;
  UIState.friendGroups = state;
  return state;
}

function saveFriendGroupingState() {
  const state = getFriendGroupingState();
  Settings.set(getFriendGroupStorageKey(), state);
}

function findFriendGroupName(name) {
  const cleaned = normalizeFriendGroupName(name);
  if (!cleaned) return '';
  const state = getFriendGroupingState();
  return state.groups.find((groupName) => groupName.toLowerCase() === cleaned.toLowerCase()) || '';
}

function ensureFriendGroup(name) {
  const cleaned = normalizeFriendGroupName(name);
  if (!cleaned) return '';
  const existing = findFriendGroupName(cleaned);
  if (existing) return existing;
  const state = getFriendGroupingState();
  state.groups.push(cleaned);
  saveFriendGroupingState();
  return cleaned;
}

function renameFriendGroup(oldName, newName) {
  const current = findFriendGroupName(oldName);
  const next = normalizeFriendGroupName(newName);
  if (!current || !next) return false;
  const dupe = findFriendGroupName(next);
  if (dupe && dupe !== current) return false;
  const state = getFriendGroupingState();
  state.groups = state.groups.map((name) => name === current ? next : name);
  Object.keys(state.assignments).forEach((friend) => {
    if (state.assignments[friend] === current) state.assignments[friend] = next;
  });
  if (Object.prototype.hasOwnProperty.call(state.collapsed, current)) {
    state.collapsed[next] = !!state.collapsed[current];
    delete state.collapsed[current];
  }
  saveFriendGroupingState();
  return true;
}

function deleteFriendGroup(groupName) {
  const existing = findFriendGroupName(groupName);
  if (!existing) return false;
  const state = getFriendGroupingState();
  state.groups = state.groups.filter((name) => name !== existing);
  Object.keys(state.assignments).forEach((friend) => {
    if (state.assignments[friend] === existing) delete state.assignments[friend];
  });
  delete state.collapsed[existing];
  saveFriendGroupingState();
  return true;
}

function getFriendGroupForFriend(friend) {
  const state = getFriendGroupingState();
  return findFriendGroupName(state.assignments[getFriendAssignmentKey(friend)]) || '';
}

function setFriendCollapsed(groupKey, collapsed) {
  const state = getFriendGroupingState();
  const key = String(groupKey || '');
  if (!key) return;
  state.collapsed[key] = !!collapsed;
  saveFriendGroupingState();
}

function assignFriendToGroup(friend, groupName) {
  const u = String(friend || '').trim();
  const key = getFriendAssignmentKey(u);
  if (!u || !key) return false;
  const state = getFriendGroupingState();
  const existing = findFriendGroupName(groupName);
  if (existing) state.assignments[key] = existing;
  else delete state.assignments[key];
  saveFriendGroupingState();
  return true;
}

async function promptForFriendGroup(defaultValue = '', opts = {}) {
  const allowBlankToUngroup = !!opts.allowBlankToUngroup;
  const state = getFriendGroupingState();
  let msg = 'Friend group name:';
  if (state.groups.length) msg += `\nExisting groups: ${state.groups.join(', ')}`;
  if (allowBlankToUngroup) msg += `\nLeave blank to move back to ${FRIEND_GROUP_DEFAULT_LABEL}.`;
  const raw = await ecPrompt(msg, defaultValue || '', {
    title: opts.title || 'Friend group',
    inputLabel: 'Group name',
    confirmLabel: opts.confirmLabel || 'Save',
    maxLength: 40,
    placeholder: 'Example: Work friends',
  });
  if (raw === null) return null;
  const cleaned = normalizeFriendGroupName(raw);
  if (!cleaned && allowBlankToUngroup) return '';
  return cleaned;
}

function getFriendGroupMeta(friends) {
  const state = getFriendGroupingState();
  const knownFriendKeys = new Set((Array.isArray(friends) ? friends : []).map((name) => getFriendAssignmentKey(name)).filter(Boolean));
  Object.keys(state.assignments).forEach((friendKey) => {
    if (!knownFriendKeys.has(friendKey)) delete state.assignments[friendKey];
  });

  const buckets = new Map();
  buckets.set(FRIEND_GROUP_DEFAULT_KEY, []);
  state.groups.forEach((name) => buckets.set(name, []));

  (Array.isArray(friends) ? friends : []).forEach((friend) => {
    const uname = String(friend);
    const assigned = getFriendGroupForFriend(uname);
    const key = assigned || FRIEND_GROUP_DEFAULT_KEY;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(uname);
  });

  buckets.forEach((list, key) => list.sort((a, b) => String(a).localeCompare(String(b), undefined, { sensitivity: 'base' })));
  saveFriendGroupingState();

  return [
    { key: FRIEND_GROUP_DEFAULT_KEY, name: FRIEND_GROUP_DEFAULT_LABEL, members: buckets.get(FRIEND_GROUP_DEFAULT_KEY) || [], isDefault: true },
    ...state.groups.map((name) => ({ key: name, name, members: buckets.get(name) || [], isDefault: false }))
  ];
}

function clearFriendDragArmedFlags() {
  document.querySelectorAll('.friendItem[data-drag-armed="1"], .friendGroupHeader[data-drag-armed="1"]').forEach((el) => {
    delete el.dataset.dragArmed;
  });
}

function clearFriendGroupDragOver() {
  document.querySelectorAll('.friendGroupHeader.dragOver, .friendGroupEmpty.dragOver, .friendItem.dragOver').forEach((el) => el.classList.remove('dragOver'));
}

function getFriendDropTargetFromPoint(x, y) {
  let el = null;
  try { el = document.elementFromPoint(Number(x || 0), Number(y || 0)); } catch (_) { el = null; }
  return el?.closest?.('[data-friend-drop-key]') || null;
}

function setFriendDropHoverTarget(target) {
  clearFriendGroupDragOver();
  if (target && target.classList) target.classList.add('dragOver');
}

function moveFriendDragPayloadToGroup(payload, groupKey, opts = {}) {
  if (!payload || payload.kind !== 'friend') return false;
  const targetKey = String(groupKey || FRIEND_GROUP_DEFAULT_KEY);
  if (payload.sourceGroupKey === targetKey) return false;
  const storedGroup = friendGroupStoredNameFromKey(targetKey);
  assignFriendToGroup(payload.friend, storedGroup);
  if (targetKey) setFriendCollapsed(targetKey, false);
  updateFriendsListUI(UIState.friendsListCache);
  if (!opts.silent) {
    toast(`📁 Moved ${payload.friend} to ${targetKey === FRIEND_GROUP_DEFAULT_KEY ? FRIEND_GROUP_DEFAULT_LABEL : targetKey}`, 'ok');
  }
  return true;
}

let EC_FRIEND_POINTER_DRAG = null;
let EC_FRIEND_POINTER_DRAG_GHOST = null;

function removeFriendPointerDragGhost() {
  try { EC_FRIEND_POINTER_DRAG_GHOST?.remove?.(); } catch (_) {}
  EC_FRIEND_POINTER_DRAG_GHOST = null;
}

function updateFriendPointerDragGhost(x, y, label = '') {
  if (!EC_FRIEND_POINTER_DRAG_GHOST) {
    const ghost = document.createElement('div');
    ghost.className = 'friendDragGhost';
    ghost.setAttribute('aria-hidden', 'true');
    (document.body || document.documentElement).appendChild(ghost);
    EC_FRIEND_POINTER_DRAG_GHOST = ghost;
  }
  EC_FRIEND_POINTER_DRAG_GHOST.textContent = label ? `Move ${label}` : 'Move friend';
  EC_FRIEND_POINTER_DRAG_GHOST.style.left = `${Math.round(Number(x || 0) + 12)}px`;
  EC_FRIEND_POINTER_DRAG_GHOST.style.top = `${Math.round(Number(y || 0) + 12)}px`;
}

function finishFriendPointerDrag(ev, cancelled = false) {
  const state = EC_FRIEND_POINTER_DRAG;
  if (!state) return;
  EC_FRIEND_POINTER_DRAG = null;
  const wasActive = !!state.active;
  const payload = EC_ACTIVE_FRIEND_DRAG;
  try { state.sourceEl?.releasePointerCapture?.(state.pointerId); } catch (_) {}
  try { state.sourceEl?.classList?.remove('dragging'); } catch (_) {}
  removeFriendPointerDragGhost();
  if (wasActive) {
    EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL = Date.now() + 500;
    if (!cancelled && payload && ev) {
      const target = getFriendDropTargetFromPoint(ev.clientX, ev.clientY);
      const targetKey = String(target?.dataset?.friendDropKey || '');
      if (targetKey) moveFriendDragPayloadToGroup(payload, targetKey);
    }
  }
  EC_ACTIVE_FRIEND_DRAG = null;
  clearFriendGroupDragOver();
}

function bindFriendPointerDrag(li, friend, groupKey) {
  if (!li || li.dataset.friendPointerDragBound === '1') return;
  li.dataset.friendPointerDragBound = '1';

  li.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    if (ev.target?.closest?.('button, a, input, textarea, select, .liActions')) return;
    EC_FRIEND_POINTER_DRAG = {
      friend,
      sourceGroupKey: groupKey,
      pointerId: ev.pointerId,
      startX: ev.clientX,
      startY: ev.clientY,
      sourceEl: li,
      active: false,
    };
    try { li.setPointerCapture?.(ev.pointerId); } catch (_) {}
  });

  li.addEventListener('pointermove', (ev) => {
    const state = EC_FRIEND_POINTER_DRAG;
    if (!state || state.sourceEl !== li || state.pointerId !== ev.pointerId) return;
    const dx = Number(ev.clientX || 0) - Number(state.startX || 0);
    const dy = Number(ev.clientY || 0) - Number(state.startY || 0);
    const moved = Math.hypot(dx, dy);
    if (!state.active && moved < 6) return;
    try { ev.preventDefault(); } catch (_) {}
    if (!state.active) {
      state.active = true;
      EC_ACTIVE_FRIEND_DRAG = { kind: 'friend', friend, sourceGroupKey: groupKey };
      li.classList.add('dragging');
    }
    updateFriendPointerDragGhost(ev.clientX, ev.clientY, friend);
    setFriendDropHoverTarget(getFriendDropTargetFromPoint(ev.clientX, ev.clientY));
  });

  li.addEventListener('pointerup', (ev) => finishFriendPointerDrag(ev, false));
  li.addEventListener('pointercancel', (ev) => finishFriendPointerDrag(ev, true));
}

function reorderFriendGroup(sourceKey, targetKey) {
  const source = friendGroupStoredNameFromKey(sourceKey);
  if (!source) return false;
  const state = getFriendGroupingState();
  const sourceName = findFriendGroupName(source);
  if (!sourceName) return false;
  const groups = state.groups.filter((name) => name !== sourceName);
  if (String(targetKey || '') === FRIEND_GROUP_DEFAULT_KEY) groups.unshift(sourceName);
  else {
    const targetName = findFriendGroupName(targetKey);
    const idx = groups.findIndex((name) => name === targetName);
    if (idx < 0) groups.push(sourceName);
    else groups.splice(idx, 0, sourceName);
  }
  state.groups = groups;
  saveFriendGroupingState();
  return true;
}

function bindFriendGroupDropTarget(el, groupKey) {
  if (!el || el.dataset.friendDropBound === '1') return;
  el.dataset.friendDropBound = '1';
  el.dataset.friendDropKey = groupKey;

  const markOver = (ev) => {
    if (!EC_ACTIVE_FRIEND_DRAG) return;
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
    el.classList.add('dragOver');
  };

  el.addEventListener('dragenter', markOver);
  el.addEventListener('dragover', markOver);

  el.addEventListener('dragleave', (ev) => {
    const rel = ev.relatedTarget;
    if (rel && el.contains(rel)) return;
    el.classList.remove('dragOver');
  });

  el.addEventListener('drop', (ev) => {
    if (!EC_ACTIVE_FRIEND_DRAG) return;
    ev.preventDefault();
    ev.stopPropagation();
    el.classList.remove('dragOver');
    const payload = EC_ACTIVE_FRIEND_DRAG;
    EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL = Date.now() + 450;
    if (payload.kind === 'friend') {
      moveFriendDragPayloadToGroup(payload, groupKey);
      return;
    }
    if (payload.kind === 'group' && payload.groupKey !== groupKey && reorderFriendGroup(payload.groupKey, groupKey)) {
      updateFriendsListUI(UIState.friendsListCache);
      toast('↕️ Friend group order updated', 'ok');
    }
  });
}

function createFriendListItem(friend, groupKey) {
  const p = ecGetPresenceForUsername(friend);
  const online = (p && typeof p === 'object') ? !!p.online : !!p;
  const presence = (p && typeof p === 'object') ? (p.presence || (online ? 'online' : 'offline')) : (online ? 'online' : 'offline');
  const customStatus = (p && typeof p === 'object') ? (p.custom_status || '') : '';
  const avatarUrl = (p && typeof p === 'object') ? (p.avatar_url || p.avatarUrl || '') : '';

  const li = document.createElement('li');
  li.dataset.name = friend;
  li.dataset.search = `${friend} ${presence} ${customStatus} ${groupKey === FRIEND_GROUP_DEFAULT_KEY ? FRIEND_GROUP_DEFAULT_LABEL : groupKey}`;
  li.dataset.groupKey = groupKey;
  li.classList.add('friendItem', 'friendGroupChild', 'isInteractive');
  li.classList.toggle('offline', !online);
  li.draggable = true;

  const left = document.createElement('div');
  left.className = 'liLeft';
  const dotState = online ? ((presence === 'busy') ? 'busy' : ((presence === 'away') ? 'away' : 'online')) : 'offline';
  createDockIdentity(left, {
    name: friend,
    presenceClass: dotState,
    meta: customStatus || humanPresenceText(online, presence),
    chip: '',
    avatarUrl
  });

  const showTooltip = !!UIState.prefs.friendStatusTooltip;
  if (showTooltip && customStatus) li.title = customStatus;

  const actions = document.createElement('div');
  actions.className = 'liActions';

  const dragBtn = document.createElement('button');
  dragBtn.className = 'iconBtn friendDragHandle';
  dragBtn.type = 'button';
  dragBtn.title = 'Drag friend to another category';
  dragBtn.setAttribute('aria-label', 'Drag friend to another category');
  dragBtn.textContent = '⋮⋮';
  dragBtn.draggable = false;
  dragBtn.addEventListener('mousedown', (ev) => { ev.stopPropagation(); li.dataset.dragArmed = '1'; });
  dragBtn.addEventListener('touchstart', (ev) => { ev.stopPropagation(); li.dataset.dragArmed = '1'; }, { passive: true });

  const chatBtn = document.createElement('button');
  chatBtn.className = 'iconBtn';
  chatBtn.title = 'Chat';
  chatBtn.textContent = '💬';
  chatBtn.onclick = (ev) => { ev.stopPropagation(); openPrivateChat(friend); };

  const blockBtn = document.createElement('button');
  blockBtn.className = 'iconBtn';
  blockBtn.title = 'Block';
  blockBtn.textContent = '🚫';
  blockBtn.onclick = (ev) => {
    ev.stopPropagation();
    blockUserWithPrompt(friend);
  };

  actions.appendChild(dragBtn);
  actions.appendChild(chatBtn);
  actions.appendChild(blockBtn);

  li.appendChild(left);
  li.appendChild(actions);
  li.title = li.title ? `${li.title}
Drag this row to another friend category.` : 'Drag this row to another friend category.';
  li.querySelectorAll('img').forEach((img) => { img.draggable = false; });
  bindFriendPointerDrag(li, friend, groupKey);
  li.onclick = (ev) => {
    if (Date.now() < EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL) {
      try { ev.preventDefault(); ev.stopPropagation(); } catch {}
      return;
    }
    selectBuddyRow(friend, 'friends', li);
    openPrivateChat(friend);
  };
  li.ondblclick = (ev) => {
    if (Date.now() < EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL) {
      try { ev.preventDefault(); ev.stopPropagation(); } catch {}
      return;
    }
    openPrivateChat(friend);
  };
  li.oncontextmenu = (ev) => {
    selectBuddyRow(friend, 'friends', li);
    showUserContextMenu(ev, friend, { source: 'friends' });
  };

  li.addEventListener('dragstart', (ev) => {
    if (EC_FRIEND_POINTER_DRAG && EC_FRIEND_POINTER_DRAG.sourceEl === li && !li.dataset.dragArmed) {
      ev.preventDefault();
      return;
    }
    const actionTarget = ev.target?.closest?.('button, a, input, textarea, select, .liActions');
    if (actionTarget && li.dataset.dragArmed !== '1') {
      ev.preventDefault();
      return;
    }
    EC_ACTIVE_FRIEND_DRAG = { kind: 'friend', friend, sourceGroupKey: groupKey };
    li.classList.add('dragging');
    if (ev.dataTransfer) {
      ev.dataTransfer.effectAllowed = 'move';
      try { ev.dataTransfer.setData('application/x-hui-friend', JSON.stringify(EC_ACTIVE_FRIEND_DRAG)); } catch {}
      try { ev.dataTransfer.setData('text/plain', friend); } catch {}
    }
  });

  li.addEventListener('dragend', () => {
    delete li.dataset.dragArmed;
    li.classList.remove('dragging');
    EC_ACTIVE_FRIEND_DRAG = null;
    EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL = Date.now() + 300;
    clearFriendGroupDragOver();
  });

  bindFriendGroupDropTarget(li, groupKey);
  return li;
}

function createFriendGroupHeader(meta) {
  const state = getFriendGroupingState();
  const header = document.createElement('li');
  header.className = 'friendGroupHeader';
  header.dataset.name = `${meta.name} group`;
  header.dataset.search = `${meta.name} ${(meta.members || []).join(' ')}`;
  header.dataset.groupKey = meta.key;
  header.draggable = !meta.isDefault;
  header.classList.toggle('isCollapsed', !!state.collapsed[meta.key]);

  const left = document.createElement('div');
  left.className = 'friendGroupHeadMain';

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'friendGroupToggle';
  toggle.title = state.collapsed[meta.key] ? 'Expand group' : 'Collapse group';
  toggle.setAttribute('aria-label', toggle.title);
  toggle.textContent = state.collapsed[meta.key] ? '▸' : '▾';

  const title = document.createElement('span');
  title.className = 'friendGroupTitle';
  title.textContent = meta.name;

  const count = document.createElement('span');
  count.className = 'friendGroupCount';
  count.textContent = String((meta.members || []).length);

  left.appendChild(toggle);
  left.appendChild(title);
  left.appendChild(count);

  const actions = document.createElement('div');
  actions.className = 'friendGroupHeadActions';

  if (!meta.isDefault) {
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'dockDragHandle friendGroupDragHandle';
    handle.title = 'Drag to reorder group';
    handle.setAttribute('aria-label', 'Drag to reorder group');
    handle.textContent = '⋮⋮';
    handle.draggable = false;
    handle.addEventListener('mousedown', (ev) => { ev.stopPropagation(); header.dataset.dragArmed = '1'; });
    handle.addEventListener('touchstart', (ev) => { ev.stopPropagation(); header.dataset.dragArmed = '1'; }, { passive: true });
    actions.appendChild(handle);
  }

  header.appendChild(left);
  header.appendChild(actions);

  header.addEventListener('click', (ev) => {
    if (Date.now() < EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL) {
      try { ev.preventDefault(); ev.stopPropagation(); } catch (_) {}
      return;
    }
    if (ev.target?.closest?.('.friendGroupDragHandle')) return;
    const nowCollapsed = !getFriendGroupingState().collapsed[meta.key];
    setFriendCollapsed(meta.key, nowCollapsed);
    applyDockSearchFilter($('dockSearch')?.value || '');
  });

  header.oncontextmenu = (ev) => showFriendGroupContextMenu(ev, { groupKey: meta.key, groupName: meta.name, isDefault: !!meta.isDefault });

  header.addEventListener('dragstart', (ev) => {
    if (meta.isDefault || header.dataset.dragArmed !== '1') {
      ev.preventDefault();
      return;
    }
    EC_ACTIVE_FRIEND_DRAG = { kind: 'group', groupKey: meta.key };
    header.classList.add('dragging');
    if (ev.dataTransfer) {
      ev.dataTransfer.effectAllowed = 'move';
      try { ev.dataTransfer.setData('text/plain', meta.key); } catch {}
    }
  });

  header.addEventListener('dragend', () => {
    delete header.dataset.dragArmed;
    header.classList.remove('dragging');
    EC_ACTIVE_FRIEND_DRAG = null;
    clearFriendGroupDragOver();
  });

  bindFriendGroupDropTarget(header, meta.key);
  return header;
}

function createFriendGroupEmptyState(meta) {
  const li = document.createElement('li');
  li.className = 'friendGroupEmpty friendGroupChild';
  li.dataset.name = 'empty';
  li.dataset.search = `${meta.name} drop friend here`;
  li.dataset.groupKey = meta.key;
  li.appendChild(ecCreateEl('div', { className: 'liLeft' }, [ecCreateEl('span', { className: 'liName muted', text: 'Drop a friend here' })]));
  bindFriendGroupDropTarget(li, meta.key);
  return li;
}

function applyFriendGroupListFilter(ul, q) {
  const state = getFriendGroupingState();
  const headers = [...ul.querySelectorAll('.friendGroupHeader')];
  let visibleFriends = 0;

  headers.forEach((header) => {
    const groupKey = String(header.dataset.groupKey || '');
    const collapsed = !!state.collapsed[groupKey];
    const headerHay = `${header.dataset?.name || ''} ${header.dataset?.search || ''} ${header.textContent || ''}`.toLowerCase();
    const children = [...ul.querySelectorAll('.friendGroupChild')].filter((child) => String(child.dataset.groupKey || '') === groupKey);
    let hasVisibleChild = false;

    children.forEach((li) => {
      const placeholder = isDockPlaceholderItem(li);
      const hay = `${li.dataset?.name || ''} ${li.dataset?.search || ''} ${li.textContent || ''}`.toLowerCase();
      let show = true;
      if (q) show = !placeholder && hay.includes(q);
      else if (collapsed) show = false;
      li.style.display = show ? '' : 'none';
      if (show) {
        hasVisibleChild = true;
        if (!placeholder) visibleFriends += 1;
      }
    });

    const showHeader = q ? (headerHay.includes(q) || hasVisibleChild) : true;
    header.style.display = showHeader ? '' : 'none';
    header.classList.toggle('isCollapsed', !q && collapsed);
    const toggle = header.querySelector('.friendGroupToggle');
    if (toggle) {
      const nextCollapsed = !q && collapsed;
      toggle.textContent = nextCollapsed ? '▸' : '▾';
      toggle.title = nextCollapsed ? 'Expand group' : 'Collapse group';
      toggle.setAttribute('aria-label', toggle.title);
    }
  });

  return visibleFriends;
}

function ensureFriendGroupContextMenu() {
  if (EC_FRIEND_GROUP_CTX_MENU) return EC_FRIEND_GROUP_CTX_MENU;
  const menu = ecCreateEl('div', { id: 'ecFriendGroupCtxMenu', className: 'ecCtxMenu hidden' });
  menu.appendChild(ecCtxHeader('Friend groups', 'ecFriendGroupCtxLabel'));
  menu.appendChild(ecCtxItem('createGroup', '📁', 'Create friend group'));
  menu.appendChild(ecCtxItem('renameGroup', '✏️', 'Rename group'));
  menu.appendChild(ecCtxItem('toggleGroup', '▾', 'Collapse group'));
  menu.appendChild(ecCtxItem('deleteGroup', '🗑️', 'Delete group', 'danger'));

  menu.addEventListener('contextmenu', (e) => { try { e.preventDefault(); } catch {} });
  menu.addEventListener('click', async (e) => {
    const item = e.target?.closest?.('.ecCtxItem');
    if (!item) return;
    const action = String(item.dataset.action || '');
    const groupKey = String(menu.dataset.groupKey || '');
    const groupName = String(menu.dataset.groupName || '');
    const isDefault = menu.dataset.isDefault === '1';
    hideFriendGroupContextMenu();
    if (action === 'createGroup') {
      const created = await promptForFriendGroup('');
      if (!created) return;
      const actual = ensureFriendGroup(created);
      updateFriendsListUI(UIState.friendsListCache);
      toast(`📁 Created friend group ${actual}`, 'ok');
      return;
    }
    if (action === 'renameGroup') {
      if (isDefault) return;
      const next = await promptForFriendGroup(groupName);
      if (!next) return;
      if (!renameFriendGroup(groupName, next)) {
        toast('⚠️ Could not rename that friend group', 'warn');
        return;
      }
      updateFriendsListUI(UIState.friendsListCache);
      toast(`✏️ Renamed ${groupName} to ${next}`, 'ok');
      return;
    }
    if (action === 'toggleGroup') {
      if (!groupKey) return;
      const collapsed = !getFriendGroupingState().collapsed[groupKey];
      setFriendCollapsed(groupKey, collapsed);
      applyDockSearchFilter($('dockSearch')?.value || '');
      return;
    }
    if (action === 'deleteGroup') {
      if (isDefault) return;
      ecConfirm(`Delete friend group ${groupName}? Friends in it will move back to ${FRIEND_GROUP_DEFAULT_LABEL}.`, {
        title: 'Delete friend group',
        confirmLabel: 'Delete group',
        danger: true,
        focusCancel: true,
      }).then((ok) => {
        if (!ok) return;
        deleteFriendGroup(groupName);
        updateFriendsListUI(UIState.friendsListCache);
        toast(`🗑️ Deleted friend group ${groupName}`, 'ok');
      });
    }
  });

  document.addEventListener('mousedown', (e) => {
    if (!EC_FRIEND_GROUP_CTX_MENU || EC_FRIEND_GROUP_CTX_MENU.classList.contains('hidden')) return;
    if (EC_FRIEND_GROUP_CTX_MENU.contains(e.target)) return;
    hideFriendGroupContextMenu();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideFriendGroupContextMenu(); });
  window.addEventListener('blur', () => hideFriendGroupContextMenu());
  window.addEventListener('resize', () => hideFriendGroupContextMenu());
  document.addEventListener('scroll', () => hideFriendGroupContextMenu(), true);

  (document.body || document.documentElement).appendChild(menu);
  EC_FRIEND_GROUP_CTX_MENU = menu;
  return menu;
}

function hideFriendGroupContextMenu() {
  if (!EC_FRIEND_GROUP_CTX_MENU) return;
  EC_FRIEND_GROUP_CTX_MENU.classList.add('hidden');
  EC_FRIEND_GROUP_CTX_MENU.dataset.groupKey = '';
  EC_FRIEND_GROUP_CTX_MENU.dataset.groupName = '';
  EC_FRIEND_GROUP_CTX_MENU.dataset.isDefault = '';
}

function showFriendGroupContextMenu(ev, opts = {}) {
  const menu = ensureFriendGroupContextMenu();
  const groupKey = String(opts.groupKey || '');
  const groupName = String(opts.groupName || FRIEND_GROUP_DEFAULT_LABEL);
  const isDefault = !!opts.isDefault;

  const rename = menu.querySelector('[data-action="renameGroup"]');
  const toggle = menu.querySelector('[data-action="toggleGroup"]');
  const del = menu.querySelector('[data-action="deleteGroup"]');
  if (rename) rename.style.display = groupKey ? (isDefault ? 'none' : '') : 'none';
  if (toggle) {
    toggle.style.display = groupKey ? '' : 'none';
    const span = toggle.querySelector('span');
    const collapsed = !!getFriendGroupingState().collapsed[groupKey];
    if (span) span.textContent = collapsed ? 'Expand group' : 'Collapse group';
  }
  if (del) del.style.display = groupKey ? (isDefault ? 'none' : '') : 'none';

  menu.dataset.groupKey = groupKey;
  menu.dataset.groupName = groupName;
  menu.dataset.isDefault = isDefault ? '1' : '0';
  const head = menu.querySelector('#ecFriendGroupCtxLabel');
  if (head) head.textContent = groupKey ? groupName : 'Friend groups';

  try { ev.preventDefault(); ev.stopPropagation(); } catch {}
  menu.classList.remove('hidden');

  const pad = 8;
  const rect = menu.getBoundingClientRect();
  let left = Number(ev.clientX || 0);
  let top = Number(ev.clientY || 0);
  if (left + rect.width + pad > window.innerWidth) left = window.innerWidth - rect.width - pad;
  if (top + rect.height + pad > window.innerHeight) top = window.innerHeight - rect.height - pad;
  left = Math.max(pad, left);
  top = Math.max(pad, top);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

function bindFriendsSectionContextMenu() {
  const section = $('friendsSectionList');
  const ul = $('friendsList');
  if (!section || !ul || section.dataset.friendCtxBound === '1') return;
  section.dataset.friendCtxBound = '1';
  section.addEventListener('contextmenu', (ev) => {
    if (ev.target?.closest?.('.friendItem')) return;
    if (ev.target?.closest?.('.friendGroupHeader')) return;
    showFriendGroupContextMenu(ev, {});
  });
}

function updateFriendsListUI(friends) {
  const ul = $('friendsList');
  if (!ul) return;
  ul.replaceChildren();
  ul.dataset.friendGroups = '1';

  const friendNames = ecCanonicalUsernameList(Array.isArray(friends) ? friends : [], { excludeSelf: true, excludeBlocked: true });
  UIState.friendsListCache = friendNames.slice();
  try { refreshDockPmSuggestions(); } catch {}

  try {
    UIState.friendSet = new Set(friendNames);
  } catch {
    UIState.friendSet = new Set();
  }

  if (!friendNames.length) {
    UIState.friendSet = new Set();
    ul.appendChild(ecListStatusItem({ name: 'empty', dot: 'offline', avatar: '+', text: 'No friends yet' }));
    renderMissedPmList(UIState.missedPmSummary);
    updateDockSummaryCounts();
    try { if (rbHasUI()) rbRenderRoomLists(); } catch {}
    return;
  }

  getFriendGroupMeta(friendNames).forEach((meta) => {
    ul.appendChild(createFriendGroupHeader(meta));
    if (meta.members.length) meta.members.forEach((friend) => ul.appendChild(createFriendListItem(friend, meta.key)));
    else if (!meta.isDefault) ul.appendChild(createFriendGroupEmptyState(meta));
  });

  renderMissedPmList(UIState.missedPmSummary);
  updateDockSummaryCounts();
  try { if (rbHasUI()) rbRenderRoomLists(); } catch {}
}
