# -*- coding: utf-8 -*-
"""#67 盘中驾驶舱：结构化操作契约测试。

覆盖：
1. 动作 schema 完整性（版本化 IntradayDecision、稳定动作枚举与标签）。
2. 手数换算：预算向下取整到整手；不足一手 → 降级为禁止追涨。
3. 动作有效期：valid_until 指向下一检查点或 now+valid_minutes，且为未来时间。
4. 上一轮差异：diff_decisions 正确分类 新增/维持/失效。
5. 休市跳过：should_run 仅在交易日连续竞价检查点窗口返回 True。
6. 陈旧数据降级：硬过期 → 人工复核；陈旧 + 风险增加类 → 等待。
7. 组合限制覆盖单票看多：闸门禁止加仓 → 持有；禁止新开仓 → 禁止追涨。
8. 快照契约：build_intraday_snapshot schema / auto_trading=False / llm_calls=0。
9. UI 接线契约：app.py 接入 cockpit 页面（page 键 + NAV + 分发块）。
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx.intraday_decision import (  # noqa: E402
    ACTION_ADD,
    ACTION_BUY_FIRST_LOT,
    ACTION_HOLD,
    ACTION_LABELS,
    ACTION_NO_CHASE,
    ACTION_REVIEW_REQUIRED,
    ACTION_WAIT,
    ACTIONS,
    DECISION_SCHEMA,
    DEGRADED_ACTIONS,
    FRESHNESS_INVALID,
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    FRESHNESS_VERIFIED,
    IntradayDecision,
    IntradayPolicy,
    PositionView,
    RISK_INCREASING_ACTIONS,
    _lots_for_budget,
    decide,
    diff_decisions,
    evaluate_data_freshness,
    should_run,
)
from nasdx.intraday_copilot import (  # noqa: E402
    SNAPSHOT_SCHEMA,
    build_intraday_snapshot,
    format_intraday_snapshot,
)
from nasdx.evidence import (  # noqa: E402
    CST,
    PRECISION_DATE,
    PRECISION_DATETIME,
    PRECISION_UNKNOWN,
    to_cst,
    to_cst_precise,
)
from nasdx.portfolio_gate import evaluate_portfolio_gate  # noqa: E402
from nasdx.trade_events import resolve_lot_rule  # noqa: E402


def _dt(hour, minute, day=5):
    # 2026-08-05 是周三；返回带 CST 时区的时间戳，便于与 valid_until 比较。
    return datetime(2026, 8, day, hour, minute, 0, tzinfo=CST)


def _verified_evidence():
    return {
        "market": {"as_of": "2026-08-05T10:20:00+08:00", "status": "verified"},
        "sector": {"as_of": "2026-08-05T10:20:00+08:00", "status": "verified"},
        "news": {"as_of": "2026-08-05T10:20:00+08:00", "status": "verified"},
    }


def _position_view(held=True, price=11.62, cost=10.0, pnl_pct=16.2, code="601101"):
    return PositionView(
        code=code,
        name="中国神华",
        asset_class="stock",
        industry="煤炭",
        quantity=100.0 if held else 0.0,
        cost=cost,
        current_price=price,
        market_value=(price * 100.0) if held else None,
        unrealized_pnl=(price - cost) * 100.0 if held else None,
        unrealized_pnl_pct=pnl_pct if held else None,
        weight_pct=5.0 if held else None,
        valuation_status="ok",
    )


def _bullish_gate(code="601101", industry="煤炭", weight_pct=5.0, cash=200000.0):
    snap = {
        "positions": [
            {
                "code": code,
                "name": "中国神华",
                "asset_class": "stock",
                "industry": industry,
                "quantity": 100.0,
                "avg_cost": 10.0,
                "last_price": 11.62,
                "market_value": 1162.0,
                "unrealized_pnl": 162.0,
                "unrealized_pct": 16.2,
                "weight_pct": weight_pct,
                "valuation_status": "ok",
            }
        ],
        "cash": cash,
        "total_assets": 200000.0,
        "exposure_pct": 0.6,
        "portfolio_version": 3,
        "snapshot_hash": "abc123",
        "fail_closed": False,
        "blocking_reasons": [],
    }
    return evaluate_portfolio_gate(code, snap, industry=industry)


_STRONG_INDICATORS = {
    "close": 11.62,
    "ma5": 11.2,
    "ma20": 10.9,
    "ma60": 10.5,
    "boll_upper": 12.1,
    "boll_lower": 10.3,
    "dif": 0.3,
    "dea": 0.2,
    "macd_bar": 0.2,
    "rsi": 62,
    "vol_ratio": 1.4,
    "up_days_20": 12,
}


def _market_payload(code="601101", **stamp):
    """构造行情快照；``stamp`` 用 ``generated_at=`` 或 ``date=`` 指定时间字段。"""
    payload = {
        "market_overview": {"上证指数": {"change_pct": 1.5}},
        "sectors": [
            {
                "name": "煤炭",
                "stocks": [
                    {"code": code, "name": "中国神华", "indicators": dict(_STRONG_INDICATORS)}
                ],
            }
        ],
    }
    payload.update(stamp)
    return payload


def _portfolio_payload(cash=200000.0, weight_pct=5.0):
    return {
        "positions": [
            {
                "code": "601101",
                "name": "中国神华",
                "asset_class": "stock",
                "industry": "煤炭",
                "quantity": 100,
                "avg_cost": 10.0,
                "last_price": 11.62,
                "market_value": 1162.0,
                "unrealized_pnl": 162.0,
                "unrealized_pct": 16.2,
                "weight_pct": weight_pct,
                "valuation_status": "ok",
            }
        ],
        "cash": cash,
        "total_assets": 200000.0,
        "exposure_pct": 0.6,
        "portfolio_version": 3,
        "snapshot_hash": "abc123",
        "fail_closed": False,
        "blocking_reasons": [],
    }


class SchemaActionTest(unittest.TestCase):
    def test_schema_version(self):
        self.assertEqual(DECISION_SCHEMA, "nasdx_intraday_decision.v1")

    def test_every_action_has_label(self):
        for action in ACTIONS:
            self.assertIn(action, ACTION_LABELS)
            self.assertTrue(ACTION_LABELS[action])

    def test_decision_to_dict_has_required_keys(self):
        gate = _bullish_gate()
        dec = decide(
            position=_position_view(),
            signal="bullish",
            confidence=0.86,
            gate=gate,
            data_as_of=_dt(10, 20),
            evidence=_verified_evidence(),
            now=_dt(10, 29),
        )
        payload = dec.to_dict()
        for key in (
            "decision_id",
            "schema",
            "code",
            "action",
            "quantity_delta",
            "amount_delta",
            "executable",
            "trigger",
            "invalidation",
            "valid_until",
            "confidence",
            "risk_level",
            "session",
            "degraded",
            "reasons",
            "blockers",
        ):
            self.assertIn(key, payload, f"缺失字段 {key}")
        self.assertEqual(payload["schema"], DECISION_SCHEMA)
        self.assertEqual(payload["action_label"], ACTION_LABELS[dec.action])
        self.assertTrue(payload["decision_id"].startswith("itd-"))


class LotSizingTest(unittest.TestCase):
    def test_budget_floored_to_lot(self):
        rule = resolve_lot_rule("601101")  # A 股主板 100 股/手
        self.assertEqual(_lots_for_budget(20000.0, 11.62, rule), 1700.0)
        self.assertEqual(_lots_for_budget(32500.0, 1500.0, rule), 0.0)  # 不足一手

    def test_zero_or_negative_budget(self):
        rule = resolve_lot_rule("601101")
        self.assertEqual(_lots_for_budget(0.0, 11.62, rule), 0.0)
        self.assertEqual(_lots_for_budget(-100.0, 11.62, rule), 0.0)


class ValidUntilTest(unittest.TestCase):
    def test_valid_until_is_future_and_points_to_next_checkpoint(self):
        gate = _bullish_gate(cash=200000.0)
        dec = decide(
            position=_position_view(),
            signal="bullish",
            confidence=0.86,
            gate=gate,
            data_as_of=_dt(10, 20),
            evidence=_verified_evidence(),
            now=_dt(10, 29),
        )
        valid = datetime.fromisoformat(dec.valid_until)
        self.assertGreater(valid, _dt(10, 29))
        # 10:29 检查点的下一检查点是 10:59
        self.assertEqual(valid, _dt(10, 59))

    def test_valid_until_falls_back_to_policy_minutes(self):
        # 收盘后（无下一检查点）应回退到 now + valid_minutes
        gate = _bullish_gate(cash=200000.0)
        dec = decide(
            position=_position_view(),
            signal="bullish",
            confidence=0.86,
            gate=gate,
            data_as_of=_dt(14, 50),
            evidence=_verified_evidence(),
            now=_dt(14, 59),
        )
        valid = datetime.fromisoformat(dec.valid_until)
        self.assertGreaterEqual(
            (valid - _dt(14, 59)).total_seconds(), 30 * 60 - 1
        )


class DiffTest(unittest.TestCase):
    def _dec(self, code, action):
        return {"code": code, "action": action}

    def test_new_maintain_expired(self):
        previous = [self._dec("601101", ACTION_HOLD)]
        current = [
            self._dec("601101", ACTION_HOLD),  # maintain
            self._dec("600519", ACTION_ADD),  # new
        ]
        diff = diff_decisions(previous, current)
        by_code = {row["code"]: row["kind"] for row in diff}
        self.assertEqual(by_code["601101"], "maintain")
        self.assertEqual(by_code["600519"], "new")
        # 上一轮有、本轮无 → expired
        expired = [row for row in diff if row["kind"] == "expired"]
        self.assertFalse(expired)  # 本轮未缺任何上一轮 code

        diff2 = diff_decisions(previous, [])
        self.assertEqual(diff2[0]["code"], "601101")
        self.assertEqual(diff2[0]["kind"], "expired")


class MarketScheduleTest(unittest.TestCase):
    def test_checkpoint_window_allowed(self):
        allowed, _ = should_run(_dt(10, 29))
        self.assertTrue(allowed)

    def test_off_checkpoint_skipped(self):
        allowed, reason = should_run(_dt(10, 5))
        self.assertFalse(allowed)
        self.assertIn("检查点", reason)

    def test_weekend_skipped(self):
        # 2026-08-08 是周六
        allowed, _ = should_run(datetime(2026, 8, 8, 10, 29, 0, tzinfo=CST))
        self.assertFalse(allowed)

    def test_lunch_break_skipped(self):
        allowed, _ = should_run(_dt(12, 0))
        self.assertFalse(allowed)


class StaleDataTest(unittest.TestCase):
    def test_hard_stale_review_required(self):
        gate = _bullish_gate()
        # data_as_of 比 now 早 2 小时（> hard_stale 1 小时）
        dec = decide(
            position=_position_view(),
            signal="bullish",
            confidence=0.86,
            gate=gate,
            data_as_of=_dt(8, 29),
            evidence=_verified_evidence(),
            now=_dt(10, 29),
        )
        self.assertEqual(dec.action, ACTION_REVIEW_REQUIRED)
        self.assertTrue(dec.degraded)

    def test_stale_risk_increasing_downgraded_to_wait(self):
        gate = _bullish_gate()
        dec = decide(
            position=_position_view(),
            signal="bullish",
            confidence=0.86,
            gate=gate,
            data_as_of=_dt(10, 5),  # 早 24 分钟：超过 stale(15min) 但不到 hard_stale(60min)
            evidence={
                "market": {"status": "verified"},
                "sector": {"status": "verified"},
                "news": {"status": "verified"},
            },
            now=_dt(10, 29),
        )
        self.assertEqual(dec.action, ACTION_WAIT)
        self.assertTrue(dec.degraded)


class DatePrecisionContractTest(unittest.TestCase):
    """Issue #84：``YYYY-MM-DD`` 与 ``YYYYMMDD`` 必须语义一致且有明确精度。"""

    def test_all_date_only_formats_normalize_to_midnight(self):
        expected = datetime(2026, 8, 5, 0, 0, 0, tzinfo=CST)
        for raw in ("2026-08-05", "20260805", "2026/08/05"):
            stamp, precision = to_cst_precise(raw)
            self.assertEqual(stamp, expected, raw)
            self.assertEqual(precision, PRECISION_DATE, raw)

    def test_datetime_inputs_keep_datetime_precision(self):
        stamp, precision = to_cst_precise("2026-08-05 10:20:00")
        self.assertEqual(stamp, _dt(10, 20))
        self.assertEqual(precision, PRECISION_DATETIME)

    def test_date_only_never_guesses_market_close(self):
        # 旧实现会把 8 位日期猜成收盘 15:00：上午运行时凭空造出未来时间戳。
        stamp, _ = to_cst_precise("20260805")
        self.assertEqual(stamp.hour, 0)
        self.assertEqual(stamp.minute, 0)

    def test_unparseable_input_is_unknown(self):
        stamp, precision = to_cst_precise("not-a-time")
        self.assertIsNone(stamp)
        self.assertEqual(precision, PRECISION_UNKNOWN)

    def test_to_cst_backward_compatible(self):
        self.assertEqual(to_cst("2026-08-05 10:20:00"), _dt(10, 20))
        self.assertIsNone(to_cst(None))


class DataFreshnessContractTest(unittest.TestCase):
    """Issue #84：未来时间戳不得被判定为"已核验"，负年龄不得冒充"更新鲜"。"""

    def test_fresh_datetime_is_verified(self):
        fresh = evaluate_data_freshness(_dt(10, 25), _dt(10, 29))
        self.assertEqual(fresh.status, FRESHNESS_VERIFIED)
        self.assertTrue(fresh.usable)
        self.assertFalse(fresh.future)
        self.assertEqual(fresh.clock_skew_seconds, 0.0)

    def test_minor_clock_skew_within_tolerance_is_verified(self):
        # 领先 30 秒属正常抖动（默认允许 60 秒），不应误杀。
        fresh = evaluate_data_freshness(_dt(10, 29) + timedelta(seconds=30), _dt(10, 29))
        self.assertEqual(fresh.status, FRESHNESS_VERIFIED)
        self.assertTrue(fresh.usable)
        self.assertFalse(fresh.future)
        self.assertAlmostEqual(fresh.clock_skew_seconds, 30.0)

    def test_future_timestamp_is_invalid_not_fresh(self):
        fresh = evaluate_data_freshness(_dt(10, 59), _dt(10, 29))
        self.assertEqual(fresh.status, FRESHNESS_INVALID)
        self.assertTrue(fresh.future)
        self.assertFalse(fresh.usable)
        self.assertTrue(fresh.stale)
        self.assertLess(fresh.age_seconds, 0)
        self.assertAlmostEqual(fresh.clock_skew_seconds, 1800.0)

    def test_negative_age_never_counts_as_fresher(self):
        # 越"未来"越不可信，绝不会因为 age 更小而更容易通过闸门。
        for minutes in (2, 30, 240, 1440):
            fresh = evaluate_data_freshness(
                _dt(10, 29) + timedelta(minutes=minutes), _dt(10, 29)
            )
            self.assertFalse(fresh.usable, minutes)
            self.assertTrue(fresh.future, minutes)

    def test_wrong_timezone_input_is_rejected(self):
        # 把 CST 时刻误标成 UTC → 实际等于未来 8 小时。
        mislabelled = datetime(2026, 8, 5, 10, 20, 0, tzinfo=timezone.utc)
        fresh = evaluate_data_freshness(mislabelled, _dt(10, 29))
        self.assertEqual(fresh.status, FRESHNESS_INVALID)
        self.assertTrue(fresh.future)

    def test_date_only_is_stale_in_morning_session(self):
        fresh = evaluate_data_freshness("20260805", _dt(9, 35))
        self.assertEqual(fresh.status, FRESHNESS_STALE)
        self.assertTrue(fresh.date_only)
        self.assertFalse(fresh.future)
        self.assertFalse(fresh.usable)
        self.assertEqual(fresh.precision, PRECISION_DATE)

    def test_date_only_is_stale_after_close(self):
        fresh = evaluate_data_freshness("2026-08-05", _dt(15, 30))
        self.assertEqual(fresh.status, FRESHNESS_STALE)
        self.assertTrue(fresh.date_only)
        self.assertFalse(fresh.usable)

    def test_missing_stamp_is_unknown(self):
        fresh = evaluate_data_freshness(None, _dt(10, 29))
        self.assertEqual(fresh.status, FRESHNESS_UNKNOWN)
        self.assertFalse(fresh.usable)
        self.assertIsNone(fresh.age_seconds)

    def test_custom_skew_tolerance_is_honoured(self):
        policy = IntradayPolicy(max_future_skew_seconds=0.0)
        fresh = evaluate_data_freshness(
            _dt(10, 29) + timedelta(seconds=30), _dt(10, 29), policy
        )
        self.assertEqual(fresh.status, FRESHNESS_INVALID)

    def test_to_dict_exposes_diagnostics(self):
        payload = evaluate_data_freshness(_dt(10, 59), _dt(10, 29)).to_dict()
        for key in (
            "data_age_seconds",
            "clock_skew_seconds",
            "future_timestamp",
            "status",
            "data_precision",
            "reason",
        ):
            self.assertIn(key, payload)
        self.assertTrue(payload["future_timestamp"])


class FutureTimestampDecisionTest(unittest.TestCase):
    """Issue #84：未来 / 纯日期时间戳下不得输出可执行的风险增加类动作。"""

    def _decide(self, data_as_of, now=None, **kwargs):
        return decide(
            position=_position_view(),
            signal="bullish",
            confidence=0.86,
            gate=_bullish_gate(),
            data_as_of=data_as_of,
            evidence=_verified_evidence(),
            now=now or _dt(10, 29),
            **kwargs,
        )

    def test_baseline_fresh_data_still_actionable(self):
        # 回归护栏：正常路径不能被这次修复误伤。
        dec = self._decide(_dt(10, 20))
        self.assertIn(dec.action, RISK_INCREASING_ACTIONS)
        self.assertTrue(dec.executable)

    def test_minor_skew_still_actionable(self):
        dec = self._decide(_dt(10, 29) + timedelta(seconds=30))
        self.assertIn(dec.action, RISK_INCREASING_ACTIONS)
        self.assertTrue(dec.executable)

    def test_future_timestamp_degrades_to_review_required(self):
        dec = self._decide(_dt(11, 59))
        self.assertEqual(dec.action, ACTION_REVIEW_REQUIRED)
        self.assertIn(dec.action, DEGRADED_ACTIONS)
        self.assertFalse(dec.executable)
        self.assertEqual(dec.quantity_delta, 0.0)
        self.assertTrue(any("未来" in reason for reason in dec.blockers))

    def test_far_future_timestamp_degrades(self):
        dec = self._decide(_dt(10, 29, day=6))
        self.assertEqual(dec.action, ACTION_REVIEW_REQUIRED)
        self.assertFalse(dec.executable)

    def test_date_only_precision_blocks_risk_increasing(self):
        dec = self._decide("20260805", now=_dt(9, 35))
        self.assertIn(dec.action, DEGRADED_ACTIONS)
        self.assertFalse(dec.executable)

    def test_decision_evidence_records_freshness_fields(self):
        dec = self._decide(_dt(11, 59))
        as_of = dec.to_dict()["evidence_as_of"]
        self.assertTrue(as_of["future_timestamp"])
        self.assertEqual(as_of["freshness_status"], FRESHNESS_INVALID)
        self.assertGreater(as_of["clock_skew_seconds"], 0)
        self.assertLess(as_of["data_age_seconds"], 0)


class SnapshotFreshnessContractTest(unittest.TestCase):
    """Issue #84：``build_intraday_snapshot`` 与 :func:`decide` 的新鲜度判定必须一致。"""

    def _snapshot(self, market, now=_dt(10, 29)):
        return build_intraday_snapshot(
            now=now,
            portfolio=_portfolio_payload(),
            data=market,
            use_ledger=False,
            news_status="verified",
        )

    def test_fresh_snapshot_is_verified(self):
        snap = self._snapshot(_market_payload(generated_at="2026-08-05T10:20:00"))
        evidence = snap["evidence"]
        self.assertFalse(evidence["market_stale"])
        self.assertEqual(evidence["freshness_status"], FRESHNESS_VERIFIED)
        self.assertFalse(evidence["future_timestamp"])
        self.assertEqual(evidence["market"]["status"], "verified")
        self.assertEqual(snap["decisions"][0]["action"], ACTION_ADD)

    def test_future_timestamp_not_marked_verified(self):
        snap = self._snapshot(_market_payload(generated_at="2026-08-05T10:59:00"))
        evidence = snap["evidence"]
        self.assertTrue(evidence["market_stale"])
        self.assertTrue(evidence["future_timestamp"])
        self.assertEqual(evidence["freshness_status"], FRESHNESS_INVALID)
        self.assertNotEqual(evidence["market"]["status"], "verified")
        self.assertNotEqual(evidence["sector"]["status"], "verified")
        self.assertLess(evidence["data_age_seconds"], 0)
        self.assertGreater(evidence["clock_skew_seconds"], 0)

    def test_future_timestamp_blocks_risk_increasing_actions(self):
        snap = self._snapshot(_market_payload(generated_at="2026-08-05T10:59:00"))
        for row in snap["decisions"] + snap["candidates"]:
            self.assertNotIn(row["action"], RISK_INCREASING_ACTIONS, row)
            self.assertFalse(row["executable"], row)

    def test_date_only_stamp_in_morning_is_stale(self):
        # 旧实现把 20260805 猜成 15:00，上午运行时反而"更新鲜" → 绕过闸门。
        snap = self._snapshot(_market_payload(date="20260805"), now=_dt(9, 35))
        evidence = snap["evidence"]
        self.assertTrue(evidence["market_stale"])
        self.assertEqual(evidence["data_precision"], PRECISION_DATE)
        self.assertEqual(evidence["freshness_status"], FRESHNESS_STALE)
        self.assertFalse(evidence["future_timestamp"])
        self.assertGreater(evidence["data_age_seconds"], 0)

    def test_date_only_stamp_after_close_is_stale(self):
        snap = self._snapshot(_market_payload(date="2026-08-05"), now=_dt(15, 30))
        self.assertTrue(snap["evidence"]["market_stale"])
        self.assertEqual(snap["evidence"]["freshness_status"], FRESHNESS_STALE)

    def test_hyphen_and_compact_dates_behave_identically(self):
        hyphen = self._snapshot(_market_payload(date="2026-08-05"), now=_dt(9, 35))
        compact = self._snapshot(_market_payload(date="20260805"), now=_dt(9, 35))
        self.assertEqual(hyphen["data_as_of"], compact["data_as_of"])
        self.assertEqual(
            hyphen["evidence"]["freshness_status"], compact["evidence"]["freshness_status"]
        )
        self.assertEqual(
            hyphen["evidence"]["data_age_seconds"], compact["evidence"]["data_age_seconds"]
        )

    def test_missing_stamp_is_unknown_and_stale(self):
        snap = self._snapshot(_market_payload())
        self.assertTrue(snap["evidence"]["market_stale"])
        self.assertEqual(snap["evidence"]["freshness_status"], FRESHNESS_UNKNOWN)
        self.assertEqual(snap["data_as_of"], "")

    def test_market_state_unknown_when_timestamp_untrusted(self):
        snap = self._snapshot(_market_payload(generated_at="2026-08-05T10:59:00"))
        self.assertEqual(snap["portfolio"]["market_state"], "数据不足")

    def test_future_timestamp_emits_auditable_blocker(self):
        snap = self._snapshot(_market_payload(generated_at="2026-08-05T10:59:00"))
        self.assertTrue(snap["evidence"]["blockers"])
        self.assertIn("未来", snap["evidence"]["freshness_reason"])
        self.assertTrue(any("未来" in note for note in snap["notes"]))

    def test_blocker_present_even_without_positions(self):
        # 空持仓时没有任何 decision，快照层仍必须留下可审计的告警。
        empty = {
            "positions": [],
            "cash": 100000.0,
            "total_assets": 100000.0,
            "exposure_pct": 0.0,
            "portfolio_version": 1,
            "snapshot_hash": "demo",
            "fail_closed": False,
            "blocking_reasons": [],
        }
        snap = build_intraday_snapshot(
            now=_dt(10, 29),
            portfolio=empty,
            data=_market_payload(generated_at="2026-08-05T10:59:00"),
            use_ledger=False,
            news_status="verified",
        )
        self.assertEqual(snap["decisions"], [])
        self.assertTrue(snap["evidence"]["blockers"])
        self.assertTrue(snap["evidence"]["future_timestamp"])
        self.assertTrue(any("未来" in note for note in snap["notes"]))

    def test_fresh_snapshot_has_no_freshness_blocker(self):
        snap = self._snapshot(_market_payload(generated_at="2026-08-05T10:20:00"))
        self.assertEqual(snap["evidence"]["blockers"], [])
        self.assertEqual(len(snap["notes"]), 1)

    def test_rendered_report_surfaces_freshness_warning(self):
        stale = format_intraday_snapshot(
            self._snapshot(_market_payload(generated_at="2026-08-05T10:59:00"))
        )
        self.assertIn("数据时间告警", stale)
        fresh = format_intraday_snapshot(
            self._snapshot(_market_payload(generated_at="2026-08-05T10:20:00"))
        )
        self.assertNotIn("数据时间告警", fresh)


class PortfolioGateOverrideTest(unittest.TestCase):
    def test_gate_blocks_add_for_held(self):
        # 持仓已达单票上限 → 闸门禁止加仓
        gate = _bullish_gate(weight_pct=10.0)
        self.assertFalse(gate.allow_add)
        dec = decide(
            position=_position_view(),
            signal="bullish",
            confidence=0.86,
            gate=gate,
            data_as_of=_dt(10, 20),
            evidence=_verified_evidence(),
            now=_dt(10, 29),
        )
        self.assertEqual(dec.action, ACTION_HOLD)  # 持有而非加仓

    def test_gate_blocks_new_entry_for_unheld(self):
        # 现金为 0 → 闸门禁止新开仓；未持仓候选降级为禁止追涨
        gate = _bullish_gate(cash=0.0)
        self.assertFalse(gate.allow_new_entry)
        dec = decide(
            position=_position_view(held=False),
            signal="bullish",
            confidence=0.86,
            gate=gate,
            data_as_of=_dt(10, 20),
            evidence=_verified_evidence(),
            now=_dt(10, 29),
        )
        self.assertEqual(dec.action, ACTION_NO_CHASE)
        self.assertFalse(dec.executable)


class SnapshotContractTest(unittest.TestCase):
    def _market(self, code="601101"):
        strong = {
            "close": 11.62,
            "ma5": 11.2,
            "ma20": 10.9,
            "ma60": 10.5,
            "boll_upper": 12.1,
            "boll_lower": 10.3,
            "dif": 0.3,
            "dea": 0.2,
            "macd_bar": 0.2,
            "rsi": 62,
            "vol_ratio": 1.4,
            "up_days_20": 12,
        }
        return {
            "generated_at": "2026-08-05T10:20:00",
            "market_overview": {"上证指数": {"change_pct": 1.5}},
            "sectors": [
                {"name": "煤炭", "stocks": [{"code": code, "name": "中国神华", "indicators": strong}]}
            ],
        }

    def _portfolio(self, cash=200000.0, weight_pct=5.0):
        return {
            "positions": [
                {
                    "code": "601101",
                    "name": "中国神华",
                    "asset_class": "stock",
                    "industry": "煤炭",
                    "quantity": 100,
                    "avg_cost": 10.0,
                    "last_price": 11.62,
                    "market_value": 1162.0,
                    "unrealized_pnl": 162.0,
                    "unrealized_pct": 16.2,
                    "weight_pct": weight_pct,
                    "valuation_status": "ok",
                }
            ],
            "cash": cash,
            "total_assets": 200000.0,
            "exposure_pct": 0.6,
            "portfolio_version": 3,
            "snapshot_hash": "abc123",
            "fail_closed": False,
            "blocking_reasons": [],
        }

    def test_snapshot_schema_and_zero_llm(self):
        snap = build_intraday_snapshot(
            now=_dt(10, 29),
            portfolio=self._portfolio(),
            data=self._market(),
            use_ledger=False,
            news_status="verified",
        )
        self.assertEqual(snap["schema"], SNAPSHOT_SCHEMA)
        self.assertFalse(snap["auto_trading"])
        self.assertEqual(snap["performance"]["llm_calls"], 0)
        self.assertEqual(len(snap["decisions"]), 1)
        # 强多头 + 现金充足 → 可执行加仓
        dec = snap["decisions"][0]
        self.assertEqual(dec["action"], ACTION_ADD)
        self.assertTrue(dec["executable"])
        self.assertGreater(dec["quantity_delta"], 0)

    def test_snapshot_no_auto_trading_clause(self):
        snap = build_intraday_snapshot(
            now=_dt(10, 29), portfolio=self._portfolio(), data=self._market(), use_ledger=False
        )
        self.assertIn("系统不会自动下单", snap["notes"][0])


class AppWiringContractTest(unittest.TestCase):
    def _app_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
        )
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_cockpit_wired_in_app(self):
        src = self._app_source()
        self.assertIn('"cockpit"', src, "cockpit 未在 _valid_pages 中注册")
        self.assertIn('("cockpit"', src, "cockpit 未加入 NAV 导航")
        self.assertIn('elif pg == "cockpit":', src, "app.py 缺少 cockpit 分发块")


if __name__ == "__main__":
    unittest.main()
