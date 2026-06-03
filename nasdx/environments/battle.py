"""
Battle 环境 — 多轮专家辩论 + 投票决策
对应 FinGenius 的 BattleEnvironment
博弈论核心：各 Agent 站在对立立场辩论，最终投票
"""
import time
from typing import Any, Dict, List, Tuple
from nasdx.schema import AnalysisResult, BattleVote
from nasdx.llm import llm


# 辩论角色配置
DEBATE_ROLES = [
    {
        "name": "多头辩手",
        "system": """你是一位激进的多头投资者。
你的任务是找出股票上涨的最有力理由，反驳空头论点。
你相信技术面改善、资金流入、板块催化都是上涨信号。
即使数据不利，你也要找到多头解释角度。
用简短有力的语言（200字以内），给出3条看多理由。""",
        "bias": "bullish",
    },
    {
        "name": "空头辩手",
        "system": """你是一位谨慎的空头分析师。
你的任务是找出股票下跌的风险，反驳多头论点。
你关注技术面恶化、资金外流、超买风险和宏观压力。
即使数据看涨，你也要找到看空理由。
用简短有力的语言（200字以内），给出3条看空理由。""",
        "bias": "bearish",
    },
    {
        "name": "中性裁判",
        "system": """你是一位独立客观的资深分析师。
你听完多空双方辩论后，给出平衡、理性的综合判断。
你不偏向任何一方，只遵循数据和逻辑。
给出你的最终倾向（看多/看空/中性）和核心理由（150字以内）。""",
        "bias": "neutral",
    },
]


class BattleEnvironment:
    """
    辩论环境：多空对决 + 裁判综合
    支持多轮辩论（每轮多空各发言一次）
    """

    def __init__(self, debate_rounds: int = 2, delay: float = 0.5):
        self.debate_rounds = debate_rounds
        self.delay = delay

    def run(
        self,
        stock_code: str,
        stock_name: str,
        research_results: Dict[str, AnalysisResult],
        verbose: bool = True,
    ) -> Tuple[List[str], List[BattleVote], float]:
        """
        执行辩论流程

        Returns:
            (transcript, votes, bullish_pct)
        """
        if verbose:
            print(f"\n⚔️  开始 Battle 辩论（{self.debate_rounds}轮）...")

        transcript: List[str] = []
        votes: List[BattleVote] = []

        # 构建共享的分析摘要（辩论基础）
        base_context = self._build_base_context(stock_code, stock_name, research_results)

        # 多轮辩论
        debate_history: List[str] = []
        for round_num in range(1, self.debate_rounds + 1):
            if verbose:
                print(f"  第 {round_num} 轮辩论...")

            # 多头发言
            bull_arg = self._make_argument(
                DEBATE_ROLES[0],
                base_context,
                debate_history,
                round_num,
            )
            debate_history.append(f"[多头-R{round_num}] {bull_arg}")
            transcript.append(f"🟢 **多头（第{round_num}轮）**：{bull_arg}")

            if self.delay > 0:
                time.sleep(self.delay)

            # 空头发言
            bear_arg = self._make_argument(
                DEBATE_ROLES[1],
                base_context,
                debate_history,
                round_num,
            )
            debate_history.append(f"[空头-R{round_num}] {bear_arg}")
            transcript.append(f"🔴 **空头（第{round_num}轮）**：{bear_arg}")

            if self.delay > 0:
                time.sleep(self.delay)

        # 裁判综合
        if verbose:
            print("  裁判综合判断...")
        judge_verdict = self._judge_verdict(
            DEBATE_ROLES[2],
            base_context,
            debate_history,
            stock_code,
            stock_name,
        )
        transcript.append(f"⚖️ **裁判综合**：{judge_verdict}")

        # 投票
        votes = self._collect_votes(
            stock_code, stock_name, base_context, debate_history, judge_verdict
        )

        # 计算看多百分比
        bullish_votes = sum(1 for v in votes if v.vote == "bullish")
        bullish_pct = bullish_votes / len(votes) * 100 if votes else 50.0

        if verbose:
            bull_count = sum(1 for v in votes if v.vote == "bullish")
            bear_count = sum(1 for v in votes if v.vote == "bearish")
            print(f"  投票结果：看多 {bull_count} | 看空 {bear_count} | 看多占比 {bullish_pct:.1f}%")

        return transcript, votes, bullish_pct

    def _build_base_context(
        self,
        stock_code: str,
        stock_name: str,
        research_results: Dict[str, AnalysisResult],
    ) -> str:
        lines = [f"=== {stock_code} {stock_name} 多维度分析摘要 ===\n"]
        dim_labels = {
            "technical": "技术面",
            "fund_flow": "资金流向",
            "risk":      "风险评估",
            "sector":    "板块分析",
        }
        for dim, result in research_results.items():
            if not result:
                continue
            label = dim_labels.get(dim, dim)
            emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(result.signal, "⚪")
            points_str = "; ".join(result.key_points[:3]) if result.key_points else "无"
            lines.append(
                f"{emoji} {label}：信号={result.signal}，置信度={result.confidence:.0%}\n"
                f"   要点：{points_str}"
            )
        return "\n".join(lines)

    def _make_argument(
        self,
        role: Dict,
        base_context: str,
        history: List[str],
        round_num: int,
    ) -> str:
        history_text = "\n".join(history[-4:]) if history else "（第一轮，无历史）"
        prompt = f"""
{base_context}

【辩论历史（最近）】
{history_text}

这是第 {round_num} 轮辩论。请根据对方论点，强化你的立场并反驳对方。
"""
        messages = [{"role": "user", "content": prompt}]
        return llm.ask(messages, system=role["system"], temperature=0.6)

    def _judge_verdict(
        self,
        role: Dict,
        base_context: str,
        history: List[str],
        stock_code: str,
        stock_name: str,
    ) -> str:
        history_text = "\n".join(history)
        prompt = f"""
{base_context}

【完整辩论记录】
{history_text}

请综合多空双方观点，对 {stock_code} {stock_name} 给出裁判综合意见。
"""
        messages = [{"role": "user", "content": prompt}]
        return llm.ask(messages, system=role["system"], temperature=0.3)

    def _collect_votes(
        self,
        stock_code: str,
        stock_name: str,
        base_context: str,
        history: List[str],
        judge_verdict: str,
    ) -> List[BattleVote]:
        """让5个模拟投票者独立给出信号"""
        VOTERS = [
            ("短线交易员", "你专注3-5日短线机会，对技术信号最敏感"),
            ("中线投资者", "你关注1-3个月走势，重视资金流和板块趋势"),
            ("风险控制官", "你优先考虑下行风险，宁可错过也不愿亏损"),
            ("多头辩手",   "你已充分表达了看多立场"),
            ("空头辩手",   "你已充分表达了看空立场"),
        ]

        full_context = (
            f"{base_context}\n\n【辩论记录】\n"
            + "\n".join(history)
            + f"\n\n【裁判意见】\n{judge_verdict}"
        )

        votes = []
        for voter_name, voter_desc in VOTERS:
            system = f"""你是{voter_name}。{voter_desc}。
基于以上所有信息，给出你对 {stock_code} {stock_name} 的最终投票。
只回复3行：
投票：bullish 或 bearish 或 neutral
理由：（一句话，30字以内）
结束"""
            messages = [{"role": "user", "content": full_context}]
            try:
                response = llm.ask(messages, system=system, temperature=0.2)
                vote_val = "neutral"
                reasoning = ""
                for line in response.split("\n"):
                    if "投票：" in line or "投票:" in line:
                        for v in ("bullish", "bearish", "neutral"):
                            if v in line.lower():
                                vote_val = v
                                break
                    if "理由：" in line or "理由:" in line:
                        reasoning = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                votes.append(BattleVote(
                    agent_name=voter_name,
                    vote=vote_val,
                    reasoning=reasoning or response[:50],
                ))
            except Exception as e:
                votes.append(BattleVote(
                    agent_name=voter_name,
                    vote="neutral",
                    reasoning=f"投票失败：{e}",
                ))
        return votes
