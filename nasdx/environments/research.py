"""
Research 环境 — 并行/顺序运行多个专家 Agent，汇总分析结果
对应 FinGenius 的 ResearchEnvironment
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence, Tuple

from nasdx.schema import AnalysisResult
from nasdx.agents.technical import TechnicalAgent
from nasdx.agents.fund_flow import FundFlowAgent
from nasdx.agents.risk import RiskAgent
from nasdx.agents.sector import SectorAgent
from nasdx.agents.chokepoint import ChokepointAgent


class ResearchEnvironment:
    """
    研究环境：协调5个专家 Agent 分析一只股票
    返回 Dict[dimension, AnalysisResult]
    """

    AGENT_ORDER = [
        ("technical",  "技术面"),
        ("fund_flow",  "资金流"),
        ("risk",       "风险"),
        ("sector",     "板块"),
        ("chokepoint", "瓶颈"),
    ]

    def __init__(self, max_steps: int = 3, delay: float = 1.0, max_workers: int | None = None):
        self.max_steps = max_steps
        self.delay = delay  # 顺序模式下 Agent 之间的间隔（秒），避免 API 过速
        self.max_workers = _resolve_max_workers(max_workers, len(self.AGENT_ORDER))

        self.agents = {
            "technical": TechnicalAgent(max_steps=max_steps),
            "fund_flow": FundFlowAgent(max_steps=max_steps),
            "risk":      RiskAgent(max_steps=max_steps),
            "sector":    SectorAgent(max_steps=max_steps),
            "chokepoint": ChokepointAgent(max_steps=max_steps),
        }

    def run(
        self,
        stock_code: str,
        stock_data: Dict[str, Any],
        verbose: bool = True,
        dimensions: Sequence[str] | None = None,
    ) -> Dict[str, AnalysisResult]:
        """
        并发执行专家 Agent。若 max_workers=1，则退回顺序执行。

        Args:
            dimensions: 仅执行给定维度（增量刷新用，#65）。None 表示全部维度，
                与历史行为完全一致。

        Returns:
            {dimension: AnalysisResult}
        """
        agent_order = self._selected_order(dimensions)
        if not agent_order:
            return {}
        if self.max_workers <= 1:
            return self._run_sequential(stock_code, stock_data, verbose=verbose, agent_order=agent_order)

        return self._run_parallel(stock_code, stock_data, verbose=verbose, agent_order=agent_order)

    def _selected_order(self, dimensions: Sequence[str] | None) -> List[tuple]:
        if dimensions is None:
            return list(self.AGENT_ORDER)
        wanted = {str(d) for d in dimensions}
        return [item for item in self.AGENT_ORDER if item[0] in wanted]

    def _run_sequential(
        self,
        stock_code: str,
        stock_data: Dict[str, Any],
        verbose: bool = True,
        agent_order: List[tuple] | None = None,
    ) -> Dict[str, AnalysisResult]:
        """顺序执行 Agent，用于调试、限流或单线程环境。"""
        results: Dict[str, AnalysisResult] = {}
        agent_order = agent_order if agent_order is not None else list(self.AGENT_ORDER)
        total = len(agent_order)

        for i, (dim, label) in enumerate(agent_order, 1):
            if verbose:
                print(f"  [{i}/{total}] {label} Agent 分析中...")

            result = self._run_one_agent(dim, stock_code, stock_data)
            results[dim] = result
            if verbose:
                _print_agent_result(result)

            # 最后一个不需要等待
            if i < total and self.delay > 0:
                time.sleep(self.delay)

        return results

    def _run_parallel(
        self,
        stock_code: str,
        stock_data: Dict[str, Any],
        verbose: bool = True,
        agent_order: List[tuple] | None = None,
    ) -> Dict[str, AnalysisResult]:
        """使用线程池并发执行 Phase 1 Agent，并按 AGENT_ORDER 返回结果。"""
        agent_order = agent_order if agent_order is not None else list(self.AGENT_ORDER)
        total = len(agent_order)
        if verbose:
            for i, (_dim, label) in enumerate(agent_order, 1):
                print(f"  [{i}/{total}] {label} Agent 已提交...")

        completed: Dict[str, AnalysisResult] = {}
        workers = max(1, min(self.max_workers, total))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nasdx-agent") as executor:
            futures = {
                executor.submit(self._run_one_agent, dim, stock_code, stock_data): dim
                for dim, _label in agent_order
                if self.agents.get(dim)
            }
            for future in as_completed(futures):
                dim = futures[future]
                result = future.result()
                completed[dim] = result
                if verbose:
                    _print_agent_result(result)

        return {
            dim: completed.get(dim) or _fallback_result(dim, "Agent 未返回结果")
            for dim, _label in agent_order
            if self.agents.get(dim)
        }

    def _run_one_agent(
        self,
        dim: str,
        stock_code: str,
        stock_data: Dict[str, Any],
    ) -> AnalysisResult:
        """执行单个 Agent，并把异常收敛为中性结果。"""
        agent = self.agents.get(dim)
        if not agent:
            return _fallback_result(dim, "Agent 未配置")

        try:
            return agent.run(stock_code, stock_data)
        except Exception as e:
            return _fallback_result(dim, f"分析失败：{e}")


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


def _resolve_max_workers(max_workers: int | None, default_workers: int) -> int:
    if max_workers is not None:
        return max(1, min(int(max_workers), default_workers))
    raw = os.environ.get("NASDX_RESEARCH_MAX_WORKERS", "")
    if raw:
        try:
            return max(1, min(int(raw), default_workers))
        except ValueError:
            pass
    return default_workers


def _fallback_result(dim: str, message: str) -> AnalysisResult:
    return AnalysisResult(
        agent_name=dim,
        dimension=dim,
        conclusion=message,
        signal="neutral",
        confidence=0.0,
        key_points=[],
    )


def _print_agent_result(result: AnalysisResult) -> None:
    emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(result.signal, "⚪")
    print(f"         → {emoji} {result.dimension}: {result.signal}（置信度 {result.confidence:.0%}）")
