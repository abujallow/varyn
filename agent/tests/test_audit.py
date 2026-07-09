from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import audit as audit_module
from audit import AuditLogger


class AuditRotationTests(unittest.TestCase):
    def test_log_rotates_past_byte_threshold_without_losing_recent_entries(self):
        with TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "audit" / "varyn-audit.jsonl"
            logger = AuditLogger(path=test_path)
            with patch.object(audit_module, "MAX_AUDIT_LOG_BYTES", 2000), patch.object(
                audit_module, "MAX_AUDIT_LOG_LINES", 5
            ):
                for i in range(50):
                    logger.log("test_event", session_id="s1", details={"i": i})

                lines = logger.path.read_text(encoding="utf-8").splitlines()
                # Bounded growth: far fewer lines survive than the 50 appended.
                self.assertLess(len(lines), 20)
                last_entry = __import__("json").loads(lines[-1])
                self.assertEqual(last_entry["details"]["i"], 49)

    def test_summary_counters_unaffected_by_rotation(self):
        with TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "audit" / "varyn-audit.jsonl"
            logger = AuditLogger(path=test_path)
            with patch.object(audit_module, "MAX_AUDIT_LOG_BYTES", 2000), patch.object(
                audit_module, "MAX_AUDIT_LOG_LINES", 5
            ):
                for i in range(50):
                    logger.log("test_event", session_id="s1", details={"i": i})
                self.assertEqual(logger.summary()["total_events"], 50)

    def test_redaction_scrubs_sensitive_keys(self):
        with TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "audit" / "varyn-audit.jsonl"
            logger = AuditLogger(path=test_path)
            entry = logger.log(
                "test_event",
                session_id="s1",
                details={"api_key": "sk-should-not-appear", "safe_field": "ok"},
            )
            self.assertEqual(entry["details"]["api_key"], "***REDACTED***")
            self.assertEqual(entry["details"]["safe_field"], "ok")
            raw = logger.path.read_text(encoding="utf-8")
            self.assertNotIn("sk-should-not-appear", raw)


if __name__ == "__main__":
    unittest.main()
