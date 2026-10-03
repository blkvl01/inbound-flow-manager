"""Reader safety and parity at the binary/source-version boundaries."""
import io
import os
import shutil
import struct
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from pyxlsb.reader import BIFF12Reader

import data_reader as dr


def record(kind, payload=b""):
    # BIFF12 record IDs retain continuation bits; lengths use 7-bit groups.
    identity = kind.to_bytes(max(1, (kind.bit_length() + 7) // 8), "little")
    length = len(payload)
    groups = bytearray()
    while length >= 128:
        groups.append((length & 127) | 128)
        length >>= 7
    groups.append(length)
    return identity + groups + payload


def sheet(blob, strings=None):
    return SimpleNamespace(_reader=BIFF12Reader(io.BytesIO(blob)),
                           _data_offset=0, _stringtable=strings)


class SelectedBinaryReaderTests(unittest.TestCase):
    def test_all_supported_values_match_library_and_sparse_rows(self):
        kinds = dr.biff12
        values = [
            (kinds.BLANK, b""),
            (kinds.NUM, struct.pack("<i", (123 << 2) | 2)),
            (kinds.BOOLERR, b"\x07"),
            (kinds.BOOL, b"\x01"),
            (kinds.FLOAT, struct.pack("<d", 12.5)),
            (kinds.STRING, struct.pack("<I", 0)),
            (kinds.FORMULA_STRING, struct.pack("<I", 3) + "ÁWB".encode("utf-16le")),
            (kinds.FORMULA_FLOAT, struct.pack("<d", 42.25)),
            (kinds.FORMULA_BOOL, b"\x00"),
            (kinds.FORMULA_BOOLERR, b"\x0f"),
        ]
        blob = record(kinds.ROW, struct.pack("<I", 11))
        for column, (kind, value) in enumerate(values):
            blob += record(kind, struct.pack("<II", column, 0) + value)
        blob += record(kinds.FLOAT, struct.pack("<II", 99, 0) + struct.pack("<d", 999))
        blob += record(kinds.ROW, struct.pack("<I", 24999))
        blob += record(kinds.STRING, struct.pack("<III", 5, 0, 0))
        blob += record(kinds.SHEETDATA_END)
        indices = list(range(len(values)))
        actual = list(dr._iter_selected_xlsb_rows(sheet(blob, ["B2B"]), indices))
        expected = list(dr._iter_decoded_xlsb_rows(sheet(blob, ["B2B"]), indices))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0][1], [None, 123.0, "0x7", True, 12.5, "B2B", "ÁWB", 42.25, False, "0xf"])
        self.assertEqual(actual[1][0], 25000)

    def test_unused_values_are_not_decoded(self):
        blob = record(dr.biff12.ROW, struct.pack("<I", 0))
        blob += record(dr.biff12.STRING, struct.pack("<III", 99, 0, 123456))
        blob += record(dr.biff12.STRING, struct.pack("<III", 1, 0, 0))
        blob += record(dr.biff12.SHEETDATA_END)
        self.assertEqual(list(dr._iter_selected_xlsb_rows(sheet(blob, ["B2B"]), [1])), [(1, ["B2B"])])

    def test_truncation_and_invalid_headers_fail_instead_of_partial_snapshot(self):
        row = record(dr.biff12.ROW, struct.pack("<I", 0))
        malformed = [b"\x80", b"\x80\x80\x80\x80", b"\x00\x80",
                     row, row + b"\x05\x10\x00",
                     row + record(dr.biff12.FLOAT, b"\x00" * 8),
                     row + record(dr.biff12.FORMULA_STRING, struct.pack("<III", 1, 0, 99)),
                     row + record(dr.biff12.ROW, struct.pack("<I", 0))]
        for blob in malformed:
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                list(dr._iter_selected_xlsb_rows(sheet(blob), [1]))

    def test_oversize_falls_back_before_emitting_any_rows(self):
        blob = record(dr.biff12.ROW, struct.pack("<I", 7)) + record(dr.biff12.SHEETDATA_END)
        with patch.object(dr, "_XLSB_BUFFER_LIMIT", 1):
            self.assertEqual(list(dr._iter_selected_xlsb_rows(sheet(blob), [1])), [(8, [None])])


class SourceSnapshotTests(unittest.TestCase):
    def test_pallets_disables_preservation_and_closes_on_parse_failure(self):
        workbook = SimpleNamespace(close=unittest.mock.Mock())
        class BrokenWorkbook:
            def __getitem__(self, key):
                raise ValueError("broken sheet")
            close = workbook.close
        with patch.object(dr.oracle_ecomm, "get_source_mode", return_value="excel"), patch.object(dr, "_temp_copy", return_value="snapshot.xlsm"), patch.object(dr, "_snapshot_source_signature", return_value={}), patch.object(dr.openpyxl, "load_workbook", return_value=BrokenWorkbook()) as load, patch.object(dr.os, "unlink"):
            result = dr._read_pallets()
        self.assertTrue(result.attrs["pallets_read_error"])
        self.assertEqual(load.call_args.kwargs, {"read_only": True, "keep_vba": False, "keep_links": False, "data_only": True})
        workbook.close.assert_called_once()

    def test_fast_uld_retries_copy_if_live_version_changes(self):
        signatures = [{"mtime": 1.0, "size": 10}, {"mtime": 2.0, "size": 20}]
        with patch.object(dr.oracle_ecomm, "get_source_mode", return_value="excel"), patch.object(dr, "_source_age_minutes", return_value=0), patch.object(dr.os.path, "getmtime", return_value=1.0), patch.object(dr, "_file_source_signature", side_effect=signatures), patch.object(dr, "_read_uld_records_from_workbook", return_value=[]) as read, patch.object(dr, "_temp_copy", return_value="snapshot.xlsb") as copy, patch.object(dr, "_snapshot_source_signature", return_value={dr.ECOMM_FILE: {"mtime": 2.0, "size": 20}}), patch.object(dr.os, "unlink"):
            result, meta = dr.load_uld_data_fast()
        copy.assert_called_once()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(meta["_source_signature"][dr.ECOMM_FILE], {"mtime": 2.0, "size": 20})
        self.assertEqual(result, [])

    def test_copy_retries_changed_source_and_signature_keeps_copied_version(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.xlsb"
            source.write_bytes(b"first")
            original = shutil.copy2
            calls = []
            def change_after_copy(src, target):
                original(src, target)
                calls.append(target)
                if len(calls) == 1:
                    source.write_bytes(b"second version")
            with patch.object(dr.shutil, "copy2", side_effect=change_after_copy), patch.object(dr.time, "sleep"):
                copied = dr._temp_copy(str(source))
            try:
                self.assertEqual(len(calls), 2)
                self.assertEqual(Path(copied).read_bytes(), b"second version")
                signature = dr._snapshot_source_signature(str(source), copied)
                source.write_bytes(b"new third version")
                self.assertEqual(signature[str(source)]["size"], len(b"second version"))
                self.assertNotEqual(signature[str(source)], dr._file_source_signature(str(source)))
            finally:
                os.unlink(copied)

    def test_unstable_copy_removes_temp_and_raises(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.xlsb"
            source.write_bytes(b"first")
            original = shutil.copy2
            targets = []
            def always_change(src, target):
                original(src, target)
                targets.append(target)
                source.write_bytes(source.read_bytes() + b"x")
            with patch.object(dr.shutil, "copy2", side_effect=always_change), patch.object(dr.time, "sleep"):
                with self.assertRaises(OSError):
                    dr._temp_copy(str(source))
            self.assertFalse(Path(targets[-1]).exists())

    def test_full_read_reuses_all_raw_uld_evidence_once(self):
        now = datetime.now()
        epoch = datetime(1899, 12, 30)
        serial = lambda stamp: (stamp - epoch).total_seconds() / 86400
        def row(**values):
            result = {column: None for column in dr._ECOMM_NAMES}
            result.update(values)
            return result
        raw = pd.DataFrame([
            row(awb="123-45678901", lmp="B2B", status_n="Felvéve", am_raw=serial(now - timedelta(hours=1)), uld_raw="PMC12345AB"),
            row(awb="123-45678901", carrier_k="AS Cargo"),
            row(awb="", am_raw=serial(now - timedelta(days=30)), uld_raw="AKE12345AB", carrier_k="Menzies"),
            row(awb="old", am_raw=serial(now - timedelta(days=5)), uld_raw="PMC99999AB", ad_raw=serial(now - timedelta(days=4))),
        ])
        source_meta = {"_source_signature": {"copied.xlsb": {"mtime": 1.0, "size": 10}}}
        with patch.object(dr, "_read_ecomm_raw", return_value=(raw, source_meta)), patch.object(dr, "_compute_kpi", return_value={}), patch.object(dr, "_fast_uld_data_from_records", wraps=dr._fast_uld_data_from_records) as extract:
            _active, _cleared, kpi, ulds = dr._read_ecomm()
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(ulds[0]["gha"], "AS Cargo")
        self.assertEqual(kpi["_uld_meta"]["uld_archive"][0]["uld_number"], "AKE12345AB")
        self.assertIn("PMC99999AB", kpi["_uld_meta"]["uld_returned"])
        self.assertIn("PMC12345AB", kpi["_uld_meta"]["uld_times_full"])
        self.assertEqual(kpi["_source_signature"], source_meta["_source_signature"])

    def test_active_normal_partial_and_en_route_fields_stay_exact(self):
        now = datetime.now()
        epoch = datetime(1899, 12, 30)
        serial = lambda stamp: (stamp - epoch).total_seconds() / 86400
        def row(awb, **values):
            result = {column: None for column in dr._ECOMM_NAMES}
            result.update(awb=awb, lmp="B2B", status_n="Felvéve", weight=20.5,
                          boxes=4, am_raw=serial(now - timedelta(hours=1)))
            result.update(values)
            return result
        raw = pd.DataFrame([
            row("123-00000001"),
            row("123-00000002", status_n="Részben", ai_raw=serial(now - timedelta(hours=2)), partial_boxes_raw=2, partial_weight_raw=7.25),
            row("123-00000003", am_raw=serial(now - timedelta(minutes=5))),
            row("123-00000004", status_n="Kiadható"),
            row("123-00000005", aq_raw=serial(now)),
            row("123-00000006", am_raw=serial(now - timedelta(hours=25))),
        ])
        with patch.object(dr, "_read_ecomm_raw", return_value=(raw, {})), patch.object(dr, "_compute_kpi", return_value={}):
            active, _cleared, _kpi, _ulds = dr._read_ecomm()
        by_awb = {item["awb"]: item for item in active.to_dict("records")}
        self.assertEqual(set(by_awb), {"123-00000001", "123-00000002", "123-00000003"})
        self.assertEqual((by_awb["123-00000001"]["weight"], by_awb["123-00000001"]["boxes"]), (20.5, 4))
        self.assertEqual((by_awb["123-00000002"]["weight"], by_awb["123-00000002"]["boxes"]), (7.25, 2))
        self.assertTrue(by_awb["123-00000002"]["is_partial"])
        self.assertTrue(by_awb["123-00000003"]["is_en_route"])
        self.assertFalse(by_awb["123-00000001"]["is_en_route"])


if __name__ == "__main__":
    unittest.main()
