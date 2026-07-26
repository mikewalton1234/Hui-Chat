(() => {
  "use strict";

  const screen = document.getElementById("huiLoadingScreen");
  if (!screen) return;

  const text = document.getElementById("huiLoadingText");
  const textOnlyMode = String(screen.dataset.loadingMode || "animation").toLowerCase() === "text";

  // Only block the page for the genuinely critical startup stages. Friends,
  // groups, profile details, presence, and secondary room-browser requests are
  // allowed to finish after the usable interface is visible.
  const required = new Set(["dom", "socket", "rooms"]);
  const completed = new Set();
  const startedAt = performance?.now?.() ?? Date.now();
  const minimumVisibleMs = 700;
  const maximumVisibleMs = 5000;
  let hidden = false;
  let hideTimer = null;

  const elapsedMs = () => Math.max(0, (performance?.now?.() ?? Date.now()) - startedAt);
  const setText = (message) => {
    if (!text) return;
    text.textContent = textOnlyMode ? "Loading…" : String(message || "Loading…");
  };

  function updateText() {
    if (hidden) return;
    if (!completed.has("dom")) return setText("Starting Hui-Chat…");
    if (!completed.has("socket")) return setText("Connecting to Hui-Chat…");
    if (!completed.has("rooms")) return setText("Loading the chat interface…");
    setText("Hui-Chat is ready.");
  }

  function removeScreen(reason) {
    if (hidden) return;
    hidden = true;

    try {
      screen.dataset.hideReason = String(reason || "ready");
      screen.classList.add("isLeaving");
      document.body?.classList.remove("huiAppBooting");
      document.body?.setAttribute("aria-busy", "false");
    } catch (_) {}

    window.setTimeout(() => {
      try { screen.remove(); } catch (_) {}
    }, 320);

    try {
      window.dispatchEvent(new CustomEvent("hui:app-ready", {
        detail: {
          reason: String(reason || "ready"),
          completed: Array.from(completed),
          elapsedMs: Math.round(elapsedMs()),
        },
      }));
    } catch (_) {}
  }

  function scheduleReadyHide() {
    if (hidden || hideTimer) return;
    const delay = Math.max(0, minimumVisibleMs - elapsedMs());
    hideTimer = window.setTimeout(() => {
      hideTimer = null;
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => removeScreen("core-interface-ready"));
      });
    }, delay);
  }

  function checkReady() {
    if (hidden) return;
    updateText();
    for (const name of required) {
      if (!completed.has(name)) return;
    }
    scheduleReadyHide();
  }

  function mark(name) {
    const normalized = String(name || "").trim();
    if (!normalized || completed.has(normalized)) return;
    completed.add(normalized);
    checkReady();
  }

  function wrapAsyncFunction(functionName, markerName) {
    const original = window[functionName];
    if (typeof original !== "function" || original.__huiLoadingWrapped) return;

    const wrapped = function (...args) {
      let result;
      try {
        result = original.apply(this, args);
      } catch (error) {
        mark(markerName);
        throw error;
      }

      return Promise.resolve(result).then(
        (value) => {
          mark(markerName);
          return value;
        },
        (error) => {
          // Reveal the normal interface so its own error/reconnect controls can
          // explain a failed first-load request.
          mark(markerName);
          throw error;
        },
      );
    };

    Object.defineProperty(wrapped, "__huiLoadingWrapped", { value: true });
    window[functionName] = wrapped;
  }

  document.body?.setAttribute("aria-busy", "true");

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => mark("dom"), { once: true });
  } else {
    mark("dom");
  }

  try {
    if (window.socket?.connected) mark("socket");
    window.socket?.on?.("connect", () => mark("socket"));
  } catch (_) {}

  // The server-rendered room payload is already enough to draw the initial room
  // list. Do not keep the splash visible merely to wait for a duplicate live
  // refresh of the same data.
  if (Array.isArray(window.INIT_ROOMS)) {
    mark("rooms");
  }

  wrapAsyncFunction("getRooms", "rooms");

  // Keep observing these stages for diagnostics, but do not block the user on
  // them. They continue loading behind the revealed interface.
  wrapAsyncFunction("initRoomBrowser", "roomBrowser");
  wrapAsyncFunction("getFriends", "friends");
  wrapAsyncFunction("refreshMyGroups", "groups");
  wrapAsyncFunction("refreshMyProfileInHub", "profile");

  window.HuiLoadingSplash = Object.freeze({
    mark,
    hide: () => removeScreen("manual"),
    getState: () => ({
      required: Array.from(required),
      completed: Array.from(completed),
      hidden,
      elapsedMs: Math.round(elapsedMs()),
    }),
  });

  // A network or authentication failure must never leave the animation covering
  // Hui-Chat. Five seconds is enough to show startup without making the app feel
  // stalled; the existing reconnect banner takes over afterward.
  window.setTimeout(() => {
    if (!hidden) {
      setText("Opening Hui-Chat…");
      removeScreen("fast-safety-timeout");
    }
  }, maximumVisibleMs);

  updateText();
})();
