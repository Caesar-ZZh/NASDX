"""
market_regime.py — 市场环境判断

判断当前市场处于什么状态：牛市 / 熊市 / 震荡 / 结构性行情。
为选股提供宏观过滤器。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import akshare as ak
import pandas as pd


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def assess_market_regime() -> Dict[str, Any]:
    """
    综合判断市场环境。

    Returns:
        {
            "regime": "bullish" | "bearish" | "neutral" | "structural",
            "score": 0-100,
            "components": { ... },
            "summary": "描述文字",
        }
    """
    components = {}

    # 1. 主要指数趋势
    components["indices"] = _assess_indices()

    # 2. 市场涨跌家数比
    components["advance_decline"] = _assess_advance_decline()

    # 3. 全市场成交额
    components["volume"] = _assess_volume()

    # 4. 赚钱效应（涨停 / 跌停数）
    components["sentiment"] = _assess_sentiment()

    # 综合评分
    score = _composite_score(components)
    regime = _score_to_regime(score, components)

    return {
        "regime": regime,
        "score": score,
        "components": components,
        "summary": _format_summary(regime, score, components),
        "assessed_at": datetime.now().isoformat(),
    }


def _assess_indices() -> Dict[str, Any]:
    """判断主要指数趋势。"""
    indices = {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz399006": "创业板指",
        "sh000300": "沪深300",
    }

    result = {}
    for code, name in indices.items():
        prefix, symbol = code[:2], code[2:]
        hist = _safe(ak.stock_zh_index_daily, symbol=f"{prefix}{symbol}")
        if isinstance(hist, pd.DataFrame) and len(hist) >= 60:
            close = hist["close"].astype(float)
            ma5 = close.rolling(5).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1]
            ma60 = close.rolling(60).mean().iloc[-1]
            latest = close.iloc[-1]
            result[name] = {
                "close": round(float(latest), 2),
                "above_ma5": latest > ma5,
                "above_ma20": latest > ma20,
                "above_ma60": latest > ma60,
                "ma_alignment": "bullish" if ma5 > ma20 > ma60 else "bearish" if ma5 < ma20 < ma60 else "mixed",
            }
        # 即使失败也继续，不影响其他组件

    bullish_count = sum(
        1 for v in result.values()
        if v.get("above_ma20", False) and v.get("ma_alignment") == "bullish"
    )
    total = len(result) or 1
    return {"index_trend": "bullish" if bullish_count >= total * 0.75 else "bearish" if bullish_count <= total * 0.25 else "mixed"}


def _assess_advance_decline() -> Dict[str, Any]:
    """涨跌家数比。"""
    df = _safe(ak.stock_market_activity_legu)
    if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
        latest = df.iloc[-1]
        up = float(latest.get("上涨家数", 0))
        down = float(latest.get("下跌家数", 0))
        total = up + down or 1
        ratio = up / total
        return {
            "up": int(up),
            "down": int(down),
            "ratio": round(ratio, 3),
            "signal": "bullish" if ratio > 0.55 else "bearish" if ratio < 0.45 else "neutral",
        }
    return {"signal": "unknown"}


def _assess_volume() -> Dict[str, Any]:
    """全市场成交额判断。"""
    df = _safe(ak.stock_zh_a_spot_em)
    if df is not None and isinstance(df, pd.DataFrame):
        total_amount = df.get("成交额", pd.Series([0])).astype(float).sum()
        # 万亿以上 = 活跃
        yiwan = total_amount / 1e8
        return {
            "total_amount_yi": round(yiwan, 1),
            "signal": "bullish" if yiwan > 10000 else "bearish" if yiwan < 6000 else "neutral",
        }
    return {"signal": "unknown"}


def _assess_sentiment() -> Dict[str, Any]:
    """涨停 / 跌停情绪。"""
    result = {}

    # 涨停板
    df_limit_up = _safe(ak.stock_zh_market_alerts_em)
    if df_limit_up is not None and isinstance(df_limit_up, pd.DataFrame):
        result["limit_up_count"] = len(df_limit_up)
    else:
        # 备选：概念板块涨停
        df_con = _safe(ak.stock_board_concept_name_em)
        if df_con is not None and isinstance(df_con, pd.DataFrame):
            result["limit_up_count"] = 0  # 简化处理
        else:
            result["limit_up_count"] = None

    # 跌停板
    df_limit_down = _safe(ak.stock_zh_market_performance_em)
    if df_limit_down is not None and isinstance(df_limit_down, pd.DataFrame):
        result["limit_down_count"] = len(df_limit_down)
    else:
        result["limit_down_count"] = None

    if result.get("limit_up_count") is not None and result.get("limit_down_count") is not None:
        up = result["limit_up_count"]
        down = result["limit_down_count"]
        total = up + down or 1
        ratio = up / total
        result["signal"] = "bullish" if ratio > 3 else "bearish" if ratio < 0.5 else "neutral"
    else:
        result["signal"] = "unknown"

    return result


def _composite_score(components: Dict[str, Any]) -> int:
    """
    综合各组件得分，输出 0-100 的市场环境分。
    80+ = 牛市环境，60-80 = 震荡偏多，40-60 = 震荡，20-40 = 震荡偏空，<20 = 熊市。
    """
    score = 50  # 基准分

    # 指数趋势
    idx = components.get("indices", {}).get("index_trend", "mixed")
    score += {"bullish": 15, "neutral": 5, "mixed": 0, "bearish": -15}.get(idx, 0)

    # 涨跌家数
    ad = components.get("advance_decline", {}).get("signal", "neutral")
    score += {"bullish": 15, "neutral": 0, "bearish": -15, "unknown": 0}.get(ad, 0)

    # 成交额
    vol = components.get("volume", {}).get("signal", "neutral")
    score += {"bullish": 10, "neutral": 0, "bearish": -10}.get(vol, 0)

    # 情绪
    sent = components.get("sentiment", {}).get("signal", "neutral")
    score += {"bullish": 10, "neutral": 0, "bearish": -10, "unknown": 0}.get(sent, 0)

    return max(0, min(100, score))


def _score_to_regime(score: int, components: Dict[str, Any]) -> str:
    """将综合分映射到市场环境类型。"""
    # 结构性行情：指数混合但情绪好
    idx = components.get("indices", {}).get("index_trend", "mixed")
    if idx == "mixed" and score >= 45:
        return "structural"
    if score >= 75:
        return "bullish"
    if score <= 25:
        return "bearish"
    if score >= 50:
        return "neutral"
    return "mixed"


def _format_summary(regime: str, score: int, components: Dict[str, Any]) -> str:
    """生成人类可读的市场环境描述。"""
    labels = {
        "bullish": "牛市环境",
        "bearish": "熊市环境",
        "neutral": "震荡市",
        "structural": "结构性行情",
        "mixed": "震荡分化",
    }
    parts = [f"当前市场处于{labels.get(regime, regime)}（综合分 {score}/100）"]

    ad = components.get("advance_decline", {})
    if ad.get("signal") in ("bullish", "bearish"):
        up = ad.get("up", "?")
        down = ad.get("down", "?")
        parts.append(f"涨跌家数比 {up}:{down}（{ad['signal']}）")

    vol = components.get("volume", {})
    if vol.get("total_amount_yi"):
        parts.append(f"全市场成交额 {vol['total_amount_yi']} 万亿")

    return "；".join(parts)
