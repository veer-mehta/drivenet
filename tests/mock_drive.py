import itertools
import json
import re
from urllib.parse import parse_qs, unquote, urlparse


def parse_multipart(content_type, body):
    boundary = content_type.split("boundary=")[1].encode()
    parts = body.split(b"--" + boundary)
    metadata, media = parts[1], parts[2]
    meta = json.loads(metadata.partition(b"\r\n\r\n")[2][:-2])
    data = media.partition(b"\r\n\r\n")[2][:-2]
    return meta, data


class MockDrive:
    def __init__(self):
        self.accounts = {}
        self.fail_next = []
        self.requests = []
        self._ids = itertools.count(1)

    def files_of(self, account):
        return self.accounts.setdefault(account, {})

    def handle(self, method, url, headers, body):
        self.requests.append((method, url))
        if self.fail_next:
            return self.fail_next.pop(0), b"{}"
        auth = headers.get("Authorization", "")
        if not auth.startswith("Bearer tok-"):
            return 401, b'{"error": "bad token"}'
        files = self.files_of(auth[len("Bearer tok-"):])
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        path = parsed.path

        if method == "POST" and path == "/upload/drive/v3/files":
            meta, data = parse_multipart(headers["Content-Type"], body)
            file_id = f"f{next(self._ids)}"
            files[file_id] = {"name": meta["name"], "data": data, "props": meta.get("appProperties", {}),
                              "parents": meta.get("parents", [])}
            return 200, json.dumps({"id": file_id}).encode()

        if method == "PATCH" and path.startswith("/upload/drive/v3/files/"):
            file_id = unquote(path.rsplit("/", 1)[1])
            if file_id not in files:
                return 404, b"{}"
            files[file_id]["data"] = body
            return 200, json.dumps({"id": file_id}).encode()

        if method == "GET" and path == "/drive/v3/files":
            name = re.search(r"name = '([^']*)'", query["q"][0]).group(1)
            hits = [{"id": i} for i, f in files.items() if f["name"] == name and "appDataFolder" in f["parents"]]
            return 200, json.dumps({"files": hits}).encode()

        if path.startswith("/drive/v3/files/"):
            file_id = unquote(path.rsplit("/", 1)[1])
            if file_id not in files:
                return 404, b"{}"
            if method == "GET":
                return 200, files[file_id]["data"]
            if method == "DELETE":
                del files[file_id]
                return 204, b""

        return 400, b"{}"
