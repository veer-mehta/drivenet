import asyncio
import inspect
import time

from . import fs_tree
from .blockcrypt import SCHEME, BlockError, new_file_id
from .vault import VaultLocked

LEGACY_BLOCK_SIZE = 5 * 1024 * 1024
CHECKPOINT_BLOCKS = 8
CHECKPOINT_SECONDS = 6


class StorageError(Exception):
    pass


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class Engine:
    def __init__(self, transport, vault, persist):
        self.transport = transport
        self.vault = vault
        self._persist = persist
        self._semaphores = {}
        self._unsaved = 0
        self._saved_at = time.monotonic()

    @property
    def fs(self):
        return self.vault.fs

    def reset_limits(self):
        self._semaphores = {}

    def _limit(self, account):
        if account not in self._semaphores:
            self._semaphores[account] = asyncio.Semaphore(self.vault.settings["transfers"])
        return self._semaphores[account]

    async def _save(self):
        await _resolve(self._persist())
        self._unsaved = 0
        self._saved_at = time.monotonic()

    async def _checkpoint(self, force=False):
        self._unsaved += 1
        if not force and self._unsaved < CHECKPOINT_BLOCKS and time.monotonic() - self._saved_at < CHECKPOINT_SECONDS:
            return
        try:
            await self._save()
        except VaultLocked:
            raise
        except Exception:
            if force:
                raise

    def _get_file(self, remote_path):
        node = fs_tree.get_node(self.fs, remote_path)
        if node is None:
            raise StorageError(f"not found: {remote_path}")
        if node.get("type") != "file":
            raise StorageError(f"not a file: {remote_path}")
        return node

    def _begin(self, remote_path, name, size, stamp):
        pending = self.vault.pending
        block_size = int(self.vault.settings["block_mb"] * 1024 * 1024)
        entry = pending.get(remote_path)
        if entry and entry["size"] == size and entry["stamp"] == stamp:
            return entry
        if entry:
            self.vault.trash.extend(entry["done"].values())
        entry = {
            "fid": new_file_id(),
            "name": name,
            "size": size,
            "stamp": stamp,
            "block_size": block_size,
            "total": (size + block_size - 1) // block_size,
            "done": {},
            "started": time.time(),
        }
        pending[remote_path] = entry
        return entry

    async def upload_file(self, read_at, size, remote_path, accounts, progress=None, stamp=0):
        if not accounts:
            raise StorageError("no accounts available")
        try:
            remote_path = fs_tree.normalize(remote_path)
            fs_tree.check_parents(self.fs, remote_path)
        except ValueError as error:
            raise StorageError(str(error)) from None
        name = remote_path.rsplit("/", 1)[1]
        if not name:
            raise StorageError("a file needs a name")
        existing = fs_tree.get_node(self.fs, remote_path)
        if existing is not None and existing.get("type") != "file":
            raise StorageError(f"{remote_path} is a folder")

        entry = self._begin(remote_path, name, size, stamp)
        fid, total, block_size, done = entry["fid"], entry["total"], entry["block_size"], entry["done"]
        await self._checkpoint(force=True)

        async def send_block(idx):
            if str(idx) in done:
                return
            account = accounts[idx % len(accounts)]
            async with self._limit(account):
                data = await _resolve(read_at(idx * block_size, block_size))
                if len(data) != min(block_size, size - idx * block_size):
                    raise StorageError("the file changed while it was uploading")
                sealed = self.vault.cipher.seal(fid, idx, total, data)
                drive_id = await self.transport.upload(
                    account, f"{fid}.{idx}", sealed, {"type": "chunk", "order": str(idx)}
                )
            done[str(idx)] = [drive_id, account]
            if progress:
                progress(len(done), total, idx)
            await self._checkpoint()

        if progress:
            for count, saved in enumerate(sorted(done, key=int), 1):
                progress(count, total, int(saved))
        tasks = [asyncio.ensure_future(send_block(i)) for i in range(total)]
        try:
            await asyncio.gather(*tasks)
        except BaseException as error:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self._checkpoint(force=True)
            except Exception:
                pass
            if isinstance(error, VaultLocked) or not isinstance(error, Exception):
                raise
            raise StorageError(
                f"upload stopped at {len(done)} of {total} blocks ({error}). choose the same file again to continue"
            ) from error

        node = {
            "type": "file",
            "name": name,
            "size": size,
            "cipher": SCHEME,
            "fid": fid,
            "block_size": block_size,
            "chunks": [done[str(i)] for i in range(total)],
            "timestamp": time.time(),
        }
        replaced = list(existing.get("chunks", [])) if existing else []
        fs_tree.add_file(self.fs, remote_path, node)
        del self.vault.pending[remote_path]
        self.vault.trash.extend(replaced)
        try:
            await self._save()
        except BaseException:
            self.vault.pending[remote_path] = entry
            for chunk in replaced:
                self.vault.trash.remove(chunk)
            if existing is None:
                fs_tree.remove_node(self.fs, remote_path)
            else:
                fs_tree.add_file(self.fs, remote_path, existing)
            raise
        await self.sweep()

    async def download_file(self, remote_path, write, progress=None, start=0):
        remote_path = fs_tree.normalize(remote_path)
        node = self._get_file(remote_path)
        chunks = node["chunks"]
        total = len(chunks)
        scheme = node.get("cipher")
        fid = node.get("fid")
        encrypted = node.get("encrypted", True)
        block_size = node.get("block_size", LEGACY_BLOCK_SIZE)
        start = max(0, min(start, total))
        skipped = min(node.get("size", 0), start * block_size)
        written = [0]
        ready = {}
        next_to_write = start
        done = start
        flush_lock = asyncio.Lock()

        def decode(idx, raw):
            try:
                if scheme == SCHEME:
                    return self.vault.cipher.open(fid, idx, total, raw)
                return self.vault.cipher.open_legacy(raw) if encrypted else raw
            except BlockError:
                raise StorageError(f"block {idx + 1} of {remote_path} failed its integrity check") from None

        async def fetch_block(idx):
            nonlocal next_to_write, done
            drive_id, account = chunks[idx]
            async with self._limit(account):
                raw = await self.transport.download(account, drive_id)
            data = decode(idx, raw)
            async with flush_lock:
                ready[idx] = data
                while next_to_write in ready:
                    block = ready.pop(next_to_write)
                    await _resolve(write(block))
                    written[0] += len(block)
                    next_to_write += 1
                done += 1
            if progress:
                progress(done, total, idx)

        tasks = [asyncio.ensure_future(fetch_block(i)) for i in range(start, total)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if written[0] != node.get("size", written[0] + skipped) - skipped:
            raise StorageError(f"{remote_path} came back {written[0] + skipped} bytes, expected {node['size']}")

    async def _purge(self, chunks, progress=None):
        total = len(chunks)
        done = 0

        async def drop(idx, chunk):
            nonlocal done
            drive_id, account = chunk
            async with self._limit(account):
                await self.transport.delete(account, drive_id)
            done += 1
            if progress:
                progress(done, total, idx)

        outcomes = await asyncio.gather(*(drop(i, c) for i, c in enumerate(chunks)), return_exceptions=True)
        gone = [c for c, outcome in zip(chunks, outcomes) if not isinstance(outcome, BaseException)]
        self.vault.trash = [c for c in self.vault.trash if c not in gone]
        return total - len(gone)

    async def sweep(self):
        return await self._purge(list(self.vault.trash))

    async def _retire(self, chunks, restore):
        self.vault.trash.extend(chunks)
        try:
            await self._save()
        except BaseException:
            for chunk in chunks:
                self.vault.trash.remove(chunk)
            restore()
            raise

    async def delete_file(self, remote_path, progress=None):
        remote_path = fs_tree.normalize(remote_path)
        node = self._get_file(remote_path)
        fs_tree.remove_node(self.fs, remote_path)
        await self._retire(node["chunks"], lambda: fs_tree.add_file(self.fs, remote_path, node))
        await self._purge(node["chunks"], progress)

    async def delete_folder(self, remote_path, progress=None):
        remote_path = fs_tree.normalize(remote_path)
        node = fs_tree.get_node(self.fs, remote_path)
        if node is None:
            raise StorageError(f"not found: {remote_path}")
        if node.get("type") != "dir":
            raise StorageError(f"not a folder: {remote_path}")
        if remote_path == "/":
            raise StorageError("refusing to delete the root folder")

        chunks = [chunk for _, file in fs_tree.iter_files(node, remote_path) for chunk in file["chunks"]]
        abandoned = {p: e for p, e in self.vault.pending.items() if p.startswith(remote_path + "/")}
        for path in abandoned:
            del self.vault.pending[path]
            chunks += list(abandoned[path]["done"].values())
        parent, name = remote_path.rsplit("/", 1)
        siblings = fs_tree.get_node(self.fs, parent or "/")["children"]
        siblings.pop(name)

        def restore():
            siblings[name] = node
            self.vault.pending.update(abandoned)

        await self._retire(chunks, restore)
        await self._purge(chunks, progress)

    async def discard_pending(self, remote_path):
        entry = self.vault.pending.get(remote_path)
        if entry is None:
            raise StorageError(f"no unfinished upload for {remote_path}")
        del self.vault.pending[remote_path]
        chunks = list(entry["done"].values())
        await self._retire(chunks, lambda: self.vault.pending.__setitem__(remote_path, entry))
        await self._purge(chunks)

    def _referenced(self):
        held = {(drive_id, account) for _, node in fs_tree.iter_files(self.fs["root"]) for drive_id, account in node["chunks"]}
        held.update((drive_id, account) for entry in self.vault.pending.values() for drive_id, account in entry["done"].values())
        held.update((drive_id, account) for drive_id, account in self.vault.trash)
        return held

    async def clean_orphans(self, accounts):
        await self._save()
        held = self._referenced()
        found = await asyncio.gather(*(self.transport.list_chunks(a) for a in accounts), return_exceptions=True)
        stray = [
            [drive_id, account]
            for account, ids in zip(accounts, found) if not isinstance(ids, BaseException)
            for drive_id in ids if (drive_id, account) not in held
        ]
        await self._purge(stray)
        return len(stray)
