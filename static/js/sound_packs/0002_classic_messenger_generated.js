(function () {
  const pack = {
    id: "classic_messenger_generated",
    file: "0002_classic_messenger_generated.js",
    label: "Classic messenger generated",
    description: "Nostalgic messenger-style UI sounds generated with JavaScript; no copied audio assets.",
    sounds: [
      { id: "classic_msg_ping", label: "Classic messenger ping" },
      { id: "classic_msg_buzz", label: "Classic messenger buzz" },
      { id: "classic_msg_knock", label: "Classic messenger knock" },
      { id: "classic_msg_door", label: "Classic messenger sign on/off" },
      { id: "classic_msg_mail", label: "Classic messenger mail" },
      { id: "classic_msg_status", label: "Classic messenger status" }
    ],
    play(soundId, ctx, h, kind) {
      const now = ctx.currentTime + 0.01;
      const shift = (f) => h.shift(f, kind);
      switch (soundId) {
        case "classic_msg_buzz":
          [0, 0.055, 0.11, 0.18].forEach((offset, idx) => {
            h.tone(ctx, now + offset, 0.045, shift(idx % 2 ? 118 : 92), { type: "sawtooth", volume: 0.014, slideTo: shift(idx % 2 ? 88 : 132), release: 0.026 });
          });
          h.noise(ctx, now + 0.015, 0.18, { filter: "lowpass", frequency: shift(240), q: 1.4, volume: 0.006 });
          break;
        case "classic_msg_knock":
          h.tone(ctx, now, 0.045, shift(205), { type: "triangle", volume: 0.027, slideTo: shift(128), release: 0.027 });
          h.tone(ctx, now + 0.082, 0.05, shift(185), { type: "triangle", volume: 0.025, slideTo: shift(112), release: 0.03 });
          h.noise(ctx, now + 0.01, 0.035, { filter: "bandpass", frequency: shift(620), q: 5, volume: 0.006 });
          break;
        case "classic_msg_door":
          h.chord(ctx, now, 0.09, [shift(392), shift(587.33)], { type: "triangle", volume: 0.013, stagger: 0.038, release: 0.055 });
          h.tone(ctx, now + 0.11, 0.10, shift(783.99), { type: "sine", volume: 0.012, release: 0.065 });
          break;
        case "classic_msg_mail":
          h.roll(ctx, now, 660, kind, { steps: [1, 1.25, 1.5], gap: 0.047, duration: 0.052, type: "triangle", volume: 0.013 });
          h.tone(ctx, now + 0.165, 0.15, shift(990), { type: "sine", volume: 0.012, release: 0.1 });
          break;
        case "classic_msg_status":
          h.tone(ctx, now, 0.045, shift(523.25), { type: "square", volume: 0.009, release: 0.025 });
          h.tone(ctx, now + 0.055, 0.045, shift(659.25), { type: "square", volume: 0.009, release: 0.025 });
          h.tone(ctx, now + 0.11, 0.075, shift(783.99), { type: "triangle", volume: 0.01, release: 0.05 });
          break;
        case "classic_msg_ping":
        default:
          h.roll(ctx, now, 740, kind, { steps: [1, 1.335], gap: 0.07, duration: 0.06, type: "square", volume: 0.011 });
          h.tone(ctx, now + 0.145, 0.12, shift(1175), { type: "triangle", volume: 0.012, release: 0.07 });
          break;
      }
    }
  };
  if (window.HuiChatSoundPacks && typeof window.HuiChatSoundPacks.register === "function") window.HuiChatSoundPacks.register(pack);
  else {
    window.EC_PENDING_SOUND_PACKS = Array.isArray(window.EC_PENDING_SOUND_PACKS) ? window.EC_PENDING_SOUND_PACKS : [];
    window.EC_PENDING_SOUND_PACKS.push(pack);
  }
})();
