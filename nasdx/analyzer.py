"""
主分析器 — 协调 Research 环境和 Battle 环境
对应 FinGenius 的 EnhancedFinGeniusAnalyzer
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from nasdx.schema import AnalysisResult, BattleVote, FinalReport
from nasdx.data_loader import load_latest_data, get_stock_data, get_market_overview
from nasdx.environments.research import ResearchEnvironment
from nasdx.environments.battle import BattleEnvironment
from nasdx.agents.synthesis import SynthesisAgent
from nasdx.data_quality import assess_data_quality
from nasdx.decision import build_decision_plan, format_decision_plan
from nasdx.history_store import record_report_history
from nasdx.paths import get_reports_dir
from nasdx.report import generate_html_report

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
    ):
        self.max_steps = max_steps
        self.debate_rounds = debate_rounds
        self.risk_profile = risk_profile
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
    ) -> FinalReport:
        """
        分析单只股票，返回完整报告

        Args:
            stock_code: 股票代码，如 "000001"
            data: 预加载的完整数据（None 则自动加载最新文件）
            verbose: 是否打印进度
        """
        # 1. 加载数据
        if data is None:
            data = load_latest_data()
        data_quality = assess_data_quality(data)

        stock_data = get_stock_data(data, stock_code)
        if not stock_data:
            raise ValueError(f"股票 {stock_code} 不在监控池中，请检查代码")

        stock_name = stock_data.get("name", stock_code)
        date_str = data.get("date", datetime.now().strftime("%Y%m%d"))

        if verbose:
            print(f"\n{'='*60}")
            print(f"🚀 NASDX 分析启动：{stock_code} {stock_name}")
            print(f"   数据日期：{date_str}  辩论轮次：{self.debate_rounds}")
            print(f"{'='*60}")

        # 2. Research Phase
        if verbose:
            print("\n📊 [Phase 1] 研究阶段 — 专家分析")
        research_results = self.research_env.run(stock_code, stock_data, verbose=verbose)

        # 3. Battle Phase
        if verbose:
            print("\n⚔️  [Phase 2] 辩论阶段 — 多空博弈")
        transcript, votes, bullish_pct = self.battle_env.run(
            stock_code=stock_code,
            stock_name=stock_name,
            research_results=research_results,
            verbose=verbose,
        )

        # 4. Synthesis
        if verbose:
            print("\n🎯 [Phase 3] 综合研判...")
        synthesis = self.synthesis_agent.synthesize(
            stock_code=stock_code,
            stock_name=stock_name,
            analysis_results=research_results,
            battle_transcript=transcript,
        )

        # 5. 构建最终信号
        final_signal = synthesis.signal
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
        )

        # 6. 组装报告
        report = FinalReport(
            stock_code=stock_code,
            stock_name=stock_name,
            date=date_str,
            research_results={
                dim: result
                for dim, result in research_results.items()
            },
            battle_transcript=transcript,
            votes=votes,
            final_signal=final_signal,
            bullish_pct=bullish_pct,
            summary=synthesis.conclusion,
            operation_advice=format_decision_plan(decision_plan),
            decision_plan=decision_plan,
            data_quality=data_quality,
        )

        if verbose:
            signal_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(final_signal, "")
            print(f"\n{'='*60}")
            print(f"{signal_emoji} 最终信号：{final_signal}  看多占比：{bullish_pct:.1f}%")
            print(f"{'='*60}")

        return report

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
    ) -> Dict[str, FinalReport]:
        """批量分析多只股票"""
        data = load_latest_data()  # 只加载一次
        results = {}

        for i, code in enumerate(stock_codes, 1):
            if verbose:
                print(f"\n[{i}/{len(stock_codes)}] 分析 {code}...")
            try:
                report = self.analyze(code, data=data, verbose=verbose)
                results[code] = report
            except Exception as e:
                if verbose:
                    print(f"  ❌ 失败：{e}")

        return results
