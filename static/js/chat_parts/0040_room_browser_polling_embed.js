const ROOM_MEDIA_STATE = new Map();
let ROOM_MEDIA_PRESENCE_TIMER = null;
let ROOM_MEDIA_DUCK_TIMER = null;
let ROOM_MEDIA_LOCAL_SUSPENDED = false;
let ROOM_MEDIA_LAST_ACTIVE_ROOM = '';
const ROOM_MEDIA_PLAYBACK_INTENT = new Map();
const ROOM_MEDIA_CONTINUE_AFTER_SWITCH = new Map();
const ROOM_MEDIA_LAST_FRAME_SRC = new Map();

function roomMediaCatalogMeta(roomName) {
  return rbFindCatalogRoom(roomName)?.meta || null;
}

function roomMediaSupportsRail(roomName) {
  const meta = roomMediaCatalogMeta(roomName);
  return !!(meta && Array.isArray(meta.features) && meta.features.some((flag) => ['music_room', 'music_share', 'room_radio'].includes(String(flag || '').trim())));
}


function roomMediaStationKey(station) {
  const normalized = roomMediaNormalizeStation(station);
  if (!normalized) return '';
  return String(normalized.embed_url || normalized.page_url || normalized.label || '').trim();
}

function roomMediaUrlWithAutoplay(url) {
  const raw = String(url || '').trim();
  if (!raw || raw === 'about:blank') return raw;
  try {
    const u = new URL(raw, window.location.origin);
    if (/iheart\.com$/i.test(u.hostname) || /(^|\.)iheart\.com$/i.test(u.hostname)) {
      if (!u.searchParams.has('embed')) u.searchParams.set('embed', 'true');
      u.searchParams.set('autoplay', 'true');
    } else if (!u.searchParams.has('autoplay')) {
      u.searchParams.set('autoplay', '1');
    }
    const next = u.toString();
    return (typeof ecNormalizeSafeUrl === 'function')
      ? ecNormalizeSafeUrl(next, { allowRelative: false, allowExternal: true })
      : next;
  } catch {
    return raw;
  }
}

function roomMediaSetPlaybackIntent(roomName, active) {
  const key = String(roomName || UIState.roomEmbedRoom || '').trim();
  if (!key) return;
  ROOM_MEDIA_PLAYBACK_INTENT.set(key, !!active);
}

function roomMediaHasPlayableFrame(frame) {
  const src = String(frame?.getAttribute?.('src') || frame?.src || '').trim();
  return !!src && src !== 'about:blank' && !/\/about:blank$/i.test(src);
}

function roomMediaIsListeningLocally(roomName = UIState.roomEmbedRoom) {
  const key = String(roomName || UIState.roomEmbedRoom || '').trim();
  if (!key || roomMediaShouldSuspendLocally(key)) return false;
  if (ROOM_MEDIA_PLAYBACK_INTENT.get(key) === true) return true;
  const pane = getRoomEmbedEl();
  const frame = pane?._ym?.mediaFrame;
  const rail = pane?._ym?.mediaRail;
  return !!(frame && roomMediaHasPlayableFrame(frame) && (!rail || !rail.classList.contains('hidden')));
}

function roomMediaShouldContinueAfterSwitch(roomName, previousStation, nextStation) {
  const key = String(roomName || '').trim();
  if (!key) return false;
  const oldKey = roomMediaStationKey(previousStation);
  const newKey = roomMediaStationKey(nextStation);
  if (!oldKey || !newKey || oldKey === newKey) return false;
  return roomMediaIsListeningLocally(key);
}

function roomMediaMarkContinueAfterSwitch(roomName) {
  const key = String(roomName || '').trim();
  if (key) ROOM_MEDIA_CONTINUE_AFTER_SWITCH.set(key, true);
}

function roomMediaNormalizeStation(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const label = String(raw.label || raw.name || '').trim();
  const provider = String(raw.provider || '').trim();
  const page_url = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(raw.page_url || raw.url || '', { allowRelative: false, allowExternal: true })
    : (/^https?:\/\//i.test(String(raw.page_url || raw.url || '')) ? String(raw.page_url || raw.url || '').trim() : '');
  let embed_url = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(raw.embed_url || '', { allowRelative: false, allowExternal: true })
    : (/^https?:\/\//i.test(String(raw.embed_url || '')) ? String(raw.embed_url || '').trim() : '');
  if (!embed_url && /iheart\.com/i.test(page_url)) {
    try {
      const u = new URL(page_url, window.location.origin);
      if (!u.searchParams.has('embed')) u.searchParams.set('embed', 'true');
      embed_url = u.toString();
    } catch {}
    if (embed_url && typeof ecNormalizeSafeUrl === 'function') {
      embed_url = ecNormalizeSafeUrl(embed_url, { allowRelative: false, allowExternal: true });
    }
  }
  if (!label && !page_url && !embed_url) return null;
  return { label: label || 'Station', provider, page_url, embed_url };
}

function roomMediaStationsFor(roomName) {
  const payload = ROOM_MEDIA_STATE.get(String(roomName || '').trim());
  const fromState = Array.isArray(payload?.stations) ? payload.stations.map(roomMediaNormalizeStation).filter(Boolean) : [];
  if (fromState.length) return fromState;
  const meta = roomMediaCatalogMeta(roomName);
  const source = Array.isArray(meta?.stations) ? meta.stations : [];
  return source.map(roomMediaNormalizeStation).filter(Boolean);
}

function roomMediaStateFor(roomName) {
  return ROOM_MEDIA_STATE.get(String(roomName || '').trim()) || null;
}

function roomMediaActiveStation(roomName) {
  const key = String(roomName || '').trim();
  if (!key) return null;
  const payload = roomMediaStateFor(key);
  const current = roomMediaNormalizeStation(payload?.station || null);
  if (current) return current;
  const stations = roomMediaStationsFor(key);
  return stations[0] || null;
}

function roomMediaSetPayload(roomName, payload, { announce = false } = {}) {
  const key = String(roomName || '').trim();
  if (!key || !payload || typeof payload !== 'object') return;
  const previousStation = roomMediaActiveStation(key);
  const continueAfterSwitch = roomMediaShouldContinueAfterSwitch(key, previousStation, payload.station || null);
  const normalized = { ...payload };
  normalized.station = roomMediaNormalizeStation(payload.station || null);
  const payloadStations = Array.isArray(payload.stations) ? payload.stations.map(roomMediaNormalizeStation).filter(Boolean) : [];
  const fallbackStations = roomMediaStationsFor(key);
  // Prefer server/admin payload stations so saved catalog edits and source deletions
  // replace stale browser state immediately instead of keeping old buttons around.
  normalized.stations = payloadStations.length ? payloadStations : fallbackStations;
  ROOM_MEDIA_STATE.set(key, normalized);
  if (continueAfterSwitch) roomMediaMarkContinueAfterSwitch(key);
  if (key === String(UIState.roomEmbedRoom || '').trim()) {
    roomMediaRender(key);
    if (announce) toast(`🎵 Room radio: ${normalized.station?.label || 'updated'}`, 'ok', 1800);
  }
}

function roomMediaLocalVolumeFor(roomName) {
  const key = String(roomName || '').trim();
  return Math.max(0, Math.min(100, Number(Settings.get(`roomMediaVolume:${key}`, 100)) || 0));
}

function roomMediaSetLocalVolume(roomName, value) {
  const key = String(roomName || '').trim();
  const vol = Math.max(0, Math.min(100, Number(value) || 0));
  Settings.set(`roomMediaVolume:${key}`, vol);
  roomMediaApplyLocalPlaybackPolicy(key);
  roomMediaRender(key);
  return vol;
}

function roomMediaDuckEnabled(roomName) {
  const key = String(roomName || '').trim();
  return !!Settings.get(`roomMediaDuck:${key}`, true);
}

function roomMediaSetDuckEnabled(roomName, enabled) {
  const key = String(roomName || '').trim();
  Settings.set(`roomMediaDuck:${key}`, !!enabled);
  roomMediaApplyLocalPlaybackPolicy(key);
  roomMediaRender(key);
}

function roomMediaExpandedFor(roomName) {
  const key = String(roomName || '').trim();
  return !!Settings.get(`roomMediaExpanded:${key}`, false);
}

function roomMediaSetExpanded(roomName, expanded) {
  const key = String(roomName || '').trim();
  Settings.set(`roomMediaExpanded:${key}`, !!expanded);
  roomMediaRender(key);
}


function roomMediaBlankPlaybackFrame(frame) {
  if (!frame) return null;
  let replacement = frame;
  try {
    frame.src = 'about:blank';
    frame.removeAttribute('src');
  } catch {}
  try {
    const clone = frame.cloneNode(false);
    clone.src = 'about:blank';
    clone.removeAttribute('src');
    frame.replaceWith(clone);
    replacement = clone;
  } catch {}
  try {
    const pane = getRoomEmbedEl();
    if (pane?._ym) pane._ym.mediaFrame = replacement;
  } catch {}
  return replacement;
}

function roomMediaStopLocalPlayback(roomName = UIState.roomEmbedRoom, { hideRail = true, heartbeat = true } = {}) {
  const key = String(roomName || UIState.roomEmbedRoom || '').trim();
  const pane = getRoomEmbedEl();
  const ym = pane?._ym || {};
  if (ym.mediaFrame) roomMediaBlankPlaybackFrame(ym.mediaFrame);
  if (ym.mediaRail && hideRail) ym.mediaRail.classList.add('hidden');
  roomMediaSetPlaybackIntent(key, false);
  ROOM_MEDIA_CONTINUE_AFTER_SWITCH.delete(key);
  ROOM_MEDIA_LAST_FRAME_SRC.delete(key);
  ROOM_MEDIA_LOCAL_SUSPENDED = true;
  if (heartbeat && key && roomMediaSupportsRail(key) && socket?.connected) {
    try { socket.emit('room_media_presence', { room: key, active: false }, () => {}); } catch {}
  }
}

function roomMediaShouldSuspendLocally(roomName) {
  const key = String(roomName || '').trim();
  if (!key) return true;
  if (roomMediaLocalVolumeFor(key) <= 0) return true;
  if (roomMediaDuckEnabled(key) && VOICE_STATE?.room?.joined && String(VOICE_STATE.room.name || '').trim() === key) return true;
  return false;
}

function roomMediaApplyLocalPlaybackPolicy(roomName = UIState.roomEmbedRoom) {
  const pane = getRoomEmbedEl();
  const ym = pane?._ym || {};
  let frame = ym.mediaFrame;
  const key = String(roomName || UIState.roomEmbedRoom || '').trim();
  if (!frame || !key) return;
  const station = roomMediaActiveStation(key);
  const shouldSuspend = roomMediaShouldSuspendLocally(key) || !(station?.embed_url);
  ROOM_MEDIA_LOCAL_SUSPENDED = shouldSuspend;
  frame.classList.toggle('is-muted-local', shouldSuspend);
  const safeEmbedUrl = (typeof ecNormalizeSafeUrl === 'function')
    ? ecNormalizeSafeUrl(station?.embed_url || '', { allowRelative: false, allowExternal: true })
    : String(station?.embed_url || '');
  let desiredSrc = shouldSuspend ? 'about:blank' : (safeEmbedUrl || 'about:blank');
  const continueAfterSwitch = ROOM_MEDIA_CONTINUE_AFTER_SWITCH.get(key) === true;
  if (continueAfterSwitch && desiredSrc !== 'about:blank') {
    desiredSrc = roomMediaUrlWithAutoplay(desiredSrc);
  }
  if (desiredSrc === 'about:blank') {
    roomMediaBlankPlaybackFrame(frame);
    roomMediaSetPlaybackIntent(key, false);
    ROOM_MEDIA_LAST_FRAME_SRC.delete(key);
    return;
  }
  frame = ym.mediaFrame || frame;
  const previousSrc = ROOM_MEDIA_LAST_FRAME_SRC.get(key) || '';
  const currentSrc = String(frame.getAttribute?.('src') || frame.src || '').trim();
  const mustReloadForContinue = continueAfterSwitch && previousSrc && previousSrc !== desiredSrc;
  if (mustReloadForContinue) {
    frame = roomMediaBlankPlaybackFrame(frame) || frame;
  }
  frame = ym.mediaFrame || frame;
  if (String(frame.getAttribute?.('src') || frame.src || '') !== desiredSrc || mustReloadForContinue || currentSrc !== desiredSrc) {
    frame.src = desiredSrc;
    try { frame.setAttribute('src', desiredSrc); } catch {}
  }
  ROOM_MEDIA_LAST_FRAME_SRC.set(key, desiredSrc);
  roomMediaSetPlaybackIntent(key, true);
  ROOM_MEDIA_CONTINUE_AFTER_SWITCH.delete(key);
}

async function roomMediaRequestState(roomName = UIState.roomEmbedRoom) {
  const key = String(roomName || '').trim();
  if (!key || !roomMediaSupportsRail(key)) return null;
  return await new Promise((resolve) => {
    socket.emit('get_room_media_state', { room: key }, (res) => {
      if (res?.success && res?.supported !== false) {
        roomMediaSetPayload(key, res, { announce: false });
        resolve(res);
        return;
      }
      resolve(res || null);
    });
  });
}

function roomMediaHeartbeat(active = true) {
  const key = String(UIState.roomEmbedRoom || '').trim();
  if (!key || !roomMediaSupportsRail(key) || !socket?.connected) return;
  socket.emit('room_media_presence', { room: key, active: !!active }, (res) => {
    if (res?.success && res?.room) {
      if (res.supported !== false && res.station) roomMediaSetPayload(res.room, res, { announce: false });
    }
  });
}

function roomMediaEnsurePresenceLoop() {
  if (!ROOM_MEDIA_PRESENCE_TIMER) {
    ROOM_MEDIA_PRESENCE_TIMER = setInterval(() => {
      const key = String(UIState.roomEmbedRoom || '').trim();
      if (!key || !roomMediaSupportsRail(key) || !socket?.connected) return;
      roomMediaHeartbeat(true);
    }, 15000);
  }
  if (!ROOM_MEDIA_DUCK_TIMER) {
    ROOM_MEDIA_DUCK_TIMER = setInterval(() => {
      const key = String(UIState.roomEmbedRoom || '').trim();
      if (!key || !roomMediaSupportsRail(key)) return;
      roomMediaApplyLocalPlaybackPolicy(key);
    }, 1500);
  }
}

function roomMediaVoteSkip(roomName = UIState.roomEmbedRoom) {
  const key = String(roomName || '').trim();
  if (!key) return;
  socket.emit('room_media_vote_skip', { room: key }, (res) => {
    if (!res?.success) {
      toast(`❌ ${res?.error || 'Skip vote failed'}`, 'error');
      return;
    }
    if (res?.switched && roomMediaIsListeningLocally(key)) roomMediaMarkContinueAfterSwitch(key);
    roomMediaSetPayload(key, res, { announce: !!res.switched });
    if (res?.switched && roomMediaIsListeningLocally(key)) {
      toast(`⏭️ Continuing radio on ${res.station?.label || 'next station'}`, 'ok', 1800);
    }
    if (res?.status === 'no_alternate') {
      toast('This room only has one configured source right now.', 'warn', 2400);
    }
  });
}

function roomMediaChangeSource(roomName, stationIndex) {
  const key = String(roomName || '').trim();
  if (!key) return;
  socket.emit('room_media_set_source', { room: key, station_index: Number(stationIndex) || 0 }, (res) => {
    if (!res?.success) {
      toast(`❌ ${res?.error || 'Could not switch source'}`, 'error');
      return;
    }
    if (roomMediaIsListeningLocally(key)) roomMediaMarkContinueAfterSwitch(key);
    roomMediaSetPayload(key, res, { announce: true });
  });
}

function roomMediaRender(roomName) {
  const pane = getRoomEmbedEl();
  const ym = pane?._ym || {};
  const rail = ym.mediaRail;
  if (!rail) return;
  const key = String(roomName || UIState.roomEmbedRoom || '').trim();
  const supports = roomMediaSupportsRail(key);
  if (!key || !supports) {
    roomMediaStopLocalPlayback(key, { hideRail: true, heartbeat: false });
    return;
  }
  const meta = roomMediaCatalogMeta(key) || {};
  const payload = roomMediaStateFor(key) || {};
  const station = roomMediaActiveStation(key);
  const stations = roomMediaStationsFor(key);
  const listenerCount = Number(payload.listener_count || 0) || 0;
  const requiredVotes = Number(payload.required_votes || 1) || 1;
  const votes = Number(payload.votes || 0) || 0;
  const stationCount = Number(payload.station_count || stations.length || 0) || stations.length || 0;
  const canSkipAdvance = payload.can_skip_advance !== false && stationCount > 1;
  const localVolume = roomMediaLocalVolumeFor(key);
  const duckEnabled = roomMediaDuckEnabled(key);
  const expanded = roomMediaExpandedFor(key);
  const isVoiceDucked = duckEnabled && VOICE_STATE?.room?.joined && String(VOICE_STATE.room.name || '').trim() === key;
  rail.classList.remove('hidden');
  rail.classList.toggle('is-expanded', !!expanded);
  rail.classList.toggle('is-compact', !expanded);
  if (ym.mediaTitle) ym.mediaTitle.textContent = station?.label ? `${station.label}` : 'Shared radio';
  if (ym.mediaMeta) {
    const tags = Array.isArray(meta.tags) ? meta.tags.slice(0, 3).join(' · ') : '';
    const counts = listenerCount > 0 ? `${listenerCount} listener${listenerCount === 1 ? '' : 's'} · ${votes}/${requiredVotes} skip votes` : `${votes}/${requiredVotes} skip votes`;
    ym.mediaMeta.textContent = station?.provider
      ? `${station.provider}${tags ? ` · ${tags}` : ''} · ${counts}`
      : `${meta.description || meta.topic || 'This room supports shared music links and radio.'}${counts ? ` · ${counts}` : ''}`;
  }
  if (ym.mediaVoteStatus) {
    ym.mediaVoteStatus.textContent = !canSkipAdvance
      ? 'This room needs at least two configured sources before /skip can advance it.'
      : (votes >= requiredVotes
        ? 'Vote threshold met. Switching source…'
        : `Type /skip or use the button. ${votes}/${requiredVotes} votes from ${listenerCount || 0} active listeners.`);
  }
  if (ym.mediaStations) {
    ym.mediaStations.replaceChildren();
    stations.forEach((entry, idx) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'roomEmbedMediaStationBtn';
      btn.textContent = entry.label || 'Station';
      const active = station && entry.label === station.label && (entry.embed_url || entry.page_url) === (station.embed_url || station.page_url);
      btn.classList.toggle('is-active', !!active);
      btn.addEventListener('click', () => roomMediaChangeSource(key, idx));
      ym.mediaStations.appendChild(btn);
    });
  }
  if (ym.mediaSkipBtn) {
    ym.mediaSkipBtn.onclick = () => roomMediaVoteSkip(key);
    ym.mediaSkipBtn.disabled = !canSkipAdvance;
    ym.mediaSkipBtn.title = canSkipAdvance ? 'Vote to switch this room to the next configured source.' : 'Add more room sources before vote skip can advance.';
    ym.mediaSkipBtn.textContent = !canSkipAdvance ? '/skip unavailable' : (votes > 0 ? `/skip vote (${votes}/${requiredVotes})` : '/skip vote');
  }
  if (ym.mediaMuteBtn) {
    ym.mediaMuteBtn.onclick = () => roomMediaSetLocalVolume(key, localVolume > 0 ? 0 : 100);
    ym.mediaMuteBtn.textContent = localVolume <= 0 ? 'Unmute local' : 'Mute local';
  }
  if (ym.mediaDuckChk) {
    ym.mediaDuckChk.checked = duckEnabled;
    ym.mediaDuckChk.onchange = () => roomMediaSetDuckEnabled(key, !!ym.mediaDuckChk.checked);
  }
  if (ym.mediaVolume) {
    ym.mediaVolume.value = String(localVolume);
    ym.mediaVolume.oninput = () => {
      const next = roomMediaSetLocalVolume(key, ym.mediaVolume.value);
      if (ym.mediaVolumeLabel) ym.mediaVolumeLabel.textContent = `${next}%`;
    };
  }
  if (ym.mediaVolumeLabel) ym.mediaVolumeLabel.textContent = `${localVolume}%`;
  if (ym.mediaVolumeHint) {
    ym.mediaVolumeHint.textContent = localVolume <= 0
      ? 'Room audio is muted locally for this account.'
      : (isVoiceDucked ? 'Room audio is paused locally while room voice is active.' : 'For iHeart embeds, local volume is limited by the provider. Set to 0% to mute locally or use Pause for voice.');
  }
  if (ym.mediaFrame) {
    ym.mediaFrame.classList.toggle('is-empty', !(station?.embed_url));
  }
  if (ym.mediaPlayerBtn) {
    ym.mediaPlayerBtn.onclick = () => roomMediaSetExpanded(key, !roomMediaExpandedFor(key));
    ym.mediaPlayerBtn.textContent = expanded ? 'Mini player' : 'Full player';
    ym.mediaPlayerBtn.setAttribute('aria-pressed', expanded ? 'true' : 'false');
    ym.mediaPlayerBtn.title = expanded ? 'Shrink the radio player so chat output has more room.' : 'Open the larger embedded station player.';
  }
  roomMediaApplyLocalPlaybackPolicy(key);
  if (ym.mediaOpenBtn) {
    ym.mediaOpenBtn.onclick = () => {
      const href = station?.page_url || station?.embed_url || '';
      if (!href) return;
      if (typeof ecOpenSafeUrl === 'function') ecOpenSafeUrl(href, { allowRelative: false, allowExternal: true });
      else window.open(href, '_blank', 'noopener,noreferrer');
    };
    ym.mediaOpenBtn.disabled = !(station?.page_url || station?.embed_url);
  }
  if (ym.mediaHideBtn) {
    ym.mediaHideBtn.onclick = () => rail.classList.add('hidden');
  }
}

function roomMediaOpenRail(roomName = UIState.roomEmbedRoom) {
  const pane = getRoomEmbedEl();
  const rail = pane?._ym?.mediaRail;
  if (!rail) return;
  roomMediaEnsurePresenceLoop();
  roomMediaRender(roomName);
  if (roomMediaSupportsRail(roomName)) {
    rail.classList.remove('hidden');
    roomMediaHeartbeat(true);
    roomMediaRequestState(roomName).catch(() => {});
  }
}

function roomMediaHandleWire(payload) {
  const room = String(payload?.room || '').trim();
  const station = roomMediaNormalizeStation(payload?.station || null);
  if (!room || !station) return null;
  roomMediaSetPayload(room, { room, station, stations: roomMediaStationsFor(room) }, { announce: false });
  return station;
}

socket.on('room_media_state_sync', (payload) => {
  const room = String(payload?.room || '').trim();
  if (!room) return;
  roomMediaSetPayload(room, payload, { announce: false });
});

function rbStartPolling() {
  if (!ROOM_BROWSER.started) return;
  if (ROOM_BROWSER._pollTimer) return;
  ROOM_BROWSER._pollTimer = setInterval(() => {
    if (typeof AUTH_EXPIRED !== 'undefined' && AUTH_EXPIRED) return;
    if (document.hidden) return;
    rbRefreshLists().catch(() => {});
  }, 60_000);
}

function rbStopPolling() {
  try {
    if (ROOM_BROWSER && ROOM_BROWSER._pollTimer) {
      clearInterval(ROOM_BROWSER._pollTimer);
      ROOM_BROWSER._pollTimer = null;
    }
  } catch {}
}


function ecNormalizeRoomNameForMatch(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function ecRoomLogHasVisibleMessages(log) {
  if (!log) return false;
  try {
    return !!log.querySelector('.ec-msgGroup, .ec-systemRow, .ec-globalAnnouncementRow, .ec-msgItem');
  } catch { return false; }
}

function ecRoomRemoveLiveOnlyState(viewEl) {
  const log = viewEl?._ym?.log || viewEl?.querySelector?.('#roomEmbedLog, .roomEmbedLog, .ym-log');
  if (!log) return;
  try { log.querySelectorAll('[data-ec-room-live-state="1"]').forEach((el) => el.remove()); } catch {}
}

function ecRoomRenderLiveOnlyState(viewEl, room, reason = '') {
  const log = viewEl?._ym?.log || viewEl?.querySelector?.('#roomEmbedLog, .roomEmbedLog, .ym-log');
  if (!log) return;
  if (ecRoomLogHasVisibleMessages(log)) return;
  let row = null;
  try { row = log.querySelector('[data-ec-room-live-state="1"]'); } catch {}
  if (!row) {
    row = document.createElement('div');
    row.className = 'ec-roomLiveState';
    row.dataset.ecRoomLiveState = '1';
    row.setAttribute('role', 'status');
    row.setAttribute('aria-live', 'polite');
    log.appendChild(row);
  }
  const label = ecNormalizeRoomNameForMatch(room || UIState?.currentRoom || UIState?.roomEmbedRoom || 'this room') || 'this room';
  row.textContent = `Live chat is ready in ${label}. New messages will appear here.`;
  if (reason) row.dataset.reason = String(reason || '');
}

function ecRoomFindVisibleMessage(viewEl, messageId) {
  const mid = String(messageId || '').trim();
  if (!mid || !viewEl) return null;
  try {
    if (typeof _findMsgEl === 'function') return _findMsgEl(viewEl, mid);
  } catch {}
  try { return viewEl.querySelector(`[data-msgid="${CSS.escape(mid)}"]`); } catch {}
  return null;
}

function ecRoomEnsureAckVisible(room, plaintext, ack, opts = {}) {
  try {
    if (!ack || !ack.success || ack.command || ack.shadowbanned) return;
    const messageId = String(ack.message_id || ack.messageId || '').trim();
    if (!messageId) return;
    const cleanRoom = ecNormalizeRoomNameForMatch(ack.room || room || UIState?.currentRoom || UIState?.roomEmbedRoom);
    if (!cleanRoom) return;
    const delayMs = Math.max(150, Number(opts.delayMs || 700));
    setTimeout(() => {
      try {
        const view = (typeof getActiveRoomView === 'function') ? getActiveRoomView(cleanRoom) : null;
        if (!view) return;
        if (ecRoomFindVisibleMessage(view, messageId)) return;
        if (typeof appendRoomMessage !== 'function') return;
        appendRoomMessage(view, {
          room: cleanRoom,
          message_id: messageId,
          username: String(currentUser || '').trim(),
          avatar_url: (UIState?.myProfile && (UIState.myProfile.avatar_url || UIState.myProfile.avatarUrl)) || '',
          message: String(plaintext ?? ''),
          encrypted: !!ack.encrypted,
          ts: Date.now(),
          message_kind: ack.message_kind || opts.messageKind || 'text',
          ttl_seconds: ack.ttl_seconds,
          expires_at: ack.expires_at,
          client_echo_fallback: true,
        });
        try { console.info('[Hui Chat] room message rendered from ACK fallback after broadcast was not visible', { room: cleanRoom, message_id: messageId }); } catch {}
      } catch (e) {
        try { console.warn('[Hui Chat] room ACK fallback render failed', e); } catch {}
      }
    }, delayMs);
  } catch {}
}

function openRoomEmbedded(room, opts = {}) {
  const preserveLog = !!opts.preserveLog;
  const previousRoom = String(UIState.roomEmbedRoom || '').trim();
  const nextRoom = String(room || '').trim();
  if (previousRoom && nextRoom && previousRoom !== nextRoom) {
    roomMediaStopLocalPlayback(previousRoom, { hideRail: true, heartbeat: true });
  }
  const pane = showRoomEmbed(room);
  if (!pane) return;

  // Bind emoticons picker for the embedded room composer
  if (pane._ym?.emojiBtn && pane._ym?.input) {
    bindEmojiButton(pane._ym.emojiBtn, pane._ym.input);
  }
  // Bind GIF picker for the embedded room composer
  if (pane._ym?.gifBtn) {
    pane._ym.gifBtn.onclick = () => {
      const roomNow = UIState.roomEmbedRoom || UIState.currentRoom;
      if (!roomNow) return toast("⚠️ Join a room first", "warn");
      openGifPicker(async (url) => {
        try {
          const clean = url;
          const msg = `gif:${clean}`;
          const res = await sendRoomTo(roomNow, msg);
          if (res?.success) ecRoomEnsureAckVisible(roomNow, msg, res, { messageKind: 'gif' });
          if (!res?.success) toast(`❌ ${res?.error || "Send failed"}`, "error");
        } catch (e) {
          console.error(e);
          toast(`❌ Send failed: ${e?.message || e}`, "error");
        }
      });
    };
  }

  // Reset the visible log for intentional joins / room switches.
  // On transient reconnect to the *same* room, preserve the current log and scroll position.
  if (pane._ym?.log && !preserveLog) resetChatLogState(pane._ym.log);
  if (pane._ym && !preserveLog) pane._ym.msgIndex = new Map();
  if (pane._ym?.log && !preserveLog) ecRoomRenderLiveOnlyState(pane, room, 'live_only_join');
  // Server emits join notifications (e.g., "user has entered room").

  // Wire send
  const sendFn = async () => {
    const input = pane._ym?.input || null;
    const sendBtn = pane._ym?.send || null;
    const msg = input?.value?.trim() || "";
    if (!msg) return;

    // Slash command: /invite <username>
    // Sends an invite notification to the target user without posting into chat.
    if (/^\/invite(\s|$)/i.test(msg)) {
      const rest = msg.replace(/^\/invite\s*/i, "").trim();
      const raw = (rest.split(/\s+/)[0] || "").trim();
      const u = raw.replace(/^@/, "");
      if (!u) {
        toast("Usage: /invite <username>", "info", 6000);
        return;
      }
      const optimistic = (typeof ecComposerBeginOptimisticSend === 'function')
        ? ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })
        : null;
      try {
        await apiJson("/api/rooms/invite", {
          method: "POST",
          body: JSON.stringify({ room, invitee: u })
        });
        toast(`✅ Invited ${u} to ${room}`, "ok");
        if (typeof ecRoomTypingStop === 'function') ecRoomTypingStop(room, input, { force: true });
        optimistic?.commit?.();
      } catch (e) {
        optimistic?.restore?.(e?.message || 'Invite failed');
        toast(`❌ ${e.message}`, "error");
      }
      return;
    }

    // Magnet paste → render as torrent card in room chat
    if (isMagnetText(msg)) {
      const pm = parseMagnet(msg);
      if (!pm) return toast("⚠️ Invalid magnet link", "warn");
      const optimistic = (typeof ecComposerBeginOptimisticSend === 'function')
        ? ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })
        : null;
      try {
        // IMPORTANT (UX): do NOT block sending on tracker scrape.
        // Torrent cards already self-refresh swarm stats asynchronously.
        const swarm = { seeds: null, leechers: null, completed: null };
        const wire = {
          _ec: "torrent",
          scope: "room",
          room,
          name: pm.name || "Magnet",
          infohash: pm.infohash,
          magnet: pm.magnet,
          total_size: 0,
          seeds: swarm.seeds,
          leechers: swarm.leechers,
          completed: swarm.completed,
          trackers: pm.trackers || [],
          declared_tracker_count: Number(pm.declared_tracker_count || 0),
          tracker_count: Number(pm.tracker_count || (Array.isArray(pm.trackers) ? pm.trackers.length : 0) || 0),
          tracker_source: pm.tracker_source || (pm.using_public_fallback_trackers ? "public_fallback" : "magnet"),
          using_public_fallback_trackers: !!pm.using_public_fallback_trackers,
          web_seeds: Array.isArray(pm.web_seeds) ? pm.web_seeds : [],
          web_seed_count: Number(pm.web_seed_count || 0),
          scrape_status: "pending",
          scrape_error: "",
          swarm_deferred: true,
          comment: "",
          created_by: "",
          creation_date: ""
        };
        sendRoomTo(room, JSON.stringify(wire)).then((res) => {
          if (res?.success) {
            ecRoomEnsureAckVisible(room, JSON.stringify(wire), res, { messageKind: 'torrent' });
            if (typeof ecRoomTypingStop === 'function') ecRoomTypingStop(room, input, { force: true });
            optimistic?.commit?.();
          } else {
            optimistic?.restore?.(res?.error || "Send failed");
            toast(`❌ ${res?.error || "Send failed"}`, "error");
          }
        }).catch((e) => {
          optimistic?.restore?.(e?.message || 'Send failed');
          console.error(e);
          toast(`❌ Send failed: ${e?.message || e}`, "error");
        });
      } catch (e) {
        optimistic?.restore?.(e?.message || 'Could not send magnet');
        console.error(e);
        toast("❌ Could not send magnet", "error");
      }
      return;
    }

    const optimistic = (typeof ecComposerBeginOptimisticSend === 'function')
      ? ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })
      : null;
    sendRoomTo(room, msg).then((res) => {
      if (res?.success) {
        ecRoomEnsureAckVisible(room, msg, res, { messageKind: res?.message_kind || 'text' });
        if (typeof ecRoomTypingStop === 'function') ecRoomTypingStop(room, input, { force: true });
        // Prefer the server broadcast because it has the authoritative message_id;
        // if Firefox/extension/socket timing hides that broadcast, the ACK fallback
        // above renders the same message once.
        optimistic?.commit?.();
      } else {
        optimistic?.restore?.(res?.error || "Send failed");
        toast(`❌ ${res?.error || "Send failed"}`, "error");
      }
    }).catch((e) => {
      optimistic?.restore?.(e?.message || 'Send failed');
      console.error(e);
      toast(`❌ Send failed: ${e?.message || e}`, "error");
    });
  };

  if (pane._ym?.send) pane._ym.send.onclick = sendFn;
  if (pane._ym?.input && typeof ecBindRoomTypingInput === 'function') ecBindRoomTypingInput(room, pane._ym.input);
  if (pane._ym?.input) {
    pane._ym.input.onkeydown = (e) => {
      const shouldSend = (typeof ecIsPlainEnterToSend === "function")
        ? ecIsPlainEnterToSend(e)
        : (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && !e.isComposing);
      if (shouldSend) {
        e.preventDefault();
        sendFn();
      }
    };

    // Torrent share (room)
    if (pane._ym?.torrentBtn && pane._ym?.torrentInput) {
      pane._ym.torrentBtn.onclick = () => pane._ym.torrentInput.click();
      pane._ym.torrentInput.onchange = async () => {
        const f = pane._ym.torrentInput.files && pane._ym.torrentInput.files[0];
        pane._ym.torrentInput.value = "";
        if (!f) return;
        if (!isTorrentName(f.name)) {
          toast("⚠️ Room share currently supports .torrent files only", "warn");
          return;
        }
        if (Number(f.size || 0) > MAX_TORRENT_UPLOAD_BYTES) {
          toast(`❌ Torrent file too large (max ${humanBytes(MAX_TORRENT_UPLOAD_BYTES)})`, "error");
          return;
        }
        try {
          // Parse + scrape
          const ab = await f.arrayBuffer();
          const u8 = new Uint8Array(ab);
          const parsed = parseTorrentBytes(u8);
          const infohash = parsed.infoSlice ? await sha1HexFromBytes(parsed.infoSlice) : "";
          const magnet = infohash ? buildMagnet(infohash, parsed.name, parsed.trackers) : "";
          // IMPORTANT (UX): do NOT block sending on tracker scrape.
          // Torrent cards already self-refresh swarm stats asynchronously.
          const swarm = { seeds: null, leechers: null, completed: null };

          // Upload .torrent so room members can download it
          const fd = new FormData();
          fd.append("file", new Blob([u8], { type: "application/x-bittorrent" }), f.name);
          fd.append("scope", "room");
          fd.append("room", room);
          // UX: upload/save only. Do not let tracker/DHT lookups delay the room post.
          // The rendered torrent card refreshes swarm stats asynchronously after it appears.
          fd.append("defer_swarm", "1");
          const upResp = await fetchWithAuth("/api/torrents/upload", { method: "POST", body: fd });
          const upData = (typeof ecReadApiJson === 'function') ? await ecReadApiJson(upResp, null) : await upResp.json().catch(() => null);
          if (!upResp || !upResp.ok || !upData?.success) {
            const msg = (typeof ecApiErrorMessage === 'function') ? ecApiErrorMessage(upResp, upData, 'Upload failed') : (upData?.error || "Upload failed");
            throw new Error(msg);
          }

          const torrent_id = upData.torrent_id;
          const download_url = upData.download_url || `/api/torrents/${encodeURIComponent(torrent_id)}/download`;

          const wire = {
            _ec: "torrent",
            scope: "room",
            room,
            torrent_id,
            download_url,
            file_name: upData.file_name || f.name,
            name: upData.name || parsed.name || f.name,
            infohash: upData.infohash || upData.infohash_hex || infohash,
            magnet,
            total_size: Number(upData.total_size || parsed.total_size || 0) || 0,
            seeds: (upData.seeds === undefined ? swarm.seeds : upData.seeds),
            leechers: (upData.leechers === undefined ? swarm.leechers : upData.leechers),
            completed: (upData.completed === undefined ? swarm.completed : upData.completed),
            trackers: Array.isArray(upData.trackers) && upData.trackers.length ? upData.trackers : (parsed.trackers || []),
            tracker_count: Number(upData.tracker_count || (Array.isArray(upData.trackers) ? upData.trackers.length : (parsed.trackers || []).length) || 0),
            declared_tracker_count: Number(upData.declared_tracker_count ?? parsed.declared_tracker_count ?? 0) || 0,
            tracker_source: upData.tracker_source || parsed.tracker_source || "torrent",
            using_public_fallback_trackers: !!(upData.using_public_fallback_trackers || parsed.using_public_fallback_trackers),
            web_seeds: Array.isArray(upData.web_seeds) && upData.web_seeds.length ? upData.web_seeds : (parsed.web_seeds || []),
            web_seed_count: Number(upData.web_seed_count || parsed.web_seed_count || 0),
            scrape_status: upData.scrape_status || upData.swarm_status || "pending",
            scrape_error: upData.scrape_error || "",
            swarm_deferred: true,
            trackers_tried: Number(upData.trackers_tried || 0),
            dht_queries: Number(upData.dht_queries || 0),
            dht_peers_seen: Number(upData.dht_peers_seen || 0),
            comment: upData.comment || parsed.comment || "",
            created_by: upData.created_by || parsed.created_by || "",
            creation_date: upData.creation_date || parsed.creation_date || ""
          };

          sendRoomTo(room, JSON.stringify(wire)).then((res) => {
            if (res?.success) ecRoomEnsureAckVisible(room, JSON.stringify(wire), res, { messageKind: 'torrent' });
            if (!res?.success) toast(`❌ ${res?.error || "Send failed"}`, "error");
          });
          toast("🧲 Torrent posted. Looking up seeds/leechers…", "ok", 2500);
        } catch (e) {
          console.error(e);
          toast(`❌ Torrent share failed: ${e?.message || e}`, "error");
        }
      };
    }

    pane._ym.input.focus();
  }

  // Leave button
  const btnLeave = $("btnRoomEmbedLeave");
  if (btnLeave) btnLeave.onclick = () => leaveRoom();

  // Invite button — friend/user invite without needing slash commands.
  const btnInvite = $("btnRoomEmbedInvite");
  if (btnInvite) {
    btnInvite.onclick = async () => {
      try {
        if (typeof ecInviteUserToCurrentRoom === "function") await ecInviteUserToCurrentRoom("");
        else toast("Invite command is not ready yet.", "warn");
      } catch (e) {
        toast(`❌ Invite failed: ${e?.message || e}`, "error", 7000);
      }
    };
  }

  // Voice (room) controls — one-button toggle
  const btnVoice = $("btnRoomEmbedVoice");
  try { voiceWireRoomTalkControls(); } catch (e) {}
  if (btnVoice) {
    btnVoice.onclick = async () => {
      try {
        if (ecMediaModeReady()) {
          await ecMediaToggleVoiceForRoom(room);
          return;
        }
        if (typeof voiceActionBusy === 'function' && voiceActionBusy('room', room)) return;
        const runLegacyRoomVoice = async () => {
          if (VOICE_STATE.room.joined && VOICE_STATE.room.name === room) {
            VOICE_STATE.room.wantRoomVoice = false;
            voiceLeaveRoom("Voice disabled", true);
            voiceUpdateRoomVoiceButton();
            return;
          }
          VOICE_STATE.room.wantRoomVoice = true;
          voiceSetMute(false);
          const res = await voiceJoinRoom(room, { silent: true });
          if (!res?.success) {
            if (res?.error_code === "voice_room_full") voiceShowRoomFull(room, res);
            else toast(`❌ ${res?.error || "Voice join failed"}`, "error");
          }
          voiceUpdateRoomVoiceButton();
        };
        if (typeof voiceWithBusy === 'function') await voiceWithBusy('room', room, runLegacyRoomVoice);
        else await runLegacyRoomVoice();
      } catch (e) {
        console.error(e);
        toast(`❌ Voice error: ${e?.message || e}`, "error");
      }
    };

    // Right-click toggles mic mute (global)
    btnVoice.oncontextmenu = (ev) => {
      try {
        ev.preventDefault();
        if (ecMediaModeReady()) {
          ecMediaToggleMic();
          return false;
        }
        if (!VOICE_STATE.micStream) return false;
        const muted = !VOICE_STATE.micMuted;
        voiceSetMute(muted);
        voiceRoomUi({ muteLabel: muted ? "Unmute" : "Mute" });
        voiceUpdateRoomVoiceButton();
        toast(muted ? "🔇 Mic muted" : "🎤 Mic unmuted", "info");
      } catch (e) {}
      return false;
    };
  }

  // Webcam has its own top-level button in enhanced media mode.
  const btnCamTop = $("btnRoomEmbedCam");
  if (btnCamTop) {
    btnCamTop.setAttribute('aria-label', 'Turn on your webcam');
    btnCamTop.onclick = async () => {
      try {
        if (!ecMediaModeReady()) return toast("📷 Webcam is not available in this room", "warn");
        if (typeof ecMediaWebcamAvailable === "function" && !ecMediaWebcamAvailable()) {
          const why = (typeof ecMediaWebcamUnavailableReason === "function") ? ecMediaWebcamUnavailableReason() : "Webcam is not available.";
          return toast(`📷 ${why}`, "warn", 6500);
        }
        await ecMediaToggleCamForRoom(room);
      } catch (e) {
        console.warn("Webcam could not start", e);
        const msg = e && e.message ? e.message : String(e || "Webcam could not start.");
        toast(`📷 ${msg}`, "warn", 7500);
      }
    };
  }

  // Voice and webcam are intentionally separate in the room GUI.

  // Hide legacy room voice bar buttons until an active media session needs them.
  const bVJoin = $("btnRoomEmbedVoiceJoin");
  if (bVJoin) bVJoin.style.display = "none";
  const bVLeave = $("btnRoomEmbedVoiceLeave");
  if (bVLeave) bVLeave.style.display = "none";
  const bVMute = $("btnRoomEmbedVoiceMute");
  if (bVMute) bVMute.style.display = "none";
  const bVCam = $("btnRoomEmbedVoiceCam");
  if (bVCam) bVCam.style.display = "none";
// Default voice UI for this room
  voiceRoomUi({ show: false, statusText: "Not connected", joinVisible: false, leaveVisible: false, muteVisible: false, camVisible: false, muteLabel: "Mute" });
  voiceUpdateRoomVoiceButton();
  try { voiceUpdateRoomCamButton(); } catch {}

  roomMediaOpenRail(room);

  // Apply any known room policy state (locked/read-only/slowmode)
  try { applyRoomPolicyToView(room, pane, getRoomPolicy(room)); } catch {}
  try { if (typeof ecRoomModeratorPanelSync === 'function') ecRoomModeratorPanelSync(room); } catch {}

  return pane;
}

function getActiveRoomView(room) {
  const target = (typeof ecNormalizeRoomNameForMatch === 'function') ? ecNormalizeRoomNameForMatch(room) : String(room || '').trim();
  const embedRoom = (typeof ecNormalizeRoomNameForMatch === 'function') ? ecNormalizeRoomNameForMatch(UIState.roomEmbedRoom) : String(UIState.roomEmbedRoom || '').trim();
  const currentRoom = (typeof ecNormalizeRoomNameForMatch === 'function') ? ecNormalizeRoomNameForMatch(UIState.currentRoom) : String(UIState.currentRoom || '').trim();
  if (target && (embedRoom === target || currentRoom === target)) {
    const embed = getRoomEmbedEl();
    if (embed) return embed;
  }
  const win = UIState.windows.get("room:" + target) || UIState.windows.get("room:" + room);
  return win || null;
}
