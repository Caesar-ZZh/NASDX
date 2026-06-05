"""
8只重点ETF深度分析 — 使用天天基金净值接口 + 规则评分 + 生成HTML报告
"""
import sys, json, time
from pathlib import Path
from datetime import datetime

import akshare as ak
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TODAY = datetime.now().strftime('%Y%m%d')

TARGETS = [
    ('159687','亚太精选ETF南方',   '海外·亚太',  '跟踪MSCI亚太精选高股息指数，重仓澳大利亚/韩国/香港高息股'),
    ('161128','标普信息科技LOF',   '海外·美股科技','跟踪标普500信息科技行业，重仓苹果/微软/英伟达'),
    ('160644','港美互联网LOF',     '海外·中概互联','重仓腾讯/阿里/美团，港股互联网龙头'),
    ('501312','海外科技LOF',       '海外·全球科技','华宝海外科技，重仓苹果/微软/谷歌/亚马逊'),
    ('513110','纳指ETF华泰柏瑞',   '海外·纳指',  '跟踪纳斯达克100指数，T+0交易'),
    ('513300','纳斯达克ETF华夏',   '海外·纳指',  '跟踪纳斯达克100指数，规模最大'),
    ('513310','中韩半导体ETF',     '半导体·跨境', '跨境中韩半导体，含三星/SK海力士/台积电'),
    ('501225','全球芯片LOF',       '半导体·跨境', '华夏全球半导体芯片LOF，含台积电/英伟达'),
]

# ── 抓数据 ──────────────────────────────────────────
def fetch(code):
    try:
        df = ak.fund_etf_fund_info_em(fund=code, start_date='20250901', end_date=TODAY)
        if isinstance(df, pd.DataFrame) and len(df) >= 5:
            # 列：净值日期, 单位净值, 累计净值, 日增长率
            df = df.sort_values('净值日期').reset_index(drop=True)
            close  = df['单位净值'].astype(float)
            chg_pct = df['日增长率'].astype(float)
            return df, close, chg_pct
    except:
        pass
    return None, None, None

def calc_indicators(close, chg_pct):
    n = len(close)
    if n < 5:
        return {}
    ma5  = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1] if n>=10 else None
    ma20 = close.rolling(20).mean().iloc[-1] if n>=20 else None
    ma60 = close.rolling(60).mean().iloc[-1] if n>=60 else None
    e12  = close.ewm(span=12, adjust=False).mean()
    e26  = close.ewm(span=26, adjust=False).mean()
    dif  = e12 - e26
    dea  = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2
    d    = close.diff()
    gain = d.clip(lower=0).rolling(14).mean()
    loss = (-d.clip(upper=0)).rolling(14).mean()
    rsi  = (100 - 100/(1+gain/(loss+1e-9))).iloc[-1] if n>=14 else 50
    mid  = close.rolling(20).mean()
    std_ = close.rolling(20).std()
    bu   = (mid+2*std_).iloc[-1] if n>=20 else None
    bl   = (mid-2*std_).iloc[-1] if n>=20 else None
    up20 = int((chg_pct.tail(20) > 0).sum()) if n>=20 else None
    cur  = close.iloc[-1]
    prev = close.iloc[-2]
    chg  = (cur-prev)/prev*100
    return {
        'close':round(float(cur),3), 'change_pct':round(float(chg),2),
        'ma5':round(float(ma5),3),
        'ma10':round(float(ma10),3) if ma10 is not None else None,
        'ma20':round(float(ma20),3) if ma20 is not None else None,
        'ma60':round(float(ma60),3) if ma60 is not None else None,
        'dif':round(float(dif.iloc[-1]),4), 'dea':round(float(dea.iloc[-1]),4),
        'macd_bar':round(float(macd.iloc[-1]),4),
        'rsi':round(float(rsi),2),
        'boll_upper':round(float(bu),3) if bu is not None else None,
        'boll_lower':round(float(bl),3) if bl is not None else None,
        'up_days_20':up20,
    }

# ── 技术评分 ─────────────────────────────────────────
def score_etf(ind):
    if not ind: return 0, 'neutral', []
    pts = 0; reasons = []
    ma5=ind.get('ma5'); ma20=ind.get('ma20'); ma60=ind.get('ma60')
    close=ind['close']; rsi=ind.get('rsi',50) or 50
    macd=ind.get('macd_bar',0) or 0
    up20=ind.get('up_days_20',10) or 10
    bu=ind.get('boll_upper'); bl=ind.get('boll_lower')

    # 均线系统 40分
    if ma5 and ma20:
        if ma5>ma20:
            pts+=25; reasons.append(f'MA5({ma5:.3f})>MA20({ma20:.3f}) 多头排列')
        else:
            pts+=5;  reasons.append(f'MA5({ma5:.3f})<MA20({ma20:.3f}) 空头排列')
    if ma20 and ma60:
        if ma20>ma60:
            pts+=15; reasons.append(f'MA20>MA60 中长期趋势向上')
        else:
            reasons.append(f'MA20<MA60 中长期趋势向下')

    # MACD 25分
    if macd>0:
        pts+=25; reasons.append(f'MACD金叉 {macd:+.4f} 动能向上')
    elif macd>-0.005:
        pts+=12; reasons.append(f'MACD接近零轴 {macd:.4f} 蓄力')
    else:
        pts+=3;  reasons.append(f'MACD死叉 {macd:.4f} 下行动能')

    # RSI 20分
    if 45<=rsi<=65: pts+=20; reasons.append(f'RSI={rsi:.0f} 健康区间')
    elif 35<=rsi<45: pts+=13; reasons.append(f'RSI={rsi:.0f} 偏弱但未超卖')
    elif rsi<35:    pts+=8;  reasons.append(f'RSI={rsi:.0f} 超卖 关注反弹')
    elif 65<rsi<=75:pts+=14; reasons.append(f'RSI={rsi:.0f} 偏强注意回调')
    else:           pts+=5;  reasons.append(f'RSI={rsi:.0f} 超买风险')

    # 布林带位置 10分
    if bu and bl:
        bw = bu-bl
        pos = (close-bl)/bw if bw>0 else 0.5
        if 0.3<=pos<=0.65: pts+=10; reasons.append(f'布林带中轨区间({pos:.0%})')
        elif pos<0.3:      pts+=7;  reasons.append(f'布林带下轨附近({pos:.0%}) 支撑位')
        else:              pts+=4;  reasons.append(f'布林带上轨附近({pos:.0%}) 压力位')

    # 上涨天数 5分
    if up20 is not None:
        if up20>=12:   pts+=5; reasons.append(f'20日上涨{up20}天 强势')
        elif up20>=8:  pts+=3; reasons.append(f'20日上涨{up20}天 均衡')
        else:               reasons.append(f'20日仅涨{up20}天 偏弱')

    sig = 'bullish' if pts>=65 else 'bearish' if pts<=40 else 'neutral'
    return pts, sig, reasons

# ── 主流程 ──────────────────────────────────────────
print(f'\n{"="*60}')
print(f'  NASDX 8只重点ETF深度分析  {TODAY}')
print(f'{"="*60}\n')

all_results = []
for code, name, category, desc in TARGETS:
    print(f'  {code} {name}...', end=' ', flush=True)
    df, close, chg_pct = fetch(code)
    if df is None:
        print('❌ 无数据')
        all_results.append({'code':code,'name':name,'category':category,'desc':desc,
                            'ind':{},'score':0,'signal':'no_data','reasons':[],'kline':[]})
        continue
    ind = calc_indicators(close, chg_pct)
    sc, sig, rsns = score_etf(ind)
    emoji = {'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(sig,'')
    print(f'{emoji} {sc}分 {sig}  净值{ind["close"]:.3f}  RSI={ind["rsi"]:.0f}  MACD={ind["macd_bar"]:+.4f}')
    kline = df[['净值日期','单位净值','日增长率']].tail(10).to_dict('records')
    all_results.append({'code':code,'name':name,'category':category,'desc':desc,
                        'ind':ind,'score':sc,'signal':sig,'reasons':rsns,'kline':kline})
    time.sleep(0.2)

# ── 排行 ─────────────────────────────────────────────
valid = [r for r in all_results if r['signal']!='no_data']
valid.sort(key=lambda r: -r['score'])
print(f'\n{"="*60}')
print('  📊 评分排行')
print(f'{"="*60}')
for i,r in enumerate(valid,1):
    emoji = {'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(r['signal'],'')
    ind = r['ind']
    chg_s = f"{ind.get('change_pct',0):+.2f}%" if ind else '-'
    print(f'  {i}. {r["code"]} {r["name"]:<20} {r["score"]}分 {emoji}{r["signal"]}  净值{ind.get("close","N/A")}  {chg_s}')

# ── 生成HTML ─────────────────────────────────────────
def sig_color(s):
    return {'bullish':'#00C853','bearish':'#FF1744','neutral':'#FFD600'}.get(s,'#888')

def bar(v, mx=100):
    c = sig_color('bullish') if v>=65 else sig_color('bearish') if v<=40 else sig_color('neutral')
    return f'<div style="background:#21262d;border-radius:3px;height:6px;"><div style="width:{v/mx*100:.0f}%;height:100%;background:{c};border-radius:3px;"></div></div>'

def kline_rows(kline):
    rows = ''
    for k in reversed(kline[-7:]):
        date = k.get('净值日期','')
        nav  = k.get('单位净值','-')
        chg  = float(k.get('日增长率',0) or 0)
        cc   = '#00C853' if chg>0 else '#FF1744' if chg<0 else '#8b949e'
        rows += f'<tr><td style="color:#8b949e">{date}</td><td style="text-align:right">{nav}</td><td style="text-align:right;color:{cc}">{chg:+.2f}%</td></tr>'
    return rows

cards_html = ''
for r in valid:
    ind = r['ind']
    sc  = r['score']; sig = r['signal']
    sc_color = sig_color(sig)
    emoji = {'bullish':'📈','bearish':'📉','neutral':'➡️'}.get(sig,'')
    sig_label = {'bullish':'看多','bearish':'看空','neutral':'中性'}.get(sig,sig)
    chg = ind.get('change_pct',0)
    chg_color = '#00C853' if chg>0 else '#FF1744' if chg<0 else '#8b949e'
    rsi = ind.get('rsi',50) or 50
    rsi_bar_w = min(100, rsi)
    rsi_bar_c = '#FF1744' if rsi>70 else '#00C853' if rsi<30 else '#58a6ff'

    # MACD
    macd = ind.get('macd_bar',0) or 0
    dif  = ind.get('dif',0) or 0
    dea  = ind.get('dea',0) or 0

    # 均线
    ma5=ind.get('ma5'); ma20=ind.get('ma20'); ma60=ind.get('ma60')
    close=ind.get('close',0)
    ma_rows = ''
    for label,val in [('MA5',ma5),('MA20',ma20),('MA60',ma60)]:
        if val is None: continue
        diff_pct = (close-val)/val*100
        dc = '#00C853' if diff_pct>0 else '#FF1744'
        ma_rows += f'<tr><td style="color:#8b949e">{label}</td><td style="text-align:right">{val:.3f}</td><td style="text-align:right;color:{dc}">{diff_pct:+.1f}%</td></tr>'

    reasons_html = ''.join(f'<div style="background:#21262d;border-radius:4px;padding:4px 8px;margin:3px 0;font-size:12px;color:#c9d1d9;">· {r2}</div>' for r2 in r['reasons'])
    boll_upper = ind.get('boll_upper')
    boll_lower = ind.get('boll_lower')
    boll_html = ''
    if boll_upper and boll_lower:
        pos = (close-boll_lower)/(boll_upper-boll_lower)*100
        boll_html = f'''
        <div style="font-size:11px;color:#8b949e;margin:6px 0 2px 0;">布林带位置 {pos:.0f}%</div>
        <div style="background:#21262d;border-radius:3px;height:6px;position:relative;">
          <div style="width:{min(100,pos):.0f}%;height:100%;background:#58a6ff;border-radius:3px;"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:#8b949e;margin-top:2px;">
          <span>下轨 {boll_lower:.3f}</span><span>上轨 {boll_upper:.3f}</span>
        </div>'''

    cards_html += f'''
<div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px;margin-bottom:20px;position:relative;overflow:hidden;">
  <div style="position:absolute;top:0;left:0;right:0;height:3px;background:{sc_color};"></div>

  <!-- 头部 -->
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;flex-wrap:wrap;gap:8px;">
    <div>
      <div style="font-size:18px;font-weight:bold;color:#fff;">{r["code"]} &nbsp; {r["name"]}</div>
      <div style="font-size:12px;color:#58a6ff;margin-top:2px;">{r["category"]}</div>
      <div style="font-size:12px;color:#8b949e;margin-top:2px;">{r["desc"]}</div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:22px;font-weight:bold;color:{sc_color};border:2px solid {sc_color};border-radius:8px;padding:6px 14px;background:{sc_color}15;">{emoji} {sig_label}</div>
      <div style="font-size:13px;color:#8b949e;margin-top:4px;">技术评分 {sc}/100</div>
    </div>
  </div>

  <!-- 评分条 -->
  <div style="margin-bottom:16px;">{bar(sc)}</div>

  <!-- 核心数据 3列 -->
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px;">

    <!-- 净值 -->
    <div style="background:#0d1117;border-radius:8px;padding:12px;">
      <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">最新净值</div>
      <div style="font-size:20px;font-weight:bold;color:#fff;">{ind.get("close","N/A")}</div>
      <div style="font-size:13px;color:{chg_color};margin-top:2px;">{chg:+.2f}%</div>
    </div>

    <!-- RSI -->
    <div style="background:#0d1117;border-radius:8px;padding:12px;">
      <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">RSI(14)</div>
      <div style="font-size:20px;font-weight:bold;color:{rsi_bar_c};">{rsi:.1f}</div>
      <div style="background:#21262d;border-radius:3px;height:4px;margin-top:6px;">
        <div style="width:{rsi_bar_w:.0f}%;height:100%;background:{rsi_bar_c};border-radius:3px;"></div>
      </div>
      <div style="font-size:10px;color:#8b949e;margin-top:2px;">{"超买" if rsi>70 else "超卖" if rsi<30 else "正常"}</div>
    </div>

    <!-- MACD -->
    <div style="background:#0d1117;border-radius:8px;padding:12px;">
      <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">MACD</div>
      <div style="font-size:16px;font-weight:bold;color:{"#00C853" if macd>0 else "#FF1744"};">{macd:+.4f}</div>
      <div style="font-size:11px;color:#8b949e;margin-top:4px;">DIF {dif:+.4f}</div>
      <div style="font-size:11px;color:#8b949e;">DEA {dea:+.4f}</div>
    </div>
  </div>

  <!-- 均线 + 布林带 | 关键信号 -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">

    <div style="background:#0d1117;border-radius:8px;padding:12px;">
      <div style="font-size:11px;color:#8b949e;margin-bottom:8px;">均线系统</div>
      <table style="width:100%;font-size:12px;">{ma_rows}</table>
      {boll_html}
    </div>

    <div style="background:#0d1117;border-radius:8px;padding:12px;">
      <div style="font-size:11px;color:#8b949e;margin-bottom:6px;">关键信号</div>
      {reasons_html}
    </div>
  </div>

  <!-- K线（近7日净值） -->
  <div style="background:#0d1117;border-radius:8px;padding:12px;">
    <div style="font-size:11px;color:#8b949e;margin-bottom:8px;">近期净值走势（近7日）</div>
    <table style="width:100%;font-size:12px;border-collapse:collapse;">
      <tr style="color:#8b949e;font-size:10px;"><th style="text-align:left;padding:3px 6px;">日期</th><th style="text-align:right;padding:3px 6px;">净值</th><th style="text-align:right;padding:3px 6px;">日涨跌</th></tr>
      {kline_rows(r["kline"])}
    </table>
  </div>
</div>'''

# 无数据卡片
for r in all_results:
    if r['signal']=='no_data':
        cards_html += f'''
<div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px;margin-bottom:20px;opacity:0.5;">
  <div style="font-size:16px;font-weight:bold;color:#8b949e;">{r["code"]} {r["name"]}</div>
  <div style="color:#FF1744;margin-top:8px;">❌ 暂无数据（停牌或接口暂时不可用）</div>
</div>'''

bull_list = [r for r in valid if r['signal']=='bullish']
neut_list = [r for r in valid if r['signal']=='neutral']
bear_list = [r for r in valid if r['signal']=='bearish']

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>NASDX 8只重点ETF深度分析 {TODAY}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",monospace;padding:24px;max-width:900px;margin:0 auto;}}
table td,table th{{padding:4px 8px;}}
</style>
</head>
<body>
<div style="border-bottom:1px solid #30363d;padding-bottom:20px;margin-bottom:24px;">
  <h1 style="color:#fff;font-size:24px;">📊 NASDX 重点ETF深度分析</h1>
  <div style="color:#8b949e;font-size:13px;margin-top:6px;">分析日期：{TODAY} · 数据来源：天天基金净值 · 技术规则评分</div>
  <div style="display:flex;gap:16px;margin-top:16px;flex-wrap:wrap;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 18px;text-align:center;">
      <div style="font-size:20px;font-weight:bold;color:#00C853;">{len(bull_list)}</div>
      <div style="font-size:11px;color:#8b949e;">📈 看多</div>
    </div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 18px;text-align:center;">
      <div style="font-size:20px;font-weight:bold;color:#FFD600;">{len(neut_list)}</div>
      <div style="font-size:11px;color:#8b949e;">➡️ 中性</div>
    </div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 18px;text-align:center;">
      <div style="font-size:20px;font-weight:bold;color:#FF1744;">{len(bear_list)}</div>
      <div style="font-size:11px;color:#8b949e;">📉 看空</div>
    </div>
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 18px;text-align:center;">
      <div style="font-size:20px;font-weight:bold;color:#58a6ff;">{valid[0]["score"] if valid else 0}</div>
      <div style="font-size:11px;color:#8b949e;">🏆 最高分 {valid[0]["name"] if valid else ""}</div>
    </div>
  </div>
</div>

{cards_html}

<div style="margin-top:32px;padding-top:16px;border-top:1px solid #30363d;color:#8b949e;font-size:11px;text-align:center;">
  ⚠️ 纯技术规则评分，净值为上一交易日数据，不构成投资建议 · NASDX · {datetime.now().strftime('%Y-%m-%d %H:%M')}
</div>
</body></html>"""

out = ROOT / 'reports' / f'focus8_{TODAY}.html'
out.parent.mkdir(exist_ok=True)
with open(out,'w',encoding='utf-8') as f:
    f.write(html)
print(f'\n📁 报告: {out}')

import subprocess
# [已移除自动弹窗] subprocess.Popen(['cmd','/c','start',str(out)])