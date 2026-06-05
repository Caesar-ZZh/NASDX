"""
全局代理 Patch — 必须在任何 akshare import 之前执行
优化：akshare 子模块 patch 延迟到首次请求时执行（避免 import 时 1.2s 阻塞）
"""
import requests as _req

_original_get = _req.get
_patched = False
_ak_patched = False   # akshare 子模块是否已 patch

PROXIED = ('eastmoney.com', 'sina.com', 'qq.com', 'gtimg.com',
           '10jqka.com', 'xueqiu.com', 'jisilu.cn')


def _patch_ak_internals():
    """懒加载：第一次实际请求时才 patch akshare 内部引用"""
    global _ak_patched
    if _ak_patched:
        return
    _ak_patched = True
    # 只 patch 已经被 import 的模块（不触发新 import）
    import sys
    for mod_name, attr in [
        ('akshare.stock_feature.stock_hist_em', 'requests'),
        ('akshare.fund.fund_etf_em',            'requests'),
        ('akshare.utils.request',               'requests'),
    ]:
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, attr):
            try:
                getattr(mod, attr).get = _smart_get
            except Exception:
                pass


def _smart_get(url, **kw):
    """eastmoney/sina 等国内数据源走代理"""
    if any(d in url for d in PROXIED):
        _patch_ak_internals()   # 懒 patch
        s = _req.Session()
        s.trust_env = True
        try:
            return s.get(url, **kw)
        except Exception:
            pass
    return _original_get(url, **kw)


def _do_patch():
    """只 patch requests.get，不触发任何 akshare import"""
    global _patched
    if _patched:
        return
    _req.get = _smart_get
    _patched = True


_do_patch()   # 模块加载时立即执行，但不 import akshare，<1ms
