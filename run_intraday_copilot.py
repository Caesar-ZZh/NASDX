# -*- coding: utf-8 -*-
"""NASDX 盘中驾驶舱 CLI（Issue #67）。

生成一份半小时快照，纯本地、零 LLM 调用、绝不自动下单。

用法:
  python run_intraday_copilot.py                       # 命中检查点才生成
  python run_intraday_copilot.py --force               # 忽略检查点窗口强制生成
  python run_intraday_copilot.py --as-of now --format json
  python run_intraday_copilot.py --watchlist 600519,601318 --news-status verified
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from nasdx.intraday_copilot import (
    build_intraday_snapshot,
    format_intraday_snapshot,
    run_checkpoint,
    save_intraday_snapshot,
)


def _parse_as_of(text: str):
    """接受 'now' 或 ISO 时间字符串，返回传给决策层的时间戳。"""
    if not text or text.strip().lower() == "now":
        return None
    from nasdx.evidence import to_cst

    parsed = to_cst(text.strip())
    if parsed is None:
        raise SystemExit(f"无法解析 --as-of 时间: {text!r}")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NASDX 盘中驾驶舱快照")
    parser.add_argument(
        "--as-of",
        default="now",
        help="快照时间，'now' 或 ISO 字符串（默认 now）。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略检查点窗口校验，强制生成快照（仍绝不下单）。",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "both"],
        default="markdown",
        help="输出格式（默认 markdown）。",
    )
    parser.add_argument(
        "--watchlist",
        default=os.environ.get("NASDX_INTRADAY_WATCHLIST", ""),
        help="逗号分隔的候选标的代码，用于生成候选动作。",
    )
    parser.add_argument(
        "--news-status",
        default=os.environ.get("NASDX_INTRADAY_NEWS_STATUS", ""),
        help="新闻证据状态：verified / stale / unknown。",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="只计算不落盘快照文件。",
    )
    parser.add_argument(
        "--no-portfolio-link",
        action="store_true",
        help="不接入权威组合账本，仅基于行情给出单票建议。",
    )
    args = parser.parse_args()

    moment = _parse_as_of(args.as_of)
    watchlist = [p.strip() for p in args.watchlist.replace(";", ",").split(",") if p.strip()]

    result = run_checkpoint(
        now=moment,
        force=args.force,
        save=not args.no_save,
        watchlist=watchlist or None,
        news_status=args.news_status or None,
        use_ledger=not args.no_portfolio_link,
    )

    if not result.get("ran"):
        print(f"⏭️  跳过：{result.get('reason')}")
        print("   使用 --force 可强制生成快照（仍绝不自动下单）。")
        return 0

    snapshot = result["snapshot"]
    print(f"✅ 盘中快照已生成（{snapshot.get('checkpoint')} 检查点，{result.get('reason')}）")
    print(f"   盘面：{snapshot.get('portfolio', {}).get('market_state')}　"
          f"组合：{snapshot.get('portfolio', {}).get('health')}")
    print(f"   动作数：持仓 {len(snapshot.get('decisions') or [])} + "
          f"候选 {len(snapshot.get('candidates') or [])}　LLM 调用："
          f"{snapshot.get('performance', {}).get('llm_calls')}（系统不会自动下单）")

    if args.format in ("json", "both"):
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
        if args.format == "both":
            print("\n--- JSON ---\n")
            print(payload)
        else:
            print(payload)

    if args.format in ("markdown", "both"):
        print()
        print(format_intraday_snapshot(snapshot))

    paths = result.get("paths") or {}
    if paths:
        print(f"\n   JSON: {paths.get('snapshot')}")
        print(f"   最新: {paths.get('latest')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
