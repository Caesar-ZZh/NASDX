"""
60只个股完整扫描 — 分批抓取，失败自动重试，合并历史结果
"""
import requests as _req
_real_get = _req.get
def _patched_get(url, **kwargs):
    if 'eastmoney.com' in url:
        s = _req.Session()
        s.trust_env = True
        return s.get(url, **kwargs)
    return _real_get(url, **kwargs)
_req.get = _patched_get

import sys, json, time
from pathlib import Path
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

ROOT  = Path(__file__).parent
NOW   = datetime.now()
TODAY = NOW.strftime('%Y%m%d')
HHMM  = NOW.strftime('%H%M')
START = (NOW - timedelta(days=90)).strftime('%Y%m%d')

STOCK_POOL = [
    ("通信·光模块",   "300308", "中际旭创"),
    ("通信·光模块",   "300502", "新易盛"),
    ("通信·光模块",   "600498", "烽火通信"),
    ("通信·光模块",   "000063", "中兴通讯"),
    ("通信·光模块",   "000988", "华工科技"),
    ("通信·光模块",   "300763", "锦浪科技"),

    ("半导体·芯片",   "603501", "韦尔股份"),
    ("半导体·芯片",   "603986", "兆易创新"),
    ("半导体·芯片",   "002049", "紫光国微"),
    ("半导体·芯片",   "300223", "北京君正"),
    ("半导体·芯片",   "688981", "中芯国际"),
    ("半导体·芯片",   "688347", "华虹半导体"),

    ("半导体设备",    "002371", "北方华创"),
    ("半导体设备",    "688012", "中微公司"),
    ("半导体设备",    "688120", "华海清科"),
    ("半导体设备",    "688037", "芯源微"),
    ("半导体设备",    "688082", "盛美上海"),
    ("半导体设备",    "688036", "传音控股"),

    ("AI算力·服务器", "688256", "寒武纪"),
    ("AI算力·服务器", "688041", "海光信息"),
    ("AI算力·服务器", "603019", "中科曙光"),
    ("AI算力·服务器", "002230", "科大讯飞"),
    ("AI算力·服务器", "002415", "海康威视"),
    ("AI算力·服务器", "300496", "中科创达"),

    ("军工·航空",    "000768", "中航西飞"),
    ("军工·航空",    "600893", "航发动力"),
    ("军工·航空",    "002179", "中航光电"),
    ("军工·航空",    "000733", "振华科技"),
    ("军工·航空",    "600765", "中航重机"),
    ("军工·航空",    "002050", "三花智控"),

    ("电力·电网",    "600900", "长江电力"),
    ("电力·电网",    "600406", "国电南瑞"),
    ("电力·电网",    "600905", "三峡能源"),
    ("电力·电网",    "601985", "中国核电"),
    ("电力·电网",    "600089", "特变电工"),
    ("电力·电网",    "601669", "中国电建"),

    ("机器人·人形",  "300024", "机器人"),
    ("机器人·人形",  "002527", "新时达"),
    ("机器人·人形",  "300699", "光威复材"),
    ("机器人·人形",  "300476", "胜宏科技"),
    ("机器人·人形",  "002747", "埃斯顿"),
    ("机器人·人形",  "688169", "石头科技"),

    ("消费·白酒",   "600519", "贵州茅台"),
    ("消费·白酒",   "000858", "五粮液"),
    ("消费·白酒",   "000568", "泸州老窖"),
    ("消费·白酒",   "002304", "洋河股份"),
    ("消费·白酒",   "603288", "海天味业"),
    ("消费·白酒",   "300498", "温氏股份"),

    ("医药·创新药",  "600276", "恒瑞医药"),
    ("医药·创新药",  "300760", "迈瑞医疗"),
    ("医药·创新药",  "002007", "华兰生物"),
    ("医药·创新药",  "600436", "片仔癀"),
    ("医药·创新药",  "300347", "泰格医药"),
    ("医药·创新药",  "688321", "科伦博泰"),

    ("金融·券商",   "600030", "中信证券"),
    ("金融·券商",   "000166", "申万宏源"),
    ("金融·券商",   "601688", "华泰证券"),
    ("金融·券商",   "002736", "国信证券"),
    ("金融·券商",   "600886", "国投电力"),
    ("金融·券商",   "600837", "海通证券"),
]

# ── 抓取+计算 ─────────────────────────────────────────
def fetch_and_calc(code, name, sector):
    time.sleep(1.0)
    df = None
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period='daily',
                                    start_date=START, end_date=TODAY, adjust='qfq')
            if isinstance(df, pd.DataFrame) and len(df) >= 10:
                break
            df = None
        except Exception:
            if attempt < 2:
                time.sleep(3 ** attempt)
            df = None
    if df is None or len(df) < 10:
        return None
    try:
        close = df['收盘'].astype(float)
        vol   = df['成交量'].astype(float)
        n = len(close)
        ma5  = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1] if n>=10 else None
        ma20 = close.rolling(20).mean().iloc[-1] if n>=20 else None
        ma60 = close.rolling(60).mean().iloc[-1] if n>=60 else None
        e12  = close.ewm(span=12,adjust=False).mean()
        e26  = close.ewm(span=26,adjust=False).mean()
        dif  = e12-e26; dea=dif.ewm(span=9,adjust=False).mean()
        macd = (dif-dea)*2
        delta= close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi  = (100-100/(1+gain/(loss+1e-9))).iloc[-1] if n>=14 else 50
        mid  = close.rolling(20).mean(); std_=close.rolling(20).std()
        bu   = (mid+2*std_).iloc[-1] if n>=20 else None
        bl   = (mid-2*std_).iloc[-1] if n>=20 else None
        vr   = (vol.iloc[-1]/vol.rolling(5).mean().iloc[-2]) if n>5 else 1
        up20 = int((df['涨跌幅'].astype(float).tail(20)>0).sum()) if n>=20 else 10
        cur  = close.iloc[-1]; prev=close.iloc[-2]
        chg  = (cur-prev)/prev*100
        turnover = df['换手率'].astype(float).iloc[-1] if '换手率' in df.columns else None
        return {
            'code':code,'name':name,'sector':sector,
            'close':round(float(cur),2),'chg':round(float(chg),2),
            'turnover':round(float(turnover),2) if turnover else None,
            'ma5':round(float(ma5),2),
            'ma10':round(float(ma10),2) if ma10 else None,
            'ma20':round(float(ma20),2) if ma20 else None,
            'ma60':round(float(ma60),2) if ma60 else None,
            'macd_bar':round(float(macd.iloc[-1]),4),
            'dif':round(float(dif.iloc[-1]),4),'dea':round(float(dea.iloc[-1]),4),
            'rsi':round(float(rsi),1),
            'boll_upper':round(float(bu),2) if bu else None,
            'boll_lower':round(float(bl),2) if bl else None,
            'vol_ratio':round(float(vr),2),'up_days_20':up20,'kline_days':n,
        }
    except:
        return None

# ── 评分 ─────────────────────────────────────────────
def score(s):
    pts=0; reasons=[]
    ma5=s.get('ma5'); ma20=s.get('ma20'); ma60=s.get('ma60')
    close=s['close']; rsi=s.get('rsi',50) or 50
    macd=s.get('macd_bar',0) or 0
    vr=s.get('vol_ratio',1) or 1
    up20=s.get('up_days_20',10) or 10
    bu=s.get('boll_upper'); bl=s.get('boll_lower')
    if ma5 and ma20:
        if ma5>ma20: pts+=25; reasons.append(f'MA多头({ma5:.1f}>{ma20:.1f})')
        else: pts+=5; reasons.append(f'MA空头({ma5:.1f}<{ma20:.1f})')
    if ma20 and ma60:
        if ma20>ma60: pts+=10; reasons.append('中长期多头')
        else: reasons.append('中长期空头')
    if macd>0.01:   pts+=25; reasons.append(f'MACD金叉+{macd:.3f}')
    elif macd>-0.01:pts+=14; reasons.append('MACD蓄力')
    else:            pts+=3;  reasons.append(f'MACD死叉{macd:.3f}')
    if 45<=rsi<=65:  pts+=20; reasons.append(f'RSI={rsi:.0f}健康')
    elif 35<=rsi<45: pts+=13; reasons.append(f'RSI={rsi:.0f}偏弱')
    elif rsi<35:     pts+=8;  reasons.append(f'RSI={rsi:.0f}超卖')
    elif 65<rsi<=75: pts+=14; reasons.append(f'RSI={rsi:.0f}偏强')
    else:            pts+=5;  reasons.append(f'RSI={rsi:.0f}超买')
    if 1.2<=vr<=3.0: pts+=10; reasons.append(f'量比{vr:.1f}放量')
    elif vr>3.0:     pts+=7;  reasons.append(f'量比{vr:.1f}异常')
    else:            pts+=5;  reasons.append(f'量比{vr:.1f}缩量')
    if bu and bl and (bu-bl)>0:
        pos=(close-bl)/(bu-bl)
        if 0.3<=pos<=0.65: pts+=7; reasons.append('布林中轨')
        elif pos<0.3:      pts+=5; reasons.append('布林下轨支撑')
        else:              pts+=3; reasons.append('布林上轨压力')
    if up20>=12: pts+=3; reasons.append(f'20日涨{up20}天')
    elif up20>=8: pts+=2
    sig='bullish' if pts>=65 else 'bearish' if pts<=40 else 'neutral'
    return min(100,pts),sig,reasons[:4]

# ── 主流程 ────────────────────────────────────────────
print(f'\n{"="*68}')
print(f'  NASDX 60只个股完整扫描  {NOW.strftime("%Y-%m-%d %H:%M")}')
print(f'{"="*68}\n')

results = []
for i,(sector,code,name) in enumerate(STOCK_POOL,1):
    print(f'  [{i:02d}/{len(STOCK_POOL)}] {code} {name:<10}({sector})...',end=' ',flush=True)
    data = fetch_and_calc(code,name,sector)
    if data is None:
        print('❌ 无数据'); continue
    sc,sig,rsns = score(data)
    data['score']=sc; data['signal']=sig; data['reasons']=rsns
    em={'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(sig,'')
    print(f'{em}{sc}分 {sig}  {data["chg"]:+.2f}%  RSI={data["rsi"]:.0f}  量比{data["vol_ratio"]:.1f}')
    results.append(data)

# ── 排行榜 ────────────────────────────────────────────
valid = sorted(results,key=lambda r:-r['score'])
bull=[r for r in valid if r['signal']=='bullish']
neut=[r for r in valid if r['signal']=='neutral']
bear=[r for r in valid if r['signal']=='bearish']
failed=len(STOCK_POOL)-len(valid)

print(f'\n{"="*68}')
print(f'  🏆 60只个股评分排行  {NOW.strftime("%H:%M")}')
print(f'  📈{len(bull)}  ➡️{len(neut)}  📉{len(bear)}  ❌{failed}只无数据（剩余{len(valid)}只）')
print(f'{"="*68}')
print(f'  {"排":<3}{"代码":<8}{"名称":<10}{"板块":<14}{"收盘":>8}{"涨跌":>8}{"换手":>6}{"量比":>5}  {"分":>3}  信号')
print(f'  {"-"*70}')
for i,r in enumerate(valid,1):
    em={'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(r['signal'],'')
    star='⭐' if i<=3 else '  '
    ts=f"{r['turnover']:.1f}%" if r.get('turnover') else '-'
    print(f'  {star}{i:<2} {r["code"]:<8}{r["name"]:<10}{r["sector"]:<14}{r["close"]:>8.2f}{r["chg"]:>+8.2f}%{ts:>6}{r["vol_ratio"]:>5.1f}  {r["score"]:>3}  {em}{r["signal"]}')

# ── 生成HTML ───────────────────────────────────────────
def sc_color(v): return '#00C853' if v>=65 else '#FF1744' if v<=40 else '#FFD600'
def bar(v):
    c=sc_color(v)
    return f'<div style="background:#21262d;border-radius:3px;height:6px;"><div style="width:{v}%;height:100%;background:{c};border-radius:3px;"></div></div>'

# 板块统计
sector_stats={}
for r in valid:
    s=r['sector']
    if s not in sector_stats: sector_stats[s]={'bull':0,'bear':0,'neut':0,'top':None,'top_sc':0,'top_chg':0}
    if r['signal']=='bullish': sector_stats[s]['bull']+=1
    elif r['signal']=='bearish': sector_stats[s]['bear']+=1
    else: sector_stats[s]['neut']+=1
    if r['score']>sector_stats[s]['top_sc']:
        sector_stats[s]['top']=r['name']; sector_stats[s]['top_sc']=r['score']; sector_stats[s]['top_chg']=r['chg']

sector_html=''
for s,v in sorted(sector_stats.items(),key=lambda x:-(x[1]['bull']/(x[1]['bull']+x[1]['bear']+x[1]['neut']+0.01))):
    total=v['bull']+v['bear']+v['neut']
    bp=v['bull']/total*100 if total else 0
    sc_c='#00C853' if bp>=60 else '#FF1744' if bp<=30 else '#FFD600'
    chg_c='#00C853' if v['top_chg']>0 else '#FF1744'
    sector_html+=f'''<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 14px;min-width:150px;">
  <div style="font-size:11px;color:#58a6ff;font-weight:bold;margin-bottom:4px;">{s}</div>
  <div style="font-size:20px;font-weight:bold;color:{sc_c};">{bp:.0f}%<span style="font-size:11px;color:#8b949e;"> 看多</span></div>
  <div style="font-size:11px;color:#8b949e;margin-top:4px;">龙头: <span style="color:#fff;">{v["top"]}</span> <span style="color:{chg_c};">{v["top_chg"]:+.1f}%</span></div>
</div>'''

rows_html=''
for i,r in enumerate(valid,1):
    sc=r['score']; sig=r['signal']
    sc_c=sc_color(sc)
    em={'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(sig,'')
    sl={'bullish':'看多','bearish':'看空','neutral':'中性'}.get(sig,sig)
    chg_c='#00C853' if r['chg']>0 else '#FF1744' if r['chg']<0 else '#8b949e'
    rsi_c='#FF1744' if r['rsi']>75 else '#00C853' if r['rsi']<30 else '#58a6ff'
    vr_c='#00C853' if r['vol_ratio']>1.5 else '#8b949e'
    medal={1:'🥇',2:'🥈',3:'🥉'}.get(i,'')
    ts=f"{r['turnover']:.2f}%" if r.get('turnover') else '-'
    rsns=' &nbsp;·&nbsp; '.join(f'<span style="background:#21262d;border-radius:3px;padding:1px 6px;font-size:10px;">{x}</span>' for x in r['reasons'][:3])
    rows_html+=f'''<tr style="{'background:#1a2332;border-left:3px solid '+sc_c+';' if i<=3 else ''}">
  <td style="color:#8b949e;font-size:12px;text-align:center;">{medal}{i}</td>
  <td style="font-size:12px;color:#8b949e;">{r["code"]}</td>
  <td style="font-weight:bold;color:#fff;">{r["name"]}</td>
  <td style="font-size:11px;color:#58a6ff;">{r["sector"]}</td>
  <td style="text-align:right;font-weight:bold;">{r["close"]}</td>
  <td style="text-align:right;color:{chg_c};font-weight:bold;">{r["chg"]:+.2f}%</td>
  <td style="text-align:right;color:#8b949e;">{ts}</td>
  <td style="text-align:right;color:{vr_c};">{r["vol_ratio"]:.1f}</td>
  <td style="text-align:right;color:{rsi_c};">{r["rsi"]:.0f}</td>
  <td style="text-align:right;color:{"#00C853" if r.get("macd_bar",0)>0 else "#FF1744"};">{r.get("macd_bar",0):+.3f}</td>
  <td style="width:80px;">{bar(sc)}<div style="font-size:11px;color:{sc_c};text-align:center;">{sc}</div></td>
  <td style="color:{sc_c};font-weight:bold;">{em}{sl}</td>
  <td style="font-size:11px;">{rsns}</td>
</tr>'''

top3=valid[:3]
top3_html=''
for i,r in enumerate(top3,1):
    medal={1:'🥇',2:'🥈',3:'🥉'}[i]
    sc_c=sc_color(r['score'])
    chg_c='#00C853' if r['chg']>0 else '#FF1744'
    ma_s='MA多头' if r.get('ma5') and r.get('ma20') and r['ma5']>r['ma20'] else 'MA空头'
    top3_html+=f'''<div style="background:#161b22;border:2px solid {sc_c};border-radius:12px;padding:18px;flex:1;min-width:180px;">
  <div style="font-size:24px;">{medal}</div>
  <div style="font-size:17px;font-weight:bold;color:#fff;margin:6px 0;">{r["code"]} {r["name"]}</div>
  <div style="font-size:11px;color:#58a6ff;margin-bottom:8px;">{r["sector"]}</div>
  <div style="font-size:28px;font-weight:bold;color:{sc_c};">{r["score"]}分</div>
  <div style="font-size:15px;color:{chg_c};font-weight:bold;margin:4px 0;">{r["chg"]:+.2f}%</div>
  <div style="font-size:12px;color:#8b949e;">RSI={r["rsi"]:.0f} · 量比{r["vol_ratio"]:.1f} · {ma_s}</div>
  <div style="font-size:11px;color:#8b949e;margin-top:6px;">{" · ".join(r["reasons"][:2])}</div>
</div>'''

html=f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>NASDX 60只个股扫描 {NOW.strftime('%H:%M')}</title>
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
    <h1 style="color:#fff;font-size:22px;">📊 NASDX 热门板块60只个股完整扫描</h1>
    <div style="color:#8b949e;font-size:12px;margin-top:4px;">{NOW.strftime('%Y-%m-%d %H:%M')} · {len(valid)}/{len(STOCK_POOL)}只有效 · 10大热门板块</div>
  </div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 16px;text-align:center;">
      <div style="font-size:22px;font-weight:bold;color:#00C853;">{len(bull)}</div><div style="font-size:10px;color:#8b949e;">📈看多</div></div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 16px;text-align:center;">
      <div style="font-size:22px;font-weight:bold;color:#FFD600;">{len(neut)}</div><div style="font-size:10px;color:#8b949e;">➡️中性</div></div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 16px;text-align:center;">
      <div style="font-size:22px;font-weight:bold;color:#FF1744;">{len(bear)}</div><div style="font-size:10px;color:#8b949e;">📉看空</div></div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 16px;text-align:center;">
      <div style="font-size:22px;font-weight:bold;color:#8b949e;">{failed}</div><div style="font-size:10px;color:#8b949e;">❌无数据</div></div>
  </div>
</div>

<div style="margin-bottom:24px;">
  <div style="font-size:14px;color:#58a6ff;font-weight:bold;margin-bottom:12px;">🏆 今日前三名</div>
  <div style="display:flex;gap:14px;flex-wrap:wrap;">{top3_html}</div>
</div>

<div style="margin-bottom:24px;">
  <div style="font-size:14px;color:#58a6ff;font-weight:bold;margin-bottom:12px;">📊 板块强弱（看多占比）</div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;">{sector_html}</div>
</div>

<table>
<thead><tr>
  <th>排名</th><th>代码</th><th>名称</th><th>板块</th>
  <th style="text-align:right">收盘</th><th style="text-align:right">涨跌</th>
  <th style="text-align:right">换手</th><th style="text-align:right">量比</th>
  <th style="text-align:right">RSI</th><th style="text-align:right">MACD</th>
  <th style="text-align:center">评分</th><th>信号</th><th>关键指标</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>

<div style="margin-top:24px;padding-top:12px;border-top:1px solid #30363d;color:#555;font-size:11px;text-align:center;">
  ⚠️ 纯技术规则评分，仅供参考，不构成投资建议 · NASDX · {NOW.strftime('%Y-%m-%d %H:%M:%S')}
</div>
</body></html>"""

out=ROOT/'reports'/f'stocks60_{TODAY}_{HHMM}.html'
latest=ROOT/'reports'/'stocks50_latest.html'
out.parent.mkdir(exist_ok=True)
with open(out,'w',encoding='utf-8') as f: f.write(html)
with open(latest,'w',encoding='utf-8') as f: f.write(html)

jout=ROOT/'reports'/f'stocks60_{TODAY}_{HHMM}.json'
with open(jout,'w',encoding='utf-8') as f:
    json.dump({'datetime':NOW.isoformat(),'total':len(valid),
               'bullish':len(bull),'neutral':len(neut),'bearish':len(bear),
               'top3':[{'code':r['code'],'name':r['name'],'score':r['score'],'signal':r['signal'],'chg':r['chg']} for r in top3],
               'results':[{k:v for k,v in r.items() if k!='reasons'} for r in valid]
              },f,ensure_ascii=False,indent=2)

print(f'\n📁 HTML: {out}')
print(f'📁 最新: {latest}')
import subprocess
subprocess.Popen(['cmd','/c','start',str(out)])
