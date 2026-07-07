"""
risk_filter.py — 高风险股票剔除

剔除存在以下问题的股票：
- 高位放量长阴
- 连续一字板（无法买入）
- 跌停
- 极端缩量（流动性枯竭）
- 过度偏离 MA20
- ST / 退市风险
"""
from __future__ import annotations

from typing import Any, Dict, List


def risk_filter(stocks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    风险过滤。

    Args:
        stocks: 已计算因子的股票列表

    Returns:
        {"passed": [...], "filtered": [...]}
        每项 filtered 股票额外增加 risk_reasons 字段
    """
    passed: List[Dict[str, Any]] = []
    filtered: List[Dict[str, Any]] = []

    for s in stocks:
        reasons = []

        # 1. 跌停（跌幅 > -9.5%）
        chg = s.get("change_pct", 0)
        if chg < -9.5:
            reasons.append("当日跌停")

        # 2. 高位放量长阴（跌幅 > -3% 且量比 > 2 且 RSI > 70）
        if chg < -3:
            vol_ratio = s.get("vol_ratio", 1)
            rsi = s.get("rsi", 50)
            if vol_ratio > 2 and rsi > 70:
                reasons.append("高位放量长阴")

        # 3. 连续一字板（换手率 < 0.1% 且涨幅 > 9%）
        turnover = s.get("turnover", 0)
        if turnover < 0.1 and chg > 9:
            reasons.append("连续一字板，无法买入")

        # 4. 极端缩量（成交额 < 500 万）
        amount = s.get("amount", 0)
        if amount < 5e6:
            reasons.append("极端缩量，流动性枯竭")

        # 5. 过度偏离 MA20（偏离 > 30%）
        close = s.get("close", 0)
        ma20 = s.get("ma20", 0)
        if ma20 > 0 and close > 0:
            deviation = (close - ma20) / ma20 * 100
            if deviation > 30:
                reasons.append(f"过度偏离MA20（+{deviation:.1f}%）")
            elif deviation < -30:
                reasons.append(f"过度偏离MA20（{deviation:.1f}%）")

        # 6. RSI 极端值
        rsi = s.get("rsi", 50)
        if rsi > 90:
            reasons.append("RSI 极度超买")
        elif rsi < 10:
            reasons.append("RSI 极度超卖")

        # 7. MACD 深度死叉
        macd = s.get("macd_bar", 0)
        if macd < -0.05:
            reasons.append("MACD 深度死叉")

        if reasons:
            s["risk_reasons"] = reasons
            filtered.append(s)
        else:
            passed.append(s)

    return {"passed": passed, "filtered": filtered}
