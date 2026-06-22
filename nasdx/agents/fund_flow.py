"""
资金流向 Agent — 分析主力/超大单/大单/中单/小单资金动向
"""
from typing import Any, Dict
from nasdx.agents.base import BaseAgent
from nasdx.schema import AnalysisResult
from nasdx.data_loader import format_fund_flow


SYSTEM_PROMPT = """你是一位专注于资金流向分析的A股机构交易员，精通读懂主力行为。
你通过主力净流入、超大单、大单动向判断机构意图和短期趋势。
你的结论要明确，给出信号（bullish/bearish/neutral）和置信度。

判断逻辑：
- 主力净流入持续为正 + 占比>5% → 强烈看多
- 主力净流入持续为负 → 看空
- 超大单流入（机构买入）与价格背离 → 特别关注
- 散户（小单）逆势买入而主力出逃 → 警惕
"""


class FundFlowAgent(BaseAgent):
    name = "fund_flow_agent"
    description = "资金流向分析专家：主力/超大单/大单行为"
    system_prompt = SYSTEM_PROMPT

    @property
    def dimension(self) -> str:
        return "fund_flow"

    def _build_context(self, stock_code: str, stock_data: Dict[str, Any]) -> str:
        name = stock_data.get("name", "")
        sector = stock_data.get("sector_name", "")
        fund_flow = stock_data.get("fund_flow", [])
        return (
            f"股票：{stock_code} {name}（{sector}板块）\n\n"
            f"【近5日资金流向】\n{format_fund_flow(fund_flow, days=5)}"
        )

    def _analyze(self, stock_code: str, stock_data: Dict[str, Any]) -> AnalysisResult:
        fund_flow = stock_data.get("fund_flow", [])
        main_net_3d = stock_data.get("main_net_3d", [])

        if not fund_flow:
            return AnalysisResult(
                agent_name=self.name,
                dimension=self.dimension,
                conclusion="该股票（科创板/ETF）无资金流向数据，跳过此维度分析。",
                signal="neutral",
                confidence=0.0,
                key_points=["688xxx科创板股票不支持资金流向接口"],
            )

        # 计算近5日主力净流向趋势
        recent = fund_flow[-5:]
        total_main = sum(r.get("主力净流入-净额", 0) for r in recent)
        positive_days = sum(1 for r in recent if r.get("主力净流入-净额", 0) > 0)
        avg_pct = sum(r.get("主力净流入-净占比", 0) for r in recent) / len(recent)

        context_summary = (
            f"近5日主力累计净流入：{total_main/1e8:.2f}亿元，"
            f"净流入天数：{positive_days}/5，"
            f"平均净占比：{avg_pct:.1f}%"
        )

        prompt = f"""
请分析 {stock_code} {stock_data.get('name','')} 的资金流向：

{context_summary}

详细数据已在上文提供。

请分析：
1. 主力资金整体意图（吸筹/出货/洗盘/观望）
2. 超大单与大单的分歧（机构 vs 游资）
3. 量价关系（价涨资金出？价跌资金进？）
4. 未来1-3日资金预判
5. 最终信号（bullish/bearish/neutral）和置信度

请用以下格式结尾：
【信号】bullish 或 bearish 或 neutral
【置信度】0.70
"""
        response, payload = self._ask_analysis(prompt)
        signal, confidence = self._parse_structured_signal(response, payload)
        key_points = self._merge_key_points(
            self._structured_key_points(payload),
            self._build_key_points(recent, total_main, positive_days, avg_pct),
        )

        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion=self._structured_conclusion(response, payload),
            signal=signal,
            confidence=confidence,
            key_points=key_points,
            raw_data_summary=context_summary,
        )

    def _parse_signal(self, text: str):
        signal = "neutral"
        confidence = 0.5
        for line in text.split("\n"):
            if "【信号】" in line:
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

    def _build_key_points(self, recent, total_main, positive_days, avg_pct) -> list:
        points = []
        if total_main > 0:
            points.append(f"近5日主力净流入{total_main/1e8:.2f}亿，持续吸筹迹象")
        else:
            points.append(f"近5日主力净流出{abs(total_main)/1e8:.2f}亿，资金撤离")
        points.append(f"5日中{positive_days}日主力净流入，稳定性{'高' if positive_days>=4 else '中' if positive_days>=2 else '低'}")
        if abs(avg_pct) > 5:
            points.append(f"平均净占比{avg_pct:.1f}%，{'资金集中度高' if avg_pct>0 else '抛压集中'}")

        # 最近一日超大单
        if recent:
            last = recent[-1]
            ultra_net = last.get("超大单净流入-净额", 0)
            if abs(ultra_net) > 5e7:
                direction = "流入" if ultra_net > 0 else "流出"
                points.append(f"最新日超大单{direction}{abs(ultra_net)/1e8:.2f}亿（机构动向）")
        return points
