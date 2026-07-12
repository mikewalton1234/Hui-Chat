// ───────────────────────────────────────────────────────────────────────────────
// Torrent helpers (bencode parse + tracker scrape via server)
// ───────────────────────────────────────────────────────────────────────────────
// A small default tracker set (best-effort). Used when a magnet has no trackers.
// Note: many public trackers are UDP, which requires the server's UDP scrape support.
const DEFAULT_PUBLIC_TRACKERS = [
  "udp://tracker.opentrackr.org:1337/announce",
  "udp://open.stealth.si:80/announce",
  "udp://tracker.torrent.eu.org:451/announce",
  "udp://tracker.moeking.me:6969/announce",
  "https://tracker2.ctix.cn:443/announce",
  "https://tracker.tamersunion.org:443/announce"
];

function _torrentBoolSetting(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (value === null || value === undefined) return !!fallback;
  if (typeof value === "number") return value !== 0;
  const text = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "on", "enabled"].includes(text)) return true;
  if (["0", "false", "no", "off", "disabled", ""].includes(text)) return false;
  return !!fallback;
}

function torrentPublicFallbackEnabled() {
  return _torrentBoolSetting(window.HUI_CFG && window.HUI_CFG.torrent_public_fallback_scrape_enabled, true);
}

function torrentDhtScrapeEnabled() {
  return _torrentBoolSetting(window.HUI_CFG && window.HUI_CFG.torrent_dht_scrape_enabled, true);
}

function configuredPublicTorrentTrackers() {
  if (!torrentPublicFallbackEnabled()) return [];
  const raw = window.HUI_CFG && window.HUI_CFG.torrent_public_fallback_trackers;
  const source = Array.isArray(raw) && raw.length ? raw : DEFAULT_PUBLIC_TRACKERS;
  const out = [];
  for (const item of source) {
    const text = String(item || "").trim();
    if (!text || out.includes(text)) continue;
    try {
      const u = new URL(text);
      if (!["udp:", "http:", "https:"].includes(u.protocol)) continue;
      if (u.username || u.password) continue;
    } catch {
      continue;
    }
    out.push(text);
    if (out.length >= 12) break;
  }
  return out.length ? out : DEFAULT_PUBLIC_TRACKERS.slice();
}

function isTorrentScrapeEnabled() {
  return _torrentBoolSetting(window.HUI_CFG && window.HUI_CFG.torrent_scrape_enabled, false);
}

function setTorrentScrapeEnabledForClient(enabled) {
  window.HUI_CFG = window.HUI_CFG || {};
  window.HUI_CFG.torrent_scrape_enabled = !!enabled;
  if (enabled) window.HUI_CFG.torrent_scrape_disabled_reason = "";
}

function torrentScrapeDisabledReason() {
  return String(
    (window.HUI_CFG && window.HUI_CFG.torrent_scrape_disabled_reason)
    || "Tracker scraping is disabled by the server administrator. Server setting torrent_scrape_enabled=false. Admin can enable tracker scraping under Admin Panel → Limits and uploads."
  );
}

// Back-compat aliases for older guards/third-party snippets. Runtime code uses
// the dynamic helpers above so the admin can enable scraping without reloading.
const TORRENT_SCRAPE_ENABLED = isTorrentScrapeEnabled();
const TORRENT_SCRAPE_DISABLED_REASON = torrentScrapeDisabledReason();

function _hexFromBytes(u8) {
  return Array.from(u8).map(b => b.toString(16).padStart(2, "0")).join("");
}

async function sha1HexFromBytes(u8) {
  if (!HAS_WEBCRYPTO) throw new Error("WebCrypto required");
  const digest = await crypto.subtle.digest("SHA-1", u8.buffer.slice(u8.byteOffset, u8.byteOffset + u8.byteLength));
  return _hexFromBytes(new Uint8Array(digest));
}

function _bdecodeWithInfoSlice(bytes) {
  // Minimal bencode decoder that also captures the raw bencoded "info" dict slice.
  let i = 0;
  const td = new TextDecoder("utf-8");
  let infoStart = null, infoEnd = null;

  const parse = () => {
    const c = bytes[i];
    if (c === 0x69) { // i
      i++;
      const end = bytes.indexOf(0x65, i);
      const num = parseInt(td.decode(bytes.slice(i, end)), 10);
      i = end + 1;
      return num;
    }
    if (c === 0x6C) { // l
      i++;
      const arr = [];
      while (bytes[i] !== 0x65) arr.push(parse());
      i++;
      return arr;
    }
    if (c === 0x64) { // d
      i++;
      const obj = {};
      while (bytes[i] !== 0x65) {
        const kBytes = parse();
        const key = td.decode(kBytes);
        if (key === "info") {
          infoStart = i;
          obj[key] = parse();
          infoEnd = i;
        } else {
          obj[key] = parse();
        }
      }
      i++;
      return obj;
    }
    // bytes: <len>:<payload>
    let colon = bytes.indexOf(0x3A, i);
    const len = parseInt(td.decode(bytes.slice(i, colon)), 10);
    i = colon + 1;
    const out = bytes.slice(i, i + len);
    i += len;
    return out;
  };

  const root = parse();
  const infoSlice = (infoStart !== null && infoEnd !== null) ? bytes.slice(infoStart, infoEnd) : null;
  return { root, infoSlice };
}

function _u8ToUtf8(u8) {
  try { return new TextDecoder("utf-8").decode(u8); } catch { return ""; }
}

function parseTorrentBytes(u8) {
  const { root, infoSlice } = _bdecodeWithInfoSlice(u8);
  const t = root || {};
  const info = t.info || {};
  const name = info.name ? _u8ToUtf8(info.name) : "Torrent";

  const trackers = [];
  if (t.announce) trackers.push(_u8ToUtf8(t.announce));
  if (Array.isArray(t["announce-list"])) {
    for (const tier of t["announce-list"]) {
      if (!Array.isArray(tier)) continue;
      for (const tr of tier) trackers.push(_u8ToUtf8(tr));
    }
  }
  const declaredTrackerCount = [...new Set(trackers.filter(Boolean))].length;
  const uniqTrackers = declaredTrackerCount ? [...new Set(trackers.filter(Boolean))].slice(0, 25) : DEFAULT_PUBLIC_TRACKERS.slice();
  const trackerSource = declaredTrackerCount ? "torrent" : "public_fallback";

  let totalSize = 0;
  if (typeof info.length === "number") totalSize = info.length;
  if (Array.isArray(info.files)) {
    totalSize = 0;
    for (const f of info.files) totalSize += (typeof f.length === "number" ? f.length : 0);
  }

  const webSeeds = [];
  const addWebSeed = (raw) => {
    const text = raw ? _u8ToUtf8(raw) : "";
    if (!text) return;
    if (!/^https?:\/\//i.test(text)) return;
    if (!webSeeds.includes(text)) webSeeds.push(text);
  };
  for (const key of ["url-list", "url-list.utf-8", "httpseeds"]) {
    const raw = t[key];
    if (Array.isArray(raw)) raw.forEach(addWebSeed);
    else addWebSeed(raw);
  }

  const creation_date = (typeof t["creation date"] === "number") ? new Date(t["creation date"] * 1000).toISOString() : "";
  const created_by = t["created by"] ? _u8ToUtf8(t["created by"]) : "";
  const comment = t.comment ? _u8ToUtf8(t.comment) : "";

  return { name, trackers: uniqTrackers, tracker_count: uniqTrackers.length, declared_tracker_count: declaredTrackerCount, tracker_source: trackerSource, using_public_fallback_trackers: trackerSource === "public_fallback", web_seeds: webSeeds, web_seed_count: webSeeds.length, total_size: totalSize, infoSlice, created_by, creation_date, comment };
}

function buildMagnet(infohashHex, name, trackers = []) {
  const xt = `urn:btih:${String(infohashHex || "").toLowerCase()}`;
  const params = new URLSearchParams();
  params.set("xt", xt);
  if (name) params.set("dn", name);
  for (const tr of (trackers || []).slice(0, 15)) params.append("tr", tr);
  return "magnet:?" + params.toString();
}

function _base32ToHex(s) {
  // Decode 32-char base32 (A-Z2-7) to 20-byte infohash, return hex.
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const clean = String(s || "").trim().toUpperCase().replace(/=+$/g, "");
  let bits = 0;
  let value = 0;
  const out = [];
  for (const ch of clean) {
    const idx = alphabet.indexOf(ch);
    if (idx === -1) return null;
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      out.push((value >>> bits) & 0xff);
    }
  }
  if (out.length !== 20) return null;
  return _hexFromBytes(new Uint8Array(out));
}

function _uniqueTorrentList(items, limit = 25) {
  const out = [];
  for (const item of (Array.isArray(items) ? items : [])) {
    const text = String(item || "").trim();
    if (!text || out.includes(text)) continue;
    out.push(text);
    if (out.length >= limit) break;
  }
  return out;
}

function _isDefaultPublicTrackerList(trackers = []) {
  const supplied = _uniqueTorrentList(trackers, 25);
  if (!supplied.length) return false;
  const defaults = new Set(configuredPublicTorrentTrackers().map((tr) => String(tr || "").trim()).filter(Boolean));
  return supplied.every((tr) => defaults.has(tr));
}

function parseMagnet(magnetText) {
  const raw = String(magnetText || "").trim();
  if (!raw.toLowerCase().startsWith("magnet:?")) return null;
  const q = raw.slice(raw.indexOf("?") + 1);
  const params = new URLSearchParams(q);
  const xts = params.getAll("xt");
  let infohash = "";
  for (const xt of xts) {
    const v = String(xt || "");
    const m = v.match(/urn:btih:([a-zA-Z0-9]+)/i);
    if (!m) continue;
    const token = m[1];
    if (/^[0-9a-fA-F]{40}$/.test(token)) {
      infohash = token.toLowerCase();
      break;
    }
    if (/^[A-Z2-7]{32}$/i.test(token)) {
      const hex = _base32ToHex(token);
      if (hex) { infohash = hex.toLowerCase(); break; }
    }
  }
  if (!infohash) return null;

  const dn = params.get("dn") || "";
  const declaredTrackers = _uniqueTorrentList(params.getAll("tr").map(String).filter(Boolean), 25);
  const fallbackTrackers = configuredPublicTorrentTrackers();
  const usedFallbackTrackers = torrentPublicFallbackEnabled() && (!declaredTrackers.length || _isDefaultPublicTrackerList(declaredTrackers));
  const usableTrackers = declaredTrackers.length ? declaredTrackers : fallbackTrackers;
  const webSeeds = _uniqueTorrentList(params.getAll("ws").concat(params.getAll("as")).map(String).filter((u) => /^https?:\/\//i.test(u)), 512);

  // Canonical magnet we share/copy. If no trackers were supplied, attach the
  // built-in public fallback tracker list and mark it as fallback metadata so
  // pasted/copied magnets can still refresh seeds/leechers even when arbitrary
  // user-supplied tracker scraping is disabled.
  const canonical = buildMagnet(infohash, dn, usableTrackers);
  return {
    infohash,
    name: dn,
    trackers: usableTrackers,
    declared_tracker_count: usedFallbackTrackers ? 0 : declaredTrackers.length,
    tracker_count: usableTrackers.length,
    tracker_source: usedFallbackTrackers ? "public_fallback" : "magnet",
    using_public_fallback_trackers: !!usedFallbackTrackers,
    web_seeds: webSeeds,
    web_seed_count: webSeeds.length,
    magnet: canonical
  };
}

function isMagnetText(text) {
  const s = String(text || "").trim();
  return s.toLowerCase().startsWith("magnet:?");
}

async function fetchTorrentSwarm(infohashHex, trackers = [], opts = {}) {
  const suppliedTrackers = Array.isArray(trackers) ? trackers.filter(Boolean) : [];
  const publicFallbackSet = new Set(configuredPublicTorrentTrackers());
  const fallbackOnly = !suppliedTrackers.length || (torrentPublicFallbackEnabled() && suppliedTrackers.every((tr) => publicFallbackSet.has(String(tr || ""))));
  if (!isTorrentScrapeEnabled() && !fallbackOnly) {
    return { seeds: null, leechers: null, completed: null, disabled: true, error: torrentScrapeDisabledReason() };
  }
  try {
    const resp = await fetchWithAuth("/api/torrent/scrape", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        infohash_hex: String(infohashHex || ""),
        trackers: (Array.isArray(trackers) ? trackers : []).slice(0, 12),
        force_refresh: !!(opts && (opts.force_refresh || opts.no_cache || opts.bypass_cache || opts.manual))
      })
    });
    const data = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(resp, null) : await resp.json().catch(() => null);
    if (!resp || !resp.ok || !data?.success) {
      return {
        seeds: null,
        leechers: null,
        completed: null,
        disabled: resp && resp.status === 403,
        error: (data && data.error) ? String(data.error) : `Swarm lookup failed${resp ? ` (${resp.status})` : ""}`,
        status: data && data.status ? String(data.status) : (resp && resp.status === 403 ? "disabled" : "error"),
        trackers_tried: Number((data && data.trackers_tried) || 0),
        tracker_count: Number((data && data.tracker_count) || (trackers || []).length || 0)
      };
    }
    return {
      seeds: (data.seeds === null || data.seeds === undefined) ? null : Number(data.seeds),
      leechers: (data.leechers === null || data.leechers === undefined) ? null : Number(data.leechers),
      completed: (data.completed === null || data.completed === undefined) ? null : Number(data.completed),
      cached: !!data.cached,
      disabled: false,
      error: data.error ? String(data.error) : "",
      status: data.status ? String(data.status) : "",
      trackers_tried: Number(data.trackers_tried || 0),
      tracker_count: Number(data.tracker_count || 0)
    };
  } catch (err) {
    return { seeds: null, leechers: null, completed: null, disabled: false, error: err && err.message ? err.message : "Swarm lookup failed" };
  }
}

async function sendTorrentShare(toUser, file, { win } = {}) {
  if (!file || !toUser) return;

  let meta = null;
  try {
    const ab = await file.arrayBuffer();
    const u8 = new Uint8Array(ab);
    const parsed = parseTorrentBytes(u8);
    const infohash = parsed.infoSlice ? await sha1HexFromBytes(parsed.infoSlice) : "";
    const magnet = infohash ? buildMagnet(infohash, parsed.name, parsed.trackers) : "";
    // IMPORTANT (UX): do NOT block sending on tracker scrape.
    // Scraping can take 10–60s when trackers are slow/unreachable.
    // Torrent cards already self-refresh swarm stats asynchronously.
    const swarm = { seeds: null, leechers: null, completed: null };

    meta = {
      _ec: "torrent",
      name: parsed.name || file.name,
      infohash,
      magnet,
      total_size: parsed.total_size || 0,
      seeds: swarm.seeds,
      leechers: swarm.leechers,
      completed: swarm.completed,
      trackers: parsed.trackers || [],
      web_seeds: parsed.web_seeds || [],
      web_seed_count: parsed.web_seed_count || 0,
      comment: parsed.comment || "",
      created_by: parsed.created_by || "",
      creation_date: parsed.creation_date || ""
    };
  } catch (e) {
    toast("⚠️ Could not parse torrent; sending as a normal file", "warn");
  }

  if (meta) {
    try {
      await sendPrivateTo(toUser, JSON.stringify(meta));
      if (win) appendTorrentLine(win, "You:", { ...meta, file_name: file.name });
    } catch {
      toast("⚠️ Could not send torrent metadata (still sending file)…", "warn");
    }
  }

  await sendDmFileTo(toUser, file, { win });
}

async function sendTorrentMagnetShare(toUser, magnetText, { win } = {}) {
  if (!toUser) return;
  const parsed = parseMagnet(magnetText);
  if (!parsed) {
    toast("⚠️ Invalid magnet link", "warn");
    return;
  }

  // IMPORTANT (UX): do NOT block sending on tracker scrape.
  // Torrent cards already self-refresh swarm stats asynchronously.
  const swarm = { seeds: null, leechers: null, completed: null };

  const meta = {
    _ec: "torrent",
    name: parsed.name || "Magnet",
    infohash: parsed.infohash,
    magnet: parsed.magnet,
    total_size: 0,
    seeds: swarm.seeds,
    leechers: swarm.leechers,
    completed: swarm.completed,
    trackers: parsed.trackers || [],
    declared_tracker_count: Number(parsed.declared_tracker_count || 0),
    tracker_count: Number(parsed.tracker_count || (Array.isArray(parsed.trackers) ? parsed.trackers.length : 0) || 0),
    tracker_source: parsed.tracker_source || (parsed.using_public_fallback_trackers ? "public_fallback" : "magnet"),
    using_public_fallback_trackers: !!parsed.using_public_fallback_trackers,
    web_seeds: Array.isArray(parsed.web_seeds) ? parsed.web_seeds : [],
    web_seed_count: Number(parsed.web_seed_count || 0),
    scrape_status: "pending",
    scrape_error: "",
    swarm_deferred: true
  };

  const ok = await sendPrivateTo(toUser, JSON.stringify(meta));
  if (!ok) return null;
  if (win) appendTorrentLine(win, "You:", meta);
  return meta;
}

function downloadPmHistory() {
  const d = loadPmHistory();
  const json = JSON.stringify(d, null, 2);
  downloadTextFile(`hui_pm_history_${currentUser}_${new Date().toISOString().slice(0,10)}.json`, json);
}

function ensureDmHistoryRendered(win, peer) {
  if (!win || !peer) return;
  if (!UIState.prefs.savePmLocal) return;

  // Render once per window instance.
  if (win.dataset.pmHistoryRendered === "1") return;
  win.dataset.pmHistoryRendered = "1";

  const hist = getPmHistory(peer);
  if (!hist.length) return;

  appendLine(win, "System:", `Loaded ${hist.length} local history message(s).`, { ts: Date.now() });
  for (const h of hist) {
    const tag = (h.dir === "out") ? "You:" : `${peer}:`;
    try {
      if (typeof parseDmPayload === "function" && typeof appendDmPayload === "function") {
        const payload = parseDmPayload(h.text);
        if (payload && (payload.kind === "file" || payload.kind === "torrent")) {
          appendDmPayload(win, tag, payload, { peer, direction: h.dir, ts: h.ts });
          continue;
        }
      }
    } catch {}
    appendLine(win, tag, h.text, { ts: h.ts, context: "dm" });
  }
}
