import asyncio
import itertools
import random

from core.transport import DriveTransport, VaultStorage


class FakeVaultStorage(VaultStorage):
    def __init__(self):
        self.blobs = {}
        self.fail_read = set()
        self.fail_write = set()

    async def read(self, account):
        if account in self.fail_read:
            raise RuntimeError(f"{account} unreachable")
        return self.blobs.get(account)

    async def write(self, account, blob):
        if account in self.fail_write:
            raise RuntimeError(f"{account} unreachable")
        self.blobs[account] = blob


class FakeTransport(DriveTransport):
    def __init__(self, jitter=0.0):
        self.files = {}
        self.jitter = jitter
        self.fail_uploads_after = None
        self.fail_deletes = False
        self.uploads = 0
        self._ids = itertools.count(1)

    async def _wait(self):
        if self.jitter:
            await asyncio.sleep(random.random() * self.jitter)

    async def upload(self, account, name, data, meta=None):
        await self._wait()
        if self.fail_uploads_after is not None and self.uploads >= self.fail_uploads_after:
            raise RuntimeError("simulated upload failure")
        self.uploads += 1
        drive_id = f"id{next(self._ids)}"
        self.files[(account, drive_id)] = bytes(data)
        return drive_id

    async def download(self, account, drive_id):
        await self._wait()
        return self.files[(account, drive_id)]

    async def delete(self, account, drive_id):
        await self._wait()
        if self.fail_deletes:
            raise RuntimeError("simulated delete failure")
        self.files.pop((account, drive_id), None)
