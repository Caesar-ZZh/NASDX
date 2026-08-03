# -*- coding: utf-8 -*-
"""#66 验收点 7：组合优先闸门 + 决策/仓位换算接线契约测试。

覆盖：
1. 未接入组合快照时，闸门 unknown，决策与仓位换算行为与 #66 之前完全一致。
2. fail-closed 账本必须阻断确定性"买入/加仓"，但不能阻断减仓/清仓。
3. 单票权重上限：达到上限禁止加仓；未达上限时仓位区间被剩余空间裁剪。
4. 行业集中度上限达到时禁止新增同类标的。
5. 现金为 0/负 或满仓时禁止新增。
6. 持仓缺价格（无法估值）时禁止加仓。
7. 确定性：同一 snapshot 反复评估结果一致；snapshot_hash / portfolio_version 透传。
8. build_position_sizing 接入快照后，敞口与现金以账本为准，fail-closed 时新增金额全部归零。
9. 真实账本端到端：portfolio_store.build_snapshot -> 闸门 -> 决策方案。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx import portfolio_store as ps  # noqa: E402
from nasdx import position_sizing as psz  # noqa: E402
from nasdx.decision import build_decision_plan, format_decision_plan  # noqa: E402
from nasdx.portfolio_gate import (  # noqa: E402
    STATUS_BLOCKED,
    STATUS_NORMAL,
    STATUS_NO_ADD,
    STATUS_UNKNOWN,
    evaluate_portfolio_gate,
    format_portfolio_gate,
)
from nasdx.schema import AnalysisResult  # noqa: E402


BUY_ACTIONS = ("分批布局", "轻仓试错")
REDUCE_ACTION = "回避或减仓"


def _result(dimension, signal, confidence=0.7):
    return AnalysisResult(
        agent_name=f"{dimension}_agent",
        dimension=dimension,
        conclusion=f"{dimension} 结论",
        signal=signal,
        confidence=confidence,
        key_points=[f"{dimension} 要点"],
    )


def _bullish_inputs():
    research = {
        "technical": _result("technical", "bullish", 0.8),
        "fund_flow": _result("fund_flow", "bullish", 0.75),
        "sector": _result("sector", "bullish", 0.7),
        "risk": _result("risk", "neutral", 0.6),
        "chokepoint": _result("chokepoint", "bullish", 0.65),
    }
    synthesis = _result("synthesis", "bullish", 0.8)
    return research, synthesis


def _bearish_inputs():
    research = {
        "technical": _result("technical", "bearish", 0.8),
        "fund_flow": _result("fund_flow", "bearish", 0.75),
        "sector": _result("sector", "bearish", 0.7),
        "risk": _result("risk", "bearish", 0.8),
        "chokepoint": _result("chokepoint", "bearish", 0.6),
    }
    synthesis = _result("synthesis", "bearish", 0.8)
    return research, synthesis


def _plan(portfolio=None, industry=None, bullish=True, bullish_pct=82.0):
    research, synthesis = _bullish_inputs() if bullish else _bearish_inputs()
    return build_decision_plan(
        stock_code="601101",
        stock_name="昊华能源",
        final_signal="bullish" if bullish else "bearish",
        bullish_pct=bullish_pct if bullish else 20.0,
        research_results=research,
        synthesis=synthesis,
        risk_profile="balanced",
        portfolio=portfolio,
        industry=industry,
    )


def _snapshot(
    positions=None,
    cash=50000.0,
    total_assets=100000.0,
    fail_closed=False,
    blocking_reasons=None,
    industry_exposure=None,
    exposure_pct=None,
    policy=None,
    snapshot_hash="hash-a",
    portfolio_version=3,
    asset_class_exposure=None,
):
    """Minimal snapshot mapping shaped like PortfolioSnapshot.to_dict()."""
    rows = positions if positions is not None else []
    market_value = sum(float(row.get("market_value") or 0.0) for row in rows)
    if exposure_pct is None and total_assets:
        exposure_pct = round(market_value / total_assets * 100, 4)
    return {
        "schema": "nasdx_portfolio_snapshot.v1",
        "generated_at": "2026-08-03T20:00:00",
        "portfolio_version": portfolio_version,
        "ledger_hash": "ledger-a",
        "snapshot_hash": snapshot_hash,
        "event_count": len(rows),
        "active_event_count": len(rows),
        "cash": cash,
        "cash_status": "known" if cash is not None else "unknown",
        "cash_baseline": cash,
        "total_market_value": market_value,
        "total_cost_basis": market_value,
        "total_assets": total_assets,
        "gross_exposure": market_value,
        "exposure_pct": exposure_pct,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "positions": rows,
        "closed_positions": [],
        "asset_class_exposure": asset_class_exposure or {},
        "industry_exposure": industry_exposure or {},
        "policy": policy or {"single_name_cap_pct": 10.0, "industry_cap_pct": 30.0},
        "fail_closed": fail_closed,
        "blocking_reasons": blocking_reasons or [],
        "warnings": [],
    }


def _position(
    code="601101",
    weight_pct=4.0,
    market_value=4000.0,
    quantity=1000.0,
    industry="煤炭",
    last_price=4.0,
    valuation_status="priced",
):
    return {
        "code": code,
        "name": "昊华能源",
        "asset_class": "股票",
        "industry": industry,
        "quantity": quantity,
        "avg_cost": 3.5,
        "cost_basis": quantity * 3.5,
        "realized_pnl": 0.0,
        "last_price": last_price,
        "price_as_of": "2026-08-03",
        "market_value": market_value,
        "unrealized_pnl": 200.0,
        "unrealized_pct": 5.0,
        "weight_pct": weight_pct,
        "valuation_status": valuation_status,
        "last_event_at": "2026-07-01 09:35:00",
    }


# ---------------------------------------------------------------------------
# 1. 未接线时保持旧行为
# ---------------------------------------------------------------------------


class TestBackwardCompatibility(unittest.TestCase):
    def test_gate_is_unknown_without_portfolio(self):
        gate = evaluate_portfolio_gate("601101")
        self.assertEqual(gate.status, STATUS_UNKNOWN)
        self.assertFalse(gate.is_enabled)
        self.assertTrue(gate.allow_new_entry and gate.allow_add and gate.allow_reduce)
        self.assertIn("未接入组合快照", format_portfolio_gate(gate))

    def test_decision_plan_without_portfolio_matches_pre_66_behaviour(self):
        plan = _plan(portfolio=None)
        self.assertIn(plan["action"], BUY_ACTIONS)
        self.assertEqual(plan["position_band"], "20%-35%")
        self.assertFalse(plan["portfolio_linked"])
        self.assertIsNone(plan["max_new_position_pct"])
        self.assertEqual(plan["portfolio_snapshot_hash"], "")
        self.assertNotIn("组合状态：", format_decision_plan(plan))

    def test_plan_keeps_all_legacy_keys(self):
        plan = _plan(portfolio=None)
        for key in (
            "stock_code",
            "direction",
            "action",
            "position_band",
            "horizon",
            "risk_profile",
            "entry_conditions",
            "exit_conditions",
            "review_triggers",
            "risk_flags",
            "evidence",
            "note",
        ):
            self.assertIn(key, plan)


# ---------------------------------------------------------------------------
# 2. fail-closed 阻断买入，不阻断减仓
# ---------------------------------------------------------------------------


class TestFailClosedBlocksNewMoney(unittest.TestCase):
    def setUp(self):
        self.snapshot = _snapshot(
            positions=[_position()],
            fail_closed=True,
            blocking_reasons=["601101 缺少最新价格"],
        )

    def test_gate_blocks_add_and_entry(self):
        gate = evaluate_portfolio_gate("601101", portfolio=self.snapshot)
        self.assertEqual(gate.status, STATUS_BLOCKED)
        self.assertTrue(gate.fail_closed)
        self.assertFalse(gate.allow_add)
        self.assertFalse(gate.allow_new_entry)
        self.assertTrue(gate.allow_reduce)
        self.assertEqual(gate.max_new_position_pct, 0.0)
        self.assertTrue(any("缺少最新价格" in item for item in gate.reasons))

    def test_bullish_plan_is_downgraded_to_no_buy(self):
        plan = _plan(portfolio=self.snapshot)
        self.assertNotIn(plan["action"], BUY_ACTIONS)
        self.assertEqual(plan["position_band"], "0%-0%")
        self.assertEqual(plan["max_new_position_pct"], 0.0)
        self.assertFalse(plan["allow_new_position"])
        self.assertTrue(any("fail-closed" in flag for flag in plan["risk_flags"]))

    def test_reduce_action_survives_fail_closed(self):
        plan = _plan(portfolio=self.snapshot, bullish=False)
        self.assertEqual(plan["action"], REDUCE_ACTION)
        self.assertFalse(plan["allow_new_position"])

    def test_untracked_code_is_also_blocked(self):
        plan = build_decision_plan(
            stock_code="600519",
            stock_name="贵州茅台",
            final_signal="bullish",
            bullish_pct=90.0,
            research_results=_bullish_inputs()[0],
            synthesis=_bullish_inputs()[1],
            portfolio=self.snapshot,
        )
        self.assertNotIn(plan["action"], BUY_ACTIONS)
        self.assertFalse(plan["allow_new_position"])

    def test_snapshot_object_and_mapping_agree(self):
        broken = ps.PortfolioSnapshot(**self.snapshot)
        from_object = evaluate_portfolio_gate("601101", portfolio=broken)
        from_mapping = evaluate_portfolio_gate("601101", portfolio=self.snapshot)
        self.assertEqual(from_object.to_dict(), from_mapping.to_dict())

    def test_invalid_portfolio_type_is_rejected(self):
        with self.assertRaises(TypeError):
            evaluate_portfolio_gate("601101", portfolio="not-a-snapshot")


# ---------------------------------------------------------------------------
# 3~6. 组合层硬约束
# ---------------------------------------------------------------------------


class TestPortfolioCaps(unittest.TestCase):
    def test_single_name_cap_reached_blocks_add(self):
        snap = _snapshot(
            positions=[_position(weight_pct=10.5, market_value=10500.0)],
            industry_exposure={"煤炭": 10500.0},
        )
        gate = evaluate_portfolio_gate("601101", portfolio=snap)
        self.assertEqual(gate.status, STATUS_NO_ADD)
        self.assertFalse(gate.allow_add)
        self.assertTrue(gate.allow_reduce)
        self.assertEqual(gate.max_new_position_pct, 0.0)

        plan = _plan(portfolio=snap)
        self.assertEqual(plan["action"], "维持持仓，不加仓")
        self.assertEqual(plan["position_band"], "0%-0%")

    def test_headroom_clamps_position_band(self):
        snap = _snapshot(
            positions=[_position(weight_pct=6.0, market_value=6000.0)],
            industry_exposure={"煤炭": 6000.0},
        )
        gate = evaluate_portfolio_gate("601101", portfolio=snap)
        self.assertEqual(gate.status, STATUS_NORMAL)
        self.assertAlmostEqual(gate.max_new_position_pct, 4.0)

        plan = _plan(portfolio=snap)
        self.assertIn(plan["action"], BUY_ACTIONS)
        # 原区间 20%-35%，被剩余 4% 裁剪
        self.assertEqual(plan["position_band"], "4%-4%")
        self.assertTrue(plan["allow_new_position"])
        self.assertAlmostEqual(plan["max_new_position_pct"], 4.0)

    def test_band_untouched_when_headroom_is_larger(self):
        snap = _snapshot(
            positions=[],
            policy={"single_name_cap_pct": 60.0, "industry_cap_pct": 80.0},
        )
        plan = _plan(portfolio=snap)
        self.assertEqual(plan["position_band"], "20%-35%")

    def test_industry_cap_blocks_new_entry(self):
        snap = _snapshot(
            positions=[_position(code="601088", weight_pct=9.0, market_value=9000.0)],
            industry_exposure={"煤炭": 31000.0},
            total_assets=100000.0,
        )
        gate = evaluate_portfolio_gate("601101", portfolio=snap, industry="煤炭")
        self.assertFalse(gate.allow_new_entry)
        self.assertTrue(any("行业敞口" in item for item in gate.reasons))

        plan = _plan(portfolio=snap, industry="煤炭")
        self.assertEqual(plan["action"], "观察等待")
        self.assertFalse(plan["allow_new_position"])

    def test_unclassified_industry_never_triggers_industry_cap(self):
        snap = _snapshot(positions=[], industry_exposure={"煤炭": 99000.0})
        gate = evaluate_portfolio_gate("601101", portfolio=snap)
        self.assertTrue(gate.allow_new_entry)
        self.assertIsNone(gate.context["industry_weight_pct"])

    def test_zero_cash_blocks_new_money(self):
        snap = _snapshot(positions=[], cash=0.0)
        gate = evaluate_portfolio_gate("601101", portfolio=snap)
        self.assertFalse(gate.allow_add)
        self.assertTrue(any("现金" in item for item in gate.reasons))

    def test_negative_cash_blocks_new_money(self):
        snap = _snapshot(positions=[], cash=-1200.0)
        gate = evaluate_portfolio_gate("601101", portfolio=snap)
        self.assertFalse(gate.allow_new_entry)

    def test_full_exposure_blocks_new_money(self):
        snap = _snapshot(
            positions=[_position(weight_pct=9.0, market_value=100000.0)],
            total_assets=100000.0,
            cash=5000.0,
        )
        gate = evaluate_portfolio_gate("601101", portfolio=snap)
        self.assertFalse(gate.allow_add)
        self.assertTrue(any("满仓" in item for item in gate.reasons))

    def test_unpriced_holding_blocks_add_but_allows_reduce(self):
        snap = _snapshot(
            positions=[
                _position(
                    weight_pct=None,
                    market_value=0.0,
                    last_price=None,
                    valuation_status="missing_price",
                )
            ],
        )
        gate = evaluate_portfolio_gate("601101", portfolio=snap)
        self.assertFalse(gate.allow_add)
        self.assertTrue(gate.allow_reduce)
        self.assertTrue(any("无法估值" in item for item in gate.reasons))

        plan = _plan(portfolio=snap, bullish=False)
        self.assertEqual(plan["action"], REDUCE_ACTION)


# ---------------------------------------------------------------------------
# 7. 确定性与透传
# ---------------------------------------------------------------------------


class TestDeterminismAndPropagation(unittest.TestCase):
    def test_same_snapshot_yields_identical_gate(self):
        snap = _snapshot(positions=[_position()], industry_exposure={"煤炭": 4000.0})
        first = evaluate_portfolio_gate("601101", portfolio=snap)
        second = evaluate_portfolio_gate("601101", portfolio=dict(snap))
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_snapshot_hash_and_version_are_propagated(self):
        snap = _snapshot(
            positions=[_position()],
            snapshot_hash="abc123",
            portfolio_version=17,
        )
        plan = _plan(portfolio=snap)
        self.assertEqual(plan["portfolio_snapshot_hash"], "abc123")
        self.assertEqual(plan["portfolio_version"], 17)
        self.assertTrue(plan["portfolio_linked"])
        self.assertEqual(plan["portfolio_gate"]["schema"], "nasdx_portfolio_gate.v1")

    def test_report_line_shows_holding_and_gate(self):
        snap = _snapshot(positions=[_position()], industry_exposure={"煤炭": 4000.0})
        text = format_decision_plan(_plan(portfolio=snap))
        self.assertIn("组合状态：", text)
        self.assertIn("持有 1000 股", text)
        self.assertIn("闸门", text)

    def test_code_matching_is_case_and_space_insensitive(self):
        snap = _snapshot(positions=[_position(code=" 601101 ")])
        gate = evaluate_portfolio_gate("601101", portfolio=snap)
        self.assertTrue(gate.held)


# ---------------------------------------------------------------------------
# 8. build_position_sizing 接线
# ---------------------------------------------------------------------------


BRIEF = {
    "generated_at": "2026-08-03T19:00:00",
    "risk_profile": "balanced",
    "risk_profile_label": "均衡",
    "action_gate": "normal",
    "posture": "试错",
    "allocation": {
        "max_total": "40%-60%",
        "etf_budget": "10%-20%",
        "stock_budget": "20%-40%",
        "single_stock_cap": "0%-10%",
        "cash_buffer": "10%-20%",
    },
    "candidate_audits": [
        {
            "code": "601101",
            "name": "昊华能源",
            "candidate": "601101 昊华能源",
            "type": "股票",
            "audit_status": "小仓试错候选",
            "status_code": "trial_candidate",
            "deep_signal": "bullish",
        },
        {
            "code": "510300",
            "name": "沪深300ETF",
            "candidate": "510300 沪深300ETF",
            "type": "ETF",
            "audit_status": "小仓试错候选",
            "status_code": "trial_candidate",
            "deep_signal": "bullish",
        },
    ],
}


class TestPositionSizingPortfolioWiring(unittest.TestCase):
    def test_legacy_manual_mode_unchanged(self):
        sizing = psz.build_position_sizing(
            BRIEF, total_capital=100000.0, current_stock_exposure=10000.0
        )
        self.assertFalse(sizing["portfolio_linked"])
        self.assertTrue(sizing["allow_new_position"])
        self.assertEqual(sizing["capital_inputs"]["current_stock_exposure"], 10000.0)
        self.assertTrue(any(row["max_new_amount"] > 0 for row in sizing["candidate_sizing"]))

    def test_snapshot_supplies_capital_and_exposure(self):
        snap = _snapshot(
            positions=[_position(market_value=12000.0, weight_pct=12.0)],
            cash=88000.0,
            total_assets=100000.0,
            asset_class_exposure={"股票": 12000.0},
        )
        sizing = psz.build_position_sizing(BRIEF, portfolio=snap)
        self.assertTrue(sizing["portfolio_linked"])
        self.assertEqual(sizing["capital_inputs"]["total_capital"], 100000.0)
        self.assertEqual(sizing["capital_inputs"]["current_stock_exposure"], 12000.0)
        self.assertEqual(sizing["exposure"]["current_cash"], 88000.0)
        self.assertEqual(sizing["portfolio_snapshot_hash"], "hash-a")
        self.assertEqual(sizing["portfolio_version"], 3)

    def test_manual_amounts_are_ignored_when_snapshot_present(self):
        snap = _snapshot(
            positions=[_position(market_value=12000.0)],
            asset_class_exposure={"股票": 12000.0},
        )
        sizing = psz.build_position_sizing(
            BRIEF, portfolio=snap, current_stock_exposure=999999.0
        )
        self.assertEqual(sizing["capital_inputs"]["current_stock_exposure"], 12000.0)
        self.assertTrue(any("以账本为准" in item for item in sizing["warnings"]))

    def test_new_money_never_exceeds_ledger_cash(self):
        snap = _snapshot(
            positions=[],
            cash=3000.0,
            total_assets=100000.0,
            asset_class_exposure={},
        )
        sizing = psz.build_position_sizing(BRIEF, portfolio=snap)
        self.assertLessEqual(sizing["exposure"]["remaining_total_capacity"], 3000.0)
        for row in sizing["candidate_sizing"]:
            self.assertLessEqual(row["max_new_amount"], 3000.0)

    def test_fail_closed_zeroes_every_candidate(self):
        snap = _snapshot(
            positions=[_position()],
            fail_closed=True,
            blocking_reasons=["账本哈希校验失败"],
            asset_class_exposure={"股票": 4000.0},
        )
        sizing = psz.build_position_sizing(BRIEF, portfolio=snap)
        self.assertFalse(sizing["allow_new_position"])
        for row in sizing["candidate_sizing"]:
            self.assertEqual(row["max_new_amount"], 0.0)
            self.assertEqual(row["first_lot_amount"], 0.0)
            self.assertIn("fail-closed", row["reason"])
        self.assertTrue(any("fail-closed" in item for item in sizing["warnings"]))

    def test_explicit_capital_still_wins_over_snapshot_total(self):
        snap = _snapshot(positions=[], total_assets=100000.0, cash=100000.0)
        sizing = psz.build_position_sizing(BRIEF, total_capital=50000.0, portfolio=snap)
        self.assertEqual(sizing["capital_inputs"]["total_capital"], 50000.0)

    def test_zero_capital_without_snapshot_still_raises(self):
        with self.assertRaises(ValueError):
            psz.build_position_sizing(BRIEF, total_capital=0.0)


# ---------------------------------------------------------------------------
# 9. 端到端：真实账本 -> 闸门 -> 决策
# ---------------------------------------------------------------------------


class TestEndToEndWithRealLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nasdx_gate_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "ledger.db")
        ps.set_cash_baseline(100000, db_path=self.db)
        ps.add_trade(
            "601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", fee=5, db_path=self.db
        )

    def test_priced_ledger_allows_capped_add(self):
        snap = ps.build_snapshot(prices={"601101": 12.0}, db_path=self.db)
        self.assertFalse(snap.fail_closed)
        plan = _plan(portfolio=snap)
        self.assertTrue(plan["portfolio_linked"])
        self.assertEqual(plan["portfolio_snapshot_hash"], snap.snapshot_hash)
        self.assertIsNotNone(plan["max_new_position_pct"])

    def test_any_trade_change_gives_decisions_a_new_snapshot_hash(self):
        """验收 #6：任一成交变化 -> 决策层读到新的 portfolio_snapshot_hash。"""
        prices = {"601101": 12.0}
        before = ps.build_snapshot(prices=prices, db_path=self.db)
        plan_before = _plan(portfolio=before)
        sizing_before = psz.build_position_sizing(
            BRIEF, total_capital=100000.0, portfolio=before
        )

        ps.add_trade(
            "601101", "buy", 200, 12.0, "2026-07-05 10:00:00", fee=2, db_path=self.db
        )
        after = ps.build_snapshot(prices=prices, db_path=self.db)
        plan_after = _plan(portfolio=after)
        sizing_after = psz.build_position_sizing(
            BRIEF, total_capital=100000.0, portfolio=after
        )

        self.assertTrue(plan_before["portfolio_snapshot_hash"])
        self.assertNotEqual(
            plan_before["portfolio_snapshot_hash"],
            plan_after["portfolio_snapshot_hash"],
        )
        self.assertNotEqual(
            sizing_before["portfolio_snapshot_hash"],
            sizing_after["portfolio_snapshot_hash"],
        )
        self.assertGreater(
            plan_after["portfolio_version"], plan_before["portfolio_version"]
        )

    def test_unchanged_ledger_keeps_the_same_hash(self):
        prices = {"601101": 12.0}
        first = _plan(portfolio=ps.build_snapshot(prices=prices, db_path=self.db))
        second = _plan(portfolio=ps.build_snapshot(prices=prices, db_path=self.db))
        self.assertEqual(
            first["portfolio_snapshot_hash"], second["portfolio_snapshot_hash"]
        )
        self.assertEqual(first["action"], second["action"])
        self.assertEqual(first["position_band"], second["position_band"])

    def test_missing_price_makes_ledger_fail_closed_and_blocks_buy(self):
        snap = ps.build_snapshot(prices={}, db_path=self.db)
        self.assertTrue(snap.fail_closed)
        plan = _plan(portfolio=snap)
        self.assertNotIn(plan["action"], BUY_ACTIONS)
        self.assertFalse(plan["allow_new_position"])

        sizing = psz.build_position_sizing(
            BRIEF, total_capital=100000.0, portfolio=snap
        )
        self.assertFalse(sizing["allow_new_position"])
        for row in sizing["candidate_sizing"]:
            self.assertEqual(row["max_new_amount"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
