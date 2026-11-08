import io
import re
import zipfile

from . import fs_tree
from .drive_rest import API_BASE, DriveApi, FetchTransport, FetchVaultStorage
from .engine import DEFAULT_BLOCK_SIZE, Engine, StorageError
from .vault import DEFAULT_N_EXP
from .vault_sync import VaultSync

TRANSFERS_PER_ACCOUNT = 3


class App:
    def __init__(self, get_token, fetch, api_base=API_BASE, block_size=DEFAULT_BLOCK_SIZE,
                 n_exp=DEFAULT_N_EXP, retry_delay=0.5):
        api = DriveApi(get_token, fetch, api_base, retry_delay)
        self.transport = FetchTransport(api)
        self.sync = VaultSync(FetchVaultStorage(api))
        self.block_size = block_size
        self.n_exp = n_exp
        self.vault = None
        self.engine = None

    def _start(self):
        self.engine = Engine(
            self.transport, self.vault.cipher, fs=self.vault.fs, persist=self._persist,
            block_size=self.block_size, concurrency=TRANSFERS_PER_ACCOUNT,
        )

    async def _persist(self, fs):
        await self.sync.save(self.vault)

    async def create(self, password, accounts):
        self.vault = await self.sync.create(password, list(accounts), n_exp=self.n_exp)
        self._start()

    async def open(self, password, accounts):
        self.vault = await self.sync.open(password, list(accounts))
        self._start()

    async def renew(self):
        await self.sync.save(self.vault)

    async def release(self):
        await self.sync.release(self.vault)
        self.vault = None
        self.engine = None

    async def add_account(self, account):
        if account not in self.vault.accounts:
            self.vault.accounts.append(account)
            await self.sync.save(self.vault)

    async def change_password(self, password):
        self.vault.change_password(password)
        await self.sync.save(self.vault)

    async def remove_account(self, account):
        if account not in self.vault.accounts:
            raise StorageError(f"{account} is not in this vault")
        if len(self.vault.accounts) == 1:
            raise StorageError("a vault needs at least one account")
        held = self.state()["usage"].get(account, 0)
        if held:
            raise StorageError(f"{account} still holds {held} blocks; delete those files first")
        self.vault.accounts.remove(account)
        await self.sync.save(self.vault)

    def state(self):
        fs = self.vault.fs
        usage = {account: 0 for account in self.vault.accounts}
        files = []
        for path, node in fs_tree.iter_files(fs["root"]):
            files.append({"path": path, "size": node.get("size", 0)})
            for _, account in node["chunks"]:
                usage[account] = usage.get(account, 0) + 1
        return {
            "accounts": list(self.vault.accounts),
            "files": files,
            "usage": usage,
            "tree": fs_tree.outline(fs["root"]),
        }

    def search(self, query, exact=False, use_regex=False, filter_type=None):
        try:
            found = fs_tree.search(self.vault.fs, query, exact, use_regex, filter_type)
        except re.error as exc:
            raise ValueError(f"invalid pattern: {exc}") from None
        return [{"path": r["path"], "name": r["name"], "type": r["type"], "size": r["size"]} for r in found]

    async def upload(self, read_at, size, remote_path, progress=None):
        await self.engine.upload_file(read_at, size, remote_path, self.vault.accounts, progress)

    async def download(self, remote_path, write, progress=None):
        await self.engine.download_file(remote_path, write, progress)

    async def download_folder(self, remote_path, progress=None):
        remote_path = fs_tree.normalize(remote_path)
        node = fs_tree.get_node(self.vault.fs, remote_path)
        if node is None or node.get("type") != "dir":
            raise StorageError(f"not a folder: {remote_path}")
        files = list(fs_tree.iter_files(node, remote_path))
        if not files:
            raise StorageError("the folder is empty")
        parent = remote_path.rsplit("/", 1)[0]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            for path, _ in files:
                parts = []
                await self.engine.download_file(path, parts.append, progress)
                archive.writestr(path[len(parent) + 1:], b"".join(parts))
        return buffer.getvalue()

    async def delete_file(self, remote_path, progress=None):
        await self.engine.delete_file(remote_path, progress)

    async def delete_folder(self, remote_path, progress=None):
        await self.engine.delete_folder(remote_path, progress)
