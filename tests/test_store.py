import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin.store import Store


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "meetings.json"
        self.store = Store(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_and_get(self):
        rid = self.store.add({"url": "https://zoom.us/j/123", "at": "2026-07-08T19:30:00"})
        self.assertTrue(rid.startswith("m-20260708-1930"))
        record = self.store.get(rid)
        self.assertIsNotNone(record)
        self.assertEqual(record["url"], "https://zoom.us/j/123")
        self.assertEqual(record["id"], rid)

    def test_id_collision_gets_numeric_suffix(self):
        rid1 = self.store.add({"url": "u1", "at": "2026-07-08T19:30:00"})
        rid2 = self.store.add({"url": "u2", "at": "2026-07-08T19:30:00"})
        self.assertNotEqual(rid1, rid2)
        self.assertTrue(rid2.endswith("-2"))

    def test_remove(self):
        rid = self.store.add({"url": "u1", "at": "2026-07-08T19:30:00"})
        self.assertTrue(self.store.remove(rid))
        self.assertIsNone(self.store.get(rid))
        self.assertFalse(self.store.remove(rid))

    def test_list_all(self):
        self.store.add({"url": "u1", "at": "2026-07-08T19:30:00"})
        self.store.add({"url": "u2", "at": "2026-07-09T19:30:00"})
        records = self.store.list_all()
        self.assertEqual(len(records), 2)

    def test_update(self):
        rid = self.store.add({"url": "u1", "at": "2026-07-08T19:30:00", "status": "scheduled"})
        updated = self.store.update(rid, status="joined")
        self.assertEqual(updated["status"], "joined")
        self.assertEqual(self.store.get(rid)["status"], "joined")

    def test_update_missing_id_returns_none(self):
        self.assertIsNone(self.store.update("nope", status="joined"))

    def test_atomic_write_survives_read_back(self):
        rid = self.store.add({"url": "u1", "at": "2026-07-08T19:30:00"})
        # Simulate a fresh process re-opening the store.
        other = Store(self.path)
        record = other.get(rid)
        self.assertIsNotNone(record)
        self.assertEqual(record["url"], "u1")
        self.assertFalse(self.path.with_suffix(".tmp").exists())

    def test_missing_file_returns_empty(self):
        missing_store = Store(Path(self.tmpdir.name) / "nope.json")
        self.assertEqual(missing_store.list_all(), [])
        self.assertIsNone(missing_store.get("anything"))


if __name__ == "__main__":
    unittest.main()
