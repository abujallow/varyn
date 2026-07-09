from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.market import extract_symbols
from tools.risk import (
    assess_score_availability,
    build_risk_analysis,
    has_explicit_comparison_language,
)


def analysis_for(message: str, *, found: bool = False, extra_fields: dict | None = None) -> dict:
    """Build a risk analysis for `message`, deriving market_contexts from extract_symbols()
    so entity-count-based routing is exercised exactly as it would be in production.

    resolve_company_query is patched out: for names extract_symbols can't resolve via the
    alias dict or bare-ticker regex, its unmapped fallback would otherwise call a live
    yfinance.Search() over the network, which tests must never do.
    """
    with patch("tools.market.resolve_company_query", return_value=None):
        symbols = extract_symbols(message)
    contexts = [
        {"symbol": symbol, "found": found, "name": symbol, **(extra_fields or {})}
        for symbol in symbols
    ]
    primary = contexts[0] if contexts else None
    return build_risk_analysis(message, primary, contexts, macro_context={"found": False, "risk_read": []}, regulatory_signals=[])


class SingleEntityRoutingTests(unittest.TestCase):
    def test_jpmorgan_question_with_sources_ask_is_single_entity(self):
        analysis = analysis_for("What are the biggest current risks for JPMorgan, and what sources support your answer?")
        self.assertEqual(analysis["intent"], "single_entity_risk_memo")
        self.assertNotEqual(analysis["title"], "Multi-Company Risk Comparison")

    def test_jpmorgan_credit_and_liquidity_is_single_entity(self):
        analysis = analysis_for("What are JPMorgan's credit and liquidity risks?")
        self.assertEqual(analysis["intent"], "single_entity_risk_memo")

    def test_apple_risks_is_single_entity(self):
        analysis = analysis_for("What are the biggest current risks for Apple?")
        self.assertEqual(analysis["intent"], "single_entity_risk_memo")

    def test_unmapped_organization_is_single_entity(self):
        analysis = analysis_for("What risks does Canisius University face?")
        self.assertEqual(analysis["intent"], "single_entity_risk_memo")

    def test_unmapped_agency_is_single_entity(self):
        analysis = analysis_for("What are the biggest risks for the Federal Reserve?")
        self.assertEqual(analysis["intent"], "single_entity_risk_memo")


class MultiCompanyComparisonRoutingTests(unittest.TestCase):
    def test_compare_two_named_banks_is_comparison(self):
        analysis = analysis_for("Compare JPMorgan and Bank of America risk")
        self.assertEqual(analysis["intent"], "multi_company_comparison")
        self.assertEqual(analysis["title"], "Multi-Company Risk Comparison")

    def test_compare_three_tickers_is_comparison(self):
        analysis = analysis_for("Compare JPM, BAC, and C risk scores")
        self.assertEqual(analysis["intent"], "multi_company_comparison")

    def test_rank_three_companies_is_comparison(self):
        analysis = analysis_for("Rank JPMorgan, Citi, and Wells Fargo by risk")
        self.assertEqual(analysis["intent"], "multi_company_comparison")

    def test_versus_language_triggers_comparison_even_with_one_resolved_symbol(self):
        # "versus" is explicit comparison language on its own, independent of how many
        # tickers actually resolve -- this must not silently fall back to single-entity.
        analysis = analysis_for("JPMorgan versus a hypothetical unnamed competitor")
        self.assertEqual(analysis["intent"], "multi_company_comparison")

    def test_which_is_riskier_triggers_comparison(self):
        analysis = analysis_for("Which is riskier, JPMorgan or Bank of America?")
        self.assertEqual(analysis["intent"], "multi_company_comparison")


class MissingDataScoringTests(unittest.TestCase):
    def test_all_fields_missing_marks_score_unavailable(self):
        available, gaps = assess_score_availability({"found": True, "price": None, "beta": None, "debt_to_equity": None, "current_ratio": None})
        self.assertFalse(available)
        self.assertEqual(len(gaps), 4)

    def test_not_found_context_marks_score_unavailable(self):
        available, gaps = assess_score_availability({"found": False})
        self.assertFalse(available)
        self.assertTrue(len(gaps) > 0)

    def test_none_context_marks_score_unavailable(self):
        available, gaps = assess_score_availability(None)
        self.assertFalse(available)

    def test_enough_fields_present_marks_score_available(self):
        available, gaps = assess_score_availability(
            {"found": True, "price": 285.4, "beta": 1.1, "debt_to_equity": None, "current_ratio": None}
        )
        self.assertTrue(available)

    def test_build_risk_analysis_refuses_numeric_score_when_data_missing(self):
        analysis = analysis_for(
            "What are the biggest current risks for JPMorgan, and what sources support your answer?",
            found=True,
            extra_fields={"price": None, "beta": None, "debt_to_equity": None, "current_ratio": None},
        )
        self.assertFalse(analysis["score_available"])
        self.assertIsNone(analysis["overall_score"])
        self.assertEqual(analysis["score_confidence"], "insufficient_data")
        self.assertIn("Insufficient data", analysis["summary"])

    def test_missing_fields_are_listed_as_data_gaps(self):
        analysis = analysis_for(
            "What are Apple's risks?",
            found=True,
            extra_fields={"price": None, "beta": None, "debt_to_equity": None, "current_ratio": None},
        )
        gap_text = " ".join(analysis["data_gaps"]).lower()
        for expected in ("price", "beta", "debt to equity", "current ratio"):
            self.assertIn(expected, gap_text)

    def test_modules_carry_a_level_instead_of_a_fabricated_score_when_unavailable(self):
        analysis = analysis_for(
            "What are Apple's risks?",
            found=True,
            extra_fields={"price": None, "beta": None, "debt_to_equity": None, "current_ratio": None},
        )
        for module in analysis["modules"]:
            self.assertIsNone(module["score"])
            self.assertIsNotNone(module["level"])

    def test_sufficient_data_still_produces_a_numeric_score(self):
        analysis = analysis_for(
            "What are the biggest current risks for JPMorgan?",
            found=True,
            extra_fields={"price": 285.4, "beta": 1.1, "debt_to_equity": 120.0, "current_ratio": 1.2},
        )
        self.assertTrue(analysis["score_available"])
        self.assertIsInstance(analysis["overall_score"], int)
        self.assertEqual(analysis["score_confidence"], "data_supported")


class SourceGroundingTests(unittest.TestCase):
    def test_sources_field_present_and_populated_when_data_found(self):
        analysis = analysis_for(
            "What are Apple's risks?",
            found=True,
            extra_fields={"price": 190.0, "data_source": "yfinance", "confidence": {"level": "High"}},
        )
        self.assertIn("sources", analysis)
        self.assertTrue(len(analysis["sources"]) > 0)

    def test_risk_categories_include_sources_list(self):
        analysis = analysis_for(
            "What are Apple's risks?",
            found=True,
            extra_fields={"price": 190.0, "data_source": "yfinance", "confidence": {"level": "High"}},
        )
        self.assertIn("risk_categories", analysis)
        for category in analysis["risk_categories"]:
            self.assertIn("sources", category)
            self.assertIn("explanation", category)
            self.assertIn("level", category)

    def test_weak_source_coverage_is_reflected_in_data_confidence(self):
        analysis = analysis_for("What risks does Canisius University face?")
        self.assertEqual(analysis["data_confidence"], "limited")


class ComparisonLanguageDetectionTests(unittest.TestCase):
    def test_detects_compare(self):
        self.assertTrue(has_explicit_comparison_language("Compare Tesla and Ford"))

    def test_detects_versus(self):
        self.assertTrue(has_explicit_comparison_language("Tesla versus Ford"))

    def test_detects_vs(self):
        self.assertTrue(has_explicit_comparison_language("Tesla vs Ford risk"))

    def test_detects_rank(self):
        self.assertTrue(has_explicit_comparison_language("Rank these banks by risk"))

    def test_detects_which_is_riskier(self):
        self.assertTrue(has_explicit_comparison_language("Which is riskier, Ford or GM?"))

    def test_detects_between(self):
        self.assertTrue(has_explicit_comparison_language("What's the difference between JPM and BAC risk?"))

    def test_plain_single_entity_question_is_not_comparison_language(self):
        self.assertFalse(has_explicit_comparison_language("What are JPMorgan's biggest risks?"))
        self.assertFalse(has_explicit_comparison_language("What risks does Canisius University face?"))


if __name__ == "__main__":
    unittest.main()
