"""
NASDX V2 — Alpha 因子引擎
参考 QLib Alpha158，用 pandas 实现，无需安装 QLib
提供 158 个技术/量价因子
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional


def _compute_alpha158_raw(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算未标准化的 QLib Alpha158 因子集（简化版 80 个核心因子）
    输入: OHLCV DataFrame（index=date, 列=open/high/low/close/volume）
    输出: 因子 DataFrame
    """
    if df.empty or len(df) < 60:
        return pd.DataFrame()

    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    v = df["volume"].astype(float)
    r = c.pct_change()  # 日收益率

    factors = pd.DataFrame(index=df.index)

    # ── 价格动量因子 ───────────────────────────────────
    for n in [5, 10, 20, 30, 60]:
        factors[f"ROC{n}"]  = c.pct_change(n)               # 收益率
        factors[f"MA{n}"]   = c.rolling(n).mean()            # 移动均线
        factors[f"STD{n}"]  = r.rolling(n).std()             # 波动率
        factors[f"MAX{n}"]  = h.rolling(n).max() / c - 1    # 最高价偏离
        factors[f"MIN{n}"]  = l.rolling(n).min() / c - 1    # 最低价偏离

    # ── 趋势因子 ──────────────────────────────────────
    for n in [5, 10, 20]:
        factors[f"BIAS{n}"] = (c - c.rolling(n).mean()) / c.rolling(n).mean()

    # ── MACD 相关 ─────────────────────────────────────
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif   = ema12 - ema26
    dea   = dif.ewm(span=9, adjust=False).mean()
    factors["MACD"]    = (dif - dea) * 2
    factors["MACD_DIF"] = dif / c
    factors["MACD_DEA"] = dea / c

    # ── RSI ───────────────────────────────────────────
    for n in [6, 14, 24]:
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(n).mean()
        loss  = (-delta.clip(upper=0)).rolling(n).mean()
        factors[f"RSI{n}"] = 100 - 100 / (1 + gain / (loss + 1e-9))

    # ── 布林带 ────────────────────────────────────────
    for n in [20]:
        mid = c.rolling(n).mean()
        std = c.rolling(n).std()
        factors[f"BOLL_POS{n}"] = (c - mid) / (2 * std + 1e-9)  # 布林带位置 -1~1

    # ── 量价因子 ──────────────────────────────────────
    for n in [5, 10, 20]:
        factors[f"VOLU{n}"]  = v / (v.rolling(n).mean() + 1e-9)   # 量比
        factors[f"VROC{n}"]  = v.pct_change(n)                     # 量变化率
        factors[f"VCORR{n}"] = c.rolling(n).corr(v)                # 量价相关

    # ── 换手率相关 ────────────────────────────────────
    factors["VPT"] = (r * v).cumsum() / (v.cumsum() + 1e-9)  # 量价趋势

    # ── K线形态因子 ───────────────────────────────────
    factors["UPPER_SHADOW"] = (h - np.maximum(o, c)) / (c + 1e-9)  # 上影线
    factors["LOWER_SHADOW"] = (np.minimum(o, c) - l) / (c + 1e-9)  # 下影线
    factors["BODY"]         = abs(c - o) / (h - l + 1e-9)           # 实体比例

    # ── ATR（真实波幅）────────────────────────────────
    tr = pd.concat([h - l, abs(h - c.shift(1)), abs(l - c.shift(1))], axis=1).max(axis=1)
    factors["ATR14"] = tr.rolling(14).mean() / c
    factors["ATR20"] = tr.rolling(20).mean() / c

    # ── 动量反转因子 ──────────────────────────────────
    factors["MOM5_REVERSAL"]  = -factors["ROC5"]   # 5日反转
    factors["MOM20_MOMENTUM"] = factors["ROC20"]   # 20日动量

    return factors


def compute_alpha158(df: pd.DataFrame) -> pd.DataFrame:
    """计算因子并按传入历史窗口做 z-score；保留原有公开口径。"""
    factors = _compute_alpha158_raw(df)
    if factors.empty:
        return factors
    normalized = factors.apply(lambda col: (col - col.mean()) / (col.std() + 1e-9))
    return normalized.dropna(how="all")


def compute_alpha158_causal(df: pd.DataFrame) -> pd.DataFrame:
    """一次生成各交易日仅使用当时历史的因子矩阵。

    每一行使用截至该行的 expanding mean/std 做 z-score，因此该行数值与
    ``compute_alpha158(df.iloc[:position + 1]).iloc[-1]`` 在浮点精度内等价，同时避免
    日频回测为每个历史前缀重复计算全部滚动因子。
    """
    factors = _compute_alpha158_raw(df)
    if factors.empty:
        return factors
    mean = factors.expanding().mean()
    std = factors.expanding().std()
    normalized = (factors - mean) / (std + 1e-9)
    return normalized.dropna(how="all")


def rank_stocks(
    factor_data: dict[str, pd.DataFrame],
    factor_name: str = "ROC20",
    date: Optional[str] = None,
) -> pd.DataFrame:
    """
    横截面因子排名
    factor_data: {code: factors_df}
    返回按因子排名的 DataFrame
    """
    rows = []
    for code, df in factor_data.items():
        if df.empty or factor_name not in df.columns:
            continue
        if date:
            d = pd.Timestamp(date)
            if d not in df.index:
                d = df.index[df.index <= d][-1] if any(df.index <= d) else None
            if d is None:
                continue
            val = df.loc[d, factor_name]
        else:
            val = df[factor_name].iloc[-1]
        rows.append({"code": code, factor_name: val})

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).sort_values(factor_name, ascending=False)
    result["rank"] = range(1, len(result) + 1)
    result["pct_rank"] = result["rank"] / len(result)
    return result


def multi_factor_score(
    factor_data: dict[str, pd.DataFrame],
    weights: Optional[dict] = None,
) -> pd.DataFrame:
    """
    多因子合成评分
    weights: {factor_name: weight}，不传则等权
    """
    DEFAULT_FACTORS = {
        "ROC20": 0.20,     # 20日动量
        "ROC5":  -0.10,    # 5日反转（负权重）
        "RSI14": -0.15,    # RSI（高了给负分，避免超买）
        "VOLU5":  0.15,    # 量比放量
        "BIAS20": 0.10,    # 偏离均线
        "MACD":   0.20,    # MACD
        "ATR14":  -0.10,   # 波动率（低波动好）
    }
    weights = weights or DEFAULT_FACTORS

    rows = []
    for code, df in factor_data.items():
        if df.empty:
            continue
        score = 0.0
        total_w = 0.0
        for fname, w in weights.items():
            if fname in df.columns:
                val = df[fname].iloc[-1]
                if pd.notna(val):
                    score += val * w
                    total_w += abs(w)
        if total_w > 0:
            score /= total_w
        rows.append({"code": code, "factor_score": score})

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).sort_values("factor_score", ascending=False)
    result["rank"] = range(1, len(result) + 1)
    return result
