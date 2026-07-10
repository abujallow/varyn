from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_core import AgentResult
from main import app


PROXY_HEADERS_DEMO = {"X-Varyn-Proxy-Key": "test-proxy-secret", "X-Varyn-Role": "demo"}
PROXY_HEADERS_OWNER = {"X-Varyn-Proxy-Key": "test-proxy-secret", "X-Varyn-Role": "owner"}


class SecuredTestCase(unittest.TestCase):
    """Base class for route tests that need proxy-secret auth active, matching
    the existing test_security.py setup so demo/owner headers behave the same way."""

    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {"VARYN_PROXY_SECRET": "test-proxy-secret", "VARYN_SECURITY_REQUIRED": "true"},
        )
        self.environment.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.environment.stop()


class PingRouteTests(unittest.TestCase):
    def test_ping_is_public_and_returns_fixed_body(self) -> None:
        client = TestClient(app)
        response = client.get("/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "awake"})
        client.close()


class SecFundamentalsRouteTests(SecuredTestCase):
    def test_demo_can_read_without_refresh(self) -> None:
        with patch("main.get_official_fundamentals", return_value={"found": True, "fields": {"revenue": 1}}):
            response = self.client.get("/sec/fundamentals/AAPL", headers=PROXY_HEADERS_DEMO)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_demo_cannot_force_refresh(self) -> None:
        with patch("main.get_official_fundamentals", return_value={"found": True, "fields": {}}):
            response = self.client.get(
                "/sec/fundamentals/AAPL", params={"refresh": "true"}, headers=PROXY_HEADERS_DEMO
            )
        self.assertEqual(response.status_code, 403)

    def test_owner_can_force_refresh(self) -> None:
        with patch("main.get_official_fundamentals", return_value={"found": True, "fields": {"revenue": 1}}):
            response = self.client.get(
                "/sec/fundamentals/AAPL", params={"refresh": "true"}, headers=PROXY_HEADERS_OWNER
            )
        self.assertEqual(response.status_code, 200)

    def test_not_found_reports_ok_false(self) -> None:
        with patch("main.get_official_fundamentals", return_value={"found": False, "fields": {}}):
            response = self.client.get("/sec/fundamentals/ZZZZ", headers=PROXY_HEADERS_DEMO)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])


class AuditRouteSchemaTests(SecuredTestCase):
    def test_response_contains_summary_and_recent(self) -> None:
        fake_audit = MagicMock()
        fake_audit.summary.return_value = {"total_events": 3}
        fake_audit.recent.return_value = [{"event_type": "test_event"}]
        with patch("main.audit", fake_audit):
            response = self.client.get("/audit", headers=PROXY_HEADERS_OWNER)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"], {"total_events": 3})
        self.assertEqual(body["recent"], [{"event_type": "test_event"}])


class HeartbeatRouteTests(SecuredTestCase):
    def test_returns_heartbeat_service_status_unchanged(self) -> None:
        canned_status = {"enabled": True, "running": False, "notices": []}
        fake_heartbeat = MagicMock()
        fake_heartbeat.status.return_value = canned_status
        with patch("main.heartbeat", fake_heartbeat):
            response = self.client.get("/heartbeat", headers=PROXY_HEADERS_DEMO)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), canned_status)


def fake_memory() -> MagicMock:
    memory = MagicMock()
    memory.recent_context.return_value = []
    memory.get_file_context.return_value = None
    memory.session_summary.return_value = {"active_file": None, "turns": 0}
    return memory


class ChatRouteTests(SecuredTestCase):
    def test_empty_message_returns_error_without_touching_agent(self) -> None:
        with patch("main.run_agent_turn") as mock_run:
            response = self.client.post(
                "/chat",
                json={"message": "   ", "session_id": "http-test-session"},
                headers=PROXY_HEADERS_DEMO,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"error": "No message provided."})
        mock_run.assert_not_called()

    def test_stop_command_short_circuits_before_agent_turn(self) -> None:
        memory = fake_memory()
        with patch("main.memory", memory), patch("main.audit", MagicMock()), patch(
            "main.run_agent_turn"
        ) as mock_run:
            response = self.client.post(
                "/chat",
                json={"message": "stop", "session_id": "http-test-session"},
                headers=PROXY_HEADERS_DEMO,
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "Speech cancelled")
        self.assertEqual(body["provider"], "local")
        self.assertEqual(body["mode"], "system_command")
        self.assertIsNone(body["analysis"])
        mock_run.assert_not_called()

    def test_successful_turn_returns_full_response_contract(self) -> None:
        memory = fake_memory()
        long_term_memory = MagicMock()
        long_term_memory.list_facts.return_value = []
        agent_result = AgentResult(
            reply="Tesla shows elevated volatility risk.",
            spoken="Tesla shows elevated volatility risk.",
            provider="openrouter",
            model="test-model",
            status="OpenRouter local agent",
            mode="analysis",
            analysis={"title": "Tesla risk"},
            market={"symbol": "TSLA"},
        )
        with patch("main.memory", memory), patch("main.long_term_memory", long_term_memory), patch(
            "main.audit", MagicMock()
        ), patch("main.run_agent_turn", return_value=agent_result) as mock_run:
            response = self.client.post(
                "/chat",
                json={"message": "What are Tesla's risks?", "session_id": "http-test-session"},
                headers=PROXY_HEADERS_DEMO,
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["reply"], agent_result.reply)
        self.assertEqual(body["provider"], "openrouter")
        self.assertEqual(body["mode"], "analysis")
        self.assertEqual(body["analysis"], {"title": "Tesla risk"})
        mock_run.assert_called_once()


class ChatStreamRouteTests(SecuredTestCase):
    def test_empty_message_returns_400(self) -> None:
        response = self.client.post(
            "/chat/stream",
            json={"message": "", "session_id": "http-test-session"},
            headers=PROXY_HEADERS_DEMO,
        )
        self.assertEqual(response.status_code, 400)

    def test_successful_stream_sets_sse_headers_and_frames(self) -> None:
        memory = fake_memory()
        long_term_memory = MagicMock()
        long_term_memory.list_facts.return_value = []

        def fake_stream(**_kwargs):
            yield {"type": "token", "text": "Hello "}
            yield {"type": "activity", "event": {"type": "risk", "label": "Risk engine active"}}
            yield {"type": "token", "text": "world."}
            yield {
                "type": "result",
                "result": AgentResult(
                    reply="Hello world.",
                    spoken="Hello world.",
                    provider="openrouter",
                    model="test-model",
                    status="OpenRouter local agent",
                    mode="chat",
                ),
            }

        with patch("main.memory", memory), patch("main.long_term_memory", long_term_memory), patch(
            "main.audit", MagicMock()
        ), patch("main.run_agent_turn_stream", fake_stream):
            response = self.client.post(
                "/chat/stream",
                json={"message": "Hello Varyn", "session_id": "http-test-session"},
                headers=PROXY_HEADERS_DEMO,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-cache, no-transform")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        body = response.text
        self.assertIn("event: token", body)
        self.assertIn("Hello ", body)
        self.assertIn("event: activity", body)
        self.assertIn("event: result", body)
        self.assertIn("Hello world.", body)

    def test_stop_command_yields_result_event_only(self) -> None:
        memory = fake_memory()
        with patch("main.memory", memory), patch("main.audit", MagicMock()), patch(
            "main.run_agent_turn_stream"
        ) as mock_stream:
            response = self.client.post(
                "/chat/stream",
                json={"message": "stop", "session_id": "http-test-session"},
                headers=PROXY_HEADERS_DEMO,
            )
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("event: result", body)
        self.assertIn("Speech cancelled", body)
        mock_stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
