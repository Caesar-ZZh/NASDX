"""
Research 环境 — 并行/顺序运行多个专家 Agent，汇总分析结果
对应 FinGenius 的 ResearchEnvironment
"""
import time
from typing import Any, Dict, List

from nasdx.schema import AnalysisResult
from nasdx.agents.technical import TechnicalAgent
from nasdx.agents.fund_flow import FundFlowAgent
from nasdx.agents.risk import RiskAgent
from nasdx.agents.sector import SectorAgent


class ResearchEnvironment:
    """
    研究环境：协调4个专家 Agent 顺序分析一只股票
    返回 Dict[dimension, AnalysisResult]
    """

    AGENT_ORDER = [
        ("technical",  "技术面"),
        ("fund_flow",  "资金流"),
        ("risk",       "风险"),
        ("sector",     "板块"),
    ]

    def __init__(self, max_steps: int = 3, delay: float = 1.0):
        self.max_steps = max_steps
        self.delay = delay  # Agent 之间的间隔（秒），避免 API 过速

        self.agents = {
            "technical": TechnicalAgent(max_steps=max_steps),
            "fund_flow": FundFlowAgent(max_steps=max_steps),
            "risk":      RiskAgent(max_steps=max_steps),
            "sector":    SectorAgent(max_steps=max_steps),
        }

    def run(
        self,
        stock_code: str,
        stock_data: Dict[str, Any],
        verbose: bool = True,
    ) -> Dict[str, AnalysisResult]:
        """
        顺序执行所有专家 Agent

        Returns:
            {dimension: AnalysisResult}
        """
        results: Dict[str, AnalysisResult] = {}
        total = len(self.AGENT_ORDER)

        for i, (dim, label) in enumerate(self.AGENT_ORDER, 1):
            agent = self.agents.get(dim)
            if not agent:
                continue

            if verbose:
                print(f"  [{i}/{total}] {label} Agent 分析中...")

            try:
                result = agent.run(stock_code, stock_data)
                results[dim] = result
                if verbose:
                    emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(result.signal, "⚪")
                    print(f"         → {emoji} {result.signal}（置信度 {result.confidence:.0%}）")
            except Exception as e:
                if verbose:
                    print(f"         → ❌ 失败：{e}")
                results[dim] = AnalysisResult(
                    agent_name=dim,
                    dimension=dim,
                    conclusion=f"分析失败：{e}",
                    signal="neutral",
                    confidence=0.0,
                )

            # 最后一个不需要等待
            if i < total and self.delay > 0:
                time.sleep(self.delay)

        return results


class SectorResearchEnvironment:
    """板块研究环境：分析板块内所有股票并汇总"""

    def __init__(self, max_steps: int = 3, delay: float = 0.5):
        self.stock_env = ResearchEnvironment(max_steps=max_steps, delay=delay)

    def run_sector(
        self,
        sector_name: str,
        sector_data: Dict[str, Any],
        market_overview: Dict[str, Any],
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """分析整个板块"""
        if verbose:
            print(f"\n📊 开始分析板块：{sector_name}")

        all_stocks = sector_data.get("stocks", []) + sector_data.get("etfs", [])
        sector_results = {}

        for stock in all_stocks:
            code = stock.get("code", "")
            name = stock.get("name", "")
            stock["sector_name"] = sector_name

            if verbose:
                print(f"\n  → {code} {name}")

            result = self.stock_env.run(code, stock, verbose=verbose)
            sector_results[code] = {
                "name": name,
                "analysis": result,
            }

        # 板块级汇总信号
        all_signals = []
        for code_data in sector_results.values():
            for dim_result in code_data["analysis"].values():
                if dim_result.signal in ("bullish", "bearish", "neutral"):
                    all_signals.append(dim_result.signal)

        bullish_pct = all_signals.count("bullish") / len(all_signals) * 100 if all_signals else 50
        sector_signal = "bullish" if bullish_pct >= 55 else "bearish" if bullish_pct <= 40 else "neutral"

        return {
            "sector_name": sector_name,
            "sector_signal": sector_signal,
            "bullish_pct": bullish_pct,
            "stock_results": sector_results,
        }
