"""
scoring.py — 综合评分合成

将 technical_score、sector_score、momentum_score、
liquidity_score、risk_score 合成为 final_score。

评分公式：
  final_score =
    technical_score * 0.25
    + sector_score * 0.20
    + momentum_score * 0.15
    + liquidity_score * 0.10
    + fundamental_score * 0.10
    + valuation_score * 0.05
    + risk_score * 0.15
"""
from __future__ import annotations

from typing import Any, Dict, List


def compute_all_scores(stocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    为每只股票计算各项子评分和综合分。

    Args:
        stocks: 已计算因子的股票列表（来自 factors.py）

    Returns:
         enriched 列表，每项增加:
        technical_score, sector_score, momentum_score,
        liquidity_score, risk_score, fundamental_score,
        valuation_score, final_score
    """
    results = []
    for s in stocks:
        try:
            s["technical_score"] = _calc_technical_score(s)
            s["sector_score"] = s.get("sector_score", 50)
            s["momentum_score"] = _calc_momentum_score(s)
            s["liquidity_score"] = _calc_liquidity_score(s)
            s["risk_score"] = _calc_risk_score(s)
            s["fundamental_score"] = _calc_fundamental_score(s)
            s["valuation_score"] = _calc_valuation_score(s)
            s["final_score"] = _calc_final_score(s)
            results.append(s)
        except Exception:
            # 失败时给默认分
            s["final_score"] = 50
            s.setdefault("technical_score", 50)
            s.setdefault("sector_score", 50)
            s.setdefault("momentum_score", 50)
            s.setdefault("liquidity_score", 50)
            s.setdefault("risk_score", 50)
            s.setdefault("fundamental_score", 50)
            s.setdefault("valuation_score", 50)
            results.append(s)

    # 按 final_score 降序排列
    results.sort(key=lambda x: x.get("final_score", 50), reverse=True)
    return results


def _calc_technical_score(f: Dict[str, Any]) -> float:
    """
    技术面评分（0-100）。

    基于 MA 结构、MACD、RSI、布林带位置。
    复用 scan_etf50.py 的评分逻辑。
    """
    score = 0

    # MA 趋势（40 分）
    ma5 = f.get("ma5", 0)
    ma20 = f.get("ma20", 0)
    ma60 = f.get("ma60", 0)
    close = f.get("close", 0)

    if ma5 > ma20 and close > ma5:
        score += 25
    elif ma5 > ma20:
        score += 20
    elif ma5 > ma60:
        score += 10

    if ma20 > ma60:
        score += 15

    # MACD（25 分）
    macd = f.get("macd_bar", 0)
    if macd > 0.001:
        score += 25
    elif -0.003 <= macd <= 0.001:
        score += 14
    else:
        score += 3

    # RSI（20 分）
    rsi = f.get("rsi", 50)
    if 45 <= rsi <= 65:
        score += 20  # 健康区间
    elif 35 <= rsi < 45:
        score += 13  # 偏低但未超卖
    elif 20 <= rsi < 35:
        score += 8   # 超卖反弹机会
    elif 65 < rsi <= 75:
        score += 14  # 偏强但未过热
    # rsi > 75 不加分（超买）

    # 布林带位置（10 分）
    boll_pos = f.get("boll_position", 0.5)
    if 0.3 <= boll_pos <= 0.65:
        score += 10  # 中轨上方，有空间
    elif boll_pos < 0.3:
        score += 7   # 靠近下轨，可能反弹
    else:
        score += 4   # 靠近上轨，阻力大

    return min(100, round(score, 1))


def _calc_momentum_score(f: Dict[str, Any]) -> float:
    """
    动量评分（0-100）。

    基于 5 日 / 20 日涨幅和 RSI 斜率。
    """
    score = 50  # 基准分

    mom_5d = f.get("momentum_5d", 0)
    mom_20d = f.get("momentum_20d", 0)
    rsi = f.get("rsi", 50)

    # 5 日动量
    if mom_5d > 5:
        score += 20
    elif mom_5d > 2:
        score += 12
    elif mom_5d > 0:
        score += 5
    elif mom_5d > -2:
        score -= 5
    else:
        score -= 15

    # 20 日动量（中长期趋势）
    if mom_20d > 15:
        score += 15
    elif mom_20d > 5:
        score += 10
    elif mom_20d > 0:
        score += 5
    elif mom_20d > -5:
        score -= 5
    else:
        score -= 15

    # RSI 斜率（配合动量判断）
    if rsi > 70 and mom_5d > 3:
        score += 5   # 强势加速
    elif rsi > 75:
        score -= 10  # 超买，动量可能衰竭

    return max(0, min(100, round(score, 1)))


def _calc_liquidity_score(f: Dict[str, Any]) -> float:
    """
    流动性评分（0-100）。

    基于成交额和换手率。
    """
    score = 50
    amount = f.get("amount", 0)  # 当日成交额
    turnover = f.get("turnover", 0)  # 换手率

    if amount > 20e8:  # 20 亿以上
        score += 25
    elif amount > 10e8:  # 10 亿
        score += 15
    elif amount > 3e8:  # 3 亿
        score += 5
    elif amount < 1e8:  # 低于 1 亿，流动性差
        score -= 20

    if turnover > 10:
        score += 10
    elif turnover > 5:
        score += 5
    elif turnover < 0.5:
        score -= 15

    return max(0, min(100, round(score, 1)))


def _calc_risk_score(f: Dict[str, Any]) -> float:
    """
    风险评分（0-100），越高越安全。

    基于 RSI 极值、布林带位置、偏离度。
    """
    score = 50
    rsi = f.get("rsi", 50)
    boll_pos = f.get("boll_position", 0.5)
    close = f.get("close", 0)
    ma20 = f.get("ma20", 0)

    # RSI 极端值
    if rsi > 80:
        score -= 30  # 严重超买
    elif rsi > 70:
        score -= 15
    elif rsi < 20:
        score -= 20  # 可能退市或暴跌
    elif rsi < 30:
        score += 10  # 超卖反弹机会

    # 布林带位置
    if boll_pos > 0.9:
        score -= 15  # 极度靠近上轨
    elif boll_pos < 0.1:
        score += 10  # 极度靠近下轨（可能反弹）

    # 偏离 MA20
    if ma20 > 0 and close > ma20 * 1.3:
        score -= 15  # 偏离过大，有回归风险
    elif ma20 > 0 and close < ma20 * 0.7:
        score -= 20  # 跌幅过大

    return max(0, min(100, round(score, 1)))


def _calc_fundamental_score(f: Dict[str, Any]) -> float:
    """
    基本面评分（0-100）。

    基于营收增速、净利润增速（如果有数据）。
    如果没有基本面数据，返回 50（中性）。
    """
    # 当前全 A 实时数据不含基本面，返回中性
    return 50.0


def _calc_valuation_score(f: Dict[str, Any]) -> float:
    """
    估值评分（0-100）。

    基于 PE / PB。
    """
    pe = f.get("pe_ttm")
    pb = f.get("pb")

    if pe is None or pb is None:
        return 50.0  # 无数据，中性

    score = 50
    if pe > 0:
        if pe < 10:
            score += 25  # 低估
        elif pe < 20:
            score += 10
        elif pe < 50:
            score -= 5
        else:
            score -= 20  # 高估

    if pb > 0:
        if pb < 1:
            score += 15  # 破净
        elif pb < 3:
            score += 5
        elif pb > 10:
            score -= 15

    return max(0, min(100, round(score, 1)))


def _calc_final_score(f: Dict[str, Any]) -> float:
    """
    综合评分（加权公式）。

    final_score =
      technical_score * 0.25
      + sector_score * 0.20
      + momentum_score * 0.15
      + liquidity_score * 0.10
      + fundamental_score * 0.10
      + valuation_score * 0.05
      + risk_score * 0.15
    """
    ts = f.get("technical_score", 50)
    ss = f.get("sector_score", 50)
    ms = f.get("momentum_score", 50)
    ls = f.get("liquidity_score", 50)
    fs = f.get("fundamental_score", 50)
    vs = f.get("valuation_score", 50)
    rs = f.get("risk_score", 50)

    score = (
        ts * 0.25
        + ss * 0.20
        + ms * 0.15
        + ls * 0.10
        + fs * 0.10
        + vs * 0.05
        + rs * 0.15
    )
    return round(max(0, min(100, score)), 1)
