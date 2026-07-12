// Missed (offline) PM notifications
// - Only counts messages received while you were offline.
// - Clicking an item opens the DM window and pulls all currently missed PMs
//   from that sender (ciphertext-only from the server).
// ───────────────────────────────────────────────────────────────────────────────
let MISSED_SUMMARY_TOAST_ARMED = false;
let EC_LAST_MISSED_PM_POPUP_TOTAL = 0;
let EC_LAST_MISSED_PM_POPUP_SIG = "";
let EC_LAST_MISSED_PM_POPUP_AT = 0;
const EC_MISSED_PM_DEBUG_STORAGE_KEY = "ec_debug_missed_messages";

function ecMissedDebugEnabled() {
  try {
    return !!(
      window.EC_DEBUG_MISSED_MESSAGES ||
      localStorage.getItem(EC_MISSED_PM_DEBUG_STORAGE_KEY) === "1" ||
      new URLSearchParams(window.location.search || "").has("ecMissedDebug")
    );
  } catch {
    return !!window.EC_DEBUG_MISSED_MESSAGES;
  }
}

function ecMissedDebug(stage, detail = {}) {
  if (!ecMissedDebugEnabled()) return;
  try {
    const payload = {
      stage,
      detail,
      state: (typeof ecDumpMissedPmDebugState === "function") ? ecDumpMissedPmDebugState({ silent: true }) : null,
    };
    console.groupCollapsed(`[Hui Chat missed PM] ${stage}`);
    console.log(payload);
    console.groupEnd();
  } catch (err) {
    try { console.debug("[Hui Chat missed PM]", stage, detail, err); } catch {}
  }
}

function ecGetMissedBubbleVisualState() {
  const btn = $("railMissedBtn");
  const countEl = $("railMissedCount");
  const rail = $("dockAlertRail");
  const flyout = $("dockAlertFlyout");
  const out = {
    exists: !!btn,
    countText: countEl ? String(countEl.textContent || "") : null,
    buttonClass: btn ? String(btn.className || "") : null,
    buttonAriaHidden: btn ? btn.getAttribute("aria-hidden") : null,
    railClass: rail ? String(rail.className || "") : null,
    flyoutClass: flyout ? String(flyout.className || "") : null,
    rect: null,
    computed: null,
  };
  try {
    if (btn) {
      const rect = btn.getBoundingClientRect();
      const cs = window.getComputedStyle ? getComputedStyle(btn) : null;
      out.rect = {
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        right: Math.round(rect.right),
        bottom: Math.round(rect.bottom),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
      out.computed = cs ? {
        display: cs.display,
        opacity: cs.opacity,
        visibility: cs.visibility,
        pointerEvents: cs.pointerEvents,
        position: cs.position,
        transform: cs.transform,
        zIndex: cs.zIndex,
      } : null;
    }
  } catch {}
  return out;
}

function ecDumpMissedPmDebugState(opts = {}) {
  const totals = (() => {
    try { return (typeof ecGetMissedPmTotals === "function") ? ecGetMissedPmTotals() : null; } catch { return null; }
  })();
  const snapshot = {
    debugEnabled: ecMissedDebugEnabled(),
    serverSummary: Array.isArray(UIState?.missedPmSummary) ? UIState.missedPmSummary : [],
    liveUnread: (() => {
      try {
        const map = ecEnsureLivePmUnreadMap();
        return Array.from(map.entries()).map(([key, value]) => ({ key, value }));
      } catch { return []; }
    })(),
    combined: totals?.items || [],
    total: Number(totals?.total || 0) || 0,
    threads: Number(totals?.threads || 0) || 0,
    bubble: ecGetMissedBubbleVisualState(),
  };
  if (!opts.silent) {
    try { console.log("[Hui Chat missed PM debug state]", snapshot); } catch {}
  }
  return snapshot;
}

function ecEnableMissedPmDebug(enabled = true) {
  const on = enabled !== false;
  try { window.EC_DEBUG_MISSED_MESSAGES = on; } catch {}
  try { localStorage.setItem(EC_MISSED_PM_DEBUG_STORAGE_KEY, on ? "1" : "0"); } catch {}
  try { document.body?.classList.toggle("ecMissedDebugActive", on); } catch {}
  try { console.info(`[Hui Chat missed PM] debug ${on ? "enabled" : "disabled"}`); } catch {}
  return ecDumpMissedPmDebugState();
}

function ecDisableMissedPmDebug() {
  return ecEnableMissedPmDebug(false);
}

function ecMissedBubbleLooksHidden() {
  try {
    const btn = $("railMissedBtn");
    if (!btn) return true;
    const rect = btn.getBoundingClientRect();
    const cs = getComputedStyle(btn);
    if (!rect || rect.width < 20 || rect.height < 20) return true;
    if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity || 0) <= 0.05) return true;
    if (rect.right <= 0 || rect.bottom <= 0 || rect.left >= window.innerWidth || rect.top >= window.innerHeight) return true;
    return false;
  } catch {
    return false;
  }
}

function ecRepairMissedBubblePaintPath(reason = "repair") {
  const btn = $("railMissedBtn");
  if (!btn) return false;
  const total = (() => {
    try { return Number(ecGetMissedPmTotals().total || 0) || 0; } catch { return Number($("railMissedCount")?.textContent || 0) || 0; }
  })();
  if (total <= 0) {
    btn.classList.remove("ecViewportFallback");
    return false;
  }
  const hidden = ecMissedBubbleLooksHidden();
  ecMissedDebug("bubble.paint_check", { reason, hidden, total, visual: ecGetMissedBubbleVisualState() });
  if (!hidden) return false;
  btn.classList.add("ecViewportFallback");
  btn.style.opacity = "1";
  btn.style.visibility = "visible";
  btn.style.pointerEvents = "auto";
  ecMissedDebug("bubble.viewport_fallback_applied", { reason, total, visual: ecGetMissedBubbleVisualState() });
  return true;
}

function ecSimulateMissedPmBubble(sender = "SimulatedUser", count = 2, opts = {}) {
  const clean = String(sender || "SimulatedUser").replace(/\s+/g, " ").trim() || "SimulatedUser";
  const n = Math.max(1, Number(count || 1) || 1);
  const current = Array.isArray(UIState?.missedPmSummary) ? UIState.missedPmSummary.slice() : [];
  const next = current.filter((it) => !it || !ecMissedSamePeer(it.sender, clean));
  next.unshift({ sender: clean, count: n, last_ts: Date.now(), simulated: true });
  const normalized = (typeof normalizeMissedPmSummaryItems === "function") ? normalizeMissedPmSummaryItems(next) : next;
  UIState.missedPmSummary = normalized;
  MISSED_SUMMARY_TOAST_ARMED = true;
  ecMissedDebug("simulator.start", { sender: clean, count: n, opts });
  try { renderMissedPmList(normalized); } catch (err) { ecMissedDebug("simulator.render_error", { error: String(err?.message || err) }); }
  try {
    ecMaybePopupMissedPmSummary(normalized, {
      total: n,
      reason: "simulator",
      forceActiveConversationPopup: true,
      forcePopupEvenIfActive: true,
    });
  } catch (err) { ecMissedDebug("simulator.popup_error", { error: String(err?.message || err) }); }
  setTimeout(() => { try { ecRepairMissedBubblePaintPath("simulator_after_render"); } catch {} }, 80);
  return ecDumpMissedPmDebugState();
}

function ecClearSimulatedMissedPmBubble() {
  const current = Array.isArray(UIState?.missedPmSummary) ? UIState.missedPmSummary.slice() : [];
  UIState.missedPmSummary = current.filter((it) => !it?.simulated);
  try { renderMissedPmList(UIState.missedPmSummary); } catch {}
  try { ecRepairMissedBubblePaintPath("simulator_clear"); } catch {}
  return ecDumpMissedPmDebugState();
}

try { if (ecMissedDebugEnabled()) document.addEventListener("DOMContentLoaded", () => { try { document.body?.classList.add("ecMissedDebugActive"); } catch {} }); } catch {}

try {
  window.ecEnableMissedPmDebug = ecEnableMissedPmDebug;
  window.ecDisableMissedPmDebug = ecDisableMissedPmDebug;
  window.ecDumpMissedPmDebugState = ecDumpMissedPmDebugState;
  window.ecSimulateMissedPmBubble = ecSimulateMissedPmBubble;
  window.ecClearSimulatedMissedPmBubble = ecClearSimulatedMissedPmBubble;
  window.ecRepairMissedBubblePaintPath = ecRepairMissedBubblePaintPath;
} catch {}

function ecMissedCanonicalPeerKey(peer) {
  try {
    if (typeof ecDmCanonicalKey === "function") return ecDmCanonicalKey(peer);
  } catch {}
  try {
    if (typeof ecPmPeerKey === "function") return ecPmPeerKey(peer);
  } catch {}
  return String(peer || "").trim().toLowerCase();
}

function ecMissedSamePeer(a, b) {
  const ka = ecMissedCanonicalPeerKey(a);
  const kb = ecMissedCanonicalPeerKey(b);
  return !!ka && !!kb && ka === kb;
}


function ecEnsureLivePmUnreadMap() {
  try {
    if (!UIState.livePmUnreadCounts || typeof UIState.livePmUnreadCounts.get !== "function") {
      UIState.livePmUnreadCounts = new Map();
    }
    return UIState.livePmUnreadCounts;
  } catch {
    return new Map();
  }
}

function ecGetLivePmUnreadEntry(peer) {
  const map = ecEnsureLivePmUnreadMap();
  const wanted = ecMissedCanonicalPeerKey(peer);
  if (!wanted) return null;
  for (const [key, item] of map.entries()) {
    if (ecMissedCanonicalPeerKey(key) === wanted || ecMissedCanonicalPeerKey(item?.sender) === wanted) {
      return { key, item };
    }
  }
  return null;
}

function ecGetCombinedMissedPmItems(items = null) {
  const merged = new Map();
  const serverKeys = new Set();
  const add = (sender, count, lastTs = null, source = '', opts = {}) => {
    const cleanSender = String(sender || '').trim();
    const key = ecMissedCanonicalPeerKey(cleanSender);
    const n = Math.max(0, Number(count || 0) || 0);
    if (!cleanSender || !key || n <= 0) return;
    const ts = (typeof lastTs === 'number' && Number.isFinite(lastTs)) ? lastTs : null;
    const sourceName = source || 'missed';
    const serverBacked = !!opts.serverBacked;
    const prev = merged.get(key);
    if (!prev) {
      merged.set(key, { sender: cleanSender, count: n, last_ts: ts, source: sourceName, server_backed: serverBacked });
      return;
    }

    // Live PMs that include a server unread id are the same durable unread queue
    // the server summary reports.  Once the server summary exists for that peer,
    // use the larger count instead of adding both together and double-counting.
    if (serverBacked && serverKeys.has(key)) {
      prev.count = Math.max(Number(prev.count || 0) || 0, n);
    } else if (sourceName === 'offline' && prev.server_backed) {
      prev.count = Math.max(Number(prev.count || 0) || 0, n);
      serverKeys.add(key);
    } else {
      prev.count += n;
    }

    prev.server_backed = !!(prev.server_backed || serverBacked);
    if (sourceName && !String(prev.source || '').includes(sourceName)) prev.source = `${prev.source || 'missed'}+${sourceName}`;
    if (ts !== null && (prev.last_ts === null || typeof prev.last_ts !== 'number' || ts > prev.last_ts)) prev.last_ts = ts;
  };

  const serverItems = Array.isArray(items) ? items : (Array.isArray(UIState?.missedPmSummary) ? UIState.missedPmSummary : []);
  for (const it of serverItems) {
    const sender = String(it?.sender || '').trim();
    const key = ecMissedCanonicalPeerKey(sender);
    if (key) serverKeys.add(key);
    add(sender, it?.count, it?.last_ts, 'offline');
  }

  try {
    const map = ecEnsureLivePmUnreadMap();
    for (const [peer, raw] of map.entries()) {
      if (raw && typeof raw === 'object') add(raw.sender || peer, raw.count, raw.last_ts, 'live', { serverBacked: !!raw.server_backed });
      else add(peer, raw, null, 'live');
    }
  } catch {}

  const combined = Array.from(merged.values()).sort((a, b) => {
    const bt = (typeof b.last_ts === 'number') ? b.last_ts : 0;
    const at = (typeof a.last_ts === 'number') ? a.last_ts : 0;
    if (bt !== at) return bt - at;
    return String(a.sender).localeCompare(String(b.sender));
  });
  ecMissedDebug("combined.items", { itemsArg: Array.isArray(items) ? items : null, combined });
  return combined;
}

function ecGetMissedPmTotals(items = null) {
  const list = ecGetCombinedMissedPmItems(items);
  const total = list.reduce((sum, it) => sum + (Number(it?.count || 0) || 0), 0);
  return { items: list, threads: list.length, total };
}

function ecMissedPmCountForPeer(peer, items = null) {
  const wanted = ecMissedCanonicalPeerKey(peer);
  if (!wanted) return 0;
  try {
    const list = (typeof ecGetCombinedMissedPmItems === 'function')
      ? ecGetCombinedMissedPmItems(items)
      : (Array.isArray(items) ? items : (Array.isArray(UIState?.missedPmSummary) ? UIState.missedPmSummary : []));
    return (Array.isArray(list) ? list : []).reduce((sum, it) => {
      const key = ecMissedCanonicalPeerKey(it?.sender);
      if (!key || key !== wanted) return sum;
      return sum + (Number(it?.count || 0) || 0);
    }, 0);
  } catch {
    return 0;
  }
}

function ecHasMissedPmForPeer(peer, items = null) {
  return ecMissedPmCountForPeer(peer, items) > 0;
}

function ecRememberMissedPmFirstSeen(items = null) {
  try {
    if (!(UIState.missedPmFirstSeenAt instanceof Map)) UIState.missedPmFirstSeenAt = new Map();
    const seen = UIState.missedPmFirstSeenAt;
    const now = Date.now();
    const activeKeys = new Set();
    const list = (typeof ecGetCombinedMissedPmItems === 'function')
      ? ecGetCombinedMissedPmItems(items)
      : (Array.isArray(items) ? items : (Array.isArray(UIState?.missedPmSummary) ? UIState.missedPmSummary : []));
    for (const it of (Array.isArray(list) ? list : [])) {
      const key = ecMissedCanonicalPeerKey(it?.sender);
      if (!key || (Number(it?.count || 0) || 0) <= 0) continue;
      activeKeys.add(key);
      if (!seen.has(key)) seen.set(key, now);
    }
    for (const key of Array.from(seen.keys())) {
      if (!activeKeys.has(key)) seen.delete(key);
    }
  } catch {}
}

function ecMissedPmSeenLongEnoughForAutoDrain(peer, minAgeMs = 750) {
  try {
    const key = ecMissedCanonicalPeerKey(peer);
    if (!key || !(UIState.missedPmFirstSeenAt instanceof Map)) return false;
    const firstSeen = Number(UIState.missedPmFirstSeenAt.get(key) || 0) || 0;
    return firstSeen > 0 && (Date.now() - firstSeen) >= Math.max(0, Number(minAgeMs || 0) || 0);
  } catch {
    return false;
  }
}



function ecResolveMissedPmTotals(items = null, providedTotal = null) {
  const totals = ecGetMissedPmTotals(items);
  const localTotal = Math.max(0, Number(totals.total || 0) || 0);
  const provided = Number(providedTotal);
  const hasProvided = providedTotal !== null && providedTotal !== undefined && Number.isFinite(provided);
  const safeProvided = hasProvided ? Math.max(0, provided) : 0;

  // beta.390: trust the browser's actual missed list over a stale/zero socket
  // total.  beta.389 could render a non-empty missed list and then immediately
  // hide the bubble because opts.total was 0.  The working beta.322 path counted
  // from UIState.missedPmSummary, so keep that behavior as the source of truth.
  const total = Math.max(localTotal, safeProvided);
  return { ...totals, total };
}

function ecMissedPmPopupSignature(items = []) {
  try {
    return (Array.isArray(items) ? items : [])
      .map((it) => `${ecMissedCanonicalPeerKey(it?.sender)}:${Number(it?.count || 0) || 0}:${Number(it?.last_ts || 0) || 0}`)
      .filter(Boolean)
      .join('|');
  } catch {
    return String(Date.now());
  }
}

function ecIsMissedPmOnlyActiveConversation(items = []) {
  try {
    const list = (Array.isArray(items) ? items : []).filter((it) => it && Number(it.count || 0) > 0);
    if (list.length !== 1) return false;
    const win = ecGetPmWindow(list[0].sender);
    return !!(win && typeof ecIsConversationWindowActive === 'function' && ecIsConversationWindowActive(win));
  } catch {
    return false;
  }
}

function ecPulseMissedPmAttention() {
  const btn = $('railMissedBtn');
  const flyout = $('dockAlertFlyout');
  [btn, flyout].forEach((el) => {
    if (!el) return;
    try {
      el.classList.remove('ecMissedPmAttention');
      // Force restart if another PM arrives while the previous pulse is active.
      void el.offsetWidth;
      el.classList.add('ecMissedPmAttention');
      setTimeout(() => { try { el.classList.remove('ecMissedPmAttention'); } catch {} }, 1800);
    } catch {}
  });
}

function ecForceMissedBubbleVisible(total = null, opts = {}) {
  const btn = $('railMissedBtn');
  const countEl = $('railMissedCount');
  const rail = $('dockAlertRail');
  let count = Number(total ?? 0) || 0;
  let threads = 0;
  try {
    const totals = (typeof ecResolveMissedPmTotals === 'function')
      ? ecResolveMissedPmTotals(null, total)
      : ((typeof ecGetMissedPmTotals === 'function') ? ecGetMissedPmTotals() : null);
    if (totals) {
      count = Number(totals.total ?? count ?? 0) || 0;
      threads = Number(totals.threads ?? 0) || 0;
    }
  } catch {}
  count = Math.max(0, count);

  ecMissedDebug("bubble.force.start", { requestedTotal: total, resolvedCount: count, threads, opts });

  if (countEl) {
    countEl.textContent = String(count);
    countEl.title = count > 0
      ? `${threads || 1} conversation${(threads || 1) === 1 ? '' : 's'}`
      : 'No missed private messages';
  }

  if (!btn) return count;

  if (count > 0) {
    btn.classList.add('hasUnread', 'isPeeked', 'isAlerting', 'ecForceVisible');
    btn.classList.remove('isCollapsed');
    btn.setAttribute('aria-hidden', 'false');
    btn.setAttribute('aria-label', `${count} missed private message${count === 1 ? '' : 's'}`);
    btn.title = `${count} missed private message${count === 1 ? '' : 's'}`;
    // Inline guard: keeps the bubble visible even if presentation CSS/classes are
    // stale during socket reconnect, mobile/desktop shell swaps, or rail refresh races.
    btn.style.opacity = '1';
    btn.style.visibility = 'visible';
    btn.style.pointerEvents = 'auto';
    if (rail) {
      rail.classList.add('hasPeekedBubble');
      rail.classList.remove('isCollapsed');
    }
    if (opts.pulse !== false) {
      try { ecPulseMissedPmAttention(); } catch {}
    }
    setTimeout(() => { try { ecRepairMissedBubblePaintPath(opts.reason || "force_visible"); } catch {} }, 80);
  } else {
    btn.classList.remove('hasUnread', 'isPeeked', 'isAlerting', 'ecForceVisible');
    btn.classList.add('isCollapsed');
    btn.setAttribute('aria-hidden', 'true');
    btn.setAttribute('aria-label', 'Missed messages');
    btn.title = 'Missed messages';
    btn.style.removeProperty('opacity');
    btn.style.removeProperty('visibility');
    btn.style.removeProperty('pointer-events');
    btn.classList.remove('ecViewportFallback');
  }

  ecMissedDebug("bubble.force.done", { count, visual: ecGetMissedBubbleVisualState() });
  return count;
}


function ecMaybePopupMissedPmSummary(items = null, opts = {}) {
  const totals = (typeof ecResolveMissedPmTotals === 'function')
    ? ecResolveMissedPmTotals(items, opts.total)
    : ecGetMissedPmTotals(items);
  const list = totals.items;
  const total = Math.max(0, Number(totals.total ?? 0) || 0);
  ecMissedDebug("popup.evaluate", { items, opts, totals, total, listLength: list.length });
  if (total <= 0 || !list.length) {
    EC_LAST_MISSED_PM_POPUP_TOTAL = 0;
    EC_LAST_MISSED_PM_POPUP_SIG = '';
    try { ecForceMissedBubbleVisible(null, { pulse: false }); } catch {}
    return;
  }

  // Keep the rail bubble visible independently from toast/flyout throttling.
  // The rest of this function decides whether to show a toast/open the flyout,
  // but the badge itself must never stay hidden while unread PMs exist.
  try { ecForceMissedBubbleVisible(total, { reason: opts.reason || 'missed_pm_summary' }); } catch {}

  const sig = ecMissedPmPopupSignature(list);
  const now = Date.now();
  const priorTotal = Number(EC_LAST_MISSED_PM_POPUP_TOTAL || 0) || 0;
  const priorSig = String(EC_LAST_MISSED_PM_POPUP_SIG || '');
  const increased = total > priorTotal;
  const changed = sig && sig !== priorSig;
  const firstArmed = !!MISSED_SUMMARY_TOAST_ARMED && total > 0;

  if (!(increased || firstArmed || (changed && now - EC_LAST_MISSED_PM_POPUP_AT > 30000))) {
    EC_LAST_MISSED_PM_POPUP_TOTAL = total;
    EC_LAST_MISSED_PM_POPUP_SIG = sig;
    return;
  }
  const forceActiveConversationPopup = !!(
    opts.forceActiveConversationPopup ||
    opts.forcePopupEvenIfActive ||
    opts.incomingPrivateMessage ||
    opts.reason === 'incoming_private_message'
  );
  if (!forceActiveConversationPopup && ecIsMissedPmOnlyActiveConversation(list)) {
    EC_LAST_MISSED_PM_POPUP_TOTAL = total;
    EC_LAST_MISSED_PM_POPUP_SIG = sig;
    return;
  }
  if (now - EC_LAST_MISSED_PM_POPUP_AT < 2500) {
    EC_LAST_MISSED_PM_POPUP_TOTAL = total;
    EC_LAST_MISSED_PM_POPUP_SIG = sig;
    return;
  }

  EC_LAST_MISSED_PM_POPUP_TOTAL = total;
  EC_LAST_MISSED_PM_POPUP_SIG = sig;
  EC_LAST_MISSED_PM_POPUP_AT = now;

  const top = list[0] || {};
  const sender = String(top.sender || '').trim();
  const threads = Number(totals.threads || list.length || 0) || 0;
  const message = sender && threads === 1
    ? `📨 New missed private message from ${sender}`
    : `📨 ${total} missed private message${total === 1 ? '' : 's'} across ${threads} chat${threads === 1 ? '' : 's'}`;

  try { ecPulseMissedPmAttention(); } catch {}

  // Visible in-app popup for missed PM count changes. beta.386 updated the rail
  // count but only showed a popup on the first socket login summary.
  if (UIState?.prefs?.missedToast !== false) {
    if (typeof toastAction === 'function') {
      toastAction(message, {
        kind: 'info',
        timeout: 10000,
        actionLabel: sender ? 'Open PM' : 'Open missed',
        dedupeKey: `missed-pm-popup:${sig}:${total}`,
        dedupeMs: 12000,
        onAction: () => {
          if (sender) openMissedPmFrom(sender);
          else if (typeof openDockRailPanel === 'function') openDockRailPanel('missed');
        },
      });
    } else if (typeof toast === 'function') {
      toast(message, 'info', 6000, { event: 'dm', dedupeKey: `missed-pm-popup:${sig}:${total}`, dedupeMs: 12000 });
    }
  }

  try {
    maybeBrowserNotify('Missed private messages', sender ? `${sender} sent you a private message.` : message, {
      dedupeKey: `missed-pm-browser:${sig}:${total}`,
      dedupeMs: 12000,
    });
  } catch {}

  // When Hui Chat is visible, also open the rail drawer so the user sees where
  // the missed PM is instead of only seeing a changing number.
  try {
    const focused = (typeof ecIsWindowActivelyFocused === 'function') ? ecIsWindowActivelyFocused() : (document.visibilityState === 'visible');
    if (focused && typeof openDockRailPanel === 'function') {
      setTimeout(() => {
        try {
          openDockRailPanel('missed');
          ecPulseMissedPmAttention();
        } catch {}
      }, 80);
    }
  } catch {}
}

function ecBumpLivePmUnread(peer, amount = 1, opts = {}) {
  const clean = (typeof ecPmPeerName === 'function') ? ecPmPeerName(peer) : String(peer || '').trim();
  const key = ecMissedCanonicalPeerKey(clean);
  if (!clean || !key) return;
  const map = ecEnsureLivePmUnreadMap();
  const existing = ecGetLivePmUnreadEntry(clean);
  const cur = Number(existing?.item?.count ?? existing?.item ?? 0) || 0;
  const ids = new Set(Array.isArray(existing?.item?.ids) ? existing.item.ids : []);
  const incomingIds = Array.isArray(opts.ids) ? opts.ids : (opts.id ? [opts.id] : []);
  const cleanIncomingIds = incomingIds.map((id) => Number(id || 0) || 0).filter((id) => id > 0);
  const alreadyCountedById = cleanIncomingIds.length > 0 && cleanIncomingIds.every((id) => ids.has(id));
  const delta = alreadyCountedById ? 0 : (Number(amount || 0) || 0);
  const next = Math.max(0, cur + delta);
  if (existing && existing.key !== clean) map.delete(existing.key);
  if (next > 0) {
    cleanIncomingIds.forEach((id) => ids.add(id));
    map.set(clean, {
      sender: clean,
      count: next,
      last_ts: (typeof opts.last_ts === 'number' ? opts.last_ts : Date.now()),
      server_backed: !!(opts.serverBacked || existing?.item?.server_backed),
      ids: Array.from(ids).slice(-500),
    });
  } else map.delete(clean);
  ecMissedDebug("live_unread.bump", { peer: clean, amount, opts, cur, delta, next, ids: Array.from(ids) });
  renderMissedPmList();
  try {
    ecMaybePopupMissedPmSummary(null, {
      reason: opts.reason || 'live_unread_bump',
      forceActiveConversationPopup: !!(opts.forceActiveConversationPopup || opts.forcePopupEvenIfActive || opts.incomingPrivateMessage),
      incomingPrivateMessage: !!opts.incomingPrivateMessage,
    });
  } catch {}
  try { ecUpdateAllOpenDmStatuses(); } catch {}
}

function ecClearLivePmUnread(peer) {
  const map = ecEnsureLivePmUnreadMap();
  const existing = ecGetLivePmUnreadEntry(peer);
  if (!existing) return false;
  map.delete(existing.key);
  renderMissedPmList();
  try { ecUpdateAllOpenDmStatuses(); } catch {}
  return true;
}

function ecClearAllLivePmUnread() {
  const map = ecEnsureLivePmUnreadMap();
  if (!map.size) return;
  map.clear();
  renderMissedPmList();
  try { ecUpdateAllOpenDmStatuses(); } catch {}
}

function ecPendingOfflineDmMapKeys(peer = null) {
  const map = UIState?.pendingOfflineDm;
  if (!map || typeof map.keys !== "function") return [];
  const all = Array.from(map.keys());
  if (!peer) return all;
  const matches = all.filter((name) => ecMissedSamePeer(name, peer));
  return matches.length ? matches : [String(peer || '').trim()].filter(Boolean);
}

function ecPendingOfflineDmStorageKey(peer) {
  const matches = ecPendingOfflineDmMapKeys(peer).filter((name) => UIState.pendingOfflineDm.has(name));
  if (matches.length) return matches[0];
  try {
    const clean = ecPmPeerName(peer);
    if (clean) return clean;
  } catch {}
  return String(peer || "").trim();
}

/**
 * Apply a local delta to the missed PM summary list and re-render immediately.
 * This keeps the UI responsive while we wait for the server to push the updated summary.
 */
function consumeMissedPmLocal(sender, consumedCount) {
  if (!sender) return;
  const n = Number(consumedCount || 0) || 0;
  if (n <= 0) return;

  const list = Array.isArray(UIState.missedPmSummary) ? UIState.missedPmSummary.slice() : [];
  let changed = false;

  const next = [];
  for (const it of list) {
    if (!it || !ecMissedSamePeer(it.sender, sender)) {
      next.push(it);
      continue;
    }
    const cur = Number(it.count ?? 0) || 0;
    const remaining = Math.max(0, cur - n);
    changed = true;
    if (remaining > 0) next.push({ ...it, count: remaining });
    // if remaining == 0, drop the entry
  }

  if (changed) {
    UIState.missedPmSummary = next;
    renderMissedPmList(next);
    try { ecUpdateAllOpenDmStatuses(); } catch {}
  }
}

function dropMissedEntryLocal(sender) {
  if (!sender) return;
  const list = Array.isArray(UIState.missedPmSummary) ? UIState.missedPmSummary : [];
  const next = list.filter((it) => it && !ecMissedSamePeer(it.sender, sender));
  if (next.length !== list.length) {
    UIState.missedPmSummary = next;
    renderMissedPmList(next);
    try { ecUpdateAllOpenDmStatuses(); } catch {}
  }
}

function removePendingRequestLocal(fromUser) {
  if (!fromUser) return;
  const list = Array.isArray(UIState.pendingRequests) ? UIState.pendingRequests : [];
  const wantedKey = ecNormalizeUsernameKey(fromUser);
  const next = list.filter((it) => ecNormalizeUsernameKey(it) !== wantedKey);
  if (next.length !== list.length) {
    UIState.pendingRequests = next;
    renderPendingFriendRequestsInto($("pendingRequestsList"), next);
    renderPendingFriendRequestsInto($("railPendingRequestsList"), next);
    updateDockSummaryCounts();
    try { if (rbHasUI()) rbRenderRoomLists(); } catch {}
  }
}

function closeDockRailPanelIfEmpty(panel = '') {
  const wanted = String(panel || '').trim();
  if (!wanted) return;
  const flyout = $('dockAlertFlyout');
  if (!flyout || flyout.classList.contains('hidden')) return;
  const active = String(document.querySelector('.dockAlertBubble.isActive')?.dataset?.railPanel || '');
  if (active !== wanted) return;
  const totals = getDockAlertActivityTotals();
  const remaining = wanted === 'missed'
    ? totals.missedTotal
    : (wanted === 'pending' ? totals.pendingTotal : totals.alertsTotal);
  if (Number(remaining || 0) <= 0) closeDockRailPanel();
}

function getMissedCountFor(sender) {
  const list = (typeof ecGetCombinedMissedPmItems === 'function') ? ecGetCombinedMissedPmItems() : (Array.isArray(UIState.missedPmSummary) ? UIState.missedPmSummary : []);
  let total = 0;
  for (const it of list) {
    if (it && ecMissedSamePeer(it.sender, sender)) total += Number(it.count ?? 0) || 0;
  }
  return total;
}

function ecPendingOfflineDmHasId(id) {
  const mid = Number(id || 0) || 0;
  if (mid <= 0) return false;
  try {
    for (const arr of UIState.pendingOfflineDm.values()) {
      if (!Array.isArray(arr)) continue;
      if (arr.some((m) => (Number(m?.id || 0) || 0) === mid)) return true;
    }
  } catch {}
  return false;
}

function queuePendingOfflineDm(peer, msg) {
  if (!peer || !msg) return false;
  const cipher = msg?.cipher;
  if (!cipher) return false;
  const id = Number(msg.id || 0) || 0;
  const mapKey = ecPendingOfflineDmStorageKey(peer);
  if (!mapKey) return false;
  const cur = UIState.pendingOfflineDm.get(mapKey) || [];

  // Avoid duplicate local ciphertext rows, but do not mark the server ID as
  // ACK-safe until the caller proves the backlog was actually persisted.
  if (id > 0 && cur.some((m) => (Number(m?.id || 0) || 0) === id)) {
    try { ecUpdateAllOpenDmStatuses(); } catch {}
    return true;
  }

  cur.push({
    id: id || null,
    cipher: String(cipher),
    ts: (typeof msg.ts === "number") ? msg.ts : null,
    needsAck: !!msg.needsAck
  });
  // Keep it bounded per peer to avoid runaway memory.
  UIState.pendingOfflineDm.set(mapKey, cur.slice(-200));
  try { ecUpdateAllOpenDmStatuses(); } catch {}
  return true;
}

async function ackOfflinePmIds(ids, { quiet = true } = {}) {
  const clean = Array.from(new Set((Array.isArray(ids) ? ids : [])
    .map((x) => Number(x) || 0)
    .filter((x) => x > 0)))
    .slice(0, 1000);
  if (!clean.length || !socket) return { success: true, updated: 0, requested: 0 };

  const res = await new Promise((resolve) => {
    try {
      socket.emit("ack_offline_pms", { ids: clean }, (r) => resolve((r && typeof r === 'object') ? r : null));
    } catch (e) {
      resolve({ success: false, error: String(e?.message || e || 'ack_failed') });
    }
  });

  if (!res || !res.success) {
    if (!quiet) toast(`⚠️ Could not acknowledge missed PMs: ${res?.error || 'server did not respond'}`, "warn", 3600);
    try { socket.emit("get_missed_pm_summary"); } catch {}
    return res || { success: false, error: 'no_response' };
  }

  try { socket.emit("get_missed_pm_summary"); } catch {}
  return { ...res, requested: Number(res.requested ?? clean.length) || clean.length };
}

function normalizeMissedPmSummaryItems(items) {
  const merged = new Map();
  for (const raw of Array.isArray(items) ? items : []) {
    const sender = String(raw?.sender || '').trim();
    const key = ecMissedCanonicalPeerKey(sender);
    const count = Math.max(0, Number(raw?.count ?? 0) || 0);
    if (!sender || !key || count <= 0) continue;
    const lastTs = (typeof raw?.last_ts === 'number' && Number.isFinite(raw.last_ts)) ? raw.last_ts : null;
    const prev = merged.get(key);
    if (!prev) {
      merged.set(key, { sender, count, last_ts: lastTs });
    } else {
      prev.count += count;
      if (lastTs !== null && (prev.last_ts === null || lastTs > prev.last_ts)) prev.last_ts = lastTs;
    }
  }
  const combined = Array.from(merged.values()).sort((a, b) => {
    const bt = (typeof b.last_ts === 'number') ? b.last_ts : 0;
    const at = (typeof a.last_ts === 'number') ? a.last_ts : 0;
    if (bt !== at) return bt - at;
    return String(a.sender).localeCompare(String(b.sender));
  });
  ecMissedDebug("combined.items", { itemsArg: Array.isArray(items) ? items : null, combined });
  return combined;
}

async function flushPendingOfflineDm(peer = null) {
  // Only attempt if we have a key.
  if (!window.myPrivateCryptoKey) return;
  const peers = peer ? ecPendingOfflineDmMapKeys(peer) : Array.from(UIState.pendingOfflineDm.keys());
  for (const p of peers) {
    const pending = UIState.pendingOfflineDm.get(p) || [];
    if (!pending.length) continue;

    const win = ecGetPmWindow(p);
    let processed = 0;
    const keep = [];
    const ackIds = [];
    for (const m of pending) {
      try {
        const cipher = m?.cipher;
        if (!cipher) continue;

        let plaintext;
        if (typeof cipher === "string" && cipher.startsWith(PM_PLAINTEXT_PREFIX) && DM_PLAINTEXT_COMPAT_ALLOWED) {
          plaintext = unwrapPlainDm(cipher);
        } else if (typeof cipher === "string" && cipher.startsWith(PM_ENVELOPE_PREFIX)) {
          plaintext = await decryptHybridEnvelope(window.myPrivateCryptoKey, cipher);
        } else {
          plaintext = await decryptLegacyRSA(window.myPrivateCryptoKey, cipher);
        }

        const payload = parseDmPayload(plaintext);
        const mid = Number(m?.id || 0) || 0;
        const rendered = win
          ? appendDmPayload(win, `${p}:`, payload, { peer: p, direction: "in", ts: m?.ts, messageId: mid })
          : true;

        if (rendered) {
          const histText = (payload.kind === "file")
            ? `📎 ${payload.name} (${humanBytes(payload.size)})`
            : (payload.kind === "torrent")
              ? `🧲 ${payload?.t?.name || payload?.t?.infohash || "Torrent"}`
              : payload.text;

          addPmHistory(p, "in", histText, m?.ts);
          processed += 1;
        }
        if (mid > 0) {
          UIState.pendingOfflineDmSeen.add(mid);
          ackIds.push(mid);
        }
      } catch (e) {
        keep.push(m);
      }
    }

    if (keep.length) UIState.pendingOfflineDm.set(p, keep);
    else UIState.pendingOfflineDm.delete(p);

    // Persist backlog changes so refresh won't lose ciphertext that is still locked.
    try { persistOfflineDmBacklog(); } catch {}

    if (ackIds.length) {
      const ack = await ackOfflinePmIds(ackIds, { quiet: true });
      if (ack?.success) {
        consumeMissedPmLocal(p, Number(ack.requested ?? ackIds.length) || ackIds.length);
        closeDockRailPanelIfEmpty('missed');
      }
    }

    if (processed) toast(`🔓 Decrypted ${processed} pending PM(s) from ${p}`, "ok", 2200);
    try { ecUpdateAllOpenDmStatuses(); } catch {}
  }
}

async function consumeOfflinePmsForPeer(peer, { promptUnlock = false, quiet = false } = {}) {
  const targetPeer = (typeof ecPmPeerName === "function") ? ecPmPeerName(peer) : String(peer || '').trim();
  if (!targetPeer) return;
  if (!socket) return;
  const consumeKey = ecMissedCanonicalPeerKey(targetPeer) || targetPeer;

  const existing = UIState.consumingOfflinePeerPromises.get(consumeKey);
  if (existing) {
    try {
      await existing;
      if (promptUnlock && !window.myPrivateCryptoKey) {
        try { await ensurePrivateKeyUnlocked(); } catch {}
      }
      if (window.myPrivateCryptoKey) {
        try { await flushPendingOfflineDm(targetPeer); } catch {}
      }
    } finally {
      try { socket.emit("get_missed_pm_summary"); } catch {}
    }
    return;
  }

  UIState.consumingOfflinePeers.add(consumeKey);
  try { ecUpdateAllOpenDmStatuses(); } catch {}

  const job = (async () => {
    // Fetch with peek=true, then explicitly ACK only after each ciphertext is
    // either rendered or safely copied into the local encrypted backlog.
    const res = await new Promise((resolve) => {
      try {
        socket.emit("fetch_offline_pms", { from_user: targetPeer, peek: true }, (r) => resolve(r));
      } catch (e) {
        resolve(null);
      }
    });

    if (!res || !res.success) {
      if (!quiet) toast(`❌ ${res?.error || "Failed to fetch offline PMs"}`, "error");
      try { socket.emit("get_missed_pm_summary"); } catch {}
      return;
    }

    const msgs = Array.isArray(res.messages) ? res.messages : [];
    if (!msgs.length) {
      // Server may already have cleared the summary; still re-sync.
      try { socket.emit("get_missed_pm_summary"); } catch {}
      return;
    }

    // Ensure DM window exists, but do not trigger a second background consume.
    const win = ecGetPmWindow(targetPeer) || openPrivateChat(targetPeer, { consumeOffline: false });
    if (win) ensureDmHistoryRendered(win, targetPeer);

    let privKey = window.myPrivateCryptoKey;
    if (!privKey && promptUnlock) {
      try { privKey = await ensurePrivateKeyUnlocked(); } catch { privKey = null; }
    }

    let processed = 0;
    let queued = 0;
    const ackIds = [];
    const queuedAckIds = [];

    for (const m of msgs) {
      const cipher = m?.cipher;
      const msgId = m?.id;
      const ts = (typeof m?.ts === "number") ? m.ts : null;
      if (!cipher || !msgId) continue;

      // Prevent duplicate processing if the server delivers the same IDs again.
      const mid = Number(msgId) || 0;
      if (mid > 0 && UIState.pendingOfflineDmSeen.has(mid)) {
        ackIds.push(mid);
        continue;
      }

      try {
        let plaintext;

        if (typeof cipher === "string" && cipher.startsWith(PM_PLAINTEXT_PREFIX) && DM_PLAINTEXT_COMPAT_ALLOWED) {
          plaintext = unwrapPlainDm(cipher);
        } else {
          if (!privKey) throw new Error("dm_locked");
          if (typeof cipher === "string" && cipher.startsWith(PM_ENVELOPE_PREFIX)) {
            plaintext = await decryptHybridEnvelope(privKey, cipher);
          } else {
            plaintext = await decryptLegacyRSA(privKey, cipher);
          }
        }

        const payload = parseDmPayload(plaintext);
        const rendered = win
          ? appendDmPayload(win, `${targetPeer}:`, payload, { peer: targetPeer, direction: "in", ts, messageId: mid })
          : true;

        if (rendered) {
          const histText = (payload.kind === "file")
            ? `📎 ${payload.name} (${humanBytes(payload.size)})`
            : (payload.kind === "torrent")
              ? `🧲 ${payload?.t?.name || payload?.t?.infohash || "Torrent"}`
              : payload.text;

          addPmHistory(targetPeer, "in", histText, ts);
          processed += 1;
        }
        if (mid > 0) {
          UIState.pendingOfflineDmSeen.add(mid);
          ackIds.push(mid);
        }
      } catch (e) {
        const queuedLocally = queuePendingOfflineDm(targetPeer, { id: msgId, cipher, ts, needsAck: true });
        if (queuedLocally) {
          queued += 1;
          if (mid > 0) queuedAckIds.push(mid);
          if (win && !win._ym?.__offlineSavedHintShown) {
            win._ym.__offlineSavedHintShown = true;
            appendLine(win, "System:", "Missed message saved locally until private messages unlock. Use Load missed in the status strip to retry.", "system");
            try { ecUpdateDmStatus(win, targetPeer); } catch {}
          }
        } else if (win) {
          appendLine(win, "System:", "Missed message could not be saved locally, so it was left on the server for retry.", "system");
          try { ecUpdateDmStatus(win, targetPeer); } catch {}
        }
      }
    }

    // Persist ciphertext backlog so refresh won't lose queued items.
    const backlogPersisted = persistOfflineDmBacklog();

    if (backlogPersisted && queuedAckIds.length) {
      queuedAckIds.forEach((mid) => UIState.pendingOfflineDmSeen.add(mid));
      ackIds.push(...queuedAckIds);
    }

    if (ackIds.length) {
      const ack = await ackOfflinePmIds(ackIds, { quiet });
      if (ack?.success) {
        consumeMissedPmLocal(targetPeer, Number(ack.requested ?? ackIds.length) || ackIds.length);
        closeDockRailPanelIfEmpty('missed');
      }
    }

    // If we have a key now, decrypt anything that was queued.
    if (window.myPrivateCryptoKey) {
      try { await flushPendingOfflineDm(targetPeer); } catch {}
    }

    if (!quiet) {
      if (processed) toast(`📥 Loaded ${processed} missed PM(s) from ${targetPeer}`, "ok");
      if (queued) {
        const note = backlogPersisted
          ? `${queued} missed PM(s) saved locally until private messages unlock.`
          : `${queued} missed PM(s) kept on the server because local browser storage was unavailable.`;
        toast(note, backlogPersisted ? "info" : "warn", 4200);
      }
    }
  })();

  UIState.consumingOfflinePeerPromises.set(consumeKey, job);

  try {
    await job;
  } finally {
    UIState.consumingOfflinePeers.delete(consumeKey);
    UIState.consumingOfflinePeerPromises.delete(consumeKey);
    try { ecUpdateAllOpenDmStatuses(); } catch {}
    // Always re-sync; server is source of truth.
    try { socket.emit("get_missed_pm_summary"); } catch {}
  }
}

function renderMissedPmListInto(ul, items) {
  if (!ul) return;
  ecClearNode(ul);

  const list = Array.isArray(items) ? items : [];
  if (!list.length) {
    ul.appendChild(ecListStatusItem({ name: 'empty', dot: 'offline', avatar: '✉', text: 'No missed messages' }));
    return;
  }

  for (const it of list) {
    const sender = it?.sender;
    const count = Number(it?.count ?? 0) || 0;
    if (!sender || count <= 0) continue;

    const p = UIState.presence.get(sender);
    const online = (p && typeof p === 'object') ? !!p.online : !!p;
    const presence = (p && typeof p === 'object') ? (p.presence || (online ? 'online' : 'offline')) : (online ? 'online' : 'offline');

    const li = document.createElement('li');
    li.dataset.name = sender;
    li.dataset.search = `${sender} missed ${count} ${presence}`;
    li.classList.add('isInteractive');

    const left = document.createElement('div');
    left.className = 'liLeft';
    const dotState = online ? ((presence === 'busy') ? 'busy' : ((presence === 'away') ? 'away' : 'online')) : 'offline';
    createDockIdentity(left, {
      name: sender,
      presenceClass: dotState,
      meta: `${count} unread message${count === 1 ? '' : 's'}`
    });

    const badge = document.createElement('span');
    badge.className = 'liBadge';
    badge.textContent = String(count);

    const actions = document.createElement('div');
    actions.className = 'liActions';
    const openBtn = document.createElement('button');
    openBtn.className = 'iconBtn';
    openBtn.title = 'Open messages';
    openBtn.textContent = '💬';
    openBtn.onclick = (ev) => { ev.stopPropagation(); openMissedPmFrom(sender); };
    actions.appendChild(openBtn);

    li.appendChild(left);
    li.appendChild(badge);
    li.appendChild(actions);

    li.onclick = () => {
      selectBuddyRow(sender, 'missed', li);
      openMissedPmFrom(sender);
    };
    li.ondblclick = () => openMissedPmFrom(sender);
    li.oncontextmenu = (ev) => {
      selectBuddyRow(sender, 'missed', li);
      showUserContextMenu(ev, sender, { source: 'missed' });
    };

    ul.appendChild(li);
  }
}

function renderMissedPmList(items = null) {
  const combined = (typeof ecGetCombinedMissedPmItems === 'function') ? ecGetCombinedMissedPmItems(items) : (Array.isArray(items) ? items : (Array.isArray(UIState.missedPmSummary) ? UIState.missedPmSummary : []));
  try { ecRememberMissedPmFirstSeen(combined); } catch {}
  ecMissedDebug("render.list.start", { itemsArg: items, combined });
  renderMissedPmListInto($('missedPmList'), combined);
  renderMissedPmListInto($('railMissedPmList'), combined);
  updateDockSummaryCounts();
  try { ecForceMissedBubbleVisible(ecResolveMissedPmTotals(combined).total, { pulse: false, reason: 'render_list' }); } catch {}
  try { if (rbHasUI()) rbRenderRoomLists(); } catch {}
  try { ecUpdateAllOpenDmStatuses(); } catch {}
  ecMissedDebug("render.list.done", { combined, visual: ecGetMissedBubbleVisualState() });
}

function ecNormalizeSocialName(name) {
  return String(name || '').trim().toLowerCase();
}

function ecBlockedAlertCleanupMatcher(peer = '') {
  const explicitPeer = ecNormalizeSocialName(peer);
  const blockedKeys = new Set();
  try {
    if (UIState.blockedSet instanceof Set) {
      UIState.blockedSet.forEach((name) => {
        const key = ecNormalizeSocialName(name);
        if (key) blockedKeys.add(key);
      });
    }
  } catch (_) {}
  return (candidate) => {
    const key = ecNormalizeSocialName(candidate);
    if (!key) return false;
    return (!!explicitPeer && key === explicitPeer) || blockedKeys.has(key);
  };
}

function cleanupBlockedPairAlerts(peer = '', opts = {}) {
  const isBlockedPeer = ecBlockedAlertCleanupMatcher(peer);
  let changedMissed = false;
  let changedPending = false;
  let changedGroups = false;
  let changedRooms = false;

  const missed = Array.isArray(UIState.missedPmSummary) ? UIState.missedPmSummary : [];
  const nextMissed = missed.filter((it) => !isBlockedPeer(it?.sender));
  changedMissed = nextMissed.length !== missed.length;
  if (changedMissed) {
    UIState.missedPmSummary = nextMissed;
    renderMissedPmList(nextMissed);
    closeDockRailPanelIfEmpty('missed');
  }

  try {
    const peers = Array.from(UIState.pendingOfflineDm?.keys?.() || []);
    peers.forEach((name) => {
      if (isBlockedPeer(name)) clearOfflineDmBacklog(name);
    });
  } catch (_) {}

  const pending = Array.isArray(UIState.pendingRequests) ? UIState.pendingRequests : [];
  const nextPending = pending.filter((name) => !isBlockedPeer(name));
  changedPending = nextPending.length !== pending.length;
  if (changedPending) {
    UIState.pendingRequests = nextPending;
    renderPendingFriendRequestsInto($('pendingRequestsList'), nextPending);
    renderPendingFriendRequestsInto($('railPendingRequestsList'), nextPending);
    closeDockRailPanelIfEmpty('pending');
  }

  const groupInvites = Array.isArray(UIState.groupInvites) ? UIState.groupInvites : [];
  const nextGroupInvites = groupInvites.filter((inv) => !isBlockedPeer(inv?.from_user || inv?.fromUser || inv?.by));
  changedGroups = nextGroupInvites.length !== groupInvites.length;
  if (changedGroups) UIState.groupInvites = nextGroupInvites;

  const roomInvites = Array.isArray(UIState.roomInvites) ? UIState.roomInvites : [];
  const nextRoomInvites = roomInvites.filter((inv) => !isBlockedPeer(inv?.by || inv?.from_user || inv?.invited_by));
  changedRooms = nextRoomInvites.length !== roomInvites.length;
  if (changedRooms) UIState.roomInvites = nextRoomInvites;

  if (changedGroups || changedRooms) {
    try { renderGroupInviteListInto($('groupInviteList'), UIState.groupInvites); } catch (_) {}
    try { renderAlertsInviteListInto($('railAlertsList'), UIState.groupInvites, UIState.roomInvites, { openRail: true }); } catch (_) {}
    closeDockRailPanelIfEmpty('alerts');
  }

  if (changedMissed || changedPending || changedGroups || changedRooms) {
    updateDockSummaryCounts();
    try { if (rbHasUI()) rbRenderRoomLists(); } catch (_) {}
  }

  if (opts.refresh !== false) {
    try { socket.emit('get_missed_pm_summary'); } catch (_) {}
    try { refreshGroupInvites(); } catch (_) {}
    try { refreshRoomInvites(); } catch (_) {}
    try { refreshCustomRoomInvites(); } catch (_) {}
  }
}

async function openMissedPmFrom(sender) {
  if (!sender) return;

  // User explicitly clicked a missed entry: open the DM and then load/ACK missed PMs.
  // We do NOT optimistically clear the UI until the consume/ACK actually happens.
  closeDockRailPanel();

  const targetPeer = (typeof ecPmPeerName === "function") ? ecPmPeerName(sender) : String(sender || '').trim();
  try { ecClearLivePmUnread(targetPeer); } catch {}
  openPrivateChat(targetPeer, { consumeOffline: false, clearLiveUnread: true });
  await consumeOfflinePmsForPeer(targetPeer, { promptUnlock: true, quiet: false });
  try { ecUpdateAllOpenDmStatuses(); } catch {}
}

// Server can push updated friends list at any time (e.g., friend accepted).
socket.on("friends_list", (friends) => {
  try {
    if (Array.isArray(friends)) {
      updateFriendsListUI(friends);
      try { socket.emit("get_friend_presence"); } catch (_) {}
    }
  } catch (e) {}
});

function renderPendingFriendRequestsInto(ul, requests) {
  if (!ul) return;
  ecClearNode(ul);

  const cleanRequests = ecCanonicalUsernameList(Array.isArray(requests) ? requests : [], { excludeSelf: true, excludeBlocked: true, excludeFriends: true });
  if (!cleanRequests.length) {
    ul.appendChild(ecListStatusItem({ name: 'none', dot: 'offline', avatar: '?', text: 'None' }));
    return;
  }

  cleanRequests.forEach(from_user => {
    const li = document.createElement("li");
    li.dataset.name = from_user;
    li.dataset.search = `${from_user} request friend invite`;
    li.title = `Friend request from ${from_user}`;
    li.setAttribute('aria-label', `Friend request from ${from_user}`);
    li.classList.add('isInteractive', 'pendingRequestItem');

    const left = document.createElement("div");
    left.className = "liLeft";
    createDockIdentity(left, {
      name: from_user,
      presenceClass: 'offline',
      meta: `Friend request from ${from_user}`,
      chip: 'New'
    });

    const actions = document.createElement("div");
    actions.className = "liActions";

    const yes = document.createElement("button");
    yes.className = "iconBtn";
    yes.textContent = "✅";
    yes.title = "Accept";
    yes.onclick = (ev) => {
      ev.stopPropagation();
      const actionKey = ecNormalizeUsernameKey(from_user);
      if (EC_PENDING_FRIEND_ACTIONS.has(`accept:${actionKey}`)) return;
      EC_PENDING_FRIEND_ACTIONS.add(`accept:${actionKey}`);
      yes.disabled = true;
      no.disabled = true;
      removePendingRequestLocal(from_user);
      closeDockRailPanelIfEmpty('pending');
      socket.emit("accept_friend_request", { from_user }, (res) => {
        if (res?.success) {
          toast("✅ Friend request accepted", "ok");
          closeDockRailPanelIfEmpty('pending');
        } else {
          toast(`❌ ${res?.error || 'Could not accept request'}`, "error");
        }
        EC_PENDING_FRIEND_ACTIONS.delete(`accept:${actionKey}`);
        getPendingFriendRequests();
        getFriends();
      });
    };

    const no = document.createElement("button");
    no.className = "iconBtn";
    no.textContent = "✖";
    no.title = "Reject";
    no.onclick = (ev) => {
      ev.stopPropagation();
      const actionKey = ecNormalizeUsernameKey(from_user);
      if (EC_PENDING_FRIEND_ACTIONS.has(`reject:${actionKey}`)) return;
      EC_PENDING_FRIEND_ACTIONS.add(`reject:${actionKey}`);
      yes.disabled = true;
      no.disabled = true;
      removePendingRequestLocal(from_user);
      closeDockRailPanelIfEmpty('pending');
      socket.emit("reject_friend_request", { from_user }, (res) => {
        if (res?.success) {
          toast("Rejected", "warn");
          closeDockRailPanelIfEmpty('pending');
        } else {
          toast(`❌ ${res?.error || 'Could not reject request'}`, "error");
        }
        EC_PENDING_FRIEND_ACTIONS.delete(`reject:${actionKey}`);
        getPendingFriendRequests();
      });
    };

    actions.appendChild(yes);
    actions.appendChild(no);

    li.appendChild(left);
    li.appendChild(actions);
    li.onclick = () => {
      selectBuddyRow(from_user, 'pending', li);
      openProfileWindow(from_user);
    };
    li.oncontextmenu = (ev) => {
      selectBuddyRow(from_user, 'pending', li);
      showUserContextMenu(ev, from_user, { source: 'pending' });
    };
    ul.appendChild(li);
  });
}

socket.on("pending_friend_requests", (requests) => {
  UIState.pendingRequests = ecCanonicalUsernameList(Array.isArray(requests) ? requests : [], { excludeSelf: true, excludeBlocked: true, excludeFriends: true });
  renderPendingFriendRequestsInto($("pendingRequestsList"), UIState.pendingRequests);
  renderPendingFriendRequestsInto($("railPendingRequestsList"), UIState.pendingRequests);
  updateDockSummaryCounts();
  try { if (rbHasUI()) rbRenderRoomLists(); } catch {}
});

socket.on("blocked_users_list", (users) => {
  const canonicalBlockedUsers = ecCanonicalUsernameList(Array.isArray(users) ? users : [], { excludeSelf: true });
  UIState.blockedUsersCache = canonicalBlockedUsers.slice();
  try { UIState.blockedSet = new Set(canonicalBlockedUsers); } catch { UIState.blockedSet = new Set(); }
  try {
    if (typeof ecPruneRoomMessagesFromBlockedUser === 'function') {
      canonicalBlockedUsers.forEach((u) => ecPruneRoomMessagesFromBlockedUser(u));
    }
  } catch {}
  try {
    if (typeof ecRefreshCurrentRoomAfterBlockStateChange === 'function') {
      ecRefreshCurrentRoomAfterBlockStateChange('', 'blocked_users_list');
    }
  } catch {}
  cleanupBlockedPairAlerts('', { refresh: false });
  const blockedCountEl = $("blockedUsersCount");
  if (blockedCountEl) blockedCountEl.textContent = String(canonicalBlockedUsers.length);
  const ul = $("blockedUsersList");
  if (!ul) return;
  ecClearNode(ul);

  if (!canonicalBlockedUsers.length) {
    UIState.blockedSet = new Set();
    ul.appendChild(ecListStatusItem({ name: 'none', dot: 'offline', avatar: '-', text: 'None' }));
    updateDockSummaryCounts();
    try { if (rbHasUI()) rbRenderRoomLists(); } catch {}
    return;
  }

  canonicalBlockedUsers.forEach(u => {
    const li = document.createElement("li");
    li.dataset.name = u;
    li.dataset.search = `${u} blocked`;

    const left = document.createElement("div");
    left.className = "liLeft";
    createDockIdentity(left, {
      name: u,
      presenceClass: 'busy',
      meta: 'Blocked contact',
      chip: 'Blocked'
    });

    const actions = document.createElement("div");
    actions.className = "liActions";

    const unblock = document.createElement("button");
    unblock.className = "iconBtn";
    unblock.textContent = "↩";
    unblock.title = "Unblock";
    unblock.onclick = (ev) => {
      ev.stopPropagation();
      socket.emit("unblock_user", { blocked: u }, (res) => {
        const canonicalBlocked = String(res?.blocked || u).trim() || u;
        const localOk = !!res?.success || /not\s*blocked/i.test(String(res?.error || ''));
        toast(res?.success ? `Unblocked ${canonicalBlocked}` : localOk ? `${canonicalBlocked} is already unblocked` : `❌ ${res?.error || 'Unblock failed'}`, localOk ? "ok" : "error");
        if (localOk) {
          if (typeof ecRemoveLocalBlockedUserEverywhere === 'function') ecRemoveLocalBlockedUserEverywhere(canonicalBlocked);
          if (typeof ecRefreshCurrentRoomAfterBlockStateChange === 'function') ecRefreshCurrentRoomAfterBlockStateChange(canonicalBlocked, 'unblock');
        }
        getFriends();
        getPendingFriendRequests();
        getBlockedUsers();
      });
    };

    actions.appendChild(unblock);

    li.appendChild(left);
    li.appendChild(actions);
    li.onclick = () => selectBuddyRow(u, 'blocked', li);
    li.oncontextmenu = (ev) => {
      selectBuddyRow(u, 'blocked', li);
      showUserContextMenu(ev, u, { source: 'blocked' });
    };
    ul.appendChild(li);
  });

  updateDockSummaryCounts();
});

socket.on("social_alert_cleanup", (payload = {}) => {
  const peer = String(payload?.peer || payload?.username || '').trim();
  const reason = String(payload?.reason || '').trim().toLowerCase();
  if (peer && reason.includes('unblock')) {
    try { UIState.roomUnblockRefreshUntil = Date.now() + 4500; } catch {}
    try { if (typeof ecRemoveLocalBlockedUserEverywhere === 'function') ecRemoveLocalBlockedUserEverywhere(peer); } catch {}
    try { if (typeof ecRefreshCurrentRoomAfterBlockStateChange === 'function') ecRefreshCurrentRoomAfterBlockStateChange(peer, 'unblock'); } catch {}
  }
  cleanupBlockedPairAlerts(peer, { refresh: true });
});

// Presence updates (server addition; falls back gracefully if not present)
socket.on("friends_presence", (payload) => {
  if (!payload || !Array.isArray(payload)) return;
  UIState.presence.clear();
  payload.forEach((row) => {
    if (!row) return;
    if (typeof row === "string") {
      ecSetPresenceForUsername(row, { online: false, presence: "offline", custom_status: "", last_seen: null });
      return;
    }
    if (!row.username) return;
    ecSetPresenceForUsername(row.username, row);
  });
  // Refresh the rendered dock from cache; do not ask the server again for every presence burst.
  scheduleFriendsListRenderRefresh('friends_presence');
});

socket.on("friend_presence_update", (p) => {
  if (!p || !p.username) return;
  ecSetPresenceForUsername(p.username, p);
  scheduleFriendsListRenderRefresh('friend_presence_update');
});

socket.on('my_profile', (p) => {
  if (!p || typeof p !== 'object') return;
  UIState.myProfile = p;
  renderMyHubIdentity(p);
  try { window.ecRefreshMessageAvatarsForUsername?.(p.username || currentUser); } catch {}
});

socket.on("my_presence", (p) => {
  if (!p) return;
  const sel = $("meStatus");
  if (sel && p.presence) {
    sel.value = p.presence;
    try {
      const autoAwayEcho = !!window.__ec_autoAwayActive && p.presence === "away";
      const autoOfflineEcho = !!window.__ec_autoOfflineActive && p.presence === "invisible";
      if (!autoAwayEcho && !autoOfflineEcho) {
        window.__ym_lastPresence = p.presence;
        window.__ec_manualPresence = p.presence;
      }
      if (p.presence !== "away") {
        window.__ec_autoAwayActive = false;
      }
      if (p.presence !== "invisible") {
        window.__ec_autoOfflineActive = false;
      }
    } catch (_) {}
  }
  try {
    window.__ym_lastCustomStatus = (p.custom_status || "");
    const disp = $("meCustomDisplay");
    if (disp) {
      const t = (p.custom_status || "").trim();
      disp.textContent = t ? `“${t}”` : "";
      disp.style.display = t ? "block" : "none";
    }
  } catch (_) {}
});

// ───────────────────────────────────────────────────────────────────────────────

// ───────────────────────────────────────────────────────────────────────────────
// Embedded room pane (left side)
// ───────────────────────────────────────────────────────────────────────────────
function getRoomEmbedEl() {
  const el = $("roomEmbed");
  if (!el) return null;
  if (!el._ym) {
    el._ym = {
      titleEl: $("roomEmbedTitle"),
      log: $("roomEmbedLog"),
      input: $("roomEmbedInput"),
      emojiBtn: $("roomEmbedEmojiBtn"),
      send: $("roomEmbedSend"),
      torrentBtn: $("roomEmbedTorrentBtn"),
      gifBtn: $("roomEmbedGifBtn"),
      formatControls: [$("roomEmbedFontFamily"), $("roomEmbedFontSize"), $("roomEmbedBoldBtn"), $("roomEmbedItalicBtn"), $("roomEmbedUnderlineBtn"), $("roomEmbedTextColor")].filter(Boolean),
      voiceBtn: $("btnRoomEmbedVoice"),
      webcamBtn: $("btnRoomEmbedCam"),
      torrentInput: $("roomEmbedTorrentInput"),
      mediaRail: $("roomEmbedMediaRail"),
      mediaTitle: $("roomEmbedMediaTitle"),
      mediaMeta: $("roomEmbedMediaMeta"),
      mediaStations: $("roomEmbedMediaStations"),
      mediaFrame: $("roomEmbedMediaFrame"),
      mediaPlayerBtn: $("btnRoomEmbedMediaPlayer"),
      mediaOpenBtn: $("btnRoomEmbedMediaOpen"),
      mediaHideBtn: $("btnRoomEmbedMediaHide"),
      mediaSkipBtn: $("btnRoomEmbedMediaSkip"),
      mediaVoteStatus: $("roomEmbedMediaVoteStatus"),
      mediaMuteBtn: $("btnRoomEmbedMediaMute"),
      mediaDuckChk: $("chkRoomEmbedMediaDuck"),
      mediaVolume: $("roomEmbedMediaVolume"),
      mediaVolumeLabel: $("roomEmbedMediaVolumeLabel"),
      mediaVolumeHint: $("roomEmbedMediaVolumeHint")
    };
    disableOutputContextMenu(el._ym.log);
  }
  return el;
}

function rbPopoutElements() {
  return {
    root: $('roomBrowserPopout'),
    body: $('roomBrowserPopoutBody'),
    closeBtn: $('btnRoomBrowserPopoutClose'),
    toggleBtn: $('btnRoomBrowserPopout'),
    placeholder: $('sitePlaceholder'),
    slot: $('sitePlaceholderSlot'),
    siteArea: $('siteArea'),
    roomEmbed: $('roomEmbed'),
  };
}

function rbSyncHomeSlotState() {
  const { slot } = rbPopoutElements();
  if (!slot) return;
  const shouldHideSlot = !!UIState.roomEmbedRoom;
  slot.classList.toggle('hidden', shouldHideSlot);
}

function rbSyncOverlayState() {
  const { root, placeholder, siteArea, roomEmbed } = rbPopoutElements();
  const overlayOpen = !!ROOM_BROWSER.popoutOpen && !!UIState.roomEmbedRoom;
  if (siteArea) siteArea.classList.toggle('room-browser-overlay-open', overlayOpen);
  if (roomEmbed) roomEmbed.classList.toggle('is-underlay', overlayOpen);
  if (root) root.classList.toggle('is-room-overlay', overlayOpen);
  if (placeholder) placeholder.classList.toggle('is-room-overlay', overlayOpen);
  rbSyncHomeSlotState();
}

function rbRestorePlaceholderHome() {
  const { placeholder, slot } = rbPopoutElements();
  if (!placeholder || !slot) return;
  if (placeholder.parentElement !== slot) slot.appendChild(placeholder);
  placeholder.classList.remove('is-popout');
  placeholder.classList.remove('is-room-overlay');
  rbSyncHomeSlotState();
}


function rbRoomBrowserOverlayIsOpen() {
  try {
    return !!(ROOM_BROWSER && ROOM_BROWSER.popoutOpen && UIState && UIState.roomEmbedRoom);
  } catch (e) {
    return false;
  }
}

function rbClosePopoutAfterRoomChoice() {
  if (!rbRoomBrowserOverlayIsOpen()) return;
  try { rbClosePopout({ keepHidden: true }); } catch (e) {}
}

function rbClosePopout(opts = {}) {
  const keepHidden = !!opts.keepHidden;
  if (opts.resetSearches !== false) {
    try { resetRoomBrowserSearchBarsAfterClose(); } catch (e) {}
  }
  const { root, placeholder, toggleBtn } = rbPopoutElements();
  ROOM_BROWSER.popoutOpen = false;
  if (root) root.classList.add('hidden');
  rbRestorePlaceholderHome();
  if (placeholder) {
    if (keepHidden && UIState.roomEmbedRoom) placeholder.classList.add('hidden');
    else placeholder.classList.remove('hidden');
  }
  if (toggleBtn) {
    toggleBtn.classList.remove('active');
    toggleBtn.setAttribute('aria-expanded', 'false');
    toggleBtn.textContent = 'Rooms';
  }
  rbSyncOverlayState();
}

function rbOpenPopout() {
  const { root, body, placeholder, toggleBtn } = rbPopoutElements();
  if (!root || !body || !placeholder || !UIState.roomEmbedRoom) return;
  ROOM_BROWSER.popoutOpen = true;
  if (placeholder.parentElement !== body) body.appendChild(placeholder);
  placeholder.classList.remove('hidden');
  placeholder.classList.add('is-popout');
  root.classList.remove('hidden');
  if (toggleBtn) {
    toggleBtn.classList.add('active');
    toggleBtn.setAttribute('aria-expanded', 'true');
    toggleBtn.textContent = 'Hide rooms';
  }
  rbSyncOverlayState();
}

function rbTogglePopout(force) {
  if (!UIState.roomEmbedRoom) return;
  const wantsOpen = typeof force === 'boolean' ? force : !ROOM_BROWSER.popoutOpen;
  if (wantsOpen) rbOpenPopout();
  else rbClosePopout({ keepHidden: true });
}

function bindRoomBrowserPopoutUi() {
  const { root, closeBtn, toggleBtn } = rbPopoutElements();
  if (toggleBtn && !toggleBtn.dataset.boundRoomBrowserPopout) {
    toggleBtn.dataset.boundRoomBrowserPopout = '1';
    toggleBtn.setAttribute('aria-haspopup', 'dialog');
    toggleBtn.setAttribute('aria-expanded', 'false');
    toggleBtn.addEventListener('click', () => rbTogglePopout());
  }
  if (closeBtn && !closeBtn.dataset.boundRoomBrowserPopout) {
    closeBtn.dataset.boundRoomBrowserPopout = '1';
    closeBtn.addEventListener('click', () => rbClosePopout({ keepHidden: true }));
  }
  if (root && !root.dataset.boundRoomBrowserPopout) {
    root.dataset.boundRoomBrowserPopout = '1';
    root.addEventListener('mousedown', (ev) => {
      if (ev.target === root) rbClosePopout({ keepHidden: true });
    });
  }
  if (!window.__rbPopoutEscBound) {
    window.__rbPopoutEscBound = true;
    window.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && ROOM_BROWSER.popoutOpen) rbClosePopout({ keepHidden: true });
    });
  }
}

function showRoomEmbed(room) {
  const pane = getRoomEmbedEl();
  const ph = $("sitePlaceholder");
  const slot = $("sitePlaceholderSlot");
  if (!pane) return null;

  bindRoomBrowserPopoutUi();
  const previousRoom = String(UIState.roomEmbedRoom || '').trim();
  const nextRoom = String(room || '').trim();
  if (previousRoom && previousRoom !== nextRoom && typeof roomMediaStopLocalPlayback === 'function') {
    try { roomMediaStopLocalPlayback(previousRoom, { hideRail: true, heartbeat: true }); } catch {}
  }
  UIState.roomEmbedRoom = room || null;

  if (room) {
    if (!ROOM_BROWSER.popoutOpen) ph?.classList.add("hidden");
    else ph?.classList.remove('hidden');
    slot?.classList.add('hidden');
    pane.classList.remove("hidden");
    if (pane._ym?.titleEl) pane._ym.titleEl.textContent = `Room — ${room}`;
  } else {
    rbClosePopout({ keepHidden: false });
    pane.classList.add("hidden");
    ph?.classList.remove("hidden");
    slot?.classList.remove('hidden');
    if (pane._ym?.titleEl) pane._ym.titleEl.textContent = "Room —";
  }

  rbSyncOverlayState();
  return pane;
}
