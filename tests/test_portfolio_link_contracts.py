# -*- coding: utf-8 -*-
"""#66 验收点 6/7 端到端接线契约测试：权威账本 -> 分析入口。

覆盖：
1. 开关语义：默认接入；``--no-portfolio-link`` / ``NASDX_PORTFOLIO_LINK=0`` 可关。
2. 未初始化账本（无库文件 / 空库）-> 返回 None，行为与 #66 之前一致，且不创建库文件。
3. 已初始化账本 -> 返回真实快照，持仓/现金/哈希来自账本。
4. 账本损坏 / 解析异常 -> fail-closed 快照，且哈希非空（不得与"未接入"撞键）。
5. 行情映射：价格与行业从已加载的 data 快照抽取，NaN/负数/缺失安全。
6. 缓存失效输入：接入账本后 ``portfolio_snapshot_hash`` 必须非空且随成交变化。
7. CLI 入口默认开启接线（run_analysis.py / analyze.py 契约）。
8. 批量入口只解析一次账本，同批次共享同一 snapshot_hash。
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx import portfolio_link as pl  # noqa: E402
from nasdx import portfolio_store as ps  # noqa: E402
from nasdx.analyzer import _portfolio_snapshot_hash  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _market_data():
    return {
        "date": "20260804",
        "sectors": [
            {
                "name": "煤炭",
                "stocks": [
                    {"code": "601101", "name": "昊华能源", "indicators": {"close": 12.5}},
                    # 只有资金流收盘价的标的也要能取到价
                    {
                        "code": "600123",
                        "name": "兰花科创",
                        "indicators": {},
                        "fund_flow": [{"收盘价": 8.8}],
                    },
                    # 脏数据：价格为 0 / 非数字 -> 不进价格表
                    {"code": "600000", "name": "脏价", "indicators": {"close": 0}},
                    {"code": "600001", "name": "非数字", "indicators": {"close": "abc"}},
                ],
                "etfs": [
                    {"code": "510300", "name": "沪深300ETF", "indicators": {"current_price": 4.2}}
                ],
            }
        ],
    }


class LinkTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nasdx_portfolio_link_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "ledger.db")

    def seed_ledger(self):
        ps.set_cash_baseline(50000.0, "2026-07-01", db_path=self.db)
        ps.add_trade(
            "601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", fee=5, db_path=self.db
        )


# ── 1. 开关语义 ─────────────────────────────────────────────


class TestLinkSwitch(LinkTestBase):
    def test_default_enabled(self):
        self.assertTrue(pl.link_enabled(None, environ={}))

    def test_env_can_disable(self):
        for raw in ("0", "false", "OFF", "No"):
            self.assertFalse(
                pl.link_enabled(None, environ={pl.LINK_ENV: raw}), f"env={raw}"
            )

    def test_env_unknown_value_keeps_enabled(self):
        self.assertTrue(pl.link_enabled(None, environ={pl.LINK_ENV: "1"}))
        self.assertTrue(pl.link_enabled(None, environ={pl.LINK_ENV: "yes"}))

    def test_explicit_flag_wins_over_env(self):
        self.assertFalse(pl.link_enabled(False, environ={pl.LINK_ENV: "1"}))
        self.assertTrue(pl.link_enabled(True, environ={pl.LINK_ENV: "0"}))

    def test_disabled_switch_returns_none_even_with_ledger(self):
        self.seed_ledger()
        self.assertIsNone(
            pl.resolve_portfolio(_market_data(), db_path=self.db, enabled=False)
        )


# ── 2. 未初始化账本 = 不接入 ─────────────────────────────────


class TestUninitializedLedger(LinkTestBase):
    def test_missing_db_is_not_initialized(self):
        self.assertFalse(pl.ledger_is_initialized(self.db))

    def test_resolve_returns_none_and_does_not_create_db(self):
        self.assertIsNone(pl.resolve_portfolio(_market_data(), db_path=self.db))
        self.assertFalse(
            os.path.exists(self.db), "解析组合不得副作用地创建账本数据库"
        )

    def test_empty_ledger_is_not_initialized(self):
        # 建库但不写任何事件 / 现金基线
        ps.portfolio_status(db_path=self.db)
        if os.path.exists(self.db):
            self.assertFalse(pl.ledger_is_initialized(self.db))
        self.assertIsNone(pl.resolve_portfolio(_market_data(), db_path=self.db))

    def test_cash_baseline_only_counts_as_initialized(self):
        ps.set_cash_baseline(10000.0, "2026-07-01", db_path=self.db)
        self.assertTrue(pl.ledger_is_initialized(self.db))
        snap = pl.resolve_portfolio(_market_data(), db_path=self.db)
        self.assertIsNotNone(snap)


# ── 3. 已初始化账本 = 强制接入 ───────────────────────────────


class TestInitializedLedger(LinkTestBase):
    def test_resolve_returns_real_snapshot(self):
        self.seed_ledger()
        snap = pl.resolve_portfolio(_market_data(), db_path=self.db)
        self.assertIsNotNone(snap)
        codes = [p["code"] for p in snap.positions]
        self.assertIn("601101", codes)
        self.assertTrue(snap.snapshot_hash)
        self.assertFalse(snap.fail_closed, snap.blocking_reasons)

    def test_snapshot_uses_market_prices(self):
        self.seed_ledger()
        snap = pl.resolve_portfolio(_market_data(), db_path=self.db)
        pos = next(p for p in snap.positions if p["code"] == "601101")
        # 行情价 12.5 * 1000 股
        self.assertAlmostEqual(float(pos["market_value"]), 12500.0, places=2)

    def test_snapshot_hash_changes_after_new_fill(self):
        self.seed_ledger()
        before = pl.resolve_portfolio(_market_data(), db_path=self.db).snapshot_hash
        ps.add_trade(
            "510300", "buy", 500, 4.10, "2026-07-02 10:05:00", fee=1, db_path=self.db
        )
        after = pl.resolve_portfolio(_market_data(), db_path=self.db).snapshot_hash
        self.assertNotEqual(before, after, "成交后 snapshot_hash 必须变化")

    def test_resolve_is_deterministic(self):
        self.seed_ledger()
        data = _market_data()
        a = pl.resolve_portfolio(data, db_path=self.db).snapshot_hash
        b = pl.resolve_portfolio(data, db_path=self.db).snapshot_hash
        self.assertEqual(a, b)


# ── 4. fail-closed 且哈希非空 ────────────────────────────────


class TestFailClosed(LinkTestBase):
    def test_corrupt_db_is_treated_as_linked_not_empty(self):
        with open(self.db, "w", encoding="utf-8") as fh:
            fh.write("this is not a sqlite database")
        self.assertTrue(
            pl.ledger_is_initialized(self.db),
            "坏账本必须算已接入，否则会被误判成没有组合而放行加仓",
        )

    def test_corrupt_db_resolves_to_fail_closed_with_non_empty_hash(self):
        with open(self.db, "w", encoding="utf-8") as fh:
            fh.write("this is not a sqlite database")
        snap = pl.resolve_portfolio(_market_data(), db_path=self.db)
        self.assertIsNotNone(snap)
        digest = pl._snapshot_field(snap, "snapshot_hash", "")
        self.assertTrue(
            digest, "fail-closed 快照哈希不得为空（空哈希 = 未接入组合的缓存键）"
        )
        self.assertTrue(pl._snapshot_field(snap, "fail_closed", False))

    def test_broken_snapshot_hash_is_non_empty_and_message_sensitive(self):
        a = ps._broken_snapshot("2026-08-04T00:00:00", "boom-a", 10.0, 30.0)
        b = ps._broken_snapshot("2026-08-04T00:00:00", "boom-b", 10.0, 30.0)
        self.assertTrue(a.snapshot_hash)
        self.assertTrue(b.snapshot_hash)
        self.assertNotEqual(a.snapshot_hash, b.snapshot_hash)
        self.assertTrue(a.fail_closed)

    def test_link_layer_fail_closed_snapshot_shape(self):
        snap = pl._fail_closed_snapshot("db locked")
        self.assertTrue(snap["snapshot_hash"])
        self.assertTrue(snap["fail_closed"])
        self.assertTrue(snap["blocking_reasons"])
        self.assertIsNone(snap["cash"])

    def test_resolve_never_raises_on_unexpected_error(self):
        self.seed_ledger()
        original = ps.build_snapshot

        def boom(*_args, **_kwargs):
            raise RuntimeError("unexpected")

        pl.build_snapshot = boom  # type: ignore[assignment]
        try:
            snap = pl.resolve_portfolio(_market_data(), db_path=self.db)
        finally:
            pl.build_snapshot = original  # type: ignore[assignment]
        self.assertIsNotNone(snap)
        self.assertTrue(pl._snapshot_field(snap, "fail_closed", False))
        self.assertTrue(pl._snapshot_field(snap, "snapshot_hash", ""))


# ── 5. 行情映射 ─────────────────────────────────────────────


class TestMarketMaps(unittest.TestCase):
    def test_price_map_extracts_close_and_current_price(self):
        prices = pl.market_price_map(_market_data())
        self.assertAlmostEqual(prices["601101"]["price"], 12.5)
        self.assertAlmostEqual(prices["510300"]["price"], 4.2)
        self.assertAlmostEqual(prices["600123"]["price"], 8.8)

    def test_price_map_drops_dirty_values(self):
        prices = pl.market_price_map(_market_data())
        self.assertNotIn("600000", prices, "0 价不得进入估值表")
        self.assertNotIn("600001", prices, "非数字价不得进入估值表")

    def test_price_map_carries_as_of(self):
        prices = pl.market_price_map(_market_data())
        self.assertEqual(prices["601101"]["as_of"], "20260804")

    def test_industry_map(self):
        industries = pl.market_industry_map(_market_data())
        self.assertEqual(industries["601101"], "煤炭")
        self.assertEqual(industries["510300"], "煤炭")

    def test_maps_tolerate_empty_data(self):
        self.assertEqual(pl.market_price_map(None), {})
        self.assertEqual(pl.market_industry_map({}), {})
        self.assertEqual(pl.market_price_map({"sectors": None}), {})


# ── 6. 缓存失效输入 ─────────────────────────────────────────


class TestCacheInvalidationHash(LinkTestBase):
    def test_no_portfolio_yields_empty_hash(self):
        self.assertEqual(_portfolio_snapshot_hash("601101", None), "")

    def test_linked_portfolio_yields_non_empty_hash(self):
        self.seed_ledger()
        snap = pl.resolve_portfolio(_market_data(), db_path=self.db)
        digest = _portfolio_snapshot_hash("601101", snap)
        self.assertTrue(digest)
        self.assertNotEqual(digest, "")

    def test_fail_closed_portfolio_yields_non_empty_hash(self):
        snap = pl._fail_closed_snapshot("db locked")
        digest = _portfolio_snapshot_hash("601101", snap)
        self.assertTrue(
            digest, "fail-closed 也必须产生非空缓存键，否则会复用无组合结论"
        )

    def test_hash_changes_when_ledger_changes(self):
        self.seed_ledger()
        before = _portfolio_snapshot_hash(
            "601101", pl.resolve_portfolio(_market_data(), db_path=self.db)
        )
        ps.add_trade(
            "510300", "buy", 500, 4.10, "2026-07-02 10:05:00", fee=1, db_path=self.db
        )
        after = _portfolio_snapshot_hash(
            "601101", pl.resolve_portfolio(_market_data(), db_path=self.db)
        )
        self.assertNotEqual(before, after)

    def test_unresolvable_portfolio_still_non_empty(self):
        class Hostile:
            def __getattr__(self, _name):
                raise RuntimeError("boom")

        self.assertTrue(_portfolio_snapshot_hash("601101", Hostile()))


# ── 7/8. 入口接线契约 ───────────────────────────────────────


class TestEntryPointWiring(unittest.TestCase):
    def _read(self, name):
        base = ROOT if name.startswith(f"nasdx{os.sep}") else os.path.join(ROOT, "scripts")
        with open(os.path.join(base, name), "r", encoding="utf-8") as fh:
            return fh.read()

    def test_run_analysis_enables_link_by_default(self):
        src = self._read("run_analysis.py")
        self.assertIn("--no-portfolio-link", src)
        self.assertIn("link_portfolio=link_portfolio", src)
        self.assertIn("link_portfolio = not args.no_portfolio_link", src)

    def test_analyze_cli_enables_link_by_default(self):
        src = self._read("analyze.py")
        self.assertIn("--no-portfolio-link", src)
        self.assertIn("link_portfolio=not args.no_portfolio_link", src)

    def test_analyzer_library_default_is_off(self):
        from nasdx.analyzer import NasdxAnalyzer

        self.assertIn("link_portfolio", NasdxAnalyzer.__init__.__code__.co_varnames)
        src = self._read(os.path.join("nasdx", "analyzer.py"))
        self.assertIn("link_portfolio: bool = False", src)

    def test_analyze_batch_resolves_ledger_once(self):
        src = self._read(os.path.join("nasdx", "analyzer.py"))
        batch = src.split("def analyze_batch(", 1)[1]
        self.assertEqual(
            1,
            len(re.findall(r"resolve_portfolio\(", batch)),
            "批量入口只能解析一次账本",
        )
        self.assertIn("link_portfolio=False if resolved_once else None", batch)

    def test_analyze_honours_link_override(self):
        src = self._read(os.path.join("nasdx", "analyzer.py"))
        self.assertIn(
            "should_link = self.link_portfolio if link_portfolio is None "
            "else bool(link_portfolio)",
            src,
        )

    def test_analyzer_passes_industry_to_decision_plan(self):
        src = self._read(os.path.join("nasdx", "analyzer.py"))
        plan_call = src.split("decision_plan = build_decision_plan(", 1)[1].split(")\n", 1)[0]
        self.assertIn("portfolio=portfolio", plan_call)
        self.assertIn("industry=", plan_call)
        self.assertIn("sector_name", plan_call)


class TestIndustryCapReachesNewCandidates(LinkTestBase):
    """未持有的新标的也必须受行业集中度上限约束（#66 验收 #7）。"""

    def _capped_ledger(self):
        """现金为正、未满仓，但煤炭行业敞口已超默认 30% 上限的账本。

        现金基线 5 万，两笔煤炭买入合计成本 1.8 万 -> 现金 3.2 万（为正，
        避免被"现金为负"的 fail-closed 提前拦掉，从而真正测到行业上限）。
        行情价 601101=12.5 / 600123=8.8 -> 煤炭市值 21300，
        总资产 32000+21300=53300，行业敞口 ≈39.96% ≥ 30%。
        """
        ps.set_cash_baseline(50000.0, "2026-07-01", db_path=self.db)
        ps.add_trade(
            "601101", "buy", 1000, 10.0, "2026-07-01 09:35:00", db_path=self.db
        )
        ps.add_trade(
            "600123", "buy", 1000, 8.0, "2026-07-01 09:36:00", db_path=self.db
        )
        return pl.resolve_portfolio(_market_data(), db_path=self.db)

    def test_fixture_is_cash_positive_and_industry_capped(self):
        """夹具前提自检：现金为正、未 fail-closed、煤炭敞口确实超限。"""
        snap = self._capped_ledger().to_dict()
        self.assertFalse(snap["fail_closed"], snap.get("blocking_reasons"))
        self.assertGreater(snap["cash"], 0.0)
        weight = snap["industry_exposure"]["煤炭"] / snap["total_assets"] * 100
        self.assertGreaterEqual(weight, snap["policy"]["industry_cap_pct"])

    def test_new_name_in_capped_industry_is_blocked_when_industry_given(self):
        from nasdx.portfolio_gate import STATUS_NORMAL, evaluate_portfolio_gate

        snap = self._capped_ledger()
        gate = evaluate_portfolio_gate("600999", portfolio=snap, industry="煤炭")
        self.assertFalse(
            gate.allow_new_entry,
            f"行业已达上限仍放行新增：{gate.status} / {gate.reasons}",
        )
        self.assertFalse(gate.allow_add)
        self.assertNotEqual(gate.status, STATUS_NORMAL)
        self.assertTrue(
            any("行业敞口" in reason for reason in gate.reasons), gate.reasons
        )

    def test_without_industry_the_cap_silently_misses(self):
        """回归护栏：不带 industry 时上限对新标的不生效，所以入口必须传。"""
        from nasdx.portfolio_gate import STATUS_NORMAL, evaluate_portfolio_gate

        snap = self._capped_ledger()
        blind = evaluate_portfolio_gate("600999", portfolio=snap)
        aware = evaluate_portfolio_gate("600999", portfolio=snap, industry="煤炭")
        # 未分类 -> 行业维度无从判断 -> 放行；这正是 #66 之前的漏洞形态
        self.assertTrue(blind.allow_new_entry, blind.reasons)
        self.assertEqual(blind.status, STATUS_NORMAL)
        self.assertFalse(
            aware.allow_new_entry,
            "若带 industry 仍放行说明上限失效，需重新评估 analyzer 的传参",
        )

    def test_analyzer_passes_sector_name_into_decision_gate(self):
        """入口契约：analyzer 必须把板块名喂进 build_decision_plan(industry=...)。"""
        source = open(
            os.path.join(ROOT, "nasdx", "analyzer.py"), encoding="utf-8"
        ).read()
        head = source.split("build_decision_plan(", 2)[-1]
        self.assertIn("industry=", head, "analyzer 未向决策层传 industry")
        self.assertIn("sector_name", head, "industry 取值口径应来自行情板块名")


# ── 9. 端到端：CLI 语义下 analyzer 真的看到了账本 ────────────


class TestAnalyzerEndToEndLink(unittest.TestCase):
    """复用 #65 的 fake LLM 夹具，验证生产接线真的把账本喂进了缓存失效链。"""

    def setUp(self):
        from nasdx.llm import LLMClient, reset_llm_counters
        from tests.test_analysis_cache_contracts import DATA, _FakeLLMClient

        self.DATA = DATA
        self.LLMClient = LLMClient
        self.LLMClient._instance = _FakeLLMClient(call_sleep=0)
        reset_llm_counters()

        self.tmp = tempfile.mkdtemp(prefix="nasdx_link_e2e_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cache_dir = os.path.join(self.tmp, "cache")
        self.db = os.path.join(self.tmp, "ledger.db")
        self._prev_db = os.environ.get("NASDX_PORTFOLIO_DB")
        os.environ["NASDX_PORTFOLIO_DB"] = self.db
        self.addCleanup(self._restore_env)

        ps.set_cash_baseline(50000.0, "2026-08-01", db_path=self.db)
        ps.add_trade(
            "600000", "buy", 1000, 10.0, "2026-08-01 09:35:00", fee=5, db_path=self.db
        )

        from nasdx.analyzer import NasdxAnalyzer

        self.analyzer = NasdxAnalyzer(
            depth="full",
            use_cache=True,
            cache_dir=self.cache_dir,
            agent_delay=0,
            battle_delay=0,
            debate_rounds=1,
            link_portfolio=True,
        )

    def _restore_env(self):
        if self._prev_db is None:
            os.environ.pop("NASDX_PORTFOLIO_DB", None)
        else:
            os.environ["NASDX_PORTFOLIO_DB"] = self._prev_db
        self.LLMClient._instance = None

    def _run(self, depth):
        return self.analyzer.analyze("600000", data=self.DATA, verbose=False, depth=depth)

    def test_linked_run_stores_non_empty_portfolio_hash(self):
        from nasdx.analysis_cache import build_identity, load_snapshot

        self._run("full")
        snapshot, _reason = load_snapshot(build_identity("600000"), self.cache_dir)
        self.assertIsNotNone(snapshot)
        digest = snapshot.inputs.get("portfolio_snapshot_hash")
        self.assertTrue(
            digest,
            "接入账本后缓存必须写入非空 portfolio_snapshot_hash（#66 验收 #6）",
        )

    def test_new_fill_invalidates_risk_dimension_on_intraday(self):
        self._run("full")
        clean = self._run("intraday")
        self.assertNotIn("risk", clean.performance["cache_miss_dimensions"])

        # 盘中新成交 -> 账本变化 -> 组合快照哈希变化 -> risk 维度必须重算
        ps.add_trade(
            "601101", "buy", 1000, 12.0, "2026-08-04 10:05:00", fee=5, db_path=self.db
        )
        after = self._run("intraday")
        self.assertIn(
            "risk",
            after.performance["cache_miss_dimensions"],
            "成交后盘中分析必须重算风险维度，不能复用旧组合下的结论",
        )

    def test_unlinked_analyzer_keeps_legacy_behaviour(self):
        from nasdx.analysis_cache import build_identity, load_snapshot
        from nasdx.analyzer import NasdxAnalyzer

        legacy_cache = os.path.join(self.tmp, "legacy")
        legacy = NasdxAnalyzer(
            depth="full",
            use_cache=True,
            cache_dir=legacy_cache,
            agent_delay=0,
            battle_delay=0,
            debate_rounds=1,
        )
        legacy.analyze("600000", data=self.DATA, verbose=False, depth="full")
        snapshot, _reason = load_snapshot(build_identity("600000"), legacy_cache)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.inputs.get("portfolio_snapshot_hash"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ── 10. Streamlit UI 接线契约（#66 收尾：app.py 接入账本）────────────

class TestAppUiPortfolioWiring(unittest.TestCase):
    """回归护栏：app.py 必须把权威账本接到资金仓位换算与展示面板。"""

    def _app_source(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            return fh.read()

    def test_app_imports_portfolio_link_and_table(self):
        src = self._app_source()
        self.assertIn(
            "resolve_portfolio_auto",
            src,
            "app.py 未引用 portfolio_link.resolve_portfolio_auto，UI 路径闸门仍 unknown",
        )
        self.assertIn(
            "_portfolio_snapshot_table",
            src,
            "app.py 未导入 plan_tables.portfolio_snapshot_table，账本持仓面板缺失",
        )

    def test_app_wires_snapshot_into_position_sizing(self):
        src = self._app_source()
        idx = src.find("build_position_sizing(")
        self.assertNotEqual(idx, -1, "app.py 未调用 build_position_sizing")
        head = src[idx: idx + 600]
        self.assertIn("portfolio=", head, "build_position_sizing 调用缺少 portfolio=，账本未参与换算")

    def test_app_defines_ledger_snapshot_loader(self):
        src = self._app_source()
        self.assertIn(
            "def load_ledger_snapshot(",
            src,
            "app.py 缺少 load_ledger_snapshot 缓存读取，账本面板无法安全加载",
        )


class TestPortfolioSnapshotTable(unittest.TestCase):
    """nasdx.ui.plan_tables.portfolio_snapshot_table 渲染契约。"""

    def _render(self, items):
        from nasdx.ui.plan_tables import portfolio_snapshot_table
        return portfolio_snapshot_table(items)

    def test_empty_positions(self):
        out = self._render([])
        self.assertIn("账本内暂无持仓", out)

    def test_renders_positions_and_pnl_color(self):
        items = [
            {
                "code": "600000", "name": "浦发银行", "asset_class": "股票",
                "industry": "银行", "quantity": 1000, "avg_cost": 10.0,
                "last_price": 11.5, "market_value": 11500.0,
                "unrealized_pnl": 1500.0, "valuation_status": "priced",
            },
            {
                "code": "510300", "name": "沪深300ETF", "asset_class": "ETF",
                "industry": "未分类", "quantity": 2000, "avg_cost": 4.0,
                "last_price": None, "market_value": 7600.0,
                "unrealized_pnl": -400.0, "valuation_status": "missing_price",
            },
        ]
        out = self._render(items)
        self.assertIn("浦发银行", out)
        self.assertIn("沪深300ETF", out)
        self.assertIn("#22c55e", out)  # 盈利绿
        self.assertIn("#ef4444", out)  # 亏损红
        self.assertIn("缺价", out)
        self.assertIn("已估值", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
