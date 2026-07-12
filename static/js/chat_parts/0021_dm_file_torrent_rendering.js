function parseDmPayload(plaintext, depth = 0) {
  if (depth > 5) return { kind: "text", text: (typeof plaintext === "string") ? plaintext : String(plaintext ?? "") };

  if (plaintext && typeof plaintext === "object" && !Array.isArray(plaintext)) {
    const file = ecNormalizeDmFilePayload(plaintext);
    if (file) return file;
    const torrent = ecNormalizeTorrentWireObject(plaintext);
    if (torrent) return { kind: "torrent", t: torrent };
    return { kind: "text", text: String(plaintext?.text ?? plaintext?.message ?? "") };
  }

  if (typeof plaintext !== "string") return { kind: "text", text: String(plaintext) };
  const text = plaintext.trimStart();

  // DM special payloads are encrypted JSON objects. Support the older `_ec`
  // wire shape, the newer `kind/type` shape used by group/file helpers, and
  // styled-text-wrapped special payloads created while formatting was enabled.
  const obj = ecTryParseWireJsonObject(text);
  if (obj) {
    const ec = String(obj._ec || obj.kind || obj.type || "").trim().toLowerCase();
    if (ec === "styled_text") {
      const nested = obj.text ?? obj.message ?? obj.value ?? obj.html;
      if (nested !== undefined && nested !== null) return parseDmPayload(String(nested), depth + 1);
    }

    const file = ecNormalizeDmFilePayload(obj);
    if (file) return file;

    const torrent = ecNormalizeTorrentWireObject(obj);
    if (torrent) return { kind: "torrent", t: torrent };
  }

  // If a user pastes a magnet link as plain text, render it as a torrent card.
  const torrent = ecTryNormalizeTorrentMessage(text, depth + 1);
  if (torrent) return { kind: "torrent", t: torrent };

  return { kind: "text", text: plaintext };
}

function ecTryParseWireJsonObject(value) {
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

function ecNormalizeTorrentWireObject(obj) {
  if (!obj || typeof obj !== "object") return null;
  const ec = String(obj._ec || obj.kind || obj.type || "").trim().toLowerCase();
  const looksTorrent = ec === "torrent" || ec === "magnet" || !!(obj.magnet || obj.infohash || obj.infohash_hex || obj.torrent_id || obj.download_url);
  if (!looksTorrent) return null;
  return {
    name: String(obj.name || obj.display_name || obj.file_name || "Torrent"),
    infohash: String(obj.infohash || obj.infohash_hex || ""),
    magnet: String(obj.magnet || ""),
    total_size: Number(obj.total_size || obj.size || 0) || 0,
    seeds: (obj.seeds === null || obj.seeds === undefined) ? null : Number(obj.seeds),
    leechers: (obj.leechers === null || obj.leechers === undefined) ? null : Number(obj.leechers),
    completed: (obj.completed === null || obj.completed === undefined) ? null : Number(obj.completed),
    trackers: Array.isArray(obj.trackers) ? obj.trackers.map(String) : [],
    declared_tracker_count: Number(obj.declared_tracker_count ?? (obj.using_public_fallback_trackers ? 0 : (Array.isArray(obj.trackers) ? obj.trackers.length : 0)) ?? 0) || 0,
    tracker_count: Number(obj.tracker_count || (Array.isArray(obj.trackers) ? obj.trackers.length : 0) || 0),
    tracker_source: obj.tracker_source ? String(obj.tracker_source) : (obj.using_public_fallback_trackers ? "public_fallback" : "torrent"),
    using_public_fallback_trackers: !!obj.using_public_fallback_trackers,
    swarm_deferred: !!obj.swarm_deferred,
    web_seeds: Array.isArray(obj.web_seeds) ? obj.web_seeds.map(String) : [],
    web_seed_count: Number(obj.web_seed_count || (Array.isArray(obj.web_seeds) ? obj.web_seeds.length : 0) || 0),
    scrape_status: obj.scrape_status ? String(obj.scrape_status) : (obj.swarm_status ? String(obj.swarm_status) : ""),
    scrape_error: obj.scrape_error ? String(obj.scrape_error) : "",
    trackers_tried: Number(obj.trackers_tried || 0),
    dht_queries: Number(obj.dht_queries || 0),
    dht_peers_seen: Number(obj.dht_peers_seen || 0),
    comment: obj.comment ? String(obj.comment) : "",
    created_by: obj.created_by ? String(obj.created_by) : "",
    creation_date: obj.creation_date ? String(obj.creation_date) : "",
    torrent_id: obj.torrent_id ? String(obj.torrent_id) : "",
    file_name: obj.file_name ? String(obj.file_name) : "",
    download_url: obj.download_url ? String(obj.download_url) : "",
    file_id: typeof obj.file_id === "string" ? obj.file_id : null,
  };
}

function ecNormalizeDmFilePayload(obj) {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
  const ec = String(obj._ec || obj.kind || obj.type || "").trim().toLowerCase();
  const fileId = (typeof obj.file_id === "string" && obj.file_id)
    ? obj.file_id
    : ((ec === "file" && typeof obj.id === "string") ? obj.id : "");
  const looksFile = ec === "file" || (!!fileId && !!(obj.name || obj.file_name || obj.mime || obj.size || obj.sha256));
  if (!looksFile || !fileId) return null;

  const out = {
    kind: "file",
    file_id: String(fileId),
    name: String(obj.name || obj.file_name || obj.filename || "file"),
    size: Number(obj.size || obj.total_size || 0) || 0,
    mime: String(obj.mime || obj.content_type || "application/octet-stream"),
    sha256: obj.sha256 ? String(obj.sha256) : null,
  };
  if (obj.source) out.source = String(obj.source);
  if (obj.transfer_id) out.transfer_id = String(obj.transfer_id);
  if (obj.group_id !== undefined && obj.group_id !== null && obj.group_id !== "") out.group_id = Number(obj.group_id);
  return out;
}

function ecTryNormalizeTorrentMessage(message, depth = 0) {
  if (depth > 5) return null;
  const obj = ecTryParseWireJsonObject(message);
  if (obj) {
    const ec = String(obj._ec || obj.kind || obj.type || "").trim().toLowerCase();
    if (ec === "styled_text") {
      // Backward compatibility for messages sent while a formatting toggle was
      // active: old composers could wrap torrent JSON inside styled_text.
      const nestedText = obj.text ?? obj.message ?? obj.value ?? obj.html;
      const nested = ecTryNormalizeTorrentMessage(nestedText, depth + 1);
      if (nested) return nested;
    }
    const t = ecNormalizeTorrentWireObject(obj);
    if (t) return t;
  }
  if (typeof message === "string") {
    const text = message.trim();
    try {
      if (typeof isMagnetText === "function" && isMagnetText(text) && typeof parseMagnet === "function") {
        const pm = parseMagnet(text);
        if (pm) {
          return {
            name: pm.name || "Magnet",
            infohash: pm.infohash,
            magnet: pm.magnet,
            total_size: 0,
            seeds: null,
            leechers: null,
            completed: null,
            trackers: pm.trackers || [],
            declared_tracker_count: Number(pm.declared_tracker_count || 0),
            tracker_count: Number(pm.tracker_count || (pm.trackers || []).length || 0),
            tracker_source: pm.tracker_source || (pm.using_public_fallback_trackers ? "public_fallback" : "magnet"),
            using_public_fallback_trackers: !!pm.using_public_fallback_trackers,
            swarm_deferred: true,
            web_seeds: Array.isArray(pm.web_seeds) ? pm.web_seeds : [],
            web_seed_count: Number(pm.web_seed_count || 0),
            scrape_status: "pending",
            scrape_error: "",
            trackers_tried: 0,
            comment: "",
            created_by: "",
            creation_date: "",
            download_url: "",
            file_id: null,
          };
        }
      }
    } catch {}
  }
  return null;
}

function ecBuildSpecialMessageBody(message) {
  const torrent = ecTryNormalizeTorrentMessage(message);
  if (torrent) return buildTorrentCard(torrent);
  return null;
}

function ecDmWireHash(value) {
  const text = String(value ?? "");
  if (!text) return "";
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}

function ecDmPayloadSummary(payload) {
  if (!payload || typeof payload !== "object") return String(payload ?? "").slice(0, 240);
  if (payload.kind === "file") return `file:${payload.file_id || ""}:${payload.name || ""}:${payload.size || 0}`;
  if (payload.kind === "torrent") {
    const t = payload.t || payload;
    return `torrent:${t.infohash || ""}:${t.magnet || ""}:${t.name || ""}`;
  }
  return `text:${String(payload.text ?? "").slice(0, 240)}`;
}

function ecDmPayloadDedupeKey(payload, { peer, direction, ts, messageId, fingerprint, cipher } = {}) {
  const id = Number(messageId || 0) || 0;
  const peerKey = (typeof ecPmPeerKey === "function") ? ecPmPeerKey(peer) : String(peer || "").trim().toLowerCase();
  const dir = String(direction || "").trim().toLowerCase() || "unknown";
  if (id > 0) return `dm:id:${id}`;
  const fp = String(fingerprint || (cipher ? ecDmWireHash(cipher) : "")).trim();
  if (!fp) return "";
  const tsKey = (ts === null || ts === undefined || ts === "") ? "no-ts" : String(ts);
  return `dm:${peerKey}:${dir}:${tsKey}:${fp}:${ecDmWireHash(ecDmPayloadSummary(payload))}`;
}

function ecRememberDmPayloadRendered(winEl, key) {
  if (!winEl || !key) return true;
  if (!winEl._ym) winEl._ym = {};
  if (!(winEl._ym.dmRenderedKeys instanceof Set)) winEl._ym.dmRenderedKeys = new Set();
  if (winEl._ym.dmRenderedKeys.has(key)) return false;
  winEl._ym.dmRenderedKeys.add(key);
  // Keep the set bounded for long-running tabs.
  if (winEl._ym.dmRenderedKeys.size > 600) {
    try {
      const keys = Array.from(winEl._ym.dmRenderedKeys);
      winEl._ym.dmRenderedKeys = new Set(keys.slice(-400));
    } catch {}
  }
  return true;
}

function appendDmPayload(winEl, who, payload, { peer, direction, ts, messageId = null, fingerprint = "", cipher = "" } = {}) {
  if (!payload || !winEl) return false;

  const dedupeKey = ecDmPayloadDedupeKey(payload, { peer, direction, ts, messageId, fingerprint, cipher });
  if (dedupeKey && !ecRememberDmPayloadRendered(winEl, dedupeKey)) return false;

  if (payload.kind === "file") {
    return appendFileLine(winEl, who, payload, { peer, direction, ts });
  } else if (payload.kind === "torrent") {
    return appendTorrentLine(winEl, who, payload.t, { peer, direction, ts });
  } else {
    appendLine(winEl, who, payload.text, { ts, context: "dm" });
    return true;
  }
}

function buildFileCardElement(filePayload, { peer, direction } = {}) {
  const card = document.createElement("span");
  card.className = "ym-fileCard";

  const icon = document.createElement("span");
  icon.textContent = "📎";

  const name = document.createElement("span");
  name.className = "ym-fileName";
  name.textContent = filePayload?.name || "file";

  const meta = document.createElement("span");
  meta.className = "ym-fileMeta";
  meta.textContent = humanBytes(filePayload?.size || 0);

  const badge = document.createElement("span");
  badge.className = "ym-fileBadge";
  const src = filePayload?.source || (filePayload?.transfer_id ? "p2p" : "server");
  badge.textContent = (src === "p2p") ? "P2P" : "SRV";

  let actionEl = null;
  if (filePayload?.blob instanceof Blob) {
    const dl = document.createElement("button");
    dl.type = "button";
    dl.className = "ym-fileDl";
    dl.textContent = "Download";
    dl.onclick = () => downloadBlob(filePayload.name || "file", filePayload.blob);
    actionEl = dl;
  } else if (typeof filePayload?.file_id === "string" && filePayload.file_id) {
    const dl = document.createElement("button");
    dl.type = "button";
    dl.className = "ym-fileDl";
    dl.textContent = "Download";
    dl.onclick = async () => {
      try {
        if (filePayload && filePayload.group_id) {
          await downloadAndDecryptGroupFile(filePayload.file_id, filePayload.name, filePayload.group_id);
        } else {
          await downloadAndDecryptDmFile(filePayload.file_id, filePayload.name);
        }
      } catch (e) {
        console.error(e);
        toast("❌ File download failed", "error");
      }
    };
    actionEl = dl;
  } else {
    const st = document.createElement("span");
    st.className = "ym-fileMeta";
    st.textContent = (direction === "out") ? "Sent" : "";
    actionEl = st;
  }

  card.appendChild(icon);
  card.appendChild(name);
  card.appendChild(meta);
  card.appendChild(badge);
  if (actionEl) card.appendChild(actionEl);
  return card;
}

function makeFileLineElement(who, filePayload, { peer, direction } = {}) {
  return buildFileCardElement(filePayload, { peer, direction });
}

function appendFileLine(winEl, who, filePayload, { peer, direction, ts } = {}) {
  const log = winEl._ym?.log;
  if (!log) return false;
  const card = buildFileCardElement(filePayload, { peer, direction });
  appendGenericMessageItem(log, who, card, { ts, kind: "file", context: "dm" });
  scheduleScrollLogToBottom(log);
  return true;
}

function isTorrentName(name) {
  return typeof name === "string" && /\.torrent$/i.test(name.trim());
}

function _shortHash(h) {
  if (!h) return "";
  const s = String(h);
  return s.length > 12 ? (s.slice(0, 6) + "…" + s.slice(-6)) : s;
}

function _torrentSafeDownloadName(raw) {
  const safe = String(raw || "download.torrent")
    .replace(/[\\/\r\n\t\x00-\x1f]+/g, "_")
    .replace(/^\.+$/, "download.torrent")
    .slice(0, 180)
    .trim();
  return safe || "download.torrent";
}

async function _copyTorrentText(value, label) {
  const text = String(value || "");
  if (!text) return false;
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    await navigator.clipboard.writeText(text);
    return true;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "readonly");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand && document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  if (!ok) throw new Error(`Could not copy ${label || "text"}`);
  return true;
}

function _torrentScrapeOn() {
  if (typeof isTorrentScrapeEnabled === "function") return isTorrentScrapeEnabled();
  const raw = window.HUI_CFG && window.HUI_CFG.torrent_scrape_enabled;
  if (typeof raw === "boolean") return raw;
  const text = String(raw ?? "").trim().toLowerCase();
  if (["1", "true", "yes", "on", "enabled"].includes(text)) return true;
  if (["0", "false", "no", "off", "disabled", ""].includes(text)) return false;
  return false;
}

function _torrentDhtOn() {
  if (typeof torrentDhtScrapeEnabled === "function") return torrentDhtScrapeEnabled();
  const raw = window.HUI_CFG && window.HUI_CFG.torrent_dht_scrape_enabled;
  if (typeof raw === "boolean") return raw;
  const text = String(raw ?? "").trim().toLowerCase();
  if (["1", "true", "yes", "on", "enabled"].includes(text)) return true;
  if (["0", "false", "no", "off", "disabled", ""].includes(text)) return false;
  return true;
}

function _torrentScrapeOffReason() {
  if (typeof torrentScrapeDisabledReason === "function") return torrentScrapeDisabledReason();
  return String((window.HUI_CFG && window.HUI_CFG.torrent_scrape_disabled_reason) || "Server setting torrent_scrape_enabled=false.");
}

async function _enableTorrentScrapeFromCard() {
  if (!window.IS_ADMIN) throw new Error("Only admins can enable tracker scraping.");
  const ok = window.confirm ? window.confirm("Enable tracker scraping now? This lets the server contact tracker URLs to show seeds/leechers/completed.") : true;
  if (!ok) return false;
  const resp = await fetchWithAuth("/admin/settings/general", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ torrent_scrape_enabled: true })
  });
  const data = (typeof ecReadApiJson === "function") ? await ecReadApiJson(resp, null) : await resp.json().catch(() => null);
  if (!resp || !resp.ok || !data?.ok) {
    const msg = (typeof ecApiErrorMessage === "function") ? ecApiErrorMessage(resp, data, "Could not enable tracker scraping") : (data?.error || "Could not enable tracker scraping");
    throw new Error(msg);
  }
  if (typeof setTorrentScrapeEnabledForClient === "function") setTorrentScrapeEnabledForClient(true);
  else {
    window.HUI_CFG = window.HUI_CFG || {};
    window.HUI_CFG.torrent_scrape_enabled = true;
    window.HUI_CFG.torrent_scrape_disabled_reason = "";
  }
  return true;
}

function buildTorrentCard(t) {
  const card = document.createElement("span");
  card.className = "ym-torrentCard";

  const icon = document.createElement("span");
  icon.textContent = "🧲";

  const main = document.createElement("span");
  main.className = "ym-torrentMain";

  const title = document.createElement("div");
  title.className = "ym-torrentTitle";
  const torrentName = document.createElement("span");
  torrentName.className = "ym-torrentName";
  torrentName.textContent = String(t?.name || "Torrent");
  title.appendChild(torrentName);

  const meta = document.createElement("div");
  meta.className = "ym-torrentMeta";

  const swarmStatus = document.createElement("div");
  swarmStatus.className = "ym-torrentSwarmStatus";

  const swarmLoading = document.createElement("div");
  swarmLoading.className = "ym-torrentSwarmLoading hidden";
  swarmLoading.setAttribute("aria-live", "polite");
  const swarmSpinner = document.createElement("span");
  swarmSpinner.className = "ym-torrentSpinner";
  swarmSpinner.setAttribute("aria-hidden", "true");
  const swarmLoadingText = document.createElement("span");
  swarmLoadingText.className = "ym-torrentSwarmLoadingText";
  swarmLoadingText.textContent = "Checking swarm…";
  swarmLoading.appendChild(swarmSpinner);
  swarmLoading.appendChild(swarmLoadingText);

  const swarmDetails = document.createElement("div");
  swarmDetails.className = "ym-torrentSwarmDetails";

  const normStat = (value) => {
    const n = Number(value);
    return (value === null || value === undefined || Number.isNaN(n)) ? "?" : String(Math.max(0, n));
  };
  const rawTrackerList = Array.isArray(t?.trackers) ? t.trackers.filter(Boolean) : [];
  const defaultPublicTrackers = (typeof configuredPublicTorrentTrackers === "function")
    ? configuredPublicTorrentTrackers()
    : (Array.isArray(window.DEFAULT_PUBLIC_TRACKERS || DEFAULT_PUBLIC_TRACKERS) ? (window.DEFAULT_PUBLIC_TRACKERS || DEFAULT_PUBLIC_TRACKERS).slice() : []);
  const fallbackTrackerList = (!rawTrackerList.length && t?.infohash && defaultPublicTrackers.length)
    ? defaultPublicTrackers.slice()
    : [];
  const defaultPublicTrackerSet = new Set(defaultPublicTrackers.map((tr) => String(tr || "").trim()).filter(Boolean));
  const rawTrackersArePublicFallback = defaultPublicTrackerSet.size > 0 && rawTrackerList.length > 0 && rawTrackerList.every((tr) => defaultPublicTrackerSet.has(String(tr || "").trim()));
  const effectiveTrackerList = rawTrackerList.length ? rawTrackerList : fallbackTrackerList;
  const usingFallbackTrackers = defaultPublicTrackerSet.size > 0 && !!(t?.using_public_fallback_trackers || t?.tracker_source === "public_fallback" || rawTrackersArePublicFallback || (!rawTrackerList.length && fallbackTrackerList.length));
  const declaredTrackerCountFromPayload = Number(t?.declared_tracker_count ?? (usingFallbackTrackers ? 0 : rawTrackerList.length) ?? 0) || 0;
  const trackerCountFromPayload = Number(t?.tracker_count || effectiveTrackerList.length || 0);
  const webSeedCountFromPayload = Number(t?.web_seed_count || (Array.isArray(t?.web_seeds) ? t.web_seeds.length : 0) || 0);
  const setSwarmLoading = (active, label = "Checking swarm…") => {
    swarmLoading.classList.toggle("hidden", !active);
    swarmLoadingText.textContent = label;
  };

  const hasSwarmNumbers = (sw = {}) => [sw?.seeds, sw?.leechers, sw?.completed]
    .some((v) => v !== null && v !== undefined && !Number.isNaN(Number(v)));
  const rememberSwarmNumbers = (sw = {}) => {
    if (sw.seeds !== null && sw.seeds !== undefined && !Number.isNaN(Number(sw.seeds))) t.seeds = Number(sw.seeds);
    if (sw.leechers !== null && sw.leechers !== undefined && !Number.isNaN(Number(sw.leechers))) t.leechers = Number(sw.leechers);
    if (sw.completed !== null && sw.completed !== undefined && !Number.isNaN(Number(sw.completed))) t.completed = Number(sw.completed);
    if (sw.status !== undefined) t.scrape_status = String(sw.status || "");
    if (sw.error !== undefined) t.scrape_error = String(sw.error || "");
    if (sw.trackers_tried !== undefined) t.trackers_tried = Number(sw.trackers_tried || 0);
  };

  const renderSwarmMeta = (sw = {}, note = "", opts = {}) => {
    rememberSwarmNumbers(sw || {});
    const sizeText = t?.total_size ? humanBytes(Number(t.total_size) || 0) : "—";
    const seeds = normStat(sw.seeds ?? t?.seeds);
    const leech = normStat(sw.leechers ?? t?.leechers);
    const done = normStat(sw.completed ?? t?.completed);
    const trackerCount = Number(sw.tracker_count ?? trackerCountFromPayload ?? 0) || 0;
    const webSeedCount = Number(sw.web_seed_count ?? webSeedCountFromPayload ?? 0) || 0;
    const status = String(sw.status || t?.scrape_status || t?.swarm_status || "").trim();
    const statusText = `${status} ${note || ""}`.toLowerCase();
    const autoLoading = /\b(pending|refreshing|checking|dht_lookup|dht lookup|looking up|lookup running)\b/.test(statusText);
    const loading = opts.loading === undefined ? autoLoading : !!opts.loading;
    const loadingLabel = opts.loadingLabel || (statusText.includes("dht") ? "Checking DHT peers…" : "Checking swarm…");
    setSwarmLoading(loading, loadingLabel);
    card.classList.toggle("ym-torrentCard--loading", loading);
    meta.textContent = `Size ${sizeText} • Seeds ${seeds} • Leechers ${leech} • Completed ${done}`;
    const detailParts = [`Trackers ${trackerCount}`];
    if (usingFallbackTrackers) detailParts.push(`Fallback public trackers ${trackerCount}`);
    if (declaredTrackerCountFromPayload === 0 && usingFallbackTrackers) detailParts.push("Declared trackers 0");
    if (webSeedCount > 0) detailParts.push(`Web seeds ${webSeedCount}`);
    if (sw.trackers_tried !== undefined || t?.trackers_tried) detailParts.push(`Tried ${Number(sw.trackers_tried ?? t.trackers_tried ?? 0) || 0}`);
    if (status) detailParts.push(`Status ${status}`);
    swarmDetails.textContent = detailParts.join(" • ");
    swarmDetails.classList.toggle("hidden", detailParts.length === 0);
    if (note) {
      swarmStatus.textContent = note;
      swarmStatus.classList.remove("hidden");
    } else {
      swarmStatus.textContent = "";
      swarmStatus.classList.add("hidden");
    }
  };

  const trList = effectiveTrackerList;
  const missingSwarm = [t?.seeds, t?.leechers, t?.completed].some((v) => v === null || v === undefined || Number.isNaN(Number(v)));
  const initialStatus = t?.scrape_error || t?.scrape_status || t?.swarm_status || "";
  const swarmWasDeferred = !!t?.swarm_deferred || String(initialStatus || "").toLowerCase() === "pending";
  const fallbackNote = usingFallbackTrackers
    ? "No trackers were declared, so Hui Chat is using public fallback trackers and DHT to look up seeds/leechers."
    : "";
  const initialSwarmState = { tracker_count: trList.length, web_seed_count: webSeedCountFromPayload, tracker_source: usingFallbackTrackers ? "public_fallback" : (t?.tracker_source || "torrent") };
  const dhtOnlyLookup = !trList.length && _torrentDhtOn();
  const scrapeAllowed = _torrentScrapeOn() || usingFallbackTrackers || dhtOnlyLookup;
  renderSwarmMeta(initialSwarmState, missingSwarm ? (fallbackNote || (scrapeAllowed ? (swarmWasDeferred ? "Posted. Looking up seeds/leechers…" : (initialStatus || "Looking up swarm stats…")) : _torrentScrapeOffReason())) : (initialStatus || ""));

  const refreshSwarm = async ({ manual = false, auto = false, attempt = 1 } = {}) => {
    if (!t?.infohash) {
      if (manual) toast("⚠️ No infohash available for this torrent", "warn");
      renderSwarmMeta({}, "No infohash available for swarm lookup.");
      return { error: "No infohash available" };
    }
    if (!trList.length) {
      renderSwarmMeta({ tracker_count: 0, web_seed_count: webSeedCountFromPayload, status: "dht_lookup" }, "Looking up seeds/leechers through DHT…", { loading: true, loadingLabel: "Checking DHT peers…" });
    }
    if (!_torrentScrapeOn() && !usingFallbackTrackers && !dhtOnlyLookup) {
      const disabled = { tracker_count: trList.length, web_seed_count: webSeedCountFromPayload, status: "disabled", error: _torrentScrapeOffReason() };
      renderSwarmMeta(disabled, _torrentScrapeOffReason());
      if (manual) toast("⚠️ Torrent swarm lookup is disabled by the server", "warn");
      return disabled;
    }
    const label = auto && attempt > 1 ? `Checking swarm… retry ${attempt}` : "Checking swarm…";
    renderSwarmMeta({ tracker_count: trList.length, web_seed_count: webSeedCountFromPayload, status: "refreshing" }, auto ? "Checking seeds/leechers…" : "Refreshing swarm stats…", { loading: true, loadingLabel: label });
    const sw = await fetchTorrentSwarm(String(t.infohash || ""), trList, {
      force_refresh: !!manual || !!auto,
      manual: !!manual,
      auto: !!auto,
      attempt
    });
    if (!sw || sw.disabled) {
      const failed = sw || { seeds: null, leechers: null, completed: null, error: _torrentScrapeOffReason(), status: "disabled" };
      renderSwarmMeta(failed, failed.error || _torrentScrapeOffReason(), { loading: false });
      return failed;
    }
    sw.web_seed_count = sw.web_seed_count ?? webSeedCountFromPayload;
    if (sw.error && !hasSwarmNumbers(sw)) {
      renderSwarmMeta(
        sw,
        auto ? "Still checking swarm stats…" : `Swarm lookup failed: ${sw.error}`,
        { loading: !!auto, loadingLabel: auto ? "Checking swarm…" : undefined }
      );
      return sw;
    }
    const tried = sw.trackers_tried ? ` (${sw.trackers_tried} tracker${sw.trackers_tried === 1 ? "" : "s"} tried)` : "";
    renderSwarmMeta(sw, sw.cached ? "Swarm stats from cache. Press Refresh swarm to bypass cache." : `Swarm stats refreshed${tried}.`, { loading: false });
    return sw;
  };

  const scheduleDeferredSwarmRefresh = () => {
    if (!(missingSwarm && t?.infohash && (trList.length || usingFallbackTrackers || dhtOnlyLookup) && (_torrentScrapeOn() || usingFallbackTrackers || dhtOnlyLookup))) return;
    // The first lookup used to fire immediately while the room card was still
    // being inserted. Slow trackers/DHT could return empty once, then the user
    // had to press Refresh manually. Retry a few times in the background so the
    // card pops up first and fills in shortly after, using the same code path as
    // the working Refresh button.
    const delays = swarmWasDeferred ? [700, 3000, 6500, 11000, 17000] : [700, 4000];
    let attempt = 0;
    const runAttempt = async () => {
      attempt += 1;
      let sw = null;
      try {
        sw = await refreshSwarm({ auto: true, attempt });
      } catch (err) {
        sw = { seeds: null, leechers: null, completed: null, status: "error", error: err && err.message ? err.message : "Swarm lookup failed" };
        renderSwarmMeta(sw, attempt < delays.length ? "Still checking swarm stats…" : "Swarm lookup failed.", { loading: attempt < delays.length });
      }
      if (hasSwarmNumbers(sw || {})) return;
      if (attempt < delays.length) {
        setTimeout(runAttempt, delays[attempt]);
      } else if (sw && sw.error) {
        renderSwarmMeta(sw, `Swarm lookup failed: ${sw.error}`, { loading: false });
      } else {
        renderSwarmMeta(sw || {}, "Swarm lookup did not return seeds/leechers yet. Try Refresh swarm.", { loading: false });
      }
    };
    setTimeout(runAttempt, delays[0]);
  };

  scheduleDeferredSwarmRefresh();

  const hash = document.createElement("div");
  hash.className = "ym-torrentHash";
  hash.textContent = `Infohash: ${_shortHash(t?.infohash || "")}`;

  const actions = document.createElement("div");
  actions.className = "ym-torrentActions";

  const btnCopyMagnet = document.createElement("button");
  btnCopyMagnet.className = "ym-fileDl";
  btnCopyMagnet.textContent = "Copy magnet";
  btnCopyMagnet.onclick = async () => {
    const m = t?.magnet || "";
    if (!m) return toast("⚠️ No magnet available", "warn");
    try {
      await _copyTorrentText(m, "magnet");
      toast("📋 Magnet copied", "ok");
    } catch {
      toast("❌ Could not copy", "error");
    }
  };

  const btnCopyHash = document.createElement("button");
  btnCopyHash.className = "ym-fileDl";
  btnCopyHash.textContent = "Copy hash";
  btnCopyHash.onclick = async () => {
    const h = t?.infohash || "";
    if (!h) return toast("⚠️ No infohash", "warn");
    try {
      await _copyTorrentText(h, "infohash");
      toast("📋 Hash copied", "ok");
    } catch {
      toast("❌ Could not copy", "error");
    }
  };

  actions.appendChild(btnCopyMagnet);
  actions.appendChild(btnCopyHash);

  const btnRefreshSwarm = document.createElement("button");
  btnRefreshSwarm.className = "ym-fileDl";
  btnRefreshSwarm.textContent = "Refresh swarm";
  btnRefreshSwarm.title = (_torrentScrapeOn() || usingFallbackTrackers || dhtOnlyLookup) ? "Refresh seeds/leechers/completed from trackers/DHT" : _torrentScrapeOffReason();
  btnRefreshSwarm.disabled = !(_torrentScrapeOn() || usingFallbackTrackers || dhtOnlyLookup);
  btnRefreshSwarm.onclick = () => refreshSwarm({ manual: true }).catch(() => renderSwarmMeta({}, "Swarm lookup failed."));
  actions.appendChild(btnRefreshSwarm);

  if (window.IS_ADMIN && trList.length && !_torrentScrapeOn() && !usingFallbackTrackers) {
    const btnEnableSwarm = document.createElement("button");
    btnEnableSwarm.className = "ym-fileDl";
    btnEnableSwarm.textContent = "Enable scrape";
    btnEnableSwarm.title = "Admin shortcut: enable torrent_scrape_enabled so this room can show seeds/leechers.";
    btnEnableSwarm.onclick = async () => {
      try {
        btnEnableSwarm.disabled = true;
        btnEnableSwarm.textContent = "Enabling…";
        const enabled = await _enableTorrentScrapeFromCard();
        if (!enabled) {
          btnEnableSwarm.disabled = false;
          btnEnableSwarm.textContent = "Enable scrape";
          return;
        }
        btnRefreshSwarm.disabled = false;
        btnRefreshSwarm.title = "Refresh seeds/leechers/completed from trackers";
        btnEnableSwarm.remove();
        toast("✅ Tracker scraping enabled. Refreshing swarm stats…", "ok");
        await refreshSwarm({ manual: true });
      } catch (err) {
        btnEnableSwarm.disabled = false;
        btnEnableSwarm.textContent = "Enable scrape";
        const msg = err && err.message ? err.message : "Could not enable tracker scraping";
        renderSwarmMeta({}, msg);
        toast(`❌ ${msg}`, "error");
      }
    };
    actions.appendChild(btnEnableSwarm);
  }

  // Optional server-stored torrent download (rooms)
  const safeDownloadUrl = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(t?.download_url || '', { allowRelative: true, allowExternal: false })
    : String(t?.download_url || '').trim();
  if (safeDownloadUrl) {
    const btnDl = document.createElement("button");
    btnDl.className = "ym-fileDl";
    btnDl.textContent = "Download .torrent";
    btnDl.onclick = () => {
      try {
        const a = document.createElement("a");
        a.href = safeDownloadUrl;
        a.download = _torrentSafeDownloadName(t?.file_name || "download.torrent");
        document.body.appendChild(a);
        a.click();
        a.remove();
      } catch {
        if (typeof ecOpenSafeUrl === 'function') ecOpenSafeUrl(safeDownloadUrl, { allowRelative: true, allowExternal: false });
        else window.open(safeDownloadUrl, "_blank");
      }
    };
    actions.appendChild(btnDl);
  }

  main.appendChild(title);
  main.appendChild(meta);
  main.appendChild(swarmLoading);
  main.appendChild(swarmDetails);
  main.appendChild(swarmStatus);
  if (t?.infohash) main.appendChild(hash);
  if (trList.length) {
    const tr = document.createElement("div");
    tr.className = "ym-torrentTrackers";
    tr.textContent = usingFallbackTrackers ? `${trList.length} public fallback tracker(s)` : `${trList.length} tracker(s)`;
    main.appendChild(tr);
  }
  const madeBits = [];
  if (t?.created_by) madeBits.push(`created by ${String(t.created_by).slice(0, 80)}`);
  if (t?.creation_date) {
    try { madeBits.push(new Date(t.creation_date).toLocaleString()); } catch { madeBits.push(String(t.creation_date)); }
  }
  if (madeBits.length) {
    const made = document.createElement("div");
    made.className = "ym-torrentCreated";
    made.textContent = madeBits.join(" • ");
    main.appendChild(made);
  }
  if (t?.comment) {
    const c = document.createElement("div");
    c.className = "ym-torrentComment";
    c.textContent = t.comment;
    main.appendChild(c);
  }

  card.appendChild(icon);
  card.appendChild(main);
  card.appendChild(actions);
  return card;
}

function appendTorrentLine(winEl, who, t, { peer, direction, ts } = {}) {
  const log = winEl._ym?.log;
  if (!log) return false;
  appendGenericMessageItem(log, who, buildTorrentCard(t), { ts, kind: "torrent", context: "dm" });
  scheduleScrollLogToBottom(log);
  return true;
}
