import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pandas as pd


def _history_frame() -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=20, freq="D")
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": [10.0] * 20,
            "收盘": [10.1] * 20,
            "最高": [10.2] * 20,
            "最低": [9.9] * 20,
            "成交量": [100_000] * 20,
            "涨跌幅": [0.1] * 20,
        }
    )


class BseMarketCoverageTest(unittest.TestCase):
    def test_official_bse_code_mapping_table_is_parsed_structurally(self):
        from nasdx.market_sources import _parse_bse_code_mapping

        content = """
        <table>
          <tr><td>序号</td><td>证券简称</td><td>上市日期</td><td>旧代码</td><td>新代码</td></tr>
          <tr><td>1</td><td>诺思兰德</td><td>2020/11/24</td><td>430047</td><td>920047</td></tr>
        </table>
        """.encode("utf-8")

        self.assertEqual({"430047": "920047"}, _parse_bse_code_mapping(content))

    def test_exchange_resolver_covers_main_boards_and_all_bse_code_families(self):
        from nasdx.market_symbols import market_symbol, resolve_exchange

        expected = {
            "600000": ("SSE", "sh600000"),
            "688001": ("SSE", "sh688001"),
            "000001": ("SZSE", "sz000001"),
            "300001": ("SZSE", "sz300001"),
            "430047": ("BSE", "bj430047"),
            "830799": ("BSE", "bj830799"),
            "920185": ("BSE", "bj920185"),
        }
        for code, (exchange, symbol) in expected.items():
            with self.subTest(code=code):
                self.assertEqual(exchange, resolve_exchange(code))
                self.assertEqual(symbol, market_symbol(code))

    def test_tencent_history_routes_new_bse_code_to_bj_namespace(self):
        from nasdx.market_sources import fetch_stock_hist

        rows = [[f"2026-04-{day:02d}", "10", "10.1", "10.2", "9.9", "100000"] for day in range(1, 21)]
        response = Mock()
        response.text = "kline_dayqfq2026=" + json.dumps({"data": {"bj920185": {"qfqday": rows}}})
        response.raise_for_status.return_value = None

        with patch("nasdx.market_sources.requests.get", return_value=response) as request:
            frame, source = fetch_stock_hist(
                "920185",
                "20260401",
                "20260430",
                min_rows=20,
                sources=("tencent_hist_tx",),
            )

        self.assertEqual("tencent_hist_tx", source)
        self.assertEqual(20, len(frame))
        self.assertIn("bj920185,day", request.call_args.kwargs["params"]["param"])

    def test_tencent_history_resolves_legacy_bse_code_to_current_code(self):
        from nasdx.market_sources import fetch_stock_hist

        rows = [[f"2026-04-{day:02d}", "10", "10.1", "10.2", "9.9", "100000"] for day in range(1, 21)]
        response = Mock()
        response.text = "kline_dayqfq2026=" + json.dumps({"data": {"bj920047": {"qfqday": rows}}})
        response.raise_for_status.return_value = None

        with (
            patch("nasdx.market_sources._get_bse_code_mapping", return_value={"430047": "920047"}),
            patch("nasdx.market_sources.requests.get", return_value=response) as request,
        ):
            frame, source = fetch_stock_hist(
                "430047",
                "20260401",
                "20260430",
                min_rows=20,
                sources=("tencent_hist_tx",),
            )

        self.assertEqual("tencent_hist_tx", source)
        self.assertEqual(20, len(frame))
        self.assertIn("bj920047,day", request.call_args.kwargs["params"]["param"])

    def test_bse_official_jsonp_page_is_parsed_to_structured_listings(self):
        from nasdx.fast_market import _parse_bse_page

        payload = {
            "content": [
                {"xxzqdm": "920185", "xxzqjc": "贝特瑞", "xxhyzl": "非金属矿物制品业"},
                {"xxzqdm": "", "xxzqjc": "invalid", "xxhyzl": ""},
            ],
            "totalElements": 327,
            "totalPages": 17,
        }
        parsed = _parse_bse_page(f"nasdxBseCallback({json.dumps([payload], ensure_ascii=False)})")

        self.assertEqual(17, parsed["total_pages"])
        self.assertEqual(
            {
                "code": "920185",
                "name": "贝特瑞",
                "sector": "非金属矿物制品业",
                "exchange": "BSE",
            },
            parsed["listings"][0],
        )

    def test_listing_coverage_reports_bse_source_failure_explicitly(self):
        from nasdx.fast_market import _build_listing_coverage

        coverage = _build_listing_coverage(
            [
                {"code": "600000", "exchange": "SSE"},
                {"code": "000001", "exchange": "SZSE"},
            ],
            {"SSE": True, "SZSE": True, "BSE": False},
        )

        self.assertFalse(coverage["complete"])
        self.assertEqual(["BSE"], coverage["unavailable_exchanges"])
        self.assertEqual(0, coverage["counts"]["BSE"])

    def test_bse_history_cache_uses_exchange_qualified_key(self):
        from nasdx.fast_market import fetch_histories

        with TemporaryDirectory() as cache_dir:
            fetch_histories(
                ["920185"],
                "20260401",
                "20260714",
                hist_fetcher=lambda *args: (_history_frame(), "tencent_hist_tx"),
                cache_dir=Path(cache_dir),
            )
            names = [path.name for path in Path(cache_dir).glob("*.json")]

        self.assertEqual(["bj920185_20260401_20260714.json"], names)

    def test_selector_bse_filter_recognizes_legacy_and_new_codes(self):
        from nasdx.selector.universe import filter_universe

        stocks = [
            {"code": "920185", "name": "贝特瑞", "close": 20, "amount": 1e8, "exchange": "BSE"},
            {"code": "430047", "name": "诺思兰德", "close": 8, "amount": 1e8},
            {"code": "600000", "name": "浦发银行", "close": 10, "amount": 1e8, "exchange": "SSE"},
        ]

        filtered = filter_universe(stocks, exclude_bj=True)

        self.assertEqual(["600000"], [stock["code"] for stock in filtered])

    def test_selector_report_and_ui_surface_exchange_coverage(self):
        runner = Path("run_stock_selector.py").read_text(encoding="utf-8")
        page = Path("selector_page.py").read_text(encoding="utf-8")

        self.assertIn('"universe_coverage": universe_coverage', runner)
        self.assertIn('coverage.get("complete"', page)


if __name__ == "__main__":
    unittest.main()
