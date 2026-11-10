import asyncio
import os
import time

from .vault import (
    Vault,
    VaultCorrupt,
    VaultError,
    VaultLocked,
    VaultNotFound,
    VaultStale,
    WrongPassword,
    read_snapshot,
)

LEASE_SECONDS = 90


class VaultSync:
    def __init__(self, storage, session=None):
        self.storage = storage
        self.session = session or os.urandom(8).hex()
        self.mine = ""
        self._writing = asyncio.Lock()

    def _lease(self):
        return {"owner": self.session, "expires": time.time() + LEASE_SECONDS}

    def _held_by_other(self, lock):
        if not lock or lock["owner"] == self.session or lock["expires"] <= time.time():
            return False
        return not (self.mine and lock["owner"].startswith(self.mine))

    async def _read_all(self, accounts):
        results = await asyncio.gather(*(self.storage.read(a) for a in accounts), return_exceptions=True)
        blobs, errors = {}, []
        for account, result in zip(accounts, results):
            if isinstance(result, BaseException):
                errors.append(result)
            else:
                blobs[account] = result
        if errors and not blobs:
            raise VaultError(f"could not read the vault from any account: {errors[0]}")
        return blobs

    async def create(self, password, accounts):
        blobs = await self._read_all(accounts)
        if any(blobs.values()):
            raise VaultError("a vault already exists in one of these accounts")
        vault = Vault.create(password, accounts)
        await self._commit(vault, self._lease())
        return vault

    async def _open(self, accounts, opener, mismatch=None):
        blobs = await self._read_all(accounts)
        found = sorted({blob for blob in blobs.values() if blob})
        if not found:
            raise VaultNotFound("no vault found in these accounts")
        unlocked, wrong_password, corrupt = [], False, None
        for blob in found:
            try:
                unlocked.append(opener(blob))
            except WrongPassword:
                wrong_password = True
            except VaultCorrupt as exc:
                corrupt = exc
        if not unlocked:
            if wrong_password:
                raise WrongPassword("wrong password")
            raise mismatch or corrupt
        return max(unlocked, key=lambda v: v.revision)

    async def unlock(self, password, accounts):
        return await self._open(accounts, lambda blob: Vault.unlock(password, blob))

    async def unlock_with_key(self, master_key, accounts):
        return await self._open(
            accounts, lambda blob: Vault.unlock_with_key(master_key, blob),
            WrongPassword("the saved session no longer matches this vault"),
        )

    def _busy(self, lock):
        wait = int(lock["expires"] - time.time()) + 1
        return VaultLocked(f"the vault is open in another session. try again in {wait}s")

    async def begin(self, vault, mine=""):
        self.mine = mine or ""
        for attempt in range(3):
            newest = await self._newest(vault)
            if newest is not None and newest.revision > vault.revision:
                if self._held_by_other(newest.lock):
                    raise self._busy(newest.lock)
                vault.adopt(newest)
            elif self._held_by_other(vault.lock):
                raise self._busy(vault.lock)
            try:
                await self._commit(vault, self._lease())
                return
            except VaultLocked:
                if attempt == 2:
                    raise

    async def open(self, password, accounts):
        vault = await self.unlock(password, accounts)
        await self.begin(vault)
        return vault

    async def save(self, vault):
        return await self._commit(vault, self._lease())

    async def release(self, vault):
        return await self._commit(vault, None)

    async def _newest(self, vault):
        blobs = await self._read_all(vault.accounts)
        newest = None
        for blob in sorted({b for b in blobs.values() if b}):
            try:
                snapshot = read_snapshot(vault.master_key, blob)
            except VaultCorrupt:
                continue
            if newest is None or snapshot.revision > newest.revision:
                newest = snapshot
        return newest

    async def _write_all(self, accounts, blob):
        if not accounts:
            raise VaultError("no accounts to save the vault to")
        results = await asyncio.gather(*(self.storage.write(a, blob) for a in accounts), return_exceptions=True)
        written = [a for a, r in zip(accounts, results) if not isinstance(r, BaseException)]
        if not written:
            raise VaultError(f"could not save the vault to any account: {results[0]}")
        return written, [a for a in accounts if a not in written]

    async def _commit(self, vault, lock):
        async with self._writing:
            return await self._write_version(vault, lock)

    async def _write_version(self, vault, lock):
        newest = await self._newest(vault)
        if newest is not None and newest.revision > vault.revision:
            if not self._held_by_other(newest.lock):
                raise VaultStale(
                    "another account holds a newer copy of the vault. lock, then sign in again with every account connected"
                )
            raise VaultLocked("the vault was taken over by another session")

        revision = vault.revision + 1
        vault.lock = lock
        blob = vault.seal(revision)
        written, failed = await self._write_all(vault.accounts, blob)

        readbacks = await asyncio.gather(*(self.storage.read(a) for a in written), return_exceptions=True)
        if any(r is not None and r != blob for r in readbacks if not isinstance(r, BaseException)):
            raise VaultLocked("another session saved at the same moment")

        vault.revision = revision
        return failed
