// ───────────────────────────────────────────────────────────────────────────────
// Settings modal
// ───────────────────────────────────────────────────────────────────────────────
function getGifResultsLimit() {
  const n = clampInt(UIState?.prefs?.gifResultsPerLoad, 12, 48, 12);
  return [12, 24, 36, 48].includes(n) ? n : 12;
}

function applyGifPickerPrefs() {
  const size = clampInt(UIState?.prefs?.gifTileSize, 96, 220, 140);
  const showTitles = UIState?.prefs?.gifShowTitles !== false;
  const labelDisplay = showTitles ? 'block' : 'none';
  const targets = [document.documentElement, document.body, GifUI?.modal, GifUI?.card, GifUI?.grid].filter(Boolean);
  try {
    targets.forEach((el) => {
      el.style.setProperty('--ec-gif-tile-size', `${size}px`);
      el.style.setProperty('--ec-gif-label-display', labelDisplay);
    });
  } catch {}

  try {
    if (GifUI?.grid) {
      GifUI.grid.style.gridTemplateColumns = `repeat(auto-fill, minmax(${size}px, 1fr))`;
    }
    document.querySelectorAll('.ym-gifItemImg').forEach((img) => {
      img.style.height = `${size}px`;
    });
    document.querySelectorAll('.ym-gifItemLabel').forEach((label) => {
      label.style.display = labelDisplay;
    });
  } catch {}

  const out = $("setGifTileSizeVal");
  if (out) out.textContent = `${size}px`;
}

const SETTINGS_PREF_DEFAULTS = Object.freeze({
  darkMode: false,
  highContrast: false,
  accentTheme: 'default',
  popupNotif: false,
  soundNotif: HUI_CFG.sound_notifications_default === undefined ? true : !!HUI_CFG.sound_notifications_default,
  soundTheme: String(HUI_CFG.sound_theme_default || HUI_CFG.default_sound_theme || 'soft_chime'),
  roomFontSize: 13,
  roomFontFamily: "Arial",
  roomComposerBold: false,
  roomComposerItalic: false,
  roomComposerUnderline: false,
  roomComposerColor: "#111111",
  emoticonSize: 26,
  gifTileSize: 140,
  gifResultsPerLoad: 12,
  gifOpenMode: 'recents',
  gifShowTitles: true,
  gifKeepOpen: false,
  missedToast: true,
  savePmLocal: false,
  friendStatusInline: true,
  friendStatusTooltip: true,
  helpHints: true
});

const SETTINGS_SECTION_KEYS = Object.freeze({
  chat: ['roomFontSize', 'roomFontFamily', 'roomComposerBold', 'roomComposerItalic', 'roomComposerUnderline', 'roomComposerColor', 'emoticonSize', 'gifTileSize', 'gifResultsPerLoad', 'gifOpenMode', 'gifShowTitles', 'gifKeepOpen'],
  theme: ['darkMode', 'highContrast', 'accentTheme'],
  alerts: ['popupNotif', 'soundNotif', 'soundTheme', 'missedToast'],
  friends: ['friendStatusInline', 'friendStatusTooltip', 'helpHints']
});


const SETTINGS_ALL_PREF_KEYS = Object.freeze(Object.keys(SETTINGS_PREF_DEFAULTS));
const SETTINGS_COMPOSER_KEYS = Object.freeze(['roomFontFamily', 'roomComposerBold', 'roomComposerItalic', 'roomComposerUnderline', 'roomComposerColor']);

let EC_SETTINGS_LAST_FOCUS = null;
let EC_SETTINGS_OPEN_SEQ = 0;
let EC_SETTINGS_SAVE_SEQ = 0;
const EC_SETTINGS_BUSY_CONTROL_IDS = Object.freeze(['btnSaveSettings', 'btnCloseSettings', 'btnResetSettingsSection', 'btnResetSettingsAll']);

function ecSettingsCleanValue(key, value) {
  switch (key) {
    case 'darkMode':
    case 'highContrast':
    case 'popupNotif':
    case 'soundNotif':
    case 'missedToast':
    case 'savePmLocal':
    case 'friendStatusInline':
    case 'friendStatusTooltip':
    case 'helpHints':
    case 'gifShowTitles':
    case 'gifKeepOpen':
    case 'roomComposerBold':
    case 'roomComposerItalic':
    case 'roomComposerUnderline':
      return !!value;
    case 'roomFontSize':
      return clampInt(value, 10, 22, 13);
    case 'emoticonSize':
      return (typeof ecClampEmoticonSize === 'function') ? ecClampEmoticonSize(value) : clampInt(value, 22, 56, 26);
    case 'gifTileSize':
      return clampInt(value, 96, 220, 140);
    case 'gifResultsPerLoad': {
      const n = clampInt(value, 12, 48, 12);
      return [12, 24, 36, 48].includes(n) ? n : 12;
    }
    case 'gifOpenMode': {
      const raw = String(value || 'recents');
      return ['recents', 'trending', 'last_search'].includes(raw) ? raw : 'recents';
    }
    case 'accentTheme':
      return (typeof ecNormalizeThemeAccent === 'function') ? ecNormalizeThemeAccent(value || 'default') : String(value || 'default');
    case 'soundTheme':
      return (typeof ecNormalizeSoundTheme === 'function') ? ecNormalizeSoundTheme(value || SETTINGS_PREF_DEFAULTS.soundTheme) : String(value || SETTINGS_PREF_DEFAULTS.soundTheme);
    case 'roomFontFamily':
      return (typeof ecNormalizeRoomFontFamily === 'function') ? ecNormalizeRoomFontFamily(value || 'Arial') : String(value || 'Arial');
    case 'roomComposerColor':
      return (typeof ecNormalizeRoomTextColor === 'function') ? ecNormalizeRoomTextColor(value || '#111111') : String(value || '#111111');
    default:
      return value;
  }
}

function ecSettingsSnapshotPrefs() {
  const snapshot = {};
  SETTINGS_ALL_PREF_KEYS.forEach((key) => {
    const fallback = getSettingsDefaultValue(key);
    const current = (UIState?.prefs && Object.prototype.hasOwnProperty.call(UIState.prefs, key)) ? UIState.prefs[key] : fallback;
    snapshot[key] = ecSettingsCleanValue(key, current == null ? fallback : current);
  });
  return snapshot;
}

function ecSettingsReadSnapshot(modal) {
  try {
    const raw = modal?.dataset?.settingsSnapshot || '';
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === 'object' ? parsed : ecSettingsSnapshotPrefs();
  } catch {
    return ecSettingsSnapshotPrefs();
  }
}

function ecSettingsWriteSnapshot(modal, snapshot = null) {
  if (!modal) return;
  try { modal.dataset.settingsSnapshot = JSON.stringify(snapshot || ecSettingsSnapshotPrefs()); } catch {}
}

function ecApplySettingsPrefsObject(prefs = {}, opts = {}) {
  if (!UIState.prefs) UIState.prefs = {};
  SETTINGS_ALL_PREF_KEYS.forEach((key) => {
    if (!Object.prototype.hasOwnProperty.call(prefs, key)) return;
    UIState.prefs[key] = ecSettingsCleanValue(key, prefs[key]);
  });
  if (typeof ecApplyRoomComposerPrefs === 'function') ecApplyRoomComposerPrefs();
  else applyRoomFontSize(UIState.prefs.roomFontSize || 13);
  if (typeof ecApplyEmoticonSizePrefs === 'function') ecApplyEmoticonSizePrefs(UIState.prefs.emoticonSize || 26);
  applyGifPickerPrefs();
  setThemeFromPrefs();
  if (Object.prototype.hasOwnProperty.call(prefs, 'helpHints')) {
    try { setHelpHintsEnabled(!!UIState.prefs.helpHints, { persist: false, syncUi: true }); } catch {}
  }
  if (opts.syncControls !== false) applySettingsDraftToControls(UIState.prefs);
}

function ecSettingsDraftFromControls() {
  const draft = ecSettingsSnapshotPrefs();
  const set = (key, raw) => { draft[key] = ecSettingsCleanValue(key, raw); };
  set('darkMode', !!$('setDarkMode')?.checked);
  set('highContrast', !!$('setHighContrast')?.checked);
  set('accentTheme', $('setAccentTheme')?.value || 'default');
  set('popupNotif', !!$('setPopupNotif')?.checked);
  set('soundNotif', $('setSoundNotif') ? !!$('setSoundNotif').checked : SETTINGS_PREF_DEFAULTS.soundNotif);
  set('soundTheme', $('setSoundTheme')?.value || SETTINGS_PREF_DEFAULTS.soundTheme);
  set('missedToast', $('setMissedToast') ? !!$('setMissedToast').checked : true);
  set('savePmLocal', false);
  set('friendStatusInline', $('setFriendStatusInline') ? !!$('setFriendStatusInline').checked : true);
  set('friendStatusTooltip', $('setFriendStatusTooltip') ? !!$('setFriendStatusTooltip').checked : true);
  set('helpHints', $('setHelpHints') ? !!$('setHelpHints').checked : true);
  set('roomFontSize', $('setRoomFontSize')?.value || 13);
  set('emoticonSize', $('setEmoticonSize')?.value || 26);
  set('gifTileSize', $('setGifTileSize')?.value || 140);
  set('gifResultsPerLoad', $('setGifResultsPerLoad')?.value || 12);
  set('gifOpenMode', $('setGifOpenMode')?.value || 'recents');
  set('gifShowTitles', $('setGifShowTitles') ? !!$('setGifShowTitles').checked : true);
  set('gifKeepOpen', $('setGifKeepOpen') ? !!$('setGifKeepOpen').checked : false);
  // Classic composer toolbar values live outside the Settings modal but are part
  // of the Chat reset section. Keep the draft aligned with the active toolbar.
  SETTINGS_COMPOSER_KEYS.forEach((key) => {
    if (UIState?.prefs && Object.prototype.hasOwnProperty.call(UIState.prefs, key)) {
      draft[key] = ecSettingsCleanValue(key, UIState.prefs[key]);
    }
  });
  return draft;
}

function ecSettingsObjectsEqual(a = {}, b = {}) {
  return SETTINGS_ALL_PREF_KEYS.every((key) => JSON.stringify(ecSettingsCleanValue(key, a[key])) === JSON.stringify(ecSettingsCleanValue(key, b[key])));
}

function updateSettingsDirtyState() {
  const modal = $('settingsModal');
  if (!modal || modal.classList.contains('hidden')) return;
  const snapshot = ecSettingsReadSnapshot(modal);
  const draft = ecSettingsDraftFromControls();
  const dirty = !ecSettingsObjectsEqual(snapshot, draft);
  modal.classList.toggle('is-dirty', dirty);
  modal.dataset.settingsDirty = dirty ? '1' : '0';
  const status = $('settingsStatus');
  if (status) {
    status.textContent = dirty ? 'Unsaved changes — Save keeps them, Close reverts previews.' : 'No unsaved changes.';
    status.classList.toggle('warnText', dirty);
  }
  const save = $('btnSaveSettings');
  if (save) save.title = dirty ? 'Save these settings in this browser' : 'Save settings';
}

function setSettingsBusy(isBusy) {
  const modal = $('settingsModal');
  if (modal) modal.dataset.settingsSaving = isBusy ? '1' : '0';
  EC_SETTINGS_BUSY_CONTROL_IDS.forEach((id) => ecSetSettingsControlDisabled(id, !!isBusy));
  const save = $('btnSaveSettings');
  if (!save) return;
  if (isBusy) {
    if (!save.dataset.prevHtml) save.dataset.prevHtml = save.innerHTML || 'Save';
    save.classList.add('isBusy');
    save.setAttribute('aria-busy', 'true');
    save.innerHTML = 'Saving…';
    const status = $('settingsStatus');
    if (status) status.textContent = 'Saving settings…';
  } else {
    save.classList.remove('isBusy');
    save.setAttribute('aria-busy', 'false');
    if (save.dataset.prevHtml) save.innerHTML = save.dataset.prevHtml;
    delete save.dataset.prevHtml;
  }
}


function ecSettingsFocusableElements(modal) {
  if (!modal) return [];
  const nodes = Array.from(modal.querySelectorAll([
    'button:not([disabled])',
    'a[href]',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',')));
  return nodes.filter((el) => {
    if (!(el instanceof HTMLElement)) return false;
    if (el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
    const panel = el.closest('.settingsPanel[hidden], [hidden]');
    if (panel) return false;
    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    return true;
  });
}

function ecTrapSettingsFocus(ev, modal) {
  if (!modal || modal.classList.contains('hidden') || ev.key !== 'Tab') return false;
  const focusable = ecSettingsFocusableElements(modal);
  if (!focusable.length) {
    ev.preventDefault();
    try { modal.focus({ preventScroll: true }); } catch {}
    return true;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const current = document.activeElement;
  if (ev.shiftKey && (!current || current === first || !modal.contains(current))) {
    ev.preventDefault();
    try { last.focus({ preventScroll: true }); } catch {}
    return true;
  }
  if (!ev.shiftKey && current === last) {
    ev.preventDefault();
    try { first.focus({ preventScroll: true }); } catch {}
    return true;
  }
  return false;
}

function ecSetSettingsControlDisabled(id, disabled) {
  const el = $(id);
  if (!el) return;
  if (disabled) {
    if (!el.dataset.settingsPrevDisabled) el.dataset.settingsPrevDisabled = el.disabled ? '1' : '0';
    el.disabled = true;
    el.setAttribute('aria-disabled', 'true');
  } else {
    const wasDisabled = el.dataset.settingsPrevDisabled === '1';
    delete el.dataset.settingsPrevDisabled;
    el.disabled = wasDisabled;
    el.setAttribute('aria-disabled', wasDisabled ? 'true' : 'false');
  }
}

function ecUpdateSettingsLivePreviewFromControls() {
  const modal = $('settingsModal');
  if (!modal || modal.classList.contains('hidden')) return;
  const draft = ecSettingsDraftFromControls();
  ecApplySettingsPrefsObject(draft, { syncControls: false });
  updateSettingsDirtyState();
}

function bindSettingsModalKeys() {
  const modal = $('settingsModal');
  if (!modal || modal.dataset.ecSettingsKeysBound === '1') return;
  modal.dataset.ecSettingsKeysBound = '1';
  modal.addEventListener('keydown', (ev) => {
    if (ev.key === 'Tab' && ecTrapSettingsFocus(ev, modal)) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      if (modal.dataset.settingsSaving === '1') {
        toast('ℹ️ Settings are saving. Please wait…', 'info');
        return;
      }
      closeSettings();
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && String(ev.key || '').toLowerCase() === 's') {
      ev.preventDefault();
      saveSettings();
    }
  });
}

function getSettingsDefaultValue(key) {
  return Object.prototype.hasOwnProperty.call(SETTINGS_PREF_DEFAULTS, key)
    ? SETTINGS_PREF_DEFAULTS[key]
    : null;
}

function getSettingsDraftValue(draft, key, fallback) {
  if (draft && Object.prototype.hasOwnProperty.call(draft, key)) return draft[key];
  if (UIState?.prefs && Object.prototype.hasOwnProperty.call(UIState.prefs, key)) return UIState.prefs[key];
  return fallback;
}

function applySettingsDraftToControls(draft = {}) {
  const boolVal = (key, fallback = false) => !!getSettingsDraftValue(draft, key, fallback);
  const strVal = (key, fallback = '') => {
    const value = getSettingsDraftValue(draft, key, fallback);
    return value == null ? String(fallback || '') : String(value);
  };
  const numVal = (key, min, max, fallback) => clampInt(getSettingsDraftValue(draft, key, fallback), min, max, fallback);

  const dark = $("setDarkMode");
  if (dark) dark.checked = boolVal('darkMode', false);
  const contrast = $("setHighContrast");
  if (contrast) contrast.checked = boolVal('highContrast', false);
  const accent = $("setAccentTheme");
  if (accent) accent.value = strVal('accentTheme', 'default');
  const popup = $("setPopupNotif");
  if (popup) popup.checked = boolVal('popupNotif', false);
  const sound = $("setSoundNotif");
  if (sound) sound.checked = boolVal('soundNotif', SETTINGS_PREF_DEFAULTS.soundNotif);
  const soundTheme = $("setSoundTheme");
  if (soundTheme) {
    try { ecPopulateSoundSelect(soundTheme, strVal('soundTheme', SETTINGS_PREF_DEFAULTS.soundTheme), { showFiles: true }); } catch {}
    soundTheme.value = ecNormalizeSoundTheme(strVal('soundTheme', SETTINGS_PREF_DEFAULTS.soundTheme));
  }
  const missed = $("setMissedToast");
  if (missed) missed.checked = boolVal('missedToast', true);
  const savePm = $("setSavePmLocal");
  if (savePm) savePm.checked = boolVal('savePmLocal', false);
  const friendInline = $("setFriendStatusInline");
  if (friendInline) friendInline.checked = boolVal('friendStatusInline', true);
  const friendTip = $("setFriendStatusTooltip");
  if (friendTip) friendTip.checked = boolVal('friendStatusTooltip', true);
  const helpHints = $("setHelpHints");
  if (helpHints) helpHints.checked = boolVal('helpHints', true);

  const roomSlider = $("setRoomFontSize");
  const roomSize = numVal('roomFontSize', 10, 22, 13);
  if (roomSlider) roomSlider.value = String(roomSize);
  applyRoomFontSize(roomSize);

  const emoticonSize = $("setEmoticonSize");
  const emoSize = (typeof ecClampEmoticonSize === 'function') ? ecClampEmoticonSize(getSettingsDraftValue(draft, 'emoticonSize', 26)) : numVal('emoticonSize', 22, 56, 26);
  if (emoticonSize) emoticonSize.value = String(emoSize);
  UIState.prefs.emoticonSize = emoSize;
  if (typeof ecApplyEmoticonSizePrefs === 'function') ecApplyEmoticonSizePrefs(emoSize);

  const gifSize = $("setGifTileSize");
  const gifTile = numVal('gifTileSize', 96, 220, 140);
  if (gifSize) gifSize.value = String(gifTile);
  UIState.prefs.gifTileSize = gifTile;

  const gifCount = $("setGifResultsPerLoad");
  const gifResultsRaw = Number(getSettingsDraftValue(draft, 'gifResultsPerLoad', 12));
  const gifResults = [12, 24, 36, 48].includes(gifResultsRaw) ? gifResultsRaw : 12;
  if (gifCount) gifCount.value = String(gifResults);

  const gifMode = $("setGifOpenMode");
  const openMode = ['recents', 'trending', 'last_search'].includes(strVal('gifOpenMode', 'recents')) ? strVal('gifOpenMode', 'recents') : 'recents';
  if (gifMode) gifMode.value = openMode;

  const gifTitles = $("setGifShowTitles");
  const showTitles = boolVal('gifShowTitles', true);
  if (gifTitles) gifTitles.checked = showTitles;
  UIState.prefs.gifShowTitles = showTitles;

  const gifKeep = $("setGifKeepOpen");
  if (gifKeep) gifKeep.checked = boolVal('gifKeepOpen', false);

  SETTINGS_COMPOSER_KEYS.forEach((key) => {
    if (draft && Object.prototype.hasOwnProperty.call(draft, key)) {
      UIState.prefs[key] = ecSettingsCleanValue(key, draft[key]);
    }
  });
  if (typeof ecApplyRoomComposerPrefs === 'function') ecApplyRoomComposerPrefs();
  applyGifPickerPrefs();
  updateSettingsResetButtons();
  updateSettingsDirtyState();
}

function updateSettingsResetButtons() {
  const btn = $("btnResetSettingsSection");
  if (!btn) return;
  const tab = String(UIState?.prefs?.settingsTab || 'chat');
  const keys = SETTINGS_SECTION_KEYS[tab] || [];
  const enabled = keys.length > 0;
  btn.disabled = !enabled;
  btn.setAttribute('aria-disabled', enabled ? 'false' : 'true');
  btn.title = enabled ? 'Reset the current settings tab to its defaults' : 'This section does not have resettable saved settings';
  const all = $('btnResetSettingsAll');
  if (all) all.title = `Reset all ${SERVER_NAME} settings in this browser back to defaults`;
}

function resetCurrentSettingsSectionDraft() {
  const modal = $('settingsModal');
  if (modal?.dataset?.settingsSaving === '1') {
    toast('ℹ️ Settings are saving. Please wait…', 'info');
    return;
  }
  const tab = String(UIState?.prefs?.settingsTab || 'chat');
  const keys = SETTINGS_SECTION_KEYS[tab] || [];
  if (!keys.length) {
    toast('ℹ️ This section has no resettable saved settings', 'info');
    updateSettingsResetButtons();
    return;
  }
  const draft = {};
  keys.forEach((key) => {
    draft[key] = getSettingsDefaultValue(key);
  });
  applySettingsDraftToControls(draft);
  updateSettingsDirtyState();
  toast('↺ Current settings section reset. Click Save to keep it.', 'info');
}

async function resetAllSettingsDraft() {
  const modal = $('settingsModal');
  if (modal?.dataset?.settingsSaving === '1') {
    toast('ℹ️ Settings are saving. Please wait…', 'info');
    return;
  }
  const ok = await ecConfirm(`Reset all ${SERVER_NAME} settings in this browser back to defaults? Click Save after reviewing to keep the reset.`, {
    title: 'Reset all settings',
    confirmLabel: 'Reset all',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  applySettingsDraftToControls(SETTINGS_PREF_DEFAULTS);
  updateSettingsDirtyState();
  toast('↺ All settings reset to defaults. Click Save to keep them.', 'info');
}

function setSettingsTab(tabName, opts = {}) {
  const requested = String(tabName || 'chat');
  const tabs = Array.from(document.querySelectorAll('.settingsTabBtn[data-settings-tab]'));
  const panels = Array.from(document.querySelectorAll('.settingsPanel[data-settings-panel]'));
  const hasRequested = tabs.some((btn) => btn.dataset.settingsTab === requested)
    && panels.some((panel) => panel.dataset.settingsPanel === requested);
  const name = hasRequested ? requested : 'chat';

  let activeTabButton = null;
  tabs.forEach((btn) => {
    const active = btn.dataset.settingsTab === name;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
    btn.setAttribute('tabindex', active ? '0' : '-1');
    if (active) activeTabButton = btn;
  });
  panels.forEach((panel) => {
    const active = panel.dataset.settingsPanel === name;
    panel.classList.toggle('is-active', active);
    panel.hidden = !active;
    panel.setAttribute('aria-hidden', active ? 'false' : 'true');
  });

  UIState.prefs.settingsTab = name;
  if (opts.persist !== false) Settings.set('settingsTab', name);
  updateSettingsResetButtons();
  if (activeTabButton && opts.scrollIntoView !== false) {
    try { activeTabButton.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' }); } catch (_) {}
  }
}

function bindSettingsTabs() {
  if (document.body?.dataset?.ecSettingsTabsBound === '1') return;
  if (document.body) document.body.dataset.ecSettingsTabsBound = '1';
  document.querySelectorAll('.settingsTabBtn[data-settings-tab]').forEach((btn) => {
    btn.addEventListener('click', () => setSettingsTab(btn.dataset.settingsTab || 'chat'));
    btn.addEventListener('keydown', (ev) => {
      const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'];
      if (!keys.includes(ev.key)) return;
      const tabs = Array.from(document.querySelectorAll('.settingsTabBtn[data-settings-tab]'));
      const idx = tabs.indexOf(btn);
      if (idx < 0 || !tabs.length) return;
      ev.preventDefault();
      let nextIdx = idx;
      if (ev.key === 'Home') nextIdx = 0;
      else if (ev.key === 'End') nextIdx = tabs.length - 1;
      else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') nextIdx = (idx - 1 + tabs.length) % tabs.length;
      else if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') nextIdx = (idx + 1) % tabs.length;
      const next = tabs[nextIdx];
      if (next) {
        setSettingsTab(next.dataset.settingsTab || 'chat');
        try { next.focus(); } catch {}
      }
    });
  });
}

function previewThemeSettingsFromControls() {
  const dark = $("setDarkMode");
  const contrast = $("setHighContrast");
  const accent = $("setAccentTheme");
  UIState.prefs.darkMode = dark ? !!dark.checked : false;
  UIState.prefs.highContrast = contrast ? !!contrast.checked : false;
  UIState.prefs.accentTheme = (typeof ecNormalizeThemeAccent === 'function')
    ? ecNormalizeThemeAccent(accent ? accent.value : 'default')
    : String(accent?.value || 'default');
  setThemeFromPrefs();
}

function bindSettingsLivePreview() {
  if (document.body?.dataset?.ecSettingsPreviewBound === '1') return;
  if (document.body) document.body.dataset.ecSettingsPreviewBound = '1';
  ['setDarkMode', 'setHighContrast', 'setAccentTheme'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', () => { previewThemeSettingsFromControls(); updateSettingsDirtyState(); });
    el.addEventListener('change', () => { previewThemeSettingsFromControls(); updateSettingsDirtyState(); });
  });
  [
    'setPopupNotif', 'setSoundNotif', 'setSoundTheme', 'setMissedToast',
    'setFriendStatusInline', 'setFriendStatusTooltip', 'setHelpHints',
    'setRoomFontSize', 'setEmoticonSize', 'setGifTileSize', 'setGifResultsPerLoad',
    'setGifOpenMode', 'setGifShowTitles', 'setGifKeepOpen'
  ].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', ecUpdateSettingsLivePreviewFromControls);
    el.addEventListener('change', ecUpdateSettingsLivePreviewFromControls);
  });
}

function openSettings() {
  clearSearchesForModalTransition();
  const modal = $("settingsModal");
  if (!modal) return;

  EC_SETTINGS_LAST_FOCUS = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  EC_SETTINGS_OPEN_SEQ += 1;
  modal.dataset.settingsOpenSeq = String(EC_SETTINGS_OPEN_SEQ);
  setSettingsBusy(false);
  ecSettingsWriteSnapshot(modal, ecSettingsSnapshotPrefs());
  applySettingsDraftToControls(UIState.prefs);
  syncHelpHintsSettingUi();
  bindSettingsLivePreview();
  bindSettingsModalKeys();

  modal.dataset.prevRoomFontSize = String(UIState.prefs.roomFontSize ?? 13);
  modal.dataset.prevEmoticonSize = String((typeof ecClampEmoticonSize === 'function') ? ecClampEmoticonSize(UIState.prefs.emoticonSize ?? 26) : clampInt(UIState.prefs.emoticonSize, 22, 56, 26));
  modal.dataset.prevGifTileSize = String(clampInt(UIState.prefs.gifTileSize, 96, 220, 140));
  modal.dataset.prevGifShowTitles = UIState.prefs.gifShowTitles === false ? '0' : '1';
  modal.dataset.prevDarkMode = UIState.prefs.darkMode ? '1' : '0';
  modal.dataset.prevHighContrast = UIState.prefs.highContrast ? '1' : '0';
  modal.dataset.prevAccentTheme = String(UIState.prefs.accentTheme || 'default');
  modal.dataset.settingsPreviewActive = '1';
  setSettingsTab(UIState.prefs.settingsTab || 'chat', { persist: false, scrollIntoView: false });

  modal.classList.remove("hidden");
  updateSettingsDirtyState();
  setTimeout(() => {
    const active = document.querySelector('.settingsTabBtn.is-active');
    try { (active || $('btnSaveSettings') || modal).focus({ preventScroll: true }); } catch {}
  }, 0);
}

function closeSettings(opts = {}) {
  const shouldRevertPreview = opts.revertPreview !== false;
  const modal = $("settingsModal");
  if (modal?.dataset?.settingsSaving === '1' && opts.force !== true) {
    toast('ℹ️ Settings are saving. Please wait…', 'info');
    return;
  }
  if (shouldRevertPreview && modal?.dataset?.settingsPreviewActive === '1') {
    ecApplySettingsPrefsObject(ecSettingsReadSnapshot(modal), { syncControls: false });
  }
  if (modal) {
    modal.dataset.settingsPreviewActive = '0';
    modal.dataset.settingsDirty = '0';
    modal.classList.remove('is-dirty');
    modal.classList.add("hidden");
  }
  clearSearchesForModalTransition();
  if (EC_SETTINGS_LAST_FOCUS && document.contains(EC_SETTINGS_LAST_FOCUS)) {
    setTimeout(() => { try { EC_SETTINGS_LAST_FOCUS.focus({ preventScroll: true }); } catch {} }, 0);
  }
}

async function requestNotifPermissionIfNeeded() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    try { await Notification.requestPermission(); } catch {}
  }
}

async function saveSettings() {
  const modal = $("settingsModal");
  if (modal?.dataset?.settingsSaving === '1') return;
  const saveSeq = ++EC_SETTINGS_SAVE_SEQ;
  const openSeq = String(modal?.dataset?.settingsOpenSeq || '');
  setSettingsBusy(true);
  try {
  const dm = $("setDarkMode");
  UIState.prefs.darkMode = dm ? !!dm.checked : false;
  const hc = $("setHighContrast");
  UIState.prefs.highContrast = hc ? !!hc.checked : false;
  const at = $("setAccentTheme");
  UIState.prefs.accentTheme = (typeof ecNormalizeThemeAccent === 'function')
    ? ecNormalizeThemeAccent(at ? at.value : UIState.prefs.accentTheme)
    : String(at?.value || UIState.prefs.accentTheme || "default");
  const popup = $("setPopupNotif");
  UIState.prefs.popupNotif = popup ? !!popup.checked : false;
  const soundNotif = $("setSoundNotif");
  UIState.prefs.soundNotif = soundNotif ? !!soundNotif.checked : SETTINGS_PREF_DEFAULTS.soundNotif;
  const soundTheme = $("setSoundTheme");
  UIState.prefs.soundTheme = ecNormalizeSoundTheme(soundTheme ? soundTheme.value : UIState.prefs.soundTheme);

  const mt = $("setMissedToast");
  UIState.prefs.missedToast = mt ? !!mt.checked : true;
  UIState.prefs.savePmLocal = false;

  const fsi = $("setFriendStatusInline");
  UIState.prefs.friendStatusInline = fsi ? !!fsi.checked : true;
  const fst = $("setFriendStatusTooltip");
  UIState.prefs.friendStatusTooltip = fst ? !!fst.checked : true;
  const hhc = $("setHelpHints");
  UIState.prefs.helpHints = hhc ? !!hhc.checked : true;


  const slider = $("setRoomFontSize");
  if (slider) {
    UIState.prefs.roomFontSize = clampInt(slider.value, 10, 22, 13);
    Settings.set("roomFontSize", UIState.prefs.roomFontSize);
    applyRoomFontSize(UIState.prefs.roomFontSize);
    const modal = $("settingsModal");
    if (modal) modal.dataset.prevRoomFontSize = String(UIState.prefs.roomFontSize);
  }

  const emoticonSize = $("setEmoticonSize");
  UIState.prefs.emoticonSize = emoticonSize
    ? ((typeof ecClampEmoticonSize === 'function') ? ecClampEmoticonSize(emoticonSize.value) : clampInt(emoticonSize.value, 22, 56, 26))
    : ((typeof ecClampEmoticonSize === 'function') ? ecClampEmoticonSize(UIState.prefs.emoticonSize) : 26);
  Settings.set("emoticonSize", UIState.prefs.emoticonSize);
  if (typeof ecApplyEmoticonSizePrefs === 'function') ecApplyEmoticonSizePrefs(UIState.prefs.emoticonSize);
  if (modal) modal.dataset.prevEmoticonSize = String(UIState.prefs.emoticonSize);

  const gifSize = $("setGifTileSize");
  UIState.prefs.gifTileSize = gifSize ? clampInt(gifSize.value, 96, 220, 140) : 140;
  const gifCount = $("setGifResultsPerLoad");
  UIState.prefs.gifResultsPerLoad = gifCount ? clampInt(gifCount.value, 12, 48, 12) : 12;
  if (![12,24,36,48].includes(UIState.prefs.gifResultsPerLoad)) UIState.prefs.gifResultsPerLoad = 12;
  const gifMode = $("setGifOpenMode");
  UIState.prefs.gifOpenMode = gifMode ? String(gifMode.value || 'recents') : 'recents';
  if (!["recents","trending","last_search"].includes(UIState.prefs.gifOpenMode)) UIState.prefs.gifOpenMode = 'recents';
  const gifTitles = $("setGifShowTitles");
  const gifKeepOpen = $("setGifKeepOpen");
  UIState.prefs.gifShowTitles = gifTitles ? !!gifTitles.checked : true;
  UIState.prefs.gifKeepOpen = gifKeepOpen ? !!gifKeepOpen.checked : false;
  if (modal) {
    modal.dataset.prevGifTileSize = String(UIState.prefs.gifTileSize);
    modal.dataset.prevGifShowTitles = UIState.prefs.gifShowTitles ? '1' : '0';
  }
  applyGifPickerPrefs();

  SETTINGS_COMPOSER_KEYS.forEach((key) => {
    UIState.prefs[key] = ecSettingsCleanValue(key, UIState.prefs[key] ?? getSettingsDefaultValue(key));
    Settings.set(key, UIState.prefs[key]);
  });

  Settings.set("darkMode", UIState.prefs.darkMode);
  Settings.set("highContrast", UIState.prefs.highContrast);
  Settings.set("accentTheme", UIState.prefs.accentTheme);
  Settings.set("popupNotif", UIState.prefs.popupNotif);
  Settings.set("soundNotif", UIState.prefs.soundNotif);
  Settings.set("soundTheme", UIState.prefs.soundTheme);
  Settings.set("missedToast", UIState.prefs.missedToast);
  Settings.set("savePmLocal", false);
  Settings.set("gifTileSize", UIState.prefs.gifTileSize);
  Settings.set("gifResultsPerLoad", UIState.prefs.gifResultsPerLoad);
  Settings.set("gifOpenMode", UIState.prefs.gifOpenMode);
  Settings.set("gifShowTitles", UIState.prefs.gifShowTitles);
  Settings.set("gifKeepOpen", UIState.prefs.gifKeepOpen);
  Settings.set("settingsTab", UIState.prefs.settingsTab || 'chat');
  Settings.set("friendStatusInline", UIState.prefs.friendStatusInline);
  Settings.set("friendStatusTooltip", UIState.prefs.friendStatusTooltip);
  setHelpHintsEnabled(UIState.prefs.helpHints, { persist: true, syncUi: false });

  setThemeFromPrefs();

  // Re-render friends list to apply display preferences immediately.
  try { getFriends(); } catch (_) {}

  if (UIState.prefs.popupNotif) await requestNotifPermissionIfNeeded();

  if (modal && String(modal.dataset.settingsOpenSeq || '') !== openSeq) {
    toast('⚠️ Settings changed while saving. Reopen Settings and review.', 'warn');
    return;
  }
  if (saveSeq !== EC_SETTINGS_SAVE_SEQ) return;
  ecSettingsWriteSnapshot(modal, ecSettingsSnapshotPrefs());
  updateSettingsDirtyState();
  toast("✅ Settings saved", "ok");
  closeSettings({ revertPreview: false, force: true });
  } finally {
    setSettingsBusy(false);
  }
}
