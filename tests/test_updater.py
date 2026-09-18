import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
import updater
from scripts.create_release_manifest import build_manifest
from scripts.verify_release import verify_release_directory


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self._read_once = False

    def read(self, _size=-1):
        if isinstance(self.payload, bytes):
            if self._read_once:
                return b""
            self._read_once = True
            return self.payload
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _release_payload(version="1.2.0", exe_bytes=b"new-flow-manager"):
    digest = hashlib.sha256(exe_bytes).hexdigest()
    manifest = {
        "format": 1,
        "app_name": "Inbound Flow Manager",
        "version": version,
        "package": {"name": "FlowManager.exe", "size": len(exe_bytes), "sha256": digest},
    }
    return {
        "tag_name": f"v{version}",
        "assets": [
            {"name": "manifest.json", "browser_download_url": "https://example.test/manifest.json", "size": 100},
            {"name": "FlowManager.exe", "browser_download_url": "https://example.test/FlowManager.exe", "size": len(exe_bytes)},
        ],
    }, manifest, exe_bytes


class UpdaterTests(unittest.TestCase):
    def test_version_comparison_supports_v_prefix_prerelease_and_patch_zero(self):
        self.assertLess(updater.compare_versions("1.2.0-rc.1", "v1.2"), 0)
        self.assertEqual(updater.compare_versions("1.2", "1.2.0+build.7"), 0)
        self.assertGreater(updater.compare_versions("2.0.0", "1.99.99"), 0)

    def test_manifest_and_sha256_are_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "FlowManager.exe"
            exe.write_bytes(b"verified executable")
            manifest = build_manifest(exe, "1.2.3")
            normalized = updater.validate_manifest(manifest)
            package = normalized["package"]
            self.assertTrue(updater.verify_file(exe, package["size"], package["sha256"]))
            exe.write_bytes(b"tampered executable")
            self.assertFalse(updater.verify_file(exe, package["size"], package["sha256"]))

    def test_release_requires_manifest_and_matching_asset(self):
        with self.assertRaises(updater.UpdateError):
            updater.check_for_update("1.0.0", opener=lambda *_args, **_kwargs: _Response({}))

    def test_release_is_only_accepted_when_newer_and_manifest_matches_tag(self):
        release_json, manifest, exe_bytes = _release_payload()
        responses = iter([_Response(release_json), _Response(manifest)])
        release = updater.check_for_update("1.0.0", opener=lambda *_args, **_kwargs: next(responses))
        self.assertEqual(release["version"], "1.2.0")
        self.assertEqual(release["download_url"], "https://example.test/FlowManager.exe")

        old_json, old_manifest, _ = _release_payload("1.0.0")
        responses = iter([_Response(old_json), _Response(old_manifest)])
        self.assertIsNone(updater.check_for_update("1.0.0", opener=lambda *_args, **_kwargs: next(responses)))

    def test_invalid_github_response_falls_back_to_current_version(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "FlowManager.exe"
            target.write_bytes(b"current")
            result = updater.run_startup_update(
                current_version="1.0.0",
                target_executable=target,
                opener=lambda *_args, **_kwargs: _Response({}),
                exit_process=False,
            )
            self.assertFalse(result)
            self.assertEqual(target.read_bytes(), b"current")
            self.assertEqual(updater.get_status()["phase"], "offline")

    def test_download_reports_hash_verification_instead_of_stopping_at_95_percent(self):
        release_json, manifest, exe_bytes = _release_payload()
        _ = release_json
        release = updater.parse_release_payload({
            **release_json,
            "_manifest": manifest,
        })
        with tempfile.TemporaryDirectory() as temp:
            stage = updater._download_to_stage(
                release,
                Path(temp),
                opener=lambda *_args, **_kwargs: _Response(exe_bytes),
            )
            self.assertEqual(stage.read_bytes(), exe_bytes)
            self.assertEqual(updater.get_status()["phase"], "verifying")
            self.assertGreaterEqual(updater.get_status()["progress"], 96)
            stage.unlink()

    def test_automatic_onedrive_workspace_discovery_uses_available_company_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "Ecommerce - Documents"
            workspace.mkdir(parents=True)
            with patch.dict(os.environ, {"FLOW_ONEDRIVE_ROOT": str(Path(temp))}, clear=False):
                self.assertEqual(Path(config._find_onedrive_folder()), workspace)

    def test_frozen_app_puts_shared_state_in_discovered_onedrive_workspace(self):
        import storage_manager

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "Ecommerce - Documents"
            workspace.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {"FLOW_ONEDRIVE_ROOT": str(Path(temp)), "FLOW_SHARED_STATE_DIR": ""},
                clear=False,
            ):
                with patch.object(storage_manager.sys, "frozen", True, create=True):
                    shared = storage_manager._shared_base_dir()
            self.assertEqual(shared, workspace)
            self.assertTrue(shared.is_dir())

    def test_loading_progress_reports_release_download_state(self):
        import app

        with patch.object(updater, "_status", {
            "phase": "downloading", "message": "Új Flow Manager letöltése",
            "progress": 47, "version": "1.0.0", "available_version": "1.1.0",
        }):
            values = app.update_loading_progress(0)
        self.assertEqual(values[0], "Új Flow Manager letöltése")
        self.assertEqual(values[1], "47%")
        self.assertEqual(values[2], {"width": "47%"})
        self.assertEqual(values[3], "47")

    def test_update_overlay_does_not_block_ready_app_when_download_is_slow(self):
        import app

        with patch.object(app.data_cache, "get_state", return_value={
            "status": "ready",
            "refresh_count": 1,
            "refreshing": False,
        }), patch.object(updater, "_status", {
            "phase": "downloading", "message": "Új Flow Manager letöltése",
            "progress": 95, "version": "1.0.0", "available_version": "1.1.0",
        }):
            values = app.update_overlay(0, None, None, False, "first")
        self.assertEqual(values[1], {"display": "none"})
        self.assertEqual(values[2], "done")

    def test_inactivity_shutdown_is_configured_for_thirty_minutes(self):
        import app

        self.assertEqual(app._INACTIVITY_TIMEOUT_MINUTES, 30)
        self.assertEqual(app._INACTIVITY_SHUTDOWN_SECONDS, 30 * 60)
        self.assertEqual(app._NO_POST_SHUTDOWN_SECONDS, 30 * 60)

    def test_release_directory_is_onefile_only_and_hash_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)
            exe = release / "FlowManager.exe"
            exe.write_bytes(b"one-file build")
            (release / "manifest.json").write_text(
                json.dumps(build_manifest(exe, "1.2.3")), encoding="utf-8"
            )
            (release / "README.md").write_text("docs", encoding="utf-8")
            self.assertEqual(verify_release_directory(release), [])
            (release / "_internal").mkdir()
            self.assertTrue(verify_release_directory(release))

    def test_build_configuration_is_onefile(self):
        spec = Path("FlowManager.spec").read_text(encoding="utf-8")
        build = Path("build.bat").read_text(encoding="utf-8")
        self.assertIn("a.binaries", spec)
        self.assertNotIn("COLLECT(", spec)
        self.assertIn("--onefile", build)
        self.assertIn("release\\FlowManager.exe", build)


if __name__ == "__main__":
    unittest.main()
