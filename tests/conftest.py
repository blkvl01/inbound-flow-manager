"""Keep shared-state files out of the repository during tests.

Without FLOW_SHARED_STATE_DIR the shared JSON/lock files fall back to the
source folder, so tests that touch ULD or stored state would leave lock files
in the repository root.  Tests that need a specific folder still override it.
"""
import os
import shutil
import tempfile

_TEST_SHARED_DIR = tempfile.mkdtemp(prefix="flow_shared_state_tests_")
os.environ.setdefault("FLOW_SHARED_STATE_DIR", _TEST_SHARED_DIR)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_SHARED_DIR, ignore_errors=True)
