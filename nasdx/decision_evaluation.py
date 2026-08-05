"""
Out-of-sample evaluation and multi-agent ablation for NASDX advice (#68).

This layer turns ``(DecisionRecord, forward labels)`` pairs into statistics that
can actually answer "is the expensive path better than the cheap one".

Guard rails
-----------
* **Leakage guard.** :func:`assert_no_lookahead` refuses any pair whose labels
  were computed from bars at or before the decision's ``data_as_of``, whose
  entry price drifted away from the frozen ``reference_price``, or whose
  ``decision_id`` does not match. Every public entry point runs it first.
* **Strict time split.** :func:`ablation_report` splits on a date: tuning may
  only look at ``train`` (``data_as_of < split_at``), verification only at
  ``test``. Records are never reused across the split.
* **No auto-winner on thin data.** A mode is only declared better when both
  sides clear ``min_samples`` *and* their mean confidence intervals do not
  overlap. Otherwise the verdict is ``insufficient_evidence``.
* **Class-aware.** ``buy``/``hold`` and ``reduce``/``avoid`` are scored with
  ``class_return_pct`` (favourable is positive), so they can be pooled honestly
  and also reported separately.
* **Deterministic.** Ordering is by ``(data_as_of, decision_id)`` everywhere.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from nasdx.decision_record import EVALUATION_CLASSES, DecisionRecord

Pair = Tuple[DecisionRecord, Mapping[str, Any]]

EVALUATION_SCHEMA = "nasdx_decision_evaluation.v1"
DEFAULT_MIN_SAMPLES = 20
DEFAULT_HORIZON = 5
Z95 = 1.959963984540054
DEFAULT_CONFIDENCE_BUCKETS: Tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)

VERDICT_INSUFFICIENT = "insufficient_sample"
VERDICT_OK = "ok"


class EvaluationLeakageError(RuntimeError):
    """Raised when labels could contain information from before the decision."""


# ---------------------------------------------------------------------------
# Guards and ordering
# ---------------------------------------------------------------------------
def assert_no_lookahead(pairs: Sequence[Pair]) -> None:
    """Fail fast when any label set could have used data the decision saw."""
    for record, labels in pairs:
        if labels.get("decision_id") not in (None, record.decision_id):
            raise EvaluationLeakageError(
                f"标签 decision_id={labels.get('decision_id')!r} 与记录 "
                f"{record.decision_id!r} 不匹配"
            )
        entry = labels.get("entry_price")
        if entry is not None and abs(float(entry) - float(record.reference_price)) > 1e-9:
            raise EvaluationLeakageError(
                f"{record.decision_id}: 标签入场价 {entry} 与冻结参考价 "
                f"{record.reference_price} 不一致，可能被未来行情覆盖"
            )
        first_date = labels.get("first_forward_date")
        if first_date and str(first_date) <= record.data_as_of_date:
            raise EvaluationLeakageError(
                f"{record.decision_id}: 前瞻窗口起点 {first_date} 未晚于决策数据时间 "
                f"{record.data_as_of_date}"
            )


def _sorted_pairs(pairs: Iterable[Pair]) -> List[Pair]:
    return sorted(pairs, key=lambda item: (item[0].data_as_of, item[0].decision_id))


def _horizon_value(labels: Mapping[str, Any], horizon: int, key: str) -> Optional[float]:
    entry = (labels.get("horizons") or {}).get(f"T+{horizon}")
    if not entry:
        return None
    value = entry.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _usable(
    record: DecisionRecord,
    labels: Mapping[str, Any],
    horizon: int,
    include_non_executable: bool,
) -> bool:
    if not include_non_executable and not labels.get("entry_executable", False):
        return False
    return _horizon_value(labels, horizon, "class_return_pct") is not None


# ---------------------------------------------------------------------------
# Descriptive statistics
# ---------------------------------------------------------------------------
def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _stdev(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    variance = sum((v - average) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _max_consecutive_losses(returns: Sequence[float]) -> int:
    worst = 0
    streak = 0
    for value in returns:
        if value < 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def evaluate_pairs(
    pairs: Sequence[Pair],
    *,
    horizon: int = DEFAULT_HORIZON,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    include_non_executable: bool = False,
    label: str = "all",
    check_leakage: bool = True,
) -> Dict[str, Any]:
    """Aggregate one cohort. Returns sample size, distribution and uncertainty."""
    ordered = _sorted_pairs(pairs)
    if check_leakage:
        assert_no_lookahead(ordered)

    usable = [
        (record, labels)
        for record, labels in ordered
        if _usable(record, labels, horizon, include_non_executable)
    ]
    returns = [
        float(_horizon_value(labels, horizon, "class_return_pct")) for _, labels in usable
    ]
    excess = [
        value
        for value in (_horizon_value(labels, horizon, "excess_pct") for _, labels in usable)
        if value is not None
    ]
    favorable = [
        float(labels["favorable_pct"])
        for _, labels in usable
        if labels.get("favorable_pct") is not None
    ]
    adverse = [
        float(labels["adverse_pct"])
        for _, labels in usable
        if labels.get("adverse_pct") is not None
    ]
    drawdowns = [
        float(labels["max_drawdown_pct"])
        for _, labels in usable
        if labels.get("max_drawdown_pct") is not None
    ]

    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    mean_return = _mean(returns)
    sd = _stdev(returns)
    ci_low = ci_high = None
    if mean_return is not None and sd is not None and returns:
        margin = Z95 * sd / math.sqrt(len(returns))
        ci_low, ci_high = mean_return - margin, mean_return + margin

    triggers = {"stop": 0, "target": 0, "none": 0}
    for _, labels in usable:
        trigger = labels.get("first_trigger")
        key = trigger["kind"] if trigger else "none"
        triggers[key] = triggers.get(key, 0) + 1

    excluded = len(ordered) - len(usable)
    n = len(returns)
    sufficient = n >= max(1, int(min_samples))

    return {
        "schema": EVALUATION_SCHEMA,
        "label": label,
        "horizon": horizon,
        "samples": n,
        "candidates": len(ordered),
        "excluded": excluded,
        "excluded_reasons": _exclusion_reasons(ordered, horizon, include_non_executable),
        "sufficient_sample": sufficient,
        "min_samples": int(min_samples),
        "verdict": VERDICT_OK if sufficient else VERDICT_INSUFFICIENT,
        "win_rate": _round(len(wins) / n) if n else None,
        "mean_return_pct": _round(mean_return),
        "median_return_pct": _round(_quantile(returns, 0.5)),
        "p25_return_pct": _round(_quantile(returns, 0.25)),
        "p75_return_pct": _round(_quantile(returns, 0.75)),
        "stdev_return_pct": _round(sd),
        "ci95_low_pct": _round(ci_low),
        "ci95_high_pct": _round(ci_high),
        "mean_excess_pct": _round(_mean(excess)),
        "excess_samples": len(excess),
        "mean_favorable_pct": _round(_mean(favorable)),
        "mean_adverse_pct": _round(_mean(adverse)),
        "median_favorable_pct": _round(_quantile(favorable, 0.5)),
        "median_adverse_pct": _round(_quantile(adverse, 0.5)),
        "mean_max_drawdown_pct": _round(_mean(drawdowns)),
        "profit_factor": _round(gross_profit / gross_loss) if gross_loss > 0 else None,
        "max_consecutive_losses": _max_consecutive_losses(returns),
        "first_trigger_counts": triggers,
        "cost": {
            "mean_llm_calls": _round(_mean([float(r.llm_calls) for r, _ in usable]), 3),
            "total_llm_calls": sum(int(r.llm_calls) for r, _ in usable),
            "mean_latency_ms": _round(_mean([float(r.latency_ms) for r, _ in usable]), 2),
        },
        "notes": [] if sufficient else [f"样本量 {n} < {min_samples}，不足以得出结论"],
    }


def _exclusion_reasons(
    pairs: Sequence[Pair], horizon: int, include_non_executable: bool
) -> Dict[str, int]:
    reasons: Dict[str, int] = {}
    for record, labels in pairs:
        if not include_non_executable and not labels.get("entry_executable", False):
            key = str(labels.get("entry_state") or "not_executable")
            reasons[key] = reasons.get(key, 0) + 1
            continue
        if _horizon_value(labels, horizon, "class_return_pct") is None:
            reasons["insufficient_data"] = reasons.get("insufficient_data", 0) + 1
    return reasons


# ---------------------------------------------------------------------------
# Grouped views
# ---------------------------------------------------------------------------
def group_by(
    pairs: Sequence[Pair],
    key: Callable[[DecisionRecord, Mapping[str, Any]], str],
    *,
    horizon: int = DEFAULT_HORIZON,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    include_non_executable: bool = False,
) -> Dict[str, Dict[str, Any]]:
    ordered = _sorted_pairs(pairs)
    assert_no_lookahead(ordered)
    buckets: Dict[str, List[Pair]] = {}
    for record, labels in ordered:
        buckets.setdefault(str(key(record, labels)), []).append((record, labels))
    return {
        name: evaluate_pairs(
            items,
            horizon=horizon,
            min_samples=min_samples,
            include_non_executable=include_non_executable,
            label=name,
            check_leakage=False,
        )
        for name, items in sorted(buckets.items())
    }


def evaluate_by_class(pairs: Sequence[Pair], **kwargs: Any) -> Dict[str, Dict[str, Any]]:
    """Per evaluation class — buy/hold/reduce/avoid are never pooled blindly."""
    report = group_by(pairs, lambda record, _labels: record.evaluation_class, **kwargs)
    for klass in EVALUATION_CLASSES:
        report.setdefault(
            klass,
            evaluate_pairs([], label=klass, check_leakage=False, **kwargs),
        )
    return dict(sorted(report.items()))


def confidence_calibration(
    pairs: Sequence[Pair],
    *,
    horizon: int = DEFAULT_HORIZON,
    buckets: Sequence[float] = DEFAULT_CONFIDENCE_BUCKETS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Dict[str, Any]:
    """Does a stated 0.8 confidence really win ~80% of the time?"""
    ordered = _sorted_pairs(pairs)
    assert_no_lookahead(ordered)
    edges = sorted({float(b) for b in buckets})
    rows: List[Dict[str, Any]] = []
    for index in range(len(edges) - 1):
        low, high = edges[index], edges[index + 1]
        is_last = index == len(edges) - 2
        selected = [
            (record, labels)
            for record, labels in ordered
            if record.confidence is not None
            and (low <= record.confidence <= high if is_last else low <= record.confidence < high)
        ]
        stats = evaluate_pairs(
            selected,
            horizon=horizon,
            min_samples=min_samples,
            label=f"[{low:.2f},{high:.2f}{']' if is_last else ')'}",
            check_leakage=False,
        )
        gap = None
        if stats["win_rate"] is not None:
            midpoint = (low + high) / 2.0
            gap = round(stats["win_rate"] - midpoint, 4)
        rows.append(
            {
                "bucket": stats["label"],
                "low": low,
                "high": high,
                "samples": stats["samples"],
                "win_rate": stats["win_rate"],
                "mean_return_pct": stats["mean_return_pct"],
                "calibration_gap": gap,
                "sufficient_sample": stats["sufficient_sample"],
            }
        )
    missing = sum(1 for record, _ in ordered if record.confidence is None)
    return {
        "schema": EVALUATION_SCHEMA,
        "horizon": horizon,
        "buckets": rows,
        "records_without_confidence": missing,
        "calibrated": None,
        "note": "样本不足的分桶不得用于校准结论",
    }


# ---------------------------------------------------------------------------
# Mode comparison and ablation
# ---------------------------------------------------------------------------
def compare_modes(
    pairs: Sequence[Pair],
    *,
    horizon: int = DEFAULT_HORIZON,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Dict[str, Any]:
    """Compare rules / full / intraday / ablation variants on one cohort."""
    per_mode = group_by(
        pairs, lambda record, _labels: record.mode, horizon=horizon, min_samples=min_samples
    )
    ranked = [
        (name, stats)
        for name, stats in per_mode.items()
        if stats["sufficient_sample"] and stats["mean_return_pct"] is not None
    ]
    ranked.sort(key=lambda item: (-item[1]["mean_return_pct"], item[0]))

    best_mode = None
    reason = "insufficient_evidence"
    if len(ranked) >= 2:
        top, runner = ranked[0][1], ranked[1][1]
        if (
            top["ci95_low_pct"] is not None
            and runner["ci95_high_pct"] is not None
            and top["ci95_low_pct"] > runner["ci95_high_pct"]
        ):
            best_mode = ranked[0][0]
            reason = "ci_separated"
        else:
            reason = "confidence_intervals_overlap"
    elif len(ranked) == 1:
        reason = "only_one_mode_has_enough_samples"

    return {
        "schema": EVALUATION_SCHEMA,
        "horizon": horizon,
        "min_samples": min_samples,
        "modes": per_mode,
        "ranking": [name for name, _ in ranked],
        "best_mode": best_mode,
        "reason": reason,
    }


def marginal_contribution(
    pairs: Sequence[Pair],
    baseline_mode: str,
    variant_mode: str,
    *,
    horizon: int = DEFAULT_HORIZON,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Dict[str, Any]:
    """Incremental value of one module (e.g. ``full`` vs ``full-no_battle``)."""
    modes = group_by(
        pairs, lambda record, _labels: record.mode, horizon=horizon, min_samples=min_samples
    )
    base = modes.get(baseline_mode.lower())
    variant = modes.get(variant_mode.lower())
    if base is None or variant is None:
        return {
            "schema": EVALUATION_SCHEMA,
            "baseline_mode": baseline_mode,
            "variant_mode": variant_mode,
            "verdict": "missing_mode",
            "delta_mean_return_pct": None,
            "conclusive": False,
        }
    # Marginal contribution is measured as variant minus baseline: a positive
    # delta means the extra module earned its keep. Inverting this sign would
    # recommend disabling the better mode.
    delta = None
    if base["mean_return_pct"] is not None and variant["mean_return_pct"] is not None:
        delta = round(variant["mean_return_pct"] - base["mean_return_pct"], 4)
    conclusive = bool(
        base["sufficient_sample"]
        and variant["sufficient_sample"]
        and base["ci95_low_pct"] is not None
        and variant["ci95_high_pct"] is not None
        and (
            base["ci95_low_pct"] > variant["ci95_high_pct"]
            or variant["ci95_low_pct"] > base["ci95_high_pct"]
        )
    )
    delta_calls = None
    if base["cost"]["mean_llm_calls"] is not None and variant["cost"]["mean_llm_calls"] is not None:
        delta_calls = round(
            variant["cost"]["mean_llm_calls"] - base["cost"]["mean_llm_calls"], 3
        )
    return {
        "schema": EVALUATION_SCHEMA,
        "horizon": horizon,
        "baseline_mode": baseline_mode.lower(),
        "variant_mode": variant_mode.lower(),
        "baseline": base,
        "variant": variant,
        "delta_mean_return_pct": delta,
        "delta_mean_llm_calls": delta_calls,
        "conclusive": conclusive,
        "verdict": VERDICT_OK if conclusive else VERDICT_INSUFFICIENT,
        # Only safe to switch the variant module off when the evidence is
        # conclusive AND the variant did not beat the baseline.
        "safe_to_disable": bool(conclusive and delta is not None and delta <= 0),
    }


def split_pairs(pairs: Sequence[Pair], split_at: str) -> Tuple[List[Pair], List[Pair]]:
    """Time split on ``data_as_of``: train < split_at <= test. No overlap."""
    boundary = str(split_at)[:10]
    if len(boundary) != 10 or boundary.count("-") != 2:
        raise ValueError(f"split_at 必须是 YYYY-MM-DD，收到 {split_at!r}")
    ordered = _sorted_pairs(pairs)
    train = [item for item in ordered if item[0].data_as_of_date < boundary]
    test = [item for item in ordered if item[0].data_as_of_date >= boundary]
    return train, test


def ablation_report(
    pairs: Sequence[Pair],
    *,
    split_at: str,
    horizon: int = DEFAULT_HORIZON,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Dict[str, Any]:
    """Walk-forward style ablation: tune on train, verify on test only."""
    ordered = _sorted_pairs(pairs)
    assert_no_lookahead(ordered)
    train, test = split_pairs(ordered, split_at)
    report = {
        "schema": EVALUATION_SCHEMA,
        "split_at": str(split_at)[:10],
        "horizon": horizon,
        "min_samples": min_samples,
        "leakage_checked": True,
        "train": {
            "samples": len(train),
            "modes": group_by(
                train,
                lambda record, _labels: record.mode,
                horizon=horizon,
                min_samples=min_samples,
            ),
        },
        "test": {
            "samples": len(test),
            "modes": group_by(
                test,
                lambda record, _labels: record.mode,
                horizon=horizon,
                min_samples=min_samples,
            ),
        },
        "verification": compare_modes(test, horizon=horizon, min_samples=min_samples),
        "by_class": evaluate_by_class(test, horizon=horizon, min_samples=min_samples),
        "calibration": confidence_calibration(
            test, horizon=horizon, min_samples=min_samples
        ),
    }
    if not test:
        report["verification"]["reason"] = "empty_test_set"
        report["verification"]["best_mode"] = None
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def format_evaluation_report(report: Mapping[str, Any]) -> str:
    """Markdown summary that always shows sample size before any conclusion."""
    lines: List[str] = []
    lines.append("# 建议样本外评价")
    lines.append("")
    lines.append(f"- 前瞻窗口: T+{report.get('horizon')}")
    if report.get("split_at"):
        lines.append(f"- 时间切分: train < {report['split_at']} <= test")
        lines.append(
            f"- 样本量: train {report['train']['samples']} / test {report['test']['samples']}"
        )
    lines.append(f"- 最小样本阈值: {report.get('min_samples')}")

    # A flat evaluate_pairs() result carries its sample size at the top level.
    # Render it here so the sample size is always stated before any conclusion.
    if report.get("samples") is not None:
        lines.append(f"- 样本量: {report['samples']}（候选 {report.get('candidates')}"
                     f" / 剔除 {report.get('excluded')}）")
        lines.append(f"- 结论可用性: {report.get('verdict')}")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| 样本量 | {report['samples']} |")
        lines.append(f"| 胜率 | {report.get('win_rate')} |")
        lines.append(f"| 均值收益% | {report.get('mean_return_pct')} |")
        lines.append(f"| 中位收益% | {report.get('median_return_pct')} |")
        ci_low, ci_high = report.get("ci95_low_pct"), report.get("ci95_high_pct")
        lines.append(f"| CI95 | {'-' if ci_low is None else f'[{ci_low}, {ci_high}]'} |")
        lines.append(f"| 平均有利% (MFE) | {report.get('mean_favorable_pct')} |")
        lines.append(f"| 平均不利% (MAE) | {report.get('mean_adverse_pct')} |")
        lines.append(f"| 最大连亏 | {report.get('max_consecutive_losses')} |")
    lines.append("")

    verification = report.get("verification") or report
    modes = verification.get("modes") or {}
    if modes:
        lines.append("## 模式对比（test）")
        lines.append("")
        lines.append(
            "| 模式 | 样本 | 胜率 | 均值收益% | 中位% | CI95 | 盈亏比 | 平均LLM调用 | 结论 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for name, stats in modes.items():
            ci = "-"
            if stats["ci95_low_pct"] is not None:
                ci = f"[{stats['ci95_low_pct']}, {stats['ci95_high_pct']}]"
            lines.append(
                f"| {name} | {stats['samples']} | {stats['win_rate']} | "
                f"{stats['mean_return_pct']} | {stats['median_return_pct']} | {ci} | "
                f"{stats['profit_factor']} | {stats['cost']['mean_llm_calls']} | "
                f"{stats['verdict']} |"
            )
        lines.append("")
        best = verification.get("best_mode")
        lines.append(
            f"**最佳模式**: {best if best else '不下结论'}（原因: {verification.get('reason')}）"
        )
        lines.append("")

    by_class = report.get("by_class") or {}
    if by_class:
        lines.append("## 分动作类别（评价语义不同，不可混算）")
        lines.append("")
        lines.append("| 类别 | 样本 | 胜率 | 均值收益% | 平均有利% | 平均不利% | 结论 |")
        lines.append("|---|---|---|---|---|---|---|")
        for name, stats in by_class.items():
            lines.append(
                f"| {name} | {stats['samples']} | {stats['win_rate']} | "
                f"{stats['mean_return_pct']} | {stats['mean_favorable_pct']} | "
                f"{stats['mean_adverse_pct']} | {stats['verdict']} |"
            )
        lines.append("")

    calibration = report.get("calibration") or {}
    if calibration.get("buckets"):
        lines.append("## 置信度校准")
        lines.append("")
        lines.append("| 分桶 | 样本 | 胜率 | 均值收益% | 偏差 | 样本充足 |")
        lines.append("|---|---|---|---|---|---|")
        for row in calibration["buckets"]:
            lines.append(
                f"| {row['bucket']} | {row['samples']} | {row['win_rate']} | "
                f"{row['mean_return_pct']} | {row['calibration_gap']} | "
                f"{'是' if row['sufficient_sample'] else '否'} |"
            )
        lines.append("")
    lines.append("> 样本不足的分组仅供观察，不能作为参数调整或模块下线依据。")
    return "\n".join(lines)
