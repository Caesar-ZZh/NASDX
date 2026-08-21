"""
Export NASDX review snapshot package.

用法:
  python run_review_snapshot.py --risk-profile balanced
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

from nasdx.review_snapshot import SnapshotValidationError, build_review_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 NASDX 复盘快照包")
    parser.add_argument(
        "--risk-profile",
        choices=["conservative", "balanced", "aggressive"],
        default=os.environ.get("NASDX_RISK_PROFILE", "balanced"),
    )
    parser.add_argument("--output-dir", default=None, help="可选输出目录，默认 reports/snapshots")
    parser.add_argument("--refresh", action="store_true", help="导出前刷新最终简报")
    args = parser.parse_args()

    try:
        snapshot = build_review_snapshot(
            risk_profile=args.risk_profile,
            output_dir=args.output_dir,
            refresh=args.refresh,
        )
    except (SnapshotValidationError, OSError) as exc:
        print(f"❌ 复盘快照生成失败：{exc}")
        return 2
    manifest = snapshot.get("manifest", {})
    print("✅ NASDX 复盘快照包已生成")
    print(f"   ZIP: {snapshot['zip_path']}")
    print(
        f"   候选: {manifest.get('candidate_count')}  "
        f"执行动作: {manifest.get('execution_action_count')}  "
        f"外部复核: {manifest.get('external_review_count')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
