"""
Generate NASDX recommendation drift tracker.

用法:
  python run_recommendation_tracker.py
"""
# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from nasdx.recommendation_tracker import (
    build_and_save_recommendation_tracker,
    format_recommendation_tracker,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NASDX 建议漂移追踪")
    parser.add_argument("--output-dir", default=None, help="可选输出目录，默认 reports")
    parser.add_argument("--print", action="store_true", help="同时在终端打印 Markdown")
    args = parser.parse_args()

    tracker, paths = build_and_save_recommendation_tracker(output_dir=args.output_dir)
    counts = tracker.get("counts", {})
    print("✅ NASDX 建议漂移追踪已生成")
    print(f"   对比状态: {tracker.get('comparison_status')}")
    print(
        f"   新增: {counts.get('added', 0)}  "
        f"移除: {counts.get('removed', 0)}  "
        f"变化: {counts.get('changed', 0)}"
    )
    print(f"   Markdown: {paths['markdown']}")
    print(f"   JSON: {paths['json']}")
    if args.print:
        print()
        print(format_recommendation_tracker(tracker))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
