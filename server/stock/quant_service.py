"""策略实验室的只读计算服务。

本模块只把已有 quant/ 能力整理成可序列化结果；不接账户、不下单、不写报告。
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date, timedelta
from functools import partial
from threading import Lock
from typing import Callable


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cosmos-quant")
_CACHE: dict[str, tuple[float, object]] = {}
_ERROR_CACHE: dict[str, tuple[float, BaseException]] = {}
_IN_FLIGHT: dict[str, Future] = {}
_CACHE_LOCK = Lock()
_TTL = 300.0
_NEGATIVE_TTL = 60.0
_CODE_RE = re.compile(r"^\d{6}$")
_STRATEGY_LABELS = {
    "momentum": "动量策略",
    "mean_reversion": "均值回归",
    "factor_rank": "因子排名",
}
_REBALANCE = {"D", "W", "W-first", "W-last", "M", "M-first", "M-last"}


def clear_caches() -> None:
    """测试与运维使用：清空内存计算缓存，不触碰磁盘。"""
    with _CACHE_LOCK:
        _CACHE.clear()
        _ERROR_CACHE.clear()
        _IN_FLIGHT.clear()


def run_guarded(key: str, fn: Callable[[], object], *, timeout: float) -> object:
    """同键单飞 + 硬等待上限 + 失败短缓存。"""
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]
        failed = _ERROR_CACHE.get(key)
        if failed and now - failed[0] < _NEGATIVE_TTL:
            raise failed[1]
        future = _IN_FLIGHT.get(key)
        owner = future is None
        if owner:
            future = _EXECUTOR.submit(fn)
            _IN_FLIGHT[key] = future

    try:
        value = future.result(timeout=timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        error = TimeoutError(f"量化计算超时（{timeout:g}s）")
        with _CACHE_LOCK:
            _ERROR_CACHE[key] = (time.time(), error)
        raise error from exc
    except BaseException as exc:
        with _CACHE_LOCK:
            _ERROR_CACHE[key] = (time.time(), exc)
        raise
    else:
        with _CACHE_LOCK:
            _CACHE[key] = (time.time(), value)
            _ERROR_CACHE.pop(key, None)
        return value
    finally:
        if owner:
            with _CACHE_LOCK:
                if _IN_FLIGHT.get(key) is future:
                    _IN_FLIGHT.pop(key, None)


def _iso_day(value: object, fallback: date) -> date:
    if value in (None, ""):
        return fallback
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"日期必须使用 YYYY-MM-DD：{value}") from exc


def normalize_backtest_request(payload: dict) -> dict:
    today = date.today()
    end = _iso_day(payload.get("end"), today)
    start = _iso_day(payload.get("start"), end - timedelta(days=365))
    if start >= end:
        raise ValueError("回测开始日期必须早于结束日期")
    if (end - start).days > 2000:
        raise ValueError("回测区间最长为 2000 天")

    raw_universe = payload.get("universe") or []
    if not isinstance(raw_universe, list):
        raise ValueError("universe 必须是代码数组")
    universe = list(dict.fromkeys(str(code).strip() for code in raw_universe if str(code).strip()))
    if not universe or len(universe) > 12:
        raise ValueError("股票池需包含 1–12 个六位代码")
    if any(not _CODE_RE.fullmatch(code) for code in universe):
        raise ValueError("股票池代码必须是六位数字")

    raw_strategies = payload.get("strategies") or ["momentum", "mean_reversion"]
    if not isinstance(raw_strategies, list):
        raise ValueError("strategies 必须是策略数组")
    strategies = list(dict.fromkeys(str(item).strip() for item in raw_strategies))
    if not strategies or any(item not in _STRATEGY_LABELS for item in strategies):
        raise ValueError("策略仅支持 momentum、mean_reversion、factor_rank")

    rebalance = str(payload.get("rebalance") or "W")
    if rebalance not in _REBALANCE:
        raise ValueError("不支持的调仓频率")
    try:
        initial_capital = float(payload.get("initial_capital", 100_000))
        top_n = int(payload.get("top_n", 3))
    except (TypeError, ValueError) as exc:
        raise ValueError("初始资金和 top_n 必须是数值") from exc
    if not math.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("初始资金必须是有限正数")
    if top_n < 1 or top_n > 10:
        raise ValueError("top_n 必须在 1–10 之间")

    return {
        "universe": universe,
        "strategies": strategies,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "initial_capital": initial_capital,
        "rebalance": rebalance,
        "top_n": top_n,
    }


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def compute_backtest(payload: dict) -> dict:
    """抓取历史行情并运行选定策略，返回可直接绘图的客观结果。"""
    import pandas as pd

    from quant.backtest import (
        Backtester,
        strategy_factor_rank,
        strategy_mean_reversion,
        strategy_momentum,
    )
    from quant.data import get_batch_ohlcv

    config = normalize_backtest_request(payload)
    start = pd.Timestamp(config["start"])
    end = pd.Timestamp(config["end"])
    days = (end.date() - start.date()).days + 30
    fetched = get_batch_ohlcv(
        config["universe"],
        days=days,
        verbose=False,
        max_workers=8,
        use_cache=True,
        cache_ttl_seconds=600,
        request_timeout=8,
    )
    price_data = {}
    for code in config["universe"]:
        frame = fetched.get(code)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            sliced = frame.loc[(frame.index >= start) & (frame.index <= end)].copy(deep=True)
            if len(sliced) >= 25:
                price_data[code] = sliced
    if not price_data:
        raise RuntimeError("所选区间没有足够行情数据")

    strategy_functions = {
        "momentum": strategy_momentum,
        "mean_reversion": strategy_mean_reversion,
        "factor_rank": strategy_factor_rank,
    }
    rows = []
    effective_top_n = min(config["top_n"], len(price_data))
    for strategy_name in config["strategies"]:
        backtester = Backtester(initial_capital=config["initial_capital"])
        signal = partial(strategy_functions[strategy_name], top_n=effective_top_n)
        result = backtester.run(price_data, signal, rebalance_freq=config["rebalance"])
        curve = [
            {"date": pd.Timestamp(index).strftime("%Y-%m-%d"), "equity": _finite(value)}
            for index, value in result.equity_curve.items()
            if _finite(value) is not None
        ]
        rows.append(
            {
                "strategy": strategy_name,
                "label": _STRATEGY_LABELS[strategy_name],
                "metrics": {
                    "total_return": _finite(result.total_return),
                    "annual_return": _finite(result.annual_return),
                    "sharpe_ratio": _finite(result.sharpe_ratio),
                    "max_drawdown": _finite(result.max_drawdown),
                    "win_rate": _finite(result.win_rate),
                    "completed_trades": int(result.completed_trades),
                },
                "equity_curve": curve,
            }
        )

    return {
        "result_type": "objective_calculation",
        "notice": "历史数据计算结果，不预测未来，不构成投资建议。",
        "parameters": config,
        "coverage": {
            "requested": len(config["universe"]),
            "available": len(price_data),
            "missing": [code for code in config["universe"] if code not in price_data],
        },
        "strategies": rows,
    }


def get_backtest(payload: dict) -> dict:
    config = normalize_backtest_request(payload)
    key = "backtest:" + json.dumps(config, ensure_ascii=False, sort_keys=True)
    return run_guarded(key, lambda: compute_backtest(config), timeout=30.0)


def compute_etf50(*, days: int, top_n: int, rebalance: str) -> dict:
    if days < 90 or days > 730:
        raise ValueError("ETF50 评分区间须为 90–730 天")
    if top_n < 1 or top_n > 10:
        raise ValueError("ETF50 top_n 须为 1–10")
    if rebalance not in _REBALANCE:
        raise ValueError("不支持的调仓频率")

    from quant.etf50_quant import run_etf50_quant

    result = dict(
        run_etf50_quant(
            days=days,
            top_n=top_n,
            rebalance_freq=rebalance,
            verbose=False,
            save_report=False,
            allow_legacy_fallback=False,
            run_backtest=False,
        )
    )
    for key in ("_saved_to", "portfolio_weights", "bullish", "bearish", "neutral", "top3"):
        result.pop(key, None)
    objective_fields = {
        "code", "name", "category", "factor_score", "factor_rank", "roc20", "rsi14",
        "macd", "bias20", "vol_ratio", "std20", "quant_score", "bt_return",
        "bt_sharpe", "bt_drawdown", "has_data",
    }
    result["results"] = [
        {key: value for key, value in row.items() if key in objective_fields}
        for row in result.get("results", [])
    ]
    result["result_type"] = "objective_calculation"
    result["notice"] = "评分仅汇总历史因子与回测指标，不预测未来，不构成投资建议。"
    return result


def get_etf50(*, days: int = 252, top_n: int = 5, rebalance: str = "W") -> dict:
    key = f"etf50:{days}:{top_n}:{rebalance}"
    return run_guarded(
        key,
        lambda: compute_etf50(days=days, top_n=top_n, rebalance=rebalance),
        timeout=45.0,
    )
