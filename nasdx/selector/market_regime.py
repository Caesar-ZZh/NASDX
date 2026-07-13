"""Fast market-regime assessment using bounded Tencent data."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List

import pandas as pd

from nasdx.fast_market import fetch_histories


INDEX_SYMBOLS = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
}


def assess_market_regime(
    stocks: List[Dict[str, Any]] | None = None,
    *,
    history_fetcher: Callable = fetch_histories,
) -> Dict[str, Any]:
    stock_rows = stocks or []
    start = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    histories = history_fetcher(
        list(INDEX_SYMBOLS),
        start,
        end,
        request_timeout=4.0,
        max_workers=4,
    )
    components = {
        "indices": _assess_indices(histories),
        "advance_decline": _assess_advance_decline(stock_rows),
        "volume": _assess_volume(stock_rows),
        "sentiment": _assess_sentiment(stock_rows),
    }
    score = _composite_score(components)
    regime = _score_to_regime(score, components)
    return {
        "regime": regime,
        "score": score,
        "components": components,
        "summary": _format_summary(regime, score, components),
        "assessed_at": datetime.now().isoformat(),
    }


def _assess_indices(histories: dict) -> Dict[str, Any]:
    result = {}
    for code, name in INDEX_SYMBOLS.items():
        hist, _source = histories.get(code, (None, None))
        if not isinstance(hist, pd.DataFrame) or len(hist) < 60:
            continue
        close = pd.to_numeric(hist["收盘"], errors="coerce").dropna()
        if len(close) < 60:
            continue
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        latest = close.iloc[-1]
        result[name] = {
            "close": round(float(latest), 2),
            "above_ma20": bool(latest > ma20),
            "ma_alignment": "bullish" if ma5 > ma20 > ma60 else "bearish" if ma5 < ma20 < ma60 else "mixed",
        }
    bullish = sum(1 for value in result.values() if value["above_ma20"] and value["ma_alignment"] == "bullish")
    total = len(result)
    trend = "unknown" if total == 0 else "bullish" if bullish >= total * 0.75 else "bearish" if bullish <= total * 0.25 else "mixed"
    return {"index_trend": trend, "available": total}


def _assess_advance_decline(stocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    changes = [float(stock.get("change_pct", 0) or 0) for stock in stocks]
    up = sum(change > 0 for change in changes)
    down = sum(change < 0 for change in changes)
    total = len(changes)
    ratio = up / (up + down or 1)
    return {
        "up": up,
        "down": down,
        "total": total,
        "ratio": round(ratio, 3),
        "signal": "unknown" if total == 0 else "bullish" if ratio > 0.55 else "bearish" if ratio < 0.45 else "neutral",
    }


def _assess_volume(stocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    amount_yi = sum(float(stock.get("amount", 0) or 0) for stock in stocks) / 1e8
    return {
        "total_amount_yi": round(amount_yi, 1),
        "signal": "unknown" if not stocks else "bullish" if amount_yi > 10000 else "bearish" if amount_yi < 6000 else "neutral",
    }


def _assess_sentiment(stocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    changes = [float(stock.get("change_pct", 0) or 0) for stock in stocks]
    limit_up = sum(change >= 9.5 for change in changes)
    limit_down = sum(change <= -9.5 for change in changes)
    ratio = limit_up / (limit_down or 1)
    return {
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "signal": "unknown" if not changes else "bullish" if ratio > 3 else "bearish" if ratio < 0.5 else "neutral",
    }


def _composite_score(components: Dict[str, Any]) -> int:
    score = 50
    score += {"bullish": 15, "bearish": -15}.get(components["indices"].get("index_trend"), 0)
    score += {"bullish": 15, "bearish": -15}.get(components["advance_decline"].get("signal"), 0)
    score += {"bullish": 10, "bearish": -10}.get(components["volume"].get("signal"), 0)
    score += {"bullish": 10, "bearish": -10}.get(components["sentiment"].get("signal"), 0)
    return max(0, min(100, score))


def _score_to_regime(score: int, components: Dict[str, Any]) -> str:
    if score >= 70:
        return "bullish"
    if score <= 30:
        return "bearish"
    index_trend = components["indices"].get("index_trend")
    breadth = components["advance_decline"].get("signal")
    if index_trend != breadth and "unknown" not in {index_trend, breadth}:
        return "structural"
    return "neutral"


def _format_summary(regime: str, score: int, components: Dict[str, Any]) -> str:
    labels = {"bullish": "偏强", "bearish": "偏弱", "structural": "结构性", "neutral": "震荡"}
    available = components["indices"].get("available", 0)
    total = components["advance_decline"].get("total", 0)
    return f"市场{labels.get(regime, '震荡')}，综合分 {score}/100；指数 {available}/4，可用实时股票 {total} 只。"
