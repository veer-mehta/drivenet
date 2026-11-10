import io
import re
import zipfile

from . import fs_tree, settings as settings_module
from .drive_rest import DriveApi, FetchTransport, FetchVaultStorage
from .engine import Engine, StorageError
from .vault_sync import VaultSync


class App:
    def __init__(self, get_token, fetch, session=None):
        api = DriveApi(get_token, fetch)
        self.transport = FetchTransport(api)
        self.sync = VaultSync(FetchVaultStorage(api), session)
        self.vault = None
        self.engine = None

    def _start(self):
        self.engine = Engine(self.transport, self.vault, self._persist)

    async def _persist(self):
        await self.sync.save(self.vault)

    async def create(self, password, accounts):
        self.vault = await self.sync.create(password, list(accounts))
        self._start()

    async def unlock(self, password, accounts):
        self.vault = await self.sync.unlock(password, list(accounts))
        self._start()

    async def unlock_key(self, master_key, accounts):
        self.vault = await self.sync.unlock_with_key(master_key.encode("ascii"), list(accounts))
        self._start()

    def export_key(self):
        return self.vault.master_key.decode("ascii")

    async def begin(self, mine=""):
        await self.sync.begin(self.vault, mine)
        try:
            await self.engine.sweep()
        except Exception:
            pass

    async def open(self, password, accounts):
        await self.unlock(password, accounts)
        await self.begin()

    def abandon(self):
        self.vault = None
        self.engine = None

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
        stale = await self.sync.save(self.vault)
        if stale:
            raise StorageError(
                f"password changed, but the vault copy in {', '.join(stale)} could not be updated and still "
                "accepts the old password. connect that account and change the password again"
            )

    async def remove_account(self, account):
        if account not in self.vault.accounts:
            raise StorageError(f"{account} is not in this vault")
        if len(self.vault.accounts) == 1:
            raise StorageError("a vault needs at least one account")
        await self.engine.sweep()
        held = self.state()["usage"].get(account, 0)
        if held:
            raise StorageError(f"{account} still holds {held} blocks; delete those files first")
        if any(owner == account for _, owner in self.vault.trash):
            raise StorageError(f"{account} still has blocks waiting to be deleted. try again in a moment")
        try:
            await self.sync.storage.delete(account)
        except Exception as error:
            raise StorageError(f"connect {account} first so its copy of the vault can be removed ({error})") from None
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
        unfinished = []
        for path, entry in self.vault.pending.items():
            for _, account in entry["done"].values():
                usage[account] = usage.get(account, 0) + 1
            unfinished.append({
                "path": path, "size": entry["size"], "stamp": entry["stamp"],
                "done": len(entry["done"]), "total": entry["total"],
            })
        return {
            "accounts": list(self.vault.accounts),
            "files": files,
            "usage": usage,
            "tree": fs_tree.outline(fs["root"]),
            "settings": dict(self.vault.settings),
            "limits": {key: list(bounds) for key, bounds in settings_module.LIMITS.items()},
            "unfinished": unfinished,
        }

    async def set_settings(self, values):
        self.vault.settings = settings_module.clean(values, self.vault.settings)
        self.engine.reset_limits()
        await self.sync.save(self.vault)

    async def discard_upload(self, remote_path):
        await self.engine.discard_pending(remote_path)

    async def clean_orphans(self):
        return await self.engine.clean_orphans(self.vault.accounts)

    def search(self, query, exact=False, use_regex=False, filter_type=None):
        try:
            found = fs_tree.search(self.vault.fs, query, exact, use_regex, filter_type)
        except re.error as exc:
            raise ValueError(f"invalid pattern: {exc}") from None
        return [{"path": r["path"], "name": r["name"], "type": r["type"], "size": r["size"]} for r in found]

    async def upload(self, read_at, size, remote_path, progress=None, stamp=0):
        await self.engine.upload_file(read_at, size, remote_path, self.vault.accounts, progress, stamp)

    async def download(self, remote_path, write, progress=None, start=0):
        await self.engine.download_file(remote_path, write, progress, start)

    async def download_folder(self, remote_path, progress=None):
        remote_path = fs_tree.normalize(remote_path)
        node = fs_tree.get_node(self.vault.fs, remote_path)
        if node is None or node.get("type") != "dir":
            raise StorageError(f"not a folder: {remote_path}")
        base = remote_path.rstrip("/")
        files = list(fs_tree.iter_files(node, base))
        if not files:
            raise StorageError("the folder is empty")
        parent = base.rsplit("/", 1)[0]
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
