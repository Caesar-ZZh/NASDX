"""
Bull/Bear 对抗结论提炼（TradingAgents 借鉴：多空辩论结构化对抗）

不重新调用 LLM，仅解析已有 battle transcript，提炼结构化反方观点，
使最终报告自带「反方论点 + 量化反驳」。零额外 LLM 成本、高可逆。
（NASDX 的 NasdxAnalyzer 已在 Battle Phase 产出 transcript/votes，本模块做轻量结构化。）
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_BULL_HINTS = ("看多", "多头", "bull", "利好", "上涨", "支撑", "买入")
_BEAR_HINTS = ("看空", "空头", "bear", "利空", "下跌", "压力", "卖出")


def _coerce_text(value: Any) -> str:
    """统一为文本：接受 str / list[str] / 其他；list 按行 join。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x) for x in value)
    return str(value)


def _classify(line: str) -> str:
    s = line.lower()
    bull = sum(h.lower() in s for h in _BULL_HINTS)
    bear = sum(h.lower() in s for h in _BEAR_HINTS)
    if bear > bull:
        return "bear"
    if bull > bear:
        return "bull"
    return "neutral"


def summarize_counter_argument(
    transcript: Any, votes: Any = None
) -> Dict[str, Any]:
    """解析辩论文本，产出结构化反方观点。transcript 可为 str 或 list[str]。"""
    text = _coerce_text(transcript)
    if not text.strip():
        return {"available": False, "bear_points": [], "bull_points": [], "rebuttal": "",
                "vote_bullish_pct": None}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    bear_points: List[str] = []
    bull_points: List[str] = []
    for ln in lines:
        role = _classify(ln)
        if role == "bear":
            bear_points.append(ln[:200])
        elif role == "bull":
            bull_points.append(ln[:200])
    rebuttal = "；".join(bear_points[:3])
    bullish_pct = votes.get("bullish_pct") if isinstance(votes, dict) else None
    return {
        "available": True,
        "bear_points": bear_points[:5],
        "bull_points": bull_points[:5],
        "rebuttal": rebuttal,
        "vote_bullish_pct": bullish_pct,
    }


def format_counter_argument_block(summary: Dict[str, Any]) -> str:
    """渲染为可读段落，供 run_analysis.py 打印/附加。"""
    if not summary.get("available"):
        return ""
    lines = ["【反方观点与量化反驳（对抗校准）】"]
    for p in summary.get("bear_points", []):
        lines.append(f"  ▪ 空方：{p}")
    for p in summary.get("bull_points", []):
        lines.append(f"  ▪ 多方：{p}")
    if summary.get("rebuttal"):
        lines.append(f"  量化反驳依据：{summary['rebuttal']}")
    return "\n".join(lines)
