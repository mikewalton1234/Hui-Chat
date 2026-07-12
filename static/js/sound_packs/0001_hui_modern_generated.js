(function () {
  const pack = {
    id: "hui_modern_generated",
    file: "0001_hui_modern_generated.js",
    label: "Hui modern generated",
    description: "Default Hui Chat generated JavaScript UI sounds.",
    sounds: [
      { id: "soft_chime", label: "Soft chime" },
      { id: "bubble_pop", label: "Bubble pop" },
      { id: "glass_ping", label: "Glass ping" },
      { id: "retro_blip", label: "Retro blip" },
      { id: "muted_knock", label: "Muted knock" },
      { id: "arcade_coin", label: "Arcade coin" },
      { id: "mellow_pluck", label: "Mellow pluck" },
      { id: "sonar_ping", label: "Sonar ping" },
      { id: "digital_drop", label: "Digital drop" },
      { id: "doorbell_duo", label: "Doorbell duo" },
      { id: "page_flip", label: "Page flip" },
      { id: "success_twinkle", label: "Success twinkle" },
      { id: "warning_pulse", label: "Warning pulse" },
      { id: "low_buzz", label: "Low buzz" }
    ],
    play(soundId, ctx, h, kind) {
      const now = ctx.currentTime + 0.01;
      const shift = (f) => h.shift(f, kind);
      switch (soundId) {
        case "bubble_pop":
          h.tone(ctx, now, 0.055, shift(360), { type: "sine", volume: 0.032, slideTo: shift(640) });
          h.noise(ctx, now + 0.018, 0.045, { frequency: shift(1450), q: 9, volume: 0.014 });
          break;
        case "glass_ping":
          h.tone(ctx, now, 0.16, shift(880), { type: "sine", volume: 0.028 });
          h.tone(ctx, now + 0.018, 0.20, shift(1320), { type: "sine", volume: 0.014 });
          break;
        case "retro_blip":
          h.tone(ctx, now, 0.06, shift(520), { type: "triangle", volume: 0.022 });
          h.tone(ctx, now + 0.065, 0.07, shift(780), { type: "sine", volume: 0.018 });
          break;
        case "muted_knock":
          h.tone(ctx, now, 0.055, shift(180), { type: "triangle", volume: 0.035, slideTo: shift(120) });
          h.tone(ctx, now + 0.075, 0.055, shift(150), { type: "triangle", volume: 0.025, slideTo: shift(95) });
          break;
        case "arcade_coin":
          h.tone(ctx, now, 0.055, shift(988), { type: "triangle", volume: 0.02 });
          h.tone(ctx, now + 0.065, 0.09, shift(1318), { type: "sine", volume: 0.018 });
          break;
        case "mellow_pluck":
          h.chord(ctx, now, 0.13, [shift(440), shift(660)], { type: "triangle", volume: 0.018, stagger: 0.018, release: 0.09 });
          h.tone(ctx, now + 0.045, 0.16, shift(330), { type: "sine", volume: 0.011, slideTo: shift(294) });
          break;
        case "sonar_ping":
          h.tone(ctx, now, 0.22, shift(740), { type: "sine", volume: 0.022, attack: 0.004, release: 0.18 });
          h.tone(ctx, now + 0.08, 0.26, shift(1480), { type: "sine", volume: 0.008, attack: 0.005, release: 0.22 });
          break;
        case "digital_drop":
          h.tone(ctx, now, 0.075, shift(1180), { type: "sawtooth", volume: 0.014, slideTo: shift(620), release: 0.045 });
          h.tone(ctx, now + 0.07, 0.085, shift(620), { type: "triangle", volume: 0.017, slideTo: shift(420) });
          break;
        case "doorbell_duo":
          h.tone(ctx, now, 0.18, shift(523.25), { type: "sine", volume: 0.021, release: 0.12 });
          h.tone(ctx, now + 0.13, 0.22, shift(659.25), { type: "sine", volume: 0.018, release: 0.16 });
          break;
        case "page_flip":
          h.noise(ctx, now, 0.052, { filter: "highpass", frequency: shift(1900), q: 1.5, volume: 0.011 });
          h.noise(ctx, now + 0.045, 0.062, { filter: "bandpass", frequency: shift(950), q: 3, volume: 0.009 });
          break;
        case "success_twinkle":
          h.chord(ctx, now, 0.11, [shift(659.25), shift(987.77), shift(1318.51)], { type: "sine", volume: 0.013, stagger: 0.042, release: 0.085 });
          break;
        case "warning_pulse":
          h.tone(ctx, now, 0.08, shift(330), { type: "triangle", volume: 0.024 });
          h.tone(ctx, now + 0.105, 0.08, shift(330), { type: "triangle", volume: 0.02 });
          break;
        case "low_buzz":
          h.tone(ctx, now, 0.12, shift(110), { type: "sawtooth", volume: 0.015, slideTo: shift(82), release: 0.08 });
          h.noise(ctx, now + 0.015, 0.10, { filter: "lowpass", frequency: shift(420), q: 2, volume: 0.008 });
          break;
        case "soft_chime":
        default:
          h.tone(ctx, now, 0.12, shift(587.33), { type: "sine", volume: 0.026 });
          h.tone(ctx, now + 0.07, 0.16, shift(880), { type: "sine", volume: 0.018 });
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
