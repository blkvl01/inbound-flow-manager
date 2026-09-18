import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import app
import data_reader
import pandas as pd


def _component_text(component):
    if component is None:
        return ""
    if isinstance(component, (str, int, float)):
        return str(component)
    if isinstance(component, (list, tuple)):
        return " ".join(_component_text(child) for child in component)
    return _component_text(getattr(component, "children", None))


def _find_by_class(component, class_part):
    found = []
    if component is None:
        return found
    if isinstance(component, (list, tuple)):
        for child in component:
            found.extend(_find_by_class(child, class_part))
        return found
    class_name = getattr(component, "className", "") or ""
    if class_part in class_name:
        found.append(component)
    found.extend(_find_by_class(getattr(component, "children", None), class_part))
    return found


class UldApiHelperTests(unittest.TestCase):
    @staticmethod
    def _excel_serial(dt):
        epoch = datetime(1899, 12, 30)
        return (dt - epoch).total_seconds() / 86400

    def test_uld_expiry_stays_visible_until_60h_active_cutoff(self):
        now = datetime.now()
        raw = pd.DataFrame([
            {
                "uld_raw": "PMC20257YAA",
                "am_raw": self._excel_serial(now - timedelta(hours=49)),
                "ad_raw": None,
                "awb": "111-00000001",
                "lmp": "LMP",
                "carrier_k": "Celebi",
            },
            {
                "uld_raw": "AKE20258YAA",
                "am_raw": self._excel_serial(now - timedelta(hours=61)),
                "ad_raw": None,
                "awb": "111-00000002",
                "lmp": "LMP",
                "carrier_k": "Celebi",
            },
        ])

        records = data_reader._extract_uld_data(raw)
        by_uld = {rec["uld_number"]: rec for rec in records}

        self.assertIn("PMC20257YAA", by_uld)
        self.assertTrue(by_uld["PMC20257YAA"]["is_expired"])
        self.assertEqual(by_uld["PMC20257YAA"]["expiry_hours"], 48.0)
        self.assertGreaterEqual(by_uld["PMC20257YAA"]["elapsed_hours"], 48.0)
        self.assertNotIn("AKE20258YAA", by_uld)

    def test_inbound_uld_badges_use_plate_color(self):
        plate_color_map = app._make_id_color_map(["TRUCK123"])
        base_row = {
            "priority_color": "standard",
            "awb": "12345678901",
            "cargo_type": "ULD",
            "rank": 1,
            "priority_label": "Standard",
            "glabs_id": "",
            "glabs_total": 0,
            "glabs_shippable": 0,
            "rendszam": "TRUCK123",
            "lmp": "LMP",
            "boxes": 0,
            "weight": 100,
            "am_time": None,
            "in_bud_pallets": False,
        }

        first = app._make_card(
            dict(base_row, awb="12345678901", uld_number="PMC001"),
            plate_color_map=plate_color_map,
        )
        second = app._make_card(
            dict(base_row, awb="12345678902", uld_number="PMC002"),
            plate_color_map=plate_color_map,
        )

        first_badge = _find_by_class(first, "inbound-uld-note")[0]
        second_badge = _find_by_class(second, "inbound-uld-note")[0]

        self.assertEqual(first_badge.style["color"], plate_color_map["TRUCK123"])
        self.assertEqual(first_badge.style, second_badge.style)

    def test_en_route_b2b_card_keeps_red_priority_header(self):
        card = app._make_card({
            "priority_color": "b2b",
            "is_b2b_priority": True,
            "is_en_route": True,
            "awb": "12345678901",
            "cargo_type": "PLT",
            "rank": 1,
            "priority_label": "B2B - AZONNALI PRIORITÁS",
            "glabs_id": "",
            "glabs_total": 0,
            "glabs_shippable": 0,
            "rendszam": "",
            "lmp": "B2B",
            "boxes": 1,
            "weight": 100,
            "am_time": datetime.now(),
            "in_bud_pallets": False,
        })

        header = _find_by_class(card, "card-strip")[0]
        self.assertEqual(header.style["background"], "#ef4444")
        self.assertIn("#1", _component_text(header))
        self.assertIn("B2B - AZONNALI PRIORITÁS", _component_text(header))
        self.assertIn("pcard-b2b", card.className)

    def test_priority_test_view_uses_production_ranking_with_b2b_first(self):
        ranked = app._priority_test_df()

        self.assertEqual(len(ranked), 5)
        self.assertEqual(ranked.iloc[0]["awb"], "TESZT-B2B-001")
        self.assertEqual(ranked.iloc[0]["rank"], 1)
        self.assertEqual(ranked.iloc[0]["priority_color"], "b2b")
        self.assertEqual(ranked.iloc[0]["priority_label"], "B2B - AZONNALI PRIORITÁS")
        self.assertEqual(list(ranked["awb"]), [
            "TESZT-B2B-001",
            "TESZT-AT-HU-001",
            "TESZT-AGED-001",
            "TESZT-DRIVER-001",
            "TESZT-UTON-001",
        ])

        card = app._make_card(ranked.iloc[0].to_dict(), test_mode=True)
        self.assertFalse(_find_by_class(card, "badge-stored-empty"))
        self.assertFalse(_find_by_class(card, "card-notes-block"))

        sidebar = app._truck_sidebar_items(ranked.to_dict("records"), {}, allow_store=False)
        self.assertNotIn("Betárolva", _component_text(sidebar))
        self.assertFalse(_find_by_class(sidebar, "truck-store-btn"))

    def test_priority_test_shortcut_is_guarded_from_inputs_and_other_views(self):
        source = (Path(app.__file__).parent / "assets" / "priority_test_mode.js").read_text(encoding="utf-8")

        self.assertIn('toLowerCase() !== "t"', source)
        self.assertIn("isEditable(event.target)", source)
        self.assertIn('classList.contains("view-tv-mode")', source)
        self.assertIn('classList.contains("view-kpi-mode")', source)
        self.assertIn('set_props("priority-test-mode"', source)
        self.assertNotIn('#loading-test-btn', source)
        self.assertIn("publish(attempt + 1)", source)

    def test_first_loading_view_exposes_real_progress_without_test_entry(self):
        state = {
            "load_progress": 42,
            "load_stage": "E_COMM adatok beolvasása",
            "load_detail": "10 500 / 25 000 sor",
            "load_started_at": datetime.now() - timedelta(seconds=20),
        }

        children = app._loading_first_children(state)
        self.assertIn("42%", _component_text(children))
        self.assertIn("E_COMM adatok beolvasása", _component_text(children))
        self.assertNotIn("Tesztnézet", _component_text(children))
        self.assertNotIn("loading-test-btn", _component_text(children))
        progress = _find_by_class(children, "l-track")[0]
        self.assertEqual(progress.role, "progressbar")
        self.assertEqual(progress.to_plotly_json()["props"]["aria-valuenow"], "42")
        self.assertFalse(_find_by_class(children, "l-step-rail"))
        self.assertNotIn("hátralévő", _component_text(children).lower())

    def test_test_mode_hides_cold_start_overlay(self):
        with mock.patch.object(app.data_cache, "get_state", return_value={
            "status": "loading",
            "refresh_count": 0,
            "refreshing": False,
        }):
            _cls, style, state = app.update_overlay(0, None, None, True, "first")

        self.assertEqual(style, {"display": "none"})
        self.assertEqual(state, "test")

    def test_loading_progress_updates_without_replacing_the_panel(self):
        with mock.patch.object(app.data_cache, "get_state", return_value={
            "load_progress": 42,
            "load_stage": "E_COMM adatok beolvasása",
        }):
            stage, percent, fill_style, aria_value = app.update_loading_progress(1)
        self.assertEqual(stage, "E_COMM adatok beolvasása")
        self.assertEqual(percent, "42%")
        self.assertEqual(fill_style, {"width": "42%"})
        self.assertEqual(aria_value, "42")

    def test_truck_sidebar_groups_by_plate_and_repeats_uld_prefixes(self):
        rows = [
            {"awb": "111", "cargo_type": "ULD", "rendszam": "TRUCK123", "uld_number": "PMC001",
             "am_time": datetime(2026, 6, 15, 8, 30)},
            {"awb": "222", "cargo_type": "ULD", "rendszam": "TRUCK123", "uld_number": "PMC002",
             "am_time": datetime(2026, 6, 15, 8, 30)},
            {"awb": "333", "cargo_type": "ULD", "rendszam": "TRUCK999", "uld_number": "PMD003",
             "am_time": datetime(2026, 6, 15, 9, 0)},
            {"awb": "444", "cargo_type": "PLT", "rendszam": "TRUCK123", "uld_number": "",
             "am_time": datetime(2026, 6, 15, 8, 30)},
        ]
        plate_color_map = app._make_id_color_map(["TRUCK123", "TRUCK999"])

        sidebar = app._truck_sidebar_items(rows, plate_color_map)
        text = _component_text(sidebar)
        truck_cards = _find_by_class(sidebar, "truck-sidebar-card")
        comp_chips = [c.children for c in _find_by_class(sidebar, "truck-comp-chip")]

        self.assertIn("Kamionok", text)
        self.assertIn("TRUCK123", text)
        self.assertIn("2 ULD", text)
        self.assertIn("3 tétel", text)
        self.assertIn("Felvéve 06.15 08:30", text)
        self.assertIn("2 ULD", comp_chips)
        self.assertIn("PLT", comp_chips)
        self.assertIn("1 ULD", comp_chips)
        self.assertTrue(_find_by_class(sidebar, "truck-sidebar-facts"))
        self.assertEqual(_component_text(_find_by_class(sidebar, "truck-time-label")[0]), "Felvéve")
        self.assertEqual(_component_text(_find_by_class(sidebar, "truck-time-value")[0]), "06.15 08:30")
        props = truck_cards[0].to_plotly_json()["props"]
        self.assertEqual(props["data-plate-key"], "TRUCK123")
        self.assertEqual(props["data-truck-turn-key"], "TRUCK123|2026-06-15T08:30")
        self.assertEqual(props["style"]["--truck-color"], plate_color_map["TRUCK123"])
        dot = _find_by_class(truck_cards[0], "truck-sidebar-dot")[0]
        self.assertEqual(dot.style["color"], plate_color_map["TRUCK123"])

    def test_truck_sidebar_splits_same_plate_by_pickup_time(self):
        rows = [
            {"awb": "111", "cargo_type": "ULD", "rendszam": "TRUCK123", "uld_number": "PMC001",
             "am_time": datetime(2026, 6, 15, 8, 30), "is_shippable": True},
            {"awb": "222", "cargo_type": "ULD", "rendszam": "TRUCK123", "uld_number": "PMC002",
             "am_time": datetime(2026, 6, 15, 11, 10), "is_shippable": True},
        ]
        plate_color_map = app._make_id_color_map(["TRUCK123"])

        groups = app._truck_groups(rows, plate_color_map)

        self.assertEqual(len(groups), 2)
        self.assertEqual(
            sorted(g["turn_key"] for g in groups),
            ["TRUCK123|2026-06-15T08:30", "TRUCK123|2026-06-15T11:10"],
        )
        self.assertEqual({g["color"] for g in groups}, {plate_color_map["TRUCK123"]})

    def test_parse_uld_numbers_preserves_11_character_yaa_codes(self):
        self.assertEqual(data_reader._parse_uld_numbers("PMC20257YAA"), ["PMC20257YAA"])
        self.assertEqual(data_reader._parse_uld_numbers("PMC20257YAA-69"), ["PMC20257YAA"])

    def test_parse_uld_numbers_keeps_10_character_codes_without_dash_suffix(self):
        self.assertEqual(
            data_reader._parse_uld_numbers("PMD34212MB-69,(05.12)PMD34353MB-61 (05.12)"),
            ["PMD34212MB", "PMD34353MB"],
        )

    def test_parse_uld_numbers_normalizes_cyrillic_o_from_excel(self):
        self.assertEqual(data_reader._parse_uld_numbers("PAG18804\u041e3"), ["PAG18804O3"])

    def test_uld_gha_is_canonicalized_and_backfilled_from_exact_awb(self):
        now = datetime.now()
        raw = pd.DataFrame([
            {
                "uld_raw": "PMC12345LH", "am_raw": self._excel_serial(now - timedelta(hours=2)),
                "ad_raw": None, "awb": "111-00000001", "lmp": "LMP", "carrier_k": "",
            },
            {
                "uld_raw": None, "am_raw": None, "ad_raw": None,
                "awb": "111-00000001", "lmp": "LMP", "carrier_k": "MENZIES Kft.",
            },
        ])

        records, _meta = data_reader._fast_uld_data_from_raw(raw)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["gha"], "Menzies")
        self.assertEqual(records[0]["gha_source"], "awb")
        self.assertFalse(records[0]["gha_conflict"])

    def test_uld_gha_conflict_is_flagged_instead_of_silently_picking_first(self):
        now = datetime.now()
        raw = pd.DataFrame([
            {
                "uld_raw": "PMC12345LH", "am_raw": self._excel_serial(now - timedelta(hours=2)),
                "ad_raw": None, "awb": "111-00000001", "lmp": "LMP", "carrier_k": "Celebi",
            },
            {
                "uld_raw": None, "am_raw": None, "ad_raw": None,
                "awb": "111-00000001", "lmp": "LMP", "carrier_k": "AS Cargo",
            },
        ])

        records, _meta = data_reader._fast_uld_data_from_raw(raw)

        self.assertEqual(records[0]["gha"], "")
        self.assertTrue(records[0]["gha_conflict"])
        self.assertEqual(records[0]["gha_candidates"], ["AS Cargo", "Celebi"])

    def test_returned_uld_older_cycle_does_not_leak_into_reused_open_cycle(self):
        now = datetime.now()
        raw = pd.DataFrame([
            {
                "uld_raw": "PMC12345LH", "am_raw": self._excel_serial(now - timedelta(hours=80)),
                "ad_raw": None, "awb": "OLD", "lmp": "OLD", "carrier_k": "Celebi",
            },
            {
                "uld_raw": "PMC12345LH", "am_raw": self._excel_serial(now - timedelta(hours=80)),
                "ad_raw": self._excel_serial(now - timedelta(hours=70)), "awb": "OLD", "lmp": "OLD", "carrier_k": "Celebi",
            },
            {
                "uld_raw": "PMC12345LH", "am_raw": self._excel_serial(now - timedelta(hours=3)),
                "ad_raw": None, "awb": "NEW", "lmp": "NEW", "carrier_k": "Menzies",
            },
        ])

        records, meta = data_reader._fast_uld_data_from_raw(raw)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["awbs"], ["NEW"])
        self.assertEqual(records[0]["gha"], "Menzies")
        self.assertNotIn("PMC12345LH", meta["uld_returned"])

    def test_blank_gha_placeholder_is_not_serialized_as_drag_identity(self):
        uld = {
            "uld_number": "PMC12345LH", "uld_type": "PMC", "awbs": ["111"],
            "gha": "", "gha_conflict": False, "status": "ok", "remaining_hours": 10,
            "expiry_time": datetime.now().isoformat(), "is_expired": False,
        }

        row = app._make_uld_row(uld, [])
        props = row.to_plotly_json()["props"]

        self.assertEqual(props["data-gha"], "")
        self.assertIn("Nincs GHA adat", _component_text(row))

    def test_stack_membership_dirty_signal_forces_full_uld_list_render(self):
        sentinel_list = object()
        state = {"data_version": 7, "uld_refreshing": False}
        all_uld = [{"uld_number": "PMC001"}]
        stacks = [{"id": "s1", "name": "Menzies 1", "ulds": ["PMC001"]}]
        fake_ctx = SimpleNamespace(
            triggered_prop_ids={
                "uld-trigger-store.data": "uld-trigger-store",
                "uld-list-dirty-store.data": "uld-list-dirty-store",
            },
            triggered_id="uld-trigger-store",
        )

        with mock.patch.object(app, "ctx", fake_ctx), \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_cached", return_value=stacks), \
             mock.patch.object(app, "_active_uld_records", return_value=all_uld), \
             mock.patch.object(app, "_active_stacks", return_value=stacks), \
             mock.patch.object(app, "_filter_uld_for_view", return_value=(all_uld, False)), \
             mock.patch.object(app, "_compute_gha_color_map", return_value={}), \
             mock.patch.object(app, "_make_uld_list_section", return_value=sentinel_list), \
             mock.patch.object(app, "_make_stacks_section", return_value=[]), \
             mock.patch.object(app, "_make_uld_stats", return_value=[]), \
             mock.patch.object(app, "_make_filter_chips", return_value=[]), \
             mock.patch.object(app, "_make_pagination", return_value=None):
            rendered = app.render_uld_view(
                0, "all", 1, "uld", "active", "", "", "", 1, 1, 7, ""
            )

        self.assertIs(rendered[0], sentinel_list)

    def test_uld_membership_flows_mark_the_list_dirty_and_have_hungarian_feedback(self):
        source = (Path(app.__file__).parent / "assets" / "uld_manager.js").read_text(encoding="utf-8")

        self.assertIn("if (opts.listDirty === true)", source)
        self.assertIn("setStore('uld-list-dirty-store', Date.now())", source)
        self.assertGreaterEqual(source.count("listDirty: true"), 8)
        self.assertIn("confirmRowsStackMembership(ulds, stackId)", source)
        self.assertIn("ULD eltávolítva a stackből", source)
        for english_fragment in (
            "Új stack created",
            "no new ULD can be hozzáadva",
            "Source stack save is in progress",
            "ULDs removed from stack",
            "table copied",
            "Másolás failed",
            "Filters reset",
            "row not found for editing",
            "For a new stack",
            "ULD edit",
            "Edit ULD",
            "h + 'h ' + m + 'm'",
        ):
            self.assertNotIn(english_fragment, source)

    def test_active_record_uses_persisted_stack_gha_when_source_is_temporarily_blank(self):
        state = {"uld_data": [{"uld_number": "PMC12345LH", "gha": ""}]}
        stacks = [{"id": "s1", "ulds": ["PMC12345LH"], "stack_gha": "Celebi"}]

        records = app._active_uld_records(state, stacks)

        self.assertEqual(records[0]["gha"], "Celebi")
        self.assertEqual(records[0]["gha_source"], "stack")

    def test_gha_validation_blocks_mixed_incoming_stack(self):
        stacks = [{"id": "s1", "name": "Target", "ulds": [], "revision": 1}]
        state = {
            "uld_data": [
                {"uld_number": "PMC001", "gha": "DHL"},
                {"uld_number": "PMC002", "gha": "UPS"},
            ]
        }

        with mock.patch.object(app.data_cache, "get_state", return_value=state):
            valid, error = app._validate_stack_gha_move("s1", ["PMC001", "PMC002"], stacks=stacks)

        self.assertFalse(valid)
        self.assertIn("GHA", error)

    def test_tk_validation_blocks_tk_uld_into_non_tk_stack(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "gha": "Menzies", "ulds": ["PMC001"], "revision": 1}]
        state = {"uld_data": [
            {"uld_number": "PMC001", "gha": "Menzies"},
            {"uld_number": "PMC002TK", "gha": "Menzies"},
        ]}
        with mock.patch.object(app.data_cache, "get_state", return_value=state):
            valid, error = app._validate_stack_gha_move("s1", ["PMC002TK"], stacks=stacks)
        self.assertFalse(valid)
        self.assertIn("TK", error)

    def test_tk_validation_blocks_non_tk_uld_into_tk_stack(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "gha": "Menzies", "ulds": ["PMC001TK"], "revision": 1}]
        state = {"uld_data": [
            {"uld_number": "PMC001TK", "gha": "Menzies"},
            {"uld_number": "PMC002", "gha": "Menzies"},
        ]}
        with mock.patch.object(app.data_cache, "get_state", return_value=state):
            valid, error = app._validate_stack_gha_move("s1", ["PMC002"], stacks=stacks)
        self.assertFalse(valid)
        self.assertIn("TK", error)

    def test_tk_validation_blocks_mixed_tk_incoming(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "gha": "Menzies", "ulds": [], "revision": 1}]
        state = {"uld_data": [
            {"uld_number": "PMC001TK", "gha": "Menzies"},
            {"uld_number": "PMC002", "gha": "Menzies"},
        ]}
        with mock.patch.object(app.data_cache, "get_state", return_value=state):
            valid, error = app._validate_stack_gha_move("s1", ["PMC001TK", "PMC002"], stacks=stacks)
        self.assertFalse(valid)
        self.assertIn("TK", error)

    def test_tk_validation_allows_tk_into_tk_only_stack(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "gha": "Menzies", "ulds": ["PMC001TK"], "revision": 1}]
        state = {"uld_data": [
            {"uld_number": "PMC001TK", "gha": "Menzies"},
            {"uld_number": "PMC002TK", "gha": "Menzies"},
        ]}
        with mock.patch.object(app.data_cache, "get_state", return_value=state):
            valid, error = app._validate_stack_gha_move("s1", ["PMC002TK"], stacks=stacks)
        self.assertTrue(valid, error)

    def test_gha_validation_allows_reorder_of_existing_inactive_ulds(self):
        stacks = [{"id": "s1", "name": "Target", "ulds": ["OLD001", "OLD002"], "revision": 1}]
        state = {"uld_data": []}

        with mock.patch.object(app.data_cache, "get_state", return_value=state):
            valid, error = app._validate_stack_gha_move("s1", ["OLD002", "OLD001"], stacks=stacks)

        self.assertTrue(valid, error)

    def test_visible_stacks_keeps_searched_inactive_uld_stack(self):
        # A stack whose only ULD is no longer active is hidden normally, but a search
        # for that ULD must surface its stack so the operator can see where it sits.
        stacks = [
            {"id": "s1", "ulds": ["PMC10995CP"], "prepared": True},  # only an inactive ULD
            {"id": "s2", "ulds": ["PMC222"]},
        ]
        all_uld = [{"uld_number": "PMC222"}]  # PMC10995CP is NOT active

        visible = app._visible_stacks_for_uld_view(stacks, all_uld)
        self.assertEqual({s["id"] for s in visible}, {"s2"})

        visible = app._visible_stacks_for_uld_view(stacks, all_uld, "PMC10995CP")
        self.assertEqual({s["id"] for s in visible}, {"s1", "s2"})

    def test_stack_api_state_contains_top_to_bottom_items_and_revisions(self):
        stacks = [{
            "id": "s1",
            "name": "GHA 1",
            "ulds": ["PMC002", "PMC001"],
            "revision": 7,
            "prepared": True,
            "created_at": "2026-06-09T08:10:00",
            "created_by": "creator",
        }]
        state = {
            "uld_data": [
                {"uld_number": "PMC001", "uld_type": "PMC", "gha": "DHL", "awbs": ["1"], "status": "ok"},
                {"uld_number": "PMC002", "uld_type": "PMC", "gha": "DHL", "awbs": ["2"], "status": "warning"},
            ]
        }

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_mtime", return_value=123.0), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._uld_stack_api_state()

        self.assertEqual(payload["mtime"], 123.0)
        self.assertEqual(payload["stacks"][0]["revision"], 7)
        self.assertTrue(payload["stacks"][0]["prepared"])
        self.assertEqual(payload["stacks"][0]["created_by"], "creator")
        self.assertEqual(payload["stacks"][0]["created_at"], "2026-06-09T08:10:00")
        self.assertEqual([item["uld"] for item in payload["stacks"][0]["items_top_to_bottom"]], ["PMC001", "PMC002"])
        self.assertEqual(payload["stacks"][0]["items_top_to_bottom"][0]["position_label"], "teteje")
        self.assertEqual(payload["stacks"][0]["items_top_to_bottom"][1]["position_label"], "alja")

    def test_stack_column_shows_created_side_info_in_body(self):
        stack = {
            "id": "s1",
            "name": "GHA 1",
            "ulds": ["PMC001"],
            "revision": 1,
            "created_at": "2026-06-09T08:10:00",
            "created_by": "creator",
        }
        lookup = {"PMC001": {"uld_number": "PMC001", "gha": "Menzies", "awbs": ["111"], "status": "ok"}}

        with mock.patch.object(app.uld_stack_manager, "get_uld_overrides", return_value={}):
            component = app._make_stack_column(stack, lookup)

        metas = _find_by_class(component, "uld-stack-created-meta")
        self.assertEqual(len(metas), 1)
        self.assertIn("Készítette: creator", _component_text(metas[0]))

    def test_stack_api_state_uses_edited_uld_override_in_stack_items(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "ulds": ["PMC009"], "revision": 7}]
        state = {
            "uld_data": [
                {"uld_number": "PMC001", "uld_type": "PMC", "gha": "Menzies", "awbs": ["111"], "status": "ok"},
            ]
        }
        override = {
            "PMC001": {
                "uld_number": "PMC009",
                "gha": "Celebi",
                "awbs": ["222", "333"],
                "edited_at": "2026-06-08T10:00:00",
                "edited_by": "tester",
                "change_summary": "ULD: PMC001 -> PMC009; AWB: 111 -> 222, 333",
            }
        }

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_mtime", return_value=123.0), \
             mock.patch.object(app.uld_stack_manager, "get_uld_renames", return_value={"PMC001": "PMC009"}), \
             mock.patch.object(app.uld_stack_manager, "get_uld_overrides", return_value=override), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._uld_stack_api_state()

        item = payload["stacks"][0]["items_top_to_bottom"][0]
        self.assertEqual(item["uld"], "PMC009")
        self.assertEqual(item["gha"], "Celebi")
        self.assertEqual(item["awbs"], ["222", "333"])
        self.assertTrue(item["is_edited"])
        self.assertEqual(item["edited_by"], "tester")
        self.assertEqual(item["edited_at"], "2026-06-08T10:00:00")
        self.assertEqual(item["change_summary"], "ULD: PMC001 -> PMC009; AWB: 111 -> 222, 333")

    def test_stack_api_state_hides_stack_when_all_ulds_are_no_longer_active(self):
        stacks = [
            {"id": "returned", "name": "GHA 1", "stack_gha": "DHL", "ulds": ["OLD001", "OLD002"], "revision": 1},
            {"id": "mixed", "name": "GHA 2", "stack_gha": "DHL", "ulds": ["OLD003", "PMC001"], "revision": 2},
            {"id": "empty", "name": "GHA 3", "ulds": [], "revision": 3},
        ]
        state = {
            "uld_data": [
                {"uld_number": "PMC001", "uld_type": "PMC", "gha": "DHL", "awbs": ["1"], "status": "ok"},
            ]
        }

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_mtime", return_value=123.0), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._uld_stack_api_state()

        returned_ids = [stack["id"] for stack in payload["stacks"]]
        self.assertNotIn("returned", returned_ids)
        self.assertIn("mixed", returned_ids)
        self.assertIn("empty", returned_ids)

    def test_stack_api_state_excludes_dispatched_stack_and_uld_from_active_view(self):
        stacks = [
            {"id": "sent", "name": "Truck", "stack_gha": "DHL", "ulds": ["PMC001"], "revision": 1, "dispatched": True},
            {"id": "active", "name": "GHA 1", "stack_gha": "DHL", "ulds": ["PMC002"], "revision": 2},
        ]
        state = {
            "uld_data": [
                {"uld_number": "PMC001", "uld_type": "PMC", "gha": "DHL", "awbs": ["1"], "status": "critical"},
                {"uld_number": "PMC002", "uld_type": "PMC", "gha": "DHL", "awbs": ["2"], "status": "ok"},
            ],
            "uld_times_full": {
                "PMC001": {"gha": "DHL", "awbs": ["1"]},
                "PMC002": {"gha": "DHL", "awbs": ["2"]},
            },
        }

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_mtime", return_value=123.0), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._uld_stack_api_state()
            active_records = app._active_uld_records(state, stacks)

        self.assertEqual([stack["id"] for stack in payload["stacks"]], ["active"])
        self.assertEqual([record["uld_number"] for record in active_records], ["PMC002"])

    def test_reused_uld_is_not_blocked_by_older_dispatched_cycle(self):
        stacks = [{
            "id": "old-sent",
            "name": "Menzies 1",
            "stack_gha": "Menzies",
            "ulds": ["PMC40729MU"],
            "dispatched": True,
            "dispatched_at": "2026-07-18T10:00:00",
            "prepared": True,
        }]
        state = {
            "uld_data": [{
                "uld_number": "PMC40729MU",
                "uld_type": "PMC",
                "gha": "Menzies",
                "awbs": ["112-09109306"],
                "am_time": "2026-07-21T12:26:00",
                "status": "ok",
            }],
            "uld_times_full": {
                "PMC40729MU": {
                    "gha": "Menzies",
                    "awbs": ["112-09109306"],
                    "am_time": "2026-07-21T12:26:00",
                },
            },
            # The fast reader deliberately keeps this empty while a newer AWB row
            # is open; cycle timestamps must still retire the historical stack.
            "uld_returned": [],
        }

        active_records = app._active_uld_records(state, stacks)
        visible_dispatched = app._strip_returned_ulds(
            app._dispatched_stacks(stacks),
            app._returned_uld_set(state),
            state["uld_times_full"],
        )
        dispatched_hits = app._dispatched_search_hits(
            stacks,
            "PMC40729MU",
            state["uld_times_full"],
            state["uld_data"],
            app._returned_uld_set(state),
        )

        self.assertEqual([record["uld_number"] for record in active_records], ["PMC40729MU"])
        self.assertEqual(visible_dispatched, [])
        self.assertEqual(dispatched_hits, [])
        self.assertEqual(
            app._reactivated_uld_codes(["PMC40729MU"], state, stacks),
            {"PMC40729MU"},
        )

    def test_current_dispatched_cycle_still_blocks_active_duplicate(self):
        stacks = [{
            "id": "current-sent",
            "ulds": ["PMC40729MU"],
            "dispatched": True,
            "dispatched_at": "2026-07-22T08:00:00",
            "prepared": True,
        }]
        state = {
            "uld_data": [{"uld_number": "PMC40729MU", "am_time": "2026-07-21T12:26:00"}],
            "uld_times_full": {"PMC40729MU": {"am_time": "2026-07-21T12:26:00"}},
        }

        self.assertEqual(app._active_uld_records(state, stacks), [])
        self.assertEqual(app._reactivated_uld_codes(["PMC40729MU"], state, stacks), set())

    def test_exact_stack_lookup_ignores_dispatched_history(self):
        stacks = [
            {"id": "sent", "ulds": ["PMC40729MU"], "dispatched": True},
            {"id": "active", "ulds": ["AKE00001MU"]},
        ]

        self.assertIsNone(app._find_exact_stack_for_ulds(["PMC40729MU"], stacks))

    def test_stack_api_state_keeps_stacks_when_uld_source_is_temporarily_empty(self):
        stacks = [{"id": "s1", "name": "GHA 1", "stack_gha": "DHL", "ulds": ["OLD001"], "revision": 1}]
        state = {"uld_data": []}

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_mtime", return_value=123.0), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._uld_stack_api_state()

        self.assertEqual([stack["id"] for stack in payload["stacks"]], ["s1"])

    def test_stack_display_name_uses_stored_stack_gha_when_live_lookup_missing(self):
        stacks = [{"id": "s1", "name": "GHA 2", "stack_gha": "Celebi", "ulds": ["PAG001"], "revision": 1}]
        state = {"uld_data": []}

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_mtime", return_value=123.0), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._uld_stack_api_state()

        self.assertEqual(payload["stacks"][0]["single_gha"], "Celebi")
        self.assertEqual(payload["stacks"][0]["display_name"], "Celebi 2")

    def test_stack_display_name_keeps_stored_number_after_other_same_gha_stacks_disappear(self):
        stacks = [
            {"id": "s5", "name": "GHA 5", "stack_gha": "Menzies", "ulds": ["PMC005"], "revision": 5},
        ]
        state = {
            "uld_data": [
                {"uld_number": "PMC005", "uld_type": "PMC", "gha": "Menzies", "awbs": ["5"], "status": "ok"},
            ]
        }

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_mtime", return_value=123.0), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._uld_stack_api_state()

        self.assertEqual(payload["stacks"][0]["display_name"], "Menzies 5")

    def test_stack_display_name_numbers_legacy_gha_only_names(self):
        stacks = [
            {"id": "s5", "name": "GHA 5", "stack_gha": "Menzies", "ulds": ["PMC005"], "revision": 5},
            {"id": "legacy-1", "name": "Menzies", "ulds": ["PMC006"], "revision": 6},
            {"id": "legacy-2", "name": "Menzies", "ulds": ["PMC007"], "revision": 7},
        ]
        state = {
            "uld_data": [
                {"uld_number": "PMC005", "uld_type": "PMC", "gha": "Menzies", "awbs": ["5"], "status": "ok"},
                {"uld_number": "PMC006", "uld_type": "PMC", "gha": "Menzies", "awbs": ["6"], "status": "ok"},
                {"uld_number": "PMC007", "uld_type": "PMC", "gha": "Menzies", "awbs": ["7"], "status": "ok"},
            ]
        }

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_stacks_mtime", return_value=123.0), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            payload = app._uld_stack_api_state()

        display = {stack["id"]: stack["display_name"] for stack in payload["stacks"]}
        self.assertEqual(display["legacy-1"], "Menzies 6")
        self.assertEqual(display["legacy-2"], "Menzies 7")

    def test_create_stack_api_returns_fresh_stack_state(self):
        stack = {"id": "s1", "name": "GHA 1", "ulds": [], "revision": 1}
        api_state = {"mtime": 123.0, "stacks": [stack]}

        with mock.patch.object(app.uld_stack_manager, "create_stack", return_value=stack) as create_stack, \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={"action": "create", "client_op_id": "op-1"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stack"]["id"], "s1")
        self.assertEqual(payload["stack_state"], api_state)
        create_stack.assert_called_once_with("", client_op_id="op-1")

    def test_add_api_unlocks_only_reactivated_dispatched_uld(self):
        stacks = [
            {
                "id": "old-sent",
                "ulds": ["PMC40729MU"],
                "dispatched": True,
                "dispatched_at": "2026-07-18T10:00:00",
                "prepared": True,
            },
            {"id": "current", "ulds": [], "revision": 2},
        ]
        state = {
            "uld_data": [{"uld_number": "PMC40729MU", "am_time": "2026-07-21T12:26:00"}],
            "uld_times_full": {"PMC40729MU": {"am_time": "2026-07-21T12:26:00"}},
        }

        with mock.patch.object(app, "_validate_stack_gha_move", return_value=(True, "")), \
             mock.patch.object(app, "_single_gha_for_ulds", return_value="Menzies"), \
             mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app.uld_stack_manager, "add_from_list_to_stack", return_value=True) as add, \
             mock.patch.object(app, "_uld_stack_api_state", return_value={"mtime": 0.0, "stacks": []}):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "add_from_list",
                    "stack_id": "current",
                    "stack_revision": 2,
                    "uld_numbers": ["PMC40729MU"],
                },
            )

        self.assertEqual(response.status_code, 200)
        add.assert_called_once_with(
            "current",
            ["PMC40729MU"],
            position=None,
            expected_revision=2,
            stack_gha="Menzies",
            allow_dispatched_ulds={"PMC40729MU"},
        )

    def test_stack_conflict_api_returns_authoritative_revision_state(self):
        api_state = {"mtime": 321.0, "stacks": [{"id": "s1", "revision": 8}]}

        with mock.patch.object(app, "_validate_stack_gha_move", return_value=(True, "")), \
             mock.patch.object(app, "_single_gha_for_ulds", return_value="Menzies"), \
             mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=[]), \
             mock.patch.object(app.uld_stack_manager, "add_from_list_to_stack", side_effect=app.uld_stack_manager.StackConflict("stale")), \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={"action": "add_from_list", "stack_id": "s1", "stack_revision": 7, "uld_numbers": ["PMC001"]},
            )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertTrue(payload["conflict"])
        self.assertEqual(payload["stack_state"], api_state)

    def test_create_with_ulds_api_creates_stack_and_adds_selection(self):
        stack = {"id": "s1", "name": "GHA 1", "ulds": [], "revision": 1}
        api_state = {"mtime": 123.0, "stacks": [stack]}

        with mock.patch.object(app, "_validate_stack_gha_move", return_value=(True, "")) as validate, \
             mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=[]), \
             mock.patch.object(app.uld_stack_manager, "create_stack_with_ulds", return_value=stack) as create_stack_with_ulds, \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "create_with_ulds",
                    "client_op_id": "op-2",
                    "uld_numbers": ["pmc-001", "AKE 002"],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stack"], stack)
        validate.assert_called_once()
        create_stack_with_ulds.assert_called_once_with("", ["PMC001", "AKE002"], position=None, client_op_id="op-2", stack_gha="")

    def test_create_with_ulds_api_reuses_exact_existing_stack(self):
        existing = {"id": "s1", "name": "GHA 1", "ulds": ["AKE002", "PMC001"], "revision": 3}
        api_state = {"mtime": 123.0, "stacks": [existing]}

        with mock.patch.object(app, "_validate_stack_gha_move", return_value=(True, "")), \
             mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=[existing]), \
             mock.patch.object(app.uld_stack_manager, "create_stack") as create_stack, \
             mock.patch.object(app.uld_stack_manager, "add_ulds_to_stack") as add_ulds, \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "create_with_ulds",
                    "client_op_id": "op-duplicate",
                    "uld_numbers": ["PMC001", "AKE002"],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["already_stacked"])
        self.assertEqual(payload["stack"], existing)
        create_stack.assert_not_called()
        add_ulds.assert_not_called()

    def test_create_with_ulds_api_backfills_gha_when_reusing_exact_stack(self):
        existing = {"id": "s1", "name": "GHA 1", "ulds": ["AKE002", "PMC001"], "revision": 3}
        updated = {**existing, "stack_gha": "Menzies", "revision": 4}
        api_state = {"mtime": 123.0, "stacks": [updated]}

        with mock.patch.object(app, "_validate_stack_gha_move", return_value=(True, "")), \
             mock.patch.object(app, "_single_gha_for_ulds", return_value="Menzies"), \
             mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=[existing]), \
             mock.patch.object(app.uld_stack_manager, "ensure_stack_gha", return_value=updated) as ensure_stack_gha, \
             mock.patch.object(app.uld_stack_manager, "create_stack_with_ulds") as create_stack_with_ulds, \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "create_with_ulds",
                    "client_op_id": "op-reuse-gha",
                    "uld_numbers": ["PMC001", "AKE002"],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["already_stacked"])
        self.assertEqual(payload["stack"]["stack_gha"], "Menzies")
        ensure_stack_gha.assert_called_once_with("s1", "Menzies")
        create_stack_with_ulds.assert_not_called()

    def test_manual_uld_api_reports_generic_required_error(self):
        for payload in [
            {"stack_id": "__new__", "uld_number": "", "gha": "Celebi"},
            {"stack_id": "__new__", "uld_number": "PAG18804O3", "gha": ""},
        ]:
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={"action": "add_manual", **payload},
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"], "Egy kötelező elem nincs kitöltve")

    def test_manual_uld_api_can_create_new_stack_target(self):
        stack = {"id": "s1", "name": "GHA 1", "stack_gha": "Celebi", "ulds": [], "revision": 1}
        api_state = {"mtime": 123.0, "stacks": [stack]}

        with mock.patch.object(app.uld_stack_manager, "create_stack", return_value=stack) as create_stack, \
             mock.patch.object(app.uld_stack_manager, "add_manual_uld_to_stack", return_value=True) as add_manual, \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "add_manual",
                    "stack_id": "__new__",
                    "uld_number": "pag18804o3",
                    "awb": "92162070142",
                    "gha": "Celebi",
                    "am_time": "2026-05-31T08:30:00.000Z",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stack"], stack)
        create_stack.assert_called_once_with(stack_gha="Celebi")
        add_manual.assert_called_once()
        self.assertEqual(add_manual.call_args.args[0], "s1")
        self.assertEqual(add_manual.call_args.kwargs["uld_number"], "PAG18804O3")

    def test_manual_uld_api_derives_gha_from_selected_stack(self):
        stack = {"id": "s1", "name": "Celebi", "ulds": ["PMC001"], "revision": 4}
        api_state = {"mtime": 123.0, "stacks": [stack]}
        state = {"uld_data": [{"uld_number": "PMC001", "gha": "Celebi"}]}

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=[stack]), \
             mock.patch.object(app.uld_stack_manager, "add_manual_uld_to_stack", return_value=True) as add_manual, \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "add_manual",
                    "stack_id": "s1",
                    "stack_revision": 4,
                    "uld_number": "PAG18804O3",
                    "awb": "92162070142",
                    "gha": "",
                },
            )

        self.assertEqual(response.status_code, 200)
        add_manual.assert_called_once()
        self.assertEqual(add_manual.call_args.kwargs["gha"], "Celebi")

    def test_manual_uld_api_allows_blank_awb(self):
        stack = {"id": "s1", "name": "Celebi", "ulds": ["PMC001"], "revision": 4}
        api_state = {"mtime": 123.0, "stacks": [stack]}
        state = {"uld_data": [{"uld_number": "PMC001", "gha": "Celebi"}]}

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=[stack]), \
             mock.patch.object(app.uld_stack_manager, "add_manual_uld_to_stack", return_value=True) as add_manual, \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "add_manual",
                    "stack_id": "s1",
                    "stack_revision": 4,
                    "uld_number": "PAG18804O3",
                    "awb": "",
                    "gha": "",
                    "am_time": "",
                },
            )

        self.assertEqual(response.status_code, 200)
        add_manual.assert_called_once()
        self.assertEqual(add_manual.call_args.kwargs["awb"], "")

    def test_manual_uld_api_rejects_duplicate_uld_or_awb_with_details(self):
        stack = {"id": "s1", "name": "Celebi", "ulds": ["PAG18804O3"], "revision": 4}
        state = {
            "uld_data": [{
                "uld_number": "PAG18804O3",
                "uld_type": "PAG",
                "gha": "Celebi",
                "awbs": ["92162070142"],
                "am_time": "2026-05-31T08:30:00",
                "expiry_time": "2026-06-02T08:30:00",
                "status": "ok",
            }]
        }

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=[stack]), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "add_manual",
                    "stack_id": "s1",
                    "uld_number": "PAG18804O3",
                    "awb": "555",
                    "gha": "",
                },
            )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["duplicate"]["uld_number"], "PAG18804O3")
        self.assertEqual(payload["duplicate"]["gha"], "Celebi")
        self.assertEqual(payload["duplicate"]["match_type"], "uld")

    def test_manual_uld_api_rejects_future_am_time(self):
        stack = {"id": "s1", "name": "Celebi", "ulds": ["PMC001"], "revision": 4}
        state = {"uld_data": [{"uld_number": "PMC001", "gha": "Celebi"}]}

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=[stack]), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "add_manual",
                    "stack_id": "s1",
                    "uld_number": "PAG18804O3",
                    "awb": "92162070142",
                    "gha": "",
                    "am_time": "2999-01-01T00:00:00",
                },
            )

        self.assertEqual(response.status_code, 400)

    def test_manual_uld_api_rejects_non_numeric_awb_and_unknown_gha(self):
        for payload in [
            {"awb": "ABC123", "gha": "Celebi"},
            {"awb": "123456", "gha": "Other"},
        ]:
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "add_manual",
                    "stack_id": "__new__",
                    "uld_number": "PAG18804O3",
                    **payload,
                },
            )
            self.assertEqual(response.status_code, 400)

    def test_manual_uld_records_accept_iso_utc_time_from_browser(self):
        stacks = [{
            "id": "s1",
            "name": "Celebi",
            "ulds": ["PAG18804O3"],
            "manual_ulds": {
                "PAG18804O3": {
                    "awb": "92162070142",
                    "gha": "Celebi",
                    "am_time": "2026-05-31T08:30:00.000Z",
                }
            },
        }]

        records = app._manual_uld_records(stacks)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["uld_number"], "PAG18804O3")
        self.assertIn("T", records[0]["expiry_time"])

    def test_delete_stack_api_treats_already_missing_stack_as_synced(self):
        api_state = {"mtime": 123.0, "stacks": []}

        with mock.patch.object(app.uld_stack_manager, "delete_stack", return_value=False), \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post("/api/uld/stack", json={"action": "delete", "stack_id": "missing"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["already_deleted"])
        self.assertEqual(payload["stack_state"], api_state)

    def test_bulk_uld_search_terms_normalize_dedupe_and_preserve_order(self):
        terms = app._bulk_uld_search_terms(" pmc-001 \nAKE 002\nPMC001\n\nake-003 ")

        self.assertEqual(terms, ["PMC001", "AKE002", "AKE003"])

    def test_single_line_search_is_not_bulk_mode(self):
        self.assertEqual(app._bulk_uld_search_terms("PMC001"), [])

    def test_bulk_search_matches_exact_normalized_uld_only(self):
        info = {"uld_type": "PMC", "gha": "DHL", "status": "ok", "awbs": ["123456"]}

        self.assertTrue(app._uld_matches_query("PMC-001", info, "PMC001\nAKE002"))
        self.assertFalse(app._uld_matches_query("PMC-001", info, "PMC01\nAKE002"))

    def test_bulk_filter_bypasses_chip_filters_and_preserves_pasted_order(self):
        all_uld = [
            {"uld_number": "PMC001", "uld_type": "PMC", "gha": "Menzies", "status": "ok", "awbs": []},
            {"uld_number": "AKE002", "uld_type": "AKE", "gha": "Celebi", "status": "warning", "awbs": []},
            {"uld_number": "PAG003", "uld_type": "PAG", "gha": "DHL", "status": "critical", "awbs": []},
        ]

        filtered, bulk_mode = app._filter_uld_for_view(
            all_uld,
            status_filter="ok",
            prefix_filter="PMC",
            gha_filter="Menzies",
            search_text="AKE002\nPMC001",
        )

        self.assertTrue(bulk_mode)
        self.assertEqual([u["uld_number"] for u in filtered], ["AKE002", "PMC001"])

    def test_bulk_filter_includes_near_uld_matches_after_exact_hits(self):
        all_uld = [
            {"uld_number": "PAG18804O3", "uld_type": "PAG", "gha": "Celebi", "status": "ok", "awbs": []},
            {"uld_number": "PAG01289GH", "uld_type": "PAG", "gha": "Celebi", "status": "ok", "awbs": []},
            {"uld_number": "PAG01287GH", "uld_type": "PAG", "gha": "Celebi", "status": "ok", "awbs": []},
        ]

        filtered, bulk_mode = app._filter_uld_for_view(all_uld, search_text="PAG18804\u041e3\nPAG01288GH")

        self.assertTrue(bulk_mode)
        self.assertEqual([u["uld_number"] for u in filtered], ["PAG18804O3", "PAG01289GH"])
        self.assertNotIn("_search_match_kind", filtered[0])
        self.assertEqual(filtered[1]["_search_match_kind"], "near")
        self.assertEqual(filtered[1]["_search_requested_uld"], "PAG01288GH")
        _exact, near, _missing = app._bulk_uld_resolution(all_uld, "PAG01288GH\nPAG99999GH")
        self.assertEqual({k: [u["uld_number"] for u in v] for k, v in near.items()}, {"PAG01288GH": ["PAG01289GH"]})

    def test_bulk_filter_does_not_return_far_uld_matches(self):
        all_uld = [
            {"uld_number": "PAG01289GH", "uld_type": "PAG", "gha": "Celebi", "status": "ok", "awbs": []},
        ]

        filtered, bulk_mode = app._filter_uld_for_view(all_uld, search_text="PAG99999GH\nPAG88888GH")

        self.assertTrue(bulk_mode)
        self.assertEqual(filtered, [])

    def test_normal_filter_still_combines_chips_and_search(self):
        all_uld = [
            {"uld_number": "PMC001", "uld_type": "PMC", "gha": "Menzies", "status": "ok", "awbs": ["111"]},
            {"uld_number": "AKE002", "uld_type": "AKE", "gha": "Celebi", "status": "ok", "awbs": ["222"]},
        ]

        filtered, bulk_mode = app._filter_uld_for_view(
            all_uld,
            status_filter="ok",
            prefix_filter="PMC",
            gha_filter="",
            search_text="111",
        )

        self.assertFalse(bulk_mode)
        self.assertEqual([u["uld_number"] for u in filtered], ["PMC001"])

    def test_dispatched_bulk_search_summary_reports_dispatched_and_missing_terms(self):
        stacks = [
            {"id": "sent", "name": "DHL 1", "stack_gha": "DHL", "ulds": ["PMC001"], "dispatched": True},
            {"id": "active", "name": "DHL 2", "stack_gha": "DHL", "ulds": ["AKE002"]},
        ]
        summary = app._dispatched_uld_search_summary(
            stacks,
            "PMC001\nAKE002\nPAG999",
            {"PMC001": {"gha": "DHL", "awbs": ["111"]}},
        )

        text = _component_text(summary)
        self.assertIn("Keresett: 3", text)
        self.assertIn("Kiadva: 1", text)
        self.assertIn("Nem kiküldött:", text)
        self.assertIn("AKE002", text)
        self.assertIn("PAG999", text)

    def test_dispatched_bulk_search_highlights_near_match_chip(self):
        stacks = [
            {
                "id": "sent",
                "name": "Celebi 1",
                "stack_gha": "Celebi",
                "ulds": ["PAG01289GH"],
                "dispatched": True,
                "dispatched_at": "2026-06-06T10:00:00",
            }
        ]
        state = {"uld_times_full": {"PAG01289GH": {"gha": "Celebi", "awbs": ["123"]}}}

        with mock.patch.object(app.data_cache, "get_state", return_value=state):
            section = app._make_dispatched_stacks_section(stacks, [], "PAG01288GH\nPMC99999")

        self.assertIn("PAG01289GH", _component_text(section))
        self.assertEqual(len(_find_by_class(section, "uld-dlist-touch-item-match")), 1)
        self.assertIn("Keresés: 1 ULD találat", _component_text(section))

    def test_dispatched_signature_covers_popup_time_fields(self):
        stack = {
            "id": "sent",
            "name": "DHL 1",
            "stack_gha": "DHL",
            "ulds": ["PMC001"],
            "revision": 1,
            "dispatched": True,
            "dispatched_at": "2026-06-06T10:00:00",
            "prepared_at": "2026-06-06T09:30:00",
        }

        base = app._dispatched_signature([stack], "", {"PMC001": {"awbs": ["1"], "am_time": "2026-06-06T08:00:00", "gha": "DHL"}})
        changed_am = app._dispatched_signature([stack], "", {"PMC001": {"awbs": ["1"], "am_time": "2026-06-06T08:05:00", "gha": "DHL"}})
        changed_prepared = app._dispatched_signature([{**stack, "prepared_at": "2026-06-06T09:35:00"}], "", {"PMC001": {"awbs": ["1"], "am_time": "2026-06-06T08:00:00", "gha": "DHL"}})

        self.assertNotEqual(base, changed_am)
        self.assertNotEqual(base, changed_prepared)

    def test_inbound_visible_dedupe_keeps_one_card_source_per_awb_and_state(self):
        import pandas as pd

        df = pd.DataFrame([
            {"awb": "111-222", "rank": 2, "is_stored": False, "am_time": "a"},
            {"awb": "111-222", "rank": 1, "is_stored": False, "am_time": "b"},
            {"awb": "111-222", "rank": 3, "is_stored": True, "am_time": "c"},
            {"awb": "157-47004381-1", "rank": 4, "is_stored": False, "am_time": "d"},
            {"awb": "157-47004381", "rank": 5, "is_stored": False, "am_time": "e"},
        ])

        deduped = app._dedupe_inbound_view_df(df)

        self.assertEqual(len(deduped), 3)
        self.assertEqual(deduped.iloc[0]["awb"], "111-222")
        self.assertFalse(bool(deduped.iloc[0]["is_stored"]))
        self.assertTrue(any(bool(row["is_stored"]) for _, row in deduped.iterrows()))
        self.assertEqual(len(deduped[deduped["awb"].isin(["157-47004381-1", "157-47004381"])]), 1)

    def test_edit_uld_api_persists_clean_awb_uld_and_gha_override(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "stack_gha": "Menzies", "ulds": ["PMC001"], "revision": 1}]
        state = {
            "uld_data": [
                {"uld_number": "PMC001", "uld_type": "PMC", "gha": "Menzies", "awbs": ["111"], "status": "ok"},
            ]
        }
        api_state = {"mtime": 123.0, "stacks": stacks}

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app.uld_stack_manager, "update_uld_details", return_value=True) as update_details, \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "edit_uld",
                    "uld_number": "PMC001",
                    "new_uld_number": "pmc-009",
                    "awb": "222, 333",
                    "gha": "Menzies",
                },
            )

        self.assertEqual(response.status_code, 200)
        update_details.assert_called_once_with(
            "PMC001",
            "PMC009",
            awbs=["222", "333"],
            gha="Menzies",
            revert=False,
            previous_awbs=["111"],
            previous_gha="Menzies",
        )
        self.assertEqual(response.get_json()["edited_uld"]["new_uld"], "PMC009")

    def test_edit_uld_api_back_to_source_values_marks_revert(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "stack_gha": "Menzies", "ulds": ["PMC001"], "revision": 1}]
        state = {
            "uld_data": [
                {"uld_number": "PMC001", "uld_type": "PMC", "gha": "Menzies", "awbs": ["111"], "status": "ok"},
            ]
        }
        api_state = {"mtime": 123.0, "stacks": stacks}

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_uld_renames", return_value={}), \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app.uld_stack_manager, "update_uld_details", return_value=True) as update_details, \
             mock.patch.object(app, "_uld_stack_api_state", return_value=api_state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "edit_uld",
                    "uld_number": "PMC001",
                    "new_uld_number": "PMC001",
                    "awb": "111",
                    "gha": "Menzies",
                },
            )

        self.assertEqual(response.status_code, 200)
        update_details.assert_called_once_with(
            "PMC001",
            "PMC001",
            awbs=["111"],
            gha="Menzies",
            revert=True,
            previous_awbs=["111"],
            previous_gha="Menzies",
        )
        edited = response.get_json()["edited_uld"]
        self.assertTrue(edited["reverted"])
        self.assertEqual(edited["edited_at"], "")

    def test_edit_uld_api_blocks_edit_that_creates_mixed_tk_stack(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "stack_gha": "Menzies",
                   "ulds": ["PMC001", "PMC002"], "revision": 1}]
        state = {"uld_data": [
            {"uld_number": "PMC001", "uld_type": "PMC", "gha": "Menzies", "awbs": ["111"], "status": "ok"},
            {"uld_number": "PMC002", "uld_type": "PMC", "gha": "Menzies", "awbs": ["222"], "status": "ok"},
        ]}
        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.uld_stack_manager, "get_uld_overrides", return_value={}), \
             mock.patch.object(app.uld_stack_manager, "get_uld_renames", return_value={}), \
             mock.patch.object(app.data_cache, "get_state", return_value=state), \
             mock.patch.object(app.uld_stack_manager, "update_uld_details", return_value=True) as update_details:
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "edit_uld",
                    "uld_number": "PMC001",
                    "new_uld_number": "PMC001TK",
                    "awb": "111",
                    "gha": "Menzies",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("TK", response.get_json().get("error", ""))
        update_details.assert_not_called()

    def test_edit_uld_api_rejects_awb_collision_with_details(self):
        stacks = [{"id": "s1", "name": "Menzies 1", "stack_gha": "Menzies", "ulds": ["PMC001"], "revision": 1}]
        state = {
            "uld_data": [
                {"uld_number": "PMC001", "uld_type": "PMC", "gha": "Menzies", "awbs": ["111"], "status": "ok"},
                {"uld_number": "PMC002", "uld_type": "PMC", "gha": "Menzies", "awbs": ["222"], "status": "ok"},
            ]
        }

        with mock.patch.object(app.uld_stack_manager, "get_stacks", return_value=stacks), \
             mock.patch.object(app.data_cache, "get_state", return_value=state):
            response = app.server.test_client().post(
                "/api/uld/stack",
                json={
                    "action": "edit_uld",
                    "uld_number": "PMC001",
                    "new_uld_number": "PMC001",
                    "awb": "222",
                    "gha": "Menzies",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("AWB", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
