import asyncio
import os
import time

from .vault import (
    DEFAULT_N_EXP,
    Vault,
    VaultCorrupt,
    VaultError,
    VaultLocked,
    VaultNotFound,
    WrongPassword,
    read_snapshot,
)

DEFAULT_LEASE = 90


class VaultSync:
    def __init__(self, storage, clock=time.time, lease=DEFAULT_LEASE):
        self.storage = storage
        self.clock = clock
        self.lease = lease
        self.session = os.urandom(8).hex()

    def _lease(self):
        return {"owner": self.session, "expires": self.clock() + self.lease}

    def _held_by_other(self, lock):
        return bool(lock) and lock["owner"] != self.session and lock["expires"] > self.clock()

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

    async def create(self, password, accounts, master_key=None, n_exp=DEFAULT_N_EXP):
        blobs = await self._read_all(accounts)
        if any(blobs.values()):
            raise VaultError("a vault already exists in one of these accounts")
        vault = Vault.create(password, accounts, master_key, n_exp)
        await self._commit(vault, self._lease())
        return vault

    async def open(self, password, accounts):
        blobs = await self._read_all(accounts)
        found = sorted({blob for blob in blobs.values() if blob})
        if not found:
            raise VaultNotFound("no vault found in these accounts")

        unlocked, wrong_password, corrupt = [], False, None
        for blob in found:
            try:
                unlocked.append(Vault.unlock(password, blob))
            except WrongPassword:
                wrong_password = True
            except VaultCorrupt as exc:
                corrupt = exc
        if not unlocked:
            if wrong_password:
                raise WrongPassword("wrong password")
            raise corrupt

        vault = max(unlocked, key=lambda v: v.revision)
        if self._held_by_other(vault.lock):
            wait = int(vault.lock["expires"] - self.clock()) + 1
            raise VaultLocked(f"the vault is open in another session. try again in {wait}s")
        await self._commit(vault, self._lease())
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
        newest = await self._newest(vault)
        if newest is not None and newest.revision != vault.revision:
            raise VaultLocked("the vault was taken over by another session")

        revision = vault.revision + 1
        vault.lock = lock
        blob = vault.seal(revision)
        written, failed = await self._write_all(vault.accounts, blob)

        readbacks = await asyncio.gather(*(self.storage.read(a) for a in written), return_exceptions=True)
        if any(r != blob for r in readbacks if not isinstance(r, BaseException)):
            raise VaultLocked("another session saved at the same moment")

        vault.revision = revision
        return failed
