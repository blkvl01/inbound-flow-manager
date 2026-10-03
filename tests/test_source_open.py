import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import config
import source_open


class ExcelDiscoveryTests(unittest.TestCase):
    def test_all_supported_sync_layouts_and_language_names(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp)
            for rootname in ('registered', 'OneDrive - HGL Group Hungary Kft', 'HGL Group Hungary Kft'):
                for name in ('Ecommerce - Dokumentumok', 'Ecommerce - Documents'):
                    root = profile / rootname
                    folder = root / name
                    folder.mkdir(parents=True)
                    source = folder / 'BUD-Pallets.xlsm'
                    source.touch()
                    with patch.dict(os.environ, USERPROFILE=temp), patch.object(config, '_onedrive_roots', return_value=[str(profile / 'registered'), str(profile / 'OneDrive - HGL Group Hungary Kft')]):
                        self.assertEqual(config._find_excel_source(source.name), str(source))
                    source.unlink()

    def test_each_workbook_resolves_separately_with_normal_roots_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registered = root / 'registered' / 'Ecommerce - Documents'
            fallback = root / 'HGL Group Hungary Kft' / 'Ecommerce - Dokumentumok'
            registered.mkdir(parents=True)
            fallback.mkdir(parents=True)
            (registered / 'BUD-Pallets.xlsm').touch()
            (fallback / 'BUD-Pallets.xlsm').touch()
            (fallback / 'E_COMM nyomonkövetés_24.xlsb').touch()
            with patch.dict(os.environ, USERPROFILE=temp), patch.object(config, '_onedrive_roots', return_value=[str(registered.parent)]):
                self.assertEqual(config._find_excel_source('BUD-Pallets.xlsm'), str(registered / 'BUD-Pallets.xlsm'))
                self.assertEqual(config._find_excel_source('E_COMM nyomonkövetés_24.xlsb'), str(fallback / 'E_COMM nyomonkövetés_24.xlsb'))


class SourceOpenTests(unittest.TestCase):
    def setUp(self):
        self.client = app.server.test_client()

    def test_endpoint_uses_runtime_original_only(self):
        with patch.object(app, 'ECOMM_FILE', 'active.xlsb'), patch.object(app.oracle_ecomm, 'get_source_mode', return_value='excel'), patch.object(source_open.opener, 'open', return_value=(True, 'Megnyitás kérve')) as launch:
            response = self.client.post('/api/sources/open', json={'kind': 'ecomm', 'path': 'client-injected.exe'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(launch.call_args.args[1], 'active.xlsb')

    def test_invalid_origin_host_remote_and_kind_rejected(self):
        for options in ({'headers': {'Origin': 'https://external.example'}}, {'headers': {'Origin': 'http://[invalid'}}, {'headers': {'Sec-Fetch-Site': 'cross-site'}}, {'base_url': 'http://evil.example'}, {'environ_overrides': {'REMOTE_ADDR': '192.168.1.2'}}):
            with patch.object(source_open.opener, 'open') as launch:
                response = self.client.post('/api/sources/open', json={'kind': 'ecomm'}, **options)
                self.assertEqual(response.status_code, 403)
                launch.assert_not_called()
        self.assertEqual(self.client.post('/api/sources/open', json={'kind': 'other'}).status_code, 400)
        self.assertEqual(self.client.post('/api/sources/open', json=[]).status_code, 400)
        self.assertEqual(self.client.post('/api/sources/open', json={'kind': []}).status_code, 400)

    def test_oracle_rejected(self):
        with patch.object(app.oracle_ecomm, 'get_source_mode', return_value='oracle'):
            self.assertEqual(self.client.post('/api/sources/open', json={'kind': 'pallets'}).status_code, 409)

    def test_t_mode_endpoint_opens_same_original_and_refreshes(self):
        for kind, target in (('ecomm', 'actual-ecomm.xlsb'), ('pallets', 'actual-pallets.xlsm')):
            with patch.object(app, 'ECOMM_FILE', 'actual-ecomm.xlsb'), patch.object(app.flow_config, 'PALLETS_FILE', 'actual-pallets.xlsm'), patch.object(app.oracle_ecomm, 'get_source_mode', return_value='excel'), patch.object(source_open.opener, 'open', return_value=(True, 'Megnyitva')) as launch, patch.object(app.data_cache, 'trigger_refresh') as refresh:
                response = self.client.post('/api/sources/open', json={'kind': kind, 'test_mode': True, 'path': 'injected-demo.xlsx'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(launch.call_args.args[0:2], (kind, target))
                launch.call_args.args[2]()
                refresh.assert_called_once_with()
        self.assertEqual(self.client.post('/api/sources/open', json={'kind': 'ecomm', 'test_mode': 'true'}).status_code, 400)

    def test_t_mode_missing_real_source_is_error_and_oracle_stays_guarded(self):
        with patch.object(app, 'ECOMM_FILE', 'missing-original.xlsb'), patch.object(app.oracle_ecomm, 'get_source_mode', return_value='excel'), patch.object(source_open.os, 'startfile') as launch:
            response = self.client.post('/api/sources/open', json={'kind': 'ecomm', 'test_mode': True})
            self.assertEqual(response.status_code, 400)
            self.assertFalse(response.get_json()['ok'])
            launch.assert_not_called()
        with patch.object(app.oracle_ecomm, 'get_source_mode', return_value='oracle'):
            self.assertEqual(self.client.post('/api/sources/open', json={'kind': 'pallets', 'test_mode': True}).status_code, 409)

    def test_t_mode_shows_real_source_buttons_during_cold_loading(self):
        import time
        with patch.object(app.data_cache, 'get_state', return_value={'status': 'loading'}), patch.object(app.oracle_ecomm, 'get_source_mode', return_value='excel'), patch.object(app.os.path, 'getmtime', return_value=time.time()):
            rows = app.update_countdown(0, None, True)
            self.assertEqual(rows[0].children, 'TESZTMÓD')
            for row in rows[1:]:
                button = row.children[1].to_plotly_json()['props']
                self.assertEqual(button['data-source-action'], 'open')
                self.assertEqual(button['children'], 'Megnyitás Excelben')
                self.assertNotIn('TESZT', row.children[0].children)
            self.assertEqual(app.update_countdown(0, None, False).children, 'Betöltés folyamatban...')

    def test_relaunch_always_launches_but_only_one_watcher_and_failure_preserves_it(self):
        opener = source_open.SourceOpener()
        launch = Mock()
        self.assertFalse(opener.open('ecomm', 'absent.xlsb', Mock(), Mock(), launch)[0])
        with tempfile.TemporaryDirectory() as temp, patch.object(source_open.threading, 'Thread') as thread:
            source = Path(temp) / 'source.exe'
            source.touch()
            self.assertFalse(opener.open('ecomm', str(source), Mock(), Mock(), launch)[0])
            source = source.with_suffix('.xlsb')
            source.touch()
            for _ in range(2):
                self.assertTrue(opener.open('ecomm', str(source), Mock(), Mock(), launch)[0])
            self.assertEqual(launch.call_count, 2)
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            watch_key = ('ecomm', str(source))
            self.assertIn(watch_key, opener._active)
            self.assertFalse(opener.open('ecomm', str(source), Mock(), Mock(), Mock(side_effect=OSError))[0])
            self.assertIn(watch_key, opener._active)
            self.assertEqual(thread.call_count, 1)

    def test_watch_waits_for_stable_change_then_refreshes_once(self):
        opener = source_open.SourceOpener()
        refresh = Mock()
        with patch.object(source_open, 'signature', side_effect=[(1, 1), (2, 2), (3, 3), (3, 3)]), patch.object(source_open.time, 'sleep'):
            opener._watch('ecomm', 'original.xlsb', (1, 1), refresh, lambda: 'original.xlsb')
        refresh.assert_called_once_with()

    def test_watch_unchanged_timeout_and_new_source_do_not_refresh(self):
        opener = source_open.SourceOpener()
        refresh = Mock()
        with patch.object(source_open.time, 'monotonic', side_effect=[0, 0, 1, 2]), patch.object(source_open.time, 'sleep'), patch.object(source_open, 'signature', return_value=(1, 1)):
            opener._watch('ecomm', 'original.xlsb', (1, 1), refresh, lambda: 'original.xlsb', timeout=2)
        with patch.object(source_open.time, 'sleep'):
            opener._watch('ecomm', 'original.xlsb', (1, 1), refresh, lambda: 'new.xlsb')
        refresh.assert_not_called()

    def test_badges_show_buttons_only_when_warning(self):
        import time
        with patch.object(app.oracle_ecomm, 'get_source_mode', return_value='excel'), patch.object(app.data_cache, 'get_state', return_value={'read_stalled': False}), patch.object(app.os.path, 'getmtime', return_value=time.time()):
            badge = app._ecomm_source_badge()
            self.assertFalse(any(getattr(child, 'className', '') == 'source-open-btn' for child in badge.children))
        with patch.object(app.oracle_ecomm, 'get_source_mode', return_value='excel'), patch.object(app.data_cache, 'get_state', return_value={'read_stalled': True}), patch.object(app.os.path, 'getmtime', return_value=time.time()):
            for badge in (app._ecomm_source_badge(), app._pallets_source_badge()):
                self.assertTrue(any(getattr(child, 'className', '') == 'source-open-btn' for child in badge.children))
        with patch.object(app.oracle_ecomm, 'get_source_mode', return_value='excel'), patch.object(app.os.path, 'getmtime', side_effect=OSError):
            button = app._ecomm_source_badge().children[1]
            self.assertEqual(button.to_plotly_json()['props']['data-source-action'], 'settings')
