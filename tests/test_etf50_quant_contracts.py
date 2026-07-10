import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from quant import etf50_quant
from quant.backtest import BacktestResult, Backtester


def _price_frame(start: float, step: float) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=90, freq="D")
    close = [start + i * step for i in range(len(dates))]
    return pd.DataFrame(
        {
            "open": [value * 0.99 for value in close],
            "high": [value * 1.02 for value in close],
            "low": [value * 0.98 for value in close],
            "close": close,
            "volume": [100_000 + i * 100 for i in range(len(dates))],
        },
        index=dates,
    )


class ETF50QuantContractsTest(unittest.TestCase):
    def test_backtest_uses_all_valid_etfs_for_rolling_factor_rebalance(self):
        captured = {}
        frames = {
            "510001": _price_frame(10.0, 0.10),
            "510002": _price_frame(20.0, 0.05),
            "510003": _price_frame(30.0, -0.03),
        }

        def fake_get_ohlcv(code, days):
            return frames[code]

        def fake_run(self, price_data, signal_func, rebalance_freq):
            captured["price_codes"] = set(price_data)
            date = next(iter(price_data.values())).index[70]
            prior_data = {code: frame[frame.index < date] for code, frame in price_data.items()}
            captured["weights"] = signal_func(date, prior_data)
            return BacktestResult(
                total_return=0.01,
                annual_return=0.02,
                sharpe_ratio=1.0,
                max_drawdown=-0.01,
                total_trades=2,
                equity_curve=pd.Series([100_000, 101_000], index=list(prior_data.values())[0].index[:2]),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "etf50_pool.json").write_text(
                json.dumps(
                    {
                        "etfs": [
                            {"code": code, "name": code, "category": "test"}
                            for code in frames
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_ohlcv", side_effect=fake_get_ohlcv),
                patch.object(etf50_quant.time, "sleep", lambda _: None),
                patch.object(Backtester, "run", fake_run),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=1, verbose=False)

        self.assertEqual(set(frames), captured["price_codes"])
        self.assertTrue(captured["weights"])
        self.assertEqual(1, len(output["portfolio_weights"]))
        self.assertEqual(0.01, output["backtest"]["total_return"])


if __name__ == "__main__":
    unittest.main()
