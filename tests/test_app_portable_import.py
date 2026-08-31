import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_IMPORT_SCRIPT = r"""
import sys


def _deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
        raise AssertionError(f"network access attempted: {event}")


sys.addaudithook(_deny_network)
import app
import pandas

assert app.pd is pandas
print("APP_IMPORT_OK", flush=True)
"""


class AppPortableImportTests(unittest.TestCase):
    def test_app_imports_without_oracle_credentials_or_network(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="flow-app-import-") as temp:
            state = Path(temp)
            local = state / "local"
            shared = state / "shared"
            source = state / "source"
            source.mkdir(parents=True)
            shared.mkdir(parents=True)

            ecomm = source / "no-live-ecomm.xlsb"
            pallets = source / "no-live-pallets.xlsm"
            ecomm.touch()
            pallets.touch()

            username = "codex-portable-import"
            computer = "codex-portable-import"
            config_dir = local / "InboundFlowManager" / computer / username
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps(
                    {
                        "ecomm_file": str(ecomm),
                        "pallets_file": str(pallets),
                        "refresh_interval_minutes": 10,
                        "port": 8501,
                    }
                ),
                encoding="utf-8",
            )

            helper = state / "portable_app_import.py"
            helper.write_text(_IMPORT_SCRIPT, encoding="utf-8")

            env = os.environ.copy()
            for key in list(env):
                if key.startswith("WEBBYCOM_ORACLE_"):
                    env.pop(key)
            existing_pythonpath = env.get("PYTHONPATH")
            env.update(
                {
                    "COMPUTERNAME": computer,
                    "FLOW_ECOMM_SOURCE": "excel",
                    "FLOW_SHARED_STATE_DIR": str(shared),
                    "LOCALAPPDATA": str(local),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "USERNAME": username,
                    "PYTHONPATH": str(root)
                    + (os.pathsep + existing_pythonpath if existing_pythonpath else ""),
                }
            )

            result = subprocess.run(
                [sys.executable, "-B", str(helper)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("APP_IMPORT_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
