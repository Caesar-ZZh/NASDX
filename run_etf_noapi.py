"""
ETF 纯技术面评分 — 无需 LLM API，基于指标规则即时出结果
输出：终端表格 + HTML汇总报告
"""
import os
# 绕过系统代理，让 akshare/requests 直连国内数据源
for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(_k, None)

import sys, json, glob
from pathlib import Path
from datetime import datetime
import akshare as ak
import pandas as pd
from datetime import timedelta

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TODAY = datetime.now().strftime("%Y%m%d")
START = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

# ── 读股票池 ─────────────────────────────────────────
with open(ROOT / "stocks.json", encoding="utf-8") as f:
    cfg = json.load(f)

# ── 补全指标 ─────────────────────────────────────────
def fetch_indicators(code):
    for fn, kw in [
        (ak.fund_etf_hist_em, {"symbol":code,"period":"daily","start_date":START,"end_date":TODAY,"adjust":""}),
        (ak.stock_zh_a_hist,  {"symbol":code,"period":"daily","start_date":START,"end_date":TODAY,"adjust":"qfq"}),
    ]:
        try:
            df = fn(**kw)
            if isinstance(df, pd.DataFrame) and len(df) >= 5:
                close = df["收盘"].astype(float)
                vol   = df["成交量"].astype(float)
                ma5   = close.rolling(5).mean().iloc[-1]
                ma10  = close.rolling(10).mean().iloc[-1] if len(close)>=10 else None
                ma20  = close.rolling(20).mean().iloc[-1] if len(close)>=20 else None
                ma60  = close.rolling(60).mean().iloc[-1] if len(close)>=60 else None
                ema12 = close.ewm(span=12,adjust=False).mean()
                ema26 = close.ewm(span=26,adjust=False).mean()
                dif   = ema12-ema26
                dea   = dif.ewm(span=9,adjust=False).mean()
                macd  = (dif-dea)*2
                delta = close.diff()
                gain  = delta.clip(lower=0).rolling(14).mean()
                loss  = (-delta.clip(upper=0)).rolling(14).mean()
                rsi   = (100-100/(1+gain/(loss+1e-9))).iloc[-1] if len(close)>=14 else 50
                mid   = close.rolling(20).mean()
                std_  = close.rolling(20).std()
                bu    = (mid+2*std_).iloc[-1] if len(close)>=20 else None
                bl    = (mid-2*std_).iloc[-1] if len(close)>=20 else None
                vr    = (vol.iloc[-1]/vol.rolling(5).mean().iloc[-2]) if len(vol)>5 else 1
                up20  = int((df["涨跌幅"].astype(float).tail(20)>0).sum()) if len(df)>=20 else 10
                cur   = close.iloc[-1]
                prev  = close.iloc[-2] if len(close)>1 else cur
                chg   = (cur-prev)/prev*100
                return {
                    "close":cur,"change_pct":round(chg,2),
                    "ma5":ma5,"ma10":ma10,"ma20":ma20,"ma60":ma60,
                    "macd_bar":macd.iloc[-1],"dif":dif.iloc[-1],"dea":dea.iloc[-1],
                    "rsi":rsi,"boll_upper":bu,"boll_lower":bl,
                    "vol_ratio":vr,"up_days_20":up20,
                    "kline_days":len(df),
                }
        except: pass
    return None

# ── 技术面评分 (0~100) ───────────────────────────────
def score(ind):
    if not ind: return 0, "neutral", []
    pts = 0
    reasons = []
    ma5,ma20,ma60 = ind.get("ma5"),ind.get("ma20"),ind.get("ma60")
    close = ind["close"]
    rsi   = ind.get("rsi",50) or 50
    macd  = ind.get("macd_bar",0) or 0
    vr    = ind.get("vol_ratio",1) or 1
    up20  = ind.get("up_days_20",10) or 10
    bu    = ind.get("boll_upper")
    bl    = ind.get("boll_lower")
    chg   = ind.get("change_pct",0) or 0

    # 均线 (30分)
    if ma5 and ma20:
        if ma5 > ma20:
            pts += 20; reasons.append(f"MA5>{ma20:.2f}多头")
        else:
            pts += 5;  reasons.append(f"MA5<{ma20:.2f}空头")
    if ma20 and ma60:
        if ma20 > ma60:
            pts += 10; reasons.append("中长期多头")
        else:
            reasons.append("中长期空头")

    # MACD (20分)
    if macd > 0:
        pts += 20; reasons.append(f"MACD金叉+{macd:.4f}")
    else:
        pts += 5;  reasons.append(f"MACD死叉{macd:.4f}")

    # RSI (20分)
    if 45 <= rsi <= 65:
        pts += 20; reasons.append(f"RSI={rsi:.0f}健康")
    elif 30 <= rsi < 45:
        pts += 15; reasons.append(f"RSI={rsi:.0f}偏弱")
    elif rsi < 30:
        pts += 10; reasons.append(f"RSI={rsi:.0f}超卖反弹")
    elif 65 < rsi <= 75:
        pts += 12; reasons.append(f"RSI={rsi:.0f}偏强")
    else:
        pts += 5;  reasons.append(f"RSI={rsi:.0f}超买")

    # 量比 (15分)
    if 1.0 <= vr <= 2.5:
        pts += 15; reasons.append(f"量比{vr:.1f}温和放量")
    elif vr > 2.5:
        pts += 10; reasons.append(f"量比{vr:.1f}放量异常")
    else:
        pts += 8;  reasons.append(f"量比{vr:.1f}缩量")

    # 布林带 (10分)
    if bu and bl:
        bw = bu - bl
        pos = (close - bl) / bw if bw > 0 else 0.5
        if 0.3 <= pos <= 0.7:
            pts += 10; reasons.append("布林带中轨区间")
        elif pos < 0.3:
            pts += 8;  reasons.append("布林带下轨支撑")
        else:
            pts += 5;  reasons.append("布林带上轨压力")

    # 上涨天数 (5分)
    if up20 >= 12: pts += 5; reasons.append(f"20日涨{up20}天强势")
    elif up20 >= 8: pts += 3
    else: reasons.append(f"20日仅涨{up20}天偏弱")

    # 信号
    if pts >= 65:   sig = "bullish"
    elif pts <= 40: sig = "bearish"
    else:           sig = "neutral"

    return pts, sig, reasons[:4]


# ── 主流程 ──────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  NASDX ETF 技术面扫描  {TODAY}  (纯规则，无需API)")
print(f"{'='*65}\n")

rows = []
for sector_cfg in cfg["sectors"]:
    sector_name = sector_cfg["name"]
    for etf_cfg in sector_cfg.get("etfs", []):
        code = etf_cfg["code"]
        name = etf_cfg["name"]
        print(f"  {code} {name}...", end=" ", flush=True)
        ind = fetch_indicators(code)
        if not ind:
            print("❌ 无数据")
            rows.append({"code":code,"name":name,"sector":sector_name,
                         "close":"-","chg":"-","score":0,"signal":"no_data","reasons":[],"ind":{}})
            continue
        sc, sig, rsns = score(ind)
        emoji = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(sig,"")
        print(f"{emoji} {sc}分 {sig}  收盘{ind['close']:.3f} 涨跌{ind['change_pct']:+.2f}%")
        rows.append({"code":code,"name":name,"sector":sector_name,
                     "close":ind["close"],"chg":ind["change_pct"],
                     "score":sc,"signal":sig,"reasons":rsns,"ind":ind})

# ── 终端汇总 ─────────────────────────────────────────
rows_valid = [r for r in rows if r["signal"] != "no_data"]
rows_valid.sort(key=lambda r: -r["score"])

print(f"\n{'='*65}")
print(f"  📊 评分排行榜（共{len(rows_valid)}只）")
print(f"{'='*65}")
print(f"  {'代码':<8}{'名称':<22}{'板块':<12}{'收盘':>7}{'涨跌':>7}  {'分数':>4}  信号")
print(f"  {'-'*65}")
for r in rows_valid:
    emoji = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(r["signal"],"")
    chg_s = f"{r['chg']:+.2f}%" if isinstance(r['chg'], float) else r['chg']
    close_s = f"{r['close']:.3f}" if isinstance(r['close'], float) else r['close']
    print(f"  {r['code']:<8}{r['name']:<22}{r['sector']:<12}{close_s:>7}{chg_s:>7}  {r['score']:>4}分  {emoji}{r['signal']}")

bull = [r for r in rows_valid if r["signal"]=="bullish"]
bear = [r for r in rows_valid if r["signal"]=="bearish"]
neut = [r for r in rows_valid if r["signal"]=="neutral"]
no_d = [r for r in rows if r["signal"]=="no_data"]
print(f"\n  📈看多:{len(bull)}  📉看空:{len(bear)}  ➡️中性:{len(neut)}  ❌无数据:{len(no_d)}")

# ── 生成 HTML 报告 ────────────────────────────────────
(ROOT / "reports").mkdir(exist_ok=True)
html_path = ROOT / "reports" / f"etf_scan_{TODAY}.html"

def _bar(score):
    color = "#00C853" if score>=65 else "#FF1744" if score<=40 else "#FFD600"
    return f'<div style="background:#21262d;border-radius:4px;height:8px;width:100%;"><div style="width:{score}%;height:100%;background:{color};border-radius:4px;"></div></div>'

rows_html = ""
for r in rows_valid:
    sig = r["signal"]
    color = {"bullish":"#00C853","bearish":"#FF1744","neutral":"#FFD600"}.get(sig,"#888")
    emoji = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(sig,"")
    chg_s = f"{r['chg']:+.2f}%" if isinstance(r['chg'],float) else r['chg']
    close_s = f"{r['close']:.3f}" if isinstance(r['close'],float) else r['close']
    chg_color = "#00C853" if isinstance(r['chg'],float) and r['chg']>0 else "#FF1744" if isinstance(r['chg'],float) and r['chg']<0 else "#c9d1d9"
    rsi_val = r['ind'].get('rsi')
    ma_trend = ""
    if r['ind'].get('ma5') and r['ind'].get('ma20'):
        ma_trend = "多头" if r['ind']['ma5']>r['ind']['ma20'] else "空头"
    reasons_html = " &nbsp;·&nbsp; ".join(r["reasons"]) if r["reasons"] else ""
    rows_html += f"""
<tr>
  <td style="color:#8b949e;font-size:12px;">{r['code']}</td>
  <td style="font-weight:bold;color:#fff;">{r['name']}</td>
  <td style="color:#8b949e;font-size:12px;">{r['sector']}</td>
  <td style="text-align:right;">{close_s}</td>
  <td style="text-align:right;color:{chg_color};">{chg_s}</td>
  <td style="color:{color};font-weight:bold;text-align:center;">{emoji} {r['score']}</td>
  <td><div style="display:flex;align-items:center;gap:8px;">{_bar(r['score'])}</div></td>
  <td style="color:{color};font-weight:bold;">{r['signal']}</td>
  <td style="color:#8b949e;font-size:11px;">{reasons_html}</td>
</tr>"""

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>NASDX ETF 技术扫描 {TODAY}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",monospace;font-size:14px;line-height:1.6;padding:24px}}
h1{{color:#fff;font-size:22px;margin-bottom:4px}}
.sub{{color:#8b949e;font-size:13px;margin-bottom:24px}}
.stats{{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}}
.stat{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 20px;text-align:center}}
.stat-val{{font-size:24px;font-weight:bold}}
.stat-lab{{font-size:11px;color:#8b949e;margin-top:2px}}
table{{width:100%;border-collapse:collapse}}
th{{color:#8b949e;font-size:11px;text-align:left;padding:8px 12px;border-bottom:2px solid #30363d;font-weight:normal;text-transform:uppercase;letter-spacing:0.5px}}
td{{padding:10px 12px;border-bottom:1px solid #21262d;vertical-align:middle}}
tr:hover td{{background:#161b22}}
.badge-bull{{background:#00C85318;color:#00C853;border:1px solid #00C85340;border-radius:10px;padding:2px 10px;font-size:11px}}
.badge-bear{{background:#FF174418;color:#FF1744;border:1px solid #FF174440;border-radius:10px;padding:2px 10px;font-size:11px}}
.badge-neutral{{background:#FFD60018;color:#FFD600;border:1px solid #FFD60040;border-radius:10px;padding:2px 10px;font-size:11px}}
.footer{{margin-top:32px;color:#8b949e;font-size:11px;text-align:center;border-top:1px solid #30363d;padding-top:16px}}
</style>
</head>
<body>
<h1>📊 NASDX ETF 技术面扫描</h1>
<div class="sub">数据日期：{TODAY} · 纯规则评分，无需AI · 共 {len(rows_valid)} 只</div>
<div class="stats">
  <div class="stat"><div class="stat-val" style="color:#00C853">{len(bull)}</div><div class="stat-lab">📈 看多</div></div>
  <div class="stat"><div class="stat-val" style="color:#FFD600">{len(neut)}</div><div class="stat-lab">➡️ 中性</div></div>
  <div class="stat"><div class="stat-val" style="color:#FF1744">{len(bear)}</div><div class="stat-lab">📉 看空</div></div>
  <div class="stat"><div class="stat-val" style="color:#8b949e">{len(no_d)}</div><div class="stat-lab">❌ 停牌/无数据</div></div>
  <div class="stat"><div class="stat-val" style="color:#58a6ff">{rows_valid[0]['score'] if rows_valid else 0}</div><div class="stat-lab">🏆 最高分</div></div>
</div>
<table>
<thead><tr>
  <th>代码</th><th>名称</th><th>板块</th><th style="text-align:right">收盘</th>
  <th style="text-align:right">涨跌</th><th style="text-align:center">评分</th>
  <th style="width:120px">进度</th><th>信号</th><th>关键指标</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
<div class="footer">⚠️ 纯技术规则评分，仅供参考，不构成投资建议 · NASDX · {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</body></html>"""

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html)

# 同时保存JSON
json_path = ROOT / "reports" / f"etf_scan_{TODAY}.json"
with open(json_path, "w", encoding="utf-8") as f:
    json.dump({
        "date": TODAY, "generated_at": datetime.now().isoformat(),
        "total": len(rows_valid), "bullish": len(bull), "bearish": len(bear), "neutral": len(neut),
        "results": [{k:v for k,v in r.items() if k!="ind"} for r in rows_valid],
    }, f, ensure_ascii=False, indent=2)

print(f"\n📁 HTML: {html_path}")
print(f"📁 JSON: {json_path}")
import subprocess
# [已移除自动弹窗] subprocess.Popen(["cmd","/c","start",str(html_path)])