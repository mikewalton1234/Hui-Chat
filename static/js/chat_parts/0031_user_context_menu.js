function bindDockMenus() {
  document.querySelectorAll('.dockMenuBtn[data-dock-menu]').forEach((btn) => {
    if (btn.dataset.menuBound === '1') return;
    btn.dataset.menuBound = '1';
    btn.addEventListener('click', (ev) => {
      try { ev.preventDefault(); ev.stopPropagation(); } catch {}
      showDockMenu(btn, btn.dataset.dockMenu);
    });
    btn.addEventListener('mouseenter', () => {
      queueDockMenuHoverSwitch(btn, btn.dataset.dockMenu);
    });
    btn.addEventListener('mouseleave', () => {
      cancelDockMenuHoverSwitch();
    });
  });

  document.querySelectorAll('.dockMenuBtn[data-dock-action]').forEach((btn) => {
    if (btn.dataset.actionBound === '1') return;
    btn.dataset.actionBound = '1';
    btn.addEventListener('click', (ev) => {
      try { ev.preventDefault(); ev.stopPropagation(); } catch {}
      hideDockMenu();
      handleDockMenuAction(btn.dataset.dockAction);
    });
  });
}

// ───────────────────────────────────────────────────────────────────────────────
// User right-click context menu + profile mini-window
// ───────────────────────────────────────────────────────────────────────────────
let EC_USER_CTX_MENU = null;

function ecCtxSameUser(a, b) {
  return ecNormalizeUsernameKey(a) === ecNormalizeUsernameKey(b);
}

function ecCtxUserSetHas(setLike, username) {
  return ecUserSetHasName(setLike, username);
}

function ecCtxRoomUsersContain(room, username) {
  const u = String(username || '').trim();
  if (!u) return false;
  try {
    const users = UIState && UIState.roomUsers && UIState.roomUsers.get(String(room || '').trim());
    if (!Array.isArray(users) || !users.length) return true;
    return users.some((name) => ecCtxSameUser(name, u));
  } catch {
    return true;
  }
}

function ecCtxCanKickFromRoomContext(policy, target, opts = {}) {
  const room = String(opts?.room || UIState.currentRoom || '').trim();
  if (String(opts?.source || '') !== 'room') return false;
  if (!room || !target || !policy) return false;
  if (!policy.is_custom_room || !policy.can_room_moderate) return false;
  if (ecCtxSameUser(target, currentUser)) return false;
  if (ecCtxSameUser(policy.room_owner, target)) return false;
  if (!ecCtxRoomUsersContain(room, target)) return false;
  return true;
}

function ensureUserContextMenu() {
  if (EC_USER_CTX_MENU) return EC_USER_CTX_MENU;

  const menu = ecCreateEl("div", { id: "ecUserCtxMenu", className: "ecCtxMenu hidden" });
  menu.appendChild(ecCtxHeader('User', 'ecCtxUser'));
  menu.appendChild(ecCtxItem('pm', '💬', 'Send message'));
  menu.appendChild(ecCtxItem('profile', '👤', 'View profile'));
  menu.appendChild(ecCtxItem('viewWebcam', '📹', 'View webcam'));
  menu.appendChild(ecCtxItem('inviteRoom', '📨', 'Invite to current room'));
  menu.appendChild(ecCtxItem('roomKick', '👢', 'Kick from this room', 'danger'));
  menu.appendChild(ecCtxItem('addFriend', '➕', 'Add to friends list'));
  menu.appendChild(ecCtxSep());
  menu.appendChild(ecCtxItem('groupMute', '🔇', 'Mute in group'));
  menu.appendChild(ecCtxItem('groupUnmute', '🎤', 'Unmute in group'));
  menu.appendChild(ecCtxItem('groupVoiceKick', '☎', 'Disconnect from group voice'));
  menu.appendChild(ecCtxItem('groupMakeMember', '⬇', 'Set group role: Member'));
  menu.appendChild(ecCtxItem('groupMakeModerator', '🛡️', 'Set group role: Moderator'));
  menu.appendChild(ecCtxItem('groupMakeAdmin', '⭐', 'Set group role: Admin'));
  menu.appendChild(ecCtxItem('groupTransferOwner', '👑', 'Transfer group ownership'));
  menu.appendChild(ecCtxItem('groupKick', '👢', 'Remove from group', 'danger'));
  menu.appendChild(ecCtxSep());
  menu.appendChild(ecCtxItem('moveToFriendGroup', '📁', 'Move to group'));
  menu.appendChild(ecCtxItem('block', '🚫', 'Block'));
  menu.appendChild(ecCtxItem('unblock', '↩', 'Unblock'));
  menu.appendChild(ecCtxItem('removeFriend', '🧹', 'Remove friend', 'danger'));

  menu.addEventListener("contextmenu", (e) => {
    // Prevent the browser context menu on our context menu.
    try { e.preventDefault(); } catch {}
  });

  menu.addEventListener("click", (e) => {
    const item = e.target?.closest?.(".ecCtxItem");
    if (!item) return;
    const action = String(item.dataset.action || "");
    const u = String(menu.dataset.username || "");
    const actionOpts = {
      source: String(menu.dataset.source || ""),
      room: String(menu.dataset.room || ""),
      group_id: Number(menu.dataset.groupId || 0),
    };
    hideUserContextMenu();
    if (!action || !u) return;
    handleUserContextAction(action, u, actionOpts);
  });

  document.addEventListener("mousedown", (e) => {
    if (!EC_USER_CTX_MENU || EC_USER_CTX_MENU.classList.contains("hidden")) return;
    if (EC_USER_CTX_MENU.contains(e.target)) return;
    hideUserContextMenu();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideUserContextMenu();
  });

  window.addEventListener("blur", () => hideUserContextMenu());
  window.addEventListener("resize", () => hideUserContextMenu());
  document.addEventListener("scroll", () => hideUserContextMenu(), true);

  (document.body || document.documentElement).appendChild(menu);
  EC_USER_CTX_MENU = menu;
  return menu;
}

function hideUserContextMenu() {
  if (!EC_USER_CTX_MENU) return;
  EC_USER_CTX_MENU.classList.add("hidden");
  EC_USER_CTX_MENU.dataset.username = "";
  EC_USER_CTX_MENU.dataset.source = "";
  EC_USER_CTX_MENU.dataset.room = "";
  EC_USER_CTX_MENU.dataset.groupId = "";
}


function ecCtxRefreshSeparators(menu) {
  if (!menu) return;
  const children = Array.from(menu.children || []);
  let sawVisibleActionSinceSep = false;
  children.forEach((el) => {
    if (!el || !el.classList) return;
    if (el.classList.contains('ecCtxSep')) {
      el.style.display = sawVisibleActionSinceSep ? '' : 'none';
      sawVisibleActionSinceSep = false;
      return;
    }
    if (el.classList.contains('ecCtxItem') && el.style.display !== 'none') sawVisibleActionSinceSep = true;
  });
  let trailing = true;
  for (let i = children.length - 1; i >= 0; i -= 1) {
    const el = children[i];
    if (!el || !el.classList) continue;
    if (el.classList.contains('ecCtxSep')) {
      if (trailing) el.style.display = 'none';
      continue;
    }
    if (el.classList.contains('ecCtxItem') && el.style.display !== 'none') trailing = false;
  }
}

function showUserContextMenu(ev, username, opts = {}) {
  const u = String(username || "").trim();
  if (!u) return;
  if (u === "empty" || u === "none") return;

  const menu = ensureUserContextMenu();
  const isSelf = ecCtxSameUser(u, currentUser);
  const isFriend = ecCtxUserSetHas(UIState.friendSet, u);
  const isBlocked = ecCtxUserSetHas(UIState.blockedSet, u);

  // Toggle items
  const pm = menu.querySelector('[data-action="pm"]');
  const prof = menu.querySelector('[data-action="profile"]');
  const add = menu.querySelector('[data-action="addFriend"]');
  const viewCam = menu.querySelector('[data-action="viewWebcam"]');
  const move = menu.querySelector('[data-action="moveToFriendGroup"]');
  const block = menu.querySelector('[data-action="block"]');
  const unblock = menu.querySelector('[data-action="unblock"]');
  const rm = menu.querySelector('[data-action="removeFriend"]');
  const inviteRoom = menu.querySelector('[data-action="inviteRoom"]');
  const roomKick = menu.querySelector('[data-action="roomKick"]');
  const groupMute = menu.querySelector('[data-action="groupMute"]');
  const groupUnmute = menu.querySelector('[data-action="groupUnmute"]');
  const groupVoiceKick = menu.querySelector('[data-action="groupVoiceKick"]');
  const groupMakeMember = menu.querySelector('[data-action="groupMakeMember"]');
  const groupMakeModerator = menu.querySelector('[data-action="groupMakeModerator"]');
  const groupMakeAdmin = menu.querySelector('[data-action="groupMakeAdmin"]');
  const groupTransferOwner = menu.querySelector('[data-action="groupTransferOwner"]');
  const groupKick = menu.querySelector('[data-action="groupKick"]');
  if (pm) pm.style.display = (isSelf || isBlocked) ? "none" : "";
  const ctxRoom = String(opts?.room || UIState.currentRoom || '').trim();
  const canViewCam = (() => {
    if (isSelf || isBlocked) return false;
    try {
      return typeof ecCanRequestUserWebcam === "function" && ecCanRequestUserWebcam(u, ctxRoom);
    } catch {
      return false;
    }
  })();

  if (prof) prof.style.display = "";
  if (viewCam) {
    viewCam.style.display = canViewCam ? "" : "none";
    viewCam.title = canViewCam ? `View ${u}'s webcam` : `${u} does not have webcam on`;
  }
  const currentInviteRoom = String(UIState.roomEmbedRoom || UIState.currentRoom || '').trim();
  if (inviteRoom) inviteRoom.style.display = (!isSelf && !isBlocked && !!currentInviteRoom) ? "" : "none";
  const activeRoomPolicy = ctxRoom ? getRoomPolicy(ctxRoom) : null;
  const canRoomKick = !isBlocked && ecCtxCanKickFromRoomContext(activeRoomPolicy, u, { ...opts, room: ctxRoom });
  if (roomKick) {
    roomKick.style.display = canRoomKick ? "" : "none";
    roomKick.title = canRoomKick ? `Kick ${u} from ${ctxRoom}` : 'Only room owners/moderators can kick users from custom rooms';
  }
  if (add) add.style.display = (!isSelf && !isFriend && !isBlocked) ? "" : "none";
  if (move) move.style.display = (!isSelf && isFriend && String(opts?.source || '') === 'friends') ? "" : "none";
  if (block) block.style.display = (!isSelf && !isBlocked) ? "" : "none";
  if (unblock) unblock.style.display = (!isSelf && isBlocked) ? "" : "none";
  if (rm) rm.style.display = (!isSelf && isFriend && !isBlocked) ? "" : "none";

  const groupId = Number(opts?.group_id || opts?.groupId || 0);
  const groupSource = String(opts?.source || '') === 'group' && groupId > 0;
  const targetRole = groupSource && typeof groupMemberDetailFor === 'function' ? (groupMemberDetailFor(groupId, u)?.role || 'member') : 'member';
  const myGroupRole = groupSource && typeof currentGroupRole === 'function' ? currentGroupRole(groupId) : 'member';
  const myGroupRank = groupSource && typeof groupRoleRank === 'function' ? groupRoleRank(myGroupRole) : 0;
  const targetRank = groupSource && typeof groupRoleRank === 'function' ? groupRoleRank(targetRole) : 0;
  const canGroupModerateTarget = groupSource && !isSelf && !isBlocked && myGroupRank >= 1 && myGroupRank > targetRank;
  const isGroupOwner = groupSource && myGroupRole === 'owner';
  const targetInGroupVoice = groupSource && typeof groupVoiceUserIsActive === 'function' && groupVoiceUserIsActive(groupId, u);
  const targetMutedInGroup = groupSource && typeof groupMemberIsMuted === 'function' && groupMemberIsMuted(groupId, u);
  if (groupMute) groupMute.style.display = (canGroupModerateTarget && !targetMutedInGroup) ? "" : "none";
  if (groupUnmute) groupUnmute.style.display = (canGroupModerateTarget && targetMutedInGroup) ? "" : "none";
  if (groupVoiceKick) groupVoiceKick.style.display = (canGroupModerateTarget && targetInGroupVoice) ? "" : "none";
  if (groupMakeMember) groupMakeMember.style.display = (isGroupOwner && !isSelf && targetRole !== 'member') ? "" : "none";
  if (groupMakeModerator) groupMakeModerator.style.display = (isGroupOwner && !isSelf && targetRole !== 'moderator') ? "" : "none";
  if (groupMakeAdmin) groupMakeAdmin.style.display = (isGroupOwner && !isSelf && targetRole !== 'admin') ? "" : "none";
  if (groupTransferOwner) groupTransferOwner.style.display = (isGroupOwner && !isSelf) ? "" : "none";
  if (groupKick) groupKick.style.display = canGroupModerateTarget ? "" : "none";
  ecCtxRefreshSeparators(menu);

  menu.dataset.username = u;
  menu.dataset.source = String(opts?.source || '');
  menu.dataset.room = String(opts?.room || UIState.currentRoom || '');
  menu.dataset.groupId = String(groupId || '');
  const head = menu.querySelector("#ecCtxUser");
  if (head) head.textContent = u;

  // Position
  try {
    ev.preventDefault();
    ev.stopPropagation();
  } catch {}
  menu.classList.remove("hidden");

  // Must measure after visible.
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


function ecRemoveLocalBlockedUserEverywhere(username) {
  const raw = String(username || '').replace(/\s+/g, ' ').trim();
  const key = (typeof ecNormalizeUsernameKey === 'function') ? ecNormalizeUsernameKey(raw) : raw.toLowerCase();
  if (!key) return false;
  let changed = false;
  try {
    const oldSet = (UIState.blockedSet instanceof Set) ? UIState.blockedSet : new Set();
    const next = new Set();
    oldSet.forEach((value) => {
      const item = String(value || '').replace(/\s+/g, ' ').trim();
      const itemKey = (typeof ecNormalizeUsernameKey === 'function') ? ecNormalizeUsernameKey(item) : item.toLowerCase();
      if (itemKey === key) {
        changed = true;
        return;
      }
      if (item) next.add(item);
    });
    UIState.blockedSet = next;
  } catch {
    try { UIState.blockedSet = new Set(); } catch {}
  }
  try {
    if (Array.isArray(UIState.blockedUsersCache)) {
      const before = UIState.blockedUsersCache.length;
      UIState.blockedUsersCache = UIState.blockedUsersCache.filter((value) => {
        const itemKey = (typeof ecNormalizeUsernameKey === 'function')
          ? ecNormalizeUsernameKey(value)
          : String(value || '').trim().toLowerCase();
        return itemKey !== key;
      });
      if (UIState.blockedUsersCache.length !== before) changed = true;
    }
  } catch {}
  try {
    const cacheKey = key;
    if (typeof RSA_PUBKEY_CACHE !== 'undefined' && RSA_PUBKEY_CACHE?.delete) RSA_PUBKEY_CACHE.delete(cacheKey);
  } catch {}
  try {
    const blockedCountEl = $('blockedUsersCount');
    if (blockedCountEl && UIState.blockedSet instanceof Set) blockedCountEl.textContent = String(UIState.blockedSet.size);
  } catch {}
  try { updateDockSummaryCounts?.(); } catch {}
  try { if (typeof rbRenderRoomLists === 'function' && typeof rbHasUI === 'function' && rbHasUI()) rbRenderRoomLists(); } catch {}
  return changed;
}

function ecRefreshCurrentRoomAfterBlockStateChange(peer, reason = 'block_state_change') {
  const room = String(UIState?.currentRoom || UIState?.roomEmbedRoom || '').trim();
  if (!room) return;
  try { if (typeof getUsersInRoom === 'function') getUsersInRoom(room); } catch {}
  try {
    if (typeof requestRoomUsers === 'function') {
      requestRoomUsers(room, 2200).catch(() => {});
    }
  } catch {}
  try {
    if (reason === 'unblock' && typeof ecScheduleRoomRosterSelfHeal === 'function') {
      ecScheduleRoomRosterSelfHeal(room, 'unblock');
    }
  } catch {}
  try {
    if (typeof ecClearRoomTyping === 'function' && peer) ecClearRoomTyping(room, peer);
  } catch {}
}

function getBlockConfirmMessage(username) {
  const u = String(username || '').trim();
  const isFriend = ecCtxUserSetHas(UIState.friendSet, u);
  if (isFriend) {
    return `Block ${u}?

Blocking a friend will remove them from your friends list and add them to your blocked users list.`;
  }
  return `Block ${u}?

They will be added to your blocked users list.`;
}

function blockUserWithPrompt(username, opts = {}) {
  const u = String(username || '').trim();
  if (!u || ecCtxSameUser(u, currentUser)) return;
  ecConfirm(String(opts.message || getBlockConfirmMessage(u)), {
    title: `Block ${u}?`,
    confirmLabel: 'Block user',
    danger: true,
    focusCancel: true,
  }).then((ok) => {
    if (!ok) return;
    socket.emit("block_user", { blocked: u }, (res) => {
      const removedFriendship = !!res?.removed_friendship;
      const removedPending = !!res?.removed_pending;
      const removedRoomInvites = !!res?.removed_room_invites;
      const removedGroupInvites = !!res?.removed_group_invites;
      const removedOfflinePms = !!res?.removed_offline_pms;
      const details = [];
      if (removedFriendship) details.push('removed from friends');
      if (removedPending) details.push('cleared friend requests');
      if (removedRoomInvites) details.push('cleared room invites');
      if (removedGroupInvites) details.push('revoked group invites');
      if (removedOfflinePms) details.push('cleared missed PMs');
      const canonicalBlocked = String(res?.blocked || u).trim() || u;
      let msg = `🚫 Blocked ${canonicalBlocked}`;
      if (details.length) msg += ` and ${details.join(', ')}`;
      toast(res?.success ? msg : `❌ ${res?.error || "Block failed"}`, res?.success ? "ok" : "error");
      if (res?.success) {
        // Apply the local block immediately. Waiting for the next blocked-users
        // refresh can leave a blocked room user in the outbound E2EE recipient
        // list long enough to fail the next room send with "missing public keys".
        try {
          if (!(UIState.blockedSet instanceof Set)) UIState.blockedSet = new Set();
          UIState.blockedSet.add(canonicalBlocked);
          UIState.blockedSet.add(String(canonicalBlocked || '').trim().toLowerCase());
        } catch {}
        try {
          if (typeof RSA_PUBKEY_CACHE !== 'undefined' && RSA_PUBKEY_CACHE?.delete) {
            RSA_PUBKEY_CACHE.delete(String(canonicalBlocked || '').trim().toLowerCase());
          }
        } catch {}
        try {
          if (typeof ecPruneRoomMessagesFromBlockedUser === 'function') ecPruneRoomMessagesFromBlockedUser(canonicalBlocked);
          if (typeof ecClearRoomTyping === 'function') ecClearRoomTyping(UIState.currentRoom, canonicalBlocked);
        } catch {}
        ecRefreshCurrentRoomAfterBlockStateChange(canonicalBlocked, 'block');
      }
      if (res?.success && typeof cleanupBlockedPairAlerts === 'function') cleanupBlockedPairAlerts(canonicalBlocked, { refresh: true });
      getFriends();
      getPendingFriendRequests();
      getBlockedUsers();
    });
  });
}

let EC_BLOCKED_USERS_MODAL_BOUND = false;

function closeBlockedUsersModal() {
  $('blockedUsersModal')?.classList.add('hidden');
}

function bindBlockedUsersModal() {
  if (EC_BLOCKED_USERS_MODAL_BOUND) return;
  EC_BLOCKED_USERS_MODAL_BOUND = true;
  $('btnCloseBlockedUsers')?.addEventListener('click', () => closeBlockedUsersModal());
  $('btnRefreshBlockedUsers')?.addEventListener('click', () => getBlockedUsers());
  $('blockedUsersModal')?.addEventListener('mousedown', (ev) => {
    if (ev.target === $('blockedUsersModal')) closeBlockedUsersModal();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !$('blockedUsersModal')?.classList.contains('hidden')) closeBlockedUsersModal();
  });
}

function viewBlockedUsersFromMenu() {
  bindBlockedUsersModal();
  const modal = $('blockedUsersModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  getBlockedUsers();
}

async function kickUserFromCurrentRoom(username, roomOverride = "") {
  const u = String(username || '').trim();
  const room = String(roomOverride || UIState.currentRoom || UIState.roomEmbedRoom || '').trim();
  if (!u || !room) return;
  const ok = await ecConfirm(`Kick ${u} from ${room}?`, {
    title: 'Kick from this room?',
    confirmLabel: 'Kick user',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  try {
    socket.emit('room_kick_user', { room, username: u }, (res) => {
      if (res && res.success) {
        toast(`👢 Kicked ${u} from ${room}`, 'ok', 4200);
        try { getUsersInRoom(room); } catch {}
      } else {
        toast(`❌ Kick failed: ${res?.error || 'room kick failed'}`, 'error', 7000);
      }
    });
  } catch (e) {
    toast(`❌ Kick failed: ${e?.message || e}`, 'error', 7000);
  }
}

async function handleUserContextAction(action, username, opts = {}) {
  const u = String(username || "").trim();
  if (!u) return;

  if (action === "pm") {
    openPrivateChat(u);
    return;
  }
  if (action === "profile") {
    openProfileWindow(u);
    return;
  }
  if (action === 'viewWebcam') {
    const room = String(opts?.room || UIState.currentRoom || '').trim();
    if (typeof huiRequestRemoteCamFromRoomUser === "function") {
      await huiRequestRemoteCamFromRoomUser(u, room);
    } else {
      toast("📷 Webcam viewing is not ready yet.", "warn");
    }
    return;
  }
  if (action === 'inviteRoom') {
    if (typeof inviteSelectedUserToCurrentRoom === 'function') await inviteSelectedUserToCurrentRoom(u);
    return;
  }
  if (action === 'roomKick') {
    await kickUserFromCurrentRoom(u, opts?.room);
    return;
  }
  if (action === 'groupMute') {
    if (typeof groupMuteMember === 'function') await groupMuteMember(opts?.group_id, u);
    return;
  }
  if (action === 'groupUnmute') {
    if (typeof groupUnmuteMember === 'function') await groupUnmuteMember(opts?.group_id, u);
    return;
  }
  if (action === 'groupVoiceKick') {
    if (typeof groupKickMemberFromVoice === 'function') await groupKickMemberFromVoice(opts?.group_id, u);
    return;
  }
  if (action === 'groupMakeMember') {
    if (typeof groupSetMemberRole === 'function') await groupSetMemberRole(opts?.group_id, u, 'member');
    return;
  }
  if (action === 'groupMakeModerator') {
    if (typeof groupSetMemberRole === 'function') await groupSetMemberRole(opts?.group_id, u, 'moderator');
    return;
  }
  if (action === 'groupMakeAdmin') {
    if (typeof groupSetMemberRole === 'function') await groupSetMemberRole(opts?.group_id, u, 'admin');
    return;
  }
  if (action === 'groupTransferOwner') {
    if (typeof groupTransferOwnership === 'function') await groupTransferOwnership(opts?.group_id, u);
    return;
  }
  if (action === 'groupKick') {
    if (typeof groupKickMember === 'function') await groupKickMember(opts?.group_id, u);
    return;
  }
  if (action === 'addFriend') {
    sendFriendRequestTo(u);
    return;
  }
  if (action === 'moveToFriendGroup') {
    const currentGroup = getFriendGroupForFriend(u);
    const next = await promptForFriendGroup(currentGroup, { allowBlankToUngroup: true });
    if (next === null) return;
    const target = next ? ensureFriendGroup(next) : '';
    assignFriendToGroup(u, target);
    updateFriendsListUI(UIState.friendsListCache);
    toast(next ? `📁 Moved ${u} to ${target}` : `📂 Moved ${u} back to ${FRIEND_GROUP_DEFAULT_LABEL}`, 'ok');
    return;
  }
  if (action === "block") {
    blockUserWithPrompt(u);
    return;
  }
  if (action === "unblock") {
    if (u === currentUser) return;
    socket.emit("unblock_user", { blocked: u }, (res) => {
      const canonicalBlocked = String(res?.blocked || u).trim() || u;
      const localOk = !!res?.success || /not\s*blocked/i.test(String(res?.error || ''));
      toast(res?.success ? `↩ Unblocked ${canonicalBlocked}` : localOk ? `↩ ${canonicalBlocked} is already unblocked` : `❌ ${res?.error || "Unblock failed"}`, localOk ? "ok" : "error");
      if (localOk) {
        ecRemoveLocalBlockedUserEverywhere(canonicalBlocked);
        ecRefreshCurrentRoomAfterBlockStateChange(canonicalBlocked, 'unblock');
      }
      getFriends();
      getPendingFriendRequests();
      getBlockedUsers();
    });
    return;
  }
  if (action === "removeFriend") {
    if (u === currentUser) return;
    ecConfirm(`Remove ${u} from your friends list?`, {
      title: `Remove ${u}?`,
      confirmLabel: 'Remove friend',
      danger: true,
      focusCancel: true,
    }).then((ok) => {
      if (!ok) return;
      socket.emit("remove_friend", { friend: u }, (res) => {
        const canonicalFriend = String(res?.friend || u).trim() || u;
        toast(res?.success ? `🧹 Removed ${canonicalFriend}` : `❌ ${res?.error || "Remove friend failed"}`, res?.success ? "ok" : "error");
        getFriends();
        getPendingFriendRequests();
      });
    });
    return;
  }
}
