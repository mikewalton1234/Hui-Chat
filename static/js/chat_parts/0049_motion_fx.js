(function () {
  const EC_ALIVE_PANEL_SELECTOR = '.sitePlaceholder, .ym-window, .modalCard, .ecProfilePageCard, .roomBrowserPopoutCard, .ecProfileOwnerEditorDialog';

  function ecMotionAllowed() {
    try {
      return !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) {
      return true;
    }
  }

  const EC_TEXT_ANIMATION_CLASSES = {
    none: '',
    fade: 'ec-enter-fade',
    rise: 'ec-enter-rise',
    slide: 'ec-enter-slide-left',
    scale: 'ec-enter-scale'
  };

  function ecNormalizeTextAnimationMode(value, fallback = 'none') {
    const mode = String(value || fallback || 'none').trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(EC_TEXT_ANIMATION_CLASSES, mode) ? mode : fallback;
  }

  function ecMessageContextFromElement(el) {
    try {
      if (el?.classList?.contains('ec-msgItem--room') || el?.classList?.contains('msgRow')) return 'room';
      const win = el?.closest?.('.ym-window');
      const kind = String(win?.dataset?.kind || '').trim().toLowerCase();
      if (kind === 'dm') return 'dm';
      if (kind === 'group') return 'group';
    } catch (e) {}
    return 'generic';
  }

  function ecTextAnimationClassForContext(context) {
    const cfg = (window.HUI_CFG && typeof window.HUI_CFG === 'object') ? window.HUI_CFG : {};
    const ctx = String(context || 'generic').trim().toLowerCase();
    const mode = ctx === 'room'
      ? ecNormalizeTextAnimationMode(cfg.chat_text_animation, 'none')
      : ctx === 'dm'
        ? ecNormalizeTextAnimationMode(cfg.dm_text_animation, 'rise')
        : ctx === 'group'
          ? ecNormalizeTextAnimationMode(cfg.group_text_animation, 'rise')
          : 'rise';
    return EC_TEXT_ANIMATION_CLASSES[mode] || '';
  }

  function ecAnimateMessageOnce(el, context) {
    if (!el) return;
    const cls = ecTextAnimationClassForContext(context || ecMessageContextFromElement(el));
    if (!cls) return;
    ecAnimateOnce(el, cls);
  }

  function ecResetAnimation(el, cls, timeoutMs = 520) {
    if (!el || !cls) return;
    const finish = () => {
      try { el.classList.remove(cls); } catch (e) {}
      try { delete el.dataset.ecAnimating; } catch (e) {}
    };
    let done = false;
    const once = () => {
      if (done) return;
      done = true;
      finish();
    };
    try { el.addEventListener('animationend', once, { once: true }); } catch (e) {}
    window.setTimeout(once, timeoutMs);
  }

  function ecAnimateOnce(el, cls = 'ec-enter-rise', timeoutMs = 520) {
    if (!el || !cls) return;
    if (!ecMotionAllowed()) return;
    try {
      if (el.dataset.ecAnimating === cls) return;
      el.dataset.ecAnimating = cls;
      if (typeof ecRestartAnimationClass === 'function') {
        ecRestartAnimationClass(el, cls, timeoutMs, () => ecResetAnimation(el, cls, timeoutMs));
      } else {
        el.classList.add(cls);
        ecResetAnimation(el, cls, timeoutMs);
      }
    } catch (e) {}
  }

  function ecAnimateTree(root) {
    if (!root || !ecMotionAllowed()) return;
    const rules = [
      ['.toast', 'ec-enter-rise'],
      ['.ym-window', 'ec-enter-scale'],
      ['.msgRow', null],
      ['.ecDockMenu', 'ec-enter-scale'],
    ];
    try {
      if (root.matches && root.matches('.ec-msgItem')) ecAnimateMessageOnce(root);
    } catch (e) {}
    try {
      root.querySelectorAll?.('.ec-msgItem')?.forEach((el) => ecAnimateMessageOnce(el));
    } catch (e) {}
    rules.forEach(([selector, cls]) => {
      try {
        if (root.matches && root.matches(selector)) {
          if (cls) ecAnimateOnce(root, cls);
          else ecAnimateMessageOnce(root);
        }
      } catch (e) {}
      try {
        root.querySelectorAll?.(selector)?.forEach((el) => {
          if (cls) ecAnimateOnce(el, cls);
          else ecAnimateMessageOnce(el);
        });
      } catch (e) {}
    });
  }

  function ecAnimatePanel(panel) {
    if (!panel || !ecMotionAllowed()) return;
    try {
      if (typeof ecRestartAnimationClass === 'function') {
        ecRestartAnimationClass(panel, 'ec-panel-enter', 420, () => ecResetAnimation(panel, 'ec-panel-enter', 420));
      } else {
        panel.classList.add('ec-panel-enter');
        ecResetAnimation(panel, 'ec-panel-enter', 420);
      }
    } catch (e) {}
  }

  function ecEnsureAmbientFx() {
    const root = document.getElementById('appRoot') || document.body || document.documentElement;
    if (!root) return null;
    let scene = document.getElementById('ecAmbientScene');
    if (scene) return scene;
    scene = ecCreateEl('div', { id: 'ecAmbientScene', ariaHidden: 'true' });
    const layer = scene;
    layer.className = 'ecAmbientFx';
    [
      'ecAmbientOrb ecAmbientOrbA',
      'ecAmbientOrb ecAmbientOrbB',
      'ecAmbientOrb ecAmbientOrbC',
      'ecAmbientGlassIcon ecAmbientGlassChat',
      'ecAmbientGlassIcon ecAmbientGlassSpark',
      'ecAmbientGlassIcon ecAmbientGlassBell',
      'ecAmbientGlassIcon ecAmbientGlassUsers',
      'ecAmbientGlyph glyph-chat',
      'ecAmbientGlyph glyph-spark',
      'ecAmbientGlyph glyph-bell',
      'ecAmbientGlyph glyph-users'
    ].forEach((cls) => {
      const node = ecCreateEl('div', { className: cls });
      if (cls.includes('ecAmbientGlassIcon') || cls.includes('ecAmbientGlyph')) node.setAttribute('aria-hidden', 'true');
      scene.appendChild(node);
    });
    root.insertBefore(scene, root.firstChild || null);
    return scene;
  }

  function ecEnsureAmbientScene() {
    return ecEnsureAmbientFx();
  }

  function ecBindAmbientParallax(scene) {
    if (!scene || scene.dataset.ecParallaxBound === '1') return;
    scene.dataset.ecParallaxBound = '1';
    scene.style.setProperty('--ec-ambient-mx', '0px');
    scene.style.setProperty('--ec-ambient-my', '0px');
  }

  function ecResetAlivePanel(panel) {
    if (!panel) return;
    panel.classList.remove('ecAlivePanelHot');
    panel.style.setProperty('--ec-panel-tilt-x', '0deg');
    panel.style.setProperty('--ec-panel-tilt-y', '0deg');
  }

  function ecWireAlivePanel(panel) {
    if (!panel || panel.dataset.ecAliveBound === '1') return;
    panel.dataset.ecAliveBound = '1';
    panel.classList.add('ecAlivePanel');
    ecResetAlivePanel(panel);
  }

  function ecWireAlivePanels(root = document) {
    if (!root) return;
    try {
      if (root instanceof Element && root.matches?.(EC_ALIVE_PANEL_SELECTOR)) ecWireAlivePanel(root);
    } catch (e) {}
    try {
      root.querySelectorAll?.(EC_ALIVE_PANEL_SELECTOR)?.forEach((panel) => ecWireAlivePanel(panel));
    } catch (e) {}
  }

  function ecInitMutationObserver() {
    if (!('MutationObserver' in window)) return;
    const root = document.getElementById('appRoot') || document.body || document.documentElement;
    if (!root) return;
    const observer = new MutationObserver((records) => {
      if (!ecMotionAllowed()) return;
      records.forEach((record) => {
        record.addedNodes.forEach((node) => {
          if (!(node instanceof Element)) return;
          ecAnimateTree(node);
          ecWireAlivePanels(node);
        });
      });
    });
    observer.observe(root, { childList: true, subtree: true });
  }

  window.ecMotionAllowed = ecMotionAllowed;
  window.ecAnimateOnce = ecAnimateOnce;
  window.ecAnimateMessageOnce = ecAnimateMessageOnce;
  window.ecTextAnimationClassForContext = ecTextAnimationClassForContext;
  window.ecAnimatePanel = ecAnimatePanel;
  window.ecEnsureAmbientFx = ecEnsureAmbientFx;
  window.ecEnsureAmbientScene = ecEnsureAmbientScene;
  window.ecWireAlivePanels = ecWireAlivePanels;

  const boot = () => {
    const scene = ecEnsureAmbientFx();
    ecBindAmbientParallax(scene);
    ecWireAlivePanels(document);
    ecInitMutationObserver();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
