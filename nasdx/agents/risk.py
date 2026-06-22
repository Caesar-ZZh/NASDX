"""
风险 Agent — 分析超买超卖、波动率、趋势背离、持仓红线
"""
from typing import Any, Dict
from nasdx.agents.base import BaseAgent
from nasdx.schema import AnalysisResult
from nasdx.data_loader import format_indicators


SYSTEM_PROMPT = """你是一位专注于风险管理的A股量化研究员。
你通过技术指标的极值、量价背离、布林带位置、RSI超买超卖来评估当前持仓风险。
你的结论是：当前风险等级（低/中/高）和对应操作建议。
信号：bullish（低风险可进）/ bearish（高风险应减）/ neutral（风险适中，持仓观望）
"""


class RiskAgent(BaseAgent):
    name = "risk_agent"
    description = "风险控制专家：超买超卖/波动/背离/持仓红线"
    system_prompt = SYSTEM_PROMPT

    @property
    def dimension(self) -> str:
        return "risk"

    def _build_context(self, stock_code: str, stock_data: Dict[str, Any]) -> str:
        name = stock_data.get("name", "")
        sector = stock_data.get("sector_name", "")
        indicators = stock_data.get("indicators", {})
        return (
            f"股票：{stock_code} {name}（{sector}板块）\n\n"
            f"【风险评估基础数据】\n{format_indicators(indicators)}"
        )

    def _analyze(self, stock_code: str, stock_data: Dict[str, Any]) -> AnalysisResult:
        indicators = stock_data.get("indicators", {})
        risk_summary = self._compute_risk_metrics(indicators)

        prompt = f"""
请对 {stock_code} {stock_data.get('name','')} 进行风险评估：

{risk_summary}

请从以下维度分析：
1. 超买超卖风险（RSI/布林带位置）
2. 趋势延续 vs 背离风险（MACD与价格）
3. 量能异常风险（量比过高/过低）
4. 当前风险等级：低风险/中等风险/高风险
5. 风险点（最多3条）
6. 操作建议（止损位、减仓条件）

请用以下格式结尾：
【信号】bullish（低风险可进场）或 bearish（高风险应离场）或 neutral（持仓观望）
【置信度】0.65
"""
        response, payload = self._ask_analysis(prompt)
        signal, confidence = self._parse_structured_signal(response, payload)
        key_points = self._merge_key_points(
            self._structured_key_points(payload),
            self._build_risk_points(indicators),
        )

        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion=self._structured_conclusion(response, payload),
            signal=signal,
            confidence=confidence,
            key_points=key_points,
            raw_data_summary=risk_summary,
        )

    def _compute_risk_metrics(self, indicators: Dict) -> str:
        from nasdx.data_loader import _get
        rsi        = _get(indicators, "rsi", "rsi14")
        close      = _get(indicators, "close", "current_price") or 0
        boll_upper = _get(indicators, "boll_upper")
        boll_lower = _get(indicators, "boll_lower")
        macd       = _get(indicators, "macd_bar") or 0
        vol_ratio  = _get(indicators, "vol_ratio") or 1
        up_days    = _get(indicators, "up_days_20") or 10

        lines = []
        if rsi:
            level = "超买⚠️" if rsi > 70 else "超卖⚠️" if rsi < 30 else "正常"
            lines.append(f"RSI={rsi:.1f}（{level}）")
        if boll_upper and boll_lower and close:
            boll_pct = (close - boll_lower) / (boll_upper - boll_lower + 1e-9) * 100
            position = "上轨附近" if boll_pct > 80 else "下轨附近" if boll_pct < 20 else "中轨区间"
            lines.append(f"布林带位置：{boll_pct:.0f}%（{position}）")
        if macd:
            lines.append(f"MACD柱：{macd:+.4f}（{'金叉' if macd > 0 else '死叉'}）")
        lines.append(f"量比：{vol_ratio:.2f}（{'放量' if vol_ratio > 1.5 else '缩量' if vol_ratio < 0.7 else '正常'}）")
        if up_days is not None:
            lines.append(f"20日上涨天数：{up_days}天（{'强势' if up_days >= 12 else '弱势' if up_days <= 8 else '均衡'}）")
        return "\n".join(lines)

    def _build_risk_points(self, indicators: Dict) -> list:
        from nasdx.data_loader import _get
        points = []
        rsi        = _get(indicators, "rsi", "rsi14")
        close      = _get(indicators, "close", "current_price") or 0
        boll_upper = _get(indicators, "boll_upper")
        boll_lower = _get(indicators, "boll_lower")

        if rsi and rsi > 75:
            points.append(f"🔴 RSI={rsi:.0f} 严重超买，回调风险大")
        elif rsi and rsi < 25:
            points.append(f"🟢 RSI={rsi:.0f} 超卖，可能反弹机会")

        if boll_upper and close >= boll_upper * 0.98:
            points.append(f"⚠️ 价格触及布林上轨（{boll_upper:.2f}），阻力位")
        elif boll_lower and close <= boll_lower * 1.02:
            points.append(f"🟡 价格触及布林下轨（{boll_lower:.2f}），支撑位")

        if not points:
            points.append("技术指标无明显极值，风险中性")
        return points

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
