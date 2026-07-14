import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from nasdx.fast_market import fetch_histories
from quant.data import get_batch_ohlcv


def _history_frame(code: str = "600000") -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=30, freq="D")
    close = pd.Series([10.0 + index * 0.1 for index in range(len(dates))])
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": close * 0.99,
            "最高": close * 1.01,
            "最低": close * 0.98,
            "收盘": close,
            "成交量": [100_000 + index for index in range(len(dates))],
            "成交额": [1_000_000 + index for index in range(len(dates))],
            "涨跌幅": close.pct_change().fillna(0) * 100,
            "代码": code,
        }
    )


def _standard_frame() -> pd.DataFrame:
    raw = _history_frame()
    return pd.DataFrame(
        {
            "open": raw["开盘"].to_numpy(),
            "high": raw["最高"].to_numpy(),
            "low": raw["最低"].to_numpy(),
            "close": raw["收盘"].to_numpy(),
            "volume": raw["成交量"].to_numpy(),
        },
        index=pd.to_datetime(raw["日期"]),
    )


class QuantDataBatchContractsTest(unittest.TestCase):
    def test_batch_uses_fast_history_service_once_and_standardizes_results(self):
        stock_raw = _history_frame("600000")
        etf_raw = _history_frame("510300")
        fast_results = {
            "600000": (stock_raw, "tdxrs"),
            "510300": (etf_raw, "tencent_hist_tx"),
        }

        with patch("nasdx.fast_market.fetch_histories", return_value=fast_results) as fetch_mock:
            with patch("quant.data.get_ohlcv") as legacy_mock:
                result = get_batch_ohlcv(
                    ["600000", "600000", "510300"],
                    days=90,
                    verbose=False,
                    max_workers=4,
                    use_cache=True,
                    cache_ttl_seconds=123.0,
                )

        self.assertEqual(["600000", "510300"], list(result))
        legacy_mock.assert_not_called()
        fetch_mock.assert_called_once()
        args, kwargs = fetch_mock.call_args
        self.assertEqual(["600000", "510300"], args[0])
        self.assertEqual(4, kwargs["max_workers"])
        self.assertTrue(kwargs["use_disk_cache"])
        self.assertEqual(123.0, kwargs["cache_ttl_seconds"])
        for frame in result.values():
            self.assertEqual(
                ["open", "high", "low", "close", "volume", "amount", "change_pct"],
                list(frame.columns),
            )
            self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertIn("日期", stock_raw.columns, "standardization must not mutate cached source frames")

    def test_batch_runs_legacy_fallback_only_for_unresolved_symbols(self):
        fast_results = {
            "600000": (_history_frame("600000"), "tdxrs"),
            "920185": (None, None),
        }
        fallback = _standard_frame()

        with patch("nasdx.fast_market.fetch_histories", return_value=fast_results) as fetch_mock:
            with patch("quant.data.get_ohlcv", return_value=fallback) as legacy_mock:
                result = get_batch_ohlcv(
                    ["600000", "920185"],
                    days=60,
                    verbose=False,
                    use_cache=False,
                )

        legacy_mock.assert_called_once_with("920185", days=60)
        self.assertFalse(fetch_mock.call_args.kwargs["use_disk_cache"])
        self.assertEqual(["600000", "920185"], list(result))
        self.assertIsNot(result["920185"], fallback)

    def test_batch_falls_back_for_every_symbol_when_fast_service_fails(self):
        fallback = _standard_frame()

        with patch("nasdx.fast_market.fetch_histories", side_effect=RuntimeError("offline")):
            with patch("quant.data.get_ohlcv", return_value=fallback) as legacy_mock:
                result = get_batch_ohlcv(["600000", "510300"], days=30, verbose=False)

        self.assertEqual(["600000", "510300"], list(result))
        self.assertEqual(
            [("600000",), ("510300",)],
            [call.args for call in legacy_mock.call_args_list],
        )
        self.assertTrue(all(call.kwargs == {"days": 30} for call in legacy_mock.call_args_list))

    def test_fast_history_disk_cache_avoids_repeat_fetch_and_returns_fresh_frame(self):
        calls = []

        def fake_fetcher(code, start_date, end_date, min_rows, request_timeout, sources):
            calls.append(code)
            return _history_frame(code), "fake_source"

        with TemporaryDirectory() as temp_dir:
            kwargs = {
                "hist_fetcher": fake_fetcher,
                "batch_hist_fetcher": None,
                "sources": ("fake_source",),
                "use_disk_cache": True,
                "cache_dir": Path(temp_dir),
                "cache_ttl_seconds": 600.0,
                "min_rows": 5,
            }
            first = fetch_histories(["600000"], "20260401", "20260630", **kwargs)
            first["600000"][0].loc[0, "收盘"] = -1
            second = fetch_histories(["600000"], "20260401", "20260630", **kwargs)

        self.assertEqual(["600000"], calls)
        self.assertNotEqual(-1, second["600000"][0].loc[0, "收盘"])

    def test_batch_has_no_fixed_per_symbol_sleep(self):
        source = inspect.getsource(get_batch_ohlcv)
        self.assertNotIn("time.sleep(0.3)", source)


if __name__ == "__main__":
    unittest.main()
