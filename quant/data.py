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
import functools
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from functools import lru_cache
import pandas as pd
import numpy as np

# 只屏蔽第三方库的无害 UserWarning（akshare/openpyxl 等噪声），保留 pandas 的
# DeprecationWarning / FutureWarning，以便升级时（如 astype(errors="ignore") 在
# pandas 3.0 移除）能提前暴露，而不是被全局静默掩盖。
warnings.filterwarnings("ignore", category=UserWarning)

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
    - close / 收盘 列非空
    - close / 收盘 非全零
    - 至少 5 行数据
    """
    if df is None or df.empty or len(df) < 5:
        return False
    close_col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
    if not close_col:
        return False
    close_val = pd.to_numeric(df[close_col], errors="coerce")
    if close_val.isna().all() or (close_val == 0).all():
        return False
    return True


# ══════════════════════════════════════════════════════════
#  重试装饰器（指数退避）
# ══════════════════════════════════════════════════════════
def retry_with_backoff(max_attempts: int = 3, initial_wait: float = 0.5, retry_on_none: bool = False):
    """
    重试装饰器，指数退避
    第1次失败等 0.5 秒，第2次等 1 秒，第3次等 2 秒

    - 捕获被装饰函数抛出的异常并重试；
    - retry_on_none=True 时，函数返回 None（数据源失败的哨兵值）也视为失败并重试；
      这是修复「数据层重试失效」的关键：数据源内部吞掉异常直接 return None，
      导致原装饰器误以为成功、永不重试。
    - 最后一次仍失败：若曾抛异常则原样抛出，若仅返回 None 则返回 None（供上层降级）。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        time.sleep(initial_wait * (2 ** attempt))
                        continue
                    raise
                if retry_on_none and result is None:
                    last_exc = RuntimeError(f"{func.__name__} 返回 None（数据源不可用）")
                    if attempt < max_attempts - 1:
                        time.sleep(initial_wait * (2 ** attempt))
                        continue
                    return None
                return result
            return None
        return wrapper
    return decorator


def _get_tdxrs_market(code: str) -> int:
    """
    推断通达信市场代码：
    沪市 (1): 60, 68, 51, 50, 58, 56, 55, 59
    深市 (0): 00, 30, 15, 16, 12, 39
    """
    if not isinstance(code, str) or not code:
        return 1
    if code.startswith(("60", "68", "51", "50", "58", "56", "55", "59")):
        return 1
    return 0


@retry_with_backoff(max_attempts=3, initial_wait=0.5, retry_on_none=True)
def _get_tdxrs(code: str, days: int) -> Optional[pd.DataFrame]:
    """
    通过 tdxrs (Rust 极速接口) 获取历史 K 线
    """
    try:
        import tdxrs
        from tdxrs import TdxHqClient
        from tdxrs.constants import KLINE_DAILY

        market = _get_tdxrs_market(code)
        client = TdxHqClient()
        if not client.connect_to_any():
            return None

        try:
            if hasattr(client, "get_security_bars_dataframe"):
                df = client.get_security_bars_dataframe(KLINE_DAILY, market, code, 0, days)
            else:
                bars = client.get_security_bars(KLINE_DAILY, market, code, 0, days)
                if not bars:
                    return None
                df = pd.DataFrame(bars)

            if isinstance(df, pd.DataFrame) and len(df) > 5:
                return df
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
    except Exception:
        pass
    return None


def _get_tdxrs_quotes(codes: list[str]) -> dict[str, dict]:
    """
    通过 tdxrs 获取 A股/ETF 实时五档盘口与最新价
    """
    try:
        import tdxrs
        from tdxrs import TdxHqClient

        reqs = [(_get_tdxrs_market(c), c) for c in codes]
        client = TdxHqClient()
        if not client.connect_to_any():
            return {}

        try:
            quotes = client.get_security_quotes(reqs)
            result = {}
            if quotes:
                for q in quotes:
                    c = str(q.get("code", ""))
                    if c in codes:
                        price = float(q.get("price", 0))
                        chg = float(q.get("reversed_bytes0", 0) if "reversed_bytes0" in q else q.get("chg", 0))
                        vol = float(q.get("amount", 0) or q.get("vol", 0))
                        result[c] = {"price": price, "chg": chg, "volume": vol}
            return result
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
    except Exception:
        return {}


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
      source: 数据源 ("auto", "tdxrs", "akshare", "mootdx")

    返回：
      DataFrame，列为 [open, high, low, close, volume, amount, change_pct]
      index: datetime
    """
    end = datetime.now()
    start = end - timedelta(days=days)
    start_s = start.strftime("%Y%m%d")
    end_s   = end.strftime("%Y%m%d")

    df = None

    # 尝试 tdxrs (Rust 极速引擎，若可用)
    if source in ("tdxrs", "auto"):
        df = _get_tdxrs(code, days)
        if df is not None and _validate_ohlcv(df):
            return _standardize_columns(df)

    # 尝试 akshare
    if source in ("akshare", "auto"):
        df = _get_akshare(code, start_s, end_s)
        if df is not None and _validate_ohlcv(df):
            return _standardize_columns(df)

    # 尝试 mootdx（备用）
    if source in ("mootdx", "auto"):
        df = _get_mootdx(code, days, start_s, end_s)
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
        "vol": "volume",
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

    # 转数值型（用 to_numeric 替代已弃用且将在 pandas 3.0 移除的 astype(errors="ignore")）
    numeric_cols = [c for c in ["open", "high", "low", "close", "volume", "amount", "change_pct"] if c in df.columns]
    if numeric_cols:
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    return df


# ══════════════════════════════════════════════════════════
#  AkShare 接口（3次重试）
# ══════════════════════════════════════════════════════════
@retry_with_backoff(max_attempts=3, initial_wait=0.5, retry_on_none=True)
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
@retry_with_backoff(max_attempts=3, initial_wait=0.5, retry_on_none=True)
def _get_mootdx(code: str, days: int, start_s: str = "", end_s: str = "") -> Optional[pd.DataFrame]:
    """
    通过 mootdx 通达信获取历史 K 线
    lookback 语义与 AkShare 对齐：按自然日 [start_s, end_s] 窗口估算 bars 数，
    并裁剪到该窗口，避免 offset=days（bars 数）与自然日不一致（issue #30）。
    """
    try:
        from mootdx.quotes import Quotes

        # 创建 API 连接（bestip=False：不做全服务器测速选优，实测测速耗时 7.7s+
        # 且本机网络下常连接失败，固定默认服务器 + 短超时快速失败）
        api = Quotes.factory(market="std", bestip=False, timeout=5)

        # 估算覆盖自然日窗口所需的 bars 数（含周末/停牌缓冲）
        try:
            start_dt = pd.to_datetime(start_s) if start_s else None
            end_dt = pd.to_datetime(end_s) if end_s else None
        except (TypeError, ValueError):
            start_dt = end_dt = None
        if start_dt is not None and end_dt is not None:
            offset = max(days, (end_dt - start_dt).days + 30)
        else:
            offset = max(days, 30)

        # 日 K 线（frequency=9）
        df = api.bars(symbol=code, frequency=9, offset=offset)

        if isinstance(df, pd.DataFrame) and len(df) > 5:
            df = df.copy(deep=True)  # 避免后续切片/赋值触发 SettingWithCopyWarning（issue #30 路径）
            # 转换日期列
            if "datetime" in df.columns:
                df["date"] = pd.to_datetime(df["datetime"])
                # 按自然日窗口裁剪，对齐 AkShare 的 start_date/end_date 语义
                if start_dt is not None and end_dt is not None:
                    mask = (df["date"] >= start_dt) & (df["date"] <= end_dt)
                    df = df[mask]
                df["date"] = df["date"].dt.date.astype(str)
            if len(df) > 5:
                return df

    except Exception as e:
        # 重试由装饰器处理
        pass

    return None


# ══════════════════════════════════════════════════════════
#  批量获取（带进度显示）
# ══════════════════════════════════════════════════════════
def get_batch_ohlcv(
    codes: list[str],
    days: int = 252,
    verbose: bool = True,
    *,
    max_workers: int = 8,
    use_cache: bool = True,
    cache_ttl_seconds: float = 600.0,
    request_timeout: float = 8.0,
) -> dict[str, pd.DataFrame]:
    """
    批量获取多只股票/ETF 的 OHLCV

    参数：
      codes: 代码列表
      days: 查询天数
      verbose: 是否打印进度
      max_workers: 缺失标的并发回退上限
      use_cache: 是否复用用户目录下的短期行情缓存
      cache_ttl_seconds: 成功行情缓存有效期

    返回：
      {code: DataFrame} 的字典，失败的代码会被跳过
    """
    from nasdx.fast_market import fetch_histories

    unique_codes = list(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    if not unique_codes:
        return {}

    normalized_days = max(1, int(days))
    end = datetime.now()
    start = end - timedelta(days=normalized_days)
    workers = max(1, min(20, int(max_workers)))
    ttl_seconds = max(0.0, float(cache_ttl_seconds))

    try:
        history_map = fetch_histories(
            unique_codes,
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            max_workers=workers,
            min_rows=5,
            use_disk_cache=bool(use_cache),
            cache_ttl_seconds=ttl_seconds,
            request_timeout=request_timeout,
        )
    except Exception:
        history_map = {}

    results: dict[str, pd.DataFrame] = {}
    total = len(unique_codes)
    for i, code in enumerate(unique_codes, 1):
        try:
            if verbose:
                print(f"  [{i:2d}/{total}] {code:8s} ... ", end="", flush=True)

            frame, _source = history_map.get(code, (None, None))
            if isinstance(frame, pd.DataFrame) and _validate_ohlcv(frame):
                df = _standardize_columns(frame)
            else:
                df = get_ohlcv(code, days=normalized_days)

            if isinstance(df, pd.DataFrame) and not df.empty:
                results[code] = df.copy(deep=True)
                if verbose:
                    print(f"✓ {len(df):4d} 行")
            else:
                if verbose:
                    print("✗ 无数据")

        except Exception as e:
            if verbose:
                print(f"✗ 异常: {str(e)[:30]}")

    return results


# ══════════════════════════════════════════════════════════
#  实时行情（快照）
# ══════════════════════════════════════════════════════════
def _map_tencent_to_quotes(tencent: dict, codes: list[str]) -> dict[str, dict]:
    """
    将腾讯 qt.gtimg.cn 的逐代码快照映射为 NASDX 行情字典（price/chg/volume）。
    只映射请求的代码，绝不为少量标的拉取全市场表。
    """
    result = {}
    for c in codes:
        q = tencent.get(c)
        if q:
            result[c] = {
                "price": float(q.get("close", 0) or 0),
                "chg": float(q.get("change_pct", 0) or 0),
                "volume": float(q.get("amount", 0) or 0),
            }
    return result


def get_realtime_quotes(
    codes: list[str],
    *,
    ths_timeout: float = 3.0,
) -> dict[str, dict]:
    """
    获取实时行情（最新价、涨幅、成交额）

    优先级：腾讯 qt.gtimg.cn（逐代码快照，最稳） > tdxrs（逐代码盘口） > ths_bridge > akshare 全表兜底

    实测依据（2026-08-13，本机网络）：
    - 腾讯 qt.gtimg.cn：10 只 ~3.1s，最稳定（HTTP 源）
    - tdxrs：连接 ~150ms 但当前网络下请求常返回空
    - ths_bridge（mootdx bestip 测速）：7.7s+ 失败，是 26s 慢路径的元凶
    - akshare 全表：72s+，仅作最后手段

    除最后的 akshare 兜底外，均按需拉取指定代码，避免为少数标的而拉取全市场 ETF 表。
    ths_bridge 调用带整体超时护栏（默认 3s），防止通达信测速黑洞拖慢整条链路。
    """
    codes = [str(c).strip() for c in codes if str(c).strip()]
    if not codes:
        return {}

    # 1) 腾讯 qt.gtimg.cn 逐代码快照——实测最稳最快，HTTP 源不受通达信服务器状态影响
    try:
        from nasdx.fast_market import fetch_tencent_quotes
        tencent = fetch_tencent_quotes(codes, request_timeout=4.0)
        result = _map_tencent_to_quotes(tencent, codes)
        if result:
            return result
    except Exception:
        pass

    # 2) tdxrs (Rust 极速引擎)，逐代码盘口；连接/请求约 150ms~1s，失败即放弃
    tdxrs_quotes = _get_tdxrs_quotes(codes)
    if tdxrs_quotes:
        return tdxrs_quotes

    # 3) ths_bridge（mootdx/pytdx 通达信）——整体超时护栏：
    #    mootdx bestip 测速与 TCP 连接失败可能耗时数十秒，超时即放弃不阻塞调用方
    try:
        from concurrent.futures import ThreadPoolExecutor
        from ths_bridge import get_realtime_batch
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(get_realtime_batch, codes)
            ths_quotes = future.result(timeout=ths_timeout)
        if ths_quotes:
            return ths_quotes
    except Exception:
        pass

    # 最终兜底：akshare 全市场 ETF 表（仅在以上均失败时，避免常态拉全表）
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
                except Exception:
                    pass  # 单行数据转换错误，跳过该行
        return result
    except Exception:
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
