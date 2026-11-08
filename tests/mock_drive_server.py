import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.mock_drive import MockDrive

drive = MockDrive()


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {"Authorization": self.headers.get("Authorization", ""),
                   "Content-Type": self.headers.get("Content-Type", "")}
        status, data = drive.handle(self.command, self.path, headers, body)
        self.send_response(status)
        self._cors()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = _serve

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
