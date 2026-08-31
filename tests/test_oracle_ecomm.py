import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from oracle_ecomm import (
    DETAIL_COLUMNS,
    HEAD_COLUMNS,
    OracleEcommStore,
    OracleSettings,
    OracleSourceError,
    get_source_mode,
)


def settings():
    return OracleSettings(
        host="test",
        port=1521,
        service="HGL",
        user="reader",
        password="secret",
        call_timeout_ms=1000,
        full_refresh_hours=6,
    )


def head(record_id=1, awb="123-45678901", changed=None, **overrides):
    row = {column: None for column in HEAD_COLUMNS}
    row.update({
        "E_COMM_ID": record_id,
        "AIR_WAYBILL": awb,
        "SUM_OF_COLLIS": 2,
        "SUM_OF_WEIGHT_KG": 10,
        "SUM_OF_PARCELS": 20,
        "LAST_CHANGE_DATE": changed or datetime(2026, 8, 12, 8, 0),
    })
    row.update(overrides)
    return row


def detail(record_id=10, head_id=1, awb="123-45678901", changed=None, **overrides):
    row = {column: None for column in DETAIL_COLUMNS}
    row.update({
        "REF_E_COMM_ID": head_id,
        "AIR_WAYBILL": awb,
        "E_COMM_DETAIL_ID": record_id,
        "CLIENT_NAME": "MEEST UA",
        "GHA_COMPANY_NAME": "AS Cargo",
        "PARCEL_STATUS_NAME": "Felvéve",
        "DETAIL_COLLIS": 2,
        "DETAIL_WEIGHT_KG": 10,
        "DETAIL_PARCELS": 20,
        "GOODS_TRANSFER_DATE": datetime(2026, 8, 12, 8, 30),
        "TRANSFER_PLATE_NUMBER": "ABC-123",
        "LAST_CHANGE_DATE": changed or datetime(2026, 8, 12, 8, 0),
    })
    row.update(overrides)
    return row


class FakeCursor:
    def __init__(self, database_time, heads, details):
        self.database_time = database_time
        self.heads = heads
        self.details = details
        self.description = []
        self._rows = []

    def execute(self, sql, binds=None):
        if "SYSTIMESTAMP" in sql:
            self._rows = [(self.database_time,)]
            self.description = [("NOW",)]
            return
        rows = self.heads if "WAREHOUSE_HEAD_REPORT_VW" in sql else self.details
        selected_text = sql.split("SELECT ", 1)[1].split(" FROM ", 1)[0]
        selected = [column.strip() for column in selected_text.split(",")]
        lower = (binds or {}).get("lower_bound")
        upper = (binds or {}).get("upper_bound")
        filtered = []
        for row in rows:
            changed = row["LAST_CHANGE_DATE"]
            if upper is not None and changed > upper:
                continue
            if lower is not None and changed < lower:
                continue
            filtered.append(tuple(row.get(column) for column in selected))
        self._rows = filtered
        self.description = [(column,) for column in selected]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, cursor, fail=False):
        self._cursor = cursor
        self.fail = fail
        self.closed = False

    def cursor(self):
        if self.fail:
            raise RuntimeError("network down")
        return self._cursor

    def close(self):
        self.closed = True


class LegacyDetailCursor(FakeCursor):
    def execute(self, sql, binds=None):
        if "BUD_PALLET_LOCATION" in sql or "BUD_PALLET_RAMP" in sql:
            raise RuntimeError('ORA-00904: "BUD_PALLET_LOCATION": invalid identifier')
        return super().execute(sql, binds)


class TestOracleEcommStore(unittest.TestCase):
    def test_full_refresh_and_raw_mapping(self):
        now = datetime(2026, 8, 12, 9, 0)
        conn = FakeConnection(FakeCursor(now, [head()], [detail()]))
        store = OracleEcommStore(lambda _settings: conn, settings_factory=settings, now_fn=lambda: now)

        snapshot = store.refresh()
        raw = store.to_raw_dataframe(snapshot)

        self.assertEqual(snapshot.refresh_kind, "full")
        self.assertEqual(len(snapshot.heads), 1)
        self.assertEqual(raw.iloc[0]["lmp"], "MEEST UA")
        self.assertEqual(raw.iloc[0]["rendszam_raw"], "ABC-123")
        self.assertAlmostEqual(raw.iloc[0]["am_raw"], 46246.3541666667)
        self.assertTrue(conn.closed)

    def test_oracle_detail_maps_to_bud_pallets_contract(self):
        now = datetime(2026, 8, 12, 9, 0)
        row = detail(
            DEST_COUNTRY_NAME="AT AP WIEN",
            DELIVERY_PLATE_NUMBER="AA-BB-123",
            GLABS_ID="HG16009-CF",
            BUD_PALLET_DUMP_NUMBER="7",
            PLT_OUT=2,
            BUD_PALLET_LOCATION="E1",
            BUD_PALLET_CHECK_IN=datetime(2026, 8, 12, 8, 15),
            BUD_PALLET_REMARK="piheno 12:30-ig",
            BUD_PALLET_RAMP="16 RAMP / Alfonz",
            ITEM_DEPARTURE=datetime(2026, 8, 12, 8, 45),
        )
        store = OracleEcommStore(
            lambda _settings: FakeConnection(FakeCursor(now, [head()], [row])),
            settings_factory=settings,
            now_fn=lambda: now,
        )

        pallets = store.to_pallets_dataframe(store.refresh())

        self.assertEqual(pallets.iloc[0]["bud_lmp"], "AT AP WIEN")
        self.assertEqual(pallets.iloc[0]["plate"], "AA-BB-123")
        self.assertEqual(pallets.iloc[0]["glabs_id"], "HG16009-CF")
        self.assertEqual(pallets.iloc[0]["dump_number"], "7")
        self.assertEqual(pallets.iloc[0]["pallets_issued"], 2.0)
        self.assertEqual(pallets.iloc[0]["location"], "E1")
        self.assertEqual(pallets.iloc[0]["rest_raw"], "piheno 12:30-ig")
        self.assertEqual(pallets.iloc[0]["load_note"], "16 RAMP / Alfonz")

    def test_bud_pallet_remark_is_note_fallback_when_ramp_is_empty(self):
        now = datetime(2026, 8, 12, 9, 0)
        row = detail(BUD_PALLET_REMARK="PLOMBA:009219", BUD_PALLET_RAMP=None)
        store = OracleEcommStore(
            lambda _settings: FakeConnection(FakeCursor(now, [head()], [row])),
            settings_factory=settings,
            now_fn=lambda: now,
        )

        pallets = store.to_pallets_dataframe(store.refresh())

        self.assertEqual(pallets.iloc[0]["eta_note"], "PLOMBA:009219")
        self.assertEqual(pallets.iloc[0]["load_note"], "PLOMBA:009219")

    def test_legacy_detail_view_without_new_bud_columns_remains_readable(self):
        now = datetime(2026, 8, 12, 9, 0)
        store = OracleEcommStore(
            lambda _settings: FakeConnection(LegacyDetailCursor(now, [head()], [detail()])),
            settings_factory=settings,
            now_fn=lambda: now,
        )

        snapshot = store.refresh()
        pallets = store.to_pallets_dataframe(snapshot)

        self.assertEqual(len(pallets), 1)
        self.assertIsNone(pallets.iloc[0]["location"])
        self.assertIsNone(pallets.iloc[0]["load_note"])

    def test_temu_head_group_restores_excel_style_lmp_marker(self):
        now = datetime(2026, 8, 12, 9, 0)
        rows = [
            detail(10, CLIENT_NAME="SI POST"),
            detail(11, CLIENT_NAME="TEMU SI BOXN"),
        ]
        temu_head = head(
            HEAD_MAIN_CLIENT_GROUP="TEMU",
            SUM_OF_COLLIS=4,
            SUM_OF_WEIGHT_KG=20,
            SUM_OF_PARCELS=40,
        )
        store = OracleEcommStore(
            lambda _settings: FakeConnection(FakeCursor(now, [temu_head], rows)),
            settings_factory=settings,
            now_fn=lambda: now,
        )

        raw = store.to_raw_dataframe(store.refresh())

        self.assertEqual(raw["lmp"].tolist(), ["TEMU SI POST", "TEMU SI BOXN"])

    def test_non_temu_head_keeps_detail_client_name(self):
        now = datetime(2026, 8, 12, 9, 0)
        store = OracleEcommStore(
            lambda _settings: FakeConnection(
                FakeCursor(now, [head(HEAD_MAIN_CLIENT_GROUP="MEEST")], [detail(CLIENT_NAME="MEEST UA")])
            ),
            settings_factory=settings,
            now_fn=lambda: now,
        )

        raw = store.to_raw_dataframe(store.refresh())

        self.assertEqual(raw.iloc[0]["lmp"], "MEEST UA")

    def test_incremental_refresh_upserts_and_keeps_unchanged_rows(self):
        first_time = datetime(2026, 8, 12, 9, 0)
        second_time = first_time + timedelta(minutes=10)
        state = {"run": 0}

        def factory(_settings):
            state["run"] += 1
            if state["run"] == 1:
                cursor = FakeCursor(first_time, [head()], [detail()])
            else:
                cursor = FakeCursor(
                    second_time,
                    [head(2, "999-00000000", changed=second_time)],
                    [detail(20, 2, "999-00000000", changed=second_time)],
                )
            return FakeConnection(cursor)

        clock = {"now": first_time}
        store = OracleEcommStore(factory, settings_factory=settings, now_fn=lambda: clock["now"])
        store.refresh()
        clock["now"] = second_time
        snapshot = store.refresh()

        self.assertEqual(snapshot.refresh_kind, "incremental")
        self.assertEqual(set(snapshot.heads), {1, 2})
        self.assertEqual(set(snapshot.details), {10, 20})
        self.assertEqual(snapshot.changed_heads, 1)
        self.assertEqual(snapshot.changed_details, 1)

    def test_failed_refresh_preserves_previous_snapshot(self):
        now = datetime(2026, 8, 12, 9, 0)
        calls = {"count": 0}

        def factory(_settings):
            calls["count"] += 1
            if calls["count"] == 1:
                return FakeConnection(FakeCursor(now, [head()], [detail()]))
            return FakeConnection(FakeCursor(now, [], []), fail=True)

        store = OracleEcommStore(factory, settings_factory=settings, now_fn=lambda: now)
        good = store.refresh()
        with self.assertRaises(OracleSourceError):
            store.refresh()
        self.assertIs(store.snapshot(), good)
        self.assertEqual(len(store.snapshot().details), 1)

    def test_orphan_detail_rejects_candidate(self):
        now = datetime(2026, 8, 12, 9, 0)
        conn = FakeConnection(FakeCursor(now, [head()], [detail(head_id=999)]))
        store = OracleEcommStore(lambda _settings: conn, settings_factory=settings, now_fn=lambda: now)
        with self.assertRaisesRegex(OracleSourceError, "Árva detail"):
            store.refresh()
        self.assertIsNone(store.snapshot())

    def test_zero_quantity_detail_is_warning_not_failure(self):
        now = datetime(2026, 8, 12, 9, 0)
        zero = detail(DETAIL_COLLIS=0, DETAIL_WEIGHT_KG=0, DETAIL_PARCELS=0)
        store = OracleEcommStore(
            lambda _settings: FakeConnection(FakeCursor(now, [head()], [zero])),
            settings_factory=settings,
            now_fn=lambda: now,
        )
        snapshot = store.refresh()
        self.assertIn("minden mennyisége nulla", snapshot.quality_warnings[0])
        self.assertEqual(snapshot.zero_quantity_details, 1)
        self.assertEqual(snapshot.head_detail_mismatches, 1)

    def test_source_mode_defaults_and_alias(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLOW_ECOMM_SOURCE", None)
            self.assertEqual(get_source_mode(), "excel")
        with patch.dict(os.environ, {"FLOW_ECOMM_SOURCE": "comparison"}):
            self.assertEqual(get_source_mode(), "shadow")


if __name__ == "__main__":
    unittest.main()
