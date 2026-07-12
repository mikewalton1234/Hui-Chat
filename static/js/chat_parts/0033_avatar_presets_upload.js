const DICEBEAR_API_BASE = 'https://api.dicebear.com/10.x';
const DICEBEAR_DEFAULT_STYLE = 'avataaars';
const DICEBEAR_DEFAULT_BG = 'dbeafe';

const DICEBEAR_AVATAR_STYLES = [
  { key: 'avataaars', label: 'Avataaars', note: 'cartoon people' },
  { key: 'personas', label: 'Personas', note: 'clean social profile look' },
  { key: 'lorelei', label: 'Lorelei', note: 'friendly illustrated faces' },
  { key: 'adventurer', label: 'Adventurer', note: 'simple character avatars' },
  { key: 'pixel-art', label: 'Pixel Art', note: 'retro pixels' },
  { key: 'bottts', label: 'Bottts', note: 'robots' },
  { key: 'fun-emoji', label: 'Fun Emoji', note: 'big expressive faces' },
  { key: 'thumbs', label: 'Thumbs', note: 'playful icons' },
  { key: 'initials', label: 'Initials', note: 'letter avatar' },
  { key: 'identicon', label: 'Identicon', note: 'geometric fallback' },
];

// Backward-compatible name used by the profile editor and self-profile studio.
const LOCAL_AVATAR_PRESET_STYLES = DICEBEAR_AVATAR_STYLES;

function ecDiceBearStyleKeys() {
  return DICEBEAR_AVATAR_STYLES.map((style) => String(style.key || '').trim()).filter(Boolean);
}

function normalizeDiceBearStyleKey(style) {
  const raw = String(style || '').trim().toLowerCase().replace(/_/g, '-');
  const aliases = {
    persona: 'personas',
    person: 'personas',
    bot: 'bottts',
    bots: 'bottts',
    pixel: 'pixel-art',
    emoji: 'fun-emoji',
    shape: 'shapes',
    shapes: 'shapes',
    alien: 'adventurer',
    animal: 'adventurer',
    retro: 'pixel-art',
    politics: 'personas',
  };
  const candidate = aliases[raw] || raw || DICEBEAR_DEFAULT_STYLE;
  return ecDiceBearStyleKeys().includes(candidate) ? candidate : DICEBEAR_DEFAULT_STYLE;
}

function normalizeDiceBearSeed(seed) {
  const value = String(seed || '').trim();
  return (value || String(currentUser || 'hui')).slice(0, 96) || 'hui';
}

function normalizeDiceBearColor(raw, fallback = DICEBEAR_DEFAULT_BG) {
  const value = String(raw || '').trim().replace(/^#/, '').toLowerCase();
  if (/^[0-9a-f]{6}$/i.test(value)) return value;
  return String(fallback || DICEBEAR_DEFAULT_BG).replace(/^#/, '').toLowerCase();
}

function normalizeDiceBearBorderRadius(raw, fallback = 50) {
  const value = Number.parseInt(String(raw ?? fallback), 10);
  if (!Number.isFinite(value)) return Number.parseInt(String(fallback), 10) || 50;
  return Math.max(0, Math.min(50, value));
}

function buildDiceBearAvatarUrl(style, seed, opts = {}) {
  const safeStyle = normalizeDiceBearStyleKey(style);
  const q = new URLSearchParams();
  q.set('seed', normalizeDiceBearSeed(seed));
  q.set('backgroundColor', normalizeDiceBearColor(opts.backgroundColor || opts.background || DICEBEAR_DEFAULT_BG));
  q.set('borderRadius', String(normalizeDiceBearBorderRadius(opts.borderRadius ?? opts.radius ?? 50)));
  if (opts.flip === true || String(opts.flip || '').toLowerCase() === 'true') q.set('flip', 'true');
  const scale = Number.parseInt(String(opts.scale || ''), 10);
  if (Number.isFinite(scale) && scale >= 50 && scale <= 200 && scale !== 100) q.set('scale', String(scale));
  return `${DICEBEAR_API_BASE}/${encodeURIComponent(safeStyle)}/svg?${q.toString()}`;
}

function buildLocalAvatarPresetUrl(style, seed, opts = {}) {
  return buildDiceBearAvatarUrl(style, seed, opts);
}

function detectAvatarPresetSelection(url) {
  try {
    const raw = String(url || '').trim();
    if (!raw) return null;
    const parsed = new URL(raw, window.location.origin);
    if (parsed.origin === window.location.origin && parsed.pathname === '/avatar-preset.svg') {
      const localStyle = String(parsed.searchParams.get('style') || '').trim().toLowerCase();
      const mappedStyle = normalizeDiceBearStyleKey(localStyle);
      const seed = normalizeDiceBearSeed(parsed.searchParams.get('seed') || 'hui');
      return { provider: 'legacy-local', style: mappedStyle, seed, backgroundColor: DICEBEAR_DEFAULT_BG, borderRadius: 50, flip: false };
    }
    if (parsed.hostname !== 'api.dicebear.com') return null;
    const parts = parsed.pathname.split('/').filter(Boolean);
    const version = parts[0] || '';
    const style = normalizeDiceBearStyleKey(parts[1] || '');
    const format = parts[2] || '';
    if (!/^\d+\.x$/i.test(version) || format !== 'svg') return null;
    const seed = normalizeDiceBearSeed(parsed.searchParams.get('seed') || 'hui');
    const backgroundColor = normalizeDiceBearColor(parsed.searchParams.get('backgroundColor') || DICEBEAR_DEFAULT_BG);
    const borderRadius = normalizeDiceBearBorderRadius(parsed.searchParams.get('borderRadius') || 50);
    const flip = String(parsed.searchParams.get('flip') || '').toLowerCase() === 'true';
    const scale = Number.parseInt(String(parsed.searchParams.get('scale') || '100'), 10) || 100;
    return { provider: 'dicebear', style, seed, backgroundColor, borderRadius, flip, scale };
  } catch {
    return null;
  }
}

function buildAvatarPresetSeed(username, style, index) {
  const base = String(username || currentUser || 'hui').trim() || 'hui';
  return `${base}-${normalizeDiceBearStyleKey(style)}-${index}`;
}

function buildDiceBearRandomSeed(username = '') {
  const base = String(username || currentUser || 'hui').trim() || 'hui';
  const randomPart = Math.random().toString(36).slice(2, 10);
  return `${base}-${Date.now().toString(36)}-${randomPart}`;
}

function ecProfileMediaConfig() {
  return (window.HUI_CFG && typeof window.HUI_CFG === 'object') ? window.HUI_CFG : {};
}

function ecProfileAvatarAcceptMimeTypes() {
  const base = [
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico',
    'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp', 'image/x-icon', 'image/vnd.microsoft.icon'
  ];
  if (ecProfileMediaConfig().allow_svg_avatars === true) base.push('.svg', 'image/svg+xml');
  return base.join(',');
}

function ecProfileBannerAcceptMimeTypes() {
  return '.png,.jpg,.jpeg,.gif,.webp,.bmp,.ico,image/png,image/jpeg,image/gif,image/webp,image/bmp,image/x-icon,image/vnd.microsoft.icon';
}

function ecProfileValidateImageFile(file, kind = 'avatar') {
  if (!(file instanceof File)) return 'Choose an image first.';
  const cfg = ecProfileMediaConfig();
  const lowerName = String(file.name || '').toLowerCase();
  const mime = String(file.type || '').toLowerCase();
  const isPostImage = kind === 'post' || kind === 'post_image' || kind === 'profile_post';
  const maxBytes = kind === 'banner'
    ? Number(cfg.max_profile_banner_bytes || (8 * 1024 * 1024))
    : (isPostImage ? Number(cfg.max_profile_post_image_bytes || (8 * 1024 * 1024)) : Number(cfg.max_profile_avatar_bytes || (5 * 1024 * 1024)));
  const label = kind === 'banner' ? 'Banner' : (isPostImage ? 'Profile post image' : 'Avatar');
  if (Number.isFinite(maxBytes) && maxBytes > 0 && Number(file.size || 0) > maxBytes) {
    return `${label} is too large. Max ${(maxBytes / 1024 / 1024).toFixed(1).replace(/\.0$/, '')} MB.`;
  }
  const isSvg = mime === 'image/svg+xml' || lowerName.endsWith('.svg');
  if (kind === 'banner' && isSvg) return 'Banner SVG uploads are not allowed. Use PNG, JPG, GIF, WEBP, BMP, or ICO.';
  if (isPostImage && isSvg) return 'Profile post image SVG uploads are not allowed. Use PNG, JPG, GIF, WEBP, BMP, or ICO.';
  if (kind === 'avatar' && isSvg && cfg.allow_svg_avatars !== true) return 'SVG avatars are disabled on this server. Use PNG, JPG, GIF, WEBP, BMP, or ICO.';
  if (mime && !mime.startsWith('image/')) return 'Choose an image file.';
  return '';
}

function uploadMyAvatarFile(file, ctx = {}) {
  const { uploadBtn = null, setUploadStatus = null, avatarInput = null, afterSuccess = null } = ctx || {};
  return (async () => {
    const fileError = ecProfileValidateImageFile(file, 'avatar');
    if (fileError) {
      setUploadStatus?.(fileError, 'error');
      toast(`⚠️ ${fileError}`, 'warn');
      return null;
    }
    setUploadStatus?.('Uploading avatar…');
    if (uploadBtn) uploadBtn.disabled = true;
    try {
      const fd = new FormData();
      fd.append('file', file, file.name || 'avatar');
      const res = await fetchPostFormWithAuth('/api/profile/avatar_upload', fd);
      if (!res?.ok || !res?.json?.success) {
        const msg = res?.json?.error || 'Avatar upload failed';
        setUploadStatus?.(msg, 'error');
        toast(`❌ ${msg}`, 'error');
        return null;
      }
      UIState.myProfile = res.json.profile || { ...(UIState.myProfile || {}), avatar_url: res.json.avatar_url || '' };
      if (avatarInput) avatarInput.value = String(UIState.myProfile.avatar_url || '');
      renderMyHubIdentity(UIState.myProfile);
      try { if (typeof ecRefreshMessageAvatarsForUsername === 'function') ecRefreshMessageAvatarsForUsername(currentUser); } catch {}
      try { if (typeof socket !== 'undefined' && socket) socket.emit('get_presence_snapshot'); } catch {}
      setUploadStatus?.('Avatar uploaded and applied.');
      if (typeof afterSuccess === 'function') {
        try { afterSuccess(UIState.myProfile); } catch {}
      }
      toast('✅ Avatar uploaded', 'ok');
      return UIState.myProfile;
    } catch (err) {
      console.error(err);
      setUploadStatus?.('Avatar upload failed', 'error');
      toast('❌ Avatar upload failed', 'error');
      return null;
    } finally {
      if (uploadBtn) uploadBtn.disabled = false;
    }
  })();
}


function uploadMyBannerFile(file, ctx = {}) {
  const { uploadBtn = null, setUploadStatus = null, bannerInput = null, afterSuccess = null } = ctx || {};
  return (async () => {
    const fileError = ecProfileValidateImageFile(file, 'banner');
    if (fileError) {
      setUploadStatus?.(fileError, 'error');
      toast(`⚠️ ${fileError}`, 'warn');
      return null;
    }
    setUploadStatus?.('Uploading banner…');
    if (uploadBtn) uploadBtn.disabled = true;
    try {
      const fd = new FormData();
      fd.append('file', file, file.name || 'banner');
      const res = await fetchPostFormWithAuth('/api/profile/banner_upload', fd);
      if (!res?.ok || !res?.json?.success) {
        const msg = res?.json?.error || 'Banner upload failed';
        setUploadStatus?.(msg, 'error');
        toast(`❌ ${msg}`, 'error');
        return null;
      }
      UIState.myProfile = res.json.profile || { ...(UIState.myProfile || {}), banner_url: res.json.banner_url || '' };
      if (bannerInput) bannerInput.value = String(UIState.myProfile.banner_url || '');
      renderMyHubIdentity(UIState.myProfile);
      setUploadStatus?.('Banner uploaded. Save any other profile edits when you are ready.');
      if (typeof afterSuccess === 'function') {
        try { afterSuccess(UIState.myProfile); } catch {}
      }
      toast('✅ Banner uploaded', 'ok');
      return UIState.myProfile;
    } catch (err) {
      console.error(err);
      setUploadStatus?.('Banner upload failed', 'error');
      toast('❌ Banner upload failed', 'error');
      return null;
    } finally {
      if (uploadBtn) uploadBtn.disabled = false;
    }
  })();
}
