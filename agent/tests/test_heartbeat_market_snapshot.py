from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from heartbeat import (
    collect_market_snapshot,
    evaluate_watched_symbol,
    heartbeat_risk_score,
)


DATA_LAYER_CONFIG = {
    "watchlist_refresh_frequency_seconds": 300,
    "stooq_validation_interval_seconds": 3600,
    "agreement_tolerance_percent": 0.5,
    "minor_difference_percent": 2.0,
}


def make_yf_download_frame(symbols: list[str], closes: dict[str, list[float]]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(next(iter(closes.values()))), freq="D")
    columns = pd.MultiIndex.from_tuples(
        [(symbol, field) for symbol in symbols for field in ("Close", "Adj Close", "Volume")]
    )
    frame = pd.DataFrame(index=dates, columns=columns, dtype="float64")
    for symbol in symbols:
        frame[(symbol, "Close")] = closes[symbol]
        frame[(symbol, "Adj Close")] = closes[symbol]
        frame[(symbol, "Volume")] = 1_000_000
    return frame


class HeartbeatRiskScoreTests(unittest.TestCase):
    """Confirms the risk-routing/scoring patch (agent/tools/risk.py) did not break the
    heartbeat's own watchlist scoring -- heartbeat_risk_score must always return a real
    int, bypassing the new score_available gate meant for user-facing chat memos."""

    def test_always_returns_an_int_even_without_fundamentals(self):
        # A heartbeat-style context: price/change only, never beta/debt/current_ratio --
        # exactly the shape that made assess_score_availability() return False.
        market_context = {"found": True, "symbol": "TSLA", "price": 250.0, "change_percent": 1.5}
        score = heartbeat_risk_score("TSLA", market_context)
        self.assertIsInstance(score, int)

    def test_score_reflects_price_move(self):
        calm = heartbeat_risk_score("GM", {"found": True, "symbol": "GM", "price": 40.0, "change_percent": 0.1})
        volatile = heartbeat_risk_score("GM", {"found": True, "symbol": "GM", "price": 40.0, "change_percent": 9.0})
        self.assertGreaterEqual(volatile, calm)


class EvaluateWatchedSymbolRobustnessTests(unittest.TestCase):
    """Regression test for the exact crash this bug report traced: a None risk_score
    (e.g. from stale persisted state) must never raise inside evaluate_watched_symbol,
    since that crash was what stopped state["last_values"] from ever updating and froze
    the market ticker row on "Unavailable" forever."""

    def _thresholds(self):
        return {
            "intraday_move_percent": 3.0,
            "five_day_move_percent": 6.0,
            "critical_move_percent": 8.0,
            "risk_score": 65,
            "critical_risk_score": 80,
            "risk_score_increase": 15,
        }

    def test_none_current_risk_score_does_not_raise(self):
        state = {"history": []}
        values = {"intraday_move_percent": 1.0, "five_day_move_percent": 1.0, "risk_score": None}
        evaluate_watched_symbol(
            state, "TSLA", values, None, self._thresholds(), {"start": "22:00", "end": "08:00"}, set(), set(),
            __import__("datetime").datetime.now().astimezone(),
        )  # must not raise

    def test_none_previous_risk_score_does_not_raise(self):
        state = {"history": []}
        values = {"intraday_move_percent": 1.0, "five_day_move_percent": 1.0, "risk_score": 70}
        previous = {"risk_score": None}
        evaluate_watched_symbol(
            state, "TSLA", values, previous, self._thresholds(), {"start": "22:00", "end": "08:00"}, set(), set(),
            __import__("datetime").datetime.now().astimezone(),
        )  # must not raise

    def test_real_risk_score_still_triggers_threshold_condition(self):
        state = {"history": []}
        values = {"intraday_move_percent": 1.0, "five_day_move_percent": 1.0, "risk_score": 90}
        current_conditions = set()
        evaluate_watched_symbol(
            state, "TSLA", values, None, self._thresholds(), {"start": "22:00", "end": "08:00"}, set(), current_conditions,
            __import__("datetime").datetime.now().astimezone(),
        )
        self.assertIn("risk_score:TSLA", current_conditions)


class CollectMarketSnapshotTests(unittest.TestCase):
    """End-to-end (mocked network) tests for the watchlist snapshot the ticker row reads."""

    def test_each_watchlist_symbol_gets_a_result_with_stable_schema(self):
        symbols = ["TSLA", "F", "GM", "NVDA", "JPM", "BAC", "MTB"]
        closes = {s: [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0] for s in symbols}
        frame = make_yf_download_frame(symbols, closes)

        with patch("yfinance.download", return_value=frame), patch(
            "heartbeat.validate_price_sources",
            side_effect=lambda symbol, payload, **kwargs: {
                "found": True,
                "price": payload["bars"][-1]["close"] if payload.get("bars") else 106.0,
                "change_percent": 0.95,
                "data_source": "yfinance",
                "confidence": {"level": "Medium"},
            },
        ):
            snapshot = collect_market_snapshot(symbols, symbols, DATA_LAYER_CONFIG)

        self.assertEqual(set(snapshot["symbols"].keys()), set(symbols))
        for symbol in symbols:
            result = snapshot["symbols"][symbol]
            for field in ("price", "intraday_move_percent", "five_day_move_percent", "risk_score", "source"):
                self.assertIn(field, result)
            self.assertIsNotNone(result["price"])
            self.assertIsInstance(result["risk_score"], int)
        self.assertEqual(snapshot["errors"], [])

    def test_stooq_failure_does_not_wipe_out_yfinance_values(self):
        symbols = ["TSLA"]
        closes = {"TSLA": [240.0, 241.0, 242.0, 243.0, 244.0, 245.0, 250.0]}
        frame = make_yf_download_frame(symbols, closes)

        # validate_price_sources degrading gracefully (Stooq unavailable) still reports
        # found=True with the yfinance price -- this must not blank out the ticker.
        with patch("yfinance.download", return_value=frame), patch(
            "heartbeat.validate_price_sources",
            return_value={
                "found": True,
                "price": 250.0,
                "change_percent": 2.04,
                "data_source": "yfinance",
                "confidence": {"level": "Low", "reason": "Stooq cross-check unavailable."},
            },
        ):
            snapshot = collect_market_snapshot(symbols, symbols, DATA_LAYER_CONFIG)

        result = snapshot["symbols"]["TSLA"]
        self.assertEqual(result["price"], 250.0)
        self.assertEqual(result["source"], "yfinance")
        self.assertIsInstance(result["risk_score"], int)

    def test_one_symbol_truly_failing_does_not_blank_the_others(self):
        # TSLA fails at both the primary parse AND the last-resort validate_price_sources
        # fallback (a genuine total failure); NVDA succeeds normally on the primary path.
        # Only TSLA should end up absent/errored -- NVDA's real data must be untouched.
        symbols = ["TSLA", "NVDA"]
        closes = {"TSLA": [240.0] * 7, "NVDA": [900.0, 905.0, 910.0, 915.0, 920.0, 925.0, 930.0]}
        frame = make_yf_download_frame(symbols, closes)
        original_close_series = __import__("heartbeat").close_series

        def flaky_close_series(frame_arg, symbol, count):
            if symbol == "TSLA":
                raise KeyError("simulated feed failure for TSLA")
            return original_close_series(frame_arg, symbol, count)

        def symbol_aware_validate(symbol, payload, **kwargs):
            if symbol == "TSLA":
                return {"found": False}
            return {"found": True, "price": 930.0, "change_percent": 0.5, "data_source": "yfinance"}

        with patch("yfinance.download", return_value=frame), patch(
            "heartbeat.close_series", side_effect=flaky_close_series
        ), patch("heartbeat.validate_price_sources", side_effect=symbol_aware_validate):
            snapshot = collect_market_snapshot(symbols, symbols, DATA_LAYER_CONFIG)

        self.assertNotIn("TSLA", snapshot["symbols"])
        self.assertIn("NVDA", snapshot["symbols"])
        self.assertEqual(snapshot["symbols"]["NVDA"]["price"], 930.0)
        self.assertTrue(any("TSLA" in error for error in snapshot["errors"]))

    def test_one_symbol_recovers_via_fallback_when_backup_source_has_data(self):
        # If the primary parse fails but the backup/Stooq-style validator still has usable
        # data for that symbol, the ticker should recover rather than being blanked --
        # this is the "graceful fallback" behavior requirement #4 asks for.
        symbols = ["TSLA"]
        closes = {"TSLA": [240.0] * 7}
        frame = make_yf_download_frame(symbols, closes)

        with patch("yfinance.download", return_value=frame), patch(
            "heartbeat.close_series", side_effect=KeyError("simulated primary feed failure")
        ), patch(
            "heartbeat.validate_price_sources",
            return_value={"found": True, "price": 245.0, "change_percent": 2.1, "data_source": "stooq"},
        ):
            snapshot = collect_market_snapshot(symbols, symbols, DATA_LAYER_CONFIG)

        self.assertIn("TSLA", snapshot["symbols"])
        self.assertEqual(snapshot["symbols"]["TSLA"]["price"], 245.0)
        self.assertEqual(snapshot["symbols"]["TSLA"]["source"], "stooq")


if __name__ == "__main__":
    unittest.main()
