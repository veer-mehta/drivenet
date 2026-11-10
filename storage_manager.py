import asyncio
import os
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))

from core import fs_tree
from core.app import App
from core.engine import LEGACY_BLOCK_SIZE, StorageError
from core.vault import VaultError, VaultLocked, VaultNotFound, WrongPassword

HEARTBEAT_SECONDS = 30

__all__ = ["Session", "StorageError", "VaultError", "VaultLocked", "VaultNotFound", "WrongPassword"]


def _request(method, url, headers, body):
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


async def _fetch(method, url, headers, body):
    return await asyncio.to_thread(_request, method, url, headers, body)


def _progress_printer(label):
    def show(done, total, idx):
        end = "\n" if done == total else "\r"
        print(f"  {label}: {done}/{total} blocks", end=end, flush=True)

    return show


class Session:
    def __init__(self, token_for):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.app = App(token_for, _fetch)
        self.beating = None

    def _run(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()

    async def _beat(self):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await self.app.renew()
            except Exception as error:
                print(f"\nwarning: could not renew the vault lock: {error}")

    def _start_heartbeat(self):
        self.beating = asyncio.run_coroutine_threadsafe(self._beat(), self.loop)

    def open(self, password, accounts, begin=True):
        self._run(self.app.unlock(password, accounts))
        if begin:
            self.begin()

    def begin(self):
        self._run(self.app.begin())
        self._start_heartbeat()

    def create(self, password, accounts):
        self._run(self.app.create(password, accounts))
        self._start_heartbeat()

    def close(self):
        if self.beating:
            self.beating.cancel()
        if self.app.vault is not None:
            self._run(self.app.release())

    def state(self):
        return self.app.state()

    def tree_lines(self):
        lines = ["root"]

        def walk(node, prefix):
            names = list(node["children"])
            for index, name in enumerate(names):
                child = node["children"][name]
                last = index == len(names) - 1
                lines.append(prefix + ("└── " if last else "├── ") + name + ("/" if child["type"] == "dir" else ""))
                if child["type"] == "dir":
                    walk(child, prefix + ("    " if last else "│   "))

        walk(self.state()["tree"], "")
        return lines

    def search(self, query, exact=False, use_regex=False, filter_type=None):
        return self.app.search(query, exact, use_regex, filter_type)

    def upload_file(self, local_path, remote_path):
        if not os.path.isfile(local_path):
            raise StorageError(f"local file not found: {local_path}")
        if not remote_path or remote_path.endswith("/"):
            remote_path += os.path.basename(local_path)
        size = os.path.getsize(local_path)
        stamp = int(os.path.getmtime(local_path) * 1000)
        with open(local_path, "rb") as source:
            def read_at(offset, count):
                source.seek(offset)
                return source.read(count)

            self._run(self.app.upload(read_at, size, remote_path, _progress_printer(os.path.basename(local_path)), stamp))

    def upload_folder(self, local_dir, remote_dir):
        if not os.path.isdir(local_dir):
            raise StorageError(f"local folder not found: {local_dir}")
        remote_dir = (remote_dir or os.path.basename(local_dir.rstrip("/\\"))).rstrip("/")
        count = 0
        for root, _, names in os.walk(local_dir):
            relative = os.path.relpath(root, local_dir).replace(os.sep, "/")
            for name in sorted(names):
                target = remote_dir + ("" if relative == "." else "/" + relative) + "/" + name
                self.upload_file(os.path.join(root, name), target)
                count += 1
        return count

    def download_file(self, remote_path, local_path):
        local_path = local_path or os.path.basename(remote_path)
        folder = os.path.dirname(local_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        node = fs_tree.get_node(self.app.vault.fs, fs_tree.normalize(remote_path))
        if node is None or node.get("type") != "file":
            raise StorageError(f"not a file: {remote_path}")
        block_size = node.get("block_size", LEGACY_BLOCK_SIZE)
        version = str(node.get("fid") or node.get("timestamp"))
        partial, marker = local_path + ".part", local_path + ".part.id"
        start = 0
        if os.path.exists(partial) and os.path.exists(marker) and open(marker).read() == version:
            start = os.path.getsize(partial) // block_size
        with open(marker, "w") as handle:
            handle.write(version)
        with open(partial, "r+b" if start else "wb") as target:
            target.seek(start * block_size)
            target.truncate()
            if start:
                print(f"  continuing from block {start + 1}")
            self._run(self.app.download(
                remote_path, target.write, _progress_printer(os.path.basename(remote_path)), start,
            ))
        os.replace(partial, local_path)
        os.remove(marker)

    def download_folder(self, remote_path, local_dir):
        remote_path = fs_tree.normalize(remote_path)
        node = fs_tree.get_node(self.app.vault.fs, remote_path)
        if node is None or node["type"] != "dir":
            raise StorageError(f"not a folder: {remote_path}")
        base = remote_path.rstrip("/")
        root = os.path.realpath(local_dir)
        targets = []
        for path, _ in fs_tree.iter_files(node, base):
            target = os.path.join(local_dir, *path[len(base):].strip("/").split("/"))
            try:
                inside = os.path.commonpath([os.path.realpath(target), root]) == root
            except ValueError:
                inside = False
            if not inside:
                raise StorageError(f"refusing to write outside {local_dir}: {path}")
            targets.append((path, target))
        for path, target in targets:
            self.download_file(path, target)
        return len(targets)

    def delete_file(self, remote_path):
        self._run(self.app.delete_file(remote_path, _progress_printer("deleting")))

    def delete_folder(self, remote_path):
        self._run(self.app.delete_folder(remote_path, _progress_printer("deleting")))

    def add_account(self, email):
        self._run(self.app.add_account(email))

    def remove_account(self, email):
        self._run(self.app.remove_account(email))

    def change_password(self, password):
        self._run(self.app.change_password(password))

    def set_settings(self, values):
        self._run(self.app.set_settings(values))

    def discard_upload(self, remote_path):
        self._run(self.app.discard_upload(remote_path))

    def clean_orphans(self):
        return self._run(self.app.clean_orphans())
