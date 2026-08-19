"""估值历史分位 —— PE-TTM / PB 近5年分位带与所处分位。

依赖 akshare.stock_zh_valuation_baidu（百度股市通）。
只呈现"处于历史什么位置"，不划买卖线、不给推荐。
"""
from __future__ import annotations

from typing import Any


class DependencyMissing(RuntimeError):
    """惰性依赖未安装时抛出。"""


def _akshare() -> Any:
    try:
        import akshare as ak
        return ak
    except ImportError as e:
        raise DependencyMissing("akshare 未安装：pip install akshare") from e


def _percentile_interp(sorted_vals: list[float], p: float) -> float:
    """线性插值分位数（与方法一同等价，兼容空序列）。"""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    idx = p * (n - 1)
    lo = int(idx)
    if lo + 1 >= n:
        return sorted_vals[-1]
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[lo + 1] * frac


def valuation_percentile(code: str, period: str = "近五年") -> dict[str, Any]:
    """返回 PE-TTM / PB 的当前值 + 历史分位带 + 所处分位。

    code: 6 位股票代码（如 "600519"）。
    period: akshare 接受的周期字符串，默认 "近五年"。

    返回结构：
    {
      "code": str,
      "period": str,
      "metrics": {
        "pe_ttm": {"current", "percentile", "min", "max", "p20", "p50", "p80", "n"},
        "pb":    {同上}
      }
    }
    任一分量缺失时对应的 key 不出现（而非报错）。
    """
    ak = _akshare()

    metrics: dict[str, dict[str, Any]] = {}

    for key, ind in (("pe_ttm", "市盈率(TTM)"), ("pb", "市净率")):
        try:
            df = ak.stock_zh_valuation_baidu(symbol=code, indicator=ind, period=period)
            raw = df.iloc[:, 1].dropna().astype(float).tolist()
            if not raw:
                continue
            cur = float(raw[-1])
            s = sorted(raw)
            below = sum(1 for x in s if x < cur)
            metrics[key] = {
                "current": round(cur, 2),
                # 百分位定义为“严格低于当前值的样本数 / 全部样本数”。
                # 因而历史最大值在 10 条样本中为 90%，不会虚构为 100%。
                "percentile": round(below / len(s) * 100, 1),
                "min": round(s[0], 2),
                "max": round(s[-1], 2),
                "p20": round(_percentile_interp(s, 0.2), 2),
                "p50": round(_percentile_interp(s, 0.5), 2),
                "p80": round(_percentile_interp(s, 0.8), 2),
                "n": len(s),
            }
        except Exception:
            continue

    return {"code": code, "period": "近5年", "metrics": metrics}
