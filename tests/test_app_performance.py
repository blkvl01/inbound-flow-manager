from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dash import no_update
import app


class AppPerformanceTests(unittest.TestCase):
    def test_pages_preserve_complete_global_order_and_filter_before_slicing(self):
        entries = [(dict(awb=str(i), is_shippable=i >= 40), False, True) for i in range(60)]
        reconstructed = []
        for page in range(1, 6):
            rows, total, current = app._page_card_entries(entries, "all", page=page)
            self.assertEqual(total, 60)
            self.assertEqual(current, page)
            self.assertLessEqual(len(rows), 12)
            reconstructed.extend(row[0]["awb"] for row in rows)
        self.assertEqual(reconstructed, [str(i) for i in range(60)])
        rows, total, current = app._page_card_entries(entries, "shippable", page=1)
        self.assertEqual(total, 20)
        self.assertEqual([row[0]["awb"] for row in rows], [str(i) for i in range(40, 52)])
        rows, total, current = app._page_card_entries(entries, "shippable", page=999)
        self.assertEqual(current, 2)
        self.assertEqual([row[0]["awb"] for row in rows], [str(i) for i in range(52, 60)])

    def test_stored_snapshot_access_and_plate_turn_focus_work_across_pages(self):
        time = datetime(2026, 10, 2, 12, 0)
        entries = [(dict(awb=str(i), rendszam="same 123", am_time=time), False, True) for i in range(20)]
        entries += [(dict(awb="stored", rendszam="same 123", am_time=time), True, True),
                    (dict(awb="snapshot", rendszam="same 123", am_time=time), True, False)]
        entries += [(dict(awb="next-turn", rendszam="same 123", am_time=datetime(2026, 10, 2, 13)), False, True)]
        rows, total, _ = app._page_card_entries(entries, "betarolt", "SAME 123|2026-10-02T12:00")
        self.assertEqual(total, 2)
        self.assertEqual([row[0]["awb"] for row in rows], ["stored", "snapshot"])
        self.assertEqual([row[2] for row in rows], [True, False])
        _, total, _ = app._page_card_entries(entries, "all", "SAME 123|2026-10-02T12:00")
        self.assertEqual(total, 20)
        _, total, _ = app._page_card_entries(entries, "all", "same 123")
        self.assertEqual(total, 21)

    def test_loading_fast_timer_stops_idle_and_wakes_during_real_loading(self):
        state = {"status": "ready", "refresh_count": 1, "refreshing": False,
                 "load_progress": 99, "load_stage": "Kész", "load_detail": "Kész"}
        with patch.object(app.data_cache, "get_state", return_value=state), patch.object(app.updater, "get_status", return_value={"phase": "idle"}):
            idle = app.update_loading_state(0, 0, None, False, "done", None)
            self.assertTrue(idle[8])
            self.assertFalse(idle[9])
            self.assertEqual(app.update_loading_state(1, 1, None, False, "done", idle[10]), (no_update,) * 11)
            state["refreshing"] = True
            loading = app.update_loading_state(1, 1, 1, False, "done", idle[10])
            self.assertFalse(loading[8])
            self.assertTrue(loading[9])
            self.assertEqual(loading[1], {"display": "flex"})
            state["refreshing"] = False
            done = app.update_loading_state(2, 1, 1, False, "refresh", loading[10])
            self.assertEqual(done[1], {"display": "none"})
            self.assertTrue(done[8])
            self.assertFalse(done[9])

    def test_updater_fast_poll_does_not_block_ready_operational_screen(self):
        with patch.object(app.data_cache, "get_state", return_value={"status": "ready", "refresh_count": 1}), patch.object(app.updater, "get_status", return_value={"phase": "downloading", "progress": 42}):
            result = app.update_loading_state(0, 0, None, False, "done", None)
            self.assertFalse(result[8])
            self.assertFalse(result[9])
            self.assertEqual(result[1], {"display": "none"})

    def test_uld_leaving_view_unmounts_once_and_returning_forces_fresh_dom(self):
        args = (0, "all", 0, "uld", "active", "", "", "", 1, 0)
        hidden = app.render_uld_view_callback(*args, "kpi", 7, "old", True)
        self.assertEqual(hidden[:6], ([], [], [], [], None, None))
        self.assertFalse(hidden[7])
        self.assertEqual(app.render_uld_view_callback(*args, "kpi", 7, "old", False), (no_update,) * 8)
        with patch.object(app, "render_uld_view", return_value=([], [], [], [], None, None, no_update)) as render:
            result = app.render_uld_view_callback(*args, "ops", 7, "old", False)
        self.assertTrue(result[7])
        self.assertEqual(render.call_args.args[-2:], (None, None))

    def test_hidden_dashboard_updates_version_without_building_cards(self):
        state = {"data_version": 9, "last_refresh": datetime(2026, 10, 2, 12)}
        fake_ctx = SimpleNamespace(triggered_prop_ids={"poll.n_intervals": "poll"}, triggered_id="poll")
        with patch.object(app.data_cache, "get_state", return_value=state), patch.object(app, "ctx", fake_ctx), patch.object(app, "_make_card") as make:
            result = app.update_dashboard(1, "uld", None, None, None, False, "all", 1, "", "ops", 8)
        self.assertIs(result[0], no_update)
        self.assertEqual(result[3], 9)
        make.assert_not_called()


if __name__ == "__main__":
    unittest.main()
