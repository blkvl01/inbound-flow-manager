import unittest
from datetime import datetime, timedelta

import pandas as pd

import priority_engine


def _items(statuses):
    return [{"status": status, "lmp": "STD"} for status in statuses]


def _row(name, statuses, *, in_bud=False, driver_active=False, shippable=2, arrival_offset_hours=None, am_hours_ago=2):
    now = datetime.now()
    return {
        "name": name,
        "lmp": "STD",
        "bud_lmp": "",
        "glabs_loading_items": _items(statuses),
        "glabs_ready_items": [],
        "glabs_total": 4,
        "glabs_shippable": shippable,
        "glabs_ratio": shippable / 4,
        "is_shippable": shippable > 0,
        "in_bud_pallets": in_bud,
        "driver_checkin": now - timedelta(hours=1) if driver_active else pd.NaT,
        "driver_in_rest": False,
        "rest_until": pd.NaT,
        "truck_arrival_time": now + timedelta(hours=arrival_offset_hours) if arrival_offset_hours is not None else pd.NaT,
        "am_time": now - timedelta(hours=am_hours_ago),
    }


class PriorityEngineTests(unittest.TestCase):
    def test_operational_bucket_outranks_status_mix(self):
        # Driver on-site + many shippable must beat a not-here/weaker-bucket item even
        # if the latter has a more advanced E_COMM status mix. The status mix is only a
        # fine tie-break, never an override of the operational situation.
        df = pd.DataFrame([
            _row("better_status_weaker_bucket", ["Felvéve", "Felvéve", "Megérkezett", "Értesítő"], in_bud=False, driver_active=False),
            _row("on_site_many_shippable", ["Felvéve", "Felvéve", "Megérkezett", "Megérkezett"], in_bud=True, driver_active=True),
        ])

        ranked = priority_engine.apply_priorities(df)

        self.assertEqual(ranked.iloc[0]["name"], "on_site_many_shippable")

    def test_resting_few_shippable_stays_behind_on_site_many_shippable(self):
        # Reproduces the reported bug: a resting + few-shippable item was ranking
        # ahead of an on-site + many-shippable one because of its status mix.
        now = datetime.now()
        resting = _row("resting_few_shippable", ["Felvéve", "Felvéve", "Felvéve", "Felvéve"],
                       in_bud=True, driver_active=False, shippable=1)
        resting["driver_in_rest"] = True
        resting["rest_until"] = now + timedelta(hours=8)  # not "rest soon"
        on_site = _row("on_site_many_shippable", ["Megérkezett", "Megérkezett", "Megérkezett", "Megérkezett"],
                       in_bud=True, driver_active=True, shippable=3)

        ranked = priority_engine.apply_priorities(pd.DataFrame([resting, on_site]))

        self.assertEqual(ranked.iloc[0]["name"], "on_site_many_shippable")

    def test_en_route_remains_behind_arrived_work_even_with_better_status(self):
        df = pd.DataFrame([
            _row("arrived_bad_status", ["Megérkezett", "Megérkezett", "Elindult", "Elindult"], in_bud=False),
            _row("future_good_status", ["Felvéve", "Felvéve", "Értesítő", "Értesítő"], in_bud=True, driver_active=True, arrival_offset_hours=2),
        ])

        ranked = priority_engine.apply_priorities(df)

        self.assertEqual(ranked.iloc[0]["name"], "arrived_bad_status")

    def test_aged_item_jumps_ahead_of_recent_strong_work(self):
        # A 4+ órája felvett, gyenge operatív helyzetű tétel a friss, erős
        # (sofőr itt + sok kiadható) tétel ELÉ kerül, mert túl régóta vár.
        df = pd.DataFrame([
            _row("recent_strong", ["Megérkezett", "Megérkezett", "Megérkezett", "Megérkezett"],
                 in_bud=True, driver_active=True, shippable=4, am_hours_ago=0.5),
            _row("aged_weak", ["Felvéve", "Felvéve", "Felvéve", "Felvéve"],
                 in_bud=False, driver_active=False, shippable=1, am_hours_ago=5),
        ])

        ranked = priority_engine.apply_priorities(df)

        self.assertEqual(ranked.iloc[0]["name"], "aged_weak")
        self.assertTrue(bool(ranked.iloc[0]["is_aged"]))

    def test_oldest_aged_item_ranks_first_among_aged(self):
        # Az aged tieren belül a legrégebben felvett tétel kerül előre.
        df = pd.DataFrame([
            _row("aged_4h", ["Felvéve"], shippable=1, am_hours_ago=4.2),
            _row("aged_7h", ["Felvéve"], shippable=1, am_hours_ago=7),
            _row("aged_5h", ["Felvéve"], shippable=1, am_hours_ago=5),
        ])

        ranked = priority_engine.apply_priorities(df)

        self.assertEqual(list(ranked["name"]), ["aged_7h", "aged_5h", "aged_4h"])

    def test_just_under_threshold_is_not_aged(self):
        # 4 óra alatt nincs öregedési boost; a normál operatív rend marad.
        df = pd.DataFrame([
            _row("recent_strong", ["Megérkezett"], in_bud=True, driver_active=True, shippable=4, am_hours_ago=0.5),
            _row("almost_aged", ["Felvéve"], in_bud=False, driver_active=False, shippable=1, am_hours_ago=3.9),
        ])

        ranked = priority_engine.apply_priorities(df)

        self.assertEqual(ranked.iloc[0]["name"], "recent_strong")
        self.assertFalse(bool(ranked.iloc[1]["is_aged"]))

    def test_athu_rest_soon_still_outranks_aged(self):
        # Az AT/HU + hamarosan végző pihenő (gate 0) az aged tier (gate 1) elé kerül.
        now = datetime.now()
        rest_soon = _row("athu_rest_soon", ["Megérkezett"], in_bud=True, shippable=2, am_hours_ago=1)
        rest_soon["lmp"] = "AT HU"
        rest_soon["driver_in_rest"] = True
        rest_soon["rest_until"] = now + timedelta(hours=2)
        aged = _row("aged_weak", ["Felvéve"], shippable=1, am_hours_ago=6)

        ranked = priority_engine.apply_priorities(pd.DataFrame([aged, rest_soon]))

        self.assertEqual(ranked.iloc[0]["name"], "athu_rest_soon")


if __name__ == "__main__":
    unittest.main()
