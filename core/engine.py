import asyncio
import inspect
import time

from . import fs_tree

DEFAULT_BLOCK_SIZE = 5 * 1024 * 1024
DEFAULT_CONCURRENCY = 5


class StorageError(Exception):
    pass


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class Engine:
    def __init__(self, transport, cipher, fs=None, persist=None,
                 block_size=DEFAULT_BLOCK_SIZE, concurrency=DEFAULT_CONCURRENCY):
        self.transport = transport
        self.cipher = cipher
        self.fs = fs if fs is not None else fs_tree.new_fs()
        self.block_size = block_size
        self._persist = persist
        self._concurrency = concurrency
        self._semaphores = {}

    def _limit(self, account):
        if account not in self._semaphores:
            self._semaphores[account] = asyncio.Semaphore(self._concurrency)
        return self._semaphores[account]

    async def _save(self):
        if self._persist:
            await _resolve(self._persist(self.fs))

    def _get_file(self, remote_path):
        node = fs_tree.get_node(self.fs, remote_path)
        if node is None:
            raise StorageError(f"not found: {remote_path}")
        if node.get("type") != "file":
            raise StorageError(f"not a file: {remote_path}")
        return node

    async def upload_file(self, read_at, size, remote_path, accounts, progress=None):
        if not accounts:
            raise StorageError("no accounts available")
        remote_path = fs_tree.normalize(remote_path)
        name = remote_path.rsplit("/", 1)[1]
        if not name:
            raise StorageError("a file needs a name")
        existing = fs_tree.get_node(self.fs, remote_path)
        if existing is not None and existing.get("type") != "file":
            raise StorageError(f"{remote_path} is a folder")

        total = (size + self.block_size - 1) // self.block_size
        placed = [None] * total
        done = 0

        async def send_block(idx):
            nonlocal done
            account = accounts[idx % len(accounts)]
            async with self._limit(account):
                data = await _resolve(read_at(idx * self.block_size, self.block_size))
                token = self.cipher.encrypt(data)
                drive_id = await self.transport.upload(
                    account, f"{name}.chunk{idx}", token, {"type": "chunk", "order": str(idx)}
                )
            placed[idx] = [drive_id, account]
            done += 1
            if progress:
                progress(done, total, idx)

        tasks = [asyncio.ensure_future(send_block(i)) for i in range(total)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._delete_chunks([c for c in placed if c], raise_on_failure=False)
            raise

        fs_tree.add_file(self.fs, remote_path, {
            "type": "file",
            "name": name,
            "size": size,
            "encrypted": True,
            "chunks": placed,
            "timestamp": time.time(),
        })
        try:
            await self._save()
        except BaseException:
            if existing is None:
                fs_tree.remove_node(self.fs, remote_path)
            else:
                fs_tree.add_file(self.fs, remote_path, existing)
            await self._delete_chunks(placed, raise_on_failure=False)
            raise

        if existing is not None:
            await self._delete_chunks(existing.get("chunks", []), raise_on_failure=False)

    async def download_file(self, remote_path, write, progress=None):
        node = self._get_file(fs_tree.normalize(remote_path))
        chunks = node["chunks"]
        encrypted = node.get("encrypted", True)
        total = len(chunks)
        ready = {}
        next_to_write = 0
        done = 0
        flush_lock = asyncio.Lock()

        async def fetch_block(idx):
            nonlocal next_to_write, done
            drive_id, account = chunks[idx]
            async with self._limit(account):
                raw = await self.transport.download(account, drive_id)
            data = self.cipher.decrypt(raw) if encrypted else raw
            async with flush_lock:
                ready[idx] = data
                while next_to_write in ready:
                    await _resolve(write(ready.pop(next_to_write)))
                    next_to_write += 1
                done += 1
            if progress:
                progress(done, total, idx)

        tasks = [asyncio.ensure_future(fetch_block(i)) for i in range(total)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _delete_chunks(self, chunks, raise_on_failure=True, progress=None):
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
        failed = [o for o in outcomes if isinstance(o, BaseException)]
        if failed and raise_on_failure:
            raise StorageError(f"{len(failed)} of {total} blocks could not be deleted: {failed[0]}")
        return len(failed)

    async def delete_file(self, remote_path, progress=None, force=False):
        remote_path = fs_tree.normalize(remote_path)
        node = self._get_file(remote_path)
        failed = await self._delete_chunks(node["chunks"], raise_on_failure=not force, progress=progress)
        fs_tree.remove_node(self.fs, remote_path)
        await self._save()
        return failed

    async def delete_folder(self, remote_path, progress=None, force=False):
        remote_path = fs_tree.normalize(remote_path)
        node = fs_tree.get_node(self.fs, remote_path)
        if node is None:
            raise StorageError(f"not found: {remote_path}")
        if node.get("type") != "dir":
            raise StorageError(f"not a folder: {remote_path}")
        if remote_path == "/":
            raise StorageError("refusing to delete the root folder")

        for path, _ in list(fs_tree.iter_files(node, remote_path)):
            await self.delete_file(path, progress=progress, force=force)
        fs_tree.remove_node(self.fs, remote_path)
        await self._save()
