import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import pandas as pd
import data_cache
import uld_stack_manager
import config


class ReaderProcessTests(unittest.TestCase):
    def test_worker_settings_skip_picker_and_never_rewrite_parent_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            settings = path / "settings.json"
            settings.write_text(json.dumps({"ecomm_file": "worker.xlsb", "pallets_file": "worker.xlsm"}))
            config_path = path / "config.json"
            original = '{"ecomm_file":"parent.xlsb","pallets_file":"parent.xlsm","port":8557}'
            config_path.write_text(original)
            with patch.dict(os.environ, {"FLOW_READ_WORKER_SETTINGS": str(settings)}), \
                    patch.object(config, "get_config_path", return_value=config_path), \
                    patch.object(config, "_pick_files_gui") as picker, \
                    patch.object(config, "_ensure_shared_state_config") as shared:
                loaded = config._load()
            self.assertEqual(loaded["ecomm_file"], "worker.xlsb")
            self.assertEqual(loaded["pallets_file"], "worker.xlsm")
            self.assertEqual(loaded["port"], 8557)
            self.assertEqual(config_path.read_text(), original)
            picker.assert_not_called()
            shared.assert_not_called()

    def test_actual_reader_is_stopped_on_timeout_and_retry_can_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "reader.pid"
            code = "import os,time,pathlib;pathlib.Path(%r).write_text(str(os.getpid()));time.sleep(60)" % str(pid_path)
            start = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                data_cache._run_isolated_read(timeout=1.5,
                    command_factory=lambda _destination: [sys.executable, "-c", code])
            self.assertLess(time.monotonic() - start, 8)
            pid = int(pid_path.read_text())
            if os.name == "nt":
                result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                        capture_output=True, text=True, check=True)
                self.assertNotIn(f'"{pid}"', result.stdout)
            else:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            successful = ("import pickle,pathlib,sys;"
                          "pickle.dump({'result':(None,{}, {}, [],{},set(),[])},"
                          "open(pathlib.Path(sys.argv[1])/'result.pkl','wb'))")
            payload = data_cache._run_isolated_read(timeout=3,
                command_factory=lambda destination: [sys.executable, "-c", successful, str(destination)])
            self.assertEqual(len(payload["result"]), 7)

    def test_crashed_reader_is_reported_and_not_applied(self):
        with self.assertRaisesRegex(RuntimeError, "Source reader exited"):
            data_cache._run_isolated_read(timeout=3,
                command_factory=lambda _: [sys.executable, "-c", "raise RuntimeError('fixture failure')"])

    def test_copy_version_and_complete_uld_metadata_survive_source_change(self):
        saved = data_cache.get_state()
        captured = {"old": True}
        live = {"new": True}
        metadata = {"uld_times_full": {"PMC001": {"am": "value"}},
                    "uld_returned": [{"uld_number": "PMC002"}], "uld_archive": []}
        result = (pd.DataFrame(), {}, {"_source_signature": captured, "_uld_meta": metadata},
                  [], {}, set(), [{"uld_number": "PMC001"}])
        try:
            with data_cache._lock:
                data_cache._state["defer_fast_uld"] = True
                data_cache._state["uld_last_refresh"] = None
            with patch.object(data_cache, "_source_signature", return_value=live), \
                    patch.object(data_cache, "_save_dashboard_cache") as save, \
                    patch.object(data_cache, "trigger_refresh") as refresh, \
                    patch.object(data_cache.threading, "Thread") as thread:
                self.assertTrue(data_cache._apply_read_result(result, first=True))
            state = data_cache.get_state()
            self.assertEqual(state["source_signature"], captured)
            self.assertEqual(state["uld_times_full"], metadata["uld_times_full"])
            self.assertTrue(state["source_changed_during_read"])
            self.assertNotIn("_uld_meta", state["kpi"])
            self.assertEqual(save.call_args.args[8], captured)
            refresh.assert_called_once()
            thread.assert_not_called()
        finally:
            with data_cache._lock:
                data_cache._state.clear()
                data_cache._state.update(saved)

    def test_fast_uld_completed_during_shared_cleanup_wins_at_publication(self):
        from datetime import datetime, timedelta
        saved = data_cache.get_state()
        started = datetime.now() - timedelta(seconds=10)
        newer = [{"uld_number": "PMC-NEW"}]
        result = (pd.DataFrame(), {}, {"_uld_meta": {"uld_archive": []}},
                  [], {}, {"issued"}, [{"uld_number": "PMC-OLD"}])
        def simultaneous_fast_read():
            with data_cache._lock:
                data_cache._state["uld_data"] = newer
                data_cache._state["uld_last_refresh"] = datetime.now()
                data_cache._state["uld_source_signature"] = {"newer": True}
                data_cache._state["uld_times_full"] = {"PMC-NEW": {"am": "new"}}
            return set()
        try:
            with data_cache._lock:
                data_cache._state["uld_last_refresh"] = None
            with patch.object(data_cache.storage_manager, "get_stored_awbs", side_effect=simultaneous_fast_read), \
                    patch.object(data_cache, "_save_dashboard_cache") as save, \
                    patch.object(data_cache, "trigger_refresh"):
                self.assertTrue(data_cache._apply_read_result(result, False, started))
            self.assertEqual(data_cache.get_state()["uld_data"], newer)
            self.assertEqual(save.call_args.args[6], newer)
            self.assertEqual(save.call_args.args[9], {"newer": True})
        finally:
            with data_cache._lock:
                data_cache._state.clear()
                data_cache._state.update(saved)

    def test_partial_shared_json_is_not_published_and_display_keeps_last_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "uld_stacks.shared.json"
            old_sig, old_value = uld_stack_manager._read_cache_sig, uld_stack_manager._read_cache_value
            try:
                with patch.object(uld_stack_manager, "get_stacks_path", return_value=path):
                    path.write_text(json.dumps({"stacks": [{"id": "one", "name": "One", "ulds": []}]}))
                    first = uld_stack_manager.get_stacks_cached()
                    path.write_text('{"stacks": [')
                    self.assertFalse(data_cache._shared_json_is_complete(str(path)))
                    self.assertEqual(uld_stack_manager.get_stacks_cached(), first)
                    path.write_text(json.dumps({"stacks": []}))
                    self.assertTrue(data_cache._shared_json_is_complete(str(path)))
                    self.assertEqual(uld_stack_manager.get_stacks_cached(), [])
            finally:
                uld_stack_manager._read_cache_sig, uld_stack_manager._read_cache_value = old_sig, old_value

    def test_legacy_display_identity_matches_authoritative_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "uld_stacks.shared.json"
            path.write_text(json.dumps({"stacks": [{"name": "Legacy", "ulds": []}]}))
            old_sig, old_value = uld_stack_manager._read_cache_sig, uld_stack_manager._read_cache_value
            try:
                with patch.object(uld_stack_manager, "get_stacks_path", return_value=path), \
                        patch.object(uld_stack_manager, "_shared_dir", return_value=Path(directory)):
                    display = uld_stack_manager.get_stacks_cached()
                    authoritative = uld_stack_manager.get_stacks()
                    self.assertEqual(display[0]["id"], authoritative[0]["id"])
            finally:
                uld_stack_manager._read_cache_sig, uld_stack_manager._read_cache_value = old_sig, old_value


if __name__ == "__main__":
    unittest.main()
