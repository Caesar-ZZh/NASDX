#!/usr/bin/env python3
"""
NASDX 分析入口
用法：
  python analyze.py 000001                     # 分析单只股票
  python analyze.py 000001 --rounds 3          # 3轮辩论
  python analyze.py 000001 --format json       # JSON 输出
  python analyze.py --batch 000001 603501 000063  # 批量分析
  python analyze.py --all-sectors              # 分析全部6板块
"""
import argparse
import os
import sys
from pathlib import Path

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser(
        description="NASDX — A股多智能体分析系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python analyze.py 603501              # 分析韦尔股份
  python analyze.py 603501 --rounds 3  # 3轮辩论
  python analyze.py 603501 --format json --output ./reports
  python analyze.py --batch 603501 000063 600900
        """,
    )

    # 主要参数
    parser.add_argument("stock_code", nargs="?", help="股票代码，如 000001")
    parser.add_argument("--batch", nargs="+", metavar="CODE", help="批量分析多只股票")
    parser.add_argument("--all-sectors", action="store_true", help="分析全部6板块所有股票")

    # 选项
    parser.add_argument("--rounds", type=int, default=2, help="辩论轮数（默认2）")
    parser.add_argument("--max-steps", type=int, default=3, help="每Agent最大步数（默认3）")
    parser.add_argument("--format", choices=["html", "json", "both"], default="html", help="输出格式")
    parser.add_argument("--output", type=str, default=None, help="报告输出目录")
    parser.add_argument("--no-verbose", action="store_true", help="静默模式")
    parser.add_argument(
        "--no-portfolio-link",
        action="store_true",
        help=(
            "不接入权威组合账本（#66）。默认接入：账本已初始化时，持仓快照参与"
            "组合闸门与盘中缓存失效；账本未初始化时行为与未接入一致。"
        ),
    )

    # API 配置（也可以用环境变量）
    parser.add_argument("--api-key", type=str, help="LLM API Key（也可设 NASDX_API_KEY 环境变量）")
    parser.add_argument("--base-url", type=str, help="API Base URL（默认 DeepSeek）")
    parser.add_argument("--model", type=str, help="模型名称（默认 deepseek-chat）")

    args = parser.parse_args()

    # 注入 API 配置
    if args.api_key:
        os.environ["NASDX_API_KEY"] = args.api_key
    if args.base_url:
        os.environ["NASDX_BASE_URL"] = args.base_url
    if args.model:
        os.environ["NASDX_MODEL"] = args.model

    # 检查 API Key（优先环境变量，其次用 llm.py 中的默认值）
    api_key = os.environ.get("NASDX_API_KEY", "")
    if not api_key:
        from nasdx.llm import API_KEY as DEFAULT_KEY
        if not DEFAULT_KEY or DEFAULT_KEY == "your-api-key-here":
            print("❌ 请设置 API Key：")
            print("   export NASDX_API_KEY=sk-xxxx")
            sys.exit(1)

    # 初始化分析器
    from nasdx.analyzer import NasdxAnalyzer
    analyzer = NasdxAnalyzer(
        max_steps=args.max_steps,
        debate_rounds=args.rounds,
        output_dir=args.output,
        link_portfolio=not args.no_portfolio_link,
    )

    verbose = not args.no_verbose
    fmt = args.format

    def save_and_print(report, code: str):
        paths = []
        if fmt in ("html", "both"):
            p = analyzer.save_report(report, fmt="html")
            paths.append(p)
        if fmt in ("json", "both"):
            p = analyzer.save_report(report, fmt="json")
            paths.append(p)
        if paths:
            print(f"\n📁 报告已保存：")
            for p in paths:
                print(f"   {p}")

    # ─── 批量模式 ──────────────────────────
    if args.batch:
        reports = analyzer.analyze_batch(args.batch, verbose=verbose)
        for code, report in reports.items():
            save_and_print(report, code)
        print(f"\n✅ 批量分析完成，共 {len(reports)} 只")

    # ─── 全板块模式 ────────────────────────
    elif args.all_sectors:
        from nasdx.data_loader import load_latest_data
        data = load_latest_data()
        all_codes = []
        for sector in data.get("sectors", []):
            for s in sector.get("stocks", []) + sector.get("etfs", []):
                all_codes.append(s["code"])

        print(f"📋 全板块模式：共 {len(all_codes)} 只标的")
        reports = analyzer.analyze_batch(all_codes, verbose=verbose)
        for code, report in reports.items():
            save_and_print(report, code)
        print(f"\n✅ 全板块分析完成")

    # ─── 单只股票 ──────────────────────────
    elif args.stock_code:
        report = analyzer.analyze(args.stock_code, verbose=verbose)
        save_and_print(report, args.stock_code)

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
