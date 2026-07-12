// Audio is blocked until a user gesture (browser autoplay policy).
// Arm sound after the first pointer interaction to avoid console spam.
let AUDIO_ARMED = false;
function armHuiAudio() { AUDIO_ARMED = true; }
document.addEventListener("pointerdown", armHuiAudio, { once: true });
document.addEventListener("keydown", armHuiAudio, { once: true });

// ───────────────────────────────────────────────────────────────────────────────
// DOM helpers
// ───────────────────────────────────────────────────────────────────────────────
function $(id) { return document.getElementById(id); }

const EC_THEME_ACCENTS = Object.freeze(["default", "blue", "purple", "emerald", "sunset", "slate", "rosewood", "paper"]);

function ecNormalizeThemeAccent(value) {
  const raw = String(value || "default").trim().toLowerCase().replace(/[\s-]+/g, "_");
  return EC_THEME_ACCENTS.includes(raw) ? raw : "default";
}

function setThemeFromPrefs() {
  const root = $("appRoot");
  const prefs = UIState?.prefs || {};
  const dark = !!prefs.darkMode;
  const highContrast = !!prefs.highContrast;

  const accent = ecNormalizeThemeAccent(prefs.accentTheme);
  const accentClasses = EC_THEME_ACCENTS.map((name) => `accent-${name}`);

  document.body.classList.toggle("theme-dark", dark);
  document.body.classList.toggle("theme-light", !dark);
  document.body.classList.toggle("contrast-high", highContrast);
  document.body.classList.remove(...accentClasses);
  document.body.classList.add(`accent-${accent}`);

  if (root) {
    root.classList.toggle("theme-dark", dark);
    root.classList.toggle("theme-light", !dark);
    root.classList.toggle("contrast-high", highContrast);
    root.classList.remove(...accentClasses);
    root.classList.add(`accent-${accent}`);
  }
}


function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (m) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  }[m]));
}

function ecNormalizeSafeUrl(raw, opts = {}) {
  const allowRelative = opts.allowRelative !== false;
  const allowExternal = opts.allowExternal !== false;
  const value = String(raw || '').trim();
  if (!value) return '';
  try {
    const parsed = new URL(value, window.location.origin);
    const protocol = String(parsed.protocol || '').toLowerCase();
    if (protocol !== 'http:' && protocol !== 'https:') return '';
    const sameOrigin = parsed.origin === window.location.origin;
    if (!allowExternal && !sameOrigin) return '';
    const wasRelative = !/^[a-z][a-z0-9+.-]*:/i.test(value) && !value.startsWith('//');
    if (wasRelative && !allowRelative) return '';
    if (wasRelative && sameOrigin) {
      return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
    return parsed.href;
  } catch {
    return '';
  }
}

function ecSafeUrlAttr(raw, opts = {}) {
  const safe = ecNormalizeSafeUrl(raw, opts);
  return safe ? escapeHtml(safe) : '';
}

function ecCssUrl(raw, opts = {}) {
  const safe = ecNormalizeSafeUrl(raw, opts);
  if (!safe) return '';
  const cssString = safe.replace(/[\\"\n\r\f]/g, (ch) => ({
    '\\': '\\\\',
    '"': '\\"',
    '\n': '\\a ',
    '\r': '\\d ',
    '\f': '\\c ',
  }[ch] || ''));
  return `url("${cssString}")`;
}

function ecClearNode(node) {
  if (!node) return;
  try {
    node.replaceChildren();
    return;
  } catch {}
  try {
    while (node.firstChild) node.removeChild(node.firstChild);
  } catch {}
}



function ecNextAnimationFrame(fn) {
  const cb = (typeof fn === 'function') ? fn : function () {};
  try {
    return window.requestAnimationFrame(cb);
  } catch {
    return window.setTimeout(cb, 16);
  }
}

function ecCancelAnimationFrame(handle) {
  if (handle === undefined || handle === null) return;
  try { window.cancelAnimationFrame(handle); } catch { try { window.clearTimeout(handle); } catch {} }
}

function ecRafThrottle(fn) {
  let frame = null;
  let lastArgs = null;
  let lastThis = null;
  const run = () => {
    frame = null;
    const args = lastArgs || [];
    const ctx = lastThis;
    lastArgs = null;
    lastThis = null;
    try { fn.apply(ctx, args); } catch (err) { console.error(err); }
  };
  const throttled = function (...args) {
    lastArgs = args;
    lastThis = this;
    if (frame !== null) return;
    frame = ecNextAnimationFrame(run);
  };
  throttled.cancel = () => {
    if (frame !== null) ecCancelAnimationFrame(frame);
    frame = null;
    lastArgs = null;
    lastThis = null;
  };
  throttled.flush = () => {
    if (frame === null) return;
    ecCancelAnimationFrame(frame);
    run();
  };
  return throttled;
}

function ecRestartAnimationClass(el, cls, timeoutMs = 520, onStarted = null) {
  if (!el || !cls) return;
  try { el.classList.remove(cls); } catch {}
  // Avoid `void el.offsetWidth` forced-layout restarts. Queue the re-add for the
  // browser's next paint cycle so style invalidation and animation start stay
  // batched with other writes.
  ecNextAnimationFrame(() => {
    ecNextAnimationFrame(() => {
      try {
        if (!el.isConnected) return;
        el.classList.add(cls);
        if (typeof onStarted === 'function') onStarted();
      } catch {}
    });
  });
}

function ecCreateEl(tag, opts = {}, children = []) {
  const el = document.createElement(tag);
  if (opts.id) el.id = String(opts.id);
  if (opts.className) el.className = String(opts.className);
  if (opts.text !== undefined) el.textContent = String(opts.text);
  if (opts.type) el.setAttribute('type', String(opts.type));
  if (opts.title) el.title = String(opts.title);
  if (opts.value !== undefined) el.value = String(opts.value);
  if (opts.placeholder !== undefined) el.setAttribute('placeholder', String(opts.placeholder));
  if (opts.autocomplete !== undefined) el.setAttribute('autocomplete', String(opts.autocomplete));
  if (opts.role) el.setAttribute('role', String(opts.role));
  if (opts.ariaLabel) el.setAttribute('aria-label', String(opts.ariaLabel));
  if (opts.ariaModal !== undefined) el.setAttribute('aria-modal', String(opts.ariaModal));
  if (opts.ariaLive) el.setAttribute('aria-live', String(opts.ariaLive));
  if (opts.ariaAtomic !== undefined) el.setAttribute('aria-atomic', String(opts.ariaAtomic));
  if (opts.ariaHidden !== undefined) el.setAttribute('aria-hidden', String(opts.ariaHidden));
  if (opts.htmlFor) el.setAttribute('for', String(opts.htmlFor));
  if (opts.attrs && typeof opts.attrs === 'object') {
    Object.entries(opts.attrs).forEach(([k, v]) => {
      if (v === undefined || v === null || v === false) return;
      el.setAttribute(String(k), v === true ? '' : String(v));
    });
  }
  if (opts.dataset && typeof opts.dataset === 'object') {
    Object.entries(opts.dataset).forEach(([k, v]) => {
      if (v === undefined || v === null) return;
      el.dataset[String(k)] = String(v);
    });
  }
  const kids = Array.isArray(children) ? children : [children];
  kids.forEach((child) => {
    if (child === undefined || child === null || child === false) return;
    if (child instanceof Node) el.appendChild(child);
    else el.appendChild(document.createTextNode(String(child)));
  });
  return el;
}

function ecListStatusItem(opts = {}) {
  const li = document.createElement('li');
  li.dataset.name = String(opts.name || 'none');
  if (opts.search !== undefined) li.dataset.search = String(opts.search || '');
  if (opts.className) li.className = String(opts.className);
  const left = ecCreateEl('div', { className: 'liLeft' });
  if (opts.dot !== false) left.appendChild(ecCreateEl('span', { className: `presDot ${String(opts.dot || 'offline')}` }));
  if (opts.avatar !== false) left.appendChild(ecCreateEl('span', { className: 'liAvatar', text: opts.avatar !== undefined ? opts.avatar : '-' }));
  left.appendChild(ecCreateEl('span', { className: opts.muted === false ? 'liName' : 'liName muted', text: opts.text !== undefined ? opts.text : 'None' }));
  li.appendChild(left);
  return li;
}

function ecCtxHeader(label, id) {
  const span = ecCreateEl('span', { id, className: 'ecCtxUser', text: label || '' });
  return ecCreateEl('div', { className: 'ecCtxHeader' }, [span]);
}

function ecCtxItem(action, icon, label, extraClass = '') {
  return ecCreateEl('div', {
    className: `ecCtxItem${extraClass ? ` ${extraClass}` : ''}`,
    dataset: { action }
  }, [String(icon || ''), ' ', ecCreateEl('span', { text: label || '' })]);
}

function ecCtxSep() {
  return ecCreateEl('div', { className: 'ecCtxSep' });
}

function ecSetSafeUrlAttr(el, attr, raw, opts = {}) {
  if (!el || !attr) return '';
  const safe = ecNormalizeSafeUrl(raw, opts);
  if (!safe) {
    try { el.removeAttribute(attr); } catch {}
    return '';
  }
  try { el.setAttribute(attr, safe); } catch {}
  return safe;
}

function ecOpenSafeUrl(raw, opts = {}) {
  const safe = ecNormalizeSafeUrl(raw, { allowRelative: false, allowExternal: true, ...opts });
  if (!safe) return false;
  const target = opts.target || '_blank';
  const features = opts.features || 'noopener,noreferrer';
  try {
    window.open(safe, target, features);
    return true;
  } catch {
    try { window.open(safe, target, 'noopener,noreferrer'); return true; } catch {}
  }
  return false;
}

// ───────────────────────────────────────────────────────────────────────────────
// Optimistic composer clearing
// ───────────────────────────────────────────────────────────────────────────────
let EC_COMPOSER_OPTIMISTIC_SEND_SEQ = 0;

function ecComposerPendingCount(input) {
  try {
    const active = input?._ecComposerOptimisticTokens;
    const tokenCount = active && typeof active.size === 'number' ? Number(active.size || 0) : 0;
    return Math.max(tokenCount, Number(input?._ecComposerPendingCount || 0));
  } catch {
    return 0;
  }
}

function ecComposerStopTypingAfterClear(input) {
  if (!input) return;
  try {
    if (typeof ecConversationTypingStop === 'function' && input._ecTypingSurface) {
      ecConversationTypingStop(input, { force: true });
    }
  } catch {}
  try {
    if (typeof ecRoomTypingStop === 'function' && input._ecTypingRoom) {
      ecRoomTypingStop(input._ecTypingRoom, input, { force: true });
    }
  } catch {}
}

function ecComposerFailedDrafts(input) {
  if (!input) return [];
  try {
    if (!Array.isArray(input._ecFailedDrafts)) input._ecFailedDrafts = [];
    return input._ecFailedDrafts;
  } catch {
    return [];
  }
}

function ecComposerMarkFailedDraftState(input) {
  if (!input) return;
  try {
    const drafts = ecComposerFailedDrafts(input);
    if (drafts.length) {
      input.setAttribute('data-ec-failed-draft', '1');
      input.setAttribute('aria-description', 'A failed message draft is saved. Press Control plus Arrow Up to restore it.');
    } else {
      input.removeAttribute('data-ec-failed-draft');
      input.removeAttribute('aria-description');
    }
  } catch {}
}

function ecComposerSaveFailedDraft(input, draft, reason = '', token = '') {
  if (!input) return;
  try {
    const text = String(draft ?? '');
    if (!text) return;
    const drafts = ecComposerFailedDrafts(input);
    drafts.push({ text, reason: String(reason || ''), token: String(token || ''), savedAt: Date.now() });
    while (drafts.length > 5) drafts.shift();
    input._ecLastFailedDraft = text;
    ecComposerMarkFailedDraftState(input);
    if (typeof toast === 'function') {
      const suffix = reason ? ` ${reason}` : '';
      toast(`Send failed; original draft was saved. Press Ctrl+↑ in the composer to restore it.${suffix}`, 'warn', 6500, {
        event: 'composer_restore',
        dedupeKey: `composer-restore:${token || Date.now()}`
      });
    }
  } catch {}
}

function ecComposerRestoreSavedFailedDraft(input, opts = {}) {
  if (!input) return false;
  try {
    if (String(input.value || '').trim() && !opts.force) return false;
    const drafts = ecComposerFailedDrafts(input);
    const entry = drafts.pop();
    if (!entry?.text) {
      ecComposerMarkFailedDraftState(input);
      return false;
    }
    input.value = String(entry.text || '');
    ecComposerMarkFailedDraftState(input);
    try { input.dispatchEvent(new Event('input', { bubbles: true })); } catch {}
    try { input.focus(); if (opts.select !== false) input.select?.(); } catch {}
    if (typeof toast === 'function') toast('Restored failed draft.', 'info', 2600, { event: 'composer_restore_saved' });
    return true;
  } catch {
    return false;
  }
}

function ecComposerBindFailedDraftShortcut(input) {
  if (!input || input._ecFailedDraftShortcutBound) return;
  input._ecFailedDraftShortcutBound = true;
  input.addEventListener('keydown', (ev) => {
    try {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'ArrowUp') {
        if (ecComposerRestoreSavedFailedDraft(input, { select: true })) {
          ev.preventDefault();
          ev.stopPropagation();
        }
      }
    } catch {}
  });
}

function ecComposerBeginOptimisticSend(input, opts = {}) {
  const original = String(opts.text !== undefined ? opts.text : (input?.value ?? ''));
  const token = `send-${Date.now()}-${++EC_COMPOSER_OPTIMISTIC_SEND_SEQ}`;
  const button = opts.button || null;

  const finishInput = () => {
    if (!input) return;
    try {
      const active = input._ecComposerOptimisticTokens;
      if (active && typeof active.delete === 'function') active.delete(token);
      input._ecComposerPendingCount = Math.max(0, Number(input._ecComposerPendingCount || 0) - 1);
      const hasPending = ecComposerPendingCount(input) > 0;
      if (!hasPending) {
        input.classList.remove('ecComposerSending');
        input.removeAttribute('aria-busy');
        input.removeAttribute('data-ec-sending');
      }
      return hasPending;
    } catch {}
    return false;
  };

  const finishButton = () => {
    if (!button) return false;
    try {
      const active = button._ecComposerOptimisticTokens;
      if (active && typeof active.delete === 'function') active.delete(token);
      const hasPending = active && active.size > 0;
      if (!hasPending) {
        button.classList.remove('ecComposerSending');
        button.removeAttribute('aria-busy');
        button.removeAttribute('data-ec-sending');
      }
      return !!hasPending;
    } catch {}
    return false;
  };

  try {
    if (input) {
      ecComposerBindFailedDraftShortcut(input);
      if (!input._ecComposerOptimisticTokens) input._ecComposerOptimisticTokens = new Set();
      input._ecComposerOptimisticTokens.add(token);
      input._ecComposerPendingCount = Number(input._ecComposerPendingCount || 0) + 1;
      input._ecLastOptimisticSendToken = token;
      input._ecLastOptimisticDraft = original;
      input.value = '';
      input.classList.add('ecComposerSending');
      input.setAttribute('aria-busy', 'true');
      input.setAttribute('data-ec-sending', '1');
      try { input.dispatchEvent(new Event('input', { bubbles: true })); } catch {}
      ecComposerStopTypingAfterClear(input);
      try { input.focus(); } catch {}
    }
    if (button) {
      if (!button._ecComposerOptimisticTokens) button._ecComposerOptimisticTokens = new Set();
      button._ecComposerOptimisticTokens.add(token);
      button.classList.add('ecComposerSending');
      button.setAttribute('aria-busy', 'true');
      button.setAttribute('data-ec-sending', '1');
    }
  } catch {}

  let closed = false;
  const finish = () => {
    if (closed) return { hasPendingInput: false, hasPendingButton: false };
    closed = true;
    const hasPendingInput = finishInput();
    const hasPendingButton = finishButton();
    return { hasPendingInput, hasPendingButton };
  };

  return {
    token,
    original,
    commit() {
      finish();
    },
    restore(reason = '') {
      const state = finish();
      if (!input) return;
      try {
        const current = String(input.value || '');
        const canRestoreNow = !current.trim() && !state.hasPendingInput && (input.isConnected !== false);
        if (canRestoreNow) {
          input.value = original;
          try { input.dispatchEvent(new Event('input', { bubbles: true })); } catch {}
          try { input.focus(); input.select?.(); } catch {}
        } else {
          ecComposerSaveFailedDraft(input, original, reason, token);
        }
      } catch {
        ecComposerSaveFailedDraft(input, original, reason, token);
      }
    }
  };
}

