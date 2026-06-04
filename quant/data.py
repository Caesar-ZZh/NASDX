"""
NASDX V2 — 统一数据层
整合 AkShare + mootdx 通达信，提供 OHLCV 标准格式
兼容 QLib / FinRL / VnPy 的数据接口
"""
from __future__ import annotations
import os, time, warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# 代理 patch（必须最先）
import requests as _req
_real_get = _req.get
def _patched_get(url, **kw):
    if 'eastmoney.com' in url:
        s = _req.Session(); s.trust_env = True
        return s.get(url, **kw)
    return _real_get(url, **kw)
_req.get = _patched_get

ROOT = Path(__file__).parent.parent


# ══════════════════════════════════════════
#  统一 OHLCV 数据获取
# ══════════════════════════════════════════
def get_ohlcv(
    code: str,
    days: int = 252,
    source: str = "akshare",
) -> pd.DataFrame:
    """
    获取标准 OHLCV 数据
    返回列：date, open, high, low, close, volume, change_pct
    index: datetime
    """
    end = datetime.now()
    start = end - timedelta(days=days)
    start_s = start.strftime("%Y%m%d")
    end_s   = end.strftime("%Y%m%d")

    df = None

    if source in ("akshare", "auto"):
        df = _get_akshare(code, start_s, end_s)

    if df is None and source in ("mootdx", "auto"):
        df = _get_mootdx(code, days)

    if df is None:
        return pd.DataFrame()

    # 标准化列名
    df = df.copy()
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "涨跌幅": "change_pct", "成交额": "amount",
    }
    df.rename(columns=col_map, inplace=True)

    # 确保有标准列
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = np.nan

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    df = df[["open", "high", "low", "close", "volume"] +
            [c for c in ["amount","change_pct"] if c in df.columns]]
    df = df.astype(float, errors="ignore")
    return df


def _get_akshare(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """通过 AkShare 获取历史 K 线"""
    try:
        import akshare as ak
        # 区分股票/ETF
        if code.startswith(("51","15","16","50","56","58","58","51")):
            df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                     start_date=start, end_date=end, adjust="")
        else:
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date=start, end_date=end, adjust="qfq")
        if isinstance(df, pd.DataFrame) and len(df) > 5:
            return df
    except Exception as e:
        pass
    return None


def _get_mootdx(code: str, days: int) -> Optional[pd.DataFrame]:
    """通过 mootdx 通达信获取历史 K 线"""
    try:
        from mootdx.quotes import Quotes
        api = Quotes.factory(market="std", bestip=True, timeout=8)
        df = api.bars(symbol=code, frequency=9, offset=days)  # 9=日K
        if isinstance(df, pd.DataFrame) and len(df) > 5:
            df["date"] = pd.to_datetime(df["datetime"]).dt.date.astype(str)
            return df
    except Exception as e:
        pass
    return None


def get_batch_ohlcv(codes: list[str], days: int = 252) -> dict[str, pd.DataFrame]:
    """批量获取多只股票/ETF 的 OHLCV"""
    results = {}
    for code in codes:
        df = get_ohlcv(code, days=days)
        if not df.empty:
            results[code] = df
        time.sleep(0.3)
    return results


def get_realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """实时行情（mootdx 通达信）"""
    try:
        from ths_bridge import get_realtime_batch
        return get_realtime_batch(codes)
    except Exception:
        pass
    try:
        import akshare as ak
        spot = ak.fund_etf_spot_em()
        result = {}
        for _, r in spot.iterrows():
            c = str(r.get("代码",""))
            if c in codes:
                try:
                    result[c] = {
                        "price":  float(r.get("最新价",0)),
                        "chg":    float(r.get("涨跌幅",0)),
                        "volume": float(r.get("成交额",0)),
                    }
                except: pass
        return result
    except Exception:
        return {}
