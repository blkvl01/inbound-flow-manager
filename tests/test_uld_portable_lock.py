import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_HOLDER_SCRIPT = r"""
import socket
import sys


def _deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError(f"network access attempted: {event}")


sys.addaudithook(_deny_network)
import uld_stack_manager

uld_stack_manager._acquire_lock()
print("LOCKED", flush=True)
sys.stdin.readline()
uld_stack_manager._release_lock()
print("RELEASED", flush=True)
"""

_PROBE_SCRIPT = r"""
import socket
import sys


def _deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError(f"network access attempted: {event}")


sys.addaudithook(_deny_network)
import uld_stack_manager

uld_stack_manager._LOCK_TIMEOUT_S = 0.25
try:
    uld_stack_manager._acquire_lock()
except TimeoutError:
    print("BLOCKED", flush=True)
else:
    try:
        print("ACQUIRED", flush=True)
    finally:
        uld_stack_manager._release_lock()
"""


class UldPortableLockTests(unittest.TestCase):
    def setUp(self):
        self._root = Path(__file__).resolve().parents[1]
        self._tempdir = tempfile.TemporaryDirectory(prefix="flow-uld-lock-")
        self._tmp = Path(self._tempdir.name)
        self._shared = self._tmp / "shared"
        self._local = self._tmp / "local"
        self._shared.mkdir(parents=True, exist_ok=True)
        self._local.mkdir(parents=True, exist_ok=True)
        self._holder_script = self._tmp / "portable_lock_holder.py"
        self._probe_script = self._tmp / "portable_lock_probe.py"
        self._holder_script.write_text(_HOLDER_SCRIPT, encoding="utf-8")
        self._probe_script.write_text(_PROBE_SCRIPT, encoding="utf-8")

        self._env = os.environ.copy()
        for key in list(self._env):
            if key.startswith("WEBBYCOM_ORACLE_"):
                self._env.pop(key)
        existing_pythonpath = self._env.get("PYTHONPATH")
        self._env.update(
            {
                "COMPUTERNAME": "codex-lock-test",
                "FLOW_ECOMM_SOURCE": "oracle",
                "FLOW_SHARED_STATE_DIR": str(self._shared),
                "LOCALAPPDATA": str(self._local),
                "PYTHONDONTWRITEBYTECODE": "1",
                "USERNAME": "codex-lock-test",
                "PYTHONPATH": str(self._root)
                + (os.pathsep + existing_pythonpath if existing_pythonpath else ""),
            }
        )
        self._children = []

    def tearDown(self):
        for child in self._children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
        self._tempdir.cleanup()

    def _start_holder(self):
        child = subprocess.Popen(
            [sys.executable, "-B", str(self._holder_script)],
            cwd=self._root,
            env=self._env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._children.append(child)
        startup_lines = []
        while True:
            line = child.stdout.readline()
            if not line:
                break
            ready = line.strip()
            if ready == "LOCKED":
                return child
            startup_lines.append(ready)
        child.wait(timeout=5)
        stdout, stderr = child.communicate(timeout=5)
        self.fail(
            f"holder did not acquire the lock (startup={startup_lines!r}, "
            f"returncode={child.returncode}, stdout={stdout!r}, stderr={stderr!r})"
        )

    def _probe(self):
        result = subprocess.run(
            [sys.executable, "-B", str(self._probe_script)],
            cwd=self._root,
            env=self._env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        statuses = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() in {"ACQUIRED", "BLOCKED"}
        ]
        self.assertEqual(len(statuses), 1, result.stdout)
        return statuses[0]

    def test_second_process_is_blocked_until_normal_release(self):
        holder = self._start_holder()

        self.assertEqual(self._probe(), "BLOCKED")

        holder.stdin.write("release\n")
        holder.stdin.flush()
        stdout, stderr = holder.communicate(timeout=10)
        self.assertEqual(holder.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), "RELEASED")
        self.assertEqual(self._probe(), "ACQUIRED")

    def test_process_termination_releases_the_operating_system_lock(self):
        holder = self._start_holder()

        holder.terminate()
        holder.wait(timeout=10)

        self.assertEqual(self._probe(), "ACQUIRED")


if __name__ == "__main__":
    unittest.main()
