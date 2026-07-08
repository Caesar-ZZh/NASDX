"""
板块 Agent — 分析板块整体轮动、ETF 表现、板块内分化
"""
from typing import Any, Dict, List
from nasdx.agents.base import BaseAgent
from nasdx.schema import AnalysisResult


SYSTEM_PROMPT = """你是一位专注于A股板块轮动和行业研究的策略分析师。
你擅长通过板块内个股表现、ETF涨跌、资金轮动判断板块强弱。
你的分析要结合大盘背景，给出板块未来1周的强弱判断。
信号：bullish（板块走强）/ bearish（板块走弱）/ neutral（横盘震荡）
"""


class SectorAgent(BaseAgent):
    name = "sector_agent"
    description = "板块轮动分析专家：板块强弱、ETF表现、分化分析"
    system_prompt = SYSTEM_PROMPT

    @property
    def dimension(self) -> str:
        return "sector"

    def _build_context(self, stock_code: str, stock_data: Dict[str, Any]) -> str:
        return ""  # 板块 Agent 使用完整板块数据，context 在 _analyze 中构建

    def run_sector(
        self,
        sector_name: str,
        sector_data: Dict[str, Any],
        market_overview: Dict[str, Any],
    ) -> AnalysisResult:
        """分析整个板块（不是单只股票）"""
        self.memory.clear()

        context = self._build_sector_context(sector_name, sector_data, market_overview)
        prompt = f"""
请分析 {sector_name} 板块的整体状况：

{context}

请分析：
1. 板块整体涨跌表现（强 vs 弱）
2. 板块内个股分化情况
3. ETF 表现是否反映机构态度
4. 与大盘的相对强弱（超额收益？）
5. 后市1周展望及重点关注标的

请用以下格式结尾：
【信号】bullish 或 bearish 或 neutral
【置信度】0.65
"""
        self.memory.messages.append(
            type("Msg", (), {"role": type("R", (), {"value": "user"})(), "content": context, "to_dict": lambda s: {"role":"user","content":context}})()
        )
        response, payload = self._ask_analysis(prompt)
        signal, confidence = self._parse_structured_signal(response, payload)
        key_points = self._merge_key_points(
            self._structured_key_points(payload),
            self._extract_sector_points(sector_data),
        )

        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion=self._structured_conclusion(response, payload),
            signal=signal,
            confidence=confidence,
            key_points=key_points,
            raw_data_summary=f"{sector_name}板块综合分析",
        )

    def _analyze(self, stock_code: str, stock_data: Dict[str, Any]) -> AnalysisResult:
        # 单只股票时，分析所属板块视角
        sector_name = stock_data.get("sector_name", "未知板块")
        prompt = f"""
请从板块视角分析 {stock_code} {stock_data.get('name','')} 所在的 {sector_name} 板块：

该股所属板块：{sector_name}
当前个股涨跌：{stock_data.get('indicators',{}).get('change_pct','N/A')}%

请给出：
1. {sector_name}板块近期整体表现判断
2. 该股在板块内的相对位置（领涨？领跌？跟随？）
3. 板块催化剂或利空因素
4. 最终信号（bullish/bearish/neutral）和置信度

【信号】neutral
【置信度】0.50
"""
        response, payload = self._ask_analysis(prompt)
        signal, confidence = self._parse_structured_signal(response, payload)
        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion=self._structured_conclusion(response, payload),
            signal=signal,
            confidence=confidence,
            key_points=self._merge_key_points(
                self._structured_key_points(payload),
                [f"所属板块：{sector_name}"],
            ),
        )

    def _build_sector_context(
        self, sector_name: str, sector_data: Dict, market_overview: Dict
    ) -> str:
        lines = [f"=== {sector_name} 板块 ===\n"]

        # 大盘
        if market_overview:
            lines.append("【大盘概况】")
            for idx, (name, info) in enumerate(market_overview.items()):
                chg = info.get("change_pct", 0)
                close = info.get("close", 0)
                lines.append(f"  {name}: {close:.2f} ({chg:+.2f}%)")

        # 股票列表
        lines.append("\n【板块内个股】")
        for s in sector_data.get("stocks", []):
            ind = s.get("indicators", {})
            close = ind.get("close", "N/A")
            chg = ind.get("change_pct", "N/A")
            chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else str(chg)
            lines.append(f"  {s['code']} {s['name']}: {close} {chg_str}")

        # ETF 列表
        lines.append("\n【板块 ETF】")
        for e in sector_data.get("etfs", []):
            ind = e.get("indicators", {})
            close = ind.get("close", "N/A")
            chg = ind.get("change_pct", "N/A")
            chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else str(chg)
            lines.append(f"  {e['code']} {e['name']}: {close} {chg_str}")

        return "\n".join(lines)

    def _extract_sector_points(self, sector_data: Dict) -> List[str]:
        points = []
        stocks = sector_data.get("stocks", [])
        up_count = sum(
            1 for s in stocks
            if isinstance(s.get("indicators", {}).get("change_pct"), (int, float))
            and s["indicators"]["change_pct"] > 0
        )
        if stocks:
            points.append(f"板块内{up_count}/{len(stocks)}只股票上涨")
        return points
