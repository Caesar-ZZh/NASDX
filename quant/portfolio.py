"""
NASDX V2 — 投资组合优化
参考 QLib Portfolio 模块
支持：均值方差优化 / 风险平价 / 黑-利特曼模型（简化）
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional


def mean_variance_optimize(
    returns:       pd.DataFrame,   # 每列一只资产的日收益率
    risk_aversion: float = 3.0,    # 风险厌恶系数（越大越保守）
    max_weight:    float = 0.4,    # 单只最大权重
    min_weight:    float = 0.0,    # 单只最小权重
) -> pd.Series:
    """
    均值方差优化（Markowitz）
    简化实现，不依赖 scipy.optimize
    """
    mu  = returns.mean() * 252         # 年化收益
    cov = returns.cov() * 252          # 年化协方差

    n = len(mu)
    if n == 0:
        return pd.Series()

    # 简化：等权 + 按 sharpe 调整
    sharpe = mu / (np.sqrt(np.diag(cov)) + 1e-9)
    sharpe = sharpe.clip(lower=0)
    total  = sharpe.sum()

    if total <= 0:
        weights = pd.Series(1.0 / n, index=mu.index)
    else:
        weights = sharpe / total

    # 限制权重范围
    weights = weights.clip(min_weight, max_weight)
    weights /= weights.sum()
    return weights


def risk_parity(
    returns:    pd.DataFrame,
    max_weight: float = 0.4,
) -> pd.Series:
    """
    风险平价策略：让每只资产贡献相同风险
    """
    vols = returns.std() * np.sqrt(252)
    inv_vol = 1.0 / (vols + 1e-9)
    weights = inv_vol / inv_vol.sum()
    weights = weights.clip(0, max_weight)
    weights /= weights.sum()
    return weights


def equal_weight(codes: list[str]) -> pd.Series:
    """等权组合"""
    n = len(codes)
    return pd.Series(1.0 / n, index=codes)


def build_portfolio(
    factor_scores: pd.DataFrame,   # multi_factor_score 的输出
    returns:       pd.DataFrame,   # 历史收益率矩阵
    method:        str = "factor", # factor / mv / rp / equal
    top_n:         int = 5,
    max_weight:    float = 0.4,
) -> pd.Series:
    """
    构建投资组合权重
    """
    if factor_scores.empty:
        return pd.Series()

    # 选 top_n
    top_codes = factor_scores.head(top_n)["code"].tolist()
    top_returns = returns[top_codes].dropna() if all(c in returns.columns for c in top_codes) else pd.DataFrame()

    if method == "factor":
        # 按因子分数加权
        top_scores = factor_scores[factor_scores["code"].isin(top_codes)].set_index("code")["factor_score"]
        top_scores = top_scores.clip(lower=0)
        total = top_scores.sum()
        if total > 0:
            w = top_scores / total
        else:
            w = equal_weight(top_codes)
        w = w.clip(0, max_weight)
        w /= w.sum()
        return w

    elif method == "mv" and not top_returns.empty and len(top_returns) >= 30:
        return mean_variance_optimize(top_returns, max_weight=max_weight)

    elif method == "rp" and not top_returns.empty and len(top_returns) >= 20:
        return risk_parity(top_returns, max_weight=max_weight)

    else:
        return equal_weight(top_codes)


def calc_portfolio_metrics(weights: pd.Series, returns: pd.DataFrame) -> dict:
    """计算当前组合的风险指标"""
    codes = [c for c in weights.index if c in returns.columns]
    if not codes:
        return {}

    w = weights[codes].values
    r = returns[codes].dropna()
    if r.empty:
        return {}

    port_ret = (r * w).sum(axis=1)
    annual_ret = port_ret.mean() * 252
    annual_vol = port_ret.std() * np.sqrt(252)
    sharpe = annual_ret / (annual_vol + 1e-9)

    cum = (1 + port_ret).cumprod()
    dd  = ((cum - cum.cummax()) / cum.cummax()).min()

    corr_matrix = r.corr()
    avg_corr = corr_matrix.values[np.triu_indices(len(corr_matrix), k=1)].mean()

    return {
        "annual_return":    round(annual_ret, 4),
        "annual_volatility": round(annual_vol, 4),
        "sharpe_ratio":     round(sharpe, 4),
        "max_drawdown":     round(dd, 4),
        "avg_correlation":  round(avg_corr, 4),
        "weights":          dict(zip(codes, w.round(4))),
    }
