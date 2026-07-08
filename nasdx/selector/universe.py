"""
universe.py — 全 A 股票池加载与过滤

从东方财富全 A 列表出发，过滤 ST、停牌、低成交额、低价股、
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

from nasdx.paths import get_reports_dir


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def load_full_a_stocks() -> List[Dict[str, Any]]:
    """
    从东方财富获取全 A 股票列表。

    Returns:
        全 A 股票基础信息列表，每项包含 code, name, market 等。
    """
    df = _safe(ak.stock_zh_a_spot_em)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    results: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            code = str(row.get("代码", ""))
            name = str(row.get("名称", ""))
            close = float(row.get("最新价", 0))
            chg = float(row.get("涨跌幅", 0))
            amount = float(row.get("成交额", 0))
            turnover = float(row.get("换手率", 0))
            pe = float(row.get("市盈率-动态", 0)) if row.get("市盈率-动态") else None
            pb = float(row.get("市净率", 0)) if row.get("市净率") else None

            results.append({
                "code": code,
                "name": name,
                "close": close,
                "change_pct": chg,
                "amount": amount,
                "turnover": turnover,
                "pe_ttm": pe,
                "pb": pb,
            })
        except Exception:
            continue

    return results


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
        exclude_bj: 是否排除北交所（8/4 开头）
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
        if exclude_bj and (code.startswith("8") or code.startswith("4")):
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
