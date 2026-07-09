from __future__ import annotations

import json
import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from memory import (
    LongTermMemoryStore,
    MemoryStore,
    _LocalFileBackend,
    _UpstashBackend,
    _long_term_memory_backend,
)
from tools.files import safe_session_id


class SessionIsolationTests(unittest.TestCase):
    def test_sessions_do_not_leak_across_ids(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("memory.DATA_DIR", Path(tmpdir)):
                store = MemoryStore()
                store.add_turn("session-a", "user", "secret a")
                store.add_turn("session-b", "user", "secret b")
                self.assertEqual(len(store.recent_context("session-a")), 1)
                self.assertEqual(len(store.recent_context("session-b")), 1)
                self.assertEqual(store.recent_context("session-a")[0]["content"], "secret a")
                self.assertEqual(store.recent_context("session-b")[0]["content"], "secret b")

    def test_reset_session_only_clears_target_session(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("memory.DATA_DIR", Path(tmpdir)):
                store = MemoryStore()
                store.add_turn("session-a", "user", "hi")
                store.add_turn("session-b", "user", "hi")
                store.reset_session("session-a")
                self.assertEqual(store.recent_context("session-a"), [])
                self.assertEqual(len(store.recent_context("session-b")), 1)

    def test_stale_sessions_pruned_fresh_sessions_kept(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("memory.DATA_DIR", Path(tmpdir)):
                store = MemoryStore()
                store.add_turn("fresh", "user", "hi")
                old_ts = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
                store.data["sessions"]["stale"] = [
                    {"role": "user", "content": "old", "timestamp": old_ts}
                ]
                store._save()
                store._prune_stale_sessions_locked()
                self.assertIn("fresh", store.data["sessions"])
                self.assertNotIn("stale", store.data["sessions"])


class UploadPathIsolationTests(unittest.TestCase):
    def test_path_traversal_cannot_escape_upload_dir(self):
        # No path separators may survive, so joining with UPLOAD_DIR can only ever
        # produce a single child segment -- traversal is structurally impossible
        # even though literal ".." characters may remain in the sanitized name.
        from pathlib import Path

        cleaned = safe_session_id("../../etc/passwd")
        self.assertNotIn("/", cleaned)
        resolved = (Path("/upload-root") / cleaned).resolve()
        self.assertTrue(str(resolved).startswith(str(Path("/upload-root").resolve())))

    def test_windows_traversal_is_neutralized(self):
        cleaned = safe_session_id("..\\..\\windows\\system32")
        self.assertNotIn("\\", cleaned)

    def test_different_sessions_produce_different_ids(self):
        self.assertNotEqual(safe_session_id("session-a"), safe_session_id("session-b"))

    def test_empty_session_id_defaults_safely(self):
        self.assertEqual(safe_session_id(""), "local-preview")


class LongTermMemoryBackendSelectionTests(unittest.TestCase):
    def test_local_backend_selected_without_upstash_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KV_REST_API_URL", None)
            os.environ.pop("KV_REST_API_TOKEN", None)
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
                backend = _long_term_memory_backend(Path(tmpdir) / "ltm.json")
                self.assertIsInstance(backend, _LocalFileBackend)

    def test_upstash_backend_selected_when_env_present(self):
        with patch.dict(
            os.environ, {"KV_REST_API_URL": "http://example.invalid", "KV_REST_API_TOKEN": "t"}
        ):
            backend = _long_term_memory_backend(None)
            self.assertIsInstance(backend, _UpstashBackend)


class MockUpstashHandler(BaseHTTPRequestHandler):
    store: dict[str, str] = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        key = self.path.split("/get/")[-1]
        value = MockUpstashHandler.store.get(key)
        body = json.dumps({"result": value}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        key = self.path.split("/set/")[-1]
        length = int(self.headers.get("Content-Length", 0))
        value = self.rfile.read(length).decode("utf-8")
        MockUpstashHandler.store[key] = value
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"result": "OK"}).encode())


class UpstashBackedLongTermMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        MockUpstashHandler.store = {}
        cls.server = HTTPServer(("127.0.0.1", 0), MockUpstashHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_remember_update_forget_round_trip_via_upstash(self):
        with patch.dict(
            os.environ,
            {
                "KV_REST_API_URL": f"http://127.0.0.1:{self.port}",
                "KV_REST_API_TOKEN": "mock-token",
            },
        ):
            store = LongTermMemoryStore()
            self.assertEqual(store.summary()["backend"], "upstash_redis")
            result = store.remember("Test fact via mock upstash.")
            self.assertTrue(result["created"])
            facts = store.list_facts()
            self.assertEqual(len(facts), 1)
            fact_id = facts[0]["id"]
            store.update(fact_id, "Updated test fact.")
            self.assertEqual(store.list_facts()[0]["statement"], "Updated test fact.")
            store.forget(fact_id)
            self.assertEqual(store.list_facts(), [])


if __name__ == "__main__":
    unittest.main()
