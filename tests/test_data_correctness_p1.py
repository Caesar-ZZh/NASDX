"""NASDX 数据层正确性契约测试（P1 修复 #39 / #30）。

不涉及真实联网：
  #39 直接调用 nasdx.fast_market._read_history_cache，并临时造一个 JSON 缓存文件，
      断言 min_rows / sources 不足或不匹配时返回 None（视为未命中）。
  #30 用 unittest.mock.patch 替换 mootdx.quotes.Quotes.factory 返回假 bars，
      bars 覆盖比自然日窗口更宽，断言返回 df 的日期被裁剪到 [start, end]，
      且列包含标准 OHLCV。
"""
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd


class TestHistoryCacheValidation(unittest.TestCase):
    """磁盘缓存校验 min_rows / sources（issue #39）。"""

    def _make_cache(self, tmp: Path, records, source: str, age_seconds: float = 1.0) -> Path:
        import time
        payload = {
            "cached_at": time.time() - age_seconds,
            "source": source,
            "records": records,
        }
        p = tmp / "cache_600000_2025-01-01_2025-02-01.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return p

    def test_min_rows_insufficient_returns_none(self):
        import nasdx.fast_market as fm
        with tempfile.TemporaryDirectory() as d:
            records = [{"date": "2025-01-02", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 100}]
            p = self._make_cache(Path(d), records, source="tdxrs")
            out = fm._read_history_cache(p, ttl_seconds=600, min_rows=20, sources=("tdxrs",))
            self.assertIsNone(out, "行数不足 min_rows 必须视为未命中（返回 None）")

    def test_source_not_in_requested_returns_none(self):
        import nasdx.fast_market as fm
        with tempfile.TemporaryDirectory() as d:
            records = [
                {"date": f"2025-01-{i:02d}", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 100}
                for i in range(1, 25)
            ]
            p = self._make_cache(Path(d), records, source="tencent_hist_tx")
            out = fm._read_history_cache(
                p, ttl_seconds=600, min_rows=20, sources=("tdxrs",)
            )
            self.assertIsNone(out, "缓存源不在请求 sources 内必须视为未命中")

    def test_valid_cache_hit(self):
        import nasdx.fast_market as fm
        with tempfile.TemporaryDirectory() as d:
            records = [
                {"date": f"2025-01-{i:02d}", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 100}
                for i in range(1, 25)
            ]
            p = self._make_cache(Path(d), records, source="tdxrs")
            out = fm._read_history_cache(p, ttl_seconds=600, min_rows=20, sources=("tdxrs",))
            self.assertIsNotNone(out, "行足且 source 匹配应命中")
            frame, source = out
            self.assertEqual(source, "tdxrs")
            self.assertGreaterEqual(len(frame), 20)

    def test_expired_cache_returns_none(self):
        import nasdx.fast_market as fm
        with tempfile.TemporaryDirectory() as d:
            records = [
                {"date": f"2025-01-{i:02d}", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 100}
                for i in range(1, 25)
            ]
            p = self._make_cache(Path(d), records, source="tdxrs", age_seconds=1000)
            out = fm._read_history_cache(p, ttl_seconds=600, min_rows=20, sources=("tdxrs",))
            self.assertIsNone(out, "超过 TTL 的缓存必须视为未命中")


class TestMootdxLookbackWindow(unittest.TestCase):
    """mootdx lookback 与自然日窗口对齐（issue #30）。"""

    def _fake_bars(self, start_dt, end_dt):
        # 覆盖比 [start,end] 更宽的 bars（含 weekend / 停牌缓冲）
        wide_idx = pd.date_range(start_dt - pd.Timedelta(days=40),
                                end_dt + pd.Timedelta(days=40), freq="D")
        df = pd.DataFrame(
            {
                "datetime": wide_idx,
                "open": [10.0 + i for i in range(len(wide_idx))],
                "high": [11.0 + i for i in range(len(wide_idx))],
                "low": [9.0 + i for i in range(len(wide_idx))],
                "close": [10.0 + i for i in range(len(wide_idx))],
                "volume": [1000] * len(wide_idx),
            }
        )
        return df

    def test_dates_clipped_to_window_and_has_ohlcv(self):
        import quant.data as qdata

        start_s, end_s = "2025-03-01", "2025-03-31"
        start_dt = pd.to_datetime(start_s)
        end_dt = pd.to_datetime(end_s)
        fake = self._fake_bars(start_dt, end_dt)

        mock_api = MagicMock()
        mock_api.bars.return_value = fake
        # _get_mootdx 内部 `from mootdx.quotes import Quotes` 局部导入，
        # 故需在 sys.modules['mootdx.quotes'] 注入假模块并置 Quotes.factory。
        # _get_mootdx 内部 `from mootdx.quotes import Quotes` 局部导入，
        # 故需在 sys.modules['mootdx.quotes'] 注入假模块（无需真实安装 mootdx）。
        fake_module = MagicMock()
        fake_module.Quotes.factory.return_value = mock_api
        saved = sys.modules.get("mootdx.quotes")
        sys.modules["mootdx.quotes"] = fake_module
        try:
            result = qdata._get_mootdx("600000", days=30, start_s=start_s, end_s=end_s)
        finally:
            if saved is None:
                sys.modules.pop("mootdx.quotes", None)
            else:
                sys.modules["mootdx.quotes"] = saved

        self.assertIsNotNone(result, "mock 下应返回裁剪后的 DataFrame")
        # 日期被裁剪进 [start, end]
        dates = pd.to_datetime(result["date"] if "date" in result.columns else result.index)
        self.assertTrue((dates >= start_dt).all() and (dates <= end_dt).all(),
                        "返回 df 的日期必须裁剪到自然日窗口 [start, end]")
        # 覆盖比窗口更宽后才裁剪 -> 不应等同于原始宽度（确实发生了裁剪）
        self.assertLessEqual(len(result), len(fake), "应被裁剪，行数不超过原始 bars")
        # 列含标准 OHLCV
        std_cols = {"open", "high", "low", "close", "volume"}
        self.assertTrue(std_cols.issubset(set(result.columns)),
                        f"返回 df 须含标准 OHLCV 列，实际：{list(result.columns)}")

    def test_no_window_returns_wide_bars(self):
        import quant.data as qdata
        fake = self._fake_bars(pd.to_datetime("2025-03-01"), pd.to_datetime("2025-03-31"))
        mock_api = MagicMock()
        mock_api.bars.return_value = fake
        fake_module = MagicMock()
        fake_module.Quotes.factory.return_value = mock_api
        saved = sys.modules.get("mootdx.quotes")
        sys.modules["mootdx.quotes"] = fake_module
        try:
            result = qdata._get_mootdx("600000", days=30)  # 无 start/end
        finally:
            if saved is None:
                sys.modules.pop("mootdx.quotes", None)
            else:
                sys.modules["mootdx.quotes"] = saved

        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
