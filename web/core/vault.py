import base64
import json
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import fs_tree, settings as settings_module
from .blockcrypt import BlockCipher

MAGIC = "ddv1"
DEFAULT_N_EXP = 17
MIN_N_EXP, MAX_N_EXP = 4, 18
SALT_SIZE = 16


class VaultError(Exception):
    pass


class VaultNotFound(VaultError):
    pass


class WrongPassword(VaultError):
    pass


class VaultCorrupt(VaultError):
    pass


class VaultLocked(VaultError):
    pass


class VaultStale(VaultLocked):
    pass


def derive_key(password, salt, n_exp):
    kdf = Scrypt(salt=salt, length=32, n=2 ** n_exp, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


@dataclass(frozen=True)
class Header:
    n_exp: int
    salt: bytes
    wrapped_key: str

    @staticmethod
    def new(password, master_key, n_exp=DEFAULT_N_EXP):
        salt = os.urandom(SALT_SIZE)
        wrapped = Fernet(derive_key(password, salt, n_exp)).encrypt(master_key).decode("ascii")
        return Header(n_exp, salt, wrapped)

    def unwrap(self, password):
        try:
            return Fernet(derive_key(password, self.salt, self.n_exp)).decrypt(self.wrapped_key.encode("ascii"))
        except InvalidToken:
            raise WrongPassword("wrong password") from None


@dataclass(frozen=True)
class Snapshot:
    header: Header
    revision: int
    accounts: list
    fs: dict
    lock: dict
    pending: dict
    trash: list
    settings: dict


def _pack(header, contents_token):
    salt = base64.urlsafe_b64encode(header.salt).decode("ascii")
    return f"{MAGIC}.{header.n_exp}.{salt}.{header.wrapped_key}.{contents_token}"


def _unpack(blob):
    try:
        magic, n_exp, salt, wrapped, contents = blob.strip().split(".", 4)
        if magic != MAGIC:
            raise ValueError("unknown format")
        n_exp = int(n_exp)
        if not MIN_N_EXP <= n_exp <= MAX_N_EXP:
            raise ValueError("unreasonable cost parameter")
        return Header(n_exp, base64.urlsafe_b64decode(salt), wrapped), contents
    except ValueError as exc:
        raise VaultCorrupt(f"not a valid vault: {exc}") from None


def read_snapshot(master_key, blob):
    header, contents = _unpack(blob)
    try:
        data = json.loads(Fernet(master_key).decrypt(contents.encode("ascii")))
        return Snapshot(
            header, int(data["revision"]), list(data["accounts"]), data["fs"], data.get("lock"),
            dict(data.get("pending") or {}), list(data.get("trash") or []), settings_module.clean(data.get("settings")),
        )
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise VaultCorrupt("vault contents are damaged") from None


class Vault:
    def __init__(self, master_key, header, accounts, fs, revision=0, lock=None, pending=None, trash=None, settings=None):
        self.master_key = master_key
        self.header = header
        self.accounts = list(accounts)
        self.fs = fs
        self.revision = revision
        self.lock = lock
        self.pending = pending if pending is not None else {}
        self.trash = trash if trash is not None else []
        self.settings = settings if settings is not None else settings_module.clean(None)
        self.cipher = BlockCipher(master_key)

    @classmethod
    def create(cls, password, accounts=()):
        master_key = Fernet.generate_key()
        return cls(master_key, Header.new(password, master_key), accounts, fs_tree.new_fs())

    @classmethod
    def unlock(cls, password, blob):
        header, _ = _unpack(blob)
        master_key = header.unwrap(password)
        snap = read_snapshot(master_key, blob)
        vault = cls(master_key, header, snap.accounts, snap.fs, snap.revision, snap.lock, snap.pending, snap.trash, snap.settings)
        if header.n_exp < vault.settings["kdf"]:
            vault.header = Header.new(password, master_key, vault.settings["kdf"])
        return vault

    @classmethod
    def unlock_with_key(cls, master_key, blob):
        header, _ = _unpack(blob)
        snap = read_snapshot(master_key, blob)
        return cls(master_key, header, snap.accounts, snap.fs, snap.revision, snap.lock, snap.pending, snap.trash, snap.settings)

    def adopt(self, snapshot):
        self.header = snapshot.header
        self.accounts = list(snapshot.accounts)
        self.fs = snapshot.fs
        self.revision = snapshot.revision
        self.lock = snapshot.lock
        self.pending = snapshot.pending
        self.trash = snapshot.trash
        self.settings = snapshot.settings

    def change_password(self, new_password):
        self.header = Header.new(new_password, self.master_key, self.settings["kdf"])

    def seal(self, revision):
        contents = json.dumps({
            "revision": revision,
            "accounts": self.accounts,
            "fs": self.fs,
            "lock": self.lock,
            "pending": self.pending,
            "trash": self.trash,
            "settings": self.settings,
        })
        token = Fernet(self.master_key).encrypt(contents.encode("utf-8")).decode("ascii")
        return _pack(self.header, token)
