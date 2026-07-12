const EC_PROFILE_RELATIONSHIP_OPTIONS = [
  { value: '', label: 'Prefer not to say' },
  { value: 'Single', label: 'Single' },
  { value: 'In a relationship', label: 'In a relationship' },
  { value: 'Married', label: 'Married' },
  { value: 'Engaged', label: 'Engaged' },
  { value: "It's complicated", label: "It's complicated" },
  { value: 'Open', label: 'Open' },
  { value: 'Private', label: 'Private' },
];

const EC_PROFILE_VISIBILITY_OPTIONS = [
  { value: 'everyone', label: 'Everyone' },
  { value: 'friends', label: 'Friends only' },
  { value: 'room_members', label: 'Room members only' },
  { value: 'nobody', label: 'Nobody' },
];

function ecProfileEditorDomEl(tag, attrs = {}, children = []) {
  const node = document.createElement(tag || 'div');
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (value === false || value === null || value === undefined) return;
    if (key === 'className') node.className = String(value || '');
    else if (key === 'text') node.textContent = String(value || '');
    else if (key === 'dataset' && value && typeof value === 'object') {
      Object.entries(value).forEach(([dKey, dValue]) => {
        if (dValue !== null && dValue !== undefined && dValue !== false) node.dataset[dKey] = String(dValue);
      });
    } else if (key === 'style' && value && typeof value === 'object') {
      Object.entries(value).forEach(([styleKey, styleValue]) => {
        if (styleValue !== null && styleValue !== undefined) node.style[styleKey] = String(styleValue);
      });
    } else if (key === 'hidden' || key === 'checked' || key === 'disabled' || key === 'readOnly') {
      node[key] = !!value;
    } else if (key === 'htmlFor') {
      node.htmlFor = String(value || '');
    } else if (key === 'value') {
      node.value = String(value ?? '');
    } else if (key === 'textContent') {
      node.textContent = String(value ?? '');
    } else if (key in node && !String(key).startsWith('aria') && !String(key).startsWith('data')) {
      try { node[key] = value; } catch { node.setAttribute(key, String(value)); }
    } else {
      node.setAttribute(key, value === true ? '' : String(value));
    }
  });
  const appendOne = (child) => {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(appendOne); return; }
    if (child instanceof Node) { node.appendChild(child); return; }
    node.appendChild(document.createTextNode(String(child)));
  };
  appendOne(children);
  return node;
}

function ecProfileEditorClearNode(node) {
  if (!node) return;
  while (node.firstChild) node.removeChild(node.firstChild);
}

function ecProfileEditorAvatarStub(username) {
  return ecProfileEditorDomEl('div', { className: 'ecAvatarStub', text: dockInitials(username || currentUser || 'U') });
}

function ecProfileEditorButton(id, label, className = 'miniBtn secondary') {
  return ecProfileEditorDomEl('button', { id, className, type: 'button', text: label });
}

function ecProfileEditorSelect(id, options, selectedValue, className = 'ecProfileSelect') {
  const sel = ecProfileEditorDomEl('select', { id, className });
  (options || []).forEach((opt) => {
    const value = String(opt?.value ?? '');
    const option = ecProfileEditorDomEl('option', { value, text: String(opt?.label ?? value) });
    option.selected = value === String(selectedValue || '');
    sel.appendChild(option);
  });
  return sel;
}

function ecProfileEditorField(labelText, control, metaText = '') {
  const field = ecProfileEditorDomEl('div', { className: 'ecProfileField' });
  field.appendChild(ecProfileEditorDomEl('span', { className: 'ecProfileFieldLabel', text: labelText }));
  if (control) field.appendChild(control);
  if (metaText) field.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted', text: metaText }));
  return field;
}

function ecProfileEditorSection(title, children = [], extraClass = '') {
  const section = ecProfileEditorDomEl('section', { className: `ecProfileSectionCard ecProfileSectionCardPremium${extraClass ? ` ${extraClass}` : ''}` });
  section.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileSectionHeader', text: title }));
  (children || []).forEach((child) => section.appendChild(child));
  return section;
}

function ecProfileEditorQuickFact(id, value, label) {
  return ecProfileEditorDomEl('div', { className: 'ecProfileQuickFact' }, [
    ecProfileEditorDomEl('strong', { id, text: value }),
    ecProfileEditorDomEl('span', { text: label }),
  ]);
}

function ecProfileBuildMyProfileEditorNode(ctx = {}) {
  const {
    p = {}, username = 'User', avatarUrl = '', bannerUrl = '', profileAccent = '#6f7cff', bio = '',
    relationshipStatus = '', relationshipVisibility = 'friends', ageValue = '', ageVisibility = 'friends',
    locationText = '', locationVisibility = 'friends', interests = '', favoriteMusic = '', favoriteMovies = '',
    favoriteGames = '', websiteUrl = '', shareRecentRooms = false, recentRoomsVisibility = 'friends',
    profilePostDefaultVisibility = 'friends', profileNotificationSettings = {}, joined = '',
  } = ctx;
  const notifySettings = {
    notify_likes: profileNotificationSettings.notify_likes !== false,
    notify_comments: profileNotificationSettings.notify_comments !== false,
    notify_admin_notices: profileNotificationSettings.notify_admin_notices !== false,
    notify_report_updates: profileNotificationSettings.notify_report_updates !== false,
    notify_profile_views: profileNotificationSettings.notify_profile_views === true,
    notify_friend_posts: profileNotificationSettings.notify_friend_posts !== false,
  };
  const serverName = (typeof SERVER_NAME !== 'undefined' && SERVER_NAME) ? String(SERVER_NAME) : 'Hui Chat';
  const card = ecProfileEditorDomEl('div', { className: 'ecProfileCard ecProfileEditorCard ecProfilePublicCard ecProfilePremiumCard ecProfilePageCard ecProfileEditorLikeProfile' });
  card.style.setProperty('--ec-profile-accent', profileAccent);

  const bannerCssUrl = ecCssUrl(bannerUrl, { allowRelative: true, allowExternal: true });
  const hero = ecProfileEditorDomEl('div', { id: 'myProfileHeroPreview', className: 'ecProfileHero ecProfileHeroPremium ecProfileHeroFacebook' });
  hero.style.backgroundImage = bannerCssUrl
    ? `linear-gradient(180deg, rgba(15,23,42,.20), rgba(15,23,42,.72)), ${bannerCssUrl}`
    : `linear-gradient(135deg, ${profileAccent}, rgba(15,23,42,.82))`;
  hero.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileHeroGlow' }));
  const overlay = ecProfileEditorDomEl('div', { className: 'ecProfileHeroOverlay' });
  const top = ecProfileEditorDomEl('div', { className: 'ecProfileTop ecProfileTopPremium ecProfileTopFacebook' });
  const avatar = ecProfileEditorDomEl('div', { id: 'myProfileAvatarPreview', className: 'ecProfileAvatar ecProfileAvatarLarge ecProfileAvatarFacebook ecProfileAvatarPreview' });
  const safeAvatar = ecNormalizeSafeUrl(avatarUrl, { allowRelative: true, allowExternal: true });
  if (safeAvatar) {
    avatar.appendChild(ecProfileEditorDomEl('img', { src: safeAvatar, alt: 'avatar', referrerPolicy: 'no-referrer' }));
  } else {
    avatar.appendChild(ecProfileEditorAvatarStub(username));
  }
  top.appendChild(avatar);

  const topText = ecProfileEditorDomEl('div', { className: 'ecProfileTopText ecProfileTopTextFacebook' });
  topText.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileTitle ecProfileTitleLarge', text: 'Your profile' }));
  const presenceClass = p.online ? (p.presence === 'busy' ? 'busy' : (p.presence === 'away' ? 'away' : 'online')) : 'offline';
  const meta = ecProfileEditorDomEl('div', { className: 'ecProfileMeta' }, [
    ecProfileEditorDomEl('span', { className: `presDot ${presenceClass}` }),
    ecProfileEditorDomEl('span', { text: humanPresenceText(!!p.online, p.presence) }),
  ]);
  if (p.custom_status) meta.appendChild(ecProfileEditorDomEl('span', { className: 'muted', text: `· ${String(p.custom_status)}` }));
  topText.appendChild(meta);
  if (joined) topText.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted', text: `Joined ${serverName} ${joined}` }));
  topText.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted', text: 'You are editing the same hero, summary, and section layout people see on your public profile page.' }));
  topText.appendChild(ecProfileEditorDomEl('div', { id: 'myProfileHeroChips', className: 'ecProfileChips ecProfileChipsHero' }));
  top.appendChild(topText);
  top.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileHeroActions' }, [
    ecProfileEditorButton('btnHeroChooseAvatar', '🖼 Avatar'),
    ecProfileEditorButton('btnHeroChooseBanner', '🖼 Banner'),
    ecProfileEditorButton('btnHeroViewProfile', '👁 View profile'),
  ]));
  overlay.appendChild(top);
  hero.appendChild(overlay);
  card.appendChild(hero);

  const bioInput = ecProfileEditorDomEl('textarea', {
    id: 'myProfileBio', className: 'ecProfileEditorBioInput', maxLength: 500,
    placeholder: 'Tell people a little about yourself.', value: bio,
  });
  const summaryLeft = ecProfileEditorDomEl('div', {}, [
    bioInput,
    ecProfileEditorDomEl('div', { className: 'ecProfileFieldMetaRow ecProfileEditorBioMeta' }, [
      ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted', text: 'Use this top summary to shape the first impression people get on your page.' }),
      ecProfileEditorDomEl('div', { id: 'myProfileBioCount', className: 'ecProfileCharCount muted', text: `${String(bio).length}/500` }),
    ]),
  ]);
  const quickFacts = ecProfileEditorDomEl('div', { className: 'ecProfileQuickFacts ecProfileQuickFactsFacebook ecProfileEditorQuickFacts' }, [
    ecProfileEditorQuickFact('myProfileQuickDraftState', 'Saved', 'Draft'),
    ecProfileEditorQuickFact('myProfileQuickRecentRooms', shareRecentRooms ? 'On' : 'Off', 'Recent rooms'),
    ecProfileEditorQuickFact('myProfileQuickWebsite', websiteUrl ? 'Added' : 'None', 'Website'),
    ecProfileEditorQuickFact('myProfileQuickAccent', profileAccent, 'Accent'),
  ]);
  card.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileFacebookBar' }, [
    ecProfileEditorDomEl('div', { className: 'ecProfileSummaryRow ecProfileSummaryRowFacebook' }, [summaryLeft, quickFacts]),
  ]));

  card.appendChild(ecProfileEditorDomEl('div', { className: 'ecProfileStickyNav ecProfileEditorStickyNav ecProfileActions ecProfileEditorActions' }, [
    ecProfileEditorDomEl('div', { className: 'ecProfileDraftStatusRow' }, [
      ecProfileEditorDomEl('div', { id: 'myProfileDraftStatus', className: 'ecProfileDraftStatus', text: 'Saved' }),
      ecProfileEditorDomEl('div', { id: 'myProfileDraftHint', className: 'ecProfileMeta muted', text: 'No unsaved profile changes.' }),
    ]),
    ecProfileEditorDomEl('div', { className: 'ecProfileActionButtons' }, [
      ecProfileEditorDomEl('button', { id: 'btnSaveMyProfile', className: 'miniBtn', type: 'button', disabled: true, text: '💾 Save profile' }),
      ecProfileEditorDomEl('button', { id: 'btnResetMyProfileDraft', className: 'miniBtn secondary', type: 'button', disabled: true, text: '↺ Revert changes' }),
      ecProfileEditorButton('btnClearMyAvatar', '🧽 Clear avatar'),
      ecProfileEditorButton('btnClearMyBanner', '🪄 Clear banner'),
      ecProfileEditorButton('btnRefreshMyProfile', '↻ Reload'),
      ecProfileEditorButton('btnOpenMyPublicProfile', '👁 View profile page'),
    ]),
  ]));

  const leftRail = ecProfileEditorDomEl('aside', { className: 'ecProfileRail ecProfileRailLeft' });
  leftRail.appendChild(ecProfileEditorSection('Identity & style', [
    ecProfileEditorField('Username', ecProfileEditorDomEl('input', { className: 'ecProfileInput', type: 'text', value: username, readOnly: true })),
    ecProfileEditorField('Website / social link', ecProfileEditorDomEl('input', { id: 'myProfileWebsiteUrl', className: 'ecProfileInput', type: 'url', placeholder: 'https://example.com', value: websiteUrl }), 'Direct http/https link only.'),
    ecProfileEditorField('Accent color', ecProfileEditorDomEl('input', { id: 'myProfileAccent', className: 'ecProfileColorInput', type: 'color', value: profileAccent }), 'Used when no banner image is set.'),
  ]));
  const privacyGrid = ecProfileEditorDomEl('div', { className: 'ecProfileGrid ecProfileGridTight' }, [
    ecProfileEditorField('Who can see relationship status?', ecProfileEditorSelect('myProfileRelationshipVisibility', EC_PROFILE_VISIBILITY_OPTIONS, relationshipVisibility)),
    ecProfileEditorField('Who can see age?', ecProfileEditorSelect('myProfileAgeVisibility', EC_PROFILE_VISIBILITY_OPTIONS, ageVisibility)),
  ]);
  const toggleLabel = ecProfileEditorDomEl('label', { className: 'ecProfileToggleRow', htmlFor: 'myProfileShareRecentRooms' }, [
    ecProfileEditorDomEl('span', { text: 'Share my last 3 joined rooms on my profile' }),
    ecProfileEditorDomEl('input', { id: 'myProfileShareRecentRooms', type: 'checkbox', checked: shareRecentRooms }),
  ]);
  leftRail.appendChild(ecProfileEditorSection('Privacy', [
    privacyGrid,
    ecProfileEditorField('Who can see location?', ecProfileEditorSelect('myProfileLocationVisibility', EC_PROFILE_VISIBILITY_OPTIONS, locationVisibility)),
    ecProfileEditorDomEl('div', { className: 'ecProfileField' }, [
      toggleLabel,
      ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted', text: 'Shows up to 3 recently joined rooms, including your current room when you are in one.' }),
    ]),
    ecProfileEditorField('Who can see recent rooms?', ecProfileEditorSelect('myProfileRecentRoomsVisibility', EC_PROFILE_VISIBILITY_OPTIONS, recentRoomsVisibility)),
    ecProfileEditorField('Default profile post visibility', ecProfileEditorSelect('myProfilePostDefaultVisibility', EC_PROFILE_VISIBILITY_OPTIONS, profilePostDefaultVisibility), 'Used when you create a profile post without picking a different visibility.'),
  ]));
  const notifyRows = [
    ['myProfileNotifyLikes', 'Likes on my profile posts', 'notify_likes'],
    ['myProfileNotifyComments', 'Comments on my profile posts', 'notify_comments'],
    ['myProfileNotifyAdmin', 'Admin/moderation profile notices', 'notify_admin_notices'],
    ['myProfileNotifyReports', 'Report review updates', 'notify_report_updates'],
    ['myProfileNotifyViews', 'Profile view notifications', 'notify_profile_views'],
    ['myProfileNotifyFriendPosts', 'Friend profile post activity', 'notify_friend_posts'],
  ].map(([id, label, key]) => ecProfileEditorDomEl('label', { className: 'ecProfileToggleRow', htmlFor: id }, [
    ecProfileEditorDomEl('span', { text: label }),
    ecProfileEditorDomEl('input', { id, type: 'checkbox', checked: !!notifySettings[key], dataset: { profileNotifyKey: key } }),
  ]));
  leftRail.appendChild(ecProfileEditorSection('Notifications', [
    ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted', text: 'Choose which profile/social alerts Hui Chat should send you.' }),
    ...notifyRows,
    ecProfileEditorDomEl('div', { className: 'ecProfileFieldMetaRow' }, [
      ecProfileEditorDomEl('button', { id: 'btnSaveProfileNotifications', className: 'miniBtn secondary', type: 'button', text: 'Save notification settings' }),
      ecProfileEditorDomEl('div', { id: 'myProfileNotificationStatus', className: 'ecProfileMeta muted', text: 'Notification settings load separately from the profile draft.' }),
    ]),
  ]));

  const main = ecProfileEditorDomEl('main', { className: 'ecProfileMainColumn' });
  const introGrid = ecProfileEditorDomEl('div', { className: 'ecProfileGrid ecProfileGridTight' }, [
    ecProfileEditorField('Relationship status', ecProfileEditorSelect('myProfileRelationshipStatus', EC_PROFILE_RELATIONSHIP_OPTIONS, relationshipStatus)),
    ecProfileEditorField('Age', ecProfileEditorDomEl('input', { id: 'myProfileAge', className: 'ecProfileInput', type: 'number', min: '1', max: '120', inputMode: 'numeric', placeholder: 'Optional', value: ageValue })),
  ]);
  const interestsInput = ecProfileEditorDomEl('textarea', { id: 'myProfileInterests', className: 'ecProfileTextarea ecProfileTextareaCompact', maxLength: 240, placeholder: 'Music, games, shows, hobbies, collecting, outdoors…', value: interests });
  main.appendChild(ecProfileEditorSection('Intro', [
    introGrid,
    ecProfileEditorField('Location', ecProfileEditorDomEl('input', { id: 'myProfileLocation', className: 'ecProfileInput', type: 'text', maxLength: 80, placeholder: 'City, state, region, or country', value: locationText })),
    ecProfileEditorDomEl('div', { className: 'ecProfileField' }, [
      ecProfileEditorDomEl('span', { className: 'ecProfileFieldLabel', text: 'Interests' }),
      interestsInput,
      ecProfileEditorDomEl('div', { className: 'ecProfileFieldMetaRow' }, [
        ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted', text: 'A short list or sentence works best here.' }),
        ecProfileEditorDomEl('div', { id: 'myProfileInterestsCount', className: 'ecProfileCharCount muted', text: `${String(interests).length}/240` }),
      ]),
    ]),
  ]));
  main.appendChild(ecProfileEditorSection('Favorites', [
    ecProfileEditorDomEl('div', { className: 'ecProfileGrid ecProfileGridTight' }, [
      ecProfileEditorField('Favorite music', ecProfileEditorDomEl('input', { id: 'myProfileFavoriteMusic', className: 'ecProfileInput', type: 'text', maxLength: 120, placeholder: 'Artists, albums, songs, genres', value: favoriteMusic })),
      ecProfileEditorField('Favorite movies / shows', ecProfileEditorDomEl('input', { id: 'myProfileFavoriteMovies', className: 'ecProfileInput', type: 'text', maxLength: 120, placeholder: 'Movies, series, anime, documentaries', value: favoriteMovies })),
    ]),
    ecProfileEditorField('Favorite games', ecProfileEditorDomEl('input', { id: 'myProfileFavoriteGames', className: 'ecProfileInput', type: 'text', maxLength: 120, placeholder: 'Games, genres, platforms', value: favoriteGames })),
  ]));
  main.appendChild(ecProfileEditorSection('Photo & banner links', [
    ecProfileEditorDomEl('div', { className: 'ecProfileGrid' }, [
      ecProfileEditorField('Avatar image URL', ecProfileEditorDomEl('input', { id: 'myProfileAvatarUrl', className: 'ecProfileInput', type: 'url', placeholder: 'https://example.com/avatar.jpg or use the built-in picker', value: avatarUrl }), 'Upload an image, choose a DiceBear avatar, or paste a direct image URL. SVG upload only appears when the server allows it.'),
      ecProfileEditorField('Banner image URL', ecProfileEditorDomEl('input', { id: 'myProfileBannerUrl', className: 'ecProfileInput', type: 'url', placeholder: 'https://example.com/banner.jpg', value: bannerUrl }), 'Optional top banner image for your public profile card.'),
    ]),
  ]));

  const rightRail = ecProfileEditorDomEl('aside', { className: 'ecProfileRail ecProfileRailRight' });
  const avatarFile = ecProfileEditorDomEl('input', { id: 'myProfileAvatarFile', className: 'ecProfileUploadInput', type: 'file', accept: ecProfileAvatarAcceptMimeTypes() });
  const bannerFile = ecProfileEditorDomEl('input', { id: 'myProfileBannerFile', className: 'ecProfileUploadInput', type: 'file', accept: ecProfileBannerAcceptMimeTypes() });
  rightRail.appendChild(ecProfileEditorSection('Upload media', [
    ecProfileEditorDomEl('div', { className: 'ecProfileField' }, [
      ecProfileEditorDomEl('span', { className: 'ecProfileFieldLabel', text: 'Upload avatar image' }),
      ecProfileEditorDomEl('div', { className: 'ecProfileUploadRow' }, [avatarFile, ecProfileEditorButton('btnUploadMyAvatar', '📤 Upload avatar')]),
      ecProfileEditorDomEl('div', { id: 'myProfileAvatarDropzone', className: 'ecProfileDropzone', role: 'button', tabIndex: 0, 'aria-label': 'Drag and drop avatar upload area' }, [
        ecProfileEditorDomEl('div', { className: 'ecProfileDropTitle', text: 'Drop an avatar image here' }),
        ecProfileEditorDomEl('div', { className: 'ecProfileDropMeta muted', text: 'or click this area to browse for a file' }),
      ]),
      ecProfileEditorDomEl('div', { id: 'myProfileUploadStatus', className: 'ecProfileUploadStatus' }),
    ]),
    ecProfileEditorDomEl('div', { className: 'ecProfileField' }, [
      ecProfileEditorDomEl('span', { className: 'ecProfileFieldLabel', text: 'Upload banner image' }),
      ecProfileEditorDomEl('div', { className: 'ecProfileUploadRow' }, [bannerFile, ecProfileEditorButton('btnUploadMyBanner', '🖼 Upload banner')]),
      ecProfileEditorDomEl('div', { id: 'myProfileBannerDropzone', className: 'ecProfileDropzone', role: 'button', tabIndex: 0, 'aria-label': 'Drag and drop banner upload area' }, [
        ecProfileEditorDomEl('div', { className: 'ecProfileDropTitle', text: 'Drop a banner image here' }),
        ecProfileEditorDomEl('div', { className: 'ecProfileDropMeta muted', text: 'Wide PNG, JPG, GIF, WEBP, BMP, or ICO images work best, or click this area to browse' }),
      ]),
      ecProfileEditorDomEl('div', { id: 'myProfileBannerUploadStatus', className: 'ecProfileUploadStatus' }),
    ]),
  ], 'ecProfileEditorMediaSection'));
  const initialDiceBear = detectAvatarPresetSelection(avatarUrl) || {};
  const diceSeed = initialDiceBear.seed || username || currentUser || 'hui';
  const diceBg = `#${normalizeDiceBearColor(initialDiceBear.backgroundColor || DICEBEAR_DEFAULT_BG)}`;
  const diceRadius = String(normalizeDiceBearBorderRadius(initialDiceBear.borderRadius ?? 50));
  const diceFlip = !!initialDiceBear.flip;
  const dicebearSeedInput = ecProfileEditorDomEl('input', { id: 'myProfileDicebearSeed', className: 'ecProfileInput', type: 'text', maxLength: 96, value: diceSeed, placeholder: 'Type a name, nickname, or random seed' });
  const dicebearBgInput = ecProfileEditorDomEl('input', { id: 'myProfileDicebearBg', className: 'ecProfileColorInput', type: 'color', value: diceBg });
  const dicebearRadiusSelect = ecProfileEditorSelect('myProfileDicebearRadius', [
    { value: '0', label: 'Square' },
    { value: '12', label: 'Slightly rounded' },
    { value: '25', label: 'Rounded' },
    { value: '50', label: 'Circle' },
  ], diceRadius);
  const dicebearFlipLabel = ecProfileEditorDomEl('label', { className: 'ecProfileToggleRow ecDicebearFlipRow', htmlFor: 'myProfileDicebearFlip' }, [
    ecProfileEditorDomEl('span', { text: 'Flip avatar' }),
    ecProfileEditorDomEl('input', { id: 'myProfileDicebearFlip', type: 'checkbox', checked: diceFlip }),
  ]);
  rightRail.appendChild(ecProfileEditorSection('DiceBear avatar builder', [
    ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted ecDicebearIntro', text: 'Build a profile avatar by picking a style, seed, background, and shape. Click a preview to try it, then apply it.' }),
    ecProfileEditorDomEl('div', { className: 'ecProfilePresetBar' }, [
      ecProfileEditorDomEl('div', { id: 'myProfilePresetStyleTabs', className: 'ecProfilePresetStyleTabs', 'aria-label': 'DiceBear avatar styles' }),
      ecProfileEditorButton('btnShuffleAvatarPresets', '🎲 More'),
    ]),
    ecProfileEditorDomEl('div', { className: 'ecDicebearControls' }, [
      ecProfileEditorField('Seed / name', dicebearSeedInput, 'Same seed + same style creates the same avatar again.'),
      ecProfileEditorDomEl('div', { className: 'ecDicebearControlButtons' }, [
        ecProfileEditorButton('btnDicebearUseUsername', 'Use username'),
        ecProfileEditorButton('btnDicebearRandomSeed', 'Random seed'),
      ]),
      ecProfileEditorDomEl('div', { className: 'ecProfileGrid ecProfileGridTight ecDicebearShapeGrid' }, [
        ecProfileEditorField('Background', dicebearBgInput),
        ecProfileEditorField('Shape', dicebearRadiusSelect),
      ]),
      ecProfileEditorDomEl('div', { className: 'ecProfileField' }, [dicebearFlipLabel]),
    ]),
    ecProfileEditorDomEl('div', { id: 'myProfilePresetGrid', className: 'ecProfilePresetGrid ecDicebearPresetGrid', 'aria-label': 'DiceBear avatar choices' }),
    ecProfileEditorDomEl('div', { id: 'myProfilePresetStatus', className: 'ecProfilePresetStatus muted', text: 'Click a DiceBear avatar below to preview it. Use the apply button to make it your current avatar right away.' }),
    ecProfileEditorDomEl('div', { className: 'ecProfilePresetActions' }, [ecProfileEditorDomEl('button', { id: 'btnApplyPresetAvatar', className: 'miniBtn secondary', type: 'button', disabled: true, text: '✨ Apply DiceBear avatar' })]),
    ecProfileEditorDomEl('div', { className: 'ecProfileMeta muted', text: 'DiceBear avatars are SVG profile pictures generated from a URL. You can still upload your own avatar above.' }),
  ], 'ecProfilePresetField ecDicebearBuilderField'));
  rightRail.appendChild(ecProfileEditorSection('Favorites spotlight', [
    ecProfileEditorDomEl('div', { id: 'myProfileMiniFavoritesPreview', className: 'ecProfileMiniFavorites' }),
  ]));

  const layout = ecProfileEditorDomEl('div', { className: 'ecProfileFacebookLayout ecProfileFacebookLayoutPremium ecProfileEditorLayout' }, [leftRail, main, rightRail]);
  card.appendChild(layout);
  return card;
}

function renderMyProfileEditor(win, profile = null) {
  const p = (profile && typeof profile === 'object') ? profile : (UIState.myProfile || {});
  const username = String(p.username || currentUser || '').trim() || currentUser || 'User';
  const avatarUrl = ecNormalizeSafeUrl(p.avatar_url || '', { allowRelative: true, allowExternal: true });
  const bannerUrl = ecNormalizeSafeUrl(p.banner_url || '', { allowRelative: true, allowExternal: true });
  const profileAccent = /^#[0-9a-f]{6}$/i.test(String(p.profile_accent || '').trim())
    ? String(p.profile_accent || '').trim()
    : '#6f7cff';
  const bio = String(p.bio || '');
  const relationshipStatus = String(p.relationship_status || '');
  const relationshipVisibility = String(p.relationship_visibility || 'friends');
  const ageValue = (p.age === null || p.age === undefined || p.age === '') ? '' : String(p.age);
  const ageVisibility = String(p.age_visibility || 'friends');
  const locationText = String(p.location_text || '');
  const locationVisibility = String(p.location_visibility || 'friends');
  const interests = String(p.interests || '');
  const favoriteMusic = String(p.favorite_music || '');
  const favoriteMovies = String(p.favorite_movies || '');
  const favoriteGames = String(p.favorite_games || '');
  const websiteUrl = ecNormalizeSafeUrl(p.website_url || '', { allowRelative: false, allowExternal: true });
  const shareRecentRooms = !!p.share_recent_rooms;
  const recentRoomsVisibility = String(p.recent_rooms_visibility || 'friends');
  const profilePostDefaultVisibility = String(p.profile_post_default_visibility || 'friends');
  const joined = p.created_at ? _fmtLocalTime(p.created_at) : '';
  const initialPreset = detectAvatarPresetSelection(avatarUrl);
  const defaultPresetStyle = initialPreset?.style || DICEBEAR_DEFAULT_STYLE;

  const log = win?._ym?.log;
  if (!log) return;

  const editorNode = ecProfileBuildMyProfileEditorNode({
    p, username, avatarUrl, bannerUrl, profileAccent, bio,
    relationshipStatus, relationshipVisibility, ageValue, ageVisibility,
    locationText, locationVisibility, interests, favoriteMusic, favoriteMovies,
    favoriteGames, websiteUrl, shareRecentRooms, recentRoomsVisibility, profilePostDefaultVisibility, joined,
  });
  ecProfileEditorClearNode(log);
  log.appendChild(editorNode);

  const avatarInput = log.querySelector('#myProfileAvatarUrl');
  const bannerInput = log.querySelector('#myProfileBannerUrl');
  const accentInput = log.querySelector('#myProfileAccent');
  const websiteInput = log.querySelector('#myProfileWebsiteUrl');
  const ageInput = log.querySelector('#myProfileAge');
  const relationshipStatusInput = log.querySelector('#myProfileRelationshipStatus');
  const relationshipVisibilityInput = log.querySelector('#myProfileRelationshipVisibility');
  const ageVisibilityInput = log.querySelector('#myProfileAgeVisibility');
  const locationInput = log.querySelector('#myProfileLocation');
  const locationVisibilityInput = log.querySelector('#myProfileLocationVisibility');
  const interestsInput = log.querySelector('#myProfileInterests');
  const favoriteMusicInput = log.querySelector('#myProfileFavoriteMusic');
  const favoriteMoviesInput = log.querySelector('#myProfileFavoriteMovies');
  const favoriteGamesInput = log.querySelector('#myProfileFavoriteGames');
  const shareRecentRoomsInput = log.querySelector('#myProfileShareRecentRooms');
  const recentRoomsVisibilityInput = log.querySelector('#myProfileRecentRoomsVisibility');
  const profilePostDefaultVisibilityInput = log.querySelector('#myProfilePostDefaultVisibility');
  const avatarFileInput = log.querySelector('#myProfileAvatarFile');
  const bannerFileInput = log.querySelector('#myProfileBannerFile');
  const uploadBtn = log.querySelector('#btnUploadMyAvatar');
  const uploadBannerBtn = log.querySelector('#btnUploadMyBanner');
  const uploadStatus = log.querySelector('#myProfileUploadStatus');
  const bannerUploadStatus = log.querySelector('#myProfileBannerUploadStatus');
  const bioInput = log.querySelector('#myProfileBio');
  const bioCount = log.querySelector('#myProfileBioCount');
  const interestsCount = log.querySelector('#myProfileInterestsCount');
  const preview = log.querySelector('#myProfileAvatarPreview');
  const heroPreview = log.querySelector('#myProfileHeroPreview');
  const dropzone = log.querySelector('#myProfileAvatarDropzone');
  const bannerDropzone = log.querySelector('#myProfileBannerDropzone');
  const presetGrid = log.querySelector('#myProfilePresetGrid');
  const presetTabs = log.querySelector('#myProfilePresetStyleTabs');
  const shuffleBtn = log.querySelector('#btnShuffleAvatarPresets');
  const presetStatus = log.querySelector('#myProfilePresetStatus');
  const applyPresetBtn = log.querySelector('#btnApplyPresetAvatar');
  const dicebearSeedInput = log.querySelector('#myProfileDicebearSeed');
  const dicebearBgInput = log.querySelector('#myProfileDicebearBg');
  const dicebearRadiusInput = log.querySelector('#myProfileDicebearRadius');
  const dicebearFlipInput = log.querySelector('#myProfileDicebearFlip');
  const dicebearUseUsernameBtn = log.querySelector('#btnDicebearUseUsername');
  const dicebearRandomSeedBtn = log.querySelector('#btnDicebearRandomSeed');
  const saveBtn = log.querySelector('#btnSaveMyProfile');
  const resetDraftBtn = log.querySelector('#btnResetMyProfileDraft');
  const draftStatus = log.querySelector('#myProfileDraftStatus');
  const draftHint = log.querySelector('#myProfileDraftHint');
  const heroChips = log.querySelector('#myProfileHeroChips');
  const quickDraftState = log.querySelector('#myProfileQuickDraftState');
  const quickRecentRooms = log.querySelector('#myProfileQuickRecentRooms');
  const quickWebsite = log.querySelector('#myProfileQuickWebsite');
  const quickAccent = log.querySelector('#myProfileQuickAccent');
  const miniFavoritesPreview = log.querySelector('#myProfileMiniFavoritesPreview');
  const notifyInputs = Array.from(log.querySelectorAll('[data-profile-notify-key]'));
  const saveNotificationsBtn = log.querySelector('#btnSaveProfileNotifications');
  const notificationStatus = log.querySelector('#myProfileNotificationStatus');
  const editorCard = log.querySelector('.ecProfileEditorCard');

  let presetStyle = defaultPresetStyle;
  let presetPage = 0;
  let selectedPreset = initialPreset ? {
    style: initialPreset.style,
    seed: initialPreset.seed,
    url: avatarUrl,
  } : null;

  const setUploadStatus = (msg = '', kind = 'muted') => {
    if (!uploadStatus) return;
    uploadStatus.textContent = String(msg || '');
    uploadStatus.classList.toggle('dangerText', kind === 'error');
  };

  const setBannerUploadStatus = (msg = '', kind = 'muted') => {
    if (!bannerUploadStatus) return;
    bannerUploadStatus.textContent = String(msg || '');
    bannerUploadStatus.classList.toggle('dangerText', kind === 'error');
  };

  const setNotificationStatus = (msg = '', kind = 'muted') => {
    if (!notificationStatus) return;
    notificationStatus.textContent = String(msg || '');
    notificationStatus.classList.toggle('dangerText', kind === 'error');
  };

  const collectProfileNotificationSettings = () => {
    const payload = {};
    notifyInputs.forEach((input) => {
      const key = String(input?.dataset?.profileNotifyKey || '').trim();
      if (!key) return;
      payload[key] = !!input.checked;
    });
    return payload;
  };

  const applyProfileNotificationSettings = (settings = {}) => {
    notifyInputs.forEach((input) => {
      const key = String(input?.dataset?.profileNotifyKey || '').trim();
      if (!key) return;
      if (Object.prototype.hasOwnProperty.call(settings, key)) input.checked = !!settings[key];
    });
  };

  const loadProfileNotificationSettings = async () => {
    if (!notifyInputs.length) return;
    setNotificationStatus('Loading notification settings…');
    try {
      const res = await fetchWithAuth('/api/profile/notification_settings', { method: 'GET' });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.success) throw new Error(json?.error || `HTTP ${res.status}`);
      applyProfileNotificationSettings(json.settings || {});
      setNotificationStatus('Notification settings loaded.');
    } catch (err) {
      setNotificationStatus(`Could not load notification settings: ${err?.message || err}`, 'error');
    }
  };

  const saveProfileNotificationSettings = async () => {
    if (!notifyInputs.length) return;
    const payload = collectProfileNotificationSettings();
    if (saveNotificationsBtn) saveNotificationsBtn.disabled = true;
    setNotificationStatus('Saving notification settings…');
    try {
      const res = await fetchWithAuth('/api/profile/notification_settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.success) throw new Error(json?.error || `HTTP ${res.status}`);
      applyProfileNotificationSettings(json.settings || payload);
      setNotificationStatus('Notification settings saved.');
      toast('✅ Notification settings saved', 'ok');
    } catch (err) {
      setNotificationStatus(`Notification settings failed: ${err?.message || err}`, 'error');
      toast('❌ Notification settings failed', 'error');
    } finally {
      if (saveNotificationsBtn) saveNotificationsBtn.disabled = false;
    }
  };

  const normalizePayloadValue = (value) => {
    if (typeof value === 'boolean') return value ? '1' : '0';
    return String(value ?? '').trim();
  };

  const collectProfilePayload = () => ({
    avatar_url: String(avatarInput?.value || '').trim(),
    banner_url: String(bannerInput?.value || '').trim(),
    profile_accent: String(accentInput?.value || '').trim(),
    website_url: String(websiteInput?.value || '').trim(),
    bio: String(bioInput?.value || '').trim(),
    relationship_status: String(relationshipStatusInput?.value || '').trim(),
    relationship_visibility: String(relationshipVisibilityInput?.value || 'friends').trim(),
    age: String(ageInput?.value || '').trim(),
    age_visibility: String(ageVisibilityInput?.value || 'friends').trim(),
    location_text: String(locationInput?.value || '').trim(),
    location_visibility: String(locationVisibilityInput?.value || 'friends').trim(),
    interests: String(interestsInput?.value || '').trim(),
    favorite_music: String(favoriteMusicInput?.value || '').trim(),
    favorite_movies: String(favoriteMoviesInput?.value || '').trim(),
    favorite_games: String(favoriteGamesInput?.value || '').trim(),
    share_recent_rooms: !!shareRecentRoomsInput?.checked,
    recent_rooms_visibility: String(recentRoomsVisibilityInput?.value || 'friends').trim(),
    profile_post_default_visibility: String(profilePostDefaultVisibilityInput?.value || 'friends').trim(),
  });

  const initialPayloadSnapshot = collectProfilePayload();

  const refreshDraftState = (forceSaved = false) => {
    const payload = collectProfilePayload();
    const isDirty = !forceSaved && Object.keys(initialPayloadSnapshot).some((key) => normalizePayloadValue(initialPayloadSnapshot[key]) !== normalizePayloadValue(payload[key]));
    if (saveBtn) saveBtn.disabled = !isDirty;
    if (resetDraftBtn) resetDraftBtn.disabled = !isDirty;
    if (draftStatus) {
      draftStatus.textContent = isDirty ? 'Unsaved changes' : 'Saved';
      draftStatus.classList.toggle('is-dirty', isDirty);
    }
    if (draftHint) {
      draftHint.textContent = isDirty ? 'Save your profile to apply the latest edits.' : 'No unsaved profile changes.';
    }
    if (quickDraftState) quickDraftState.textContent = isDirty ? 'Unsaved' : 'Saved';
    if (quickRecentRooms) quickRecentRooms.textContent = shareRecentRoomsInput?.checked ? 'On' : 'Off';
    if (quickWebsite) quickWebsite.textContent = String(websiteInput?.value || '').trim() ? 'Added' : 'None';
    if (quickAccent) quickAccent.textContent = String(accentInput?.value || '').trim() || '#6f7cff';
    return isDirty;
  };

  const autosizeTextarea = (node) => {
    if (!(node instanceof HTMLTextAreaElement)) return;
    node.style.height = 'auto';
    node.style.height = `${Math.max(node.scrollHeight, node.classList.contains('ecProfileTextareaCompact') ? 84 : 96)}px`;
  };

  const updateCharCount = (node, counterNode, maxLen) => {
    if (!node || !counterNode) return;
    const used = String(node.value || '').length;
    counterNode.textContent = `${used}/${maxLen}`;
    counterNode.classList.toggle('is-limit', used >= Math.max(0, maxLen - 25));
  };

  const syncRecentRoomsVisibilityState = () => {
    const enabled = !!shareRecentRoomsInput?.checked;
    if (recentRoomsVisibilityInput) recentRoomsVisibilityInput.disabled = !enabled;
  };

  const updateHeroPreview = () => {
    if (!heroPreview) return;
    const nextBanner = ecNormalizeSafeUrl(String(bannerInput?.value || '').trim(), { allowRelative: true, allowExternal: true });
    const nextAccent = String(accentInput?.value || '').trim() || '#6f7cff';
    try { editorCard?.style.setProperty('--ec-profile-accent', nextAccent); } catch {}
    if (nextBanner) {
      heroPreview.style.backgroundImage = `linear-gradient(180deg, rgba(15,23,42,.20), rgba(15,23,42,.72)), ${ecCssUrl(nextBanner, { allowRelative: true, allowExternal: true })}`;
      return;
    }
    heroPreview.style.backgroundImage = `linear-gradient(135deg, ${nextAccent}, rgba(15,23,42,.82))`;
  };

  const updatePreview = () => {
    if (!preview || !avatarInput) return;
    const url = ecNormalizeSafeUrl(String(avatarInput.value || '').trim(), { allowRelative: true, allowExternal: true });
    ecProfileEditorClearNode(preview);
    if (url) {
      const img = document.createElement('img');
      img.src = url;
      img.alt = `${username} avatar preview`;
      img.referrerPolicy = 'no-referrer';
      img.addEventListener('error', () => {
        ecProfileEditorClearNode(preview);
        preview.appendChild(ecProfileEditorAvatarStub(username));
      }, { once: true });
      preview.appendChild(img);
      return;
    }
    preview.appendChild(ecProfileEditorAvatarStub(username));
  };

  const updateHeroChips = () => {
    if (!heroChips) return;
    const chipData = [];
    const relationship = String(relationshipStatusInput?.value || '').trim();
    const age = String(ageInput?.value || '').trim();
    const location = String(locationInput?.value || '').trim();
    if (relationship) chipData.push(['❤️', relationship]);
    if (age) chipData.push(['🎂', age]);
    if (location) chipData.push(['📍', location]);
    ecProfileEditorClearNode(heroChips);
    chipData.forEach(([icon, value]) => {
      const chip = document.createElement('span');
      chip.className = 'ecProfileChip';
      chip.textContent = `${icon} ${value}`;
      heroChips.appendChild(chip);
    });
    heroChips.hidden = chipData.length === 0;
  };

  const updateFavoritesPreview = () => {
    if (!miniFavoritesPreview) return;
    ecProfileEditorClearNode(miniFavoritesPreview);
    [
      ['🎵', 'Music', String(favoriteMusicInput?.value || '').trim(), 'Not listed'],
      ['🎬', 'Movies / shows', String(favoriteMoviesInput?.value || '').trim(), 'Not listed'],
      ['🎮', 'Games', String(favoriteGamesInput?.value || '').trim(), 'Not listed'],
    ].forEach(([icon, label, value, emptyText]) => {
      miniFavoritesPreview.appendChild(ecProfileEditorFavoriteCard(icon, label, value, emptyText));
    });
  };

  const updateQuickFactsPreview = (isDirty = refreshDraftState(false)) => {
    if (quickDraftState) quickDraftState.textContent = isDirty ? 'Unsaved' : 'Saved';
    if (quickRecentRooms) quickRecentRooms.textContent = shareRecentRoomsInput?.checked ? 'On' : 'Off';
    if (quickWebsite) quickWebsite.textContent = String(websiteInput?.value || '').trim() ? 'Added' : 'None';
    if (quickAccent) quickAccent.textContent = String(accentInput?.value || '').trim() || '#6f7cff';
  };

  const updatePresetSelection = () => {
    if (!presetGrid || !avatarInput) return;
    const current = String(avatarInput.value || '').trim();
    presetGrid.querySelectorAll('.ecProfilePresetBtn').forEach((btn) => {
      const btnUrl = String(btn.dataset.avatarUrl || '');
      btn.classList.toggle('is-selected', btnUrl === current);
      btn.classList.toggle('is-pending', !!selectedPreset && btnUrl === String(selectedPreset.url || '') && btnUrl !== current);
    });
    if (applyPresetBtn) {
      applyPresetBtn.disabled = !(selectedPreset && String(selectedPreset.url || '').trim());
    }
    if (presetStatus) {
      if (selectedPreset && String(selectedPreset.url || '').trim()) {
        const sameAsCurrent = String(selectedPreset.url || '') === current;
        presetStatus.textContent = sameAsCurrent
          ? 'DiceBear avatar is selected and already active for this profile.'
          : 'DiceBear avatar selected. Click “Apply DiceBear avatar” to save it right away, or use Save profile below.';
      } else {
        presetStatus.textContent = 'Click a DiceBear avatar below to preview it. Use the apply button to make it your current avatar right away.';
      }
    }
  };

  const currentDiceBearOptions = () => ({
    backgroundColor: normalizeDiceBearColor(dicebearBgInput?.value || DICEBEAR_DEFAULT_BG),
    borderRadius: normalizeDiceBearBorderRadius(dicebearRadiusInput?.value ?? 50),
    flip: !!dicebearFlipInput?.checked,
  });

  const currentDiceBearSeed = () => normalizeDiceBearSeed(dicebearSeedInput?.value || username || currentUser || 'hui');

  const syncDiceBearControlsFromSelection = (selection = null) => {
    if (!selection) return;
    if (dicebearSeedInput && selection.seed) dicebearSeedInput.value = normalizeDiceBearSeed(selection.seed);
    if (dicebearBgInput) dicebearBgInput.value = `#${normalizeDiceBearColor(selection.backgroundColor || DICEBEAR_DEFAULT_BG)}`;
    if (dicebearRadiusInput) dicebearRadiusInput.value = String(normalizeDiceBearBorderRadius(selection.borderRadius ?? 50));
    if (dicebearFlipInput) dicebearFlipInput.checked = !!selection.flip;
  };

  const renderPresetTabs = () => {
    if (!presetTabs) return;
    while (presetTabs.firstChild) presetTabs.removeChild(presetTabs.firstChild);
    LOCAL_AVATAR_PRESET_STYLES.forEach((style) => {
      const btn = document.createElement('button');
      btn.className = `ecProfilePresetTab${style.key === presetStyle ? ' is-active' : ''}`;
      btn.type = 'button';
      btn.dataset.style = String(style.key || 'persona');
      btn.textContent = String(style.label || style.key || 'Avatar');
      btn.addEventListener('click', () => {
        presetStyle = normalizeDiceBearStyleKey(btn.dataset.style || DICEBEAR_DEFAULT_STYLE);
        presetPage = 0;
        renderPresetTabs();
        renderPresetGrid();
      });
      presetTabs.appendChild(btn);
    });
  };

  const renderPresetGrid = () => {
    if (!presetGrid) return;
    while (presetGrid.firstChild) presetGrid.removeChild(presetGrid.firstChild);
    const baseIndex = presetPage * 12;
    for (let i = 0; i < 12; i += 1) {
      const seedBase = currentDiceBearSeed();
      const seed = `${seedBase}-${presetStyle}-${baseIndex + i + 1}`;
      const url = ecNormalizeSafeUrl(buildDiceBearAvatarUrl(presetStyle, seed, currentDiceBearOptions()), { allowRelative: false, allowExternal: true });
      const btn = document.createElement('button');
      btn.className = 'ecProfilePresetBtn';
      btn.type = 'button';
      btn.dataset.style = String(presetStyle || 'persona');
      btn.dataset.seed = String(seed || '');
      btn.dataset.avatarUrl = url;
      btn.title = `Use DiceBear ${String(presetStyle || 'profile')} avatar ${i + 1}`;
      const img = document.createElement('img');
      img.src = url;
      img.alt = `DiceBear ${String(presetStyle || 'profile')} avatar option ${i + 1}`;
      img.loading = 'lazy';
      btn.appendChild(img);
      const selectPreset = () => {
        const nextUrl = String(btn.dataset.avatarUrl || '');
        selectedPreset = {
          provider: 'dicebear',
          style: normalizeDiceBearStyleKey(btn.dataset.style || presetStyle || DICEBEAR_DEFAULT_STYLE),
          seed: String(btn.dataset.seed || ''),
          url: nextUrl,
          ...currentDiceBearOptions(),
        };
        if (avatarInput) avatarInput.value = nextUrl;
        setUploadStatus('DiceBear avatar selected. Click Apply DiceBear avatar or Save profile.');
        updatePreview();
        updatePresetSelection();
        refreshDraftState();
      };
      btn.addEventListener('click', (ev) => {
        try { ev.preventDefault(); ev.stopPropagation(); } catch {}
        selectPreset();
      });
      btn.addEventListener('dblclick', async (ev) => {
        try { ev.preventDefault(); ev.stopPropagation(); } catch {}
        selectPreset();
        applyPresetBtn?.click();
      });
      presetGrid.appendChild(btn);
    }
    updatePresetSelection();
  };

  const uploadSelectedFile = async (file) => {
    await uploadMyAvatarFile(file, {
      uploadBtn,
      setUploadStatus,
      avatarInput,
      afterSuccess: () => {
        updatePreview();
        updatePresetSelection();
        refreshDraftState(true);
      }
    });
  };

  const uploadSelectedBannerFile = async (file) => {
    await uploadMyBannerFile(file, {
      uploadBtn: uploadBannerBtn,
      setUploadStatus: setBannerUploadStatus,
      bannerInput,
      afterSuccess: () => {
        updateHeroPreview();
        refreshDraftState(true);
      }
    });
  };

  const handleAvatarInputChange = () => {
    const current = String(avatarInput?.value || '').trim();
    const detected = detectAvatarPresetSelection(current);
    if (!current) {
      selectedPreset = null;
    } else if (detected) {
      selectedPreset = { style: detected.style, seed: detected.seed, url: current };
    } else if (selectedPreset && String(selectedPreset.url || '') !== current) {
      selectedPreset = null;
    }
    setUploadStatus('');
    updatePreview();
    updatePresetSelection();
  };

  const bindDraftInput = (node, options = {}) => {
    if (!node) return;
    const eventName = options.eventName || 'input';
    node.addEventListener(eventName, () => {
      if (typeof options.onAfter === 'function') {
        try { options.onAfter(); } catch {}
      }
      if (options.autosize) autosizeTextarea(node);
      if (options.counterNode && Number.isFinite(options.maxLen)) updateCharCount(node, options.counterNode, options.maxLen);
      refreshDraftState();
      updateHeroChips();
      updateFavoritesPreview();
    });
    if (options.autosize) autosizeTextarea(node);
    if (options.counterNode && Number.isFinite(options.maxLen)) updateCharCount(node, options.counterNode, options.maxLen);
  };

  const rerenderDiceBearChoices = () => {
    selectedPreset = null;
    renderPresetGrid();
    updatePresetSelection();
    refreshDraftState();
  };

  [dicebearSeedInput, dicebearBgInput, dicebearRadiusInput, dicebearFlipInput].forEach((node) => {
    node?.addEventListener(node === dicebearFlipInput || node === dicebearRadiusInput ? 'change' : 'input', () => {
      presetPage = 0;
      rerenderDiceBearChoices();
    });
  });
  dicebearUseUsernameBtn?.addEventListener('click', () => {
    if (dicebearSeedInput) dicebearSeedInput.value = username || currentUser || 'hui';
    presetPage = 0;
    rerenderDiceBearChoices();
  });
  dicebearRandomSeedBtn?.addEventListener('click', () => {
    if (dicebearSeedInput) dicebearSeedInput.value = buildDiceBearRandomSeed(username);
    presetPage = 0;
    rerenderDiceBearChoices();
  });

  bindDraftInput(avatarInput, { onAfter: handleAvatarInputChange });
  bindDraftInput(bannerInput, { onAfter: () => { updateHeroPreview(); setBannerUploadStatus(''); } });
  bindDraftInput(accentInput, { onAfter: updateHeroPreview });
  bindDraftInput(websiteInput);
  bindDraftInput(ageInput);
  bindDraftInput(relationshipStatusInput, { eventName: 'change' });
  bindDraftInput(relationshipVisibilityInput, { eventName: 'change' });
  bindDraftInput(ageVisibilityInput, { eventName: 'change' });
  bindDraftInput(locationInput);
  bindDraftInput(locationVisibilityInput, { eventName: 'change' });
  bindDraftInput(interestsInput, { autosize: true, counterNode: interestsCount, maxLen: 240 });
  bindDraftInput(favoriteMusicInput);
  bindDraftInput(favoriteMoviesInput);
  bindDraftInput(favoriteGamesInput);
  bindDraftInput(shareRecentRoomsInput, { eventName: 'change', onAfter: syncRecentRoomsVisibilityState });
  bindDraftInput(recentRoomsVisibilityInput, { eventName: 'change' });
  bindDraftInput(bioInput, { autosize: true, counterNode: bioCount, maxLen: 500 });

  log.querySelector('#btnClearMyAvatar')?.addEventListener('click', () => {
    if (avatarInput) avatarInput.value = '';
    if (avatarFileInput) avatarFileInput.value = '';
    selectedPreset = null;
    setUploadStatus('');
    updatePreview();
    updatePresetSelection();
    refreshDraftState();
  });

  log.querySelector('#btnClearMyBanner')?.addEventListener('click', () => {
    if (bannerInput) bannerInput.value = '';
    if (bannerFileInput) bannerFileInput.value = '';
    setBannerUploadStatus('');
    updateHeroPreview();
    refreshDraftState();
  });

  resetDraftBtn?.addEventListener('click', () => {
    renderMyProfileEditor(win, UIState.myProfile || p);
  });

  log.querySelector('#btnRefreshMyProfile')?.addEventListener('click', async () => {
    const fresh = await refreshMyProfileInHub();
    renderMyProfileEditor(win, fresh || UIState.myProfile);
  });

  log.querySelector('#btnOpenMyPublicProfile')?.addEventListener('click', () => {
    openProfileWindow(username);
  });

  log.querySelector('#btnHeroChooseAvatar')?.addEventListener('click', () => avatarFileInput?.click());
  log.querySelector('#btnHeroChooseBanner')?.addEventListener('click', () => bannerFileInput?.click());
  log.querySelector('#btnHeroViewProfile')?.addEventListener('click', () => openProfileWindow(username));

  uploadBtn?.addEventListener('click', async () => {
    const file = avatarFileInput?.files?.[0] || null;
    if (!file) {
      setUploadStatus('Choose an avatar image to upload.');
      avatarFileInput?.click();
      return;
    }
    await uploadSelectedFile(file);
  });

  uploadBannerBtn?.addEventListener('click', async () => {
    const file = bannerFileInput?.files?.[0] || null;
    if (!file) {
      setBannerUploadStatus('Choose a banner image to upload.');
      bannerFileInput?.click();
      return;
    }
    await uploadSelectedBannerFile(file);
  });

  avatarFileInput?.addEventListener('change', async () => {
    const file = avatarFileInput?.files?.[0] || null;
    if (!file) return;
    setUploadStatus(`Uploading ${file.name}…`);
    await uploadSelectedFile(file);
  });

  bannerFileInput?.addEventListener('change', async () => {
    const file = bannerFileInput?.files?.[0] || null;
    if (!file) return;
    setBannerUploadStatus(`Uploading ${file.name}…`);
    await uploadSelectedBannerFile(file);
  });

  dropzone?.addEventListener('click', () => avatarFileInput?.click());
  bannerDropzone?.addEventListener('click', () => bannerFileInput?.click());

  const bindDropzoneUpload = (zone, inputNode, onUpload) => {
    if (!zone) return;
    zone.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        try { ev.preventDefault(); } catch {}
        inputNode?.click();
      }
    });
    ['dragenter', 'dragover'].forEach((evtName) => {
      zone.addEventListener(evtName, (ev) => {
        try { ev.preventDefault(); } catch {}
        zone.classList.add('is-dragover');
      });
    });
    ['dragleave', 'dragend', 'drop'].forEach((evtName) => {
      zone.addEventListener(evtName, (ev) => {
        try { ev.preventDefault(); } catch {}
        zone.classList.remove('is-dragover');
      });
    });
    zone.addEventListener('drop', async (ev) => {
      const file = ev?.dataTransfer?.files?.[0] || null;
      if (!file) return;
      if (inputNode && ev?.dataTransfer?.files?.length) {
        try {
          const dt = new DataTransfer();
          dt.items.add(file);
          inputNode.files = dt.files;
        } catch {}
      }
      await onUpload(file);
    });
  };

  bindDropzoneUpload(dropzone, avatarFileInput, uploadSelectedFile);
  bindDropzoneUpload(bannerDropzone, bannerFileInput, uploadSelectedBannerFile);

  shuffleBtn?.addEventListener('click', () => {
    presetPage += 1;
    renderPresetGrid();
  });

  const saveMyProfile = (mode = 'full') => {
    const payload = collectProfilePayload();
    if (saveBtn) saveBtn.disabled = true;
    if (resetDraftBtn) resetDraftBtn.disabled = true;
    if (draftStatus) {
      draftStatus.textContent = 'Saving…';
      draftStatus.classList.remove('is-dirty');
    }
    if (draftHint) draftHint.textContent = 'Applying your profile changes now.';
    socket.emit('set_my_profile', payload, async (res) => {
      if (!res?.success) {
        refreshDraftState();
        if (draftStatus) draftStatus.textContent = 'Unsaved changes';
        if (draftHint) draftHint.textContent = res?.error || 'Profile update failed';
        toast(`❌ ${res?.error || 'Profile update failed'}`, 'error');
        return;
      }
      UIState.myProfile = res.profile || { ...(UIState.myProfile || {}), ...payload, username };
      const detected = detectAvatarPresetSelection(String(UIState.myProfile?.avatar_url || '').trim());
      selectedPreset = detected ? {
        style: detected.style,
        seed: detected.seed,
        url: String(UIState.myProfile.avatar_url || '').trim(),
      } : null;
      renderMyHubIdentity(UIState.myProfile);
      setUploadStatus('');
      setBannerUploadStatus('');
      if (mode === 'preset') {
        toast('✅ DiceBear avatar applied', 'ok');
      } else {
        toast('✅ Profile updated', 'ok');
      }
      renderMyProfileEditor(win, UIState.myProfile);
    });
  };

  applyPresetBtn?.addEventListener('click', () => {
    if (!selectedPreset || !String(selectedPreset.url || '').trim()) {
      toast('⚠️ Choose a DiceBear avatar first', 'warn');
      return;
    }
    if (avatarInput) avatarInput.value = String(selectedPreset.url || '').trim();
    saveMyProfile('preset');
  });

  saveBtn?.addEventListener('click', () => {
    saveMyProfile('full');
  });
  saveNotificationsBtn?.addEventListener('click', saveProfileNotificationSettings);

  syncDiceBearControlsFromSelection(initialPreset);
  renderPresetTabs();
  renderPresetGrid();
  updatePreview();
  updatePresetSelection();
  updateHeroPreview();
  updateHeroChips();
  updateFavoritesPreview();
  syncRecentRoomsVisibilityState();
  refreshDraftState(true);
  loadProfileNotificationSettings();
}
