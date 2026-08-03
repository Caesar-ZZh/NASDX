"""
主分析器 — 协调 Research 环境和 Battle 环境
对应 FinGenius 的 EnhancedFinGeniusAnalyzer

执行深度（#65）
---------------
* ``full``     — 完整 Research → Battle → Synthesis，历史行为，默认值。
* ``intraday`` — 复用缓存中的慢变量结论，只刷新失效的行情类维度；不跑辩论，
  最多 1 次综合调用。缓存缺失/损坏/慢维度未缓存时自动回退 ``full``。
* ``refresh``  — 只重跑被失效规则命中的维度；有维度重跑才重跑辩论与综合。

任何一次复用都会在报告的 ``freshness`` 中标注"复用 + 上次刷新时间"，
不会把旧结论伪装成刚刚重新分析。
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from nasdx.schema import AnalysisResult, BattleVote, FinalReport
from nasdx.data_loader import load_latest_data, get_stock_data, get_market_overview
from nasdx.environments.research import ResearchEnvironment
from nasdx.environments.battle import BattleEnvironment
from nasdx.agents.synthesis import SynthesisAgent
from nasdx.analysis_cache import (
    DEPTH_FULL,
    DEPTH_INTRADAY,
    DEPTH_REFRESH,
    INTRADAY_REFRESHABLE,
    RESEARCH_DIMENSIONS,
    AnalysisCacheError,
    AnalysisSnapshot,
    build_identity,
    build_invalidation_inputs,
    dimension_payload,
    load_snapshot,
    normalize_depth,
    plan_reuse,
    save_snapshot,
    utc_now_iso,
)
from nasdx.data_quality import assess_data_quality
from nasdx.decision import build_decision_plan, format_decision_plan
from nasdx.history_store import record_report_history
from nasdx.llm import LLMCallMeter
from nasdx.paths import get_reports_dir
from nasdx.report import generate_html_report


def _restore_result(payload: Any) -> Optional[AnalysisResult]:
    """Rebuild an ``AnalysisResult`` from a cached dimension payload."""
    if not isinstance(payload, Mapping):
        return None
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return None
    try:
        return AnalysisResult(**dict(result))
    except Exception:
        return None


def _restore_votes(payload: Any) -> List[BattleVote]:
    votes: List[BattleVote] = []
    if not isinstance(payload, (list, tuple)):
        return votes
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        try:
            votes.append(BattleVote(**dict(item)))
        except Exception:
            continue
    return votes


def _dump(model: Any) -> Dict[str, Any]:
    dumper = getattr(model, "model_dump", None) or getattr(model, "dict", None)
    return dict(dumper()) if callable(dumper) else dict(model or {})


def _bullish_pct_from_votes(votes: List[BattleVote]) -> float:
    if not votes:
        return 50.0
    bullish = sum(1 for v in votes if v.vote == "bullish")
    return bullish / len(votes) * 100.0


def _portfolio_snapshot_hash(stock_code: str, portfolio: Any) -> str:
    """Derive the portfolio invalidation input; empty when no ledger is linked."""
    if portfolio is None:
        return ""
    try:
        from nasdx.portfolio_gate import evaluate_portfolio_gate

        return str(evaluate_portfolio_gate(stock_code, portfolio).snapshot_hash or "")
    except Exception:
        return ""


class NasdxAnalyzer:
    """
    NASDX 核心分析器

    三阶段管道：
    1. Research Phase — 5个专家 Agent 各维度分析
    2. Battle Phase   — 多空辩论 + 投票
    3. Synthesis & Report — 综合研判 + HTML 报告
    """

    def __init__(
        self,
        max_steps: int = 3,
        debate_rounds: int = 2,
        agent_delay: float = 1.0,
        battle_delay: float = 0.5,
        output_dir: Optional[str] = None,
        risk_profile: str = "balanced",
        depth: str = DEPTH_FULL,
        use_cache: bool = True,
        cache_dir: Optional[str] = None,
    ):
        self.max_steps = max_steps
        self.debate_rounds = debate_rounds
        self.risk_profile = risk_profile
        self.depth = normalize_depth(depth)
        self.use_cache = bool(use_cache)
        self.cache_dir = cache_dir
        self.output_dir = Path(output_dir) if output_dir else get_reports_dir(create=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 初始化环境
        self.research_env = ResearchEnvironment(
            max_steps=max_steps,
            delay=agent_delay,
        )
        self.battle_env = BattleEnvironment(
            debate_rounds=debate_rounds,
            delay=battle_delay,
        )
        self.synthesis_agent = SynthesisAgent(max_steps=max_steps)

    def analyze(
        self,
        stock_code: str,
        data: Optional[Dict[str, Any]] = None,
        verbose: bool = True,
        depth: Optional[str] = None,
        portfolio: Any = None,
    ) -> FinalReport:
        """
        分析单只股票，返回完整报告

        Args:
            stock_code: 股票代码，如 "000001"
            data: 预加载的完整数据（None 则自动加载最新文件）
            verbose: 是否打印进度
            depth: full / intraday / refresh，缺省沿用构造参数
            portfolio: 可选持仓快照，参与组合闸门与缓存失效
        """
        started = time.perf_counter()
        meter = LLMCallMeter()
        phase_ms: Dict[str, float] = {}
        requested_depth = normalize_depth(depth if depth is not None else self.depth)

        # 1. 加载数据
        if data is None:
            data = load_latest_data()
        data_quality = assess_data_quality(data)

        stock_data = get_stock_data(data, stock_code)
        if not stock_data:
            raise ValueError(f"股票 {stock_code} 不在监控池中，请检查代码")

        stock_name = stock_data.get("name", stock_code)
        date_str = data.get("date", datetime.now().strftime("%Y%m%d"))

        # 2. 缓存身份与失效输入
        identity = build_identity(stock_code)
        inputs = build_invalidation_inputs(
            stock_data,
            risk_profile=self.risk_profile,
            portfolio_snapshot_hash=_portfolio_snapshot_hash(stock_code, portfolio),
            trading_day=str(date_str),
        )

        snapshot: Optional[AnalysisSnapshot] = None
        snapshot_reason = "cache_disabled" if not self.use_cache else "full_depth"
        if self.use_cache and requested_depth != DEPTH_FULL:
            snapshot, snapshot_reason = load_snapshot(identity, self.cache_dir)

        plan = plan_reuse(
            snapshot,
            inputs,
            snapshot_reason=snapshot_reason,
            dimensions=RESEARCH_DIMENSIONS,
        )

        effective_depth = requested_depth
        degraded_reason = ""

        reused_results: Dict[str, AnalysisResult] = {}
        if snapshot is not None and requested_depth != DEPTH_FULL:
            for dim in plan.hit_dimensions:
                restored = _restore_result(snapshot.dimensions.get(dim))
                if restored is not None:
                    reused_results[dim] = restored

        to_run = [dim for dim in RESEARCH_DIMENSIONS if dim not in reused_results]

        if effective_depth == DEPTH_INTRADAY:
            slow_missing = [d for d in to_run if d not in INTRADAY_REFRESHABLE]
            if slow_missing:
                # 盘中模式不允许重跑慢变量；缓存不足时安全回退完整模式。
                effective_depth = DEPTH_FULL
                degraded_reason = "intraday_needs_cached:" + ",".join(slow_missing)

        cached_battle = dict(snapshot.battle) if snapshot else {}
        cached_transcript = [str(x) for x in (cached_battle.get("transcript") or [])]
        cached_votes = _restore_votes(cached_battle.get("votes"))

        if effective_depth == DEPTH_FULL:
            run_battle = True
        elif effective_depth == DEPTH_INTRADAY:
            run_battle = False
        else:  # refresh：有维度失效才重开辩论
            run_battle = bool(to_run)

        if not run_battle and not cached_votes and not cached_transcript:
            if effective_depth == DEPTH_INTRADAY:
                effective_depth = DEPTH_FULL
                degraded_reason = degraded_reason or "battle_cache_missing"
            run_battle = True

        if effective_depth == DEPTH_FULL:
            reused_results = {}
            to_run = list(RESEARCH_DIMENSIONS)

        if verbose:
            print(f"\n{'='*60}")
            print(f"🚀 NASDX 分析启动：{stock_code} {stock_name}")
            print(f"   数据日期：{date_str}  辩论轮次：{self.debate_rounds}")
            print(f"   执行深度：{requested_depth}"
                  + (f" → {effective_depth}（{degraded_reason}）" if degraded_reason else ""))
            if reused_results:
                print(f"   缓存复用：{', '.join(sorted(reused_results))}")
            print(f"{'='*60}")

        # 3. Research Phase（只跑失效维度）
        phase_start = time.perf_counter()
        fresh_results: Dict[str, AnalysisResult] = {}
        if to_run:
            if verbose:
                print("\n📊 [Phase 1] 研究阶段 — 专家分析")
            fresh_results = self.research_env.run(
                stock_code, stock_data, verbose=verbose, dimensions=to_run
            )
        elif verbose:
            print("\n📊 [Phase 1] 研究阶段 — 全部维度命中缓存，跳过")
        phase_ms["research"] = (time.perf_counter() - phase_start) * 1000

        research_results: Dict[str, AnalysisResult] = {}
        for dim in RESEARCH_DIMENSIONS:
            if dim in fresh_results and fresh_results[dim] is not None:
                research_results[dim] = fresh_results[dim]
            elif dim in reused_results:
                research_results[dim] = reused_results[dim]

        # 4. Battle Phase
        phase_start = time.perf_counter()
        if run_battle:
            if verbose:
                print("\n⚔️  [Phase 2] 辩论阶段 — 多空博弈")
            transcript, votes, bullish_pct = self.battle_env.run(
                stock_code=stock_code,
                stock_name=stock_name,
                research_results=research_results,
                verbose=verbose,
            )
            battle_source = "refreshed"
        else:
            transcript = list(cached_transcript)
            votes = list(cached_votes)
            cached_pct = cached_battle.get("bullish_pct")
            bullish_pct = (
                float(cached_pct)
                if isinstance(cached_pct, (int, float))
                else _bullish_pct_from_votes(votes)
            )
            battle_source = "reused"
            if verbose:
                print("\n⚔️  [Phase 2] 辩论阶段 — 复用缓存结论（未重新辩论）")
        phase_ms["battle"] = (time.perf_counter() - phase_start) * 1000

        # 5. Synthesis
        phase_start = time.perf_counter()
        synthesis: Optional[AnalysisResult] = None
        synthesis_source = "refreshed"
        need_synthesis = run_battle or bool(to_run) or effective_depth == DEPTH_FULL
        if not need_synthesis and snapshot is not None:
            synthesis = _restore_result(snapshot.synthesis)
            if synthesis is not None:
                synthesis_source = "reused"
        if synthesis is None:
            if verbose:
                print("\n🎯 [Phase 3] 综合研判...")
            synthesis = self.synthesis_agent.synthesize(
                stock_code=stock_code,
                stock_name=stock_name,
                analysis_results=research_results,
                battle_transcript=transcript,
            )
            synthesis_source = "refreshed"
        elif verbose:
            print("\n🎯 [Phase 3] 综合研判 — 复用缓存结论")
        phase_ms["synthesis"] = (time.perf_counter() - phase_start) * 1000

        # 6. 构建最终信号
        if bullish_pct >= 60:
            final_signal = "bullish"
        elif bullish_pct <= 40:
            final_signal = "bearish"
        else:
            final_signal = "neutral"

        decision_plan = build_decision_plan(
            stock_code=stock_code,
            stock_name=stock_name,
            final_signal=final_signal,
            bullish_pct=bullish_pct,
            research_results=research_results,
            synthesis=synthesis,
            risk_profile=self.risk_profile,
            data_quality=data_quality,
            portfolio=portfolio,
        )

        now_dt = datetime.now(timezone.utc)
        now_iso = utc_now_iso(now_dt)
        freshness = self._build_freshness(
            research_results=research_results,
            fresh_results=fresh_results,
            plan=plan,
            now_iso=now_iso,
            battle_source=battle_source,
            battle_refreshed_at=(
                now_iso if battle_source == "refreshed" else str(cached_battle.get("refreshed_at", ""))
            ),
            synthesis_source=synthesis_source,
            synthesis_refreshed_at=(
                now_iso
                if synthesis_source == "refreshed"
                else str((snapshot.synthesis or {}).get("refreshed_at", "") if snapshot else "")
            ),
        )

        meter.stop()
        performance = {
            "requested_depth": requested_depth,
            "effective_depth": effective_depth,
            "degraded_reason": degraded_reason,
            "total_elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "phase_elapsed_ms": {k: round(v, 1) for k, v in phase_ms.items()},
            "llm_call_count": meter.calls,
            "llm_failed_call_count": meter.failures,
            "cache_hit_dimensions": sorted(reused_results),
            "cache_miss_dimensions": sorted(to_run),
            "cache_snapshot_reason": plan.snapshot_reason,
            "snapshot_data_as_of": str(snapshot.data_as_of) if snapshot else "",
            "snapshot_created_at": str(snapshot.created_at) if snapshot else "",
            "battle_source": battle_source,
            "synthesis_source": synthesis_source,
            "model": identity.model,
            "provider": identity.provider,
        }

        # 7. 组装报告
        report = FinalReport(
            stock_code=stock_code,
            stock_name=stock_name,
            date=date_str,
            research_results=dict(research_results),
            battle_transcript=transcript,
            votes=votes,
            final_signal=final_signal,
            bullish_pct=bullish_pct,
            summary=synthesis.conclusion,
            operation_advice=format_decision_plan(decision_plan),
            decision_plan=decision_plan,
            data_quality=data_quality,
            analysis_depth=effective_depth,
            freshness=freshness,
            performance=performance,
        )

        # 8. 写回快照（复用维度保留原始刷新时间，避免"假新鲜"）
        if self.use_cache:
            self._persist_snapshot(
                identity=identity,
                previous=snapshot,
                inputs=inputs,
                data_as_of=str(date_str),
                research_results=research_results,
                fresh_dimensions=set(fresh_results),
                now_dt=now_dt,
                transcript=transcript,
                votes=votes,
                bullish_pct=bullish_pct,
                battle_source=battle_source,
                cached_battle=cached_battle,
                synthesis=synthesis,
                synthesis_source=synthesis_source,
                performance=performance,
                verbose=verbose,
            )

        if verbose:
            signal_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(final_signal, "")
            print(f"\n{'='*60}")
            print(f"{signal_emoji} 最终信号：{final_signal}  看多占比：{bullish_pct:.1f}%")
            print(
                f"   深度 {effective_depth} · LLM 调用 {performance['llm_call_count']} 次 · "
                f"耗时 {performance['total_elapsed_ms']:.0f}ms"
            )
            print(f"{'='*60}")

        return report

    # ------------------------------------------------------------------
    # cache helpers
    # ------------------------------------------------------------------
    def _build_freshness(
        self,
        *,
        research_results: Dict[str, AnalysisResult],
        fresh_results: Dict[str, AnalysisResult],
        plan: Any,
        now_iso: str,
        battle_source: str,
        battle_refreshed_at: str,
        synthesis_source: str,
        synthesis_refreshed_at: str,
    ) -> Dict[str, Any]:
        freshness: Dict[str, Any] = {}
        for dim in research_results:
            decision = plan.decisions.get(dim)
            if dim in fresh_results:
                freshness[dim] = {
                    "status": "refreshed",
                    "refreshed_at": now_iso,
                    "age_seconds": 0.0,
                    "ttl_seconds": getattr(decision, "ttl_seconds", None),
                    "reason": getattr(decision, "reason", "forced"),
                }
            else:
                freshness[dim] = {
                    "status": "reused",
                    "refreshed_at": getattr(decision, "refreshed_at", ""),
                    "age_seconds": getattr(decision, "age_seconds", None),
                    "ttl_seconds": getattr(decision, "ttl_seconds", None),
                    "reason": "cache_hit",
                }
        freshness["battle"] = {
            "status": battle_source,
            "refreshed_at": battle_refreshed_at,
            "reason": "battle_rerun" if battle_source == "refreshed" else "cache_hit",
        }
        freshness["synthesis"] = {
            "status": synthesis_source,
            "refreshed_at": synthesis_refreshed_at,
            "reason": "synthesis_rerun" if synthesis_source == "refreshed" else "cache_hit",
        }
        return freshness

    def _persist_snapshot(
        self,
        *,
        identity: Any,
        previous: Optional[AnalysisSnapshot],
        inputs: Dict[str, str],
        data_as_of: str,
        research_results: Dict[str, AnalysisResult],
        fresh_dimensions: set,
        now_dt: datetime,
        transcript: List[str],
        votes: List[BattleVote],
        bullish_pct: float,
        battle_source: str,
        cached_battle: Dict[str, Any],
        synthesis: AnalysisResult,
        synthesis_source: str,
        performance: Dict[str, Any],
        verbose: bool,
    ) -> None:
        dimensions: Dict[str, Dict[str, Any]] = {}
        for dim, result in research_results.items():
            if dim in fresh_dimensions:
                dimensions[dim] = dimension_payload(result, inputs, now=now_dt)
            elif previous is not None and isinstance(previous.dimensions.get(dim), Mapping):
                # 复用维度保留原始 refreshed_at / inputs，TTL 才会真正到期。
                dimensions[dim] = dict(previous.dimensions[dim])

        if battle_source == "refreshed":
            battle_payload = {
                "refreshed_at": utc_now_iso(now_dt),
                "transcript": list(transcript),
                "votes": [_dump(v) for v in votes],
                "bullish_pct": float(bullish_pct),
            }
        else:
            battle_payload = dict(cached_battle)

        if synthesis_source == "refreshed":
            synthesis_payload = dimension_payload(synthesis, inputs, now=now_dt)
        else:
            synthesis_payload = dict(previous.synthesis) if previous else {}

        snapshot = AnalysisSnapshot(
            identity=identity,
            created_at=utc_now_iso(now_dt),
            data_as_of=data_as_of,
            inputs=dict(inputs),
            dimensions=dimensions,
            battle=battle_payload,
            synthesis=synthesis_payload,
            stats=dict(performance),
        )
        try:
            save_snapshot(snapshot, self.cache_dir)
        except (AnalysisCacheError, TypeError) as exc:
            if verbose:
                print(f"  ⚠️ 分析快照写入失败（不影响本次结果）：{exc}")

    def save_report(
        self,
        report: FinalReport,
        fmt: str = "html",
    ) -> str:
        """保存报告到文件，返回文件路径"""
        filename = f"report_{report.stock_code}_{report.date}"

        if fmt == "html":
            html = generate_html_report(report)
            path = self.output_dir / f"{filename}.html"
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

        elif fmt == "json":
            path = self.output_dir / f"{filename}.json"
            # FinalReport 转 dict（AnalysisResult 也要序列化）
            data = {
                "stock_code": report.stock_code,
                "stock_name": report.stock_name,
                "date": report.date,
                "final_signal": report.final_signal,
                "bullish_pct": report.bullish_pct,
                "summary": report.summary,
                "operation_advice": report.operation_advice,
                "decision_plan": report.decision_plan,
                "data_quality": report.data_quality,
                "analysis_depth": report.analysis_depth,
                "freshness": report.freshness,
                "performance": report.performance,
                "research_results": {
                    dim: {
                        "agent_name": r.agent_name,
                        "signal": r.signal,
                        "confidence": r.confidence,
                        "key_points": r.key_points,
                        "conclusion": r.conclusion[:500],  # 截断避免太长
                    }
                    for dim, r in report.research_results.items()
                    if r
                },
                "votes": [
                    {"agent": v.agent_name, "vote": v.vote, "reasoning": v.reasoning}
                    for v in report.votes
                ],
                "battle_transcript": report.battle_transcript,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            record_report_history(
                report.stock_code,
                report.date,
                data,
                source_path=path,
                generated_at=report.date,
            )

        else:
            raise ValueError(f"不支持的格式：{fmt}，请用 html 或 json")

        return str(path)

    def analyze_batch(
        self,
        stock_codes: List[str],
        verbose: bool = True,
        depth: Optional[str] = None,
    ) -> Dict[str, FinalReport]:
        """批量分析多只股票"""
        data = load_latest_data()  # 只加载一次
        results = {}

        for i, code in enumerate(stock_codes, 1):
            if verbose:
                print(f"\n[{i}/{len(stock_codes)}] 分析 {code}...")
            try:
                report = self.analyze(code, data=data, verbose=verbose, depth=depth)
                results[code] = report
            except Exception as e:
                if verbose:
                    print(f"  ❌ 失败：{e}")

        return results
