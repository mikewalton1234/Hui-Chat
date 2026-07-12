// ───────────────────────────────────────────────────────────────────────────────
// Image emoticon picker and typed-shortcut rendering (rooms + DMs + groups)
//
// The picker is data-driven from /api/emoticons/catalog. It shows image previews
// only, while typed shortcuts stay behind the scenes for insertion/replacement.
// ───────────────────────────────────────────────────────────────────────────────

function insertAtCursor(inputEl, text) {
  if (!inputEl) return;
  const rawText = String(text || "");
  const rich = inputEl._ecRichComposer || null;
  if (rich && typeof rich.insertTextOrEmoticon === "function") {
    rich.insertTextOrEmoticon(rawText);
    return;
  }
  const v = String(inputEl.value || "");
  const start = (typeof inputEl.selectionStart === "number") ? inputEl.selectionStart : v.length;
  const end = (typeof inputEl.selectionEnd === "number") ? inputEl.selectionEnd : v.length;
  const next = v.slice(0, start) + rawText + v.slice(end);
  inputEl.value = next;
  const pos = start + rawText.length;
  try { inputEl.setSelectionRange(pos, pos); } catch { /* ignore */ }
  try { inputEl.focus(); } catch { /* ignore */ }
  try { inputEl.dispatchEvent(new Event("input", { bubbles: true })); } catch { /* ignore */ }
}

function ecVersionedStaticUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return raw;
  const version = String(window.HUI_APP_VERSION || "").trim();
  if (!version) return raw;
  try {
    const u = new URL(raw, window.location.origin);
    if (u.origin !== window.location.origin || !(u.pathname.startsWith("/static/") || u.pathname.startsWith("/api/"))) return raw;
    if (!u.searchParams.has("v")) u.searchParams.set("v", version);
    return u.pathname + u.search + u.hash;
  } catch (_) {
    return raw;
  }
}

const EmojiUI = {
  pop: null,
  activeInput: null,
  activeAnchor: null,
  visible: false,
  statusEl: null,
  codeGrid: null,
  codeSearch: null
};

function ecClampEmoticonSize(value) {
  const n = parseInt(value, 10);
  if (Number.isNaN(n)) return 26;
  return Math.max(22, Math.min(56, n));
}

function ecApplyEmoticonSizePrefs(value) {
  const source = value ?? (typeof UIState !== "undefined" ? UIState?.prefs?.emoticonSize : undefined) ?? (typeof Settings !== "undefined" ? Settings.get("emoticonSize", 26) : 26);
  const size = ecClampEmoticonSize(source);
  if (typeof UIState !== "undefined" && UIState?.prefs) UIState.prefs.emoticonSize = size;
  const pickerSize = Math.max(32, Math.min(68, size + 8));
  const tokenSize = Math.max(28, Math.min(64, size + 4));
  try {
    document.documentElement.style.setProperty("--ec-emoticon-inline-size", `${size}px`);
    document.documentElement.style.setProperty("--ec-emoticon-picker-size", `${pickerSize}px`);
    document.documentElement.style.setProperty("--ec-emoticon-token-size", `${tokenSize}px`);
  } catch (_) {}
  const out = document.getElementById("setEmoticonSizeVal");
  if (out) out.textContent = `${size}px`;
  return size;
}

window.ecClampEmoticonSize = ecClampEmoticonSize;
window.ecApplyEmoticonSizePrefs = ecApplyEmoticonSizePrefs;


function ecMessageEmoticonLimit(value = undefined) {
  const cfg = (window.HUI_CFG && typeof window.HUI_CFG === "object") ? window.HUI_CFG : {};
  const raw = value ?? cfg.max_emoticons_per_message ?? cfg.max_message_emoticons ?? 15;
  const n = parseInt(raw, 10);
  if (Number.isNaN(n)) return 15;
  // 0 disables the limiter for admins that explicitly configure it that way.
  return Math.max(0, Math.min(100, n));
}

function ecMakeEmoticonRenderState(opts = {}) {
  const max = ecMessageEmoticonLimit(opts.max);
  return { count: 0, removed: 0, max };
}

function ecCountCodeEmoticonsInText(rawText) {
  const text = String(rawText ?? "");
  const re = CodeEmoticons.regexp;
  if (!CodeEmoticons.enabled || !re) return 0;
  re.lastIndex = 0;
  let count = 0;
  for (const match of text.matchAll(re)) {
    const code = String(match[0] || "");
    if (CodeEmoticons.byCode.has(code.toLowerCase())) count += 1;
  }
  return count;
}

function ecLimitCodeEmoticonsInText(rawText, opts = {}) {
  const text = String(rawText ?? "");
  const max = ecMessageEmoticonLimit(opts.max);
  const re = CodeEmoticons.regexp;
  if (!CodeEmoticons.enabled || !re || max <= 0) {
    return { text, count: CodeEmoticons.enabled ? ecCountCodeEmoticonsInText(text) : 0, kept: CodeEmoticons.enabled ? ecCountCodeEmoticonsInText(text) : 0, removed: 0, max };
  }

  re.lastIndex = 0;
  let last = 0;
  let out = "";
  let count = 0;
  let kept = 0;
  let removed = 0;
  for (const match of text.matchAll(re)) {
    const code = String(match[0] || "");
    const start = match.index || 0;
    const entry = CodeEmoticons.byCode.get(code.toLowerCase());
    if (!entry) continue;
    if (start > last) out += text.slice(last, start);
    count += 1;
    if (count <= max) {
      out += code;
      kept += 1;
    } else {
      removed += 1;
    }
    last = start + code.length;
  }
  if (last < text.length) out += text.slice(last);
  return { text: out, count, kept, removed, max };
}

function ecNotifyEmoticonLimitTrim(result, opts = {}) {
  const removed = Number(result?.removed || 0);
  const max = Number(result?.max || ecMessageEmoticonLimit());
  if (!removed || !max) return;
  const surface = String(opts.surface || "message").trim().toLowerCase();
  const label = surface === "pm" || surface === "dm"
    ? "PM"
    : surface === "group"
      ? "group message"
      : surface === "room"
        ? "room message"
        : "message";
  try {
    if (typeof toast === "function") toast(`Emoticon limit is ${max} per ${label}. Removed ${removed} extra.`, "info", 4200);
  } catch (_) {}
}

async function ecLimitOutgoingChatEmoticons(rawText, opts = {}) {
  try {
    if (!CodeEmoticons.loaded || !CodeEmoticons.enabled || !CodeEmoticons.regexp) {
      await ensureCodeEmoticonsLoaded({ retryOnEmpty: true });
    }
  } catch (_) {}
  const result = ecLimitCodeEmoticonsInText(rawText, opts);
  if (opts.notify !== false) ecNotifyEmoticonLimitTrim(result, opts);
  return result;
}

window.ecMessageEmoticonLimit = ecMessageEmoticonLimit;
window.ecMakeEmoticonRenderState = ecMakeEmoticonRenderState;
window.ecCountCodeEmoticonsInText = ecCountCodeEmoticonsInText;
window.ecLimitCodeEmoticonsInText = ecLimitCodeEmoticonsInText;
window.ecLimitOutgoingChatEmoticons = ecLimitOutgoingChatEmoticons;

async function ensureEmojiLibraryLoaded(opts = {}) {
  // The old Unicode picker has been removed from this popover. Keep the helper
  // as a compatibility shim for older callers.
  return ensureCodeEmoticonsLoaded(opts);
}

function setEmojiStatus(message) {
  if (!EmojiUI.statusEl) return;
  const msg = String(message || "").trim();
  EmojiUI.statusEl.textContent = msg;
  EmojiUI.statusEl.classList.toggle("hidden", !msg);
}

function ensureEmojiPopover() {
  if (EmojiUI.pop) return EmojiUI.pop;

  const pop = document.createElement("div");
  pop.id = "ecEmojiPopover";
  pop.className = "ec-emojiPopover hidden";
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", "Emoticons");

  const status = document.createElement("div");
  status.className = "ec-emojiStatus hidden";
  status.setAttribute("aria-live", "polite");
  pop.appendChild(status);

  const codePanel = document.createElement("div");
  codePanel.className = "ec-codeEmoticonPanel";

  const codeHead = document.createElement("div");
  codeHead.className = "ec-codeEmoticonHead";
  const codeTitle = document.createElement("div");
  codeTitle.className = "ec-codeEmoticonTitle";
  codeTitle.textContent = "Emoticons";
  const codeHint = document.createElement("div");
  codeHint.className = "ec-codeEmoticonHint";
  codeHint.textContent = "Click a face";
  codeHead.appendChild(codeTitle);
  codeHead.appendChild(codeHint);

  const codeSearch = document.createElement("input");
  codeSearch.type = "search";
  codeSearch.className = "ec-codeEmoticonSearch";
  codeSearch.placeholder = "Search emoticons";
  codeSearch.setAttribute("aria-label", "Search emoticons");

  const codeGrid = document.createElement("div");
  codeGrid.className = "ec-codeEmoticonGrid";
  codeGrid.setAttribute("role", "listbox");

  codePanel.appendChild(codeHead);
  codePanel.appendChild(codeSearch);
  codePanel.appendChild(codeGrid);
  pop.appendChild(codePanel);

  codeSearch.addEventListener("input", () => renderCodeEmoticonGrid(codeGrid, codeSearch.value));

  document.body.appendChild(pop);

  const position = () => {
    if (!EmojiUI.activeAnchor) return;
    const r = EmojiUI.activeAnchor.getBoundingClientRect();

    // Match the fixed CSS sizes without forcing a popover layout read.
    const compact = (() => {
      try { return window.matchMedia('(max-width: 520px)').matches; } catch { return false; }
    })();
    const w = compact ? 320 : 360;
    const h = compact ? 380 : 420;

    let left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w));
    let top = r.top - h - 8;
    if (top < 8) top = Math.min(window.innerHeight - h - 8, r.bottom + 8);

    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  };

  // One global outside-click handler
  if (!document.body.dataset.ecEmojiOutsideBound) {
    document.body.dataset.ecEmojiOutsideBound = "1";
    document.addEventListener("mousedown", (e) => {
      if (!EmojiUI.visible || !EmojiUI.pop) return;
      const t = e.target;
      if (!(t instanceof Node)) return;
      if (EmojiUI.pop.contains(t)) return;
      if (EmojiUI.activeAnchor && EmojiUI.activeAnchor.contains(t)) return;
      closeEmojiPicker();
    });
    window.addEventListener("resize", () => { if (EmojiUI.visible && EmojiUI.pop?._ecPosition) EmojiUI.pop._ecPosition(); });
    window.addEventListener("scroll", () => { if (EmojiUI.visible && EmojiUI.pop?._ecPosition) EmojiUI.pop._ecPosition(); }, true);
    document.addEventListener("keydown", (e) => { if (EmojiUI.visible && e.key === "Escape") closeEmojiPicker(); });
  }

  // Expose helpers
  EmojiUI.pop = pop;
  EmojiUI.statusEl = status;
  EmojiUI.codeGrid = codeGrid;
  EmojiUI.codeSearch = codeSearch;
  pop._ecPosition = position;
  return pop;
}

async function openEmojiPicker(anchorEl, inputEl) {
  const pop = ensureEmojiPopover();

  // Toggle immediately so a second click on the same button closes the picker.
  if (EmojiUI.visible && EmojiUI.activeAnchor === anchorEl) {
    closeEmojiPicker();
    return;
  }

  EmojiUI.activeInput = inputEl || null;
  EmojiUI.activeAnchor = anchorEl || null;
  EmojiUI.visible = true;
  pop.classList.remove("hidden");
  setEmojiStatus("Loading emoticons…");
  renderCodeEmoticonGrid(EmojiUI.codeGrid, EmojiUI.codeSearch?.value || "");
  if (typeof ecNextAnimationFrame === 'function') ecNextAnimationFrame(() => pop._ecPosition && pop._ecPosition());
  else pop._ecPosition && pop._ecPosition();

  const ok = await ensureEmojiLibraryLoaded({ retryOnEmpty: true });
  if (!EmojiUI.visible || EmojiUI.activeAnchor !== anchorEl) return;
  if (!ok) {
    setEmojiStatus("Emoticons could not load. Check /api/emoticons/catalog and the root emoticons folder.");
  } else {
    setEmojiStatus("");
  }
  renderCodeEmoticonGrid(EmojiUI.codeGrid, EmojiUI.codeSearch?.value || "");
  if (typeof ecNextAnimationFrame === 'function') ecNextAnimationFrame(() => pop._ecPosition && pop._ecPosition());
  else pop._ecPosition && pop._ecPosition();
}
function closeEmojiPicker() {
  if (!EmojiUI.pop) return;
  EmojiUI.pop.classList.add("hidden");
  EmojiUI.visible = false;
  EmojiUI.activeInput = null;
  EmojiUI.activeAnchor = null;
}

const CodeEmoticons = {
  loading: false,
  loaded: false,
  enabled: false,
  entries: [],
  animationStopMs: 4500,
  regexp: null,
  byCode: new Map(),
  promise: null,
  assetPreloadPromise: null,
  assetsPreloaded: false,
  preloadedSrcs: new Set()
};

function escapeCodeRegexp(text) {
  return String(text || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeCodeEmoticonEntry(raw) {
  const name = String(raw?.name || "").trim();
  const src = String(raw?.src || "").trim();
  const codes = Array.isArray(raw?.codes)
    ? raw.codes.map((code) => String(code || "").trim()).filter(Boolean)
    : [String(raw?.code || "").trim()].filter(Boolean);
  if (!name || !src || !codes.length) return null;
  const fallbackSrcs = Array.isArray(raw?.fallback_srcs)
    ? raw.fallback_srcs.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  return {
    name,
    code: codes[0],
    codes,
    src,
    fallbackSrcs: [src, ...fallbackSrcs].filter((value, index, arr) => value && arr.indexOf(value) === index),
    label: String(raw?.label || name).trim() || name,
    category: String(raw?.category || "emoticons").trim() || "emoticons",
    availableLocal: !!raw?.available_local,
    assetExt: String(raw?.asset_ext || "").trim().toLowerCase(),
    animationStopMs: Number.isFinite(Number(raw?.animation_stop_ms)) ? Math.max(0, Number(raw.animation_stop_ms)) : null,
    order: Number.isFinite(Number(raw?.order)) ? Number(raw.order) : 0
  };
}

function ecAttachImageFallback(img, entry, fallbackText = "") {
  if (!img || !entry) return;
  const srcs = Array.isArray(entry.fallbackSrcs) && entry.fallbackSrcs.length ? entry.fallbackSrcs : [entry.src].filter(Boolean);
  img.dataset.ecSrcIndex = "0";
  img.onerror = () => {
    const nextIndex = Number(img.dataset.ecSrcIndex || 0) + 1;
    if (nextIndex < srcs.length) {
      img.dataset.ecSrcIndex = String(nextIndex);
      img.dataset.ecFreezeScheduled = "0";
      img.dataset.ecFrozen = "0";
      img.src = srcs[nextIndex];
      return;
    }
    const text = String(fallbackText || "");
    if (text) {
      try { img.replaceWith(document.createTextNode(text)); } catch {}
    }
  };
}

function ecIsAnimatedEmoticonCandidate(entry, src) {
  const ext = String(entry?.assetExt || "").toLowerCase();
  const raw = String(src || entry?.src || "").toLowerCase();
  return ext === "gif" || /\.gif(?:[?#]|$)/i.test(raw);
}

function ecCurrentEmoticonStopMs(entry) {
  if (entry && Number.isFinite(Number(entry.animationStopMs))) return Math.max(0, Number(entry.animationStopMs));
  return Math.max(0, Number(CodeEmoticons.animationStopMs || 0));
}

function ecFreezeAnimatedEmoticon(img) {
  if (!img || !img.isConnected || img.dataset.ecFrozen === "1") return false;
  const w = img.naturalWidth || img.width || 0;
  const h = img.naturalHeight || img.height || 0;
  if (!w || !h) return false;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: false });
    if (!ctx) return false;
    ctx.drawImage(img, 0, 0, w, h);
    const frozenSrc = canvas.toDataURL("image/png");
    if (!frozenSrc) return false;
    img.dataset.ecFrozen = "1";
    img.dataset.ecAnimatedSrc = img.currentSrc || img.src || "";
    img.onerror = null;
    img.src = frozenSrc;
    img.classList.add("is-frozen-emoticon");
    return true;
  } catch (err) {
    // Cross-origin images without CORS cannot be snapshotted. Keep them usable.
    try { console.debug("[emoticons] could not freeze GIF frame", err); } catch {}
    return false;
  }
}

function ecScheduleEmoticonFreeze(img, entry) {
  const stopMs = ecCurrentEmoticonStopMs(entry);
  if (!img || !stopMs || img.dataset.ecFreezeScheduled === "1") return;
  if (!ecIsAnimatedEmoticonCandidate(entry, img.currentSrc || img.src)) return;
  img.dataset.ecFreezeScheduled = "1";
  window.setTimeout(() => ecFreezeAnimatedEmoticon(img), stopMs);
}

function ecSetEmoticonImageSource(img, entry, fallbackText = "") {
  if (!img || !entry) return;
  ecAttachImageFallback(img, entry, fallbackText);
  img.addEventListener("load", () => ecScheduleEmoticonFreeze(img, entry));
  if (/^https?:/i.test(String(entry.src || ""))) {
    try { img.crossOrigin = "anonymous"; } catch {}
  }
  img.src = entry.src;
}

function rebuildCodeEmoticonIndex(entries) {
  const pairs = [];
  const byCode = new Map();
  for (const entry of entries || []) {
    for (const code of entry.codes || []) {
      const key = String(code || "").toLowerCase();
      if (!key || byCode.has(key)) continue;
      byCode.set(key, entry);
      pairs.push(String(code));
    }
  }
  pairs.sort((a, b) => b.length - a.length || a.localeCompare(b));
  CodeEmoticons.byCode = byCode;
  CodeEmoticons.regexp = pairs.length
    ? new RegExp(pairs.map((code) => `(${escapeCodeRegexp(code)})`).join("|"), "gi")
    : null;
}

function ecEmoticonBootPreloadEnabled() {
  const cfg = (window.HUI_CFG && typeof window.HUI_CFG === "object") ? window.HUI_CFG : {};
  if (cfg.emoticons_boot_preload_enabled === false) return false;
  if (cfg.emoticons_preload_enabled === false) return false;
  return true;
}

function ecEmoticonBootPreloadLimit() {
  const cfg = (window.HUI_CFG && typeof window.HUI_CFG === "object") ? window.HUI_CFG : {};
  const raw = cfg.emoticons_boot_preload_limit ?? cfg.emoticons_preload_limit ?? 180;
  const n = parseInt(raw, 10);
  if (Number.isNaN(n)) return 180;
  // 0 means catalog-only boot loading. Keep a hard upper bound so one oversized
  // custom catalog cannot flood a browser on first chat connect.
  return Math.max(0, Math.min(240, n));
}

function ecEmoticonBootPreloadConcurrency() {
  const cfg = (window.HUI_CFG && typeof window.HUI_CFG === "object") ? window.HUI_CFG : {};
  const device = (window.HUI_DEVICE && typeof window.HUI_DEVICE === "object") ? window.HUI_DEVICE : {};
  const fallback = device.is_phone || device.is_mobile || String(cfg.device_profile || "").toLowerCase() === "phone" ? 2 : 4;
  const n = parseInt(cfg.emoticons_boot_preload_concurrency ?? cfg.emoticons_preload_concurrency ?? fallback, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.max(1, Math.min(8, n));
}

function ecOnBrowserIdle(callback, timeout = 900) {
  if (typeof callback !== "function") return;
  try {
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(callback, { timeout });
      return;
    }
  } catch (_) {}
  window.setTimeout(callback, Math.max(25, Math.min(1000, timeout)));
}

function ecPreloadEmoticonImage(entry) {
  return new Promise((resolve) => {
    const srcs = Array.isArray(entry?.fallbackSrcs) && entry.fallbackSrcs.length
      ? entry.fallbackSrcs
      : [entry?.src].filter(Boolean);
    const uniqueSrcs = srcs.filter((src, index, arr) => src && arr.indexOf(src) === index);
    if (!uniqueSrcs.length) { resolve(false); return; }

    let index = 0;
    const img = new Image();
    img.decoding = "async";
    img.referrerPolicy = "no-referrer";
    if (/^https?:/i.test(String(uniqueSrcs[0] || ""))) {
      try { img.crossOrigin = "anonymous"; } catch (_) {}
    }

    const tryNext = () => {
      if (index >= uniqueSrcs.length) { resolve(false); return; }
      const src = uniqueSrcs[index++];
      if (CodeEmoticons.preloadedSrcs.has(src)) { resolve(true); return; }
      img.onload = () => {
        CodeEmoticons.preloadedSrcs.add(src);
        resolve(true);
      };
      img.onerror = () => tryNext();
      img.src = src;
    };

    tryNext();
  });
}

async function ecPreloadCodeEmoticonAssets(opts = {}) {
  if (!ecEmoticonBootPreloadEnabled() && !opts.force) return false;
  if (CodeEmoticons.assetsPreloaded && !opts.force) return true;
  if (CodeEmoticons.assetPreloadPromise && !opts.force) return CodeEmoticons.assetPreloadPromise;

  CodeEmoticons.assetPreloadPromise = (async () => {
    const ok = await ensureCodeEmoticonsLoaded({ retryOnEmpty: true });
    if (!ok || !CodeEmoticons.enabled) return false;

    const limit = opts.limit === undefined ? ecEmoticonBootPreloadLimit() : Math.max(0, Math.min(240, parseInt(opts.limit, 10) || 0));
    if (!limit) return true;

    const entries = (CodeEmoticons.entries || []).slice(0, limit);
    const concurrency = opts.concurrency === undefined ? ecEmoticonBootPreloadConcurrency() : Math.max(1, Math.min(8, parseInt(opts.concurrency, 10) || 1));
    let next = 0;
    let active = 0;
    let finished = 0;

    await new Promise((resolve) => {
      const pump = () => {
        if (finished >= entries.length) { resolve(); return; }
        while (active < concurrency && next < entries.length) {
          const entry = entries[next++];
          active += 1;
          ecPreloadEmoticonImage(entry).finally(() => {
            active -= 1;
            finished += 1;
            pump();
          });
        }
      };
      pump();
    });

    CodeEmoticons.assetsPreloaded = true;
    try { document.dispatchEvent(new CustomEvent("ec:emoticon-assets-preloaded", { detail: { count: CodeEmoticons.preloadedSrcs.size } })); } catch (_) {}
    return true;
  })();

  try {
    return await CodeEmoticons.assetPreloadPromise;
  } finally {
    CodeEmoticons.assetPreloadPromise = null;
  }
}

function ecPrimeEmoticonsOnChatBoot(opts = {}) {
  // Called as soon as the chat bundle loads and again from DOM boot. The first
  // phase fetches /api/emoticons/catalog immediately, so typed shortcuts can
  // render without waiting for the picker. The second phase warms image assets
  // during browser idle time so opening the picker feels instant.
  const loadPromise = ensureCodeEmoticonsLoaded({ retryOnEmpty: true });
  if (!ecEmoticonBootPreloadEnabled() && !opts.force) return loadPromise;
  ecOnBrowserIdle(() => {
    try { void ecPreloadCodeEmoticonAssets(opts); } catch (_) {}
  }, opts.idleTimeout || 750);
  return loadPromise;
}

async function ensureCodeEmoticonsLoaded(opts = {}) {
  const retryOnEmpty = !!opts.retryOnEmpty;
  if (CodeEmoticons.loaded && (!retryOnEmpty || CodeEmoticons.enabled)) return true;
  if (CodeEmoticons.promise) return CodeEmoticons.promise;
  CodeEmoticons.promise = (async () => {
    try {
      const resp = await fetch(ecVersionedStaticUrl("/api/emoticons/catalog"), { credentials: "same-origin", cache: "force-cache" });
      if (!resp.ok) throw new Error(`catalog HTTP ${resp.status}`);
      const data = await resp.json().catch(() => ({}));
      if (data && data.success === false) throw new Error(String(data.error || "catalog disabled"));
      const entries = Array.isArray(data?.entries) ? data.entries.map(normalizeCodeEmoticonEntry).filter(Boolean) : [];
      entries.sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
      CodeEmoticons.entries = entries;
      CodeEmoticons.animationStopMs = Number.isFinite(Number(data?.animation_stop_ms)) ? Math.max(0, Number(data.animation_stop_ms)) : 4500;
      CodeEmoticons.enabled = !!data?.enabled && entries.length > 0;
      rebuildCodeEmoticonIndex(entries);
      CodeEmoticons.loaded = true;
      try { document.dispatchEvent(new CustomEvent("ec:emoticons-loaded")); } catch {}
      return CodeEmoticons.enabled;
    } catch (err) {
      console.warn("[emoticons] catalog unavailable", err);
      CodeEmoticons.loaded = true;
      CodeEmoticons.enabled = false;
      CodeEmoticons.entries = [];
      CodeEmoticons.regexp = null;
      CodeEmoticons.byCode = new Map();
      return false;
    } finally {
      CodeEmoticons.promise = null;
    }
  })();
  return CodeEmoticons.promise;
}
function renderCodeEmoticonGrid(grid, query) {
  if (!grid) return;
  if (typeof ecClearNode === "function") ecClearNode(grid);
  else grid.replaceChildren();
  const q = String(query || "").trim().toLowerCase();
  const entries = (CodeEmoticons.entries || []).filter((entry) => {
    if (!q) return true;
    return entry.name.toLowerCase().includes(q) || entry.label.toLowerCase().includes(q) || (entry.codes || []).some((code) => String(code).toLowerCase().includes(q));
  }).slice(0, 180);
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "ec-codeEmoticonEmpty";
    empty.textContent = CodeEmoticons.loaded ? "No emoticons found" : "Loading…";
    grid.appendChild(empty);
    return;
  }
  entries.forEach((entry) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ec-codeEmoticonBtn";
    btn.title = entry.label;
    btn.setAttribute("aria-label", entry.label);
    const img = document.createElement("img");
    img.className = "ec-codeEmoticonPreview";
    img.alt = "";
    img.loading = "eager";
    img.decoding = "async";
    img.referrerPolicy = "no-referrer";
    img.draggable = false;
    img.addEventListener("load", () => { btn.classList.add("is-loaded-image"); btn.classList.remove("is-missing-image"); });
    img.addEventListener("error", () => { btn.classList.add("is-missing-image"); });
    ecSetEmoticonImageSource(img, entry, "");
    btn.appendChild(img);
    btn.addEventListener("click", () => {
      if (EmojiUI.activeInput) insertAtCursor(EmojiUI.activeInput, entry.code);
      closeEmojiPicker();
    });
    grid.appendChild(btn);
  });
}

function ecAppendCodeEmoticons(container, rawText, opts = {}) {
  const text = String(rawText ?? "");
  if (!container) return false;
  const renderState = (opts && opts.state) ? opts.state : ecMakeEmoticonRenderState(opts);
  const maxEmoticons = Number(renderState.max || 0);
  if (!CodeEmoticons.loaded) {
    const pending = document.createElement("span");
    pending.className = "ec-emoticonPendingText";
    pending.textContent = text;
    container.appendChild(pending);
    ensureCodeEmoticonsLoaded().then(() => {
      if (!pending.isConnected || !CodeEmoticons.enabled || !CodeEmoticons.regexp) return;
      const frag = document.createDocumentFragment();
      ecAppendCodeEmoticons(frag, text, { state: renderState });
      try { pending.replaceWith(frag); } catch {}
    }).catch(() => {});
    return false;
  }
  const re = CodeEmoticons.regexp;
  if (!CodeEmoticons.enabled || !re) {
    container.appendChild(document.createTextNode(text));
    return false;
  }
  re.lastIndex = 0;
  let last = 0;
  let replaced = false;
  for (const match of text.matchAll(re)) {
    const code = String(match[0] || "");
    const start = match.index || 0;
    const entry = CodeEmoticons.byCode.get(code.toLowerCase());
    if (!entry) continue;
    if (start > last) container.appendChild(document.createTextNode(text.slice(last, start)));
    renderState.count = Number(renderState.count || 0) + 1;
    if (maxEmoticons > 0 && renderState.count > maxEmoticons) {
      renderState.removed = Number(renderState.removed || 0) + 1;
      last = start + code.length;
      replaced = true;
      continue;
    }
    const img = document.createElement("img");
    img.className = "ec-inlineEmoticon";
    img.alt = code;
    img.title = entry.label;
    img.loading = "eager";
    img.decoding = "async";
    img.referrerPolicy = "no-referrer";
    img.draggable = false;
    ecSetEmoticonImageSource(img, entry, code);
    container.appendChild(img);
    last = start + code.length;
    replaced = true;
  }
  if (last < text.length) container.appendChild(document.createTextNode(text.slice(last)));
  return replaced;
}

window.ensureCodeEmoticonsLoaded = ensureCodeEmoticonsLoaded;
window.ecPreloadCodeEmoticonAssets = ecPreloadCodeEmoticonAssets;
window.ecPrimeEmoticonsOnChatBoot = ecPrimeEmoticonsOnChatBoot;
window.ecAppendCodeEmoticons = ecAppendCodeEmoticons;
try { void ecPrimeEmoticonsOnChatBoot({ reason: "module-load" }); } catch {}


function ecRichComposerEntryForCode(code) {
  const key = String(code || "").trim().toLowerCase();
  if (!key) return null;
  return CodeEmoticons.byCode.get(key) || null;
}

function ecRichComposerSelectionInside(editor) {
  try {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    if (!editor.contains(range.commonAncestorContainer)) return null;
    return range;
  } catch (_) {
    return null;
  }
}

function ecRichComposerPlaceCaretAtEnd(editor) {
  if (!editor) return;
  try {
    const range = document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  } catch (_) {}
}

function ecRichComposerNodeToText(node) {
  if (!node) return "";
  if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const el = node;
  if (el.dataset && el.dataset.ecEmoticonCode) return String(el.dataset.ecEmoticonCode || "");
  if (el.tagName === "BR") return "\n";
  let out = "";
  for (const child of Array.from(el.childNodes || [])) out += ecRichComposerNodeToText(child);
  return out;
}

function ecRichComposerValueFromEditor(editor) {
  let out = "";
  for (const child of Array.from(editor?.childNodes || [])) out += ecRichComposerNodeToText(child);
  return out.replace(/\u00a0/g, " ");
}

function ecLimitRichComposerText(rawText, opts = {}) {
  const text = String(rawText ?? "");
  const max = ecMessageEmoticonLimit(opts.max);
  if (!CodeEmoticons.loaded || !CodeEmoticons.enabled || !CodeEmoticons.regexp || max <= 0) {
    return { text, trimmed: false, result: { text, count: 0, kept: 0, removed: 0, max } };
  }
  const result = ecLimitCodeEmoticonsInText(text, { max });
  const limitedText = String(result?.text ?? text);
  return { text: limitedText, trimmed: Number(result?.removed || 0) > 0, result };
}

function ecRichComposerMakeToken(entry, code) {
  const token = document.createElement("span");
  token.className = "ec-richEmoticonToken";
  token.contentEditable = "false";
  token.dataset.ecEmoticonCode = String(code || entry?.code || "");
  token.title = String(entry?.label || entry?.name || token.dataset.ecEmoticonCode || "Emoticon");
  token.setAttribute("aria-label", token.title);

  const img = document.createElement("img");
  img.className = "ec-richEmoticonImg";
  img.alt = token.dataset.ecEmoticonCode;
  img.loading = "eager";
  img.decoding = "async";
  img.referrerPolicy = "no-referrer";
  img.draggable = false;
  if (entry) ecSetEmoticonImageSource(img, entry, token.dataset.ecEmoticonCode);
  token.appendChild(img);
  return token;
}

function ecRichComposerRenderPlain(editor, text) {
  if (!editor) return;
  let raw = String(text || "");
  const limited = ecLimitRichComposerText(raw, { notify: false });
  raw = limited.text;
  if (typeof ecClearNode === "function") ecClearNode(editor);
  else editor.replaceChildren();
  if (!raw) return;
  const re = CodeEmoticons.loaded && CodeEmoticons.enabled ? CodeEmoticons.regexp : null;
  if (!re) {
    editor.appendChild(document.createTextNode(raw));
    return;
  }
  re.lastIndex = 0;
  let last = 0;
  for (const match of raw.matchAll(re)) {
    const code = String(match[0] || "");
    const start = match.index || 0;
    const entry = ecRichComposerEntryForCode(code);
    if (!entry) continue;
    if (start > last) editor.appendChild(document.createTextNode(raw.slice(last, start)));
    editor.appendChild(ecRichComposerMakeToken(entry, code));
    last = start + code.length;
  }
  if (last < raw.length) editor.appendChild(document.createTextNode(raw.slice(last)));
}

function ecRichComposerInsertNode(editor, node) {
  if (!editor || !node) return;
  editor.focus();
  let range = ecRichComposerSelectionInside(editor);
  if (!range) {
    ecRichComposerPlaceCaretAtEnd(editor);
    range = ecRichComposerSelectionInside(editor);
  }
  try {
    range.deleteContents();
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  } catch (_) {
    editor.appendChild(node);
    ecRichComposerPlaceCaretAtEnd(editor);
  }
}

function ecRichComposerInsertText(editor, text) {
  const node = document.createTextNode(String(text || ""));
  ecRichComposerInsertNode(editor, node);
}

function ecRichComposerInsertNewline(editor) {
  ecRichComposerInsertText(editor, "\n");
}

function ecIsPlainEnterToSend(ev) {
  if (!ev || ev.key !== "Enter" || ev.isComposing) return false;
  return !ev.shiftKey && !ev.ctrlKey && !ev.metaKey && !ev.altKey;
}

window.ecIsPlainEnterToSend = ecIsPlainEnterToSend;

function ecEnsureRichComposer(inputEl) {
  if (!inputEl || inputEl._ecRichComposer) return inputEl?._ecRichComposer || null;
  if (inputEl.dataset.ecRichComposer === "off") return null;

  const editor = document.createElement("div");
  editor.className = "ec-richComposer ym-input";
  editor.contentEditable = "true";
  editor.setAttribute("role", "textbox");
  editor.setAttribute("aria-multiline", "true");
  editor.setAttribute("spellcheck", inputEl.getAttribute("spellcheck") || "true");
  editor.dataset.placeholder = inputEl.getAttribute("placeholder") || "Type a message…";
  if (inputEl.id) editor.dataset.forInput = inputEl.id;

  inputEl.classList.add("ec-richComposerHiddenInput");
  inputEl.setAttribute("aria-hidden", "true");
  inputEl.tabIndex = -1;
  inputEl.insertAdjacentElement("beforebegin", editor);

  const proto = Object.getPrototypeOf(inputEl);
  const nativeValue = Object.getOwnPropertyDescriptor(proto || HTMLInputElement.prototype, "value") || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
  let internalValue = nativeValue && nativeValue.get ? String(nativeValue.get.call(inputEl) || "") : String(inputEl.value || "");
  let syncing = false;

  const composer = {
    editor,
    input: inputEl,
    get value() { return internalValue; },
    set value(v) {
      let nextValue = String(v || "");
      const limited = ecLimitRichComposerText(nextValue, { notify: false });
      nextValue = limited.text;
      internalValue = nextValue;
      if (nativeValue && nativeValue.set) {
        try { nativeValue.set.call(inputEl, internalValue); } catch (_) {}
      }
      if (!syncing) {
        syncing = true;
        ecRichComposerRenderPlain(editor, internalValue);
        ecRichComposerPlaceCaretAtEnd(editor);
        syncing = false;
      }
    },
    syncFromEditor() {
      if (syncing) return;
      syncing = true;
      let nextValue = ecRichComposerValueFromEditor(editor);
      const limited = ecLimitRichComposerText(nextValue, { notify: false });
      nextValue = limited.text;
      internalValue = nextValue;
      if (nativeValue && nativeValue.set) {
        try { nativeValue.set.call(inputEl, internalValue); } catch (_) {}
      }
      if (limited.trimmed) {
        const now = Date.now();
        if (!this._lastTrimNoticeAt || (now - this._lastTrimNoticeAt) > 2200) {
          this._lastTrimNoticeAt = now;
          ecNotifyEmoticonLimitTrim(limited.result, { surface: inputEl.dataset?.ecSurface || "message" });
        }
        ecRichComposerRenderPlain(editor, internalValue);
        ecRichComposerPlaceCaretAtEnd(editor);
      }
      syncing = false;
      try { inputEl.dispatchEvent(new Event("input", { bubbles: true })); } catch (_) {}
    },
    insertTextOrEmoticon(text) {
      const code = String(text || "");
      const entry = ecRichComposerEntryForCode(code);
      if (entry) {
        const max = ecMessageEmoticonLimit();
        if (max > 0 && ecCountCodeEmoticonsInText(internalValue) >= max) {
          ecNotifyEmoticonLimitTrim({ removed: 1, max }, { surface: "message" });
          editor.focus();
          return;
        }
        ecRichComposerInsertNode(editor, ecRichComposerMakeToken(entry, code));
      } else {
        ecRichComposerInsertText(editor, code);
      }
      this.syncFromEditor();
      editor.focus();
    },
    refreshVisuals() {
      if (document.activeElement === editor) return;
      syncing = true;
      ecRichComposerRenderPlain(editor, internalValue);
      syncing = false;
    }
  };

  try {
    Object.defineProperty(inputEl, "value", {
      configurable: true,
      enumerable: true,
      get() { return internalValue; },
      set(v) { composer.value = v; }
    });
  } catch (_) {}

  try { inputEl.focus = () => editor.focus(); } catch (_) {}
  inputEl._ecRichComposer = composer;

  editor.addEventListener("input", () => composer.syncFromEditor());
  editor.addEventListener("paste", (ev) => {
    try {
      ev.preventDefault();
      const text = ev.clipboardData?.getData("text/plain") || "";
      ecRichComposerInsertText(editor, text);
      composer.syncFromEditor();
    } catch (_) {}
  });
  editor.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" || ev.isComposing) return;

    // Plain Enter sends. Ctrl+Enter / Cmd+Enter / Shift+Enter inserts a real
    // newline into the hidden message value so room chat, PMs, and group chat
    // can send multi-line messages without accidentally firing Send.
    if (ev.ctrlKey || ev.metaKey || ev.shiftKey) {
      ev.preventDefault();
      ecRichComposerInsertNewline(editor);
      composer.syncFromEditor();
      return;
    }

    ev.preventDefault();
    composer.syncFromEditor();
    try {
      inputEl.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Enter",
        code: "Enter",
        bubbles: true,
        cancelable: true,
        ctrlKey: false,
        altKey: false,
        metaKey: false,
        shiftKey: false
      }));
    } catch (_) {
      try {
        inputEl.onkeydown && inputEl.onkeydown({
          key: "Enter",
          isComposing: false,
          shiftKey: false,
          ctrlKey: false,
          metaKey: false,
          altKey: false,
          preventDefault() {}
        });
      } catch {}
    }
  });
  editor.addEventListener("blur", () => {
    composer.syncFromEditor();
    try { inputEl.dispatchEvent(new Event("blur", { bubbles: false })); } catch (_) {}
  });

  document.addEventListener("ec:emoticons-loaded", () => {
    const limited = ecLimitRichComposerText(internalValue, { notify: false });
    if (limited.trimmed) composer.value = limited.text;
    else composer.refreshVisuals();
  });
  composer.value = internalValue;
  return composer;
}

window.ecEnsureRichComposer = ecEnsureRichComposer;

function bindEmojiButton(btnEl, inputEl) {
  if (!btnEl || !inputEl) return;
  const composer = ecEnsureRichComposer(inputEl);
  if (btnEl.dataset.ecEmojiBound === "1") return;
  btnEl.dataset.ecEmojiBound = "1";
  btnEl.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    void openEmojiPicker(btnEl, inputEl);
    try { composer?.syncFromEditor?.(); } catch {}
  });
}
