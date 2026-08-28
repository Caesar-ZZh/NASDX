"""策略实验室 API 路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import quant_service


router = APIRouter()


class BacktestRequest(BaseModel):
    universe: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(default_factory=lambda: ["momentum", "mean_reversion"])
    start: str | None = None
    end: str | None = None
    initial_capital: float = 100_000
    rebalance: str = "W"
    top_n: int = 3


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, TimeoutError):
        raise HTTPException(504, str(exc)) from exc
    raise HTTPException(502, f"量化计算暂不可用：{exc}") from exc


@router.post("/api/quant/backtest")
def quant_backtest(req: BacktestRequest):
    try:
        return {"data": quant_service.get_backtest(req.model_dump())}
    except Exception as exc:  # noqa: BLE001
        _raise_http(exc)


@router.get("/api/quant/etf50")
def quant_etf50(
    days: int = Query(252, ge=90, le=730),
    top_n: int = Query(5, ge=1, le=10),
    rebalance: str = Query("W"),
):
    try:
        return {"data": quant_service.get_etf50(days=days, top_n=top_n, rebalance=rebalance)}
    except Exception as exc:  # noqa: BLE001
        _raise_http(exc)
