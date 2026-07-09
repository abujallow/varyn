from __future__ import annotations

import unittest

from risk_memo import (
    CONFIDENCE_LEVELS,
    assess_risk_confidence,
    confidence_level,
    evidence_row,
    validate_provenance,
)


class EvidenceRowTests(unittest.TestCase):
    def test_unavailable_forces_flagged_regardless_of_input_confidence(self):
        row = evidence_row("Revenue", "N/A", "SEC EDGAR", "2026-01-01", "High", available=False)
        self.assertEqual(row["confidence"], "Flagged")
        self.assertEqual(row["value"], "Not available")
        self.assertIsNone(row["raw_value"])

    def test_available_row_preserves_confidence(self):
        row = evidence_row("Revenue", "$1.2B", "SEC EDGAR", "2026-01-01", "High", available=True)
        self.assertEqual(row["confidence"], "High")
        self.assertEqual(row["value"], "$1.2B")


class ConfidenceLevelNormalizationTests(unittest.TestCase):
    def test_accepts_dict_input(self):
        self.assertEqual(confidence_level({"level": "high"}, "Low"), "High")

    def test_accepts_string_input(self):
        self.assertEqual(confidence_level("medium", "Low"), "Medium")

    def test_invalid_value_falls_back(self):
        self.assertEqual(confidence_level("nonsense", "Low"), "Low")

    def test_none_falls_back(self):
        self.assertEqual(confidence_level(None, "Flagged"), "Flagged")

    def test_result_always_in_allowed_levels(self):
        for candidate in ["high", "MEDIUM", "low", "flagged", None, "garbage", {"level": "high"}]:
            self.assertIn(confidence_level(candidate, "Low"), CONFIDENCE_LEVELS)


class ProvenanceValidationTests(unittest.TestCase):
    def test_valid_report_passes(self):
        report = {
            "market_rows": [
                {"metric": "Price", "value": "$100", "source": "yfinance", "date": "2026-01-01", "confidence": "High"}
            ]
        }
        validate_provenance(report)  # must not raise

    def test_unavailable_row_without_flagged_confidence_fails(self):
        report = {
            "market_rows": [
                {"metric": "Price", "value": "Not available", "source": None, "date": None, "confidence": "High"}
            ]
        }
        with self.assertRaises(ValueError):
            validate_provenance(report)

    def test_available_row_missing_source_fails(self):
        report = {
            "fundamental_rows": [
                {"metric": "Revenue", "value": "$1B", "source": None, "date": "2026-01-01", "confidence": "High"}
            ]
        }
        with self.assertRaises(ValueError):
            validate_provenance(report)

    def test_available_row_missing_date_fails(self):
        report = {
            "macro_rows": [
                {"metric": "CPI", "value": "3.1%", "source": "FRED", "date": None, "confidence": "High"}
            ]
        }
        with self.assertRaises(ValueError):
            validate_provenance(report)

    def test_available_row_invalid_confidence_fails(self):
        report = {
            "regulatory_rows": [
                {"metric": "Complaints", "value": "12", "source": "CFPB", "date": "2026-01-01", "confidence": "Maybe"}
            ]
        }
        with self.assertRaises(ValueError):
            validate_provenance(report)

    def test_multiple_violations_are_aggregated(self):
        report = {
            "risk_rows": [
                {"metric": "A", "value": "1", "source": None, "date": None, "confidence": "Bad"},
            ]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_provenance(report)
        message = str(ctx.exception)
        self.assertIn("source missing", message)
        self.assertIn("relevant date missing", message)
        self.assertIn("confidence missing or invalid", message)

    def test_empty_report_passes(self):
        validate_provenance({})  # no rows anywhere -- nothing to validate


class AssessRiskConfidenceTests(unittest.TestCase):
    def test_never_auto_escalates_to_high(self):
        result = assess_risk_confidence(
            {"confidence": "High"},
            {"confidence": "High", "fields": {}},
            {"confidence": "High"},
        )
        self.assertNotEqual(result["level"], "High")

    def test_any_flagged_input_escalates_overall_to_flagged(self):
        result = assess_risk_confidence(
            {"confidence": "Flagged"},
            {"confidence": "High", "fields": {}},
            {"confidence": "High"},
        )
        self.assertEqual(result["level"], "Flagged")

    def test_missing_material_fundamentals_forces_at_least_low(self):
        result = assess_risk_confidence(
            {"confidence": "High"},
            {"confidence": "High", "fields": {}},  # required fields all missing
            {"confidence": "High"},
        )
        self.assertIn(result["level"], {"Low", "Flagged"})

    def test_regulatory_not_applicable_is_excluded_from_inputs(self):
        result = assess_risk_confidence(
            {"confidence": "High"},
            {"confidence": "High", "fields": {}},
            {"confidence": "High"},
            regulatory={"applicable": False, "confidence": "Flagged"},
        )
        self.assertEqual(result["inputs"]["regulatory"], "Not applicable")


if __name__ == "__main__":
    unittest.main()
