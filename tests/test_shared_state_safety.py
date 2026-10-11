import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import data_cache
import notes_manager
import overrides_manager
import shared_json
import storage_manager


class SharedStateDirMixin:
    def setUp(self):
        self._old_shared = os.environ.get("FLOW_SHARED_STATE_DIR")
        self.tmp = Path(tempfile.mkdtemp(prefix="flow_safety_"))
        os.environ["FLOW_SHARED_STATE_DIR"] = str(self.tmp)
        notes_manager._cache["mtime"] = None
        overrides_manager._cache["mtime"] = None

    def tearDown(self):
        if self._old_shared is None:
            os.environ.pop("FLOW_SHARED_STATE_DIR", None)
        else:
            os.environ["FLOW_SHARED_STATE_DIR"] = self._old_shared
        shutil.rmtree(self.tmp, ignore_errors=True)


class ReadForWriteTests(SharedStateDirMixin, unittest.TestCase):
    def test_missing_file_is_empty_state(self):
        self.assertEqual(shared_json.read_dict_for_write(self.tmp / "nope.json", "x", delay_s=0), {})

    def test_unreadable_file_refuses_and_keeps_backup(self):
        path = self.tmp / "state.json"
        path.write_text('{"A": {"half', encoding="utf-8")
        with self.assertRaises(shared_json.SharedStateUnreadableError):
            shared_json.read_dict_for_write(path, "x", retries=2, delay_s=0)
        backups = list(self.tmp.glob("state.corrupt.*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), '{"A": {"half')

    def test_non_object_root_is_refused(self):
        path = self.tmp / "state.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaises(shared_json.SharedStateUnreadableError):
            shared_json.read_dict_for_write(path, "x", retries=1, delay_s=0)

    def test_transient_partial_file_recovers_on_retry(self):
        path = self.tmp / "state.json"
        path.write_text("{", encoding="utf-8")
        real_sleep = shared_json.time.sleep

        def heal(_):
            path.write_text('{"A": 1}', encoding="utf-8")
        with mock.patch.object(shared_json.time, "sleep", side_effect=heal):
            self.assertEqual(shared_json.read_dict_for_write(path, "x", retries=3), {"A": 1})
        self.assertIs(shared_json.time.sleep, real_sleep)


class CorruptSharedFileNeverWipedTests(SharedStateDirMixin, unittest.TestCase):
    CORRUPT = '{"111": {"stored_at": "2026'

    def _assert_untouched(self, name):
        self.assertEqual((self.tmp / name).read_text(encoding="utf-8"), self.CORRUPT)
        self.assertFalse((self.tmp / name.replace(".json", ".lock")).exists(), "lock must be released")

    def test_store_write_does_not_replace_unreadable_file(self):
        (self.tmp / "stored_awbs.shared.json").write_text(self.CORRUPT, encoding="utf-8")
        with mock.patch.object(shared_json, "_READ_RETRY_DELAY_S", 0), \
                self.assertRaises(shared_json.SharedStateUnreadableError):
            storage_manager.set_stored("222", {"awb": "222"}, True)
        self._assert_untouched("stored_awbs.shared.json")

    def test_note_write_does_not_replace_unreadable_file(self):
        (self.tmp / "item_notes.shared.json").write_text(self.CORRUPT, encoding="utf-8")
        with mock.patch.object(shared_json, "_READ_RETRY_DELAY_S", 0), \
                self.assertRaises(shared_json.SharedStateUnreadableError):
            notes_manager.add_note("222", "x")
        self._assert_untouched("item_notes.shared.json")

    def test_override_write_does_not_replace_unreadable_file(self):
        (self.tmp / "item_overrides.shared.json").write_text(self.CORRUPT, encoding="utf-8")
        with mock.patch.object(shared_json, "_READ_RETRY_DELAY_S", 0), \
                self.assertRaises(shared_json.SharedStateUnreadableError):
            overrides_manager.set_override("222", "lmp", "TEMU")
        self._assert_untouched("item_overrides.shared.json")


class IdempotentStoreTests(SharedStateDirMixin, unittest.TestCase):
    def test_stale_store_click_does_not_unstore_other_users_change(self):
        changed, stored = storage_manager.set_stored("111", {"awb": "111"}, True)
        self.assertTrue(changed)
        # A second user clicks "Betárolva" on a card rendered before the first write.
        changed, stored = storage_manager.set_stored("111", {"awb": "111"}, True)
        self.assertFalse(changed)
        self.assertEqual(stored, {"111"})

    def test_unstore_is_idempotent(self):
        storage_manager.set_stored("111", {"awb": "111"}, True)
        self.assertTrue(storage_manager.set_stored("111", {}, False)[0])
        changed, stored = storage_manager.set_stored("111", {}, False)
        self.assertFalse(changed)
        self.assertEqual(stored, set())

    def test_mark_many_unstored_removes_batch_in_one_write(self):
        storage_manager.mark_many_stored({"1": {}, "2": {}, "3": {}})
        with mock.patch.object(storage_manager, "_acquire_lock", wraps=storage_manager._acquire_lock) as lock:
            self.assertEqual(storage_manager.mark_many_unstored({"1", "2", "missing", ""}), 2)
        self.assertEqual(lock.call_count, 1)
        self.assertEqual(storage_manager.get_stored_awbs(), {"3"})


class StoreButtonTargetTests(unittest.TestCase):
    def setUp(self):
        import app
        self.target = app._store_button_target_state

    def test_target_follows_rendered_button_class(self):
        ids = [{"type": "store-btn", "index": "A"}, {"type": "store-btn", "index": "B"}]
        classes = ["badge-stored badge-stored-empty", "badge-stored badge-stored-active action-pending"]
        self.assertIs(self.target("A", classes, ids), True)
        self.assertIs(self.target("B", classes, ids), False)
        self.assertIsNone(self.target("C", classes, ids))
        self.assertIsNone(self.target("A", ["badge-stored"], ids[:1]))


class WorkerSurvivesPublishErrorsTests(unittest.TestCase):
    def setUp(self):
        with data_cache._lock:
            self.saved_state = dict(data_cache._state)

    def tearDown(self):
        with data_cache._lock:
            data_cache._state.clear()
            data_cache._state.update(self.saved_state)

    def test_busy_auto_unstore_does_not_block_fresh_data(self):
        df = pd.DataFrame([{"awb": "123-45678901", "weight": 10.0}])
        result = (df, {}, {}, [], {}, {"999"}, [])
        with mock.patch.object(data_cache.storage_manager, "get_stored_awbs", return_value={"999"}), \
                mock.patch.object(data_cache.storage_manager, "mark_many_unstored",
                                  side_effect=TimeoutError("busy")), \
                mock.patch.object(data_cache, "apply_priorities", side_effect=lambda d: d), \
                mock.patch.object(data_cache.notes_manager, "cleanup_missing"), \
                mock.patch.object(data_cache, "_save_dashboard_cache"):
            self.assertTrue(data_cache._apply_read_result(result, first=False))
        self.assertEqual(len(data_cache.get_state()["df"]), 1)

    def test_worker_keeps_running_after_publish_exception(self):
        calls = {"n": 0}

        class Stop(BaseException):
            pass

        def wait(timeout=None):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise Stop()
            return True

        with mock.patch.object(data_cache, "_run_isolated_read", return_value={"result": None}), \
                mock.patch.object(data_cache, "_apply_read_result", side_effect=RuntimeError("boom")) as apply, \
                mock.patch.object(data_cache._refresh_event, "wait", side_effect=wait), \
                mock.patch.object(data_cache, "_mark_read_stalled") as stalled:
            with self.assertRaises(Stop):
                data_cache._worker()
        self.assertEqual(apply.call_count, 2, "worker must retry after a publish error")
        self.assertEqual(stalled.call_count, 2)


class ForeignPostRejectedTests(unittest.TestCase):
    def setUp(self):
        import app
        self.client = app.server.test_client()

    def test_cross_site_post_is_rejected(self):
        resp = self.client.post("/api/settings", data=json.dumps({}), content_type="text/plain",
                                headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(resp.status_code, 403)

    def test_rebound_host_is_rejected(self):
        resp = self.client.post("/api/log/ui", json={"action": "x"}, base_url="http://attacker.example:8501")
        self.assertEqual(resp.status_code, 403)

    def test_same_origin_post_is_allowed(self):
        with mock.patch("activity_log.log_event"):
            resp = self.client.post("/api/log/ui", json={"action": "x"}, base_url="http://127.0.0.1:8501",
                                    headers={"Origin": "http://127.0.0.1:8501", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
