// Groups (HTTP endpoints + Socket.IO group room join)
// ───────────────────────────────────────────────────────────────────────────────
let _myGroupsRefreshTicket = 0;
let _groupInvitesRefreshTicket = 0;
let _groupCreatePending = false;
let _groupJoinPending = false;
const _groupInviteActionPending = new Set();

async function apiJson(url, opts = {}) {
  const resp = await fetchWithAuth(url, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = (typeof ecReadApiJson === "function") ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
  if (!resp || !resp.ok) {
    const msg = (typeof ecApiErrorMessage === "function") ? ecApiErrorMessage(resp, data, "Request failed") : (data?.error || data?.msg || "Request failed");
    throw new Error(msg);
  }
  return data;
}

function setMiniActionBusy(btn, busy, idleLabel) {
  if (!btn) return;
  if (busy) {
    if (!btn.dataset.idleLabel) btn.dataset.idleLabel = btn.textContent || idleLabel || '';
    btn.disabled = true;
    btn.textContent = btn.dataset.busyLabel || 'Working…';
    btn.classList.add('isBusy');
    return;
  }
  btn.disabled = false;
  btn.textContent = btn.dataset.idleLabel || idleLabel || btn.textContent || '';
  btn.classList.remove('isBusy');
}

function groupRoleCanInvite(role) {
  const rank = { member: 0, moderator: 1, admin: 2, owner: 3 };
  return (rank[String(role || 'member').toLowerCase()] || 0) >= rank.moderator;
}

function normalizeGroupNameInput(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function selectGroupDockRow(groupId, rowEl = null) {
  const gid = String(groupId || '').trim();
  document.querySelectorAll('#groupList .groupDockItem.selected').forEach((el) => el.classList.remove('selected'));
  if (!rowEl && gid) rowEl = document.querySelector(`#groupList .groupDockItem[data-group-id="${CSS.escape(gid)}"]`);
  if (rowEl) rowEl.classList.add('selected');
}

function dockGroupUnreadSet(groupId, count) {
  const gid = String(groupId || '').trim();
  const n = Math.max(0, Number(count || 0) || 0);
  if (!gid) return;
  try {
    if (!UIState.groupUnreadCounts) UIState.groupUnreadCounts = new Map();
    UIState.groupUnreadCounts.set(gid, n);
    const numeric = Number(gid);
    if (Number.isFinite(numeric) && numeric > 0) UIState.groupUnreadCounts.set(numeric, n);
  } catch {}
}

function groupRoleChip(role, unreadCount = 0) {
  const r = String(role || 'member').toLowerCase();
  if (Number(unreadCount || 0) > 0) return String(unreadCount);
  if (r === 'owner') return 'Owner';
  if (r === 'admin') return 'Admin';
  if (r === 'moderator') return 'Mod';
  return 'Group';
}


async function sendGroupInviteFromDock(groupId, groupName) {
  const u = await ecPrompt('Invite which username?', '', {
    title: `Invite to ${groupName || 'group'}`,
    inputLabel: 'Username',
    confirmLabel: 'Send invite',
    maxLength: 80,
    placeholder: 'username',
  });
  const targetUser = String(u || '').trim();
  if (!targetUser) return;
  const res = await apiJson(`/api/groups/${encodeURIComponent(groupId)}/invite`, { method: 'POST', body: JSON.stringify({ to_user: targetUser }) });
  if (res?.status === 'already_member') toast('That user is already in this group', 'info');
  else toast(`✅ Invite sent${res?.to_user ? ` to ${res.to_user}` : ''}`, 'ok');
}

async function revokeGroupInviteFromDock(groupId, groupName) {
  const u = await ecPrompt('Revoke invite for which username?', '', {
    title: `Revoke invite${groupName ? ` · ${groupName}` : ''}`,
    inputLabel: 'Username',
    confirmLabel: 'Revoke invite',
    maxLength: 80,
    placeholder: 'username',
  });
  const targetUser = String(u || '').trim();
  if (!targetUser) return;
  const res = await apiJson(`/api/groups/${encodeURIComponent(groupId)}/revoke_invite`, { method: 'POST', body: JSON.stringify({ to_user: targetUser }) });
  if (res?.status === 'revoked') toast(`Invite revoked${res?.to_user ? ` for ${res.to_user}` : ''}`, 'info');
  else toast('No pending invite for that user', 'info');
}

async function refreshMyGroups() {
  const ul = $('groupList');
  if (!ul) return;
  const requestId = ++_myGroupsRefreshTicket;

  try {
    const data = await apiJson('/api/groups/mine', { method: 'GET' });
    if (requestId !== _myGroupsRefreshTicket) return;

    const rawGroups = Array.isArray(data?.groups) ? data.groups : [];
    const seenGroupIds = new Set();
    const groups = rawGroups.filter((g) => {
      const gid = String(g?.id ?? '').trim();
      if (!gid || seenGroupIds.has(gid)) return false;
      seenGroupIds.add(gid);
      return true;
    });

    UIState.myGroups = groups.slice();
    ecClearNode(ul);

    if (groups.length === 0) {
      ul.appendChild(ecListStatusItem({ name: 'none', dot: 'offline', avatar: '#', text: 'No groups yet' }));
      updateDockSummaryCounts();
      return;
    }

    groups.forEach(g => {
      const gid = String(g.id || g.group_id || '');
      const gname = normalizeGroupNameInput(g.group_name || gid) || gid;
      const role = String(g.role || 'member').toLowerCase();
      const memberCount = Number(g.member_count || 0) || 0;
      const unreadCount = Number(g.unread_count ?? g.unread ?? 0) || 0;
      dockGroupUnreadSet(gid, unreadCount);
      const li = document.createElement('li');
      li.dataset.name = gname;
      li.dataset.search = `${gname} ${gid} group ${role} ${memberCount} member ${unreadCount} unread`;
      li.dataset.groupId = gid;
      li.classList.add('isInteractive', 'groupDockItem');
      li.classList.toggle('hasUnread', unreadCount > 0);
      li.dataset.unread = String(unreadCount);

      const left = document.createElement('div');
      left.className = 'liLeft';
      createDockIdentity(left, {
        name: gname,
        presenceClass: 'online',
        meta: `Group chat · ID #${gid}${memberCount ? ` · ${memberCount} member${memberCount === 1 ? '' : 's'}` : ''}${unreadCount ? ` · ${unreadCount} unread` : ''}`,
        chip: groupRoleChip(role, unreadCount)
      });

      const actions = document.createElement('div');
      actions.className = 'liActions';

      const openBtn = document.createElement('button');
      openBtn.className = 'iconBtn';
      openBtn.textContent = '💬';
      openBtn.title = 'Open';
      openBtn.onclick = (ev) => { ev.stopPropagation(); selectGroupDockRow(gid, li); openGroupWindow(gid, gname); };

      const inviteBtn = document.createElement('button');
      inviteBtn.className = 'iconBtn';
      inviteBtn.textContent = '➕';
      inviteBtn.title = 'Invite user';
      inviteBtn.onclick = async (ev) => {
        ev.stopPropagation();
        setMiniActionBusy(inviteBtn, true, '➕');
        try {
          await sendGroupInviteFromDock(gid, gname);
          try { await refreshGroupInvites(); } catch {}
        } catch (e) {
          toast(`❌ ${e.message}`, 'error');
        } finally {
          setMiniActionBusy(inviteBtn, false, '➕');
        }
      };

      const revokeInviteBtn = document.createElement('button');
      revokeInviteBtn.className = 'iconBtn';
      revokeInviteBtn.textContent = '↩';
      revokeInviteBtn.title = 'Revoke pending invite';
      revokeInviteBtn.onclick = async (ev) => {
        ev.stopPropagation();
        setMiniActionBusy(revokeInviteBtn, true, '↩');
        try {
          await revokeGroupInviteFromDock(gid, gname);
          try { await refreshGroupInvites(); } catch {}
        } catch (e) {
          toast(`❌ ${e.message}`, 'error');
        } finally {
          setMiniActionBusy(revokeInviteBtn, false, '↩');
        }
      };

      const leaveBtn = document.createElement('button');
      leaveBtn.className = 'iconBtn';
      leaveBtn.textContent = '🚪';
      leaveBtn.title = 'Leave group';
      leaveBtn.onclick = async (ev) => {
        ev.stopPropagation();
        const ok = await ecConfirm(`Leave group "${gname}"?`, {
          title: 'Leave group',
          confirmLabel: 'Leave group',
          danger: true,
          focusCancel: true,
        });
        if (!ok) return;
        try {
          const leaveRes = await apiJson(`/api/groups/${encodeURIComponent(g.id)}/leave`, { method: 'POST', body: JSON.stringify({}) });
          if (typeof forceLeaveGroupUI === 'function') forceLeaveGroupUI(gid, leaveRes?.status === 'deleted' ? 'deleted' : 'left');
          else {
            toast('Left group', 'info');
            await refreshMyGroups();
          }
        } catch (e) {
          toast(`❌ ${e.message}`, 'error');
        }
      };

      actions.appendChild(openBtn);
      if (groupRoleCanInvite(role)) {
        actions.appendChild(inviteBtn);
        actions.appendChild(revokeInviteBtn);
      }
      actions.appendChild(leaveBtn);

      li.appendChild(left);
      li.appendChild(actions);
      li.onclick = () => { selectGroupDockRow(gid, li); openGroupWindow(gid, gname); };
      li.ondblclick = () => { selectGroupDockRow(gid, li); openGroupWindow(gid, gname); };

      ul.appendChild(li);
    });
    updateDockSummaryCounts();
    try { applyDockSearchFilter($('dockSearch')?.value || ''); } catch {}
  } catch (e) {
    if (requestId !== _myGroupsRefreshTicket) return;
    console.error(e);
    UIState.myGroups = [];
    ecClearNode(ul);
    ul.appendChild(ecListStatusItem({ name: 'error', dot: 'busy', avatar: '!', text: 'Could not load groups' }));
    updateDockSummaryCounts();
  }
}

function roomInviteKey(inv) {
  return `${String(inv?.kind || 'room')}:${String(inv?.room || '').toLowerCase()}:${String(inv?.by || '').toLowerCase()}`;
}

function groupInviteKey(inv) {
  // The backend stores one pending invite per group + recipient. A re-invite
  // from another moderator updates the same row, so the UI key must be the
  // group id only or the Alerts tab can show duplicate invites for one group.
  return `${String(inv?.group_id || '').trim()}`;
}

function groupInvitePendingKey(groupId) {
  return `groupinvite:${String(groupId || '').trim()}`;
}

function groupInviteActionKey(inv) {
  return groupInvitePendingKey(String(inv?.group_id || '').trim());
}

function groupInviteActionIsPending(inv) {
  const key = groupInviteActionKey(inv);
  return !!(key && _groupInviteActionPending.has(key));
}

function setGroupInviteButtonsBusy(buttons, busy) {
  (Array.isArray(buttons) ? buttons : []).forEach((btn) => {
    if (!btn) return;
    btn.disabled = !!busy;
    btn.classList.toggle('isBusy', !!busy);
    btn.setAttribute('aria-busy', busy ? 'true' : 'false');
  });
}

async function runGroupInviteButtonAction(inv, buttons, actionFn) {
  if (!inv || typeof actionFn !== 'function') return;
  if (groupInviteActionIsPending(inv)) return;
  setGroupInviteButtonsBusy(buttons, true);
  try {
    await actionFn(inv);
  } finally {
    setGroupInviteButtonsBusy(buttons, groupInviteActionIsPending(inv));
  }
}

function mergeGroupInvites(invites) {
  const existing = new Map();
  (Array.isArray(UIState.groupInvites) ? UIState.groupInvites : []).forEach((inv) => {
    existing.set(groupInviteKey(inv), inv);
  });
  (Array.isArray(invites) ? invites : []).forEach((inv) => {
    const groupId = String(inv?.group_id || '').trim();
    if (!groupId) return;
    const normalized = {
      ...inv,
      group_id: Number(groupId) || groupId,
      group_name: normalizeGroupNameInput(inv?.group_name || groupId) || groupId,
      group_description: String(inv?.group_description || ''),
      from_user: String(inv?.from_user || '').replace(/\s+/g, ' ').trim(),
      created_at: String(inv?.created_at || inv?.sent_at || new Date().toISOString()),
      sent_at: String(inv?.sent_at || inv?.created_at || new Date().toISOString()),
    };
    existing.set(groupInviteKey(normalized), normalized);
  });
  UIState.groupInvites = Array.from(existing.values()).sort((a, b) => {
    const at = Date.parse(a?.sent_at || a?.created_at || '') || 0;
    const bt = Date.parse(b?.sent_at || b?.created_at || '') || 0;
    return bt - at;
  });
}

function removeGroupInviteFromState(groupId, fromUser = '') {
  const wantedId = String(groupId || '').trim();
  const wantedFrom = String(fromUser || '').trim().toLowerCase();
  UIState.groupInvites = (Array.isArray(UIState.groupInvites) ? UIState.groupInvites : []).filter((inv) => {
    const sameId = String(inv?.group_id || '').trim() === wantedId;
    if (!sameId) return true;
    if (!wantedFrom) return false;
    return String(inv?.from_user || '').trim().toLowerCase() !== wantedFrom;
  });
}

async function acceptGroupInvite(inv) {
  const groupId = String(inv?.group_id || '').trim();
  const fromUser = String(inv?.from_user || '').trim();
  if (!groupId) return;
  const pendingKey = groupInvitePendingKey(groupId);
  if (_groupInviteActionPending.has(pendingKey)) return;
  _groupInviteActionPending.add(pendingKey);
  try {
    const res = await apiJson(`/api/groups/${encodeURIComponent(groupId)}/accept`, { method: 'POST', body: JSON.stringify({}) });
    removeGroupInviteFromState(groupId, fromUser);
    renderGroupInviteListInto($('groupInviteList'), UIState.groupInvites);
    renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
    updateDockSummaryCounts();
    try { await refreshMyGroups(); } catch {}
    try {
      if (res?.group_id && typeof openGroupWindow === 'function') {
        openGroupWindow(String(res.group_id), String(res.group_name || inv?.group_name || res.group_id));
      }
    } catch {}
    toast(`✅ Joined group${res?.group_name ? ` ${res.group_name}` : ''}`, 'ok');
  } finally {
    _groupInviteActionPending.delete(pendingKey);
  }
}

async function declineGroupInvite(inv) {
  const groupId = String(inv?.group_id || '').trim();
  const fromUser = String(inv?.from_user || '').trim();
  if (!groupId) return;
  const pendingKey = groupInvitePendingKey(groupId);
  if (_groupInviteActionPending.has(pendingKey)) return;
  _groupInviteActionPending.add(pendingKey);
  try {
    await apiJson(`/api/groups/${encodeURIComponent(groupId)}/decline`, { method: 'POST', body: JSON.stringify({}) });
    removeGroupInviteFromState(groupId, fromUser);
    renderGroupInviteListInto($('groupInviteList'), UIState.groupInvites);
    renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
    updateDockSummaryCounts();
    toast('Declined', 'info');
  } finally {
    _groupInviteActionPending.delete(pendingKey);
  }
}

function showGroupInviteToast(inv) {
  const groupId = String(inv?.group_id || '').trim();
  if (!groupId) return;
  const groupName = String(inv?.group_name || groupId).trim();
  const fromUser = String(inv?.from_user || '').trim();
  const key = groupInvitePendingKey(groupId);
  if (UIState.inviteSeen?.has?.(key)) return;
  rememberInviteSeen(key);
  const label = fromUser ? `📨 Group invite: ${groupName} (from ${fromUser})` : `📨 Group invite: ${groupName}`;
  toastChoice(label, {
    kind: 'info',
    event: 'group_invite',
    timeout: 14000,
    acceptLabel: '✅',
    declineLabel: '❌',
    onAccept: async () => {
      try {
        await acceptGroupInvite(inv);
        try { forgetInviteSeen(key); } catch {}
      } catch (e) {
        toast(`❌ ${e.message || `Could not join ${groupName}`}`, 'error');
      }
    },
    onDecline: async () => {
      try {
        await declineGroupInvite(inv);
        try { forgetInviteSeen(key); } catch {}
      } catch (e) {
        toast(`❌ ${e.message || 'Could not decline invite'}`, 'error');
      }
    },
  });
  maybeBrowserNotify('Group invite', fromUser ? `${fromUser} invited you to ${groupName}` : `Invite to ${groupName}`);
}

function mergeRoomInvites(kind, invites) {
  const keep = Array.isArray(UIState.roomInvites) ? UIState.roomInvites.filter((inv) => String(inv?.kind || 'room') !== String(kind || 'room')) : [];
  const next = Array.isArray(invites) ? invites.map((inv) => ({ ...inv, kind: String(inv?.kind || kind || 'room') })) : [];
  UIState.roomInvites = keep.concat(next);
}

function removeRoomInviteFromState(room, by, kind = 'room') {
  const wantedRoom = String(room || '').trim().toLowerCase();
  const wantedBy = String(by || '').trim().toLowerCase();
  const wantedKind = String(kind || 'room');
  UIState.roomInvites = (Array.isArray(UIState.roomInvites) ? UIState.roomInvites : []).filter((inv) => {
    return !(String(inv?.kind || 'room') === wantedKind && String(inv?.room || '').trim().toLowerCase() === wantedRoom && String(inv?.by || '').trim().toLowerCase() === wantedBy);
  });
}

async function acceptRoomInvite(inv) {
  const room = String(inv?.room || '').trim();
  const by = String(inv?.by || '').trim();
  const kind = String(inv?.kind || 'room');
  if (!room) return;
  const url = kind === 'custom_private' ? '/api/custom_rooms/invites/accept' : '/api/rooms/invites/accept';

  // Private custom-room invites must be accepted before Socket.IO join.  The
  // server treats pending custom_room_invites rows as visibility-only, so a
  // guessed/direct join cannot enter until this call persists membership.
  let acceptedRoom = room;
  if (kind === 'custom_private') {
    const accepted = await apiJson(url, { method: 'POST', body: JSON.stringify({ room }) });
    acceptedRoom = String(accepted?.room || room).trim() || room;
    try {
      if (accepted?.category && accepted?.subcategory && typeof ROOM_BROWSER === 'object') {
        ROOM_BROWSER.selectedCategory = String(accepted.category);
        ROOM_BROWSER.selectedSubcategory = String(accepted.subcategory);
        ROOM_BROWSER.roomScope = 'custom';
        if (typeof rbRenderCategoryTree === 'function') rbRenderCategoryTree();
      }
    } catch {}
  }

  const res = await joinRoom(acceptedRoom);
  if (!res || !res.success) {
    toast(`❌ Could not join ${acceptedRoom}`, 'error');
    return;
  }

  if (kind !== 'custom_private') {
    await apiJson(url, { method: 'POST', body: JSON.stringify({ room: acceptedRoom }) });
  }
  removeRoomInviteFromState(acceptedRoom, by, kind);
  if (acceptedRoom.toLowerCase() !== room.toLowerCase()) removeRoomInviteFromState(room, by, kind);
  try { forgetInviteSeen(_inviteKey(acceptedRoom, by, kind)); } catch {}
  try { forgetInviteSeen(_inviteKey(room, by, kind)); } catch {}
  renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
  updateDockSummaryCounts();
  try { if (typeof rbScheduleInviteBrowserRefresh === 'function') rbScheduleInviteBrowserRefresh('accepted_room_invite'); } catch {}
  toast(`✅ Joined ${acceptedRoom}`, 'ok');
}

async function declineRoomInvite(inv) {
  const room = String(inv?.room || '').trim();
  const by = String(inv?.by || '').trim();
  const kind = String(inv?.kind || 'room');
  if (!room) return;
  const url = kind === 'custom_private' ? '/api/custom_rooms/invites/decline' : '/api/rooms/invites/decline';
  await apiJson(url, { method: 'POST', body: JSON.stringify({ room }) });
  removeRoomInviteFromState(room, by, kind);
  try { forgetInviteSeen(_inviteKey(room, by, kind)); } catch {}
  renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
  updateDockSummaryCounts();
  try { if (typeof rbScheduleInviteBrowserRefresh === 'function') rbScheduleInviteBrowserRefresh('declined_room_invite'); } catch {}
  toast('Invite declined', 'info');
}

// Custom private-room invites (room browser feature)
async function refreshCustomRoomInvites() {
  try {
    const data = await apiJson("/api/custom_rooms/invites", { method: "GET" });
    const invites = (Array.isArray(data?.invites) ? data.invites : []).map((inv) => ({ ...inv, kind: 'custom_private' }));
    mergeRoomInvites('custom_private', invites);
    invites.forEach((inv) => {
      try { showRoomInviteToast(inv?.room, inv?.by, { kind: inv?.kind }); } catch {}
    });
    renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
    updateDockSummaryCounts();
    try { if (typeof rbScheduleInviteBrowserRefresh === 'function') rbScheduleInviteBrowserRefresh('custom_invites_poll'); } catch {}
  } catch (e) {
    // ignore
  }
}

// Generic room invites (official/public rooms)
async function refreshRoomInvites() {
  try {
    const data = await apiJson("/api/rooms/invites", { method: "GET" });
    const invites = (Array.isArray(data?.invites) ? data.invites : []).map((inv) => ({ ...inv, kind: 'room' }));
    mergeRoomInvites('room', invites);
    invites.forEach((inv) => {
      try { showRoomInviteToast(inv?.room, inv?.by, { kind: inv?.kind }); } catch {}
    });
    renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
    updateDockSummaryCounts();
    try { applyDockSearchFilter($('dockSearch')?.value || ''); } catch {}
  } catch (e) {
    // ignore
  }
}


function mergeProfilePostNotifications(notifications) {
  const incoming = Array.isArray(notifications) ? notifications : [];
  const byId = new Map();
  const keep = Array.isArray(UIState.profilePostNotifications) ? UIState.profilePostNotifications : [];
  keep.forEach((n) => {
    const id = Number(n?.id || 0) || 0;
    if (id > 0 && !n?.is_read) byId.set(id, n);
  });
  incoming.forEach((n) => {
    const id = Number(n?.id || 0) || 0;
    if (id > 0 && !n?.is_read) byId.set(id, n);
  });
  UIState.profilePostNotifications = Array.from(byId.values()).sort((a, b) => (Date.parse(b?.created_at || '') || 0) - (Date.parse(a?.created_at || '') || 0));
}

async function refreshProfilePostNotifications() {
  try {
    const data = await apiJson('/api/profile/notifications?unread_only=1&limit=25', { method: 'GET' });
    mergeProfilePostNotifications(Array.isArray(data?.notifications) ? data.notifications : []);
    renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
    updateDockSummaryCounts();
    try { applyDockSearchFilter($('dockSearch')?.value || ''); } catch {}
  } catch (e) {
    // ignore: profile alerts should not block group/room invite rendering
  }
}

async function markProfilePostNotificationRead(notificationId) {
  const id = Number(notificationId || 0) || 0;
  if (id <= 0) return;
  await apiJson('/api/profile/notifications/read', {
    method: 'POST',
    body: JSON.stringify({ ids: [id] }),
  });
  UIState.profilePostNotifications = (Array.isArray(UIState.profilePostNotifications) ? UIState.profilePostNotifications : []).filter((n) => Number(n?.id || 0) !== id);
  renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
  updateDockSummaryCounts();
}

async function markAllProfilePostNotificationsRead() {
  await apiJson('/api/profile/notifications/read', {
    method: 'POST',
    body: JSON.stringify({ all: true }),
  });
  UIState.profilePostNotifications = [];
  renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
  updateDockSummaryCounts();
}

function attachProfilePostNotificationSocket() {
  try {
    if (!socket || window.__ecProfilePostNotificationSocketBound) return;
    window.__ecProfilePostNotificationSocketBound = true;
    socket.on('profile_post_notification', (payload) => {
      mergeProfilePostNotifications([payload]);
      renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
      updateDockSummaryCounts();
      const msg = String(payload?.message || 'New profile post notification');
      try { toast(msg, 'info'); } catch {}
    });
  } catch {}
}

function renderAlertsInviteListInto(ul, groupInvites, roomInvites, opts = {}) {
  if (!ul) return;
  ecClearNode(ul);

  const roomList = Array.isArray(roomInvites) ? roomInvites.slice() : [];
  const groupList = Array.isArray(groupInvites) ? groupInvites.slice() : [];
  const webcamList = Array.isArray(UIState.webcamRequests) ? UIState.webcamRequests.slice() : [];
  const profileList = Array.isArray(UIState.profilePostNotifications) ? UIState.profilePostNotifications.slice() : [];
  const merged = [];
  roomList.forEach((inv) => merged.push({ entryType: 'room', sortTs: Date.parse(inv?.created_at || '') || 0, payload: inv }));
  groupList.forEach((inv) => merged.push({ entryType: 'group', sortTs: Date.parse(inv?.sent_at || inv?.created_at || '') || 0, payload: inv }));
  webcamList.forEach((req) => merged.push({ entryType: 'webcam', sortTs: Date.parse(req?.requested_at || req?.created_at || '') || 0, payload: req }));
  profileList.forEach((n) => merged.push({ entryType: 'profile', sortTs: Date.parse(n?.created_at || '') || 0, payload: n }));
  merged.sort((a, b) => Number(b.sortTs || 0) - Number(a.sortTs || 0));

  if (!merged.length) {
    ul.appendChild(ecListStatusItem({ name: 'none', dot: 'offline', avatar: '!', text: 'No important notifications' }));
    return;
  }

  if (profileList.length > 1) {
    const bulkLi = document.createElement('li');
    bulkLi.className = 'groupDockItem profilePostNotificationBulkItem';
    const left = document.createElement('div');
    left.className = 'liLeft';
    createDockIdentity(left, {
      name: 'Profile notifications',
      presenceClass: 'away',
      meta: `${profileList.length} unread profile notification${profileList.length === 1 ? '' : 's'}`,
      chip: 'Profile alerts'
    });
    const actions = document.createElement('div');
    actions.className = 'liActions';
    const clearBtn = document.createElement('button');
    clearBtn.className = 'miniBtn secondary';
    clearBtn.type = 'button';
    clearBtn.textContent = 'Mark all read';
    clearBtn.onclick = async (ev) => {
      ev.stopPropagation();
      clearBtn.disabled = true;
      try { await markAllProfilePostNotificationsRead(); }
      catch (e) { try { toast(`❌ ${e.message || 'Could not mark all read'}`, 'error'); } catch {} }
      finally { clearBtn.disabled = false; }
    };
    actions.appendChild(clearBtn);
    bulkLi.append(left, actions);
    ul.appendChild(bulkLi);
  }

  merged.forEach((entry) => {
    if (entry.entryType === 'profile') {
      const note = entry.payload || {};
      const actor = String(note.actor || 'Someone');
      const msg = String(note.message || 'Profile post activity');
      const li = document.createElement('li');
      li.dataset.name = actor;
      li.dataset.search = `${actor} profile post like comment notification`;
      li.classList.add('isInteractive', 'groupDockItem');
      li.classList.add('profilePostNotificationItem');

      const left = document.createElement('div');
      left.className = 'liLeft';
      createDockIdentity(left, {
        name: msg,
        presenceClass: 'away',
        meta: `${actor} · Profile post`,
        chip: String(note.type || '').includes('comment') ? 'Profile comment' : 'Profile like'
      });

      const actions = document.createElement('div');
      actions.className = 'liActions';
      const openBtn = document.createElement('button');
      openBtn.className = 'iconBtn';
      openBtn.textContent = '👁';
      openBtn.title = 'Open your profile posts';
      openBtn.onclick = async (ev) => {
        ev.stopPropagation();
        try {
          if (typeof openProfileWindow === 'function') openProfileWindow(currentUser, { fitMode: 'public' });
          await markProfilePostNotificationRead(note.id);
        } catch (e) {
          try { toast(`❌ ${e.message || 'Could not open notification'}`, 'error'); } catch {}
        }
      };
      const doneBtn = document.createElement('button');
      doneBtn.className = 'iconBtn';
      doneBtn.textContent = '✓';
      doneBtn.title = 'Mark read';
      doneBtn.onclick = async (ev) => {
        ev.stopPropagation();
        try { await markProfilePostNotificationRead(note.id); }
        catch (e) { try { toast(`❌ ${e.message || 'Could not mark read'}`, 'error'); } catch {} }
      };
      actions.append(openBtn, doneBtn);
      li.append(left, actions);
      li.onclick = () => { if (opts.openRail) openDockRailPanel('alerts'); };
      ul.appendChild(li);
      return;
    }

    if (entry.entryType === 'webcam') {
      const req = entry.payload || {};
      const viewer = String(req.viewer || 'Someone');
      const room = String(req.room || 'current room');
      const li = document.createElement('li');
      li.dataset.name = viewer;
      li.dataset.search = `${viewer} ${room} webcam camera request`;
      li.classList.add('isInteractive', 'groupDockItem');
      li.classList.add('webcamRequestItem');

      const left = document.createElement('div');
      left.className = 'liLeft';
      createDockIdentity(left, {
        name: viewer,
        presenceClass: 'away',
        meta: `Wants to view your webcam · ${room}`,
        chip: 'Webcam request'
      });

      const actions = document.createElement('div');
      actions.className = 'liActions';

      const allowBtn = document.createElement('button');
      allowBtn.className = 'iconBtn';
      allowBtn.textContent = '✅';
      allowBtn.title = 'Allow webcam view';
      allowBtn.onclick = async (ev) => {
        ev.stopPropagation();
        try {
          if (typeof huiRespondToCamViewRequest === 'function') await huiRespondToCamViewRequest(room, viewer, true);
          else toast('❌ Webcam response handler is not ready', 'error');
        } catch (e) {
          toast(`❌ ${e.message || 'Could not allow webcam request'}`, 'error');
        }
      };

      const denyBtn = document.createElement('button');
      denyBtn.className = 'iconBtn';
      denyBtn.textContent = '❌';
      denyBtn.title = 'Deny webcam view';
      denyBtn.onclick = async (ev) => {
        ev.stopPropagation();
        try {
          if (typeof huiRespondToCamViewRequest === 'function') await huiRespondToCamViewRequest(room, viewer, false);
          else toast('❌ Webcam response handler is not ready', 'error');
        } catch (e) {
          toast(`❌ ${e.message || 'Could not deny webcam request'}`, 'error');
        }
      };

      actions.appendChild(allowBtn);
      actions.appendChild(denyBtn);
      li.appendChild(left);
      li.appendChild(actions);
      li.onclick = () => { if (opts.openRail) openDockRailPanel('alerts'); };
      ul.appendChild(li);
      return;
    }

    if (entry.entryType === 'group') {
      const inv = entry.payload;
      const label = String(inv.group_name || inv.group_id);
      const li = document.createElement('li');
      li.dataset.name = `${label}`;
      li.dataset.search = `${label} ${inv.group_id} ${inv.from_user} invite`;
      li.classList.add('isInteractive', 'groupDockItem');

      const left = document.createElement('div');
      left.className = 'liLeft';
      createDockIdentity(left, {
        name: label,
        presenceClass: 'away',
        meta: `Invited by ${inv.from_user} · Group #${inv.group_id}`,
        chip: 'Group invite'
      });

      const actions = document.createElement('div');
      actions.className = 'liActions';

      const acceptBtn = document.createElement('button');
      acceptBtn.className = 'iconBtn';
      acceptBtn.textContent = '✅';
      acceptBtn.title = 'Accept';
      acceptBtn.onclick = async (ev) => {
        ev.stopPropagation();
        try {
          await runGroupInviteButtonAction(inv, [acceptBtn, declineBtn], acceptGroupInvite);
        } catch (e) {
          toast(`❌ ${e.message}`, 'error');
        }
      };

      const declineBtn = document.createElement('button');
      declineBtn.className = 'iconBtn';
      declineBtn.textContent = '❌';
      declineBtn.title = 'Decline';
      declineBtn.onclick = async (ev) => {
        ev.stopPropagation();
        try {
          await runGroupInviteButtonAction(inv, [acceptBtn, declineBtn], declineGroupInvite);
        } catch (e) {
          toast(`❌ ${e.message}`, 'error');
        }
      };

      setGroupInviteButtonsBusy([acceptBtn, declineBtn], groupInviteActionIsPending(inv));
      actions.appendChild(acceptBtn);
      actions.appendChild(declineBtn);
      li.appendChild(left);
      li.appendChild(actions);
      li.onclick = () => { if (opts.openRail) openDockRailPanel('alerts'); else setActiveTab('groups'); };
      ul.appendChild(li);
      return;
    }

    const inv = entry.payload;
    const label = String(inv.room || 'Room');
    const inviter = String(inv.by || 'Someone');
    const kind = String(inv.kind || 'room');
    const li = document.createElement('li');
    li.dataset.name = label;
    li.dataset.search = `${label} ${inviter} room invite`;
    li.classList.add('isInteractive', 'groupDockItem');

    const left = document.createElement('div');
    left.className = 'liLeft';
    createDockIdentity(left, {
      name: label,
      presenceClass: 'away',
      meta: kind === 'custom_private' ? `Private room invite · From ${inviter}` : `Room invite · From ${inviter}`,
      chip: kind === 'custom_private' ? 'Private invite' : 'Room invite'
    });

    const actions = document.createElement('div');
    actions.className = 'liActions';

    const acceptBtn = document.createElement('button');
    acceptBtn.className = 'iconBtn';
    acceptBtn.textContent = '✅';
    acceptBtn.title = 'Join';
    acceptBtn.onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await acceptRoomInvite(inv);
      } catch (e) {
        toast(`❌ ${e.message}`, 'error');
      }
    };

    const declineBtn = document.createElement('button');
    declineBtn.className = 'iconBtn';
    declineBtn.textContent = '❌';
    declineBtn.title = 'No';
    declineBtn.onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await declineRoomInvite(inv);
      } catch (e) {
        toast(`❌ ${e.message}`, 'error');
      }
    };

    actions.appendChild(acceptBtn);
    actions.appendChild(declineBtn);
    li.appendChild(left);
    li.appendChild(actions);
    li.onclick = () => { if (opts.openRail) openDockRailPanel('alerts'); };
    ul.appendChild(li);
  });
}

function renderGroupInviteListInto(ul, invites, opts = {}) {
  if (!ul) return;
  ecClearNode(ul);

  if (!invites.length) {
    ul.appendChild(ecListStatusItem({ name: 'none', dot: 'offline', avatar: '#', text: 'No invites' }));
    return;
  }

  invites.forEach(inv => {
    const label = String(inv.group_name || inv.group_id);
    const li = document.createElement('li');
    li.dataset.name = `${label}`;
    li.dataset.search = `${label} ${inv.group_id} ${inv.from_user} invite`;
    li.classList.add('isInteractive', 'groupDockItem');

    const left = document.createElement('div');
    left.className = 'liLeft';
    createDockIdentity(left, {
      name: label,
      presenceClass: 'away',
      meta: `Invited by ${inv.from_user} · Group #${inv.group_id}`,
      chip: 'Invite'
    });

    const actions = document.createElement('div');
    actions.className = 'liActions';

    const acceptBtn = document.createElement('button');
    acceptBtn.className = 'iconBtn';
    acceptBtn.textContent = '✅';
    acceptBtn.title = 'Accept';
    acceptBtn.onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await runGroupInviteButtonAction(inv, [acceptBtn, declineBtn], acceptGroupInvite);
      } catch (e) {
        toast(`❌ ${e.message}`, 'error');
      }
    };

    const declineBtn = document.createElement('button');
    declineBtn.className = 'iconBtn';
    declineBtn.textContent = '❌';
    declineBtn.title = 'Decline';
    declineBtn.onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await runGroupInviteButtonAction(inv, [acceptBtn, declineBtn], declineGroupInvite);
      } catch (e) {
        toast(`❌ ${e.message}`, 'error');
      }
    };

    setGroupInviteButtonsBusy([acceptBtn, declineBtn], groupInviteActionIsPending(inv));
    actions.appendChild(acceptBtn);
    actions.appendChild(declineBtn);

    li.appendChild(left);
    li.appendChild(actions);
    li.onclick = () => {
      if (opts.openRail) openDockRailPanel('alerts');
      else setActiveTab('groups');
    };
    ul.appendChild(li);
  });
}

async function refreshGroupInvites() {
  const requestId = ++_groupInvitesRefreshTicket;
  try {
    const data = await apiJson('/api/groups/invites', { method: 'GET' });
    if (requestId !== _groupInvitesRefreshTicket) return;
    const invites = Array.isArray(data?.invites) ? data.invites : [];
    mergeGroupInvites(invites);
    invites.forEach((inv) => {
      try { showGroupInviteToast(inv); } catch {}
    });
    renderGroupInviteListInto($('groupInviteList'), UIState.groupInvites);
    renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true });
    updateDockSummaryCounts();
    try { applyDockSearchFilter($('dockSearch')?.value || ''); } catch {}
  } catch (e) {
    if (requestId !== _groupInvitesRefreshTicket) return;
    // Do not clear already-known pending invites on a transient auth/network
    // failure. Otherwise the Alerts tab can briefly hide actionable invites
    // and then re-add them on the next successful refresh.
    if (!Array.isArray(UIState.groupInvites) || UIState.groupInvites.length === 0) {
      const listEls = [$('groupInviteList')].filter(Boolean);
      listEls.forEach((ul) => {
        ecClearNode(ul);
        ul.appendChild(ecListStatusItem({ name: 'error', dot: 'busy', avatar: '!', text: 'Could not load invites' }));
      });
    }
    toast(`❌ ${e.message}`, 'error');
    updateDockSummaryCounts();
  }
}

attachProfilePostNotificationSocket();
setTimeout(() => { try { refreshProfilePostNotifications(); } catch {} }, 1600);

async function createGroup() {
  const input = $("groupCreateName");
  const btn = $("btnCreateGroup");
  const name = normalizeGroupNameInput(input?.value);
  const desc = normalizeGroupNameInput($("groupCreateDescription")?.value);
  if (!name) return toast("⚠️ Enter a group name", "warn");
  if (name.length > 64) return toast("⚠️ Group name max is 64 characters", "warn");
  if (_groupCreatePending) return;

  _groupCreatePending = true;
  if (btn) btn.dataset.busyLabel = 'Creating…';
  setMiniActionBusy(btn, true, 'Create');

  try {
    const res = await apiJson("/api/groups", { method: "POST", body: JSON.stringify({ name, description: desc }) });
    toast(`✅ Group created${res?.group_name ? `: ${res.group_name}` : ` (#${res.group_id})`}`, "ok");
    if (input) input.value = "";
    const descInput = $("groupCreateDescription");
    if (descInput) descInput.value = "";
    await refreshMyGroups();
    if (res?.group_id) {
      try { openGroupWindow(String(res.group_id), String(res.group_name || name)); } catch {}
    }
  } catch (e) {
    toast(`❌ ${e.message}`, "error");
  } finally {
    _groupCreatePending = false;
    setMiniActionBusy(btn, false, 'Create');
  }
}

async function joinGroupById() {
  const input = $("groupJoinId");
  const btn = $("btnJoinGroup");
  const id = input?.value.trim();
  if (!id) return toast("⚠️ Enter invite group ID", "warn");
  if (_groupJoinPending) return;

  _groupJoinPending = true;
  if (btn) btn.dataset.busyLabel = 'Joining…';
  setMiniActionBusy(btn, true, 'Join');

  try {
    await apiJson(`/api/groups/${encodeURIComponent(id)}/join`, { method: "POST", body: JSON.stringify({}) });
    toast(`✅ Joined group #${id}`, "ok");
    if (input) input.value = "";
    await refreshMyGroups();
  } catch (e) {
    toast(`❌ ${e.message}`, "error");
  } finally {
    _groupJoinPending = false;
    setMiniActionBusy(btn, false, 'Join');
  }
}
