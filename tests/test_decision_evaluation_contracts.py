# -*- coding: utf-8 -*-
"""#68 建议决策记录 / 前瞻结果标签 / 多模式消融评估 契约测试。

覆盖验收点：
1. 决策记录冻结不可变，字段校验（价格/周期/置信度/时间序）
2. 同内容重复写入幂等；同 id 内容变化必须拒绝（不可篡改）
3. 结果标签只使用 data_as_of 之后的 K 线（无前视）
4. 交易日对齐以真实 K 线为准（跳过周末/停牌自然日）
5. 停牌 / 一字涨停 首日不可成交标记
6. MFE / MAE 与最大回撤
7. 类别语义（buy/hold 涨为好，reduce/avoid 跌为好）
8. 止损/止盈首次触发顺序（同一根 K 线内不利优先）
9. 基准与超额收益
10. 样本量不足必须先报样本量、不下结论
11. 置信度校准分桶
12. 多模式对比 / 边际贡献 / 时间切分消融（含泄漏防护）
13. 单位约定：*_pct 一律为百分比（5.0 表示 5%）
14. 隐私：自由文本落库前脱敏
"""
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx import decision_evaluation as de  # noqa: E402
from nasdx import decision_record as dr  # noqa: E402
from nasdx import outcome_labels as ol  # noqa: E402


def bar(date, close, high=None, low=None, open_=None, volume=1000.0):
    return {
        "date": date,
        "open": close if open_ is None else open_,
        "high": close if high is None else high,
        "low": close if low is None else low,
        "close": close,
        "volume": volume,
    }


def series(start_day, closes, **kw):
    """Build consecutive trading bars starting at 2026-01-<start_day>."""
    out = []
    for i, c in enumerate(closes):
        out.append(bar("2026-01-%02d" % (start_day + i), c, **kw))
    return out


def make_record(**kw):
    params = dict(
        code="600519",
        mode="full",
        action="分批布局",
        reference_price=100.0,
        data_as_of="2026-01-05",
        horizon_trading_days=5,
    )
    params.update(kw)
    return dr.build_decision_record(**params)


POLICY_5 = ol.with_horizons(ol.LabelPolicy(), (1, 3, 5))


# ---------------------------------------------------------------------------
# 1. 决策记录：冻结、校验、幂等
# ---------------------------------------------------------------------------
class DecisionRecordContractTest(unittest.TestCase):
    def test_record_is_frozen(self):
        rec = make_record()
        with self.assertRaises(FrozenInstanceError):
            rec.reference_price = 1.0  # type: ignore[misc]

    def test_decision_id_is_content_hash_and_stable(self):
        a = make_record(generated_at="2026-01-05T15:00:00+08:00")
        b = make_record(generated_at="2026-01-05T15:00:00+08:00")
        self.assertEqual(a.decision_id, b.decision_id)
        c = make_record(generated_at="2026-01-05T15:00:00+08:00", reference_price=101.0)
        self.assertNotEqual(a.decision_id, c.decision_id)

    def test_reference_price_must_be_positive_finite(self):
        for bad in (0.0, -1.0, float("nan"), float("inf"), "abc", None, True):
            with self.assertRaises(dr.DecisionRecordError):
                make_record(reference_price=bad)

    def test_horizon_must_be_positive_int(self):
        for bad in (0, -3, 2.5, "5", True):
            with self.assertRaises(dr.DecisionRecordError):
                make_record(horizon_trading_days=bad)

    def test_confidence_range(self):
        for bad in (-0.01, 1.01, float("nan"), True):
            with self.assertRaises(dr.DecisionRecordError):
                make_record(confidence=bad)
        self.assertEqual(make_record(confidence=0.0).confidence, 0.0)
        self.assertEqual(make_record(confidence=1.0).confidence, 1.0)

    def test_data_as_of_must_not_exceed_generated_at(self):
        with self.assertRaises(dr.DecisionRecordError):
            make_record(data_as_of="2026-02-01", generated_at="2026-01-05T15:00:00+08:00")

    def test_stop_and_target_are_percent_units(self):
        """5.0 表示 5%，禁止把 0.05 当 5% 传入。"""
        rec = make_record(stop_loss_pct=5.0, take_profit_pct=8.0)
        self.assertEqual(rec.stop_loss_pct, 5.0)
        for bad in (0.0, 100.0, 150.0, -5.0):
            with self.assertRaises(dr.DecisionRecordError):
                make_record(stop_loss_pct=bad)

    def test_action_classification(self):
        self.assertEqual(dr.classify_action("分批布局"), "buy")
        self.assertEqual(dr.classify_action("轻仓试错"), "buy")
        self.assertEqual(dr.classify_action("buy_first_lot"), "buy")
        self.assertEqual(dr.classify_action("hold"), "hold")
        self.assertEqual(dr.classify_action("reduce"), "reduce")
        self.assertEqual(dr.classify_action("回避或减仓", held=True), "reduce")
        self.assertEqual(dr.classify_action("回避或减仓", held=False), "avoid")
        for cls in dr.EVALUATION_CLASSES:
            self.assertIn(cls, dr.CLASS_SIGN)

    def test_unknown_class_rejected(self):
        with self.assertRaises(dr.DecisionRecordError):
            make_record(evaluation_class="moon")


# ---------------------------------------------------------------------------
# 2. 持久化：幂等、不可篡改、结果分表
# ---------------------------------------------------------------------------
class DecisionPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nasdx_dec_")
        self.db = os.path.join(self.tmp, "decisions.db")
        dr.init_decision_db(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_insert_is_idempotent(self):
        rec = make_record(generated_at="2026-01-05T15:00:00+08:00")
        dr.record_decision(rec, db_path=self.db)
        dr.record_decision(rec, db_path=self.db)
        self.assertEqual(len(dr.list_records(db_path=self.db)), 1)

    def test_content_change_on_same_id_is_rejected(self):
        rec = make_record(generated_at="2026-01-05T15:00:00+08:00")
        dr.record_decision(rec, db_path=self.db)
        tampered = dr.build_decision_record(
            code="600519",
            mode="full",
            action="分批布局",
            reference_price=999.0,
            data_as_of="2026-01-05",
            generated_at="2026-01-05T15:00:00+08:00",
            horizon_trading_days=5,
            decision_id=rec.decision_id,
        )
        with self.assertRaises(dr.DecisionRecordError):
            dr.record_decision(tampered, db_path=self.db)
        stored = dr.get_record(rec.decision_id, db_path=self.db)
        self.assertEqual(stored.reference_price, 100.0)

    def test_roundtrip_preserves_fields(self):
        rec = make_record(
            generated_at="2026-01-05T15:00:00+08:00",
            confidence=0.66,
            stop_loss_pct=5.0,
            take_profit_pct=9.0,
            name="贵州茅台",
            industry="白酒",
            llm_calls=3,
            latency_ms=1234.5,
        )
        dr.record_decision(rec, db_path=self.db)
        got = dr.get_record(rec.decision_id, db_path=self.db)
        self.assertEqual(got.confidence, 0.66)
        self.assertEqual(got.stop_loss_pct, 5.0)
        self.assertEqual(got.llm_calls, 3)
        self.assertEqual(got.evaluation_class, "buy")

    def test_outcome_stored_separately_and_never_mutates_record(self):
        rec = make_record(generated_at="2026-01-05T15:00:00+08:00")
        dr.record_decision(rec, db_path=self.db)
        labels = ol.compute_forward_labels(
            rec, series(5, [100, 102, 104, 106, 108, 110]), policy=POLICY_5
        )
        dr.save_outcome(rec.decision_id, labels, db_path=self.db)
        again = dr.get_record(rec.decision_id, db_path=self.db)
        self.assertEqual(again.reference_price, rec.reference_price)
        stored = dr.get_outcome(rec.decision_id, db_path=self.db)
        self.assertEqual(stored["decision_id"], rec.decision_id)
        pairs = dr.load_pairs(db_path=self.db)
        self.assertEqual(len(pairs), 1)

    def test_deterministic_ordering(self):
        made = []
        for i, day in enumerate(["2026-01-05", "2026-01-06", "2026-01-07"]):
            r = make_record(data_as_of=day, generated_at="%sT15:00:00+08:00" % day,
                            reference_price=100.0 + i)
            dr.record_decision(r, db_path=self.db)
            made.append(r)
        first = [r.decision_id for r in dr.list_records(db_path=self.db)]
        second = [r.decision_id for r in dr.list_records(db_path=self.db)]
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)

    def test_free_text_credentials_are_redacted_before_persist(self):
        """自由文本走 decision_log.sanitize_text，凭据形态不得落库。"""
        # 动态拼接，避免仓库 secret 扫描把测试样例当成真实泄漏
        fake_key = "sk" + "-" + ("a1b2c3d4e5" * 3)
        rec = make_record(
            generated_at="2026-01-05T15:00:00+08:00",
            stop_condition="密钥 %s 跌破20日线离场" % fake_key,
        )
        self.assertNotIn(fake_key, rec.stop_condition)
        dr.record_decision(rec, db_path=self.db)
        got = dr.get_record(rec.decision_id, db_path=self.db)
        self.assertNotIn(fake_key, got.stop_condition)
        self.assertIn("跌破20日线离场", got.stop_condition)

    def test_status_and_clear(self):
        dr.record_decision(make_record(generated_at="2026-01-05T15:00:00+08:00"), db_path=self.db)
        st = dr.decision_status(self.db)
        self.assertGreaterEqual(st["records"], 1)
        dr.clear_decisions(self.db)
        self.assertEqual(dr.decision_status(self.db)["records"], 0)


# ---------------------------------------------------------------------------
# 3. 前瞻标签：无前视、交易日对齐、停牌涨停
# ---------------------------------------------------------------------------
class ForwardLabelTest(unittest.TestCase):
    def test_window_starts_strictly_after_data_as_of(self):
        rec = make_record(data_as_of="2026-01-05")
        bars = series(5, [100, 102, 104, 106, 108, 110])
        labels = ol.compute_forward_labels(rec, bars, policy=POLICY_5)
        self.assertEqual(labels["first_forward_date"], "2026-01-06")
        self.assertEqual(labels["horizons"]["T+1"]["date"], "2026-01-06")
        self.assertEqual(labels["horizons"]["T+1"]["close"], 102.0)

    def test_entry_price_is_frozen_reference_not_future_price(self):
        rec = make_record(reference_price=100.0, data_as_of="2026-01-05")
        bars = series(5, [100, 200, 300, 400, 500, 600])
        labels = ol.compute_forward_labels(rec, bars, policy=POLICY_5)
        self.assertEqual(labels["entry_price"], 100.0)
        self.assertAlmostEqual(labels["horizons"]["T+1"]["return_pct"], 100.0, places=6)

    def test_trading_day_alignment_skips_calendar_gaps(self):
        """T+3 应落在第 3 根可用 K 线，而不是自然日 +3。"""
        rec = make_record(data_as_of="2026-01-05")
        bars = [
            bar("2026-01-05", 100),
            bar("2026-01-06", 101),
            bar("2026-01-09", 102),  # 跳过 07/08
            bar("2026-01-20", 103),  # 长假
            bar("2026-01-21", 104),
        ]
        labels = ol.compute_forward_labels(rec, bars, policy=POLICY_5)
        self.assertEqual(labels["horizons"]["T+3"]["date"], "2026-01-20")
        self.assertEqual(labels["horizons"]["T+3"]["trading_days"], 3)

    def test_unsorted_and_duplicate_bars_normalized(self):
        raw = [bar("2026-01-07", 104), bar("2026-01-05", 100),
               bar("2026-01-06", 102), bar("2026-01-06", 102)]
        bars = ol.normalize_bars(raw)
        self.assertEqual([b.date for b in bars], ["2026-01-05", "2026-01-06", "2026-01-07"])

    def test_suspended_first_bar_marks_non_executable(self):
        rec = make_record(data_as_of="2026-01-05")
        bars = [bar("2026-01-05", 100),
                bar("2026-01-06", 100, volume=0.0),
                bar("2026-01-07", 102), bar("2026-01-08", 103)]
        labels = ol.compute_forward_labels(rec, bars, policy=POLICY_5)
        self.assertEqual(labels["entry_state"], "suspended")
        self.assertFalse(labels["entry_executable"])
        self.assertEqual(labels["suspended_days"], 1)

    def test_limit_up_first_bar_marks_non_executable(self):
        rec = make_record(data_as_of="2026-01-05")
        bars = [bar("2026-01-05", 100),
                bar("2026-01-06", 110, high=110, low=110, open_=110),
                bar("2026-01-07", 112, high=113, low=111),
                bar("2026-01-08", 113, high=114, low=112)]
        labels = ol.compute_forward_labels(rec, bars, policy=POLICY_5)
        self.assertEqual(labels["entry_state"], "limit_up")
        self.assertFalse(labels["entry_executable"])

    def test_growth_board_uses_wider_limit(self):
        self.assertAlmostEqual(ol.board_limit_pct("600519"), 9.5)
        self.assertAlmostEqual(ol.board_limit_pct("300750"), 19.5)
        self.assertAlmostEqual(ol.board_limit_pct("688981"), 19.5)
        self.assertAlmostEqual(ol.board_limit_pct("830799"), 29.5)
        # 创业板 +12% 一字不算涨停锁死
        rec = make_record(code="300750", data_as_of="2026-01-05")
        bars = [bar("2026-01-05", 100),
                bar("2026-01-06", 112, high=112, low=112, open_=112),
                bar("2026-01-07", 113, high=114, low=112),
                bar("2026-01-08", 114, high=115, low=113)]
        labels = ol.compute_forward_labels(rec, bars, policy=POLICY_5)
        self.assertTrue(labels["entry_executable"])

    def test_missing_price_data_is_reported_not_guessed(self):
        rec = make_record(data_as_of="2026-01-05")
        labels = ol.compute_forward_labels(rec, [bar("2026-01-05", 100)], policy=POLICY_5)
        self.assertEqual(labels["status"], "insufficient_data")
        self.assertEqual(labels["bars_available"], 0)
        self.assertIsNone(labels["horizons"]["T+1"]["return_pct"])

    def test_mfe_mae_use_intrabar_extremes(self):
        rec = make_record(data_as_of="2026-01-05", horizon_trading_days=3)
        bars = [bar("2026-01-05", 100),
                bar("2026-01-06", 101, high=108, low=97),
                bar("2026-01-07", 102, high=104, low=95),
                bar("2026-01-08", 103, high=105, low=99)]
        labels = ol.compute_forward_labels(rec, bars, policy=ol.with_horizons(ol.LabelPolicy(), (1, 3)))
        self.assertAlmostEqual(labels["mfe_pct"], 8.0, places=6)
        self.assertAlmostEqual(labels["mae_pct"], -5.0, places=6)
        self.assertAlmostEqual(labels["favorable_pct"], 8.0, places=6)
        self.assertAlmostEqual(labels["adverse_pct"], -5.0, places=6)

    def test_class_aware_semantics_for_reduce(self):
        """减仓/回避：价格下跌是正确决策，class_return 应为正。"""
        up = series(5, [100, 100, 105, 110, 115, 120])
        down = series(5, [100, 100, 95, 90, 85, 80])
        buy = make_record(action="分批布局", data_as_of="2026-01-05")
        avoid = make_record(action="回避或减仓", held=False, data_as_of="2026-01-05")
        self.assertEqual(buy.evaluation_class, "buy")
        self.assertEqual(avoid.evaluation_class, "avoid")

        buy_down = ol.compute_forward_labels(buy, down, policy=POLICY_5)
        avoid_down = ol.compute_forward_labels(avoid, down, policy=POLICY_5)
        self.assertLess(buy_down["horizons"]["T+5"]["class_return_pct"], 0)
        self.assertGreater(avoid_down["horizons"]["T+5"]["class_return_pct"], 0)

        avoid_up = ol.compute_forward_labels(avoid, up, policy=POLICY_5)
        self.assertLess(avoid_up["horizons"]["T+5"]["class_return_pct"], 0)
        # 类别语义下 MFE/MAE 也要翻转
        self.assertGreater(avoid_down["favorable_pct"], 0)

    def test_first_trigger_prefers_adverse_within_same_bar(self):
        rec = make_record(data_as_of="2026-01-05", stop_loss_pct=5.0, take_profit_pct=5.0,
                          horizon_trading_days=3)
        bars = [bar("2026-01-05", 100),
                bar("2026-01-06", 100, high=106, low=94),  # 同一根同时打到止损止盈
                bar("2026-01-07", 101), bar("2026-01-08", 102)]
        labels = ol.compute_forward_labels(rec, bars,
                                           policy=ol.with_horizons(ol.LabelPolicy(), (1, 3)))
        trig = labels["first_trigger"]
        self.assertEqual(trig["kind"], "stop")
        self.assertTrue(trig["same_bar_ambiguous"])
        self.assertEqual(trig["trading_days"], 1)

    def test_first_trigger_order_is_chronological(self):
        rec = make_record(data_as_of="2026-01-05", stop_loss_pct=5.0, take_profit_pct=8.0,
                          horizon_trading_days=5)
        bars = [bar("2026-01-05", 100),
                bar("2026-01-06", 97, high=98, low=94),   # 先打止损 95
                bar("2026-01-07", 105, high=110, low=104),  # 后到止盈 108
                bar("2026-01-08", 106), bar("2026-01-09", 107), bar("2026-01-12", 108)]
        labels = ol.compute_forward_labels(rec, bars, policy=POLICY_5)
        self.assertEqual(labels["first_trigger"]["kind"], "stop")
        self.assertEqual(labels["first_trigger"]["trading_days"], 1)

    def test_stop_level_uses_percent_semantics(self):
        """stop_loss_pct=5.0 -> 止损价 95，而不是 99.95。"""
        rec = make_record(data_as_of="2026-01-05", stop_loss_pct=5.0, horizon_trading_days=3)
        bars = [bar("2026-01-05", 100),
                bar("2026-01-06", 99.9, high=100.2, low=99.8),  # 未触发 95
                bar("2026-01-07", 99.5, high=100, low=99.4),
                bar("2026-01-08", 99.4, high=99.8, low=99.2)]
        labels = ol.compute_forward_labels(rec, bars,
                                           policy=ol.with_horizons(ol.LabelPolicy(), (1, 3)))
        self.assertIsNone(labels["first_trigger"])

    def test_benchmark_excess_return(self):
        rec = make_record(data_as_of="2026-01-05", benchmark_code="000300")
        stock = series(5, [100, 100, 105, 110, 115, 120])
        bench = series(5, [100, 100, 102, 104, 106, 108])
        labels = ol.compute_forward_labels(rec, stock, benchmark=bench, policy=POLICY_5)
        h5 = labels["horizons"]["T+5"]
        self.assertAlmostEqual(h5["return_pct"], 20.0, places=6)
        self.assertAlmostEqual(h5["benchmark_return_pct"], 8.0, places=6)
        self.assertAlmostEqual(h5["excess_pct"], 12.0, places=6)

    def test_batch_labeling(self):
        recs = [make_record(code="600519", data_as_of="2026-01-05"),
                make_record(code="000001", data_as_of="2026-01-05", reference_price=50.0)]
        price_map = {"600519": series(5, [100, 101, 102, 103, 104, 105]),
                     "000001": series(5, [50, 51, 52, 53, 54, 55])}
        pairs = ol.compute_labels_batch(recs, price_map, policy=POLICY_5)
        self.assertEqual(len(pairs), 2)
        self.assertEqual({p[0].code for p in pairs}, {"600519", "000001"})


# ---------------------------------------------------------------------------
# 4. 评价与消融
# ---------------------------------------------------------------------------
def build_pairs(n, mode, day_start=5, drift=1.0, confidence=0.7, code="600519",
                drifts=None):
    """生成 n 组 (record, labels)。

    drift 给定时每组收益相同（零方差，用于确定性断言）；
    drifts 给定时逐条使用，用于构造有方差、可重叠的分布。
    """
    pairs = []
    for i in range(n):
        if drifts is not None:
            drift = drifts[i % len(drifts)]
        d = "2026-01-%02d" % (day_start + i)
        rec = dr.build_decision_record(
            code=code,
            mode=mode,
            action="分批布局",
            reference_price=100.0,
            data_as_of=d,
            generated_at="%sT15:00:00+08:00" % d,
            horizon_trading_days=5,
            confidence=confidence,
            llm_calls=2 if mode == "full" else 1,
            latency_ms=100.0,
            extra={"seq": i},
        )
        closes = [100.0] + [100.0 + drift * (k + 1) for k in range(6)]
        bars = []
        for k, c in enumerate(closes):
            bars.append(bar("2026-01-%02d" % (day_start + i + k), c))
        pairs.append((rec, ol.compute_forward_labels(rec, bars, policy=POLICY_5)))
    return pairs


class EvaluationTest(unittest.TestCase):
    def test_no_lookahead_guard_passes_for_valid_pairs(self):
        de.assert_no_lookahead(build_pairs(5, "full"))

    def test_leakage_detected_when_label_starts_on_or_before_decision_date(self):
        rec, labels = build_pairs(1, "full")[0]
        bad = dict(labels)
        bad["first_forward_date"] = rec.data_as_of  # 与决策同日 = 前视
        with self.assertRaises(de.EvaluationLeakageError):
            de.assert_no_lookahead([(rec, bad)])

    def test_leakage_detected_on_entry_price_drift(self):
        rec, labels = build_pairs(1, "full")[0]
        bad = dict(labels)
        bad["entry_price"] = 123.0
        with self.assertRaises(de.EvaluationLeakageError):
            de.assert_no_lookahead([(rec, bad)])

    def test_leakage_detected_on_id_mismatch(self):
        rec, labels = build_pairs(1, "full")[0]
        bad = dict(labels)
        bad["decision_id"] = "other"
        with self.assertRaises(de.EvaluationLeakageError):
            de.assert_no_lookahead([(rec, bad)])

    def test_small_sample_reports_size_and_refuses_conclusion(self):
        rep = de.evaluate_pairs(build_pairs(3, "full"), horizon=5, min_samples=20)
        self.assertEqual(rep["samples"], 3)
        self.assertEqual(rep["verdict"], "insufficient_sample")
        self.assertTrue(rep["notes"])

    def test_sufficient_sample_reports_ok(self):
        rep = de.evaluate_pairs(build_pairs(20, "full"), horizon=5, min_samples=20)
        self.assertEqual(rep["samples"], 20)
        self.assertEqual(rep["verdict"], "ok")
        self.assertAlmostEqual(rep["win_rate"], 1.0)
        self.assertGreater(rep["mean_return_pct"], 0)
        self.assertIsNotNone(rep["ci95_low_pct"])
        self.assertIsNotNone(rep["ci95_high_pct"])

    def test_aggregate_stats_are_deterministic(self):
        pairs = build_pairs(20, "full")
        a = de.evaluate_pairs(pairs, horizon=5, min_samples=20)
        b = de.evaluate_pairs(list(reversed(pairs)), horizon=5, min_samples=20)
        self.assertEqual(a["mean_return_pct"], b["mean_return_pct"])
        self.assertEqual(a["samples"], b["samples"])

    def test_non_executable_excluded_by_default(self):
        rec = make_record(data_as_of="2026-01-05", generated_at="2026-01-05T15:00:00+08:00")
        bars = [bar("2026-01-05", 100),
                bar("2026-01-06", 110, high=110, low=110, open_=110),
                bar("2026-01-07", 112), bar("2026-01-08", 113),
                bar("2026-01-09", 114), bar("2026-01-12", 115)]
        labels = ol.compute_forward_labels(rec, bars, policy=POLICY_5)
        rep = de.evaluate_pairs([(rec, labels)], horizon=5, min_samples=1)
        self.assertEqual(rep["samples"], 0)
        rep2 = de.evaluate_pairs([(rec, labels)], horizon=5, min_samples=1,
                                 include_non_executable=True)
        self.assertEqual(rep2["samples"], 1)

    def test_evaluate_by_class(self):
        pairs = build_pairs(10, "full") + build_pairs(10, "full", day_start=5, drift=-1.0)
        out = de.evaluate_by_class(pairs, horizon=5, min_samples=1)
        self.assertIn("buy", out)
        self.assertEqual(out["buy"]["samples"], 20)

    def test_confidence_calibration_buckets(self):
        pairs = build_pairs(10, "full", confidence=0.55) + \
            build_pairs(10, "full", day_start=5, drift=-1.0, confidence=0.95)
        cal = de.confidence_calibration(pairs, horizon=5, min_samples=1)
        self.assertIn("buckets", cal)
        self.assertTrue(any(b["samples"] > 0 for b in cal["buckets"]))

    def test_compare_modes_refuses_winner_when_intervals_overlap(self):
        """噪声主导、置信区间重叠时不得宣称最佳模式。"""
        noisy = [-3.0, -1.0, 0.0, 1.0, 3.0]
        pairs = build_pairs(20, "full", drifts=noisy) + \
            build_pairs(20, "light", day_start=5, drifts=noisy)
        cmp_ = de.compare_modes(pairs, horizon=5, min_samples=20)
        self.assertEqual(set(cmp_["modes"]), {"full", "light"})
        self.assertIsNone(cmp_["best_mode"])
        self.assertEqual(cmp_["reason"], "confidence_intervals_overlap")

    def test_compare_modes_declares_winner_when_clearly_separated(self):
        pairs = build_pairs(20, "full", drift=3.0) + \
            build_pairs(20, "light", day_start=5, drift=0.2)
        cmp_ = de.compare_modes(pairs, horizon=5, min_samples=20)
        self.assertEqual(cmp_["best_mode"], "full")
        self.assertEqual(cmp_["ranking"][0], "full")

    def test_compare_modes_withholds_winner_on_small_sample(self):
        pairs = build_pairs(3, "full", drift=3.0) + \
            build_pairs(3, "light", day_start=5, drift=0.2)
        cmp_ = de.compare_modes(pairs, horizon=5, min_samples=20)
        self.assertIsNone(cmp_["best_mode"])

    def test_marginal_contribution_reports_incremental_value(self):
        pairs = build_pairs(20, "light", drift=0.5) + \
            build_pairs(20, "full", day_start=5, drift=2.0)
        mc = de.marginal_contribution(pairs, "light", "full", horizon=5, min_samples=20)
        self.assertEqual(mc["baseline_mode"], "light")
        self.assertEqual(mc["variant_mode"], "full")
        # delta = variant - baseline，正数表示增量模块有贡献
        self.assertGreater(mc["delta_mean_return_pct"], 0)
        # 增量模块明显更好时，绝不能建议下线
        self.assertFalse(mc["safe_to_disable"])
        # 额外 LLM 调用成本也按 variant - baseline 记
        self.assertGreater(mc["delta_mean_llm_calls"], 0)

    def test_marginal_contribution_allows_disable_when_variant_adds_nothing(self):
        pairs = build_pairs(20, "light", drift=2.0) + \
            build_pairs(20, "full", day_start=5, drift=0.5)
        mc = de.marginal_contribution(pairs, "light", "full", horizon=5, min_samples=20)
        self.assertLess(mc["delta_mean_return_pct"], 0)
        self.assertTrue(mc["safe_to_disable"])

    def test_marginal_contribution_not_conclusive_on_small_sample(self):
        pairs = build_pairs(3, "light", drift=0.5) + \
            build_pairs(3, "full", day_start=5, drift=2.0)
        mc = de.marginal_contribution(pairs, "light", "full", horizon=5, min_samples=20)
        self.assertFalse(mc["conclusive"])
        self.assertFalse(mc["safe_to_disable"])

    def test_split_pairs_is_time_ordered(self):
        pairs = build_pairs(10, "full", day_start=5)
        train, test = de.split_pairs(pairs, "2026-01-10")
        self.assertTrue(all(r.data_as_of < "2026-01-10" for r, _ in train))
        self.assertTrue(all(r.data_as_of >= "2026-01-10" for r, _ in test))
        self.assertEqual(len(train) + len(test), len(pairs))

    def test_ablation_report_structure(self):
        pairs = build_pairs(12, "full", day_start=5) + build_pairs(12, "light", day_start=5)
        rep = de.ablation_report(pairs, split_at="2026-01-11", horizon=5, min_samples=5)
        self.assertIn("train", rep)
        self.assertIn("test", rep)
        self.assertIn("verification", rep)
        self.assertTrue(rep["leakage_checked"])
        self.assertIn("modes", rep["verification"])
        self.assertEqual(rep["schema"], de.EVALUATION_SCHEMA)

    def test_report_shows_sample_size_before_conclusion(self):
        rep = de.evaluate_pairs(build_pairs(3, "full"), horizon=5, min_samples=20)
        text = de.format_evaluation_report(rep)
        self.assertIn("样本量: 3", text)
        self.assertIn("insufficient_sample", text)
        # 样本量必须出现在任何结论之前
        self.assertLess(text.index("样本量"), text.index("insufficient_sample"))

    def test_ablation_report_text_shows_sample_size(self):
        pairs = build_pairs(12, "full", day_start=5) + build_pairs(12, "light", day_start=5)
        rep = de.ablation_report(pairs, split_at="2026-01-11", horizon=5, min_samples=5)
        text = de.format_evaluation_report(rep)
        self.assertIn("样本量", text)
        self.assertIn("模式对比", text)


# ---------------------------------------------------------------------------
# 5. 多模式适配器：rules / full / intraday 同 schema、可统一评价
# ---------------------------------------------------------------------------
PLAN = {
    "stock_code": "600150",
    "stock_name": "中国船舶",
    "industry": "国防军工",
    "action": "分批布局",
    "confidence": 0.72,
    "entry_conditions": ["回踩20日线", "量能温和放大", "行业景气确认", "第四条应被截断"],
    "exit_conditions": ["跌破前低", "基本面证伪"],
    "review_triggers": ["行业政策变化"],
    "portfolio_snapshot_hash": "snap-abc",
}


class _Position:
    last_price = 32.8


class ModeAdapterTest(unittest.TestCase):
    def test_plan_adapter_maps_upstream_keys(self):
        """键名必须与 nasdx.decision.build_decision_plan 的真实输出一致。"""
        rec = dr.record_from_decision_plan(
            PLAN, reference_price=32.5, data_as_of="2026-01-05", mode="full"
        )
        self.assertEqual(rec.code, "600150")
        self.assertEqual(rec.name, "中国船舶")
        self.assertEqual(rec.industry, "国防军工")
        self.assertEqual(rec.mode, "full")
        self.assertEqual(rec.evaluation_class, dr.CLASS_BUY)
        self.assertEqual(rec.confidence, 0.72)
        self.assertEqual(rec.portfolio_snapshot_hash, "snap-abc")
        # 条件文本只取前三条，避免无界文本落库
        self.assertIn("回踩20日线", rec.target_condition)
        self.assertNotIn("第四条应被截断", rec.target_condition)
        self.assertIn("跌破前低", rec.stop_condition)
        self.assertIn("行业政策变化", rec.invalidation_condition)

    def test_plan_adapter_rejects_non_mapping(self):
        for bad in (None, [], "plan", 3):
            with self.assertRaises(dr.DecisionRecordError):
                dr.record_from_decision_plan(
                    bad, reference_price=10.0, data_as_of="2026-01-05"
                )

    def test_intraday_adapter_accepts_object_and_falls_back_to_position_price(self):
        """IntradayDecision 是 dataclass，不是 dict；没给价就取 position.last_price。"""

        class _Decision:
            decision_id = ""
            generated_at = "2026-01-05T10:05:00+08:00"
            data_as_of = "2026-01-05T10:00:00+08:00"
            code = "600150"
            action = "hold"
            name = "中国船舶"
            industry = "国防军工"
            trigger = "均线多头"
            invalidation = "跌破分时均价"
            snapshot_hash = "snap-abc"
            confidence = 0.6
            position = _Position()

        rec = dr.record_from_intraday_decision(_Decision())
        self.assertEqual(rec.mode, "intraday")
        self.assertEqual(rec.reference_price, 32.8)
        self.assertEqual(rec.evaluation_class, dr.CLASS_HOLD)
        self.assertEqual(rec.llm_calls, 0)
        self.assertEqual(rec.target_condition, "均线多头")
        self.assertEqual(rec.invalidation_condition, "跌破分时均价")

    def test_all_modes_write_the_same_schema(self):
        full = dr.record_from_decision_plan(
            PLAN, reference_price=32.5, data_as_of="2026-01-05", mode="full"
        )
        rules = dr.record_from_decision_plan(
            PLAN, reference_price=32.5, data_as_of="2026-01-05", mode="rules", llm_calls=0
        )
        intraday = dr.record_from_intraday_decision(
            {
                "code": "600150",
                "action": "hold",
                "data_as_of": "2026-01-05T10:00:00+08:00",
                "generated_at": "2026-01-05T10:05:00+08:00",
                "confidence": 0.6,
            },
            reference_price=32.8,
        )
        keys = {tuple(sorted(r.to_dict())) for r in (full, rules, intraday)}
        self.assertEqual(len(keys), 1, "三种模式必须写同一套字段")
        self.assertEqual({full.schema, rules.schema, intraday.schema}, {dr.DECISION_RECORD_SCHEMA})
        # 同一输入的不同模式必须是可区分的独立记录
        self.assertEqual(len({full.decision_id, rules.decision_id, intraday.decision_id}), 3)

    def test_modes_from_one_input_are_jointly_evaluable(self):
        """同一标的、同一 data_as_of，三模式可以进同一张评价表对比。"""
        bars = series(5, [32.5, 33.0, 33.6, 34.2, 34.8, 35.4])
        pairs = []
        for mode, action in (("rules", "分批布局"), ("full", "分批布局"), ("intraday", "hold")):
            if mode == "intraday":
                rec = dr.record_from_intraday_decision(
                    {
                        "code": "600150",
                        "action": action,
                        "data_as_of": "2026-01-05T10:00:00+08:00",
                        "generated_at": "2026-01-05T10:05:00+08:00",
                        "confidence": 0.6,
                    },
                    reference_price=32.5,
                    horizon_trading_days=3,
                )
            else:
                rec = dr.record_from_decision_plan(
                    PLAN,
                    reference_price=32.5,
                    data_as_of="2026-01-05",
                    mode=mode,
                    horizon_trading_days=3,
                )
            pairs.append((rec, ol.compute_forward_labels(rec, bars, policy=POLICY_5)))

        per_mode = de.group_by(pairs, lambda record, _labels: record.mode, horizon=3, min_samples=1)
        self.assertEqual(set(per_mode), {"rules", "full", "intraday"})
        for name, block in per_mode.items():
            self.assertEqual(block["samples"], 1, name)
            self.assertIsNotNone(block["mean_return_pct"], name)
        cmp_ = de.compare_modes(pairs, horizon=3, min_samples=1)
        self.assertEqual(len(cmp_["ranking"]), 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
