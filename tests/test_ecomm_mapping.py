import unittest

import data_reader


class EcommColumnMappingTests(unittest.TestCase):
    _CURRENT_HEADER_BY_INDEX = {
        1: "Ügyfél",
        3: "AWB",
        4: "Colli",
        5: "Súly",
        6: "Parcel",
        9: "POE",
        10: "GHA",
        11: "Várhatóérk.",
        13: "Státusz",
        14: "=",
        15: "Részben(kg)",
        21: "ULD azonosító",
        29: "Vissza",
        33: "PRE-ALERT",
        35: "ATA",
        36: "NOA",
        37: "Áttár",
        39: "Rendszám",
        41: "Vámkez.k.",
        42: "Vámkez.v.",
        44: "Tétel indulás",
    }

    @classmethod
    def _current_headers(cls):
        headers = [None] * 47
        for index, label in cls._CURRENT_HEADER_BY_INDEX.items():
            headers[index] = label
        return headers

    def test_current_live_mapping_without_blank_t_column(self):
        resolved = data_reader._resolve_ecomm_column_indices(self._current_headers())

        self.assertEqual(resolved, data_reader._ECOMM_COLUMN_INDEX)
        self.assertEqual(
            data_reader._ECOMM_COLS,
            [1, 3, 4, 5, 6, 9, 10, 11, 13, 14, 15, 21, 29, 33, 35, 36, 37, 39, 41, 42, 44],
        )
        self.assertEqual(data_reader._ECOMM_ULD_COLS, [1, 3, 10, 21, 29, 37])

    def test_header_driven_mapping_also_handles_blank_t_insertion_schema(self):
        headers = self._current_headers()
        headers.insert(19, None)
        resolved = data_reader._resolve_ecomm_column_indices(headers)

        expected = {
            name: (index + 1 if index >= 19 else index)
            for name, index in data_reader._ECOMM_COLUMN_INDEX.items()
        }
        self.assertEqual(resolved, expected)

    def test_duplicate_expected_arrival_prefers_operational_l_column(self):
        headers = self._current_headers()
        headers[24] = "Várhatóérk."

        resolved = data_reader._resolve_ecomm_column_indices(headers)

        self.assertEqual(resolved["expected_arrival_raw"], 11)

    def test_header_mapping_accepts_legacy_customs_abbreviation_spacing(self):
        headers = self._current_headers()
        headers[41] = "Vámkez k."
        headers[42] = "Vámkez v."

        resolved = data_reader._resolve_ecomm_column_indices(headers)

        self.assertEqual(resolved["aq_raw"], 41)
        self.assertEqual(resolved["ar_raw"], 42)

    def test_header_mapping_accepts_customs_abbreviations_without_final_dot(self):
        headers = self._current_headers()
        headers[41] = "Vámkez.k"
        headers[42] = "Vámkez v"

        resolved = data_reader._resolve_ecomm_column_indices(headers)

        self.assertEqual(resolved["aq_raw"], 41)
        self.assertEqual(resolved["ar_raw"], 42)

    def test_header_mapping_accepts_current_customs_end_label(self):
        headers = self._current_headers()
        headers[42] = "Vámkez.vége"

        resolved = data_reader._resolve_ecomm_column_indices(headers)

        self.assertEqual(resolved["ar_raw"], 42)

    def test_header_row_anchor_does_not_require_fixed_b_and_d_positions(self):
        headers = self._current_headers()
        headers.insert(0, "Új technikai oszlop")

        self.assertTrue(data_reader._is_ecomm_header_row(headers))
        resolved = data_reader._resolve_ecomm_column_indices(headers)
        self.assertEqual(resolved["lmp"], 2)
        self.assertEqual(resolved["awb"], 4)
        self.assertEqual(resolved["aq_raw"], 42)
        self.assertEqual(resolved["ar_raw"], 43)

    def test_missing_semantic_header_fails_loudly(self):
        headers = self._current_headers()
        headers[44] = None

        with self.assertRaisesRegex(ValueError, "departure_raw .*Tétel indulás"):
            data_reader._resolve_ecomm_column_indices(headers)


if __name__ == "__main__":
    unittest.main()
