import os
import unittest

from core.app import App
from core.drive_rest import DriveApi, DriveError, FetchTransport, FetchVaultStorage, multipart
from core.vault import VaultLocked, VaultNotFound, WrongPassword
from tests.mock_drive import MockDrive, parse_multipart

BLOCK = 100


def make_app(drive):
    async def fetch(method, url, headers, body):
        return drive.handle(method, url, headers, body)

    return App(lambda account: "tok-" + account, fetch, block_size=BLOCK, n_exp=4, retry_delay=0)


def reader(data):
    return lambda offset, n: data[offset:offset + n]


class RestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.drive = MockDrive()

        async def fetch(method, url, headers, body):
            return self.drive.handle(method, url, headers, body)

        self.api = DriveApi(lambda account: "tok-" + account, fetch, retry_delay=0)

    def test_multipart_roundtrip_with_binary_data(self):
        data = os.urandom(500) + b"\r\n--x\r\n"
        content_type, body = multipart({"name": "n"}, data, "application/octet-stream")
        meta, parsed = parse_multipart(content_type, body)
        self.assertEqual((meta, parsed), ({"name": "n"}, data))

    async def test_transport_roundtrip_and_idempotent_delete(self):
        transport = FetchTransport(self.api)
        data = os.urandom(300)
        drive_id = await transport.upload("a", "file.chunk0", data, {"type": "chunk", "order": "0"})
        self.assertEqual(await transport.download("a", drive_id), data)
        stored = self.drive.files_of("a")[drive_id]
        self.assertEqual((stored["props"], stored["parents"]), ({"type": "chunk", "order": "0"}, ["appDataFolder"]))
        await transport.delete("a", drive_id)
        await transport.delete("a", drive_id)
        self.assertEqual(self.drive.files_of("a"), {})

    async def test_retries_transient_errors_then_gives_up(self):
        transport = FetchTransport(self.api)
        self.drive.fail_next = [503, 429]
        self.assertTrue(await transport.upload("a", "n", b"x"))
        self.drive.fail_next = [503] * 10
        with self.assertRaises(DriveError):
            await transport.upload("a", "n", b"x")

    async def test_bad_token_is_an_error(self):
        async def fetch(method, url, headers, body):
            return self.drive.handle(method, url, headers, body)

        api = DriveApi(lambda account: "nope", fetch, retry_delay=0)
        with self.assertRaises(DriveError) as ctx:
            await FetchTransport(api).download("a", "f1")
        self.assertEqual(ctx.exception.status, 401)

    async def test_vault_storage_create_then_overwrite(self):
        storage = FetchVaultStorage(self.api)
        self.assertIsNone(await storage.read("a"))
        await storage.write("a", "first")
        await storage.write("a", "second")
        self.assertEqual(await storage.read("a"), "second")
        self.assertEqual(len(self.drive.files_of("a")), 1)

    async def test_async_token_provider(self):
        async def get_token(account):
            return "tok-" + account

        async def fetch(method, url, headers, body):
            return self.drive.handle(method, url, headers, body)

        await FetchVaultStorage(DriveApi(get_token, fetch, retry_delay=0)).write("a", "x")


class AppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.drive = MockDrive()

    async def test_full_flow_across_two_accounts_and_two_sessions(self):
        first = make_app(self.drive)
        await first.create("pw", ["a@x", "b@x"])
        data = os.urandom(BLOCK * 5 + 7)
        await first.upload(reader(data), len(data), "/docs/report.bin")
        self.assertEqual(first.state()["files"], [{"path": "/docs/report.bin", "size": len(data)}])
        await first.release()

        second = make_app(self.drive)
        await second.open("pw", ["b@x"])
        self.assertEqual(second.state()["accounts"], ["a@x", "b@x"])
        out = bytearray()
        await second.download("/docs/report.bin", out.extend)
        self.assertEqual(bytes(out), data)

        await second.delete_file("/docs/report.bin")
        self.assertEqual(second.state()["files"], [])
        chunk_files = [f for acc in self.drive.accounts.values() for f in acc.values() if "chunk" in f["name"]]
        self.assertEqual(chunk_files, [])

    async def test_chunks_are_encrypted(self):
        app = make_app(self.drive)
        await app.create("pw", ["a@x"])
        data = b"secret-" * 20
        await app.upload(reader(data), len(data), "/f.bin")
        stored = [f["data"] for f in self.drive.files_of("a@x").values() if "chunk" in f["name"]]
        self.assertEqual(len(stored), 2)
        for blob in stored:
            self.assertNotIn(b"secret-", blob)

    async def test_vault_is_locked_while_open_elsewhere(self):
        first = make_app(self.drive)
        await first.create("pw", ["a@x"])
        with self.assertRaises(VaultLocked):
            await make_app(self.drive).open("pw", ["a@x"])

    async def test_wrong_password_and_new_account(self):
        app = make_app(self.drive)
        with self.assertRaises(VaultNotFound):
            await app.open("pw", ["a@x"])
        await app.create("pw", ["a@x"])
        await app.add_account("c@x")
        await app.release()
        self.assertEqual(len(self.drive.files_of("c@x")), 1)
        with self.assertRaises(WrongPassword):
            await make_app(self.drive).open("bad", ["c@x"])
        reopened = make_app(self.drive)
        await reopened.open("pw", ["c@x"])
        self.assertEqual(reopened.state()["accounts"], ["a@x", "c@x"])


class FeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.drive = MockDrive()
        self.app = make_app(self.drive)
        await self.app.create("pw", ["a@x", "b@x"])
        self.contents = {"/docs/a.txt": b"alpha" * 50, "/docs/sub/b.bin": os.urandom(250), "/top.md": b"hi"}
        for path, data in self.contents.items():
            await self.app.upload(reader(data), len(data), path)

    async def test_state_reports_usage_and_tree(self):
        state = self.app.state()
        self.assertEqual(sum(state["usage"].values()), 3 + 3 + 1)
        self.assertEqual(sorted(f["path"] for f in state["files"]), sorted(self.contents))
        b_bin = state["tree"]["children"]["docs"]["children"]["sub"]["children"]["b.bin"]
        self.assertEqual((b_bin["type"], b_bin["size"], len(b_bin["shards"])), ("file", 250, 3))
        self.assertTrue(set(b_bin["shards"]) <= {"a@x", "b@x"})

    async def test_progress_reports_which_block_landed(self):
        seen = []
        data = os.urandom(BLOCK * 4)
        await self.app.upload(reader(data), len(data), "/p.bin", lambda d, t, i: seen.append(i))
        self.assertEqual(sorted(seen), [0, 1, 2, 3])

    async def test_search_modes(self):
        names = lambda **kw: sorted(r["path"] for r in self.app.search(**kw))
        self.assertEqual(names(query="a.txt"), ["/docs/a.txt"])
        self.assertEqual(names(query="docs", filter_type="dir"), ["/docs", "/docs/sub"])
        self.assertEqual(names(query=r"\.(md|bin)$", use_regex=True), ["/docs/sub/b.bin", "/top.md"])
        self.assertEqual(names(query="TOP.MD", exact=True), ["/top.md"])
        with self.assertRaises(ValueError):
            self.app.search("(", use_regex=True)

    async def test_download_folder_as_zip(self):
        import io
        import zipfile

        archive = zipfile.ZipFile(io.BytesIO(await self.app.download_folder("/docs")))
        self.assertEqual(sorted(archive.namelist()), ["docs/a.txt", "docs/sub/b.bin"])
        self.assertEqual(archive.read("docs/sub/b.bin"), self.contents["/docs/sub/b.bin"])

    async def test_change_password_keeps_files_readable(self):
        await self.app.change_password("brand new pw")
        await self.app.release()
        with self.assertRaises(WrongPassword):
            await make_app(self.drive).open("pw", ["a@x"])
        reopened = make_app(self.drive)
        await reopened.open("brand new pw", ["a@x"])
        out = bytearray()
        await reopened.download("/top.md", out.extend)
        self.assertEqual(bytes(out), self.contents["/top.md"])

    async def test_delete_folder(self):
        await self.app.delete_folder("/docs")
        self.assertEqual([f["path"] for f in self.app.state()["files"]], ["/top.md"])

    async def test_remove_account_rules(self):
        with self.assertRaises(Exception):
            await self.app.remove_account("a@x")
        for path in list(self.contents):
            await self.app.delete_file(path)
        await self.app.remove_account("b@x")
        self.assertEqual(self.app.state()["accounts"], ["a@x"])
        with self.assertRaises(Exception):
            await self.app.remove_account("a@x")


if __name__ == "__main__":
    unittest.main()
