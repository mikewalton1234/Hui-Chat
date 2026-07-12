function parseGifMarker(text) {
  if (typeof text !== 'string') return null;
  const s = text.trim();
  if (!s) return null;
  if (!s.toLowerCase().startsWith('gif:')) return null;
  const url = s.slice(4).trim();
  if (!url) return null;
  if (url.length > 2048) return null;
  const safe = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(url, { allowRelative: false, allowExternal: true })
    : (/^https?:\/\//i.test(url) ? url : '');
  return safe || null;
}

function _gifFallbackUrl(url) {
  try {
    const u = new URL(url);
    const host = String(u.hostname || '').toLowerCase();
    if (!host.endsWith('giphy.com')) return null;
    const parts = (u.pathname || "").split("/").filter(Boolean);
    const mi = parts.indexOf("media");
    if (mi !== -1 && parts.length > (mi + 1)) {
      const id = String(parts[mi + 1] || '').trim();
      // GIPHY ids are case-sensitive. Never treat the newer opaque v1.* media
      // path segment as a GIF id, and do not lower/upper-case the value.
      if (/^[A-Za-z0-9_-]{4,128}$/.test(id) && !id.startsWith('v1.')) {
        return `https://media.giphy.com/media/${id}/giphy.gif`;
      }
    }
  } catch {}
  return null;
}

function _gifCacheBust(url) {
  try {
    const u = new URL(url);
    u.searchParams.set("cb", String(Date.now()));
    return u.toString();
  } catch {
    const sep = url.includes("?") ? "&" : "?";
    return url + sep + "cb=" + Date.now();
  }
}

function setGifInlineUiState(img, state) {
  if (!img) return;
  const ui = img._ecGifUi || null;
  const wrap = ui?.wrap || (typeof img.closest === 'function' ? img.closest('.ym-gifWrap') : null);
  const placeholder = ui?.placeholder || (wrap ? wrap.querySelector('.ym-gifPlaceholder') : null);
  if (!wrap) return;

  wrap.classList.remove('is-loading', 'is-loaded', 'is-broken');
  if (state === 'loaded') wrap.classList.add('is-loaded');
  else if (state === 'broken') wrap.classList.add('is-broken');
  else wrap.classList.add('is-loading');

  if (!placeholder) return;
  if (state === 'loaded') {
    placeholder.hidden = true;
    placeholder.setAttribute('aria-hidden', 'true');
    placeholder.removeAttribute('aria-busy');
    placeholder.textContent = '';
  } else if (state === 'broken') {
    placeholder.hidden = false;
    placeholder.setAttribute('aria-hidden', 'false');
    placeholder.removeAttribute('aria-busy');
    placeholder.textContent = 'GIF unavailable · click to retry';
  } else {
    placeholder.hidden = false;
    placeholder.setAttribute('aria-hidden', 'false');
    placeholder.setAttribute('aria-busy', 'true');
    placeholder.textContent = 'Loading GIF…';
  }
}

function refreshGifInlineImage(img, reason = "manual") {
  if (!img) return false;

  const maxTries = 3;
  const tries = Number(img.dataset.gifTry || "0");
  if (tries >= maxTries) {
    img.dataset.gifFailed = "1";
    img.classList.add("ym-gifBroken");
    setGifInlineUiState(img, 'broken');
    return false;
  }

  const rawOriginal = img.dataset.gifOrig || img.dataset.gifBase || img.src || "";
  const original = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(rawOriginal, { allowRelative: false, allowExternal: true })
    : (/^https?:\/\//i.test(rawOriginal) ? rawOriginal : '');
  const rawFallback = img.dataset.gifFallback || _gifFallbackUrl(original) || "";
  const fallback = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(rawFallback, { allowRelative: false, allowExternal: true })
    : (/^https?:\/\//i.test(rawFallback) ? rawFallback : '');
  const canonical = (tries === 0 || !fallback) ? original : fallback;
  if (!canonical) return false;

  img.dataset.gifBase = canonical;
  if (fallback) img.dataset.gifFallback = fallback;
  img.dataset.gifTry = String(tries + 1);
  img.dataset.gifLoaded = "0";
  img.dataset.gifFailed = "0";
  img.classList.remove("ym-gifBroken");
  setGifInlineUiState(img, 'loading');

  const next = _gifCacheBust(canonical);

  // Force a reload (cache-busted) without spamming the network.
  try { img.src = ""; } catch {}
  setTimeout(() => { img.src = next; }, 0);

  return true;
}

function scheduleGifLoadCheck(img) {
  if (!img) return;
  if (img.dataset.gifWatch === "1") return;
  img.dataset.gifWatch = "1";

  // Some browsers keep images in a "stuck" state inside scroll containers.
  // If it doesn't load within a few seconds, retry with a cache-busted URL.
  setTimeout(() => {
    if (img.dataset.gifLoaded === "1") return;
    if (!navigator.onLine) return;
    if (!img.complete || img.naturalWidth === 0) {
      refreshGifInlineImage(img, "timeout");
    }
  }, 7000);
}

function refreshUnloadedGifsInScope(scope = document) {
  const root = scope || document;
  const imgs = root.querySelectorAll('img[data-ec-gif="1"]');
  let n = 0;

  imgs.forEach((img) => {
    const loaded = img.dataset.gifLoaded === "1";
    if (loaded) return;

    if (!img.complete || img.naturalWidth === 0 || img.dataset.gifFailed === "1") {
      if (refreshGifInlineImage(img, "scan")) n++;
    }
  });

  return n;
}

// Console / power-user hook: window.HuiChatRefreshGifs()
if (!window.HuiChatRefreshGifs) {
  window.HuiChatRefreshGifs = () => {
    const n = refreshUnloadedGifsInScope(document);
    try { toast(`↻ Retried ${n} GIF(s)`, "info"); } catch {}
    return n;
  };
}

// Auto-recover when network comes back or user returns to the tab
if (!window.__ecGifAutoRefreshBound) {
  window.__ecGifAutoRefreshBound = true;
  window.addEventListener("online", () => setTimeout(() => refreshUnloadedGifsInScope(document), 400));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) setTimeout(() => refreshUnloadedGifsInScope(document), 400);
  });
}

function configureGifInlineImage(img, gifUrl, ui = null) {
  img.className = "ym-gifInline";
  // Lazy-loading inside scroll containers is unreliable; force eager for chat UX.
  img.loading = "eager";
  img.decoding = "async";
  img.referrerPolicy = "no-referrer";
  img.alt = "";
  img.setAttribute('role', 'img');
  img.setAttribute('aria-label', 'GIF');
  img.draggable = false;

  img.dataset.ecGif = "1";
  const safeGifUrl = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(gifUrl, { allowRelative: false, allowExternal: true })
    : (/^https?:\/\//i.test(String(gifUrl || '')) ? String(gifUrl || '') : '');
  img.dataset.gifOrig = safeGifUrl;
  img._ecGifUi = ui || null;

  const base = safeGifUrl;
  const fallback = _gifFallbackUrl(safeGifUrl);
  if (!base) {
    setGifInlineUiState(img, 'broken');
    return;
  }
  img.dataset.gifBase = base;
  if (fallback) img.dataset.gifFallback = fallback;
  img.dataset.gifTry = "0";
  img.dataset.gifLoaded = "0";
  img.dataset.gifFailed = "0";

  setGifInlineUiState(img, 'loading');
  img.src = base;
  scheduleGifLoadCheck(img);
  disableOutputContextMenu(img);

  img.onload = () => {
    img.dataset.gifLoaded = "1";
    img.dataset.gifFailed = "0";
    img.classList.remove("ym-gifBroken");
    setGifInlineUiState(img, 'loaded');
    if (img._ecScrollLog) scheduleScrollLogToBottom(img._ecScrollLog);
  };

  img.onerror = () => {
    refreshGifInlineImage(img, "error");
  };

  img.onclick = () => {
    const notLoaded = (img.dataset.gifLoaded !== "1") && (!img.complete || img.naturalWidth === 0);

    // If it looks stuck/broken, clicking retries the GIF load only.
    if (notLoaded || img.dataset.gifFailed === "1") {
      refreshGifInlineImage(img, "click");
    }
  };

  if (ui?.wrap) {
    ui.wrap.onclick = () => {
      const notLoaded = (img.dataset.gifLoaded !== "1") && (!img.complete || img.naturalWidth === 0);
      if (notLoaded || img.dataset.gifFailed === "1") {
        refreshGifInlineImage(img, 'wrap-click');
      }
    };
  }
}
