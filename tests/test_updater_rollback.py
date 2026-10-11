import hashlib
import http.server
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import updater


class _FakeProcess:
    def __init__(self, exit_code=None, write_probe=None):
        self.pid = 4242
        self._exit = exit_code
        self._write_probe = write_probe

    def poll(self):
        if self._write_probe is not None:
            self._write_probe.write_text("ok", encoding="utf-8")
        return self._exit


class HelperRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.helper_dir = root / "local" / "FlowManager" / "updates"
        self.helper_dir.mkdir(parents=True)
        self.target = root / "FlowManager.exe"
        self.target.write_bytes(b"old")
        self.stage = root / "new.tmp"
        self.stage.write_bytes(b"new")
        self.digest = hashlib.sha256(b"new").hexdigest()
        self.args = ["helper", updater.HELPER_ARG, "1234", str(self.target), str(self.stage), "3", self.digest]
        self.patches = [
            patch.object(updater, "_helper_dir", return_value=self.helper_dir),
            patch.object(updater, "_wait_for_parent", return_value=True),
            patch.object(updater, "_HEALTH_POLL_S", 0),
            patch.object(updater, "_kill_tree"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _probe_from(self, launch):
        command = launch.call_args.args[0]
        return Path(command[command.index(updater.HEALTH_PROBE_ARG) + 1])

    def test_healthy_new_version_keeps_previous_as_backup(self):
        def launch(command, **_):
            probe = Path(command[command.index(updater.HEALTH_PROBE_ARG) + 1])
            return _FakeProcess(write_probe=probe)
        with patch.object(updater.subprocess, "Popen", side_effect=launch):
            self.assertEqual(updater.run_helper_cli(self.args), 0)
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertEqual(updater.backup_path(self.target.resolve()).read_bytes(), b"old")
        self.assertEqual(updater._load_bad_digests(), set())

    def test_crashing_new_version_is_rolled_back_and_blocked(self):
        with patch.object(updater.subprocess, "Popen", return_value=_FakeProcess(exit_code=1)), \
                patch.object(updater, "_restart_executable", return_value=True) as restart:
            self.assertEqual(updater.run_helper_cli(self.args), 7)
        updater._kill_tree.assert_called_once()
        self.assertEqual(self.target.read_bytes(), b"old")
        restart.assert_called_once()
        self.assertIn(self.digest, updater._load_bad_digests())

    def test_slow_start_is_never_rolled_back_or_killed(self):
        with patch.object(updater.subprocess, "Popen", return_value=_FakeProcess(exit_code=None)), \
                patch.object(updater, "_HEALTH_TIMEOUT_S", 0.05), \
                patch.object(updater, "_restart_executable") as restart:
            self.assertEqual(updater.run_helper_cli(self.args), 0)
        updater._kill_tree.assert_not_called()
        restart.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertEqual(updater._load_bad_digests(), set())

    def test_clean_exit_before_serving_is_not_a_crash(self):
        with patch.object(updater.subprocess, "Popen", return_value=_FakeProcess(exit_code=0)), \
                patch.object(updater, "_restart_executable") as restart:
            self.assertEqual(updater.run_helper_cli(self.args), 0)
        restart.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertEqual(updater._load_bad_digests(), set())

    def test_blocked_package_is_not_offered_again(self):
        updater._mark_bad_digest(self.digest, "teszt")
        release = {"version": "9.9.9", "manifest": {"package": {"sha256": self.digest.upper()}}}
        with patch.object(updater, "_load_release", return_value=release):
            self.assertIsNone(updater.check_for_update("1.0.0"))
        other = {"version": "9.9.9", "manifest": {"package": {"sha256": "a" * 64}}}
        with patch.object(updater, "_load_release", return_value=other):
            self.assertIs(updater.check_for_update("1.0.0"), other)


class HealthProbeTests(unittest.TestCase):
    def test_probe_written_once_layout_is_served_and_bypasses_proxy(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/_dash-layout" else 404)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as temp:
            helper_dir = Path(temp)
            probe = helper_dir / "probe-1-abcdefabcdef.ok"
            with patch.object(updater, "_helper_dir", return_value=helper_dir), \
                    patch.dict("os.environ", {"HTTP_PROXY": "http://10.255.255.1:9", "http_proxy": "http://10.255.255.1:9"}):
                thread = updater.start_health_probe_from_arguments(
                    server.server_address[1], ["app", updater.HEALTH_PROBE_ARG, str(probe)])
                thread.join(timeout=10)
            server.shutdown()
            self.assertEqual(probe.read_text(encoding="utf-8"), "ok")

    def test_probe_path_outside_helper_dir_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(updater, "_helper_dir", return_value=Path(temp) / "updates"):
                self.assertIsNone(updater.start_health_probe_from_arguments(
                    1, ["app", updater.HEALTH_PROBE_ARG, str(Path(temp) / "probe-1-abcdefabcdef.ok")]))
                self.assertIsNone(updater.start_health_probe_from_arguments(1, ["app"]))


if __name__ == "__main__":
    unittest.main()
