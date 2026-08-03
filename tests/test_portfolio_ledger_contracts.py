# -*- coding: utf-8 -*-
"""#66 权威持仓账本契约测试。

覆盖验收点：
1. 重复事件幂等（同一笔成交提交两次只记一次）
2. CSV 重复导入去重
3. 乱序录入重放确定性（相同事件集合 -> 相同持仓与相同 snapshot_hash）
4. 部分卖出 / 手续费 / 移动加权成本
5. 修正事件保留审计链（不改写、不删除历史）
6. 快照哈希：状态不变则稳定，事件变化则变化
7. A股整数手规则（含科创板/北交所/ETF 与可配置覆盖）
8. fail-closed：缺价格 / 缺现金基线 / 现金为负 / 账本损坏
9. 输入健壮性：NaN/Inf/负数/布尔/未知方向
10. 隐私：不落券商账号与凭据，自由文本脱敏
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx import portfolio_store as ps  # noqa: E402
from nasdx import trade_events as te  # noqa: E402


CSV_TEXT = (
    "日期,证券代码,证券名称,买卖,成交数量,成交价,手续费,印花税\n"
    "2026-07-01 09:35:00,601101,昊华能源,买入,1000,10.00,5,0\n"
    "2026-07-02 10:05:00,510300,沪深300ETF,买入,500,4.10,1,0\n"
    "2026-07-03 14:20:00,601101,昊华能源,卖出,400,12.00,3,1\n"
)


class PortfolioLedgerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nasdx_portfolio_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "ledger.db")
        self.addCleanup(te.set_lot_rule_overrides, None)

    def other_db(self, name="other.db"):
        return os.path.join(self.tmp, name)


class TestIdempotency(PortfolioLedgerTestBase):
    def test_same_fill_recorded_twice_is_a_noop(self):
        first = ps.add_trade(
            "601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", fee=5, db_path=self.db
        )
        second = ps.add_trade(
            "601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", fee=5, db_path=self.db
        )
        self.assertEqual(first["status"], "recorded")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(first["event_id"], second["event_id"])

        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertEqual(len(snap.positions), 1)
        self.assertAlmostEqual(snap.positions[0]["quantity"], 1000.0)
        self.assertEqual(snap.event_count, 1)

    def test_explicit_event_id_is_the_idempotency_key(self):
        ps.add_trade(
            "601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", event_id="broker-42",
            db_path=self.db,
        )
        again = ps.add_trade(
            "601101", "buy", 1000, 10.5, "2026-07-01 09:36:00", event_id="broker-42",
            db_path=self.db,
        )
        self.assertEqual(again["status"], "duplicate")
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 1)

    def test_csv_reimport_is_deduplicated(self):
        first = ps.import_trades_csv(csv_text=CSV_TEXT, db_path=self.db)
        second = ps.import_trades_csv(csv_text=CSV_TEXT, db_path=self.db)
        self.assertEqual(first["parsed"], 3)
        self.assertEqual(first["recorded"], 3)
        self.assertEqual(first["duplicate"], 0)
        self.assertEqual(second["recorded"], 0)
        self.assertEqual(second["duplicate"], 3)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 3)

    def test_csv_rejects_bad_rows_without_blocking_good_rows(self):
        bad_csv = (
            "日期,证券代码,买卖,成交数量,成交价\n"
            "2026-07-01,601101,买入,1000,10.00\n"
            "2026-07-01,601101,买入,1000,0\n"
            "2026-07-01,601101,魔法操作,1000,10.00\n"
        )
        result = ps.import_trades_csv(csv_text=bad_csv, db_path=self.db)
        self.assertEqual(result["recorded"], 1)
        self.assertEqual(len(result["rejected"]), 2)


class TestDeterministicReplay(PortfolioLedgerTestBase):
    def _seed(self, db_path, order):
        fills = {
            "a": ("601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", 5.0),
            "b": ("510300", "buy", 500, 4.10, "2026-07-02 10:05:00", 1.0),
            "c": ("601101", "sell", 400, 12.0, "2026-07-03 14:20:00", 3.0),
        }
        ps.set_cash_baseline(100000, db_path=db_path)
        for key in order:
            code, side, qty, price, at, fee = fills[key]
            ps.add_trade(code, side, qty, price, at, fee=fee, db_path=db_path)

    def test_out_of_order_recording_yields_identical_snapshot(self):
        forward = self.other_db("forward.db")
        backward = self.other_db("backward.db")
        self._seed(forward, ["a", "b", "c"])
        self._seed(backward, ["c", "b", "a"])
        prices = {"601101": 12.5, "510300": 4.2}
        left = ps.build_snapshot(prices=prices, db_path=forward)
        right = ps.build_snapshot(prices=prices, db_path=backward)

        self.assertEqual(left.positions, right.positions)
        self.assertEqual(left.cash, right.cash)
        self.assertEqual(left.realized_pnl, right.realized_pnl)
        self.assertEqual(left.ledger_hash, right.ledger_hash)
        self.assertEqual(left.snapshot_hash, right.snapshot_hash)

    def test_partial_sell_uses_moving_average_cost_and_includes_fees(self):
        ps.set_cash_baseline(100000, db_path=self.db)
        ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", fee=5, db_path=self.db)
        ps.add_trade(
            "601101", "sell", 400, 12.0, "2026-07-03 14:20:00", fee=3, tax=1, db_path=self.db
        )
        snap = ps.build_snapshot(prices={"601101": 12.5}, db_path=self.db)
        position = snap.position("601101")
        self.assertIsNotNone(position)
        self.assertAlmostEqual(position["quantity"], 600.0)
        # 买入成本含手续费：(1000*10+5)/1000 = 10.005
        self.assertAlmostEqual(position["avg_cost"], 10.005, places=6)
        self.assertAlmostEqual(position["cost_basis"], 6003.0, places=4)
        # 已实现：400*12 - 4(费税) - 400*10.005 = 794.0
        self.assertAlmostEqual(snap.realized_pnl, 794.0, places=4)
        # 现金：100000 - 10005 + (4800 - 4) = 94791
        self.assertAlmostEqual(snap.cash, 94791.0, places=4)
        self.assertFalse(snap.fail_closed, snap.blocking_reasons)

    def test_snapshot_hash_is_stable_and_changes_with_the_ledger(self):
        ps.set_cash_baseline(100000, db_path=self.db)
        ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", db_path=self.db)
        prices = {"601101": 11.0}
        first = ps.build_snapshot(prices=prices, db_path=self.db)
        second = ps.build_snapshot(prices=prices, db_path=self.db)
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)
        self.assertEqual(first.snapshot_hash, ps.current_snapshot_hash(prices, db_path=self.db))

        ps.add_trade("601101", "buy", 200, 10.4, "2026-07-04 09:40:00", db_path=self.db)
        third = ps.build_snapshot(prices=prices, db_path=self.db)
        self.assertNotEqual(first.snapshot_hash, third.snapshot_hash)
        self.assertGreater(third.portfolio_version, first.portfolio_version)

        # 价格变化也要改变快照哈希（否则 #65 的缓存键会读到陈旧估值）
        fourth = ps.build_snapshot(prices={"601101": 11.5}, db_path=self.db)
        self.assertNotEqual(third.snapshot_hash, fourth.snapshot_hash)

    def test_portfolio_version_is_monotonic(self):
        versions = [ps.set_cash_baseline(100000, db_path=self.db)["portfolio_version"]]
        versions.append(
            ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)[
                "portfolio_version"
            ]
        )
        versions.append(
            ps.add_trade("510300", "buy", 500, 4.1, "2026-07-02", db_path=self.db)[
                "portfolio_version"
            ]
        )
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(len(set(versions)), len(versions))


class TestCorrectionAuditChain(PortfolioLedgerTestBase):
    def test_correction_supersedes_without_destroying_history(self):
        original = ps.add_trade(
            "601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", db_path=self.db
        )
        before = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)

        result = ps.correct_event(
            original["event_id"], db_path=self.db, reason="券商回单为800股", quantity=800
        )
        self.assertEqual(result["status"], "corrected")
        self.assertNotEqual(result["replacement_event_id"], original["event_id"])

        active = ps.list_events(db_path=self.db)
        every = ps.list_events(db_path=self.db, include_superseded=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(len(every), 2)
        self.assertAlmostEqual(active[0]["quantity"], 800.0)
        self.assertEqual(active[0]["corrects"], original["event_id"])

        old_row = [row for row in every if row["event_id"] == original["event_id"]][0]
        self.assertEqual(old_row["superseded_by"], result["replacement_event_id"])
        self.assertAlmostEqual(old_row["quantity"], 1000.0)  # 原始数值未被改写
        self.assertEqual(old_row["supersede_reason"], "券商回单为800股")

        after = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(after.position("601101")["quantity"], 800.0)
        self.assertNotEqual(before.snapshot_hash, after.snapshot_hash)
        self.assertGreater(after.portfolio_version, before.portfolio_version)

    def test_void_removes_the_position_but_keeps_the_row(self):
        original = ps.add_trade(
            "601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", db_path=self.db
        )
        result = ps.correct_event(original["event_id"], db_path=self.db, reason="重复录入")
        self.assertEqual(result["status"], "voided")
        self.assertEqual(ps.list_events(db_path=self.db), [])
        self.assertEqual(len(ps.list_events(db_path=self.db, include_superseded=True)), 1)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertEqual(snap.positions, [])
        self.assertEqual(snap.active_event_count, 0)
        self.assertEqual(snap.event_count, 1)

    def test_cannot_correct_the_same_event_twice(self):
        original = ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)
        ps.correct_event(original["event_id"], db_path=self.db, quantity=800)
        with self.assertRaises(te.TradeEventError):
            ps.correct_event(original["event_id"], db_path=self.db, quantity=600)

    def test_correcting_an_unknown_event_fails_loudly(self):
        with self.assertRaises(te.TradeEventError):
            ps.correct_event("does-not-exist", db_path=self.db, quantity=100)

    def test_export_keeps_the_full_audit_chain(self):
        original = ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)
        ps.correct_event(original["event_id"], db_path=self.db, quantity=800, reason="回单修正")
        payload = ps.export_events(db_path=self.db)
        self.assertEqual(len(payload["events"]), 2)
        self.assertTrue(payload["status"]["healthy"])
        self.assertEqual(payload["status"]["superseded_count"], 1)


class TestLotRules(PortfolioLedgerTestBase):
    def test_a_share_requires_whole_lots(self):
        with self.assertRaises(te.LotSizeError):
            ps.add_trade("601101", "buy", 150, 10.0, "2026-07-01", db_path=self.db)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 0)
        ps.add_trade("601101", "buy", 200, 10.0, "2026-07-01", db_path=self.db)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 1)

    def test_odd_sell_only_allowed_when_it_closes_the_position(self):
        ps.add_trade("601101", "buy", 200, 10.0, "2026-07-01", db_path=self.db)
        with self.assertRaises(te.LotSizeError):
            ps.add_trade("601101", "sell", 150, 11.0, "2026-07-02", db_path=self.db)
        # 分红送股产生的零碎股，清仓时允许
        ps.add_trade("601101", "adjustment", 30, 0, "2026-07-02", note="送股", db_path=self.db)
        ps.add_trade("601101", "sell", 230, 11.0, "2026-07-03", db_path=self.db)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertEqual(snap.positions, [])

    def test_board_specific_rules(self):
        # 科创板：最小 200 股，其上按 1 股递增
        with self.assertRaises(te.LotSizeError):
            ps.add_trade("688981", "buy", 150, 50.0, "2026-07-01", db_path=self.db)
        ps.add_trade("688981", "buy", 253, 50.0, "2026-07-01", db_path=self.db)
        # 北交所：最小 100 股，其上按 1 股递增
        ps.add_trade("830799", "buy", 137, 12.0, "2026-07-01", db_path=self.db)
        # ETF：100 份整数手
        ps.add_trade("510300", "buy", 500, 4.1, "2026-07-01", db_path=self.db)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 3)

    def test_lot_rules_are_configurable_not_hardcoded(self):
        te.set_lot_rule_overrides({"601101": te.LotRule(lot_size=1, min_buy_quantity=1, label="测试")})
        ps.add_trade("601101", "buy", 137, 10.0, "2026-07-01", db_path=self.db)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 1)
        te.set_lot_rule_overrides(None)
        with self.assertRaises(te.LotSizeError):
            ps.add_trade("601101", "buy", 137, 10.5, "2026-07-02", db_path=self.db)

    def test_asset_class_classification(self):
        self.assertEqual(te.classify_asset_class("510300"), "ETF")
        self.assertEqual(te.classify_asset_class("588000"), "ETF")
        self.assertEqual(te.classify_asset_class("601101"), "股票")
        self.assertEqual(te.classify_asset_class("688981"), "股票")

    def test_broker_receipts_can_bypass_enforcement_but_still_warn(self):
        result = ps.add_trade(
            "601101", "buy", 137, 10.0, "2026-07-01", db_path=self.db, enforce_lot_rules=False
        )
        self.assertEqual(result["status"], "recorded")
        self.assertTrue(result["lot_warnings"])


class TestCorrectionLotRules(PortfolioLedgerTestBase):
    """#70：correct_event 必须与 add_event 共用同一套手数校验。"""

    def _ledger_fingerprint(self):
        status = ps.portfolio_status(db_path=self.db)
        snap = ps.build_snapshot(prices={"601101": 11.0, "688981": 50.0}, db_path=self.db)
        return (
            status["event_count"],
            status["active_event_count"],
            status["portfolio_version"],
            snap.snapshot_hash,
        )

    # --- 验收 1：买入修正为不足最小买入量 ---------------------------------
    def test_buy_corrected_below_min_lot_is_rejected(self):
        original = ps.add_trade(
            "601101", "buy", 100, 10.73, "2026-08-03 09:31:00", db_path=self.db
        )
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(original["event_id"], db_path=self.db, quantity=50, reason="修改数量")
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("601101")["quantity"], 100.0)

    def test_buy_corrected_to_non_multiple_of_lot_is_rejected(self):
        original = ps.add_trade("601101", "buy", 200, 10.0, "2026-08-01", db_path=self.db)
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(original["event_id"], db_path=self.db, quantity=250)
        ps.correct_event(original["event_id"], db_path=self.db, quantity=300)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("601101")["quantity"], 300.0)

    # --- 验收 2：非清仓卖出修正为零碎股 -----------------------------------
    def test_partial_sell_corrected_to_odd_lot_is_rejected(self):
        ps.add_trade("601101", "buy", 300, 10.0, "2026-08-01 09:31:00", db_path=self.db)
        sell = ps.add_trade("601101", "sell", 100, 11.0, "2026-08-02 09:31:00", db_path=self.db)
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(sell["event_id"], db_path=self.db, quantity=50, reason="改数量")
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("601101")["quantity"], 200.0)

    # --- 验收 3 + 6：零碎股清仓，且 held 排除原事件 ------------------------
    def test_odd_lot_liquidation_correction_is_allowed(self):
        ps.add_trade("601101", "buy", 100, 10.0, "2026-08-01", db_path=self.db)
        ps.add_trade("601101", "adjustment", 50, 0, "2026-08-02", note="送股", db_path=self.db)
        sell = ps.add_trade("601101", "sell", 100, 11.0, "2026-08-03", db_path=self.db)
        # 若 held 没有排除被修正的卖出，基线会变成 50 股，150 股清仓会被误判为零碎股。
        result = ps.correct_event(sell["event_id"], db_path=self.db, quantity=150, reason="全部卖出")
        self.assertEqual(result["status"], "corrected")
        self.assertEqual(result["lot_warnings"], [])
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertEqual(snap.positions, [])

    def test_held_baseline_excludes_the_superseded_event_for_buys(self):
        first = ps.add_trade("601101", "buy", 100, 10.0, "2026-08-01", db_path=self.db)
        ps.add_trade("601101", "buy", 200, 10.5, "2026-08-02", db_path=self.db)
        ps.correct_event(first["event_id"], db_path=self.db, quantity=400)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("601101")["quantity"], 600.0)

    # --- 验收 4：各板块 / ETF / 自定义 override 与 add_event 完全一致 -------
    def test_star_board_rules_apply_to_corrections(self):
        original = ps.add_trade("688981", "buy", 253, 50.0, "2026-08-01", db_path=self.db)
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(original["event_id"], db_path=self.db, quantity=150)
        ps.correct_event(original["event_id"], db_path=self.db, quantity=201)
        snap = ps.build_snapshot(prices={"688981": 50.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("688981")["quantity"], 201.0)

    def test_bse_board_rules_apply_to_corrections(self):
        original = ps.add_trade("830799", "buy", 137, 12.0, "2026-08-01", db_path=self.db)
        ps.correct_event(original["event_id"], db_path=self.db, quantity=113)
        snap = ps.build_snapshot(prices={"830799": 12.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("830799")["quantity"], 113.0)
        latest = ps.list_events(db_path=self.db)[0]
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(latest["event_id"], db_path=self.db, quantity=99)

    def test_etf_rules_apply_to_corrections(self):
        original = ps.add_trade("510300", "buy", 500, 4.1, "2026-08-01", db_path=self.db)
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(original["event_id"], db_path=self.db, quantity=450)
        ps.correct_event(original["event_id"], db_path=self.db, quantity=400)
        snap = ps.build_snapshot(prices={"510300": 4.2}, db_path=self.db)
        self.assertAlmostEqual(snap.position("510300")["quantity"], 400.0)

    def test_custom_overrides_apply_to_corrections(self):
        te.set_lot_rule_overrides(
            {"601101": te.LotRule(lot_size=1, min_buy_quantity=1, label="测试")}
        )
        original = ps.add_trade("601101", "buy", 137, 10.0, "2026-08-01", db_path=self.db)
        ps.correct_event(original["event_id"], db_path=self.db, quantity=37)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("601101")["quantity"], 37.0)

        te.set_lot_rule_overrides(None)
        latest = ps.list_events(db_path=self.db)[0]
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(latest["event_id"], db_path=self.db, quantity=45)

    # --- 验收 5：修正 code / side 后按 replacement 重新判定 -----------------
    def test_cross_code_correction_uses_the_replacement_board_rule(self):
        original = ps.add_trade("601101", "buy", 100, 10.0, "2026-08-01", db_path=self.db)
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(original["event_id"], db_path=self.db, code="688981", reason="改代码")
        ps.correct_event(
            original["event_id"], db_path=self.db, code="688981", quantity=250, reason="改代码"
        )
        snap = ps.build_snapshot(prices={"688981": 50.0}, db_path=self.db)
        self.assertIsNone(snap.position("601101"))
        self.assertAlmostEqual(snap.position("688981")["quantity"], 250.0)

    def test_side_change_is_revalidated_as_the_replacement_side(self):
        original = ps.add_trade(
            "601101", "adjustment", 50, 0, "2026-08-01", note="送股", db_path=self.db
        )
        # adjustment 不受手数约束，改成 buy 之后必须按买入规则重新校验。
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(original["event_id"], db_path=self.db, side="buy", price=10.0)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 1)

    # --- 验收 7：校验失败不留痕 -------------------------------------------
    def test_rejected_correction_rolls_everything_back(self):
        original = ps.add_trade("601101", "buy", 100, 10.0, "2026-08-01", db_path=self.db)
        before = self._ledger_fingerprint()
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(original["event_id"], db_path=self.db, quantity=50, reason="改数量")
        self.assertEqual(self._ledger_fingerprint(), before)
        rows = ps.list_events(db_path=self.db, include_superseded=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["superseded_by"], "")
        # 原事件仍可被正常修正，失败的尝试没有把它锁死。
        self.assertEqual(
            ps.correct_event(original["event_id"], db_path=self.db, quantity=200)["status"],
            "corrected",
        )

    # --- 验收 8：两种 replacement 入口共用同一校验 -------------------------
    def test_replacement_object_entry_point_is_validated(self):
        original = ps.add_trade("601101", "buy", 100, 10.0, "2026-08-01", db_path=self.db)
        bad = te.build_trade_event(
            code="601101", side="buy", quantity=37, price=10.0, occurred_at="2026-08-01"
        )
        with self.assertRaises(te.LotSizeError):
            ps.correct_event(original["event_id"], db_path=self.db, replacement=bad)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 1)

        good = te.build_trade_event(
            code="601101", side="buy", quantity=300, price=10.0, occurred_at="2026-08-01"
        )
        self.assertEqual(
            ps.correct_event(original["event_id"], db_path=self.db, replacement=good)["status"],
            "corrected",
        )

    def test_replacement_must_be_a_trade_event(self):
        original = ps.add_trade("601101", "buy", 100, 10.0, "2026-08-01", db_path=self.db)
        with self.assertRaises(te.TradeEventError):
            ps.correct_event(original["event_id"], db_path=self.db, replacement={"quantity": 100})
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 1)

    # --- 逃生口与既有行为 --------------------------------------------------
    def test_explicit_escape_hatch_records_but_still_warns(self):
        original = ps.add_trade("601101", "buy", 100, 10.0, "2026-08-01", db_path=self.db)
        result = ps.correct_event(
            original["event_id"],
            db_path=self.db,
            quantity=137,
            reason="券商真实回单",
            enforce_lot_rules=False,
        )
        self.assertEqual(result["status"], "corrected")
        self.assertTrue(result["lot_warnings"])
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("601101")["quantity"], 137.0)

    def test_price_only_correction_of_a_historical_odd_lot_is_not_re_judged(self):
        # 券商真实回单的 137 股通过 enforce_lot_rules=False 入账，
        # 之后只改价格不会改变手数敞口，不应被本次校验误拦。
        original = ps.add_trade(
            "601101", "buy", 137, 10.0, "2026-08-01", db_path=self.db, enforce_lot_rules=False
        )
        priced = ps.correct_event(original["event_id"], db_path=self.db, price=10.5, reason="改价")
        self.assertEqual(priced["status"], "corrected")
        self.assertEqual(priced["lot_warnings"], [])
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("601101")["quantity"], 137.0)

    def test_price_only_correction_of_a_liquidation_survives_later_fills(self):
        ps.add_trade("601101", "buy", 100, 10.0, "2026-08-01", db_path=self.db)
        ps.add_trade("601101", "adjustment", 50, 0, "2026-08-02", note="送股", db_path=self.db)
        sell = ps.add_trade("601101", "sell", 150, 11.0, "2026-08-03", db_path=self.db)
        ps.add_trade("601101", "buy", 200, 10.2, "2026-08-04", db_path=self.db)
        # 后续买入改变了当前持仓，但只改价格的修正没有引入新的手数敞口。
        result = ps.correct_event(sell["event_id"], db_path=self.db, price=11.2, reason="改价")
        self.assertEqual(result["status"], "corrected")
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertAlmostEqual(snap.position("601101")["quantity"], 200.0)

    def test_void_is_unaffected_by_the_lot_guard(self):
        original = ps.add_trade(
            "601101", "buy", 137, 10.0, "2026-08-01", db_path=self.db, enforce_lot_rules=False
        )
        voided = ps.correct_event(original["event_id"], db_path=self.db, reason="重复录入")
        self.assertEqual(voided["status"], "voided")
        self.assertEqual(voided["lot_warnings"], [])
        self.assertEqual(ps.build_snapshot(db_path=self.db).positions, [])

    def test_cli_correct_is_fail_closed_by_default(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            ps.main(["--db", self.db, "add-trade", "--code", "601101", "--side", "buy",
                     "--qty", "100", "--price", "10.0", "--at", "2026-08-01 09:31:00"])
        event_id = json.loads(buffer.getvalue())["event_id"]

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ps.main(["--db", self.db, "correct", "--event-id", event_id, "--qty", "50"])
        output = buffer.getvalue()
        self.assertEqual(code, 2)
        self.assertIn("错误：", output)
        self.assertIn("A股普通股票", output)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["active_event_count"], 1)

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ps.main(["--db", self.db, "correct", "--event-id", event_id,
                            "--qty", "50", "--allow-odd-lot"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["status"], "corrected")
        self.assertTrue(payload["lot_warnings"])


class TestFailClosed(PortfolioLedgerTestBase):
    def test_missing_price_blocks_the_snapshot(self):
        ps.set_cash_baseline(100000, db_path=self.db)
        ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)
        snap = ps.build_snapshot(prices={}, db_path=self.db)
        self.assertTrue(snap.fail_closed)
        self.assertTrue(any("601101" in reason for reason in snap.blocking_reasons))
        self.assertEqual(snap.positions[0]["valuation_status"], "missing_price")
        self.assertIsNone(snap.positions[0]["market_value"])

    def test_invalid_price_is_treated_as_missing(self):
        ps.set_cash_baseline(100000, db_path=self.db)
        ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)
        for bad in (float("nan"), float("inf"), 0, -1, "N/A", None, True):
            snap = ps.build_snapshot(prices={"601101": bad}, db_path=self.db)
            self.assertTrue(snap.fail_closed, f"price={bad!r} 应当 fail-closed")

    def test_missing_cash_baseline_blocks_the_snapshot(self):
        ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertTrue(snap.fail_closed)
        self.assertIsNone(snap.cash)
        self.assertEqual(snap.cash_status, "unknown")
        self.assertIsNone(snap.total_assets)

    def test_negative_cash_blocks_the_snapshot(self):
        ps.set_cash_baseline(1000, db_path=self.db)
        ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertTrue(snap.fail_closed)
        self.assertEqual(snap.cash_status, "negative")

    def test_sell_without_a_recorded_buy_blocks_the_snapshot(self):
        ps.set_cash_baseline(100000, db_path=self.db)
        ps.add_trade("601101", "sell", 1000, 10.0, "2026-07-01", db_path=self.db)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertTrue(snap.fail_closed)
        self.assertTrue(any("无持仓卖出" in reason for reason in snap.blocking_reasons))

    def test_corrupt_ledger_never_looks_like_an_empty_account(self):
        broken = self.other_db("broken.db")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("this is definitely not a sqlite database" * 8)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=broken)
        self.assertTrue(snap.fail_closed)
        self.assertEqual(snap.positions, [])
        self.assertEqual(snap.cash_status, "unavailable")
        self.assertTrue(snap.blocking_reasons)
        with self.assertRaises(ps.PortfolioLedgerError):
            ps.build_snapshot(prices={"601101": 11.0}, db_path=broken, strict=True)
        self.assertFalse(ps.portfolio_status(db_path=broken)["healthy"])

    def test_schema_mismatch_fails_closed(self):
        import sqlite3

        ps.init_portfolio_db(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "update portfolio_meta set value = 'nasdx_portfolio.v999' "
                "where key = 'schema_version'"
            )
        snap = ps.build_snapshot(prices={}, db_path=self.db)
        self.assertTrue(snap.fail_closed)
        self.assertTrue(any("schema" in reason for reason in snap.blocking_reasons))

    def test_healthy_snapshot_is_not_fail_closed(self):
        ps.set_cash_baseline(100000, db_path=self.db)
        ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)
        snap = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertFalse(snap.fail_closed, snap.blocking_reasons)
        self.assertAlmostEqual(snap.total_market_value, 11000.0)
        self.assertAlmostEqual(snap.cash, 90000.0)
        self.assertAlmostEqual(snap.total_assets, 101000.0)
        self.assertAlmostEqual(snap.positions[0]["weight_pct"], round(11000 / 101000 * 100, 4))


class TestInputRobustness(PortfolioLedgerTestBase):
    def test_rejects_non_finite_and_negative_numbers(self):
        bad_cases = [
            dict(code="601101", side="buy", quantity=float("nan"), price=10.0),
            dict(code="601101", side="buy", quantity=100, price=float("inf")),
            dict(code="601101", side="buy", quantity=-100, price=10.0),
            dict(code="601101", side="buy", quantity=True, price=10.0),
            dict(code="601101", side="buy", quantity=100, price=0),
            dict(code="601101", side="buy", quantity=0, price=10.0),
            dict(code="", side="buy", quantity=100, price=10.0),
            dict(code="601101", side="魔法", quantity=100, price=10.0),
            dict(code="601101", side="buy", quantity=100, price=10.0, fee=-1),
        ]
        for case in bad_cases:
            with self.subTest(case=case):
                with self.assertRaises(te.TradeEventError):
                    te.build_trade_event(occurred_at="2026-07-01", **case)

    def test_validation_happens_before_any_write(self):
        with self.assertRaises(te.TradeEventError):
            ps.add_trade("601101", "buy", float("nan"), 10.0, "2026-07-01", db_path=self.db)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["event_count"], 0)

    def test_cash_baseline_rejects_bad_values(self):
        for bad in (float("nan"), float("inf"), -1, True, "abc"):
            with self.subTest(bad=bad):
                with self.assertRaises(te.TradeEventError):
                    ps.set_cash_baseline(bad, db_path=self.db)

    def test_code_and_side_normalization(self):
        event = te.build_trade_event(
            code="sh601101", side="买入", quantity=100, price=10.0, occurred_at="2026/07/01"
        )
        self.assertEqual(event.code, "601101")
        self.assertEqual(event.side, "buy")
        self.assertEqual(event.occurred_at, "2026-07-01T00:00:00")

    def test_unparsable_timestamp_is_rejected(self):
        with self.assertRaises(te.TradeEventError):
            te.build_trade_event(
                code="601101", side="buy", quantity=100, price=10.0, occurred_at="上周三"
            )

    def test_clear_requires_confirmation(self):
        ps.add_trade("601101", "buy", 100, 10.0, "2026-07-01", db_path=self.db)
        with self.assertRaises(ps.PortfolioLedgerError):
            ps.clear_portfolio(db_path=self.db)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["event_count"], 1)
        ps.clear_portfolio(confirm=True, db_path=self.db)
        self.assertEqual(ps.portfolio_status(db_path=self.db)["event_count"], 0)


class TestPrivacy(PortfolioLedgerTestBase):
    def test_no_account_or_credential_columns_exist(self):
        import sqlite3

        ps.init_portfolio_db(self.db)
        with sqlite3.connect(self.db) as conn:
            columns = {
                row[1].lower()
                for row in conn.execute("pragma table_info(trade_events)").fetchall()
            }
        forbidden = {
            "account", "account_id", "broker", "broker_account", "password",
            "token", "api_key", "secret", "id_card", "phone",
        }
        self.assertEqual(columns & forbidden, set())

    def test_free_text_is_redacted_before_persistence(self):
        ps.add_trade(
            "601101", "buy", 100, 10.0, "2026-07-01",
            note="broker api_key=sk-abcdef1234567890 请勿外传",
            db_path=self.db,
        )
        note = ps.list_events(db_path=self.db)[0]["note"]
        self.assertNotIn("sk-abcdef1234567890", note)
        self.assertIn("[REDACTED]", note)

    def test_long_free_text_is_bounded(self):
        event = te.build_trade_event(
            code="601101", side="buy", quantity=100, price=10.0,
            occurred_at="2026-07-01", name="名" * 200, note="备" * 2000,
        )
        self.assertLessEqual(len(event.name), 60)
        self.assertLessEqual(len(event.note), 220)

    def test_backup_stays_local_and_reproduces_the_ledger(self):
        ps.set_cash_baseline(100000, db_path=self.db)
        ps.add_trade("601101", "buy", 1000, 10.0, "2026-07-01", db_path=self.db)
        target = ps.backup_portfolio(self.other_db("backup.db"), db_path=self.db)
        self.assertTrue(os.path.exists(target))
        restored = ps.build_snapshot(prices={"601101": 11.0}, db_path=target)
        original = ps.build_snapshot(prices={"601101": 11.0}, db_path=self.db)
        self.assertEqual(restored.snapshot_hash, original.snapshot_hash)


class TestCli(PortfolioLedgerTestBase):
    def _run(self, *args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ps.main(["--db", self.db, *args])
        return code, buffer.getvalue()

    def test_cli_round_trip(self):
        self.assertEqual(self._run("set-cash", "--amount", "100000")[0], 0)
        self.assertEqual(
            self._run(
                "add-trade", "--code", "601101", "--side", "buy",
                "--qty", "1000", "--price", "10.0", "--at", "2026-07-01 09:35:00",
            )[0],
            0,
        )
        code, output = self._run("show", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["schema"], ps.SNAPSHOT_SCHEMA)
        self.assertEqual(len(payload["positions"]), 1)
        # 缺行情时 CLI 快照必须 fail-closed
        self.assertTrue(payload["fail_closed"])

        code, output = self._run("status")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["healthy"])

        code, output = self._run("events")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(output)), 1)

    def test_cli_reports_validation_errors_without_traceback(self):
        code, output = self._run(
            "add-trade", "--code", "601101", "--side", "buy", "--qty", "150", "--price", "10"
        )
        self.assertEqual(code, 2)
        self.assertIn("错误：", output)

    def test_cli_show_renders_markdown_by_default(self):
        code, output = self._run("show")
        self.assertEqual(code, 0)
        self.assertIn("NASDX 组合快照", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
