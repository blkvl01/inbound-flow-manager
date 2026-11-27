"""Nyitó oldal 2026-11-27: AWB copy list, card strip structure, elapsed-time
copy, accented priority labels, same-value store writes and the poll gate."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from dash import html, no_update

import app
import data_cache
import priority_engine


def _ctx(trigger, prop="data"):
    return SimpleNamespace(triggered_prop_ids={f"{trigger}.{prop}": trigger}, triggered_id=trigger)


def _walk(component):
    if component is None:
        return
    if isinstance(component, (list, tuple)):
        for child in component:
            yield from _walk(child)
        return
    if isinstance(component, (str, int, float)):
        return
    yield component
    yield from _walk(getattr(component, "children", None))


def _card_row(**overrides):
    row = {
        "priority_color": "standard",
        "awb": "317-10434675",
        "cargo_type": "PLT",
        "rank": 1,
        "priority_label": "NINCS ITT / PIHENŐN - KEVÉS KIADHATÓ",
        "glabs_id": "",
        "glabs_total": 0,
        "glabs_shippable": 0,
        "rendszam": "",
        "lmp": "MEEST UA",
        "boxes": 73,
        "weight": 1443,
        "am_time": datetime.now() - timedelta(hours=5, minutes=3),
        "in_bud_pallets": False,
        "is_aged": True,
        "hours_since_am": 5,
    }
    row.update(overrides)
    return row


class ActiveAwbLinesTest(unittest.TestCase):
    def test_active_only_rank_order_dedup_and_blank_skip(self):
        df = pd.DataFrame([
            {"awb": "B-2", "rank": 2, "is_stored": False},
            {"awb": "A-1", "rank": 1, "is_stored": False},
            {"awb": "S-9", "rank": 3, "is_stored": True},
            {"awb": " A-1 ", "rank": 4, "is_stored": False},
            {"awb": None, "rank": 5, "is_stored": False},
            {"awb": "nan", "rank": 6, "is_stored": False},
            {"awb": "C-7", "rank": 7, "is_stored": False},
        ])
        self.assertEqual(app._active_awb_lines(df), ["A-1", "B-2", "C-7"])

    def test_empty_inputs(self):
        self.assertEqual(app._active_awb_lines(None), [])
        self.assertEqual(app._active_awb_lines(pd.DataFrame()), [])

    def test_summary_body_carries_full_list_beyond_top10(self):
        rows = [{
            "awb": f"AWB-{i:03d}", "rank": i, "is_stored": False, "priority_color": "standard",
            "cargo_type": "PLT", "priority_label": "X", "lmp": "L", "weight": 1,
            "am_time": datetime.now(),
        } for i in range(1, 16)]
        body = app._build_summary_table(pd.DataFrame(rows))
        holders = [c for c in _walk(body) if "summary-awb-data" in (getattr(c, "className", "") or "")]
        self.assertEqual(len(holders), 1)
        data = getattr(holders[0], "data-awbs")
        self.assertEqual(data.split("\n"), [f"AWB-{i:03d}" for i in range(1, 16)])
        self.assertEqual(getattr(holders[0], "data-count"), "15")

    def test_summary_empty_state_has_empty_list(self):
        body = app._build_summary_table(None)
        holders = [c for c in _walk(body) if "summary-awb-data" in (getattr(c, "className", "") or "")]
        self.assertEqual(getattr(holders[0], "data-awbs"), "")


class CardStripTest(unittest.TestCase):
    def test_badges_grouped_and_no_empty_placeholders(self):
        card = app._make_card(_card_row(), test_mode=True)
        strip = next(c for c in _walk(card) if getattr(c, "className", "") == "card-strip")
        children = [c for c in strip.children if c is not None]
        classes = [c.className for c in children]
        self.assertEqual(classes, ["rank-num", "priority-label-text", "card-strip-badges"])
        badge_classes = [b.className for b in children[2].children]
        self.assertTrue(any("badge-aged" in c for c in badge_classes))
        self.assertTrue(any("badge-cargo" in c for c in badge_classes))
        # No childless, classless html.Span() fillers anywhere in the card.
        empties = [c for c in _walk(card)
                   if isinstance(c, html.Span) and not getattr(c, "className", None)
                   and getattr(c, "children", None) in (None, "", [])]
        self.assertEqual(empties, [])

    def test_en_route_card_has_no_rank_or_reason(self):
        card = app._make_card(_card_row(is_en_route=True, is_aged=False), test_mode=True)
        strip = next(c for c in _walk(card) if getattr(c, "className", "") == "card-strip")
        classes = [c.className for c in strip.children if c is not None]
        self.assertEqual(classes, ["card-strip-badges"])

    def test_elapsed_time_has_no_duplicate_label(self):
        self.assertEqual(app._fmt_since(datetime.now() - timedelta(hours=1, minutes=10, seconds=5)),
                         "1 óra 10 perce")
        self.assertEqual(app._fmt_since(datetime.now() - timedelta(hours=2, seconds=5)), "2 órája")
        self.assertEqual(app._fmt_since(datetime.now() - timedelta(minutes=7, seconds=5)), "7 perce")
        self.assertEqual(app._fmt_since(None), "")


class PriorityLabelTest(unittest.TestCase):
    def test_labels_are_accented_hungarian(self):
        labels = [meta[0] for meta in priority_engine._GROUP_META.values()]
        joined = " ".join(labels)
        for ascii_word in ("PIHENON", "KIADHATO", "SOFOR", "HELYSZINEN", "PRIORITAS", "UTON"):
            self.assertNotIn(ascii_word, joined.replace("Ő", "").replace("Ó", ""))
        self.assertIn("NINCS ITT / PIHENŐN - KEVÉS KIADHATÓ", labels)
        self.assertIn("ÚTON", labels)


class SameValueStoreWriteTest(unittest.TestCase):
    def test_filter_click_does_not_rewrite_unchanged_version_or_ts(self):
        ts = datetime(2026, 11, 27, 9, 0)
        state = {"data_version": 12, "last_refresh": ts, "status": "ready", "df": None}
        with mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "ctx", _ctx("active-filter")):
            result = app.update_dashboard(1, "inbound", None, None, None, False, "driver", 1, "",
                                          "ops", 12, ts.isoformat())
        self.assertIs(result[2], no_update)
        self.assertIs(result[3], no_update)

    def test_changed_version_is_still_written(self):
        ts = datetime(2026, 11, 27, 9, 0)
        state = {"data_version": 13, "last_refresh": ts, "status": "ready", "df": None}
        with mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "ctx", _ctx("active-filter")):
            result = app.update_dashboard(1, "inbound", None, None, None, False, "driver", 1, "",
                                          "ops", 12, "2026-11-27T08:00:00")
        self.assertEqual(result[3], 13)
        self.assertEqual(result[2], ts.isoformat())


class PollGateTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(data_cache._state)

    def tearDown(self):
        data_cache._state.clear()
        data_cache._state.update(self._saved)

    def test_signature_follows_version_and_refresh_state(self):
        data_cache._state.update({"data_version": 5, "status": "ready", "refreshing": False,
                                  "uld_refreshing": False, "refresh_count": 1, "df": None,
                                  "errors": {}})
        first = app._poll_gate_signature()
        self.assertEqual(first, app._poll_gate_signature())
        data_cache._state["refreshing"] = True
        refreshing = app._poll_gate_signature()
        self.assertNotEqual(first, refreshing)
        data_cache._state.update({"refreshing": False, "data_version": 6})
        self.assertNotEqual(first, app._poll_gate_signature())

    def test_gate_endpoint_returns_signature(self):
        data_cache._state.update({"data_version": 7, "status": "ready", "df": None, "errors": {}})
        client = app.server.test_client()
        response = client.get("/api/poll-gate")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["sig"].startswith("7|ready|"))
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_layout_poll_interval_never_ticks_itself(self):
        found = [c for c in _walk(app.app.layout) if getattr(c, "id", None) == "poll"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].disabled)


if __name__ == "__main__":
    unittest.main()
