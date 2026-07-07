import math
import re
import unittest
from pathlib import Path

import pandas as pd

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib

from quant.backtest import Backtester, strategy_mean_reversion, strategy_momentum
from quant.data import _is_etf, _standardize_columns
from quant.factors import compute_alpha158


ROOT = Path(__file__).resolve().parents[1]


def _price_frame(start: float = 10.0, step: float = 0.1, periods: int = 90) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=periods, freq="D")
    close = [start + i * step for i in range(periods)]
    return pd.DataFrame(
        {
            "open": [v * 0.99 for v in close],
            "high": [v * 1.02 for v in close],
            "low": [v * 0.98 for v in close],
            "close": close,
            "volume": [100_000 + i * 100 for i in range(periods)],
        },
        index=dates,
    )


class QuantCoreContractsTest(unittest.TestCase):
    def test_stock_and_etf_code_classification(self):
        for code in ["510300", "159915", "588000", "560000"]:
            self.assertTrue(_is_etf(code), code)

        for code in ["600519", "000001", "300750", "", "ABC"]:
            self.assertFalse(_is_etf(code), code)

    def test_ohlcv_standardization_from_chinese_columns(self):
        raw = pd.DataFrame(
            {
                "日期": ["2025-01-03", "2025-01-01", "2025-01-02"],
                "开盘": ["10.1", "9.8", "10.0"],
                "最高": ["10.5", "10.0", "10.3"],
                "最低": ["9.9", "9.7", "9.8"],
                "收盘": ["10.2", "9.9", "10.1"],
                "成交量": ["1200", "1000", "1100"],
                "成交额": ["12000", "9900", "11100"],
                "涨跌幅": ["1.0", "0.0", "2.0"],
            }
        )

        standardized = _standardize_columns(raw)

        self.assertEqual(
            ["open", "high", "low", "close", "volume", "amount", "change_pct"],
            list(standardized.columns),
        )
        self.assertTrue(standardized.index.is_monotonic_increasing)
        self.assertEqual(pd.Timestamp("2025-01-01"), standardized.index[0])
        self.assertTrue(all(pd.api.types.is_float_dtype(standardized[col]) for col in standardized.columns))

    def test_alpha158_factor_output_has_core_columns(self):
        factors = compute_alpha158(_price_frame())

        self.assertFalse(factors.empty)
        for column in ["ROC5", "MA20", "MACD", "RSI14", "VOLU5"]:
            self.assertIn(column, factors.columns)
        self.assertEqual(factors.index.name, None)

    def test_backtester_generates_basic_metrics(self):
        prices = {"AAA": _price_frame(), "BBB": _price_frame(start=20.0, step=0.05)}
        backtester = Backtester(initial_capital=100_000, commission_rate=0.0, stamp_duty=0.0, slippage=0.0)

        result = backtester.run(prices, lambda date, data: {"AAA": 0.5, "BBB": 0.5}, rebalance_freq="D")

        self.assertGreater(result.total_trades, 0)
        self.assertEqual(len(result.equity_curve), len(prices["AAA"]))
        self.assertTrue(math.isfinite(result.total_return))
        self.assertTrue(math.isfinite(result.max_drawdown))

    def test_strategy_helpers_return_expected_weights(self):
        prices = {
            "FAST": _price_frame(start=10.0, step=0.2),
            "SLOW": _price_frame(start=10.0, step=0.05),
            "DOWN": _price_frame(start=30.0, step=-0.1),
        }

        momentum = strategy_momentum(prices["FAST"].index[-1], prices, top_n=2)
        self.assertEqual({"FAST", "SLOW"}, set(momentum))
        self.assertAlmostEqual(1.0, sum(momentum.values()))

        mean_reversion = strategy_mean_reversion(prices["FAST"].index[-1], prices, top_n=1)
        self.assertEqual({"DOWN"}, set(mean_reversion))
        self.assertAlmostEqual(1.0, sum(mean_reversion.values()))

    def test_config_example_is_parseable_and_non_secret(self):
        config = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))

        self.assertIn("llm", config)
        self.assertIn("analysis", config)
        self.assertIn("output", config)
        self.assertIn("paths", config)
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9_-]{20,}", text))
        self.assertNotIn("sk-your", text)
