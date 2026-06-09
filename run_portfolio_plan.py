"""
Generate NASDX portfolio-level investment roadmap.

用法:
  python run_portfolio_plan.py --risk-profile balanced
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from nasdx.portfolio import build_portfolio_plan, format_portfolio_plan, save_portfolio_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NASDX 投资路线")
    parser.add_argument(
        "--risk-profile",
        choices=["conservative", "balanced", "aggressive"],
        default=os.environ.get("NASDX_RISK_PROFILE", "balanced"),
    )
    parser.add_argument("--max-etfs", type=int, default=5)
    parser.add_argument("--max-stocks", type=int, default=5)
    parser.add_argument("--print", action="store_true", help="同时在终端打印 Markdown")
    args = parser.parse_args()

    plan = build_portfolio_plan(
        risk_profile=args.risk_profile,
        max_etfs=args.max_etfs,
        max_stocks=args.max_stocks,
    )
    paths = save_portfolio_plan(plan)

    print("✅ NASDX 投资路线已生成")
    print(f"   风险画像: {plan['risk_profile_label']}  姿态: {plan['posture']}  总仓位: {plan['allocation']['max_total']}")
    print(f"   Markdown: {paths['markdown']}")
    print(f"   JSON: {paths['json']}")

    if args.print:
        print()
        print(format_portfolio_plan(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
