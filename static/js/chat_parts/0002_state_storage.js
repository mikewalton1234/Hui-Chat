// ───────────────────────────────────────────────────────────────────────────────
// Settings (localStorage)
// ───────────────────────────────────────────────────────────────────────────────
const EC_STORAGE_PREFIX = 'ec_';

function ecStorageScopeUser() {
  try {
    const raw = String((window.CURRENT_USER || window.USERNAME || '').trim()).toLowerCase();
    if (!raw) return '';
    return raw.replace(/[^a-z0-9_.@-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 96);
  } catch {
    return '';
  }
}

function getCurrentAccountStorageToken() {
  const user = ecStorageScopeUser();
  return user ? `__acct_${user}` : '__acct_global';
}

function buildScopedStorageKey(key, opts = {}) {
  const clean = String(key || '').trim();
  const baseKey = `${EC_STORAGE_PREFIX}${clean}`;
  if (!clean || !baseKey || baseKey === EC_STORAGE_PREFIX) return baseKey;
  if (opts.scope === 'global') return baseKey;
  if (!ecStorageScopeUser()) return baseKey;
  return `ec_user_${ecStorageScopeUser()}_${clean}`;
}

function ecScopedStorageKey(key, scope = 'account') {
  return buildScopedStorageKey(key, scope === 'global' ? { scope: 'global' } : {});
}

const Settings = {
  get(key, fallback, opts = {}) {
    try {
      const scopedKey = buildScopedStorageKey(key, opts);
      const raw = localStorage.getItem(scopedKey);
      if (raw !== null) return JSON.parse(raw);
      if (opts.allowLegacyFallback) {
        const legacyRaw = localStorage.getItem(ecScopedStorageKey(key, 'global'));
        return legacyRaw === null ? fallback : JSON.parse(legacyRaw);
      }
      return fallback;
    } catch {
      return fallback;
    }
  },
  set(key, val, opts = {}) {
    try {
      localStorage.setItem(buildScopedStorageKey(key, opts), JSON.stringify(val));
      if (opts.clearLegacy) {
        try { localStorage.removeItem(ecScopedStorageKey(key, 'global')); } catch {}
      }
    } catch {
      // Some browser contexts/extensions block storage; fall back to in-memory prefs.
    }
  },
  remove(key, opts = {}) {
    try {
      localStorage.removeItem(buildScopedStorageKey(key, opts));
      if (opts.clearLegacy) {
        try { localStorage.removeItem(ecScopedStorageKey(key, 'global')); } catch {}
      }
    } catch {}
  }
};

// Offline-DM backlog persistence (ciphertext only)
// Purpose: eliminate the "missed bubble resurrects on refresh" failure mode by allowing
// the client to *consume* offline_messages on the server immediately, while retaining
// ciphertext locally until private messages are ready in this tab.
const OFFLINE_DM_BACKLOG_KEY = `ec_offline_dm_backlog_v1:${ecStorageScopeUser()}`;

function getOfflineDmBacklogStorageKey() {
  return OFFLINE_DM_BACKLOG_KEY;
}

function persistOfflineDmBacklog() {
  try {
    const peers = {};
    let total = 0;
    for (const [peer, arr] of UIState.pendingOfflineDm.entries()) {
      if (!peer || !Array.isArray(arr) || !arr.length) continue;
      const cleaned = [];
      for (const m of arr.slice(-80)) { // bounded per peer
        const cipher = m?.cipher;
        if (!cipher) continue;
        cleaned.push({
          id: (Number(m?.id || 0) || 0) || null,
          cipher: String(cipher),
          ts: (typeof m?.ts === 'number') ? m.ts : null,
          needsAck: !!m?.needsAck
        });
      }
      if (cleaned.length) {
        peers[peer] = cleaned;
        total += cleaned.length;
      }
      if (total > 220) break; // global bound
    }
    localStorage.setItem(getOfflineDmBacklogStorageKey(), JSON.stringify({ v: 1, saved: Date.now(), peers }));
    try { localStorage.removeItem(`ec_offline_dm_backlog_v1`); } catch {}
    return true;
  } catch {
    return false;
  }
}

function loadOfflineDmBacklog() {
  try {
    const raw = localStorage.getItem(getOfflineDmBacklogStorageKey());
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const peers = parsed?.peers && typeof parsed.peers === 'object' ? parsed.peers : null;
    if (!peers) return;

    for (const peer of Object.keys(peers)) {
      const arr = peers[peer];
      if (!peer || !Array.isArray(arr) || !arr.length) continue;
      const cleaned = [];
      for (const m of arr) {
        const cipher = m?.cipher;
        if (!cipher) continue;
        const id = Number(m?.id || 0) || 0;
        if (id > 0) UIState.pendingOfflineDmSeen.add(id);
        cleaned.push({
          id: id > 0 ? id : null,
          cipher: String(cipher),
          ts: (typeof m?.ts === 'number') ? m.ts : null,
          needsAck: !!m?.needsAck
        });
      }
      if (cleaned.length) UIState.pendingOfflineDm.set(peer, cleaned.slice(-200));
    }
  } catch {
    // ignore
  }
}

function clearOfflineDmBacklog(peer = null) {
  try {
    if (!peer) {
      UIState.pendingOfflineDm.clear();
      localStorage.removeItem(getOfflineDmBacklogStorageKey());
      try { localStorage.removeItem(`ec_offline_dm_backlog_v1`); } catch {}
      return;
    }
    const keys = Array.from(UIState.pendingOfflineDm?.keys?.() || []);
    const matches = keys.filter((name) => {
      try {
        if (typeof ecMissedSamePeer === 'function') return ecMissedSamePeer(name, peer);
      } catch {}
      return String(name || '') === String(peer || '');
    });
    if (matches.length) matches.forEach((name) => UIState.pendingOfflineDm.delete(name));
    else UIState.pendingOfflineDm.delete(peer);
    persistOfflineDmBacklog();
  } catch {
    // ignore
  }
}

const PRESENCE_REFRESH_INTERVAL_MS = 15000;
let EC_LAST_PRESENCE_PULL_AT = 0;
let EC_PRESENCE_POLL_STARTED = false;

function requestPresenceRefresh(reason = "manual", { force = false } = {}) {
  try {
    if (!socket || !socket.connected) return;
    const now = Date.now();
    if (!force && (now - EC_LAST_PRESENCE_PULL_AT) < 3000) return;
    EC_LAST_PRESENCE_PULL_AT = now;
    socket.emit("get_friend_presence");
    socket.emit("get_my_presence");
  } catch (e) {
    console.warn("presence refresh failed", reason, e);
  }
}

function ensurePresencePolling() {
  if (EC_PRESENCE_POLL_STARTED) return;
  EC_PRESENCE_POLL_STARTED = true;

  setInterval(() => {
    if (!socket || !socket.connected) return;
    requestPresenceRefresh("interval");
  }, PRESENCE_REFRESH_INTERVAL_MS);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) requestPresenceRefresh("visibility");
  });

  window.addEventListener("focus", () => {
    requestPresenceRefresh("focus");
  });

  window.addEventListener("online", () => {
    setTimeout(() => requestPresenceRefresh("online"), 600);
  });
}

const UIState = {
  highestZ: 1000,
  windows: new Map(),      // id -> window element
  minimized: new Map(),    // id -> task button element
  activeTab: "friends",
  currentRoom: null,       // server-side one room at a time
  roomsCache: [],          // last known room list for re-rendering (policy badges)
  roomUsers: new Map(),    // room -> [usernames] (last known)
  roomUnblockRefreshUntil: 0, // short grace while room roster heals after unblock
  friendSet: new Set(),    // fast check for (is friend)
  blockedSet: new Set(),   // fast check for (is blocked by me)
  blockedUsersCache: [],   // last known blocked usernames for the blocked-users modal
  pendingRequests: [],     // pending inbound friend requests
  myGroups: [],            // cached group list for the right dock
  groupInvites: [],        // cached group invites for the right dock
  roomInvites: [],         // cached room invites for the notification bubble
  profilePostNotifications: [], // unread likes/comments on profile posts
  webcamRequests: [],     // pending inbound webcam view requests shown in Alerts
  roomPolicy: new Map(),   // room -> {locked, readonly, slowmode_seconds, can_send, ...}
  groupMembers: new Map(), // group_id -> [usernames] (last known)
  groupMemberDetails: new Map(), // group_id -> [{username, role}] for roster UI
  groupMutedMembers: new Map(), // group_id -> Set(username lower-case) for group mute-list UI
  groupUnreadCounts: new Map(), // group_id -> unread group chat count
  roomEmbedRoom: null,     // room currently shown in the left embedded pane
  presence: new Map(),     // username -> {online, presence, custom_status, last_seen}
  friendsListCache: [],    // cached friend usernames for grouped rendering / search refresh
  friendGroups: null,      // { groups: [], assignments: {}, collapsed: {} } from localStorage
  selectedBuddy: '',       // last clicked / right-clicked username in dock or room list
  selectedBuddySource: '', // friends | room | blocked | pending | missed
  missedPmSummary: [],     // [{sender, count}] from server (offline-only)
  livePmUnreadCounts: new Map(), // peer -> unread live PMs not yet opened/focused
  consumingOfflinePeers: new Set(), // peers currently being consumed (avoid duplicate fetch loops)
  consumingOfflinePeerPromises: new Map(), // peer -> in-flight consume promise
  pendingOfflineDm: new Map(),      // peer -> [{id, cipher, ts}]
  pendingOfflineDmSeen: new Set(),  // offline id -> already queued/processed in this tab
  unlockSkipped: false,
  prefs: {
    darkMode: Settings.get("darkMode", false),
    highContrast: Settings.get("highContrast", false),
    accentTheme: Settings.get("accentTheme", "default"),
    popupNotif: Settings.get("popupNotif", false),
    soundNotif: Settings.get("soundNotif", HUI_CFG.sound_notifications_default === undefined ? true : !!HUI_CFG.sound_notifications_default),
    soundTheme: Settings.get("soundTheme", String(HUI_CFG.sound_theme_default || HUI_CFG.default_sound_theme || "soft_chime")),
    rememberUnlock: false,
    roomFontSize: Settings.get("roomFontSize", 13),
    roomFontFamily: Settings.get("roomFontFamily", "Arial"),
    roomComposerBold: Settings.get("roomComposerBold", false),
    roomComposerItalic: Settings.get("roomComposerItalic", false),
    roomComposerUnderline: Settings.get("roomComposerUnderline", false),
    roomComposerColor: Settings.get("roomComposerColor", "#111111"),
    emoticonSize: Settings.get("emoticonSize", 26),
    gifTileSize: Settings.get("gifTileSize", 140),
    gifResultsPerLoad: Settings.get("gifResultsPerLoad", 12),
    gifOpenMode: Settings.get("gifOpenMode", "recents"),
    gifShowTitles: Settings.get("gifShowTitles", true),
    gifKeepOpen: Settings.get("gifKeepOpen", false),
    settingsTab: Settings.get("settingsTab", "chat"),
    missedToast: Settings.get("missedToast", true),
    savePmLocal: Settings.get("savePmLocal", false),
    friendStatusInline: Settings.get("friendStatusInline", true),
    friendStatusTooltip: Settings.get("friendStatusTooltip", true),
    helpHints: Settings.get("helpHints", true)
  },
  inviteSeen: new Set(),
  myProfile: null
};

// Local message saving is intentionally disabled for room/group/PM chat logs on this build.
try {
  UIState.prefs.savePmLocal = false;
  Settings.set("savePmLocal", false);
} catch {}

// Restore any ciphertext-only backlog that was consumed earlier (but not yet decrypted).
// This makes "consume now, decrypt later" safe even across page refreshes.
try { loadOfflineDmBacklog(); } catch {}

const DOCK_SECTION_DEFAULT_ORDER = {
  friends: ["friendsSectionList"],
  groups: ["groupsSectionList", "groupsSectionCreate", "groupsSectionJoin"]
};
