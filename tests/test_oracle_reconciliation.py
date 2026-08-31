import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from oracle_reconciliation import comparison_key, prepare_oracle_details, reconcile, write_report_data


def detail(**overrides):
    row = {
        "REF_E_COMM_ID": 1,
        "AIR_WAYBILL": "123-45678901",
        "E_COMM_DETAIL_ID": 10,
        "CLIENT_NAME": "TEMU HU",
        "GHA_COMPANY_NAME": "Celebi",
        "EXPECTED_ARRIVAL": datetime(2026, 8, 12, 8, 0),
        "PARCEL_STATUS_NAME": "Felvéve",
        "DETAIL_COLLIS": 10,
        "DETAIL_WEIGHT_KG": 125.5,
        "DETAIL_PARCELS": 100,
        "ULD_NUMBER": "PMC12345MU",
        "ULD_RETURN_DATE": None,
        "ATA": datetime(2026, 8, 12, 8, 5),
        "NOA": datetime(2026, 8, 12, 8, 30),
        "GOODS_TRANSFER_DATE": datetime(2026, 8, 12, 9, 0),
        "TRANSFER_PLATE_NUMBER": "ABC-123",
        "CUSTOMS_CLEARANCE_START": None,
        "CUSTOMS_CLEARANCE_FINISH": None,
        "ITEM_DEPARTURE": None,
        "LAST_CHANGE_DATE": datetime(2026, 8, 12, 10, 0),
    }
    row.update(overrides)
    return row


def head(**overrides):
    row = {
        "E_COMM_ID": 1,
        "AIR_WAYBILL": "123-45678901",
        "SUM_OF_COLLIS": 10,
        "SUM_OF_WEIGHT_KG": 125.5,
        "SUM_OF_PARCELS": 100,
        "HEAD_MAIN_CLIENT_GROUP": "TEMU",
        "CREATION_DATE": datetime(2026, 8, 12, 7, 0),
        "LAST_CHANGE_DATE": datetime(2026, 8, 12, 10, 0),
    }
    row.update(overrides)
    return row


def serial(value):
    return (value - datetime(1899, 12, 30)).total_seconds() / 86400


def excel_row(**overrides):
    row = {
        "awb": "123-45678901",
        "lmp": "TEMU HU",
        "boxes": 10,
        "weight": 125.5,
        "parcel_count": 100,
        "carrier_k": "Celebi",
        "expected_arrival_raw": serial(datetime(2026, 8, 12, 8, 0)),
        "status_n": "Felvéve",
        "uld_raw": "PMC12345MU",
        "ad_raw": None,
        "ak_raw": serial(datetime(2026, 8, 12, 8, 5)),
        "noa_raw": serial(datetime(2026, 8, 12, 8, 30)),
        "am_raw": serial(datetime(2026, 8, 12, 9, 0)),
        "rendszam_raw": "ABC-123",
        "aq_raw": None,
        "ar_raw": None,
        "departure_raw": None,
    }
    row.update(overrides)
    row["comparison_key"] = comparison_key(row["awb"], row["lmp"])
    return row


class OracleReconciliationTests(unittest.TestCase):
    def test_equal_detail_is_fully_matching(self):
        report = reconcile([excel_row()], [head()], [detail()])
        self.assertEqual(report["summary"]["matched_keys"], 1)
        self.assertEqual(report["summary"]["fully_matching_keys"], 1)
        self.assertEqual(report["summary"]["mismatch_cells"], 0)
        self.assertEqual(report["summary"]["head_quantity_check_failures"], 0)

    def test_mismatch_and_unmatched_are_reported(self):
        other = detail(
            REF_E_COMM_ID=2,
            E_COMM_DETAIL_ID=20,
            AIR_WAYBILL="999-00000000",
            CLIENT_NAME="OTHER",
        )
        report = reconcile(
            [excel_row(weight=100), excel_row(awb="111-00000000", lmp="ONLY EXCEL")],
            [head(), head(E_COMM_ID=2, AIR_WAYBILL="999-00000000")],
            [detail(), other],
        )
        self.assertEqual(report["summary"]["keys_with_mismatch"], 1)
        self.assertEqual(report["summary"]["unmatched_excel_keys"], 1)
        self.assertEqual(report["summary"]["unmatched_oracle_keys"], 1)
        self.assertIn("Súly", {row["field"] for row in report["mismatches"]})

    def test_head_detail_control_detects_double_count_risk(self):
        _prepared, checks = prepare_oracle_details(
            [head()],
            [detail(), detail(E_COMM_DETAIL_ID=11, CLIENT_NAME="TEMU AT")],
        )
        self.assertFalse(checks[0]["WEIGHT_KG_MATCH"])
        self.assertEqual(checks[0]["DETAIL_ROWS"], 2)

    def test_date_tolerance_accepts_excel_rounding(self):
        report = reconcile(
            [excel_row(ak_raw=serial(datetime(2026, 8, 12, 8, 5)))],
            [head()],
            [detail(ATA=datetime(2026, 8, 12, 8, 5, 45))],
        )
        self.assertNotIn("ATA", {row["field"] for row in report["mismatches"]})

    def test_dup_is_not_compared_as_oracle_lifecycle_status(self):
        report = reconcile(
            [excel_row(status_n="DUP")],
            [head()],
            [detail(PARCEL_STATUS_NAME="Kiadva")],
        )
        self.assertNotIn("Státusz", {row["field"] for row in report["mismatches"]})

    def test_plate_candidates_profile_both_oracle_fields(self):
        report = reconcile(
            [excel_row(rendszam_raw="ABC-123")],
            [head()],
            [detail(TRANSFER_PLATE_NUMBER="ABC-123", DELIVERY_PLATE_NUMBER="XYZ-789")],
        )
        profiles = {row["oracle_field"]: row for row in report["plate_candidates"]}
        self.assertEqual(profiles["TRANSFER_PLATE_NUMBER"]["exact_matches"], 1)
        self.assertEqual(profiles["DELIVERY_PLATE_NUMBER"]["exact_matches"], 0)

    def test_report_writer_emits_json_and_csv(self):
        report = reconcile([excel_row()], [head()], [detail()])
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report_data(report, tmp)
            self.assertTrue(path.exists())
            self.assertTrue((Path(tmp) / "mismatches.csv").exists())
            self.assertTrue((Path(tmp) / "head_detail_checks.csv").exists())
            self.assertTrue((Path(tmp) / "plate_candidates.csv").exists())


if __name__ == "__main__":
    unittest.main()
