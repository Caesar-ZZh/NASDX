# -*- coding: utf-8 -*-
"""#65 分层缓存与盘中增量分析路径契约测试。

覆盖：
1. 缓存命中（load_snapshot -> HIT；plan_reuse 全维度可复用）。
2. 局部失效：price / sector / fundamental 指纹变化只命中依赖它的维度。
3. 完整失效：trading_day 变化使所有维度失效；过期(TTL)同样全失效。
4. 损坏 / schema 版本不匹配 / 身份(模型)不匹配的快照绝不静默复用。
5. 盘中增量路径：full 后第二次 intraday 同指纹 -> ≤1 次 LLM 调用，
   墙钟时间较 full 减少 ≥70%，且复用维度标注 reused 而非 refreshed。
6. 模型 / Prompt 版本触发：identity 变更 -> 文件不同 -> 回退 full。
7. 持仓变更触发：portfolio_snapshot_hash 变化 -> 仅 risk 维度失效并重算。
8. 并发投票：5 位投票者并发，结果按 VOTERS 顺序重排；单票失败只降级该票。
9. 调用计数与阶段耗时：防后续无意恢复 14+ 次全量调用。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nasdx.analysis_cache import (  # noqa: E402
    AnalysisSnapshot,
    DEPTH_FULL,
    DEPTH_INTRADAY,
    HIT,
    INTRADAY_REFRESHABLE,
    MISS_CORRUPT,
    MISS_MISSING,
    MISS_SCHEMA,
    RESEARCH_DIMENSIONS,
    AnalysisCacheError,
    build_identity,
    build_invalidation_inputs,
    clear_snapshots,
    dimension_payload,
    load_snapshot,
    normalize_depth,
    plan_reuse,
    save_snapshot,
    snapshot_path,
    utc_now_iso,
)
from nasdx.analyzer import NasdxAnalyzer  # noqa: E402
from nasdx.environments.battle import VOTERS, BattleEnvironment  # noqa: E402
from nasdx.llm import LLMClient, reset_llm_counters  # noqa: E402
from nasdx.schema import AnalysisResult  # noqa: E402

STOCK = {
    "code": "600000",
    "name": "浦发银行",
    "industry": "银行",
    "sector_name": "银行",
    "close": 10.50, "open": 10.40, "high": 10.60, "low": 10.30,
    "pre_close": 10.40, "change": 0.10, "change_pct": 0.95,
    "volume": 12345678, "amount": 1.2e9, "turnover": 1.2, "turnover_rate": 1.3,
    "ma5": 10.40, "ma10": 10.30, "ma20": 10.20, "ma60": 10.00,
    "macd": 0.05, "macd_bar": 0.02, "rsi": 55.0, "kdj_k": 60.0, "kdj_d": 55.0,
    "boll_upper": 10.80, "boll_lower": 9.80,
    "main_net_inflow": 5.0e8, "vol_ratio": 1.2, "bid": 10.50, "ask": 10.51,
    "fund_flow": [
        {"日期": "2026-08-01", "收盘价": 10.40, "涨跌幅": 0.50,
         "主力净流入-净额": 5.0e8, "主力净流入-净占比": 6.0, "超大单净流入-净额": 3.0e8},
        {"日期": "2026-08-02", "收盘价": 10.45, "涨跌幅": 0.48,
         "主力净流入-净额": 4.0e8, "主力净流入-净占比": 5.0, "超大单净流入-净额": 2.5e8},
        {"日期": "2026-08-03", "收盘价": 10.48, "涨跌幅": 0.29,
         "主力净流入-净额": 6.0e8, "主力净流入-净占比": 7.0, "超大单净流入-净额": 3.5e8},
        {"日期": "2026-08-04", "收盘价": 10.50, "涨跌幅": 0.19,
         "主力净流入-净额": 5.5e8, "主力净流入-净占比": 6.5, "超大单净流入-净额": 3.0e8},
    ],
    "indicators": {
        "close": 10.50, "ma5": 10.40, "ma20": 10.20, "rsi": 55.0,
        "macd_bar": 0.02, "vol_ratio": 1.2,
    },
    "pe": 5.5, "pb": 0.6, "market_cap": 3.0e11,
    "concepts": ["银行", "红利"], "tags": ["低估值"],
}

STOCK_PRICE = {**STOCK, "close": 11.20, "change_pct": 6.0}
# 仅改 sector_name（属 sector 指纹），不改 industry（属 fundamental 指纹），
# 以隔离"板块变化只失效 sector 维度"的断言。
STOCK_SECTOR = {**STOCK, "sector_name": "保险"}
STOCK_FUND = {**STOCK, "name": "浦发银行A", "pe": 6.0, "market_cap": 3.5e11,
              "concepts": ["银行", "红利", "破净"]}

DATA = {
    "date": "20260804",
    "generated_at": "2026-08-04T09:30:00",
    "sectors": [{"name": "银行", "stocks": [STOCK]}],
}

_TRADING_DAY = "20260804"


def _portfolio(snapshot_hash: str) -> dict:
    """Minimal portfolio mapping producing a deterministic gate snapshot_hash."""
    return {
        "snapshot_hash": snapshot_hash,
        "fail_closed": False,
        "policy": {"single_name_cap_pct": 10.0, "industry_cap_pct": 30.0},
        "total_assets": 100000.0,
        "cash": 50000.0,
        "positions": [],
    }


def _result(dim: str, signal: str = "bullish", confidence: float = 0.7) -> AnalysisResult:
    return AnalysisResult(
        agent_name=dim,
        dimension=dim,
        conclusion=f"{dim} 结论",
        signal=signal,
        confidence=confidence,
        key_points=[f"{dim} 要点"],
    )


def _research_map() -> dict:
    return {dim: _result(dim) for dim in RESEARCH_DIMENSIONS}


def _make_snapshot(identity, inputs, now, dims=None):
    dims = dims or RESEARCH_DIMENSIONS
    dimensions = {
        d: dimension_payload(_result(d), inputs, now=now) for d in dims
    }
    return AnalysisSnapshot(
        identity=identity,
        created_at=utc_now_iso(now),
        data_as_of=_TRADING_DAY,
        inputs=dict(inputs),
        dimensions=dimensions,
        battle={
            "refreshed_at": utc_now_iso(now),
            "transcript": ["多头发言", "空头发言"],
            "votes": [],
            "bullish_pct": 80.0,
        },
        synthesis=dimension_payload(_result("synthesis"), inputs, now=now),
    )


class _FakeLLMClient:
    """Deterministic fake routed through the shared ``llm`` proxy.

    Its ``ask`` returns a string that satisfies both the structured-output
    contract (a ```json``` block) and the voter parser (a ``投票：bullish`` line),
    so it drives the entire pipeline without a network. An optional per-call
    sleep creates a measurable wall-clock gap between full and incremental runs,
    and ``fail_voter`` lets a single voter raise to exercise failure isolation.
    """

    def __init__(self, call_sleep: float = 0.012, fail_voter: str | None = None):
        self.call_sleep = call_sleep
        self.fail_voter = fail_voter
        self.ask_calls = 0

    @staticmethod
    def _payload() -> str:
        return (
            "【最终信号】bullish\n【置信度】0.72\n"
            "投票：bullish\n理由：多维度共振\n\n"
            "```json\n"
            "{\n"
            '  "signal": "bullish",\n'
            '  "confidence": 0.72,\n'
            '  "conclusion": "技术面与资金面共振，短期看多。",\n'
            '  "key_points": ["均线多头", "主力净流入"]\n'
            "}\n"
            "```"
        )

    def _maybe_sleep(self) -> None:
        if self.call_sleep:
            time.sleep(self.call_sleep)

    def ask(self, messages, system=None, **kwargs):
        self.ask_calls += 1
        if self.fail_voter and system and self.fail_voter in system:
            raise RuntimeError("injected vote failure")
        self._maybe_sleep()
        return self._payload()

    def ask_json(self, messages, system=None, **kwargs):
        self.ask_calls += 1
        self._maybe_sleep()
        return {"signal": "bullish", "confidence": 0.72,
                "conclusion": "综合看多。", "key_points": ["多维度共振"]}


# ---------------------------------------------------------------------------
# 1~4. 缓存基底契约（纯函数，不跑 LLM）
# ---------------------------------------------------------------------------


class TestAnalysisCacheContract(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.mkdtemp(prefix="nasdx_cache_test_")
        self.addCleanup(shutil.rmtree, self.cache_dir, True)
        self.identity = build_identity("600000")
        self.inputs = build_invalidation_inputs(STOCK, trading_day=_TRADING_DAY)
        self.now = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)
        save_snapshot(_make_snapshot(self.identity, self.inputs, self.now), self.cache_dir)

    def test_cache_hit_with_all_dimensions_reusable(self):
        loaded, reason = load_snapshot(self.identity, self.cache_dir)
        self.assertEqual(reason, HIT)
        plan = plan_reuse(loaded, self.inputs, now=self.now)
        self.assertEqual(plan.hit_dimensions, tuple(RESEARCH_DIMENSIONS))
        self.assertEqual(plan.miss_dimensions, ())

    def test_missing_snapshot_is_explicit_miss(self):
        other = build_identity("000001")
        loaded, reason = load_snapshot(other, self.cache_dir)
        self.assertIsNone(loaded)
        self.assertEqual(reason, MISS_MISSING)

    def test_corrupt_snapshot_never_silently_reused(self):
        path = snapshot_path(self.identity, self.cache_dir)
        path.write_text("{ this is not valid json", encoding="utf-8")
        loaded, reason = load_snapshot(self.identity, self.cache_dir)
        self.assertIsNone(loaded)
        self.assertEqual(reason, MISS_CORRUPT)

    def test_schema_version_mismatch_is_explicit_miss(self):
        path = snapshot_path(self.identity, self.cache_dir)
        payload = _make_snapshot(self.identity, self.inputs, self.now).to_dict()
        payload["cache_schema_version"] = "999"
        path.write_text(__import__("json").dumps(payload), encoding="utf-8")
        loaded, reason = load_snapshot(self.identity, self.cache_dir)
        self.assertIsNone(loaded)
        self.assertEqual(reason, MISS_SCHEMA)

    def test_price_change_invalidates_price_dependent_dimensions_only(self):
        loaded, reason = load_snapshot(self.identity, self.cache_dir)
        self.assertEqual(reason, HIT)
        inputs2 = build_invalidation_inputs(STOCK_PRICE, trading_day=_TRADING_DAY)
        plan = plan_reuse(loaded, inputs2, now=self.now)
        # technical / fund_flow / risk 依赖 price_fingerprint；sector / chokepoint 不受影响。
        self.assertEqual(set(plan.miss_dimensions), {"technical", "fund_flow", "risk"})
        self.assertEqual(set(plan.hit_dimensions), {"sector", "chokepoint"})

    def test_sector_change_invalidates_only_sector_dimension(self):
        loaded, _ = load_snapshot(self.identity, self.cache_dir)
        inputs2 = build_invalidation_inputs(STOCK_SECTOR, trading_day=_TRADING_DAY)
        plan = plan_reuse(loaded, inputs2, now=self.now)
        self.assertEqual(plan.miss_dimensions, ("sector",))
        self.assertEqual(set(plan.hit_dimensions), {"technical", "fund_flow", "risk", "chokepoint"})

    def test_fundamental_change_invalidates_only_chokepoint_dimension(self):
        loaded, _ = load_snapshot(self.identity, self.cache_dir)
        inputs2 = build_invalidation_inputs(STOCK_FUND, trading_day=_TRADING_DAY)
        plan = plan_reuse(loaded, inputs2, now=self.now)
        self.assertEqual(plan.miss_dimensions, ("chokepoint",))
        self.assertEqual(set(plan.hit_dimensions), {"technical", "fund_flow", "risk", "sector"})

    def test_trading_day_change_invalidates_every_dimension(self):
        loaded, _ = load_snapshot(self.identity, self.cache_dir)
        inputs2 = build_invalidation_inputs(STOCK, trading_day="20260805")
        plan = plan_reuse(loaded, inputs2, now=self.now)
        self.assertEqual(plan.miss_dimensions, tuple(RESEARCH_DIMENSIONS))

    def test_ttl_expiry_invalidates_every_dimension(self):
        loaded, _ = load_snapshot(self.identity, self.cache_dir)
        future = self.now + timedelta(hours=10)  # 超过最大 TTL(14400s)
        plan = plan_reuse(loaded, self.inputs, now=future)
        self.assertEqual(plan.miss_dimensions, tuple(RESEARCH_DIMENSIONS))

    def test_model_change_yields_different_identity_file(self):
        id_default = build_identity("600000", model="deepseek-chat")
        id_other = build_identity("600000", model="gpt-4o-mini")
        self.assertNotEqual(id_default.key, id_other.key)
        # 缓存只落在默认模型文件上，换模型后读取应为 miss。
        loaded, reason = load_snapshot(id_other, self.cache_dir)
        self.assertIsNone(loaded)
        self.assertEqual(reason, MISS_MISSING)

    def test_clear_snapshots_removes_files(self):
        self.assertEqual(clear_snapshots(cache_dir=self.cache_dir), 1)
        loaded, reason = load_snapshot(self.identity, self.cache_dir)
        self.assertIsNone(loaded)
        self.assertEqual(reason, MISS_MISSING)


# ---------------------------------------------------------------------------
# 5~9. 盘中增量路径 + 并发投票（跑真实 analyzer / battle，注入 fake LLM）
# ---------------------------------------------------------------------------


class TestIntradayIncrementalPerformance(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeLLMClient(call_sleep=0.012)
        LLMClient._instance = self.fake
        reset_llm_counters()
        self.cache_dir = tempfile.mkdtemp(prefix="nasdx_intraday_test_")
        self.addCleanup(shutil.rmtree, self.cache_dir, True)
        clear_snapshots(cache_dir=self.cache_dir)
        # 启用并发投票，覆盖"5 个独立投票者并发"验收项。
        os.environ["NASDX_VOTE_MAX_WORKERS"] = "5"
        self.analyzer = NasdxAnalyzer(
            depth="full",
            use_cache=True,
            cache_dir=self.cache_dir,
            agent_delay=0,
            battle_delay=0,
            debate_rounds=2,
        )

    def tearDown(self):
        LLMClient._instance = None
        os.environ.pop("NASDX_VOTE_MAX_WORKERS", None)

    def _full(self):
        reset_llm_counters()
        return self.analyzer.analyze("600000", data=DATA, verbose=False, depth="full")

    def _intraday(self, portfolio=None):
        reset_llm_counters()
        return self.analyzer.analyze(
            "600000", data=DATA, verbose=False, depth="intraday", portfolio=portfolio
        )

    def test_full_run_makes_full_set_of_llm_calls(self):
        report = self._full()
        # research(5) + battle(2*rounds + 1 judge + 5 votes) + synthesis(1) = 16
        self.assertEqual(report.performance["llm_call_count"], 16)
        self.assertEqual(report.analysis_depth, "full")

    def test_second_intraday_run_makes_at_most_one_llm_call(self):
        self._full()
        r1 = self._intraday()
        self.assertLessEqual(r1.performance["llm_call_count"], 1)
        self.assertEqual(r1.analysis_depth, "intraday")
        # 复用维度必须显式标注 reused，不得伪装成 refreshed。
        for dim in RESEARCH_DIMENSIONS:
            self.assertEqual(r1.freshness[dim]["status"], "reused")
        self.assertEqual(r1.freshness["battle"]["status"], "reused")
        r2 = self._intraday()
        self.assertLessEqual(r2.performance["llm_call_count"], 1)

    def test_intraday_wall_clock_reduction_at_least_70pct(self):
        full = self._full()
        full_ms = full.performance["total_elapsed_ms"]
        intra = self._intraday()
        intra2 = self._intraday()
        self.assertGreater(full_ms, 0)
        # 第二次 intraday 相对 full 减少 ≥70%（即 ≤30%）。
        self.assertLessEqual(intra2.performance["total_elapsed_ms"], 0.3 * full_ms)
        # 第一次也应显著更快。
        self.assertLessEqual(intra.performance["total_elapsed_ms"], 0.3 * full_ms)

    def test_intraday_does_not_regress_to_full_call_count(self):
        self._full()
        intra = self._intraday()
        self.assertLess(intra.performance["llm_call_count"], 16)

    def test_model_change_triggers_full_rerun_on_intraday(self):
        self._full()
        os.environ["NASDX_MODEL"] = "gpt-4o-mini"
        try:
            reset_llm_counters()
            report = self.analyzer.analyze("600000", data=DATA, verbose=False, depth="intraday")
        finally:
            os.environ.pop("NASDX_MODEL", None)
        # 不同模型 -> 不同 identity 文件 -> 缓存 miss -> 安全回退 full。
        self.assertEqual(report.performance["llm_call_count"], 16)
        self.assertEqual(report.analysis_depth, "full")

    def test_portfolio_change_recomputes_risk_only(self):
        self._full()
        report = self._intraday(portfolio=_portfolio("hash-A"))
        # risk 依赖 portfolio_snapshot_hash -> 失效并重算；其余复用，辩论复用。
        self.assertGreaterEqual(report.performance["llm_call_count"], 1)
        self.assertLess(report.performance["llm_call_count"], 16)
        self.assertIn("risk", report.performance["cache_miss_dimensions"])
        self.assertEqual(report.freshness["risk"]["status"], "refreshed")
        self.assertEqual(report.freshness["battle"]["status"], "reused")
        # 不同持仓快照应产生不同的缓存失效输入。
        self.assertNotEqual(
            build_invalidation_inputs(STOCK, trading_day=_TRADING_DAY)["portfolio_snapshot_hash"],
            build_invalidation_inputs(STOCK, trading_day=_TRADING_DAY,
                                      portfolio_snapshot_hash="hash-A")["portfolio_snapshot_hash"],
        )


class TestConcurrentVoting(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeLLMClient()
        LLMClient._instance = self.fake
        reset_llm_counters()
        os.environ["NASDX_VOTE_MAX_WORKERS"] = "5"

    def tearDown(self):
        LLMClient._instance = None
        os.environ.pop("NASDX_VOTE_MAX_WORKERS", None)

    def test_concurrent_votes_preserve_voters_order(self):
        env = BattleEnvironment(debate_rounds=1, vote_workers=5, delay=0)
        transcript, votes, bullish_pct = env.run(
            "600000", "浦发银行", _research_map(), verbose=False
        )
        self.assertEqual([v.agent_name for v in votes], [name for name, _ in VOTERS])
        self.assertEqual(len(votes), len(VOTERS))
        self.assertEqual(bullish_pct, 100.0)
        self.assertTrue(all(v.vote == "bullish" for v in votes))

    def test_single_vote_failure_is_isolated(self):
        failing = _FakeLLMClient(fail_voter="风险控制官")
        LLMClient._instance = failing
        env = BattleEnvironment(debate_rounds=1, vote_workers=5, delay=0)
        transcript, votes, bullish_pct = env.run(
            "600000", "浦发银行", _research_map(), verbose=False
        )
        # 顺序与数量仍稳定。
        self.assertEqual([v.agent_name for v in votes], [name for name, _ in VOTERS])
        risk = next(v for v in votes if v.agent_name == "风险控制官")
        self.assertEqual(risk.vote, "neutral")
        self.assertIn("投票失败", risk.reasoning)
        # 其余投票不受影响。
        others = [v for v in votes if v.agent_name != "风险控制官"]
        self.assertTrue(all(v.vote == "bullish" for v in others))
        self.assertEqual(bullish_pct, 80.0)  # 4/5 看多


if __name__ == "__main__":
    unittest.main(verbosity=2)
