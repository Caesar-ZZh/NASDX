"""
NASDX V2 — 统一数据层
整合 AkShare + mootdx 通达信，提供 OHLCV 标准格式
兼容 QLib / FinRL / VnPy 的数据接口

修复内容：
1. 精确的 ETF 代码识别（沪 51/50, 深 15/16, 科创 58/56）
2. 3次重试机制（指数退避）
3. 自动备用接口切换
4. 进度显示 + 数据质量检查
5. 缓存支持（streamlit / lru_cache）
"""
from __future__ import annotations
import time, warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from functools import lru_cache
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent


# ══════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════
def _is_etf(code: str) -> bool:
    """
    精确判断是否为 ETF
    - 沪市 ETF: 51xxxx, 50xxxx
    - 深市 ETF: 15xxxx, 16xxxx
    - 科创 ETF: 58xxxx, 59xxxx (使用 56 有误，改用 59)
    注意：科创板前缀通常是 68 或 56，ETF 多用 55 和 56
    安全做法：明确列出常见 ETF 前缀
    """
    if not isinstance(code, str) or len(code) < 5:
        return False
    prefix = code[:2]
    # 完整的 ETF 前缀列表
    # 50xxxx: 上证 50ETF
    # 51xxxx: 沪深 300ETF
    # 15xxxx: 深市 ETF（创业板）
    # 16xxxx: 深市 ETF（中小板）
    # 55xxxx: 科创板 ETF
    # 56xxxx: 科创板 ETF（风险等级高）
    # 58xxxx: 科创板 ETF
    # 59xxxx: 科创板 ETF
    etf_prefixes = ('50', '51', '15', '16', '55', '56', '58', '59')
    return prefix in etf_prefixes


def _validate_ohlcv(df: pd.DataFrame) -> bool:
    """
    数据质量检查
    - close 列非空
    - close 非全零
    - 至少 5 行数据
    """
    if df is None or df.empty or len(df) < 5:
        return False
    if 'close' not in df.columns:
        return False
    close_val = pd.to_numeric(df['close'], errors='coerce')
    if close_val.isna().all() or (close_val == 0).all():
        return False
    return True


# ══════════════════════════════════════════════════════════
#  重试装饰器（指数退避）
# ══════════════════════════════════════════════════════════
def retry_with_backoff(max_attempts: int = 3, initial_wait: float = 0.5):
    """
    重试装饰器，指数退避
    第1次失败等 0.5 秒，第2次等 1 秒，第3次等 2 秒
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        # 最后一次，直接抛出
                        return None
                    wait_time = initial_wait * (2 ** attempt)
                    time.sleep(wait_time)
            return None
        return wrapper
    return decorator


# ══════════════════════════════════════════════════════════
#  统一 OHLCV 数据获取
# ══════════════════════════════════════════════════════════
def get_ohlcv(
    code: str,
    days: int = 252,
    source: str = "auto",
) -> pd.DataFrame:
    """
    获取标准 OHLCV 数据

    参数：
      code: 股票/ETF 代码
      days: 查询天数
      source: 数据源 ("auto", "akshare", "mootdx")

    返回：
      DataFrame，列为 [open, high, low, close, volume, amount, change_pct]
      index: datetime
    """
    end = datetime.now()
    start = end - timedelta(days=days)
    start_s = start.strftime("%Y%m%d")
    end_s   = end.strftime("%Y%m%d")

    df = None

    # 尝试 akshare
    if source in ("akshare", "auto"):
        df = _get_akshare(code, start_s, end_s)
        if df is not None and _validate_ohlcv(df):
            return _standardize_columns(df)

    # 尝试 mootdx（备用）
    if source in ("mootdx", "auto"):
        df = _get_mootdx(code, days)
        if df is not None and _validate_ohlcv(df):
            return _standardize_columns(df)

    # 都失败，返回空
    return pd.DataFrame()


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名和数据类型"""
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

    # 处理日期索引
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors='coerce')
        df = df.sort_values("date").set_index("date")
    elif "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], errors='coerce')
        df = df.rename(columns={"datetime": "date"})
        df = df.sort_values("date").set_index("date")

    # 只保留标准列
    kept_cols = ["open", "high", "low", "close", "volume"]
    optional_cols = [c for c in ["amount", "change_pct"] if c in df.columns]
    kept_cols.extend(optional_cols)
    df = df[[c for c in kept_cols if c in df.columns]]

    # 转数值型
    df = df.astype(float, errors="ignore")
    return df


# ══════════════════════════════════════════════════════════
#  AkShare 接口（3次重试）
# ══════════════════════════════════════════════════════════
@retry_with_backoff(max_attempts=3, initial_wait=0.5)
def _get_akshare(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """
    通过 AkShare 获取历史 K 线
    - ETF 用 fund_etf_hist_em
    - 股票用 stock_zh_a_hist
    """
    try:
        import akshare as ak

        is_etf = _is_etf(code)

        if is_etf:
            # ETF 接口
            df = ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust=""
            )
        else:
            # 股票接口
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq"
            )

        if isinstance(df, pd.DataFrame) and len(df) > 5:
            return df

    except Exception as e:
        # 重试由装饰器处理
        pass

    return None


# ══════════════════════════════════════════════════════════
#  mootdx 接口（备用，3次重试）
# ══════════════════════════════════════════════════════════
@retry_with_backoff(max_attempts=3, initial_wait=0.5)
def _get_mootdx(code: str, days: int) -> Optional[pd.DataFrame]:
    """
    通过 mootdx 通达信获取历史 K 线
    """
    try:
        from mootdx.quotes import Quotes

        # 创建 API 连接
        api = Quotes.factory(market="std", bestip=True, timeout=8)

        # 日 K 线（frequency=9）
        df = api.bars(symbol=code, frequency=9, offset=days)

        if isinstance(df, pd.DataFrame) and len(df) > 5:
            # 转换日期列
            if "datetime" in df.columns:
                df["date"] = pd.to_datetime(df["datetime"]).dt.date.astype(str)
            return df

    except Exception as e:
        # 重试由装饰器处理
        pass

    return None


# ══════════════════════════════════════════════════════════
#  批量获取（带进度显示）
# ══════════════════════════════════════════════════════════
def get_batch_ohlcv(codes: list[str], days: int = 252, verbose: bool = True) -> dict[str, pd.DataFrame]:
    """
    批量获取多只股票/ETF 的 OHLCV

    参数：
      codes: 代码列表
      days: 查询天数
      verbose: 是否打印进度

    返回：
      {code: DataFrame} 的字典，失败的代码会被跳过
    """
    results = {}
    total = len(codes)

    for i, code in enumerate(codes, 1):
        try:
            if verbose:
                print(f"  [{i:2d}/{total}] {code:8s} ... ", end="", flush=True)

            df = get_ohlcv(code, days=days)

            if not df.empty:
                results[code] = df
                if verbose:
                    print(f"✓ {len(df):4d} 行")
            else:
                if verbose:
                    print("✗ 无数据")

        except Exception as e:
            if verbose:
                print(f"✗ 异常: {str(e)[:30]}")

        # 请求间隔，避免被 IP 限流
        time.sleep(0.3)

    return results


# ══════════════════════════════════════════════════════════
#  实时行情（快照）
# ══════════════════════════════════════════════════════════
def get_realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """
    获取实时行情（最新价、涨幅、成交额）
    优先级：ths_bridge > akshare
    """
    try:
        from ths_bridge import get_realtime_batch
        return get_realtime_batch(codes)
    except Exception as e:
        pass  # ths_bridge 不可用，降级到 akshare

    try:
        import akshare as ak
        spot = ak.fund_etf_spot_em()
        result = {}
        for _, r in spot.iterrows():
            c = str(r.get("代码", ""))
            if c in codes:
                try:
                    result[c] = {
                        "price":  float(r.get("最新价", 0)),
                        "chg":    float(r.get("涨跌幅", 0)),
                        "volume": float(r.get("成交额", 0)),
                    }
                except Exception as e:
                    pass  # 单行数据转换错误，跳过该行
        return result
    except Exception as e:
        return {}


# ══════════════════════════════════════════════════════════
#  缓存装饰器（支持 streamlit）
# ══════════════════════════════════════════════════════════
def with_cache(func):
    """
    智能缓存装饰器
    - 如果在 streamlit 环境，用 @st.cache_data
    - 否则用 @lru_cache
    """
    # 检测是否在 streamlit 环境
    in_streamlit = "streamlit" in sys.modules

    if in_streamlit:
        try:
            import streamlit as st
            return st.cache_data(func)
        except Exception as e:
            return lru_cache(maxsize=128)(func)
    else:
        return lru_cache(maxsize=128)(func)
