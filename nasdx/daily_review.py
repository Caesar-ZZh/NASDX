"""每日复盘聚合层 —— 市场情绪 / 板块资金 / 短线情绪 / 成交榜 / 全球指数。

仅呈现客观公开数据（涨跌家数、涨停跌停、连板梯队计数、封板率、炸板率、
晋级率、行业资金净额、成交额 TOP20、主要海外指数）。不输出个股推荐/排名/预测，
守「零标的」红线。

数据源全部免费、无 key：
  - 市场情绪：akshare stock_market_activity_legu
  - 板块资金：akshare stock_fund_flow_industry
  - 短线情绪：东方财富 push2ex 涨停四池（聚合计数/比率，不暴露个股清单）
  - 成交榜：东财榜单接口
  - 全球指数：nasdx/gstock.global_indices

缓存：全站共享 5 分钟 TTL，与 nasdx.analysis_cache 接口一致；空结果不缓存。
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

BEIJING = timezone(timedelta(hours=8))
_TTL: int = 300  # 5 分钟

# ── 缓存槽 ──────────────────────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, Any]] = {}


def _num(v: Any) -> int:
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _numf(v: Any) -> Optional[float]:
    """同 _num 但返回 float，用于金额/市值等浮点字段。"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _cached(key: str, fn: Callable[[], Any], valid: Callable[[Any], bool] = bool) -> Any:
    """TTL 缓存。空结果不缓存（valid 判否），下次请求直接重试。"""
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    if valid(val):
        _CACHE[key] = (now, val)
    return val


def clear_cache(keys: Optional[list[str]] = None) -> None:
    """清空指定 key 或全部缓存。测试与手动刷新使用。"""
    if keys is None:
        _CACHE.clear()
        return
    for k in keys:
        _CACHE.pop(k, None)


# ── 市场情绪 ────────────────────────────────────────────────────────────────

def _sentiment() -> dict[str, Any]:
    """市场情绪：涨跌家数 / 涨停跌停 / 活跃度 + 大盘宽度、题材投机分档。"""
    import akshare as ak  # 惰性导入，避免未装时服务启动失败
    try:
        df = ak.stock_market_activity_legu()
        d = {str(row["item"]): row["value"] for _, row in df.iterrows()}
    except Exception:
        return {}

    up = _num(d.get("上涨"))
    down = _num(d.get("下跌"))
    flat = _num(d.get("平盘"))
    zt = _num(d.get("涨停"))
    zt_real = _num(d.get("真实涨停"))
    dt = _num(d.get("跌停"))
    dt_real = _num(d.get("真实跌停"))

    ratio = up / max(down, 1)
    breadth = (
        "冰点" if up < 600
        else "偏弱" if ratio < 0.7
        else "中性" if ratio < 1.2
        else "偏强" if ratio < 2.5
        else "普涨"
    )
    speculation = (
        "亢奋" if zt_real >= 100
        else "活跃" if zt_real >= 60
        else "普通" if zt_real >= 30
        else "冰点"
    )
    return {
        "up": up,
        "down": down,
        "flat": flat,
        "zt": zt,
        "zt_real": zt_real,
        "dt": dt,
        "dt_real": dt_real,
        "active": str(d.get("活跃度", "")),
        "breadth": breadth,
        "speculation": speculation,
        "date": str(d.get("统计日期", "")),
    }


# ── 板块资金流 ──────────────────────────────────────────────────────────────

def _sectors() -> list[dict[str, Any]]:
    """行业资金流（按净额降序）。仅行业维度，不含个股清单。"""
    import akshare as ak
    try:
        f = ak.stock_fund_flow_industry(symbol="即时")
        f = f.sort_values("净额", ascending=False)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for _, row in f.iterrows():
        out.append({
            "name": str(row["行业"]),
            "pct": round(float(row.get("行业-涨跌幅", 0) or 0), 2),
            "net": round(float(row.get("净额", 0) or 0), 2),
            "inflow": round(float(row.get("流入资金", 0) or 0), 2),
            "outflow": round(float(row.get("流出资金", 0) or 0), 2),
            "firms": _num(row.get("公司家数")),
        })
    return out


# ── 短线情绪（东财四池聚合，零个股名） ────────────────────────────────────

def _em_zt_pool(topic: str, date_str: str, sort_type: str) -> list[dict]:
    """调用东财 push2ex 涨停池（薄封装，供 _emotion 内部使用）。

    topic 取值：getTopicZTPool / getTopicZBPool / getTopicDTPool / getYesterdayZTPool
    date_str 格式：YYYYMMDD
    sort_type：如 fbt:asc / fund:asc / zs:desc
    """
    try:
        import nasdx.astock as _astock
        return _astock.em_zt_topic_pool(topic, date_str, sort_type)
    except Exception:
        return []


def _emotion() -> dict[str, Any]:
    """短线情绪（聚合口径，零个股名）：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率。

    数据源 = 东财涨停板四池。只把池子聚合成计数与比率，不输出任何个股 code/name，
    守「零标的」红线。
    """
    today = datetime.now(BEIJING).date()
    resolved: str = ""
    zt: list[dict] = []
    for back in range(8):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        zt = _em_zt_pool("getTopicZTPool", d, "fbt:asc")
        if zt:
            resolved = d
            break
    if not resolved:
        return {}

    zb = _em_zt_pool("getTopicZBPool", resolved, "fbt:asc")
    dt = _em_zt_pool("getTopicDTPool", resolved, "fund:asc")
    yzt = _em_zt_pool("getYesterdayZTPool", resolved, "zs:desc")

    boards = [_num(p.get("lbc")) or 1 for p in zt]
    lianban = [b for b in boards if b >= 2]
    tiers = Counter(min(b, 5) for b in lianban)
    ladder = [{"boards": b, "count": tiers[b], "plus": b >= 5} for b in sorted(tiers)]

    zt_count = len(zt)
    zb_count = len(zb)
    yzt_count = len(yzt)
    attempts = zt_count + zb_count
    seal_rate = round(zt_count / attempts, 3) if attempts else None
    break_rate = round(zb_count / attempts, 3) if attempts else None
    promotion_rate = round(len(lianban) / yzt_count, 3) if yzt_count else None

    return {
        "date": f"{resolved[:4]}-{resolved[4:6]}-{resolved[6:]}",
        "zt_count": zt_count,
        "dt_count": len(dt),
        "zb_count": zb_count,
        "max_boards": max(boards) if boards else 0,
        "lianban_count": len(lianban),
        "ladder": ladder,
        "seal_rate": seal_rate,
        "break_rate": break_rate,
        "promotion_rate": promotion_rate,
        "yzt_count": yzt_count,
    }


# ── 公开导出 ────────────────────────────────────────────────────────────────

def get_overview() -> dict[str, Any]:
    """市场情绪 + 板块资金（含缓存）。"""
    def build() -> dict[str, Any]:
        return {
            "sentiment": _sentiment(),
            "sectors": _sectors(),
            "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        }
    return _cached("dr_overview", build, valid=lambda v: bool(v.get("sentiment") or v.get("sectors")))


def get_short_term_emotion() -> dict[str, Any]:
    """短线情绪（含缓存，5 分钟）。"""
    return _cached("dr_emotion", _emotion)


def get_turnover_top(limit: int = 20) -> dict[str, Any]:
    """全市场成交额榜 Top limit（含缓存 5 分钟）。"""
    def build() -> dict[str, Any]:
        try:
            import nasdx.astock as _astock
            stocks = _astock.market_turnover_rank(limit)
        except Exception:
            stocks = []
        return {
            "stocks": stocks,
            "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        }
    return _cached("dr_turnover", build, valid=lambda v: bool(v.get("stocks")))


def get_global_indices() -> list[dict[str, Any]]:
    """全球指数快照（美股 / 港股，含缓存 5 分钟）。空结果不缓存。"""
    try:
        import nasdx.gstock as _gstock
        return _cached("dr_global", _gstock.global_indices, valid=bool)
    except Exception:
        return _cached("dr_global", lambda: [], valid=bool)
