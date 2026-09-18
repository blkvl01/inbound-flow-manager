import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import data_cache


class TestOracleCacheSafety(unittest.TestCase):
    def setUp(self):
        with data_cache._lock:
            self.saved_state = dict(data_cache._state)

    def tearDown(self):
        with data_cache._lock:
            data_cache._state.clear()
            data_cache._state.update(self.saved_state)

    def test_hard_oracle_failure_keeps_last_successful_dataframe(self):
        good = pd.DataFrame([{"awb": "123-45678901", "weight": 10.0}])
        with data_cache._lock:
            data_cache._state["df"] = good
            data_cache._state["outbound_cards"] = []
            data_cache._state["uld_data"] = []
            data_cache._state["status"] = "ready"
            data_cache._state["read_stalled"] = False

        result = (
            pd.DataFrame(),
            {"error": "Oracle E_COMM lekérdezési hiba", "hard_read_failure": True},
            {},
            [],
            {},
            set(),
            [],
        )
        updated = data_cache._apply_read_result(result, first=False)

        state = data_cache.get_state()
        self.assertFalse(updated)
        self.assertIs(state["df"], good)
        self.assertTrue(state["read_stalled"])
        self.assertEqual(state["status"], "ready")

    def test_load_progress_is_clamped_and_published(self):
        data_cache._set_load_progress(140, "E_COMM adatok", "25 000 / 25 000 sor")

        state = data_cache.get_state()
        self.assertEqual(state["load_progress"], 100)
        self.assertEqual(state["load_stage"], "E_COMM adatok")
        self.assertEqual(state["load_detail"], "25 000 / 25 000 sor")

    def test_oracle_source_signature_does_not_depend_on_excel_file_stat(self):
        real_stat = data_cache.os.stat

        def guarded_stat(path):
            if path in {data_cache.ECOMM_FILE, data_cache.PALLETS_FILE}:
                raise AssertionError("Oracle mode must not stat either Excel source")
            return real_stat(path)

        with patch.object(data_cache.oracle_ecomm, "get_source_mode", return_value="oracle"), patch.object(
            data_cache.os, "stat", side_effect=guarded_stat
        ):
            signature = data_cache._source_signature()
        self.assertEqual(signature[data_cache.ECOMM_FILE], {"source": "oracle"})
        self.assertEqual(signature[data_cache.PALLETS_FILE], {"source": "oracle"})

    def test_late_full_read_does_not_overwrite_newer_fast_uld_snapshot(self):
        read_started_at = datetime.now() - timedelta(seconds=10)
        fast_snapshot = [{"uld_number": "PMC-NEW", "gha": "Menzies"}]
        stale_snapshot = [{"uld_number": "PMC-OLD", "gha": "Celebi"}]
        with data_cache._lock:
            data_cache._state["uld_data"] = fast_snapshot
            data_cache._state["uld_last_refresh"] = datetime.now()
            data_cache._state["uld_source_signature"] = {"fast": True}

        result = (
            pd.DataFrame(),
            {},
            {},
            [],
            {},
            set(),
            stale_snapshot,
        )
        with patch.object(data_cache, "_source_signature", return_value={"full": True}), patch.object(
            data_cache, "_save_dashboard_cache"
        ) as save_cache:
            self.assertTrue(data_cache._apply_read_result(result, first=False, read_started_at=read_started_at))

        state = data_cache.get_state()
        self.assertEqual(state["uld_data"], fast_snapshot)
        self.assertEqual(state["uld_source_signature"], {"fast": True})
        self.assertEqual(save_cache.call_args.args[6], fast_snapshot)

    def test_warm_cache_restores_last_successful_refresh_timestamp(self):
        loaded_at = datetime(2026, 8, 25, 9, 45, 0)
        payload = {
            "df": pd.DataFrame([{"awb": "123-45678901"}]),
            "errors": {},
            "kpi": {},
            "outbound_cards": [],
            "outbound_errors": {},
            "issued_awbs": set(),
            "uld_data": [],
            "loaded_at": loaded_at,
            "source_signature": {"ecomm": "ok"},
            "_source_signature_match": True,
            "_missing_uld_data": False,
        }
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "dashboard_cache.pkl"
            cache_path.touch()
            with patch.object(data_cache, "_cache_paths", return_value=[cache_path]), patch.object(
                data_cache, "_read_cache_file", return_value=payload
            ), patch.object(data_cache.storage_manager, "get_stored_awbs", return_value=set()), patch.object(
                data_cache.overrides_manager, "apply_overrides_to_df", side_effect=lambda frame: frame
            ), patch.object(data_cache, "_apply_stored_state", side_effect=lambda frame, _stored: frame), patch.object(
                data_cache, "apply_priorities", side_effect=lambda frame: frame
            ):
                self.assertTrue(data_cache._load_dashboard_cache())

        state = data_cache.get_state()
        self.assertEqual(state["last_refresh"], loaded_at)
        self.assertEqual(state["last_success_refresh"], loaded_at)


if __name__ == "__main__":
    unittest.main()
