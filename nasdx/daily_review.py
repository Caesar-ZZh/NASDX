"""每日复盘聚合：市场宽度、板块资金、短线情绪与成交额集中度。

本模块只输出市场级或板块级客观统计。涨停池和成交额原始数据仅在内存中
聚合，不返回证券代码、证券名称或个股排名，守住 NASDX 的“零标的”边界。

数据由 AkShare 的公开接口按需加载；导入模块本身不联网。每个公开查询使用
进程级 5 分钟 TTL 缓存，数据源返回空结果时不缓存，以便下次立即重试。
"""

from __future__ import annotations

import math
import statistics
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional


BEIJING = timezone(timedelta(hours=8))
CACHE_TTL_SECONDS = 300.0

_CACHE: dict[str, tuple[float, Any]] = {}


def _monotonic() -> float:
    return time.monotonic()


def _today() -> date:
    return datetime.now(BEIJING).date()


def _updated_at() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")


def _load_akshare() -> Any:
    import akshare as ak

    return ak


def _rows(frame: Any) -> list[dict[str, Any]]:
    """把 AkShare DataFrame 或测试 fixture 归一为字典列表。"""
    if frame is None:
        return []
    if isinstance(frame, list):
        return [dict(row) for row in frame if isinstance(row, Mapping)]
    try:
        records = frame.to_dict("records")
    except Exception:
        try:
            records = [row for _, row in frame.iterrows()]
        except Exception:
            return []
    result: list[dict[str, Any]] = []
    for row in records:
        if isinstance(row, Mapping):
            result.append(dict(row))
            continue
        try:
            result.append(dict(row))
        except Exception:
            continue
    return result


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int:
    number = _number(value)
    return int(number) if number is not None else 0


def _cached(
    key: str,
    loader: Callable[[], Any],
    *,
    valid: Callable[[Any], bool] = bool,
) -> Any:
    """读取进程级 TTL 缓存；无效或空结果不写缓存。"""
    now = _monotonic()
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    value = loader()
    if valid(value):
        _CACHE[key] = (now, value)
    return value


def clear_cache(keys: Optional[list[str]] = None) -> None:
    """清空全部或指定缓存项，供手动刷新与测试使用。"""
    if keys is None:
        _CACHE.clear()
        return
    for key in keys:
        _CACHE.pop(key, None)


def _sentiment() -> dict[str, Any]:
    """市场涨跌家数与机械分档，不产生预测。"""
    try:
        rows = _rows(_load_akshare().stock_market_activity_legu())
        values = {str(row.get("item", "")): row.get("value") for row in rows}
    except Exception:
        return {}
    if not values:
        return {}

    up = _integer(values.get("上涨"))
    down = _integer(values.get("下跌"))
    flat = _integer(values.get("平盘"))
    limit_up = _integer(values.get("涨停"))
    real_limit_up = _integer(values.get("真实涨停"))
    limit_down = _integer(values.get("跌停"))
    real_limit_down = _integer(values.get("真实跌停"))
    ratio = up / max(down, 1)

    if up < 600:
        breadth = "冰点"
    elif ratio < 0.7:
        breadth = "偏弱"
    elif ratio < 1.2:
        breadth = "中性"
    elif ratio < 2.5:
        breadth = "偏强"
    else:
        breadth = "普涨"

    if real_limit_up >= 100:
        speculation = "亢奋"
    elif real_limit_up >= 60:
        speculation = "活跃"
    elif real_limit_up >= 30:
        speculation = "普通"
    else:
        speculation = "冰点"

    return {
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
        "real_limit_up": real_limit_up,
        "limit_down": limit_down,
        "real_limit_down": real_limit_down,
        "breadth_ratio": round(ratio, 3),
        "breadth": breadth,
        "speculation": speculation,
        "activity": str(values.get("活跃度", "")),
        "date": str(values.get("统计日期", "")),
    }


def _sectors() -> list[dict[str, Any]]:
    """行业资金流；只保留行业级字段。"""
    try:
        rows = _rows(_load_akshare().stock_fund_flow_industry(symbol="即时"))
    except Exception:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        net = _number(row.get("净额"))
        if net is None:
            continue
        result.append(
            {
                "sector": str(row.get("行业", "")),
                "change_pct": _number(row.get("行业-涨跌幅")),
                "net": net,
                "inflow": _number(row.get("流入资金")),
                "outflow": _number(row.get("流出资金")),
                "firms": _integer(row.get("公司家数")),
            }
        )
    return sorted(result, key=lambda item: item["net"], reverse=True)


def get_overview() -> dict[str, Any]:
    """市场宽度与行业资金流。"""

    def build() -> dict[str, Any]:
        sentiment = _sentiment()
        sectors = _sectors()
        if not sentiment and not sectors:
            return {}
        return {
            "sentiment": sentiment,
            "sectors": sectors,
            "updated": _updated_at(),
        }

    return _cached("daily_review:overview", build)


_POOL_FUNCTIONS = {
    "limit_up": "stock_zt_pool_em",
    "broken": "stock_zt_pool_zbgc_em",
    "limit_down": "stock_zt_pool_dtgc_em",
    "previous_limit_up": "stock_zt_pool_previous_em",
}


def _pool_rows(kind: str, date_text: str) -> list[dict[str, Any]]:
    """加载东财涨停相关原始池；调用方只能消费聚合结果。"""
    function_name = _POOL_FUNCTIONS[kind]
    try:
        function = getattr(_load_akshare(), function_name)
        return _rows(function(date=date_text))
    except Exception:
        return []


def _board_count(row: Mapping[str, Any]) -> int:
    for key in ("连板数", "lbc", "涨停统计"):
        raw = row.get(key)
        if key == "涨停统计" and isinstance(raw, str) and "/" in raw:
            raw = raw.rsplit("/", 1)[-1]
        value = _integer(raw)
        if value > 0:
            return value
    return 1


def _emotion() -> dict[str, Any]:
    """聚合涨停四池，只返回计数、梯队和比率。"""
    resolved = ""
    limit_up_rows: list[dict[str, Any]] = []
    today = _today()
    for offset in range(8):
        candidate = (today - timedelta(days=offset)).strftime("%Y%m%d")
        limit_up_rows = _pool_rows("limit_up", candidate)
        if limit_up_rows:
            resolved = candidate
            break
    if not resolved:
        return {}

    broken_rows = _pool_rows("broken", resolved)
    limit_down_rows = _pool_rows("limit_down", resolved)
    previous_rows = _pool_rows("previous_limit_up", resolved)

    boards = [_board_count(row) for row in limit_up_rows]
    consecutive = [board for board in boards if board >= 2]
    tiers = Counter(min(board, 5) for board in consecutive)
    ladder = [
        {"boards": board, "count": tiers[board], "plus": board == 5}
        for board in sorted(tiers)
    ]

    limit_up_count = len(limit_up_rows)
    broken_count = len(broken_rows)
    previous_count = len(previous_rows)
    attempts = limit_up_count + broken_count
    return {
        "date": f"{resolved[:4]}-{resolved[4:6]}-{resolved[6:]}",
        "limit_up_count": limit_up_count,
        "limit_down_count": len(limit_down_rows),
        "broken_count": broken_count,
        "max_boards": max(boards) if boards else 0,
        "consecutive_count": len(consecutive),
        "ladder": ladder,
        "seal_rate": round(limit_up_count / attempts, 3) if attempts else None,
        "break_rate": round(broken_count / attempts, 3) if attempts else None,
        "promotion_rate": (
            round(len(consecutive) / previous_count, 3) if previous_count else None
        ),
        "previous_limit_up_count": previous_count,
    }


def get_short_term_emotion() -> dict[str, Any]:
    """短线情绪聚合，5 分钟缓存。"""
    return _cached("daily_review:emotion", _emotion)


def _turnover_aggregate(limit: int) -> dict[str, Any]:
    """聚合成交额最高的 N 条原始记录，不返回其代码或名称。"""
    try:
        rows = _rows(_load_akshare().stock_zh_a_spot_em())
    except Exception:
        return {}

    market_rows: list[tuple[float, Optional[float]]] = []
    for row in rows:
        amount = _number(row.get("成交额"))
        if amount is None or amount < 0:
            continue
        market_rows.append((amount, _number(row.get("涨跌幅"))))
    if not market_rows:
        return {}

    market_rows.sort(key=lambda item: item[0], reverse=True)
    selected = market_rows[:limit]
    amounts = [item[0] for item in selected]
    changes = [item[1] for item in selected if item[1] is not None]
    market_amount = sum(item[0] for item in market_rows)
    return {
        "requested_top_n": limit,
        "sample_size": len(selected),
        "total_amount": round(sum(amounts), 2),
        "median_amount": round(statistics.median(amounts), 2),
        "market_amount_share": (
            round(sum(amounts) / market_amount, 4) if market_amount > 0 else None
        ),
        "up_count": sum(1 for change in changes if change > 0),
        "down_count": sum(1 for change in changes if change < 0),
        "flat_count": sum(1 for change in changes if change == 0),
        "mean_change_pct": (
            round(statistics.fmean(changes), 3) if changes else None
        ),
        "updated": _updated_at(),
    }


def get_turnover_top(limit: int = 20) -> dict[str, Any]:
    """成交额 Top-N 的市场级聚合，不暴露成分个股或顺序。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    return _cached(
        f"daily_review:turnover:{limit}",
        lambda: _turnover_aggregate(limit),
    )


def get_daily_review(turnover_limit: int = 20) -> dict[str, Any]:
    """组装每日复盘三个分区；各分区独立缓存和失效。"""
    return {
        "overview": get_overview(),
        "short_term_emotion": get_short_term_emotion(),
        "turnover": get_turnover_top(turnover_limit),
        "updated": _updated_at(),
    }
