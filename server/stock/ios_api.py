"""NASDX iOS 专用契约层（/api/v1/ios/*）。

设计目标：
- 复用 server/stock 的 astock / market / portfolio，不重复数据逻辑。
- 重塑为移动端友好的 camelCase JSON，收敛字段、便于 Swift `Codable` 直解。
- 每个数据源独立 try/except：单点源异常只影响该字段，整体返回 `partial: true` 而非 500。

接入方式：在 base_app.py 末尾 `app.include_router(ios_api.router)`。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

import astock
import market
import portfolio

router = APIRouter(prefix="/api/v1/ios", tags=["ios"])

_API_VERSION = "1.0.0"


def _ok(payload: dict) -> dict:
    return {"ok": True, "partial": False, **payload}


def _partial(payload: dict, missing: list[str]) -> dict:
    return {"ok": True, "partial": True, "missing": missing, **payload}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@router.get("/health")
def health():
    return {"ok": True, "service": "nasdx-ios", "version": _API_VERSION}


# ---------------------------------------------------------------------------
# 批量实时行情（复用 astock.tencent_quote）
# ---------------------------------------------------------------------------
@router.get("/quote")
def quote(codes: str = Query(..., description="逗号分隔的 6 位代码，如 600519,000001")):
    lst = [c.strip() for c in codes.split(",") if c.strip()]
    if not lst or any(not c.isdigit() or len(c) != 6 for c in lst):
        from fastapi import HTTPException
        raise HTTPException(400, "codes 必须是逗号分隔的 6 位数字")
    try:
        raw = astock.tencent_quote(lst)
    except Exception as e:  # noqa: BLE001 — 边界统一兜底
        from fastapi import HTTPException
        raise HTTPException(502, f"行情源异常：{e}") from e

    items = []
    for code, q in raw.items():
        items.append({
            "code": code,
            "name": q.get("name", ""),
            "price": q.get("price", 0.0),
            "lastClose": q.get("last_close", 0.0),
            "open": q.get("open", 0.0),
            "changeAmt": q.get("change_amt", 0.0),
            "changePct": q.get("change_pct", 0.0),
            "high": q.get("high", 0.0),
            "low": q.get("low", 0.0),
            "amountWan": q.get("amount_wan", 0.0),
            "turnoverPct": q.get("turnover_pct", 0.0),
            "peTtm": q.get("pe_ttm", 0.0),
            "peStatic": q.get("pe_static", 0.0),
            "pb": q.get("pb", 0.0),
            "amplitudePct": q.get("amplitude_pct", 0.0),
            "mcapYi": q.get("mcap_yi", 0.0),
            "floatMcapYi": q.get("float_mcap_yi", 0.0),
            "limitUp": q.get("limit_up", 0.0),
            "limitDown": q.get("limit_down", 0.0),
            "volRatio": q.get("vol_ratio", 0.0),
        })
    return _ok({"quotes": items})


# ---------------------------------------------------------------------------
# K线（复用 astock.kline，mootdx bars）
# ---------------------------------------------------------------------------
@router.get("/kline/{code}")
def kline(code: str, category: int = 4, offset: int = 60):
    if not code.isdigit() or len(code) != 6:
        from fastapi import HTTPException
        raise HTTPException(400, "code 必须是 6 位数字")
    try:
        raw = astock.kline(code, category=category, offset=offset)
        bars = []
        for r in raw:
            get = lambda k, d: r.get(k, r.get(d))  # 兼容不同列名大小写
            bars.append({
                "date": str(r.get("date", "")),
                "open": _to_float(get("open", "open")),
                "high": _to_float(get("high", "high")),
                "low": _to_float(get("low", "low")),
                "close": _to_float(get("close", "close")),
                "volume": _to_float(get("volume", "vol")),
                "amount": _to_float(get("amount", "amount")),
            })
        return _ok({"code": code, "category": category, "bars": bars})
    except Exception as e:  # noqa: BLE001
        from fastapi import HTTPException
        if "DependencyMissing" in type(e).__name__:
            raise HTTPException(503, f"K线数据源不可用（mootdx 未安装）：{e}") from e
        raise HTTPException(502, f"K线异常：{e}") from e


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# 市场总览（指数 + 情绪 + 成交额榜）
# ---------------------------------------------------------------------------
@router.get("/market/overview")
def market_overview():
    missing = []
    overview = None
    turnover = None
    try:
        overview = market.get_overview()
    except Exception:  # noqa: BLE001
        missing.append("overview")
    try:
        turnover = market.get_turnover_top()
    except Exception:  # noqa: BLE001
        missing.append("turnoverTop")
    payload = {"overview": overview, "turnoverTop": turnover}
    return _partial(payload, missing) if missing else _ok(payload)


# ---------------------------------------------------------------------------
# 自选快照（批量 quote 聚合，便于客户端一次拉全部自选）
# ---------------------------------------------------------------------------
@router.get("/watchlist")
def watchlist(codes: str = Query("", description="逗号分隔的 6 位代码，可空")):
    lst = [c.strip() for c in codes.split(",") if c.strip()]
    if not lst:
        return _ok({"quotes": []})
    try:
        raw = astock.tencent_quote(lst)
    except Exception as e:  # noqa: BLE001
        from fastapi import HTTPException
        raise HTTPException(502, f"行情源异常：{e}") from e
    items = [{
        "code": c,
        "name": q.get("name", ""),
        "price": q.get("price", 0.0),
        "changePct": q.get("change_pct", 0.0),
        "changeAmt": q.get("change_amt", 0.0),
    } for c, q in raw.items()]
    return _ok({"quotes": items})


# ---------------------------------------------------------------------------
# 持仓概览（复用 portfolio.get_portfolio）
# ---------------------------------------------------------------------------
@router.get("/portfolio")
def portfolio_view():
    try:
        data = portfolio.get_portfolio()
        return _ok({"portfolio": data})
    except Exception as e:  # noqa: BLE001
        from fastapi import HTTPException
        raise HTTPException(502, f"持仓数据异常：{e}") from e
