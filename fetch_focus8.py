"""抓取8只重点ETF数据 + 测试API"""
import os
# 绕过系统代理，让 akshare/requests 直连国内数据源
for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(_k, None)

import sys, json, openai
sys.path.insert(0, '.')
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

TODAY = datetime.now().strftime('%Y%m%d')
START = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')

# 测试API
print('=== 测试API ===')
c = openai.OpenAI(api_key='sk-auVdJleHad3fqilXphK8U3c6QvdKWPWIUcWz2Ygc2MWeYaUT',
                  base_url='https://newapi.ecdigit.cn/v1', timeout=12)
api_ok = False
for model in ['claude-opus-4-6-thinking','claude-sonnet-4-6','claude-haiku-4-5-20251001']:
    try:
        r = c.chat.completions.create(model=model,
            messages=[{'role':'user','content':'hi'}], max_tokens=5)
        print(f'OK {model}: {r.choices[0].message.content}')
        api_ok = True; break
    except Exception as e:
        print(f'FAIL {model}: {str(e)[:70]}')

# 抓数据
print('\n=== 抓取数据 ===')
targets = [
    ('159687','亚太精选ETF南方'),('161128','标普信息科技LOF'),
    ('160644','港美互联网LOF'),('501312','海外科技LOF'),
    ('513110','纳指ETF华泰柏瑞'),('513300','纳斯达克ETF华夏'),
    ('513310','中韩半导体ETF'),('501225','全球芯片LOF'),
]

def get_indicators(code):
    for fn, kw in [
        (ak.fund_etf_hist_em,{'symbol':code,'period':'daily','start_date':START,'end_date':TODAY,'adjust':''}),
        (ak.stock_zh_a_hist, {'symbol':code,'period':'daily','start_date':START,'end_date':TODAY,'adjust':'qfq'}),
    ]:
        try:
            df = fn(**kw)
            if not isinstance(df, pd.DataFrame) or len(df) < 5: continue
            close = df['收盘'].astype(float); vol = df['成交量'].astype(float)
            ma5  = close.rolling(5).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1] if len(close)>=20 else None
            ma60 = close.rolling(60).mean().iloc[-1] if len(close)>=60 else None
            ema12 = close.ewm(span=12,adjust=False).mean()
            ema26 = close.ewm(span=26,adjust=False).mean()
            dif = ema12-ema26; dea = dif.ewm(span=9,adjust=False).mean()
            macd = (dif-dea)*2
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi = (100-100/(1+gain/(loss+1e-9))).iloc[-1] if len(close)>=14 else 50
            mid = close.rolling(20).mean(); std_ = close.rolling(20).std()
            bu = (mid+2*std_).iloc[-1] if len(close)>=20 else None
            bl = (mid-2*std_).iloc[-1] if len(close)>=20 else None
            vr = (vol.iloc[-1]/vol.rolling(5).mean().iloc[-2]) if len(vol)>5 else 1
            up20 = int((df['涨跌幅'].astype(float).tail(20)>0).sum()) if len(df)>=20 else 10
            cur = close.iloc[-1]; prev = close.iloc[-2] if len(close)>1 else cur
            chg = (cur-prev)/prev*100
            cols = [c for c in ['日期','开盘','收盘','最高','最低','成交量','涨跌幅'] if c in df.columns]
            return {
                'close':round(float(cur),3),'change_pct':round(float(chg),2),
                'ma5':round(float(ma5),3),
                'ma20':round(float(ma20),3) if ma20 is not None else None,
                'ma60':round(float(ma60),3) if ma60 is not None else None,
                'macd_bar':round(float(macd.iloc[-1]),4),
                'dif':round(float(dif.iloc[-1]),4),'dea':round(float(dea.iloc[-1]),4),
                'rsi':round(float(rsi),2),
                'boll_upper':round(float(bu),3) if bu is not None else None,
                'boll_lower':round(float(bl),3) if bl is not None else None,
                'vol_ratio':round(float(vr),2),'up_days_20':up20,
                'kline_days':len(df),
                'kline_last5':df[cols].tail(5).to_dict('records'),
            }
        except: pass
    return None

results = {}
for code, name in targets:
    ind = get_indicators(code)
    if ind:
        print(f"OK {code} {name}: {ind['close']} {ind['change_pct']:+.2f}% RSI={ind['rsi']:.1f}")
        results[code] = {'name':name, **ind}
    else:
        print(f"NO {code} {name}")
        results[code] = None

with open('focus8_data.json','w',encoding='utf-8') as f:
    json.dump({'api_ok':api_ok,'date':TODAY,'results':results},f,ensure_ascii=False,indent=2,default=str)
print(f'\napi_ok={api_ok}')
print('数据已保存到 focus8_data.json')
