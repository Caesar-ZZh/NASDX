"""
Calculate money-based NASDX position sizing from the latest local brief.

用法:
  python run_position_sizing.py --capital 100000 --current-etf 10000 --current-stock 5000
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

from nasdx.investment_brief import build_investment_brief
from nasdx.position_sizing import (
    build_position_sizing,
    dumps_position_sizing,
    format_position_sizing,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="把 NASDX 投资路线换算成账户金额上限")
    parser.add_argument(
        "--risk-profile",
        choices=["conservative", "balanced", "aggressive"],
        default=os.environ.get("NASDX_RISK_PROFILE", "balanced"),
    )
    parser.add_argument("--capital", type=float, required=True, help="账户总资金，仅用于本次计算，不落盘")
    parser.add_argument("--current-etf", type=float, default=0.0, help="当前 ETF/基金已投入金额")
    parser.add_argument("--current-stock", type=float, default=0.0, help="当前个股已投入金额")
    parser.add_argument("--current-other", type=float, default=0.0, help="其他已占用仓位金额")
    parser.add_argument("--round-to", type=float, default=100.0, help="金额向下取整粒度，默认 100")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    args = parser.parse_args()

    brief = build_investment_brief(risk_profile=args.risk_profile)
    sizing = build_position_sizing(
        brief,
        total_capital=args.capital,
        current_etf_exposure=args.current_etf,
        current_stock_exposure=args.current_stock,
        current_other_exposure=args.current_other,
        round_to=args.round_to,
    )
    print(dumps_position_sizing(sizing) if args.json else format_position_sizing(sizing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
