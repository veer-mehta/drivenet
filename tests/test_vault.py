import asyncio
import unittest

from cryptography.fernet import Fernet

from core import fs_tree
from core.vault import VaultCorrupt, VaultError, VaultLocked, VaultNotFound, WrongPassword
from core.vault_sync import VaultSync
from tests.fakes import FakeVaultStorage

ACCOUNTS = ["a", "b", "c"]
N = 4
LEASE = 300


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def file_node(name):
    return {"type": "file", "name": name, "size": 1, "encrypted": True, "chunks": [], "timestamp": 1.0}


def paths(vault):
    return sorted(p for p, _ in fs_tree.iter_files(vault.fs["root"]))


class VaultTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = FakeVaultStorage()
        self.clock = Clock()
        self.laptop = VaultSync(self.storage, clock=self.clock, lease=LEASE)
        self.phone = VaultSync(self.storage, clock=self.clock, lease=LEASE)

    async def new_vault(self, password="pw"):
        vault = await self.laptop.create(password, ACCOUNTS, n_exp=N)
        await self.laptop.release(vault)
        return vault

    async def test_create_then_open_from_any_single_account(self):
        vault = await self.new_vault()
        vault.fs["root"]["children"]["x"] = file_node("x")
        await self.laptop.save(vault)
        await self.laptop.release(vault)
        reopened = await self.phone.open("pw", ["b"])
        self.assertEqual(paths(reopened), ["/x"])
        self.assertEqual(reopened.accounts, ACCOUNTS)
        self.assertEqual(reopened.master_key, vault.master_key)

    async def test_copy_in_every_account_and_nothing_readable(self):
        await self.new_vault()
        self.assertEqual(set(self.storage.blobs), set(ACCOUNTS))
        for blob in self.storage.blobs.values():
            self.assertTrue(blob.startswith("ddv1."))
            for account in ACCOUNTS:
                self.assertNotIn(f'"{account}"', blob)

    async def test_wrong_password_and_missing_vault(self):
        await self.new_vault()
        with self.assertRaises(WrongPassword):
            await self.phone.open("nope", ACCOUNTS)
        with self.assertRaises(VaultNotFound):
            await VaultSync(FakeVaultStorage()).open("pw", ACCOUNTS)
        with self.assertRaises(VaultNotFound):
            await self.phone.open("pw", [])

    async def test_create_refuses_to_overwrite_existing_vault(self):
        await self.new_vault()
        with self.assertRaises(VaultError):
            await self.phone.create("other", ACCOUNTS, n_exp=N)

    async def test_import_existing_secret_key(self):
        old_key = Fernet.generate_key()
        token = Fernet(old_key).encrypt(b"uploaded earlier by the cli")
        vault = await self.laptop.create("pw", ACCOUNTS, master_key=old_key, n_exp=N)
        await self.laptop.release(vault)
        reopened = await self.phone.open("pw", ACCOUNTS)
        self.assertEqual(reopened.cipher.decrypt(token), b"uploaded earlier by the cli")

    async def test_change_password_keeps_data_readable(self):
        vault = await self.new_vault("old")
        token = vault.cipher.encrypt(b"chunk")
        vault.change_password("new")
        await self.laptop.save(vault)
        await self.laptop.release(vault)
        with self.assertRaises(WrongPassword):
            await self.phone.open("old", ACCOUNTS)
        reopened = await self.phone.open("new", ACCOUNTS)
        self.assertEqual(reopened.cipher.decrypt(token), b"chunk")

    async def test_second_session_is_refused_while_the_vault_is_open(self):
        await self.laptop.create("pw", ACCOUNTS, n_exp=N)
        with self.assertRaisesRegex(VaultLocked, r"try again in 301s"):
            await self.phone.open("pw", ACCOUNTS)

    async def test_released_vault_can_be_opened_by_someone_else(self):
        vault = await self.laptop.create("pw", ACCOUNTS, n_exp=N)
        await self.laptop.release(vault)
        other = await self.phone.open("pw", ACCOUNTS)
        with self.assertRaises(VaultLocked):
            await self.laptop.open("pw", ACCOUNTS)
        await self.phone.release(other)
        await self.laptop.open("pw", ACCOUNTS)

    async def test_same_session_can_reopen_its_own_lock(self):
        await self.laptop.create("pw", ACCOUNTS, n_exp=N)
        await self.laptop.open("pw", ACCOUNTS)

    async def test_saving_renews_the_lock(self):
        vault = await self.laptop.create("pw", ACCOUNTS, n_exp=N)
        self.clock.now += LEASE - 10
        await self.laptop.save(vault)
        self.clock.now += LEASE - 10
        with self.assertRaises(VaultLocked):
            await self.phone.open("pw", ACCOUNTS)

    async def test_expired_lock_can_be_taken_and_the_old_owner_is_told(self):
        vault = await self.laptop.create("pw", ACCOUNTS, n_exp=N)
        self.clock.now += LEASE + 1
        await self.phone.open("pw", ACCOUNTS)
        vault.fs["root"]["children"]["x"] = file_node("x")
        with self.assertRaises(VaultLocked):
            await self.laptop.save(vault)

    async def test_two_simultaneous_opens_never_both_win(self):
        vault = await self.laptop.create("pw", ACCOUNTS, n_exp=N)
        await self.laptop.release(vault)
        results = await asyncio.gather(
            self.laptop.open("pw", ACCOUNTS), self.phone.open("pw", ACCOUNTS), return_exceptions=True
        )
        winners = [r for r in results if not isinstance(r, BaseException)]
        self.assertLessEqual(len(winners), 1)

    async def test_unreachable_account_is_tolerated_then_repaired(self):
        await self.new_vault()
        vault = await self.laptop.open("pw", ACCOUNTS)
        self.storage.fail_write.add("c")
        vault.fs["root"]["children"]["x"] = file_node("x")
        self.assertEqual(await self.laptop.save(vault), ["c"])
        self.storage.fail_write.clear()
        await self.laptop.save(vault)
        await self.laptop.release(vault)
        self.assertEqual(paths(await self.phone.open("pw", ["c"])), ["/x"])

    async def test_open_prefers_newest_copy(self):
        await self.new_vault()
        vault = await self.laptop.open("pw", ACCOUNTS)
        self.storage.fail_write.add("c")
        vault.fs["root"]["children"]["x"] = file_node("x")
        await self.laptop.save(vault)
        await self.laptop.release(vault)
        self.assertEqual(paths(await self.phone.open("pw", ACCOUNTS)), ["/x"])

    async def test_all_accounts_unreachable(self):
        await self.new_vault()
        vault = await self.laptop.open("pw", ACCOUNTS)
        self.storage.fail_write.update(ACCOUNTS)
        with self.assertRaises(VaultError):
            await self.laptop.save(vault)

    async def test_damaged_or_foreign_data(self):
        await self.new_vault()
        good = self.storage.blobs["a"]
        for bad in ["garbage", "ddv1.99.AAAA.x.y", good[:-6] + "AAAAAA"]:
            storage = FakeVaultStorage()
            storage.blobs["a"] = bad
            with self.assertRaises((VaultCorrupt, WrongPassword)):
                await VaultSync(storage).open("pw", ["a"])


if __name__ == "__main__":
    unittest.main()
