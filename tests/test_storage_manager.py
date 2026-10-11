import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import storage_manager


class StorageManagerTests(unittest.TestCase):
    def setUp(self):
        self._old_shared = os.environ.get("FLOW_SHARED_STATE_DIR")
        self.tmp = Path(tempfile.mkdtemp(prefix="flow_storage_"))
        os.environ["FLOW_SHARED_STATE_DIR"] = str(self.tmp)

    def tearDown(self):
        if self._old_shared is None:
            os.environ.pop("FLOW_SHARED_STATE_DIR", None)
        else:
            os.environ["FLOW_SHARED_STATE_DIR"] = self._old_shared
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def data(self):
        return storage_manager.load_stored()

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
