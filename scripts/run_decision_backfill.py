# -*- coding: utf-8 -*-
"""NASDX 决策前瞻标签回填 CLI（Issue #74）。

对库里每条冻结决策记录，抓取 data_as_of 之后的 K 线，重算并落盘前瞻标签。
幂等：重复运行只覆盖 decision_outcomes，绝不改写冻结的 decision_records。
落库/抓取失败只计数跳过，不中断。

用法:
  python run_decision_backfill.py
  python run_decision_backfill.py --code 600519
  python run_decision_backfill.py --since 2026-01-01
  python run_decision_backfill.py --db path/to/nasdx_decisions.db
"""
# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nasdx.decision_backfill import backfill_labels


def main() -> int:
    parser = argparse.ArgumentParser(description="NASDX 决策前瞻标签回填")
    parser.add_argument("--code", help="只看某只标的")
    parser.add_argument("--since", help="只看 data_as_of >= 该日期的记录")
    parser.add_argument("--db", help="决策数据库路径（默认 runtime 目录）")
    parser.add_argument("--today", help="回填终点日期 YYYY-MM-DD（默认今天）")
    args = parser.parse_args()

    summary = backfill_labels(
        db_path=args.db,
        code=args.code or None,
        since=args.since or None,
        today=args.today,
    )
    print("NASDX 决策标签回填完成")
    print(f"  读取记录: {summary['records']}")
    print(f"  已回填标签: {summary['labeled']}")
    print(f"  跳过(无行情): {summary['skipped']}")
    print(f"  错误: {summary['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
