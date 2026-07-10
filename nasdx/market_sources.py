"""
Resilient A-share market data sources for NASDX.

The primary Eastmoney K-line endpoint is occasionally unstable behind local
proxy settings. This module keeps the rest of the project on a single normalized
Chinese-column DataFrame contract while trying several AkShare sources.
"""
from __future__ import annotations

from typing import Callable

import akshare as ak
import pandas as pd


HistFetcher = Callable[[str, str, str], pd.DataFrame | None]


def fetch_stock_hist(
    code: str,
    start_date: str,
    end_date: str,
    min_rows: int = 10,
) -> tuple[pd.DataFrame | None, str | None]:
    """Fetch A-share daily K-line data and normalize it to Chinese columns."""
    for source, fetcher in (
        ("tencent_hist_tx", _fetch_tencent_hist),
        ("sina_daily", _fetch_sina_daily),
        ("eastmoney_hist", _fetch_eastmoney_hist),
    ):
        try:
            df = fetcher(code, start_date, end_date)
        except Exception:
            continue
        if _is_usable(df, min_rows):
            return df, source
    return None, None


def last_trade_date(df: pd.DataFrame | None) -> str | None:
    """Return the latest K-line date as YYYY-MM-DD."""
    if df is None or df.empty or "日期" not in df.columns:
        return None
    value = df["日期"].iloc[-1]
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def _fetch_tencent_hist(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    df = ak.stock_zh_a_hist_tx(
        symbol=_prefixed_symbol(code),
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    normalized = pd.DataFrame(
        {
            "日期": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
            "开盘": pd.to_numeric(df["open"], errors="coerce"),
            "收盘": pd.to_numeric(df["close"], errors="coerce"),
            "最高": pd.to_numeric(df["high"], errors="coerce"),
            "最低": pd.to_numeric(df["low"], errors="coerce"),
            "成交量": pd.to_numeric(df["amount"], errors="coerce"),
        }
    )
    return _finalize(normalized)


def _fetch_sina_daily(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    df = ak.stock_zh_a_daily(
        symbol=_prefixed_symbol(code),
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    normalized = pd.DataFrame(
        {
            "日期": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
            "开盘": pd.to_numeric(df["open"], errors="coerce"),
            "收盘": pd.to_numeric(df["close"], errors="coerce"),
            "最高": pd.to_numeric(df["high"], errors="coerce"),
            "最低": pd.to_numeric(df["low"], errors="coerce"),
            "成交量": pd.to_numeric(df["volume"], errors="coerce"),
            "成交额": pd.to_numeric(df.get("amount"), errors="coerce"),
            "换手率": pd.to_numeric(df.get("turnover"), errors="coerce") * 100,
        }
    )
    return _finalize(normalized)


def _fetch_eastmoney_hist(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    return _finalize(df.copy())


def _prefixed_symbol(code: str) -> str:
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["日期", "收盘"]).copy()
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    for column in ("开盘", "收盘", "最高", "最低", "成交量", "成交额", "换手率"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.sort_values("日期").reset_index(drop=True)
    if "涨跌幅" not in df.columns:
        df["涨跌幅"] = df["收盘"].pct_change().fillna(0) * 100
    else:
        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce").fillna(0)
    return df


def _is_usable(df: pd.DataFrame | None, min_rows: int) -> bool:
    if not isinstance(df, pd.DataFrame) or len(df) < min_rows:
        return False
    required = {"日期", "收盘", "成交量", "涨跌幅"}
    return required.issubset(df.columns)
