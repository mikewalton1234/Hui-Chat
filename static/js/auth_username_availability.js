(function(){
  'use strict';

  function $(selector, root){
    try { return (root || document).querySelector(selector); } catch (_) { return null; }
  }

  function setState(root, state, message){
    if (!root) return;
    root.dataset.usernameState = state || 'idle';
    root.classList.toggle('isAvailable', state === 'available');
    root.classList.toggle('isTaken', state === 'taken');
    root.classList.toggle('isInvalid', state === 'invalid');
    root.classList.toggle('isChecking', state === 'checking');
    root.classList.toggle('isUnknown', state === 'unknown');
    const text = $('.usernameAvailabilityText', root);
    if (text) text.textContent = message || '';
  }

  function initUsernameAvailability(root){
    if (!root || root.dataset.usernameAvailabilityReady === '1') return;
    const inputSelector = root.getAttribute('data-username-input') || 'input[name="username"]';
    const url = root.getAttribute('data-username-url') || '/api/username_available';
    const input = $(inputSelector, document);
    if (!input) return;

    root.dataset.usernameAvailabilityReady = '1';
    const minLength = Math.max(1, parseInt(root.getAttribute('data-username-min') || input.getAttribute('minlength') || '3', 10) || 3);
    const maxLength = Math.max(minLength, parseInt(root.getAttribute('data-username-max') || input.getAttribute('maxlength') || '24', 10) || 24);
    const patternMessage = input.getAttribute('title') || 'Username not allowed.';
    let timer = null;
    let seq = 0;

    async function checkNow(){
      const raw = (input.value || '').trim();
      seq += 1;
      const mySeq = seq;
      if (!raw){
        input.setCustomValidity('');
        setState(root, 'idle', 'Enter a username to check availability.');
        return;
      }
      if (raw.length < minLength){
        input.setCustomValidity('Username too short.');
        setState(root, 'invalid', `Username too short. Use at least ${minLength} characters.`);
        return;
      }
      if (raw.length > maxLength){
        input.setCustomValidity('Username too long.');
        setState(root, 'invalid', `Username too long. Use ${maxLength} characters or fewer.`);
        return;
      }
      input.setCustomValidity('');
      if (input.validity && input.validity.patternMismatch){
        input.setCustomValidity(patternMessage);
        setState(root, 'invalid', patternMessage);
        return;
      }
      setState(root, 'checking', 'Checking username…');
      try{
        const resp = await fetch(url + '?username=' + encodeURIComponent(raw), {
          method: 'GET',
          credentials: 'same-origin',
          headers: {'Accept': 'application/json'}
        });
        const data = await resp.json().catch(()=>null);
        if (mySeq !== seq) return;
        const status = data && data.status ? String(data.status) : (resp.ok ? 'unknown' : 'unknown');
        const msg = data && data.message ? String(data.message) : (resp.ok ? 'Could not read username check.' : 'Could not check username right now.');
        if (status === 'available' && data && data.available === true){
          input.setCustomValidity('');
          setState(root, 'available', msg || 'Username is available.');
        } else if (status === 'taken'){
          input.setCustomValidity('Username already exists.');
          setState(root, 'taken', msg || 'Username already exists.');
        } else if (status === 'invalid'){
          input.setCustomValidity(msg || 'Username not allowed.');
          setState(root, 'invalid', msg || 'Username not allowed.');
        } else {
          input.setCustomValidity('');
          setState(root, 'unknown', msg || 'Username availability could not be checked.');
        }
      }catch(_){
        if (mySeq !== seq) return;
        input.setCustomValidity('');
        setState(root, 'unknown', 'Could not check username right now. You can still submit; the server will verify it.');
      }
    }

    function schedule(){
      if (timer) clearTimeout(timer);
      timer = setTimeout(checkNow, 300);
    }

    input.addEventListener('input', schedule);
    input.addEventListener('change', checkNow);
    checkNow();
  }

  function initAll(){
    document.querySelectorAll('[data-username-availability-root="1"]').forEach(initUsernameAvailability);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAll);
  else initAll();
  window.HuiChatUsernameAvailability = { initAll };
})();
