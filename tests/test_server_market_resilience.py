from __future__ import annotations

import concurrent.futures
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
STOCK_SERVER = ROOT / "server" / "stock"
if str(STOCK_SERVER) not in sys.path:
    sys.path.insert(0, str(STOCK_SERVER))

import astock  # noqa: E402
import market  # noqa: E402


class ServerMarketResilienceTests(unittest.TestCase):
    def setUp(self):
        for name in ("_CACHE", "_NEGATIVE_CACHE", "_IN_FLIGHT"):
            cache = getattr(market, name, None)
            if cache is not None:
                cache.clear()

    def test_akshare_call_has_a_hard_overall_timeout(self):
        class SlowAkshare:
            @staticmethod
            def slow_call():
                time.sleep(0.15)
                return "late"

        started = time.perf_counter()
        with patch.object(astock, "_akshare", return_value=SlowAkshare()):
            with self.assertRaises(TimeoutError):
                astock._call_akshare("slow_call", timeout=0.02)

        self.assertLess(time.perf_counter() - started, 0.1)

    def test_server_stock_calls_use_the_bounded_akshare_helper(self):
        market_source = (STOCK_SERVER / "market.py").read_text(encoding="utf-8")
        astock_source = (STOCK_SERVER / "astock.py").read_text(encoding="utf-8")

        self.assertNotIn("astock._akshare().", market_source)
        for call in (
            "ak.stock_profit_forecast_ths",
            "ak.stock_news_em",
            "ak.stock_individual_info_em",
            "ak.stock_zh_a_disclosure_report_cninfo",
            "ak.stock_financial_abstract_ths",
            "ak.stock_zh_valuation_baidu",
        ):
            self.assertNotIn(call, astock_source)

    def test_market_cache_is_prewarmed_when_the_api_starts(self):
        market_source = (STOCK_SERVER / "market.py").read_text(encoding="utf-8")
        app_source = (STOCK_SERVER / "base_app.py").read_text(encoding="utf-8")

        self.assertIn("def warm_cache", market_source)
        self.assertIn("market.warm_cache()", app_source)

    def test_overview_single_flight_merges_concurrent_requests(self):
        calls = {"sentiment": 0, "sectors": 0}
        lock = threading.Lock()

        def sentiment():
            with lock:
                calls["sentiment"] += 1
            time.sleep(0.08)
            return {"breadth": "中性"}

        def sectors():
            with lock:
                calls["sectors"] += 1
            time.sleep(0.08)
            return [{"name": "测试行业"}]

        with (
            patch.object(market, "_sentiment", side_effect=sentiment),
            patch.object(market, "_sectors", side_effect=sectors),
            concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool,
        ):
            results = list(pool.map(lambda _: market.get_overview(), range(4)))

        self.assertEqual(4, len(results))
        self.assertEqual({"sentiment": 1, "sectors": 1}, calls)

    def test_failed_overview_is_negative_cached(self):
        calls = {"sentiment": 0, "sectors": 0}

        def sentiment():
            calls["sentiment"] += 1
            return {}

        def sectors():
            calls["sectors"] += 1
            return []

        with (
            patch.object(market, "_sentiment", side_effect=sentiment),
            patch.object(market, "_sectors", side_effect=sectors),
        ):
            first = market.get_overview()
            second = market.get_overview()

        self.assertEqual(first, second)
        self.assertEqual({"sentiment": 1, "sectors": 1}, calls)

    def test_overview_fetches_independent_sources_in_parallel(self):
        def sentiment():
            time.sleep(0.15)
            return {"breadth": "中性"}

        def sectors():
            time.sleep(0.15)
            return [{"name": "测试行业"}]

        started = time.perf_counter()
        with (
            patch.object(market, "_sentiment", side_effect=sentiment),
            patch.object(market, "_sectors", side_effect=sectors),
        ):
            market.get_overview()

        self.assertLess(time.perf_counter() - started, 0.24)


if __name__ == "__main__":
    unittest.main()
