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


def _own(name):
    from shared_ledger import _host
    return f"{name}.{_host()}.json"


class CorruptSharedFileNeverWipedTests(SharedStateDirMixin, unittest.TestCase):
    CORRUPT = '{"records": {"111": {"stored_at": "2026'

    def _assert_untouched(self, name):
        self.assertEqual((self.tmp / name).read_text(encoding="utf-8"), self.CORRUPT)
        self.assertFalse(list(self.tmp.glob("*.lock")), "lock must be released")

    def _refused(self, name, write):
        (self.tmp / name).write_text(self.CORRUPT, encoding="utf-8")
        with mock.patch.object(shared_json, "_READ_RETRY_DELAY_S", 0), \
                self.assertRaises(shared_json.SharedStateUnreadableError):
            write()
        self._assert_untouched(name)

    def test_store_write_does_not_replace_unreadable_own_file(self):
        self._refused(_own("stored_awbs"), lambda: storage_manager.set_stored("222", {"awb": "222"}, True))

    def test_note_write_does_not_replace_unreadable_own_file(self):
        self._refused(_own("item_notes"), lambda: notes_manager.add_note("222", "x"))

    def test_override_write_does_not_replace_unreadable_own_file(self):
        self._refused(_own("item_overrides"), lambda: overrides_manager.set_override("222", "lmp", "TEMU"))

    def test_corrupt_legacy_file_is_never_written(self):
        (self.tmp / "stored_awbs.shared.json").write_text(self.CORRUPT, encoding="utf-8")
        storage_manager.set_stored("222", {"awb": "222"}, True)
        self.assertEqual((self.tmp / "stored_awbs.shared.json").read_text(encoding="utf-8"), self.CORRUPT)
        self.assertEqual(storage_manager.get_stored_awbs(), {"222"})


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
        ledger = storage_manager._ledger
        with mock.patch.object(ledger, "_acquire_lock", wraps=ledger._acquire_lock) as lock:
            self.assertEqual(storage_manager.mark_many_unstored({"1", "2", "missing", ""}), 2)
        self.assertEqual(lock.call_count, 1)
        self.assertEqual(storage_manager.get_stored_awbs(), {"3"})


class PerMachineLedgerTests(SharedStateDirMixin, unittest.TestCase):
    def as_host(self, name):
        return mock.patch("shared_ledger._host", return_value=name)

    def test_two_machines_never_lose_each_others_writes(self):
        with self.as_host("PC_A"):
            storage_manager.set_stored("A1", {"awb": "A1"}, True)
        with self.as_host("PC_B"):
            # B writes without having seen A's file content in its own file.
            storage_manager.set_stored("B1", {"awb": "B1"}, True)
        self.assertTrue((self.tmp / "stored_awbs.PC_A.json").exists())
        self.assertTrue((self.tmp / "stored_awbs.PC_B.json").exists())
        self.assertEqual(storage_manager.get_stored_awbs(), {"A1", "B1"})

    def test_other_machine_can_unstore_and_latest_wins(self):
        with self.as_host("PC_A"):
            storage_manager.set_stored("X", {"awb": "X"}, True)
        with self.as_host("PC_B"):
            storage_manager.set_stored("X", {}, False)
        self.assertEqual(storage_manager.get_stored_awbs(), set())
        with self.as_host("PC_A"):
            storage_manager.set_stored("X", {"awb": "X"}, True)
        self.assertEqual(storage_manager.get_stored_awbs(), {"X"})

    def test_sequential_action_wins_even_if_other_clock_runs_ahead(self):
        future = "2099-01-01T00:00:00.000000"
        (self.tmp / "stored_awbs.PC_FAST.json").write_text(json.dumps({"version": 1, "records": {
            "X": {"stored_at": future, "stored_by": "u", "stored_on": "PC_FAST",
                  "snapshot": {}, "ts": future, "deleted": False}}}), encoding="utf-8")
        with self.as_host("PC_A"):
            self.assertTrue(storage_manager.set_stored("X", {}, False)[0])
        self.assertEqual(storage_manager.get_stored_awbs(), set())

    def test_legacy_file_is_merged_and_can_be_overridden(self):
        legacy = {"OLD": {"stored_at": "2026-01-01T08:00:00", "stored_by": "u", "stored_on": "pc",
                          "snapshot": {"awb": "OLD"}}}
        (self.tmp / "stored_awbs.shared.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(storage_manager.get_stored_awbs(), {"OLD"})
        self.assertEqual(storage_manager.load_stored()["OLD"]["snapshot"], {"awb": "OLD"})
        storage_manager.set_stored("OLD", {}, False)
        self.assertEqual(storage_manager.get_stored_awbs(), set())
        self.assertEqual(json.loads((self.tmp / "stored_awbs.shared.json").read_text()), legacy)

    def test_half_synced_other_machine_file_keeps_last_good_view(self):
        with self.as_host("PC_B"):
            storage_manager.set_stored("B1", {"awb": "B1"}, True)
        self.assertEqual(storage_manager.get_stored_awbs(), {"B1"})
        (self.tmp / "stored_awbs.PC_B.json").write_text('{"records": {"B1"', encoding="utf-8")
        self.assertEqual(storage_manager.get_stored_awbs(), {"B1"})

    def test_notes_from_two_machines_and_cross_machine_delete(self):
        with self.as_host("PC_A"):
            a = notes_manager.add_note("AWB", "A irta")
        with self.as_host("PC_B"):
            notes_manager.add_note("AWB", "B irta")
            self.assertTrue(notes_manager.delete_note("AWB", a["id"]))
        self.assertEqual([n["text"] for n in notes_manager.get_notes("AWB")], ["B irta"])

    def test_legacy_notes_and_overrides_are_visible(self):
        (self.tmp / "item_notes.shared.json").write_text(json.dumps({"AWB": [
            {"id": "n1", "text": "elso", "at": "2026-01-01T08:00:00"},
            {"id": "n2", "text": "masodik", "at": "2026-01-01T08:00:00"}]}), encoding="utf-8")
        (self.tmp / "item_overrides.shared.json").write_text(json.dumps({"AWB": {
            "fields": {"lmp": "TEMU"}, "set_by": "u", "set_at": "2026-01-01T08:00:00"}}), encoding="utf-8")
        self.assertEqual([n["text"] for n in notes_manager.get_notes("AWB")], ["elso", "masodik"])
        self.assertEqual(overrides_manager.get_all_overrides()["AWB"]["fields"], {"lmp": "TEMU"})
        overrides_manager.clear_override("AWB")
        self.assertEqual(overrides_manager.get_all_overrides(), {})

    def test_compaction_drops_superseded_and_old_tombstones_only(self):
        from datetime import datetime, timedelta
        with self.as_host("PC_A"):
            storage_manager.set_stored("S", {"awb": "S"}, True)    # superseded by B below
            storage_manager.set_stored("T", {"awb": "T"}, True)
            storage_manager.set_stored("T", {}, False)             # own tombstone, nobody else
            storage_manager.set_stored("K", {"awb": "K"}, True)    # live, kept
        with self.as_host("PC_B"):
            storage_manager.set_stored("S", {}, False)
        before = storage_manager.get_stored_awbs()
        with self.as_host("PC_A"):
            removed = storage_manager._ledger.compact(now=datetime.now() + timedelta(days=30))
        own = json.loads((self.tmp / "stored_awbs.PC_A.json").read_text())["records"]
        self.assertEqual(removed, 2)
        self.assertEqual(set(own), {"K"})
        self.assertEqual(storage_manager.get_stored_awbs(), before)

    def test_compaction_keeps_tombstone_hiding_another_source(self):
        from datetime import datetime, timedelta
        (self.tmp / "stored_awbs.shared.json").write_text(json.dumps({"OLD": {
            "stored_at": "2026-01-01T08:00:00", "snapshot": {}}}), encoding="utf-8")
        with self.as_host("PC_A"):
            storage_manager.set_stored("OLD", {}, False)
            storage_manager._ledger.compact(now=datetime.now() + timedelta(days=60))
        self.assertEqual(storage_manager.get_stored_awbs(), set())


class LedgerMigrationTests(unittest.TestCase):
    def test_legacy_location_migration_copies_per_machine_ledgers(self):
        import config
        src, dst = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        try:
            for name in ("stored_awbs.PC_A.json", "item_notes.PC_B.json", "stored_awbs.shared.json",
                         "stored_awbs.PC_A.lock", "stored_awbs.shared.corrupt.20260101-000000.json"):
                (src / name).write_text("{}", encoding="utf-8")
            config._migrate_legacy_shared_state(src, dst)
            self.assertEqual(sorted(p.name for p in dst.iterdir()),
                             ["item_notes.PC_B.json", "stored_awbs.PC_A.json", "stored_awbs.shared.json"])
        finally:
            shutil.rmtree(src, ignore_errors=True)
            shutil.rmtree(dst, ignore_errors=True)


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


class StoredFlagsConcurrencyTests(unittest.TestCase):
    def setUp(self):
        with data_cache._lock:
            self.saved_state = dict(data_cache._state)

    def tearDown(self):
        with data_cache._lock:
            data_cache._state.clear()
            data_cache._state.update(self.saved_state)

    def test_ui_reads_are_not_blocked_while_flags_recompute(self):
        import threading
        df = pd.DataFrame([{"awb": "A"}])
        with data_cache._lock:
            data_cache._state["df"] = df
        started, release = threading.Event(), threading.Event()

        def slow_priorities(d):
            started.set()
            release.wait(5)
            return d

        with mock.patch.object(data_cache, "apply_priorities", side_effect=slow_priorities), \
                mock.patch.object(data_cache, "_apply_stored_state", side_effect=lambda d, s: d), \
                mock.patch.object(data_cache.overrides_manager, "apply_overrides_to_df", side_effect=lambda d: d), \
                mock.patch.object(data_cache.storage_manager, "get_stored_awbs", return_value=set()):
            worker = threading.Thread(target=data_cache.update_stored_flags)
            worker.start()
            self.assertTrue(started.wait(5))
            acquired = data_cache._lock.acquire(timeout=1)
            self.assertTrue(acquired, "global state lock must be free during the recompute")
            data_cache._lock.release()
            release.set()
            worker.join(5)

    def test_recomputes_on_data_replaced_by_a_refresh_meanwhile(self):
        old, new = pd.DataFrame([{"awb": "OLD"}]), pd.DataFrame([{"awb": "NEW"}])
        with data_cache._lock:
            data_cache._state["df"] = old
        calls = []

        def priorities(d):
            calls.append(d["awb"].iloc[0])
            if len(calls) == 1:
                with data_cache._lock:
                    data_cache._state["df"] = new  # a full refresh lands mid-recompute
            return d.assign(flagged=True)

        with mock.patch.object(data_cache, "apply_priorities", side_effect=priorities), \
                mock.patch.object(data_cache, "_apply_stored_state", side_effect=lambda d, s: d), \
                mock.patch.object(data_cache.overrides_manager, "apply_overrides_to_df", side_effect=lambda d: d), \
                mock.patch.object(data_cache.storage_manager, "get_stored_awbs", return_value=set()):
            data_cache.update_stored_flags()
        self.assertEqual(calls, ["OLD", "NEW"])
        result = data_cache.get_state()["df"]
        self.assertEqual(result["awb"].tolist(), ["NEW"])
        self.assertTrue(result["flagged"].all())


class LedgerReadCacheTests(SharedStateDirMixin, unittest.TestCase):
    def test_card_lookups_do_not_relist_the_shared_folder(self):
        notes_manager.add_note("AWB", "x")
        notes_manager.get_notes("AWB")
        ledger = notes_manager._ledger
        with mock.patch.object(ledger, "_fresh_signature", wraps=ledger._fresh_signature) as fresh:
            for _ in range(50):
                notes_manager.get_notes("AWB")
        self.assertLessEqual(fresh.call_count, 1)

    def test_own_write_is_visible_immediately_despite_cache(self):
        self.assertEqual(notes_manager.get_notes("AWB"), [])
        notes_manager.add_note("AWB", "uj")
        self.assertEqual([n["text"] for n in notes_manager.get_notes("AWB")], ["uj"])
        storage_manager.get_stored_awbs()
        storage_manager.set_stored("S", {}, True)
        self.assertEqual(storage_manager.get_stored_awbs(), {"S"})
