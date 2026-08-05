# -*- coding: utf-8 -*-
"""NASDX 决策样本外评价报告 CLI（Issue #74）。

读取已落库且已回填前瞻标签的决策记录，输出可读性 Markdown 报告。
样本不足时**明确不下结论**（#74 验收 #5）：compare_modes 不会给出 best_mode，
evaluate_pairs 标 insufficient_sample，报告首行即说明样本量。

用法:
  python run_decision_evaluation.py
  python run_decision_evaluation.py --by-class
  python run_decision_evaluation.py --calibration
  python run_decision_evaluation.py --ablation --split-at 2026-01-01
  python run_decision_evaluation.py --code 600519 --mode full --min-samples 30
  python run_decision_evaluation.py --output evaluation_report.md
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from nasdx.decision_evaluation import (
    DEFAULT_HORIZON,
    DEFAULT_MIN_SAMPLES,
    ablation_report,
    compare_modes,
    confidence_calibration,
    evaluate_by_class,
    evaluate_pairs,
    format_evaluation_report,
)
from nasdx.decision_record import load_pairs


def build_report(args: argparse.Namespace) -> dict:
    """Assemble one report dict that ``format_evaluation_report`` can render.

    The optional sections (``modes`` / ``by_class`` / ``calibration``) are
    nested under the keys the formatter looks for, so a single run can show the
    headline numbers *and* the breakdowns without hiding the sample size.
    """
    pairs = load_pairs(
        code=args.code or None,
        mode=args.mode or None,
        since=args.since or None,
        db_path=args.db,
    )
    horizon = args.horizon
    min_samples = args.min_samples
    include_non_exec = args.include_non_executable

    if not pairs:
        # Explicitly refuse to conclude anything (#74 acceptance 5).
        return {
            "schema": "nasdx_decision_evaluation.v1",
            "horizon": horizon,
            "min_samples": min_samples,
            "samples": 0,
            "candidates": 0,
            "excluded": 0,
            "verdict": "insufficient_sample",
            "note": "无带标签样本：请先运行 run_decision_backfill.py 回填前瞻标签。",
        }

    if args.ablation:
        if not args.split_at:
            raise SystemExit("--ablation 必须配合 --split-at YYYY-MM-DD")
        return ablation_report(
            pairs, split_at=args.split_at, horizon=horizon, min_samples=min_samples
        )

    report = dict(
        evaluate_pairs(
            pairs,
            horizon=horizon,
            min_samples=min_samples,
            include_non_executable=include_non_exec,
            label=args.mode or "all",
        )
    )
    if args.compare:
        comparison = compare_modes(pairs, horizon=horizon, min_samples=min_samples)
        report["modes"] = comparison.get("modes") or {}
        report["ranking"] = comparison.get("ranking") or []
        report["best_mode"] = comparison.get("best_mode")
        report["reason"] = comparison.get("reason")
    if args.by_class:
        report["by_class"] = evaluate_by_class(
            pairs,
            horizon=horizon,
            min_samples=min_samples,
            include_non_executable=include_non_exec,
        )
    if args.calibration:
        report["calibration"] = confidence_calibration(
            pairs, horizon=horizon, min_samples=min_samples
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="NASDX 决策样本外评价报告")
    parser.add_argument("--code", help="只看某只标的（如 600519）")
    parser.add_argument("--mode", help="只看某模式（rules / full / intraday / ...）")
    parser.add_argument("--since", help="只看 data_as_of >= 该日期的记录")
    parser.add_argument("--db", help="决策数据库路径（默认 runtime 目录）")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON, help="前瞻窗口 T+N（默认 5）")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES, help="最小样本阈值（默认 20）")
    parser.add_argument("--include-non-executable", action="store_true", help="把不可执行/停牌样本计入（默认剔除）")
    parser.add_argument("--by-class", action="store_true", help="按评价类别（buy/hold/reduce/avoid）分表")
    parser.add_argument("--calibration", action="store_true", help="置信度校准分桶")
    parser.add_argument("--compare", action="store_true", help="模式对比（rules/full/intraday）")
    parser.add_argument("--ablation", action="store_true", help="时间切分消融（需 --split-at）")
    parser.add_argument("--split-at", help="消融切分日 YYYY-MM-DD（train < split <= test）")
    parser.add_argument("--output", help="把 Markdown 报告写入该文件")
    args = parser.parse_args()

    report = build_report(args)
    markdown = format_evaluation_report(report)
    print(markdown)

    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
        print(f"\n报告已写入: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
