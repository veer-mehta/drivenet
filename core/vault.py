import base64
import json
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import fs_tree

MAGIC = "ddv1"
DEFAULT_N_EXP = 15
MIN_N_EXP, MAX_N_EXP = 4, 20
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
        return Snapshot(header, int(data["revision"]), list(data["accounts"]), data["fs"], data.get("lock"))
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise VaultCorrupt("vault contents are damaged") from None


class Vault:
    def __init__(self, master_key, header, accounts, fs, revision=0, lock=None):
        self.master_key = master_key
        self.header = header
        self.accounts = list(accounts)
        self.fs = fs
        self.revision = revision
        self.lock = lock

    @classmethod
    def create(cls, password, accounts=(), master_key=None, n_exp=DEFAULT_N_EXP):
        master_key = master_key or Fernet.generate_key()
        Fernet(master_key)
        return cls(master_key, Header.new(password, master_key, n_exp), accounts, fs_tree.new_fs())

    @classmethod
    def unlock(cls, password, blob):
        header, _ = _unpack(blob)
        master_key = header.unwrap(password)
        snap = read_snapshot(master_key, blob)
        return cls(master_key, header, snap.accounts, snap.fs, snap.revision, snap.lock)

    @property
    def cipher(self):
        return Fernet(self.master_key)

    def change_password(self, new_password):
        self.header = Header.new(new_password, self.master_key, self.header.n_exp)

    def seal(self, revision):
        contents = json.dumps({
            "revision": revision,
            "accounts": self.accounts,
            "fs": self.fs,
            "lock": self.lock,
        })
        token = Fernet(self.master_key).encrypt(contents.encode("utf-8")).decode("ascii")
        return _pack(self.header, token)
