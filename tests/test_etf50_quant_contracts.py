import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from quant import etf50_quant
from quant.backtest import BacktestResult, Backtester

# ── #73 基准成本模型（虚拟时钟，确定性，不依赖真实网络/墙钟抖动）──────
NETWORK_RTT = 0.40          # 单只行情一次网络往返的模拟耗时（秒）
CACHE_READ = 0.001          # 磁盘缓存命中每只的模拟耗时（秒）
LEGACY_FIXED_SLEEP = 0.20   # #73 修复前循环内的固定限速 sleep（秒）


def _price_frame(start: float, step: float, periods: int = 90) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=periods, freq="D")
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


def _write_pool(temp_path: Path, codes) -> None:
    (temp_path / "etf50_pool.json").write_text(
        json.dumps({"etfs": [{"code": code, "name": code, "category": "test"} for code in codes]}),
        encoding="utf-8",
    )


class _VirtualClock:
    """把模拟网络耗时累加成确定性的 '墙钟成本'，避免 CI 上真实 sleep 抖动。"""

    def __init__(self) -> None:
        self.elapsed = 0.0

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds


class ETF50QuantContractsTest(unittest.TestCase):
    def test_scan_summary_distinguishes_success_partial_and_failed(self):
        from nasdx.etf_scan_contract import summarize_scan_results

        successful = [{"signal": "bullish"}] * 20 + [{"signal": "neutral"}] * 20
        summary = summarize_scan_results(successful + [{"signal": "no_data"}] * 10, pool_total=50)
        self.assertEqual("success", summary["scan_status"])
        self.assertEqual(40, summary["success_count"])
        self.assertEqual(10, summary["no_data_count"])

        partial = summarize_scan_results(successful[:5] + [{"signal": "no_data"}] * 45, pool_total=50)
        self.assertEqual("partial", partial["scan_status"])
        failed = summarize_scan_results([{"signal": "no_data"}] * 50, pool_total=50)
        self.assertEqual("failed", failed["scan_status"])

    def test_backtest_uses_all_valid_etfs_for_rolling_factor_rebalance(self):
        captured = {}
        frames = {
            "510001": _price_frame(10.0, 0.10),
            "510002": _price_frame(20.0, 0.05),
            "510003": _price_frame(30.0, -0.03),
        }

        def fake_batch(codes, **kwargs):
            return {code: frames[code].copy(deep=True) for code in codes if code in frames}

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
            _write_pool(temp_path, frames)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
                patch.object(Backtester, "run", fake_run),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=1, verbose=False)

        self.assertEqual(set(frames), captured["price_codes"])
        self.assertTrue(captured["weights"])
        self.assertEqual(1, len(output["portfolio_weights"]))
        self.assertEqual(0.01, output["backtest"]["total_return"])


class ETF50BatchFetchContractsTest(unittest.TestCase):
    """#73：ETF50 量化入口必须消费已有 batch OHLCV，不得逐只串行 + 固定 sleep。"""

    def setUp(self):
        self.codes = [f"5100{i:02d}" for i in range(1, 13)]
        self.frames = {
            code: _price_frame(10.0 + idx, 0.05 + idx * 0.001)
            for idx, code in enumerate(self.codes)
        }

    # ── 验收 1 / 2：一次 batch，无逐只 sleep，无逐只串行 ──────────
    def test_pool_history_triggers_exactly_one_batch_call(self):
        calls = []

        def fake_batch(codes, **kwargs):
            calls.append({"codes": list(codes), "kwargs": dict(kwargs)})
            return {code: self.frames[code].copy(deep=True) for code in codes}

        single_calls = []

        def fake_single(code, days=None, **kwargs):
            single_calls.append(code)
            return self.frames[code].copy(deep=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
                patch("quant.data.get_ohlcv", side_effect=fake_single),
            ):
                output = etf50_quant.run_etf50_quant(days=120, verbose=False)

        self.assertEqual(1, len(calls), "整池历史行情必须只触发一次 batch 调用")
        self.assertEqual(self.codes, calls[0]["codes"])
        self.assertTrue(calls[0]["kwargs"]["use_cache"])
        self.assertEqual(120, calls[0]["kwargs"]["days"])
        self.assertEqual([], single_calls, "batch 命中时不得再走单只路径")
        self.assertEqual(len(self.codes), output["success"])
        self.assertFalse(output["batch_layer_failed"])

    def test_scan_loop_never_sleeps(self):
        slept = []
        real_sleep = time.sleep

        def tracking_sleep(seconds):
            slept.append(seconds)
            real_sleep(0)

        def fake_batch(codes, **kwargs):
            return {code: self.frames[code].copy(deep=True) for code in codes}

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
                patch.object(time, "sleep", tracking_sleep),
            ):
                etf50_quant.run_etf50_quant(verbose=False)

        self.assertEqual([], slept, f"扫描路径不应存在固定限速 sleep，实际: {slept}")

    def test_module_source_has_no_fixed_rate_limit_sleep(self):
        source = Path(etf50_quant.__file__).read_text(encoding="utf-8")
        code_lines = [
            line for line in source.splitlines()
            if "sleep(" in line and not line.strip().startswith("#")
        ]
        self.assertEqual([], code_lines, "etf50_quant 不应再调用 sleep 限速")

    # ── 验收 3：输出顺序 / 因子分 / TopN / 回测输入 / 覆盖率兼容 ──
    def test_output_shape_and_ranking_stay_compatible(self):
        def fake_batch(codes, **kwargs):
            return {code: self.frames[code].copy(deep=True) for code in codes}

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=3, verbose=False)

        for key in ("datetime", "days", "total", "success", "results", "top3",
                    "portfolio_weights", "backtest", "bullish", "bearish", "neutral"):
            self.assertIn(key, output)

        scores = [r["quant_score"] for r in output["results"]]
        self.assertEqual(sorted(scores, reverse=True), scores, "排行必须按量化分降序")
        self.assertEqual(set(self.codes), {r["code"] for r in output["results"]})
        self.assertEqual(3, len(output["portfolio_weights"]))
        self.assertAlmostEqual(1.0, sum(output["portfolio_weights"].values()), places=6)
        self.assertEqual(len(self.codes), output["total"])
        self.assertEqual(1.0, output["coverage"])
        self.assertEqual([], output["missing_codes"])

    def test_deterministic_across_repeated_runs(self):
        def fake_batch(codes, **kwargs):
            return {code: self.frames[code].copy(deep=True) for code in codes}

        outputs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                _write_pool(temp_path, self.codes)
                with (
                    patch.object(etf50_quant, "ROOT", temp_path),
                    patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
                ):
                    outputs.append(etf50_quant.run_etf50_quant(days=90, top_n=3, verbose=False))

        self.assertEqual(
            [(r["code"], round(r["quant_score"], 10)) for r in outputs[0]["results"]],
            [(r["code"], round(r["quant_score"], 10)) for r in outputs[1]["results"]],
        )
        self.assertEqual(outputs[0]["portfolio_weights"], outputs[1]["portfolio_weights"])

    # ── 验收 4：独立副本，不发生跨标的污染 ─────────────────────
    def test_batch_frames_are_independent_copies(self):
        shared = _price_frame(10.0, 0.05)
        captured = {}

        def fake_batch(codes, **kwargs):
            # 恶意场景：batch 层对多个标的返回同一个 DataFrame 对象
            return {code: shared for code in codes}

        def fake_run(self, price_data, signal_func, rebalance_freq):
            captured["frames"] = dict(price_data)
            return BacktestResult(
                total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
                max_drawdown=0.0, total_trades=0,
                equity_curve=pd.Series(dtype=float),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes[:3])
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
                patch.object(Backtester, "run", fake_run),
            ):
                etf50_quant.run_etf50_quant(days=90, top_n=2, verbose=False)

        frames = captured["frames"]
        self.assertEqual(3, len(frames))
        ids = {id(frame) for frame in frames.values()}
        self.assertEqual(3, len(ids), "每个标的必须持有独立 DataFrame 副本")
        for frame in frames.values():
            self.assertIsNot(shared, frame)

        first_code = self.codes[0]
        frames[first_code].loc[frames[first_code].index[0], "close"] = -999.0
        for code, frame in frames.items():
            if code == first_code:
                continue
            self.assertNotEqual(-999.0, float(frame["close"].iloc[0]), "跨标的发生了污染")
        self.assertNotEqual(-999.0, float(shared["close"].iloc[0]), "污染回流到了 batch 源对象")

    # ── 验收 6：部分失败保留成功结果并列出缺失标的 ───────────────
    def test_partial_failure_keeps_successes_and_lists_missing_codes(self):
        available = self.codes[:8]
        missing = self.codes[8:]

        def fake_batch(codes, **kwargs):
            return {code: self.frames[code].copy(deep=True) for code in codes if code in available}

        single_calls = []

        def fake_single(code, days=None, **kwargs):
            single_calls.append(code)
            return pd.DataFrame()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
                patch("quant.data.get_ohlcv", side_effect=fake_single),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=3, verbose=False)

        self.assertEqual(len(available), output["success"])
        self.assertEqual(sorted(missing), sorted(output["missing_codes"]))
        self.assertAlmostEqual(len(available) / len(self.codes), output["coverage"], places=4)
        self.assertEqual(len(self.codes), len(output["results"]))
        self.assertEqual(
            [],
            single_calls,
            "batch 已在内部对缺失项做过有界回退，外层不得二次打网络",
        )
        for row in output["results"]:
            if row["code"] in missing:
                self.assertFalse(row["has_data"])
                self.assertEqual(0.0, row["quant_score"])

    def test_short_history_is_reported_as_missing(self):
        short = self.codes[0]

        def fake_batch(codes, **kwargs):
            out = {}
            for code in codes:
                frame = self.frames[code].copy(deep=True)
                out[code] = frame.iloc[:10] if code == short else frame
            return out

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=3, verbose=False)

        self.assertIn(short, output["missing_codes"])
        self.assertEqual(len(self.codes) - 1, output["success"])

    def test_batch_layer_exception_degrades_to_bounded_single_fallback(self):
        def exploding_batch(codes, **kwargs):
            raise RuntimeError("batch layer down")

        single_calls = []

        def fake_single(code, days=None, **kwargs):
            single_calls.append(code)
            return self.frames[code].copy(deep=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=exploding_batch),
                patch("quant.data.get_ohlcv", side_effect=fake_single),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=3, verbose=False)

        self.assertTrue(output["batch_layer_failed"])
        self.assertEqual(self.codes, single_calls, "降级路径必须每只最多一次，且有界")
        self.assertEqual(len(self.codes), output["success"])

    def test_single_fallback_exception_is_isolated(self):
        def exploding_batch(codes, **kwargs):
            raise RuntimeError("batch layer down")

        def flaky_single(code, days=None, **kwargs):
            if code == self.codes[0]:
                raise TimeoutError("single fetch timeout")
            return self.frames[code].copy(deep=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=exploding_batch),
                patch("quant.data.get_ohlcv", side_effect=flaky_single),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=3, verbose=False)

        self.assertEqual([self.codes[0]], output["missing_codes"])
        self.assertEqual(len(self.codes) - 1, output["success"])

    def test_whitespace_padded_pool_codes_still_hit_batch_result(self):
        padded = [f" {code} " for code in self.codes[:4]]
        seen_codes = []

        def fake_batch(codes, **kwargs):
            seen_codes.extend(codes)
            return {code: self.frames[code].copy(deep=True) for code in codes}

        single_calls = []

        def fake_single(code, days=None, **kwargs):
            single_calls.append(code)
            return pd.DataFrame()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, padded)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
                patch("quant.data.get_ohlcv", side_effect=fake_single),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=2, verbose=False)

        self.assertEqual(self.codes[:4], seen_codes, "batch 入参应为去空白后的代码")
        self.assertEqual(4, output["success"], "空白填充的代码必须仍能命中 batch 结果")
        self.assertEqual([], output["missing_codes"])
        self.assertEqual([], single_calls)

    def test_empty_pool_does_not_call_batch(self):
        calls = []

        def fake_batch(codes, **kwargs):
            calls.append(list(codes))
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, [])
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
            ):
                output = etf50_quant.run_etf50_quant(days=90, verbose=False)

        self.assertEqual([], calls, "空池不应触发任何网络调用")
        self.assertEqual(0, output["total"])
        self.assertEqual(0.0, output["coverage"])

    def test_progress_callback_still_covers_whole_pool_in_order(self):
        seen = []

        def fake_batch(codes, **kwargs):
            return {code: self.frames[code].copy(deep=True) for code in codes}

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("quant.data.get_batch_ohlcv", side_effect=fake_batch),
            ):
                etf50_quant.run_etf50_quant(
                    days=90, verbose=False,
                    progress_cb=lambda i, total, code, name: seen.append((i, total, code)),
                )

        self.assertEqual([(i, len(self.codes), code) for i, code in enumerate(self.codes, 1)], seen)


class ETF50BatchBenchmarkTest(unittest.TestCase):
    """#73 验收 5：冷/热缓存基准回归阈值（虚拟时钟成本模型，确定性）。"""

    POOL_SIZE = 50
    BATCH_WORKERS = etf50_quant.BATCH_MAX_WORKERS

    def setUp(self):
        self.codes = [f"5100{i:02d}" for i in range(1, self.POOL_SIZE + 1)]
        base = _price_frame(10.0, 0.05)
        self.raw = {
            code: base.reset_index().rename(columns={"index": "date"})
            for code in self.codes
        }

    @property
    def legacy_cost(self) -> float:
        """#73 修复前的成本：逐只串行网络往返 + 每只固定 0.2s sleep。"""
        return self.POOL_SIZE * (NETWORK_RTT + LEGACY_FIXED_SLEEP)

    def _measure(self, *, cache_hot: bool):
        clock = _VirtualClock()
        stats = {"batch_calls": 0, "single_calls": 0}

        def fake_fetch_histories(codes, start_date, end_date, **kwargs):
            stats["batch_calls"] += 1
            codes = list(codes)
            if cache_hot:
                clock.advance(CACHE_READ * len(codes))
            else:
                workers = max(1, int(kwargs.get("max_workers", self.BATCH_WORKERS)))
                clock.advance(math.ceil(len(codes) / workers) * NETWORK_RTT)
            return {code: (self.raw[code].copy(deep=True), "mock") for code in codes}

        def fake_single(code, days=None, **kwargs):
            stats["single_calls"] += 1
            clock.advance(NETWORK_RTT)
            return _price_frame(10.0, 0.05)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_pool(temp_path, self.codes)
            with (
                patch.object(etf50_quant, "ROOT", temp_path),
                patch("nasdx.fast_market.fetch_histories", side_effect=fake_fetch_histories),
                patch("quant.data.get_ohlcv", side_effect=fake_single),
                patch.object(Backtester, "run", lambda *a, **k: None),
            ):
                output = etf50_quant.run_etf50_quant(days=90, top_n=5, verbose=False)

        return clock.elapsed, stats, output

    def test_cold_cache_cost_beats_legacy_serial_baseline(self):
        cost, stats, output = self._measure(cache_hot=False)
        self.assertEqual(1, stats["batch_calls"], "冷缓存也只应触发一次 batch")
        self.assertEqual(0, stats["single_calls"], "batch 全命中时不应有单只回退")
        self.assertEqual(self.POOL_SIZE, output["success"])
        self.assertLessEqual(
            cost, self.legacy_cost * 0.50,
            f"冷缓存成本 {cost:.3f}s 未达到相对基线 {self.legacy_cost:.3f}s 至少 50% 的下降",
        )

    def test_hot_cache_cost_drops_at_least_70_percent_vs_legacy(self):
        cost, stats, output = self._measure(cache_hot=True)
        self.assertEqual(1, stats["batch_calls"])
        self.assertEqual(0, stats["single_calls"])
        self.assertEqual(self.POOL_SIZE, output["success"])
        reduction = 1.0 - (cost / self.legacy_cost)
        self.assertGreaterEqual(
            reduction, 0.70,
            f"热缓存成本 {cost:.3f}s 相对基线 {self.legacy_cost:.3f}s 只下降 {reduction:.1%}，未达 70%",
        )

    def test_hot_cache_is_cheaper_than_cold_cache(self):
        cold, _, _ = self._measure(cache_hot=False)
        hot, _, _ = self._measure(cache_hot=True)
        self.assertLess(hot, cold)


if __name__ == "__main__":
    unittest.main()
