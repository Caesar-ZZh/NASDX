"""东财数据中心资金面直连层。

对齐 reportName 枚举，走 datacenter-web.eastmoney.com/api/data/v1/get 统一入口。
互动易走巨潮 irm.cninfo.com.cn 公共查询端点。

合规：只按【用户传入的单个代码/条件】返回客观公开数据，不预置标的、不推荐、不预测。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# 全局配置
# ---------------------------------------------------------------------------

_EASTMONEY_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EASTMONEY_REPORT_NAMES = {
    "margin_trading": "RPTA_WEB_RZRQ_GGMX",
    "block_trade": "RPT_DATA_BLOCKTRADE",
    "holder_num_change": "RPT_HOLDERNUMLATEST",
    "dividend_history": "RPT_SHAREBONUS_DET",
    "dragon_tiger_board": "RPT_DAILYBILLBOARD_DETAILSNEW",
    "lockup_expiry": "RPT_LIFT_STAGE",
    "sector_stock_list": "RPTA_STOCK_FINDER",
}

_INTERACTIVE_QA_URL = "https://irm.cninfo.com.cn/ircs/irmweb/stock/common/InteractiveQa/QueryIrmRecord"
_CONCEPT_RANK_URL = "https://emappdata.eastmoney.com/stockrank/getHotStockRankList"
_FUND_FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_CACHE_DIR = Path(os.environ.get("NASDX_DATA_CACHE", Path.home() / ".cache" / "nasdx"))
_EM_RATE_SEC = float(os.environ.get("EM_GET_RATE_SEC", "1.0"))

_last_em_ts: float = 0.0

# ---------------------------------------------------------------------------
# 缓存工具
# ---------------------------------------------------------------------------


def _cache_key(*parts: Any) -> str:
    return "::".join(str(p) for p in parts)


def _read_cache(key: str) -> Any | None:
    path = _CACHE_DIR / (key.replace("::", "_") + ".json")
    if not path.exists():
        return None
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(key: str, value: Any, ttl_sec: int = 1800) -> None:
    if value in (None, [], {}):
        return
    ttl_sec = min(int(ttl_sec), 300)
    now = time.time()
    payload = {
        "ts": now,
        "ttl": ttl_sec,
        "value": value,
    }
    path = _CACHE_DIR / (key.replace("::", "_") + ".json")
    try:
        import json

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass


def _load_cache(key: str, ttl_sec: int = 1800) -> Any | None:
    val = _read_cache(key)
    if val is None:
        return None
    if isinstance(val, dict) and "ts" in val:
        if time.time() - val["ts"] > min(int(ttl_sec), 300):
            return None
        return val.get("value")
    return val

# ---------------------------------------------------------------------------
# em_get 限流入口
# ---------------------------------------------------------------------------


def em_get(params: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    """东财数据中心统一入口，串行限流 >= _EM_RATE_SEC 秒。

    失败时抛 RuntimeError； callers 应捕获后优雅降级返回空列表/空字典。
    """
    global _last_em_ts
    elapsed = time.time() - _last_em_ts
    if elapsed < _EM_RATE_SEC:
        time.sleep(_EM_RATE_SEC - elapsed)
    _last_em_ts = time.time()

    resp = requests.get(
        _EASTMONEY_DATACENTER,
        params=params,
        headers={
            "User-Agent": UA,
            "Referer": "https://data.eastmoney.com/",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()

# ---------------------------------------------------------------------------
# 内部：公共字段归一
# ---------------------------------------------------------------------------


def _safe_float(x: Any) -> float:
    if x is None or x == "":
        return 0.0
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _safe_int(x: Any) -> int:
    if x is None or x == "":
        return 0
    try:
        return int(float(str(x).replace(",", "")))
    except (ValueError, TypeError):
        return 0


def _ensure_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("result", "data", "list", "items", "cards"):
            v = data.get(k)
            if isinstance(v, list):
                return v
        return []
    return []

# ---------------------------------------------------------------------------
# 资金面接口
# ---------------------------------------------------------------------------


def margin_trading(code: str, days: int = 120) -> list[dict[str, Any]]:
    """融资融券（融资余额/融券余额/融资净买入/融券余量）日度数据。

    返回按日期降序的最新 days 条；若接口失败或无数据返回 []。
    """
    key = _cache_key("margin_trading", code, days)
    cached = _load_cache(key, ttl_sec=1800)
    if cached is not None:
        return cached

    try:
        secid = _to_secid(code)
        params = {
            "reportName": _EASTMONEY_REPORT_NAMES["margin_trading"],
            "columns": "SECURITY_CODE,TRADE_DATE,FIN_BALANCE,RTO_BALANCE,FIN_NET_BUY,RTO_NET_BUY",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageSize": str(days),
            "pageNumber": "1",
            "sortTypes": "-1",
            "sortColumns": "TRADE_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        out = em_get(params, timeout=20)
        rows = _ensure_list(out.get("result") or out.get("data"))
        result = []
        for r in rows[:days]:
            result.append({
                "date": str(r.get("TRADE_DATE", ""))[:10],
                "code": str(r.get("SECURITY_CODE", code)),
                "fin_balance": _safe_float(r.get("FIN_BALANCE")),
                "rto_balance": _safe_float(r.get("RTO_BALANCE")),
                "fin_net_buy": _safe_float(r.get("FIN_NET_BUY")),
                "rto_net_buy": _safe_float(r.get("RTO_NET_BUY")),
            })
        _write_cache(key, result, ttl_sec=1800)
        return result
    except Exception:
        return []


def block_trade(code: str, limit: int = 50) -> list[dict[str, Any]]:
    """大宗交易记录（买方/卖方/成交价/折溢价）。"""
    key = _cache_key("block_trade", code, limit)
    cached = _load_cache(key, ttl_sec=1800)
    if cached is not None:
        return cached

    try:
        params = {
            "reportName": _EASTMONEY_REPORT_NAMES["block_trade"],
            "columns": "SECURITY_CODE,TRADE_DATE,BUYER_SELLER,BUYER_NAME,SELLER_NAME,TRADE_PRICE,PRECEIVE_PRICE,RATIO",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageSize": str(limit),
            "pageNumber": "1",
            "sortTypes": "-1",
            "sortColumns": "TRADE_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        out = em_get(params, timeout=20)
        rows = _ensure_list(out.get("result") or out.get("data"))
        result = []
        for r in rows:
            result.append({
                "date": str(r.get("TRADE_DATE", ""))[:10],
                "code": str(r.get("SECURITY_CODE", code)),
                "buyer": str(r.get("BUYER_NAME", "")),
                "seller": str(r.get("SELLER_NAME", "")),
                "price": _safe_float(r.get("TRADE_PRICE")),
                "preclose": _safe_float(r.get("PRECEIVE_PRICE")),
                "discount_pct": _safe_float(r.get("RATIO")),
            })
        _write_cache(key, result, ttl_sec=1800)
        return result
    except Exception:
        return []


def holder_num_change(code: str, limit: int = 20) -> list[dict[str, Any]]:
    """股东户数变动（最新报告期户数、较上期增减）。"""
    key = _cache_key("holder_num", code, limit)
    cached = _load_cache(key, ttl_sec=1800)
    if cached is not None:
        return cached

    try:
        params = {
            "reportName": _EASTMONEY_REPORT_NAMES["holder_num_change"],
            "columns": "SECURITY_CODE,REPORT_DATE,HOLDER_NUM,HOLDER_CHANGE_RATIO",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageSize": str(limit),
            "pageNumber": "1",
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        out = em_get(params, timeout=20)
        rows = _ensure_list(out.get("result") or out.get("data"))
        result = []
        for r in rows:
            result.append({
                "date": str(r.get("REPORT_DATE", ""))[:10],
                "code": str(r.get("SECURITY_CODE", code)),
                "holder_num": _safe_int(r.get("HOLDER_NUM")),
                "change_ratio": _safe_float(r.get("HOLDER_CHANGE_RATIO")),
            })
        _write_cache(key, result, ttl_sec=1800)
        return result
    except Exception:
        return []


def dividend_history(code: str, limit: int = 20) -> list[dict[str, Any]]:
    """分红送配历史（方案/除权除息日/派息率）。"""
    key = _cache_key("dividend", code, limit)
    cached = _load_cache(key, ttl_sec=1800)
    if cached is not None:
        return cached

    try:
        params = {
            "reportName": _EASTMONEY_REPORT_NAMES["dividend_history"],
            "columns": "SECURITY_CODE,PUBLIC_DATE,IMPLEMENT_DATE,PER_SHARE_BONUS,PER_SHARE_TRANSFER,PER_SHARE_DIVIDEND",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageSize": str(limit),
            "pageNumber": "1",
            "sortTypes": "-1",
            "sortColumns": "PUBLIC_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        out = em_get(params, timeout=20)
        rows = _ensure_list(out.get("result") or out.get("data"))
        result = []
        for r in rows:
            result.append({
                "public_date": str(r.get("PUBLIC_DATE", ""))[:10],
                "implement_date": str(r.get("IMPLEMENT_DATE", ""))[:10],
                "code": str(r.get("SECURITY_CODE", code)),
                "per_share_bonus": _safe_float(r.get("PER_SHARE_BONUS")),
                "per_share_transfer": _safe_float(r.get("PER_SHARE_TRANSFER")),
                "per_share_dividend": _safe_float(r.get("PER_SHARE_DIVIDEND")),
            })
        _write_cache(key, result, ttl_sec=1800)
        return result
    except Exception:
        return []


def stock_fund_flow_120d(code: str) -> list[dict[str, Any]]:
    """个股资金流（主/散/中/大/超大 净流入）近 120 日。

    走 push2his.fflow 端点；与数据中心 report 并行为兜底。
    """
    key = _cache_key("fund_flow", code)
    cached = _load_cache(key, ttl_sec=900)
    if cached is not None:
        return cached

    try:
        secid = _to_secid(code)
        params = {
            "secid": secid,
            "lmt": "0",
            "klt": "101",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            "ut": "fa5fd1943c7b386f172d6893dbbd1d0c",
        }
        resp = requests.get(
            _FUND_FLOW_URL,
            params=params,
            headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        arr = data.get("data") or {}
        klines = arr.get("klines") or []
        result = []
        for line in klines[-120:]:
            cols = str(line).split(",")
            if len(cols) < 11:
                continue
            result.append({
                "date": cols[0][:10],
                "code": code,
                "main_net_inflow": _safe_float(cols[1]),
                "small_net_inflow": _safe_float(cols[2]),
                "mid_net_inflow": _safe_float(cols[3]),
                "large_net_inflow": _safe_float(cols[4]),
                "super_large_net_inflow": _safe_float(cols[5]),
                "main_net_buy_ratio": _safe_float(cols[6]),
                "small_net_buy_ratio": _safe_float(cols[7]),
                "mid_net_buy_ratio": _safe_float(cols[8]),
                "large_net_buy_ratio": _safe_float(cols[9]),
                "super_large_net_buy_ratio": _safe_float(cols[10]),
            })
        _write_cache(key, result, ttl_sec=900)
        return result
    except Exception:
        return []


def dragon_tiger_board(code: str, limit: int = 30) -> list[dict[str, Any]]:
    """龙虎榜上榜记录（上榜日/买入额/卖出额/净买入/席位）。"""
    key = _cache_key("dragon_tiger", code, limit)
    cached = _load_cache(key, ttl_sec=900)
    if cached is not None:
        return cached

    try:
        params = {
            "reportName": _EASTMONEY_REPORT_NAMES["dragon_tiger_board"],
            "columns": "SECURITY_CODE,TRADE_DATE,BUY_AMOUNT,SELL_AMOUNT,NET_BUY_AMOUNT,REASON,SEAT_BUY,SEAT_SELL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageSize": str(limit),
            "pageNumber": "1",
            "sortTypes": "-1",
            "sortColumns": "TRADE_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        out = em_get(params, timeout=20)
        rows = _ensure_list(out.get("result") or out.get("data"))
        result = []
        for r in rows:
            result.append({
                "date": str(r.get("TRADE_DATE", ""))[:10],
                "code": str(r.get("SECURITY_CODE", code)),
                "buy_amount": _safe_float(r.get("BUY_AMOUNT")),
                "sell_amount": _safe_float(r.get("SELL_AMOUNT")),
                "net_buy_amount": _safe_float(r.get("NET_BUY_AMOUNT")),
                "reason": str(r.get("REASON", "")),
                "seat_buy": str(r.get("SEAT_BUY", "")),
                "seat_sell": str(r.get("SEAT_SELL", "")),
            })
        _write_cache(key, result, ttl_sec=900)
        return result
    except Exception:
        return []


def lockup_expiry(code: str, limit: int = 30) -> list[dict[str, Any]]:
    """限售解禁（解禁日期/解禁数量/解禁市值/占总股本比例）。"""
    key = _cache_key("lockup_expiry", code, limit)
    cached = _load_cache(key, ttl_sec=1800)
    if cached is not None:
        return cached

    try:
        params = {
            "reportName": _EASTMONEY_REPORT_NAMES["lockup_expiry"],
            "columns": "SECURITY_CODE,LIMITED_SHARES_DATE,LIMITED_SHARES_NUM,LIMITED_SHARES_VALUE,LIMITED_SHARES_RATIO",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageSize": str(limit),
            "pageNumber": "1",
            "sortTypes": "1",
            "sortColumns": "LIMITED_SHARES_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        out = em_get(params, timeout=20)
        rows = _ensure_list(out.get("result") or out.get("data"))
        result = []
        for r in rows[:limit]:
            result.append({
                "date": str(r.get("LIMITED_SHARES_DATE", ""))[:10],
                "code": str(r.get("SECURITY_CODE", code)),
                "num": _safe_float(r.get("LIMITED_SHARES_NUM")),
                "value": _safe_float(r.get("LIMITED_SHARES_VALUE")),
                "ratio": _safe_float(r.get("LIMITED_SHARES_RATIO")),
            })
        _write_cache(key, result, ttl_sec=1800)
        return result
    except Exception:
        return []


def sector_overview(sector: str, limit: int = 200) -> dict[str, Any]:
    """板块聚合快照；不返回个股代码、名称、名单或排名。"""
    key = _cache_key("sector_list", sector, limit)
    cached = _load_cache(key, ttl_sec=300)
    if cached is not None:
        return cached

    try:
        params = {
            "reportName": _EASTMONEY_REPORT_NAMES["sector_stock_list"],
            "columns": "SECURITY_CODE,SECURITY_NAME_ABB,PRICE,CHANGE_RATE,LATEST_MCAP",
            "filter": f'(INDUSTRY_NAME="{sector}" OR CONCEPT_NAME="{sector}")',
            "pageSize": str(limit),
            "pageNumber": "1",
            "sortTypes": "-1",
            "sortColumns": "LATEST_MCAP",
            "source": "WEB",
            "client": "WEB",
        }
        out = em_get(params, timeout=20)
        rows = _ensure_list(out.get("result") or out.get("data"))
        selected = rows[:limit]
        changes = [_safe_float(r.get("CHANGE_RATE")) for r in selected]
        result = {
            "sector": sector,
            "constituent_count": len(selected),
            "up_count": sum(value > 0 for value in changes),
            "down_count": sum(value < 0 for value in changes),
            "flat_count": sum(value == 0 for value in changes),
            "avg_change_pct": round(sum(changes) / len(changes), 4) if changes else None,
            "total_mcap_yi": round(
                sum(_safe_float(r.get("LATEST_MCAP")) for r in selected) / 1e8,
                4,
            ),
        }
        if selected:
            _write_cache(key, result, ttl_sec=300)
        return result
    except Exception:
        return {
            "sector": sector,
            "constituent_count": 0,
            "up_count": 0,
            "down_count": 0,
            "flat_count": 0,
            "avg_change_pct": None,
            "total_mcap_yi": 0.0,
        }


def hot_concepts(days: int = 5, limit: int = 50) -> list[dict[str, Any]]:
    """热门概念排行（按区间涨幅/成交额加权）。

    只返回榜单本身，不代表推荐。
    """
    key = _cache_key("hot_concepts", days, limit)
    cached = _load_cache(key, ttl_sec=300)
    if cached is not None:
        return cached

    try:
        params = {
            "rt": "hot",
            "gs": "1",
            "type": "zt" if days <= 3 else "zdf",
            "pageSize": str(limit),
            "page": "1",
            "days": str(days),
        }
        resp = requests.get(
            _CONCEPT_RANK_URL,
            params=params,
            headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = _ensure_list(data.get("data") or data.get("list"))
        result = []
        for r in rows[:limit]:
            result.append({
                "name": str(r.get("name") or r.get("conceptName") or ""),
                "change_pct": _safe_float(r.get("changePercent") or r.get("zdf")),
                "count": _safe_int(r.get("count") or r.get("stockCount")),
            })
        _write_cache(key, result, ttl_sec=300)
        return result
    except Exception:
        return []


def investor_qa(query: str, limit: int = 30) -> list[dict[str, Any]]:
    """互动易问答：按问题关键词检索，或按 code 检索该股的问答。

    若 query 形如 'code:600519' 会提取代码并过滤该股；否则仅做文本检索。
    """
    key = _cache_key("interactive_qa", query, limit)
    cached = _load_cache(key, ttl_sec=3600)
    if cached is not None:
        return cached

    code = ""
    if query.lower().startswith("code:") or query.lower().startswith("stock:"):
        parts = query.split(":", 1)
        if len(parts) == 2:
            code = parts[1].strip()
            text = query[len(parts[0]) + 1:].strip()
        else:
            text = query
    else:
        text = query

    try:
        params = {
            "keyword": text,
            "pageSize": str(limit),
            "pageNo": "1",
            "stockCode": code,
            "channel": "sns",
            "platform": "CNINFO",
        }
        resp = requests.get(
            _INTERACTIVE_QA_URL,
            params=params,
            headers={"User-Agent": UA, "Referer": "https://irm.cninfo.com.cn/"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = _ensure_list(data.get("data") or data.get("list") or data.get("items"))
        result = []
        for r in rows[:limit]:
            result.append({
                "date": str(r.get("date", r.get("publishTime", "")))[:10],
                "question": str(r.get("question", r.get("title", ""))),
                "answer": str(r.get("answer", r.get("reply", ""))),
                "code": str(r.get("stockCode", code)),
                "name": str(r.get("stockName", r.get("companyName", ""))),
            })
        _write_cache(key, result, ttl_sec=3600)
        return result
    except Exception:
        return []

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _to_secid(code: str) -> str:
    """6 位代码 → 东财 secid（sh600000 / sz000001）。"""
    c = str(code).strip()
    if len(c) != 6 or not c.isdigit():
        raise ValueError(f"invalid code: {code}")
    prefix = "sh" if c.startswith(("6", "9")) else "sz"
    return f"{prefix}{c}"


def get_margin_trading_summary(code: str, days: int = 120) -> dict[str, Any]:
    """融资融券简要汇总（最新/均值/趋势方向）。

    仅作客观统计描述，不给出是否看多/看空的结论。
    """
    rows = margin_trading(code, days)
    if not rows:
        return {"code": code, "days": days, "rows": 0, "note": "no data"}
    fin_vals = [r["fin_balance"] for r in rows]
    rto_vals = [r["rto_balance"] for r in rows]
    net_vals = [r["fin_net_buy"] for r in rows]
    return {
        "code": code,
        "days": days,
        "rows": len(rows),
        "latest_date": rows[0]["date"],
        "latest_fin_balance": fin_vals[0],
        "latest_rto_balance": rto_vals[0],
        "avg_fin_balance": round(sum(fin_vals) / len(fin_vals), 2),
        "avg_rto_balance": round(sum(rto_vals) / len(rto_vals), 2),
        "total_net_buy": round(sum(net_vals), 2),
        "net_buy_trend_up": sum(net_vals) > 0,
    }


def get_fund_flow_summary(code: str, days: int = 60) -> dict[str, Any]:
    """资金流简要汇总。"""
    rows = stock_fund_flow_120d(code)
    rows = rows[:days]
    if not rows:
        return {"code": code, "days": days, "rows": 0, "note": "no data"}
    main = [r["main_net_inflow"] for r in rows]
    super_large = [r["super_large_net_inflow"] for r in rows]
    large = [r["large_net_inflow"] for r in rows]
    mid = [r["mid_net_inflow"] for r in rows]
    small = [r["small_net_inflow"] for r in rows]
    return {
        "code": code,
        "days": days,
        "rows": len(rows),
        "latest_date": rows[0]["date"],
        "total_main_net_inflow": round(sum(main), 2),
        "total_super_large": round(sum(super_large), 2),
        "total_large": round(sum(large), 2),
        "total_mid": round(sum(mid), 2),
        "total_small": round(sum(small), 2),
        "main_trend_up": sum(main) > 0,
    }


def get_dividend_yield_summary(code: str, years: int = 5) -> dict[str, Any]:
    """分红汇总（累计分红/次数/最新方案）。"""
    rows = dividend_history(code, limit=years * 4)
    if not rows:
        return {"code": code, "years": years, "rows": 0, "note": "no data"}
    total_cash = sum(r["per_share_dividend"] for r in rows)
    total_bonus = sum(r["per_share_bonus"] for r in rows)
    total_transfer = sum(r["per_share_transfer"] for r in rows)
    return {
        "code": code,
        "years": years,
        "rows": len(rows),
        "latest_public_date": rows[0]["public_date"],
        "latest_per_share_dividend": rows[0]["per_share_dividend"],
        "latest_per_share_bonus": rows[0]["per_share_bonus"],
        "total_cash_per_share": round(total_cash, 4),
        "total_bonus_per_share": round(total_bonus, 4),
        "total_transfer_per_share": round(total_transfer, 4),
        "dividend_count": len(rows),
    }


def get_all_fundamentals(code: str) -> dict[str, Any]:
    """单票资金面聚合，供上游统一消费。

    仍遵守零标的红线：仅返回该 code 自身的客观数据，不与其他票对比。
    """
    return {
        "code": code,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "margin_trading": margin_trading(code, days=120),
        "margin_trading_summary": get_margin_trading_summary(code, days=120),
        "block_trade": block_trade(code, limit=30),
        "holder_num_change": holder_num_change(code, limit=20),
        "dividend_history": dividend_history(code, limit=20),
        "dividend_summary": get_dividend_yield_summary(code, years=5),
        "fund_flow": stock_fund_flow_120d(code),
        "fund_flow_summary": get_fund_flow_summary(code, days=60),
        "dragon_tiger_board": dragon_tiger_board(code, limit=10),
        "lockup_expiry": lockup_expiry(code, limit=10),
    }
