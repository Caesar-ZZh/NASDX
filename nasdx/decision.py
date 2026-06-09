"""
Investment decision layer — turns agent signals into a concrete research plan.

This module is deterministic by design. LLM output explains the thesis, while
this layer standardizes action, sizing, risk caps, and review rules.
"""
from __future__ import annotations

from typing import Any, Dict

from nasdx.schema import AnalysisResult


RISK_PROFILES = {
    "conservative": {
        "label": "保守",
        "position_map": {
            "strong_bull": "10%-20%",
            "bull": "5%-10%",
            "neutral": "0%-5%",
            "bear": "0%-5%",
        },
        "note": "保守画像优先控制回撤，所有仓位建议自动下调。",
    },
    "balanced": {
        "label": "均衡",
        "position_map": {
            "strong_bull": "20%-35%",
            "bull": "10%-20%",
            "neutral": "0%-10%",
            "bear": "0%-10%",
        },
        "note": "均衡画像在机会和风险之间取中间仓位。",
    },
    "aggressive": {
        "label": "进取",
        "position_map": {
            "strong_bull": "25%-45%",
            "bull": "15%-30%",
            "neutral": "0%-15%",
            "bear": "0%-10%",
        },
        "note": "进取画像允许更高试错仓位，但遇到风险红灯仍会降仓。",
    },
}


def build_decision_plan(
    stock_code: str,
    stock_name: str,
    final_signal: str,
    bullish_pct: float,
    research_results: Dict[str, AnalysisResult],
    synthesis: AnalysisResult,
    risk_profile: str = "balanced",
    data_quality: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a standardized decision plan from multi-agent evidence."""
    profile_key = risk_profile if risk_profile in RISK_PROFILES else "balanced"
    profile = RISK_PROFILES[profile_key]
    position_map = profile["position_map"]
    data_quality = data_quality or {}

    risk = research_results.get("risk")
    technical = research_results.get("technical")
    fund_flow = research_results.get("fund_flow")
    sector = research_results.get("sector")
    chokepoint = research_results.get("chokepoint")

    risk_is_high = _is_bearish(risk, min_conf=0.55)
    tech_is_bullish = _is_bullish(technical, min_conf=0.55)
    flow_is_bullish = _is_bullish(fund_flow, min_conf=0.55)
    sector_is_bullish = _is_bullish(sector, min_conf=0.55)
    chokepoint_is_bullish = _is_bullish(chokepoint, min_conf=0.55)

    if final_signal == "bullish" and bullish_pct >= 75 and not risk_is_high:
        direction = "偏多"
        action = "分批布局"
        position_band = position_map["strong_bull"]
        horizon = "1-4周"
    elif final_signal == "bullish" and bullish_pct >= 60:
        direction = "谨慎偏多"
        action = "轻仓试错"
        position_band = position_map["bull"]
        horizon = "3-10个交易日"
    elif final_signal == "bearish" or bullish_pct <= 40:
        direction = "偏空"
        action = "回避或减仓"
        position_band = position_map["bear"]
        horizon = "等待下一次信号修复"
    else:
        direction = "震荡/证据不足"
        action = "观察等待"
        position_band = position_map["neutral"]
        horizon = "1-2周复核"

    risk_flags = []
    if data_quality.get("action_gate") == "refresh_required":
        risk_flags.append(data_quality.get("message", "行情数据需要刷新"))
        position_band = "0%-5%"
    elif data_quality.get("action_gate") == "position_cap":
        risk_flags.append(data_quality.get("message", "行情数据偏旧，仓位上限下调"))
        position_band = _cap_position(position_band, "0%-10%")
    if risk_is_high:
        risk_flags.append("风险维度偏空，仓位上限下调")
        position_band = _cap_position(position_band, "0%-15%")
    if technical and technical.signal == "bearish":
        risk_flags.append("技术面偏弱，需等待趋势修复")
    if fund_flow and fund_flow.signal == "bearish":
        risk_flags.append("资金流偏弱，避免追高")
    if chokepoint and chokepoint.signal == "neutral":
        risk_flags.append("供应链卡点证据不足，需公告/财报核验")
    if synthesis.confidence < 0.55:
        risk_flags.append("综合置信度偏低，只适合观察或小仓位验证")

    entry_conditions = _entry_conditions(
        direction=direction,
        tech_is_bullish=tech_is_bullish,
        flow_is_bullish=flow_is_bullish,
        sector_is_bullish=sector_is_bullish,
        chokepoint_is_bullish=chokepoint_is_bullish,
    )
    exit_conditions = _exit_conditions(direction)
    review_triggers = _review_triggers(chokepoint)

    evidence = {}
    for dim, result in research_results.items():
        if not result:
            continue
        evidence[dim] = {
            "signal": result.signal,
            "confidence": round(result.confidence, 3),
            "key_points": result.key_points[:3],
        }

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "direction": direction,
        "action": action,
        "position_band": position_band,
        "horizon": horizon,
        "risk_profile": profile_key,
        "risk_profile_label": profile["label"],
        "risk_profile_note": profile["note"],
        "data_quality": data_quality,
        "bullish_pct": round(bullish_pct, 1),
        "confidence": round(float(synthesis.confidence), 3),
        "entry_conditions": entry_conditions,
        "exit_conditions": exit_conditions,
        "review_triggers": review_triggers,
        "risk_flags": risk_flags or ["暂无强风险红灯，但仍需按仓位纪律执行"],
        "evidence": evidence,
        "note": "研究辅助，不保证收益；下单前需结合账户风险承受能力和最新公告/行情复核。",
    }


def format_decision_plan(plan: Dict[str, Any]) -> str:
    """Render decision plan as compact Chinese text for reports."""
    lines = [
        f"方向：{plan.get('direction', '未知')} · 动作：{plan.get('action', '观察')} · 仓位区间：{plan.get('position_band', '0%-10%')}",
        f"风险画像：{plan.get('risk_profile_label', '均衡')} · 周期：{plan.get('horizon', '待定')} · 看多占比：{plan.get('bullish_pct', 50):.1f}% · 综合置信度：{plan.get('confidence', 0.5):.0%}",
        "数据状态：" + plan.get("data_quality", {}).get("message", "未评估"),
        "入场条件：" + "；".join(plan.get("entry_conditions", [])[:3]),
        "退出/止损：" + "；".join(plan.get("exit_conditions", [])[:3]),
        "复核触发：" + "；".join(plan.get("review_triggers", [])[:3]),
        "风险红灯：" + "；".join(plan.get("risk_flags", [])[:3]),
        plan.get("note", ""),
    ]
    return "\n".join(line for line in lines if line)


def _is_bullish(result: AnalysisResult | None, min_conf: float) -> bool:
    return bool(result and result.signal == "bullish" and result.confidence >= min_conf)


def _is_bearish(result: AnalysisResult | None, min_conf: float) -> bool:
    return bool(result and result.signal == "bearish" and result.confidence >= min_conf)


def _cap_position(current: str, cap: str) -> str:
    order = {
        "0%-5%": 0,
        "0%-10%": 1,
        "0%-15%": 2,
        "5%-10%": 2,
        "10%-20%": 3,
        "15%-30%": 4,
        "20%-35%": 5,
        "25%-45%": 6,
    }
    return current if order.get(current, 99) <= order.get(cap, 99) else cap


def _entry_conditions(
    direction: str,
    tech_is_bullish: bool,
    flow_is_bullish: bool,
    sector_is_bullish: bool,
    chokepoint_is_bullish: bool,
) -> list[str]:
    if direction in ("偏空", "震荡/证据不足"):
        return [
            "等待最终信号转为偏多",
            "技术面重新站上关键均线",
            "资金流或板块强度出现同步改善",
        ]
    conditions = []
    conditions.append("不追高，优先等回踩或突破确认")
    conditions.append("技术面维持偏多" if tech_is_bullish else "技术面修复后再加仓")
    conditions.append("资金流继续净流入" if flow_is_bullish else "资金流转正后提高仓位")
    if sector_is_bullish:
        conditions.append("板块保持相对强势")
    if chokepoint_is_bullish:
        conditions.append("供应链卡点证据继续增强")
    return conditions


def _exit_conditions(direction: str) -> list[str]:
    if direction == "偏空":
        return ["已有持仓以减仓为主", "反弹但量能不足时降低仓位", "跌破近期支撑继续回避"]
    return [
        "跌破关键均线或前低时止损/降仓",
        "风险维度转 bearish 且置信度高于55%时复核",
        "涨幅兑现但资金流走弱时分批止盈",
    ]


def _review_triggers(chokepoint: AnalysisResult | None) -> list[str]:
    triggers = [
        "下一份财报/业绩预告",
        "重大合同、客户验证、产能扩张或融资公告",
        "板块从强转弱或主题拥挤度明显升高",
    ]
    if chokepoint and chokepoint.signal != "bullish":
        triggers.insert(0, "核验供应链卡点是否有官方披露支撑")
    return triggers
