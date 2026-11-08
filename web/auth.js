const Auth = (() => {
  const params = new URLSearchParams(location.search);
  const mock = params.get("mock");
  const storedId = (() => {
    try {
      return localStorage.getItem("drivenet.clientId") || "";
    } catch (err) {
      return "";
    }
  })();
  const clientId = params.get("client_id") || storedId || (window.APP_CONFIG && window.APP_CONFIG.clientId) || "";

  function setClientId(value) {
    try {
      if (value) localStorage.setItem("drivenet.clientId", value);
      else localStorage.removeItem("drivenet.clientId");
    } catch (err) {
      return;
    }
  }
  const scope = "openid email https://www.googleapis.com/auth/drive.appdata";
  const grants = new Map();

  function loadGoogle() {
    return new Promise((resolve, reject) => {
      if (window.google && google.accounts) return resolve();
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.onload = resolve;
      script.onerror = () => reject(new Error("could not load Google sign-in"));
      document.head.appendChild(script);
    });
  }

  async function requestGrant(hint, prompt) {
    await loadGoogle();
    return new Promise((resolve, reject) => {
      const client = google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope,
        hint,
        prompt,
        callback: (reply) => {
          if (reply.error) return reject(new Error(reply.error));
          resolve({ token: reply.access_token, expiresAt: Date.now() + reply.expires_in * 1000 });
        },
        error_callback: (err) => reject(new Error(err.type || "sign-in was cancelled")),
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
    if (mock) {
      grants.set(hint, { token: "tok-" + hint, expiresAt: Infinity });
      return hint;
    }
    const grant = await requestGrant(hint || "", hint ? "" : "select_account");
    const email = await emailOf(grant.token);
    if (hint && email !== hint) throw new Error(`signed in as ${email}, expected ${hint}`);
    grants.set(email, grant);
    return email;
  }

  async function tokenFor(email) {
    if (!isConnected(email)) {
      if (mock) {
        await connect(email);
      } else {
        const grant = await requestGrant(email, "none");
        if ((await emailOf(grant.token)) !== email) throw new Error("signed in as a different account");
        grants.set(email, grant);
      }
    }
    return grants.get(email).token;
  }

  return {
    mock,
    clientId,
    ownClientId: Boolean(storedId),
    setClientId,
    apiBase: mock || "https://www.googleapis.com",
    connect,
    isConnected,
    tokenFor,
  };
})();
