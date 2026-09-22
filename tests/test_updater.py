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

    def test_download_retries_transient_network_failure(self):
        release_json, manifest, exe_bytes = _release_payload()
        release = updater.parse_release_payload({
            **release_json,
            "_manifest": manifest,
        })
        calls = 0

        def flaky_opener(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporary network stall")
            return _Response(exe_bytes)

        with tempfile.TemporaryDirectory() as temp:
            stage = updater._download_to_stage(release, Path(temp), opener=flaky_opener)
            self.assertEqual(calls, 2)
            self.assertTrue(updater.verify_file(stage, len(exe_bytes), manifest["package"]["sha256"]))
            stage.unlink()

    def test_download_window_allows_slow_release_asset(self):
        self.assertGreaterEqual(updater._DOWNLOAD_DEADLINE_S, 15 * 60)
        self.assertEqual(updater._DOWNLOAD_ATTEMPTS, 3)

    def test_automatic_onedrive_workspace_discovery_uses_available_company_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "Ecommerce - Documents"
            workspace.mkdir(parents=True)
            with patch.dict(os.environ, {"FLOW_ONEDRIVE_ROOT": str(Path(temp))}, clear=False):
                self.assertEqual(Path(config._find_onedrive_folder()), workspace)

    def test_shared_state_discovery_prefers_program_hub_shared_state(self):
        import storage_manager

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "Ecommerce - Documents" / "Program HUB" / "Flow Manager" / "_shared_state"
            workspace.mkdir(parents=True)
            with patch.dict(os.environ, {"FLOW_SHARED_STATE_DIR": ""}, clear=False), \
                    patch.object(config, "_onedrive_roots", return_value=[str(Path(temp))]), \
                    patch("config.read_config_snapshot", return_value={"shared_state_dir": ""}):
                with patch.object(storage_manager.sys, "frozen", True, create=True):
                    shared = storage_manager._shared_base_dir()
            self.assertEqual(shared, workspace)
            self.assertTrue(shared.is_dir())

    def test_shared_state_discovery_supports_localized_ecommerce_documents(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "Ecommerce - Dokumentumok" / "Program HUB" / "Flow Manager" / "_shared_state"
            workspace.mkdir(parents=True)
            with patch.object(config, "_onedrive_roots", return_value=[str(Path(temp))]):
                self.assertEqual(Path(config._find_shared_state_folder()), workspace)

    def test_missing_shared_state_does_not_invent_a_path(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(config, "_onedrive_roots", return_value=[str(Path(temp))]):
                self.assertEqual(config._find_shared_state_folder(), "")

    def test_missing_shared_state_is_created_under_existing_ecommerce_root(self):
        with tempfile.TemporaryDirectory() as temp:
            documents = Path(temp) / "Ecommerce - Documents"
            documents.mkdir(parents=True)
            expected = documents / "Program HUB" / "Flow Manager" / "_shared_state"
            with patch.object(config, "_onedrive_roots", return_value=[str(Path(temp))]):
                self.assertEqual(config._ensure_shared_state_folder(), str(expected))
            self.assertTrue(expected.is_dir())

    def test_frozen_startup_offers_shared_state_picker_when_auto_discovery_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path = Path(temp) / "config.json"
            selected = Path(temp) / "existing-shared-state"
            selected.mkdir()
            cfg = {"shared_state_dir": ""}
            with patch.object(config.sys, "frozen", True, create=True), \
                    patch.object(config, "_discover_shared_state_folder", return_value=""), \
                    patch.object(config, "pick_shared_directory", return_value=str(selected)):
                result = config._ensure_shared_state_config(cfg, cfg_path)
            self.assertEqual(result["shared_state_dir"], str(selected))
            saved = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["shared_state_dir"], str(selected))

    def test_legacy_project_shared_state_is_migrated_to_program_hub_shared_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "Ecommerce - Documents" / "Flow Manager" / "_shared_state"
            automatic = root / "Ecommerce - Documents" / "Program HUB" / "Flow Manager" / "_shared_state"
            automatic.mkdir(parents=True)
            legacy.mkdir(parents=True)
            (legacy / "stored_awbs.shared.json").write_text('{"123": {}}', encoding="utf-8")
            cfg_path = root / "config.json"
            cfg = {"shared_state_dir": str(legacy)}
            with patch.object(config, "_onedrive_roots", return_value=[str(root)]):
                result = config._ensure_shared_state_config(cfg, cfg_path)
            self.assertEqual(result["shared_state_dir"], "")
            self.assertEqual(
                (automatic / "stored_awbs.shared.json").read_text(encoding="utf-8"),
                '{"123": {}}',
            )
            self.assertTrue(legacy.is_dir())

    def test_legacy_launcher_does_not_force_project_owned_shared_state(self):
        launcher = Path("Inditas.bat").read_text(encoding="utf-8")
        self.assertNotIn("set \"FLOW_SHARED_STATE_DIR=", launcher)
        self.assertNotIn("_shared_state", launcher)
        self.assertNotIn("FLOW_USER_CONFIG_DIR", launcher)

    def test_selected_shared_state_directory_is_overwritten_by_automatic_discovery(self):
        import storage_manager

        with tempfile.TemporaryDirectory() as temp:
            selected = Path(temp) / "Flow Manager" / "_shared_state"
            selected.mkdir(parents=True)
            automatic = Path(temp) / "Ecommerce - Documents" / "Program HUB" / "Flow Manager" / "_shared_state"
            automatic.mkdir(parents=True)
            with patch.dict(os.environ, {"FLOW_SHARED_STATE_DIR": ""}, clear=False):
                with patch.object(storage_manager.sys, "frozen", True, create=True):
                    with patch("config.read_config_snapshot", return_value={"shared_state_dir": str(selected)}), \
                            patch("config._find_shared_state_folder", return_value=str(automatic)):
                        self.assertEqual(storage_manager._shared_base_dir(), automatic)

    def test_shared_state_directory_is_persisted_as_a_user_setting(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path = Path(temp) / "config.json"
            selected = Path(temp) / "Flow Manager" / "_shared_state"
            selected.mkdir(parents=True)
            with patch.object(config, "get_config_path", return_value=cfg_path):
                config.save_config_updates({"shared_state_dir": str(selected)})
                self.assertEqual(config.read_config_snapshot()["shared_state_dir"], str(selected))

    def test_loading_progress_reports_release_download_state(self):
        import app

        with patch.object(updater, "_status", {
            "phase": "downloading", "message": "Új Flow Manager letöltése",
            "progress": 47, "version": "1.0.0", "available_version": "1.1.0",
        }):
            values = app.update_loading_progress(0)
        self.assertEqual(values[0], "Új Flow Manager letöltése")
        self.assertEqual(values[1], "Frissítés ellenőrzése folyamatban")
        self.assertEqual(values[2], "47%")
        self.assertEqual(values[3], {"width": "47%"})
        self.assertEqual(values[4], "47")

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

    def test_inactivity_shutdown_is_configured_for_two_hours(self):
        import app

        self.assertEqual(app._INACTIVITY_TIMEOUT_MINUTES, 120)
        self.assertEqual(app._INACTIVITY_SHUTDOWN_SECONDS, 120 * 60)
        self.assertEqual(app._NO_POST_SHUTDOWN_SECONDS, 120 * 60)

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
        self.assertIn("flow_manager_version.txt", spec)
        self.assertIn("write_build_version.py", build)
        self.assertIn("--onefile", build)
        self.assertIn("release\\FlowManager.exe", build)


if __name__ == "__main__":
    unittest.main()
