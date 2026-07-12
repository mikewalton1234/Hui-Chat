const EC_PROFILE_POST_ACTION_PENDING = new Set();
const EC_PROFILE_COMMENT_ACTION_PENDING = new Set();

function _profilePendingKey(...parts) {
  return parts.map((part) => String(part ?? '').trim().toLowerCase()).join(':');
}

function _profileIsPending(set, key) {
  return !!(set && key && set.has(key));
}

function _profileSetButtonBusy(btn, busy, busyText = 'Working…') {
  if (!btn) return;
  if (busy) {
    if (!btn.dataset.idleLabel) btn.dataset.idleLabel = btn.textContent || '';
    btn.disabled = true;
    btn.classList.add('isBusy');
    btn.setAttribute('aria-busy', 'true');
    if (busyText) btn.textContent = busyText;
    return;
  }
  btn.disabled = false;
  btn.classList.remove('isBusy');
  btn.setAttribute('aria-busy', 'false');
  if (btn.dataset.idleLabel) btn.textContent = btn.dataset.idleLabel;
}

function _profileRevokeObjectUrl(url) {
  const value = String(url || '').trim();
  if (!value || !value.startsWith('blob:') || typeof URL === 'undefined' || typeof URL.revokeObjectURL !== 'function') return;
  try { URL.revokeObjectURL(value); } catch {}
}

function _profileSafeObjectUrl(file, state, key) {
  if (!file || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return '';
  if (state && key) {
    _profileRevokeObjectUrl(state[key]);
    state[key] = '';
  }
  let nextUrl = '';
  try { nextUrl = URL.createObjectURL(file); } catch { nextUrl = ''; }
  if (state && key) state[key] = nextUrl;
  return nextUrl;
}

function _profileValidateOptionalLink(raw, label = 'link') {
  const value = String(raw || '').trim();
  if (!value) return { ok: true, url: '' };
  const safe = ecNormalizeSafeUrl(value, { allowRelative: false, allowExternal: true });
  if (!safe) return { ok: false, url: '', error: `Use a valid http/https ${label}.` };
  return { ok: true, url: safe };
}

function _profileDefaultPostVisibility(profile = {}) {
  return _profilePostVisibilityValue(profile?.profile_post_default_visibility || 'friends');
}

async function _profileLimitPostEmoticons(text, opts = {}) {
  if (typeof ecLimitOutgoingChatEmoticons !== 'function') return { text: String(text || ''), removed: 0 };
  try {
    return await ecLimitOutgoingChatEmoticons(String(text || ''), { surface: 'profile post', notify: opts.notify !== false });
  } catch {
    return { text: String(text || ''), removed: 0 };
  }
}

function _profileWindowKey(username) {
  const raw = String(username || '').trim();
  const key = (typeof ecNormalizeUsernameKey === 'function') ? ecNormalizeUsernameKey(raw) : raw.toLowerCase();
  return key || raw;
}

function _profileReplaceTextBlock(container, className, text) {
  if (!container) return null;
  _profileClearNode(container);
  const block = document.createElement('div');
  block.className = className || 'ecProfileMutedBlock';
  block.textContent = String(text || '');
  container.appendChild(block);
  return block;
}

function _clampProfileWindowIntoViewport(win) {
  if (!win) return;
  const width = Math.max(320, Math.round(parseFloat(win.style.width) || win.offsetWidth || 0));
  const height = Math.max(280, Math.round(parseFloat(win.style.height) || win.offsetHeight || 0));
  const maxLeft = Math.max(10, window.innerWidth - width - 12);
  const maxTop = Math.max(10, window.innerHeight - height - 12);
  const left = Math.min(Math.max(10, Math.round(parseFloat(win.style.left) || 10)), maxLeft);
  const top = Math.min(Math.max(10, Math.round(parseFloat(win.style.top) || 10)), maxTop);
  win.style.left = `${left}px`;
  win.style.top = `${top}px`;
}

function _fitProfileWindow(win, mode = 'public') {
  if (!win) return;
  if (win.classList && win.classList.contains('is-profile-fullscreen')) return;
  const viewportWidth = Math.max(360, window.innerWidth || 0);
  const viewportHeight = Math.max(360, window.innerHeight || 0);
  const usableRight = Math.max(360, (typeof _profileUsableRightEdge === 'function') ? _profileUsableRightEdge() : viewportWidth);
  const horizontalMargin = 18;
  const verticalMargin = 72;
  const maxWidth = Math.max(520, Math.min(viewportWidth - horizontalMargin * 2, usableRight - horizontalMargin * 2));
  const maxHeight = Math.max(440, viewportHeight - verticalMargin);
  const minGoodWidth = mode === 'editor' ? 980 : 1080;
  const fillWidth = mode === 'editor' ? usableRight * 0.78 : usableRight * 0.84;
  const desiredWidth = Math.round(Math.min(maxWidth, Math.max(minGoodWidth, fillWidth)));
  const desiredHeight = mode === 'editor'
    ? Math.round(Math.min(maxHeight, Math.max(600, viewportHeight * 0.74)))
    : Math.round(Math.min(maxHeight, Math.max(640, viewportHeight * 0.76)));
  win.style.width = `${desiredWidth}px`;
  win.style.height = `${desiredHeight}px`;
  win.style.maxWidth = `${maxWidth}px`;
  win.style.maxHeight = `${maxHeight}px`;
  win.style.minWidth = `${Math.min(maxWidth, mode === 'editor' ? 920 : 960)}px`;
  win.style.minHeight = `${Math.min(maxHeight, mode === 'editor' ? 560 : 580)}px`;
  _positionProfileWindowNearTop(win);
}

function _profileUsableRightEdge() {
  const viewportWidth = Math.max(360, window.innerWidth || 0);
  try {
    const root = document.getElementById('appRoot');
    const dock = document.getElementById('ecDock');
    const dockVisible = dock && (!root || !root.classList || !root.classList.contains('is-hub-collapsed'));
    if (dockVisible) {
      const dockWidth = Math.max(0, Math.round(dock.getBoundingClientRect?.().width || dock.offsetWidth || 0));
      if (dockWidth > 0 && dockWidth < viewportWidth * 0.45) return viewportWidth - dockWidth;
    }
  } catch {}
  return viewportWidth;
}

function _positionProfileWindowNearTop(win) {
  if (!win || (win.classList && win.classList.contains('is-profile-fullscreen'))) return;
  const width = Math.max(320, Math.round(parseFloat(win.style.width) || win.offsetWidth || 0));
  const height = Math.max(280, Math.round(parseFloat(win.style.height) || win.offsetHeight || 0));
  const usableRight = Math.max(360, _profileUsableRightEdge());
  const left = Math.max(10, Math.min(Math.max(10, usableRight - width - 10), Math.round((usableRight - width) / 2)));
  const maxTop = Math.max(10, (window.innerHeight || 0) - height - 12);
  win.style.left = `${left}px`;
  win.style.top = `${Math.min(10, maxTop)}px`;
}

function _profileGetScrollHost(target) {
  if (!target) return null;
  if (target._ym && target._ym.log) return target._ym.log;
  if (target.classList && target.classList.contains('ym-log')) return target;
  if (typeof target.querySelector === 'function') return target.querySelector('.ym-log') || target;
  return target;
}

function _profileForceTopScroll(target) {
  const log = _profileGetScrollHost(target);
  if (!log) return;
  const reset = () => {
    try { log.style.scrollBehavior = 'auto'; } catch {}
    try { log.style.overflowAnchor = 'none'; } catch {}
    try { log.scrollTop = 0; } catch {}
    try { log.scrollTo({ top: 0, left: 0, behavior: 'instant' }); } catch {
      try { log.scrollTo(0, 0); } catch {}
    }
    try {
      log.querySelectorAll?.('.ecProfilePageCard, .ecProfileCard, [data-profile-feed-root], [data-profile-photos-root]').forEach((node) => {
        if (!node || typeof node.scrollTop !== 'number') return;
        try { node.style.scrollBehavior = 'auto'; } catch {}
        try { node.style.overflowAnchor = 'none'; } catch {}
        node.scrollTop = 0;
      });
    } catch {}
  };

  reset();
  try { requestAnimationFrame(reset); } catch { try { queueMicrotask(reset); } catch {} }
}

function _profileStartTopLock(win) {
  if (!win) return 0;
  const token = (Number(win.__ecProfileTopLockToken || 0) || 0) + 1;
  win.__ecProfileTopLockToken = token;
  try { win.classList.add('is-profile-toplock'); } catch {}
  _profileForceTopScroll(win);
  return token;
}

function _profileReleaseTopLock(win, token, delayMs = 0) {
  if (!win) return;
  const release = () => {
    if (token && Number(win.__ecProfileTopLockToken || 0) !== Number(token)) return;
    _profileForceTopScroll(win);
    const log = _profileGetScrollHost(win);
    const endAt = (typeof performance !== 'undefined' ? performance.now() : Date.now()) + 260;
    const settle = () => {
      if (token && Number(win.__ecProfileTopLockToken || 0) !== Number(token)) return;
      _profileForceTopScroll(log || win);
      const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      if (now < endAt) {
        try { requestAnimationFrame(settle); } catch { setTimeout(settle, 16); }
        return;
      }
      try { win.classList.remove('is-profile-toplock'); } catch {}
      if (token && Number(win.__ecProfileTopLockToken || 0) === Number(token)) win.__ecProfileTopLockToken = 0;
      _profileForceTopScroll(log || win);
    };
    try { requestAnimationFrame(settle); } catch { setTimeout(settle, 0); }
  };
  if (delayMs > 0) setTimeout(release, delayMs);
  else release();
}

function _profileExpandedAppBounds() {
  const fallbackWidth = Math.max(360, window.innerWidth || 0);
  const fallbackHeight = Math.max(360, window.innerHeight || 0);
  let left = 8;
  let top = 8;
  let width = Math.max(360, fallbackWidth - 16);
  let height = Math.max(360, fallbackHeight - 16);

  try {
    const site = document.getElementById('siteArea');
    const rect = site && site.getBoundingClientRect ? site.getBoundingClientRect() : null;
    if (rect && rect.width > 360 && rect.height > 360) {
      const margin = 8;
      left = Math.max(0, Math.round(rect.left + margin));
      top = Math.max(0, Math.round(rect.top + margin));
      width = Math.max(360, Math.round(rect.width - margin * 2));
      height = Math.max(360, Math.round(rect.height - margin * 2));
    } else {
      const usableRight = Math.max(360, (typeof _profileUsableRightEdge === 'function') ? _profileUsableRightEdge() : fallbackWidth);
      width = Math.max(360, Math.round(usableRight - 16));
    }
  } catch {}

  return { left, top, width, height };
}

function _profileSetFullscreen(win, fullscreen) {
  if (!win) return;
  const btn = win.querySelector('[data-profile-fullscreen-btn]');
  if (fullscreen) {
    if (!win.__ecProfileRestoreState) {
      win.__ecProfileRestoreState = {
        left: win.style.left || '',
        top: win.style.top || '',
        width: win.style.width || '',
        height: win.style.height || '',
        maxWidth: win.style.maxWidth || '',
        maxHeight: win.style.maxHeight || '',
      };
    }
    const bounds = _profileExpandedAppBounds();
    win.classList.add('is-profile-fullscreen');
    win.style.left = `${bounds.left}px`;
    win.style.top = `${bounds.top}px`;
    win.style.width = `${bounds.width}px`;
    win.style.height = `${bounds.height}px`;
    win.style.maxWidth = 'none';
    win.style.maxHeight = 'none';
    try { bringToFront(win); } catch {}
  } else {
    const restore = win.__ecProfileRestoreState || {};
    win.classList.remove('is-profile-fullscreen');
    win.style.left = restore.left || win.style.left || '10px';
    win.style.top = restore.top || '10px';
    win.style.width = restore.width || win.style.width || '';
    win.style.height = restore.height || win.style.height || '';
    win.style.maxWidth = restore.maxWidth || '';
    win.style.maxHeight = restore.maxHeight || '';
    win.__ecProfileRestoreState = null;
    _positionProfileWindowNearTop(win);
  }
  if (btn) {
    const isFull = win.classList.contains('is-profile-fullscreen');
    btn.setAttribute('aria-pressed', isFull ? 'true' : 'false');
    btn.title = isFull ? 'Restore profile window' : 'Full screen profile';
    btn.setAttribute('aria-label', isFull ? 'Restore profile window' : 'Full screen profile');
    btn.textContent = isFull ? '❐' : '□';
  }
}

function _ensureProfileFullscreenButton(win) {
  if (!win) return null;
  const btns = win.querySelector('.ym-winBtns');
  if (!btns) return null;
  let btn = btns.querySelector('[data-profile-fullscreen-btn]');
  if (btn) return btn;
  btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'winBtn ecProfileFullscreenBtn';
  btn.dataset.profileFullscreenBtn = '1';
  btn.title = 'Full screen profile';
  btn.setAttribute('aria-label', 'Full screen profile');
  btn.setAttribute('aria-pressed', 'false');
  btn.textContent = '□';
  btn.addEventListener('mousedown', (event) => {
    event.preventDefault();
    event.stopPropagation();
  });
  btn.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    _profileSetFullscreen(win, !(win.classList && win.classList.contains('is-profile-fullscreen')));
  });
  const closeBtn = btns.querySelector('.winBtn.danger');
  if (closeBtn) btns.insertBefore(btn, closeBtn);
  else btns.appendChild(btn);
  return btn;
}

try {
  if (!window.__ecProfileFullscreenResizeBound) {
    window.__ecProfileFullscreenResizeBound = true;
    window.addEventListener('resize', () => {
      try {
        document.querySelectorAll('.ecProfileWindow.is-profile-fullscreen').forEach((win) => _profileSetFullscreen(win, true));
      } catch {}
    });
  }
} catch {}

function openMyProfileEditor() {
  openProfileWindow(currentUser, { fitMode: 'editor', ownerEditSurface: true });
}

function _fmtLocalTime(ts) {
  try {
    if (!ts) return '';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return String(ts);
    return d.toLocaleString();
  } catch {
    return ts ? String(ts) : '';
  }
}

function _profileHeroStyle(profile) {
  const bannerCssUrl = ecCssUrl(profile?.banner_url || '', { allowRelative: true, allowExternal: true });
  const accent = /^#[0-9a-f]{6}$/i.test(String(profile?.profile_accent || '').trim())
    ? String(profile.profile_accent).trim()
    : '#6f7cff';
  if (bannerCssUrl) {
    return `background-image:linear-gradient(180deg, rgba(15,23,42,.18), rgba(15,23,42,.74)),${bannerCssUrl};`;
  }
  return `background-image:linear-gradient(135deg, ${escapeHtml(accent)}, rgba(15,23,42,.84));`;
}

function _profileToneStyle(profile) {
  const accent = /^#[0-9a-f]{6}$/i.test(String(profile?.profile_accent || '').trim())
    ? String(profile.profile_accent).trim()
    : '#6f7cff';
  return `--ec-profile-accent:${escapeHtml(accent)};`;
}

function _profileBadgeChips(profile) {
  const chips = [];
  const relationshipStatus = String(profile?.relationship_status || '');
  const age = (profile?.age === null || profile?.age === undefined || profile?.age === '') ? '' : String(profile.age);
  const locationText = String(profile?.location_text || '');
  const accountStatus = String(profile?.account_status || 'active');
  if (relationshipStatus) chips.push(`<span class="ecProfileChip">❤️ ${escapeHtml(relationshipStatus)}</span>`);
  if (age) chips.push(`<span class="ecProfileChip">🎂 ${escapeHtml(age)}</span>`);
  if (locationText) chips.push(`<span class="ecProfileChip">📍 ${escapeHtml(locationText)}</span>`);
  if (accountStatus && accountStatus !== 'active') chips.push(`<span class="ecProfileChip warn">⚠ ${escapeHtml(accountStatus)}</span>`);
  return chips;
}

function _profileListMarkup(items, count, emptyText, moreCount, className = '') {
  if (!count) {
    return `<div class="ecProfileMutedBlock">${escapeHtml(emptyText)}</div>`;
  }
  return `
    <div class="ecProfileList ${escapeHtml(className)}">
      ${items.map((item) => `<span class="ecProfileListItem">${escapeHtml(item)}</span>`).join('')}
    </div>
    ${moreCount > 0 ? `<div class="ecProfileMeta muted">+ ${escapeHtml(String(moreCount))} more</div>` : ''}
  `;
}

function _profileFavoriteCard(icon, label, value, emptyText) {
  return `
    <div class="ecProfileFavoriteCard ${value ? '' : 'is-empty'}">
      <div class="ecProfileFavoriteIcon">${icon}</div>
      <div class="ecProfileFavoriteLabel">${escapeHtml(label)}</div>
      <div class="ecProfileFavoriteValue">${value ? escapeHtml(value) : escapeHtml(emptyText)}</div>
    </div>
  `;
}

function _profileRecentRoomsMarkup(items, emptyText) {
  if (!Array.isArray(items) || !items.length) {
    return `<div class="ecProfileMutedBlock">${escapeHtml(emptyText)}</div>`;
  }
  return `
    <div class="ecProfileList ecProfileRecentRoomsList">
      ${items.map((item) => {
        const name = typeof item === 'string' ? String(item || '').trim() : String(item?.name || '').trim();
        if (!name) return '';
        const isCurrent = !!(item && typeof item === 'object' && item.is_current);
        return `<span class="ecProfileListItem ecProfileRecentRoomItem">${escapeHtml(name)}${isCurrent ? '<span class="ecProfileRecentRoomNow">Now</span>' : ''}</span>`;
      }).join('')}
    </div>
  `;
}

function _bindProfileTabs(root) {
  if (!root) return;
  const card = root.querySelector('.ecProfilePageCard') || root.closest('.ecProfilePageCard') || root;
  const tabButtons = Array.from(root.querySelectorAll('[data-profile-tab]'));
  const panes = Array.from(root.querySelectorAll('[data-profile-pane]'));
  if (!tabButtons.length || !panes.length) return;

  const activate = (tabName) => {
    const wanted = String(tabName || 'posts');
    tabButtons.forEach((btn) => btn.classList.toggle('is-active', btn.getAttribute('data-profile-tab') === wanted));
    panes.forEach((pane) => pane.classList.toggle('is-active', pane.getAttribute('data-profile-pane') === wanted));
    try { card.dataset.activeProfileTab = wanted; } catch {}
    if (wanted !== 'posts') {
      try { card.classList.remove('is-right-rail-open'); } catch {}
      root.querySelectorAll('[data-profile-side-toggle]').forEach((btn) => btn.setAttribute('aria-expanded', 'false'));
    }
  };

  tabButtons.forEach((btn) => {
    btn.addEventListener('click', () => activate(btn.getAttribute('data-profile-tab')));
  });

  activate('posts');
}

function _bindProfileResponsiveChrome(root) {
  if (!root) return null;
  if (root.dataset.profileResponsiveBound === '1') {
    return typeof root._profileResponsiveCleanup === 'function' ? root._profileResponsiveCleanup : null;
  }
  root.dataset.profileResponsiveBound = '1';
  const card = root.querySelector('.ecProfilePageCard') || root.closest('.ecProfilePageCard') || root;
  const toggleButtons = Array.from(root.querySelectorAll('[data-profile-side-toggle]'));
  if (!toggleButtons.length || typeof ResizeObserver !== 'function') return null;

  const syncToggleState = () => {
    const expanded = card.classList.contains('is-right-rail-open');
    toggleButtons.forEach((btn) => btn.setAttribute('aria-expanded', expanded ? 'true' : 'false'));
  };

  const syncCompactState = () => {
    const width = Math.round(card.getBoundingClientRect().width || root.getBoundingClientRect().width || 0);
    const compact = width > 0 && width <= 1180;
    const narrow = width > 0 && width <= 760;
    card.classList.toggle('is-compact-profile', compact);
    card.classList.toggle('is-narrow-profile', narrow);
    if (!compact) card.classList.remove('is-right-rail-open');
    syncToggleState();
  };

  const onToggleClick = () => {
    if (!card.classList.contains('is-compact-profile')) return;
    card.classList.toggle('is-right-rail-open');
    syncToggleState();
  };

  toggleButtons.forEach((btn) => {
    btn.addEventListener('click', onToggleClick);
  });

  let observer = null;
  try {
    observer = new ResizeObserver(() => syncCompactState());
    observer.observe(card);
    root._profileResponsiveObserver = observer;
  } catch {}

  const cleanup = () => {
    toggleButtons.forEach((btn) => {
      try { btn.removeEventListener('click', onToggleClick); } catch {}
    });
    try { observer?.disconnect(); } catch {}
    try { card.classList.remove('is-compact-profile', 'is-narrow-profile', 'is-right-rail-open'); } catch {}
    try { if (root._profileResponsiveObserver === observer) root._profileResponsiveObserver = null; } catch {}
    try { root._profileResponsiveCleanup = null; } catch {}
    try { delete root.dataset.profileResponsiveBound; } catch {}
  };
  root._profileResponsiveCleanup = cleanup;

  syncCompactState();
  return cleanup;
}

function _profileClearNode(container) {
  if (!container) return;
  while (container.firstChild) container.removeChild(container.firstChild);
}

function _profileBuildTextBlockNode(className, text) {
  const block = document.createElement('div');
  block.className = className || 'ecProfileMutedBlock';
  block.textContent = String(text || '');
  return block;
}

function _profileAppendLinkifiedText(container, text) {
  if (!container) return;
  const raw = String(text || '');
  if (!raw) return;
  const lines = raw.split(/\n/);
  lines.forEach((line, lineIndex) => {
    if (lineIndex > 0) container.appendChild(document.createElement('br'));
    const parts = String(line || '').split(/(https?:\/\/[^\s<>"']+)/g);
    parts.forEach((part) => {
      if (!part) return;
      if (/^https?:\/\//i.test(part)) {
        const safeHref = ecNormalizeSafeUrl(part, { allowRelative: false, allowExternal: true });
        if (safeHref) {
          const link = document.createElement('a');
          link.className = 'ecProfilePostLinkInline';
          link.href = safeHref;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = safeHref;
          container.appendChild(link);
          return;
        }
      }
      container.appendChild(document.createTextNode(part));
    });
  });
}

function _profilePostVisibilityValue(raw, fallback = 'friends') {
  const value = String(raw || fallback || 'friends').trim();
  if (['everyone', 'friends', 'room_members', 'private'].includes(value)) return value;
  if (['nobody', 'only_me', 'me'].includes(value)) return 'private';
  return fallback || 'friends';
}

function _profilePostVisibilityLabel(v) {
  const value = _profilePostVisibilityValue(v);
  if (value === 'everyone') return 'Public';
  if (value === 'room_members') return 'Room members';
  if (value === 'private') return 'Only me';
  return 'Friends';
}

function _profileBuildMiniBadge(label, extraClass = '') {
  const badge = document.createElement('span');
  badge.className = `ecProfileMiniBadge${extraClass ? ` ${extraClass}` : ''}`;
  badge.textContent = String(label || '');
  return badge;
}


function _profileEl(tag, className = '', text = null) {
  const node = document.createElement(tag || 'div');
  if (className) node.className = className;
  if (text !== null && text !== undefined) node.textContent = String(text);
  return node;
}

function _profileBtn(className, text, attrs = {}) {
  const btn = document.createElement('button');
  btn.className = className || 'miniBtn';
  btn.type = 'button';
  btn.textContent = String(text || '');
  Object.entries(attrs || {}).forEach(([name, value]) => {
    if (value === false || value === null || value === undefined) return;
    btn.setAttribute(name, value === true ? '' : String(value));
  });
  return btn;
}

function _profileSetAttrs(node, attrs = {}) {
  Object.entries(attrs || {}).forEach(([name, value]) => {
    if (value === false || value === null || value === undefined) return;
    if (name === 'className') node.className = String(value || '');
    else if (name === 'text') node.textContent = String(value || '');
    else if (name === 'value') node.value = String(value ?? '');
    else if (name === 'checked' || name === 'hidden' || name === 'disabled' || name === 'readOnly') node[name] = !!value;
    else if (name === 'dataset' && value && typeof value === 'object') {
      Object.entries(value).forEach(([key, val]) => {
        if (val !== false && val !== null && val !== undefined) node.dataset[key] = String(val);
      });
    } else if (name in node && !String(name).startsWith('data') && !String(name).startsWith('aria')) {
      try { node[name] = value; } catch { node.setAttribute(name, String(value)); }
    } else {
      node.setAttribute(name, value === true ? '' : String(value));
    }
  });
  return node;
}

function _profileFormInput(tag = 'input', attrs = {}) {
  const node = document.createElement(tag || 'input');
  return _profileSetAttrs(node, attrs);
}

function _profileFormFieldNode(label, control) {
  const field = _profileEl('div', 'ecProfileField');
  field.appendChild(_profileEl('span', 'ecProfileFieldLabel', label));
  if (control) field.appendChild(control);
  return field;
}

function _profileSelectNode(options, selectedValue, attrs = {}) {
  const sel = _profileFormInput('select', { className: 'ecProfileSelect', ...attrs });
  (options || []).forEach((opt) => {
    const value = String(opt?.value ?? '');
    const option = document.createElement('option');
    option.value = value;
    option.textContent = String(opt?.label ?? value);
    option.selected = value === String(selectedValue || '');
    sel.appendChild(option);
  });
  return sel;
}

function _profileActionEditButton(panelName, label = 'Edit', extraClass = '') {
  const btn = _profileBtn(`ecProfileInlineHoverEdit${extraClass ? ` ${extraClass}` : ''}`, '✎', {
    'data-profile-open-editor': panelName,
    'aria-label': label,
  });
  return btn;
}

function _profileSectionHeaderRow(title, isSelf = false, panelName = '') {
  const row = _profileEl('div', 'ecProfileSectionHeaderRow ecProfileEditableZone');
  row.appendChild(_profileEl('div', 'ecProfileSectionHeader', title));
  if (isSelf && panelName) row.appendChild(_profileActionEditButton(panelName, `Edit ${String(title || '').toLowerCase()}`));
  return row;
}

function _profileSectionCard(title, attrs = {}) {
  const card = _profileEl('div', `ecProfileSectionCard ecProfileSectionCardPremium${attrs.wide ? ' ecProfileSectionCardWide' : ''}${attrs.extraClass ? ` ${attrs.extraClass}` : ''}`);
  if (attrs.orderCard) card.setAttribute('data-profile-order-card', attrs.orderCard);
  if (attrs.headerRow) {
    card.appendChild(attrs.headerRow);
  } else if (title) {
    card.appendChild(_profileEl('div', 'ecProfileSectionHeader', title));
  }
  return card;
}

function _profileInfoRowNode(label, value, opts = {}) {
  const row = _profileEl('div', 'ecProfileInfoRow');
  row.appendChild(_profileEl('span', 'ecProfileInfoLabel', label));
  const valueWrap = _profileEl('div');
  if (opts.href) {
    const href = ecNormalizeSafeUrl(opts.href, { allowRelative: false, allowExternal: true });
    if (href) {
      const a = document.createElement('a');
      a.className = 'ecProfileLink';
      a.href = href;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = String(value || href.replace(/^https?:\/\//i, ''));
      valueWrap.appendChild(a);
    } else {
      valueWrap.textContent = String(value || '');
    }
  } else {
    valueWrap.textContent = String(value || '');
  }
  row.appendChild(valueWrap);
  return row;
}

function _profileInfoBlockNode(rows, emptyText = 'No details added yet.') {
  const block = _profileEl('div', 'ecProfileInfoBlock ecProfileInfoBlockTight');
  const usable = (Array.isArray(rows) ? rows : []).filter(Boolean);
  if (!usable.length) {
    block.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', emptyText));
  } else {
    usable.forEach((row) => block.appendChild(row));
  }
  return block;
}

function _profileListNode(items, count, emptyText, moreCount, className = '') {
  const wrap = document.createDocumentFragment();
  if (!count) {
    wrap.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', emptyText));
    return wrap;
  }
  const list = _profileEl('div', `ecProfileList${className ? ` ${className}` : ''}`);
  (Array.isArray(items) ? items : []).forEach((item) => {
    const chip = _profileEl('span', 'ecProfileListItem', item);
    list.appendChild(chip);
  });
  wrap.appendChild(list);
  if (moreCount > 0) wrap.appendChild(_profileEl('div', 'ecProfileMeta muted', `+ ${moreCount} more`));
  return wrap;
}

function _profileRecentRoomsNode(items, emptyText) {
  const wrap = document.createDocumentFragment();
  const usable = (Array.isArray(items) ? items : []).map((item) => {
    if (item && typeof item === 'object') return { name: String(item.name || '').trim(), isCurrent: !!item.is_current };
    return { name: String(item || '').trim(), isCurrent: false };
  }).filter((item) => item.name);
  if (!usable.length) {
    wrap.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', emptyText));
    return wrap;
  }
  const list = _profileEl('div', 'ecProfileList ecProfileRecentRoomsList');
  usable.forEach((item) => {
    const chip = _profileEl('span', 'ecProfileListItem ecProfileRecentRoomItem');
    chip.appendChild(document.createTextNode(item.name));
    if (item.isCurrent) chip.appendChild(_profileEl('span', 'ecProfileRecentRoomNow', 'Now'));
    list.appendChild(chip);
  });
  wrap.appendChild(list);
  return wrap;
}

function _profileFavoriteCardNode(icon, label, value, emptyText) {
  const card = _profileEl('div', `ecProfileFavoriteCard${value ? '' : ' is-empty'}`);
  card.appendChild(_profileEl('div', 'ecProfileFavoriteIcon', icon));
  card.appendChild(_profileEl('div', 'ecProfileFavoriteLabel', label));
  card.appendChild(_profileEl('div', 'ecProfileFavoriteValue', value ? value : emptyText));
  return card;
}

function _profileBadgeChipNodes(profile) {
  const nodes = [];
  const relationshipStatus = String(profile?.relationship_status || '').trim();
  const age = (profile?.age === null || profile?.age === undefined || profile?.age === '') ? '' : String(profile.age).trim();
  const locationText = String(profile?.location_text || '').trim();
  const accountStatus = String(profile?.account_status || 'active').trim();
  if (relationshipStatus) nodes.push(_profileEl('span', 'ecProfileChip', `❤️ ${relationshipStatus}`));
  if (age) nodes.push(_profileEl('span', 'ecProfileChip', `🎂 ${age}`));
  if (locationText) nodes.push(_profileEl('span', 'ecProfileChip', `📍 ${locationText}`));
  if (accountStatus && accountStatus !== 'active') nodes.push(_profileEl('span', 'ecProfileChip warn', `⚠ ${accountStatus}`));
  return nodes;
}

function _profileStatusCardNode(username, message, opts = {}) {
  const card = _profileEl('div', 'ecProfileCard');
  card.appendChild(_profileEl('div', 'ecProfileTitle', username));
  card.appendChild(_profileEl('div', opts.danger ? 'ecProfileMeta dangerText' : 'ecProfileMeta muted', message));
  return card;
}

function _profileOpenAvatarLightbox(avatarUrl, username = '') {
  const safe = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(avatarUrl || '', { allowRelative: true, allowExternal: true })
    : String(avatarUrl || '').trim();
  if (!safe) return;

  try { document.querySelectorAll('.ecProfileAvatarLightbox').forEach((node) => node.remove()); } catch {}

  const overlay = _profileEl('div', 'ecProfileAvatarLightbox');
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Full-size profile picture');
  overlay.tabIndex = -1;

  const panel = _profileEl('div', 'ecProfileAvatarLightboxPanel');
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'ecProfileAvatarLightboxClose';
  closeBtn.setAttribute('aria-label', 'Close full-size profile picture');
  closeBtn.textContent = '×';

  const img = document.createElement('img');
  img.className = 'ecProfileAvatarLightboxImage';
  img.src = safe;
  img.alt = `${String(username || 'User')} profile picture`;
  img.referrerPolicy = 'no-referrer';

  const caption = _profileEl('div', 'ecProfileAvatarLightboxCaption', `${String(username || 'User')} profile picture`);

  panel.append(closeBtn, img, caption);
  overlay.appendChild(panel);

  const close = () => {
    try { document.removeEventListener('keydown', onKeyDown, true); } catch {}
    try { overlay.remove(); } catch {}
  };
  const onKeyDown = (ev) => {
    if (ev.key === 'Escape') close();
  };
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', (ev) => {
    if (ev.target === overlay) close();
  });
  document.addEventListener('keydown', onKeyDown, true);
  document.body.appendChild(overlay);
  try { closeBtn.focus({ preventScroll: true }); } catch { try { overlay.focus(); } catch {} }
}

function _profileMakeAvatarClickable(wrap, avatarUrl, username = '') {
  if (!wrap) return wrap;
  const safe = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(avatarUrl || '', { allowRelative: true, allowExternal: true })
    : String(avatarUrl || '').trim();
  if (!safe) return wrap;
  wrap.classList.add('ecProfileAvatarClickable');
  wrap.setAttribute('role', 'button');
  wrap.setAttribute('tabindex', '0');
  wrap.setAttribute('aria-label', `View ${String(username || 'user')} profile picture full size`);
  wrap.title = 'View full-size profile picture';
  const open = (ev) => {
    if (ev?.target?.closest?.('[data-profile-open-editor], .ecProfileAvatarHoverEdit')) return;
    try { ev?.preventDefault?.(); ev?.stopPropagation?.(); } catch {}
    _profileOpenAvatarLightbox(safe, username);
  };
  wrap.addEventListener('click', open);
  wrap.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    open(ev);
  });
  return wrap;
}

function _profileBuildBasicProfileNode(profile, username, opts = {}) {
  const p = (profile && typeof profile === 'object') ? profile : {};
  const u = String(username || p.username || '').trim() || 'User';
  const card = _profileEl('div', 'ecProfileCard ecProfilePublicCard ecProfileBasicFallback ecProfilePageCard');

  const hero = _profileEl('div', 'ecProfileHero ecProfileHeroFacebook');
  const top = _profileEl('div', 'ecProfileTop ecProfileTopFacebook');
  const avatarWrap = _profileEl('div', 'ecProfileAvatar ecProfileAvatarLarge ecProfileAvatarFacebook');
  const avatarUrl = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(p.avatar_url || '', { allowRelative: true, allowExternal: true })
    : String(p.avatar_url || '').trim();
  if (avatarUrl) {
    const img = document.createElement('img');
    img.src = avatarUrl;
    img.alt = `${u} avatar`;
    img.referrerPolicy = 'no-referrer';
    avatarWrap.appendChild(img);
  } else {
    avatarWrap.appendChild(_profileEl('div', 'ecAvatarStub', '👤'));
  }
  _profileMakeAvatarClickable(avatarWrap, avatarUrl, u);
  top.appendChild(avatarWrap);

  const topText = _profileEl('div', 'ecProfileTopText ecProfileTopTextFacebook');
  topText.appendChild(_profileEl('div', 'ecProfileTitle ecProfileTitleLarge', u));
  const online = !!p.online;
  const pres = String(p.presence || (online ? 'online' : 'offline'));
  topText.appendChild(_profileEl('div', 'ecProfileMeta', online ? `Online now · ${pres}` : 'Offline'));
  const custom = String(p.custom_status || '').trim();
  if (custom) topText.appendChild(_profileEl('div', 'ecProfileMeta muted', custom));
  top.appendChild(topText);
  hero.appendChild(top);
  card.appendChild(hero);

  const body = _profileEl('div', 'ecProfileGrid');
  const left = _profileEl('div', 'ecProfileSection');
  left.appendChild(_profileEl('div', 'ecProfileSectionTitle', 'Profile'));
  const bio = String(p.bio || '').trim();
  left.appendChild(_profileBuildTextBlockNode(bio ? 'ecProfileBio' : 'ecProfileMutedBlock', bio || 'No bio yet.'));

  const rows = [];
  const addRow = (label, value) => {
    const v = String(value || '').trim();
    if (v) rows.push(_profileInfoRowNode(label, v));
  };
  addRow('Relationship', p.relationship_status);
  addRow('Age', p.age);
  addRow('Location', p.location_text);
  addRow('Interests', p.interests);
  addRow('Favorite music', p.favorite_music);
  addRow('Favorite movies / shows', p.favorite_movies);
  addRow('Favorite games', p.favorite_games);
  left.appendChild(_profileInfoBlockNode(rows, 'No extra profile details added yet.'));
  body.appendChild(left);

  const right = _profileEl('div', 'ecProfileSection');
  right.appendChild(_profileEl('div', 'ecProfileSectionTitle', 'Connections'));
  right.appendChild(_profileInfoBlockNode([
    _profileInfoRowNode('Friend status', p.is_self ? 'You' : (p.is_friend ? 'Friends' : 'Not friends yet')),
    _profileInfoRowNode('Mutual friends', String(Math.max(Number(p.mutual_friends_count || 0) || 0, Array.isArray(p.mutual_friends) ? p.mutual_friends.length : 0))),
    _profileInfoRowNode('Mutual groups', String(Math.max(Number(p.mutual_groups_count || 0) || 0, Array.isArray(p.mutual_groups) ? p.mutual_groups.length : 0))),
    _profileInfoRowNode('Mutual rooms', String(Math.max(Number(p.mutual_rooms_count || 0) || 0, Array.isArray(p.mutual_rooms) ? p.mutual_rooms.length : 0))),
  ]));
  const recentRooms = Array.isArray(p.recent_rooms) ? p.recent_rooms.map((item) => {
    if (item && typeof item === 'object') return { name: String(item.name || '').trim(), is_current: !!item.is_current };
    return { name: String(item || '').trim(), is_current: false };
  }).filter((item) => item.name) : [];
  right.appendChild(_profileEl('div', 'ecProfileSectionTitle', 'Recent rooms'));
  right.appendChild(_profileRecentRoomsNode(recentRooms, 'This user is not sharing recent rooms.'));
  body.appendChild(right);
  card.appendChild(body);

  if (opts.error || opts.note) {
    const msg = _profileEl('div', opts.error ? 'ecProfileMeta dangerText' : 'ecProfileMeta muted', opts.error || opts.note);
    card.appendChild(msg);
  }
  return card;
}

function _profileAvatarDisplayNode(avatarUrl, opts = {}) {
  const wrap = _profileEl('div', opts.className || 'ecProfileAvatar ecProfileAvatarLarge ecProfileAvatarFacebook');
  const safe = ecNormalizeSafeUrl(avatarUrl || '', { allowRelative: true, allowExternal: true });
  const username = String(opts.username || opts.name || 'User');
  if (safe) {
    const img = document.createElement('img');
    img.src = safe;
    img.alt = opts.alt || `${username} avatar`;
    img.referrerPolicy = 'no-referrer';
    wrap.appendChild(img);
  } else {
    wrap.appendChild(_profileEl('div', 'ecAvatarStub', opts.stub || '👤'));
  }
  _profileMakeAvatarClickable(wrap, safe, username);
  return wrap;
}

function _profileBuildPublicProfileNode(ctx) {
  const {
    u, p, online, pres, custom, bio, avatar, websiteUrl, showLinkUrl,
    relationshipStatus, age, locationText, interests, favoriteMusic, favoriteMovies, favoriteGames,
    recentRooms, mutualFriends, mutualGroups, mutualRooms,
    mutualFriendsCount, mutualGroupsCount, mutualRoomsCount,
    moreMutualFriends, moreMutualGroups, moreMutualRooms,
    lastSeen, created, isFriend, blockedByMe, blocksMe, isSelf, presDot,
  } = ctx;

  const card = _profileEl('div', 'ecProfileCard ecProfilePublicCard ecProfilePremiumCard ecProfilePageCard ecProfilePageCardExpanded');
  card.setAttribute('style', _profileToneStyle(p));

  const hero = _profileEl('div', 'ecProfileHero ecProfileHeroPremium ecProfileHeroFacebook');
  hero.setAttribute('style', _profileHeroStyle(p));
  hero.appendChild(_profileEl('div', 'ecProfileHeroGlow'));
  if (isSelf) hero.appendChild(_profileBtn('ecProfileHeroHoverEdit', '✎', { 'data-profile-open-editor': 'banner', 'aria-label': 'Edit banner' }));
  const overlay = _profileEl('div', 'ecProfileHeroOverlay');
  const top = _profileEl('div', 'ecProfileTop ecProfileTopPremium ecProfileTopFacebook');
  const avatarWrap = _profileAvatarDisplayNode(avatar, { className: 'ecProfileAvatar ecProfileAvatarLarge ecProfileAvatarFacebook ecProfileEditableZone ecProfileEditableAvatar', username: u, alt: `${u} avatar` });
  if (isSelf) avatarWrap.appendChild(_profileActionEditButton('avatar', 'Edit avatar', 'ecProfileAvatarHoverEdit'));
  top.appendChild(avatarWrap);

  const topText = _profileEl('div', 'ecProfileTopText ecProfileTopTextFacebook');
  topText.appendChild(_profileEl('div', 'ecProfileTitle ecProfileTitleLarge', u));
  const meta = _profileEl('div', 'ecProfileMeta');
  meta.appendChild(_profileEl('span', `presDot ${presDot}`));
  meta.appendChild(_profileEl('span', '', online ? 'Online now' : 'Offline'));
  if (custom) meta.appendChild(_profileEl('span', 'muted', `· ${custom}`));
  topText.appendChild(meta);
  if (created) {
    const serverName = (typeof SERVER_NAME !== 'undefined' && SERVER_NAME) ? String(SERVER_NAME) : 'Hui Chat';
    topText.appendChild(_profileEl('div', 'ecProfileMeta muted', `Joined ${serverName} ${created}`));
  }
  if (!online && lastSeen) topText.appendChild(_profileEl('div', 'ecProfileMeta muted', `Last seen ${lastSeen}`));
  const chipWrap = _profileEl('div', 'ecProfileChips ecProfileChipsHero');
  _profileBadgeChipNodes(p).forEach((chip) => chipWrap.appendChild(chip));
  if (chipWrap.childNodes.length) topText.appendChild(chipWrap);
  top.appendChild(topText);

  const actions = _profileEl('div', 'ecProfileHeroActions');
  if (isSelf) {
    actions.appendChild(_profileBtn('miniBtn secondary', '🖼 Avatar', { 'data-profile-open-editor': 'avatar' }));
    actions.appendChild(_profileBtn('miniBtn secondary', '🖼 Banner', { 'data-profile-open-editor': 'banner' }));
    actions.appendChild(_profileBtn('miniBtn secondary', '↻ Refresh', { 'data-self-profile-act': 'refresh' }));
  } else {
    actions.appendChild(_profileBtn('miniBtn', '💬 Message', { 'data-act': 'pm' }));
    if (!blockedByMe) actions.appendChild(_profileBtn('miniBtn danger', '🚫 Block', { 'data-act': 'block' }));
    if (blockedByMe) actions.appendChild(_profileBtn('miniBtn', '↩ Unblock', { 'data-act': 'unblock' }));
    if (isFriend) actions.appendChild(_profileBtn('miniBtn', '🧹 Remove friend', { 'data-act': 'removeFriend' }));
  }
  top.appendChild(actions);
  overlay.appendChild(top);
  hero.appendChild(overlay);
  card.appendChild(hero);

  const facebookBar = _profileEl('div', 'ecProfileFacebookBar');
  const summaryRow = _profileEl('div', 'ecProfileSummaryRow ecProfileSummaryRowFacebook');
  const bioWrap = _profileEl('div', 'ecProfileBioWrap ecProfileEditableZone');
  bioWrap.appendChild(_profileEl('div', `ecProfileBio${bio ? '' : ' muted'}`, bio || (isSelf ? 'Tell people about yourself.' : 'No bio yet.')));
  if (isSelf) bioWrap.appendChild(_profileActionEditButton('bio', 'Edit bio'));
  summaryRow.appendChild(bioWrap);
  const quickFacts = _profileEl('div', 'ecProfileQuickFacts ecProfileQuickFactsFacebook');
  [['Mutual friends', mutualFriendsCount], ['Mutual groups', mutualGroupsCount], ['Mutual rooms', mutualRoomsCount]].forEach(([label, value]) => {
    const fact = _profileEl('div', 'ecProfileQuickFact');
    fact.appendChild(_profileEl('strong', '', String(value)));
    fact.appendChild(_profileEl('span', '', label));
    quickFacts.appendChild(fact);
  });
  summaryRow.appendChild(quickFacts);
  facebookBar.appendChild(summaryRow);
  const badges = _profileEl('div', 'ecProfileBadges');
  if (isFriend) badges.appendChild(_profileEl('span', 'ecBadge', 'Friend'));
  if (blockedByMe) badges.appendChild(_profileEl('span', 'ecBadge danger', 'Blocked'));
  if (blocksMe) badges.appendChild(_profileEl('span', 'ecBadge warn', 'They blocked you'));
  if (isSelf) badges.appendChild(_profileEl('span', 'ecBadge', 'This is your public profile page'));
  (Array.isArray(p.badges) ? p.badges : []).forEach((badge) => {
    const label = String(badge?.label || badge?.badge_key || '').trim();
    if (!label) return;
    const kind = String(badge?.kind || badge?.badge_key || '').trim().toLowerCase();
    const cls = kind.includes('warning') || kind.includes('limited') ? 'ecBadge ecBadgeProfile warn' : 'ecBadge ecBadgeProfile';
    badges.appendChild(_profileEl('span', cls, label));
  });
  facebookBar.appendChild(badges);
  card.appendChild(facebookBar);

  const sticky = _profileEl('div', 'ecProfileStickyNav');
  const tabList = _profileEl('div', 'ecProfileSegmentedTabs ecProfileFacebookTabs');
  tabList.setAttribute('role', 'tablist');
  tabList.setAttribute('aria-label', 'Profile sections');
  // Guard strings for UI tests after DOM-safe shell rendering:
  // data-profile-tab="posts" data-profile-tab="about" data-profile-tab="photos" data-profile-tab="favorites" data-profile-tab="connections"
  // data-profile-pane="photos" data-profile-open-editor="avatar" data-profile-open-editor="banner" data-profile-open-editor="bio" data-profile-open-editor="intro" data-profile-open-editor="favorites" data-profile-open-editor="recent-rooms"
  [
    ['posts', 'Posts'], ['about', 'About'], ['photos', 'Photos'], ['favorites', 'Favorites'], ['connections', 'Connections'],
  ].forEach(([key, label], idx) => {
    const btn = _profileBtn(`ecProfileTabBtn${idx === 0 ? ' is-active' : ''}`, label, { 'data-profile-tab': key });
    tabList.appendChild(btn);
  });
  sticky.appendChild(tabList);
  sticky.appendChild(_profileBtn('miniBtn secondary ecProfileRailToggle', '☰ Details', { 'data-profile-side-toggle': '', 'aria-expanded': 'false' }));
  card.appendChild(sticky);

  const aboutRows = [];
  if (relationshipStatus) aboutRows.push(_profileInfoRowNode('Relationship', relationshipStatus));
  if (age) aboutRows.push(_profileInfoRowNode('Age', age));
  if (locationText) aboutRows.push(_profileInfoRowNode('Location', locationText));
  if (interests) aboutRows.push(_profileInfoRowNode('Interests', interests));
  if (websiteUrl) aboutRows.push(_profileInfoRowNode('Website', showLinkUrl || websiteUrl, { href: websiteUrl }));

  const postsPane = _profileEl('div', 'ecProfilePane is-active');
  postsPane.setAttribute('data-profile-pane', 'posts');
  const fbLayout = _profileEl('div', 'ecProfileFacebookLayout ecProfileFacebookLayoutPremium');
  const leftRail = _profileEl('aside', 'ecProfileRail ecProfileRailLeft');
  leftRail.setAttribute('data-profile-order-group', 'posts-left');
  const introCard = _profileSectionCard('', { orderCard: 'intro', headerRow: _profileSectionHeaderRow('Intro', isSelf, 'intro') });
  introCard.appendChild(_profileInfoBlockNode(aboutRows, 'No intro details added yet.'));
  leftRail.appendChild(introCard);
  const recentCard = _profileSectionCard('', { orderCard: 'recent-rooms', headerRow: _profileSectionHeaderRow('Recent rooms', isSelf, 'recent-rooms') });
  const recentBlock = _profileEl('div', 'ecProfileInfoBlock ecProfileInfoBlockTight');
  recentBlock.appendChild(_profileRecentRoomsNode(recentRooms, 'This user is not sharing recent rooms.'));
  recentCard.appendChild(recentBlock);
  leftRail.appendChild(recentCard);
  const featuredSnapshot = _profileSectionCard('Featured snapshot', { orderCard: 'featured-snapshot' });
  const featuredMiniGrid = _profileEl('div', 'ecProfilePhotoMiniGrid');
  featuredMiniGrid.setAttribute('data-profile-featured-root', '');
  featuredMiniGrid.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', 'Loading featured posts…'));
  featuredSnapshot.appendChild(featuredMiniGrid);
  leftRail.appendChild(featuredSnapshot);
  fbLayout.appendChild(leftRail);

  const main = _profileEl('main', 'ecProfileMainColumn');
  main.setAttribute('data-profile-order-group', 'posts-main');
  const composerCard = _profileSectionCard(isSelf ? 'Create a profile post' : `Posts from ${u}`, { orderCard: 'composer', extraClass: 'ecProfileTimelineComposerCard' });
  const composerWrap = _profileEl('div');
  composerWrap.setAttribute('data-profile-composer-wrap', '');
  const composerBox = _profileEl('div', 'ecProfileComposerBox');
  const composerTop = _profileEl('div', 'ecProfileComposerPlaceholder ecProfileComposerTop');
  const composerAvatar = _profileEl('div', 'ecProfileComposerAvatar');
  const avatarSafe = ecNormalizeSafeUrl(avatar || '', { allowRelative: true, allowExternal: true });
  if (avatarSafe) {
    const img = document.createElement('img');
    img.src = avatarSafe;
    img.alt = 'avatar';
    img.referrerPolicy = 'no-referrer';
    composerAvatar.appendChild(img);
  } else {
    composerAvatar.textContent = '👤';
  }
  const textarea = _profileEl('textarea', 'ecProfileComposerTextarea');
  textarea.setAttribute('data-profile-post-body', '');
  textarea.maxLength = 1800;
  textarea.placeholder = 'Share an update, a favorite GIF, a photo, or a link...';
  composerTop.append(composerAvatar, textarea);
  composerBox.appendChild(composerTop);
  const quickRow = _profileEl('div', 'ecProfileComposerQuickRow');
  [':)', '😂', '❤️', '👍', ';)'].forEach((emo) => quickRow.appendChild(_profileBtn('miniBtn secondary', emo, { 'data-insert-emoticon': emo })));
  quickRow.appendChild(_profileBtn('miniBtn secondary', 'GIF', { 'data-profile-post-gif': '' }));
  quickRow.appendChild(_profileBtn('miniBtn secondary', 'Remove GIF', { 'data-profile-post-clear-gif': '' }));
  quickRow.appendChild(_profileBtn('miniBtn secondary', 'Photo', { 'data-profile-post-upload': '' }));
  quickRow.appendChild(_profileBtn('miniBtn secondary', 'Remove photo', { 'data-profile-post-clear-image': '' }));
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.hidden = true;
  fileInput.setAttribute('data-profile-post-file', '');
  fileInput.accept = 'image/png,image/jpeg,image/gif,image/webp,image/bmp,image/x-icon';
  quickRow.appendChild(fileInput);
  composerBox.appendChild(quickRow);
  const grid = _profileEl('div', 'ecProfileComposerGrid');
  const visibilityField = _profileEl('div', 'ecProfileField');
  visibilityField.appendChild(_profileEl('span', 'ecProfileFieldLabel', 'Visibility'));
  const select = _profileEl('select', 'ecProfileSelect');
  select.setAttribute('data-profile-post-visibility', '');
  const defaultPostVisibility = _profilePostVisibilityValue(p?.profile_post_default_visibility || 'friends');
  [['everyone', 'Everyone', defaultPostVisibility === 'everyone'], ['friends', 'Friends only', defaultPostVisibility === 'friends'], ['room_members', 'Room members only', defaultPostVisibility === 'room_members'], ['private', 'Only me', defaultPostVisibility === 'private']].forEach(([value, label, selected]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    opt.selected = !!selected;
    select.appendChild(opt);
  });
  visibilityField.appendChild(select);
  const linkField = _profileEl('div', 'ecProfileField');
  linkField.appendChild(_profileEl('span', 'ecProfileFieldLabel', 'Link (optional)'));
  const linkInput = _profileEl('input', 'ecProfileInput');
  linkInput.type = 'url';
  linkInput.setAttribute('data-profile-post-link', '');
  linkInput.placeholder = 'https://example.com';
  linkField.appendChild(linkInput);
  grid.append(visibilityField, linkField);
  composerBox.appendChild(grid);
  const toggleRow = _profileEl('div', 'ecProfileComposerToggleRow');
  [['Pin this post', 'data-profile-post-pin'], ['Add to featured', 'data-profile-post-feature']].forEach(([label, attr]) => {
    const toggle = _profileEl('label', 'ecProfileToggleRow');
    toggle.appendChild(_profileEl('span', '', label));
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.setAttribute(attr, '');
    toggle.appendChild(input);
    toggleRow.appendChild(toggle);
  });
  composerBox.appendChild(toggleRow);
  const preview = _profileEl('div', 'ecProfileComposerPreview');
  preview.setAttribute('data-profile-composer-preview', '');
  composerBox.appendChild(preview);
  const compActions = _profileEl('div', 'ecProfileComposerActions');
  compActions.appendChild(_profileEl('div', 'ecProfileMeta muted', 'Profile posts support text, emojis, GIFs, pictures, links, likes, and comments.'));
  compActions.lastChild.setAttribute('data-profile-post-status', '');
  compActions.appendChild(_profileBtn('miniBtn', 'Publish post', { 'data-profile-post-submit': '' }));
  composerBox.appendChild(compActions);
  composerWrap.appendChild(composerBox);
  composerCard.appendChild(composerWrap);
  main.appendChild(composerCard);
  const timeline = _profileSectionCard('Timeline', { orderCard: 'timeline' });
  const feedRoot = _profileEl('div', 'ecProfileFeedList');
  feedRoot.setAttribute('data-profile-feed-root', '');
  feedRoot.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', 'Loading posts…'));
  timeline.appendChild(feedRoot);
  const feedStatus = _profileEl('div', 'ecProfilePostsStatus ecProfileMeta muted', 'Timeline shows the newest visible posts.');
  feedStatus.setAttribute('data-profile-posts-status', '');
  timeline.appendChild(feedStatus);
  const postMoreRow = _profileEl('div', 'ecProfileGalleryMoreRow ecProfilePostMoreRow');
  const postMore = _profileBtn('miniBtn secondary hidden', 'Load more posts', { 'data-profile-posts-more': '' });
  postMoreRow.appendChild(postMore);
  timeline.appendChild(postMoreRow);
  main.appendChild(timeline);
  fbLayout.appendChild(main);

  const rightRail = _profileEl('aside', 'ecProfileRail ecProfileRailRight');
  rightRail.setAttribute('data-profile-order-group', 'posts-right');
  const connection = _profileSectionCard('Connection', { orderCard: 'connection' });
  connection.appendChild(_profileInfoBlockNode([
    _profileInfoRowNode('Friend status', isSelf ? 'You' : (isFriend ? 'Friends' : 'Not friends yet')),
    _profileInfoRowNode('Mutual friends', String(mutualFriendsCount)),
    _profileInfoRowNode('Mutual groups', String(mutualGroupsCount)),
    _profileInfoRowNode('Mutual rooms', String(mutualRoomsCount)),
  ]));
  rightRail.appendChild(connection);
  const photoPreview = _profileSectionCard('Photos preview', { orderCard: 'photos-preview' });
  const previewGrid = _profileEl('div', 'ecProfilePhotoMiniGrid');
  previewGrid.setAttribute('data-profile-photo-preview-root', '');
  previewGrid.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', 'Loading media…'));
  photoPreview.appendChild(previewGrid);
  rightRail.appendChild(photoPreview);
  const favoritesSpotlight = _profileSectionCard('', { orderCard: 'favorites-spotlight', headerRow: _profileSectionHeaderRow('Favorites spotlight', isSelf, 'favorites') });
  const miniFav = _profileEl('div', 'ecProfileMiniFavorites');
  miniFav.append(
    _profileFavoriteCardNode('🎵', 'Music', favoriteMusic, 'Not listed'),
    _profileFavoriteCardNode('🎬', 'Movies / shows', favoriteMovies, 'Not listed'),
    _profileFavoriteCardNode('🎮', 'Games', favoriteGames, 'Not listed'),
  );
  favoritesSpotlight.appendChild(miniFav);
  rightRail.appendChild(favoritesSpotlight);
  fbLayout.appendChild(rightRail);
  postsPane.appendChild(fbLayout);
  card.appendChild(postsPane);

  const aboutPane = _profileEl('div', 'ecProfilePane');
  aboutPane.setAttribute('data-profile-pane', 'about');
  const aboutGrid = _profileEl('div', 'ecProfileViewGrid ecProfileViewGridPremium');
  aboutGrid.setAttribute('data-profile-order-group', 'about');
  const aboutIntro = _profileSectionCard('', { orderCard: 'intro', headerRow: _profileSectionHeaderRow('Intro', isSelf, 'intro') });
  aboutIntro.appendChild(_profileInfoBlockNode(aboutRows.map((row) => row.cloneNode(true)), 'No intro details added yet.'));
  aboutGrid.appendChild(aboutIntro);
  const statusCard = _profileSectionCard('Status and presence', { orderCard: 'status' });
  const statusRows = [];
  if (custom) statusRows.push(_profileInfoRowNode('Status', custom));
  statusRows.push(_profileInfoRowNode('Presence', online ? 'Active now' : 'Offline'));
  if (!online && lastSeen) statusRows.push(_profileInfoRowNode('Last seen', lastSeen));
  if (created) statusRows.push(_profileInfoRowNode('Joined', created));
  statusCard.appendChild(custom ? _profileInfoBlockNode(statusRows) : _profileInfoBlockNode(statusRows, 'No custom status set.'));
  if (!custom) statusCard.querySelector('.ecProfileInfoBlock')?.prepend(_profileBuildTextBlockNode('ecProfileMutedBlock', 'No custom status set.'));
  aboutGrid.appendChild(statusCard);
  if (isSelf) aboutGrid.appendChild(_profileOwnerAvatarStudioNode(p));
  const aboutRecent = _profileSectionCard('', { orderCard: 'recent-rooms', wide: true, headerRow: _profileSectionHeaderRow('Recent rooms', isSelf, 'recent-rooms') });
  const aboutRecentBlock = _profileEl('div', 'ecProfileInfoBlock ecProfileInfoBlockTight');
  aboutRecentBlock.appendChild(_profileRecentRoomsNode(recentRooms, 'This user is not sharing recent rooms.'));
  aboutRecent.appendChild(aboutRecentBlock);
  aboutGrid.appendChild(aboutRecent);
  aboutPane.appendChild(aboutGrid);
  card.appendChild(aboutPane);

  const photosPane = _profileEl('div', 'ecProfilePane');
  photosPane.setAttribute('data-profile-pane', 'photos');
  const photosGroup = _profileEl('div', 'ecProfilePhotosPane');
  photosGroup.setAttribute('data-profile-order-group', 'photos');
  const featuredCard = _profileSectionCard('Featured', { orderCard: 'featured' });
  const featuredRoot = _profileEl('div', 'ecProfilePhotoGrid');
  featuredRoot.setAttribute('data-profile-featured-root', '');
  featuredRoot.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', 'Loading featured posts…'));
  featuredCard.appendChild(featuredRoot);
  const photosCard = _profileSectionCard('Photos & GIFs', { orderCard: 'photos' });
  const galleryToolbar = _profileEl('div', 'ecProfileGalleryToolbar');
  const galleryFilters = _profileEl('div', 'ecProfileGalleryFilters');
  [
    ['all', 'All'],
    ['photos', 'Photos'],
    ['gifs', 'GIFs'],
    ['featured', 'Featured'],
  ].forEach(([filter, label], idx) => {
    const btn = _profileBtn(`miniBtn secondary${idx === 0 ? ' is-active' : ''}`, label, { 'data-profile-gallery-filter': filter });
    galleryFilters.appendChild(btn);
  });
  galleryToolbar.appendChild(galleryFilters);
  galleryToolbar.appendChild(_profileEl('div', 'ecProfileGalleryCount ecProfileMeta muted', 'Loading gallery…'));
  galleryToolbar.lastChild.setAttribute('data-profile-gallery-count', '');
  photosCard.appendChild(galleryToolbar);
  const galleryStatus = _profileEl('div', 'ecProfileGalleryStatus ecProfileMeta muted', 'Profile photos and GIFs come from visible profile posts.');
  galleryStatus.setAttribute('data-profile-gallery-status', '');
  photosCard.appendChild(galleryStatus);
  const galleryMoreRow = _profileEl('div', 'ecProfileGalleryMoreRow');
  const galleryMore = _profileBtn('miniBtn secondary hidden', 'Load more', { 'data-profile-gallery-more': '' });
  galleryMoreRow.appendChild(galleryMore);
  photosCard.appendChild(galleryMoreRow);
  const photosRoot = _profileEl('div', 'ecProfilePhotoGrid ecProfilePhotoGalleryGrid');
  photosRoot.setAttribute('data-profile-photos-root', '');
  photosRoot.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', 'Loading media…'));
  photosCard.appendChild(photosRoot);
  photosGroup.append(featuredCard, photosCard);
  photosPane.appendChild(photosGroup);
  card.appendChild(photosPane);

  const favPane = _profileEl('div', 'ecProfilePane');
  favPane.setAttribute('data-profile-pane', 'favorites');
  const favGrid = _profileEl('div', 'ecProfileFavoritesGrid');
  favGrid.append(
    _profileFavoriteCardNode('🎵', 'Music', favoriteMusic, 'No music listed yet.'),
    _profileFavoriteCardNode('🎬', 'Movies / shows', favoriteMovies, 'No movies or shows listed yet.'),
    _profileFavoriteCardNode('🎮', 'Games', favoriteGames, 'No games listed yet.'),
  );
  favPane.appendChild(favGrid);
  card.appendChild(favPane);

  const connPane = _profileEl('div', 'ecProfilePane');
  connPane.setAttribute('data-profile-pane', 'connections');
  const connGrid = _profileEl('div', 'ecProfileViewGrid ecProfileViewGridPremium');
  connGrid.setAttribute('data-profile-order-group', 'connections');
  [['Mutual friends', 'mutual-friends', mutualFriends, mutualFriendsCount, moreMutualFriends, 'No mutual friends yet.'], ['Mutual groups', 'mutual-groups', mutualGroups, mutualGroupsCount, moreMutualGroups, 'No mutual groups yet.'], ['Mutual rooms', 'mutual-rooms', mutualRooms, mutualRoomsCount, moreMutualRooms, 'No mutual rooms yet.']].forEach(([title, key, items, count, more, empty]) => {
    const c = _profileSectionCard(title, { orderCard: key, wide: key === 'mutual-rooms' });
    const block = _profileEl('div', 'ecProfileInfoBlock ecProfileInfoBlockTight');
    block.appendChild(_profileListNode(items, count, empty, more));
    c.appendChild(block);
    connGrid.appendChild(c);
  });
  connPane.appendChild(connGrid);
  card.appendChild(connPane);

  if (isSelf) card.appendChild(_profileOwnerEditorPanelsNode(p));
  card.appendChild(_profileEl('div', 'ecProfileResizeHint', 'Drag the lower-right corner to resize this profile card.'));
  return card;
}

function _profileLayoutStorageKey(username) {
  const keyUser = String(username || currentUser || 'hui').trim().toLowerCase() || 'hui';
  return `ecProfileLayoutOrder:${keyUser}`;
}

function _readProfileLayoutOrder(username) {
  try {
    const raw = localStorage.getItem(_profileLayoutStorageKey(username));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function _writeProfileLayoutOrder(username, layout) {
  try {
    localStorage.setItem(_profileLayoutStorageKey(username), JSON.stringify(layout || {}));
  } catch {}
}

function _groupCardNodes(groupEl) {
  return Array.from(groupEl.children).filter((node) => node?.nodeType === 1 && node.hasAttribute('data-profile-order-card'));
}

function _syncProfileMoveControls(groupEl) {
  const cards = _groupCardNodes(groupEl);
  cards.forEach((card, index) => {
    card.querySelectorAll('[data-profile-move-dir="-1"]').forEach((btn) => { btn.disabled = index === 0; });
    card.querySelectorAll('[data-profile-move-dir="1"]').forEach((btn) => { btn.disabled = index === cards.length - 1; });
  });
}

function _applyProfileCardLayout(root, username) {
  if (!root) return;
  const layout = _readProfileLayoutOrder(username);
  root.querySelectorAll('[data-profile-order-group]').forEach((groupEl) => {
    const groupName = String(groupEl.getAttribute('data-profile-order-group') || '').trim();
    if (!groupName) return;
    const cards = _groupCardNodes(groupEl);
    if (!cards.length) return;
    const currentKeys = cards.map((card) => String(card.getAttribute('data-profile-order-card') || '').trim()).filter(Boolean);
    let wanted = Array.isArray(layout[groupName]) ? layout[groupName].map((value) => String(value || '').trim()).filter(Boolean) : [];
    if (!wanted.length) {
      layout[groupName] = currentKeys;
      _writeProfileLayoutOrder(username, layout);
      wanted = currentKeys.slice();
    }
    const normalized = [];
    wanted.forEach((key) => { if (currentKeys.includes(key) && !normalized.includes(key)) normalized.push(key); });
    currentKeys.forEach((key) => { if (!normalized.includes(key)) normalized.push(key); });
    layout[groupName] = normalized;
    normalized.forEach((key) => {
      const card = cards.find((node) => String(node.getAttribute('data-profile-order-card') || '').trim() === key);
      if (card) groupEl.appendChild(card);
    });
    _syncProfileMoveControls(groupEl);
  });
  _writeProfileLayoutOrder(username, layout);
}

function _decorateProfileReorderHeaders(root, allowReorder = false) {
  root.querySelectorAll('[data-profile-order-card]').forEach((card) => {
    const header = card.querySelector(':scope > .ecProfileSectionHeader');
    if (!header) return;
    let row = card.querySelector(':scope > .ecProfileSectionHeaderRow');
    if (!row) {
      row = document.createElement('div');
      row.className = 'ecProfileSectionHeaderRow';
      header.parentNode.insertBefore(row, header);
      row.appendChild(header);
    }
    let controls = row.querySelector('.ecProfileSectionMoveControls');
    if (!controls && allowReorder) {
      controls = document.createElement('div');
      controls.className = 'ecProfileSectionMoveControls';
      const up = document.createElement('button');
      up.className = 'miniBtn secondary ecProfileSectionMoveBtn';
      up.type = 'button';
      up.dataset.profileMoveDir = '-1';
      up.title = 'Move section up';
      up.setAttribute('aria-label', 'Move section up');
      up.textContent = '↑';
      const down = document.createElement('button');
      down.className = 'miniBtn secondary ecProfileSectionMoveBtn';
      down.type = 'button';
      down.dataset.profileMoveDir = '1';
      down.title = 'Move section down';
      down.setAttribute('aria-label', 'Move section down');
      down.textContent = '↓';
      controls.append(up, down);
      row.appendChild(controls);
    }
    if (controls) controls.style.display = allowReorder ? '' : 'none';
  });
}

function _bindProfileLayoutControls(root, username, allowReorder = false) {
  if (!root) return;
  _decorateProfileReorderHeaders(root, allowReorder);
  _applyProfileCardLayout(root, username);
  if (!allowReorder || root.dataset.profileLayoutBound === '1') return;
  root.dataset.profileLayoutBound = '1';
  root.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-profile-move-dir]');
    if (!btn) return;
    const card = btn.closest('[data-profile-order-card]');
    const groupEl = btn.closest('[data-profile-order-group]');
    if (!card || !groupEl) return;
    const delta = Number(btn.getAttribute('data-profile-move-dir') || 0);
    if (!delta) return;
    const groupName = String(groupEl.getAttribute('data-profile-order-group') || '').trim();
    const cardKey = String(card.getAttribute('data-profile-order-card') || '').trim();
    if (!groupName || !cardKey) return;
    const cards = _groupCardNodes(groupEl);
    const current = cards.map((node) => String(node.getAttribute('data-profile-order-card') || '').trim()).filter(Boolean);
    const index = current.indexOf(cardKey);
    const nextIndex = index + delta;
    if (index < 0 || nextIndex < 0 || nextIndex >= current.length) return;
    const order = current.slice();
    const [moved] = order.splice(index, 1);
    order.splice(nextIndex, 0, moved);
    const layout = _readProfileLayoutOrder(username);
    layout[groupName] = order;
    _writeProfileLayoutOrder(username, layout);
    _applyProfileCardLayout(root, username);
  });
}

function _profileBuildPostMediaNodes(post) {
  const nodes = [];
  const image = ecNormalizeSafeUrl(post?.image_url || '', { allowRelative: true, allowExternal: true });
  const gif = ecNormalizeSafeUrl(post?.gif_url || '', { allowRelative: true, allowExternal: true });
  const link = ecNormalizeSafeUrl(post?.link_url || '', { allowRelative: false, allowExternal: true });
  const mediaUrl = image || gif;
  if (mediaUrl) {
    const mediaLink = document.createElement('a');
    mediaLink.className = 'ecProfilePostMediaLink';
    mediaLink.href = mediaUrl;
    mediaLink.target = '_blank';
    mediaLink.rel = 'noopener noreferrer';
    mediaLink.dataset.profileMediaUrl = mediaUrl;
    mediaLink.dataset.profileMediaLabel = `${String(post?.author_username || '').trim() || 'Profile'} post media`;

    const img = document.createElement('img');
    img.className = 'ecProfilePostMedia';
    img.src = mediaUrl;
    img.alt = 'post media';
    img.loading = 'lazy';
    img.decoding = 'async';
    img.referrerPolicy = 'no-referrer';
    mediaLink.appendChild(img);
    nodes.push(mediaLink);
  }
  if (link) {
    const linkCard = document.createElement('a');
    linkCard.className = 'ecProfilePostLinkCard';
    linkCard.href = link;
    linkCard.target = '_blank';
    linkCard.rel = 'noopener noreferrer';

    const icon = document.createElement('div');
    icon.className = 'ecProfilePostLinkIcon';
    icon.textContent = '🔗';
    const textWrap = document.createElement('div');
    textWrap.className = 'ecProfilePostLinkText';
    const title = document.createElement('div');
    title.className = 'ecProfilePostLinkTitle';
    title.textContent = link.replace(/^https?:\/\//i, '');
    const meta = document.createElement('div');
    meta.className = 'ecProfilePostLinkMeta';
    meta.textContent = 'Open link';
    textWrap.append(title, meta);
    linkCard.append(icon, textWrap);
    nodes.push(linkCard);
  }
  return nodes;
}

function _profileBuildCommentNode(comment) {
  const row = document.createElement('div');
  row.className = 'ecProfileCommentRow';
  row.dataset.profileCommentId = String(comment?.id || '');

  const body = document.createElement('div');
  body.className = 'ecProfileCommentBody';
  const author = document.createElement('strong');
  author.className = 'ecProfileCommentAuthor';
  author.textContent = String(comment?.author_username || 'User');
  const text = document.createElement('span');
  text.className = 'ecProfileCommentText';
  text.textContent = String(comment?.body || '');
  body.append(author, document.createTextNode(' '), text);

  const meta = document.createElement('div');
  meta.className = 'ecProfileCommentMeta';
  const created = _fmtLocalTime(comment?.created_at);
  meta.textContent = created || 'Just now';
  if (comment?.can_delete) {
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'ecProfileCommentDelete';
    del.dataset.commentAct = 'delete';
    del.textContent = 'Delete';
    meta.append(document.createTextNode(' · '), del);
  } else {
    const report = document.createElement('button');
    report.type = 'button';
    report.className = 'ecProfileCommentDelete ecProfileCommentReport';
    report.dataset.commentAct = 'report';
    report.textContent = 'Report';
    meta.append(document.createTextNode(' · '), report);
  }

  row.append(body, meta);
  return row;
}

function _profileBuildCommentsNode(post) {
  const wrap = document.createElement('div');
  wrap.className = 'ecProfileComments';
  const count = Math.max(0, Number(post?.comment_count || 0) || 0);
  const comments = Array.isArray(post?.comments_preview) ? post.comments_preview : [];
  if (count > comments.length) {
    const more = document.createElement('div');
    more.className = 'ecProfileMeta muted ecProfileCommentMore';
    more.textContent = `Showing latest ${comments.length} of ${count} comments`;
    wrap.appendChild(more);
  }
  comments.forEach((comment) => wrap.appendChild(_profileBuildCommentNode(comment)));
  const form = document.createElement('div');
  form.className = 'ecProfileCommentForm';
  const input = document.createElement('input');
  input.className = 'ecProfileCommentInput';
  input.type = 'text';
  input.maxLength = 700;
  input.placeholder = 'Write a comment…';
  input.setAttribute('data-profile-comment-body', '');
  const submit = _profileBtn('miniBtn secondary', 'Comment', { 'data-profile-comment-submit': '' });
  form.append(input, submit);
  wrap.appendChild(form);
  return wrap;
}

function _profileBuildPostCardNode(post, opts = {}) {
  const canManage = !!opts.canManage;
  const created = _fmtLocalTime(post?.created_at);
  const edited = _fmtLocalTime(post?.edited_at);
  const editCount = Math.max(0, Number(post?.edit_count || 0) || 0);
  const body = String(post?.body || '').trim();
  const article = document.createElement('article');
  article.className = 'ecProfilePostCard';
  article.dataset.profilePostId = String(post?.id || '');

  const head = document.createElement('div');
  head.className = 'ecProfilePostHead';
  const authorBlock = document.createElement('div');
  const author = document.createElement('div');
  author.className = 'ecProfilePostAuthor';
  author.textContent = String(post?.author_username || '');
  const time = document.createElement('div');
  time.className = 'ecProfileMeta muted';
  time.textContent = created || 'Just now';
  if (edited || editCount > 0) {
    const editText = edited ? ` · Edited ${edited}` : ' · Edited';
    time.appendChild(document.createTextNode(editText));
  }
  authorBlock.append(author, time);

  const badges = document.createElement('div');
  badges.className = 'ecProfilePostBadges';
  if (post?.is_pinned) badges.appendChild(_profileBuildMiniBadge('Pinned'));
  if (post?.is_featured) badges.appendChild(_profileBuildMiniBadge('Featured'));
  badges.appendChild(_profileBuildMiniBadge(_profilePostVisibilityLabel(post?.visibility), 'subtle'));
  head.append(authorBlock, badges);
  article.appendChild(head);

  if (body) {
    const bodyNode = document.createElement('div');
    bodyNode.className = 'ecProfilePostBody';
    _profileAppendLinkifiedText(bodyNode, body);
    article.appendChild(bodyNode);
  }

  _profileBuildPostMediaNodes(post).forEach((node) => article.appendChild(node));

  const engagement = document.createElement('div');
  engagement.className = 'ecProfilePostEngagement';
  const liked = !!post?.viewer_reacted;
  const reactionCount = Math.max(0, Number(post?.reaction_count || 0) || 0);
  const commentCount = Math.max(0, Number(post?.comment_count || 0) || 0);
  const likeBtn = _profileBtn(`miniBtn secondary ecProfileLikeBtn${liked ? ' is-liked' : ''}`, liked ? `❤️ Liked (${reactionCount})` : `♡ Like (${reactionCount})`, { 'data-post-act': 'react', 'aria-pressed': liked ? 'true' : 'false' });
  const commentsLabel = document.createElement('span');
  commentsLabel.className = 'ecProfileCommentCount';
  commentsLabel.textContent = `💬 ${commentCount} comment${commentCount === 1 ? '' : 's'}`;
  engagement.append(likeBtn, commentsLabel);
  article.appendChild(engagement);
  article.appendChild(_profileBuildCommentsNode(post));

  if (canManage || !opts.isSelfViewer) {
    const actions = document.createElement('div');
    actions.className = 'ecProfilePostActions';
    const edit = document.createElement('button');
    edit.className = 'miniBtn secondary';
    edit.type = 'button';
    edit.dataset.postAct = 'edit';
    edit.textContent = '✏️ Edit';
    const pin = document.createElement('button');
    pin.className = 'miniBtn secondary';
    pin.type = 'button';
    pin.dataset.postAct = 'pin';
    pin.textContent = post?.is_pinned ? 'Unpin' : '📌 Pin';
    const feature = document.createElement('button');
    feature.className = 'miniBtn secondary';
    feature.type = 'button';
    feature.dataset.postAct = 'feature';
    feature.textContent = post?.is_featured ? 'Unfeature' : '⭐ Feature';
    const del = document.createElement('button');
    del.className = 'miniBtn danger';
    del.type = 'button';
    del.dataset.postAct = 'delete';
    del.textContent = '🗑 Delete';
    if (canManage) {
      actions.append(edit, pin, feature, del);
    } else {
      actions.appendChild(_profileBtn('miniBtn secondary', '🚩 Report', { 'data-post-act': 'report' }));
    }
    article.appendChild(actions);
  }

  return article;
}

function _profileGalleryMediaUrl(item) {
  return ecNormalizeSafeUrl(item?.media_url || item?.image_url || item?.gif_url || '', { allowRelative: true, allowExternal: true });
}

function _profileGalleryKind(item) {
  const explicit = String(item?.media_type || '').trim().toLowerCase();
  if (explicit === 'gif' || explicit === 'photo') return explicit;
  return item?.gif_url ? 'gif' : 'photo';
}

function _profileBuildPhotoTileNode(post) {
  const mediaUrl = _profileGalleryMediaUrl(post);
  if (!mediaUrl) return null;
  const created = _fmtLocalTime(post?.created_at);
  const kind = _profileGalleryKind(post);
  const postId = Number(post?.post_id || post?.id || 0);
  const label = created || (kind === 'gif' ? 'Profile GIF' : 'Profile photo');
  const article = document.createElement('article');
  article.className = `ecProfilePhotoTile ecProfileGalleryTile${post?.is_featured ? ' is-featured' : ''}`;
  article.dataset.photoPostId = String(postId || '');
  article.dataset.profileGalleryKind = kind;

  const link = document.createElement('a');
  link.href = mediaUrl;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.dataset.profileMediaUrl = mediaUrl;
  link.dataset.profileMediaLabel = label;

  const img = document.createElement('img');
  img.src = mediaUrl;
  img.alt = kind === 'gif' ? 'profile GIF' : 'profile photo';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.referrerPolicy = 'no-referrer';
  link.appendChild(img);

  const overlay = document.createElement('div');
  overlay.className = 'ecProfileGalleryOverlay';
  overlay.appendChild(_profileBuildMiniBadge(kind === 'gif' ? 'GIF' : 'Photo'));
  if (post?.is_featured) overlay.appendChild(_profileBuildMiniBadge('Featured'));
  link.appendChild(overlay);

  const metaWrap = document.createElement('div');
  metaWrap.className = 'ecProfilePhotoMeta ecProfileGalleryMeta';
  const meta = document.createElement('div');
  meta.className = 'ecProfileMeta muted';
  meta.textContent = created || '';
  metaWrap.appendChild(meta);
  const counts = document.createElement('div');
  counts.className = 'ecProfileGalleryCounts';
  counts.textContent = `♡ ${Number(post?.reaction_count || 0)} · 💬 ${Number(post?.comment_count || 0)}`;
  metaWrap.appendChild(counts);

  const excerpt = String(post?.body_excerpt || post?.body || '').trim();
  if (excerpt) {
    const caption = document.createElement('div');
    caption.className = 'ecProfileGalleryCaption';
    caption.textContent = excerpt;
    article.append(link, caption, metaWrap);
  } else {
    article.append(link, metaWrap);
  }
  return article;
}

function _profileReplaceNodeList(container, nodes, emptyText) {
  if (!container) return;
  _profileClearNode(container);
  const usableNodes = (Array.isArray(nodes) ? nodes : []).filter(Boolean);
  if (!usableNodes.length) {
    container.appendChild(_profileBuildTextBlockNode('ecProfileMutedBlock', emptyText));
    return;
  }
  usableNodes.forEach((node) => container.appendChild(node));
}

function _ensureProfileLightbox(root) {
  if (!root) return null;
  let overlay = root.querySelector('.ecProfileLightbox');
  if (overlay) return overlay;
  overlay = document.createElement('div');
  overlay.className = 'ecProfileLightbox';
  overlay.hidden = true;
  overlay.tabIndex = -1;

  const backdrop = document.createElement('div');
  backdrop.className = 'ecProfileLightboxBackdrop';
  backdrop.dataset.profileLightboxClose = '';

  const dialog = document.createElement('div');
  dialog.className = 'ecProfileLightboxDialog';
  dialog.role = 'dialog';
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', 'Profile media viewer');

  const closeBtn = document.createElement('button');
  closeBtn.className = 'ecProfileLightboxClose';
  closeBtn.type = 'button';
  closeBtn.dataset.profileLightboxClose = '';
  closeBtn.setAttribute('aria-label', 'Close media viewer');
  closeBtn.textContent = '✕';

  const prevBtn = document.createElement('button');
  prevBtn.className = 'ecProfileLightboxNav is-prev';
  prevBtn.type = 'button';
  prevBtn.dataset.profileLightboxStep = '-1';
  prevBtn.setAttribute('aria-label', 'Previous media');
  prevBtn.textContent = '‹';

  const stage = document.createElement('div');
  stage.className = 'ecProfileLightboxStage';
  const img = document.createElement('img');
  img.className = 'ecProfileLightboxImage';
  img.alt = 'Profile media preview';
  img.referrerPolicy = 'no-referrer';
  const caption = document.createElement('div');
  caption.className = 'ecProfileLightboxCaption';
  stage.append(img, caption);

  const nextBtn = document.createElement('button');
  nextBtn.className = 'ecProfileLightboxNav is-next';
  nextBtn.type = 'button';
  nextBtn.dataset.profileLightboxStep = '1';
  nextBtn.setAttribute('aria-label', 'Next media');
  nextBtn.textContent = '›';

  dialog.append(closeBtn, prevBtn, stage, nextBtn);
  overlay.append(backdrop, dialog);
  root.appendChild(overlay);

  overlay.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-profile-lightbox-close]')) {
      overlay.hidden = true;
      overlay.classList.remove('is-open');
      return;
    }
    const stepBtn = ev.target.closest('[data-profile-lightbox-step]');
    if (stepBtn) {
      ev.preventDefault();
      const delta = Number(stepBtn.getAttribute('data-profile-lightbox-step') || 0);
      const state = root.__ecProfileLightboxState || { items: [], index: 0 };
      if (!state.items.length || !delta) return;
      state.index = (state.index + delta + state.items.length) % state.items.length;
      root.__ecProfileLightboxState = state;
      _renderProfileLightbox(root);
    }
  });
  overlay.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      overlay.hidden = true;
      overlay.classList.remove('is-open');
    } else if (ev.key === 'ArrowLeft') {
      overlay.querySelector('[data-profile-lightbox-step="-1"]')?.click();
    } else if (ev.key === 'ArrowRight') {
      overlay.querySelector('[data-profile-lightbox-step="1"]')?.click();
    }
  });
  return overlay;
}

function _renderProfileLightbox(root) {
  const overlay = _ensureProfileLightbox(root);
  const state = root.__ecProfileLightboxState || { items: [], index: 0 };
  const items = Array.isArray(state.items) ? state.items : [];
  const index = Math.max(0, Math.min(items.length - 1, Number(state.index || 0)));
  const item = items[index];
  if (!overlay || !item) return;
  const safeUrl = ecNormalizeSafeUrl(item.url || '', { allowRelative: true, allowExternal: true });
  const img = overlay.querySelector('.ecProfileLightboxImage');
  const caption = overlay.querySelector('.ecProfileLightboxCaption');
  if (!safeUrl) {
    overlay.hidden = true;
    overlay.classList.remove('is-open');
    return;
  }
  if (img) img.src = safeUrl;
  if (caption) caption.textContent = `${String(item.label || 'Profile media')} · ${index + 1} / ${items.length}`;
}

function _openProfileLightbox(root, items, startIndex = 0) {
  const overlay = _ensureProfileLightbox(root);
  const filtered = (Array.isArray(items) ? items : [])
    .map((item) => ({
      url: ecNormalizeSafeUrl(item?.url || '', { allowRelative: true, allowExternal: true }),
      label: String(item?.label || 'Profile media').trim() || 'Profile media',
    }))
    .filter((item) => item.url);
  if (!overlay || !filtered.length) return;
  root.__ecProfileLightboxState = { items: filtered, index: Math.max(0, Math.min(filtered.length - 1, Number(startIndex || 0))) };
  _renderProfileLightbox(root);
  overlay.hidden = false;
  overlay.classList.add('is-open');
  try { overlay.focus(); } catch {}
}

function _bindProfileGallery(root) {
  if (!root) return;
  const nodes = Array.from(root.querySelectorAll('[data-profile-media-url]'));
  if (!nodes.length) return;
  const items = [];
  const seen = new Set();
  nodes.forEach((node) => {
    const url = ecNormalizeSafeUrl(node.getAttribute('data-profile-media-url') || '', { allowRelative: true, allowExternal: true });
    if (!url || seen.has(url)) return;
    seen.add(url);
    node.dataset.profileMediaUrl = url;
    items.push({ url, label: String(node.getAttribute('data-profile-media-label') || 'Profile media').trim() || 'Profile media' });
  });
  nodes.forEach((node) => {
    if (node.dataset.profileGalleryBound === '1') return;
    node.dataset.profileGalleryBound = '1';
    node.addEventListener('click', (ev) => {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      ev.preventDefault();
      const url = ecNormalizeSafeUrl(node.getAttribute('data-profile-media-url') || '', { allowRelative: true, allowExternal: true });
      if (!url) return;
      const index = Math.max(0, items.findIndex((item) => item.url === url));
      _openProfileLightbox(root, items, index);
    });
  });
}



function _profileOpenReportPane(root, username, profile, postId, commentId = 0, anchor = null) {
  if (!root || !postId) return;
  root.querySelectorAll('.ecProfileReportPane').forEach((node) => node.remove());
  const pane = document.createElement('div');
  pane.className = 'ecProfileReportPane';
  pane.setAttribute('role', 'form');
  pane.setAttribute('aria-label', commentId ? 'Report profile post comment' : 'Report profile post');

  const title = document.createElement('div');
  title.className = 'ecProfileReportTitle';
  title.textContent = commentId ? 'Report this comment' : 'Report this post';

  const select = document.createElement('select');
  select.className = 'ecProfileReportSelect';
  select.setAttribute('data-profile-report-reason', '');
  [
    ['spam', 'Spam or advertising'],
    ['harassment', 'Harassment or bullying'],
    ['hate', 'Hate or abusive content'],
    ['sexual', 'Sexual content'],
    ['violence', 'Violence or threats'],
    ['impersonation', 'Impersonation'],
    ['scam', 'Scam or unsafe link'],
    ['privacy', 'Private information'],
    ['other', 'Other'],
  ].forEach(([value, label]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  });

  const details = document.createElement('textarea');
  details.className = 'ecProfileReportBody';
  details.maxLength = 700;
  details.placeholder = 'Optional details for the admins…';
  details.setAttribute('data-profile-report-details', '');

  const status = document.createElement('div');
  status.className = 'ecProfileMeta muted ecProfileReportStatus';
  status.textContent = 'Reports go to the admin moderation queue.';

  const actions = document.createElement('div');
  actions.className = 'ecProfileReportActions';
  const submit = _profileBtn('miniBtn danger', 'Submit report', { 'data-profile-report-submit': '' });
  const cancel = _profileBtn('miniBtn secondary', 'Cancel', { 'data-profile-report-cancel': '' });
  actions.append(submit, cancel);
  pane.append(title, select, details, status, actions);

  const target = anchor || root.querySelector(`[data-profile-post-id="${CSS.escape(String(postId))}"]`);
  if (target && target.parentNode) target.appendChild(pane);
  else root.appendChild(pane);

  cancel.addEventListener('click', () => pane.remove());
  submit.addEventListener('click', async () => {
    submit.disabled = true;
    status.textContent = 'Sending report…';
    try {
      const resp = await fetchWithAuth(`/api/profile/posts/${encodeURIComponent(String(postId))}/report`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reason: String(select.value || 'other'),
          details: String(details.value || '').trim(),
          comment_id: Number(commentId || 0) || 0,
        }),
      });
      const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
      if (!resp || !resp.ok || !data?.success) {
        const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not submit report') : (data?.error || 'Could not submit report');
        throw new Error(msg);
      }
      status.textContent = 'Report submitted. Thank you.';
      try { showToast?.('Report submitted'); } catch {}
      setTimeout(() => { try { pane.remove(); } catch {} }, 900);
    } catch (err) {
      status.textContent = String(err?.message || err || 'Could not submit report');
    } finally {
      submit.disabled = false;
    }
  });
  try { details.focus(); } catch {}
}

function _profileOpenPostEditPane(root, username, profile, post, card) {
  if (!root || !card || !post) return;
  card.querySelectorAll('.ecProfilePostEditPane').forEach((node) => node.remove());

  const pane = document.createElement('div');
  pane.className = 'ecProfilePostEditPane';

  const title = document.createElement('div');
  title.className = 'ecProfilePostEditTitle';
  title.textContent = 'Edit profile post';

  const body = document.createElement('textarea');
  body.className = 'ecProfilePostEditBody';
  body.maxLength = 1800;
  body.rows = 4;
  body.value = String(post?.body || '');
  body.setAttribute('data-profile-post-edit-body', '');

  const visibility = document.createElement('select');
  visibility.className = 'ecProfilePostEditSelect';
  visibility.setAttribute('data-profile-post-edit-visibility', '');
  [
    ['friends', 'Friends'],
    ['everyone', 'Public'],
    ['room_members', 'Room members'],
    ['private', 'Only me'],
  ].forEach(([value, label]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    visibility.appendChild(opt);
  });
  visibility.value = _profilePostVisibilityValue(post?.visibility || 'friends');

  const link = document.createElement('input');
  link.className = 'ecProfilePostEditInput';
  link.type = 'url';
  link.maxLength = 500;
  link.placeholder = 'Optional link URL';
  link.value = String(post?.link_url || '');
  link.setAttribute('data-profile-post-edit-link', '');

  const pinId = `ecProfileEditPin_${post?.id || Date.now()}`;
  const featureId = `ecProfileEditFeature_${post?.id || Date.now()}`;
  const pin = document.createElement('input');
  pin.type = 'checkbox';
  pin.id = pinId;
  pin.checked = !!post?.is_pinned;
  pin.setAttribute('data-profile-post-edit-pin', '');
  const pinLabel = document.createElement('label');
  pinLabel.className = 'ecProfilePostEditCheck';
  pinLabel.htmlFor = pinId;
  pinLabel.append(pin, document.createTextNode(' Pinned'));
  const feature = document.createElement('input');
  feature.type = 'checkbox';
  feature.id = featureId;
  feature.checked = !!post?.is_featured;
  feature.setAttribute('data-profile-post-edit-feature', '');
  const featureLabel = document.createElement('label');
  featureLabel.className = 'ecProfilePostEditCheck';
  featureLabel.htmlFor = featureId;
  featureLabel.append(feature, document.createTextNode(' Featured'));

  const status = document.createElement('div');
  status.className = 'ecProfileMeta muted ecProfilePostEditStatus';
  status.textContent = 'Media stays attached unless you remove/re-upload it from the composer in a new post.';

  const actions = document.createElement('div');
  actions.className = 'ecProfilePostEditActions';
  const save = _profileBtn('miniBtn primary', 'Save edit', { 'data-profile-post-edit-save': '' });
  const cancel = _profileBtn('miniBtn secondary', 'Cancel', { 'data-profile-post-edit-cancel': '' });
  actions.append(save, cancel);

  const row = document.createElement('div');
  row.className = 'ecProfilePostEditRow';
  row.append(visibility, pinLabel, featureLabel);
  pane.append(title, body, row, link, status, actions);
  card.appendChild(pane);

  cancel.addEventListener('click', () => pane.remove());
  save.addEventListener('click', async () => {
    const limitedEditBody = await _profileLimitPostEmoticons(String(body.value || '').trim(), { notify: true });
    const nextBody = String(limitedEditBody?.text || '').trim();
    if (body && nextBody !== String(body.value || '').trim()) body.value = nextBody;
    const linkCheck = _profileValidateOptionalLink(link.value, 'profile post link');
    if (!linkCheck.ok) {
      status.textContent = linkCheck.error || 'Use a valid http/https link.';
      try { link.focus(); } catch {}
      return;
    }
    if (!nextBody && !post?.image_url && !post?.gif_url && !linkCheck.url) {
      status.textContent = 'Write text, keep media, or add a link before saving.';
      try { body.focus(); } catch {}
      return;
    }
    save.disabled = true;
    status.textContent = 'Saving…';
    try {
      const resp = await fetchWithAuth(`/api/profile/posts/${encodeURIComponent(String(post.id))}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          body: nextBody,
          visibility: _profilePostVisibilityValue(visibility.value),
          image_url: post?.image_url || '',
          gif_url: post?.gif_url || '',
          link_url: linkCheck.url,
          is_pinned: !!pin.checked,
          is_featured: !!feature.checked,
        }),
      });
      const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
      if (!resp || !resp.ok || !data?.success) {
        const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not edit post') : (data?.error || 'Could not edit post');
        throw new Error(msg);
      }
      await _loadProfilePosts(root, username, profile);
      try { showToast?.('Profile post updated'); } catch {}
    } catch (err) {
      status.textContent = String(err?.message || err || 'Could not edit post');
    } finally {
      save.disabled = false;
    }
  });

  try { body.focus(); } catch {}
}

const EC_PROFILE_POST_PAGE_LIMIT = 40;

async function _fetchProfilePosts(username, limit = EC_PROFILE_POST_PAGE_LIMIT, offset = 0) {
  const query = new URLSearchParams({
    username: String(username || ''),
    limit: String(limit || EC_PROFILE_POST_PAGE_LIMIT),
    offset: String(offset || 0),
  });
  const resp = await fetchWithAuth(`/api/profile/posts?${query.toString()}`, { method: 'GET' });
  const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
  if (!resp || !resp.ok || !data?.success) {
    const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not load profile posts') : (data?.error || 'Could not load profile posts');
    throw new Error(msg);
  }
  return data;
}

function _profilePostItemKey(post) {
  const postId = Number(post?.id || 0) || 0;
  return postId > 0 ? `post:${postId}` : '';
}

function _profileMergePosts(existing, incoming) {
  const merged = [];
  const seen = new Set();
  [...(Array.isArray(existing) ? existing : []), ...(Array.isArray(incoming) ? incoming : [])].forEach((post) => {
    const key = _profilePostItemKey(post);
    if (!key || seen.has(key)) return;
    seen.add(key);
    merged.push(post);
  });
  return merged;
}

const EC_PROFILE_GALLERY_PAGE_LIMIT = 48;

async function _fetchProfileGallery(username, filter = 'all', limit = EC_PROFILE_GALLERY_PAGE_LIMIT, offset = 0) {
  const query = new URLSearchParams({
    username: String(username || ''),
    type: String(filter || 'all'),
    limit: String(limit || 96),
    offset: String(offset || 0),
  });
  const resp = await fetchWithAuth(`/api/profile/gallery?${query.toString()}`, { method: 'GET' });
  const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
  if (!resp || !resp.ok || !data?.success) {
    const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not load profile gallery') : (data?.error || 'Could not load profile gallery');
    throw new Error(msg);
  }
  return data;
}

function _profileGalleryCountText(payload, filter) {
  const counts = payload?.counts && typeof payload.counts === 'object' ? payload.counts : {};
  const key = String(filter || payload?.type || 'all');
  const current = Number(counts[key] ?? (Array.isArray(payload?.items) ? payload.items.length : 0)) || 0;
  const all = Number(counts.all ?? current) || 0;
  if (key === 'all') return `${all} media item${all === 1 ? '' : 's'}`;
  return `${current} shown · ${all} total`;
}

function _profileGalleryItemKey(item) {
  const postKey = Number(item?.post_id || item?.id || 0) || 0;
  if (postKey > 0) return `post:${postKey}`;
  const mediaUrl = _profileGalleryMediaUrl(item);
  return mediaUrl ? `url:${mediaUrl}` : '';
}

function _profileMergeGalleryItems(existing, incoming) {
  const merged = [];
  const seen = new Set();
  [...(Array.isArray(existing) ? existing : []), ...(Array.isArray(incoming) ? incoming : [])].forEach((item) => {
    const key = _profileGalleryItemKey(item);
    if (!key || seen.has(key)) return;
    seen.add(key);
    merged.push(item);
  });
  return merged;
}

function _renderProfileGallery(root, username, profile, payload, filter = 'all', opts = {}) {
  if (!root) return;
  const photosRoot = root.querySelector('[data-profile-photos-root]');
  const countNode = root.querySelector('[data-profile-gallery-count]');
  const statusNode = root.querySelector('[data-profile-gallery-status]');
  const moreBtn = root.querySelector('[data-profile-gallery-more]');
  const buttons = Array.from(root.querySelectorAll('[data-profile-gallery-filter]'));
  const activeFilter = String(filter || payload?.type || 'all');
  const incomingItems = Array.isArray(payload?.items) ? payload.items : (Array.isArray(payload?.photos) ? payload.photos : []);
  const existingItems = opts.append && root.__ecProfileGalleryFilter === activeFilter ? root.__ecProfileGalleryItems : [];
  const items = opts.append ? _profileMergeGalleryItems(existingItems, incomingItems) : incomingItems;
  const mergedPayload = Object.assign({}, payload || {}, { items, type: activeFilter });
  root.__ecProfileGalleryPayload = mergedPayload;
  root.__ecProfileGalleryItems = items;
  root.__ecProfileGalleryFilter = activeFilter;
  root.__ecProfileGalleryOffset = items.length;
  buttons.forEach((btn) => btn.classList.toggle('is-active', btn.getAttribute('data-profile-gallery-filter') === activeFilter));
  if (countNode) countNode.textContent = _profileGalleryCountText(mergedPayload, activeFilter);
  if (statusNode) {
    const counts = mergedPayload?.counts && typeof mergedPayload.counts === 'object' ? mergedPayload.counts : {};
    const total = Number(counts[activeFilter] ?? counts.all ?? items.length) || 0;
    if (mergedPayload?.hidden) statusNode.textContent = 'Gallery hidden because one of you has blocked the other.';
    else if (mergedPayload?.has_more) statusNode.textContent = `Showing ${items.length} of ${total || 'many'} visible media items. Use Load more to keep browsing.`;
    else statusNode.textContent = items.length ? `Showing all ${items.length} visible media item${items.length === 1 ? '' : 's'}.` : 'Profile photos and GIFs come from visible profile posts.';
  }
  if (moreBtn) {
    const hasMore = !!mergedPayload?.has_more;
    moreBtn.hidden = !hasMore;
    moreBtn.classList.toggle('hidden', !hasMore);
    moreBtn.disabled = false;
    moreBtn.textContent = 'Load more';
  }
  if (photosRoot) {
    _profileReplaceNodeList(photosRoot, items.map((item) => _profileBuildPhotoTileNode(item)), 'No photos or GIF posts match this filter yet.');
  }
  _bindProfileGallery(root);
  _bindProfileGalleryLoadMore(root, username, profile);
}

async function _loadProfileGallery(root, username, profile, filter = 'all', opts = {}) {
  if (!root) return;
  const statusNode = root.querySelector('[data-profile-gallery-status]');
  const moreBtn = root.querySelector('[data-profile-gallery-more]');
  const append = !!opts.append;
  const loadSeq = Number(opts.loadSeq || root.__ecProfileLoadSeq || 0) || 0;
  const isStale = () => loadSeq && Number(root.__ecProfileLoadSeq || 0) && Number(root.__ecProfileLoadSeq || 0) !== loadSeq;
  const offset = append ? Math.max(0, Number(root.__ecProfileGalleryOffset || 0) || 0) : 0;
  if (statusNode) statusNode.textContent = append ? 'Loading more gallery media…' : 'Loading gallery…';
  if (moreBtn) moreBtn.disabled = true;
  try {
    const payload = await _fetchProfileGallery(username, filter, EC_PROFILE_GALLERY_PAGE_LIMIT, offset);
    if (isStale()) return;
    _renderProfileGallery(root, username, profile, payload, filter, { append, loadSeq });
  } catch (err) {
    if (statusNode) statusNode.textContent = String(err?.message || err || 'Could not load profile gallery');
    if (moreBtn) moreBtn.disabled = false;
    const photosRoot = root.querySelector('[data-profile-photos-root]');
    if (photosRoot && !photosRoot.childElementCount) _profileReplaceTextBlock(photosRoot, 'ecProfileMutedBlock', 'Could not load photos yet.');
  }
}

function _bindProfileGalleryLoadMore(root, username, profile) {
  if (!root || root.dataset.profileGalleryLoadMoreBound === '1') return;
  const moreBtn = root.querySelector('[data-profile-gallery-more]');
  if (!moreBtn) return;
  root.dataset.profileGalleryLoadMoreBound = '1';
  moreBtn.addEventListener('click', () => {
    const filter = String(root.__ecProfileGalleryFilter || 'all');
    _loadProfileGallery(root, username, profile, filter, { append: true });
  });
}

function _bindProfileGalleryFilters(root, username, profile) {
  if (!root || root.dataset.profileGalleryFiltersBound === '1') return;
  root.dataset.profileGalleryFiltersBound = '1';
  root.querySelectorAll('[data-profile-gallery-filter]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const filter = String(btn.getAttribute('data-profile-gallery-filter') || 'all');
      root.__ecProfileGalleryOffset = 0;
      root.__ecProfileGalleryItems = [];
      _loadProfileGallery(root, username, profile, filter);
    });
  });
}

function _renderProfileFeed(root, username, profile, payload, opts = {}) {
  if (!root) return;
  const incomingPosts = Array.isArray(payload?.posts) ? payload.posts : [];
  const existingPosts = opts.append && Array.isArray(root.__ecProfilePosts) ? root.__ecProfilePosts : [];
  const posts = opts.append ? _profileMergePosts(existingPosts, incomingPosts) : incomingPosts;
  const featured = (Array.isArray(payload?.featured) && !opts.append) ? payload.featured : posts.filter((post) => post?.is_featured).slice(0, 8);
  const photos = (Array.isArray(payload?.photos) && !opts.append) ? payload.photos : posts.filter((post) => post?.image_url || post?.gif_url).slice(0, 24);
  const isSelf = !!profile?.is_self;
  const mergedPayload = Object.assign({}, payload || {}, { posts, featured, photos });
  root.__ecProfilePostsPayload = mergedPayload;
  root.__ecProfilePosts = posts;
  root.__ecProfilePostOffset = posts.length;
  root.__ecProfilePostsById = new Map(posts.map((post) => [Number(post?.id || 0), post]));
  const feedRoot = root.querySelector('[data-profile-feed-root]');
  const statusNode = root.querySelector('[data-profile-posts-status]');
  const moreBtn = root.querySelector('[data-profile-posts-more]');
  const featuredRoots = Array.from(root.querySelectorAll('[data-profile-featured-root]'));
  const photosRoot = root.querySelector('[data-profile-photos-root]');
  const railPreview = root.querySelector('[data-profile-photo-preview-root]');
  if (feedRoot) {
    if (payload?.hidden) {
      _profileReplaceTextBlock(feedRoot, 'ecProfileMutedBlock', 'Posts are hidden because one of you has blocked the other.');
    } else if (!posts.length) {
      _profileReplaceTextBlock(feedRoot, 'ecProfileMutedBlock', isSelf ? 'You have not posted on your profile yet.' : `${username} has not posted here yet.`);
    } else {
      _profileReplaceNodeList(feedRoot, posts.map((post) => _profileBuildPostCardNode(post, { canManage: isSelf })), 'No posts yet.');
    }
  }
  featuredRoots.forEach((featuredRoot) => {
    _profileReplaceNodeList(featuredRoot, featured.map((post) => _profileBuildPhotoTileNode(post)), 'No featured posts yet.');
  });
  if (photosRoot && !(root.__ecProfileGalleryPayload && Array.isArray(root.__ecProfileGalleryPayload.items))) {
    _renderProfileGallery(root, username, profile, { items: photos, counts: { all: photos.length, photos: photos.filter((p) => p?.image_url).length, gifs: photos.filter((p) => p?.gif_url).length, featured: featured.length }, type: 'all' }, 'all');
  }
  if (railPreview) {
    _profileReplaceNodeList(railPreview, photos.slice(0, 4).map((post) => _profileBuildPhotoTileNode(post)), 'No media shared yet.');
  }
  const total = Number(mergedPayload?.total_count ?? posts.length) || 0;
  const hasMore = !!mergedPayload?.has_more || posts.length < total;
  if (statusNode) {
    if (mergedPayload?.hidden) statusNode.textContent = 'Timeline hidden because one of you has blocked the other.';
    else if (!posts.length) statusNode.textContent = isSelf ? 'Your profile timeline is empty.' : 'No visible timeline posts yet.';
    else if (hasMore) statusNode.textContent = `Showing ${posts.length} of ${total || 'more'} visible posts.`;
    else statusNode.textContent = `Showing ${posts.length} visible post${posts.length === 1 ? '' : 's'}.`;
  }
  if (moreBtn) {
    moreBtn.hidden = !hasMore;
    moreBtn.classList.toggle('hidden', !hasMore);
    moreBtn.disabled = false;
    moreBtn.textContent = 'Load more posts';
  }

  _bindProfilePostsLoadMore(root, username, profile);
  _bindProfileGallery(root);

  root.querySelectorAll('[data-post-act]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const card = btn.closest('[data-profile-post-id]');
      const postId = Number(card?.getAttribute('data-profile-post-id') || 0);
      const act = String(btn.getAttribute('data-post-act') || '');
      if (!postId || !act) return;
      if (act === 'edit') {
        const post = root.__ecProfilePostsById instanceof Map ? root.__ecProfilePostsById.get(postId) : null;
        _profileOpenPostEditPane(root, username, profile, post, card);
        return;
      }
      if (act === 'report') {
        _profileOpenReportPane(root, username, profile, postId, 0, card);
        return;
      }
      const pendingKey = _profilePendingKey('post', postId, act);
      if (_profileIsPending(EC_PROFILE_POST_ACTION_PENDING, pendingKey)) return;
      EC_PROFILE_POST_ACTION_PENDING.add(pendingKey);
      _profileSetButtonBusy(btn, true);
      try {
        if (act === 'react') {
          const isLiked = btn.getAttribute('aria-pressed') === 'true' || btn.classList.contains('is-liked');
          const resp = await fetchWithAuth(`/api/profile/posts/${encodeURIComponent(String(postId))}/react`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ state: !isLiked }),
          });
          const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
          if (!resp || !resp.ok || !data?.success) {
            const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not update reaction') : (data?.error || 'Could not update reaction');
            throw new Error(msg);
          }
        } else if (act === 'delete') {
          const resp = await fetchWithAuth(`/api/profile/posts/${encodeURIComponent(String(postId))}`, { method: 'DELETE' });
          const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
          if (!resp || !resp.ok || !data?.success) {
            const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not delete post') : (data?.error || 'Could not delete post');
            throw new Error(msg);
          }
        } else if (act === 'pin') {
          const isPinned = btn.textContent.toLowerCase().includes('unpin');
          const resp = await fetchWithAuth(`/api/profile/posts/${encodeURIComponent(String(postId))}/pin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ state: !isPinned }),
          });
          const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
          if (!resp || !resp.ok || !data?.success) {
            const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not update pin') : (data?.error || 'Could not update pin');
            throw new Error(msg);
          }
        } else if (act === 'feature') {
          const isFeatured = btn.textContent.toLowerCase().includes('unfeature');
          const resp = await fetchWithAuth(`/api/profile/posts/${encodeURIComponent(String(postId))}/feature`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ state: !isFeatured }),
          });
          const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
          if (!resp || !resp.ok || !data?.success) {
            const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not update featured status') : (data?.error || 'Could not update featured status');
            throw new Error(msg);
          }
        }
        await _loadProfilePosts(root, username, profile);
      } catch (err) {
        try { showToast?.(String(err?.message || err || 'Could not update post')); } catch {}
      } finally {
        EC_PROFILE_POST_ACTION_PENDING.delete(pendingKey);
        _profileSetButtonBusy(btn, false);
      }
    });
  });

  root.querySelectorAll('[data-profile-comment-submit]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const card = btn.closest('[data-profile-post-id]');
      const postId = Number(card?.getAttribute('data-profile-post-id') || 0);
      const input = card?.querySelector('[data-profile-comment-body]');
      const body = String(input?.value || '').trim();
      if (!postId || !body) {
        try { input?.focus(); } catch {}
        return;
      }
      const pendingKey = _profilePendingKey('comment-add', postId);
      if (_profileIsPending(EC_PROFILE_COMMENT_ACTION_PENDING, pendingKey)) return;
      EC_PROFILE_COMMENT_ACTION_PENDING.add(pendingKey);
      _profileSetButtonBusy(btn, true, 'Sending…');
      try {
        const resp = await fetchWithAuth(`/api/profile/posts/${encodeURIComponent(String(postId))}/comments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ body }),
        });
        const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
        if (!resp || !resp.ok || !data?.success) {
          const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not add comment') : (data?.error || 'Could not add comment');
          throw new Error(msg);
        }
        if (input) input.value = '';
        await _loadProfilePosts(root, username, profile);
      } catch (err) {
        try { showToast?.(String(err?.message || err || 'Could not add comment')); } catch {}
      } finally {
        EC_PROFILE_COMMENT_ACTION_PENDING.delete(pendingKey);
        _profileSetButtonBusy(btn, false);
      }
    });
  });

  root.querySelectorAll('[data-profile-comment-body]').forEach((input) => {
    input.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter' || ev.shiftKey) return;
      ev.preventDefault();
      input.closest('.ecProfileCommentForm')?.querySelector('[data-profile-comment-submit]')?.click();
    });
  });

  root.querySelectorAll('[data-comment-act]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const card = btn.closest('[data-profile-post-id]');
      const row = btn.closest('[data-profile-comment-id]');
      const postId = Number(card?.getAttribute('data-profile-post-id') || 0);
      const commentId = Number(row?.getAttribute('data-profile-comment-id') || 0);
      const act = String(btn.getAttribute('data-comment-act') || '');
      if (!postId || !commentId || !act) return;
      if (act === 'report') {
        _profileOpenReportPane(root, username, profile, postId, commentId, row);
        return;
      }
      if (act !== 'delete') return;
      const pendingKey = _profilePendingKey('comment', postId, commentId, act);
      if (_profileIsPending(EC_PROFILE_COMMENT_ACTION_PENDING, pendingKey)) return;
      EC_PROFILE_COMMENT_ACTION_PENDING.add(pendingKey);
      _profileSetButtonBusy(btn, true);
      try {
        const resp = await fetchWithAuth(`/api/profile/posts/${encodeURIComponent(String(postId))}/comments/${encodeURIComponent(String(commentId))}`, { method: 'DELETE' });
        const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
        if (!resp || !resp.ok || !data?.success) {
          const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not delete comment') : (data?.error || 'Could not delete comment');
          throw new Error(msg);
        }
        await _loadProfilePosts(root, username, profile);
      } catch (err) {
        try { showToast?.(String(err?.message || err || 'Could not delete comment')); } catch {}
      } finally {
        EC_PROFILE_COMMENT_ACTION_PENDING.delete(pendingKey);
        _profileSetButtonBusy(btn, false);
      }
    });
  });
}

async function _loadProfilePosts(root, username, profile, opts = {}) {
  if (!root) return;
  const append = !!opts.append;
  const loadSeq = Number(opts.loadSeq || root.__ecProfileLoadSeq || 0) || 0;
  const isStale = () => loadSeq && Number(root.__ecProfileLoadSeq || 0) && Number(root.__ecProfileLoadSeq || 0) !== loadSeq;
  const offset = append ? Number(root.__ecProfilePostOffset || 0) : 0;
  const statusNode = root.querySelector('[data-profile-posts-status]');
  const moreBtn = root.querySelector('[data-profile-posts-more]');
  if (statusNode) statusNode.textContent = append ? 'Loading more posts…' : 'Loading posts…';
  if (moreBtn) moreBtn.disabled = true;
  try {
    const payload = await _fetchProfilePosts(username, EC_PROFILE_POST_PAGE_LIMIT, offset);
    if (isStale()) return;
    _renderProfileFeed(root, username, profile, payload, { append, loadSeq });
  } catch (err) {
    if (statusNode) statusNode.textContent = String(err?.message || err || 'Could not load profile posts');
    if (moreBtn) moreBtn.disabled = false;
    const feedRoot = root.querySelector('[data-profile-feed-root]');
    if (feedRoot && !feedRoot.childElementCount) _profileReplaceTextBlock(feedRoot, 'ecProfileMeta dangerText', 'Could not load posts yet.');
    throw err;
  }
}

function _bindProfilePostsLoadMore(root, username, profile) {
  if (!root || root.dataset.profilePostsLoadMoreBound === '1') return;
  const moreBtn = root.querySelector('[data-profile-posts-more]');
  if (!moreBtn) return;
  root.dataset.profilePostsLoadMoreBound = '1';
  moreBtn.addEventListener('click', () => {
    _loadProfilePosts(root, username, profile, { append: true });
  });
}

function _composerState(root) {
  if (!root.__ecProfileComposerState) {
    root.__ecProfileComposerState = { imageUrl: '', gifUrl: '', linkUrl: '' };
  }
  return root.__ecProfileComposerState;
}

function _renderComposerPreview(root) {
  const state = _composerState(root);
  const preview = root.querySelector('[data-profile-composer-preview]');
  if (!preview) return;
  _profileClearNode(preview);
  const safeGifUrl = ecNormalizeSafeUrl(state.gifUrl, { allowRelative: true, allowExternal: true });
  const safeImageUrl = ecNormalizeSafeUrl(state.imageUrl, { allowRelative: true, allowExternal: true });
  const safeLinkUrl = ecNormalizeSafeUrl(state.linkUrl, { allowRelative: false, allowExternal: true });
  const addPreviewCard = (labelText, imageUrl, altText) => {
    const card = document.createElement('div');
    card.className = 'ecProfileComposerPreviewCard';
    const label = document.createElement('div');
    label.className = 'ecProfileComposerPreviewLabel';
    label.textContent = labelText;
    const img = document.createElement('img');
    img.src = imageUrl;
    img.alt = altText;
    img.loading = 'lazy';
    img.decoding = 'async';
    card.append(label, img);
    preview.appendChild(card);
  };
  if (safeGifUrl) addPreviewCard('GIF attached', safeGifUrl, 'GIF preview');
  if (safeImageUrl) addPreviewCard('Photo attached', safeImageUrl, 'Image preview');
  if (safeLinkUrl) {
    const linkPreview = document.createElement('div');
    linkPreview.className = 'ecProfileComposerPreviewLink';
    linkPreview.textContent = `🔗 ${safeLinkUrl.replace(/^https?:\/\//i, '')}`;
    preview.appendChild(linkPreview);
  }
  if (!preview.childNodes.length) {
    preview.appendChild(_profileBuildTextBlockNode('ecProfileMeta muted', 'Add a GIF, photo, or link to make this post stand out.'));
  }
}

function _bindProfileComposer(root, username, profile) {
  const wrap = root.querySelector('[data-profile-composer-wrap]');
  if (!wrap) return;
  if (!profile?.is_self) {
    _profileReplaceTextBlock(wrap, 'ecProfileMutedBlock', `You can browse ${username}’s posts here. Posting is available on your own profile.`);
    return;
  }

  const state = _composerState(root);
  const textarea = wrap.querySelector('[data-profile-post-body]');
  const visibility = wrap.querySelector('[data-profile-post-visibility]');
  const linkInput = wrap.querySelector('[data-profile-post-link]');
  const pinInput = wrap.querySelector('[data-profile-post-pin]');
  const featureInput = wrap.querySelector('[data-profile-post-feature]');
  const status = wrap.querySelector('[data-profile-post-status]');
  const fileInput = wrap.querySelector('[data-profile-post-file]');
  const submitBtn = wrap.querySelector('[data-profile-post-submit]');

  const setStatus = (msg = '', isError = false) => {
    if (!status) return;
    status.textContent = String(msg || '');
    status.classList.toggle('dangerText', !!isError);
  };

  const appendToTextarea = (chunk) => {
    if (!textarea) return;
    const text = String(chunk || '');
    const before = textarea.value || '';
    const next = `${before}${before && !before.endsWith(' ') ? ' ' : ''}${text}`;
    const limited = (typeof ecLimitCodeEmoticonsInText === 'function') ? ecLimitCodeEmoticonsInText(next, { surface: 'profile post' }) : { text: next, removed: 0 };
    textarea.value = String(limited?.text ?? next);
    if (limited?.removed) setStatus(`Profile posts allow ${limited.max || 15} emoticons. Extra emoticons were removed.`, false);
    textarea.focus();
  };

  wrap.querySelectorAll('[data-insert-emoticon]').forEach((btn) => {
    btn.addEventListener('click', () => appendToTextarea(btn.getAttribute('data-insert-emoticon') || ''));
  });

  wrap.querySelector('[data-profile-post-gif]')?.addEventListener('click', () => {
    openGifPicker((url) => {
      state.gifUrl = String(url || '').trim();
      _renderComposerPreview(root);
      setStatus('GIF attached.');
    });
  });

  wrap.querySelector('[data-profile-post-clear-gif]')?.addEventListener('click', () => {
    state.gifUrl = '';
    _renderComposerPreview(root);
    setStatus('GIF removed.');
  });

  wrap.querySelector('[data-profile-post-upload]')?.addEventListener('click', () => fileInput?.click());
  fileInput?.addEventListener('change', async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const fileError = (typeof ecProfileValidateImageFile === 'function') ? ecProfileValidateImageFile(file, 'post_image') : '';
    if (fileError) {
      setStatus(fileError, true);
      try { showToast?.(`⚠️ ${fileError}`); } catch {}
      try { fileInput.value = ''; } catch {}
      return;
    }
    const form = new FormData();
    form.append('file', file);
    setStatus('Uploading image…');
    try {
      const resp = await fetchWithAuth('/api/profile/post_image_upload', { method: 'POST', body: form });
      const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
      if (!resp || !resp.ok || !data?.success || !data?.image_url) {
        const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not upload image') : (data?.error || 'Could not upload image');
        throw new Error(msg);
      }
      state.imageUrl = String(data.image_url || '').trim();
      _renderComposerPreview(root);
      setStatus('Photo attached.');
    } catch (err) {
      setStatus(String(err?.message || err || 'Could not upload image'), true);
    } finally {
      try { fileInput.value = ''; } catch {}
    }
  });

  wrap.querySelector('[data-profile-post-clear-image]')?.addEventListener('click', () => {
    state.imageUrl = '';
    _renderComposerPreview(root);
    setStatus('Photo removed.');
  });

  textarea?.addEventListener('input', () => {
    const original = String(textarea.value || '');
    const limited = (typeof ecLimitCodeEmoticonsInText === 'function') ? ecLimitCodeEmoticonsInText(original, { surface: 'profile post' }) : { text: original, removed: 0 };
    if (limited?.removed) {
      textarea.value = String(limited.text || '');
      setStatus(`Profile posts allow ${limited.max || 15} emoticons. Extra emoticons were removed.`, false);
    }
  });

  linkInput?.addEventListener('input', () => {
    state.linkUrl = String(linkInput.value || '').trim();
    _renderComposerPreview(root);
  });

  submitBtn?.addEventListener('click', async () => {
    if (root.__ecProfileComposerBusy) return;
    const limitedBody = await _profileLimitPostEmoticons(String(textarea?.value || '').trim(), { notify: true });
    const body = String(limitedBody?.text || '').trim();
    if (textarea && body !== String(textarea.value || '').trim()) textarea.value = body;
    const linkCheck = _profileValidateOptionalLink(state.linkUrl || '', 'profile post link');
    if (!linkCheck.ok) {
      setStatus(linkCheck.error || 'Use a valid http/https link.', true);
      try { linkInput?.focus(); } catch {}
      return;
    }
    const payload = {
      body,
      visibility: _profilePostVisibilityValue(visibility?.value || _profileDefaultPostVisibility(profile)),
      gif_url: state.gifUrl || '',
      image_url: state.imageUrl || '',
      link_url: linkCheck.url,
      pin_post: !!pinInput?.checked,
      feature_post: !!featureInput?.checked,
    };
    if (!payload.body && !payload.gif_url && !payload.image_url && !payload.link_url) {
      setStatus('Add text, a GIF, a photo, or a link before publishing.', true);
      try { textarea?.focus(); } catch {}
      return;
    }
    root.__ecProfileComposerBusy = true;
    _profileSetButtonBusy(submitBtn, true, 'Posting…');
    setStatus('Posting…');
    try {
      const resp = await fetchWithAuth('/api/profile/posts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, {}) : await resp.json().catch(() => ({}));
      if (!resp || !resp.ok || !data?.success) {
        const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(resp, data, 'Could not create post') : (data?.error || 'Could not create post');
        throw new Error(msg);
      }
      textarea.value = '';
      if (visibility) visibility.value = _profileDefaultPostVisibility(profile);
      if (linkInput) linkInput.value = '';
      if (pinInput) pinInput.checked = false;
      if (featureInput) featureInput.checked = false;
      state.imageUrl = '';
      state.gifUrl = '';
      state.linkUrl = '';
      _renderComposerPreview(root);
      setStatus('Post published.');
      await _loadProfilePosts(root, username, profile);
      try { root.querySelector('[data-profile-tab="posts"]')?.click(); } catch {}
    } catch (err) {
      setStatus(String(err?.message || err || 'Could not create post'), true);
    } finally {
      root.__ecProfileComposerBusy = false;
      _profileSetButtonBusy(submitBtn, false);
    }
  });

  _renderComposerPreview(root);
}


function _profileSelectOptions(options, selectedValue) {
  return (Array.isArray(options) ? options : []).map((opt) => {
    const selected = String(opt?.value || '') === String(selectedValue || '') ? ' selected' : '';
    return `<option value="${escapeHtml(String(opt?.value || ''))}"${selected}>${escapeHtml(String(opt?.label || opt?.value || ''))}</option>`;
  }).join('');
}

function _profileSavePayload(profile = {}, patch = {}) {
  const merged = { ...(profile || {}), ...(patch || {}) };
  return {
    avatar_url: String(merged.avatar_url || '').trim(),
    banner_url: String(merged.banner_url || '').trim(),
    profile_accent: String(merged.profile_accent || '').trim(),
    website_url: String(merged.website_url || '').trim(),
    bio: String(merged.bio || '').trim(),
    relationship_status: String(merged.relationship_status || '').trim(),
    relationship_visibility: String(merged.relationship_visibility || 'friends').trim(),
    age: (merged.age === null || merged.age === undefined) ? '' : String(merged.age).trim(),
    age_visibility: String(merged.age_visibility || 'friends').trim(),
    location_text: String(merged.location_text || '').trim(),
    location_visibility: String(merged.location_visibility || 'friends').trim(),
    interests: String(merged.interests || '').trim(),
    favorite_music: String(merged.favorite_music || '').trim(),
    favorite_movies: String(merged.favorite_movies || '').trim(),
    favorite_games: String(merged.favorite_games || '').trim(),
    share_recent_rooms: !!merged.share_recent_rooms,
    recent_rooms_visibility: String(merged.recent_rooms_visibility || 'friends').trim(),
    profile_post_default_visibility: String(merged.profile_post_default_visibility || 'friends').trim(),
  };
}

function _profileOwnerCloseButton() {
  return _profileBtn('miniBtn secondary', '✕', { 'data-profile-editor-close': true });
}

function _profileOwnerDialogNode(panelName, title, ariaLabel, bodyChildren = [], saveButtonText = '', saveAttr = '') {
  const section = _profileEl('section', 'ecProfileOwnerEditorDialog');
  section.hidden = true;
  section.setAttribute('data-profile-owner-panel', panelName);
  section.setAttribute('role', 'dialog');
  section.setAttribute('aria-modal', 'true');
  section.setAttribute('aria-label', ariaLabel || title || panelName);
  const head = _profileEl('div', 'ecProfileOwnerEditorHead');
  head.appendChild(_profileEl('div', 'ecProfileSectionHeader', title));
  head.appendChild(_profileOwnerCloseButton());
  const body = _profileEl('div', 'ecProfileOwnerEditorBody');
  (bodyChildren || []).forEach((child) => body.appendChild(child));
  const actions = _profileEl('div', 'ecProfileOwnerEditorActions');
  actions.appendChild(_profileBtn('miniBtn secondary', 'Cancel', { 'data-profile-editor-close': true }));
  if (saveButtonText && saveAttr) actions.appendChild(_profileBtn('miniBtn', saveButtonText, { [saveAttr]: true }));
  section.append(head, body, actions);
  return section;
}

function _profileOwnerUploadRow(chooseAttr, removeAttr, chooseText = 'Choose image', removeText = 'Remove / reset') {
  const row = _profileEl('div', 'ecProfileUploadRow');
  row.appendChild(_profileBtn('miniBtn secondary', chooseText, { [chooseAttr]: true }));
  row.appendChild(_profileBtn('miniBtn secondary', removeText, { [removeAttr]: true }));
  return row;
}

function _profileBuildPresetTabNode(style, activeKey) {
  const btn = document.createElement('button');
  btn.className = `ecProfilePresetTab${String(style?.key || '') === String(activeKey || '') ? ' is-active' : ''}`;
  btn.type = 'button';
  btn.dataset.style = normalizeDiceBearStyleKey(style?.key || DICEBEAR_DEFAULT_STYLE);
  btn.textContent = String(style?.label || style?.key || 'Avatar');
  return btn;
}


function _profileCreateAvatarPreviewNode(username, avatarUrl = '') {
  const u = String(username || currentUser || 'User').trim() || 'User';
  const wrap = _profileEl('div', 'ecProfileAvatar ecProfileAvatarLarge ecProfileAvatarFacebook ecProfileAvatarPreview');
  const safeUrl = ecNormalizeSafeUrl(avatarUrl || '', { allowRelative: true, allowExternal: true });
  if (safeUrl) {
    const img = document.createElement('img');
    img.src = safeUrl;
    img.alt = `${u} avatar preview`;
    img.referrerPolicy = 'no-referrer';
    wrap.appendChild(img);
  } else {
    const initials = u.slice(0, 2).toUpperCase() || 'EC';
    wrap.appendChild(_profileEl('div', 'ecAvatarStub', initials));
  }
  return wrap;
}

function _profileRenderAvatarPreview(container, username, avatarUrl = '') {
  if (!container) return null;
  _profileClearNode(container);
  const node = _profileCreateAvatarPreviewNode(username, avatarUrl);
  container.appendChild(node);
  return node;
}

function _profileCreateBannerPreviewNode(profile = {}) {
  const card = _profileEl('div', 'ecProfileBannerPreview');
  const safeBanner = ecNormalizeSafeUrl(profile?.banner_url || '', { allowRelative: true, allowExternal: true });
  const accent = /^#[0-9a-f]{6}$/i.test(String(profile?.profile_accent || '').trim())
    ? String(profile.profile_accent).trim()
    : '#6f7cff';
  if (safeBanner) {
    const cssUrl = ecCssUrl(safeBanner, { allowRelative: true, allowExternal: true });
    if (cssUrl) card.setAttribute('style', `background-image:linear-gradient(180deg, rgba(15,23,42,.12), rgba(15,23,42,.66)),${cssUrl};`);
  } else {
    card.setAttribute('style', `background-image:linear-gradient(135deg, ${escapeHtml(accent)}, rgba(15,23,42,.86));`);
  }
  card.appendChild(_profileEl('div', 'ecProfileBannerPreviewLabel', 'Banner preview'));
  return card;
}

function _profileRenderBannerPreview(container, profile = {}) {
  if (!container) return null;
  _profileClearNode(container);
  const node = _profileCreateBannerPreviewNode(profile);
  container.appendChild(node);
  return node;
}

function _profileBuildAvatarPresetButtonNode(card, buttonClass) {
  const btn = document.createElement('button');
  btn.className = buttonClass || 'ecProfilePresetBtn';
  btn.type = 'button';
  btn.dataset.style = normalizeDiceBearStyleKey(card?.style || DICEBEAR_DEFAULT_STYLE);
  btn.dataset.seed = String(card?.seed || '');
  const safeUrl = ecNormalizeSafeUrl(card?.url || '', { allowRelative: false, allowExternal: true });
  btn.dataset.avatarUrl = safeUrl;
  btn.title = `Use DiceBear ${String(card?.style || 'profile')} avatar ${Number(card?.index || 0) || ''}`.trim();
  const img = document.createElement('img');
  img.src = safeUrl;
  img.alt = `DiceBear ${String(card?.style || 'profile')} avatar option ${Number(card?.index || 0) || ''}`.trim();
  img.loading = 'lazy';
  btn.appendChild(img);
  return btn;
}

function _profileOwnerAvatarStudioNode(profile = {}) {
  const username = String(profile.username || currentUser || '').trim() || currentUser || 'User';
  const avatarUrl = ecNormalizeSafeUrl(profile.avatar_url || '', { allowRelative: true, allowExternal: true });
  const card = _profileEl('div', 'ecProfileSectionCard ecProfileSectionCardPremium ecProfileAvatarStudioCard');
  card.setAttribute('data-profile-order-card', 'avatar-studio');
  const header = _profileEl('div', 'ecProfileSectionHeaderRow ecProfileEditableZone');
  header.appendChild(_profileEl('div', 'ecProfileSectionHeader', 'Avatar studio'));
  header.appendChild(_profileBtn('ecProfileInlineHoverEdit', '✎', { 'data-profile-open-editor': 'avatar', 'aria-label': 'Open avatar studio' }));
  card.appendChild(header);
  const top = _profileEl('div', 'ecProfileAvatarStudioTop');
  const previewWrap = _profileEl('div', 'ecProfileAvatarStudioPreview');
  previewWrap.setAttribute('data-profile-inline-avatar-preview-wrap', '');
  previewWrap.appendChild(_profileCreateAvatarPreviewNode(username, avatarUrl));
  const copy = _profileEl('div', 'ecProfileAvatarStudioCopy');
  copy.appendChild(_profileEl('div', 'ecProfileAvatarStudioTitle', 'Choose a DiceBear avatar right from your profile'));
  copy.appendChild(_profileEl('div', 'ecProfileMeta muted', 'Try DiceBear styles here, apply one instantly, or open the full avatar builder for uploads and extra controls.'));
  top.append(previewWrap, copy);
  card.appendChild(top);
  const presetBar = _profileEl('div', 'ecProfilePresetBar');
  const tabs = _profileEl('div', 'ecProfilePresetStyleTabs');
  tabs.setAttribute('data-profile-inline-avatar-tabs', '');
  presetBar.appendChild(tabs);
  presetBar.appendChild(_profileBtn('miniBtn secondary', '🎲 Shuffle', { 'data-profile-inline-avatar-shuffle': true }));
  card.appendChild(presetBar);
  const grid = _profileEl('div', 'ecProfileAvatarStudioGrid');
  grid.setAttribute('data-profile-inline-avatar-grid', '');
  grid.setAttribute('aria-label', 'DiceBear profile avatar choices');
  card.appendChild(grid);
  const status = _profileEl('div', 'ecProfilePresetStatus muted', 'Pick a DiceBear avatar below to preview it here on your profile.');
  status.setAttribute('data-profile-inline-avatar-status', '');
  card.appendChild(status);
  const actions = _profileEl('div', 'ecProfileAvatarStudioActions');
  actions.appendChild(_profileBtn('miniBtn secondary', 'Open full avatar builder', { 'data-profile-inline-avatar-open': true }));
  const apply = _profileBtn('miniBtn', 'Apply selected avatar', { 'data-profile-inline-avatar-apply': true });
  apply.disabled = true;
  actions.appendChild(apply);
  card.appendChild(actions);
  return card;
}

function _profileOwnerAvatarDialogNode(profile = {}) {
  const username = String(profile.username || currentUser || '').trim() || currentUser || 'User';
  const avatarUrl = ecNormalizeSafeUrl(profile.avatar_url || '', { allowRelative: true, allowExternal: true });
  const previewWrap = _profileEl('div');
  previewWrap.setAttribute('data-owner-avatar-preview-wrap', '');
  previewWrap.appendChild(_profileCreateAvatarPreviewNode(username, avatarUrl));
  const file = _profileFormInput('input', { type: 'file', accept: (typeof ecProfileAvatarAcceptMimeTypes === 'function' ? ecProfileAvatarAcceptMimeTypes() : '.png,.jpg,.jpeg,.gif,.webp,.bmp,.ico,image/png,image/jpeg,image/gif,image/webp,image/bmp,image/x-icon,image/vnd.microsoft.icon'), hidden: true });
  file.setAttribute('data-owner-avatar-file', '');
  const status = _profileEl('div', 'ecProfileUploadStatus');
  status.setAttribute('data-owner-avatar-status', '');
  const preset = _profileEl('section', 'ecProfileOwnerAvatarPresetPanel');
  preset.appendChild(_profileEl('div', 'ecProfileSectionHeader', 'DiceBear avatars'));
  preset.appendChild(_profileEl('div', 'ecProfileMeta muted ecDicebearIntro', 'Pick a DiceBear style and shuffle through generated SVG profile pictures.'));
  const bar = _profileEl('div', 'ecProfilePresetBar');
  const tabs = _profileEl('div', 'ecProfilePresetStyleTabs');
  tabs.setAttribute('data-owner-avatar-preset-tabs', '');
  bar.appendChild(tabs);
  bar.appendChild(_profileBtn('miniBtn secondary', '🎲 Shuffle', { 'data-owner-avatar-shuffle': true }));
  preset.appendChild(bar);
  const grid = _profileEl('div', 'ecProfilePresetGrid');
  grid.setAttribute('data-owner-avatar-preset-grid', '');
  grid.setAttribute('aria-label', 'DiceBear profile avatar choices');
  preset.appendChild(grid);
  const presetStatus = _profileEl('div', 'ecProfilePresetStatus muted', 'Click a DiceBear avatar below to preview it here inside your profile.');
  presetStatus.setAttribute('data-owner-avatar-preset-status', '');
  preset.appendChild(presetStatus);
  const presetActions = _profileEl('div', 'ecProfilePresetActions');
  const applyPreset = _profileBtn('miniBtn secondary', '✨ Apply DiceBear avatar', { 'data-owner-avatar-apply-preset': true });
  applyPreset.disabled = true;
  presetActions.appendChild(applyPreset);
  preset.appendChild(presetActions);
  return _profileOwnerDialogNode('avatar', 'Avatar', 'Avatar editor', [
    previewWrap,
    _profileEl('div', 'ecProfileMeta muted', 'Upload an avatar, remove it, or build a DiceBear avatar without leaving the profile page.'),
    file,
    _profileOwnerUploadRow('data-owner-avatar-choose', 'data-owner-avatar-remove', 'Choose avatar', 'Remove / reset'),
    status,
    preset,
  ], 'Save avatar', 'data-owner-avatar-save');
}

function _profileOwnerBannerDialogNode(profile = {}) {
  const accent = /^#[0-9a-f]{6}$/i.test(String(profile.profile_accent || '').trim()) ? String(profile.profile_accent).trim() : '#6f7cff';
  const previewWrap = _profileEl('div');
  previewWrap.setAttribute('data-owner-banner-preview-wrap', '');
  previewWrap.appendChild(_profileCreateBannerPreviewNode(profile));
  const accentInput = _profileFormInput('input', { className: 'ecProfileColorInput', type: 'color', value: accent });
  accentInput.setAttribute('data-owner-banner-accent', '');
  const file = _profileFormInput('input', { type: 'file', accept: (typeof ecProfileBannerAcceptMimeTypes === 'function' ? ecProfileBannerAcceptMimeTypes() : '.png,.jpg,.jpeg,.gif,.webp,.bmp,.ico,image/png,image/jpeg,image/gif,image/webp,image/bmp,image/x-icon,image/vnd.microsoft.icon'), hidden: true });
  file.setAttribute('data-owner-banner-file', '');
  const status = _profileEl('div', 'ecProfileUploadStatus');
  status.setAttribute('data-owner-banner-status', '');
  const grid = _profileEl('div', 'ecProfileGrid ecProfileGridTight');
  grid.appendChild(_profileFormFieldNode('Accent color', accentInput));
  return _profileOwnerDialogNode('banner', 'Banner', 'Banner editor', [
    previewWrap,
    grid,
    file,
    _profileOwnerUploadRow('data-owner-banner-choose', 'data-owner-banner-remove', 'Choose banner', 'Remove / reset'),
    status,
  ], 'Save banner', 'data-owner-banner-save');
}

function _profileOwnerEditorPanelsNode(profile = {}) {
  const relationshipStatus = String(profile.relationship_status || '').trim();
  const ageValue = (profile.age === null || profile.age === undefined || profile.age === '') ? '' : String(profile.age).trim();
  const locationText = String(profile.location_text || '').trim();
  const interests = String(profile.interests || '').trim();
  const websiteUrl = ecNormalizeSafeUrl(profile.website_url || '', { allowRelative: false, allowExternal: true });
  const favoriteMusic = String(profile.favorite_music || '').trim();
  const favoriteMovies = String(profile.favorite_movies || '').trim();
  const favoriteGames = String(profile.favorite_games || '').trim();
  const shareRecentRooms = !!profile.share_recent_rooms;
  const recentRoomsVisibility = String(profile.recent_rooms_visibility || 'friends').trim();
  const profilePostDefaultVisibility = String(profile.profile_post_default_visibility || 'friends').trim();
  const bio = String(profile.bio || '').trim();

  const layer = _profileEl('div', 'ecProfileOwnerEditorLayer');
  layer.hidden = true;
  layer.setAttribute('data-profile-editor-layer', '');
  const backdrop = _profileEl('div', 'ecProfileOwnerEditorBackdrop');
  backdrop.setAttribute('data-profile-editor-close', '');
  layer.appendChild(backdrop);
  layer.appendChild(_profileOwnerAvatarDialogNode(profile));
  layer.appendChild(_profileOwnerBannerDialogNode(profile));

  layer.appendChild(_profileOwnerDialogNode('bio', 'Bio', 'Bio editor', [
    _profileFormFieldNode('Bio', _profileFormInput('textarea', { className: 'ecProfileTextarea', maxLength: 280, placeholder: 'Tell people about yourself', value: bio, 'data-owner-bio-input': true })),
  ], 'Save bio', 'data-owner-bio-save'));

  const introGrid = _profileEl('div', 'ecProfileGrid ecProfileGridTight');
  introGrid.appendChild(_profileFormFieldNode('Relationship', _profileSelectNode(EC_PROFILE_RELATIONSHIP_OPTIONS, relationshipStatus, { 'data-owner-intro-relationship': true })));
  introGrid.appendChild(_profileFormFieldNode('Age', _profileFormInput('input', { className: 'ecProfileInput', type: 'number', min: '1', max: '120', value: ageValue, 'data-owner-intro-age': true })));
  layer.appendChild(_profileOwnerDialogNode('intro', 'Intro', 'Intro editor', [
    introGrid,
    _profileFormFieldNode('Location', _profileFormInput('input', { className: 'ecProfileInput', type: 'text', maxLength: 80, value: locationText, 'data-owner-intro-location': true })),
    _profileFormFieldNode('Interests', _profileFormInput('textarea', { className: 'ecProfileTextarea ecProfileTextareaCompact', maxLength: 240, value: interests, 'data-owner-intro-interests': true })),
    _profileFormFieldNode('Website', _profileFormInput('input', { className: 'ecProfileInput', type: 'url', value: websiteUrl, placeholder: 'https://example.com', 'data-owner-intro-website': true })),
  ], 'Save intro', 'data-owner-intro-save'));

  layer.appendChild(_profileOwnerDialogNode('favorites', 'Favorites', 'Favorites editor', [
    _profileFormFieldNode('Music', _profileFormInput('input', { className: 'ecProfileInput', type: 'text', maxLength: 120, value: favoriteMusic, 'data-owner-fav-music': true })),
    _profileFormFieldNode('Movies / shows', _profileFormInput('input', { className: 'ecProfileInput', type: 'text', maxLength: 120, value: favoriteMovies, 'data-owner-fav-movies': true })),
    _profileFormFieldNode('Games', _profileFormInput('input', { className: 'ecProfileInput', type: 'text', maxLength: 120, value: favoriteGames, 'data-owner-fav-games': true })),
  ], 'Save favorites', 'data-owner-favorites-save'));

  const roomsToggle = _profileEl('label', 'ecProfileToggleRow');
  roomsToggle.appendChild(_profileEl('span', '', 'Share my last 3 joined rooms on my profile'));
  const roomsCheckbox = _profileFormInput('input', { type: 'checkbox', checked: shareRecentRooms });
  roomsCheckbox.setAttribute('data-owner-rooms-share', '');
  roomsToggle.appendChild(roomsCheckbox);
  layer.appendChild(_profileOwnerDialogNode('recent-rooms', 'Recent rooms', 'Recent rooms editor', [
    roomsToggle,
    _profileFormFieldNode('Who can see recent rooms?', _profileSelectNode(EC_PROFILE_VISIBILITY_OPTIONS, recentRoomsVisibility, { 'data-owner-rooms-visibility': true })),
    _profileFormFieldNode('Default profile post visibility', _profileSelectNode(EC_PROFILE_VISIBILITY_OPTIONS, profilePostDefaultVisibility, { 'data-owner-post-default-visibility': true })),
  ], 'Save recent rooms', 'data-owner-rooms-save'));

  return layer;
}

function _bindProfileSelfEditing(root, win, username, profile) {
  if (!root || !profile?.is_self) return;
  const layer = root.querySelector('[data-profile-editor-layer]');
  if (!layer) return;
  const state = {
    avatarFile: null,
    avatarRemoved: false,
    bannerFile: null,
    bannerRemoved: false,
    avatarPresetStyle: (detectAvatarPresetSelection(String(profile.avatar_url || '').trim())?.style || LOCAL_AVATAR_PRESET_STYLES?.[0]?.key || DICEBEAR_DEFAULT_STYLE),
    avatarPresetPage: 0,
    avatarPresetSelected: detectAvatarPresetSelection(String(profile.avatar_url || '').trim()) ? { ...detectAvatarPresetSelection(String(profile.avatar_url || '').trim()), url: String(profile.avatar_url || '').trim() } : null,
    avatarPreviewObjectUrl: '',
    bannerPreviewObjectUrl: '',
  };

  const cleanupPreviewObjectUrls = () => {
    _profileRevokeObjectUrl(state.avatarPreviewObjectUrl);
    _profileRevokeObjectUrl(state.bannerPreviewObjectUrl);
    state.avatarPreviewObjectUrl = '';
    state.bannerPreviewObjectUrl = '';
  };
  try { registerWindowCleanup(win, cleanupPreviewObjectUrls); } catch {}

  const closePanels = () => {
    layer.hidden = true;
    layer.querySelectorAll('[data-profile-owner-panel]').forEach((panel) => { panel.hidden = true; });
  };

  const openPanel = (name) => {
    if (!name) return;
    layer.hidden = false;
    layer.querySelectorAll('[data-profile-owner-panel]').forEach((panel) => {
      panel.hidden = panel.getAttribute('data-profile-owner-panel') !== name;
    });
  };

  const refreshSelfProfile = async () => {
    try { await refreshMyProfileInHub(); } catch {}
    openProfileWindow(username, { fitMode: String(win?.dataset?.profileOpenMode || 'editor') });
  };

  const savePatch = (patch, successText = 'Profile updated') => new Promise((resolve) => {
    const payload = _profileSavePayload(profile, patch);
    socket.emit('set_my_profile', payload, async (res) => {
      if (!res?.success) {
        toast(`❌ ${res?.error || 'Profile update failed'}`, 'error');
        resolve(false);
        return;
      }
      UIState.myProfile = res.profile || { ...(UIState.myProfile || {}), ...payload, username };
      renderMyHubIdentity(UIState.myProfile);
      toast(`✅ ${successText}`, 'ok');
      closePanels();
      await refreshSelfProfile();
      resolve(true);
    });
  });

  root.querySelectorAll('[data-profile-open-editor]').forEach((btn) => {
    btn.addEventListener('click', () => openPanel(String(btn.getAttribute('data-profile-open-editor') || '')));
  });
  layer.querySelectorAll('[data-profile-editor-close]').forEach((btn) => btn.addEventListener('click', closePanels));
  layer.querySelector('[data-profile-editor-close]');
  layer.querySelector('.ecProfileOwnerEditorBackdrop')?.addEventListener('click', closePanels);

  const avatarPreviewWrap = layer.querySelector('[data-owner-avatar-preview-wrap]');
  const avatarFileInput = layer.querySelector('[data-owner-avatar-file]');
  const avatarStatus = layer.querySelector('[data-owner-avatar-status]');
  const avatarPresetTabs = layer.querySelector('[data-owner-avatar-preset-tabs]');
  const avatarPresetGrid = layer.querySelector('[data-owner-avatar-preset-grid]');
  const avatarPresetStatus = layer.querySelector('[data-owner-avatar-preset-status]');
  const avatarApplyPresetBtn = layer.querySelector('[data-owner-avatar-apply-preset]');
  const avatarShufflePresetBtn = layer.querySelector('[data-owner-avatar-shuffle]');
  const inlineAvatarPreviewWrap = root.querySelector('[data-profile-inline-avatar-preview-wrap]');
  const inlineAvatarTabs = root.querySelector('[data-profile-inline-avatar-tabs]');
  const inlineAvatarGrid = root.querySelector('[data-profile-inline-avatar-grid]');
  const inlineAvatarStatus = root.querySelector('[data-profile-inline-avatar-status]');
  const inlineAvatarApplyBtn = root.querySelector('[data-profile-inline-avatar-apply]');
  const inlineAvatarOpenBtn = root.querySelector('[data-profile-inline-avatar-open]');
  const inlineAvatarShuffleBtn = root.querySelector('[data-profile-inline-avatar-shuffle]');
  const setAvatarStatus = (msg = '', kind = 'muted') => {
    if (!avatarStatus) return;
    avatarStatus.textContent = String(msg || '');
    avatarStatus.classList.toggle('dangerText', kind === 'error');
  };
  const setInlineAvatarStatus = (msg = '', kind = 'muted') => {
    if (!inlineAvatarStatus) return;
    inlineAvatarStatus.textContent = String(msg || '');
    inlineAvatarStatus.classList.toggle('dangerText', kind === 'error');
  };
  const renderInlineAvatarPreview = (previewUrl = '', removed = false) => {
    if (!inlineAvatarPreviewWrap) return;
    _profileRenderAvatarPreview(inlineAvatarPreviewWrap, username, removed ? '' : (previewUrl || state.avatarPresetSelected?.url || profile.avatar_url || ''));
  };
  const updateInlineAvatarStatus = () => {
    if (!inlineAvatarStatus) return;
    if (state.avatarRemoved) {
      setInlineAvatarStatus('Avatar will be cleared when you save or upload a new one.');
      return;
    }
    if (state.avatarPresetSelected?.url) {
      const sameAsCurrent = String(state.avatarPresetSelected.url || '') === String(profile.avatar_url || '').trim();
      setInlineAvatarStatus(sameAsCurrent ? 'This DiceBear avatar is already active.' : 'Built-in avatar selected. Apply it here or open the full studio.');
      return;
    }
    setInlineAvatarStatus('Pick a DiceBear avatar below to preview it here on your profile.');
  };
  const updateOwnerAvatarPresetStatus = () => {
    if (!avatarPresetStatus) return;
    if (state.avatarRemoved) {
      avatarPresetStatus.textContent = 'Avatar will be cleared when you save.';
      return;
    }
    if (state.avatarPresetSelected?.url) {
      const sameAsCurrent = String(state.avatarPresetSelected.url || '') === String(profile.avatar_url || '').trim();
      avatarPresetStatus.textContent = sameAsCurrent
        ? 'This DiceBear avatar is already active for your profile.'
        : 'DiceBear avatar selected. Apply it now or keep browsing.';
      return;
    }
    avatarPresetStatus.textContent = 'Click a DiceBear avatar below to preview it here inside your profile.';
  };
  const renderAvatarPreview = (previewUrl = '', removed = false) => {
    if (avatarPreviewWrap) {
      _profileRenderAvatarPreview(avatarPreviewWrap, username, removed ? '' : (previewUrl || state.avatarPresetSelected?.url || profile.avatar_url || ''));
    }
    renderInlineAvatarPreview(previewUrl, removed);
  };
  const updateOwnerAvatarPresetSelection = () => {
    const currentAvatarUrl = String(profile.avatar_url || '').trim();
    const pendingAvatarUrl = String(state.avatarPresetSelected?.url || '').trim();
    avatarPresetGrid?.querySelectorAll('.ecProfilePresetBtn').forEach((btn) => {
      const btnUrl = String(btn.dataset.avatarUrl || '');
      btn.classList.toggle('is-selected', btnUrl === currentAvatarUrl);
      btn.classList.toggle('is-pending', !!pendingAvatarUrl && btnUrl === pendingAvatarUrl && btnUrl !== currentAvatarUrl);
    });
    inlineAvatarGrid?.querySelectorAll('.ecProfileAvatarStudioBtn').forEach((btn) => {
      const btnUrl = String(btn.dataset.avatarUrl || '');
      btn.classList.toggle('is-selected', btnUrl === currentAvatarUrl);
      btn.classList.toggle('is-pending', !!pendingAvatarUrl && btnUrl === pendingAvatarUrl && btnUrl !== currentAvatarUrl);
    });
    if (avatarApplyPresetBtn) avatarApplyPresetBtn.disabled = !pendingAvatarUrl;
    if (inlineAvatarApplyBtn) inlineAvatarApplyBtn.disabled = !pendingAvatarUrl;
    updateOwnerAvatarPresetStatus();
    updateInlineAvatarStatus();
  };
  const renderOwnerAvatarPresetTabs = () => {
    [avatarPresetTabs, inlineAvatarTabs].forEach((host) => {
      if (!host) return;
      _profileClearNode(host);
      LOCAL_AVATAR_PRESET_STYLES.forEach((style) => {
        const btn = _profileBuildPresetTabNode(style, state.avatarPresetStyle);
        btn.addEventListener('click', () => {
          state.avatarPresetStyle = normalizeDiceBearStyleKey(btn.dataset.style || DICEBEAR_DEFAULT_STYLE);
          state.avatarPresetPage = 0;
          renderOwnerAvatarPresetTabs();
          renderOwnerAvatarPresetGrid();
        });
        host.appendChild(btn);
      });
    });
  };
  const renderOwnerAvatarPresetGrid = () => {
    const cards = [];
    const baseIndex = state.avatarPresetPage * 12;
    for (let i = 0; i < 12; i += 1) {
      const seed = buildAvatarPresetSeed(username, state.avatarPresetStyle, baseIndex + i + 1);
      const url = buildDiceBearAvatarUrl(state.avatarPresetStyle, seed, { backgroundColor: DICEBEAR_DEFAULT_BG, borderRadius: 50 });
      cards.push({
        style: normalizeDiceBearStyleKey(state.avatarPresetStyle),
        seed,
        url,
        index: i + 1,
      });
    }
    if (avatarPresetGrid) {
      _profileClearNode(avatarPresetGrid);
      cards.forEach((card) => avatarPresetGrid.appendChild(_profileBuildAvatarPresetButtonNode(card, 'ecProfilePresetBtn')));
    }
    if (inlineAvatarGrid) {
      _profileClearNode(inlineAvatarGrid);
      cards.slice(0, 6).forEach((card) => inlineAvatarGrid.appendChild(_profileBuildAvatarPresetButtonNode(card, 'ecProfileAvatarStudioBtn')));
    }
    const choosePreset = (btn, applyNow = false) => {
      if (!btn) return;
      state.avatarFile = null;
      state.avatarRemoved = false;
      _profileRevokeObjectUrl(state.avatarPreviewObjectUrl);
      state.avatarPreviewObjectUrl = '';
      if (avatarFileInput) avatarFileInput.value = '';
      state.avatarPresetSelected = {
        style: normalizeDiceBearStyleKey(btn.dataset.style || state.avatarPresetStyle || DICEBEAR_DEFAULT_STYLE),
        seed: String(btn.dataset.seed || ''),
        url: String(btn.dataset.avatarUrl || ''),
      };
      renderAvatarPreview(String(state.avatarPresetSelected.url || ''), false);
      setAvatarStatus('DiceBear avatar selected. Apply it now or click Save avatar later.');
      setInlineAvatarStatus('DiceBear avatar selected. Apply it here or open the full avatar builder.');
      updateOwnerAvatarPresetSelection();
      if (applyNow) {
        if (btn.classList.contains('ecProfilePresetBtn')) avatarApplyPresetBtn?.click();
        else inlineAvatarApplyBtn?.click();
      }
    };
    avatarPresetGrid?.querySelectorAll('.ecProfilePresetBtn').forEach((btn) => {
      btn.addEventListener('click', (ev) => {
        try { ev.preventDefault(); ev.stopPropagation(); } catch {}
        choosePreset(btn, false);
      });
      btn.addEventListener('dblclick', (ev) => {
        try { ev.preventDefault(); ev.stopPropagation(); } catch {}
        choosePreset(btn, true);
      });
    });
    inlineAvatarGrid?.querySelectorAll('.ecProfileAvatarStudioBtn').forEach((btn) => {
      btn.addEventListener('click', (ev) => {
        try { ev.preventDefault(); ev.stopPropagation(); } catch {}
        choosePreset(btn, false);
      });
      btn.addEventListener('dblclick', (ev) => {
        try { ev.preventDefault(); ev.stopPropagation(); } catch {}
        choosePreset(btn, true);
      });
    });
    updateOwnerAvatarPresetSelection();
  };

  layer.querySelector('[data-owner-avatar-choose]')?.addEventListener('click', () => avatarFileInput?.click());
  avatarFileInput?.addEventListener('change', async () => {
    const file = avatarFileInput.files?.[0] || null;
    state.avatarFile = null;
    state.avatarRemoved = false;
    state.avatarPresetSelected = null;
    _profileRevokeObjectUrl(state.avatarPreviewObjectUrl);
    state.avatarPreviewObjectUrl = '';
    if (file) {
      const fileError = (typeof ecProfileValidateImageFile === 'function') ? ecProfileValidateImageFile(file, 'avatar') : '';
      if (fileError) {
        setAvatarStatus(fileError, 'error');
        try { showToast?.(`⚠️ ${fileError}`); } catch {}
        try { avatarFileInput.value = ''; } catch {}
        renderAvatarPreview('', false);
        updateOwnerAvatarPresetSelection();
        return;
      }
      state.avatarFile = file;
      const previewUrl = _profileSafeObjectUrl(file, state, 'avatarPreviewObjectUrl');
      renderAvatarPreview(previewUrl, false);
      setAvatarStatus(`Uploading ${file.name}…`);
      await uploadMyAvatarFile(file, {
        setUploadStatus: setAvatarStatus,
        afterSuccess: async () => {
          state.avatarFile = null;
          _profileRevokeObjectUrl(state.avatarPreviewObjectUrl);
          state.avatarPreviewObjectUrl = '';
          closePanels();
          await refreshSelfProfile();
        },
      });
      try { avatarFileInput.value = ''; } catch {}
    }
    updateOwnerAvatarPresetSelection();
  });
  layer.querySelector('[data-owner-avatar-remove]')?.addEventListener('click', () => {
    state.avatarFile = null;
    state.avatarRemoved = true;
    state.avatarPresetSelected = null;
    _profileRevokeObjectUrl(state.avatarPreviewObjectUrl);
    state.avatarPreviewObjectUrl = '';
    if (avatarFileInput) avatarFileInput.value = '';
    renderAvatarPreview('', true);
    setAvatarStatus('Avatar will be removed when you save.');
    updateOwnerAvatarPresetSelection();
  });
  avatarShufflePresetBtn?.addEventListener('click', () => {
    state.avatarPresetPage += 1;
    renderOwnerAvatarPresetGrid();
  });
  inlineAvatarShuffleBtn?.addEventListener('click', () => {
    state.avatarPresetPage += 1;
    renderOwnerAvatarPresetGrid();
  });
  inlineAvatarOpenBtn?.addEventListener('click', () => openPanel('avatar'));
  avatarApplyPresetBtn?.addEventListener('click', async () => {
    if (!state.avatarPresetSelected || !String(state.avatarPresetSelected.url || '').trim()) {
      toast('⚠️ Choose a DiceBear avatar first', 'warn');
      return;
    }
    await savePatch({ avatar_url: String(state.avatarPresetSelected.url || '').trim() }, 'Avatar updated');
  });
  inlineAvatarApplyBtn?.addEventListener('click', async () => {
    if (!state.avatarPresetSelected || !String(state.avatarPresetSelected.url || '').trim()) {
      toast('⚠️ Choose a DiceBear avatar first', 'warn');
      return;
    }
    await savePatch({ avatar_url: String(state.avatarPresetSelected.url || '').trim() }, 'Avatar updated');
  });
  layer.querySelector('[data-owner-avatar-save]')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    if (btn) btn.disabled = true;
    try {
      if (state.avatarRemoved && !state.avatarFile) {
        await savePatch({ avatar_url: '' }, 'Avatar updated');
      } else if (state.avatarFile) {
        await uploadMyAvatarFile(state.avatarFile, {
          uploadBtn: btn,
          setUploadStatus: setAvatarStatus,
          afterSuccess: async () => {
            _profileRevokeObjectUrl(state.avatarPreviewObjectUrl);
            state.avatarPreviewObjectUrl = '';
            closePanels();
            await refreshSelfProfile();
          },
        });
      } else if (state.avatarPresetSelected?.url) {
        await savePatch({ avatar_url: String(state.avatarPresetSelected.url || '').trim() }, 'Avatar updated');
      } else {
        closePanels();
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  renderOwnerAvatarPresetTabs();
  renderOwnerAvatarPresetGrid();
  renderAvatarPreview('', false);

  const bannerPreviewWrap = layer.querySelector('[data-owner-banner-preview-wrap]');
  const bannerFileInput = layer.querySelector('[data-owner-banner-file]');
  const bannerStatus = layer.querySelector('[data-owner-banner-status]');
  const bannerAccentInput = layer.querySelector('[data-owner-banner-accent]');
  const setBannerStatus = (msg = '', kind = 'muted') => {
    if (!bannerStatus) return;
    bannerStatus.textContent = String(msg || '');
    bannerStatus.classList.toggle('dangerText', kind === 'error');
  };
  const renderBannerPreview = (previewUrl = '', removed = false) => {
    if (!bannerPreviewWrap) return;
    const bannerProfile = {
      ...profile,
      banner_url: removed ? '' : (previewUrl || profile.banner_url || ''),
      profile_accent: String(bannerAccentInput?.value || profile.profile_accent || '#6f7cff').trim(),
    };
    _profileRenderBannerPreview(bannerPreviewWrap, bannerProfile);
  };
  layer.querySelector('[data-owner-banner-choose]')?.addEventListener('click', () => bannerFileInput?.click());
  bannerFileInput?.addEventListener('change', () => {
    const file = bannerFileInput.files?.[0] || null;
    state.bannerFile = null;
    state.bannerRemoved = false;
    _profileRevokeObjectUrl(state.bannerPreviewObjectUrl);
    state.bannerPreviewObjectUrl = '';
    if (file) {
      const fileError = (typeof ecProfileValidateImageFile === 'function') ? ecProfileValidateImageFile(file, 'banner') : '';
      if (fileError) {
        setBannerStatus(fileError, 'error');
        try { showToast?.(`⚠️ ${fileError}`); } catch {}
        try { bannerFileInput.value = ''; } catch {}
        renderBannerPreview('', false);
        return;
      }
      state.bannerFile = file;
      const previewUrl = _profileSafeObjectUrl(file, state, 'bannerPreviewObjectUrl');
      renderBannerPreview(previewUrl, false);
      setBannerStatus(`Ready to upload ${file.name}`);
    }
  });
  bannerAccentInput?.addEventListener('input', () => renderBannerPreview(state.bannerPreviewObjectUrl || '', state.bannerRemoved));
  layer.querySelector('[data-owner-banner-remove]')?.addEventListener('click', () => {
    state.bannerFile = null;
    state.bannerRemoved = true;
    _profileRevokeObjectUrl(state.bannerPreviewObjectUrl);
    state.bannerPreviewObjectUrl = '';
    if (bannerFileInput) bannerFileInput.value = '';
    renderBannerPreview('', true);
    setBannerStatus('Banner will be removed when you save.');
  });
  layer.querySelector('[data-owner-banner-save]')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    if (btn) btn.disabled = true;
    try {
      const accentPatch = { profile_accent: String(bannerAccentInput?.value || profile.profile_accent || '#6f7cff').trim() };
      if (state.bannerRemoved && !state.bannerFile) {
        await savePatch({ ...accentPatch, banner_url: '' }, 'Banner updated');
      } else if (state.bannerFile) {
        await uploadMyBannerFile(state.bannerFile, {
          uploadBtn: btn,
          setUploadStatus: setBannerStatus,
          afterSuccess: async () => {
            _profileRevokeObjectUrl(state.bannerPreviewObjectUrl);
            state.bannerPreviewObjectUrl = '';
            await savePatch(accentPatch, 'Banner updated');
          },
        });
      } else {
        await savePatch(accentPatch, 'Banner updated');
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  layer.querySelector('[data-owner-bio-save]')?.addEventListener('click', () => {
    savePatch({ bio: String(layer.querySelector('[data-owner-bio-input]')?.value || '').trim() }, 'Bio updated');
  });
  layer.querySelector('[data-owner-intro-save]')?.addEventListener('click', () => {
    savePatch({
      relationship_status: String(layer.querySelector('[data-owner-intro-relationship]')?.value || '').trim(),
      age: String(layer.querySelector('[data-owner-intro-age]')?.value || '').trim(),
      location_text: String(layer.querySelector('[data-owner-intro-location]')?.value || '').trim(),
      interests: String(layer.querySelector('[data-owner-intro-interests]')?.value || '').trim(),
      website_url: String(layer.querySelector('[data-owner-intro-website]')?.value || '').trim(),
    }, 'Intro updated');
  });
  layer.querySelector('[data-owner-favorites-save]')?.addEventListener('click', () => {
    savePatch({
      favorite_music: String(layer.querySelector('[data-owner-fav-music]')?.value || '').trim(),
      favorite_movies: String(layer.querySelector('[data-owner-fav-movies]')?.value || '').trim(),
      favorite_games: String(layer.querySelector('[data-owner-fav-games]')?.value || '').trim(),
    }, 'Favorites updated');
  });
  layer.querySelector('[data-owner-rooms-save]')?.addEventListener('click', () => {
    savePatch({
      share_recent_rooms: !!layer.querySelector('[data-owner-rooms-share]')?.checked,
      recent_rooms_visibility: String(layer.querySelector('[data-owner-rooms-visibility]')?.value || 'friends').trim(),
      profile_post_default_visibility: String(layer.querySelector('[data-owner-post-default-visibility]')?.value || 'friends').trim(),
    }, 'Privacy defaults updated');
  });
}

async function _profileFetchDirectHttp(username, opts = {}) {
  const u = String(username || '').trim();
  if (!u) return { success: false, error: 'missing_username' };
  const timeoutMs = Math.max(900, Number(opts.timeoutMs || 4500) || 4500);
  let controller = null;
  let timer = null;
  try {
    const path = `/api/profile/${encodeURIComponent(u)}`;
    const requestUrl = `${path}${path.includes('?') ? '&' : '?'}_=${Date.now()}`;
    const requestOpts = {
      method: 'GET',
      credentials: 'include',
      cache: 'no-store',
      headers: {
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
    };
    if (typeof AbortController === 'function') {
      controller = new AbortController();
      requestOpts.signal = controller.signal;
      timer = setTimeout(() => {
        try { controller.abort(); } catch {}
      }, timeoutMs);
    }
    const response = await fetch(requestUrl, requestOpts);
    let text = '';
    try { text = await response.text(); } catch (_) { text = ''; }
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch (_) { payload = null; }
    if (!response.ok) {
      return payload || { success: false, error: `http_${response.status}` };
    }
    if (payload && payload.success === true && payload.profile) return payload;
    if (payload && payload.ok === true && payload.profile) return { success: true, profile: payload.profile };
    if (payload && payload.profile) return { success: true, profile: payload.profile };
    if (payload && payload.user) return { success: true, profile: payload.user };
    return { success: false, error: 'profile_payload_missing', payload };
  } catch (err) {
    const isAbort = String(err?.name || '').toLowerCase() === 'aborterror';
    return { success: false, error: isAbort ? 'profile_http_timeout' : String(err?.message || err || 'profile_http_error') };
  } finally {
    try { if (timer) clearTimeout(timer); } catch {}
  }
}

function openProfileWindow(username, opts = {}) {
  const u = String(username || '').trim();
  if (!u) return;

  const id = 'profile:' + _profileWindowKey(u);
  const title = ((typeof ecNormalizeUsernameKey === 'function' ? ecNormalizeUsernameKey(u) === ecNormalizeUsernameKey(currentUser) : u === currentUser)) ? `My profile — ${u}` : `Profile — ${u}`;
  const win = createWindow({ id, title, kind: 'room' });
  if (!win) return;

  try {
    win.classList.add('ecProfileWindow');
    const compose = win.querySelector('.ym-compose');
    if (compose) compose.style.display = 'none';
    _ensureProfileFullscreenButton(win);
  } catch {}

  const fitMode = String(opts?.fitMode || win.dataset.profileOpenMode || 'public');
  try {
    if (fitMode === 'editor') {
      _fitProfileWindow(win, 'editor');
    } else {
      _fitProfileWindow(win, 'public');
    }
    win.dataset.profileSized = '1';
    win.dataset.profileOpenMode = fitMode;
  } catch {}

  // Profiles open in the expanded app workspace by default, matching the room
  // selector/chat workspace size instead of the smaller floating window layout.
  try { _profileSetFullscreen(win, true); } catch {}
  const topLockToken = _profileStartTopLock(win);
  _profileForceTopScroll(win);
  bringToFront(win);

  const loadSeq = (Number(win.__ecProfileLoadSeq || 0) || 0) + 1;
  win.__ecProfileLoadSeq = loadSeq;
  const isProfileLoadStale = () => Number(win.__ecProfileLoadSeq || 0) !== loadSeq;

  if (win._ym?.log) {
    try { win._profileViewCleanup?.(); } catch {}
    win._profileViewCleanup = null;
    _profileClearNode(win._ym.log);
    _profileForceTopScroll(win._ym.log);
    win._ym.log.appendChild(_profileStatusCardNode(u, 'Opening profile shell. Loading live profile details…'));
    _profileForceTopScroll(win._ym.log);
  }

  (async () => {
    const showFailure = (message, detail = '') => {
      if (isProfileLoadStale()) return;
      const log = win._ym?.log;
      if (!log) return;
      _profileClearNode(log);
      const card = _profileStatusCardNode(u, message || 'Profile not available', { danger: true });
      const hint = document.createElement('div');
      hint.className = 'ecProfileMeta';
      hint.textContent = detail || ('Profile failed to load. Check the server log for GET /api/profile/' + u + '.');
      card.appendChild(hint);
      log.appendChild(card);
      _profileReleaseTopLock(win, topLockToken, 0);
    };

    try {
      let res = null;
      try {
        res = await _profileFetchDirectHttp(u, { timeoutMs: 4500 });
        if (!res?.success && typeof fetchUserProfileForUI === 'function') {
          const fallback = await fetchUserProfileForUI(u, { timeoutMs: 2500 });
          if (fallback?.success && fallback?.profile) res = fallback;
        }
      } catch (err) {
        res = { success: false, error: String(err?.message || err || 'profile_fetch_exception') };
      }
      if (isProfileLoadStale()) return;
      const log = win._ym?.log;
      if (!log) return;
      log.__ecProfileLoadSeq = loadSeq;
      if (!res?.success || !res?.profile) {
        showFailure(res?.error || 'Profile not available');
        return;
      }

      const p = res.profile || {};

      // Immediately replace the loading card with a simple, dependency-light profile
      // view. If the richer profile renderer ever throws, users still see the
      // profile instead of being stranded on “Loading profile page…”.
      try {
        _profileClearNode(log);
        _profileForceTopScroll(log);
        log.appendChild(_profileBuildBasicProfileNode(p, u, { note: 'Profile loaded. Building full profile view…' }));
        _profileForceTopScroll(log);
      } catch (basicErr) {
        console.warn(`${((typeof SERVER_NAME !== 'undefined' && SERVER_NAME) ? String(SERVER_NAME) : 'Hui Chat')} basic profile renderer failed`, basicErr);
      }

      const online = !!p.online;
      const pres = String(p.presence || (online ? 'online' : 'offline'));
      const custom = String(p.custom_status || '');
      const bio = String(p.bio || '');
      const avatar = ecNormalizeSafeUrl(p.avatar_url || '', { allowRelative: true, allowExternal: true });
      const interests = String(p.interests || '');
      const favoriteMusic = String(p.favorite_music || '');
      const favoriteMovies = String(p.favorite_movies || '');
      const favoriteGames = String(p.favorite_games || '');
      const websiteUrl = ecNormalizeSafeUrl(p.website_url || '', { allowRelative: false, allowExternal: true });
      const relationshipStatus = String(p.relationship_status || '').trim();
      const age = (p?.age === null || p?.age === undefined || p?.age === '') ? '' : String(p.age);
      const locationText = String(p.location_text || '').trim();
      const recentRooms = Array.isArray(p.recent_rooms) ? p.recent_rooms.map((item) => {
        if (item && typeof item === 'object') {
          return { name: String(item.name || '').trim(), is_current: !!item.is_current };
        }
        return { name: String(item || '').trim(), is_current: false };
      }).filter((item) => item.name) : [];
      const mutualFriends = Array.isArray(p.mutual_friends) ? p.mutual_friends.map((v) => String(v || '').trim()).filter(Boolean) : [];
      const mutualGroups = Array.isArray(p.mutual_groups) ? p.mutual_groups.map((v) => String(v || '').trim()).filter(Boolean) : [];
      const mutualRooms = Array.isArray(p.mutual_rooms) ? p.mutual_rooms.map((v) => String(v || '').trim()).filter(Boolean) : [];
      const mutualFriendsCount = Math.max(Number(p.mutual_friends_count || 0) || 0, mutualFriends.length);
      const mutualGroupsCount = Math.max(Number(p.mutual_groups_count || 0) || 0, mutualGroups.length);
      const mutualRoomsCount = Math.max(Number(p.mutual_rooms_count || 0) || 0, mutualRooms.length);
      const lastSeen = _fmtLocalTime(p.last_seen);
      const created = _fmtLocalTime(p.created_at);
      const isFriend = !!p.is_friend;
      const blockedByMe = !!p.blocked_by_me;
      const blocksMe = !!p.blocks_me;
      const isSelf = !!p.is_self;

      const presDot = online ? (pres === 'busy' ? 'busy' : (pres === 'away' ? 'away' : 'online')) : 'offline';
      const moreMutualFriends = Math.max(0, mutualFriendsCount - mutualFriends.length);
      const moreMutualGroups = Math.max(0, mutualGroupsCount - mutualGroups.length);
      const moreMutualRooms = Math.max(0, mutualRoomsCount - mutualRooms.length);
      const showLinkUrl = websiteUrl ? websiteUrl.replace(/^https?:\/\//i, '') : '';

      try {
        const profileNode = _profileBuildPublicProfileNode({
          u, p, online, pres, custom, bio, avatar, websiteUrl, showLinkUrl,
          relationshipStatus, age, locationText, interests, favoriteMusic, favoriteMovies, favoriteGames,
          recentRooms, mutualFriends, mutualGroups, mutualRooms,
          mutualFriendsCount, mutualGroupsCount, mutualRoomsCount,
          moreMutualFriends, moreMutualGroups, moreMutualRooms,
          lastSeen, created, isFriend, blockedByMe, blocksMe, isSelf, presDot,
        });
        _profileClearNode(log);
        _profileForceTopScroll(log);
        log.appendChild(profileNode);
        _profileForceTopScroll(log);

        _bindProfileTabs(log);
        const cleanupProfileResponsiveChrome = _bindProfileResponsiveChrome(log);
        if (typeof cleanupProfileResponsiveChrome === 'function') {
          try { win._profileViewCleanup?.(); } catch {}
          win._profileViewCleanup = cleanupProfileResponsiveChrome;
          try {
            if (!win._profileViewCleanupRegistered) {
              registerWindowCleanup(win, () => {
                try { win._profileViewCleanup?.(); } catch {}
                win._profileViewCleanup = null;
                win._profileViewCleanupRegistered = false;
              });
              win._profileViewCleanupRegistered = true;
            }
          } catch {}
        }
        _bindProfileLayoutControls(log, u, !!isSelf);
        _bindProfileComposer(log, u, p);
        _bindProfileGalleryFilters(log, u, p);
        _bindProfileSelfEditing(log, win, u, p);
        log.querySelectorAll('button[data-act]').forEach((btn) => {
          btn.addEventListener('click', () => {
            const act = String(btn.getAttribute('data-act') || '');
            handleUserContextAction(act, u);
          });
        });
        log.querySelector('[data-self-profile-act="refresh"]')?.addEventListener('click', () => {
          openProfileWindow(u, { fitMode: String(win?.dataset?.profileOpenMode || 'public') });
        });
      } catch (renderErr) {
        console.error(`${((typeof SERVER_NAME !== 'undefined' && SERVER_NAME) ? String(SERVER_NAME) : 'Hui Chat')} profile renderer failed; showing safe profile fallback`, renderErr);
        _profileClearNode(log);
        _profileForceTopScroll(log);
        log.appendChild(_profileBuildBasicProfileNode(p, u, {
          error: `Full profile view hit a browser-side render error, so ${((typeof SERVER_NAME !== 'undefined' && SERVER_NAME) ? String(SERVER_NAME) : 'Hui Chat')} showed the safe profile view instead.`,
        }));
        _profileForceTopScroll(log);
        _profileReleaseTopLock(win, topLockToken, 0);
        return;
      }

      try {
        if (isProfileLoadStale()) return;
        await _loadProfilePosts(log, u, p, { loadSeq });
        if (isProfileLoadStale()) return;
        _profileForceTopScroll(log);
        await _loadProfileGallery(log, u, p, 'all', { loadSeq });
        if (isProfileLoadStale()) return;
        _profileForceTopScroll(log);
        _profileReleaseTopLock(win, topLockToken, 0);
      } catch (err) {
        const feedRoot = log.querySelector('[data-profile-feed-root]');
        if (feedRoot) _profileReplaceTextBlock(feedRoot, 'ecProfileMeta dangerText', String(err?.message || err || 'Could not load posts'));
        const photosRoot = log.querySelector('[data-profile-photos-root]');
        if (photosRoot) _profileReplaceTextBlock(photosRoot, 'ecProfileMutedBlock', 'Could not load photos yet.');
      }
    } catch (err) {
      console.error(`${((typeof SERVER_NAME !== 'undefined' && SERVER_NAME) ? String(SERVER_NAME) : 'Hui Chat')} profile load crashed`, err);
      showFailure('Profile crashed while rendering', String(err?.message || err || 'profile_render_exception'));
      _profileReleaseTopLock(win, topLockToken, 0);
    }
  })();
}

// ───────────────────────────────────────────────────────────────────────────────
