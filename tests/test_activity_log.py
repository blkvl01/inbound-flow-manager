import json
import os
import shutil
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import activity_log


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self._old_log_dir = os.environ.get("FLOW_ACTIVITY_LOG_DIR")
        self._tmp = Path(__file__).resolve().parents[1] / "_codex_log_tests" / uuid.uuid4().hex
        self._tmp.mkdir(parents=True, exist_ok=True)
        os.environ["FLOW_ACTIVITY_LOG_DIR"] = str(self._tmp)

    def tearDown(self):
        if self._old_log_dir is None:
            os.environ.pop("FLOW_ACTIVITY_LOG_DIR", None)
        else:
            os.environ["FLOW_ACTIVITY_LOG_DIR"] = self._old_log_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_log_event_appends_jsonl_with_user_and_time(self):
        activity_log.log_event("store_toggle", {"awb": "X", "stored": True})
        activity_log.log_event("note_add", {"awb": "Y"})

        today = datetime.now().strftime("%Y-%m-%d")
        path = activity_log._day_file(today)
        self.assertTrue(path.exists())
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["action"], "store_toggle")
        self.assertEqual(first["detail"], {"awb": "X", "stored": True})
        self.assertTrue(first["user"])
        datetime.fromisoformat(first["ts"])

    def test_read_events_newest_first_with_filters(self):
        activity_log.log_event("a_action", {"k": 1})
        activity_log.log_event("b_action", {"k": 2})
        today = datetime.now().strftime("%Y-%m-%d")

        events = activity_log.read_events(today)
        self.assertEqual([e["action"] for e in events], ["b_action", "a_action"])

        filtered = activity_log.read_events(today, search="b_action")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["action"], "b_action")

        none = activity_log.read_events(today, user="nincs-ilyen-user")
        self.assertEqual(none, [])

    def test_list_log_days_and_users(self):
        activity_log.log_event("x")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertIn(today, activity_log.list_log_days())
        users = activity_log.list_users(today)
        self.assertEqual(users, [os.environ.get("USERNAME", "unknown")])

    def test_cleanup_old_removes_only_past_retention(self):
        old_day = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
        recent_day = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        for day in (old_day, recent_day):
            activity_log._day_file(day).write_text('{"action":"x"}\n', encoding="utf-8")

        activity_log.cleanup_old(days=30)
        self.assertFalse(activity_log._day_file(old_day).exists())
        self.assertTrue(activity_log._day_file(recent_day).exists())

    def test_log_event_never_raises_on_bad_detail(self):
        activity_log.log_event("weird", {"obj": object()})
        today = datetime.now().strftime("%Y-%m-%d")
        events = activity_log.read_events(today)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0].get("detail"), str)


if __name__ == "__main__":
    unittest.main()
