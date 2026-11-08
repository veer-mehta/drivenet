import asyncio
import inspect
import json
import os
from urllib.parse import quote, urlencode

from .transport import VAULT_FILENAME, DriveTransport, VaultStorage

API_BASE = "https://www.googleapis.com"
RETRY_STATUSES = {429, 500, 502, 503, 504}
ATTEMPTS = 4


class DriveError(Exception):
    def __init__(self, status, detail):
        super().__init__(f"drive error {status}: {detail}")
        self.status = status


def multipart(metadata, data, content_type):
    boundary = os.urandom(12).hex()
    head = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\nContent-Type: {content_type}\r\n\r\n"
    )
    return f"multipart/related; boundary={boundary}", head.encode("utf-8") + data + f"\r\n--{boundary}--".encode()


class DriveApi:
    def __init__(self, get_token, fetch, base=API_BASE, retry_delay=0.5):
        self.get_token = get_token
        self.fetch = fetch
        self.base = base
        self.retry_delay = retry_delay

    async def call(self, account, method, path, params=None, headers=None, body=None, accept=(200,)):
        url = self.base + path + ("?" + urlencode(params) if params else "")
        for attempt in range(ATTEMPTS):
            token = self.get_token(account)
            if inspect.isawaitable(token):
                token = await token
            status, data = await self.fetch(method, url, {"Authorization": f"Bearer {token}", **(headers or {})}, body)
            if status in accept:
                return status, data
            if status in RETRY_STATUSES and attempt < ATTEMPTS - 1:
                await asyncio.sleep(self.retry_delay * 2 ** attempt)
                continue
            raise DriveError(status, data[:200].decode("utf-8", "replace"))


class FetchTransport(DriveTransport):
    def __init__(self, api):
        self.api = api

    async def upload(self, account, name, data, meta=None):
        metadata = {"name": name, "parents": ["appDataFolder"], "appProperties": dict(meta or {})}
        content_type, body = multipart(metadata, data, "application/octet-stream")
        _, raw = await self.api.call(
            account, "POST", "/upload/drive/v3/files",
            {"uploadType": "multipart", "fields": "id"}, {"Content-Type": content_type}, body,
        )
        return json.loads(raw)["id"]

    async def download(self, account, drive_id):
        _, raw = await self.api.call(account, "GET", f"/drive/v3/files/{quote(drive_id)}", {"alt": "media"})
        return raw

    async def delete(self, account, drive_id):
        await self.api.call(account, "DELETE", f"/drive/v3/files/{quote(drive_id)}", accept=(200, 204, 404))


class FetchVaultStorage(VaultStorage):
    def __init__(self, api):
        self.api = api

    async def _find(self, account):
        _, raw = await self.api.call(
            account, "GET", "/drive/v3/files",
            {"spaces": "appDataFolder", "q": f"name = '{VAULT_FILENAME}'", "fields": "files(id)", "orderBy": "createdTime"},
        )
        files = json.loads(raw)["files"]
        return files[0]["id"] if files else None

    async def read(self, account):
        file_id = await self._find(account)
        if file_id is None:
            return None
        _, raw = await self.api.call(account, "GET", f"/drive/v3/files/{quote(file_id)}", {"alt": "media"})
        return raw.decode("utf-8")

    async def write(self, account, blob):
        file_id = await self._find(account)
        data = blob.encode("utf-8")
        if file_id:
            await self.api.call(
                account, "PATCH", f"/upload/drive/v3/files/{quote(file_id)}",
                {"uploadType": "media"}, {"Content-Type": "text/plain"}, data,
            )
        else:
            content_type, body = multipart({"name": VAULT_FILENAME, "parents": ["appDataFolder"]}, data, "text/plain")
            await self.api.call(
                account, "POST", "/upload/drive/v3/files",
                {"uploadType": "multipart"}, {"Content-Type": content_type}, body,
            )
