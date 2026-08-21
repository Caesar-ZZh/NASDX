"""
Patch akshare 的东财接口，让 push2his 请求走 socket 直连而非 requests
在 import akshare 之后、使用之前调用 apply_patch() 即可
"""
# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import socket, ssl, json, gzip
from unittest.mock import MagicMock


def _socket_get(url: str, params: dict = None, timeout: int = 10, **kwargs):
    """替代 requests.get，走 socket 直接访问东财"""
    import urllib.parse

    # 解析 URL
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    path = parsed.path
    if params:
        qs = urllib.parse.urlencode(params)
        path = f'{path}?{qs}'

    # socket 连接
    try:
        sock = socket.create_connection((host, 443), timeout=timeout)
        ctx = ssl.create_default_context()
        ssock = ctx.wrap_socket(sock, server_hostname=host)

        req = (
            f'GET {path} HTTP/1.1\r\n'
            f'Host: {host}\r\n'
            f'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n'
            f'Accept: */*\r\n'
            f'Accept-Encoding: gzip, deflate\r\n'
            f'Connection: close\r\n\r\n'
        )
        ssock.sendall(req.encode())

        data = b''
        while True:
            try:
                chunk = ssock.recv(65536)
                if not chunk:
                    break
                data += chunk
            except:
                break
        ssock.close()

        if not data or b'\r\n\r\n' not in data:
            raise ConnectionError('Empty response from server')

        header_raw, body = data.split(b'\r\n\r\n', 1)
        headers_lower = header_raw.lower()

        # 处理 chunked
        if b'transfer-encoding: chunked' in headers_lower:
            dec = b''
            while body:
                try:
                    pos = body.index(b'\r\n')
                    size = int(body[:pos], 16)
                    if size == 0:
                        break
                    dec += body[pos+2:pos+2+size]
                    body = body[pos+2+size+2:]
                except:
                    dec += body
                    break
            body = dec

        # 处理 gzip
        if b'content-encoding: gzip' in headers_lower:
            try:
                body = gzip.decompress(body)
            except:
                pass

        # 包装成类 requests.Response 对象
        mock_resp = MagicMock()
        mock_resp.text = body.decode('utf-8', errors='replace')
        mock_resp.content = body
        mock_resp.status_code = 200
        mock_resp.json = lambda: json.loads(body)
        return mock_resp

    except Exception as e:
        raise ConnectionError(f'Socket request failed: {e}') from e


def apply_patch():
    """打补丁：把 akshare 里的 requests.get 替换为 socket 直连版本"""
    import akshare.stock_feature.stock_hist_em as em_mod
    import requests

    # 只 patch 这个模块里的 requests.get
    _orig_get = requests.get

    def patched_get(url, **kwargs):
        if 'push2his.eastmoney.com' in url or 'push2.eastmoney.com' in url:
            return _socket_get(url, **kwargs)
        return _orig_get(url, **kwargs)

    # 替换模块级别的 requests 引用
    import akshare.stock_feature.stock_hist_em as mod
    mod.requests.get = patched_get

    # 也 patch fund ETF 模块
    try:
        import akshare.fund.fund_em as fund_mod
        fund_mod.requests.get = patched_get
    except:
        pass

    try:
        import akshare.utils.request as req_mod
        req_mod.requests.get = patched_get
    except:
        pass

    print('✅ akshare patch 已应用 (push2his → socket直连)')


if __name__ == '__main__':
    apply_patch()
    import akshare as ak
    print('测试 600519 贵州茅台...')
    df = ak.stock_zh_a_hist(symbol='600519', period='daily',
                            start_date='20260501', end_date='20260603', adjust='qfq')
    print('OK 行数:', len(df))
    print(df.tail(2)[['日期', '收盘', '涨跌幅']])
