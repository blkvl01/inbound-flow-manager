"""Run the complete test suite with isolated state and no live connections."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    # All configuration and runtime writes belong to this temporary test run.
    with tempfile.TemporaryDirectory(prefix="flow-codex-tests-") as temp:
        state = Path(temp)
        for key in list(os.environ):
            if key.startswith("WEBBYCOM_ORACLE_"):
                os.environ.pop(key)
        os.environ.update(
            LOCALAPPDATA=str(state / "local"),
            USERNAME="codex-test", COMPUTERNAME="isolated-test",
            FLOW_SHARED_STATE_DIR=str(state / "shared"),
            FLOW_ECOMM_SOURCE="oracle",
        )

        def disallow_network(event, args):
            if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
                raise RuntimeError("Live network access is disabled in the isolated test suite.")

        sys.addaudithook(disallow_network)
        # Oracle mode only suppresses the desktop file-picker during import.
        # No Oracle refresh is started. Tests then use the normal Excel default.
        import config

        config.ECOMM_FILE = str(state / "no-live-ecomm.xlsb")
        config.PALLETS_FILE = str(state / "no-live-pallets.xlsm")
        config.CFG.update(ecomm_file=config.ECOMM_FILE, pallets_file=config.PALLETS_FILE)
        config._DEFAULT.update(ecomm_file=config.ECOMM_FILE, pallets_file=config.PALLETS_FILE)
        config.save_config_updates(config.CFG)
        os.environ["FLOW_ECOMM_SOURCE"] = "excel"
        tests = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(tests)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
