import asyncio
import io
import os
import unittest

from cryptography.fernet import Fernet

from core import fs_tree
from core.engine import Engine, StorageError
from tests.fakes import FakeTransport

BLOCK = 100


def reader(data):
    return lambda offset, n: data[offset:offset + n]


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = FakeTransport(jitter=0.01)
        self.cipher = Fernet(Fernet.generate_key())
        self.saves = 0

        def persist(fs):
            self.saves += 1

        self.engine = Engine(self.transport, self.cipher, persist=persist, block_size=BLOCK, concurrency=2)

    async def upload(self, data, path="/docs/a.bin", accounts=("a", "b", "c")):
        await self.engine.upload_file(reader(data), len(data), path, list(accounts))

    async def download(self, path):
        out = io.BytesIO()
        await self.engine.download_file(path, out.write)
        return out.getvalue()

    async def test_roundtrip_with_out_of_order_completion(self):
        data = os.urandom(BLOCK * 7 + 33)
        await self.upload(data)
        self.assertEqual(await self.download("/docs/a.bin"), data)

    async def test_blocks_are_spread_round_robin_and_encrypted(self):
        data = os.urandom(BLOCK * 6)
        await self.upload(data)
        chunks = fs_tree.get_node(self.engine.fs, "/docs/a.bin")["chunks"]
        self.assertEqual([acc for _, acc in chunks], ["a", "b", "c", "a", "b", "c"])
        for stored in self.transport.files.values():
            self.assertNotIn(data[:BLOCK], stored)

    async def test_empty_file(self):
        await self.upload(b"")
        self.assertEqual(await self.download("/docs/a.bin"), b"")

    async def test_failed_upload_leaves_nothing_behind(self):
        self.transport.fail_uploads_after = 3
        with self.assertRaises(RuntimeError):
            await self.upload(os.urandom(BLOCK * 8))
        self.assertEqual(self.transport.files, {})
        self.assertIsNone(fs_tree.get_node(self.engine.fs, "/docs/a.bin"))

    async def test_overwrite_frees_old_blocks(self):
        await self.upload(os.urandom(BLOCK * 4))
        new = os.urandom(BLOCK * 2)
        await self.upload(new)
        self.assertEqual(len(self.transport.files), 2)
        self.assertEqual(await self.download("/docs/a.bin"), new)

    async def test_delete_file(self):
        await self.upload(os.urandom(BLOCK * 3))
        await self.engine.delete_file("/docs/a.bin")
        self.assertEqual(self.transport.files, {})
        self.assertIsNone(fs_tree.get_node(self.engine.fs, "/docs/a.bin"))

    async def test_failed_delete_keeps_entry_so_it_can_be_retried(self):
        await self.upload(os.urandom(BLOCK * 3))
        self.transport.fail_deletes = True
        with self.assertRaises(StorageError):
            await self.engine.delete_file("/docs/a.bin")
        self.assertIsNotNone(fs_tree.get_node(self.engine.fs, "/docs/a.bin"))
        self.transport.fail_deletes = False
        await self.engine.delete_file("/docs/a.bin")
        self.assertEqual(self.transport.files, {})

    async def test_force_delete_drops_entry_despite_failures(self):
        await self.upload(os.urandom(BLOCK * 3))
        self.transport.fail_deletes = True
        failed = await self.engine.delete_file("/docs/a.bin", force=True)
        self.assertEqual(failed, 3)
        self.assertIsNone(fs_tree.get_node(self.engine.fs, "/docs/a.bin"))

    async def test_delete_folder(self):
        await self.upload(os.urandom(BLOCK * 2), "/docs/a.bin")
        await self.upload(os.urandom(BLOCK * 2), "/docs/sub/b.bin")
        await self.upload(os.urandom(BLOCK), "/keep.bin")
        await self.engine.delete_folder("/docs")
        self.assertIsNone(fs_tree.get_node(self.engine.fs, "/docs"))
        self.assertEqual(len(self.transport.files), 1)

    async def test_wrong_key_is_detected(self):
        data = os.urandom(BLOCK * 2)
        await self.upload(data)
        other = Engine(self.transport, Fernet(Fernet.generate_key()), fs=self.engine.fs, block_size=BLOCK)
        with self.assertRaises(Exception):
            await other.download_file("/docs/a.bin", lambda b: None)

    async def test_errors(self):
        with self.assertRaises(StorageError):
            await self.engine.upload_file(reader(b"x"), 1, "/a", [])
        with self.assertRaises(StorageError):
            await self.engine.download_file("/missing", lambda b: None)
        with self.assertRaises(ValueError):
            await self.upload(b"x", "/../escape")

    async def test_progress_and_persist_callbacks(self):
        seen = []
        data = os.urandom(BLOCK * 3)
        await self.engine.upload_file(reader(data), len(data), "/p.bin", ["a"], progress=lambda d, t, i: seen.append((d, t)))
        self.assertEqual(seen[-1], (3, 3))
        self.assertEqual(self.saves, 1)

    async def test_failed_index_save_undoes_the_upload(self):
        async def failing_persist(fs):
            raise RuntimeError("index could not be saved")

        engine = Engine(self.transport, self.cipher, persist=failing_persist, block_size=BLOCK)
        data = os.urandom(BLOCK * 3)
        with self.assertRaises(RuntimeError):
            await engine.upload_file(reader(data), len(data), "/x.bin", ["a", "b"])
        self.assertEqual(self.transport.files, {})
        self.assertIsNone(fs_tree.get_node(engine.fs, "/x.bin"))

    async def test_async_read_and_write_callbacks(self):
        data = os.urandom(BLOCK * 3)

        async def read_at(offset, n):
            await asyncio.sleep(0)
            return data[offset:offset + n]

        out = bytearray()

        async def write(b):
            await asyncio.sleep(0)
            out.extend(b)

        await self.engine.upload_file(read_at, len(data), "/x.bin", ["a", "b"])
        await self.engine.download_file("/x.bin", write)
        self.assertEqual(bytes(out), data)


class FsTreeTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(fs_tree.normalize("a//b/"), "/a/b")
        with self.assertRaises(ValueError):
            fs_tree.split_path("/a/../b")

    def test_search_and_tree(self):
        fs = fs_tree.new_fs()
        fs_tree.add_file(fs, "/docs/a.txt", {"type": "file", "size": 1, "chunks": []})
        fs_tree.add_file(fs, "/docs/raw.bin", {"type": "file", "size": 2, "encrypted": False, "chunks": []})
        self.assertEqual([r["path"] for r in fs_tree.search(fs, "a.txt")], ["/docs/a.txt"])
        self.assertEqual([r["name"] for r in fs_tree.search(fs, filter_mode="raw")], ["raw.bin"])
        self.assertEqual([r["name"] for r in fs_tree.search(fs, r"\.txt$", use_regex=True)], ["a.txt"])
        self.assertIn("├── a.txt [encrypted]", "\n".join(fs_tree.tree_lines(fs)))

    def test_file_cannot_be_used_as_folder(self):
        fs = fs_tree.new_fs()
        fs_tree.add_file(fs, "/a", {"type": "file", "chunks": []})
        with self.assertRaises(ValueError):
            fs_tree.add_file(fs, "/a/b", {"type": "file", "chunks": []})


if __name__ == "__main__":
    unittest.main()
