"""Wave 1 audit fixes, ULD area (B6, B15, cold-start poll gate)."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from dash import no_update

import app
import data_cache


def _walk(component):
    if component is None or isinstance(component, (str, int, float)):
        return
    if isinstance(component, (list, tuple)):
        for child in component:
            yield from _walk(child)
        return
    yield component
    yield from _walk(getattr(component, "children", None))


def _by_class(component, class_part):
    return [c for c in _walk(component) if class_part in (getattr(c, "className", "") or "").split()]


def _text(component):
    if component is None:
        return ""
    if isinstance(component, (str, int, float)):
        return str(component)
    if isinstance(component, (list, tuple)):
        return " ".join(_text(c) for c in component)
    return _text(getattr(component, "children", None))


class UldRowBadgeStyleTest(unittest.TestCase):
    def test_type_badge_keeps_prefix_color_for_stacked_row(self):
        uld = {
            "uld_number": "PMC12345LH", "uld_type": "PMC", "awbs": ["111"],
            "gha": "Menzies", "gha_conflict": False, "status": "ok", "remaining_hours": 10,
            "expiry_time": datetime.now().isoformat(), "is_expired": False,
        }
        stacks = [{"id": "s1", "name": "Menzies 1", "ulds": ["PMC12345LH"]}]

        row = app._make_uld_row(uld, stacks)

        pcolor = app._uld_prefix_color("PMC")
        type_badge = _by_class(row, "uld-type-badge")[0]
        self.assertEqual(type_badge.style["color"], pcolor)
        stack_badge = _by_class(row, "uld-in-stack-badge")[0]
        self.assertIn("--stack-gha-color", stack_badge.style)


class UldReadErrorStateTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(data_cache._state)
        self.addCleanup(self._restore)
        data_cache._state.update({
            "uld_data": [{"uld_number": "OLD"}],
            "uld_source_signature": None,
            "uld_refreshing": False,
            "uld_read_error": None,
            "uld_read_error_at": None,
            "status": "ready",
            "refresh_count": 1,
            "data_version": 10,
        })
        patcher = mock.patch.object(data_cache, "_uld_source_signature", return_value={"source": "x"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        data_cache._state.clear()
        data_cache._state.update(self._saved)

    def test_failed_read_records_error_keeps_last_good_and_backs_off(self):
        with mock.patch.object(data_cache, "load_uld_data_fast",
                               return_value=([], {"error": "fájl zárolva"})) as loader:
            self.assertFalse(data_cache.refresh_uld_data(force=False))
            state = data_cache.get_state()
            self.assertEqual(state["uld_read_error"], "fájl zárolva")
            self.assertIsInstance(state["uld_read_error_at"], datetime)
            self.assertFalse(state["uld_refreshing"])
            self.assertEqual(state["uld_data"], [{"uld_number": "OLD"}])
            self.assertGreater(state["data_version"], 10)

            # Within the backoff a non-forced call must not read the Excel again.
            self.assertFalse(data_cache.refresh_uld_data(force=False))
            self.assertEqual(loader.call_count, 1)

    def test_reader_exception_is_recorded_not_raised(self):
        with mock.patch.object(data_cache, "load_uld_data_fast", side_effect=OSError("placeholder")):
            self.assertFalse(data_cache.refresh_uld_data(force=False))
        self.assertIn("placeholder", data_cache.get_state()["uld_read_error"])

    def test_success_after_backoff_clears_error(self):
        data_cache._state["uld_read_error"] = "régi hiba"
        data_cache._state["uld_read_error_at"] = datetime.now() - timedelta(seconds=120)
        with mock.patch.object(data_cache, "load_uld_data_fast",
                               return_value=([{"uld_number": "NEW"}], {})), \
             mock.patch.object(data_cache, "_save_current_cache_snapshot"):
            self.assertTrue(data_cache.refresh_uld_data(force=False))
        state = data_cache.get_state()
        self.assertIsNone(state["uld_read_error"])
        self.assertIsNone(state["uld_read_error_at"])
        self.assertEqual(state["uld_data"], [{"uld_number": "NEW"}])

    def test_retry_due_helper(self):
        now = datetime(2026, 11, 19, 12, 0, 0)
        self.assertTrue(app._uld_retry_due({}, now))
        self.assertFalse(app._uld_retry_due({"uld_read_error_at": now - timedelta(seconds=10)}, now))
        self.assertTrue(app._uld_retry_due({"uld_read_error_at": now - timedelta(seconds=61)}, now))


class UldViewErrorPanelTest(unittest.TestCase):
    def _render(self, state, triggered, current_version):
        fake_ctx = SimpleNamespace(triggered_prop_ids={f"{triggered}.n_intervals": triggered},
                                   triggered_id=triggered)
        with mock.patch.object(app, "ctx", fake_ctx), \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_cached", return_value=[]), \
             mock.patch.object(app, "_active_uld_records", return_value=[]), \
             mock.patch.object(app, "_make_stacks_section", return_value=[]), \
             mock.patch.object(app.threading, "Thread") as thread:
            rendered = app.render_uld_view(
                0, "all", 1, "uld", "active", "", "", "", 1, None, current_version, ""
            )
        return rendered, thread

    def test_failed_first_read_shows_error_panel_without_new_thread(self):
        state = {
            "status": "ready", "uld_initial_load_complete": False, "data_version": 5,
            "uld_last_refresh": None, "uld_refreshing": False,
            "uld_read_error": "E_COMM nem elérhető", "uld_read_error_at": datetime.now(),
        }
        rendered, thread = self._render(state, "uld-page-store", 4)

        text = _text(rendered[0])
        self.assertIn("Az ULD adatok nem olvashatók", text)
        self.assertIn("E_COMM nem elérhető", text)
        self.assertNotIn("betöltése folyamatban", text)
        thread.assert_not_called()

    def test_failed_first_read_retries_after_backoff_even_on_unchanged_poll(self):
        state = {
            "status": "ready", "uld_initial_load_complete": False, "data_version": 5,
            "uld_last_refresh": None, "uld_refreshing": False,
            "uld_read_error": "E_COMM nem elérhető",
            "uld_read_error_at": datetime.now() - timedelta(seconds=90),
        }
        rendered, thread = self._render(state, "poll", 5)

        self.assertEqual(rendered, (no_update,) * 7)
        thread.assert_called_once()

    def test_cold_start_skeleton_not_rebuilt_on_unchanged_poll(self):
        state = {"status": "loading", "uld_initial_load_complete": False, "data_version": 3,
                 "uld_last_refresh": None, "uld_refreshing": True}
        rendered, _thread = self._render(state, "poll", 3)
        self.assertEqual(rendered, (no_update,) * 7)

        rendered, _thread = self._render(state, "poll", 2)
        self.assertIn("betöltése folyamatban", _text(rendered[0]))


if __name__ == "__main__":
    unittest.main()
