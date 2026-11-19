"""Wave 2 / P5: data_version bumps only on rendered changes, ULD active-view
signature guard, TV-mode poll gating and the cached /api/tv/ops payload."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from dash import no_update

import app
import data_cache


def _poll_ctx(trigger="poll", prop="n_intervals"):
    return SimpleNamespace(triggered_prop_ids={f"{trigger}.{prop}": trigger}, triggered_id=trigger)


class DataVersionBumpTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(data_cache._state)

    def tearDown(self):
        data_cache._state.clear()
        data_cache._state.update(self._saved)

    def test_manual_refresh_start_does_not_bump(self):
        data_cache._state.update({"status": "ready", "data_version": 10, "refreshing": False})
        data_cache.mark_refresh_started()
        self.assertTrue(data_cache._state["refreshing"])
        self.assertEqual(data_cache._state["data_version"], 10)

    def test_successful_uld_refresh_bumps_exactly_once(self):
        data_cache._state.update({
            "data_version": 20, "uld_refreshing": False, "uld_data": [],
            "uld_source_signature": None, "uld_read_error_at": None, "status": "ready",
            "refresh_count": 1,
        })
        with mock.patch.object(data_cache, "load_uld_data_fast",
                               return_value=([{"uld_number": "PMC00001LH"}], {})), \
             mock.patch.object(data_cache, "_save_current_cache_snapshot"), \
             mock.patch.object(data_cache, "trigger_refresh"):
            self.assertTrue(data_cache.refresh_uld_data(force=True))
        self.assertEqual(data_cache._state["data_version"], 21)

    def test_failed_uld_refresh_still_bumps_for_error_panel(self):
        data_cache._state.update({
            "data_version": 30, "uld_refreshing": False, "uld_data": [],
            "uld_source_signature": None, "uld_read_error_at": None, "status": "ready",
            "refresh_count": 1,
        })
        with mock.patch.object(data_cache, "load_uld_data_fast",
                               return_value=([], {"error": "zárolt fájl"})):
            self.assertFalse(data_cache.refresh_uld_data(force=True))
        self.assertEqual(data_cache._state["data_version"], 31)
        self.assertEqual(data_cache._state["uld_read_error"], "zárolt fájl")

    def test_repeated_stall_bumps_only_on_transition(self):
        df = pd.DataFrame([{"awb": "1"}])
        data_cache._state.update({
            "df": df, "status": "ready", "errors": {}, "data_version": 40,
            "read_stalled": False, "read_stall_since": None,
        })
        data_cache._mark_read_stalled("időtúllépés", first=False)
        self.assertEqual(data_cache._state["data_version"], 41)
        # The 60 s retry hits the same stall: metadata only, no rerender.
        data_cache._mark_read_stalled("időtúllépés", first=False)
        data_cache._mark_read_stalled("másik ok", first=False)
        self.assertEqual(data_cache._state["data_version"], 41)
        self.assertEqual(data_cache._state["read_stall_reason"], "másik ok")

    def test_cold_start_stall_error_text_change_bumps(self):
        data_cache._state.update({
            "df": None, "outbound_cards": [], "uld_data": [], "status": "loading",
            "errors": {}, "data_version": 50, "read_stalled": False, "read_stall_since": None,
        })
        data_cache._mark_read_stalled("A", first=True)
        self.assertEqual(data_cache._state["status"], "error")
        self.assertEqual(data_cache._state["data_version"], 51)
        data_cache._mark_read_stalled("A", first=True)
        self.assertEqual(data_cache._state["data_version"], 51)
        data_cache._mark_read_stalled("B", first=True)  # rendered error text changed
        self.assertEqual(data_cache._state["data_version"], 52)

    def test_notes_and_stored_flags_still_bump(self):
        data_cache._state.update({"data_version": 60, "df": pd.DataFrame()})
        data_cache.bump_data_version()
        self.assertEqual(data_cache._state["data_version"], 61)
        data_cache.update_stored_flags(set())
        self.assertEqual(data_cache._state["data_version"], 62)


class UldActiveSignatureTest(unittest.TestCase):
    RECORDS = [{"uld_number": "PMC00001LH", "awbs": ["111"], "gha": "MNZ",
                "status": "ok", "am_time": datetime(2026, 11, 19, 8, 0)}]
    STACKS = [{"id": "s1", "name": "Stack 1", "ulds": ["PMC00001LH"]}]

    def _sig(self, records=None, stacks=None, state=None, **params):
        args = {"status_filter": "all", "prefix_filter": "", "gha_filter": "",
                "search_text": "", "page": 1}
        args.update(params)
        with mock.patch.object(app.uld_stack_manager, "get_uld_overrides", return_value={}):
            return app._uld_active_signature(
                state or {"uld_last_refresh": datetime(2026, 11, 19, 9)},
                self.STACKS if stacks is None else stacks,
                self.RECORDS if records is None else records,
                args["status_filter"], args["prefix_filter"], args["gha_filter"],
                args["search_text"], args["page"],
            )

    def test_signature_is_stable_and_prefixed(self):
        self.assertEqual(self._sig(), self._sig())
        self.assertTrue(self._sig().startswith("active:"))

    def test_signature_changes_with_every_rendered_input(self):
        base = self._sig()
        changed_record = [dict(self.RECORDS[0], status="expired")]
        self.assertNotEqual(base, self._sig(records=changed_record))
        self.assertNotEqual(base, self._sig(stacks=[dict(self.STACKS[0], name="Másik")]))
        self.assertNotEqual(base, self._sig(page=2))
        self.assertNotEqual(base, self._sig(search_text="PMC"))
        self.assertNotEqual(base, self._sig(gha_filter="MNZ"))
        self.assertNotEqual(base, self._sig(state={"uld_last_refresh": None}))
        times = {"uld_last_refresh": datetime(2026, 11, 19, 9),
                 "uld_times_full": {"PMC00001LH": {"am_time": "2026-11-19T07:00"}}}
        self.assertNotEqual(base, self._sig(state=times))

    def _render(self, records, current_version, current_sig, trigger="poll"):
        prop = "n_intervals" if trigger == "poll" else "data"
        state = {"status": "ready", "uld_initial_load_complete": True, "data_version": 9,
                 "uld_last_refresh": datetime(2026, 11, 19, 9), "uld_refreshing": False}
        with mock.patch.object(app, "ctx", _poll_ctx(trigger, prop)), \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_cached", return_value=self.STACKS), \
             mock.patch.object(app.uld_stack_manager, "get_uld_overrides", return_value={}), \
             mock.patch.object(app, "_active_uld_records", return_value=records), \
             mock.patch.object(app, "_make_uld_list_section", return_value=["lista"]) as make_list, \
             mock.patch.object(app, "_make_stacks_section", return_value=["stackek"]):
            result = app.render_uld_view(0, "all", 1, "uld", "active", "", "", "", 1, None,
                                         current_version, current_sig)
        return result, make_list

    def test_version_bump_without_uld_change_keeps_dom(self):
        first, make_list = self._render(self.RECORDS, 8, None)
        self.assertEqual(first[0], ["lista"])
        sig = first[6]
        self.assertTrue(str(sig).startswith("active:"))

        # Global bump (e.g. notes / inbound refresh): same ULD content -> no_update.
        second, make_list = self._render(self.RECORDS, 8, sig)
        self.assertEqual(second, (no_update,) * 7)
        make_list.assert_not_called()

    def test_real_uld_change_still_renders(self):
        first, _ = self._render(self.RECORDS, 8, None)
        changed = [dict(self.RECORDS[0], awbs=["111", "222"])]
        second, make_list = self._render(changed, 8, first[6])
        self.assertEqual(second[0], ["lista"])
        self.assertNotEqual(second[6], first[6])
        make_list.assert_called_once()

    def test_user_trigger_renders_even_with_same_signature(self):
        first, _ = self._render(self.RECORDS, 8, None)
        again, make_list = self._render(self.RECORDS, 9, first[6], trigger="uld-filter-store")
        self.assertEqual(again[0], ["lista"])
        make_list.assert_called_once()


class TvModePollGateTest(unittest.TestCase):
    def test_tv_poll_freezes_dashboard_and_version_store(self):
        state = {"data_version": 12, "last_refresh": datetime(2026, 11, 19, 12)}
        with mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "ctx", _poll_ctx()):
            result = app.update_dashboard(1, "inbound", None, None, None, False, "all", 1, "", "tv", 8)
        self.assertEqual(result, (no_update,) * 4)

    def test_kpi_view_poll_still_advances_version_store(self):
        state = {"data_version": 12, "last_refresh": datetime(2026, 11, 19, 12)}
        with mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "ctx", _poll_ctx()):
            result = app.update_dashboard(1, "inbound", None, None, None, False, "all", 1, "", "kpi", 8)
        self.assertEqual(result[3], 12)

    def test_heavy_poll_callbacks_idle_in_tv_mode(self):
        state = {"data_version": 12, "kpi": {}, "df": None}
        with mock.patch.object(app.data_cache, "get_state", return_value=state) as get_state, \
             mock.patch.object(app, "ctx", _poll_ctx()):
            self.assertEqual(app.update_stat_counts(1, None, "inbound", False, "tv", 8), (no_update,) * 12)
            self.assertEqual(app.update_kpi_cards(1, None, "inbound", "inbound", "tv", 8), (no_update,) * 39)
            self.assertEqual(app.update_header_saturation(1, None, "tv", 8), (no_update,) * 3)
            self.assertIs(app.update_bec_prefixes(1, None, "tv", 8), no_update)
        get_state.assert_not_called()

    def test_leaving_tv_rerenders_header_callbacks(self):
        state = {"data_version": 12, "kpi": {"bec_prefixes": {"123": 1}}}
        ctx = SimpleNamespace(triggered_prop_ids={"view-mode.data": "view-mode"}, triggered_id="view-mode")
        with mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "ctx", ctx):
            # Store already equals state (set by update_dashboard on the same exit).
            self.assertEqual(app.update_bec_prefixes(1, None, "ops", 12), {"123": 1})
            sat = app.update_header_saturation(1, None, "ops", 12)
        self.assertIsNot(sat[0], no_update)


class TvOpsCacheTest(unittest.TestCase):
    def setUp(self):
        app._TV_OPS_CACHE.update({"version": None, "at": 0.0, "payload": None})

    def tearDown(self):
        app._TV_OPS_CACHE.update({"version": None, "at": 0.0, "payload": None})

    def test_same_version_reuses_payload_with_fresh_freshness(self):
        last_ok = datetime.now() - timedelta(minutes=3)
        state = {"data_version": 5, "last_success_refresh": last_ok, "read_stalled": False}
        built = {"status": "ready", "trucks": [], "freshness": {"stalled": False}}
        with mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "_build_tv_ops_payload", return_value=built) as build:
            app._cached_tv_ops_payload()
            state["read_stalled"] = True  # stall flag changes without a version bump
            second = app._cached_tv_ops_payload()
        build.assert_called_once()
        self.assertTrue(second["freshness"]["stalled"])
        self.assertGreaterEqual(second["freshness"]["age_seconds"], 170)
        self.assertFalse(built["freshness"]["stalled"])  # cached object not mutated

    def test_version_change_rebuilds(self):
        state = {"data_version": 5}
        with mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "_build_tv_ops_payload", return_value={"status": "ready"}) as build:
            app._cached_tv_ops_payload()
            state["data_version"] = 6
            app._cached_tv_ops_payload()
        self.assertEqual(build.call_count, 2)

    def test_ttl_expiry_rebuilds(self):
        state = {"data_version": 5}
        with mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "_build_tv_ops_payload", return_value={"status": "ready"}) as build:
            app._cached_tv_ops_payload()
            app._TV_OPS_CACHE["at"] -= app._TV_OPS_CACHE_TTL_S + 1
            app._cached_tv_ops_payload()
        self.assertEqual(build.call_count, 2)

    def test_payload_uses_only_needed_columns(self):
        df = pd.DataFrame([{
            "awb": "111", "lmp": "X", "weight": 10.0, "waiting_hours": 2.0, "is_aged": False,
            "driver_checkin": None, "driver_in_rest": False, "glabs_shippable": 1,
            "glabs_total": 2, "cargo_type": "ULD", "priority_rank": 1,
            "is_stored": False, "is_en_route": False, "unused_wide_column": "x" * 50,
        }, {
            "awb": "222", "lmp": "Y", "weight": 5.0, "waiting_hours": 1.0, "is_aged": False,
            "driver_checkin": None, "driver_in_rest": False, "glabs_shippable": 0,
            "glabs_total": 1, "cargo_type": "PLT", "priority_rank": 2,
            "is_stored": True, "is_en_route": False, "unused_wide_column": "y",
        }])
        state = {"df": df, "kpi": {}, "outbound_cards": [], "status": "ready"}
        with mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._build_tv_ops_payload()
        self.assertEqual(payload["stored_today"], 1)
        self.assertEqual([i["awb"] for i in payload["active_items"]], ["111"])
        self.assertEqual(payload["active_items"][0]["cargo_type"], "ULD")
        self.assertEqual(list(df.columns)[-1], "unused_wide_column")  # source df untouched


if __name__ == "__main__":
    unittest.main()
