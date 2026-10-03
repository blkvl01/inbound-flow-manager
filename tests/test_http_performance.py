import gzip
import json
import tempfile
import threading
import unittest
from pathlib import Path

from flask import Flask, Response, send_file

from http_performance import install_http_performance


class HttpPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.server = Flask(__name__)
        self.calls = 0
        self.entered = threading.Event()
        self.finish = threading.Event()
        self.finish.set()

        @self.server.post("/_dash-update-component")
        def action():
            self.calls += 1
            self.entered.set()
            self.finish.wait(3)
            return Response(json.dumps({"stored": True, "count": self.calls}), mimetype="application/json")

        install_http_performance(self.server)
        self.body = json.dumps({"output": "store-action.data", "changedPropIds": ['{"index":"123","type":"store-btn"}.n_clicks']})

    def post(self, token, body=None):
        with self.server.test_client() as client:
            return client.post("/_dash-update-component", data=body or self.body,
                               content_type="application/json", headers={"X-Flow-Action": token})

    def test_retry_replays_completed_toggle_but_new_click_can_toggle_again(self):
        first = self.post("click-one")
        replay = self.post("click-one")
        self.assertEqual(first.data, replay.data)
        self.assertEqual(self.calls, 1)
        self.post("click-two")
        self.assertEqual(self.calls, 2)

    def test_concurrent_retry_waits_for_first_operation(self):
        self.finish.clear()
        results = []
        first = threading.Thread(target=lambda: results.append(self.post("concurrent")))
        first.start()
        self.assertTrue(self.entered.wait(2))
        retry = threading.Thread(target=lambda: results.append(self.post("concurrent")))
        retry.start()
        self.finish.set()
        first.join(3)
        retry.join(3)
        self.assertEqual(len(results), 2)
        self.assertEqual(self.calls, 1)
        self.assertEqual(results[0].data, results[1].data)

    def test_token_reuse_with_different_body_is_rejected(self):
        self.post("collision")
        changed = json.dumps({"output": "store-action.data", "changedPropIds": ["different.n_clicks"]})
        response = self.post("collision", changed)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.calls, 1)

    def test_static_compression_revalidates_and_keeps_range_responses_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "style.css"
            original = b"body { color: white; background: black; }\n" * 200
            path.write_bytes(original)

            @self.server.get("/assets/style.css")
            def asset():
                return send_file(path, conditional=True)

            with self.server.test_client() as client:
                response = client.get("/assets/style.css", headers={"Accept-Encoding": "gzip"})
                self.assertEqual(gzip.decompress(response.data), original)
                self.assertLess(len(response.data), len(original) // 4)
                self.assertEqual(response.headers["Content-Encoding"], "gzip")
                self.assertIn("Accept-Encoding", response.headers["Vary"])
                self.assertNotIn("no-store", response.headers["Cache-Control"])
                validated = client.get("/assets/style.css", headers={"If-None-Match": response.headers["ETag"], "Accept-Encoding": "gzip"})
                self.assertEqual(validated.status_code, 304)
                ranged = client.get("/assets/style.css", headers={"Range": "bytes=0-9", "Accept-Encoding": "gzip"})
                self.assertEqual(ranged.status_code, 206)
                self.assertEqual(ranged.data, original[:10])
                self.assertNotIn("Content-Encoding", ranged.headers)
                response.close()
                validated.close()
                ranged.close()
                path.write_bytes(original + b"changed")
                changed = client.get("/assets/style.css", headers={"Accept-Encoding": "gzip"})
                self.assertEqual(gzip.decompress(changed.data), original + b"changed")
                changed.close()


if __name__ == "__main__":
    unittest.main()
