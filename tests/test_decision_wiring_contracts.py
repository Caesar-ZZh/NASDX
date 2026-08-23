# -*- coding: utf-8 -*-
"""#74 决策记录生产接线契约测试。

覆盖 Issue #74 的六条验收：
1. rules / full / intraday 三条生产路径各产出一条字段完整的冻结记录；
2. ``NASDX_DECISION_RECORDS=0`` 时行为与接线前一致，且**不创建数据库文件**；
3. 落库异常不打断分析和盘中快照（fail-open）；
4. 标签回填入口可重复执行且幂等，绝不写 ``decision_records``；
5. 报告 CLI 在样本不足时明确不下结论；
6. 以上均由自动化测试覆盖。

所有用例都显式传入临时 db_path / ``NASDX_DECISION_DB``，避免在仓库根目录
留下 ``nasdx_decisions.db``（该文件并不在 .gitignore 里）。
"""
import contextlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx import decision_record as dr  # noqa: E402
from nasdx import decision_wiring as dw  # noqa: E402
from nasdx.decision_backfill import backfill_labels  # noqa: E402


@contextlib.contextmanager
def env_var(key, value):
    """只设置/还原单个环境变量（本环境有超长变量，patch.dict 会报 ValueError）。"""
    old = os.environ.get(key)
    try:
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


@contextlib.contextmanager
def temp_db():
    """给出一个尚不存在的 db 路径，并把 NASDX_DECISION_DB 指过去。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wiring_test.db"
        with env_var(dr.DECISION_DB_ENV, str(path)):
            yield path


# ---------------------------------------------------------------------------
# 测试替身：只带被接线代码真正读取的字段
# ---------------------------------------------------------------------------
class FakeReport:
    """最小可用的 FinalReport 替身。"""

    def __init__(self, code="600519", action="分批布局", date="2026-01-05"):
        self.stock_code = code
        self.date = date
        self.decision_plan = {
            "stock_code": code,
            "stock_name": "样本标的",
            "action": action,
            "confidence": 0.72,
            "entry_conditions": ["站上20日线"],
            "exit_conditions": ["跌破成本线5%"],
            "review_triggers": ["行业逻辑变化"],
            "portfolio_snapshot_hash": "portfolio-hash-abc",
        }
        self.performance = {
            "provider": "test-provider",
            "model": "test-model",
            "llm_call_count": 7,
            "total_elapsed_ms": 1234.5,
        }


class FakePosition:
    def __init__(self, price=18.5):
        self.current_price = price


class FakeIntradayDecision:
    def __init__(self, code="512880", action="hold", price=18.5):
        self.code = code
        self.name = "证券ETF"
        self.industry = "非银金融"
        self.action = action
        self.data_as_of = "2026-01-05T10:30:00"
        self.generated_at = "2026-01-05T10:30:05"
        self.decision_id = ""
        self.confidence = 0.6
        self.trigger = "分时站稳均价线"
        self.invalidation = "跌破日内低点"
        self.snapshot_hash = "portfolio-hash-intraday"
        self.position = FakePosition(price)


MARKET_DATA = {
    "date": "20260105",
    "sectors": [
        {
            "name": "白酒",
            "stocks": [{"code": "600519", "name": "贵州茅台", "close": 1500.0}],
            "etfs": [{"code": "512880", "name": "证券ETF", "close": 18.5}],
        }
    ],
}


def _bars(start_price=100.0, days=25, step=1.0):
    """生成一段稳定上行的日线，日期从 2026-01-05 起。"""
    import datetime as dt

    out = []
    day = dt.date(2026, 1, 5)
    price = start_price
    for _ in range(days):
        out.append(
            {
                "date": day.isoformat(),
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 1_000_000,
            }
        )
        day += dt.timedelta(days=1)
        price += step
    return out


# ---------------------------------------------------------------------------
# 验收 1：三条生产路径同一 schema、字段完整
# ---------------------------------------------------------------------------
class ThreeModesSameSchemaTest(unittest.TestCase):
    REQUIRED = (
        "provider",
        "model",
        "prompt_schema_version",
        "agent_config_version",
        "portfolio_snapshot_hash",
        "market_snapshot_hash",
    )

    def test_rules_full_intraday_all_produce_complete_records(self):
        with temp_db() as path:
            mhash = dw.market_snapshot_hash_from_data(MARKET_DATA)
            self.assertTrue(mhash, "市场快照哈希不能为空")

            made = {}
            for mode in ("rules", "full"):
                record = dw.record_report_if_enabled(
                    FakeReport(),
                    reference_price=1500.0,
                    mode=mode,
                    industry="白酒",
                    market_snapshot_hash=mhash,
                )
                self.assertIsNotNone(record, f"{mode} 路径应产出记录")
                made[mode] = record

            intraday = dw.record_intraday_if_enabled(
                FakeIntradayDecision(), market_snapshot_hash=mhash
            )
            self.assertIsNotNone(intraday, "intraday 路径应产出记录")
            made["intraday"] = intraday

            for mode, record in made.items():
                self.assertEqual(record.mode, mode)
                self.assertEqual(record.schema, dr.DECISION_RECORD_SCHEMA)
                self.assertGreater(record.reference_price, 0)
                self.assertIn(record.evaluation_class, dr.EVALUATION_CLASSES)
                for field in self.REQUIRED:
                    self.assertTrue(
                        getattr(record, field),
                        f"{mode} 记录缺少字段 {field}",
                    )

            stored = dr.list_records(db_path=str(path))
            self.assertEqual(len(stored), 3, "三条路径应各落一条记录")
            self.assertEqual(
                sorted(r.mode for r in stored), ["full", "intraday", "rules"]
            )

    def test_intraday_reads_current_price_from_position(self):
        """PositionView 用的是 current_price，不是 last_price。"""
        with temp_db():
            record = dw.record_intraday_if_enabled(FakeIntradayDecision(price=22.25))
            self.assertIsNotNone(record)
            self.assertAlmostEqual(record.reference_price, 22.25)

    def test_intraday_accepts_serialised_snapshot_dict(self):
        """snapshot['decisions'] 里存的是 to_dict() 结果，也必须能落库。"""
        with temp_db():
            payload = {
                "code": "512880",
                "name": "证券ETF",
                "industry": "非银金融",
                "action": "hold",
                "data_as_of": "2026-01-05T10:30:00",
                "generated_at": "2026-01-05T10:30:05",
                "confidence": 0.6,
                "trigger": "站稳均价线",
                "invalidation": "跌破日内低点",
                "snapshot_hash": "hash-1",
                "position": {"quantity": 100, "current_price": 18.5},
            }
            record = dw.record_intraday_if_enabled(payload)
            self.assertIsNotNone(record)
            self.assertAlmostEqual(record.reference_price, 18.5)

    def test_portfolio_candidate_needs_explicit_class(self):
        """组合候选动作串不在内置映射里，必须显式传 evaluation_class。"""
        with temp_db():
            plan = {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "action": "优先跟踪，等待入场条件",
                "entry_conditions": ["回踩不破20日线"],
                "exit_conditions": ["跌破前低"],
                "review_triggers": ["下一轮扫描"],
            }
            without = dw.record_candidate_if_enabled(
                plan, reference_price=1500.0, data_as_of="2026-01-05"
            )
            self.assertIsNone(without, "未给类别时应 fail-open 返回 None")

            with_class = dw.record_candidate_if_enabled(
                plan,
                reference_price=1500.0,
                data_as_of="2026-01-05",
                evaluation_class=dr.CLASS_BUY,
            )
            self.assertIsNotNone(with_class)
            self.assertEqual(with_class.evaluation_class, dr.CLASS_BUY)
            self.assertEqual(with_class.mode, "portfolio")


# ---------------------------------------------------------------------------
# 验收 2：开关关闭 → 行为一致 + 不建库文件
# ---------------------------------------------------------------------------
class SwitchOffTest(unittest.TestCase):
    def test_disabled_writes_nothing_and_creates_no_file(self):
        with temp_db() as path:
            with env_var(dr.DECISION_RECORDS_ENV, "0"):
                self.assertFalse(dr.persistence_enabled())
                self.assertIsNone(
                    dw.record_report_if_enabled(FakeReport(), reference_price=1500.0)
                )
                self.assertIsNone(
                    dw.record_intraday_if_enabled(FakeIntradayDecision())
                )
                self.assertIsNone(
                    dw.record_candidate_if_enabled(
                        {"stock_code": "600519", "action": "只观察"},
                        reference_price=1500.0,
                        data_as_of="2026-01-05",
                        evaluation_class=dr.CLASS_AVOID,
                    )
                )
            self.assertFalse(path.exists(), "关闭时绝不允许创建数据库文件")

    def test_on_off_difference_is_only_persistence(self):
        """开关只影响是否落库，不影响调用方拿到的分析结果对象。"""
        report = FakeReport()
        before = dict(report.decision_plan)
        with temp_db():
            with env_var(dr.DECISION_RECORDS_ENV, "0"):
                dw.record_report_if_enabled(report, reference_price=1500.0)
            self.assertEqual(report.decision_plan, before)
        with temp_db():
            dw.record_report_if_enabled(report, reference_price=1500.0)
            self.assertEqual(report.decision_plan, before)


# ---------------------------------------------------------------------------
# 验收 3：fail-open
# ---------------------------------------------------------------------------
class FailOpenTest(unittest.TestCase):
    def test_bad_inputs_never_raise(self):
        with temp_db():
            cases = [
                lambda: dw.record_report_if_enabled(None, reference_price=1500.0),
                lambda: dw.record_report_if_enabled(
                    FakeReport(), reference_price=None
                ),
                lambda: dw.record_report_if_enabled(
                    FakeReport(), reference_price=float("nan")
                ),
                lambda: dw.record_report_if_enabled(
                    FakeReport(), reference_price=-1.0
                ),
                lambda: dw.record_report_if_enabled(
                    FakeReport(date=""), reference_price=1500.0
                ),
                lambda: dw.record_report_if_enabled(
                    FakeReport(action="乱七八糟不认识的动作"), reference_price=1500.0
                ),
                lambda: dw.record_intraday_if_enabled(None),
                lambda: dw.record_intraday_if_enabled(object()),
                lambda: dw.record_candidate_if_enabled(
                    None, reference_price=1.0, data_as_of="bad"
                ),
            ]
            for index, call in enumerate(cases):
                self.assertIsNone(call(), f"第 {index} 个异常输入应返回 None")

    def test_persistence_error_does_not_propagate(self):
        """底层落库抛错时也必须吞掉。"""
        with temp_db():
            original = dr.record_decision

            def boom(record, **kwargs):
                raise sqlite3.OperationalError("database is locked")

            dw.record_decision = boom
            try:
                self.assertIsNone(
                    dw.record_report_if_enabled(FakeReport(), reference_price=1500.0)
                )
            finally:
                dw.record_decision = original

    def test_normalize_data_as_of_rejects_empty(self):
        self.assertEqual(dw.normalize_data_as_of("20260105"), "2026-01-05")
        self.assertEqual(dw.normalize_data_as_of("2026-01-05"), "2026-01-05")
        self.assertEqual(
            dw.normalize_data_as_of("2026-01-05T10:30:00"), "2026-01-05"
        )
        with self.assertRaises(ValueError):
            dw.normalize_data_as_of("")
        with self.assertRaises(ValueError):
            dw.normalize_data_as_of(None)


# ---------------------------------------------------------------------------
# 验收 4：回填幂等，且绝不写 decision_records
# ---------------------------------------------------------------------------
class BackfillIdempotentTest(unittest.TestCase):
    def _seed(self, path):
        record = dr.build_decision_record(
            code="600519",
            mode="full",
            action="分批布局",
            reference_price=100.0,
            data_as_of="2026-01-05",
            generated_at="2026-01-05T15:00:00",
        )
        dr.record_decision(record, db_path=str(path))
        return record

    def test_rerun_is_idempotent(self):
        with temp_db() as path:
            self._seed(path)
            prices = _bars()

            def price_fn(code, start, end):
                return prices

            first = backfill_labels(db_path=str(path), price_fn=price_fn)
            second = backfill_labels(db_path=str(path), price_fn=price_fn)
            self.assertEqual(first, second, "重复回填结果必须一致")
            self.assertEqual(first["records"], 1)
            self.assertEqual(first["labeled"], 1)

            pairs_a = dr.load_pairs(db_path=str(path))
            backfill_labels(db_path=str(path), price_fn=price_fn)
            pairs_b = dr.load_pairs(db_path=str(path))
            self.assertEqual(len(pairs_a), len(pairs_b))
            self.assertEqual(pairs_a[0][1], pairs_b[0][1], "标签必须稳定")

    def test_backfill_never_touches_decision_records(self):
        with temp_db() as path:
            record = self._seed(path)
            before = dr.list_records(db_path=str(path))

            def price_fn(code, start, end):
                return _bars()

            backfill_labels(db_path=str(path), price_fn=price_fn)
            after = dr.list_records(db_path=str(path))
            self.assertEqual(len(before), len(after))
            self.assertEqual(before[0].to_dict(), after[0].to_dict())
            self.assertEqual(after[0].decision_id, record.decision_id)

    def test_missing_prices_are_skipped_not_fatal(self):
        with temp_db() as path:
            self._seed(path)
            summary = backfill_labels(db_path=str(path), price_fn=lambda *a: None)
            self.assertEqual(summary["skipped"], 1)
            self.assertEqual(summary["labeled"], 0)
            self.assertEqual(summary["errors"], 0)

    def test_fetcher_exception_counted_not_raised(self):
        with temp_db() as path:
            self._seed(path)

            def boom(code, start, end):
                raise RuntimeError("network down")

            summary = backfill_labels(db_path=str(path), price_fn=boom)
            self.assertEqual(summary["errors"], 1)


# ---------------------------------------------------------------------------
# 验收 5：报告 CLI 样本不足时不下结论
# ---------------------------------------------------------------------------
class EvaluationCliTest(unittest.TestCase):
    def _args(self, **over):
        import argparse

        base = dict(
            code=None, mode=None, since=None, db=None,
            horizon=5, min_samples=20, include_non_executable=False,
            by_class=False, calibration=False, compare=False,
            ablation=False, split_at=None, output=None,
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_no_samples_refuses_to_conclude(self):
        from scripts import run_decision_evaluation as cli

        with temp_db() as path:
            report = cli.build_report(self._args(db=str(path)))
            self.assertEqual(report["samples"], 0)
            self.assertEqual(report["verdict"], "insufficient_sample")
            markdown = cli.format_evaluation_report(report)
            self.assertIn("insufficient_sample", markdown)

    def test_small_sample_marks_insufficient(self):
        from scripts import run_decision_evaluation as cli

        with temp_db() as path:
            record = dr.build_decision_record(
                code="600519", mode="full", action="分批布局",
                reference_price=100.0, data_as_of="2026-01-05",
                generated_at="2026-01-05T15:00:00",
            )
            dr.record_decision(record, db_path=str(path))
            backfill_labels(db_path=str(path), price_fn=lambda *a: _bars())

            report = cli.build_report(self._args(db=str(path)))
            self.assertLess(report["samples"], 20)
            self.assertEqual(report["verdict"], dr_insufficient())
            markdown = cli.format_evaluation_report(report)
            self.assertIn("样本量", markdown)

    def test_optional_sections_render(self):
        from scripts import run_decision_evaluation as cli

        with temp_db() as path:
            for code, action in (("600519", "分批布局"), ("512880", "观察等待")):
                record = dr.build_decision_record(
                    code=code, mode="full", action=action,
                    reference_price=100.0, data_as_of="2026-01-05",
                    generated_at="2026-01-05T15:00:00", confidence=0.8,
                )
                dr.record_decision(record, db_path=str(path))
            backfill_labels(db_path=str(path), price_fn=lambda *a: _bars())

            report = cli.build_report(
                self._args(db=str(path), by_class=True, calibration=True, compare=True)
            )
            self.assertIn("by_class", report)
            self.assertIn("calibration", report)
            self.assertIn("modes", report)
            self.assertIsNone(report["best_mode"], "样本不足时不得给出最佳模式")
            markdown = cli.format_evaluation_report(report)
            self.assertIn("分动作类别", markdown)
            self.assertIn("置信度校准", markdown)
            self.assertIn("不下结论", markdown)


def dr_insufficient():
    from nasdx.decision_evaluation import VERDICT_INSUFFICIENT

    return VERDICT_INSUFFICIENT


# ---------------------------------------------------------------------------
# 接线存在性：入口脚本必须真的调用了 wiring（防止回归时被摘掉）
# ---------------------------------------------------------------------------
class EntryPointsWiredTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def test_production_entry_points_call_wiring(self):
        expected = {
            "run_analysis.py": "record_report_if_enabled",
            "analyze.py": "record_report_if_enabled",
            "run_intraday_copilot.py": "record_intraday_if_enabled",
            "run_investment_workflow.py": "record_candidate_if_enabled",
        }
        for name, symbol in expected.items():
            text = (self.ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("nasdx.decision_wiring", text, f"{name} 未导入接线模块")
            self.assertIn(symbol, text, f"{name} 未调用 {symbol}")

    def test_cli_entry_points_exist(self):
        for name in ("run_decision_backfill.py", "run_decision_evaluation.py"):
            self.assertTrue((self.ROOT / "scripts" / name).exists(), f"缺少 CLI {name}")

    def test_wiring_calls_are_guarded(self):
        """接线调用必须被 try/except 包住，异常不能冒泡到主流程。"""
        for name in (
            "run_analysis.py",
            "analyze.py",
            "run_intraday_copilot.py",
            "run_investment_workflow.py",
        ):
            text = (self.ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("决策记录落库跳过", text, f"{name} 缺少 fail-open 兜底")


if __name__ == "__main__":
    unittest.main(verbosity=2)
