"""
批量分析所有 ETF — 多线程并发，每只独立日志
"""
# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import os
# 绕过系统代理，让 akshare/requests 直连国内数据源
for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(_k, None)

import sys, os, json, glob, time, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── 加载数据 + 实时补全缺失指标 ──────────────────────────
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

files = sorted(glob.glob(str(ROOT / "stock_data_*.json")))
with open(files[-1], encoding="utf-8") as f:
    DATA = json.load(f)

TODAY = datetime.now().strftime("%Y%m%d")
START = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

def _compute_indicators(df):
    if df is None or df.empty or len(df) < 5:
        return {}
    close = df["收盘"].astype(float)
    vol   = df["成交量"].astype(float)
    ma5   = close.rolling(5).mean().iloc[-1]
    ma20  = close.rolling(20).mean().iloc[-1] if len(close)>=20 else None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif   = ema12 - ema26
    dea   = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = (100 - 100/(1+gain/(loss+1e-9))).iloc[-1] if len(close)>=14 else None
    mid   = close.rolling(20).mean()
    std   = close.rolling(20).std()
    boll_upper = (mid+2*std).iloc[-1] if len(close)>=20 else None
    boll_lower = (mid-2*std).iloc[-1] if len(close)>=20 else None
    vol_ratio  = (vol.iloc[-1]/vol.rolling(5).mean().iloc[-2]) if len(vol)>5 else None
    up_days_20 = int((df["涨跌幅"].astype(float).tail(20)>0).sum()) if len(df)>=20 else None
    current = close.iloc[-1]
    prev    = close.iloc[-2] if len(close)>1 else current
    chg_pct = (current-prev)/prev*100
    kline_cols = [c for c in ["日期","开盘","收盘","最高","最低","成交量","涨跌幅"] if c in df.columns]
    return {
        "close": round(float(current),3), "change_pct": round(float(chg_pct),2),
        "ma5":   round(float(ma5),3),     "ma20": round(float(ma20),3) if ma20 else None,
        "dif":   round(float(dif.iloc[-1]),4), "dea": round(float(dea.iloc[-1]),4),
        "macd_bar": round(float(macd_bar.iloc[-1]),4),
        "rsi":      round(float(rsi),2) if rsi else None,
        "boll_upper": round(float(boll_upper),3) if boll_upper else None,
        "boll_lower": round(float(boll_lower),3) if boll_lower else None,
        "vol_ratio":  round(float(vol_ratio),2) if vol_ratio else None,
        "up_days_20": up_days_20,
        "kline_last5": df[kline_cols].tail(5).to_dict("records"),
    }

def _fetch_etf_indicators(code):
    for fn, kw in [
        (ak.fund_etf_hist_em, {"symbol":code,"period":"daily","start_date":START,"end_date":TODAY,"adjust":""}),
        (ak.stock_zh_a_hist,  {"symbol":code,"period":"daily","start_date":START,"end_date":TODAY,"adjust":"qfq"}),
    ]:
        try:
            df = fn(**kw)
            if isinstance(df, pd.DataFrame) and len(df) >= 5:
                return _compute_indicators(df)
        except:
            pass
    return {}

# 收集所有 ETF，缺指标的实时补抓
ALL_ETFS = []
print("📡 检查并补全 ETF 指标...")
for sector in DATA["sectors"]:
    for etf in sector.get("etfs", []):
        code = etf["code"]
        ind  = etf.get("indicators", {})
        if not ind or ind.get("error") or not ind.get("close"):
            print(f"  补抓 {code} {etf['name']}...", end=" ", flush=True)
            ind = _fetch_etf_indicators(code)
            etf["indicators"] = ind
            if ind.get("close"):
                print(f"✅ {ind['close']}")
            else:
                print("❌ 无数据")
        if ind and not ind.get("error") and ind.get("close"):
            ALL_ETFS.append((code, etf["name"], sector["name"]))

print(f"\n📋 共 {len(ALL_ETFS)} 只 ETF 有效数据，开始分析...\n")
print(f"{'代码':<8} {'名称':<20} {'板块'}")
print("─" * 50)
for code, name, sector in ALL_ETFS:
    print(f"  {code}  {name:<20} {sector}")
print("─" * 50)

# ── 分析函数（每只一个线程）────────────────────────────
from nasdx.analyzer import NasdxAnalyzer

_lock = threading.Lock()
_results = {}   # code → report
_done = 0

def analyze_one(code: str, name: str) -> dict:
    global _done
    try:
        analyzer = NasdxAnalyzer(max_steps=3, debate_rounds=1, agent_delay=0.1, battle_delay=0.1)
        report = analyzer.analyze(code, data=DATA, verbose=False)
        html_path = analyzer.save_report(report, fmt="html")
        json_path = analyzer.save_report(report, fmt="json")
        with _lock:
            _done += 1
            total = len(ALL_ETFS)
            sig = report.final_signal
            pct = report.bullish_pct
            emoji = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(sig,"")
            print(f"  [{_done:02d}/{total}] {emoji} {code} {name:<20} {sig:<8} 看多{pct:.0f}%")
        return {"code": code, "name": name, "signal": sig, "bullish_pct": pct,
                "html": html_path, "json": json_path, "ok": True}
    except Exception as e:
        with _lock:
            _done += 1
            print(f"  [{_done:02d}/{len(ALL_ETFS)}] ❌ {code} {name} — {str(e)[:60]}")
        return {"code": code, "name": name, "signal": "error", "ok": False, "error": str(e)}

# ── 并发执行（最多4线程，避免API过速）───────────────────
MAX_WORKERS = 4
start_time = time.time()
print(f"\n🚀 并发线程数: {MAX_WORKERS}，预计 {len(ALL_ETFS)//MAX_WORKERS * 4} 分钟...\n")

all_results = []
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = {executor.submit(analyze_one, code, name): (code, name)
               for code, name, _ in ALL_ETFS}
    for future in as_completed(futures):
        result = future.result()
        all_results.append(result)

elapsed = time.time() - start_time

# ── 汇总报告 ─────────────────────────────────────────
print(f"\n{'='*60}")
print(f"✅ 全部完成！耗时 {elapsed/60:.1f} 分钟")
print(f"{'='*60}\n")

# 按信号分组
bullish = [r for r in all_results if r.get("signal") == "bullish"]
bearish = [r for r in all_results if r.get("signal") == "bearish"]
neutral = [r for r in all_results if r.get("signal") == "neutral"]
errors  = [r for r in all_results if not r.get("ok")]

print(f"📈 看多 ({len(bullish)}只): " + ", ".join(f"{r['code']}{r['name']}" for r in bullish))
print(f"📉 看空 ({len(bearish)}只): " + ", ".join(f"{r['code']}{r['name']}" for r in bearish))
print(f"➡️  中性 ({len(neutral)}只): " + ", ".join(f"{r['code']}{r['name']}" for r in neutral))
if errors:
    print(f"❌ 失败 ({len(errors)}只): " + ", ".join(f"{r['code']}" for r in errors))

# 保存汇总JSON
summary = {
    "date": DATA["date"],
    "generated_at": datetime.now().isoformat(),
    "total": len(ALL_ETFS),
    "bullish": len(bullish), "bearish": len(bearish), "neutral": len(neutral),
    "elapsed_minutes": round(elapsed/60, 1),
    "results": sorted(all_results, key=lambda r: -r.get("bullish_pct", 0)),
}
summary_path = ROOT / "reports" / f"etf_summary_{DATA['date']}.json"
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f"\n📁 汇总: {summary_path}")
print(f"📁 个股报告: {ROOT}/reports/")
