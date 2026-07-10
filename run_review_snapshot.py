"""
Export NASDX review snapshot package.

用法:
  python run_review_snapshot.py --risk-profile balanced
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from nasdx.review_snapshot import build_review_snapshot


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

    snapshot = build_review_snapshot(
        risk_profile=args.risk_profile,
        output_dir=args.output_dir,
        refresh=args.refresh,
    )
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
