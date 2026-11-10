const Auth = (() => {
  const storedId = (() => {
    try {
      return localStorage.getItem("drivenet.clientId") || "";
    } catch (err) {
      return "";
    }
  })();
  const clientId = storedId || (window.APP_CONFIG && window.APP_CONFIG.clientId) || "";
  const scope = "openid email https://www.googleapis.com/auth/drive.appdata";
  const grants = new Map();
  const GRANTS_KEY = "drivenet.grants";
  const SESSION_KEY = "drivenet.session";
  const ACCOUNT_KEY = "drivenet.account";

  function persist() {
    try {
      localStorage.setItem(GRANTS_KEY, JSON.stringify([...grants]));
    } catch (err) {
      return;
    }
  }

  try {
    for (const [email, grant] of JSON.parse(localStorage.getItem(GRANTS_KEY) || "[]")) {
      if (grant.expiresAt > Date.now() + 60000) grants.set(email, grant);
    }
  } catch (err) {
    grants.clear();
  }

  function lastAccount() {
    try {
      return localStorage.getItem(ACCOUNT_KEY) || "";
    } catch (err) {
      return "";
    }
  }

  function remember(email) {
    try {
      localStorage.setItem(ACCOUNT_KEY, email);
    } catch (err) {
      return;
    }
  }

  function forget() {
    grants.clear();
    try {
      localStorage.removeItem(GRANTS_KEY);
      localStorage.removeItem(ACCOUNT_KEY);
    } catch (err) {
      return;
    }
  }

  function browserId() {
    try {
      let id = localStorage.getItem("drivenet.browser");
      if (!id) {
        id = crypto.randomUUID().replace(/-/g, "").slice(0, 12);
        localStorage.setItem("drivenet.browser", id);
      }
      return id;
    } catch (err) {
      return "browser";
    }
  }

  function sessionId() {
    try {
      let tab = sessionStorage.getItem(SESSION_KEY);
      if (!tab) {
        tab = crypto.randomUUID().replace(/-/g, "").slice(0, 8);
        sessionStorage.setItem(SESSION_KEY, tab);
      }
      return browserId() + "-" + tab;
    } catch (err) {
      return "";
    }
  }

  function setClientId(value) {
    try {
      if (value) localStorage.setItem("drivenet.clientId", value);
      else localStorage.removeItem("drivenet.clientId");
    } catch (err) {
      return;
    }
  }

  function loadGoogle() {
    return new Promise((resolve, reject) => {
      if (window.google && google.accounts) return resolve();
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.onload = resolve;
      script.onerror = () => reject(new Error("could not load google sign-in"));
      document.head.appendChild(script);
    });
  }

  async function requestGrant(hint, prompt) {
    await loadGoogle();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("google sign-in timed out")), 20000);
      const succeed = (value) => { clearTimeout(timer); resolve(value); };
      const fail = (error) => { clearTimeout(timer); reject(error); };
      const client = google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope,
        hint,
        prompt,
        callback: (reply) => {
          if (reply.error) return fail(new Error(reply.error));
          if (!reply.scope || !reply.scope.includes("drive.appdata")) {
            return fail(new Error("google did not grant the drive app data permission. sign in again and tick every permission box"));
          }
          succeed({ token: reply.access_token, expiresAt: Date.now() + reply.expires_in * 1000 });
        },
        error_callback: (err) => fail(new Error(err.type || "sign-in was cancelled")),
      });
      client.requestAccessToken();
    });
  }

  async function emailOf(token) {
    const response = await fetch("https://openidconnect.googleapis.com/v1/userinfo", {
      headers: { Authorization: `Bearer ${token}` },
    });
    return (await response.json()).email;
  }

  function isConnected(email) {
    const grant = grants.get(email);
    return Boolean(grant) && grant.expiresAt > Date.now() + 30000;
  }

  async function connect(hint) {
    const grant = await requestGrant(hint || "", hint ? "" : "select_account");
    const email = await emailOf(grant.token);
    if (hint && email !== hint) throw new Error(`signed in as ${email}, expected ${hint}`);
    grants.set(email, grant);
    persist();
    return email;
  }

  function remembered() {
    return [...grants.keys()].find(isConnected) || "";
  }

  function expiresIn(email) {
    const grant = grants.get(email);
    return grant ? grant.expiresAt - Date.now() : -Infinity;
  }

  function knows(email) {
    return grants.has(email);
  }

  async function ensure(email, margin = 30000) {
    if (expiresIn(email) > margin) return;
    let grant;
    try {
      grant = await requestGrant(email, "");
    } catch (err) {
      throw new Error(`could not refresh your google sign-in (${err.message}). press sign in with google to continue`);
    }
    if ((await emailOf(grant.token)) !== email) throw new Error("signed in as a different account. use logout to switch accounts");
    grants.set(email, grant);
    persist();
  }

  async function tokenFor(email) {
    if (!grants.has(email)) throw new Error(`${email} is not connected`);
    if (!isConnected(email)) {
      let grant;
      try {
        grant = await requestGrant(email, "none");
      } catch (err) {
        throw new Error(`google sign-in for ${email} expired (${err.message}). click anywhere in the page to refresh it, or lock and unlock`);
      }
      if ((await emailOf(grant.token)) !== email) throw new Error("signed in as a different account");
      grants.set(email, grant);
      persist();
    }
    return grants.get(email).token;
  }

  return {
    clientId,
    ownClientId: Boolean(storedId),
    setClientId,
    connect,
    isConnected,
    tokenFor,
    remembered,
    expiresIn,
    knows,
    lastAccount,
    remember,
    forget,
    ensure,
    browserId,
    sessionId,
  };
})();


const Keep = (() => {
  const ITEM = "drivenet.keep";
  const MODE = "drivenet.keepMode";
  const MODES = ["off", "tab", "1", "7", "30"];
  const DAY = 86400000;
  const DB = "drivenet";
  const STORE = "keys";
  const NAME = "session";

  function mode() {
    try {
      const saved = localStorage.getItem(MODE);
      return MODES.includes(saved) ? saved : "30";
    } catch (err) {
      return "off";
    }
  }

  const enabled = () => mode() !== "off";

  const wait = (request) => new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

  const open = () => {
    const request = indexedDB.open(DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    return wait(request);
  };

  async function wrappingKey(create) {
    const db = await open();
    try {
      const existing = await wait(db.transaction(STORE).objectStore(STORE).get(NAME));
      if (existing || !create) return existing || null;
      const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
      await wait(db.transaction(STORE, "readwrite").objectStore(STORE).put(key, NAME));
      return key;
    } finally {
      db.close();
    }
  }

  const toText = (bytes) => btoa(String.fromCharCode(...bytes));
  const toBytes = (text) => Uint8Array.from(atob(text), (c) => c.charCodeAt(0));

  function read() {
    for (const store of [sessionStorage, localStorage]) {
      try {
        const raw = store.getItem(ITEM);
        if (raw) return JSON.parse(raw);
      } catch (err) {
        continue;
      }
    }
    return null;
  }

  function has() {
    const saved = enabled() ? read() : null;
    return Boolean(saved) && !(saved.expiresAt && saved.expiresAt < Date.now());
  }

  async function save(email, secret) {
    const chosen = mode();
    if (chosen === "off") return;
    const key = await wrappingKey(true);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(secret)));
    const blob = { email, iv: toText(iv), data: toText(data), expiresAt: chosen === "tab" ? 0 : Date.now() + Number(chosen) * DAY };
    const target = chosen === "tab" ? sessionStorage : localStorage;
    const other = chosen === "tab" ? localStorage : sessionStorage;
    other.removeItem(ITEM);
    target.setItem(ITEM, JSON.stringify(blob));
  }

  async function clear() {
    try {
      sessionStorage.removeItem(ITEM);
      localStorage.removeItem(ITEM);
      const db = await open();
      await wait(db.transaction(STORE, "readwrite").objectStore(STORE).delete(NAME));
      db.close();
    } catch (err) {
      return;
    }
  }

  async function load() {
    try {
      if (!enabled()) return null;
      const saved = read();
      if (!saved) return null;
      if (saved.expiresAt && saved.expiresAt < Date.now()) {
        await clear();
        return null;
      }
      const key = await wrappingKey(false);
      if (!key) return null;
      const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: toBytes(saved.iv) }, key, toBytes(saved.data));
      return { email: saved.email, secret: new TextDecoder().decode(plain) };
    } catch (err) {
      return null;
    }
  }

  async function setMode(next) {
    try {
      localStorage.setItem(MODE, next);
    } catch (err) {
      return;
    }
    await clear();
  }

  return { mode, enabled, has, setMode, save, load, clear };
})();
