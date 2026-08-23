"""Issue #34 — 扫描脚本吞吐契约。

覆盖重开后的追加验收：
1. ``fetch_stock_data.py`` / ``scan_stocks_full.py`` 不再含逐标的固定 sleep；
2. 同一工作流内重复代码只抓一次历史行情（批量层 + 共享磁盘缓存键）；
3. 数据源限流走「有界并发 + provider 级限流」，不放大并发；
4. 批量层异常时有界降级为逐只回退，不整批失败；
5. 输出顺序、字段与既有行为保持一致（确定性 / 无前视偏差）。
"""

import ast
import importlib
import re
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from nasdx.fast_market import RateLimiter, bounded_map

ROOT = Path(__file__).resolve().parents[1]
FETCH_SCRIPT = ROOT / "scripts" / "fetch_stock_data.py"
SCAN_SCRIPT = ROOT / "scripts" / "scan_stocks_full.py"
ETF_SCRIPT = ROOT / "scripts" / "scan_etf50.py"

# 逐标的固定 sleep 的典型写法：time.sleep(0.2) / sleep(0.4)
_SLEEP_CALL = re.compile(r"(?<![\w.])(?:time\.)?sleep\s*\(", re.MULTILINE)


def _history_frame(code: str = "600000", rows: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=rows, freq="D")
    close = pd.Series([10.0 + index * 0.1 for index in range(rows)])
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": close * 0.99,
            "最高": close * 1.01,
            "最低": close * 0.98,
            "收盘": close,
            "成交量": [100_000 + index for index in range(rows)],
            "成交额": [1_000_000 + index for index in range(rows)],
            "涨跌幅": close.pct_change().fillna(0) * 100,
            "换手率": [1.5] * rows,
            "代码": code,
        }
    )


def _source_without_comments(path: Path) -> str:
    """剔除注释/文档字符串，避免注释里出现 sleep 字样造成误判。"""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    lines = [line.split("#", 1)[0] for line in text.splitlines()]
    stripped = "\n".join(lines)
    for doc in docstrings:
        stripped = stripped.replace(doc, "")
    return stripped


class SleepFreeScanContractTests(unittest.TestCase):
    """验收 1：扫描脚本不再依赖逐标的固定 sleep 来限流。"""

    def test_scan_scripts_have_no_per_symbol_sleep(self):
        for path in (FETCH_SCRIPT, SCAN_SCRIPT, ETF_SCRIPT):
            with self.subTest(script=path.name):
                self.assertIsNone(
                    _SLEEP_CALL.search(_source_without_comments(path)),
                    f"{path.name} 仍存在逐标的固定 sleep（issue #34 回归）",
                )

    def test_scan_scripts_do_not_import_time_for_pacing(self):
        for path in (FETCH_SCRIPT, SCAN_SCRIPT):
            with self.subTest(script=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(alias.name.split(".")[0] for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.add(node.module.split(".")[0])
                self.assertNotIn("time", imported, f"{path.name} 不应再引入 time 做串行节流")

    def test_scan_scripts_route_history_through_batch_layer(self):
        for path in (FETCH_SCRIPT, SCAN_SCRIPT, ETF_SCRIPT):
            with self.subTest(script=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn(
                    "fetch_histories",
                    source,
                    f"{path.name} 应通过 nasdx.fast_market.fetch_histories 批量抓取",
                )


class RateLimiterContractTests(unittest.TestCase):
    """验收 3：provider 级限流可控、可注入时钟、拒绝非法参数。"""

    def test_paces_acquisitions_on_virtual_clock(self):
        now = {"t": 0.0}
        slept: list[float] = []

        def clock():
            return now["t"]

        def sleep(seconds):
            slept.append(seconds)
            now["t"] += seconds

        limiter = RateLimiter(0.12, clock=clock, sleep=sleep)
        for _ in range(10):
            limiter.acquire()

        # 首次不等待，之后每次恰好补齐一个 interval
        self.assertEqual(len(slept), 9)
        for delay in slept:
            self.assertAlmostEqual(delay, 0.12, places=6)
        self.assertAlmostEqual(now["t"], 0.12 * 9, places=6)

    def test_zero_interval_never_sleeps(self):
        slept: list[float] = []
        limiter = RateLimiter(0, clock=lambda: 0.0, sleep=slept.append)
        for _ in range(5):
            self.assertEqual(limiter.acquire(), 0.0)
        self.assertEqual(slept, [])

    def test_rejects_invalid_interval(self):
        for bad in (True, -1, float("inf"), float("nan"), "0.1", None):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    RateLimiter(bad)

    def test_is_thread_safe_under_contention(self):
        limiter = RateLimiter(0.001)
        seen: list[int] = []
        lock = threading.Lock()

        def worker(index):
            limiter.acquire()
            with lock:
                seen.append(index)
            return index

        outcomes = bounded_map(range(20), worker, max_workers=8, rate_limiter=limiter)
        self.assertEqual([value for value, _ in outcomes], list(range(20)))
        self.assertEqual(sorted(seen), list(range(20)))


class BoundedMapContractTests(unittest.TestCase):
    """验收 3/5：有界并发、保序、单点失败隔离。"""

    def test_preserves_input_order(self):
        outcomes = bounded_map(["c", "a", "b"], lambda item: item.upper(), max_workers=3)
        self.assertEqual([value for value, _ in outcomes], ["C", "A", "B"])
        self.assertTrue(all(error is None for _, error in outcomes))

    def test_concurrency_never_exceeds_max_workers(self):
        state = {"live": 0, "peak": 0}
        lock = threading.Lock()
        gate = threading.Event()

        def worker(index):
            with lock:
                state["live"] += 1
                state["peak"] = max(state["peak"], state["live"])
            gate.wait(0.05)
            with lock:
                state["live"] -= 1
            return index

        try:
            outcomes = bounded_map(range(24), worker, max_workers=4)
        finally:
            gate.set()

        self.assertEqual([value for value, _ in outcomes], list(range(24)))
        self.assertLessEqual(state["peak"], 4, "并发上限被突破")

    def test_single_failure_does_not_abort_batch(self):
        def worker(item):
            if item == 2:
                raise RuntimeError("boom")
            return item * 10

        outcomes = bounded_map([1, 2, 3], worker, max_workers=3)
        self.assertEqual(outcomes[0][0], 10)
        self.assertIsNone(outcomes[0][1])
        self.assertIsNone(outcomes[1][0])
        self.assertIsInstance(outcomes[1][1], RuntimeError)
        self.assertEqual(outcomes[2][0], 30)

    def test_empty_input_short_circuits(self):
        calls: list[object] = []
        self.assertEqual(bounded_map([], calls.append, max_workers=4), [])
        self.assertEqual(calls, [])

    def test_rejects_invalid_max_workers(self):
        for bad in (0, -1, True, 1.5, "4"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    bounded_map([1], lambda item: item, max_workers=bad)


class FetchStockDataBatchTests(unittest.TestCase):
    """验收 2/4：fetch_stock_data 走批量层，一次调用覆盖整池。"""

    def setUp(self):
        self.module = importlib.import_module("scripts.fetch_stock_data")

    def test_pool_histories_calls_batch_layer_once_with_unique_codes(self):
        captured = {}

        def fake_fetch_histories(codes, start, end, **kwargs):
            captured["codes"] = list(codes)
            captured["start"] = start
            captured["end"] = end
            captured["kwargs"] = kwargs
            captured["calls"] = captured.get("calls", 0) + 1
            return {code: (_history_frame(code), "tdxrs") for code in codes}

        with patch.object(self.module, "fetch_histories", side_effect=fake_fetch_histories):
            result = self.module.fetch_pool_histories(
                ["600000", " 600000 ", "300750", "", "688981"]
            )

        self.assertEqual(captured["calls"], 1, "整池必须只调用一次批量层")
        self.assertEqual(captured["codes"], ["600000", "300750", "688981"])
        self.assertEqual(captured["start"], self.module.START_DATE)
        self.assertEqual(captured["end"], self.module.TODAY)
        self.assertEqual(captured["kwargs"]["sources"], self.module.HISTORY_SOURCES)
        self.assertGreater(captured["kwargs"]["max_workers"], 1)
        self.assertEqual(set(result), {"600000", "300750", "688981"})

    def test_pool_histories_skips_network_when_pool_empty(self):
        with patch.object(self.module, "fetch_histories") as batch:
            self.assertEqual(self.module.fetch_pool_histories([]), {})
        batch.assert_not_called()

    def test_resolve_history_uses_batch_hit_without_refetch(self):
        frame = _history_frame()
        with patch.object(self.module, "fetch_stock_hist") as single:
            resolved, source = self.module._resolve_history("600000", (frame, "tdxrs"))
        single.assert_not_called()
        self.assertIs(resolved, frame)
        self.assertEqual(source, "tdxrs")

    def test_resolve_history_does_not_retry_empty_batch_entry(self):
        with patch.object(self.module, "fetch_stock_hist") as single:
            resolved, source = self.module._resolve_history("600000", (None, None))
        single.assert_not_called()
        self.assertIsNone(resolved)
        self.assertIsNone(source)

    def test_resolve_history_falls_back_when_batch_missing(self):
        frame = _history_frame()
        with patch.object(
            self.module, "fetch_stock_hist", return_value=(frame, "tencent_hist_tx")
        ) as single:
            resolved, source = self.module._resolve_history("600000", None)
        single.assert_called_once()
        self.assertIs(resolved, frame)
        self.assertEqual(source, "tencent_hist_tx")

    def test_fund_flows_use_bounded_concurrency_and_skip_star_market(self):
        codes = [f"6000{index:02d}" for index in range(12)] + ["688981"]
        state = {"live": 0, "peak": 0}
        lock = threading.Lock()

        def fake_fund_flow(code):
            with lock:
                state["live"] += 1
                state["peak"] = max(state["peak"], state["live"])
            try:
                return {"fund_flow": [{"code": code}], "main_net_3d": [1.0]}
            finally:
                with lock:
                    state["live"] -= 1

        with patch.object(self.module, "fetch_fund_flow", side_effect=fake_fund_flow):
            flows = self.module.fetch_fund_flows(codes)

        self.assertNotIn("688981", flows, "科创板不应调用资金流接口")
        self.assertEqual(len(flows), 12)
        self.assertLessEqual(state["peak"], self.module.FUND_FLOW_WORKERS)

    def test_fund_flow_failure_is_isolated_per_symbol(self):
        def fake_fund_flow(code):
            if code == "600002":
                raise RuntimeError("upstream down")
            return {"fund_flow": [{"code": code}], "main_net_3d": [1.0]}

        with patch.object(self.module, "fetch_fund_flow", side_effect=fake_fund_flow):
            flows = self.module.fetch_fund_flows(["600001", "600002", "600003"])

        self.assertEqual(flows["600002"], self.module._empty_fund_flow())
        self.assertEqual(flows["600001"]["fund_flow"], [{"code": "600001"}])
        self.assertEqual(flows["600003"]["fund_flow"], [{"code": "600003"}])

    def test_market_overview_is_bounded_and_deterministic(self):
        snapshots = {
            code: {"close": 100.0 + index, "change_pct": 0.1 * index}
            for index, code in enumerate(self.module.INDEX_MAP)
        }
        with patch.object(
            self.module, "_fetch_index_snapshot", side_effect=lambda code: snapshots[code]
        ):
            overview = self.module.fetch_market_overview()

        self.assertEqual(
            list(overview), [self.module.INDEX_MAP[code] for code in self.module.INDEX_MAP]
        )

    def test_market_overview_drops_failed_index_without_aborting(self):
        def fake_snapshot(code):
            if code == "sh000001":
                raise RuntimeError("timeout")
            return {"close": 1.0, "change_pct": 0.0}

        with patch.object(self.module, "_fetch_index_snapshot", side_effect=fake_snapshot):
            overview = self.module.fetch_market_overview()

        self.assertNotIn("上证指数", overview)
        self.assertEqual(len(overview), len(self.module.INDEX_MAP) - 1)

    def test_collect_pool_preserves_config_order(self):
        config = {
            "sectors": [
                {"name": "A", "stocks": [{"code": "600000"}], "etfs": [{"code": "510300"}]},
                {"name": "B", "stocks": [{"code": "000001"}], "etfs": []},
            ]
        }
        entries = self.module._collect_pool(config)
        self.assertEqual(
            [(index, kind, item["code"]) for index, kind, item in entries],
            [(0, "stocks", "600000"), (0, "etfs", "510300"), (1, "stocks", "000001")],
        )


class ScanStocksFullBatchTests(unittest.TestCase):
    """验收 2/4：scan_stocks_full 走批量层且保留覆盖率字段。

    该脚本是顶层执行脚本（import 即联网），因此用 AST 做结构契约校验。
    """

    def setUp(self):
        self.source = SCAN_SCRIPT.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.functions = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
        }

    def test_defines_batch_history_helpers(self):
        self.assertIn("fetch_pool_histories", self.functions)
        self.assertIn("_resolve_history", self.functions)

    def test_fetch_and_calc_accepts_injected_history(self):
        node = self.functions["fetch_and_calc"]
        args = [arg.arg for arg in node.args.args]
        self.assertEqual(args[:3], ["code", "name", "sector"])
        self.assertIn("history", args, "fetch_and_calc 必须接受批量注入的历史行情")
        self.assertTrue(node.args.defaults, "history 必须有默认值以保持既有调用方兼容")

    def test_module_level_batch_call_before_loop(self):
        batch_line = None
        loop_line = None
        for node in ast.walk(self.tree):
            if (
                batch_line is None
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "fetch_pool_histories"
            ):
                batch_line = node.lineno
            if (
                loop_line is None
                and isinstance(node, ast.For)
                and isinstance(node.iter, ast.Call)
                and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "enumerate"
            ):
                loop_line = node.lineno
        self.assertIsNotNone(batch_line, "缺少整池批量抓取调用")
        self.assertIsNotNone(loop_line, "缺少个股遍历循环")
        self.assertLess(batch_line, loop_line, "批量抓取必须先于逐只组装")

    def test_batch_layer_failure_degrades_instead_of_aborting(self):
        handlers = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Try)
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "fetch_pool_histories"
                for child in ast.walk(node)
            )
        ]
        self.assertTrue(handlers, "批量层调用必须被 try/except 包裹以支持有界降级")

    def test_coverage_fields_are_preserved(self):
        for field in ("expected_total", "valid_count", "no_data", "coverage_ratio"):
            with self.subTest(field=field):
                self.assertIn(
                    f"'{field}'", self.source, f"json_payload 必须保留 {field}（审计依赖）"
                )

    def test_history_window_matches_fetch_stock_data_cache_key(self):
        """同一工作流内两个脚本的缓存键必须一致，才能只抓一次。"""
        fetch_module = importlib.import_module("scripts.fetch_stock_data")
        self.assertIn("timedelta(days=90)", self.source)
        self.assertIn("timedelta(days=90)", FETCH_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(fetch_module.HISTORY_SOURCES, ("tdxrs", "tencent_hist_tx", "eastmoney_hist"))
        self.assertIn(
            'HISTORY_SOURCES = ("tdxrs", "tencent_hist_tx", "eastmoney_hist")',
            self.source,
            "两个脚本的 sources 必须一致，否则磁盘缓存判定为未命中",
        )


class WorkflowDedupTests(unittest.TestCase):
    """验收 2：同一工作流内重复代码只真正抓一次历史行情。"""

    def test_refresh_then_full_scan_fetches_each_code_once(self):
        from nasdx.fast_market import fetch_histories

        refresh_codes = [f"6000{index:02d}" for index in range(69)]
        # Stocks60 与 refresh 池重叠 30 只，其余 30 只为新增
        scan_codes = refresh_codes[:30] + [f"3007{index:02d}" for index in range(30)]
        calls: list[str] = []
        lock = threading.Lock()

        def counting_fetcher(code, start_date, end_date, min_rows, request_timeout, sources):
            with lock:
                calls.append(code)
            return _history_frame(code), "tencent_hist_tx"

        with TemporaryDirectory() as temp_dir:
            shared = {
                "hist_fetcher": counting_fetcher,
                "batch_hist_fetcher": None,
                "sources": ("tencent_hist_tx",),
                "use_disk_cache": True,
                "cache_dir": Path(temp_dir),
                "cache_ttl_seconds": 600.0,
                "min_rows": 10,
                "max_workers": 12,
            }
            fetch_histories(refresh_codes, "20260501", "20260808", **shared)
            first_round = len(calls)
            fetch_histories(scan_codes, "20260501", "20260808", **shared)

        self.assertEqual(first_round, 69, "refresh 阶段应抓满整池")
        self.assertEqual(
            len(calls),
            len(set(refresh_codes) | set(scan_codes)),
            "重复代码必须命中缓存，不得重复联网",
        )
        self.assertEqual(len(calls), len(set(calls)), "同一代码不得抓两次")


class ThroughputRegressionTests(unittest.TestCase):
    """验收 5：mock 基准证明墙钟显著下降，并设回归阈值。"""

    # 旧实现的固定等待下限：refresh 每只 sleep(0.4)，Stocks60 每只 sleep(0.2)
    LEGACY_REFRESH_FLOOR = 69 * 0.4
    LEGACY_SCAN_FLOOR = 60 * 0.2
    REGRESSION_RATIO = 0.35  # 新实现墙钟须低于旧下限的 35%

    @staticmethod
    def _run_bounded(codes, latency, workers):
        import time as _time

        def worker(code):
            _time.sleep(latency)
            return code

        started = _time.perf_counter()
        outcomes = bounded_map(codes, worker, max_workers=workers)
        return _time.perf_counter() - started, outcomes

    def test_refresh_pool_beats_legacy_serial_floor(self):
        codes = [f"6000{index:02d}" for index in range(69)]
        elapsed, outcomes = self._run_bounded(codes, latency=0.02, workers=12)

        self.assertEqual([value for value, _ in outcomes], codes, "顺序必须保持")
        self.assertLess(
            elapsed,
            self.LEGACY_REFRESH_FLOOR * self.REGRESSION_RATIO,
            f"69 标的 refresh 墙钟 {elapsed:.2f}s 未低于旧下限 "
            f"{self.LEGACY_REFRESH_FLOOR}s 的 {self.REGRESSION_RATIO:.0%}",
        )

    def test_full_scan_pool_beats_legacy_serial_floor(self):
        codes = [f"3003{index:02d}" for index in range(60)]
        elapsed, outcomes = self._run_bounded(codes, latency=0.02, workers=12)

        self.assertEqual([value for value, _ in outcomes], codes, "顺序必须保持")
        self.assertLess(
            elapsed,
            self.LEGACY_SCAN_FLOOR * self.REGRESSION_RATIO,
            f"Stocks60 墙钟 {elapsed:.2f}s 未低于旧下限 "
            f"{self.LEGACY_SCAN_FLOOR}s 的 {self.REGRESSION_RATIO:.0%}",
        )

    def test_rate_limited_pool_is_faster_than_serial_sleep(self):
        pool_size = 60
        legacy_serial_seconds = pool_size * 0.4  # 旧实现：逐只 sleep(0.4)

        now = {"t": 0.0}
        lock = threading.Lock()

        def clock():
            return now["t"]

        def sleep(seconds):
            with lock:
                now["t"] += seconds

        limiter = RateLimiter(0.12, clock=clock, sleep=sleep)
        for _ in range(pool_size):
            limiter.acquire()

        self.assertAlmostEqual(now["t"], 0.12 * (pool_size - 1), places=6)
        self.assertLess(
            now["t"],
            legacy_serial_seconds * 0.5,
            "限流后的墙钟基准未较原串行方案下降 50% 以上",
        )


if __name__ == "__main__":
    unittest.main()
