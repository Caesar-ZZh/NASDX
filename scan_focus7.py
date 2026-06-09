"""
扫描7只重点 ETF/LOF，输出技术评分 + 实时溢价 + 配置建议
"""
import requests as _req
_real = _req.get
def _pg(url,**kw):
    if 'eastmoney' in url:
        s=_req.Session(); s.trust_env=True; return s.get(url,**kw)
    return _real(url,**kw)
_req.get = _pg

import sys, time, json
from datetime import datetime, timedelta
sys.path.insert(0,'.')

import akshare as ak
import pandas as pd, numpy as np

TARGETS = [
    ('161128','标普信息科技LOF','海外·美股科技'),
    ('159687','亚太精选ETF南方','海外·亚太'),
    ('501312','海外科技LOF','海外·全球科技'),
    ('160644','港美互联网LOF','海外·中概互联'),
    ('159941','纳指ETF广发','海外·纳指'),
    ('513310','中韩半导体ETF','半导体·跨境'),
    ('515880','通信ETF国泰','通信'),
]

TODAY = datetime.now().strftime('%Y%m%d')
START = (datetime.now()-timedelta(days=120)).strftime('%Y%m%d')

# ── 实时行情 ─────────────────────────────────────────────
print('获取实时行情...')
spot_map = {}
try:
    spot = ak.fund_etf_spot_em()
    for _,r in spot.iterrows():
        try: spot_map[str(r['代码'])] = {'price':float(r['最新价']),'chg':float(r['涨跌幅'])}
        except: pass
except Exception as e:
    print(f'行情获取失败: {e}')

# ── 技术指标 + 评分 ───────────────────────────────────────
def calc(df):
    c=df['close'].astype(float); v=df['volume'].astype(float); n=len(c)
    if n<20: return {}
    ma5=c.rolling(5).mean().iloc[-1]; ma20=c.rolling(20).mean().iloc[-1]
    ma60=c.rolling(60).mean().iloc[-1] if n>=60 else None
    e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean()
    dif=e12-e26; dea=dif.ewm(span=9,adjust=False).mean(); macd=(dif-dea)*2
    d=c.diff(); g=d.clip(lower=0).rolling(14).mean(); l=(-d.clip(upper=0)).rolling(14).mean()
    rsi=(100-100/(1+g/(l+1e-9))).iloc[-1]
    mid=c.rolling(20).mean(); std=c.rolling(20).std()
    bu=(mid+2*std).iloc[-1]; bl=(mid-2*std).iloc[-1]
    boll_pct=(c.iloc[-1]-bl)/(bu-bl+1e-9)*100
    vr=v.iloc[-1]/(v.rolling(5).mean().iloc[-2]+1e-9)
    cur=c.iloc[-1]
    return {'close':cur,'ma5':ma5,'ma20':ma20,'ma60':ma60,
            'macd':macd.iloc[-1],'rsi':rsi,'boll_pct':boll_pct,'vr':vr}

def score(ind, spot_chg=None):
    if not ind: return 0,[]
    pts=0; rsns=[]
    ma5=ind['ma5']; ma20=ind['ma20']; ma60=ind.get('ma60')
    if ma5>ma20:
        pts+=22; rsns.append('MA多头')
    else:
        pts+=4; rsns.append('MA空头')
    if ma60 and ma20>ma60: pts+=12; rsns.append('中期多头')
    elif ma60: rsns.append('中期空头')
    macd=ind['macd']
    if macd>0.002: pts+=20; rsns.append(f'MACD金叉+{macd:.4f}')
    elif macd>-0.002: pts+=12; rsns.append('MACD蓄力')
    else: pts+=3; rsns.append(f'MACD死叉{macd:.4f}')
    rsi=ind['rsi']
    if 42<=rsi<=65: pts+=18; rsns.append(f'RSI={rsi:.0f}健康')
    elif rsi<30: pts+=14; rsns.append(f'RSI={rsi:.0f}超卖')
    elif rsi<42: pts+=10; rsns.append(f'RSI={rsi:.0f}偏弱')
    else: pts+=8; rsns.append(f'RSI={rsi:.0f}偏强')
    bp=ind['boll_pct']
    if bp<25: pts+=12; rsns.append(f'布林下轨{bp:.0f}%支撑')
    elif bp<55: pts+=8
    else: pts+=3; rsns.append(f'布林上轨{bp:.0f}%压力')
    vr=ind['vr']
    if 1.2<=vr<=3: pts+=8; rsns.append(f'量比{vr:.1f}放量')
    elif vr<0.7: rsns.append(f'量比{vr:.1f}缩量')
    return min(100,int(pts)), rsns[:3]

# ── 主循环 ────────────────────────────────────────────────
print(f'\n{"="*70}')
print(f'  7只 ETF/LOF 配置分析  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print(f'{"="*70}\n')

results = []
for code,name,cat in TARGETS:
    print(f'  分析 {code} {name}...', end=' ', flush=True)
    # 获取历史数据 — 依次尝试三个接口
    df = None
    for fn, kw, col_map in [
        (ak.fund_etf_hist_em,
         {'symbol':code,'period':'daily','start_date':START,'end_date':TODAY,'adjust':''},
         {'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume'}),
        (ak.fund_etf_fund_info_em,
         {'fund':code,'start_date':START,'end_date':TODAY},
         {'净值日期':'date','单位净值':'close','日增长率':'chg_pct'}),
        (ak.stock_zh_a_hist,
         {'symbol':code,'period':'daily','start_date':START,'end_date':TODAY,'adjust':'qfq'},
         {'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume'}),
    ]:
        try:
            tmp = fn(**kw)
            if isinstance(tmp,pd.DataFrame) and len(tmp)>=20:
                tmp = tmp.rename(columns=col_map)
                # fund_etf_fund_info_em 没有 volume，补0
                if 'volume' not in tmp.columns: tmp['volume'] = 1e6
                if 'open' not in tmp.columns: tmp['open'] = tmp['close']
                if 'high' not in tmp.columns: tmp['high'] = tmp['close']
                if 'low' not in tmp.columns: tmp['low'] = tmp['close']
                tmp['close'] = pd.to_numeric(tmp['close'], errors='coerce')
                tmp = tmp.dropna(subset=['close'])
                if len(tmp)>=20: df=tmp; break
        except Exception as e:
            continue

    if df is None or df.empty:
        print('无数据'); results.append({'code':code,'name':name,'cat':cat,'score':0,'signal':'no_data'}); continue

    ind = calc(df)
    spot = spot_map.get(code,{})
    spot_price = spot.get('price')
    spot_chg   = spot.get('chg')

    sc, rsns = score(ind)
    nav = ind.get('close',0)
    prem = (spot_price-nav)/nav*100 if spot_price and nav else None

    sig = 'bullish' if sc>=65 else 'bearish' if sc<=40 else 'neutral'
    emoji = {'bullish':'📈','bearish':'📉','neutral':'➡️'}[sig]

    chg_s = f'{spot_chg:+.2f}%' if spot_chg is not None else '-'
    prem_s = f'溢价{prem:+.2f}%' if prem is not None else '溢价N/A'
    print(f'{emoji} {sc}分 {sig}  今日{chg_s}  {prem_s}')

    results.append({'code':code,'name':name,'cat':cat,'score':sc,'signal':sig,
                    'nav':nav,'spot_price':spot_price,'spot_chg':spot_chg,
                    'premium':prem,'reasons':rsns,'ind':ind})
    time.sleep(0.5)

# ── 配置建议 ──────────────────────────────────────────────
results_valid = [r for r in results if r['signal']!='no_data']
results_sorted = sorted(results_valid, key=lambda x:-x['score'])

print(f'\n{"="*70}')
print(f'  配置评分排行')
print(f'{"="*70}')
print(f'  {"代码":<8}{"名称":<16}{"类别":<14}{"评分":>4}  {"信号":<8}  {"今日":>7}  {"溢价":>8}  关键因子')
print(f'  {"-"*70}')
for i,r in enumerate(results_sorted,1):
    medal={1:'🥇',2:'🥈',3:'🥉'}.get(i,'  ')
    chg_s = f'{r["spot_chg"]:+.2f}%' if r.get('spot_chg') is not None else '  -   '
    prem_s = f'{r["premium"]:+.2f}%' if r.get("premium") is not None else '  N/A  '
    prem_warn = '⚠️' if r.get("premium") and r["premium"]>1.5 else ('✅折价' if r.get("premium") and r["premium"]<-0.5 else '')
    rsns_s = ' · '.join(r.get('reasons',[]))
    print(f'  {medal}{i} {r["code"]:<8}{r["name"]:<16}{r["cat"]:<14}{r["score"]:>4}  {r["signal"]:<8}  {chg_s:>7}  {prem_s:>8} {prem_warn}  {rsns_s}')

# ── 资金配置建议（假设26000元）──────────────────────────────
CAPITAL = 26000
print(f'\n{"="*70}')
print(f'  投资配置建议（参考资金 {CAPITAL:,} 元）')
print(f'{"="*70}')

bull_ok = [r for r in results_sorted if r['signal']=='bullish' and (r.get('premium') is None or r['premium']<1.5)]
neut_ok = [r for r in results_sorted if r['signal']=='neutral']
high_prem = [r for r in results_valid if r.get('premium') and r['premium']>1.5]

if bull_ok:
    print(f'\n  【可配置标的】（看多 + 溢价合理）')
    total_alloc = 0
    allocs = []
    for r in bull_ok[:3]:
        sc = r['score']
        w = 0.35 if sc>=80 else 0.25 if sc>=70 else 0.15
        amt = int(CAPITAL * w / 100) * 100
        allocs.append((r,w,amt))
        total_alloc += amt

    for r,w,amt in allocs:
        prem_s = f'溢价{r["premium"]:+.2f}%' if r.get('premium') is not None else ''
        nav_s = f'净值{r["nav"]:.3f}' if r.get('nav') else ''
        print(f'  {r["code"]} {r["name"]:<14} → 配置 {w:.0%}  约 {amt:,} 元  {nav_s} {prem_s}')

    cash = CAPITAL - total_alloc
    print(f'  现金留存: {cash:,} 元 ({cash/CAPITAL:.0%})  等待二次确认或补仓')

if neut_ok and len(bull_ok)<3:
    print(f'\n  【观望标的】（中性信号，等待突破）')
    for r in neut_ok[:3]:
        prem_s = f'溢价{r["premium"]:+.2f}%' if r.get('premium') is not None else ''
        print(f'  {r["code"]} {r["name"]:<14}  评分{r["score"]}  {prem_s}  → 不急于建仓')

if high_prem:
    print(f'\n  【⚠️ 高溢价警告】（溢价>1.5%，不建议场内追买）')
    for r in high_prem:
        print(f'  {r["code"]} {r["name"]}  溢价{r["premium"]:+.2f}%  → 等溢价收窄再买，或走场外申购')

print(f'\n  免责声明：仅供参考，不构成投资建议。股市有风险，操作需谨慎。')
