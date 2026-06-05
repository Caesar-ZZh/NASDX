"""
Serenity Chokepoint Agent — 供应链瓶颈、需求冲击、贝叶斯更新分析
"""
from typing import Any, Dict, List

from nasdx.agents.base import BaseAgent
from nasdx.data_loader import format_fund_flow, format_indicators
from nasdx.schema import AnalysisResult


SYSTEM_PROMPT = """你是一位专注于产业链卡点投资研究的A股分析师。
你使用 Serenity Chokepoint Investing 框架：需求冲击、物理供应链层级、真正瓶颈、财务映射、贝叶斯更新。

关键要求：
- 只做研究线索，不给投资建议，不模仿任何个人风格。
- 明确区分：项目内已有事实、合理推断、待核验事项。
- 不要编造客户、订单、公告、产能、市场份额或政策文件。
- 如果缺少官方公告/新闻核验，必须写清楚“待核验”。
- 信号含义：bullish=卡点假设较强；bearish=卡点假设弱或风险压过机会；neutral=证据不足。
"""


class ChokepointAgent(BaseAgent):
    name = "chokepoint_agent"
    description = "Serenity供应链瓶颈专家：需求冲击、产业链卡点、贝叶斯更新"
    system_prompt = SYSTEM_PROMPT

    @property
    def dimension(self) -> str:
        return "chokepoint"

    def _build_context(self, stock_code: str, stock_data: Dict[str, Any]) -> str:
        name = stock_data.get("name", "")
        sector = stock_data.get("sector_name", "")
        note = stock_data.get("note", "")
        indicators = stock_data.get("indicators", {})
        fund_flow = stock_data.get("fund_flow", [])

        demand_shock = self._infer_demand_shock(sector, note)
        supply_node = self._infer_supply_node(sector, name, note)

        return (
            f"股票：{stock_code} {name}（{sector}板块）\n"
            f"项目备注：{note or '无'}\n"
            f"初始需求冲击假设：{demand_shock}\n"
            f"初始供应链节点假设：{supply_node}\n\n"
            f"【技术指标】\n{format_indicators(indicators)}\n\n"
            f"【近5日资金流向】\n{format_fund_flow(fund_flow, days=5)}\n\n"
            "【数据边界】当前项目未直接接入公告、财报、互动平台或新闻核验；"
            "涉及客户、订单、产能、份额、政策的结论必须标为待核验。"
        )

    def _analyze(self, stock_code: str, stock_data: Dict[str, Any]) -> AnalysisResult:
        name = stock_data.get("name", "")
        sector = stock_data.get("sector_name", "未知板块")
        note = stock_data.get("note", "")
        demand_shock = self._infer_demand_shock(sector, note)
        supply_node = self._infer_supply_node(sector, name, note)
        key_points = self._build_key_points(sector, note, demand_shock, supply_node)

        prompt = f"""
请用 Serenity Chokepoint Investing 框架分析 {stock_code} {name}。

请按以下结构输出：
1. 需求冲击：这个标的可能对应什么真实需求浪潮，为什么与当前板块有关。
2. 供应链层级：从终端需求往上拆至少3层，指出该公司可能位于哪一层。
3. 候选卡点：该节点是否可能稀缺、扩产慢、认证周期长、替代困难或具有国产替代价值。
4. 财务映射：如果卡点成立，它更可能影响收入、毛利率、订单、估值重估还是只影响叙事。
5. 贝叶斯更新：先验、正证据、反证据、后验方向。
6. 待核验清单：列出必须查官方公告/财报/互动平台/客户或供应商资料的事项。
7. 风险：替代、客户集中、扩产、估值、流动性、政策或融资风险。

请用以下格式结尾：
【信号】bullish 或 bearish 或 neutral
【置信度】0.60
"""
        response = self._ask(prompt, temperature=0.25)
        signal, confidence = self._parse_signal(response)

        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion=response,
            signal=signal,
            confidence=confidence,
            key_points=key_points,
            raw_data_summary=f"需求冲击={demand_shock}；候选节点={supply_node}",
        )

    def _infer_demand_shock(self, sector: str, note: str) -> str:
        text = f"{sector} {note}"
        if any(k in text for k in ("通信", "光模块", "光器件", "CPO", "5G")):
            return "AI数据中心网络升级、CPO/高速光模块、通信基础设施扩容"
        if any(k in text for k in ("半导体设备", "刻蚀", "PVD", "CMP", "涂胶", "清洗")):
            return "国内晶圆厂扩产、先进制程迭代、半导体设备国产化"
        if any(k in text for k in ("半导体", "芯片", "晶圆", "传感器", "Flash", "DRAM", "MCU")):
            return "AI芯片、汽车电子、国产替代与先进封装带来的半导体需求"
        if any(k in text for k in ("电力", "电网", "特高压", "变压", "新能源")):
            return "AI数据中心用电、特高压、电网升级和新能源消纳"
        if any(k in text for k in ("AI", "算力", "服务器", "GPU")):
            return "AI训练/推理算力扩张和国产算力基础设施建设"
        if any(k in text for k in ("军工", "航空", "航发", "导弹", "雷达")):
            return "国防装备更新、军工供应链国产化和高可靠零部件需求"
        return "所属主题需求冲击尚不明确，需要结合公告和行业资料核验"

    def _infer_supply_node(self, sector: str, name: str, note: str) -> str:
        text = f"{sector} {name} {note}"
        rules = [
            (("光模块", "光器件", "光纤", "通信", "CPO"), "光模块/光器件/激光器/光纤等数据中心网络节点"),
            (("刻蚀", "PVD", "CMP", "涂胶", "显影", "清洗", "设备"), "晶圆制造设备与关键工艺装备节点"),
            (("晶圆", "代工", "特色工艺", "射频", "功率"), "晶圆代工、特色工艺或功率器件制造产能节点"),
            (("传感器", "CMOS"), "图像传感器及汽车/消费电子感知器件节点"),
            (("Flash", "DRAM", "MCU", "存储"), "存储、控制器或嵌入式处理器节点"),
            (("电网", "特高压", "调度", "变压", "电力"), "电网调度、变压器、开关设备或电力基础设施节点"),
            (("算力", "服务器", "GPU", "AI"), "AI服务器、国产算力或基础设施集成节点"),
            (("航空", "航发", "连接器", "雷达", "军工"), "高可靠军工零部件、航空装备或国产替代节点"),
        ]
        for keywords, node in rules:
            if any(k in text for k in keywords):
                return node
        return "候选供应链节点待进一步拆解"

    def _build_key_points(
        self,
        sector: str,
        note: str,
        demand_shock: str,
        supply_node: str,
    ) -> List[str]:
        points = [
            f"需求冲击：{demand_shock}",
            f"候选卡点：{supply_node}",
        ]
        if sector:
            points.append(f"所属板块：{sector}")
        if note:
            points.append(f"项目备注：{note}")
        points.append("公告/客户/订单/产能：当前项目未接入实时核验")
        return points

    def _parse_signal(self, text: str):
        signal = "neutral"
        confidence = 0.5
        for line in text.split("\n"):
            if "【信号】" in line:
                lower = line.lower()
                if "bullish" in lower:
                    signal = "bullish"
                elif "bearish" in lower:
                    signal = "bearish"
                elif "neutral" in lower:
                    signal = "neutral"
            if "【置信度】" in line:
                try:
                    confidence = float(line.split("】")[-1].strip())
                except Exception:
                    pass
        confidence = min(1.0, max(0.0, confidence))
        return signal, confidence
