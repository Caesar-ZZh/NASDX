"""
Generate NASDX final investment brief.

用法:
  python run_investment_brief.py --risk-profile balanced
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

from nasdx.investment_brief import build_and_save_investment_brief


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NASDX 最终投资简报")
    parser.add_argument(
        "--risk-profile",
        choices=["conservative", "balanced", "aggressive"],
        default=os.environ.get("NASDX_RISK_PROFILE", "balanced"),
    )
    parser.add_argument("--max-etfs", type=int, default=5)
    parser.add_argument("--max-stocks", type=int, default=5)
    parser.add_argument("--print", action="store_true", help="同时在终端打印 Markdown")
    args = parser.parse_args()

    brief, paths = build_and_save_investment_brief(
        risk_profile=args.risk_profile,
        max_etfs=args.max_etfs,
        max_stocks=args.max_stocks,
    )
    allocation = brief.get("allocation", {})
    print("✅ NASDX 最终投资简报已生成")
    print(
        f"   风险画像: {brief.get('risk_profile_label')}  "
        f"姿态: {brief.get('posture')}  总仓位: {allocation.get('max_total')}"
    )
    print(f"   Markdown: {paths['markdown']}")
    print(f"   JSON: {paths['json']}")

    if args.print:
        print()
        print(brief.get("markdown", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
