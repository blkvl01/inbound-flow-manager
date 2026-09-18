"""Canonical application identity and build-time version.

The development checkout intentionally uses ``0.0.0`` until a release build
supplies ``FLOW_MANAGER_VERSION``.  The build script passes the release version
as an environment variable so the frozen executable and its manifest always
carry the same value.
"""

from __future__ import annotations

import os


APP_NAME = "Inbound Flow Manager"
PACKAGE_NAME = "FlowManager.exe"
GITHUB_REPOSITORY = "blkvl01/inbound-flow-manager"
DEFAULT_VERSION = "0.0.0"


APP_VERSION = (os.environ.get("FLOW_MANAGER_VERSION") or DEFAULT_VERSION).strip()
