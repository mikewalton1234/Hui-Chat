(function(){
  'use strict';

  const DEFAULT_MIN = 15;
  const DEFAULT_MAX = 128;
  const DEFAULT_RECOMMENDED = 20;
  const DEFAULT_COMMON_WEAK = [
    'password','password1','password12','password123','password1234','passw0rd',
    'qwerty','qwerty123','letmein','welcome','welcome1','admin','admin123',
    'administrator','changeme','defaultpassword','hui','hui-chat123',
    'mikeschatserver','mikeserver','iloveyou','abc123','12345678','123456789','1234567890'
  ];

  function $(selector, root){
    try { return (root || document).querySelector(selector); } catch (_) { return null; }
  }

  function commonWeakSet(root){
    const raw = root && root.getAttribute ? root.getAttribute('data-password-common') : '';
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length) return new Set(parsed.map(v => String(v || '').toLowerCase()));
      } catch (_) { }
    }
    return new Set(DEFAULT_COMMON_WEAK);
  }

  function compact(value){
    return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
  }

  function deobfuscate(value){
    return compact(value).replace(/[013457@$!]/g, ch => ({'0':'o','1':'l','3':'e','4':'a','5':'s','7':'t','@':'a','$':'s','!':'i'}[ch] || ch));
  }

  function isRepeatedChunk(compacted){
    if (!compacted || compacted.length < DEFAULT_MIN) return false;
    const limit = Math.min(8, Math.max(1, Math.floor(compacted.length / 2)));
    for (let size = 1; size <= limit; size += 1){
      if (compacted.length % size !== 0) continue;
      const repeats = compacted.length / size;
      if (repeats < 3) continue;
      const chunk = compacted.slice(0, size);
      if (chunk && chunk.repeat(repeats) === compacted) return true;
    }
    return false;
  }

  function hasObviousSequence(compacted){
    if (!compacted || compacted.length < DEFAULT_MIN) return false;
    const sequences = ['abcdefghijklmnopqrstuvwxyz','zyxwvutsrqponmlkjihgfedcba','qwertyuiop','poiuytrewq','asdfghjkl','lkjhgfdsa','zxcvbnm','mnbvcxz','1234567890','0987654321'];
    return sequences.some(seq => {
      for (let size = 6; size <= seq.length; size += 1){
        for (let start = 0; start <= seq.length - size; start += 1){
          if (compacted.includes(seq.slice(start, start + size))) return true;
        }
      }
      return false;
    });
  }

  function isDigitPaddedWeakSeed(compacted){
    const seeds = ['administrator','defaultpassword','mikeschatserver','changeme','mikeserver','hui','iloveyou','letmein','welcome','qwerty','admin'];
    return seeds.some(seed => {
      if (compacted.startsWith(seed)){
        const rest = compacted.slice(seed.length);
        if (rest && /^[0-9]+$/.test(rest)) return true;
      }
      if (compacted.endsWith(seed)){
        const rest = compacted.slice(0, compacted.length - seed.length);
        if (rest && /^[0-9]+$/.test(rest)) return true;
      }
      return false;
    });
  }

  function isSeededWeak(password, compacted, commonWeak){
    const folded = String(password || '').toLowerCase();
    const variants = new Set([compacted, deobfuscate(password)]);
    for (const variant of variants){
      if (!variant) continue;
      if (commonWeak.has(variant)) return true;
      if (isDigitPaddedWeakSeed(variant)) return true;
      if (hasObviousSequence(variant)) return true;
    }
    return folded.includes('passw0rd');
  }

  function emailLocal(value){
    const raw = String(value || '').trim().toLowerCase();
    return raw.includes('@') ? raw.split('@')[0] : '';
  }

  function toInt(value, fallback){
    const n = parseInt(value, 10);
    return Number.isFinite(n) ? n : fallback;
  }

  function visibleValue(root, attrName){
    const selector = root.getAttribute(attrName);
    if (!selector) return '';
    const el = $(selector, document);
    return el ? (el.value || el.textContent || '') : '';
  }

  function setRule(rule, pass){
    if (!rule) return;
    rule.classList.toggle('pass', !!pass);
    rule.classList.toggle('fail', !pass);
    rule.setAttribute('aria-checked', pass ? 'true' : 'false');
    const mark = rule.querySelector('.passwordRuleMark');
    if (mark) mark.textContent = pass ? '✓' : '•';
  }

  function strengthText(result, password, min, max){
    if (!password) return 'Start typing';
    if (password.length < min) return 'Too short';
    if (password.length > max) return 'Too long';
    if (result.flags && result.flags.hasControl) return 'Remove hidden characters';
    if (result.flags && result.flags.containsContext) return 'Contains account details';
    if (result.flags && result.flags.isCommon) return 'Too common';
    if (result.flags && result.flags.isRepetitive) return 'Too repetitive';
    const score = result.score || 0;
    if (score >= 5) return 'Excellent';
    if (score >= 4) return 'Strong';
    if (score >= 3) return 'Good';
    return 'Usable';
  }

  function evaluatePassword(password, context){
    const min = context.min;
    const max = context.max;
    const recommended = context.recommended;
    const compacted = compact(password);
    const folded = String(password || '').toLowerCase();
    const username = compact(context.username);
    const emailName = compact(emailLocal(context.email));
    const serverName = compact(context.serverName);
    const hasControl = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(password || '');
    const commonWeak = context.commonWeak || new Set(DEFAULT_COMMON_WEAK);
    const isCommon = commonWeak.has(compacted) || folded.includes('password') || isSeededWeak(password, compacted, commonWeak);
    const isRepetitive = compacted.length >= min && (new Set(compacted.split('')).size <= 2 || isRepeatedChunk(compacted));
    const containsContext = [username, emailName, serverName].some(term => term.length >= 4 && compacted.includes(term));
    const longEnough = String(password || '').length >= min;
    const notTooLong = String(password || '').length <= max;
    const recommendedLength = String(password || '').length >= recommended;
    const hasWords = /\s/.test(password || '') || /[A-Za-z]{4,}/.test(password || '');
    const hasVariety = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9\s]/, /\s/].reduce((count, re) => count + (re.test(password || '') ? 1 : 0), 0);

    let score = 0;
    if (longEnough) score += 1;
    if (recommendedLength) score += 1;
    if (String(password || '').length >= 28) score += 1;
    if (hasWords) score += 1;
    if (hasVariety >= 2) score += 1;
    if (hasVariety >= 3) score += 1;
    if (isCommon || isRepetitive || containsContext || hasControl || !notTooLong) score = Math.min(score, 1);

    return {
      score: Math.max(0, Math.min(5, score)),
      flags: {
        hasControl,
        notTooLong,
        isCommon,
        isRepetitive,
        containsContext
      },
      rules: {
        length: longEnough,
        recommended: recommendedLength,
        noContext: !containsContext,
        notCommon: !isCommon && !isRepetitive,
        allowedChars: !hasControl && notTooLong
      }
    };
  }

  function initMeter(root){
    if (!root || root.dataset.passwordMeterReady === '1') return;
    const passwordSelector = root.getAttribute('data-password-input') || 'input[type="password"]';
    const confirmSelector = root.getAttribute('data-confirm-input') || '';
    const pass = $(passwordSelector, document);
    const confirm = confirmSelector ? $(confirmSelector, document) : null;
    if (!pass) return;

    root.dataset.passwordMeterReady = '1';
    const min = toInt(root.getAttribute('data-password-min'), DEFAULT_MIN);
    const max = toInt(root.getAttribute('data-password-max'), DEFAULT_MAX);
    const recommended = toInt(root.getAttribute('data-password-recommended'), DEFAULT_RECOMMENDED);
    const status = $('.passwordStrengthStatus', root);
    const bar = $('.passwordStrengthFill', root);
    const live = $('.passwordStrengthLive', root);
    const ruleEls = {
      length: $('[data-password-rule="length"]', root),
      recommended: $('[data-password-rule="recommended"]', root),
      noContext: $('[data-password-rule="no-context"]', root),
      notCommon: $('[data-password-rule="not-common"]', root),
      allowedChars: $('[data-password-rule="allowed-chars"]', root),
      match: $('[data-password-rule="match"]', root)
    };

    function update(){
      const context = {
        min, max, recommended,
        username: root.getAttribute('data-password-username') || visibleValue(root, 'data-password-username-source'),
        email: root.getAttribute('data-password-email') || visibleValue(root, 'data-password-email-source'),
        serverName: root.getAttribute('data-password-server-name') || '',
        commonWeak: commonWeakSet(root)
      };
      const value = pass.value || '';
      const result = evaluatePassword(value, context);
      const matchOk = !confirm || !confirm.value ? false : value === confirm.value;
      const pct = value ? Math.max(8, Math.round((result.score / 5) * 100)) : 0;
      const label = strengthText(result, value, min, max);

      root.dataset.strengthScore = String(result.score);
      root.dataset.strengthLabel = label.toLowerCase().replace(/\s+/g, '-');
      if (bar) bar.style.width = pct + '%';
      if (status) status.textContent = label;
      if (live) live.textContent = value ? `Password strength: ${label}` : 'Password strength checklist ready.';

      setRule(ruleEls.length, result.rules.length);
      setRule(ruleEls.recommended, result.rules.recommended);
      setRule(ruleEls.noContext, result.rules.noContext);
      setRule(ruleEls.notCommon, result.rules.notCommon);
      setRule(ruleEls.allowedChars, result.rules.allowedChars);
      setRule(ruleEls.match, matchOk);

      if (confirm) {
        if (confirm.value && value !== confirm.value) confirm.setCustomValidity('Passwords do not match.');
        else confirm.setCustomValidity('');
      }
    }

    pass.addEventListener('input', update);
    pass.addEventListener('change', update);
    if (confirm) {
      confirm.addEventListener('input', update);
      confirm.addEventListener('change', update);
    }
    const usernameSource = root.getAttribute('data-password-username-source');
    const emailSource = root.getAttribute('data-password-email-source');
    [usernameSource, emailSource].forEach(selector => {
      const source = selector ? $(selector, document) : null;
      if (source) source.addEventListener('input', update);
    });
    update();
  }

  function initAll(){
    document.querySelectorAll('[data-password-meter-root="1"]').forEach(initMeter);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAll);
  else initAll();
  window.HuiChatPasswordMeter = { initAll };
})();
