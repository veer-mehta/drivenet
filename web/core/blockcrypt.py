import base64
import os
import struct

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SCHEME = "gcm1"
VERSION = b"\x01"
NONCE_SIZE = 12
TAG_SIZE = 16
FILE_ID_SIZE = 16


class BlockError(Exception):
    pass


def new_file_id():
    return os.urandom(FILE_ID_SIZE).hex()


class BlockCipher:
    def __init__(self, master_key):
        self._master = base64.urlsafe_b64decode(master_key)
        self._legacy = Fernet(master_key)
        self._aead = {}

    def _for_file(self, file_id):
        if file_id not in self._aead:
            key = HKDF(
                algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"drivenet/file/v1/" + bytes.fromhex(file_id),
            ).derive(self._master)
            self._aead[file_id] = AESGCM(key)
        return self._aead[file_id]

    @staticmethod
    def _context(file_id, index, total):
        return bytes.fromhex(file_id) + struct.pack(">II", index, total)

    def seal(self, file_id, index, total, data):
        nonce = os.urandom(NONCE_SIZE)
        return VERSION + nonce + self._for_file(file_id).encrypt(nonce, data, self._context(file_id, index, total))

    def open(self, file_id, index, total, blob):
        if blob[:1] != VERSION or len(blob) < 1 + NONCE_SIZE + TAG_SIZE:
            raise BlockError("unknown block format")
        nonce = blob[1:1 + NONCE_SIZE]
        try:
            return self._for_file(file_id).decrypt(nonce, blob[1 + NONCE_SIZE:], self._context(file_id, index, total))
        except Exception:
            raise BlockError("block failed its integrity check") from None

    def open_legacy(self, blob):
        try:
            return self._legacy.decrypt(blob)
        except InvalidToken:
            raise BlockError("block failed its integrity check") from None
