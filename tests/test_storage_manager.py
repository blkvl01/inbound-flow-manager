import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest import mock

import storage_manager


class StorageManagerTests(unittest.TestCase):
    def setUp(self):
        self.data = {}

        @contextmanager
        def fake_locked_data():
            yield self.data

        self.lock_patch = mock.patch.object(storage_manager, "_locked_data", fake_locked_data)
        self.lock_patch.start()

    def tearDown(self):
        self.lock_patch.stop()

    def test_mark_many_stored_writes_new_awbs_in_one_call(self):
        count, stored = storage_manager.mark_many_stored({
            "111": {"awb": "111", "am_time": datetime(2026, 1, 1, 8, 30)},
            "222": {"awb": "222", "cargo_type": "ULD"},
        })

        self.assertEqual(count, 2)
        self.assertEqual(stored, {"111", "222"})
        self.assertEqual(set(self.data), {"111", "222"})
        self.assertEqual(self.data["111"]["snapshot"]["am_time"], "2026-01-01T08:30:00")

        count, stored = storage_manager.mark_many_stored({
            "111": {"awb": "111"},
            "333": {"awb": "333"},
        })

        self.assertEqual(count, 1)
        self.assertEqual(stored, {"111", "222", "333"})


if __name__ == "__main__":
    unittest.main()
