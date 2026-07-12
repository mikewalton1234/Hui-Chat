// ───────────────────────────────────────────────────────────────────────────────
// Toasts + optional browser notifications + optional sound
// ───────────────────────────────────────────────────────────────────────────────
// Sound-pack registry.  Sound-pack files may be online HTTPS .js URLs or local
// /static/js/sound_packs/*.js fallbacks. They register themselves before the chat runtime parts
// runs. If they load first, they queue metadata in window.EC_PENDING_SOUND_PACKS
// and this registry consumes it.
const EC_SOUND_PACK_DEFAULT = "hui_modern_generated";
const EC_SOUND_PACK_FILES = Object.freeze([
  { id: "hui_modern_generated", file: "0001_hui_modern_generated.js", label: "Hui modern generated", description: "Default generated JavaScript UI sounds; local fallback copy." },
  { id: "classic_messenger_generated", file: "0002_classic_messenger_generated.js", label: "Classic messenger generated", description: "Nostalgic generated messenger-style sounds; local fallback copy." }
]);

window.EC_PENDING_SOUND_PACKS = Array.isArray(window.EC_PENDING_SOUND_PACKS) ? window.EC_PENDING_SOUND_PACKS : [];
window.HuiChatSoundPacks = window.HuiChatSoundPacks || (function () {
  const packs = new Map();
  const soundToPack = new Map();

  function normId(value) {
    return String(value || "").trim().toLowerCase().replace(/[^a-z0-9_:-]+/g, "_").replace(/_+/g, "_").replace(/^_+|_+$/g, "");
  }

  function register(pack) {
    if (!pack || typeof pack !== "object") return false;
    const id = normId(pack.id || pack.name || "");
    if (!id) return false;
    const sounds = Array.isArray(pack.sounds) ? pack.sounds
      .map((sound) => ({
        id: normId(sound?.id || sound?.name || ""),
        label: String(sound?.label || sound?.name || sound?.id || "").trim(),
        description: String(sound?.description || "").trim(),
        url: String(sound?.url || sound?.src || sound?.href || "").trim(),
        volume: Number.isFinite(Number(sound?.volume)) ? Math.max(0, Math.min(1, Number(sound.volume))) : undefined
      }))
      .filter((sound) => sound.id)
      : [];
    const normalized = {
      id,
      file: String(pack.file || `${id}.js`).trim(),
      label: String(pack.label || id).trim(),
      description: String(pack.description || "").trim(),
      sounds,
      play: (typeof pack.play === "function") ? pack.play : null
    };
    packs.set(id, normalized);
    sounds.forEach((sound) => soundToPack.set(sound.id, id));
    return true;
  }

  function listPacks() {
    const configured = EC_SOUND_PACK_FILES.map((entry) => entry.id);
    const ordered = [];
    configured.forEach((id) => { if (packs.has(id)) ordered.push(packs.get(id)); });
    packs.forEach((pack, id) => { if (!configured.includes(id)) ordered.push(pack); });
    return ordered.map((pack) => ({ ...pack, sounds: pack.sounds.slice() }));
  }

  function listSounds() {
    const out = [];
    listPacks().forEach((pack) => {
      pack.sounds.forEach((sound) => out.push({ ...sound, packId: pack.id, packLabel: pack.label, file: pack.file }));
    });
    return out;
  }

  function hasSound(soundId) {
    return soundToPack.has(normId(soundId));
  }

  function getPackForSound(soundId) {
    const id = normId(soundId);
    const packId = soundToPack.get(id);
    return packId ? packs.get(packId) || null : null;
  }

  function labelForSound(soundId) {
    const id = normId(soundId);
    const pack = getPackForSound(id);
    const sound = pack?.sounds?.find((item) => item.id === id);
    return sound?.label || EC_SOUND_THEME_LABELS?.[id] || id;
  }

  function playRemoteUrl(url, volume) {
    const src = String(url || "").trim();
    if (!src || !(src.startsWith("https://") || src.startsWith("data:audio/"))) return false;
    try {
      const audio = new Audio(src);
      audio.preload = "auto";
      audio.volume = Math.max(0, Math.min(1, Number.isFinite(Number(volume)) ? Number(volume) : 0.7));
      const result = audio.play();
      if (result && typeof result.catch === "function") {
        result.catch((err) => { try { console.warn("Hui Chat remote sound play failed", err); } catch {} });
      }
      return true;
    } catch (err) {
      try { console.warn("Hui Chat remote sound failed", err); } catch {}
      return false;
    }
  }

  function play(soundId, ctx, helpers, kind) {
    const id = normId(soundId);
    const pack = getPackForSound(id);
    if (!pack) return false;
    const sound = pack.sounds.find((item) => item.id === id);
    if (typeof pack.play === "function") {
      try {
        pack.play(id, ctx, helpers, kind);
        return true;
      } catch (err) {
        try { console.warn("Hui Chat sound pack failed", pack.id, id, err); } catch {}
      }
    }
    if (sound?.url) return playRemoteUrl(sound.url, sound.volume);
    return false;
  }

  return { register, listPacks, listSounds, hasSound, getPackForSound, labelForSound, play };
})();

try {
  const pending = Array.isArray(window.EC_PENDING_SOUND_PACKS) ? window.EC_PENDING_SOUND_PACKS.splice(0) : [];
  pending.forEach((pack) => window.HuiChatSoundPacks.register(pack));
} catch {}

const HuiChatSoundPacks = window.HuiChatSoundPacks;

function ecRegisterKnownOnlineSoundLibraries() {
  try {
    const sns = window.SimpleNotificationSounds;
    if (sns && !HuiChatSoundPacks.getPackForSound?.("sns_attention_medium")) {
      const variants = ["short", "medium", "long"];
      const groups = [
        ["attention", "Attention", "playAttention"],
        ["alert", "Alert", "playAlert"],
        ["success", "Success", "playSuccess"],
        ["warning", "Warning", "playWarning"],
        ["error", "Error", "playError"]
      ];
      const sounds = [];
      groups.forEach(([name, label]) => {
        variants.forEach((variant) => sounds.push({
          id: `sns_${name}_${variant}`,
          label: `SNS ${label} ${variant}`,
          description: `Online Simple Notification Sounds ${label.toLowerCase()} cue (${variant}).`
        }));
      });
      HuiChatSoundPacks.register({
        id: "simple_notification_sounds_cdn",
        file: "https://cdn.jsdelivr.net/npm/simple-notification-sounds@1.0.0/dist/simple-notification-sounds.umd.js",
        label: "Simple Notification Sounds CDN",
        description: "MIT browser notification sound library loaded from an online CDN.",
        sounds,
        play(soundId) {
          const parts = String(soundId || "").split("_");
          const type = parts[1] || "attention";
          const variant = parts[2] || "medium";
          const match = groups.find(([name]) => name === type);
          const fn = match ? sns?.[match[2]] : null;
          if (typeof fn === "function") fn.call(sns, variant);
        }
      });
    }
  } catch (err) {
    try { console.warn("Hui Chat online sound library bridge failed", err); } catch {}
  }
}

ecRegisterKnownOnlineSoundLibraries();
window.ecRefreshKnownOnlineSoundLibraries = ecRegisterKnownOnlineSoundLibraries;

const EC_SOUND_THEME_DEFAULT = "soft_chime";
const EC_SOUND_THEME_OPTIONS = Object.freeze([
  "soft_chime",
  "bubble_pop",
  "glass_ping",
  "retro_blip",
  "muted_knock",
  "arcade_coin",
  "mellow_pluck",
  "sonar_ping",
  "digital_drop",
  "doorbell_duo",
  "page_flip",
  "success_twinkle",
  "warning_pulse",
  "low_buzz",
  "classic_msg_ping",
  "classic_msg_buzz",
  "classic_msg_knock",
  "classic_msg_door",
  "classic_msg_mail",
  "classic_msg_status"
]);

const EC_SOUND_THEME_LABELS = Object.freeze({
  soft_chime: "Soft chime",
  bubble_pop: "Bubble pop",
  glass_ping: "Glass ping",
  retro_blip: "Retro blip",
  muted_knock: "Muted knock",
  arcade_coin: "Arcade coin",
  mellow_pluck: "Mellow pluck",
  sonar_ping: "Sonar ping",
  digital_drop: "Digital drop",
  doorbell_duo: "Doorbell duo",
  page_flip: "Page flip",
  success_twinkle: "Success twinkle",
  warning_pulse: "Warning pulse",
  low_buzz: "Low buzz",
  classic_msg_ping: "Classic messenger ping",
  classic_msg_buzz: "Classic messenger buzz",
  classic_msg_knock: "Classic messenger knock",
  classic_msg_door: "Classic messenger sign on/off",
  classic_msg_mail: "Classic messenger mail",
  classic_msg_status: "Classic messenger status"
});

// Server/default event sound map. Admins can override these from the injected
// admin-only System settings panel; clients receive the non-secret values in
// HUI_CFG.sound_event_themes. End users can still turn sounds off locally.
const EC_SOUND_EVENT_DEFAULTS = Object.freeze({
  dm: "mellow_pluck",
  room_message: "soft_chime",
  group_message: "sonar_ping",
  room_invite: "doorbell_duo",
  group_invite: "doorbell_duo",
  friend_request: "success_twinkle",
  room_join: "page_flip",
  file: "digital_drop",
  error: "warning_pulse"
});

const EC_SOUND_EVENT_LABELS = Object.freeze({
  dm: "Private message",
  room_message: "Room message",
  group_message: "Group message",
  room_invite: "Room invite",
  group_invite: "Group invite",
  friend_request: "Friend request",
  room_join: "Room join/leave",
  file: "File or torrent",
  error: "Error/warning"
});

function ecNormalizeSoundPackId(value) {
  const key = String(value || "").trim().toLowerCase().replace(/[^a-z0-9_:-]+/g, "_").replace(/_+/g, "_").replace(/^_+|_+$/g, "");
  const known = (HuiChatSoundPacks?.listPacks?.() || []).map((pack) => pack.id);
  if (known.includes(key)) return key;
  const fileMatch = (HuiChatSoundPacks?.listPacks?.() || []).find((pack) => String(pack.file || "").toLowerCase() === key || String(pack.file || "").toLowerCase().replace(/\.js$/, "") === key.replace(/\.js$/, ""));
  return fileMatch?.id || EC_SOUND_PACK_DEFAULT;
}

function ecSoundPackOptionRows() {
  const packs = HuiChatSoundPacks?.listPacks?.() || [];
  if (packs.length) return packs;
  return EC_SOUND_PACK_FILES.map((entry) => ({ ...entry, sounds: [] }));
}

function ecSoundThemeOptionRows() {
  const loaded = HuiChatSoundPacks?.listSounds?.() || [];
  if (loaded.length) return loaded;
  return EC_SOUND_THEME_OPTIONS.map((id) => ({
    id,
    label: EC_SOUND_THEME_LABELS[id] || id,
    packId: EC_SOUND_PACK_DEFAULT,
    packLabel: "Built-in generated",
    file: "built-in sound fallback"
  }));
}

function ecPopulateSoundPackSelect(select, selectedValue) {
  if (!select) return;
  const selected = ecNormalizeSoundPackId(selectedValue || HUI_CFG?.sound_pack_default || EC_SOUND_PACK_DEFAULT);
  select.textContent = "";
  ecSoundPackOptionRows().forEach((pack) => {
    const opt = document.createElement("option");
    opt.value = pack.id;
    opt.textContent = `${pack.label || pack.id} — ${pack.file || `${pack.id}.js`}`;
    opt.title = pack.description || pack.file || pack.id;
    select.appendChild(opt);
  });
  select.value = selected;
  if (select.value !== selected) select.value = EC_SOUND_PACK_DEFAULT;
}

function ecPopulateSoundSelect(select, selectedValue, opts = {}) {
  if (!select) return;
  const selected = ecNormalizeSoundTheme(selectedValue || EC_SOUND_THEME_DEFAULT);
  const rows = ecSoundThemeOptionRows();
  const grouped = new Map();
  rows.forEach((row) => {
    const group = row.packLabel || row.packId || "Generated sounds";
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(row);
  });
  select.textContent = "";
  grouped.forEach((items, label) => {
    const og = document.createElement("optgroup");
    const file = items[0]?.file ? ` (${items[0].file})` : "";
    og.label = opts.showFiles === false ? label : `${label}${file}`;
    items.forEach((row) => {
      const opt = document.createElement("option");
      opt.value = row.id;
      opt.textContent = opts.prefixPack ? `${row.packLabel || row.packId}: ${row.label || row.id}` : (row.label || row.id);
      opt.title = [row.description, row.file].filter(Boolean).join(" — ");
      og.appendChild(opt);
    });
    select.appendChild(og);
  });
  select.value = selected;
  if (select.value !== selected) select.value = EC_SOUND_THEME_DEFAULT;
}

window.ecPopulateSoundSelect = ecPopulateSoundSelect;
window.ecPopulateSoundPackSelect = ecPopulateSoundPackSelect;
window.ecSoundThemeOptionRows = ecSoundThemeOptionRows;
window.ecSoundPackOptionRows = ecSoundPackOptionRows;

function ecNormalizeSoundTheme(value) {
  const key = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_")
    .replace(/[^a-z0-9_:-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  // v0.11.0-beta.52: remove the old computer-style beep.
  // Existing browsers that saved "classic_beep" now migrate to the soft chime.
  if (key === "classic_beep" || key === "beep" || key === "computer_beep") return EC_SOUND_THEME_DEFAULT;
  if (HuiChatSoundPacks?.hasSound?.(key)) return key;
  return EC_SOUND_THEME_OPTIONS.includes(key) ? key : EC_SOUND_THEME_DEFAULT;
}

function ecMigrateLegacyBeepTheme() {
  try {
    const before = String(UIState?.prefs?.soundTheme || "");
    const after = ecNormalizeSoundTheme(before);
    UIState.prefs.soundTheme = after;
    if (before && before !== after) Settings.set("soundTheme", after);
  } catch {}
}

function ecNormalizeSoundEvent(value) {
  const key = String(value || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  const aliases = {
    pm: "dm",
    private_message: "dm",
    direct_message: "dm",
    private_file: "file",
    torrent: "file",
    room_enter: "room_join",
    room_leave: "room_join",
    join: "room_join",
    leave: "room_join",
    invite: "room_invite",
    room: "room_message",
    group: "group_message",
    warn: "error",
    warning: "error"
  };
  const normalized = aliases[key] || key;
  return Object.prototype.hasOwnProperty.call(EC_SOUND_EVENT_DEFAULTS, normalized) ? normalized : "";
}

function ecGetServerSoundThemeDefault() {
  return ecNormalizeSoundTheme(
    HUI_CFG?.sound_theme_default ||
    HUI_CFG?.default_sound_theme ||
    EC_SOUND_THEME_DEFAULT
  );
}

function ecGetServerEventSoundTheme(eventName) {
  const eventKey = ecNormalizeSoundEvent(eventName);
  if (!eventKey) return "";
  const configured = (HUI_CFG && typeof HUI_CFG.sound_event_themes === "object")
    ? HUI_CFG.sound_event_themes
    : {};
  const raw = configured[eventKey] || HUI_CFG?.[`sound_event_${eventKey}`] || EC_SOUND_EVENT_DEFAULTS[eventKey];
  return ecNormalizeSoundTheme(raw);
}

function ecSelectSoundTheme(kind = "info", opts = {}) {
  if (opts?.theme) return ecNormalizeSoundTheme(opts.theme);

  const eventKey = ecNormalizeSoundEvent(opts?.event || ((kind === "error" || kind === "err") ? "error" : ""));
  if (eventKey) {
    const eventTheme = ecGetServerEventSoundTheme(eventKey);
    if (eventTheme) return eventTheme;
  }

  return ecNormalizeSoundTheme(UIState?.prefs?.soundTheme || ecGetServerSoundThemeDefault());
}

function ecGetAudioContext() {
  const AudioCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtor) return null;
  if (!window.__ecNotifyAudioCtx || window.__ecNotifyAudioCtx.state === "closed") {
    window.__ecNotifyAudioCtx = new AudioCtor();
  }
  return window.__ecNotifyAudioCtx;
}

function ecScheduleTone(ctx, at, duration, freq, opts = {}) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  const type = opts.type || "sine";
  const volume = Math.max(0, Math.min(0.12, Number(opts.volume ?? 0.035)));
  const attack = Math.max(0.002, Math.min(0.04, Number(opts.attack ?? 0.006)));
  const release = Math.max(0.01, Math.min(duration, Number(opts.release ?? 0.035)));

  osc.type = type;
  osc.frequency.setValueAtTime(freq, at);
  if (opts.slideTo) {
    osc.frequency.exponentialRampToValueAtTime(Math.max(1, Number(opts.slideTo)), at + duration);
  }

  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(volume, at + attack);
  gain.gain.setValueAtTime(volume, Math.max(at + attack, at + duration - release));
  gain.gain.exponentialRampToValueAtTime(0.0001, at + duration);

  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(at);
  osc.stop(at + duration + 0.01);
}

function ecScheduleNoisePop(ctx, at, duration, opts = {}) {
  const sampleRate = ctx.sampleRate || 44100;
  const frameCount = Math.max(1, Math.floor(sampleRate * duration));
  const buffer = ctx.createBuffer(1, frameCount, sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < frameCount; i += 1) {
    const fade = 1 - (i / frameCount);
    data[i] = (Math.random() * 2 - 1) * fade * 0.32;
  }

  const src = ctx.createBufferSource();
  const filter = ctx.createBiquadFilter();
  const gain = ctx.createGain();
  filter.type = opts.filter || "bandpass";
  filter.frequency.value = Number(opts.frequency || 900);
  filter.Q.value = Number(opts.q || 6);
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(Number(opts.volume || 0.018), at + 0.008);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + duration);

  src.buffer = buffer;
  src.connect(filter);
  filter.connect(gain);
  gain.connect(ctx.destination);
  src.start(at);
  src.stop(at + duration + 0.01);
}

function ecSoundKindShift(kind = "info") {
  const k = String(kind || "info").toLowerCase();
  if (k === "error") return 0.74;
  if (k === "warn" || k === "warning") return 0.86;
  if (k === "ok" || k === "success") return 1.12;
  return 1;
}

function ecShiftFreq(freq, kind = "info") {
  return Math.max(40, Number(freq || 440) * ecSoundKindShift(kind));
}

function ecScheduleChord(ctx, at, duration, freqs = [], opts = {}) {
  freqs.forEach((freq, idx) => {
    ecScheduleTone(ctx, at + Number(opts.stagger || 0) * idx, duration, freq, {
      type: opts.type || "sine",
      volume: Number(opts.volume || 0.016) * (idx === 0 ? 1 : 0.72),
      attack: opts.attack ?? 0.006,
      release: opts.release ?? 0.055,
      slideTo: opts.slideTo ? Number(opts.slideTo) * (freq / Number(freqs[0] || freq)) : undefined
    });
  });
}

function ecScheduleMessengerRoll(ctx, at, baseFreq, kind = "info", opts = {}) {
  const steps = Array.isArray(opts.steps) && opts.steps.length ? opts.steps : [1, 1.25, 1.5];
  steps.forEach((mult, idx) => {
    ecScheduleTone(ctx, at + idx * Number(opts.gap || 0.052), Number(opts.duration || 0.055), ecShiftFreq(baseFreq * Number(mult || 1), kind), {
      type: opts.type || "square",
      volume: Number(opts.volume || 0.012) * (1 - idx * 0.08),
      attack: 0.003,
      release: Number(opts.release || 0.032)
    });
  });
}

function ecGetSoundPackHelpers() {
  return {
    tone: ecScheduleTone,
    noise: ecScheduleNoisePop,
    chord: ecScheduleChord,
    roll: ecScheduleMessengerRoll,
    shift: ecShiftFreq,
    kindShift: ecSoundKindShift,
  };
}

function ecPlaySoundTheme(theme, kind = "info") {
  const ctx = ecGetAudioContext();
  if (!ctx) return;
  try { ctx.resume?.(); } catch {}
  const now = ctx.currentTime + 0.01;
  const normalizedTheme = ecNormalizeSoundTheme(theme);

  // Prefer the loaded /static/js/sound_packs/*.js implementation.  The switch
  // below remains as a safe built-in fallback if a pack file is missing.
  if (HuiChatSoundPacks?.play?.(normalizedTheme, ctx, ecGetSoundPackHelpers(), kind)) return;

  switch (normalizedTheme) {
    case "bubble_pop":
      ecScheduleTone(ctx, now, 0.055, ecShiftFreq(360, kind), { type: "sine", volume: 0.032, slideTo: ecShiftFreq(640, kind) });
      ecScheduleNoisePop(ctx, now + 0.018, 0.045, { frequency: ecShiftFreq(1450, kind), q: 9, volume: 0.014 });
      break;
    case "glass_ping":
      ecScheduleTone(ctx, now, 0.16, ecShiftFreq(880, kind), { type: "sine", volume: 0.028 });
      ecScheduleTone(ctx, now + 0.018, 0.20, ecShiftFreq(1320, kind), { type: "sine", volume: 0.014 });
      break;
    case "retro_blip":
      ecScheduleTone(ctx, now, 0.06, ecShiftFreq(520, kind), { type: "triangle", volume: 0.022 });
      ecScheduleTone(ctx, now + 0.065, 0.07, ecShiftFreq(780, kind), { type: "sine", volume: 0.018 });
      break;
    case "muted_knock":
      ecScheduleTone(ctx, now, 0.055, ecShiftFreq(180, kind), { type: "triangle", volume: 0.035, slideTo: ecShiftFreq(120, kind) });
      ecScheduleTone(ctx, now + 0.075, 0.055, ecShiftFreq(150, kind), { type: "triangle", volume: 0.025, slideTo: ecShiftFreq(95, kind) });
      break;
    case "arcade_coin":
      ecScheduleTone(ctx, now, 0.055, ecShiftFreq(988, kind), { type: "triangle", volume: 0.02 });
      ecScheduleTone(ctx, now + 0.065, 0.09, ecShiftFreq(1318, kind), { type: "sine", volume: 0.018 });
      break;
    case "mellow_pluck":
      ecScheduleChord(ctx, now, 0.13, [ecShiftFreq(440, kind), ecShiftFreq(660, kind)], { type: "triangle", volume: 0.018, stagger: 0.018, release: 0.09 });
      ecScheduleTone(ctx, now + 0.045, 0.16, ecShiftFreq(330, kind), { type: "sine", volume: 0.011, slideTo: ecShiftFreq(294, kind) });
      break;
    case "sonar_ping":
      ecScheduleTone(ctx, now, 0.22, ecShiftFreq(740, kind), { type: "sine", volume: 0.022, attack: 0.004, release: 0.18 });
      ecScheduleTone(ctx, now + 0.08, 0.26, ecShiftFreq(1480, kind), { type: "sine", volume: 0.008, attack: 0.005, release: 0.22 });
      break;
    case "digital_drop":
      ecScheduleTone(ctx, now, 0.075, ecShiftFreq(1180, kind), { type: "sawtooth", volume: 0.014, slideTo: ecShiftFreq(620, kind), release: 0.045 });
      ecScheduleTone(ctx, now + 0.07, 0.085, ecShiftFreq(620, kind), { type: "triangle", volume: 0.017, slideTo: ecShiftFreq(420, kind) });
      break;
    case "doorbell_duo":
      ecScheduleTone(ctx, now, 0.18, ecShiftFreq(523.25, kind), { type: "sine", volume: 0.021, release: 0.12 });
      ecScheduleTone(ctx, now + 0.13, 0.22, ecShiftFreq(659.25, kind), { type: "sine", volume: 0.018, release: 0.16 });
      break;
    case "page_flip":
      ecScheduleNoisePop(ctx, now, 0.052, { filter: "highpass", frequency: ecShiftFreq(1900, kind), q: 1.5, volume: 0.011 });
      ecScheduleNoisePop(ctx, now + 0.045, 0.062, { filter: "bandpass", frequency: ecShiftFreq(950, kind), q: 3, volume: 0.009 });
      break;
    case "success_twinkle":
      ecScheduleChord(ctx, now, 0.11, [ecShiftFreq(659.25, kind), ecShiftFreq(987.77, kind), ecShiftFreq(1318.51, kind)], { type: "sine", volume: 0.013, stagger: 0.042, release: 0.085 });
      break;
    case "warning_pulse":
      ecScheduleTone(ctx, now, 0.08, ecShiftFreq(330, kind), { type: "triangle", volume: 0.024 });
      ecScheduleTone(ctx, now + 0.105, 0.08, ecShiftFreq(330, kind), { type: "triangle", volume: 0.02 });
      break;
    case "low_buzz":
      ecScheduleTone(ctx, now, 0.12, ecShiftFreq(110, kind), { type: "sawtooth", volume: 0.015, slideTo: ecShiftFreq(82, kind), release: 0.08 });
      ecScheduleNoisePop(ctx, now + 0.015, 0.10, { filter: "lowpass", frequency: ecShiftFreq(420, kind), q: 2, volume: 0.008 });
      break;
    case "classic_msg_ping":
      // Nostalgic messenger-style two-step ping. Generated locally; no copied audio assets.
      ecScheduleMessengerRoll(ctx, now, 740, kind, { steps: [1, 1.335], gap: 0.07, duration: 0.06, type: "square", volume: 0.011 });
      ecScheduleTone(ctx, now + 0.145, 0.12, ecShiftFreq(1175, kind), { type: "triangle", volume: 0.012, release: 0.07 });
      break;
    case "classic_msg_buzz":
      // Short BUZZ-style vibration: urgent but quieter than the removed system beep.
      [0, 0.055, 0.11, 0.18].forEach((offset, idx) => {
        ecScheduleTone(ctx, now + offset, 0.045, ecShiftFreq(idx % 2 ? 118 : 92, kind), { type: "sawtooth", volume: 0.014, slideTo: ecShiftFreq(idx % 2 ? 88 : 132, kind), release: 0.026 });
      });
      ecScheduleNoisePop(ctx, now + 0.015, 0.18, { filter: "lowpass", frequency: ecShiftFreq(240, kind), q: 1.4, volume: 0.006 });
      break;
    case "classic_msg_knock":
      ecScheduleTone(ctx, now, 0.045, ecShiftFreq(205, kind), { type: "triangle", volume: 0.027, slideTo: ecShiftFreq(128, kind), release: 0.027 });
      ecScheduleTone(ctx, now + 0.082, 0.05, ecShiftFreq(185, kind), { type: "triangle", volume: 0.025, slideTo: ecShiftFreq(112, kind), release: 0.03 });
      ecScheduleNoisePop(ctx, now + 0.01, 0.035, { filter: "bandpass", frequency: ecShiftFreq(620, kind), q: 5, volume: 0.006 });
      break;
    case "classic_msg_door":
      ecScheduleChord(ctx, now, 0.09, [ecShiftFreq(392, kind), ecShiftFreq(587.33, kind)], { type: "triangle", volume: 0.013, stagger: 0.038, release: 0.055 });
      ecScheduleTone(ctx, now + 0.11, 0.10, ecShiftFreq(783.99, kind), { type: "sine", volume: 0.012, release: 0.065 });
      break;
    case "classic_msg_mail":
      ecScheduleMessengerRoll(ctx, now, 660, kind, { steps: [1, 1.25, 1.5], gap: 0.047, duration: 0.052, type: "triangle", volume: 0.013 });
      ecScheduleTone(ctx, now + 0.165, 0.15, ecShiftFreq(990, kind), { type: "sine", volume: 0.012, release: 0.1 });
      break;
    case "classic_msg_status":
      ecScheduleTone(ctx, now, 0.045, ecShiftFreq(523.25, kind), { type: "square", volume: 0.009, release: 0.025 });
      ecScheduleTone(ctx, now + 0.055, 0.045, ecShiftFreq(659.25, kind), { type: "square", volume: 0.009, release: 0.025 });
      ecScheduleTone(ctx, now + 0.11, 0.075, ecShiftFreq(783.99, kind), { type: "triangle", volume: 0.01, release: 0.05 });
      break;
    case "soft_chime":
    default:
      ecScheduleTone(ctx, now, 0.12, ecShiftFreq(587.33, kind), { type: "sine", volume: 0.026 });
      ecScheduleTone(ctx, now + 0.07, 0.16, ecShiftFreq(880, kind), { type: "sine", volume: 0.018 });
      break;
  }
}

function playUiSound(kind = "info", opts = {}) {
  if (!opts.force && !UIState.prefs.soundNotif) return;
  if (!opts.force && !AUDIO_ARMED) return;
  try {
    const theme = ecSelectSoundTheme(kind, opts || {});
    ecPlaySoundTheme(theme, kind);
  } catch {}
}

function testUiSoundTheme() {
  const select = $("setSoundTheme");
  const theme = ecNormalizeSoundTheme(select ? select.value : UIState?.prefs?.soundTheme);
  playUiSound("test", { force: true, theme });
}

function ecTestSoundTheme(theme, kind = "test") {
  playUiSound(kind || "test", { force: true, theme: ecNormalizeSoundTheme(theme || EC_SOUND_THEME_DEFAULT) });
}
window.ecTestSoundTheme = ecTestSoundTheme;
window.playUiSound = playUiSound;

// Backwards-compat with older chat code/tests that still call the legacy beep helper.
// It now routes through the selected non-beep theme.
function playBeep(kind = "info") {
  playUiSound(kind);
}

ecMigrateLegacyBeepTheme();

const EC_TOAST_DEDUPE_MS = 3500;
const EC_TOAST_HISTORY_LIMIT = 80;
const EC_TOAST_STACK_LIMIT = 6;
const EC_TOAST_HISTORY = new Map();
const EC_BROWSER_NOTIFY_HISTORY = new Map();

function ecNormalizeNotificationText(value) {
  return String(value || "")
    .replace(/[\u{1F300}-\u{1FAFF}]/gu, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function ecTrimRecentMap(map, limit = EC_TOAST_HISTORY_LIMIT) {
  try {
    while (map.size > limit) {
      const first = map.keys().next().value;
      if (first === undefined) break;
      map.delete(first);
    }
  } catch {}
}

function ecShouldAllowRecentNotification(map, key, windowMs) {
  const fingerprint = String(key || "").trim();
  if (!fingerprint) return true;
  const now = Date.now();
  const previous = Number(map.get(fingerprint) || 0);
  if (previous && (now - previous) < Number(windowMs || EC_TOAST_DEDUPE_MS)) return false;
  map.set(fingerprint, now);
  ecTrimRecentMap(map);
  return true;
}

function ecShouldShowToast(message, kind = "info", opts = {}) {
  if (opts && opts.dedupe === false) return true;
  const key = String(opts?.dedupeKey || `${kind}:${ecNormalizeNotificationText(message)}`);
  return ecShouldAllowRecentNotification(EC_TOAST_HISTORY, key, Number(opts?.dedupeMs || EC_TOAST_DEDUPE_MS));
}
function ecNormalizeToastKind(kind) {
  const key = String(kind || "info").trim().toLowerCase();
  if (["ok", "success"].includes(key)) return "ok";
  if (["warn", "warning"].includes(key)) return "warn";
  if (["error", "err", "danger"].includes(key)) return "error";
  return "info";
}

function ecToastTimeoutMs(value, fallback = 3500) {
  const raw = Number(value);
  if (!Number.isFinite(raw)) return fallback;
  return Math.max(800, Math.min(30000, raw));
}


function ecIsSelfRoomPresenceNotification(room, message) {
  const me = ecNormalizeNotificationText(currentUser || "");
  const r = ecNormalizeNotificationText(room || UIState?.currentRoom || "");
  const msg = ecNormalizeNotificationText(message || "");
  if (!me || !msg) return false;

  const selfPresencePatterns = [
    `${me} has entered ${r}`,
    `${me} entered ${r}`,
    `${me} has left ${r}`,
    `${me} left ${r}`,
    `${me} disconnected`,
    `${me} connected`,
  ].filter(Boolean);

  if (selfPresencePatterns.some((pattern) => msg === pattern || msg === `${pattern}.`)) return true;

  // Be conservative for room-scoped payloads: a local user already gets an explicit
  // "Joined room" / "Left room" action toast, so the server presence echo is duplicate noise.
  if (r && (msg === `${me} has entered ${r}.` || msg === `${me} has left ${r}.`)) return true;
  return false;
}

function ecIsWindowActivelyFocused() {
  try {
    return document.visibilityState === "visible" && document.hasFocus && document.hasFocus();
  } catch {
    return false;
  }
}

function ecShouldBrowserNotifyNow(opts = {}) {
  // Browser/OS notifications are for away-state attention. When Hui Chat is
  // already focused, the in-app UI is the notification surface; firing both a
  // toast and OS popup creates the duplicate/noisy behavior seen during room chat.
  if (opts?.force === true || opts?.allowWhileFocused === true) return true;
  return !ecIsWindowActivelyFocused();
}

function maybeBrowserNotify(title, body, opts = {}) {
  if (!UIState.prefs.popupNotif) return;
  if (!("Notification" in window)) return;
  if (!ecShouldBrowserNotifyNow(opts || {})) return;

  const dedupeKey = String(opts?.dedupeKey || `${ecNormalizeNotificationText(title)}:${ecNormalizeNotificationText(body)}`);
  if (!ecShouldAllowRecentNotification(EC_BROWSER_NOTIFY_HISTORY, dedupeKey, Number(opts?.dedupeMs || 5000))) return;

  if (Notification.permission === "granted") {
    try { new Notification(title, { body }); } catch {}
    return;
  }
}

function dismissToast(div) {
  if (!div) return;
  if (!window.ecMotionAllowed || !window.ecMotionAllowed()) {
    try { div.remove(); } catch {}
    return;
  }
  if (div.dataset.ecClosing === '1') return;
  div.dataset.ecClosing = '1';
  div.classList.add('ec-exit-fade');
  const finish = () => { try { div.remove(); } catch {} };
  try { div.addEventListener('animationend', finish, { once: true }); } catch {}
  setTimeout(finish, 220);
}

function ecTrimToastStack(stack, limit = EC_TOAST_STACK_LIMIT) {
  if (!stack) return;
  const max = Math.max(1, Number(limit || EC_TOAST_STACK_LIMIT));
  try {
    while (stack.children.length >= max) {
      dismissToast(stack.firstElementChild);
      if (stack.children.length < max) break;
      try { stack.firstElementChild?.remove?.(); } catch {}
    }
  } catch {}
}

function toast(message, kind = "info", timeout = 3500, opts = {}) {
  const stack = $("toastStack");
  if (!stack) return;
  const normalizedKind = ecNormalizeToastKind(kind);
  if (!ecShouldShowToast(message, normalizedKind, opts || {})) return;
  ecTrimToastStack(stack);

  const div = document.createElement("div");
  div.className = `toast ${normalizedKind}`;
  div.textContent = message;
  stack.appendChild(div);
  try { window.ecAnimateOnce?.(div, 'ec-enter-rise'); } catch {}

  playUiSound(normalizedKind, opts || {});

  setTimeout(() => dismissToast(div), ecToastTimeoutMs(timeout, 3500));
}

// Action toast (clickable CTA button)
function toastAction(message, opts = {}) {
  const kind = ecNormalizeToastKind(opts.kind || "info");
  const timeout = ecToastTimeoutMs(opts.timeout, 9000);
  const actionLabel = String(opts.actionLabel || "Open");
  const onAction = (typeof opts.onAction === "function") ? opts.onAction : null;

  const stack = $("toastStack");
  if (!stack) return;
  if (!ecShouldShowToast(message, kind, { ...(opts || {}), dedupeKey: opts?.dedupeKey || `action:${kind}:${ecNormalizeNotificationText(message)}` })) return;
  ecTrimToastStack(stack);

  const div = document.createElement("div");
  div.className = `toast ${kind} actionToast`;

  const msg = document.createElement("div");
  msg.className = "toastMsg";
  msg.textContent = message;

  const btn = document.createElement("button");
  btn.className = "toastBtn";
  btn.type = "button";
  btn.textContent = actionLabel;

  const finish = () => { dismissToast(div); };
  btn.onclick = (e) => {
    try { e?.stopPropagation?.(); } catch {}
    try { if (onAction) onAction(); } catch {}
    finish();
  };
  div.onclick = () => {
    try { if (onAction) onAction(); } catch {}
    finish();
  };

  div.appendChild(msg);
  div.appendChild(btn);
  stack.appendChild(div);

  try { window.ecAnimateOnce?.(div, 'ec-enter-rise'); } catch {}

  playUiSound(kind, opts || {});
  setTimeout(() => dismissToast(div), timeout);
}

function toastChoice(message, opts = {}) {
  const kind = ecNormalizeToastKind(opts.kind || "info");
  const timeout = ecToastTimeoutMs(opts.timeout, 12000);
  const acceptLabel = String(opts.acceptLabel || "✅");
  const declineLabel = String(opts.declineLabel || "❌");
  const onAccept = (typeof opts.onAccept === "function") ? opts.onAccept : null;
  const onDecline = (typeof opts.onDecline === "function") ? opts.onDecline : null;
  const stack = $("toastStack");
  if (!stack) return;
  if (!ecShouldShowToast(message, kind, { ...(opts || {}), dedupeKey: opts?.dedupeKey || `choice:${kind}:${ecNormalizeNotificationText(message)}` })) return;
  ecTrimToastStack(stack);

  const div = document.createElement("div");
  div.className = `toast ${kind} actionToast`;

  const msg = document.createElement("div");
  msg.className = "toastMsg";
  msg.textContent = message;

  const actions = document.createElement("div");
  actions.className = "toastActions";

  const acceptBtn = document.createElement("button");
  acceptBtn.className = "toastBtn";
  acceptBtn.type = "button";
  acceptBtn.textContent = acceptLabel;
  acceptBtn.title = "Join";

  const declineBtn = document.createElement("button");
  declineBtn.className = "toastBtn";
  declineBtn.type = "button";
  declineBtn.textContent = declineLabel;
  declineBtn.title = "No";

  const finish = () => { dismissToast(div); };
  acceptBtn.onclick = async (e) => {
    try { e?.stopPropagation?.(); } catch {}
    try { if (onAccept) await onAccept(); } catch {}
    finish();
  };
  declineBtn.onclick = async (e) => {
    try { e?.stopPropagation?.(); } catch {}
    try { if (onDecline) await onDecline(); } catch {}
    finish();
  };

  actions.appendChild(acceptBtn);
  actions.appendChild(declineBtn);
  div.appendChild(msg);
  div.appendChild(actions);
  stack.appendChild(div);

  try { window.ecAnimateOnce?.(div, 'ec-enter-rise'); } catch {}

  playUiSound(kind, opts || {});
  setTimeout(() => dismissToast(div), timeout);
}

// Backwards-compat with old code path:
function notify(msg) { toast(msg, "info"); }

// ───────────────────────────────────────────────────────────────────────────────
// Unlock private key (E2EE) — modal-based
// ───────────────────────────────────────────────────────────────────────────────
const PM_ENVELOPE_PREFIX = "EC1:";
const PM_PLAINTEXT_PREFIX = "ECP1:"; // plaintext DM wrapper (explicit legacy compat mode only)
const ROOM_ENVELOPE_PREFIX = "ECR1:";
const GROUP_ENVELOPE_PREFIX = "ECG1:";

// Cache RSA public keys (username -> { key: CryptoKey, fetchedAt: ms })
// NOTE: Keys can rotate (e.g., after password reset). Never cache forever.
const RSA_PUBKEY_CACHE = new Map();
const RSA_PUBKEY_CACHE_TTL_MS = Number((window.HUI_CFG && window.HUI_CFG.pubkey_cache_ttl_ms) || 60_000);
// Non-secret server-provided client config (injected in templates/chat.html)

// DM encryption policy (server-configurable)
const ALLOW_PLAINTEXT_DM_FALLBACK = (HUI_CFG.allow_plaintext_dm_fallback === undefined) ? false : !!HUI_CFG.allow_plaintext_dm_fallback;
const REQUIRE_DM_E2EE = (HUI_CFG.require_dm_e2ee === undefined) ? true : !!HUI_CFG.require_dm_e2ee;
const DM_PLAINTEXT_COMPAT_ALLOWED = ALLOW_PLAINTEXT_DM_FALLBACK && !REQUIRE_DM_E2EE;

function ecConfigBool(value, defaultValue = false) {
  if (value === undefined || value === null || value === "") return !!defaultValue;
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  const s = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "y", "on", "enabled"].includes(s)) return true;
  if (["0", "false", "no", "n", "off", "disabled", "none", "null"].includes(s)) return false;
  return !!defaultValue;
}

function ecConfigInt(value, defaultValue, minValue = 1, maxValue = Number.MAX_SAFE_INTEGER) {
  const n = Number(value);
  const fallback = Number(defaultValue);
  const base = Number.isFinite(n) ? Math.trunc(n) : Math.trunc(Number.isFinite(fallback) ? fallback : 0);
  return Math.max(Number(minValue), Math.min(Number(maxValue), base));
}

// Keep in sync with server max_dm_file_bytes (routes_main.py). Server can override per config.
const MAX_DM_FILE_BYTES = ecConfigInt(HUI_CFG.max_dm_file_bytes, 10 * 1024 * 1024, 1, 1024 * 1024 * 1024);
const MAX_GROUP_FILE_BYTES = ecConfigInt(HUI_CFG.max_group_file_bytes, MAX_DM_FILE_BYTES, 1, 1024 * 1024 * 1024);
const MAX_TORRENT_UPLOAD_BYTES = ecConfigInt(HUI_CFG.max_torrent_upload_bytes, 1_000_000, 1024, 5_000_000);
const FILE_TRANSFER_DISABLED = ecConfigBool(HUI_CFG.disable_file_transfer_globally, false);
const DM_FILE_DISABLED = FILE_TRANSFER_DISABLED || ecConfigBool(HUI_CFG.disable_dm_files_globally, false);
const GROUP_FILE_DISABLED = FILE_TRANSFER_DISABLED || ecConfigBool(HUI_CFG.disable_group_files_globally, false);

// Attempt WebRTC P2P first, fallback to server upload.
const P2P_FILE_ENABLED = !FILE_TRANSFER_DISABLED && ecConfigBool(HUI_CFG.p2p_file_enabled, true);
const P2P_FILE_CHUNK_BYTES = ecConfigInt(HUI_CFG.p2p_file_chunk_bytes ?? HUI_CFG.p2p_chunk_bytes, 64 * 1024, 1024, 1024 * 1024);
const P2P_FILE_HANDSHAKE_TIMEOUT_MS = ecConfigInt(HUI_CFG.p2p_file_handshake_timeout_ms ?? HUI_CFG.p2p_handshake_timeout_ms, 7_000, 100, 600_000);
const P2P_FILE_TRANSFER_TIMEOUT_MS = ecConfigInt(HUI_CFG.p2p_file_transfer_timeout_ms ?? HUI_CFG.p2p_transfer_timeout_ms, 120_000, 1000, 30 * 60_000);
const P2P_ICE_SERVERS = (Array.isArray(HUI_CFG.p2p_ice_servers) && HUI_CFG.p2p_ice_servers.length)
  ? HUI_CFG.p2p_ice_servers
  : [
      { urls: "stun:stun.l.google.com:19302" },
      { urls: "stun:stun1.l.google.com:19302" },
    ];
const P2P_TRANSFERS = new Map(); // transfer_id -> { role, peer, pc, dc, ui, meta, ... }
const P2P_PENDING_ICE = new Map(); // transfer_id -> RTCIceCandidateInit[] queued until PC/remoteDescription exists
const P2P_RECENT_TRANSFER_IDS = new Map(); // transfer_id -> expiresAt, prevents local reuse after close
const P2P_RECENT_TRANSFER_ID_TTL_MS = 5 * 60_000;
const P2P_ICE_QUEUE_LIMIT = 64;

// ───────────────────────────────────────────────────────────────────────────────
// Voice chat (WebRTC audio) — rooms + 1:1 calls
// ───────────────────────────────────────────────────────────────────────────────
const VOICE_ENABLED = ecConfigBool(HUI_CFG.voice_enabled, true);
// Missing/blank defaults to 100. IMPORTANT: explicit 0 still means unlimited.
const VOICE_MAX_ROOM_PEERS = (() => {
  const v = HUI_CFG.voice_max_room_peers;
  if (v === undefined || v === null || v === "") return 100;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
})();
const VOICE_ICE_SERVERS = (Array.isArray(HUI_CFG.voice_ice_servers) && HUI_CFG.voice_ice_servers.length)
  ? HUI_CFG.voice_ice_servers
  : P2P_ICE_SERVERS;
const VOICE_AUDIO_QUALITY = String(HUI_CFG.voice_audio_quality || "balanced").toLowerCase();
const VOICE_AUDIO_SAMPLE_RATE = Number(HUI_CFG.voice_audio_sample_rate) || 24000;
const VOICE_AUDIO_MAX_BITRATE = Number(HUI_CFG.voice_audio_max_bitrate) || 40000;
const VOICE_AUTO_QUALITY = ecConfigBool(HUI_CFG.voice_auto_quality, true);
const VOICE_NOISE_CANCELLATION = ecConfigBool(HUI_CFG.voice_noise_cancellation, true);
const VOICE_ECHO_CANCELLATION = ecConfigBool(HUI_CFG.voice_echo_cancellation, true);
const VOICE_AUTO_GAIN_CONTROL = ecConfigBool(HUI_CFG.voice_auto_gain_control, true);
const VOICE_DEFAULT_PUSH_TO_TALK = ecConfigBool(HUI_CFG.voice_default_push_to_talk, true);
const VOICE_QUALITY_PROFILES = (HUI_CFG.voice_quality_profiles && typeof HUI_CFG.voice_quality_profiles === "object")
  ? HUI_CFG.voice_quality_profiles
  : {
      low: { label: "Low bandwidth", sample_rate: 16000, max_bitrate: 24000 },
      balanced: { label: "Balanced", sample_rate: 24000, max_bitrate: 40000 },
      high: { label: "High quality", sample_rate: 48000, max_bitrate: 64000 },
    };

const VOICE_STATE = {
  micStream: null,
  // Single mic for DM + room voice. Mute is global.
  micMuted: false,
  talkHeld: false,
  handsFree: false,
  autoQualityTimers: new Map(),
  mediaByRoom: new Map(), // room -> Map(username -> { voice_on, webcam_on }) for GUI indicators
  dmCalls: new Map(), // peer -> { call_id, pc, remoteEl, state, muted, isCaller }
  room: {
    name: null,
    joined: false,
    wantRoomVoice: false, // user preference follows room switches until disabled
    viewerOnly: false, // true when joined only to receive an approved webcam stream
    peers: new Map(), // peer -> { pc, remoteEl }
    iceQueues: new Map(), // peer -> queued ICE candidates that arrived before remoteDescription
  }
};

// ───────────────────────────────────────────────────────────────────────────────
