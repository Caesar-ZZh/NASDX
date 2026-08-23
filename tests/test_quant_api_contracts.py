from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
STOCK_SERVER = ROOT / "server" / "stock"
if str(STOCK_SERVER) not in sys.path:
    sys.path.insert(0, str(STOCK_SERVER))

import quant_service  # noqa: E402


def price_frame(multiplier: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=100, freq="B")
    close = np.linspace(10.0, 15.0 * multiplier, len(dates))
    return pd.DataFrame(
        {
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(len(dates), 1_000_000),
        },
        index=dates,
    )


class QuantApiContractTests(unittest.TestCase):
    def setUp(self):
        quant_service.clear_caches()

    def test_backtest_returns_objective_multi_strategy_series(self):
        frames = {
            "510300": price_frame(1.0),
            "510500": price_frame(1.1),
            "159915": price_frame(0.95),
        }
        payload = {
            "universe": list(frames),
            "strategies": ["momentum", "mean_reversion"],
            "start": "2025-01-01",
            "end": "2025-05-20",
            "initial_capital": 100_000,
            "rebalance": "W",
            "top_n": 2,
        }

        with patch("quant.data.get_batch_ohlcv", return_value=frames) as batch:
            result = quant_service.compute_backtest(payload)

        self.assertEqual(["momentum", "mean_reversion"], [row["strategy"] for row in result["strategies"]])
        self.assertEqual(3, result["coverage"]["available"])
        self.assertTrue(all(row["equity_curve"] for row in result["strategies"]))
        self.assertFalse(batch.call_args.kwargs["fallback_missing"])
        self.assertEqual("objective_calculation", result["result_type"])
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("recommendation", rendered.lower())
        self.assertNotIn("建议买入", rendered)

    def test_backtest_rejects_unknown_strategy_and_bad_codes(self):
        with self.assertRaises(ValueError):
            quant_service.compute_backtest({"universe": ["510300"], "strategies": ["future_oracle"]})
        with self.assertRaises(ValueError):
            quant_service.compute_backtest({"universe": ["51030X"], "strategies": ["momentum"]})

    def test_single_symbol_can_use_default_top_n(self):
        normalized = quant_service.normalize_backtest_request(
            {"universe": ["510300"], "strategies": ["momentum"]}
        )
        self.assertEqual(3, normalized["top_n"])

    def test_guard_has_hard_timeout(self):
        started = time.perf_counter()
        with self.assertRaises(TimeoutError):
            quant_service.run_guarded("slow-test", lambda: time.sleep(0.15), timeout=0.02)
        self.assertLess(time.perf_counter() - started, 0.1)

    def test_failures_are_negative_cached(self):
        calls = 0

        def fail():
            nonlocal calls
            calls += 1
            raise RuntimeError("upstream unavailable")

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                quant_service.run_guarded("negative-test", fail, timeout=1)
        self.assertEqual(1, calls)

    def test_etf50_endpoint_is_read_only(self):
        fake = {
            "results": [{"code": "510300", "quant_score": 61.2, "signal": "bullish", "reasons": ["test"]}],
            "bullish": 1,
            "bearish": 0,
            "top3": [{"code": "510300", "signal": "bullish"}],
            "_saved_to": "should-not-leak",
        }
        with patch("quant.etf50_quant.run_etf50_quant", return_value=fake) as run:
            result = quant_service.compute_etf50(days=180, top_n=5, rebalance="W")

        run.assert_called_once_with(
            days=180,
            top_n=5,
            rebalance_freq="W",
            verbose=False,
            save_report=False,
            allow_legacy_fallback=False,
            run_backtest=False,
        )
        self.assertNotIn("_saved_to", result)
        self.assertEqual("objective_calculation", result["result_type"])
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("bullish", rendered)
        self.assertNotIn("bearish", rendered)
        self.assertNotIn('"signal"', rendered)
        self.assertNotIn('"reasons"', rendered)

    def test_routes_and_existing_etf_default_are_preserved(self):
        router_source = (STOCK_SERVER / "quant_router.py").read_text(encoding="utf-8")
        app_source = (STOCK_SERVER / "base_app.py").read_text(encoding="utf-8")
        etf_source = (ROOT / "quant" / "etf50_quant.py").read_text(encoding="utf-8")

        self.assertIn('router.post("/api/quant/backtest")', router_source)
        self.assertIn('router.get("/api/quant/etf50")', router_source)
        self.assertIn("app.include_router(quant_router.router)", app_source)
        self.assertIn("save_report: bool = True", etf_source)


if __name__ == "__main__":
    unittest.main()
