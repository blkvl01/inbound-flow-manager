"""Canonical application identity and build-time version.

The development checkout intentionally uses ``0.0.0`` until a release build
supplies ``FLOW_MANAGER_VERSION``.  The build script passes the release version
as an environment variable so the frozen executable and its manifest always
carry the same value.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


APP_NAME = "Inbound Flow Manager"
PACKAGE_NAME = "FlowManager.exe"
GITHUB_REPOSITORY = "blkvl01/inbound-flow-manager"
DEFAULT_VERSION = "0.0.0"


def _embedded_frozen_version() -> str:
    if not getattr(sys, "frozen", False):
        return ""
    try:
        version_file = Path(sys._MEIPASS) / "flow_manager_version.txt"
        return version_file.read_text(encoding="utf-8").strip()
    except (AttributeError, OSError, UnicodeError):
        return ""


APP_VERSION = (
    _embedded_frozen_version()
    or os.environ.get("FLOW_MANAGER_VERSION")
    or DEFAULT_VERSION
).strip()
