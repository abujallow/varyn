from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_core import AgentResult
from audit import AuditLogger
from main import app
from safety import SafetyRails
from tools.registry import ToolRuntime, build_tool_registry


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


def canned_market_context(symbol, prefer_cache=True):
    return {
        "symbol": symbol,
        "found": True,
        "price": 175.20,
        "change_percent": 0.4,
        "data_source": "yfinance",
        "confidence": {"level": "Medium"},
        "official_fundamentals": {"found": True, "source": "SEC EDGAR", "latest_filing_date": "2026-05-01", "fields": {}},
    }


def canned_fundamentals(symbol, force=False):
    return {"found": True, "source": "SEC EDGAR", "latest_filing_date": "2026-05-01", "fields": {}}


def canned_macro(query=""):
    return {"source": "FRED", "confidence": {"level": "High"}, "pulled_at": "2026-07-09T00:00:00Z", "series": []}


def canned_regulatory(symbol, force=False):
    return {
        "symbol": symbol,
        "found": True,
        "applicable": True,
        "confidence": {"level": "High"},
        "current": {"count": 297, "start": "2026-01-01", "end": "2026-06-30"},
        "previous": {"count": 339, "start": "2025-07-01", "end": "2025-12-31"},
        "source": "CFPB Consumer Complaint Database",
        "pulled_at": "2026-07-09T00:00:00Z",
    }


def canned_narrative(messages, tools=None):
    from providers import ProviderResult

    return ProviderResult(
        reply="M&T Bank shows stable market pricing with limited available fundamentals.",
        provider="openrouter",
        model="test-model",
        status="OpenRouter local agent",
    )


class ConfirmationResolutionRouteTests(SecuredTestCase):
    """Full HTTP-level round trip through /confirmations/{id}: the exact
    boundary the frontend's Vercel safety proxy calls."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.rails = SafetyRails(
            state_path=root / "safety_state.json",
            confirmations_path=root / "confirmations.json",
            audit=AuditLogger(path=root / "audit.jsonl"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()
        super().tearDown()

    def test_demo_can_resolve_its_own_export_confirmation_and_receives_all_artifacts(self) -> None:
        with patch("main.safety", self.rails):
            create_runtime = ToolRuntime(session_id="http-demo-session", access_role="demo", safety=self.rails)
            created = build_tool_registry().run(
                "export_risk_memo", {"query": "risk memo for M&T Bank"}, create_runtime
            )
            confirmation_id = created.confirmation["id"]

            with patch("main.memory", MagicMock(get_file_context=MagicMock(return_value=None))), patch(
                "main.long_term_memory", MagicMock()
            ), patch("main.audit", MagicMock()), patch(
                "risk_memo.fetch_market_context", side_effect=canned_market_context
            ), patch("risk_memo.get_official_fundamentals", side_effect=canned_fundamentals), patch(
                "risk_memo.get_macro_context", side_effect=canned_macro
            ), patch("risk_memo.get_complaint_signal", side_effect=canned_regulatory), patch(
                "risk_memo.complete", side_effect=canned_narrative
            ):
                response = self.client.post(
                    f"/confirmations/{confirmation_id}",
                    json={"session_id": "http-demo-session", "decision": "approve"},
                    headers=PROXY_HEADERS_DEMO,
                )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        formats = {artifact["format"] for artifact in body["artifacts"]}
        self.assertEqual(formats, {"markdown", "html", "pdf"})
        for artifact in body["artifacts"]:
            self.assertTrue(artifact["content"])

    def test_demo_without_owner_role_cannot_resolve_an_owner_only_confirmation(self) -> None:
        with patch("main.safety", self.rails):
            owner_runtime = ToolRuntime(session_id="http-owner-session", access_role="owner", safety=self.rails)
            created = build_tool_registry().run("remember_fact", {"statement": "test fact"}, owner_runtime)
            confirmation_id = created.confirmation["id"]

            response = self.client.post(
                f"/confirmations/{confirmation_id}",
                json={"session_id": "http-owner-session", "decision": "approve"},
                headers=PROXY_HEADERS_DEMO,
            )
        self.assertEqual(response.status_code, 403)

    def test_owner_can_still_resolve_an_owner_only_confirmation(self) -> None:
        with patch("main.safety", self.rails), patch("main.long_term_memory", MagicMock()), patch(
            "main.audit", MagicMock()
        ):
            owner_runtime = ToolRuntime(session_id="http-owner-session-2", access_role="owner", safety=self.rails)
            created = build_tool_registry().run("remember_fact", {"statement": "test fact"}, owner_runtime)
            confirmation_id = created.confirmation["id"]

            response = self.client.post(
                f"/confirmations/{confirmation_id}",
                json={"session_id": "http-owner-session-2", "decision": "approve"},
                headers=PROXY_HEADERS_OWNER,
            )
        self.assertEqual(response.status_code, 200)

    def test_cross_session_resolution_is_rejected(self) -> None:
        with patch("main.safety", self.rails):
            create_runtime = ToolRuntime(session_id="http-demo-session-A", access_role="demo", safety=self.rails)
            created = build_tool_registry().run(
                "export_risk_memo", {"query": "risk memo for M&T Bank"}, create_runtime
            )
            confirmation_id = created.confirmation["id"]

            response = self.client.post(
                f"/confirmations/{confirmation_id}",
                json={"session_id": "http-demo-session-B", "decision": "approve"},
                headers=PROXY_HEADERS_DEMO,
            )
        self.assertEqual(response.status_code, 400)

    def test_unknown_confirmation_id_fails_safely(self) -> None:
        with patch("main.safety", self.rails):
            response = self.client.post(
                "/confirmations/confirm-does-not-exist",
                json={"session_id": "any-session", "decision": "approve"},
                headers=PROXY_HEADERS_DEMO,
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
