// Split chat runtime part: mobile shell detection and panel switching.

(function () {
  "use strict";

  const root = document.getElementById("appRoot");
  if (!root) return;

  const nav = document.getElementById("mobileShellNav");
  const html = document.documentElement;
  const cfg = window.HUI_CFG || {};
  const serverDevice = window.HUI_DEVICE || {};
  const COMPACT_SHELL_WIDTH = 1120;
  const COMPACT_SHELL_QUERY = `(max-width: ${COMPACT_SHELL_WIDTH}px), (max-width: 1024px) and (pointer: coarse)`;
  const KEYBOARD_SHELL_QUERY = `(max-height: 520px) and (max-width: ${COMPACT_SHELL_WIDTH}px)`;
  const mq = window.matchMedia
    ? window.matchMedia(COMPACT_SHELL_QUERY)
    : null;
  const keyboardMq = window.matchMedia
    ? window.matchMedia(KEYBOARD_SHELL_QUERY)
    : null;

  function isMobileNow() {
    const byViewport = !!(mq && mq.matches);
    const byServerHint = !!(cfg.mobile_device_hint || serverDevice.is_mobile);
    const viewportW = window.innerWidth || document.documentElement.clientWidth || 9999;
    const screenW = window.screen ? window.screen.width || 9999 : 9999;
    const narrowFallback = Math.min(viewportW, screenW) <= COMPACT_SHELL_WIDTH;
    return byViewport || byServerHint || narrowFallback;
  }

  function hasOpenRoom() {
    const room = document.getElementById("roomEmbed");
    return !!(room && !room.classList.contains("hidden"));
  }

  function storageKey() {
    const user = String(window.USERNAME || "guest").replace(/[^a-zA-Z0-9_.-]/g, "_");
    return `hui.mobile.panel.${user}`;
  }

  function readPanel() {
    try {
      const saved = localStorage.getItem(storageKey());
      if (["rooms", "chat", "hub"].includes(saved)) return saved;
    } catch (e) {}
    return hasOpenRoom() ? "chat" : "rooms";
  }

  function writePanel(panel) {
    try { localStorage.setItem(storageKey(), panel); } catch (e) {}
  }

  function updateMobileChatAvailability() {
    if (!nav) return;
    const chatBtn = nav.querySelector('[data-mobile-panel="chat"]');
    if (!chatBtn) return;
    const available = hasOpenRoom();
    chatBtn.disabled = !available;
    chatBtn.setAttribute("aria-disabled", available ? "false" : "true");
    chatBtn.title = available ? "Open current room chat" : "Join a room first";
  }

  let mobileUsersBtn = null;
  let mobileUsersCloseBtn = null;
  let mobileBackBtn = null;
  let mobileComposerToolsBtn = null;
  let mobileLatestBtn = null;
  const MOBILE_ROOM_STEPS = ["categories", "official", "custom"];
  const MOBILE_ROOM_STEP_HINTS = {
    categories: "Pick a category/subcategory first. The room list opens next.",
    official: "Search or filter official rooms, then tap Join.",
    custom: "Browse user-created rooms or create a new one."
  };
  const MOBILE_HUB_SECTIONS = ["friends", "alerts", "groups", "me"];

  function mobileHubSectionKey() {
    const user = String(window.USERNAME || "guest").replace(/[^a-zA-Z0-9_.-]/g, "_");
    return `hui.mobile.hubSection.${user}`;
  }

  function normalizeMobileHubSection(section) {
    const s = String(section || "").trim();
    return MOBILE_HUB_SECTIONS.includes(s) ? s : "friends";
  }

  function readMobileHubSection() {
    try {
      return normalizeMobileHubSection(localStorage.getItem(mobileHubSectionKey()) || "friends");
    } catch (e) {
      return "friends";
    }
  }

  function writeMobileHubSection(section) {
    try { localStorage.setItem(mobileHubSectionKey(), normalizeMobileHubSection(section)); } catch (e) {}
  }

  function clickById(id) {
    const el = document.getElementById(id);
    if (!el) return false;
    try { el.click(); return true; } catch (e) { return false; }
  }

  function applyMobileHubBackedTab(section) {
    if (section === "groups") {
      if (typeof window.setActiveTab === "function") window.setActiveTab("groups");
      else clickById("tabGroups");
      return;
    }
    if (section === "friends") {
      if (typeof window.setActiveTab === "function") window.setActiveTab("friends");
      else clickById("tabFriends");
    }
  }

  function setMobileHubSection(section, options) {
    const opts = options || {};
    const next = normalizeMobileHubSection(section);
    root.setAttribute("data-mobile-hub-section", next);
    document.querySelectorAll(".mobileHubSectionBtn[data-mobile-hub-section]").forEach((btn) => {
      const active = String(btn.getAttribute("data-mobile-hub-section") || "") === next;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (next === "friends" || next === "groups") applyMobileHubBackedTab(next);
    if (next !== "alerts") {
      const flyout = document.getElementById("dockAlertFlyout");
      if (flyout) flyout.classList.add("hidden");
      document.querySelectorAll(".dockAlertBubble.isActive").forEach((btn) => btn.classList.remove("isActive"));
    }
    if (!opts.skipSave) writeMobileHubSection(next);
  }

  function syncMobileHubSection() {
    if (!root.classList.contains("is-mobile-shell")) return;
    const existing = root.getAttribute("data-mobile-hub-section") || readMobileHubSection();
    setMobileHubSection(existing, { skipSave: true });
  }

  window.ecSetMobileHubSection = function ecSetMobileHubSection(section, options) {
    setMobileHubSection(section, options || {});
  };

  function bindMobileHubActions() {
    const nav = document.getElementById("mobileHubNav");
    if (nav && nav.dataset.mobileHubBound !== "1") {
      nav.dataset.mobileHubBound = "1";
      nav.addEventListener("click", (event) => {
        const btn = event.target && event.target.closest ? event.target.closest(".mobileHubSectionBtn[data-mobile-hub-section]") : null;
        if (!btn) return;
        setMobileHubSection(btn.getAttribute("data-mobile-hub-section") || "friends");
      });
    }

    const bindings = [
      ["mobileHubOpenProfile", () => clickById("btnEditMeProfile") || (typeof window.openMyProfileEditor === "function" && window.openMyProfileEditor())],
      ["mobileHubOpenSettings", () => clickById("btnSettings") || (typeof window.openSettings === "function" && window.openSettings())],
      ["mobileHubAddFriend", () => { if (typeof window.openDockAddFriendPopup === "function") window.openDockAddFriendPopup(); }],
      ["mobileHubNewPm", () => { if (typeof window.openDockNewPmPopup === "function") window.openDockNewPmPopup(""); }],
      ["mobileHubBlockedUsers", () => { if (typeof window.viewBlockedUsersFromMenu === "function") window.viewBlockedUsersFromMenu(); }],
      ["mobileHubReplayTour", () => clickById("btnHelpTour")]
    ];
    bindings.forEach(([mobileId, action]) => {
      const btn = document.getElementById(mobileId);
      if (!btn || btn.dataset.mobileHubActionBound === "1") return;
      btn.dataset.mobileHubActionBound = "1";
      btn.addEventListener("click", () => {
        try { action(); } catch (e) {}
      });
    });
  }


  function minimizeMobileWindowToHub(win) {
    if (!win) return;
    const id = String(win.dataset.winId || "");
    const title = String(win.dataset.windowTitle || win.dataset.windowFullTitle || "Chat");
    try {
      if (id && typeof window.minimizeWindow === "function") window.minimizeWindow(id, title);
      else win.classList.add("hidden");
    } catch (e) {
      win.classList.add("hidden");
    }
    setPanel("hub", { allowEmptyChat: true });
    window.setTimeout(syncMobileWindows, 0);
  }

  function closeMobileWindow(win) {
    const id = String(win?.dataset?.winId || "");
    try {
      if (id && typeof window.closeWindow === "function") window.closeWindow(id);
      else win?.remove?.();
    } catch (e) {
      win?.remove?.();
    }
    window.setTimeout(syncMobileWindows, 0);
  }

  function toggleMobileWindowTools(win) {
    if (!win) return;
    const opened = !win.classList.contains("is-mobile-window-tools-open");
    win.classList.toggle("is-mobile-window-tools-open", opened);
    if (opened) {
      closeMobileGroupMembers(win);
    }
    const btn = win.querySelector(".ym-mobileWindowToolsBtn");
    if (btn) btn.setAttribute("aria-expanded", opened ? "true" : "false");
  }

  function closeMobileGroupMembers(win) {
    if (!win) return;
    win.classList.remove("is-mobile-group-members-open");
    const usersBtn = win.querySelector(".ym-mobileWindowUsersBtn");
    if (usersBtn) usersBtn.setAttribute("aria-expanded", "false");
    const panel = win.querySelector(".ym-groupMembersPanel");
    if (panel) panel.setAttribute("aria-hidden", "true");
  }

  function closeMobileWindowTools(win) {
    if (!win) return;
    win.classList.remove("is-mobile-window-tools-open");
    const toolsBtn = win.querySelector(".ym-mobileWindowToolsBtn");
    if (toolsBtn) toolsBtn.setAttribute("aria-expanded", "false");
  }

  function closeMobileProfileEditDrawer(win) {
    if (!win) return;
    win.classList.remove("is-mobile-profile-edit-open");
    const editBtn = win.querySelector(".ym-mobileProfileEditBtn");
    if (editBtn) editBtn.setAttribute("aria-expanded", "false");
  }

  function resetMobileOnlyWindowState(win) {
    if (!win || win.nodeType !== 1) return;
    try { closeMobileWindowTools(win); } catch (e) {}
    try { closeMobileGroupMembers(win); } catch (e) {}
    try { closeMobileProfileEditDrawer(win); } catch (e) {}
    try { win.classList.remove("is-mobile-active-window"); } catch (e) {}
    try { if (win.getAttribute("aria-hidden") === "true") win.setAttribute("aria-hidden", "false"); } catch (e) {}
    try { if (win.dataset.kind === "dm" || win.dataset.kind === "group" || win.classList.contains("ecProfileWindow")) win.setAttribute("aria-modal", "false"); } catch (e) {}
  }

  function ensureMobileGroupMembersBackdrop(win) {
    if (!win || win.dataset.kind !== "group") return null;
    let backdrop = win.querySelector(":scope > .ym-mobileGroupUsersBackdrop");
    if (backdrop) return backdrop;
    backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.className = "ym-mobileGroupUsersBackdrop";
    backdrop.setAttribute("aria-label", "Close group users drawer");
    backdrop.addEventListener("click", () => closeMobileGroupMembers(win));
    win.appendChild(backdrop);
    return backdrop;
  }

  function bindMobileGroupMembersClose(win) {
    if (!win) return;
    win.querySelectorAll(".ym-groupMembersClose").forEach((btn) => {
      if (!btn || btn.dataset.mobileGroupMembersCloseBound === "1") return;
      btn.dataset.mobileGroupMembersCloseBound = "1";
      btn.addEventListener("click", () => closeMobileGroupMembers(win));
    });
  }

  function toggleMobileGroupMembers(win) {
    if (!win) return;
    bindMobileGroupMembersClose(win);
    const opened = !win.classList.contains("is-mobile-group-members-open");
    win.classList.toggle("is-mobile-group-members-open", opened);
    const panel = win.querySelector(".ym-groupMembersPanel");
    if (panel) panel.setAttribute("aria-hidden", opened ? "false" : "true");
    if (opened) {
      ensureMobileGroupMembersBackdrop(win);
      win.classList.remove("is-mobile-window-tools-open");
      const toolsBtn = win.querySelector(".ym-mobileWindowToolsBtn");
      if (toolsBtn) toolsBtn.setAttribute("aria-expanded", "false");
      const groupId = Number(win.dataset.groupId || String(win.dataset.winId || "").replace(/^group:/, "") || 0);
      if (groupId && typeof refreshGroupMemberRoster === "function") {
        refreshGroupMemberRoster(groupId, win).catch(() => {});
      }
    }
    const btn = win.querySelector(".ym-mobileWindowUsersBtn");
    if (btn) btn.setAttribute("aria-expanded", opened ? "true" : "false");
  }

  function removeGenericMobileWindowNav(win) {
    if (!win) return;
    win.querySelectorAll(":scope > .ym-mobileWindowNav").forEach((node) => {
      try { node.remove(); } catch (e) {}
    });
    if (win.classList && win.classList.contains("ecProfileWindow")) {
      win.dataset.mobileWindowDecorated = "profile-skipped";
    }
  }

  function syncMobileProfileEditAvailability(win) {
    if (!win || !win.classList || !win.classList.contains("ecProfileWindow")) return;
    const editBtn = win.querySelector(".ym-mobileProfileEditBtn");
    const hasEditorActions = !!win.querySelector("[data-profile-open-editor]");
    win.classList.toggle("has-mobile-profile-edit", hasEditorActions);
    if (editBtn) {
      editBtn.disabled = !hasEditorActions;
      editBtn.setAttribute("aria-disabled", hasEditorActions ? "false" : "true");
      editBtn.title = hasEditorActions ? "Edit your profile sections" : "Open your own profile to edit";
      if (!hasEditorActions) {
        win.classList.remove("is-mobile-profile-edit-open");
        editBtn.setAttribute("aria-expanded", "false");
      }
    }
  }

  function decorateMobileWindow(win) {
    if (!win || win.nodeType !== 1 || !win.classList.contains("ym-window")) return;
    if (win.classList.contains("ecProfileWindow")) {
      removeGenericMobileWindowNav(win);
      return;
    }
    const kind = String(win.dataset.kind || "").trim();
    if (!["dm", "group"].includes(kind)) return;
    win.setAttribute("aria-modal", root.classList.contains("is-mobile-shell") ? "true" : "false");
    if (kind === "dm") win.dataset.mobileSheet = "pm";
    if (kind === "group") win.dataset.mobileSheet = "group";
    if (win.dataset.mobileWindowDecorated === "1") return;
    win.dataset.mobileWindowDecorated = "1";

    const titlebar = win.querySelector(".ym-titlebar");
    if (!titlebar) return;

    const navBar = document.createElement("div");
    navBar.className = "ym-mobileWindowNav";
    navBar.setAttribute("aria-label", kind === "group" ? "Mobile group chat actions" : "Mobile private message actions");

    const hubBtn = document.createElement("button");
    hubBtn.type = "button";
    hubBtn.className = "ym-mobileWindowNavBtn ym-mobileWindowHubBtn";
    hubBtn.textContent = "← Hub";
    hubBtn.addEventListener("click", () => minimizeMobileWindowToHub(win));
    navBar.appendChild(hubBtn);

    if (kind === "group") {
      bindMobileGroupMembersClose(win);
      const usersBtn = document.createElement("button");
      usersBtn.type = "button";
      usersBtn.className = "ym-mobileWindowNavBtn ym-mobileWindowUsersBtn";
      usersBtn.textContent = "Users";
      usersBtn.setAttribute("aria-expanded", "false");
      const usersPanel = win.querySelector(".ym-groupMembersPanel");
      if (usersPanel) {
        if (!usersPanel.id) usersPanel.id = `ym-group-users-${String(win.dataset.winId || '').replace(/[^a-zA-Z0-9_-]+/g, '-')}`;
        usersPanel.setAttribute("aria-hidden", "true");
        usersBtn.setAttribute("aria-controls", usersPanel.id);
      }
      ensureMobileGroupMembersBackdrop(win);
      usersBtn.addEventListener("click", () => toggleMobileGroupMembers(win));
      navBar.appendChild(usersBtn);
    }

    const toolsBtn = document.createElement("button");
    toolsBtn.type = "button";
    toolsBtn.className = "ym-mobileWindowNavBtn ym-mobileWindowToolsBtn";
    toolsBtn.textContent = kind === "group" ? "Tools" : "More";
    toolsBtn.setAttribute("aria-expanded", "false");
    toolsBtn.addEventListener("click", () => toggleMobileWindowTools(win));
    navBar.appendChild(toolsBtn);

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "ym-mobileWindowNavBtn ym-mobileWindowCloseBtn";
    closeBtn.textContent = "Close";
    closeBtn.addEventListener("click", () => closeMobileWindow(win));
    navBar.appendChild(closeBtn);

    titlebar.insertAdjacentElement("afterend", navBar);

    const input = win.querySelector(".ym-input");
    if (input && input.dataset.mobileWindowFocusBound !== "1") {
      input.dataset.mobileWindowFocusBound = "1";
      input.addEventListener("focus", () => {
        win.classList.remove("is-mobile-window-tools-open");
        closeMobileGroupMembers(win);
        const btn = win.querySelector(".ym-mobileWindowToolsBtn");
        if (btn) btn.setAttribute("aria-expanded", "false");
      });
    }
  }


  function activateMobileProfileTab(win, tab) {
    if (!win) return;
    win.classList.remove("is-mobile-profile-edit-open");
    const editBtn = win.querySelector(".ym-mobileProfileEditBtn");
    if (editBtn) editBtn.setAttribute("aria-expanded", "false");
    const wanted = String(tab || "posts").trim() || "posts";
    const btn = win.querySelector(`[data-profile-tab="${wanted}"]`);
    if (btn) {
      try { btn.click(); } catch (e) {}
    }
    win.setAttribute("data-mobile-profile-tab", wanted);
    win.querySelectorAll(".ym-mobileProfileNavBtn[data-mobile-profile-tab]").forEach((node) => {
      const active = String(node.getAttribute("data-mobile-profile-tab") || "") === wanted;
      node.classList.toggle("is-active", active);
      node.setAttribute("aria-pressed", active ? "true" : "false");
    });
    try {
      const log = win.querySelector(".ym-log");
      if (log) log.scrollTop = 0;
    } catch (e) {}
  }

  function openMobileProfileEditorPanel(win, panel) {
    if (!win) return false;
    const wanted = String(panel || "").trim();
    if (!wanted) return false;
    const trigger = win.querySelector(`[data-profile-open-editor="${wanted}"]`);
    if (!trigger) {
      try { toast("Open your own profile to edit this section.", "info", 2600); } catch (e) {}
      return false;
    }
    try { trigger.click(); } catch (e) {}
    win.classList.remove("is-mobile-profile-edit-open");
    const editBtn = win.querySelector(".ym-mobileProfileEditBtn");
    if (editBtn) editBtn.setAttribute("aria-expanded", "false");
    return true;
  }

  function toggleMobileProfileEditDrawer(win) {
    if (!win) return;
    const opened = !win.classList.contains("is-mobile-profile-edit-open");
    win.classList.toggle("is-mobile-profile-edit-open", opened);
    const btn = win.querySelector(".ym-mobileProfileEditBtn");
    if (btn) btn.setAttribute("aria-expanded", opened ? "true" : "false");
  }

  function decorateMobileProfileWindow(win) {
    if (!win || win.nodeType !== 1 || !win.classList.contains("ecProfileWindow")) return;
    if (win.dataset.mobileProfileDecorated === "1") return;
    win.dataset.mobileProfileDecorated = "1";
    win.dataset.mobileProfileTab = win.dataset.mobileProfileTab || "posts";

    const titlebar = win.querySelector(".ym-titlebar");
    if (!titlebar) return;

    const navBar = document.createElement("div");
    navBar.className = "ym-mobileProfileNav";
    navBar.setAttribute("aria-label", "Mobile profile navigation");

    const hubBtn = document.createElement("button");
    hubBtn.type = "button";
    hubBtn.className = "ym-mobileProfileNavBtn ym-mobileProfileHubBtn";
    hubBtn.textContent = "← Hub";
    hubBtn.addEventListener("click", () => minimizeMobileWindowToHub(win));
    navBar.appendChild(hubBtn);

    [
      ["posts", "Posts"],
      ["about", "About"],
      ["photos", "Photos"],
      ["favorites", "Faves"],
    ].forEach(([tab, label]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ym-mobileProfileNavBtn";
      btn.textContent = label;
      btn.setAttribute("data-mobile-profile-tab", tab);
      btn.setAttribute("aria-pressed", tab === "posts" ? "true" : "false");
      btn.addEventListener("click", () => activateMobileProfileTab(win, tab));
      navBar.appendChild(btn);
    });

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "ym-mobileProfileNavBtn ym-mobileProfileEditBtn";
    editBtn.textContent = "Edit";
    editBtn.setAttribute("aria-expanded", "false");
    editBtn.addEventListener("click", () => toggleMobileProfileEditDrawer(win));
    navBar.appendChild(editBtn);

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "ym-mobileProfileNavBtn ym-mobileProfileCloseBtn";
    closeBtn.textContent = "Close";
    closeBtn.addEventListener("click", () => closeMobileWindow(win));
    navBar.appendChild(closeBtn);

    const editDrawer = document.createElement("div");
    editDrawer.className = "ym-mobileProfileEditDrawer";
    editDrawer.setAttribute("aria-label", "Edit profile sections");
    [
      ["avatar", "Avatar"],
      ["banner", "Banner"],
      ["bio", "Bio"],
      ["intro", "Intro"],
      ["favorites", "Favorites"],
      ["recent-rooms", "Privacy"],
    ].forEach(([panel, label]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ym-mobileProfileEditAction";
      btn.textContent = label;
      btn.setAttribute("data-mobile-profile-edit-panel", panel);
      btn.addEventListener("click", () => openMobileProfileEditorPanel(win, panel));
      editDrawer.appendChild(btn);
    });

    removeGenericMobileWindowNav(win);

    titlebar.insertAdjacentElement("afterend", editDrawer);
    titlebar.insertAdjacentElement("afterend", navBar);

    const log = win.querySelector(".ym-log");
    if (log && window.MutationObserver && win.dataset.mobileProfileEditObserverBound !== "1") {
      win.dataset.mobileProfileEditObserverBound = "1";
      const profileObserver = new MutationObserver(() => syncMobileProfileEditAvailability(win));
      profileObserver.observe(log, { childList: true, subtree: true });
      if (typeof registerWindowCleanup === "function") {
        try { registerWindowCleanup(win, () => profileObserver.disconnect()); } catch (e) {}
      }
    }
    syncMobileProfileEditAvailability(win);
  }

  function isMobileWindowVisible(win) {
    if (!win || win.nodeType !== 1) return false;
    if (!win.classList || !win.classList.contains("ym-window")) return false;
    if (win.classList.contains("hidden")) return false;
    return true;
  }

  function mobileWindowZ(win) {
    const z = parseInt((win && win.style && win.style.zIndex) || win?.dataset?.mobileZ || "0", 10);
    return Number.isFinite(z) ? z : 0;
  }

  function setMobileActiveWindow(win, options) {
    const opts = options || {};
    const layer = document.getElementById("windowsLayer");
    if (!layer) return;
    if (!root.classList.contains("is-mobile-shell") && !opts.forceClear) return;
    const mobile = root.classList.contains("is-mobile-shell");
    const wanted = win && isMobileWindowVisible(win) ? win : null;
    layer.querySelectorAll(".ym-window").forEach((node) => {
      const managed = node.dataset.kind === "dm" || node.dataset.kind === "group" || node.classList.contains("ecProfileWindow");
      const visible = isMobileWindowVisible(node);
      const active = !!(mobile && wanted && node === wanted && visible);
      node.classList.toggle("is-mobile-active-window", active);
      if (mobile) {
        if (visible) node.setAttribute("aria-hidden", active ? "false" : "true");
        if (managed) node.setAttribute("aria-modal", active ? "true" : "false");
        if (!active) {
          try { closeMobileWindowTools(node); } catch (e) {}
          try { closeMobileGroupMembers(node); } catch (e) {}
          try { closeMobileProfileEditDrawer(node); } catch (e) {}
        }
      } else {
        resetMobileOnlyWindowState(node);
      }
    });
  }

  function chooseTopMobileWindow() {
    const layer = document.getElementById("windowsLayer");
    if (!layer) return null;
    let top = null;
    layer.querySelectorAll(".ym-window").forEach((win) => {
      if (!isMobileWindowVisible(win)) return;
      if (!top || mobileWindowZ(win) >= mobileWindowZ(top)) top = win;
    });
    return top;
  }

  function bindMobileWindowActivation(win) {
    if (!win || win.dataset.mobileActivationBound === "1") return;
    win.dataset.mobileActivationBound = "1";
    ["pointerdown", "focusin"].forEach((eventName) => {
      win.addEventListener(eventName, () => {
        if (!root.classList.contains("is-mobile-shell")) return;
        setMobileActiveWindow(win);
      }, { passive: true });
    });
  }

  function syncMobileWindows() {
    const layer = document.getElementById("windowsLayer");
    if (!layer) return;
    let existingActive = null;
    let top = null;
    layer.querySelectorAll(".ym-window").forEach((win) => {
      decorateMobileProfileWindow(win);
      decorateMobileWindow(win);
      syncMobileProfileEditAvailability(win);
      bindMobileWindowActivation(win);
      if (!isMobileWindowVisible(win)) return;
      if (win.classList.contains("is-mobile-active-window")) existingActive = win;
      if (!top || mobileWindowZ(win) >= mobileWindowZ(top)) top = win;
    });
    if (root.classList.contains("is-mobile-shell")) {
      setMobileActiveWindow(isMobileWindowVisible(existingActive) ? existingActive : top);
    } else {
      setMobileActiveWindow(null, { forceClear: true });
    }
  }

  window.ecSyncMobileWindows = syncMobileWindows;

  if (typeof window.bringToFront === "function" && !window.bringToFront.__ecMobileWrapped) {
    const originalBringToFront = window.bringToFront;
    const wrappedBringToFront = function ecMobileBringToFront(winEl) {
      const result = originalBringToFront.apply(this, arguments);
      if (winEl && root.classList.contains("is-mobile-shell")) {
        setMobileActiveWindow(winEl);
      }
      return result;
    };
    wrappedBringToFront.__ecMobileWrapped = true;
    window.bringToFront = wrappedBringToFront;
  }

  function mobileRoomStepKey() {
    const user = String(window.USERNAME || "guest").replace(/[^a-zA-Z0-9_.-]/g, "_");
    return `hui.mobile.roomStep.${user}`;
  }

  function normalizeMobileRoomStep(step) {
    const s = String(step || "").trim();
    return MOBILE_ROOM_STEPS.includes(s) ? s : "categories";
  }

  function readMobileRoomStep() {
    try {
      return normalizeMobileRoomStep(localStorage.getItem(mobileRoomStepKey()) || "categories");
    } catch (e) {
      return "categories";
    }
  }

  function writeMobileRoomStep(step) {
    try { localStorage.setItem(mobileRoomStepKey(), normalizeMobileRoomStep(step)); } catch (e) {}
  }

  function setMobileRoomStep(step, options) {
    const opts = options || {};
    const next = normalizeMobileRoomStep(step);
    document.querySelectorAll(".roomBrowser").forEach((browser) => {
      browser.setAttribute("data-rb-mobile-step", next);
      browser.querySelectorAll(".rbMobileStep[data-rb-mobile-step]").forEach((btn) => {
        const active = String(btn.getAttribute("data-rb-mobile-step") || "") === next;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-selected", active ? "true" : "false");
      });
      const hint = browser.querySelector(".rbMobileHint");
      if (hint) hint.textContent = MOBILE_ROOM_STEP_HINTS[next] || MOBILE_ROOM_STEP_HINTS.categories;
    });
    if (!opts.skipSave) writeMobileRoomStep(next);
  }

  function syncMobileRoomBrowserStep() {
    if (!root.classList.contains("is-mobile-shell")) return;
    const browser = document.querySelector(".roomBrowser");
    const existing = browser ? browser.getAttribute("data-rb-mobile-step") : "";
    setMobileRoomStep(existing || readMobileRoomStep(), { skipSave: true });
  }

  window.ecSetMobileRoomBrowserStep = function ecSetMobileRoomBrowserStep(step, options) {
    setMobileRoomStep(step, options || {});
  };

  document.addEventListener("click", (event) => {
    const btn = event.target && event.target.closest ? event.target.closest(".rbMobileStep[data-rb-mobile-step]") : null;
    if (!btn) return;
    setMobileRoomStep(btn.getAttribute("data-rb-mobile-step") || "categories");
  });

  function setButtonState(panel) {
    if (!nav) return;
    updateMobileChatAvailability();
    nav.querySelectorAll("[data-mobile-panel]").forEach((btn) => {
      const active = btn.getAttribute("data-mobile-panel") === panel;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function scrollActiveRoomToBottom() {
    const log = document.getElementById("roomEmbedLog");
    if (!log) return;
    const run = () => {
      try { log.scrollTop = log.scrollHeight; } catch (e) {}
      updateMobileLatestButton();
    };
    if (window.requestAnimationFrame) window.requestAnimationFrame(run);
    else window.setTimeout(run, 0);
  }

  function isRoomLogNearBottom() {
    const log = document.getElementById("roomEmbedLog");
    if (!log) return true;
    return (log.scrollHeight - log.scrollTop - log.clientHeight) < 150;
  }

  function updateMobileLatestButton() {
    if (!mobileLatestBtn) return;
    const show = root.classList.contains("is-mobile-shell")
      && root.getAttribute("data-mobile-panel") === "chat"
      && hasOpenRoom()
      && !isRoomLogNearBottom();
    mobileLatestBtn.classList.toggle("is-visible", !!show);
    mobileLatestBtn.hidden = !show;
    mobileLatestBtn.setAttribute("aria-hidden", show ? "false" : "true");
  }

  function ensureMobileLatestButton() {
    const chatCol = document.querySelector(".roomEmbedChatCol");
    if (!chatCol) return null;
    mobileLatestBtn = document.getElementById("btnRoomEmbedLatestMobile");
    if (mobileLatestBtn) {
      updateMobileLatestButton();
      return mobileLatestBtn;
    }
    const btn = document.createElement("button");
    btn.id = "btnRoomEmbedLatestMobile";
    btn.className = "mobileLatestBtn";
    btn.type = "button";
    btn.textContent = "Latest ↓";
    btn.hidden = true;
    btn.setAttribute("aria-hidden", "true");
    btn.addEventListener("click", () => scrollActiveRoomToBottom());
    chatCol.appendChild(btn);
    mobileLatestBtn = btn;
    updateMobileLatestButton();
    return btn;
  }

  function closeMobileComposerTools() {
    root.classList.remove("is-mobile-compose-tools-open");
    if (mobileComposerToolsBtn) mobileComposerToolsBtn.setAttribute("aria-expanded", "false");
  }

  function ensureMobileBackButton() {
    const actions = document.querySelector(".roomEmbedActions");
    ensureMobileUsersCloseButton();
    if (!actions) return null;
    mobileBackBtn = document.getElementById("btnRoomEmbedBackMobile");
    if (mobileBackBtn) return mobileBackBtn;
    const btn = document.createElement("button");
    btn.id = "btnRoomEmbedBackMobile";
    btn.className = "miniBtn mobileBackRoomsBtn";
    btn.type = "button";
    btn.textContent = "← Rooms";
    btn.setAttribute("aria-label", "Back to room list");
    btn.addEventListener("click", () => {
      closeMobileComposerTools();
      setPanel("rooms");
    });
    actions.insertBefore(btn, actions.firstChild || null);
    mobileBackBtn = btn;
    return btn;
  }

  function ensureMobileComposerTools() {
    const composer = document.querySelector(".roomEmbedCompose");
    if (!composer) return null;
    [
      "roomEmbedFontFamily",
      "roomEmbedFontSize",
      "roomEmbedBoldBtn",
      "roomEmbedItalicBtn",
      "roomEmbedUnderlineBtn",
      "roomEmbedTextColor",
      "roomEmbedEmojiBtn",
      "roomEmbedTorrentBtn",
      "roomEmbedGifBtn",
      "btnRoomEmbedVoice",
      "btnRoomEmbedCam"
    ].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.setAttribute("data-mobile-compose-tool", "true");
    });
    mobileComposerToolsBtn = document.getElementById("btnRoomEmbedToolsMobile");
    const send = document.getElementById("roomEmbedSend");
    const btn = mobileComposerToolsBtn || document.createElement("button");
    btn.id = "btnRoomEmbedToolsMobile";
    btn.className = "ym-toolBtn mobileComposeMoreBtn";
    btn.type = "button";
    btn.textContent = "Tools";
    btn.setAttribute("aria-label", root.classList.contains("is-mobile-compose-tools-open") ? "Hide chat tools" : "Show chat tools");
    btn.setAttribute("aria-expanded", root.classList.contains("is-mobile-compose-tools-open") ? "true" : "false");
    if (btn.dataset.mobileComposerBound !== "1") {
      btn.dataset.mobileComposerBound = "1";
      btn.addEventListener("click", () => {
        const opened = !root.classList.contains("is-mobile-compose-tools-open");
        root.classList.toggle("is-mobile-compose-tools-open", opened);
        btn.setAttribute("aria-expanded", opened ? "true" : "false");
        btn.setAttribute("aria-label", opened ? "Hide chat tools" : "Show chat tools");
      });
    }
    if (!mobileComposerToolsBtn) {
      if (send && send.parentNode === composer) composer.insertBefore(btn, send);
      else composer.appendChild(btn);
    }
    mobileComposerToolsBtn = btn;
    return btn;
  }

  function updateMobileUsersButton() {
    if (!mobileUsersBtn) return;
    const countEl = document.getElementById("roomUsersCount");
    const count = countEl ? String(countEl.textContent || "0").trim() || "0" : "0";
    const badge = mobileUsersBtn.querySelector(".mobileUsersCount");
    if (badge) badge.textContent = count;
    mobileUsersBtn.setAttribute("aria-label", `Show room users (${count})`);
  }

  function closeMobileUsersSheet() {
    root.classList.remove("is-mobile-users-open");
    if (mobileUsersBtn) mobileUsersBtn.setAttribute("aria-expanded", "false");
  }

  function ensureMobileUsersCloseButton() {
    mobileUsersCloseBtn = document.getElementById("btnRoomEmbedUsersCloseMobile");
    if (!mobileUsersCloseBtn || mobileUsersCloseBtn.dataset.mobileCloseBound === "1") return mobileUsersCloseBtn;
    mobileUsersCloseBtn.dataset.mobileCloseBound = "1";
    mobileUsersCloseBtn.addEventListener("click", closeMobileUsersSheet);
    return mobileUsersCloseBtn;
  }

  function ensureMobileUsersButton() {
    const actions = document.querySelector(".roomEmbedActions");
    ensureMobileUsersCloseButton();
    if (!actions) return null;
    const backBtn = ensureMobileBackButton();
    mobileUsersBtn = document.getElementById("btnRoomEmbedUsersMobile");
    if (mobileUsersBtn) {
      if (backBtn && mobileUsersBtn.previousElementSibling !== backBtn) {
        actions.insertBefore(mobileUsersBtn, backBtn.nextSibling);
      }
      updateMobileUsersButton();
      return mobileUsersBtn;
    }
    const btn = document.createElement("button");
    btn.id = "btnRoomEmbedUsersMobile";
    btn.className = "miniBtn mobileUsersBtn";
    btn.type = "button";
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", "roomEmbedUsers");
    const label = document.createElement("span");
    label.textContent = "Users";
    const badge = document.createElement("span");
    badge.className = "mobileUsersCount";
    badge.textContent = "0";
    btn.appendChild(label);
    btn.appendChild(badge);
    btn.addEventListener("click", () => {
      const opened = !root.classList.contains("is-mobile-users-open");
      root.classList.toggle("is-mobile-users-open", opened);
      btn.setAttribute("aria-expanded", opened ? "true" : "false");
      updateMobileUsersButton();
    });
    if (backBtn && backBtn.nextSibling) actions.insertBefore(btn, backBtn.nextSibling);
    else if (backBtn) actions.appendChild(btn);
    else actions.insertBefore(btn, actions.firstChild || null);
    mobileUsersBtn = btn;
    updateMobileUsersButton();
    return btn;
  }

  function setPanel(panel, options) {
    const opts = options || {};
    if (!["rooms", "chat", "hub"].includes(panel)) panel = "rooms";
    if (panel === "chat" && !hasOpenRoom() && !opts.allowEmptyChat) panel = "rooms";
    root.setAttribute("data-mobile-panel", panel);
    if (panel !== "chat") {
      closeMobileUsersSheet();
      closeMobileComposerTools();
    }
    if (panel === "chat") {
      ensureMobileBackButton();
      ensureMobileUsersButton();
      ensureMobileUsersCloseButton();
      ensureMobileComposerTools();
      ensureMobileLatestButton();
      scrollActiveRoomToBottom();
    }
    if (panel === "hub") {
      bindMobileHubActions();
      syncMobileHubSection();
    }
    setButtonState(panel);
    if (!opts.skipSave) writePanel(panel);
  }

  function syncViewportMetrics() {
    if (!root.classList.contains("is-mobile-shell")) return;
    const vv = window.visualViewport || null;
    const h = vv && vv.height ? vv.height : (window.innerHeight || document.documentElement.clientHeight || 0);
    const w = vv && vv.width ? vv.width : (window.innerWidth || document.documentElement.clientWidth || 0);
    const roundedH = Math.max(320, Math.round(h || 0));
    const roundedW = Math.max(280, Math.round(w || 0));
    const setVar = (node) => {
      if (!node || !node.style) return;
      node.style.setProperty("--ecMobileViewportH", `${roundedH}px`);
      node.style.setProperty("--ecMobileViewportW", `${roundedW}px`);
    };
    setVar(html);
    setVar(document.body);
    setVar(root);
  }

  function setMobile(enabled) {
    root.classList.toggle("is-mobile-shell", !!enabled);
    if (html) html.classList.toggle("ec-mobile-runtime-active", !!enabled);
    document.body.classList.toggle("ec-mobile-shell-active", !!enabled);
    if (nav) {
      if (enabled) nav.removeAttribute("hidden");
      else nav.setAttribute("hidden", "");
    }
    if (enabled) {
      syncViewportMetrics();
      ensureMobileBackButton();
      ensureMobileUsersButton();
      ensureMobileUsersCloseButton();
      ensureMobileComposerTools();
      ensureMobileLatestButton();
      updateMobileUsersButton();
      updateMobileLatestButton();
      bindMobileHubActions();
      syncMobileWindows();
      syncMobileRoomBrowserStep();
      syncMobileHubSection();
      const current = root.getAttribute("data-mobile-panel") || readPanel();
      setPanel(current, { skipSave: true });
    } else {
      root.removeAttribute("data-mobile-panel");
      root.removeAttribute("data-mobile-hub-section");
      root.classList.remove("is-mobile-users-open");
      root.classList.remove("is-mobile-compose-tools-open");
      if (html) {
        html.style.removeProperty("--ecMobileViewportH");
        html.style.removeProperty("--ecMobileViewportW");
      }
      if (document.body && document.body.style) {
        document.body.style.removeProperty("--ecMobileViewportH");
        document.body.style.removeProperty("--ecMobileViewportW");
      }
      root.style.removeProperty("--ecMobileViewportH");
      root.style.removeProperty("--ecMobileViewportW");
      if (mobileUsersBtn) mobileUsersBtn.setAttribute("aria-expanded", "false");
      if (mobileComposerToolsBtn) mobileComposerToolsBtn.setAttribute("aria-expanded", "false");
      updateMobileLatestButton();
      setMobileActiveWindow(null, { forceClear: true });
      document.querySelectorAll('.ym-window').forEach((win) => resetMobileOnlyWindowState(win));
    }
  }

  function syncKeyboardHint() {
    const shortViewport = !!(keyboardMq && keyboardMq.matches);
    const visualShort = window.visualViewport
      ? (window.visualViewport.height < Math.max(420, window.innerHeight * 0.72))
      : false;
    document.body.classList.toggle("ec-mobile-keyboard-open", root.classList.contains("is-mobile-shell") && (shortViewport || visualShort));
  }

  function syncAfterViewportChange(options) {
    const opts = options || {};
    syncViewportMetrics();
    syncKeyboardHint();
    if (root.classList.contains("is-mobile-shell")) {
      syncMobileWindows();
      updateMobileLatestButton();
      if (opts.scrollChat && root.getAttribute("data-mobile-panel") === "chat" && isRoomLogNearBottom()) {
        scrollActiveRoomToBottom();
      }
    }
  }

  let mobileViewportSyncRaf = 0;
  function scheduleMobileViewportSync(options) {
    const opts = options || {};
    if (mobileViewportSyncRaf && window.cancelAnimationFrame) window.cancelAnimationFrame(mobileViewportSyncRaf);
    const run = () => {
      mobileViewportSyncRaf = 0;
      setMobile(isMobileNow());
      syncAfterViewportChange(opts);
    };
    if (window.requestAnimationFrame) mobileViewportSyncRaf = window.requestAnimationFrame(run);
    else window.setTimeout(run, 0);
  }

  function sync() {
    setMobile(isMobileNow());
    syncAfterViewportChange({ scrollChat: true });
  }

  if (nav) {
    nav.addEventListener("click", (event) => {
      const btn = event.target && event.target.closest ? event.target.closest("[data-mobile-panel]") : null;
      if (!btn || btn.disabled || btn.getAttribute("aria-disabled") === "true") return;
      setPanel(btn.getAttribute("data-mobile-panel") || "rooms");
    });
  }

  const room = document.getElementById("roomEmbed");
  if (room && window.MutationObserver) {
    const observer = new MutationObserver(() => {
      if (!root.classList.contains("is-mobile-shell")) return;
      const current = root.getAttribute("data-mobile-panel");
      updateMobileChatAvailability();
      if (hasOpenRoom() && (!current || current === "rooms")) {
        setPanel("chat", { skipSave: true });
      } else if (!hasOpenRoom() && current === "chat") {
        closeMobileUsersSheet();
        closeMobileComposerTools();
        updateMobileLatestButton();
        setPanel("rooms", { skipSave: true });
      }
    });
    observer.observe(room, { attributes: true, attributeFilter: ["class"] });
  }

  const usersCount = document.getElementById("roomUsersCount");
  if (usersCount && window.MutationObserver) {
    const countObserver = new MutationObserver(updateMobileUsersButton);
    countObserver.observe(usersCount, { childList: true, characterData: true, subtree: true });
  }

  const roomLog = document.getElementById("roomEmbedLog");
  if (roomLog && window.MutationObserver) {
    const logObserver = new MutationObserver(() => {
      if (root.classList.contains("is-mobile-shell") && root.getAttribute("data-mobile-panel") === "chat") {
        const nearBottom = (roomLog.scrollHeight - roomLog.scrollTop - roomLog.clientHeight) < 140;
        if (nearBottom) scrollActiveRoomToBottom();
        else updateMobileLatestButton();
      }
    });
    logObserver.observe(roomLog, { childList: true, subtree: true });
  }
  if (roomLog) {
    roomLog.addEventListener("scroll", updateMobileLatestButton, { passive: true });
  }
  const windowsLayer = document.getElementById("windowsLayer");
  if (windowsLayer && window.MutationObserver) {
    let mobileWindowSyncTimer = 0;
    const scheduleMobileWindowSync = () => {
      if (!root.classList.contains("is-mobile-shell")) return;
      if (mobileWindowSyncTimer) window.clearTimeout(mobileWindowSyncTimer);
      mobileWindowSyncTimer = window.setTimeout(() => {
        mobileWindowSyncTimer = 0;
        syncMobileWindows();
      }, 30);
    };
    const winObserver = new MutationObserver(scheduleMobileWindowSync);
    winObserver.observe(windowsLayer, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style", "aria-hidden"] });
  }


  const roomInput = document.getElementById("roomEmbedInput");
  if (roomInput) {
    roomInput.addEventListener("focus", () => {
      if (root.classList.contains("is-mobile-shell")) {
        setPanel("chat", { skipSave: true, allowEmptyChat: true });
        closeMobileComposerTools();
        window.setTimeout(scrollActiveRoomToBottom, 160);
      }
    }, { passive: true });
  }

  const roomSend = document.getElementById("roomEmbedSend");
  if (roomSend) {
    roomSend.addEventListener("click", () => {
      if (root.classList.contains("is-mobile-shell")) closeMobileComposerTools();
    }, { passive: true });
  }

  document.addEventListener("click", (event) => {
    if (!root.classList.contains("is-mobile-shell")) return;
    if (!root.classList.contains("is-mobile-compose-tools-open")) return;
    const target = event.target;
    if (target && target.closest && target.closest(".roomEmbedCompose")) return;
    closeMobileComposerTools();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMobileUsersSheet();
      closeMobileComposerTools();
      document.querySelectorAll('.ym-window[data-kind="group"].is-mobile-group-members-open').forEach((win) => closeMobileGroupMembers(win));
    }
  });

  if (mq) {
    if (mq.addEventListener) mq.addEventListener("change", sync);
    else if (mq.addListener) mq.addListener(sync);
  }
  if (keyboardMq) {
    if (keyboardMq.addEventListener) keyboardMq.addEventListener("change", syncKeyboardHint);
    else if (keyboardMq.addListener) keyboardMq.addListener(syncKeyboardHint);
  }
  window.addEventListener("resize", () => scheduleMobileViewportSync({ scrollChat: true }), { passive: true });
  window.addEventListener("orientationchange", () => window.setTimeout(() => scheduleMobileViewportSync({ scrollChat: true }), 80), { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", () => scheduleMobileViewportSync({ scrollChat: true }), { passive: true });
    window.visualViewport.addEventListener("scroll", () => scheduleMobileViewportSync({ scrollChat: false }), { passive: true });
  }

  document.addEventListener("DOMContentLoaded", sync, { once: true });
  sync();
})();
