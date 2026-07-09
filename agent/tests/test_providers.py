from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import providers


class AttemptBudgetTests(unittest.TestCase):
    def test_attempt_allowed_under_max_attempts(self):
        with patch.object(providers, "PROVIDER_MAX_ATTEMPTS", 3), patch.object(
            providers, "PROVIDER_MAX_TOTAL_SECONDS", 42
        ):
            self.assertTrue(providers.attempt_allowed(time.monotonic(), 2))

    def test_attempt_not_allowed_once_max_attempts_reached(self):
        with patch.object(providers, "PROVIDER_MAX_ATTEMPTS", 3), patch.object(
            providers, "PROVIDER_MAX_TOTAL_SECONDS", 42
        ):
            self.assertFalse(providers.attempt_allowed(time.monotonic(), 3))

    def test_attempt_not_allowed_once_time_budget_exhausted(self):
        with patch.object(providers, "PROVIDER_MAX_ATTEMPTS", 10), patch.object(
            providers, "PROVIDER_MAX_TOTAL_SECONDS", 1.0
        ):
            started = time.monotonic() - 5.0  # already 5s in, budget is 1s
            self.assertFalse(providers.attempt_allowed(started, 0))

    def test_retry_reserves_budget_for_remaining_models(self):
        with patch.object(providers, "PROVIDER_MAX_ATTEMPTS", 3), patch.object(
            providers, "PROVIDER_MAX_TOTAL_SECONDS", 42
        ):
            started = time.monotonic()
            # 2 attempts used, 1 remaining model still needs a reserved slot,
            # 2 + 1 = 3 which is NOT < max_attempts(3) -> retry should be refused.
            self.assertFalse(providers.retry_allowed(started, 2, model_index=0, model_count=2))
            # With only the current (last) model remaining, no reservation needed.
            self.assertTrue(providers.retry_allowed(started, 2, model_index=1, model_count=2))

    def test_request_timeout_is_fair_share_of_remaining_budget(self):
        with patch.object(providers, "PROVIDER_TIMEOUT_SECONDS", 35), patch.object(
            providers, "PROVIDER_MAX_TOTAL_SECONDS", 10
        ):
            started = time.monotonic()
            timeout = providers.request_timeout(started, remaining_models=2)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 35)

    def test_bounded_backoff_never_sleeps_past_remaining_budget(self):
        with patch.object(providers, "PROVIDER_BACKOFF_SECONDS", 100), patch.object(
            providers, "PROVIDER_MAX_TOTAL_SECONDS", 0.05
        ):
            started = time.monotonic()
            elapsed_before = time.monotonic()
            providers.bounded_backoff(started, attempt=0)
            elapsed = time.monotonic() - elapsed_before
            # Backoff of 100s requested, but budget only allows ~0.05s -- must be capped.
            self.assertLess(elapsed, 1.0)


class FreeModelDetectionTests(unittest.TestCase):
    def test_explicit_free_suffix(self):
        self.assertTrue(providers.is_explicitly_free_model("openai/gpt-oss-20b:free"))

    def test_explicit_free_fallback_name(self):
        self.assertTrue(providers.is_explicitly_free_model("openrouter/free"))

    def test_non_free_model_rejected(self):
        self.assertFalse(providers.is_explicitly_free_model("openai/gpt-4"))

    def test_pricing_is_free_zero_cost(self):
        self.assertTrue(providers.pricing_is_free({"prompt": "0", "completion": "0"}))

    def test_pricing_is_free_nonzero_cost(self):
        self.assertFalse(providers.pricing_is_free({"prompt": "0.001", "completion": "0"}))

    def test_pricing_is_free_malformed_input(self):
        self.assertFalse(providers.pricing_is_free({"prompt": "not-a-number", "completion": "0"}))


class TransientStatusCodeTests(unittest.TestCase):
    def test_known_transient_codes(self):
        for code in (408, 409, 429, 500, 502, 503, 504):
            self.assertIn(code, providers.TRANSIENT_STATUS_CODES)

    def test_non_transient_codes_excluded(self):
        for code in (400, 401, 403, 404):
            self.assertNotIn(code, providers.TRANSIENT_STATUS_CODES)


if __name__ == "__main__":
    unittest.main()
