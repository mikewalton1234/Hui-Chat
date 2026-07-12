function b64FromBytes(bytes) {
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

function bytesFromB64(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// Plaintext DM compatibility wrapper (used when WebCrypto is unavailable or peer lacks keys).
// WARNING: not E2EE; should only be used as a last-resort compatibility mode.
const _DM_UTF8_ENC = new TextEncoder();
const _DM_UTF8_DEC = new TextDecoder();

function wrapPlainDm(plaintext) {
  const bytes = _DM_UTF8_ENC.encode(String(plaintext ?? ""));
  return PM_PLAINTEXT_PREFIX + b64FromBytes(bytes);
}

function unwrapPlainDm(cipher) {
  const b64 = String(cipher || "").slice(PM_PLAINTEXT_PREFIX.length);
  return _DM_UTF8_DEC.decode(bytesFromB64(b64));
}

// PEM helpers
// WebCrypto expects DER (ArrayBuffer) for pkcs8/spki imports, but we store PEM text.
// Accepts either full PEM (with BEGIN/END lines) or a raw base64 body.
function pemToArrayBuffer(pemText) {
  const pem = String(pemText || "");
  const b64 = pem
    .replace(/-----BEGIN [^-]+-----/g, "")
    .replace(/-----END [^-]+-----/g, "")
    .replace(/\s+/g, "");
  if (!b64) throw new Error("Invalid PEM (empty)");
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

async function importMyPrivateKey(encryptedPrivStr, password) {
  // Supports:
  //  - v2:<salt_b64>:<nonce_b64>:<cipher_b64> (PBKDF2->AES-256-GCM, AAD "hui:keyblob:v2")
  //  - legacy: <salt_b64>:<cipher_b64> (PBKDF2->XOR)
  if (!encryptedPrivStr || typeof encryptedPrivStr !== "string") {
    throw new Error("No encrypted private key available.");
  }

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const parts = encryptedPrivStr.split(":");

  // v2 AES-GCM (preferred)
  if (parts.length === 4 && parts[0] === "v2") {
    const salt = b64ToBytes(parts[1]);
    const nonce = b64ToBytes(parts[2]);
    const cipher = b64ToBytes(parts[3]);

    const keyMaterial = await crypto.subtle.importKey(
      "raw",
      enc.encode(password),
      { name: "PBKDF2" },
      false,
      ["deriveBits"]
    );

    const derivedBits = await crypto.subtle.deriveBits(
      {
        name: "PBKDF2",
        salt: salt,
        iterations: 390000,
        hash: "SHA-256",
      },
      keyMaterial,
      256
    );

    const aesKey = await crypto.subtle.importKey(
      "raw",
      derivedBits,
      { name: "AES-GCM" },
      false,
      ["decrypt"]
    );

    const aad = enc.encode("hui:keyblob:v2");
    const plainBuf = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: nonce, additionalData: aad, tagLength: 128 },
      aesKey,
      cipher
    );

    const privatePem = dec.decode(new Uint8Array(plainBuf));
    const privateKey = await crypto.subtle.importKey(
      "pkcs8",
      pemToArrayBuffer(privatePem),
      { name: "RSA-OAEP", hash: "SHA-256" },
      false,
      ["decrypt"]
    );
    return privateKey;
  }

  // legacy v1 XOR fallback
  if (parts.length < 2) {
    throw new Error("Invalid encrypted private key format.");
  }
  const saltB64 = parts[0];
  const cipherB64 = parts.slice(1).join(":"); // tolerate extra ':' if any
  const salt = b64ToBytes(saltB64);
  const encryptedBytes = b64ToBytes(cipherB64);

  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    enc.encode(password),
    { name: "PBKDF2" },
    false,
    ["deriveBits"]
  );

  const derivedBits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      salt: salt,
      iterations: 390000,
      hash: "SHA-256",
    },
    keyMaterial,
    256
  );
  const derivedKey = new Uint8Array(derivedBits);

  // XOR decrypt
  const decryptedBytes = new Uint8Array(encryptedBytes.length);
  for (let i = 0; i < encryptedBytes.length; i++) {
    decryptedBytes[i] = encryptedBytes[i] ^ derivedKey[i % derivedKey.length];
  }

  const privatePem = dec.decode(decryptedBytes);
  const privateKey = await crypto.subtle.importKey(
    "pkcs8",
    pemToArrayBuffer(privatePem),
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["decrypt"]
  );
  return privateKey;
}

window.myPrivateCryptoKey = null;

function ecDmPasswordScopeUser(username = null) {
  try {
    const source = (username !== null) ? username : (window.CURRENT_USER || window.USERNAME || '');
    const raw = String(source || '').trim().toLowerCase();
    if (!raw) return '';
    return raw.replace(/[^a-z0-9_.@-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 96);
  } catch {
    return '';
  }
}

function getDmPasswordStorageKey(username = null) {
  const token = ecDmPasswordScopeUser(username);
  return token ? `hui_dm_pwd_v2:${token}` : '';
}

function clearStoredDmPasswordForCurrentUser() {
  try {
    const key = getDmPasswordStorageKey();
    if (key) {
      sessionStorage.removeItem(key);
      sessionStorage.removeItem(`${key}:set_at`);
    }
    // beta.265-and-earlier global keys are deliberately cleared so the next
    // account opened in this tab cannot reuse the wrong password.
    sessionStorage.removeItem("hui_dm_pwd");
    sessionStorage.removeItem("hui_dm_pwd_set_at");
  } catch (_e) {}
}

function getStoredDmPassword() {
  try {
    const token = ecDmPasswordScopeUser();
    const key = getDmPasswordStorageKey();
    const maxAgeMs = 24 * 60 * 60 * 1000;
    if (key) {
      const pwd = sessionStorage.getItem(key) || "";
      const setAt = Number(sessionStorage.getItem(`${key}:set_at`) || 0) || 0;
      if (pwd && (!setAt || (Date.now() - setAt) <= maxAgeMs)) return pwd;
      if (pwd && setAt && (Date.now() - setAt) > maxAgeMs) {
        sessionStorage.removeItem(key);
        sessionStorage.removeItem(`${key}:set_at`);
      }
    }

    // Legacy fallback: only trust the old global password when the login page
    // also recorded the matching account token. This keeps old stale tab state
    // from trying to unlock a different user's private key.
    const legacyUser = String(sessionStorage.getItem("hui_dm_pwd_user") || '').trim().toLowerCase();
    if (token && legacyUser && legacyUser === token) {
      const legacyPwd = sessionStorage.getItem("hui_dm_pwd") || "";
      const legacySetAt = Number(sessionStorage.getItem("hui_dm_pwd_set_at") || 0) || 0;
      if (legacyPwd && (!legacySetAt || (Date.now() - legacySetAt) <= maxAgeMs)) return legacyPwd;
    }
    return "";
  } catch (_e) {
    return "";
  }
}

async function tryAutoUnlockPrivateMessages(reason = "") {
  // If already unlocked, nothing to do.
  if (window.myPrivateCryptoKey) return true;

  // Only attempt if we have everything we need.
  if (!HAS_WEBCRYPTO) return false;
  if (!window.ENCRYPTED_PRIV_KEY) return false;

  const pwd = getStoredDmPassword();
  if (!pwd) return false;

  try {
    const key = await importMyPrivateKey(window.ENCRYPTED_PRIV_KEY, pwd);
    window.myPrivateCryptoKey = key;
    UIState.unlockSkipped = false;
    // Keep password in sessionStorage for this tab/session so refreshes don't re-prompt.
    if (reason) {
      // Avoid spamming toasts: only show when reason is explicit.
      toast("Private messages ready", "ok");
    }
    return true;
  } catch (e) {
    console.error("Auto-unlock failed", e);
    clearStoredDmPasswordForCurrentUser();
    return false;
  }
}
