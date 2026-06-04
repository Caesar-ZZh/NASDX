"""
50只ETF全量扫描 — 工作日早10点/下午2:30自动运行
输出：终端排行榜 + HTML报告（自动打开）+ JSON数据
"""
import os
# 绕过系统代理，让 akshare/requests 直连国内数据源
for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(_k, None)

import sys, json, time, os
from pathlib import Path
from datetime import datetime

import akshare as ak
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

NOW   = datetime.now()
TODAY = NOW.strftime('%Y%m%d')
HHMM  = NOW.strftime('%H%M')
START = (NOW - pd.Timedelta(days=90)).strftime('%Y%m%d')

# ── 加载池子 ──────────────────────────────────────────
with open(ROOT / 'etf50_pool.json', encoding='utf-8') as f:
    pool = json.load(f)['etfs']

print(f'\n{"="*65}')
print(f'  NASDX ETF50 全量扫描  {NOW.strftime("%Y-%m-%d %H:%M")}')
print(f'  共 {len(pool)} 只 ETF')
print(f'{"="*65}\n')

# ── 获取实时行情（场内价）— 这是今日真实涨跌 ──────────────
print('📡 获取实时行情...', end=' ', flush=True)
try:
    spot_df = ak.fund_etf_spot_em()
    spot_df['成交额'] = spot_df['成交额'].astype(float)
    spot_map = {}
    for _, r in spot_df.iterrows():
        try:
            spot_map[r['代码']] = {
                'price':  float(r['最新价']),
                'chg':    float(r['涨跌幅']),    # ← 今日实际涨跌幅
                'vol':    float(r['成交额']),
                'name':   r['名称'],
            }
        except: pass
    print(f'✅ {len(spot_map)} 只')
except Exception as e:
    spot_map = {}
    print(f'❌ {e}')

# ── 获取天天基金净值（历史序列）────────────────────────
def fetch_nav(code):
    try:
        df = ak.fund_etf_fund_info_em(fund=code, start_date=START, end_date=TODAY)
        if isinstance(df, pd.DataFrame) and len(df) >= 5:
            df = df.sort_values('净值日期').reset_index(drop=True)
            return df
    except: pass
    return None

# ── 计算技术指标 ─────────────────────────────────────
def calc(df):
    close    = df['单位净值'].astype(float)
    chg_pct  = df['日增长率'].astype(float)
    n = len(close)
    if n < 5: return {}

    ma5  = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1] if n>=20 else None
    ma60 = close.rolling(60).mean().iloc[-1] if n>=60 else None
    e12  = close.ewm(span=12,adjust=False).mean()
    e26  = close.ewm(span=26,adjust=False).mean()
    dif  = e12-e26; dea=dif.ewm(span=9,adjust=False).mean()
    macd = (dif-dea)*2
    d    = close.diff()
    g    = d.clip(lower=0).rolling(14).mean()
    l    = (-d.clip(upper=0)).rolling(14).mean()
    rsi  = (100-100/(1+g/(l+1e-9))).iloc[-1] if n>=14 else 50
    mid  = close.rolling(20).mean(); std_=close.rolling(20).std()
    bu   = (mid+2*std_).iloc[-1] if n>=20 else None
    bl   = (mid-2*std_).iloc[-1] if n>=20 else None
    up20 = int((chg_pct.tail(20)>0).sum()) if n>=20 else None
    cur  = close.iloc[-1]; prev=close.iloc[-2]
    chg  = (cur-prev)/prev*100
    return {
        'nav':round(float(cur),3), 'nav_chg':round(float(chg),2),
        'ma5':round(float(ma5),3),
        'ma20':round(float(ma20),3) if ma20 is not None else None,
        'ma60':round(float(ma60),3) if ma60 is not None else None,
        'macd_bar':round(float(macd.iloc[-1]),4),
        'dif':round(float(dif.iloc[-1]),4),'dea':round(float(dea.iloc[-1]),4),
        'rsi':round(float(rsi),2),
        'boll_upper':round(float(bu),3) if bu is not None else None,
        'boll_lower':round(float(bl),3) if bl is not None else None,
        'up_days_20':up20,
    }

# ── 评分 (0-100) ──────────────────────────────────────
def score(ind, spot_price=None):
    if not ind: return 0, 'neutral', []
    pts=0; reasons=[]
    ma5=ind.get('ma5'); ma20=ind.get('ma20'); ma60=ind.get('ma60')
    nav=ind['nav']; rsi=ind.get('rsi',50) or 50
    macd=ind.get('macd_bar',0) or 0
    up20=ind.get('up_days_20',10) or 10
    bu=ind.get('boll_upper'); bl=ind.get('boll_lower')

    # 均线 40分
    if ma5 and ma20:
        if ma5>ma20:
            pts+=25; reasons.append(f'MA5>MA20 多头排列')
        else:
            pts+=5;  reasons.append(f'MA5<MA20 空头排列')
    if ma20 and ma60:
        if ma20>ma60:
            pts+=15; reasons.append('中长期趋势向上')
        else:
            reasons.append('中长期趋势向下')

    # MACD 25分
    if macd>0.001:
        pts+=25; reasons.append(f'MACD金叉+{macd:.4f}')
    elif macd>-0.003:
        pts+=14; reasons.append(f'MACD蓄力{macd:.4f}')
    else:
        pts+=3;  reasons.append(f'MACD死叉{macd:.4f}')

    # RSI 20分
    if 45<=rsi<=65: pts+=20; reasons.append(f'RSI={rsi:.0f}健康')
    elif 35<=rsi<45:pts+=13; reasons.append(f'RSI={rsi:.0f}偏弱')
    elif rsi<35:    pts+=8;  reasons.append(f'RSI={rsi:.0f}超卖')
    elif 65<rsi<=75:pts+=14; reasons.append(f'RSI={rsi:.0f}偏强')
    else:           pts+=5;  reasons.append(f'RSI={rsi:.0f}超买')

    # 布林带 10分
    if bu and bl and (bu-bl)>0:
        pos=(nav-bl)/(bu-bl)
        if 0.3<=pos<=0.65: pts+=10; reasons.append(f'布林中轨{pos:.0%}')
        elif pos<0.3:      pts+=7;  reasons.append(f'布林下轨{pos:.0%}支撑')
        else:              pts+=4;  reasons.append(f'布林上轨{pos:.0%}压力')

    # 上涨天数 5分
    if up20 is not None:
        if up20>=12: pts+=5; reasons.append(f'20日涨{up20}天强势')
        elif up20>=8:pts+=3
        else:        reasons.append(f'20日仅涨{up20}天偏弱')

    # 溢价率加减分（若有实时价）
    premium = None
    if spot_price and nav:
        premium = (spot_price-nav)/nav*100
        if -0.5<=premium<=0.5:
            pts+=3; reasons.append(f'溢价{premium:+.2f}%合理')
        elif premium>2:
            pts-=5; reasons.append(f'⚠️溢价{premium:+.2f}%偏高')
        elif premium<-1:
            pts+=3; reasons.append(f'折价{premium:+.2f}%机会')

    sig = 'bullish' if pts>=65 else 'bearish' if pts<=40 else 'neutral'
    return min(100,pts), sig, reasons, premium

# ── 主循环 ───────────────────────────────────────────
results = []
for i, etf in enumerate(pool, 1):
    code = etf['code']; name = etf['name']; cat = etf['category']
    print(f'  [{i:02d}/{len(pool)}] {code} {name}...', end=' ', flush=True)

    # 实时行情
    # 实时行情 — 今日真实价格和涨跌
    spot = spot_map.get(code, {})
    spot_price = spot.get('price')   # 今日场内实时价
    spot_chg   = spot.get('chg')     # 今日实际涨跌幅（这才是对的）
    spot_vol   = spot.get('vol', 0)  # 成交额

    # 净值历史
    df = fetch_nav(code)
    if df is None:
        print('❌ 无净值数据')
        results.append({'code':code,'name':name,'category':cat,
                        'score':0,'signal':'no_data','premium':None,
                        'spot_price':spot_price,'spot_chg':spot_chg,'ind':{},'reasons':[]})
        time.sleep(0.1); continue

    ind = calc(df)
    if not ind:
        print('❌ 指标不足')
        results.append({'code':code,'name':name,'category':cat,
                        'score':0,'signal':'no_data','premium':None,
                        'spot_price':spot_price,'spot_chg':spot_chg,'ind':{},'reasons':[]})
        time.sleep(0.1); continue

    sc, sig, rsns, prem = score(ind, spot_price)
    emoji = {'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(sig,'')
    prem_s = f'溢价{prem:+.2f}%' if prem is not None else ''
    # 优先显示实时涨跌，没有则用净值涨跌
    real_chg_s = f'今日{spot_chg:+.2f}%' if spot_chg is not None else f'净值{ind.get("nav_chg",0):+.2f}%'
    spot_s2 = f'场内{spot_price:.3f}' if spot_price is not None else '场内N/A'
    rsi_v = ind.get('rsi') or 0; macd_v = ind.get('macd_bar') or 0
    print(f'{emoji}{sc}分 {sig}  {real_chg_s}  {spot_s2}  RSI={rsi_v:.0f}  MACD={macd_v:+.4f}  {prem_s}')

    results.append({'code':code,'name':name,'category':cat,
                    'score':sc,'signal':sig,'premium':prem,
                    'spot_price':spot_price,'spot_chg':spot_chg,'ind':ind,'reasons':rsns})
    time.sleep(0.15)

# ── 排行榜 ───────────────────────────────────────────
valid = sorted([r for r in results if r['signal']!='no_data'], key=lambda r:-r['score'])
no_data = [r for r in results if r['signal']=='no_data']
bull = [r for r in valid if r['signal']=='bullish']
neut = [r for r in valid if r['signal']=='neutral']
bear = [r for r in valid if r['signal']=='bearish']

print(f'\n{"="*70}')
print(f'  🏆 ETF50 评分排行榜  {NOW.strftime("%H:%M")}')
print(f'  📈看多:{len(bull)}  ➡️中性:{len(neut)}  📉看空:{len(bear)}  ❌无数据:{len(no_data)}')
print(f'{"="*70}')
print(f'  {"排名":<4}{"代码":<8}{"名称":<22}{"类别":<14}{"净值":>7}{"涨跌":>7}{"场内":>8}{"溢价":>7}  {"分":>3}  信号')
print(f'  {"-"*70}')
for i,r in enumerate(valid[:20],1):
    ind=r['ind']
    nav_s   = f"{ind.get('nav',''):.3f}" if ind.get('nav') else '-'
    # 优先用今日实时涨跌，fallback到净值涨跌
    today_chg = r.get('spot_chg')
    chg_s   = f"{today_chg:+.2f}%" if today_chg is not None else (f"{ind.get('nav_chg',0):+.2f}%*" if ind.get('nav_chg') is not None else '-')
    spot_s  = f"{r['spot_price']:.3f}" if r['spot_price'] else '-'
    prem_s  = f"{r['premium']:+.2f}%" if r['premium'] is not None else '-'
    emoji   = {'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(r['signal'],'')
    star    = '⭐' if i<=3 else '  '
    print(f'  {star}{i:<3} {r["code"]:<8}{r["name"]:<22}{r["category"]:<14}{nav_s:>7}{chg_s:>8}{spot_s:>8}{prem_s:>7}  {r["score"]:>3}  {emoji}{r["signal"]}')

if len(valid) >= 1:
    print(f'  🥇 #1 推荐: {valid[0]["code"]} {valid[0]["name"]} ({valid[0]["score"]}分)')
if len(valid) >= 2:
    print(f'  🥈 #2 推荐: {valid[1]["code"]} {valid[1]["name"]} ({valid[1]["score"]}分)')
if len(valid) >= 3:
    print(f'  🥉 #3 推荐: {valid[2]["code"]} {valid[2]["name"]} ({valid[2]["score"]}分)')
if len(valid) == 0:
    print(f'  ⚠️ 无有效扫描结果')

# ── 生成HTML报告 ─────────────────────────────────────
def sc_color(sc):
    return '#00C853' if sc>=65 else '#FF1744' if sc<=40 else '#FFD600'

def bar(v):
    c=sc_color(v)
    return f'<div style="background:#21262d;border-radius:3px;height:6px;"><div style="width:{v}%;height:100%;background:{c};border-radius:3px;"></div></div>'

rows_html = ''
for i,r in enumerate(valid,1):
    ind=r['ind']
    sc=r['score']; sig=r['signal']
    sc_c=sc_color(sc)
    em={'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(sig,'')
    sl={'bullish':'看多','bearish':'看空','neutral':'中性'}.get(sig,sig)
    nav_s=f"{ind.get('nav',''):.3f}" if ind.get('nav') else '-'
    # 今日真实涨跌 = 实时行情，不是净值
    today_chg = r.get('spot_chg')
    display_chg = today_chg if today_chg is not None else (ind.get('nav_chg') or 0)
    chg_label = f"{display_chg:+.2f}%" if display_chg is not None else '-'
    chg_c='#00C853' if (display_chg or 0)>0 else '#FF1744' if (display_chg or 0)<0 else '#8b949e'
    spot_s=f"{r['spot_price']:.3f}" if r['spot_price'] else '-'
    spot_chg_v=r.get('spot_chg') or 0
    sc2='#00C853' if spot_chg_v>0 else '#FF1744' if spot_chg_v<0 else '#8b949e'
    prem_s=f"{r['premium']:+.2f}%" if r['premium'] is not None else '-'
    prem_c='#FF1744' if (r['premium'] or 0)>1.5 else '#00C853' if (r['premium'] or 0)<-0.5 else '#8b949e'
    rsi_v=ind.get('rsi',50) or 50
    rsi_c='#FF1744' if rsi_v>70 else '#00C853' if rsi_v<30 else '#58a6ff'
    macd_v=ind.get('macd_bar',0) or 0
    rsns='  '.join(f'<span style="background:#21262d;border-radius:3px;padding:1px 6px;font-size:10px;">{x}</span>' for x in r['reasons'][:3])
    medal = {1:'🥇',2:'🥈',3:'🥉'}.get(i,'')
    rows_html += f'''<tr style="{'background:#1a2332;' if i<=3 else ''}">
  <td style="color:#8b949e;font-size:12px;text-align:center;">{medal}{i}</td>
  <td style="font-size:12px;color:#8b949e;">{r["code"]}</td>
  <td style="font-weight:bold;color:#fff;">{r["name"]}</td>
  <td style="font-size:11px;color:#58a6ff;">{r["category"]}</td>
  <td style="text-align:right;">{nav_s}</td>
  <td style="text-align:right;color:{chg_c};">{chg_label}</td>
  <td style="text-align:right;">{spot_s}</td>
  <td style="text-align:right;color:{prem_c};">{prem_s}</td>
  <td style="text-align:right;color:{rsi_c};">{rsi_v:.0f}</td>
  <td style="text-align:right;color:{"#00C853" if macd_v>0 else "#FF1744"};">{macd_v:+.4f}</td>
  <td style="text-align:center;width:80px;">{bar(sc)}<div style="font-size:11px;color:{sc_c};text-align:center;margin-top:2px;">{sc}</div></td>
  <td style="color:{sc_c};font-weight:bold;">{em}{sl}</td>
  <td style="font-size:11px;">{rsns}</td>
</tr>'''

no_rows = ''
for r in no_data:
    no_rows += f'<tr><td colspan="13" style="color:#555;font-size:12px;padding:4px 8px;">{r["code"]} {r["name"]} — 暂无数据</td></tr>'

top3 = valid[:3]
top3_html = ''
for i,r in enumerate(top3,1):
    ind=r['ind']
    medal={1:'🥇',2:'🥈',3:'🥉'}.get(i,'')
    sc_c=sc_color(r['score'])
    prem_tip=''
    if r.get('premium') is not None:
        prem_c = "#FF1744" if r["premium"]>1 else "#00C853"
        prem_tip=f'溢价 <span style="color:{prem_c}">{r["premium"]:+.2f}%</span>'
    top3_html += f'''
<div style="background:#161b22;border:2px solid {sc_c};border-radius:10px;padding:16px;flex:1;min-width:200px;">
  <div style="font-size:22px;">{medal}</div>
  <div style="font-size:16px;font-weight:bold;color:#fff;margin:4px 0;">{r["code"]} {r["name"]}</div>
  <div style="font-size:12px;color:#58a6ff;">{r["category"]}</div>
  <div style="font-size:24px;font-weight:bold;color:{sc_c};margin:8px 0;">{r["score"]}分</div>
  <div style="font-size:13px;color:#8b949e;">净值 {ind.get("nav","N/A")}  {prem_tip}</div>
  <div style="font-size:12px;color:#8b949e;margin-top:4px;">RSI={ind.get("rsi","-"):.0f}  MACD={ind.get("macd_bar",0):+.4f}</div>
</div>'''

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="1800">
<title>NASDX ETF50 {NOW.strftime('%H:%M')}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",monospace;padding:20px;font-size:13px;}}
table{{width:100%;border-collapse:collapse;}}
th{{color:#8b949e;font-size:10px;text-align:left;padding:6px 8px;border-bottom:2px solid #30363d;text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap;}}
td{{padding:7px 8px;border-bottom:1px solid #1a1f29;vertical-align:middle;white-space:nowrap;}}
tr:hover td{{background:#161b22!important;}}
</style>
</head>
<body>
<div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #30363d;padding-bottom:16px;margin-bottom:20px;flex-wrap:wrap;gap:8px;">
  <div>
    <h1 style="color:#fff;font-size:22px;">📊 NASDX ETF50 全量扫描</h1>
    <div style="color:#8b949e;font-size:12px;margin-top:4px;">{NOW.strftime('%Y-%m-%d %H:%M')} · {len(valid)}只有效 · 每30分钟自动刷新</div>
  </div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 14px;text-align:center;">
      <div style="font-size:18px;font-weight:bold;color:#00C853;">{len(bull)}</div><div style="font-size:10px;color:#8b949e;">📈看多</div></div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 14px;text-align:center;">
      <div style="font-size:18px;font-weight:bold;color:#FFD600;">{len(neut)}</div><div style="font-size:10px;color:#8b949e;">➡️中性</div></div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 14px;text-align:center;">
      <div style="font-size:18px;font-weight:bold;color:#FF1744;">{len(bear)}</div><div style="font-size:10px;color:#8b949e;">📉看空</div></div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 14px;text-align:center;">
      <div style="font-size:18px;font-weight:bold;color:#8b949e;">{len(no_data)}</div><div style="font-size:10px;color:#8b949e;">❌无数据</div></div>
  </div>
</div>

<div style="margin-bottom:24px;">
  <div style="font-size:14px;color:#58a6ff;font-weight:bold;margin-bottom:12px;">🏆 今日投资前三名</div>
  <div style="display:flex;gap:12px;flex-wrap:wrap;">{top3_html}</div>
</div>

<table>
<thead><tr>
  <th>排名</th><th>代码</th><th>名称</th><th>类别</th>
  <th style="text-align:right">昨日净值</th><th style="text-align:right">今日涨跌↑实时</th>
  <th style="text-align:right">场内实时价</th><th style="text-align:right">溢价率</th>
  <th style="text-align:right">RSI</th><th style="text-align:right">MACD</th>
  <th style="text-align:center">评分</th><th>信号</th><th>关键指标</th>
</tr></thead>
<tbody>{rows_html}{no_rows}</tbody>
</table>

<div style="margin-top:24px;padding-top:12px;border-top:1px solid #30363d;color:#555;font-size:11px;text-align:center;">
  ⚠️ 技术规则评分，净值T+1，溢价率仅供参考，不构成投资建议 · NASDX · {NOW.strftime('%Y-%m-%d %H:%M:%S')}
</div>
</body></html>"""

out = ROOT / 'reports' / f'etf50_{TODAY}_{HHMM}.html'
out.parent.mkdir(exist_ok=True)
with open(out,'w',encoding='utf-8') as f:
    f.write(html)

# 同时更新 latest 链接（覆盖）
latest = ROOT / 'reports' / 'etf50_latest.html'
with open(latest,'w',encoding='utf-8') as f:
    f.write(html)

# 保存JSON
json_out = ROOT / 'reports' / f'etf50_{TODAY}_{HHMM}.json'
with open(json_out,'w',encoding='utf-8') as f:
    json.dump({
        'datetime': NOW.isoformat(),
        'total': len(valid),
        'bullish':len(bull),'neutral':len(neut),'bearish':len(bear),
        'top3': [{'code':r['code'],'name':r['name'],'score':r['score'],
                  'signal':r['signal'],'premium':r['premium']} for r in top3],
        'results': [{k:v for k,v in r.items() if k!='ind'} for r in valid],
    }, f, ensure_ascii=False, indent=2)

print(f'\n📁 HTML: {out}')
print(f'📁 JSON: {json_out}')
print(f'📁 最新报告: {latest}')

import subprocess
subprocess.Popen(['cmd','/c','start',str(out)])
