"""
股票/ETF/LOF 数据抓取脚本 — 供 NASDX AI 分析
运行: python fetch_stock_data.py
输出: stock_data_YYYYMMDD.json

字段统一规范（indicators）:
  close, change_pct, ma5, ma10, ma20, ma60,
  dif, dea, macd_bar, rsi, boll_upper, boll_mid, boll_lower,
  vol_ratio, up_days_20, kline_last5
"""

import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd

from nasdx.fast_market import RateLimiter, bounded_map, fetch_histories
from nasdx.market_sources import fetch_stock_hist, last_trade_date
from nasdx.paths import get_market_data_dir

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "stocks.json"
TODAY      = datetime.now().strftime("%Y%m%d")
START_DATE = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
OUTPUT_FILE = get_market_data_dir(create=True) / f"stock_data_{TODAY}.json"

# ── 批量行情参数（issue #34）──────────────────────────────
# 历史 K 线统一走 nasdx.fast_market.fetch_histories：线程池并发 + 磁盘缓存
# （缓存键 = 代码+起止日期，跨进程共享），同一工作流内重复代码只抓一次。
HISTORY_SOURCES = ("tdxrs", "tencent_hist_tx", "eastmoney_hist")
HISTORY_MIN_ROWS = 5
HISTORY_TIMEOUT = 6.0
HISTORY_WORKERS = 12
HISTORY_CACHE_TTL = 600.0
# 资金流 / 指数走 akshare，用「有界并发 + provider 级限流」代替逐只固定 sleep。
FUND_FLOW_WORKERS = 4
FUND_FLOW_MIN_INTERVAL = 0.12
INDEX_WORKERS = 3
INDEX_MIN_INTERVAL = 0.12

INDEX_MAP = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000688": "科创50",
}


def safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return None


def compute_indicators(df: pd.DataFrame) -> dict:
    """从 K 线 DataFrame 计算技术指标，列名统一用英文"""
    if df is None or df.empty or len(df) < 5:
        return {}

    close = df["收盘"].astype(float)
    vol   = df["成交量"].astype(float)

    ma5  = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1] if len(close) >= 10 else None
    ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
    ma60 = close.rolling(60).mean().iloc[-1] if len(close) >= 60 else None

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif   = ema12 - ema26
    dea   = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = (100 - 100 / (1 + gain / (loss + 1e-9))).iloc[-1] if len(close) >= 14 else None

    mid        = close.rolling(20).mean()
    std        = close.rolling(20).std()
    boll_upper = (mid + 2 * std).iloc[-1] if len(close) >= 20 else None
    boll_lower = (mid - 2 * std).iloc[-1] if len(close) >= 20 else None

    vol_ratio  = (vol.iloc[-1] / vol.rolling(5).mean().iloc[-2]) if len(vol) > 5 else None
    up_days_20 = int((df["涨跌幅"].astype(float).tail(20) > 0).sum()) if len(df) >= 20 else None

    current = close.iloc[-1]
    prev    = close.iloc[-2] if len(close) > 1 else current
    chg_pct = (current - prev) / prev * 100

    # kline_last5：保留原始中文列名供参考
    kline_cols = [c for c in ["日期","开盘","收盘","最高","最低","成交量","涨跌幅"] if c in df.columns]
    kline_last5 = df[kline_cols].tail(5).to_dict("records")

    return {
        # ── 统一字段名（data_loader.py 读这套）──
        "close":       round(float(current), 3),
        "change_pct":  round(float(chg_pct), 2),
        "ma5":         round(float(ma5), 3),
        "ma10":        round(float(ma10), 3) if ma10 is not None else None,
        "ma20":        round(float(ma20), 3) if ma20 is not None else None,
        "ma60":        round(float(ma60), 3) if ma60 is not None else None,
        "dif":         round(float(dif.iloc[-1]), 4),
        "dea":         round(float(dea.iloc[-1]), 4),
        "macd_bar":    round(float(macd_bar.iloc[-1]), 4),
        "rsi":         round(float(rsi), 2) if rsi is not None else None,
        "boll_upper":  round(float(boll_upper), 3) if boll_upper is not None else None,
        "boll_mid":    round(float(mid.iloc[-1]), 3) if len(close) >= 20 else None,
        "boll_lower":  round(float(boll_lower), 3) if boll_lower is not None else None,
        "vol_ratio":   round(float(vol_ratio), 2) if vol_ratio is not None else None,
        "up_days_20":  up_days_20,
        "kline_last5": kline_last5,
    }


def _empty_fund_flow() -> dict:
    return {"fund_flow": [], "main_net_3d": []}


def fetch_fund_flow(code: str) -> dict:
    """抓取个股资金流向（科创板688/ETF/LOF 不支持，返回空）"""
    market = "sh" if code.startswith("6") else "sz"
    df = safe(ak.stock_individual_fund_flow, stock=code, market=market)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return _empty_fund_flow()
    tail = df.tail(10)
    main_col = "主力净流入-净额"
    main_3d = df[main_col].tail(3).tolist() if main_col in df.columns else []
    return {"fund_flow": tail.to_dict("records"), "main_net_3d": main_3d}


def fetch_pool_histories(codes, start_date: str | None = None, end_date: str | None = None) -> dict:
    """整池一次性并发抓历史 K 线（issue #34）。

    走 ``nasdx.fast_market.fetch_histories``：tdxrs 批量 → tencent/eastmoney
    有界并发回退 → 超时翻倍重试，并写磁盘缓存。缓存键与 scan_stocks_full /
    scan_etf50 一致，因此同一工作流内重复代码只会真正联网一次。
    """
    unique = [code for code in dict.fromkeys(str(c).strip() for c in codes) if code]
    if not unique:
        return {}
    return fetch_histories(
        unique,
        start_date or START_DATE,
        end_date or TODAY,
        request_timeout=HISTORY_TIMEOUT,
        max_workers=HISTORY_WORKERS,
        min_rows=HISTORY_MIN_ROWS,
        sources=HISTORY_SOURCES,
        cache_ttl_seconds=HISTORY_CACHE_TTL,
    )


def fetch_fund_flows(codes) -> dict:
    """资金流批量抓取：有界并发 + provider 级限流，取代逐只固定 sleep。"""
    targets = [
        code
        for code in dict.fromkeys(str(c).strip() for c in codes)
        if code and not code.startswith("688")
    ]
    if not targets:
        return {}
    limiter = RateLimiter(FUND_FLOW_MIN_INTERVAL)
    outcomes = bounded_map(
        targets,
        fetch_fund_flow,
        max_workers=FUND_FLOW_WORKERS,
        rate_limiter=limiter,
    )
    flows = {}
    for code, (payload, error) in zip(targets, outcomes):
        flows[code] = payload if error is None and isinstance(payload, dict) else _empty_fund_flow()
    return flows


def _resolve_history(code: str, history):
    """批量层命中就直接用；未提供批量结果时才单只回退抓取。

    批量层内部已含「多源回退 + 超时翻倍重试」，所以批量层返回空的标的
    不再重复联网，避免退化成串行抓取。
    """
    if history is not None:
        frame, source = history
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            return frame, source
        return None, None
    return fetch_stock_hist(code, START_DATE, TODAY, min_rows=HISTORY_MIN_ROWS)


def _apply_history(result: dict, hist, source) -> None:
    if isinstance(hist, pd.DataFrame) and not hist.empty:
        result["indicators"] = compute_indicators(hist)
        result["kline_days"] = len(hist)
        result["data_source"] = source
        result["data_date"] = last_trade_date(hist)
    else:
        result["indicators"] = {}
        result["data_source"] = None
        result["data_date"] = None


def fetch_stock(item: dict, history=None, fund_flow=None) -> dict:
    """组装 A 股个股结果（K 线 + 资金流）。

    ``history`` / ``fund_flow`` 由批量层注入；两者缺省时退回单只抓取，
    保持既有调用方兼容。
    """
    code = str(item["code"]).strip()
    name = item.get("name", code)
    print(f"    {code} {name}", flush=True)
    result = {"code": code, "name": name, "note": item.get("note", ""), "type": "stock"}

    _apply_history(result, *_resolve_history(code, history))

    # 科创板 688xxx 资金流接口不支持
    if code.startswith("688"):
        result.update(_empty_fund_flow())
    elif isinstance(fund_flow, dict):
        result.update(fund_flow)
    else:
        result.update(fetch_fund_flow(code))
    return result


def fetch_etf(item: dict, history=None) -> dict:
    """抓取 ETF / LOF K 线（无资金流数据）"""
    code = str(item["code"]).strip()
    name = item.get("name", code)
    kind = item.get("type", "etf").upper()
    print(f"    {code} {name} [{kind}]", flush=True)
    result = {
        "code": code,
        "name": name,
        "note": item.get("note", ""),
        "type": item.get("type", "etf"),
        "fund_flow":   [],   # ETF/LOF 无资金流
        "main_net_3d": [],
    }

    _apply_history(result, *_resolve_history(code, history))
    return result


def _fetch_index_snapshot(code: str) -> dict | None:
    hist = safe(ak.stock_zh_index_daily, symbol=code)
    if not isinstance(hist, pd.DataFrame) or len(hist) < 2:
        return None
    last = hist.tail(2)
    chg = (last["close"].iloc[-1] - last["close"].iloc[-2]) / last["close"].iloc[-2] * 100
    return {
        "close":      round(float(last["close"].iloc[-1]), 2),
        "change_pct": round(float(chg), 2),
    }


def fetch_market_overview() -> dict:
    print("  [大盘指数]", flush=True)
    codes = list(INDEX_MAP)
    outcomes = bounded_map(
        codes,
        _fetch_index_snapshot,
        max_workers=INDEX_WORKERS,
        rate_limiter=RateLimiter(INDEX_MIN_INTERVAL),
    )
    indices = {}
    for code, (snapshot, error) in zip(codes, outcomes):
        if error is None and isinstance(snapshot, dict):
            indices[INDEX_MAP[code]] = snapshot
    return indices


def _collect_pool(config: dict) -> list[tuple[int, str, dict]]:
    """按配置顺序摊平 (板块序号, stocks/etfs, item)，保证输出顺序不变。"""
    entries: list[tuple[int, str, dict]] = []
    for index, sector in enumerate(config.get("sectors", [])):
        for item in sector.get("stocks", []):
            entries.append((index, "stocks", item))
        for item in sector.get("etfs", []):
            entries.append((index, "etfs", item))
    return entries


def main():
    print(f"\n=== NASDX 数据抓取 {TODAY} ===\n", flush=True)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = json.load(f)

    output = {
        "date":           TODAY,
        "generated_at":   datetime.now().isoformat(),
        "market_overview": {},
        "sectors":        [],
        "errors":         [],
    }

    output["market_overview"] = fetch_market_overview()

    # ── Phase 0：整池一次批量抓历史（并发 + 磁盘缓存），不再逐只联网 ──
    entries = _collect_pool(config)
    try:
        history_map = fetch_pool_histories(str(item["code"]).strip() for _, _, item in entries)
    except Exception:
        tb = traceback.format_exc()
        print(f"  ⚠️ 批量行情层异常，逐只回退：{tb[:120]}", flush=True)
        output["errors"].append({"code": "__batch_history__", "error": tb})
        history_map = {}
    # ── Phase 1：资金流单独限流并发（仅 A 股个股，688 无该接口）──
    try:
        fund_flow_map = fetch_fund_flows(
            str(item["code"]).strip() for _, kind, item in entries if kind == "stocks"
        )
    except Exception:
        tb = traceback.format_exc()
        print(f"  ⚠️ 资金流批量层异常，逐只回退：{tb[:120]}", flush=True)
        output["errors"].append({"code": "__batch_fund_flow__", "error": tb})
        fund_flow_map = {}

    # ── Phase 2：按配置顺序组装，纯本地计算 ──
    for sector in config.get("sectors", []):
        sname = sector["name"]
        print(f"\n  [{sname}]", flush=True)
        sec_data = {"name": sname, "stocks": [], "etfs": []}

        for item in sector.get("stocks", []):
            code = str(item["code"]).strip()
            try:
                sec_data["stocks"].append(
                    fetch_stock(item, history_map.get(code), fund_flow_map.get(code))
                )
            except Exception:
                tb = traceback.format_exc()
                print(f"    ❌ {item['code']} 失败：{tb[:100]}", flush=True)
                output["errors"].append({"code": item["code"], "error": tb})

        for item in sector.get("etfs", []):
            code = str(item["code"]).strip()
            try:
                sec_data["etfs"].append(fetch_etf(item, history_map.get(code)))
            except Exception:
                tb = traceback.format_exc()
                print(f"    ❌ {item['code']} 失败：{tb[:100]}", flush=True)
                output["errors"].append({"code": item["code"], "error": tb})

        output["sectors"].append(sec_data)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    total_stocks = sum(len(s["stocks"]) for s in output["sectors"])
    total_etfs   = sum(len(s["etfs"])   for s in output["sectors"])
    print(f"\n✅ 完成！股票 {total_stocks} 只 + ETF/LOF {total_etfs} 只，错误 {len(output['errors'])} 个")
    print(f"   输出：{OUTPUT_FILE}")
    return str(OUTPUT_FILE)


if __name__ == "__main__":
    main()
