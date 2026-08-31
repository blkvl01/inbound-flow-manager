import os
import shutil
import unittest
import uuid
from pathlib import Path

import pandas as pd

import overrides_manager


class OverridesManagerTests(unittest.TestCase):
    def setUp(self):
        self._old_shared = os.environ.get("FLOW_SHARED_STATE_DIR")
        self._tmp = Path(__file__).resolve().parents[1] / "_codex_ovr_tests" / uuid.uuid4().hex
        self._tmp.mkdir(parents=True, exist_ok=True)
        os.environ["FLOW_SHARED_STATE_DIR"] = str(self._tmp)
        overrides_manager._cache["mtime"] = None

    def tearDown(self):
        if self._old_shared is None:
            os.environ.pop("FLOW_SHARED_STATE_DIR", None)
        else:
            os.environ["FLOW_SHARED_STATE_DIR"] = self._old_shared
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _df(self):
        return pd.DataFrame([
            {"awb": "AWB1", "lmp": "MEEST", "weight": 100.0, "is_shippable": False},
            {"awb": "AWB2", "lmp": "4PX", "weight": 200.0, "is_shippable": True},
        ])

    def test_set_override_coerces_types_and_records_author(self):
        ok, err = overrides_manager.set_override("AWB1", "weight", "123,5")
        self.assertTrue(ok, err)
        ok, err = overrides_manager.set_override("AWB1", "is_shippable", "igen")
        self.assertTrue(ok, err)

        entry = overrides_manager.get_all_overrides()["AWB1"]
        self.assertEqual(entry["fields"]["weight"], 123.5)
        self.assertIs(entry["fields"]["is_shippable"], True)
        self.assertTrue(entry["set_by"])
        self.assertTrue(entry["set_at"])

    def test_unknown_field_and_bad_value_rejected(self):
        ok, err = overrides_manager.set_override("AWB1", "awb", "x")
        self.assertFalse(ok)
        self.assertIn("Nem felülbírálható", err)
        ok, err = overrides_manager.set_override("AWB1", "weight", "nem szám")
        self.assertFalse(ok)
        self.assertIn("Érvénytelen", err)

    def test_apply_overrides_changes_df_and_backs_up_original(self):
        overrides_manager.set_override("AWB1", "lmp", "TEMU")
        df = overrides_manager.apply_overrides_to_df(self._df())
        self.assertEqual(df.loc[df["awb"] == "AWB1", "lmp"].iloc[0], "TEMU")
        self.assertEqual(df.loc[df["awb"] == "AWB1", "lmp__orig"].iloc[0], "MEEST")
        # untouched row keeps its value
        self.assertEqual(df.loc[df["awb"] == "AWB2", "lmp"].iloc[0], "4PX")

    def test_clearing_override_restores_original_on_reapply(self):
        overrides_manager.set_override("AWB1", "lmp", "TEMU")
        df = overrides_manager.apply_overrides_to_df(self._df())
        self.assertEqual(df.loc[df["awb"] == "AWB1", "lmp"].iloc[0], "TEMU")

        self.assertTrue(overrides_manager.clear_override("AWB1", "lmp"))
        df2 = overrides_manager.apply_overrides_to_df(df)
        self.assertEqual(df2.loc[df2["awb"] == "AWB1", "lmp"].iloc[0], "MEEST")
        self.assertNotIn("AWB1", overrides_manager.get_all_overrides())

    def test_clear_whole_awb(self):
        overrides_manager.set_override("AWB1", "lmp", "TEMU")
        overrides_manager.set_override("AWB1", "weight", "1")
        self.assertTrue(overrides_manager.clear_override("AWB1"))
        self.assertEqual(overrides_manager.get_all_overrides(), {})

    def test_apply_handles_missing_awb_and_empty_df(self):
        overrides_manager.set_override("NINCS", "lmp", "X")
        df = overrides_manager.apply_overrides_to_df(self._df())
        self.assertNotIn("lmp__orig", df.columns)
        self.assertIsNone(overrides_manager.apply_overrides_to_df(None))


if __name__ == "__main__":
    unittest.main()
