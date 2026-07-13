import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd


def _history_frame() -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=70, freq="D")
    close = pd.Series([10 + index * 0.02 for index in range(70)])
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": close * 0.99,
            "收盘": close,
            "最高": close * 1.01,
            "最低": close * 0.98,
            "成交量": [100_000 + index for index in range(70)],
            "涨跌幅": close.pct_change().fillna(0) * 100,
        }
    )


class RuntimeMarketReliabilityTest(unittest.TestCase):
    def test_tencent_history_uses_one_direct_bounded_request(self):
        from nasdx.market_sources import fetch_stock_hist

        rows = [[f"2026-04-{day:02d}", "10", str(10 + day / 100), "10.5", "9.8", "100000"] for day in range(1, 21)]
        response = unittest.mock.Mock()
        response.text = 'kline_dayqfq2026=' + __import__("json").dumps(
            {"data": {"sh600000": {"qfqday": rows}}}
        )
        response.raise_for_status.return_value = None

        with patch("nasdx.market_sources.requests.get", return_value=response) as get:
            frame, source = fetch_stock_hist(
                "600000",
                "20260401",
                "20260430",
                min_rows=20,
                request_timeout=2.5,
                sources=("tencent_hist_tx",),
            )

        self.assertEqual("tencent_hist_tx", source)
        self.assertEqual(20, len(frame))
        self.assertEqual(2.5, get.call_args.kwargs["timeout"])

    def test_tencent_quote_payload_is_parsed_without_eastmoney(self):
        from nasdx.fast_market import parse_tencent_quotes

        payload = (
            'v_sh600000="1~浦发银行~600000~9.19~9.06~9.04~757620~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~'
            '20260713161449~0.13~1.43~9.21~9.01~9.19/757620/693325381~757620~69333~0.23";'
        )

        quotes = parse_tencent_quotes(payload)

        self.assertEqual("浦发银行", quotes["600000"]["name"])
        self.assertEqual(9.19, quotes["600000"]["close"])
        self.assertEqual(1.43, quotes["600000"]["change_pct"])
        self.assertEqual(693_330_000.0, quotes["600000"]["amount"])
        self.assertEqual(0.23, quotes["600000"]["turnover"])

    def test_history_batch_is_concurrent_and_propagates_request_timeout(self):
        from nasdx.fast_market import fetch_histories

        calls = []

        def fake_fetcher(code, start_date, end_date, min_rows, request_timeout, sources):
            calls.append((code, request_timeout, tuple(sources)))
            time.sleep(0.08)
            return _history_frame(), "tencent_hist_tx"

        started = time.monotonic()
        results = fetch_histories(
            ["600000", "000001", "300001", "600001"],
            "20260401",
            "20260713",
            request_timeout=3.0,
            max_workers=4,
            hist_fetcher=fake_fetcher,
            use_disk_cache=False,
        )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.22)
        self.assertEqual(4, len(results))
        self.assertTrue(all(timeout == 3.0 for _, timeout, _ in calls))
        self.assertTrue(all(sources == ("tencent_hist_tx",) for _, _, sources in calls))

    def test_history_batch_retries_only_missing_symbols_with_longer_timeout(self):
        from nasdx.fast_market import fetch_histories

        calls = []

        def flaky_fetcher(code, start_date, end_date, min_rows, request_timeout, sources):
            calls.append((code, request_timeout))
            if code == "600000" and request_timeout < 6:
                return None, None
            return _history_frame(), "tencent_hist_tx"

        results = fetch_histories(
            ["600000", "000001"],
            "20260401",
            "20260713",
            request_timeout=3.0,
            max_workers=2,
            hist_fetcher=flaky_fetcher,
            use_disk_cache=False,
        )

        self.assertIsNotNone(results["600000"][0])
        self.assertEqual([("600000", 3.0), ("600000", 6.0)], [call for call in calls if call[0] == "600000"])
        self.assertEqual(1, sum(code == "000001" for code, _ in calls))

    def test_history_batch_reuses_short_lived_disk_cache(self):
        from nasdx.fast_market import fetch_histories

        calls = []

        def fake_fetcher(code, start_date, end_date, min_rows, request_timeout, sources):
            calls.append(code)
            return _history_frame(), "tencent_hist_tx"

        with TemporaryDirectory() as cache_dir:
            first = fetch_histories(
                ["600000"],
                "20260401",
                "20260713",
                hist_fetcher=fake_fetcher,
                cache_dir=Path(cache_dir),
            )
            second = fetch_histories(
                ["600000"],
                "20260401",
                "20260713",
                hist_fetcher=fake_fetcher,
                cache_dir=Path(cache_dir),
            )

        self.assertEqual(["600000"], calls)
        self.assertEqual(len(first["600000"][0]), len(second["600000"][0]))

    def test_history_cache_falls_back_when_windows_atomic_replace_fails(self):
        from nasdx.fast_market import _write_history_cache

        with TemporaryDirectory() as cache_dir:
            path = Path(cache_dir) / "history.json"
            with patch("pathlib.Path.replace", side_effect=OSError("WinError 6")):
                _write_history_cache(path, _history_frame(), "tencent_hist_tx")

            self.assertTrue(path.exists())

    def test_selector_universe_uses_listings_plus_batched_quotes(self):
        from nasdx.selector import universe

        listings = [
            {"code": "600000", "name": "浦发银行", "sector": "银行"},
            {"code": "000001", "name": "平安银行", "sector": "银行"},
        ]
        quotes = {
            "600000": {"code": "600000", "name": "浦发银行", "close": 9.19, "change_pct": 1.43, "amount": 9e8, "turnover": 0.23},
            "000001": {"code": "000001", "name": "平安银行", "close": 10.54, "change_pct": 0.86, "amount": 8e8, "turnover": 0.48},
        }
        with (
            patch.object(universe, "load_a_share_listings", return_value=listings),
            patch.object(universe, "fetch_tencent_quotes", return_value=quotes),
        ):
            stocks = universe.load_full_a_stocks()

        self.assertEqual(["600000", "000001"], [stock["code"] for stock in stocks])
        self.assertEqual("银行", stocks[0]["sector"])
        self.assertEqual(9e8, stocks[0]["amount"])

    def test_selector_factors_use_bounded_tencent_history(self):
        from nasdx.selector.factors import compute_factors_for_stocks

        calls = []

        def fake_history(codes, start_date, end_date, **kwargs):
            calls.append((codes, kwargs))
            return {code: (_history_frame(), "tencent_hist_tx") for code in codes}

        stocks = [
            {"code": "600000", "name": "浦发银行", "close": 9.19, "change_pct": 1.43, "amount": 9e8, "turnover": 0.23, "sector": "银行"}
        ]
        result = compute_factors_for_stocks(stocks, history_fetcher=fake_history, request_timeout=4.0)

        self.assertEqual(1, len(result))
        self.assertEqual("tencent_hist_tx", result[0]["data_source"])
        self.assertEqual(4.0, calls[0][1]["request_timeout"])

    def test_market_regime_uses_available_quotes_and_never_removed_akshare_api(self):
        from nasdx.selector.market_regime import assess_market_regime

        stocks = [
            {"change_pct": 1.0, "amount": 8e8},
            {"change_pct": -0.5, "amount": 5e8},
            {"change_pct": 9.9, "amount": 3e8},
        ]
        histories = {code: (_history_frame(), "tencent_hist_tx") for code in ["000001", "399001", "399006", "000300"]}

        result = assess_market_regime(stocks=stocks, history_fetcher=lambda *args, **kwargs: histories)

        self.assertIn(result["regime"], {"bullish", "bearish", "neutral", "structural", "mixed"})
        self.assertEqual(3, result["components"]["advance_decline"]["total"])
        source = Path("nasdx/selector/market_regime.py").read_text(encoding="utf-8")
        self.assertNotIn("stock_zh_market_alerts_em", source)

    def test_web_scans_default_to_fast_bounded_runtime(self):
        selector_source = Path("selector_page.py").read_text(encoding="utf-8")
        etf_source = Path("scan_etf50.py").read_text(encoding="utf-8")
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn('"selector_limit", 50', selector_source)
        self.assertIn('"selector_timeout", 180', selector_source)
        self.assertIn("fetch_tencent_quotes", etf_source)
        self.assertIn("fetch_histories", etf_source)
        self.assertIn("score(ind, None)", etf_source)
        self.assertNotIn("实时溢价率", etf_source)
        self.assertNotIn("实时溢价率", app_source)
        self.assertNotIn("fund_etf_spot_em", etf_source)
        self.assertNotIn("fund_etf_fund_info_em", etf_source)


if __name__ == "__main__":
    unittest.main()
