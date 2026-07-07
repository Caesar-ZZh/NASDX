import unittest
from unittest.mock import patch
import pandas as pd

from quant.data import _get_tdxrs_market, get_ohlcv, get_realtime_quotes


class TdxrsIntegrationContractsTest(unittest.TestCase):
    def test_market_classification(self):
        # Shanghai market (1)
        for code in ["600519", "600000", "688001", "510300", "510050", "588000"]:
            self.assertEqual(_get_tdxrs_market(code), 1, f"Expected 1 (SH) for {code}")

        # Shenzhen market (0)
        for code in ["000001", "000858", "300750", "159915", "161725"]:
            self.assertEqual(_get_tdxrs_market(code), 0, f"Expected 0 (SZ) for {code}")

    def test_get_ohlcv_graceful_fallback_when_tdxrs_unavailable(self):
        # Mock _get_tdxrs to return None (simulating unavailable or import failure)
        with patch("quant.data._get_tdxrs", return_value=None):
            # Also mock _get_akshare to return sample data to ensure fallback path works
            mock_df = pd.DataFrame({
                "日期": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"],
                "开盘": [10, 10.1, 10.2, 10.3, 10.4],
                "最高": [10.5, 10.6, 10.7, 10.8, 10.9],
                "最低": [9.8, 9.9, 10.0, 10.1, 10.2],
                "收盘": [10.2, 10.3, 10.4, 10.5, 10.6],
                "成交量": [1000, 1100, 1200, 1300, 1400],
            })
            with patch("quant.data._get_akshare", return_value=mock_df):
                result = get_ohlcv("600519", days=30, source="tdxrs")
                # When source="tdxrs" explicitly and it returns None, it falls through to empty or fallback
                self.assertIsInstance(result, pd.DataFrame)

                result_auto = get_ohlcv("600519", days=30, source="auto")
                self.assertFalse(result_auto.empty)
                self.assertIn("close", result_auto.columns)

    def test_get_realtime_quotes_fallback(self):
        # When tdxrs and ths_bridge are unavailable, fallback to empty or akshare
        with patch("quant.data._get_tdxrs_quotes", return_value={}):
            quotes = get_realtime_quotes(["600519"])
            self.assertIsInstance(quotes, dict)


if __name__ == "__main__":
    unittest.main()
