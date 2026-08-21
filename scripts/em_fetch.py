"""
东方财富数据直连抓取 — 绕过 push2his，使用 datacenter/quote 接口
通过代理隧道访问，支持 A股 K 线 + 实时行情
"""
# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import socket, ssl, json, time, gzip
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd


PROXY_HOST = '127.0.0.1'
PROXY_PORT = 7890


def _raw_get(host: str, path: str, timeout: int = 10) -> bytes:
    """通过代理隧道发起 HTTPS GET，返回 body bytes"""
    sock = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=timeout)
    sock.send(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        buf += sock.recv(4096)

    ctx = ssl.create_default_context()
    ssock = ctx.wrap_socket(sock, server_hostname=host)

    req = (
        f'GET {path} HTTP/1.1\r\n'
        f'Host: {host}\r\n'
        f'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n'
        f'Accept: application/json, */*\r\n'
        f'Accept-Encoding: gzip, deflate\r\n'
        f'Connection: close\r\n\r\n'
    )
    ssock.send(req.encode())

    data = b''
    while True:
        try:
            chunk = ssock.recv(32768)
            if not chunk:
                break
            data += chunk
        except:
            break
    ssock.close()

    if not data or b'\r\n\r\n' not in data:
        return b''

    header_part, body = data.split(b'\r\n\r\n', 1)

    # 处理 chunked 编码
    if b'Transfer-Encoding: chunked' in header_part:
        decoded = b''
        while body:
            try:
                crlf = body.index(b'\r\n')
                size = int(body[:crlf], 16)
                if size == 0:
                    break
                decoded += body[crlf+2:crlf+2+size]
                body = body[crlf+2+size+2:]
            except:
                decoded += body
                break
        body = decoded

    # 处理 gzip
    if b'Content-Encoding: gzip' in header_part:
        try:
            body = gzip.decompress(body)
        except:
            pass

    return body


def fetch_kline(code: str, days: int = 90) -> Optional[pd.DataFrame]:
    """
    抓取个股日K线数据
    code: 6位股票代码
    返回 DataFrame，列：日期/开盘/收盘/最高/最低/成交量/涨跌幅/换手率
    """
    today = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # secid: 1=沪 0=深 判断
    if code.startswith('6'):
        secid = f'1.{code}'
    elif code.startswith('5') or code.startswith('51') or code.startswith('58') or code.startswith('56'):
        secid = f'1.{code}'  # 上海ETF
    else:
        secid = f'0.{code}'

    path = (
        f'/api/qt/stock/kline/get'
        f'?secid={secid}'
        f'&fields1=f1,f2,f3,f4,f5,f6,f7'
        f'&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
        f'&klt=101&fqt=1&beg={start}&end={today}'
        f'&ut=bd1d9ddb04089700cf9c27f6f7426281'
    )

    for host in ['push2delay.eastmoney.com', 'push2his.eastmoney.com', 'push2.eastmoney.com']:
        try:
            body = _raw_get(host, path, timeout=10)
            if not body:
                continue
            data = json.loads(body)
            klines = data.get('data', {}).get('klines', [])
            if not klines:
                continue

            rows = []
            for k in klines:
                parts = k.split(',')
                if len(parts) >= 11:
                    rows.append({
                        '日期':   parts[0],
                        '开盘':   float(parts[1]),
                        '收盘':   float(parts[2]),
                        '最高':   float(parts[3]),
                        '最低':   float(parts[4]),
                        '成交量': float(parts[5]),
                        '成交额': float(parts[6]),
                        '振幅':   float(parts[7]),
                        '涨跌幅': float(parts[8]),
                        '涨跌额': float(parts[9]),
                        '换手率': float(parts[10]),
                    })
            if rows:
                return pd.DataFrame(rows)
        except Exception as e:
            continue

    return None


def fetch_realtime_batch(codes: list) -> dict:
    """
    批量获取实时行情
    返回 {code: {price, chg_pct, vol, name, ...}}
    """
    results = {}

    # 按市场分组
    sh_codes = [c for c in codes if c.startswith('6') or (c.startswith('5') and not c.startswith('50'))]
    sz_codes = [c for c in codes if c not in sh_codes]

    def fetch_group(mkt_codes, market_prefix):
        if not mkt_codes:
            return
        secids = ','.join(f'{market_prefix}.{c}' for c in mkt_codes)
        path = (
            f'/api/qt/ulist/get'
            f'?fltt=2&invt=2'
            f'&fields=f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18'
            f'&secids={secids}'
            f'&ut=bd1d9ddb04089700cf9c27f6f7426281'
        )
        try:
            body = _raw_get('push2.eastmoney.com', path, timeout=8)
            if not body:
                return
            data = json.loads(body)
            diff = data.get('data', {}).get('diff', {})
            for key, val in diff.items():
                code = val.get('f12', '')
                if code:
                    results[code] = {
                        'price': val.get('f2', 0),
                        'chg_pct': val.get('f3', 0),
                        'chg_amt': val.get('f4', 0),
                        'vol': val.get('f5', 0),
                        'amount': val.get('f6', 0),
                        'name': val.get('f14', ''),
                        'high': val.get('f15', 0),
                        'low': val.get('f16', 0),
                        'open': val.get('f17', 0),
                        'prev_close': val.get('f18', 0),
                    }
        except:
            pass

    fetch_group([c for c in codes if c.startswith('6') or c.startswith('51') or c.startswith('58') or c.startswith('56')], '1')
    fetch_group([c for c in codes if not (c.startswith('6') or c.startswith('51') or c.startswith('58') or c.startswith('56'))], '0')

    return results


def calc_indicators(df: pd.DataFrame) -> dict:
    """从 K 线 DataFrame 计算技术指标"""
    if df is None or len(df) < 5:
        return {}
    close = df['收盘'].astype(float)
    vol   = df['成交量'].astype(float)
    n = len(close)

    ma5  = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1] if n >= 10 else None
    ma20 = close.rolling(20).mean().iloc[-1] if n >= 20 else None
    ma60 = close.rolling(60).mean().iloc[-1] if n >= 60 else None

    e12  = close.ewm(span=12, adjust=False).mean()
    e26  = close.ewm(span=26, adjust=False).mean()
    dif  = e12 - e26
    dea  = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = (100 - 100 / (1 + gain / (loss + 1e-9))).iloc[-1] if n >= 14 else 50

    mid  = close.rolling(20).mean()
    std_ = close.rolling(20).std()
    bu   = (mid + 2 * std_).iloc[-1] if n >= 20 else None
    bl   = (mid - 2 * std_).iloc[-1] if n >= 20 else None

    vr   = (vol.iloc[-1] / vol.rolling(5).mean().iloc[-2]) if n > 5 else 1
    up20 = int((df['涨跌幅'].astype(float).tail(20) > 0).sum()) if n >= 20 else None
    turnover = df['换手率'].astype(float).iloc[-1] if '换手率' in df.columns else None

    cur  = close.iloc[-1]
    prev = close.iloc[-2] if n > 1 else cur
    chg  = (cur - prev) / prev * 100

    return {
        'close':  round(float(cur), 3),
        'chg':    round(float(chg), 2),
        'turnover': round(float(turnover), 2) if turnover is not None else None,
        'ma5':    round(float(ma5), 3),
        'ma10':   round(float(ma10), 3) if ma10 is not None else None,
        'ma20':   round(float(ma20), 3) if ma20 is not None else None,
        'ma60':   round(float(ma60), 3) if ma60 is not None else None,
        'macd_bar': round(float(macd.iloc[-1]), 4),
        'dif':    round(float(dif.iloc[-1]), 4),
        'dea':    round(float(dea.iloc[-1]), 4),
        'rsi':    round(float(rsi), 1),
        'boll_upper': round(float(bu), 3) if bu is not None else None,
        'boll_lower': round(float(bl), 3) if bl is not None else None,
        'vol_ratio': round(float(vr), 2),
        'up_days_20': up20,
        'kline_days': n,
    }


if __name__ == '__main__':
    # 快速自测
    print('测试 600519 贵州茅台...')
    df = fetch_kline('600519')
    if df is not None:
        print(f'✅ K线 {len(df)} 行')
        print(df.tail(2)[['日期','收盘','涨跌幅','换手率']])
        ind = calc_indicators(df)
        print(f'收盘={ind["close"]} RSI={ind["rsi"]} MACD={ind["macd_bar"]}')
    else:
        print('❌ 失败')
