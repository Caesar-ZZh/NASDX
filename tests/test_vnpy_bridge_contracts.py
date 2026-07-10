import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from quant.vnpy_bridge import calc_performance_vnpy


ROOT = Path(__file__).resolve().parents[1]


class VnPyBridgeContractsTest(unittest.TestCase):
    def test_performance_fallback_returns_public_metrics_without_vnpy(self):
        equity = pd.Series(
            [100_000.0, 101_000.0, 99_500.0, 104_000.0],
            index=pd.date_range("2025-01-01", periods=4, freq="D"),
        )
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("vnpy"):
                raise ImportError("vnpy unavailable")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = calc_performance_vnpy(equity)

        for key in [
            "total_return",
            "annual_return",
            "max_drawdown",
            "sharpe_ratio",
            "calmar_ratio",
            "win_rate",
            "total_days",
            "max_losing_streak",
        ]:
            self.assertIn(key, result)
        self.assertEqual(4, result["total_days"])
        self.assertAlmostEqual(0.04, result["total_return"])

    def test_vnpy_bridge_does_not_force_import_error_to_hide_placeholder(self):
        source = (ROOT / "quant" / "vnpy_bridge.py").read_text(encoding="utf-8")

        self.assertNotIn('raise ImportError("use pandas fallback")', source)
        self.assertIn("_calc_performance_pandas", source)


if __name__ == "__main__":
    unittest.main()
