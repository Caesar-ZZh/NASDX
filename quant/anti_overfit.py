"""
NASDX V2 — 抗过拟合核心模块
借鉴三大框架的最佳实践：

QLib  → Walk-Forward Validation / IC 稳定性检验 / 因子衰减监控
FinRL → 多环境并行训练 / Dropout / Early Stopping
VnPy  → 参数鲁棒性测试 / 样本外验证 / 滑点压力测试

核心原则：
  1. 时间序列不可用随机 K-Fold（用 Walk-Forward）
  2. 因子 IC 需在多个窗口上稳定
  3. 策略参数需对扰动不敏感
  4. 实盘信号需用集成投票而非单模型
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Callable, Optional
from dataclasses import dataclass, field


# ══════════════════════════════════════════
#  1. Walk-Forward 时序交叉验证（QLib 核心）
# ══════════════════════════════════════════
@dataclass
class WalkForwardConfig:
    train_days: int = 252     # 训练窗口（1年）
    test_days:  int = 63      # 测试窗口（1季度）
    step_days:  int = 63      # 步进（每季度前进一次）
    min_train:  int = 60      # 最小训练样本（必须 <= train_days）

    def __post_init__(self):
        # 自动修正：min_train 不能超过 train_days
        if self.min_train > self.train_days:
            self.min_train = self.train_days // 2


def walk_forward_split(
    index: pd.DatetimeIndex,
    cfg:   WalkForwardConfig = WalkForwardConfig(),
) -> list[tuple[pd.DatetimeIndex, pd.DatetimeIndex]]:
    """
    生成 Walk-Forward 时序分割
    返回 [(train_idx, test_idx), ...] 列表
    注意：test 永远在 train 之后，严格防止未来数据泄漏
    """
    splits = []
    n = len(index)
    start = 0

    while True:
        train_end = start + cfg.train_days
        test_end  = train_end + cfg.test_days

        # 数据不够就停止
        if test_end > n:
            break
        # 实际训练样本太少就停止（用窗口内样本数判断，而非train_end绝对值）
        actual_train = train_end - start
        if actual_train < cfg.min_train:
            break

        train_idx = index[start:train_end]
        test_idx  = index[train_end:test_end]
        splits.append((train_idx, test_idx))

        start += cfg.step_days

    return splits


def walk_forward_backtest(
    data:        pd.DataFrame,       # 价格/因子数据，index=date
    model_fn:    Callable,           # fn(train_df) → predict_fn
    predict_fn_type: str = "return", # return / signal
    cfg:         WalkForwardConfig = WalkForwardConfig(),
) -> pd.DataFrame:
    """
    Walk-Forward 回测框架
    每个窗口：在 train 上训练模型，在 test 上预测，汇总结果
    """
    splits = walk_forward_split(data.index, cfg)
    all_preds = []

    for i, (train_idx, test_idx) in enumerate(splits):
        train_df = data.loc[train_idx]
        test_df  = data.loc[test_idx]

        try:
            predict_fn = model_fn(train_df)
            preds = predict_fn(test_df)
            preds = pd.Series(preds, index=test_idx[:len(preds)])
            preds.name = f"fold_{i}"
            all_preds.append(preds)
        except Exception as e:
            print(f"  ⚠️ Fold {i} 训练失败：{e}")
            continue

    if not all_preds:
        return pd.DataFrame()

    result = pd.concat(all_preds).sort_index()
    return result


# ══════════════════════════════════════════
#  2. 因子 IC 稳定性检验（QLib Alpha 评估）
# ══════════════════════════════════════════
def calc_ic(
    factor: pd.Series,     # 因子值（今日）
    forward_ret: pd.Series,# 未来N日收益率
) -> float:
    """计算因子 IC（Information Coefficient）= Spearman 相关"""
    aligned = pd.concat([factor, forward_ret], axis=1).dropna()
    if len(aligned) < 10:
        return np.nan
    return aligned.iloc[:, 0].corr(aligned.iloc[:, 1], method="spearman")


def calc_icir(ic_series: pd.Series) -> float:
    """ICIR = IC均值 / IC标准差（越高越好，>0.5 算稳定）"""
    if ic_series.std() < 1e-9:
        return 0.0
    return ic_series.mean() / ic_series.std()


def factor_stability_test(
    factor_data:  dict[str, pd.DataFrame],   # {code: factors_df}
    price_data:   dict[str, pd.DataFrame],   # {code: OHLCV}
    factor_names: list[str],
    forward_days: int = 5,
    windows:      list[int] = [30, 60, 120, 252],
) -> pd.DataFrame:
    """
    因子稳定性检验
    在不同时间窗口上计算 IC，筛选出稳定因子

    返回 DataFrame：factor | window | IC_mean | ICIR | is_stable
    """
    rows = []
    for fname in factor_names:
        for window in windows:
            ic_list = []
            for code in factor_data:
                if code not in price_data:
                    continue
                fdf = factor_data[code]
                pdf = price_data[code]

                if fname not in fdf.columns or len(fdf) < window + forward_days:
                    continue

                # 计算未来 N 日收益率
                fwd_ret = pdf["close"].pct_change(forward_days).shift(-forward_days)

                # 滚动计算 IC
                for i in range(window, len(fdf) - forward_days, 10):
                    factor_val = fdf[fname].iloc[i]
                    ret_val    = fwd_ret.iloc[i] if i < len(fwd_ret) else np.nan
                    if pd.notna(factor_val) and pd.notna(ret_val):
                        ic_list.append(factor_val * np.sign(ret_val))  # 简化 IC

                if len(ic_list) >= 5:
                    ic_ser = pd.Series(ic_list)
                    ic_mean = ic_ser.mean()
                    icir = calc_icir(ic_ser)
                    rows.append({
                        "factor": fname,
                        "window": window,
                        "IC_mean": round(ic_mean, 4),
                        "ICIR":    round(icir, 4),
                        "is_stable": abs(icir) > 0.3 and abs(ic_mean) > 0.02,
                    })

    return pd.DataFrame(rows)


# ══════════════════════════════════════════
#  3. 参数鲁棒性测试（VnPy 压力测试思路）
# ══════════════════════════════════════════
def parameter_robustness_test(
    strategy_fn:    Callable,             # fn(params) → backtest_result
    base_params:    dict,                 # 基准参数
    param_ranges:   dict,                 # {param: [val1, val2, ...]}
    metric:         str = "sharpe_ratio", # 评估指标
) -> pd.DataFrame:
    """
    参数鲁棒性测试：
    扰动每个参数，观察策略表现变化幅度
    变化越小 → 参数越鲁棒 → 过拟合风险越低

    判断标准（借鉴 VnPy）：
      优秀：扰动10%后 sharpe 变化 < 20%
      合格：扰动10%后 sharpe 变化 < 40%
      过拟合风险：sharpe 变化 > 40%
    """
    rows = []

    # 先跑基准
    try:
        base_result = strategy_fn(base_params)
        base_metric = base_result.get(metric, 0)
    except Exception as e:
        print(f"基准参数运行失败：{e}")
        return pd.DataFrame()

    print(f"\n基准 {metric}: {base_metric:.4f}")

    for param_name, values in param_ranges.items():
        for val in values:
            test_params = base_params.copy()
            test_params[param_name] = val
            try:
                result = strategy_fn(test_params)
                test_metric = result.get(metric, 0)
                change_pct  = (test_metric - base_metric) / (abs(base_metric) + 1e-9) * 100
                robust = abs(change_pct) < 40
                rows.append({
                    "param":       param_name,
                    "value":       val,
                    "base_value":  base_params[param_name],
                    metric:        round(test_metric, 4),
                    "change_pct":  round(change_pct, 2),
                    "is_robust":   robust,
                    "risk":        "低" if abs(change_pct) < 20 else "中" if abs(change_pct) < 40 else "高⚠️",
                })
            except Exception as e:
                rows.append({
                    "param": param_name, "value": val, "error": str(e),
                    metric: np.nan, "change_pct": np.nan, "is_robust": False, "risk": "失败",
                })

    return pd.DataFrame(rows)


# ══════════════════════════════════════════
#  4. 集成信号投票（防止单模型过拟合）
# ══════════════════════════════════════════
@dataclass
class SignalVoter:
    """
    多策略信号集成投票
    参考 FinRL 的多环境并行训练思路
    用多个独立信号源投票，消除单模型的过拟合

    信号来源：
      - 技术面（NASDX 评分）
      - 因子模型（Alpha158）
      - 趋势跟踪（MA/MACD）
      - 量价关系
      - AI 分析（DeepSeek）
    """
    weights: dict = field(default_factory=lambda: {
        "technical":  0.25,  # 技术面评分
        "factor":     0.25,  # 因子模型
        "trend":      0.20,  # 趋势信号
        "volume":     0.15,  # 量价信号
        "ai":         0.15,  # AI 分析信号
    })
    threshold_bull: float = 0.6   # 看多阈值
    threshold_bear: float = 0.4   # 看空阈值

    def vote(self, signals: dict[str, dict]) -> dict:
        """
        输入各信号源的信号，输出最终综合信号
        signals: {source: {code: score}}  score in [0,1]，0.5=中性
        返回: {code: {"score": float, "signal": str, "confidence": float}}
        """
        # 收集所有 code
        all_codes = set()
        for src_signals in signals.values():
            all_codes.update(src_signals.keys())

        results = {}
        for code in all_codes:
            weighted_score = 0.0
            total_weight   = 0.0
            source_scores  = {}

            for src, src_signals in signals.items():
                w   = self.weights.get(src, 0.1)
                val = src_signals.get(code, 0.5)  # 缺失按中性 0.5 处理
                weighted_score += val * w
                total_weight   += w
                source_scores[src] = round(val, 3)

            final_score = weighted_score / (total_weight + 1e-9)

            # 信号一致性（越一致越可信）
            scores_list = list(source_scores.values())
            consistency = 1.0 - np.std(scores_list)  # std 越小越一致

            if final_score >= self.threshold_bull:
                signal = "bullish"
            elif final_score <= self.threshold_bear:
                signal = "bearish"
            else:
                signal = "neutral"

            results[code] = {
                "score":        round(final_score, 4),
                "signal":       signal,
                "confidence":   round(max(0, consistency), 3),
                "source_scores": source_scores,
                # 只有所有信号一致时才高置信
                "high_confidence": consistency > 0.7 and final_score not in [0.45, 0.55],
            }

        return results


# ══════════════════════════════════════════
#  5. 过拟合综合诊断报告
# ══════════════════════════════════════════
def overfit_diagnosis(
    in_sample_metrics:  dict,   # 样本内指标
    out_sample_metrics: dict,   # 样本外指标
    strategy_name:      str = "策略",
) -> dict:
    """
    过拟合诊断
    参考 QLib 的 backtest analysis 模块

    关键检验：
      1. 样本外/样本内 Sharpe 比率衰减 < 50% → 合格
      2. 样本外最大回撤 < 样本内的 2倍 → 合格
      3. 样本外年化收益 > 0 → 合格
    """
    is_sharpe = in_sample_metrics.get("sharpe_ratio", 0)
    os_sharpe = out_sample_metrics.get("sharpe_ratio", 0)
    is_dd     = in_sample_metrics.get("max_drawdown", -0.1)
    os_dd     = out_sample_metrics.get("max_drawdown", -0.2)
    is_ret    = in_sample_metrics.get("annual_return", 0)
    os_ret    = out_sample_metrics.get("annual_return", 0)

    # Sharpe 衰减
    sharpe_decay = (is_sharpe - os_sharpe) / (abs(is_sharpe) + 1e-9)

    # 回撤恶化
    dd_ratio = abs(os_dd) / (abs(is_dd) + 1e-9)

    # 综合诊断
    issues = []
    if sharpe_decay > 0.5:
        issues.append(f"⚠️ Sharpe 样本外衰减 {sharpe_decay:.0%}（过拟合风险高）")
    if dd_ratio > 2.0:
        issues.append(f"⚠️ 样本外回撤是样本内的 {dd_ratio:.1f} 倍")
    if os_ret < 0:
        issues.append(f"⚠️ 样本外年化收益为负 ({os_ret:.1%})")
    if os_sharpe < 0.5:
        issues.append(f"⚠️ 样本外 Sharpe 过低 ({os_sharpe:.2f})")

    risk_level = "低" if len(issues) == 0 else "中" if len(issues) == 1 else "高"

    summary = {
        "strategy":        strategy_name,
        "in_sample":       in_sample_metrics,
        "out_sample":      out_sample_metrics,
        "sharpe_decay":    round(sharpe_decay, 4),
        "dd_ratio":        round(dd_ratio, 4),
        "issues":          issues,
        "overfit_risk":    risk_level,
        "verdict":         "✅ 通过" if not issues else f"❌ 存在 {len(issues)} 个风险点",
        "recommendation":  _get_recommendation(issues, sharpe_decay),
    }
    return summary


def _get_recommendation(issues: list, sharpe_decay: float) -> str:
    if not issues:
        return "策略表现稳健，可考虑小仓位实盘验证（建议先用10%资金跑1个月）"
    recs = []
    if sharpe_decay > 0.5:
        recs.append("减少参数数量，使用更保守的特征选择（如因子数量从20降到10）")
        recs.append("增大 Walk-Forward 测试窗口（从63天增到126天）")
        recs.append("引入 L2 正则化或 Dropout 防止模型过拟合")
    if len(issues) >= 2:
        recs.append("考虑使用更简单的基准策略（等权 + 动量过滤）替代复杂模型")
    return "；".join(recs) if recs else "继续观察更多样本外数据"
