import json
import re
import secrets
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_PORT = 8765
SCOPE = "openid email https://www.googleapis.com/auth/drive.appdata"
CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$")
WAIT_SECONDS = 240

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>drivenet</title>
<style>
  body { margin: 0; min-height: 100vh; background: #0c0c0c; color: #ccc; font: 14px/1.65 Consolas, Menlo, monospace; }
  main { width: min(44ch, 100% - 48px); margin: 20vh 0 0 max(24px, calc((100% - 104ch) / 2)); }
  h1 { margin: 0 0 40px; color: #f2f2f2; font-size: 40px; letter-spacing: -0.02em; }
  h1 span { color: #ff4136; }
  button { padding: 6px 16px; font: inherit; font-weight: 700; color: #0c0c0c; background: #f2f2f2; border: 1px solid #f2f2f2; cursor: pointer; }
  button:hover { color: #f2f2f2; background: #ff4136; border-color: #ff4136; }
  p { margin: 16px 0 0; color: #8a8a8a; }
  .err { color: #ff4136; }
</style>
</head>
<body>
<main>
  <h1>drive<span>net</span></h1>
  <button id="go" type="button">Sign in with Google</button>
  <p id="status">This signs the terminal in. Nothing leaves your computer except the sign-in itself.</p>
</main>
<script src="https://accounts.google.com/gsi/client" async defer></script>
<script>
const config = __CONFIG__;
const status = (text, bad) => { const el = document.getElementById("status"); el.textContent = text; el.className = bad ? "err" : ""; };
document.getElementById("go").onclick = () => {
  if (!window.google || !google.accounts) return status("Google sign-in is still loading. Try again in a moment.", true);
  google.accounts.oauth2.initTokenClient({
    client_id: config.clientId,
    scope: config.scope,
    hint: config.hint,
    prompt: config.hint ? "" : "select_account",
    callback: async (reply) => {
      if (reply.error) return status("Sign-in failed: " + reply.error, true);
      if (!reply.scope || !reply.scope.includes("drive.appdata")) return status("The Drive app data permission was not granted. Try again and tick every box.", true);
      const answer = await fetch("/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nonce: config.nonce, token: reply.access_token, expires_in: reply.expires_in }),
      });
      status(answer.ok ? "Done. You can close this tab and return to the terminal." : "The terminal did not accept the sign-in.", !answer.ok);
    },
    error_callback: (error) => status("Sign-in was cancelled: " + (error.type || "unknown"), true),
  }).requestAccessToken();
};
</script>
</body>
</html>
"""


class _Server(HTTPServer):
    allow_reuse_address = False


def _userinfo_email(token):
    request = urllib.request.Request(
        "https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["email"]


def _authorize(client_id, hint):
    nonce = secrets.token_urlsafe(16)
    config = json.dumps({"clientId": client_id, "hint": hint or "", "nonce": nonce, "scope": SCOPE}).replace("<", "\\u003c")
    page = PAGE.replace("__CONFIG__", config).encode("utf-8")
    received = {}

    hosts = {f"localhost:{AUTH_PORT}", f"127.0.0.1:{AUTH_PORT}"}
    origins = {f"http://{host}" for host in hosts}

    class Handler(BaseHTTPRequestHandler):
        def _local(self, body=False):
            origin = self.headers.get("Origin")
            if self.headers.get("Host", "") in hosts and (origin is None or origin in origins):
                return True
            if body:
                self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 16384))
            self.send_error(403)
            return False

        def do_GET(self):
            if not self._local():
                return
            if self.path.split("?")[0] != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            if not self._local(body=True):
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 16384:
                    raise ValueError
                body = json.loads(self.rfile.read(length))
                accepted = self.path == "/token" and secrets.compare_digest(str(body.get("nonce", "")), nonce) and bool(body.get("token"))
            except (ValueError, TypeError):
                accepted = False
            if accepted:
                received.update(token=body["token"], expires_in=int(body.get("expires_in") or 3600))
            self.send_response(200 if accepted else 400)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    try:
        server = _Server(("127.0.0.1", AUTH_PORT), Handler)
    except OSError:
        raise RuntimeError(f"port {AUTH_PORT} is busy. close whatever is using it and try again")

    server.timeout = 1
    url = f"http://localhost:{AUTH_PORT}/"
    print(f"opening {url} in your browser to sign in with google...")
    print(f"(if nothing opens, go there yourself. the origin {url.rstrip('/')} must be an authorized javascript origin for your google client id)")
    webbrowser.open(url)
    deadline = time.time() + WAIT_SECONDS
    try:
        while not received and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not received:
        raise RuntimeError("sign-in timed out")
    return received["token"], received["expires_in"]


class AccountManager:
    def __init__(self, client_id):
        if not CLIENT_ID.match(client_id or ""):
            raise ValueError("GOOGLE_CLIENT_ID is missing or not a google client id")
        self.client_id = client_id
        self.grants = {}
        self._lock = threading.Lock()

    def connect(self, hint=None):
        token, expires_in = _authorize(self.client_id, hint)
        email = _userinfo_email(token)
        if hint and email != hint:
            raise RuntimeError(f"signed in as {email}, expected {hint}")
        with self._lock:
            self.grants[email] = (token, time.time() + expires_in)
        return email

    def is_connected(self, email):
        with self._lock:
            grant = self.grants.get(email)
        return bool(grant) and grant[1] > time.time() + 60

    def token(self, email):
        with self._lock:
            grant = self.grants.get(email)
        if not grant or grant[1] <= time.time() + 15:
            raise RuntimeError(f"{email} needs to sign in again")
        return grant[0]
