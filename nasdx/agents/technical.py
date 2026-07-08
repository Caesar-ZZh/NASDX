"""
技术面 Agent — 分析 MA/MACD/RSI/布林带/量比
"""
from typing import Any, Dict
from nasdx.agents.base import BaseAgent
from nasdx.schema import AnalysisResult
from nasdx.data_loader import format_indicators, format_kline_summary


SYSTEM_PROMPT = """你是一位专精技术分析的A股交易员，有15年量化经验。
你善于通过均线、MACD、RSI、布林带、量比等指标判断个股短中期走势。
你的分析要基于数据，结论要明确（看多/看空/中性），给出置信度（0.0-1.0）。

分析格式要求：
1. 趋势判断（多头/空头/震荡）
2. 关键指标解读（各指标含义及当前信号）
3. 操作建议
4. 信号：bullish / bearish / neutral
5. 置信度：0.0-1.0
"""


class TechnicalAgent(BaseAgent):
    name = "technical_agent"
    description = "技术面分析专家：MA/MACD/RSI/布林带/量比"
    system_prompt = SYSTEM_PROMPT

    @property
    def dimension(self) -> str:
        return "technical"

    def _build_context(self, stock_code: str, stock_data: Dict[str, Any]) -> str:
        name = stock_data.get("name", "")
        sector = stock_data.get("sector_name", "")
        indicators = stock_data.get("indicators", {})
        return (
            f"股票：{stock_code} {name}（{sector}板块）\n\n"
            f"【技术指标】\n{format_indicators(indicators)}"
        )

    def _analyze(self, stock_code: str, stock_data: Dict[str, Any]) -> AnalysisResult:
        indicators = stock_data.get("indicators", {})
        summary = format_kline_summary(indicators)

        prompt = f"""
请基于以下技术面数据对 {stock_code} {stock_data.get('name','')} 进行分析：

技术摘要：{summary}

详细数据已在上文提供。

请给出：
1. 整体趋势（多头/空头/震荡）
2. 各指标解读（MA均线系统、MACD、RSI、布林带、量比）
3. 支撑压力位估算
4. 短期操作建议（1-5个交易日）
5. 最终信号（bullish/bearish/neutral）和置信度（0.0-1.0，只给数字）

请用以下格式结尾：
【信号】bullish 或 bearish 或 neutral
【置信度】0.75
"""
        response, payload = self._ask_analysis(prompt)

        # 解析信号和置信度
        signal, confidence = self._parse_structured_signal(response, payload)

        # 提取关键点
        key_points = self._merge_key_points(
            self._structured_key_points(payload),
            self._extract_key_points(indicators),
        )

        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion=self._structured_conclusion(response, payload),
            signal=signal,
            confidence=confidence,
            key_points=key_points,
            raw_data_summary=summary,
        )

    def _extract_key_points(self, indicators: Dict) -> list:
        from nasdx.data_loader import _get
        points = []
        close     = _get(indicators, "close", "current_price") or 0
        ma5       = _get(indicators, "ma5")
        ma20      = _get(indicators, "ma20")
        rsi       = _get(indicators, "rsi", "rsi14")
        macd      = _get(indicators, "macd_bar") or 0
        vol_ratio = _get(indicators, "vol_ratio") or 1

        if ma5 and ma20:
            if ma5 > ma20:
                points.append(f"MA5({ma5:.2f}) > MA20({ma20:.2f})，均线多头")
            else:
                points.append(f"MA5({ma5:.2f}) < MA20({ma20:.2f})，均线空头")
        if rsi:
            if rsi > 70:
                points.append(f"RSI={rsi:.0f} 超买区间，注意回调风险")
            elif rsi < 30:
                points.append(f"RSI={rsi:.0f} 超卖区间，可能反弹")
            else:
                points.append(f"RSI={rsi:.0f} 正常区间")
        if macd > 0:
            points.append(f"MACD金叉 +{macd:.4f}，上涨动能")
        else:
            points.append(f"MACD死叉 {macd:.4f}，下跌动能")
        if vol_ratio > 1.5:
            points.append(f"量比{vol_ratio:.2f} 放量，关注突破")
        elif vol_ratio < 0.7:
            points.append(f"量比{vol_ratio:.2f} 缩量，观望为主")
        return points
