"""
持仓 ETF 快速抓取 — 不走代理，直连东方财富
用法: python grab_holding.py
"""
import os
for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(_k, None)

# Clash 通过 Windows 注册表注入代理，清环境变量不够，直接 patch requests.get 强制直连
import requests as _requests
_orig_get = _requests.get
def _direct_get(url, **kwargs):
    kwargs['proxies'] = {'http': None, 'https': None}
    return _orig_get(url, **kwargs)
_requests.get = _direct_get

import akshare as ak
import pandas as pd
import json

TARGETS = {
    '159611': '电力ETF(广发)',
    '159687': '亚太精选ETF(南方)',
    '159941': '纳指ETF(广发)',
}

result = {}
for code, name in TARGETS.items():
    try:
        df = ak.fund_etf_hist_em(symbol=code, period='daily',
                                  start_date='20260501', end_date='20260603', adjust='qfq')
        df.columns = [c.strip() for c in df.columns]
        closes = df['收盘'].tolist()

        def ma(n):
            return round(sum(closes[-n:]) / n, 4) if len(closes) >= n else None

        delta = df['收盘'].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = round((100 - 100 / (1 + gain / loss)).iloc[-1], 1)

        ema12 = df['收盘'].ewm(span=12).mean()
        ema26 = df['收盘'].ewm(span=26).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9).mean()
        macd_bar = round((dif.iloc[-1] - dea.iloc[-1]) * 2, 4)

        bm = df['收盘'].rolling(20).mean()
        bs = df['收盘'].rolling(20).std()
        up20 = int((df['涨跌幅'].tail(20) > 0).sum())

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        result[code] = {
            'name': name,
            'close': round(float(latest['收盘']), 3),
            'change_pct': round(float(latest['涨跌幅']), 2),
            'volume': int(latest['成交量']),
            'amount_亿': round(float(latest['成交额']) / 1e8, 2),
            'prev_close': round(float(prev['收盘']), 3),
            'ma5': ma(5), 'ma10': ma(10), 'ma20': ma(20), 'ma60': ma(60),
            'rsi': rsi,
            'dif': round(float(dif.iloc[-1]), 4),
            'dea': round(float(dea.iloc[-1]), 4),
            'macd_bar': macd_bar,
            'boll_upper': round(float(bm.iloc[-1] + 2 * bs.iloc[-1]), 4),
            'boll_mid': round(float(bm.iloc[-1]), 4),
            'boll_lower': round(float(bm.iloc[-1] - 2 * bs.iloc[-1]), 4),
            'up_days_20': up20,
        }
        print(f'✅ {code} {name}: {result[code]["close"]} ({result[code]["change_pct"]:+.2f}%)')
    except Exception as e:
        print(f'❌ {code} {name}: {e}')
        result[code] = {'name': name, 'error': str(e)}

print()
print(json.dumps(result, ensure_ascii=False, indent=2))
