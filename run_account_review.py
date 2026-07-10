"""
Generate NASDX account ledger review from a trade CSV.

用法:
  python run_account_review.py --ledger trades.csv --capital 100000 --print
  python run_account_review.py --template
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from nasdx.account_review import (
    build_account_review,
    build_and_save_account_review,
    dumps_account_review,
    format_account_review,
    template_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NASDX 真实账户复盘")
    parser.add_argument("--ledger", default=None, help="成交 CSV 路径")
    parser.add_argument("--capital", type=float, default=None, help="账户总资金，可选，用于计算仓位占比")
    parser.add_argument("--output-dir", default=None, help="可选输出目录，默认 reports")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    parser.add_argument("--print", action="store_true", help="同时打印复盘正文")
    parser.add_argument("--no-save", action="store_true", help="只计算，不写入 reports")
    parser.add_argument("--template", action="store_true", help="打印成交 CSV 模板")
    args = parser.parse_args()

    if args.template:
        print(template_csv())
        return 0

    if not args.ledger:
        review = build_account_review(None, total_capital=args.capital)
        print(format_account_review(review))
        return 0

    if args.no_save:
        review = build_account_review(args.ledger, total_capital=args.capital)
        print(dumps_account_review(review) if args.json else format_account_review(review))
        return 0

    review, paths = build_and_save_account_review(
        ledger_path=args.ledger,
        total_capital=args.capital,
        output_dir=args.output_dir,
    )
    summary = review.get("summary", {})
    print("✅ NASDX 真实账户复盘已生成")
    print(f"   交易笔数: {review.get('trade_count', 0)}")
    print(
        f"   已实现: {float(summary.get('realized_pnl') or 0):,.2f}  "
        f"浮动: {float(summary.get('unrealized_pnl') or 0):,.2f}  "
        f"市值: {float(summary.get('known_market_value') or 0):,.2f}"
    )
    print(f"   Markdown: {paths['markdown']}")
    print(f"   JSON: {paths['json']}")
    if args.print:
        print()
        print(dumps_account_review(review) if args.json else format_account_review(review))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
