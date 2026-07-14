"""数据层缺陷修复契约测试（issue #31 / #34 / #35 / #37）。

这些测试不依赖网络：通过 monkeypatch 隔离真实数据源，验证修复后的行为契约。
"""
import unittest
from unittest.mock import patch

import pandas as pd

from quant.data import get_batch_ohlcv, retry_with_backoff, _standardize_columns


class RetryContractsTest(unittest.TestCase):
    def test_retry_on_none_retries_then_succeeds(self):
        """#31: 数据源返回 None（失败哨兵）时必须真正重试，而非误判成功。"""
        calls = []

        @retry_with_backoff(max_attempts=3, initial_wait=0.01, retry_on_none=True)
        def flaky():
            calls.append(1)
            return None if len(calls) < 3 else "ok"

        with patch("quant.data.time.sleep") as sleep_mock:
            result = flaky()
        self.assertEqual("ok", result)
        self.assertEqual(3, len(calls))
        self.assertEqual(2, sleep_mock.call_count)

    def test_retry_on_none_returns_none_after_max_attempts(self):
        @retry_with_backoff(max_attempts=3, initial_wait=0.01, retry_on_none=True)
        def always_none():
            return None

        with patch("quant.data.time.sleep"):
            self.assertIsNone(always_none())

    def test_retry_reraises_on_final_exception(self):
        @retry_with_backoff(max_attempts=2, initial_wait=0.01)
        def boom():
            raise ValueError("x")

        with patch("quant.data.time.sleep"):
            with self.assertRaises(ValueError):
                boom()


class StandardizeContractsTest(unittest.TestCase):
    def test_standardize_coerces_numeric_without_deprecated_errors_ignore(self):
        """#37: 用 to_numeric(coerce) 替代弃用的 astype(errors="ignore")。"""
        df = pd.DataFrame(
            {
                "日期": ["2026-01-01", "2026-01-02"],
                "开盘": ["1.0", "2.0"],
                "最高": ["1.5", "2.5"],
                "最低": ["0.5", "1.5"],
                "收盘": ["1.2", "bad"],
                "成交量": ["10", "20"],
            }
        )
        out = _standardize_columns(df)
        for col in ["open", "high", "low", "close", "volume"]:
            self.assertIn(out[col].dtype.kind, ("i", "f"), f"{col} 应为数值型, 实际 {out[col].dtype}")
        # 非数值被 coerce 为 NaN，而不是原样保留为 object（这正是 errors="ignore" 的隐患）
        self.assertTrue(pd.isna(out["close"].iloc[1]))
        self.assertFalse((out.dtypes == object).any())


class QuantDataBatchTimeoutTest(unittest.TestCase):
    def test_batch_propagates_request_timeout_to_history_service(self):
        """#34: 批量为空时仍需把有界超时传给并发历史服务，避免单标的挂起拖垮整体。"""
        with patch("nasdx.fast_market.fetch_histories", return_value={}) as fetch_mock:
            with patch("quant.data.get_ohlcv", return_value=None):
                get_batch_ohlcv(["600000"], days=30, verbose=False, request_timeout=12.0)
        self.assertEqual(12.0, fetch_mock.call_args.kwargs["request_timeout"])


class RealtimeQuotesContractsTest(unittest.TestCase):
    def test_realtime_maps_tencent_snapshot_per_code(self):
        """#35: 腾讯逐代码快照应只映射请求的代码，不拉全市场 ETF 表。"""
        from quant.data import _map_tencent_to_quotes

        tencent_payload = {"600000": {"close": 12.3, "change_pct": 1.5, "amount": 1000.0}}
        self.assertEqual(
            {"600000": {"price": 12.3, "chg": 1.5, "volume": 1000.0}},
            _map_tencent_to_quotes(tencent_payload, ["600000"]),
        )
        # 只映射请求的代码；未请求的代码（即使全市场表里有）不会混入
        self.assertEqual({}, _map_tencent_to_quotes(tencent_payload, ["000001"]))


if __name__ == "__main__":
    unittest.main()
