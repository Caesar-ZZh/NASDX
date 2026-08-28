"""Issue #87 — 批量行情结果的三态解析契约（行为级，非 AST 结构级）。

背景：``nasdx.fast_market.fetch_histories`` 内部已经跑完「共享 TDX 批量 →
多源有界并发回退 → 超时翻倍并发重试」，最后用 ``(None, None)`` 填充仍未解出的
标的。消费方如果把 ``(None, None)`` 当成「批量层没给结果」再单只联网，上游故障
时整池就会退化成 O(N) 串行等待，issue #34 的延迟保证随之失效。

本套用例全部**执行真实函数**（不做源码字符串/AST 断言），覆盖：
1. ``(None, None)``：零次单只联网；
2. ``(frame, source)``：直接复用注入帧，不重抓；
3. ``history is None``：允许一次有界单只兜底，且超时显式有界；
4. 全量停摆 / 部分停摆下，组装循环不产生 O(N) 追加请求；
5. 两个脚本（fetch_stock_data / scan_stocks_full）语义一致。
"""

import ast
import importlib
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from nasdx.fast_market import resolve_batch_history

ROOT = Path(__file__).resolve().parents[1]
SCAN_SCRIPT = ROOT / "scripts" / "scan_stocks_full.py"


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


def _is_bootstrap_guard(node: ast.If) -> bool:
    """识别脚本头部「运行引导」if：``if __name__ == "__main__":`` 或
    ``if _ROOT_DIR not in sys.path:`` 这类只做 sys.path / 入口判断、无业务副作用的守卫。

    脚本自根目录迁入 ``scripts/`` 后，头部新增了 sys.path 引导块。若不跳过，
    前缀加载器会截断在引导 if 处，导致后续 STOCK_POOL / 解析函数 /
    fetch_stock_hist 等定义无法加载（这些 if 在 ``__name__ != "__main__"``
    或 sys.path 已含根目录时本就不会执行，跳过安全）。
    """
    test = node.test
    if not isinstance(test, ast.Compare):
        return False
    names: list[str] = []

    def _collect(value: object) -> None:
        if isinstance(value, ast.Name):
            names.append(value.id)
        elif isinstance(value, ast.Attribute):
            names.append(value.attr)

    _collect(test.left)
    for comparator in test.comparators:
        _collect(comparator)
    return "__name__" in names or "path" in names or "sys" in names


def _load_script_definitions(path: Path, module_name: str) -> types.ModuleType:
    """只执行脚本的「定义前缀」，拿到真实函数对象做行为级测试。

    ``scan_stocks_full.py`` 是顶层顺序执行脚本（import 即联网并写报告文件），
    所以按 AST 截断到第一条非 import/赋值/函数定义的顶层语句，只编译执行前缀。
    这样既不触发扫描，也不像 AST 断言那样只能校验结构。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    prefix: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(
            node,
            (
                ast.Import,
                ast.ImportFrom,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Assign,
                ast.AnnAssign,
            ),
        ):
            prefix.append(node)
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            prefix.append(node)  # 模块 docstring
            continue
        # 跳过脚本头部 sys.path / __name__ 运行引导块（迁入 scripts/ 后新增），
        # 否则前缀截断在引导 if 处，后续 STOCK_POOL / 解析函数 / fetch_stock_hist 等定义无法加载。
        if isinstance(node, ast.If) and _is_bootstrap_guard(node):
            continue
        break
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    compiled = compile(ast.Module(body=prefix, type_ignores=[]), str(path), "exec")
    exec(compiled, module.__dict__)  # noqa: S102 — 受控的定义前缀，无顶层副作用
    return module


class ResolveBatchHistoryContractTests(unittest.TestCase):
    """共享解析器本身的三态语义。"""

    def test_exhausted_batch_entry_never_calls_single_fetcher(self):
        calls: list[str] = []

        resolved = resolve_batch_history(
            "600000",
            (None, None),
            single_fetcher=lambda code: calls.append(code) or (None, None),
            min_rows=10,
        )

        self.assertEqual(resolved, (None, None))
        self.assertEqual(calls, [], "批量层已耗尽回退，不得再单只联网")

    def test_batch_hit_is_returned_without_refetch(self):
        frame = _history_frame()
        calls: list[str] = []

        resolved, source = resolve_batch_history(
            "600000",
            (frame, "tdxrs"),
            single_fetcher=lambda code: calls.append(code) or (None, None),
            min_rows=10,
        )

        self.assertIs(resolved, frame)
        self.assertEqual(source, "tdxrs")
        self.assertEqual(calls, [])

    def test_short_batch_frame_is_terminal_not_a_refetch_trigger(self):
        """行数不足同样是「批量层已给过结论」，不能借此重新联网。"""
        calls: list[str] = []

        resolved = resolve_batch_history(
            "600000",
            (_history_frame(rows=3), "tdxrs"),
            single_fetcher=lambda code: calls.append(code) or (_history_frame(), "tencent_hist_tx"),
            min_rows=10,
        )

        self.assertEqual(resolved, (None, None))
        self.assertEqual(calls, [])

    def test_malformed_batch_entry_is_terminal(self):
        """损坏的批量条目降级为「无数据」，不得触发 O(N) 串行联网。"""
        calls: list[str] = []
        fetcher = lambda code: calls.append(code) or (_history_frame(), "tencent_hist_tx")  # noqa: E731

        for bogus in ("oops", 42, (), (None,), (None, None, None), {"frame": None}):
            with self.subTest(entry=repr(bogus)):
                self.assertEqual(
                    resolve_batch_history("600000", bogus, single_fetcher=fetcher, min_rows=10),
                    (None, None),
                )
        self.assertEqual(calls, [])

    def test_missing_entry_allows_exactly_one_bounded_fallback(self):
        calls: list[str] = []

        def fetcher(code):
            calls.append(code)
            return _history_frame(code), "tencent_hist_tx"

        resolved, source = resolve_batch_history(
            "600000", None, single_fetcher=fetcher, min_rows=10
        )

        self.assertEqual(calls, ["600000"])
        self.assertEqual(source, "tencent_hist_tx")
        self.assertEqual(len(resolved), 30)

    def test_missing_entry_without_fetcher_is_no_data(self):
        self.assertEqual(resolve_batch_history("600000", None), (None, None))

    def test_fallback_result_is_filtered_by_min_rows(self):
        resolved = resolve_batch_history(
            "600000",
            None,
            single_fetcher=lambda code: (_history_frame(rows=4), "tencent_hist_tx"),
            min_rows=10,
        )
        self.assertEqual(resolved, (None, None))

    def test_fallback_errors_follow_caller_policy(self):
        def boom(code):
            raise RuntimeError("upstream down")

        self.assertEqual(
            resolve_batch_history("600000", None, single_fetcher=boom, suppress_errors=True),
            (None, None),
        )
        with self.assertRaises(RuntimeError):
            resolve_batch_history("600000", None, single_fetcher=boom)

    def test_empty_frame_entry_is_terminal(self):
        calls: list[str] = []
        resolved = resolve_batch_history(
            "600000",
            (pd.DataFrame(), "tdxrs"),
            single_fetcher=lambda code: calls.append(code) or (None, None),
        )
        self.assertEqual(resolved, (None, None))
        self.assertEqual(calls, [])

    def test_resolution_is_deterministic(self):
        frame = _history_frame()
        entries = [(frame, "tdxrs"), (None, None), None]
        fetcher = lambda code: (_history_frame(code), "eastmoney_hist")  # noqa: E731
        first = [resolve_batch_history("600000", e, single_fetcher=fetcher, min_rows=10) for e in entries]
        second = [resolve_batch_history("600000", e, single_fetcher=fetcher, min_rows=10) for e in entries]
        self.assertEqual([r[1] for r in first], [r[1] for r in second])
        self.assertEqual([r[0] is None for r in first], [r[0] is None for r in second])


class ScanStocksFullOutagePathTests(unittest.TestCase):
    """验收核心：scan_stocks_full 在上游停摆时不得退回串行联网。"""

    @classmethod
    def setUpClass(cls):
        cls.module = _load_script_definitions(SCAN_SCRIPT, "scan_stocks_full_defs")

    def test_definition_prefix_loader_does_not_run_the_scan(self):
        """加载器只执行定义，不产生扫描输出（否则本套用例本身就在联网）。"""
        self.assertTrue(callable(self.module._resolve_history))
        self.assertTrue(callable(self.module.fetch_and_calc))
        self.assertEqual(len(self.module.STOCK_POOL), 60)
        self.assertFalse(hasattr(self.module, "history_map"))
        self.assertFalse(hasattr(self.module, "valid"))

    def test_resolve_history_makes_zero_calls_for_exhausted_entry(self):
        with patch.object(self.module, "fetch_stock_hist") as single:
            self.assertEqual(self.module._resolve_history("600000", (None, None)), (None, None))
        self.assertEqual(single.call_count, 0, "issue #87 回归：批量层已耗尽仍在单只重抓")

    def test_resolve_history_uses_batch_hit_without_refetch(self):
        frame = _history_frame()
        with patch.object(self.module, "fetch_stock_hist") as single:
            resolved, source = self.module._resolve_history("600000", (frame, "tdxrs"))
        single.assert_not_called()
        self.assertIs(resolved, frame)
        self.assertEqual(source, "tdxrs")

    def test_missing_entry_falls_back_once_with_bounded_timeout(self):
        frame = _history_frame()
        with patch.object(
            self.module, "fetch_stock_hist", return_value=(frame, "tencent_hist_tx")
        ) as single:
            resolved, source = self.module._resolve_history("600000", None)

        single.assert_called_once()
        kwargs = single.call_args.kwargs
        self.assertIn("request_timeout", kwargs, "单只兜底必须显式传有界超时")
        self.assertLessEqual(kwargs["request_timeout"], 10.0)
        self.assertEqual(kwargs["min_rows"], self.module.HISTORY_MIN_ROWS)
        self.assertIs(resolved, frame)
        self.assertEqual(source, "tencent_hist_tx")

    def test_fallback_exception_is_swallowed_as_no_data(self):
        with patch.object(self.module, "fetch_stock_hist", side_effect=RuntimeError("down")):
            self.assertEqual(self.module._resolve_history("600000", None), (None, None))

    def test_full_outage_assembly_loop_issues_no_extra_requests(self):
        """全池 60 只均未解出：组装循环必须零追加联网。"""
        history_map = {code: (None, None) for _, code, _ in self.module.STOCK_POOL}

        with patch.object(self.module, "fetch_stock_hist") as single:
            outcomes = [
                self.module.fetch_and_calc(code, name, sector, history=history_map.get(code))
                for sector, code, name in self.module.STOCK_POOL
            ]

        self.assertEqual(single.call_count, 0, "全量停摆时不得产生 O(N) 串行请求")
        self.assertEqual(outcomes, [None] * 60)

    def test_partial_outage_only_resolved_symbols_produce_rows(self):
        """一半解出一半停摆：仍然零追加联网，且有效标的正常计算。"""
        pool = self.module.STOCK_POOL
        resolved_codes = {code for _, code, _ in pool[:30]}
        history_map = {
            code: ((_history_frame(code), "tdxrs") if code in resolved_codes else (None, None))
            for _, code, _ in pool
        }

        with patch.object(self.module, "fetch_stock_hist") as single:
            outcomes = [
                self.module.fetch_and_calc(code, name, sector, history=history_map.get(code))
                for sector, code, name in pool
            ]

        self.assertEqual(single.call_count, 0, "部分停摆时同样不得串行补抓")
        self.assertEqual(sum(1 for item in outcomes if item is not None), 30)
        self.assertTrue(all(item is None for item in outcomes[30:]))

    def test_batch_layer_outage_still_allows_bounded_per_symbol_fallback(self):
        """批量层整体异常（history_map={}）时，兼容兜底仍在，每只至多一次。"""
        with patch.object(
            self.module,
            "fetch_stock_hist",
            side_effect=lambda code, *a, **k: (_history_frame(code), "tencent_hist_tx"),
        ) as single:
            outcomes = [
                self.module.fetch_and_calc(code, name, sector, history={}.get(code))
                for sector, code, name in self.module.STOCK_POOL
            ]

        self.assertEqual(single.call_count, 60, "批量层缺失时保留既有逐只兜底")
        self.assertEqual(len(single.call_args_list), len(set(call.args[0] for call in single.call_args_list)))
        self.assertTrue(all(item is not None for item in outcomes))


class CrossScriptSemanticsTests(unittest.TestCase):
    """验收：两个脚本的解析语义不得漂移。"""

    @classmethod
    def setUpClass(cls):
        cls.scan = _load_script_definitions(SCAN_SCRIPT, "scan_stocks_full_defs_cross")
        cls.fetch = importlib.import_module("fetch_stock_data")

    def test_both_scripts_delegate_to_the_shared_resolver(self):
        self.assertIs(self.scan.resolve_batch_history, resolve_batch_history)
        self.assertIs(self.fetch.resolve_batch_history, resolve_batch_history)

    def test_exhausted_entry_is_terminal_in_both_scripts(self):
        with patch.object(self.scan, "fetch_stock_hist") as scan_single, patch.object(
            self.fetch, "fetch_stock_hist"
        ) as fetch_single:
            self.assertEqual(self.scan._resolve_history("600000", (None, None)), (None, None))
            self.assertEqual(self.fetch._resolve_history("600000", (None, None)), (None, None))
        scan_single.assert_not_called()
        fetch_single.assert_not_called()

    def test_batch_hit_is_reused_in_both_scripts(self):
        frame = _history_frame()
        with patch.object(self.scan, "fetch_stock_hist") as scan_single, patch.object(
            self.fetch, "fetch_stock_hist"
        ) as fetch_single:
            self.assertIs(self.scan._resolve_history("600000", (frame, "tdxrs"))[0], frame)
            self.assertIs(self.fetch._resolve_history("600000", (frame, "tdxrs"))[0], frame)
        scan_single.assert_not_called()
        fetch_single.assert_not_called()

    def test_missing_entry_triggers_one_bounded_fallback_in_both_scripts(self):
        frame = _history_frame()
        for module in (self.scan, self.fetch):
            with self.subTest(module=module.__name__):
                with patch.object(
                    module, "fetch_stock_hist", return_value=(frame, "tencent_hist_tx")
                ) as single:
                    module._resolve_history("600000", None)
                single.assert_called_once()
                self.assertIn("request_timeout", single.call_args.kwargs)

    def test_error_policy_difference_is_intentional_and_documented(self):
        """scan 吞异常记为无数据；fetch_stock_data 抛出交给 main() 记 errors。"""
        with patch.object(self.scan, "fetch_stock_hist", side_effect=RuntimeError("down")):
            self.assertEqual(self.scan._resolve_history("600000", None), (None, None))
        with patch.object(self.fetch, "fetch_stock_hist", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                self.fetch._resolve_history("600000", None)


if __name__ == "__main__":
    unittest.main()
