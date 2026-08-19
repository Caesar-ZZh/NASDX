"""全球指数与用户指定的美港韩证券客观数据。

数据源限定为东方财富公开行情与财务接口。模块不预置个股、不做排名、预测或
买卖结论；只有调用方明确传入代码时才查询单一证券。所有成功结果使用进程级
5 分钟缓存，空结果不缓存。网络不可达时返回空结果或字段完整的 None 形状。
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional


BEIJING = timezone(timedelta(hours=8))
CACHE_TTL_SECONDS = 300.0
REQUEST_TIMEOUT_SECONDS = 5.0

_PUSH_HOSTS = ("push2.eastmoney.com", "push2delay.eastmoney.com")
_PUSH_STATE = {"host_index": 0}
_CACHE: dict[str, tuple[float, Any]] = {}
_HTTP_SESSIONS: dict[bool, Any] = {}

_QUOTE_FIELDS = "f43,f44,f45,f46,f48,f57,f58,f59,f60,f116,f170"
_INDEX_DEFINITIONS = (
    {"key": "dji", "name": "道琼斯", "secid": "100.DJIA", "region": "美股"},
    {"key": "spx", "name": "标普500", "secid": "100.SPX", "region": "美股"},
    {"key": "ndx", "name": "纳斯达克", "secid": "100.NDX", "region": "美股"},
    {"key": "hsi", "name": "恒生指数", "secid": "100.HSI", "region": "港股"},
    {"key": "hstech", "name": "恒生科技", "secid": "124.HSTECH", "region": "港股"},
)

_HK_CASHFLOW_ITEMS = {
    "003999": "经营活动现金流净额",
    "005999": "投资活动现金流净额",
    "007999": "筹资活动现金流净额",
    "006999": "汇率变动前现金净额",
    "011997": "汇率变动等其他影响",
    "010999": "现金及等价物净增加",
    "011001": "期初现金及等价物",
    "011999": "期末现金及等价物",
}
_HK_CASHFLOW_ORDER = tuple(_HK_CASHFLOW_ITEMS)


def _monotonic() -> float:
    return time.monotonic()


def _updated_at() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")


def _cached(
    cache_key: str,
    loader: Callable[[], Any],
    *,
    valid: Callable[[Any], bool] = bool,
) -> Any:
    now = _monotonic()
    hit = _CACHE.get(cache_key)
    if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    value = loader()
    if valid(value):
        _CACHE[cache_key] = (now, value)
    return value


def clear_cache(*, reset_host: bool = True) -> None:
    """清空成功结果缓存；测试或手动刷新时可重置主机探测。"""
    _CACHE.clear()
    if reset_host:
        _PUSH_STATE["host_index"] = 0


def _http_session(*, direct: bool) -> Any:
    session = _HTTP_SESSIONS.get(direct)
    if session is not None:
        return session
    import requests

    session = requests.Session()
    session.trust_env = not direct
    _HTTP_SESSIONS[direct] = session
    return session


def _request_json(
    url: str,
    *,
    params: Mapping[str, Any],
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """同一 URL 先直连、再按系统代理环境重试，失败返回空字典。"""
    headers = {
        "User-Agent": "Mozilla/5.0 NASDX/1.0",
        "Referer": "https://quote.eastmoney.com/",
    }
    for direct in (True, False):
        try:
            response = _http_session(direct=direct).get(
                url,
                params=dict(params),
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _push2_stock_get(secid: str) -> Optional[dict[str, Any]]:
    """实时主机失败后降级延迟主机，并锁存本进程可用主机。"""

    def load() -> Optional[dict[str, Any]]:
        for index in range(_PUSH_STATE["host_index"], len(_PUSH_HOSTS)):
            payload = _request_json(
                f"https://{_PUSH_HOSTS[index]}/api/qt/stock/get",
                params={"secid": secid, "fields": _QUOTE_FIELDS},
            )
            data = payload.get("data")
            if isinstance(data, dict) and data:
                _PUSH_STATE["host_index"] = index
                return data
        return None

    return _cached(f"global_market:quote:{secid}", load)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _price(payload: Mapping[str, Any], field: str) -> Optional[float]:
    value = _number(payload.get(field))
    decimals = payload.get("f59")
    if value is None:
        return None
    if isinstance(decimals, bool) or not isinstance(decimals, int) or decimals < 0:
        decimals = 2
    return round(value / (10**decimals), decimals)


def _change_pct(payload: Mapping[str, Any]) -> Optional[float]:
    value = _number(payload.get("f170"))
    return round(value / 100, 2) if value is not None else None


def _quote_from(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "code": str(payload.get("f57") or "") or None,
        "name": str(payload.get("f58") or "") or None,
        "price": _price(payload, "f43"),
        "open": _price(payload, "f46"),
        "high": _price(payload, "f44"),
        "low": _price(payload, "f45"),
        "prev_close": _price(payload, "f60"),
        "amount": _number(payload.get("f48")),
        "market_cap": _number(payload.get("f116")),
        "change_pct": _change_pct(payload),
    }


def global_indices() -> list[dict[str, Any]]:
    """道指、标普、纳指、恒指与恒生科技快照；缺失项跳过。"""

    def load() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for definition in _INDEX_DEFINITIONS:
            payload = _push2_stock_get(definition["secid"])
            if not payload:
                continue
            result.append(
                {
                    "key": definition["key"],
                    "name": definition["name"],
                    "region": definition["region"],
                    "price": _price(payload, "f43"),
                    "change_pct": _change_pct(payload),
                }
            )
        return result

    return _cached("global_market:indices", load)


def _symbol_candidates(query: str) -> list[dict[str, Any]]:
    raw = str(query or "").strip().upper()
    if not raw:
        return []

    korean = re.fullmatch(r"([0-9]{6})\.(KS|KQ|KR)", raw)
    if korean:
        code, suffix = korean.groups()
        return [
            {
                "code": code,
                "secid_prefix": 177,
                "secid": f"177.{code}",
                "secucode": f"{code}.{suffix}",
                "market": "KR",
                "explicit": True,
            }
        ]

    hong_kong = re.fullmatch(r"([0-9]{1,5})(?:\.HK)?", raw)
    if hong_kong:
        code = hong_kong.group(1).zfill(5)
        return [
            {
                "code": code,
                "secid_prefix": 116,
                "secid": f"116.{code}",
                "secucode": f"{code}.HK",
                "market": "HK",
                "explicit": True,
            }
        ]

    us = re.fullmatch(r"([A-Z][A-Z0-9-]{0,14})\.(O|N|US)", raw)
    if us is None:
        us = re.fullmatch(r"([A-Z][A-Z0-9.-]{0,14})", raw)
    if us is None:
        return []
    groups = us.groups()
    code = groups[0]
    suffix = groups[1] if len(groups) > 1 else None
    if suffix == "O":
        prefixes = ((105, ".O", "NASDAQ"),)
    elif suffix == "N":
        prefixes = ((106, ".N", "NYSE"),)
    else:
        prefixes = (
            (105, ".O", "NASDAQ"),
            (106, ".N", "NYSE"),
            (107, ".O", "US"),
        )
    return [
        {
            "code": code,
            "secid_prefix": prefix,
            "secid": f"{prefix}.{code}",
            "secucode": f"{code}{secucode_suffix}",
            "market": market,
            "explicit": suffix in {"O", "N"},
        }
        for prefix, secucode_suffix, market in prefixes
    ]


def resolve_symbol(query: str) -> Optional[dict[str, Any]]:
    """解析用户输入的美股、港股或带 .KS/.KQ/.KR 后缀的韩股代码。"""

    def load() -> Optional[dict[str, Any]]:
        candidates = _symbol_candidates(query)
        if len(candidates) == 1 and candidates[0]["explicit"]:
            candidate = candidates[0]
            return {
                **{key: value for key, value in candidate.items() if key != "explicit"},
                "name": "",
            }
        for candidate in candidates:
            quote = _push2_stock_get(candidate["secid"])
            if quote:
                return {
                    **{key: value for key, value in candidate.items() if key != "explicit"},
                    "name": str(quote.get("f58") or ""),
                }
        return None

    normalized = str(query or "").strip().upper()
    return _cached(f"global_market:symbol:{normalized}", load)


def _datacenter_rows(
    report_name: str,
    *,
    filter_text: str,
    page_size: int = 50,
    sort_columns: str = "REPORT_DATE",
) -> list[dict[str, Any]]:
    cache_key = f"global_market:datacenter:{report_name}:{filter_text}:{page_size}"

    def load() -> list[dict[str, Any]]:
        payload = _request_json(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": report_name,
                "columns": "ALL",
                "filter": filter_text,
                "pageNumber": 1,
                "pageSize": page_size,
                "sortColumns": sort_columns,
                "sortTypes": -1,
                "source": "WEB",
                "client": "WEB",
            },
        )
        rows = ((payload.get("result") or {}).get("data")) if payload else None
        return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []

    return _cached(cache_key, load)


def _key_metrics(secucode: str) -> Optional[dict[str, Any]]:
    market = "HK" if secucode.endswith(".HK") else "US"
    rows = _datacenter_rows(
        f"RPT_{market}F10_FN_GMAININDICATOR",
        filter_text=f'(SECUCODE="{secucode}")',
        page_size=1,
    )
    if not rows:
        return None
    metrics = rows[0]
    net_profit = metrics.get("PARENT_HOLDER_NETPROFIT")
    if net_profit is None:
        net_profit = metrics.get("HOLDER_PROFIT")
    return {
        "report_date": str(metrics.get("REPORT_DATE") or "")[:10] or None,
        "revenue": _number(metrics.get("OPERATE_INCOME")),
        "revenue_yoy": _number(metrics.get("OPERATE_INCOME_YOY")),
        "net_profit": _number(net_profit),
        "eps": _number(metrics.get("BASIC_EPS")),
        "roe": _number(metrics.get("ROE_AVG")),
        "gross_margin": _number(metrics.get("GROSS_PROFIT_RATIO")),
        "net_margin": _number(metrics.get("NET_PROFIT_RATIO")),
        "debt_ratio": _number(metrics.get("DEBT_ASSET_RATIO")),
    }


def us_hk_stock(query: str) -> dict[str, Any]:
    """返回用户指定证券的行情与关键财务；无法解析时返回空字典。"""
    info = resolve_symbol(query)
    if not info:
        return {}
    payload = _push2_stock_get(info["secid"]) or {}
    quote = _quote_from(payload)
    return {
        "code": info["code"],
        "name": info["name"] or quote.get("name") or info["code"],
        "market": info["market"],
        "data_as_of": _updated_at(),
        "quote": quote,
        "metrics": _key_metrics(info["secucode"]) if info["market"] != "KR" else None,
    }


def hk_cashflow(query: str, periods: int = 8) -> dict[str, Any]:
    """港股最近若干期现金流汇总；非港股或无数据返回空字典。"""
    if isinstance(periods, bool) or not isinstance(periods, int) or periods <= 0:
        raise ValueError("periods must be a positive integer")
    info = resolve_symbol(query)
    if not info or info["market"] != "HK":
        return {}
    item_filter = ",".join(f'"{code}"' for code in _HK_CASHFLOW_ORDER)
    rows = _datacenter_rows(
        "RPT_HKSK_FN_CASHFLOW",
        filter_text=(
            f'(SECUCODE="{info["secucode"]}")'
            f"(STD_ITEM_CODE in ({item_filter}))"
        ),
        page_size=300,
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        report_date = str(row.get("REPORT_DATE") or "")[:10]
        item_code = str(row.get("STD_ITEM_CODE") or "")
        if not report_date or item_code not in _HK_CASHFLOW_ITEMS:
            continue
        period = grouped.setdefault(
            report_date,
            {
                "report_date": report_date,
                "report": row.get("REPORT"),
                "currency": row.get("CURRENCY"),
                "account_standard": row.get("ACCOUNT_STANDARD"),
                "items": {},
            },
        )
        period["items"][_HK_CASHFLOW_ITEMS[item_code]] = {
            "amount": _number(row.get("AMOUNT")),
            "yoy": _number(row.get("YOY_RATIO")),
        }
    if not grouped:
        return {}
    output_periods = sorted(
        grouped.values(),
        key=lambda item: item["report_date"],
        reverse=True,
    )[:periods]
    return {
        "code": info["code"],
        "name": info["name"] or info["code"],
        "market": "HK",
        "currency": output_periods[0].get("currency"),
        "item_order": [_HK_CASHFLOW_ITEMS[code] for code in _HK_CASHFLOW_ORDER],
        "periods": output_periods,
    }
