from __future__ import annotations

import unittest

from tools.risk import is_risk_request, score_from_context


class RiskScoreCappingTests(unittest.TestCase):
    def test_baseline_scores_with_no_signals(self):
        scores = score_from_context("hello there", None)
        self.assertEqual(scores["Market Risk"], 50)
        self.assertEqual(scores["Credit Risk"], 45)
        self.assertEqual(scores["Liquidity Risk"], 45)
        self.assertEqual(scores["Operational Risk"], 45)

    def test_all_scores_are_capped_at_95_under_extreme_input(self):
        message = (
            "merger acquisition debt credit funding cash liquidity runway "
            "operations operational supply chain vendor cyber volatile "
            "volatility market dow nasdaq s&p"
        )
        market_context = {"found": True, "change_percent": 500}
        scores = score_from_context(message, market_context)
        for value in scores.values():
            self.assertLessEqual(value, 95)

    def test_credit_keywords_increase_credit_and_liquidity(self):
        baseline = score_from_context("hello", None)
        bumped = score_from_context("this involves a merger and new debt", None)
        self.assertGreater(bumped["Credit Risk"], baseline["Credit Risk"])
        self.assertGreater(bumped["Liquidity Risk"], baseline["Liquidity Risk"])
        self.assertEqual(bumped["Operational Risk"], baseline["Operational Risk"])

    def test_market_volatility_increases_only_market_risk(self):
        baseline = score_from_context("hello", None)
        bumped = score_from_context("the market is very volatile today", None)
        self.assertGreater(bumped["Market Risk"], baseline["Market Risk"])
        self.assertEqual(bumped["Credit Risk"], baseline["Credit Risk"])

    def test_market_context_change_percent_scales_market_risk(self):
        small_move = score_from_context("hello", {"found": True, "change_percent": 1})
        big_move = score_from_context("hello", {"found": True, "change_percent": 10})
        self.assertGreaterEqual(big_move["Market Risk"], small_move["Market Risk"])

    def test_market_context_not_found_is_ignored(self):
        scores = score_from_context("hello", {"found": False, "change_percent": 50})
        self.assertEqual(scores["Market Risk"], 50)


class RiskRequestClassificationTests(unittest.TestCase):
    def test_conceptual_question_without_analysis_terms_is_not_a_risk_request(self):
        self.assertFalse(is_risk_request("what is a merger?"))

    def test_transaction_keyword_triggers_risk_request(self):
        self.assertTrue(is_risk_request("Assess the ticker JPM for risk."))

    def test_plain_greeting_is_not_a_risk_request(self):
        self.assertFalse(is_risk_request("hello, how are you?"))


if __name__ == "__main__":
    unittest.main()
