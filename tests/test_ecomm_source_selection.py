import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

import data_reader
import oracle_ecomm


def raw_row(**overrides):
    now_serial = (datetime.now() - datetime(1899, 12, 30)).total_seconds() / 86400.0
    row = {column: None for column in oracle_ecomm.RAW_COLUMNS}
    row.update({
        "lmp": "MEEST UA",
        "awb": "123-45678901",
        "boxes": 2,
        "weight": 10,
        "parcel_count": 20,
        "carrier_k": "AS Cargo",
        "status_n": "Felvéve",
        "am_raw": now_serial,
        "rendszam_raw": "ABC-123",
    })
    row.update(overrides)
    return row


class TestEcommSourceSelection(unittest.TestCase):
    def test_oracle_mode_returns_oracle_contract(self):
        frame = pd.DataFrame([raw_row()])
        with patch.object(oracle_ecomm, "get_source_mode", return_value="oracle"), patch.object(
            oracle_ecomm, "refresh_raw", return_value=(frame, {"oracle_head_count": 1})
        ):
            raw, meta = data_reader._read_ecomm_raw()
        self.assertEqual(len(raw), 1)
        self.assertEqual(meta["ecomm_source_mode"], "oracle")
        self.assertEqual(meta["oracle_head_count"], 1)

    def test_shadow_failure_keeps_excel_active(self):
        frame = pd.DataFrame([raw_row()])
        with patch.object(oracle_ecomm, "get_source_mode", return_value="shadow"), patch.object(
            data_reader, "_read_ecomm_excel_raw", return_value=(frame, {"ecomm_source_type": "excel"})
        ), patch.object(oracle_ecomm, "refresh_raw", side_effect=RuntimeError("offline")):
            raw, meta = data_reader._read_ecomm_raw()
        self.assertEqual(len(raw), 1)
        self.assertEqual(meta["ecomm_source_type"], "excel")
        self.assertFalse(meta["oracle_shadow_ok"])
        self.assertIn("offline", meta["oracle_shadow_error"])

    def test_oracle_raw_flows_through_existing_active_item_model(self):
        frame = pd.DataFrame([raw_row()])
        with patch.object(data_reader, "_read_ecomm_raw", return_value=(frame, {
            "ecomm_source_type": "oracle",
            "ecomm_source_mode": "oracle",
            "oracle_last_refresh": datetime.now().isoformat(),
        })):
            active, _ar_awbs, kpi, ulds = data_reader._read_ecomm()
        self.assertEqual(len(active), 1)
        self.assertEqual(active.iloc[0]["awb"], "123-45678901")
        self.assertEqual(active.iloc[0]["rendszam"], "ABC-123")
        self.assertEqual(kpi["ecomm_source_type"], "oracle")
        self.assertEqual(ulds, [])

    def test_oracle_fast_uld_uses_same_raw_contract(self):
        frame = pd.DataFrame([raw_row(uld_raw="PMC12345AB", ad_raw=None)])
        with patch.object(oracle_ecomm, "get_source_mode", return_value="oracle"), patch.object(
            oracle_ecomm, "current_raw", return_value=(frame, {"ecomm_source_type": "oracle"})
        ):
            ulds, meta = data_reader.load_uld_data_fast()
        self.assertEqual(len(ulds), 1)
        self.assertEqual(ulds[0]["uld_number"], "PMC12345AB")
        self.assertEqual(meta["ecomm_source_type"], "oracle")

    def test_oracle_pallets_flow_through_existing_outbound_contract(self):
        pallets = pd.DataFrame([{
            "bud_lmp": "AT AP WIEN",
            "awb": "123-45678901",
            "plate": "AA-BB-123",
            "eta_note": "PLOMBA:009219",
            "glabs_id": "HG16009-CF",
            "boxes": 7.0,
            "weight": 167.3,
            "driver_checkin": datetime.now(),
            "issued_time": None,
            "rest_raw": "piheno 23:59-ig",
            "kiad_status": "Kiadhato",
            "pallets_issued": 2.0,
            "location": "E1",
            "load_note": "16 RAMP / Alfonz",
            "dump_number": "7",
        }])
        with patch.object(oracle_ecomm, "get_source_mode", return_value="oracle"), patch.object(
            oracle_ecomm, "current_pallets", return_value=(pallets, {
                "oracle_pallet_rows": 1,
                "oracle_pallet_locations": 1,
            })
        ):
            result = data_reader._read_pallets()

        self.assertEqual(result.iloc[0]["location"], "E1")
        self.assertEqual(result.iloc[0]["pallets_issued"], 2.0)
        self.assertTrue(result.iloc[0]["rest_declared"])
        self.assertEqual(result.attrs["pallets_source_type"], "oracle")


if __name__ == "__main__":
    unittest.main()
