"""
全局代理 Patch — 必须在任何 akshare import 之前执行
用法：
    import quant.patch_requests  # 放在文件最顶部
    import akshare as ak         # 之后 akshare 自动走代理
"""
import requests as _req

_original_get = _req.get
_patched = False


def _smart_get(url, **kw):
    """eastmoney/sina 等国内数据源走代理，其余直连"""
    PROXIED = ('eastmoney.com', 'sina.com', 'qq.com', 'gtimg.com',
               '10jqka.com', 'xueqiu.com', 'jisilu.cn')
    if any(d in url for d in PROXIED):
        s = _req.Session()
        s.trust_env = True
        try:
            return s.get(url, **kw)
        except Exception:
            pass
    return _original_get(url, **kw)


def _do_patch():
    """执行 patch，只运行一次"""
    global _patched
    if _patched:
        return
    _req.get = _smart_get
    try:
        import akshare.stock_feature.stock_hist_em as _em
        _em.requests.get = _smart_get
    except Exception:
        pass
    try:
        import akshare.fund.fund_etf_em as _etf
        _etf.requests.get = _smart_get
    except Exception:
        pass
    try:
        import akshare.utils.request as _ur
        _ur.requests.get = _smart_get
    except Exception:
        pass
    _patched = True


_do_patch()
