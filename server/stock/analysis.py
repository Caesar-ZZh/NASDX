"""深度分析的数据装配层：把任意 6 位 A 股代码的实时行情装成 analyzer 需要的 data 骨架。

CLI 的 analyze(data=None) 会读本地 stock_data_*.json（服务器/全新环境没有），
且要求代码在监控池内。本模块用服务器可达的稳定源（腾讯 gtimg 实时 + 腾讯 qfq 日 K）
现拉数据，构造 {date, sectors:[{stocks:[{code,name,indicators,...}]}]} 骨架，
让 /api/analysis/{code} 支持**任意** 6 位代码。
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime

from astock import tencent_quote

# scripts/ 是根目录下的 CLI 脚本（含 compute_indicators），server 进程已有根目录在 sys.path
try:
    from scripts.fetch_stock_data import compute_indicators  # type: ignore
except Exception:  # pragma: no cover - 依赖缺失时该维度降级
    compute_indicators = None  # type: ignore


def _fetch_tencent_kline(code: str, days: int = 240):
    """腾讯 qfq 日 K（服务器实测可达），返回中文列名 DataFrame；失败返回 None。"""
    import pandas as pd

    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    symbol = f"{prefix}{code}"
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{days},qfq"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.load(r)
        rows = (payload.get("data") or {}).get(symbol, {})
        items = rows.get("qfqday") or rows.get("day") or []
        if not items:
            return None
        df = pd.DataFrame(items, columns=["日期", "开盘", "收盘", "最高", "最低", "成交量"])
        for col in ["开盘", "收盘", "最高", "最低", "成交量"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["涨跌幅"] = df["收盘"].pct_change() * 100
        return df
    except Exception:
        return None


def build_data_for(code: str) -> dict:
    """给任意 6 位代码构造 analyzer 可直接消费的 data 骨架。"""
    name = code
    price = None
    change_pct = None
    try:
        q = tencent_quote([code]).get(code, {})
        name = q.get("name") or code
        price = q.get("price")
        change_pct = q.get("change_pct")
    except Exception:
        pass

    indicators = {}
    if compute_indicators is not None:
        df = _fetch_tencent_kline(code)
        if df is not None and not df.empty:
            indicators = compute_indicators(df)

    stock = {
        "code": code,
        "name": name,
        "type": "stock",
        "note": "",
        "indicators": indicators,
        "price": price,
        "change_pct": change_pct,
        "sector_name": "自定义",
        "fund_flow": [],
        "main_net_3d": [],
        "data_source": "tencent-rt",
        "data_date": datetime.now().strftime("%Y-%m-%d"),
    }
    return {
        "date": datetime.now().strftime("%Y%m%d"),
        "sectors": [{"name": "自定义", "stocks": [stock]}],
    }


# 供 /api/analysis 调用：装好 data 后再交给 NasdxAnalyzer
def load_data_for_analysis(code: str) -> dict:
    data = build_data_for(code)
    # 如果行情或指标都没拿到，抛出可读错误（避免 analyzer 报"不在监控池"）
    stock = data["sectors"][0]["stocks"][0]
    if not stock.get("indicators"):
        raise RuntimeError("未能获取该股票的行情数据（K 线或实时行情拉取失败），请稍后重试")
    return data
