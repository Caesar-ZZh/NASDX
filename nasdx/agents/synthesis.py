"""
综合 Agent — 汇总各维度分析，给出最终操作建议
"""
from typing import Any, Dict, List
from nasdx.agents.base import BaseAgent
from nasdx.schema import AnalysisResult


SYSTEM_PROMPT = """你是一位资深的A股综合分析师和基金经理，拥有20年投资经验。
你能整合技术面、资金面、风险面、板块面的多维度信号，给出综合研判。
你的建议要具体：是否操作、操作时机、仓位比例、止盈止损位。
你代表投资决策委员会的最终意见，要果断而负责。
"""


class SynthesisAgent(BaseAgent):
    name = "synthesis_agent"
    description = "综合分析专家：整合多维度信号给出最终操作建议"
    system_prompt = SYSTEM_PROMPT

    @property
    def dimension(self) -> str:
        return "synthesis"

    def _build_context(self, stock_code: str, stock_data: Dict[str, Any]) -> str:
        return f"股票：{stock_code} {stock_data.get('name','')}"

    def synthesize(
        self,
        stock_code: str,
        stock_name: str,
        analysis_results: Dict[str, AnalysisResult],
        battle_transcript: List[str],
    ) -> AnalysisResult:
        """综合所有 Agent 结果，生成最终报告"""
        self.memory.clear()

        # 构建各维度摘要
        dim_summaries = []
        signals = []
        for dim, result in analysis_results.items():
            if result and result.signal:
                signals.append(result.signal)
                emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(result.signal, "⚪")
                dim_summaries.append(
                    f"{emoji} [{dim}] 信号={result.signal}，置信度={result.confidence:.0%}\n"
                    f"   关键点：{'; '.join(result.key_points[:2]) if result.key_points else '无'}"
                )

        # 统计投票
        bullish_count = signals.count("bullish")
        bearish_count = signals.count("bearish")
        neutral_count = signals.count("neutral")
        total = len(signals) or 1
        bullish_pct = bullish_count / total * 100

        signals_summary = "\n".join(dim_summaries) if dim_summaries else "无信号数据"
        battle_summary = "\n".join(battle_transcript[-3:]) if battle_transcript else "无辩论记录"

        prompt = f"""
请综合以下多维度分析结果，对 {stock_code} {stock_name} 给出最终投资建议：

【各维度信号汇总】
{signals_summary}

【辩论摘要（最近3条）】
{battle_summary}

【投票统计】
看多：{bullish_count}票 | 看空：{bearish_count}票 | 中性：{neutral_count}票
看多占比：{bullish_pct:.1f}%

请给出：
1. 综合研判（200字以内，要有逻辑）
2. 明确操作建议：
   - 操作方向：买入/卖出/观望
   - 建议仓位：X%（满仓=100%）
   - 入场时机：（如：突破MA20可买）
   - 止损位：（具体价位或条件）
   - 止盈目标：（近期目标价）
3. 最大风险提示（1条）

请用以下格式结尾：
【最终信号】bullish 或 bearish 或 neutral
【置信度】0.72
"""
        response = self._ask(prompt, temperature=0.2)
        signal, confidence = self._parse_signal(response)

        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion=response,
            signal=signal,
            confidence=confidence,
            key_points=[
                f"看多{bullish_count}票，看空{bearish_count}票，中性{neutral_count}票",
                f"看多占比{bullish_pct:.1f}%",
            ],
            raw_data_summary=f"综合{total}个维度信号",
        )

    def _analyze(self, stock_code: str, stock_data: Dict[str, Any]) -> AnalysisResult:
        # 单独调用时的 fallback
        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion="请通过 synthesize() 方法调用",
            signal="neutral",
            confidence=0.0,
        )

    def _parse_signal(self, text: str):
        signal = "neutral"
        confidence = 0.5
        for line in text.split("\n"):
            if "【最终信号】" in line or "【信号】" in line:
                if "bullish" in line.lower():
                    signal = "bullish"
                elif "bearish" in line.lower():
                    signal = "bearish"
            if "【置信度】" in line:
                try:
                    confidence = float(line.split("】")[-1].strip())
                except:
                    pass
        return signal, confidence
