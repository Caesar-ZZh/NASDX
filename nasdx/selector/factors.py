"""
factors.py — 个股因子计算

计算趋势、动量、相对强弱、量能、换手、RSI、MACD、布林位置、均线结构等因子。
复用 fetch_stock_data.py.compute_indicators 的字段命名规范。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

from nasdx.selector.sector_strength import get_top_sectors


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def compute_factors_for_stocks(
    stocks: List[Dict[str, Any]],
    sector_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    批量计算股票因子。

    Args:
        stocks: 股票列表，每项含 code, name, close, change_pct, amount 等
        sector_map: code -> sector_name 映射（来自板块成分股查询）

    Returns:
         enriched 股票列表，每项增加:
        ma5, ma10, ma20, ma60, rsi, macd_bar, vol_ratio, boll_position,
        momentum_5d, momentum_20d, relative_strength, trend_score, etc.
    """
    top_sectors = get_top_sectors(10)  # 缓存板块强度，避免重复请求
    sector_scores = {s["board_name"]: s["strength_score"] for s in top_sectors}

    results = []
    for i, stock in enumerate(stocks):
        code = stock.get("code", "")
        try:
            # 获取 K 线数据（90 日）
            hist = _safe(ak.stock_zh_a_hist, symbol=code, period="daily",
                         start_date=(datetime.now() - timedelta(days=90)).strftime("%Y%m%d"),
                         end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")

            factors = {}
            if isinstance(hist, pd.DataFrame) and len(hist) >= 20:
                factors = _compute_technical_factors(hist, code)
                # 关联板块强度
                sector = _find_sector_for_stock(code, hist, sector_scores)
                factors["sector_score"] = sector.get("strength_score", 50) if sector else 50
            else:
                factors = _fallback_factors(stock)

            factors["code"] = code
            factors["name"] = stock.get("name", code)
            factors["close"] = stock.get("close", 0)
            factors["change_pct"] = stock.get("change_pct", 0)
            factors["amount"] = stock.get("amount", 0)
            factors["turnover"] = stock.get("turnover", 0)

            # 综合评分（在 scoring.py 中计算，这里先放占位）
            results.append(factors)

        except Exception:
            # 单个股票失败不影响整体
            results.append({
                **stock,
                "code": code,
                "name": stock.get("name", code),
                "technical_score": 50,
                "sector_score": 50,
                "momentum_score": 50,
                "liquidity_score": 50,
                "risk_score": 50,
                "final_score": 50,
            })

        # 限速，避免被东方财富封 IP
        if i % 10 == 0 and i > 0:
            import time
            time.sleep(0.3)

    return results


def _compute_technical_factors(
    hist: pd.DataFrame, code: str
) -> Dict[str, Any]:
    """从 K 线 DataFrame 计算所有技术指标因子。"""
    close = hist["收盘"].astype(float)
    volume = hist["成交量"].astype(float)
    high = hist["最高"].astype(float)
    low = hist["最低"].astype(float)
    pct_change = hist["涨跌幅"].astype(float) if "涨跌幅" in hist.columns else close.pct_change() * 100

    n = len(close)

    # 均线系统
    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1] if n >= 10 else ma5
    ma20 = close.rolling(20).mean().iloc[-1] if n >= 20 else ma5
    ma60 = close.rolling(60).mean().iloc[-1] if n >= 60 else ma5

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    macd_val = float(macd_bar.iloc[-1]) if n > 0 else 0

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = float((100 - 100 / (1 + gain / (loss + 1e-9))).iloc[-1]) if n >= 14 else 50

    # 布林带位置
    boll_mid = close.rolling(20).mean()
    boll_std = close.rolling(20).std()
    boll_upper = boll_mid + 2 * boll_std
    boll_lower = boll_mid - 2 * boll_std
    if n >= 20 and (float(boll_upper.iloc[-1]) - float(boll_lower.iloc[-1])) > 0:
        boll_pos = (float(close.iloc[-1]) - float(boll_lower.iloc[-1])) / (float(boll_upper.iloc[-1]) - float(boll_lower.iloc[-1]))
    else:
        boll_pos = 0.5

    # 量比（当日成交量 / 5 日均量）
    vol_5ma = volume.rolling(5).mean().iloc[-2] if n >= 3 else float(volume.iloc[-1])
    vol_ratio = float(volume.iloc[-1]) / vol_5ma if vol_5ma > 0 else 1.0

    # 动量
    momentum_5d = float((close.iloc[-1] / close.iloc[-5] - 1) * 100) if n >= 5 else 0
    momentum_20d = float((close.iloc[-1] / close.iloc[-20] - 1) * 100) if n >= 20 else 0

    # 均线结构分
    trend_score = _calc_trend_score(ma5, ma10, ma20, ma60, close.iloc[-1])

    # 换手率
    avg_amount_20 = float(volume.tail(20).mean()) if n >= 20 else float(volume.iloc[-1])
    latest_amount = float(volume.iloc[-1])
    avg_price = float(close.iloc[-1])
    turnover = (latest_amount / (avg_price * 100)) if avg_price > 0 else 0  # 简化估算

    return {
        "ma5": round(float(ma5), 3),
        "ma10": round(float(ma10), 3),
        "ma20": round(float(ma20), 3),
        "ma60": round(float(ma60), 3),
        "rsi": round(rsi, 2),
        "macd_bar": round(macd_val, 4),
        "vol_ratio": round(vol_ratio, 2),
        "boll_position": round(boll_pos, 3),
        "momentum_5d": round(momentum_5d, 2),
        "momentum_20d": round(momentum_20d, 2),
        "trend_score": round(trend_score, 1),
        "turnover": round(turnover, 4),
    }


def _calc_trend_score(ma5: float, ma10: float, ma20: float, ma60: float, close: float) -> float:
    """
    计算均线结构分（0-100）。
    多头排列 = 100，空头排列 = 0，混合 = 50。
    """
    score = 50
    if ma5 > ma10 > ma20 and close > ma5:
        score += 30  # 完美多头
    elif ma5 > ma10 and close > ma5:
        score += 15
    elif ma5 > ma20:
        score += 5
    if ma5 < ma10 < ma20 and close < ma5:
        score -= 30  # 完美空头
    elif ma5 < ma10 and close < ma5:
        score -= 15
    elif ma5 < ma20:
        score -= 5
    return max(0, min(100, score))


def _fallback_factors(stock: Dict[str, Any]) -> Dict[str, Any]:
    """当 K 线数据获取失败时的降级方案。"""
    close = stock.get("close", 0)
    chg = stock.get("change_pct", 0)
    return {
        "ma5": close, "ma10": close, "ma20": close, "ma60": close,
        "rsi": 50, "macd_bar": 0, "vol_ratio": 1.0,
        "boll_position": 0.5, "momentum_5d": chg, "momentum_20d": chg * 4,
        "trend_score": 50 + chg * 2, "turnover": stock.get("turnover", 0),
    }


def _find_sector_for_stock(
    code: str, hist: pd.DataFrame, sector_scores: Dict[str, float]
) -> Optional[Dict[str, Any]]:
    """
    简易板块关联：根据股票名称匹配最强板块。
    实际项目中可对接 akshare 的板块成分股接口做精确匹配。
    """
    # 这里简化处理：返回最强板块的分数
    if not sector_scores:
        return None
    # 返回板块强度分数的平均值作为 sector_score
    avg = sum(sector_scores.values()) / len(sector_scores)
    return {"strength_score": round(avg, 2)}
