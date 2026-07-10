from __future__ import annotations

import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import risk_memo
from audit import AuditLogger
from providers import ProviderResult
from safety import SafetyRails
from tools.registry import build_tool_registry, ToolRuntime


def canned_market_context(symbol, prefer_cache=True):
    return {
        "symbol": symbol,
        "found": True,
        "price": 175.20,
        "change_percent": 0.4,
        "data_source": "yfinance",
        "confidence": {"level": "Medium"},
        # MTB-style: no generic corporate fundamentals mapped for a bank -- the
        # documented, expected "Failure 11" case, not a bug.
        "official_fundamentals": {
            "found": True,
            "source": "SEC EDGAR",
            "latest_filing_date": "2026-05-01",
            "fields": {},
        },
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
    return ProviderResult(
        reply="M&T Bank shows stable market pricing with limited available fundamentals.",
        provider="openrouter",
        model="test-model",
        status="OpenRouter local agent",
    )


def mocked_data_sources():
    return (
        patch.object(risk_memo, "fetch_market_context", side_effect=canned_market_context),
        patch.object(risk_memo, "get_official_fundamentals", side_effect=canned_fundamentals),
        patch.object(risk_memo, "get_macro_context", side_effect=canned_macro),
        patch.object(risk_memo, "get_complaint_signal", side_effect=canned_regulatory),
        patch.object(risk_memo, "complete", side_effect=canned_narrative),
    )


def make_isolated_rails(root: Path) -> SafetyRails:
    """SafetyRails with every piece of state -- including the audit logger --
    pointed at an isolated temp path, so confirmation_requested/resolved events
    (logged unconditionally inside SafetyRails, not behind an `if runtime.audit`
    guard) never reach the real local agent/data/audit/varyn-audit.jsonl."""
    return SafetyRails(
        state_path=root / "safety_state.json",
        confirmations_path=root / "confirmations.json",
        audit=AuditLogger(path=root / "audit.jsonl"),
    )


class ExportRiskMemoToolAndConfirmationTests(unittest.TestCase):
    """A demo/public session must be able to request and approve its own
    export_risk_memo confirmation; every other owner-only capability must
    remain blocked for that same session."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.rails = make_isolated_rails(root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_demo_request_creates_a_confirmation_not_an_owner_error(self):
        runtime = ToolRuntime(session_id="demo-1", access_role="demo", safety=self.rails)
        result = build_tool_registry().run("export_risk_memo", {"query": "risk memo for M&T Bank"}, runtime)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.confirmation)
        self.assertNotEqual(result.error, "Owner authentication is required for this capability.")

    def test_memo_is_not_generated_before_approval(self):
        runtime = ToolRuntime(session_id="demo-1", access_role="demo", safety=self.rails)
        with patch.object(risk_memo, "fetch_market_context") as mock_fetch:
            build_tool_registry().run("export_risk_memo", {"query": "risk memo for M&T Bank"}, runtime)
        mock_fetch.assert_not_called()

    def test_pending_confirmation_records_correct_action_and_session(self):
        runtime = ToolRuntime(session_id="demo-42", access_role="demo", safety=self.rails)
        result = build_tool_registry().run("export_risk_memo", {"query": "risk memo for M&T Bank"}, runtime)
        stored = self.rails.peek_confirmation(result.confirmation["id"])
        self.assertEqual(stored["action"], "export_risk_memo")
        self.assertEqual(stored["action_kind"], "tool")
        self.assertEqual(stored["session_id"], "demo-42")
        self.assertEqual(stored["status"], "pending")

    def test_owner_request_still_works(self):
        runtime = ToolRuntime(session_id="owner-1", access_role="owner", safety=self.rails)
        result = build_tool_registry().run("export_risk_memo", {"query": "risk memo for M&T Bank"}, runtime)
        self.assertIsNotNone(result.confirmation)

    def test_owner_only_memory_tools_remain_blocked_for_demo(self):
        for tool_name, arguments in (
            ("remember_fact", {"statement": "x"}),
            ("update_fact", {"fact_id": "f1", "statement": "x"}),
            ("forget_fact", {"fact_id": "f1"}),
        ):
            runtime = ToolRuntime(session_id="demo-1", access_role="demo", safety=self.rails)
            result = build_tool_registry().run(tool_name, arguments, runtime)
            self.assertFalse(result.ok, f"{tool_name} should be blocked for demo")
            self.assertIsNone(result.confirmation, f"{tool_name} should not create a confirmation for demo")
            self.assertEqual(result.error, "Owner authentication is required for this capability.")

    def test_active_file_remains_blocked_for_demo(self):
        runtime = ToolRuntime(session_id="demo-1", access_role="demo")
        result = build_tool_registry().run("active_file", {"question": "what does it say?"}, runtime)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Owner authentication is required for this capability.")


class ExportRiskMemoResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.rails = make_isolated_rails(root)
        create_runtime = ToolRuntime(session_id="demo-A", access_role="demo", safety=self.rails)
        self.created = build_tool_registry().run(
            "export_risk_memo", {"query": "risk memo for M&T Bank"}, create_runtime
        )
        self.confirmation_id = self.created.confirmation["id"]

    def tearDown(self):
        self._tmp.cleanup()

    def test_correct_session_can_approve_its_own_confirmation(self):
        resolved = self.rails.resolve_confirmation(self.confirmation_id, "demo-A", "approve")
        self.assertEqual(resolved["status"], "approved")

    def test_different_session_cannot_approve(self):
        with self.assertRaises(ValueError):
            self.rails.resolve_confirmation(self.confirmation_id, "demo-B", "approve")

    def test_reusing_the_same_confirmation_fails_safely(self):
        self.rails.resolve_confirmation(self.confirmation_id, "demo-A", "approve")
        with self.assertRaises(ValueError):
            self.rails.resolve_confirmation(self.confirmation_id, "demo-A", "approve")

    def test_unknown_confirmation_id_fails_safely(self):
        with self.assertRaises(ValueError):
            self.rails.resolve_confirmation("confirm-does-not-exist", "demo-A", "approve")

    def test_expired_confirmation_fails_safely(self):
        from datetime import datetime, timedelta, timezone
        from safety import write_json

        payload = self.rails._read_confirmations()
        for item in payload["confirmations"]:
            if item["id"] == self.confirmation_id:
                item["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        write_json(self.rails.confirmations_path, payload)
        with self.assertRaises(ValueError):
            self.rails.resolve_confirmation(self.confirmation_id, "demo-A", "approve")

    def test_approved_export_executes_exactly_once(self):
        resolved = self.rails.resolve_confirmation(self.confirmation_id, "demo-A", "approve")
        mocks = mocked_data_sources()
        with mocks[0] as mock_fetch, mocks[1], mocks[2], mocks[3], mocks[4]:
            exec_runtime = ToolRuntime(session_id="demo-A", access_role="demo", safety=self.rails, audit=self.rails.audit)
            result = build_tool_registry().run(
                "export_risk_memo", resolved["arguments"], exec_runtime, confirmation_granted=True
            )
            self.assertTrue(result.ok)
            self.assertEqual(mock_fetch.call_count, 1)


class ExportRiskMemoGenerationTests(unittest.TestCase):
    """Verifies the memo itself: MTB-style missing fundamentals, all three
    formats, valid artifact payloads, and correct delivery status."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.rails = make_isolated_rails(root)

    def tearDown(self):
        self._tmp.cleanup()

    def _generate(self):
        create_runtime = ToolRuntime(session_id="demo-A", access_role="demo", safety=self.rails)
        created = build_tool_registry().run("export_risk_memo", {"query": "risk memo for M&T Bank"}, create_runtime)
        resolved = self.rails.resolve_confirmation(created.confirmation["id"], "demo-A", "approve")
        mocks = mocked_data_sources()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
            exec_runtime = ToolRuntime(session_id="demo-A", access_role="demo", safety=self.rails, audit=self.rails.audit)
            return build_tool_registry().run(
                "export_risk_memo", resolved["arguments"], exec_runtime, confirmation_granted=True
            )

    def test_memo_generated_despite_missing_bank_fundamentals(self):
        result = self._generate()
        self.assertTrue(result.ok)
        self.assertEqual(result.output["symbol"], "MTB")

    def test_missing_fundamentals_labeled_not_available_never_fabricated(self):
        result = self._generate()
        markdown_artifact = next(a for a in result.output["artifacts"] if a["format"] == "markdown")
        markdown_text = base64.b64decode(markdown_artifact["content"]).decode("utf-8")
        self.assertIn("Not available", markdown_text)
        # None of the mapped fundamental labels should show a fabricated numeric value --
        # every fundamentals row must read "Not available" given the mocked empty fields.
        for label in ("Revenue", "Net income", "Total assets", "Total debt"):
            self.assertIn(f"{label} | Not available", markdown_text.replace("  ", " "))

    def test_all_three_artifact_formats_are_produced(self):
        result = self._generate()
        formats = {artifact["format"] for artifact in result.output["artifacts"]}
        self.assertEqual(formats, {"markdown", "html", "pdf"})

    def test_every_artifact_has_valid_fields(self):
        result = self._generate()
        expected_mime = {
            "markdown": "text/markdown;charset=utf-8",
            "html": "text/html;charset=utf-8",
            "pdf": "application/pdf",
        }
        for artifact in result.output["artifacts"]:
            self.assertTrue(artifact["filename"])
            self.assertEqual(artifact["mime_type"], expected_mime[artifact["format"]])
            self.assertEqual(artifact["encoding"], "base64")
            self.assertGreater(artifact["size_bytes"], 0)
            decoded = base64.b64decode(artifact["content"])
            self.assertEqual(len(decoded), artifact["size_bytes"])
            self.assertGreater(len(decoded), 0)

    def test_delivery_status_is_ready_on_success(self):
        result = self._generate()
        self.assertEqual(result.output["delivery_status"], "ready")


class ExportRiskMemoAuditTests(unittest.TestCase):
    """Confirmation and generation events stay audit-logged, and audit
    entries never contain memo content, artifact payloads, or secrets."""

    def test_confirmation_lifecycle_is_audit_logged_without_content_leakage(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_logger = AuditLogger(path=root / "audit.jsonl")
            rails = SafetyRails(
                state_path=root / "safety_state.json",
                confirmations_path=root / "confirmations.json",
                audit=fake_logger,
            )
            create_runtime = ToolRuntime(session_id="demo-A", access_role="demo", safety=rails, audit=fake_logger)
            created = build_tool_registry().run(
                "export_risk_memo", {"query": "risk memo for M&T Bank"}, create_runtime
            )
            resolved = rails.resolve_confirmation(created.confirmation["id"], "demo-A", "approve")

            mocks = mocked_data_sources()
            with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
                exec_runtime = ToolRuntime(session_id="demo-A", access_role="demo", safety=rails, audit=fake_logger)
                result = build_tool_registry().run(
                    "export_risk_memo", resolved["arguments"], exec_runtime, confirmation_granted=True
                )
            self.assertTrue(result.ok)

            raw_log = (root / "audit.jsonl").read_text(encoding="utf-8")
            event_types = [
                __import__("json").loads(line)["event_type"] for line in raw_log.splitlines() if line.strip()
            ]
            self.assertIn("confirmation_requested", event_types)
            self.assertIn("confirmation_resolved", event_types)

            markdown_artifact = next(a for a in result.output["artifacts"] if a["format"] == "markdown")
            self.assertNotIn(markdown_artifact["content"], raw_log)
            decoded_snippet = base64.b64decode(markdown_artifact["content"]).decode("utf-8")[:100]
            self.assertNotIn(decoded_snippet, raw_log)


if __name__ == "__main__":
    unittest.main()
