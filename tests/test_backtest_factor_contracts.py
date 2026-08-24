"""回测因子计算优化契约测试（issue #32）。

验证 strategy_factor_rank 在同一标的、相同窗口（行数 + 末日期 + 末收盘）下
只计算一次 Alpha158 因子矩阵，避免每个调仓日重算全量因子（日频回测的 O(重) 冗余）。
"""
import unittest
from unittest.mock import patch

import pandas as pd
import pandas.testing as pdt


def _price_frame(n: int) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    base = list(range(1, n + 1))
    return pd.DataFrame(
        {
            "open": base,
            "high": [x + 1 for x in base],
            "low": [x - 1 for x in base],
            "close": base,
            "volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


class BacktestFactorContractsTest(unittest.TestCase):
    def setUp(self):
        import quant.backtest as bt

        bt._factor_matrix_cache.clear()

    def test_strategy_factor_rank_memoizes_alpha158_across_rebalances(self):
        import quant.backtest as bt
        import quant.factors as factors_mod

        real_compute = factors_mod.compute_alpha158
        compute_calls = []

        def counting(df, *args, **kwargs):
            compute_calls.append(id(df))
            return real_compute(df, *args, **kwargs)

        def fake_ranking(factor_data, *args, **kwargs):
            return pd.DataFrame({"code": list(factor_data.keys())})

        price_data = {"600000": _price_frame(100), "600519": _price_frame(100)}
        with patch.object(factors_mod, "compute_alpha158", side_effect=counting), patch.object(
            factors_mod, "multi_factor_score", side_effect=fake_ranking
        ):
            first = bt.strategy_factor_rank("2026-01-01", price_data)
            second = bt.strategy_factor_rank("2026-01-02", price_data)  # 相同窗口，再次调用

        # 两个标的各计算一次；跨两次调仓日不重复计算
        self.assertEqual(2, len(compute_calls), f"compute_alpha158 被调用 {len(compute_calls)} 次，预期 2 次")
        # top_n 默认 5，两个标的均入选 -> 各 1/5
        self.assertEqual({c: 0.2 for c in price_data}, first)
        self.assertEqual(first, second)

    def test_causal_factor_matrix_matches_each_legacy_prefix(self):
        import quant.factors as factors_mod

        frame = _price_frame(110)
        causal = factors_mod.compute_alpha158_causal(frame)
        for length in (60, 75, 100):
            legacy = factors_mod.compute_alpha158(frame.iloc[:length])
            expected = legacy.iloc[-1]
            actual = causal.loc[frame.index[length - 1]]
            # expanding 使用稳定的在线方差算法；常数列与逐前缀 std 仅有约 1e-5
            # 的浮点噪声，其余因子逐位一致，且不会改变下方策略排名契约。
            pdt.assert_series_equal(actual, expected, check_names=False, rtol=1e-9, atol=1e-4)

    def test_precomputed_factor_strategy_matches_legacy_daily_signals(self):
        import quant.backtest as bt

        price_data = {
            "600000": _price_frame(110),
            "600519": _price_frame(110).assign(close=lambda df: df["close"] * 1.03),
            "000001": _price_frame(110).assign(volume=lambda df: df["volume"] * 1.7),
        }
        optimized = bt.build_factor_rank_strategy(price_data, top_n=2)

        for position in (60, 75, 100):
            date = price_data["600000"].index[position]
            past_data = {code: frame[frame.index < date] for code, frame in price_data.items()}
            expected = bt.strategy_factor_rank(date, past_data, top_n=2)
            self.assertEqual(expected, optimized(date, past_data))

    def test_precomputed_factor_strategy_calculates_each_symbol_once(self):
        import quant.backtest as bt
        import quant.factors as factors_mod

        price_data = {"600000": _price_frame(100), "600519": _price_frame(100)}
        with patch.object(
            factors_mod,
            "compute_alpha158_causal",
            wraps=factors_mod.compute_alpha158_causal,
        ) as compute:
            strategy = bt.build_factor_rank_strategy(price_data, top_n=2)
            for position in range(60, 100):
                date = price_data["600000"].index[position]
                past_data = {code: frame[frame.index < date] for code, frame in price_data.items()}
                strategy(date, past_data)

        self.assertEqual(2, compute.call_count)


if __name__ == "__main__":
    unittest.main()
