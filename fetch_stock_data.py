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
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd

from nasdx.market_sources import fetch_stock_hist, last_trade_date

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "stocks.json"
TODAY      = datetime.now().strftime("%Y%m%d")
START_DATE = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
OUTPUT_FILE = SCRIPT_DIR / f"stock_data_{TODAY}.json"


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


def fetch_fund_flow(code: str) -> dict:
    """抓取个股资金流向（科创板688/ETF/LOF 不支持，返回空）"""
    market = "sh" if code.startswith("6") else "sz"
    df = safe(ak.stock_individual_fund_flow, stock=code, market=market)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {"fund_flow": [], "main_net_3d": []}
    tail = df.tail(10)
    main_col = "主力净流入-净额"
    main_3d = df[main_col].tail(3).tolist() if main_col in df.columns else []
    return {"fund_flow": tail.to_dict("records"), "main_net_3d": main_3d}


def fetch_stock(item: dict) -> dict:
    """抓取 A 股个股 K 线 + 资金流"""
    code = item["code"]
    name = item.get("name", code)
    print(f"    {code} {name}", flush=True)
    result = {"code": code, "name": name, "note": item.get("note", ""), "type": "stock"}

    hist, source = fetch_stock_hist(code, START_DATE, TODAY, min_rows=5)
    if isinstance(hist, pd.DataFrame) and not hist.empty:
        result["indicators"] = compute_indicators(hist)
        result["kline_days"] = len(hist)
        result["data_source"] = source
        result["data_date"] = last_trade_date(hist)
    else:
        result["indicators"] = {}
        result["data_source"] = None
        result["data_date"] = None

    # 科创板 688xxx 资金流接口不支持
    if not code.startswith("688"):
        ff = fetch_fund_flow(code)
        result.update(ff)
    else:
        result["fund_flow"]   = []
        result["main_net_3d"] = []

    time.sleep(0.4)
    return result


def fetch_etf(item: dict) -> dict:
    """抓取 ETF / LOF K 线（无资金流数据）"""
    code = item["code"]
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

    hist, source = fetch_stock_hist(code, START_DATE, TODAY, min_rows=5)

    if isinstance(hist, pd.DataFrame) and not hist.empty:
        result["indicators"] = compute_indicators(hist)
        result["kline_days"] = len(hist)
        result["data_source"] = source
        result["data_date"] = last_trade_date(hist)
    else:
        result["indicators"] = {}
        result["data_source"] = None
        result["data_date"] = None

    time.sleep(0.4)
    return result


def fetch_market_overview() -> dict:
    print("  [大盘指数]", flush=True)
    indices = {}
    index_map = {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz399006": "创业板指",
        "sh000300": "沪深300",
        "sh000688": "科创50",
    }
    for code, iname in index_map.items():
        prefix, symbol = code[:2], code[2:]
        hist = safe(ak.stock_zh_index_daily, symbol=f"{prefix}{symbol}")
        if isinstance(hist, pd.DataFrame) and len(hist) >= 2:
            last = hist.tail(2)
            chg = (last["close"].iloc[-1] - last["close"].iloc[-2]) / last["close"].iloc[-2] * 100
            indices[iname] = {
                "close":      round(float(last["close"].iloc[-1]), 2),
                "change_pct": round(float(chg), 2),
            }
        time.sleep(0.3)
    return indices


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

    for sector in config.get("sectors", []):
        sname = sector["name"]
        print(f"\n  [{sname}]", flush=True)
        sec_data = {"name": sname, "stocks": [], "etfs": []}

        for item in sector.get("stocks", []):
            try:
                sec_data["stocks"].append(fetch_stock(item))
            except Exception:
                tb = traceback.format_exc()
                print(f"    ❌ {item['code']} 失败：{tb[:100]}", flush=True)
                output["errors"].append({"code": item["code"], "error": tb})

        for item in sector.get("etfs", []):
            try:
                sec_data["etfs"].append(fetch_etf(item))
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
