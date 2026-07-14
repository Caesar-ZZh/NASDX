"""
universe.py — 全 A 股票池加载与过滤

从交易所官方全 A 列表出发，过滤 ST、停牌、低成交额、低价股、
上市时间过短、流动性差的标的，得到可交易股票池。
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

from nasdx.fast_market import (
    fetch_tencent_quotes,
    get_listing_coverage,
    load_a_share_listings,
)
from nasdx.market_symbols import resolve_exchange
from nasdx.paths import get_reports_dir


_LAST_UNIVERSE_COVERAGE: dict = {}


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def load_full_a_stocks() -> List[Dict[str, Any]]:
    """
    从交易所官方列表获取全 A 股票，并合并腾讯实时行情。

    Returns:
        全 A 股票基础信息列表，每项包含 code, name, exchange 等。
    """
    global _LAST_UNIVERSE_COVERAGE
    listings = load_a_share_listings()
    listing_coverage = get_listing_coverage()
    if not listings:
        listings = _load_local_fallback_listings()
    normalized_listings = [
        {**item, "exchange": item.get("exchange") or resolve_exchange(item.get("code", ""))}
        for item in listings
    ]
    quotes = fetch_tencent_quotes([item["code"] for item in normalized_listings])
    results: List[Dict[str, Any]] = []
    listed_counts = {"SSE": 0, "SZSE": 0, "BSE": 0}
    quoted_counts = {"SSE": 0, "SZSE": 0, "BSE": 0}
    for item in normalized_listings:
        exchange = item["exchange"]
        if exchange in listed_counts:
            listed_counts[exchange] += 1
        quote = quotes.get(item["code"])
        if not quote:
            continue
        if exchange in quoted_counts:
            quoted_counts[exchange] += 1
        results.append({**item, **quote, "pe_ttm": None, "pb": None})

    missing_quotes = [
        exchange
        for exchange, count in listed_counts.items()
        if count > 0 and quoted_counts[exchange] == 0
    ]
    _LAST_UNIVERSE_COVERAGE = {
        **listing_coverage,
        "complete": bool(listing_coverage.get("complete")) and not missing_quotes,
        "counts": listed_counts,
        "quoted_counts": quoted_counts,
        "quote_unavailable_exchanges": missing_quotes,
    }
    return results


def get_universe_coverage() -> dict:
    """Return a detached snapshot of the latest listing and quote coverage."""
    return json.loads(json.dumps(_LAST_UNIVERSE_COVERAGE)) if _LAST_UNIVERSE_COVERAGE else {}


def _load_local_fallback_listings() -> List[Dict[str, Any]]:
    path = Path(__file__).resolve().parents[2] / "stocks.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    listings: dict[str, dict] = {}
    for sector in payload.get("sectors", []):
        sector_name = str(sector.get("name", "本地股票池"))
        for stock in sector.get("stocks", []):
            code = str(stock.get("code", "")).strip()
            if code:
                listings[code] = {
                    "code": code,
                    "name": str(stock.get("name", code)),
                    "sector": sector_name,
                    "exchange": resolve_exchange(code),
                }
    return list(listings.values())


def filter_universe(
    stocks: List[Dict[str, Any]],
    min_amount: float = 3e7,    # 最低成交额 3000 万
    min_price: float = 2.0,     # 最低价 2 元
    max_price: float = 200.0,   # 最高价 200 元
    exclude_st: bool = True,
    exclude_bj: bool = False,   # 是否排除北交所
    exclude_kcb: bool = False,  # 是否排除科创板（数据不全）
) -> List[Dict[str, Any]]:
    """
    过滤股票池。

    Args:
        stocks: 全 A 股票列表（来自 load_full_a_stocks）
        min_amount: 最低日均成交额（元）
        min_price: 最低股价
        max_price: 最高股价
        exclude_st: 是否排除 ST / *ST
        exclude_bj: 是否排除北交所（4/8/920 开头）
        exclude_kcb: 是否排除科创板（688 开头，资金流数据缺失）

    Returns:
        过滤后的股票列表
    """
    filtered: List[Dict[str, Any]] = []

    for s in stocks:
        code = s.get("code", "")
        name = s.get("name", "")
        close = s.get("close", 0)
        amount = s.get("amount", 0)

        # 价格过滤
        if close < min_price or close > max_price:
            continue

        # 成交额过滤
        if amount < min_amount:
            continue

        # ST 过滤
        if exclude_st and ("ST" in name or "*ST" in name or name.startswith("ST")):
            continue

        # 北交所过滤
        if exclude_bj and (s.get("exchange") or resolve_exchange(code)) == "BSE":
            continue

        # 科创板过滤
        if exclude_kcb and code.startswith("688"):
            continue

        filtered.append(s)

    return filtered


def load_etf_universe() -> List[Dict[str, Any]]:
    """
    从东方财富获取全市场 ETF 列表。

    Returns:
        ETF 列表，每项包含 code, name, close, amount 等。
    """
    df = _safe(ak.fund_etf_spot_em)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    results: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            code = str(row.get("代码", ""))
            name = str(row.get("名称", ""))
            price = float(row.get("最新价", 0))
            amount = float(row.get("成交额", 0))

            results.append({
                "code": code,
                "name": name,
                "close": price,
                "amount": amount,
                "type": "etf",
            })
        except Exception:
            continue

    return results


def get_sector_list() -> List[Dict[str, str]]:
    """
    获取行业/概念板块列表（东方财富）。

    Returns:
        板块列表，每项包含 {board_code, board_name}。
    """
    # 行业板块
    df_ind = _safe(ak.stock_board_industry_name_em)
    df_con = _safe(ak.stock_board_concept_name_em)

    sectors: List[Dict[str, str]] = []
    for df, btype in [(df_ind, "industry"), (df_con, "concept")]:
        if df is None or not isinstance(df, pd.DataFrame):
            continue
        for _, row in df.iterrows():
            try:
                sectors.append({
                    "board_code": str(row.get("板块代码", "")),
                    "board_name": str(row.get("板块名称", "")),
                    "type": btype,
                })
            except Exception:
                continue

    return sectors


def save_universe(stocks: List[Dict], etfs: List[Dict], path: Optional[Path] = None):
    """保存筛选后的宇宙列表到本地 JSON。"""
    out = path or (get_reports_dir(create=True) / "universe_latest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now().isoformat(),
        "stock_count": len(stocks),
        "etf_count": len(etfs),
        "stocks": stocks[:500],  # 只存头部，避免文件过大
        "etfs": etfs,
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)
