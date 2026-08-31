import os
import shutil
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import notes_manager


class NotesManagerTests(unittest.TestCase):
    def setUp(self):
        self._old_shared = os.environ.get("FLOW_SHARED_STATE_DIR")
        self._tmp = Path(__file__).resolve().parents[1] / "_codex_notes_tests" / uuid.uuid4().hex
        self._tmp.mkdir(parents=True, exist_ok=True)
        os.environ["FLOW_SHARED_STATE_DIR"] = str(self._tmp)
        notes_manager._cache["mtime"] = None

    def tearDown(self):
        if self._old_shared is None:
            os.environ.pop("FLOW_SHARED_STATE_DIR", None)
        else:
            os.environ["FLOW_SHARED_STATE_DIR"] = self._old_shared
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_add_note_records_author_and_time(self):
        note = notes_manager.add_note("123-45678901", "  sérült csomag  ")
        self.assertIsNotNone(note)
        self.assertEqual(note["text"], "sérült csomag")
        self.assertTrue(note["id"])
        self.assertTrue(note["by"])
        datetime.fromisoformat(note["at"])  # parses

        notes = notes_manager.get_notes("123-45678901")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["id"], note["id"])

    def test_multiple_notes_accumulate_in_order(self):
        notes_manager.add_note("AWB1", "első")
        notes_manager.add_note("AWB1", "második")
        texts = [n["text"] for n in notes_manager.get_notes("AWB1")]
        self.assertEqual(texts, ["első", "második"])

    def test_empty_text_or_awb_rejected(self):
        self.assertIsNone(notes_manager.add_note("", "valami"))
        self.assertIsNone(notes_manager.add_note("AWB1", "   "))
        self.assertEqual(notes_manager.get_all_notes(), {})

    def test_text_truncated_to_max_len(self):
        note = notes_manager.add_note("AWB1", "x" * 1000)
        self.assertEqual(len(note["text"]), notes_manager.MAX_NOTE_LEN)

    def test_delete_note_removes_entry_and_empty_awb_key(self):
        n1 = notes_manager.add_note("AWB1", "egy")
        n2 = notes_manager.add_note("AWB1", "kettő")
        self.assertTrue(notes_manager.delete_note("AWB1", n1["id"]))
        self.assertEqual([n["id"] for n in notes_manager.get_notes("AWB1")], [n2["id"]])
        self.assertTrue(notes_manager.delete_note("AWB1", n2["id"]))
        self.assertNotIn("AWB1", notes_manager.get_all_notes())
        self.assertFalse(notes_manager.delete_note("AWB1", "nincs"))

    def test_cleanup_keeps_active_stored_and_recent(self):
        notes_manager.add_note("ACTIVE", "marad")
        notes_manager.add_note("STORED", "marad")
        notes_manager.add_note("FRESH-ORPHAN", "friss, marad")
        old = notes_manager.add_note("OLD-ORPHAN", "régi, törlődik")

        # age the orphan note past the 48h grace
        with notes_manager._locked_data() as data:
            data["OLD-ORPHAN"][0]["at"] = (datetime.now() - timedelta(hours=72)).isoformat()
        self.assertIsNotNone(old)

        removed = notes_manager.cleanup_missing({"ACTIVE"}, {"STORED"})
        self.assertEqual(removed, 1)
        remaining = set(notes_manager.get_all_notes().keys())
        self.assertEqual(remaining, {"ACTIVE", "STORED", "FRESH-ORPHAN"})

    def test_cleanup_noop_when_no_keep_sets(self):
        notes_manager.add_note("AWB1", "x")
        self.assertEqual(notes_manager.cleanup_missing(set(), set()), 0)
        self.assertIn("AWB1", notes_manager.get_all_notes())


if __name__ == "__main__":
    unittest.main()
