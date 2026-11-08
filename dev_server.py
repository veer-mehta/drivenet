import os
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "web")


def render_headers():
    text = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    pairs = re.findall(r"name:\s*(\S+)\s*\n\s*value:\s*(.+)", text)
    return [(name, value.strip()) for name, value in pairs if name != "drivenet"]


class Handler(SimpleHTTPRequestHandler):
    headers_to_send = render_headers()
    extra_connect = os.environ.get("DEV_CONNECT_EXTRA", "")

    def end_headers(self):
        for name, value in self.headers_to_send:
            if name == "Content-Security-Policy" and self.extra_connect:
                value = value.replace("connect-src 'self'", f"connect-src 'self' {self.extra_connect}")
            self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=SITE))
    print(f"serving web/ on http://localhost:{port} with the same headers as Render")
    server.serve_forever()
