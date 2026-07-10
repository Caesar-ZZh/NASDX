"""
watchlist.py — 观察池生成

将过滤后的候选股分为：
- A 级候选：综合分高 + 风险低，优先跟踪
- B 级候选：综合分中等，可观察
- 回踩候选：趋势良好但短期回踩
- 突破候选：即将突破关键阻力
- 回避池：评分低但有潜在风险
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def generate_watchlist(
    stocks: List[Dict[str, Any]],
    n_a: int = 10,
    n_b: int = 15,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    生成分类观察池。

    Args:
        stocks: 已通过风险过滤的股票列表（已按 final_score 排序）
        n_a: A 级候选数量
        n_b: B 级候选数量

    Returns:
        {
            "tier_a": [...],
            "tier_b": [...],
            "pullback": [...],
            "breakout": [...],
            "avoid": [...],
        }
    """
    tier_a: List[Dict[str, Any]] = []
    tier_b: List[Dict[str, Any]] = []
    pullback: List[Dict[str, Any]] = []
    breakout: List[Dict[str, Any]] = []
    avoid: List[Dict[str, Any]] = []

    for s in stocks:
        score = s.get("final_score", 50)
        chg = s.get("change_pct", 0)
        rsi = s.get("rsi", 50)
        ma5 = s.get("ma5", 0)
        ma20 = s.get("ma20", 0)
        close = s.get("close", 0)
        vol_ratio = s.get("vol_ratio", 1)
        boll_pos = s.get("boll_position", 0.5)
        momentum_5d = s.get("momentum_5d", 0)

        # 分类逻辑
        if score >= 70 and rsi < 75:
            # 高分 + 未超买 → A 级
            tier_a.append(_enrich_category(s, "trend_breakout" if chg > 2 else "trend_pullback" if momentum_5d < 0 else "sector_leader"))
        elif score >= 55:
            # 中高分 → B 级
            tier_b.append(_enrich_category(s, "value_repair"))
        elif score >= 40:
            # 中等 → 看形态
            if _is_pullback(s):
                pullback.append(_enrich_category(s, "trend_pullback"))
            elif _is_breakout(s):
                breakout.append(_enrich_category(s, "trend_breakout"))
            else:
                tier_b.append(_enrich_category(s, "etf_alternative"))
        else:
            # 低分 → 回避
            avoid.append(_enrich_category(s, "avoid"))

        if len(tier_a) >= n_a and len(tier_b) >= n_b and len(pullback) >= 10 and len(breakout) >= 10 and len(avoid) >= 10:
            break

    # 确保避免池至少有内容
    if not avoid and len(stocks) > n_a + n_b:
        remaining = stocks[n_a + n_b:]
        avoid = [_enrich_category(s, "avoid") for s in remaining[:10]]

    return {
        "tier_a": tier_a[:n_a],
        "tier_b": tier_b[:n_b],
        "pullback": pullback[:10],
        "breakout": breakout[:10],
        "avoid": avoid[:10],
    }


def _is_pullback(s: Dict[str, Any]) -> bool:
    """判断是否为趋势回踩形态。"""
    close = s.get("close", 0)
    ma20 = s.get("ma20", 0)
    ma5 = s.get("ma5", 0)
    rsi = s.get("rsi", 50)
    chg = s.get("change_pct", 0)

    # 价格在 MA20 附近，短期回落但 RSI 未超卖
    if ma20 > 0 and abs(close - ma20) / ma20 < 0.05:
        if rsi > 30 and rsi < 60:
            return True
    # 短期回调但中期趋势向上
    if ma5 < ma20 and chg < -1 and chg > -5:
        if rsi > 35:
            return True
    return False


def _is_breakout(s: Dict[str, Any]) -> bool:
    """判断是否为突破形态。"""
    close = s.get("close", 0)
    ma5 = s.get("ma5", 0)
    ma20 = s.get("ma20", 0)
    vol_ratio = s.get("vol_ratio", 1)
    boll_pos = s.get("boll_position", 0.5)
    rsi = s.get("rsi", 50)

    # 放量突破 MA20
    if ma5 > ma20 and vol_ratio > 1.5 and close > ma20:
        return True
    # 布林带接近上轨 + 放量
    if boll_pos > 0.8 and vol_ratio > 1.5 and rsi < 75:
        return True
    return False


def _enrich_category(
    s: Dict[str, Any], candidate_type: str
) -> Dict[str, Any]:
    """为股票添加候选类型和入场/退出条件。"""
    s["candidate_type"] = candidate_type

    # 入场条件
    entry_conditions = _get_entry_condition(s, candidate_type)
    s["entry_condition"] = entry_conditions

    # 风险条件
    risk_conditions = _get_risk_condition(s)
    s["risk_condition"] = risk_conditions

    # 操作级别
    s["action_level"] = _get_action_level(s.get("final_score", 50), candidate_type)

    return s


def _get_entry_condition(s: Dict[str, Any], ctype: str) -> str:
    """根据候选类型生成入场条件。"""
    ma5 = s.get("ma5", 0)
    ma20 = s.get("ma20", 0)
    rsi = s.get("rsi", 50)
    close = s.get("close", 0)

    conditions = {
        "trend_breakout": f"放量突破 MA20（{ma20:.2f}）或量比 > 1.5",
        "trend_pullback": f"回踩 MA20（{ma20:.2f}）不破，RSI > 35",
        "sector_leader": f"板块持续走强，RSI 在 45-65 健康区间",
        "value_repair": f"RSI < 40 超卖区反弹，或跌破 MA20 后企稳",
        "etf_alternative": f"作为 ETF 替代标的，关注板块 ETF 表现",
        "avoid": "不建议入场",
    }
    return conditions.get(ctype, "等待信号确认")


def _get_risk_condition(s: Dict[str, Any]) -> str:
    """生成风险条件。"""
    ma20 = s.get("ma20", 0)
    rsi = s.get("rsi", 50)
    close = s.get("close", 0)

    risks = []
    if ma20 > 0:
        risks.append(f"跌破 MA20（{ma20:.2f}）止损")
    if rsi > 75:
        risks.append("RSI 超买，注意回调")
    if close > ma20 * 1.2 and ma20 > 0:
        risks.append("偏离 MA20 超过 20%，有回归风险")
    return "；".join(risks) if risks else "暂无明显风险"


def _get_action_level(score: float, ctype: str) -> str:
    """根据分数和类型给出操作级别。"""
    if ctype == "avoid":
        return "回避"
    if score >= 75:
        return "重点跟踪"
    if score >= 60:
        return "观察试错"
    if score >= 45:
        return "只观察"
    return "回避"
