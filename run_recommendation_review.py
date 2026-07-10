"""
Generate NASDX recommendation outcome review.

用法:
  python run_recommendation_review.py
  python run_recommendation_review.py --baseline reports/investment_brief_20260612_1059.json --print
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from nasdx.recommendation_review import (
    build_and_save_recommendation_review,
    format_recommendation_review,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NASDX 建议结果复盘")
    parser.add_argument("--baseline", default=None, help="可选基准简报 JSON；默认使用上一份不同时间简报")
    parser.add_argument("--output-dir", default=None, help="可选输出目录，默认 reports")
    parser.add_argument("--print", action="store_true", help="同时在终端打印 Markdown")
    args = parser.parse_args()

    review, paths = build_and_save_recommendation_review(
        baseline_path=args.baseline,
        output_dir=args.output_dir,
    )
    counts = review.get("counts", {})
    print("✅ NASDX 建议结果复盘已生成")
    print(f"   基准: {review.get('baseline_generated_at')}")
    print(
        f"   延续: {counts.get('signal_continues', 0)}  "
        f"降级: {counts.get('downgrade_review', 0)}  "
        f"待补: {counts.get('pending_evidence', 0)}  "
        f"缺数据: {counts.get('missing_current_data', 0)}"
    )
    print(f"   Markdown: {paths['markdown']}")
    print(f"   JSON: {paths['json']}")
    if args.print:
        print()
        print(format_recommendation_review(review))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
