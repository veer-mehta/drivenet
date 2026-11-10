import os
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "web")
CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$")

HEADERS = [
    (
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'wasm-unsafe-eval' https://cdn.jsdelivr.net https://accounts.google.com; "
        "connect-src 'self' https://cdn.jsdelivr.net https://www.googleapis.com "
        "https://openidconnect.googleapis.com https://accounts.google.com; "
        "frame-src https://accounts.google.com; "
        "style-src 'self' https://accounts.google.com; "
        "img-src 'self' data:; worker-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    ("Cache-Control", "no-store"),
]


def read_client_id():
    value = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not value:
        try:
            with open(os.path.join(ROOT, ".env"), encoding="utf-8") as env_file:
                for line in env_file:
                    key, _, rest = line.strip().partition("=")
                    if key == "GOOGLE_CLIENT_ID":
                        value = rest.strip().strip("\"'")
        except FileNotFoundError:
            pass
    return value if CLIENT_ID.match(value) else ""


class Server(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        for name, value in HEADERS:
            self.send_header(name, value)
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/config.js":
            body = f'window.APP_CONFIG = {{ clientId: "{read_client_id()}" }};\n'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    try:
        server = Server(("127.0.0.1", port), partial(Handler, directory=SITE))
    except OSError:
        print(f"port {port} is already in use.")
        print("stop the other server, or pick another port: python dev_server.py <port>")
        print("if you do, add http://localhost:<port> as an authorized JavaScript origin in Google Cloud.")
        sys.exit(1)
    status = "found" if read_client_id() else "missing (set GOOGLE_CLIENT_ID in .env)"
    print(f"google client id: {status}")
    print(f"serving web/ on http://localhost:{port}")
    server.serve_forever()
