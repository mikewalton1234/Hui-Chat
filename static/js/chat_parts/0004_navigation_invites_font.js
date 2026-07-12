// Ensure we don't leave "ghost" room occupants behind when the user navigates away
// (Back button, tab close, BFCache pagehide). This helps keep room counts accurate.
window.addEventListener("pagehide", () => {
  try {
    if (socket && socket.connected) {
      if (UIState.currentRoom) {
        // Best-effort; server disconnect handler is the real cleanup.
        socket.emit("leave", { room: UIState.currentRoom });
      }
      socket.disconnect();
    }
  } catch (e) {}
});

// Track which invite notifications we've already shown this tab/session.
// This prevents repeated toasts on reconnect/reload while still allowing
// invites to be re-surfaced after a full sign-out.
const INV_SEEN_SS_KEY = "hui_invite_seen_v1";
try {
  const raw = sessionStorage.getItem(INV_SEEN_SS_KEY);
  const arr = raw ? JSON.parse(raw) : [];
  if (Array.isArray(arr)) UIState.inviteSeen = new Set(arr.map(String));
} catch (e) {}

function rememberInviteSeen(key) {
  try {
    UIState.inviteSeen.add(String(key));
    sessionStorage.setItem(INV_SEEN_SS_KEY, JSON.stringify([...UIState.inviteSeen].slice(-200)));
  } catch (e) {}
}

function forgetInviteSeen(key) {
  try {
    UIState.inviteSeen.delete(String(key));
    sessionStorage.setItem(INV_SEEN_SS_KEY, JSON.stringify([...UIState.inviteSeen].slice(-200)));
  } catch (e) {}
}

function clampInt(v, min, max, fallback) {
  const n = parseInt(v, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

function applyRoomFontSize(px) {
  const val = clampInt(px, 10, 22, 13);
  document.documentElement.style.setProperty("--room-font-size", `${val}px`);
  const out = $("setRoomFontSizeVal");
  if (out) out.textContent = `${val}px`;
}

const EC_CLASSIC_ROOM_FONTS = [
  "Arial",
  "Verdana",
  "Tahoma",
  "Times New Roman",
  "Georgia",
  "Courier New",
  "Trebuchet MS"
];

function ecNormalizeRoomFontFamily(value) {
  const raw = String(value || "").trim();
  const match = EC_CLASSIC_ROOM_FONTS.find((f) => f.toLowerCase() === raw.toLowerCase());
  return match || "Arial";
}

function ecNormalizeRoomTextColor(value) {
  const raw = String(value || "").trim();
  return /^#[0-9a-f]{6}$/i.test(raw) ? raw.toLowerCase() : "#111111";
}

function ecGetRoomComposerPrefs() {
  const prefs = (typeof UIState !== "undefined" && UIState.prefs) ? UIState.prefs : {};
  return {
    font: ecNormalizeRoomFontFamily(prefs.roomFontFamily || Settings.get("roomFontFamily", "Arial")),
    size: clampInt(prefs.roomFontSize ?? Settings.get("roomFontSize", 13), 10, 22, 13),
    bold: !!(prefs.roomComposerBold ?? Settings.get("roomComposerBold", false)),
    italic: !!(prefs.roomComposerItalic ?? Settings.get("roomComposerItalic", false)),
    underline: !!(prefs.roomComposerUnderline ?? Settings.get("roomComposerUnderline", false)),
    color: ecNormalizeRoomTextColor(prefs.roomComposerColor || Settings.get("roomComposerColor", "#111111")),
  };
}

function ecApplyRoomComposerPrefs() {
  const p = ecGetRoomComposerPrefs();
  document.documentElement.style.setProperty("--room-font-family", `"${p.font.replace(/"/g, "")}"`);
  document.documentElement.style.setProperty("--room-composer-color", p.color);
  document.documentElement.style.setProperty("--room-composer-font-weight", p.bold ? "700" : "400");
  document.documentElement.style.setProperty("--room-composer-font-style", p.italic ? "italic" : "normal");
  document.documentElement.style.setProperty("--room-composer-text-decoration", p.underline ? "underline" : "none");
  applyRoomFontSize(p.size);

  const font = $("roomEmbedFontFamily");
  if (font) font.value = p.font;
  const size = $("roomEmbedFontSize");
  if (size) size.value = String(p.size);
  const color = $("roomEmbedTextColor");
  if (color) color.value = p.color;
  const map = [
    ["roomEmbedBoldBtn", p.bold],
    ["roomEmbedItalicBtn", p.italic],
    ["roomEmbedUnderlineBtn", p.underline],
  ];
  for (const [id, active] of map) {
    const btn = $(id);
    if (!btn) continue;
    btn.setAttribute("aria-pressed", active ? "true" : "false");
    btn.classList.toggle("active", active);
  }
}

function ecSetRoomComposerPref(key, value) {
  if (typeof UIState !== "undefined" && UIState.prefs) UIState.prefs[key] = value;
  Settings.set(key, value);
  ecApplyRoomComposerPrefs();
}

function ecBindClassicRoomComposerToolbar() {
  const toolbar = document.querySelector(".roomEmbedCompose .ecClassicComposeToolbar");
  if (!toolbar || toolbar.dataset.ecClassicBound === "1") return;
  toolbar.dataset.ecClassicBound = "1";

  const font = $("roomEmbedFontFamily");
  if (font) font.addEventListener("change", () => ecSetRoomComposerPref("roomFontFamily", ecNormalizeRoomFontFamily(font.value)));

  const size = $("roomEmbedFontSize");
  if (size) size.addEventListener("change", () => ecSetRoomComposerPref("roomFontSize", clampInt(size.value, 10, 22, 13)));

  const color = $("roomEmbedTextColor");
  if (color) color.addEventListener("input", () => ecSetRoomComposerPref("roomComposerColor", ecNormalizeRoomTextColor(color.value)));

  const toggles = [
    ["roomEmbedBoldBtn", "roomComposerBold"],
    ["roomEmbedItalicBtn", "roomComposerItalic"],
    ["roomEmbedUnderlineBtn", "roomComposerUnderline"],
  ];
  for (const [id, key] of toggles) {
    const btn = $(id);
    if (!btn) continue;
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      const next = btn.getAttribute("aria-pressed") !== "true";
      ecSetRoomComposerPref(key, next);
      try { $("roomEmbedInput")?.focus(); } catch {}
    });
  }
  ecApplyRoomComposerPrefs();
}

function ecClassicRoomComposerHasStyle() {
  const p = ecGetRoomComposerPrefs();
  return p.font !== "Arial" || p.size !== 13 || p.bold || p.italic || p.underline || p.color !== "#111111";
}

function ecClassicRoomComposerShouldBypassStyle(plaintext) {
  const text = String(plaintext ?? "").trim();
  if (!text) return false;
  // These payloads are control/media wires, not human text. Applying classic
  // font formatting to them wraps the JSON/magnet marker in styled_text and
  // makes room renderers show raw code instead of cards.
  if (/^gif:/i.test(text)) return true;
  try {
    if (typeof isMagnetText === "function" && isMagnetText(text)) return true;
  } catch {}
  if (!text.startsWith("{")) return false;
  try {
    const obj = JSON.parse(text);
    if (!obj || typeof obj !== "object") return false;
    const ec = String(obj._ec || obj.kind || obj.type || "").trim().toLowerCase();
    if (["torrent", "magnet", "file", "upload", "gif", "room_radio"].includes(ec)) return true;
    if (obj.magnet || obj.infohash || obj.infohash_hex || obj.torrent_id || obj.download_url) return true;
  } catch {}
  return false;
}

function ecBuildStyledRoomMessagePayload(plaintext) {
  const text = String(plaintext ?? "");
  if (!text.trim() || !ecClassicRoomComposerHasStyle()) return text;
  if (ecClassicRoomComposerShouldBypassStyle(text)) return text;
  const p = ecGetRoomComposerPrefs();
  return JSON.stringify({
    _ec: "styled_text",
    text,
    style: {
      font: p.font,
      size: p.size,
      bold: p.bold,
      italic: p.italic,
      underline: p.underline,
      color: p.color
    }
  });
}
