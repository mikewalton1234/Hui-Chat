const CHAT_GROUP_WINDOW_MS = 5 * 60 * 1000;

function forceScrollLogToBottom(log) {
  if (!log) return;
  try { log.scrollTop = log.scrollHeight; } catch {}
}

function scheduleScrollLogToBottom(log) {
  if (!log) return;
  const run = () => forceScrollLogToBottom(log);
  run();
  try {
    requestAnimationFrame(() => {
      run();
      requestAnimationFrame(run);
    });
  } catch {}
  setTimeout(run, 0);
  setTimeout(run, 50);
  setTimeout(run, 180);
}

function disableOutputContextMenu(el) {
  if (!el || el.dataset?.ecNoContextMenu === "1") return;
  try { el.dataset.ecNoContextMenu = "1"; } catch {}
  el.addEventListener('contextmenu', (ev) => {
    try { ev.preventDefault(); ev.stopPropagation(); } catch {}
    return false;
  });
}

function normalizeChatTs(ts) {
  if (ts === null || ts === undefined || ts === "") return Date.now();
  if (ts instanceof Date) return ts.getTime();
  if (typeof ts === "number" && Number.isFinite(ts)) return ts < 1e12 ? Math.round(ts * 1000) : Math.round(ts);
  const n = Number(ts);
  if (Number.isFinite(n)) return n < 1e12 ? Math.round(n * 1000) : Math.round(n);
  const parsed = Date.parse(String(ts));
  return Number.isNaN(parsed) ? Date.now() : parsed;
}

function chatDateKey(ts) {
  const d = new Date(normalizeChatTs(ts));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function formatChatTime(ts) {
  return new Date(normalizeChatTs(ts)).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatChatDateLabel(ts) {
  const dt = new Date(normalizeChatTs(ts));
  const that = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diffDays = Math.round((today - that) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  const opts = { weekday: "long", month: "short", day: "numeric" };
  if (dt.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return dt.toLocaleDateString([], opts);
}

function ensureChatLogState(log) {
  if (!log) return null;
  if (!log._ecChatUi) log._ecChatUi = { lastDateKey: null, lastGroup: null };
  return log._ecChatUi;
}

function resetChatLogState(log) {
  if (!log) return;
  try { ecClearNode(log); } catch {}
  const st = ensureChatLogState(log);
  if (!st) return;
  st.lastDateKey = null;
  st.lastGroup = null;
}

function makeDateSeparatorElement(ts) {
  const el = document.createElement("div");
  el.className = "ec-dateSep";
  el.dataset.dateKey = chatDateKey(ts);
  const label = document.createElement("span");
  label.textContent = formatChatDateLabel(ts);
  el.appendChild(label);
  return el;
}

function ensureDateSeparatorForLog(log, tsMs) {
  const st = ensureChatLogState(log);
  if (!st) return null;
  const key = chatDateKey(tsMs);
  if (st.lastDateKey === key) return null;
  const sep = makeDateSeparatorElement(tsMs);
  log.appendChild(sep);
  st.lastDateKey = key;
  st.lastGroup = null;
  return sep;
}

function getGroupAvatarInitial(label) {
  const s = String(label || "?").trim();
  const m = s.match(/[A-Za-z0-9]/);
  return (m ? m[0] : (s[0] || "?")).toUpperCase();
}

function ecMessageAvatarUsername(senderLabel) {
  const raw = String(senderLabel || "").replace(/:\s*$/, "").trim();
  if (/^you$/i.test(raw)) return String(currentUser || raw || "").trim();
  return raw;
}

function ecDomAvatarUrlForUsername(username) {
  const name = String(username || "").replace(/\s+/g, " ").trim();
  const key = name.toLowerCase();
  if (!key) return "";

  const normalize = (raw) => {
    let out = String(raw || "").trim();
    if (!out) return "";
    try {
      if (typeof normalizeDockAvatarUrl === "function") out = normalizeDockAvatarUrl(out) || "";
      else if (typeof ecNormalizeSafeUrl === "function") out = ecNormalizeSafeUrl(out, { allowRelative: true, allowExternal: true }) || "";
    } catch { out = ""; }
    return out;
  };

  const selfKey = String(currentUser || "").trim().toLowerCase();
  if (selfKey && key === selfKey) {
    const img = document.querySelector("#meAvatar img");
    const url = normalize(img?.getAttribute("src") || img?.src || "");
    if (url) return url;
  }

  const candidates = [
    ...document.querySelectorAll("#friendsList li[data-name], #userList li[data-name], .roomUsersList li[data-name]")
  ];
  for (const li of candidates) {
    const liKey = String(li?.dataset?.name || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (liKey !== key) continue;
    const img = li.querySelector(".liAvatar img, img");
    const url = normalize(img?.getAttribute("src") || img?.src || "");
    if (url) return url;
  }
  return "";
}

function ecMessageAvatarUrlForSender(senderLabel) {
  const username = ecMessageAvatarUsername(senderLabel);
  if (!username) return "";
  const selfKey = String(currentUser || "").trim().toLowerCase();
  const userKey = String(username || "").trim().toLowerCase();
  if (selfKey && userKey && selfKey === userKey) {
    const mine = (window.UIState && UIState.myProfile && typeof UIState.myProfile === "object") ? UIState.myProfile : null;
    if (mine && (mine.avatar_url || mine.avatarUrl)) return String(mine.avatar_url || mine.avatarUrl || "").trim();
  }
  try {
    const presence = (typeof ecGetPresenceForUsername === "function") ? ecGetPresenceForUsername(username) : null;
    if (presence && typeof presence === "object" && (presence.avatar_url || presence.avatarUrl)) {
      return String(presence.avatar_url || presence.avatarUrl || "").trim();
    }
  } catch {}

  // Last-resort live DOM lookup. This fixes messages that render before the
  // profile/presence cache is hydrated, while the hub/friend list already has
  // the real profile image on screen.
  return ecDomAvatarUrlForUsername(username);
}

function ecRenderMessageAvatar(avatar, senderLabel) {
  if (!avatar) return;
  const username = ecMessageAvatarUsername(senderLabel);
  const fallback = getGroupAvatarInitial(username || senderLabel);
  const userKey = String(username || senderLabel || "").trim().toLowerCase();
  avatar.dataset.ecAvatarUser = username || String(senderLabel || "");
  avatar.dataset.ecAvatarUserKey = userKey;
  avatar.classList.remove("hasImage");
  avatar.replaceChildren(document.createTextNode(fallback));

  let safeAvatarUrl = ecMessageAvatarUrlForSender(username || senderLabel);
  try {
    if (typeof normalizeDockAvatarUrl === "function") safeAvatarUrl = normalizeDockAvatarUrl(safeAvatarUrl);
    else if (typeof ecNormalizeSafeUrl === "function") safeAvatarUrl = ecNormalizeSafeUrl(safeAvatarUrl, { allowRelative: true, allowExternal: true }) || "";
  } catch { safeAvatarUrl = ""; }
  if (!safeAvatarUrl) return;

  const img = document.createElement("img");
  img.src = safeAvatarUrl;
  img.alt = `${username || senderLabel || "User"} avatar`;
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  img.addEventListener("error", () => {
    avatar.classList.remove("hasImage");
    avatar.replaceChildren(document.createTextNode(fallback));
  }, { once: true });
  avatar.classList.add("hasImage");
  avatar.replaceChildren(img);
}

function ecRefreshMessageAvatarsForUsername(username = "") {
  const name = ecMessageAvatarUsername(username);
  const key = String(name || username || "").trim().toLowerCase();
  if (!key) return;
  document.querySelectorAll('.ec-msgAvatar[data-ec-avatar-user-key]').forEach((avatar) => {
    const avatarKey = String(avatar.dataset.ecAvatarUserKey || "").trim().toLowerCase();
    if (avatarKey && avatarKey === key) ecRenderMessageAvatar(avatar, avatar.dataset.ecAvatarUser || name);
  });
}

function makeChatGroupElement(senderLabel, tsMs, { variant = "generic" } = {}) {
  const group = document.createElement("div");
  group.className = `ec-msgGroup ec-msgGroup--${variant}`;
  group.dataset.senderKey = String(senderLabel || "").trim().toLowerCase();
  group.dataset.dateKey = chatDateKey(tsMs);
  group.dataset.variant = variant;
  const mine = /^you$/i.test(String(senderLabel || "").trim()) || String(senderLabel || "").trim() === String(currentUser || "").trim();
  if (mine) group.classList.add("is-self");

  const avatar = document.createElement("div");
  avatar.className = "ec-msgAvatar";
  avatar.setAttribute("aria-hidden", "true");
  ecRenderMessageAvatar(avatar, senderLabel);

  const main = document.createElement("div");
  main.className = "ec-msgGroupMain";

  const head = document.createElement("div");
  head.className = "ec-msgGroupHead";

  const nameEl = document.createElement("span");
  nameEl.className = "ec-msgSender";
  nameEl.textContent = String(senderLabel || "Unknown");

  const timeEl = document.createElement("span");
  timeEl.className = "ec-msgTime";
  timeEl.textContent = formatChatTime(tsMs);
  timeEl.title = new Date(normalizeChatTs(tsMs)).toLocaleString();

  const items = document.createElement("div");
  items.className = "ec-msgItems";

  head.appendChild(nameEl);
  head.appendChild(timeEl);
  main.appendChild(head);
  main.appendChild(items);
  group.appendChild(avatar);
  group.appendChild(main);

  return { group, items, timeEl };
}

function ecShowSenderEveryMessage(context, variant) {
  const cfg = (window.HUI_CFG && typeof window.HUI_CFG === "object") ? window.HUI_CFG : {};
  const ctx = String(context || "").trim().toLowerCase();
  const v = String(variant || "").trim().toLowerCase();
  if (v === "room" || ctx === "room") return !!cfg.room_show_sender_every_message;
  if (ctx === "dm") return !!cfg.dm_show_sender_every_message;
  if (ctx === "group") return !!cfg.group_show_sender_every_message;
  return false;
}

function canReuseChatGroup(state, senderKey, tsMs, variant, { senderEveryMessage = false } = {}) {
  if (senderEveryMessage) return false;
  const last = state?.lastGroup;
  if (!last) return false;
  if (last.variant !== variant) return false;
  if (last.senderKey !== senderKey) return false;
  if (last.dateKey !== chatDateKey(tsMs)) return false;
  return Math.abs(tsMs - last.tsMs) <= CHAT_GROUP_WINDOW_MS;
}

function getOrCreateChatGroup(log, senderLabel, tsMs, { variant = "generic", context = null } = {}) {
  const st = ensureChatLogState(log);
  if (!st) return null;
  const senderKey = String(senderLabel || "unknown").trim().toLowerCase() || "unknown";
  const messageContext = context || (variant === "room" ? "room" : ecMessageContextForLog(log));
  const senderEveryMessage = ecShowSenderEveryMessage(messageContext, variant);
  if (canReuseChatGroup(st, senderKey, tsMs, variant, { senderEveryMessage })) {
    st.lastGroup.tsMs = tsMs;
    return st.lastGroup;
  }

  ensureDateSeparatorForLog(log, tsMs);
  const built = makeChatGroupElement(senderLabel, tsMs, { variant });
  if (senderEveryMessage) built.group.classList.add("ec-msgGroup--senderEveryMessage");
  log.appendChild(built.group);

  st.lastGroup = {
    variant,
    messageContext,
    senderEveryMessage,
    senderKey,
    tsMs,
    dateKey: chatDateKey(tsMs),
    el: built.group,
    itemsEl: built.items,
    timeEl: built.timeEl,
  };
  return st.lastGroup;
}

function parseWhoInfo(who) {
  const raw = String(who || "").trim();
  const label = raw.replace(/:\s*$/, "").trim();
  const isSystem = /^system$/i.test(label);
  return { raw, label: isSystem ? "System" : (label || "Unknown"), isSystem };
}

function makeSystemRow(text, tsMs) {
  const row = document.createElement("div");
  row.className = "ec-systemRow";

  const msg = document.createElement("span");
  msg.className = "ec-systemText";
  msg.textContent = String(text || "");

  const tm = document.createElement("span");
  tm.className = "ec-systemTime";
  tm.textContent = formatChatTime(tsMs);
  tm.title = new Date(normalizeChatTs(tsMs)).toLocaleString();

  row.appendChild(msg);
  row.appendChild(tm);
  return row;
}

function makeGlobalAnnouncementRow(payload, tsMs) {
  const row = document.createElement("div");
  row.className = "ec-globalAnnouncementRow";
  row.setAttribute("role", "status");
  row.setAttribute("aria-live", "polite");

  const card = document.createElement("div");
  card.className = "ec-globalAnnouncementCard";

  const badge = document.createElement("div");
  badge.className = "ec-globalAnnouncementBadge";
  badge.setAttribute("aria-hidden", "true");
  badge.textContent = "📣";

  const body = document.createElement("div");
  body.className = "ec-globalAnnouncementBody";

  const title = document.createElement("div");
  title.className = "ec-globalAnnouncementTitle";
  title.textContent = "Server announcement";

  const message = document.createElement("div");
  message.className = "ec-globalAnnouncementText";
  message.textContent = String(payload?.message || "").trim();

  const meta = document.createElement("div");
  meta.className = "ec-globalAnnouncementMeta";
  const actor = String(payload?.actor || "").trim();
  const pieces = [formatChatTime(tsMs)];
  if (actor) pieces.push(`sent by ${actor}`);
  meta.textContent = pieces.join(" • ");

  body.appendChild(title);
  body.appendChild(message);
  body.appendChild(meta);
  card.appendChild(badge);
  card.appendChild(body);
  row.appendChild(card);
  return row;
}

function appendGlobalAnnouncement(viewEl, payload) {
  const log = viewEl?._ym?.log;
  const message = String(payload?.message || "").trim();
  if (!log || !message) return null;
  const tsMs = normalizeChatTs(payload?.created_at || payload?.timestamp || payload?.ts);
  ensureDateSeparatorForLog(log, tsMs);
  const row = makeGlobalAnnouncementRow(payload, tsMs);
  log.appendChild(row);
  const st = ensureChatLogState(log);
  if (st) st.lastGroup = null;
  scheduleScrollLogToBottom(log);
  return row;
}

function ecExtractSingleUrl(text) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  const match = raw.match(/https?:\/\/[^\s<>"]+/i);
  if (!match) return '';
  const only = match[0] === raw || raw === `<${match[0]}>`;
  return only ? match[0] : '';
}

function ecParseSharedMediaUrl(text) {
  const rawUrl = ecExtractSingleUrl(text);
  if (!rawUrl) return null;
  const safeRawUrl = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(rawUrl, { allowRelative: false, allowExternal: true })
    : (/^https?:\/\//i.test(rawUrl) ? rawUrl : '');
  if (!safeRawUrl) return null;
  let url;
  try {
    url = new URL(safeRawUrl, window.location.origin);
  } catch {
    return null;
  }
  const host = url.hostname.replace(/^www\./i, '').toLowerCase();
  const lowerPath = `${url.pathname}${url.search}`.toLowerCase();
  const out = {
    url: url.toString(),
    host,
    label: host,
    kind: 'link',
    previewKind: '',
    embedUrl: '',
    openLabel: 'Open link',
  };
  if (/\.(mp4|webm|mov|m4v)(\?|#|$)/i.test(lowerPath)) {
    out.kind = 'video';
    out.previewKind = 'video';
    out.label = 'Direct video';
    return out;
  }
  if (/\.(mp3|wav|ogg|aac|m4a|flac)(\?|#|$)/i.test(lowerPath)) {
    out.kind = 'audio';
    out.previewKind = 'audio';
    out.label = 'Direct audio';
    return out;
  }
  if (host.includes('youtube.com') || host === 'youtu.be') {
    let videoId = '';
    if (host === 'youtu.be') videoId = url.pathname.replace(/^\//, '').split('/')[0];
    if (!videoId) videoId = url.searchParams.get('v') || '';
    if (!videoId && /\/shorts\//.test(url.pathname)) videoId = url.pathname.split('/shorts/')[1]?.split('/')[0] || '';
    if (!videoId && /\/embed\//.test(url.pathname)) videoId = url.pathname.split('/embed/')[1]?.split('/')[0] || '';
    if (videoId) {
      out.kind = 'video';
      out.previewKind = 'embed';
      out.label = 'YouTube';
      const embedUrl = new URL(`https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}`);
      embedUrl.searchParams.set('autoplay', '1');
      embedUrl.searchParams.set('playsinline', '1');
      embedUrl.searchParams.set('rel', '0');
      embedUrl.searchParams.set('enablejsapi', '1');
      try {
        if (window.location?.origin) embedUrl.searchParams.set('origin', window.location.origin);
      } catch {}
      out.embedUrl = embedUrl.toString();
      out.openLabel = 'Watch';
      return out;
    }
  }
  if (host.includes('vimeo.com')) {
    const idMatch = url.pathname.match(/\/(\d+)(?:$|\/|\?)/);
    if (idMatch) {
      out.kind = 'video';
      out.previewKind = 'embed';
      out.label = 'Vimeo';
      out.embedUrl = `https://player.vimeo.com/video/${encodeURIComponent(idMatch[1])}`;
      out.openLabel = 'Watch';
      return out;
    }
  }
  if (host.includes('iheart.com')) {
    out.kind = 'audio';
    out.previewKind = 'embed';
    out.label = 'iHeartRadio';
    const embedUrl = new URL(url.toString());
    if (!embedUrl.searchParams.has('embed')) embedUrl.searchParams.set('embed', 'true');
    out.embedUrl = embedUrl.toString();
    out.openLabel = 'Listen';
    return out;
  }
  if (lowerPath.includes('/embed/video')) {
    out.kind = 'video';
    out.previewKind = 'embed';
    out.label = 'Embedded stream';
    out.embedUrl = url.toString();
    out.openLabel = 'Watch';
    return out;
  }
  return null;
}

function ecEnsureSharedMediaPreviewModal() {
  let root = document.getElementById('ecSharedMediaPreviewModal');
  if (root) return root;
  root = document.createElement('div');
  root.id = 'ecSharedMediaPreviewModal';
  root.className = 'ecSharedMediaPreviewModal hidden';

  const backdrop = document.createElement('div');
  backdrop.className = 'ecSharedMediaPreviewBackdrop';
  backdrop.dataset.ecMediaClose = '1';

  const card = document.createElement('div');
  card.className = 'ecSharedMediaPreviewCard';
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  card.setAttribute('aria-label', 'Media preview');

  const head = document.createElement('div');
  head.className = 'ecSharedMediaPreviewHead';

  const titleWrap = document.createElement('div');
  const title = document.createElement('div');
  title.id = 'ecSharedMediaPreviewTitle';
  title.className = 'ecSharedMediaPreviewTitle';
  title.textContent = 'Media preview';
  const meta = document.createElement('div');
  meta.id = 'ecSharedMediaPreviewMeta';
  meta.className = 'ecSharedMediaPreviewMeta muted';
  titleWrap.appendChild(title);
  titleWrap.appendChild(meta);

  const actions = document.createElement('div');
  actions.className = 'ecSharedMediaPreviewHeadActions';
  [
    ['–', 'ecMediaMin', 'Minimize media preview'],
    ['↗', 'ecMediaPopout', 'Pop out media preview'],
    ['✕', 'ecMediaClose', 'Close media preview'],
  ].forEach(([label, dataKey, aria]) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dockMiniPopupClose';
    btn.textContent = label;
    btn.setAttribute('aria-label', aria);
    btn.dataset[dataKey] = '1';
    actions.appendChild(btn);
  });

  const body = document.createElement('div');
  body.id = 'ecSharedMediaPreviewBody';
  body.className = 'ecSharedMediaPreviewBody';

  head.appendChild(titleWrap);
  head.appendChild(actions);
  card.appendChild(head);
  card.appendChild(body);
  root.appendChild(backdrop);
  root.appendChild(card);

  root.addEventListener('click', (ev) => {
    const target = ev.target;
    if (!target || !target.getAttribute) return;
    if (target.getAttribute('data-ec-media-close') === '1') {
      ecCloseSharedMediaPreview();
      return;
    }
    if (target.getAttribute('data-ec-media-min') === '1') {
      ecToggleSharedMediaPreviewMinimized();
      return;
    }
    if (target.getAttribute('data-ec-media-popout') === '1') {
      ecPopoutSharedMediaPreview();
    }
  });
  document.body.appendChild(root);
  return root;
}

function ecToggleSharedMediaPreviewMinimized(force) {
  const root = document.getElementById('ecSharedMediaPreviewModal');
  if (!root) return;
  const next = typeof force === 'boolean' ? force : !root.classList.contains('is-minimized');
  root.classList.toggle('is-minimized', next);
  const btn = root.querySelector('[data-ec-media-min="1"]');
  if (btn) {
    btn.textContent = next ? '▢' : '–';
    btn.setAttribute('aria-label', next ? 'Restore media preview' : 'Minimize media preview');
    btn.title = next ? 'Restore' : 'Minimize';
  }
}

function ecPopoutSharedMediaPreview() {
  const root = document.getElementById('ecSharedMediaPreviewModal');
  const href = String(root?._ecMediaMeta?.url || '').trim();
  if (!href) return;
  if (typeof ecOpenSafeUrl === 'function') {
    ecOpenSafeUrl(href, { features: 'noopener,noreferrer,width=1180,height=760' });
    return;
  }
  try { window.open(href, '_blank', 'noopener,noreferrer,width=1180,height=760'); } catch { window.open(href, '_blank', 'noopener,noreferrer'); }
}

function ecCloseSharedMediaPreview() {
  const root = document.getElementById('ecSharedMediaPreviewModal');
  if (!root) return;
  root.classList.add('hidden');
  root.classList.remove('is-minimized');
  root._ecMediaMeta = null;
  const body = document.getElementById('ecSharedMediaPreviewBody');
  if (body) {
    if (typeof ecClearNode === 'function') ecClearNode(body);
    else body.replaceChildren();
  }
  const btn = root.querySelector('[data-ec-media-min="1"]');
  if (btn) {
    btn.textContent = '–';
    btn.setAttribute('aria-label', 'Minimize media preview');
    btn.title = 'Minimize';
  }
}

function ecOpenSharedMediaPreview(meta) {
  const media = ecParseSharedMediaUrl(meta?.url || '');
  if (!media) return;
  const root = ecEnsureSharedMediaPreviewModal();
  const title = document.getElementById('ecSharedMediaPreviewTitle');
  const sub = document.getElementById('ecSharedMediaPreviewMeta');
  const body = document.getElementById('ecSharedMediaPreviewBody');
  if (!body) return;
  root._ecMediaMeta = media;
  ecToggleSharedMediaPreviewMinimized(false);
  if (title) title.textContent = media.label || 'Media preview';
  if (sub) sub.textContent = media.url.replace(/^https?:\/\//i, '');
  if (typeof ecClearNode === 'function') ecClearNode(body);
  else body.replaceChildren();

  const hint = document.createElement('div');
  hint.className = 'ecSharedMediaEmbedHint muted';
  if (media.label === 'YouTube') {
    hint.textContent = 'Embedded YouTube playback works for videos that allow embedding. If this one is blocked, use Pop out or Watch.';
  } else if (media.previewKind === 'embed') {
    hint.textContent = 'If this provider blocks embedded playback for this item, use Pop out or Open.';
  }
  if (hint.textContent) body.appendChild(hint);

  if (media.previewKind === 'video') {
    const video = document.createElement('video');
    video.className = 'ecSharedMediaPlayer';
    video.controls = true;
    video.preload = 'metadata';
    video.src = media.url;
    body.appendChild(video);
  } else if (media.previewKind === 'audio') {
    const audio = document.createElement('audio');
    audio.className = 'ecSharedMediaAudio';
    audio.controls = true;
    audio.preload = 'metadata';
    audio.src = media.url;
    body.appendChild(audio);
  } else if (media.previewKind === 'embed' && media.embedUrl) {
    const frame = document.createElement('iframe');
    frame.className = 'ecSharedMediaFrame';
    frame.loading = 'lazy';
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    frame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen';
    frame.allowFullscreen = true;
    frame.setAttribute('allowfullscreen', '');
    frame.src = media.embedUrl;
    body.appendChild(frame);
  }
  root.classList.remove('hidden');
}

function ecBuildSharedMediaCard(meta) {
  const wrap = document.createElement('div');
  wrap.className = 'ecSharedMediaCard';

  const top = document.createElement('div');
  top.className = 'ecSharedMediaCardTop';

  const icon = document.createElement('div');
  icon.className = 'ecSharedMediaIcon';
  icon.textContent = meta.kind === 'audio' ? '🎵' : '▶';

  const text = document.createElement('div');
  text.className = 'ecSharedMediaText';
  const label = document.createElement('div');
  label.className = 'ecSharedMediaLabel';
  label.textContent = meta.label || 'Media link';
  const urlLine = document.createElement('div');
  urlLine.className = 'ecSharedMediaUrl';
  urlLine.textContent = String(meta.url || '').replace(/^https?:\/\//i, '');
  text.appendChild(label);
  text.appendChild(urlLine);
  top.appendChild(icon);
  top.appendChild(text);

  const actions = document.createElement('div');
  actions.className = 'ecSharedMediaActions';

  if (meta.previewKind) {
    const previewBtn = document.createElement('button');
    previewBtn.type = 'button';
    previewBtn.className = 'miniBtn';
    previewBtn.textContent = meta.kind === 'audio' ? 'Listen here' : 'Preview here';
    previewBtn.addEventListener('click', () => ecOpenSharedMediaPreview(meta));
    actions.appendChild(previewBtn);
  }
  const openBtn = document.createElement('button');
  openBtn.type = 'button';
  openBtn.className = 'miniBtn';
  openBtn.textContent = meta.openLabel || 'Open';
  openBtn.addEventListener('click', () => {
    if (typeof ecOpenSafeUrl === 'function') ecOpenSafeUrl(meta.url, { allowRelative: false, allowExternal: true });
    else window.open(meta.url, '_blank', 'noopener,noreferrer');
  });
  actions.appendChild(openBtn);

  wrap.appendChild(top);
  wrap.appendChild(actions);
  return wrap;
}


function ecSplitTrailingUrlPunctuation(rawUrl) {
  let url = String(rawUrl || '');
  let tail = '';
  while (url && /[.,!?;:]$/.test(url)) {
    tail = url.slice(-1) + tail;
    url = url.slice(0, -1);
  }
  // For common sentence punctuation, do not include a closing paren/bracket
  // unless the URL visibly contains its matching opener.
  const pairs = [[")", "("], ["]", "["], ["}", "{"]];
  let changed = true;
  while (changed && url) {
    changed = false;
    for (const [close, open] of pairs) {
      if (!url.endsWith(close)) continue;
      const opens = Array.from(url).filter((ch) => ch === open).length;
      const closes = Array.from(url).filter((ch) => ch === close).length;
      if (closes > opens) {
        tail = close + tail;
        url = url.slice(0, -1);
        changed = true;
      }
    }
  }
  return { url, tail };
}

function ecNormalizeChatLink(rawUrl) {
  let raw = String(rawUrl || '').trim();
  if (!raw) return '';
  if (/^www\./i.test(raw)) raw = `https://${raw}`;
  if (typeof ecNormalizeSafeUrl === 'function') {
    return ecNormalizeSafeUrl(raw, { allowRelative: false, allowExternal: true });
  }
  return /^https?:\/\//i.test(raw) ? raw : '';
}

function ecAppendChatTextSegment(container, text, emoticonState = null) {
  if (!container) return;
  if (typeof window.ecAppendCodeEmoticons === 'function') {
    try {
      window.ecAppendCodeEmoticons(container, text, emoticonState ? { state: emoticonState } : {});
      return;
    } catch {}
  }
  container.appendChild(document.createTextNode(String(text ?? '')));
}

function ecAppendLinkifiedText(container, rawText) {
  const text = String(rawText ?? '');
  const emoticonState = (typeof window.ecMakeEmoticonRenderState === 'function') ? window.ecMakeEmoticonRenderState() : null;
  const re = /(?:https?:\/\/|www\.)[^\s<>"']+/ig;
  let last = 0;
  let found = false;
  for (const match of text.matchAll(re)) {
    const start = match.index || 0;
    const rawMatch = String(match[0] || '');
    if (start > last) ecAppendChatTextSegment(container, text.slice(last, start), emoticonState);
    const { url, tail } = ecSplitTrailingUrlPunctuation(rawMatch);
    const href = ecNormalizeChatLink(url);
    if (href) {
      const a = document.createElement('a');
      a.className = 'ec-chatLink';
      a.href = href;
      a.target = '_blank';
      a.rel = 'noopener noreferrer nofollow ugc';
      a.textContent = url;
      a.title = href;
      container.appendChild(a);
      found = true;
    } else {
      container.appendChild(document.createTextNode(url));
    }
    if (tail) container.appendChild(document.createTextNode(tail));
    last = start + rawMatch.length;
  }
  if (last < text.length) ecAppendChatTextSegment(container, text.slice(last), emoticonState);
  return found;
}


function ecStyledTextSafeFont(value) {
  const allowed = ["Arial", "Verdana", "Tahoma", "Times New Roman", "Georgia", "Courier New", "Trebuchet MS"];
  const raw = String(value || "").trim();
  return allowed.find((f) => f.toLowerCase() === raw.toLowerCase()) || "Arial";
}

function ecStyledTextSafeColor(value) {
  const raw = String(value || "").trim();
  return /^#[0-9a-f]{6}$/i.test(raw) ? raw.toLowerCase() : "#111111";
}

function ecBuildStyledTextMessageBody(obj) {
  const body = document.createElement("div");
  body.className = "ec-msgText ec-msgText--styled";
  const text = ecStyledTextInnerValue(obj);
  const style = (obj && typeof obj.style === "object") ? obj.style : {};
  const size = (typeof clampInt === "function") ? clampInt(style.size, 10, 22, 13) : Math.max(10, Math.min(22, parseInt(style.size || 13, 10) || 13));
  body.style.setProperty("--ec-msg-font-family", `"${ecStyledTextSafeFont(style.font).replace(/"/g, "")}"`);
  body.style.setProperty("--ec-msg-font-size", `${size}px`);
  body.style.setProperty("--ec-msg-color", ecStyledTextSafeColor(style.color));
  body.style.setProperty("--ec-msg-font-weight", style.bold ? "700" : "400");
  body.style.setProperty("--ec-msg-font-style", style.italic ? "italic" : "normal");
  body.style.setProperty("--ec-msg-text-decoration", style.underline ? "underline" : "none");
  ecAppendLinkifiedText(body, text);
  return body;
}

function ecTryParseChatWireObject(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) return value;
  if (typeof value !== "string") return null;
  const text = value.trimStart();
  if (!text.startsWith("{")) return null;
  try {
    const obj = JSON.parse(text);
    return (obj && typeof obj === "object" && !Array.isArray(obj)) ? obj : null;
  } catch {
    return null;
  }
}

function ecTryGetStyledTextObject(rawMessage) {
  const obj = ecTryParseChatWireObject(rawMessage);
  return (obj && String(obj._ec || "").trim().toLowerCase() === "styled_text") ? obj : null;
}

function ecStyledTextInnerValue(obj) {
  if (!obj || typeof obj !== "object") return "";
  const inner = obj.text;
  if (inner === null || inner === undefined) return "";
  if (typeof inner === "string") return inner;
  if (inner && typeof inner === "object") {
    try { return JSON.stringify(inner); } catch {}
  }
  return String(inner);
}

function ecBuildGifMessageBody(gifUrl, { autoScrollLog = null } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "ym-gifWrap is-loading";

  const placeholder = document.createElement("div");
  placeholder.className = "ym-gifPlaceholder";
  placeholder.textContent = "Loading GIF…";
  placeholder.setAttribute('aria-live', 'polite');
  placeholder.setAttribute('aria-busy', 'true');

  const img = document.createElement("img");
  configureGifInlineImage(img, gifUrl, { wrap, placeholder });
  if (autoScrollLog) img._ecScrollLog = autoScrollLog;

  wrap.appendChild(placeholder);
  wrap.appendChild(img);
  return wrap;
}

function ecTryParseRoomRadioWireObject(message) {
  const obj = ecTryParseChatWireObject(message);
  if (!obj) return null;
  if (String(obj._ec || obj.kind || obj.type || "").trim().toLowerCase() !== "room_radio") return null;
  return obj;
}

function ecBuildRoomRadioWireCard(obj, { username = "" } = {}) {
  let station = null;
  try {
    if (typeof roomMediaHandleWire === "function") station = roomMediaHandleWire(obj);
  } catch {}
  const card = document.createElement('div');
  card.className = 'ecRoomRadioWireCard';
  const title = document.createElement('div');
  title.className = 'ecRoomRadioWireTitle';
  title.textContent = '🎵 Shared radio updated';
  const meta = document.createElement('div');
  meta.className = 'ecRoomRadioWireMeta';
  meta.textContent = `${String(obj?.actor || username || 'Someone')} switched this room to ${String(station?.label || obj?.label || obj?.name || 'a new station')}.`;
  card.appendChild(title);
  card.appendChild(meta);
  return card;
}

function ecTryBuildWireSpecialMessageBody(message, { autoScrollLog = null, username = "" } = {}) {
  const special = (typeof ecBuildSpecialMessageBody === "function") ? ecBuildSpecialMessageBody(message) : null;
  if (special) return special;

  const radio = ecTryParseRoomRadioWireObject(message);
  if (radio) return ecBuildRoomRadioWireCard(radio, { username });

  const rawText = (typeof message === "string") ? message : "";
  const gifUrl = rawText ? parseGifMarker(rawText) : null;
  if (gifUrl) return ecBuildGifMessageBody(gifUrl, { autoScrollLog });

  const media = rawText ? ecParseSharedMediaUrl(rawText) : null;
  if (media) return ecBuildSharedMediaCard(media);

  return null;
}

function ecTryBuildStyledTextMessageBody(rawMessage, { autoScrollLog = null, username = "" } = {}) {
  const obj = ecTryGetStyledTextObject(rawMessage);
  if (!obj) return null;

  // Backward compatibility and safety: if an older composer wrapped a media/control
  // wire payload in styled_text, render the intended card instead of raw JSON/text.
  const nestedSpecial = ecTryBuildWireSpecialMessageBody(ecStyledTextInnerValue(obj), { autoScrollLog, username });
  if (nestedSpecial) return nestedSpecial;

  return ecBuildStyledTextMessageBody(obj);
}

function ecClassifyChatMessageKind(message, depth = 0) {
  if (depth > 4) return "text";
  const styledObj = ecTryGetStyledTextObject(message);
  if (styledObj) {
    const innerKind = ecClassifyChatMessageKind(ecStyledTextInnerValue(styledObj), depth + 1);
    return innerKind && innerKind !== "text" ? innerKind : "styled";
  }

  try {
    if (typeof ecTryNormalizeTorrentMessage === "function" && ecTryNormalizeTorrentMessage(message)) return "torrent";
  } catch {}

  if (ecTryParseRoomRadioWireObject(message)) return "room-radio";

  if (typeof message === "string") {
    if (parseGifMarker(message)) return "gif";
    if (ecParseSharedMediaUrl(message)) return "media";
  }

  return "text";
}

function buildTextMessageBody(text, { autoScrollLog = null, username = "" } = {}) {
  const special = ecTryBuildWireSpecialMessageBody(text, { autoScrollLog, username });
  if (special) return special;

  const styled = ecTryBuildStyledTextMessageBody(text, { autoScrollLog, username });
  if (styled) return styled;

  const body = document.createElement("div");
  body.className = "ec-msgText";
  const rawText = (typeof text === "string") ? text : String(text ?? "");
  ecAppendLinkifiedText(body, rawText);
  return body;
}

function ecBuildRoomMessageBody(message, { autoScrollLog = null, username = "" } = {}) {
  return buildTextMessageBody(message, { autoScrollLog, username });
}

function ecMessageContextForLog(log) {
  try {
    const win = log?.closest?.('.ym-window');
    const kind = String(win?.dataset?.kind || '').trim().toLowerCase();
    if (kind === 'dm') return 'dm';
    if (kind === 'group') return 'group';
  } catch {}
  return 'generic';
}

function appendGenericMessageItem(log, who, contentEl, { ts = null, kind = "text", context = null } = {}) {
  if (!log) return null;
  const meta = parseWhoInfo(who);
  const tsMs = normalizeChatTs(ts);

  if (meta.isSystem) {
    ensureDateSeparatorForLog(log, tsMs);
    const row = makeSystemRow(contentEl?.textContent ?? contentEl, tsMs);
    log.appendChild(row);
    const st = ensureChatLogState(log);
    if (st) st.lastGroup = null;
    return row;
  }

  const msgContext = context || ecMessageContextForLog(log);
  const group = getOrCreateChatGroup(log, meta.label, tsMs, { variant: "generic", context: msgContext });
  if (!group?.itemsEl) return null;

  const item = document.createElement("div");
  item.className = `ec-msgItem ec-msgItem--${kind}`;
  if (contentEl instanceof Node) item.appendChild(contentEl);
  else item.textContent = String(contentEl ?? "");
  group.itemsEl.appendChild(item);
  try { window.ecAnimateMessageOnce?.(item, msgContext); } catch {}
  return item;
}

function appendLine(winEl, who, text, kind = "msg", opts = {}) {
  if (kind && typeof kind === "object" && !Array.isArray(kind)) {
    opts = kind;
    kind = "msg";
  }
  const log = winEl._ym?.log;
  if (!log) return;

  const body = buildTextMessageBody(text, { autoScrollLog: log });
  const msgKind = (typeof ecClassifyChatMessageKind === "function") ? ecClassifyChatMessageKind(text) : (parseGifMarker(text) ? "gif" : "text");
  appendGenericMessageItem(log, who, body, { ts: opts?.ts, kind: msgKind, context: opts?.context || null });
  scheduleScrollLogToBottom(log);
}
