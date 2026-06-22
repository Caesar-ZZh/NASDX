"""
NASDX 一键投研工作流

把行情刷新、规则扫描、多 Agent 深度分析串成可选择的执行链。
默认只跑深度分析，避免误触发较慢的全量抓取/扫描。
"""
import argparse
import glob
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from nasdx.investment_brief import build_and_save_investment_brief


ROOT = Path(__file__).parent

WORKFLOW_LABELS = {
    "analysis-only": "仅深度分析",
    "quick": "刷新行情 + ETF50 扫描 + 深度分析",
    "full": "刷新行情 + ETF50/个股扫描 + 深度分析",
}


def _latest(pattern: str) -> str | None:
    files = sorted(glob.glob(str(ROOT / pattern)), key=os.path.getmtime)
    return files[-1] if files else None


def _step_command(
    step: str,
    stock_code: str,
    rounds: int,
    risk_profile: str,
    analysis_mode: str,
) -> List[str]:
    if step == "refresh":
        return [sys.executable, "-u", str(ROOT / "fetch_stock_data.py")]
    if step == "scan_etf50":
        return [sys.executable, "-u", str(ROOT / "scan_etf50.py")]
    if step == "scan_stocks60":
        return [sys.executable, "-u", str(ROOT / "scan_stocks_full.py")]
    if step == "analysis":
        return [
            sys.executable,
            "-u",
            str(ROOT / "run_analysis.py"),
            stock_code,
            "--rounds",
            str(rounds),
            "--risk-profile",
            risk_profile,
            "--mode",
            analysis_mode,
        ]
    raise ValueError(f"未知步骤: {step}")


def _workflow_steps(workflow: str) -> List[Tuple[str, str]]:
    if workflow == "analysis-only":
        return [("analysis", "多 Agent 深度分析")]
    if workflow == "quick":
        return [
            ("refresh", "刷新行情"),
            ("scan_etf50", "ETF50 规则扫描"),
            ("analysis", "多 Agent 深度分析"),
        ]
    if workflow == "full":
        return [
            ("refresh", "刷新行情"),
            ("scan_etf50", "ETF50 规则扫描"),
            ("scan_stocks60", "60只个股规则扫描"),
            ("analysis", "多 Agent 深度分析"),
        ]
    raise ValueError(f"未知工作流: {workflow}")


def _collect_artifacts(stock_code: str) -> Dict[str, str | None]:
    return {
        "data": _latest("stock_data_*.json"),
        "etf50_json": _latest("reports/etf50_[0-9]*_[0-9]*.json"),
        "stocks60_json": _latest("reports/stocks60_*.json"),
        "analysis_html": _latest(f"reports/report_{stock_code}_*.html"),
        "analysis_json": _latest(f"reports/report_{stock_code}_*.json"),
        "investment_brief": _latest("reports/investment_brief_*.md"),
    }


def _run_step(command: List[str], timeout: int | None) -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    proc = subprocess.run(command, cwd=str(ROOT), env=env, timeout=timeout)
    elapsed = time.time() - started
    print(f"[NASDX-WORKFLOW] 步骤耗时 {elapsed / 60:.1f} 分钟", flush=True)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="NASDX 一键投研工作流")
    parser.add_argument("stock_code", nargs="?", default="603501")
    parser.add_argument(
        "--workflow",
        choices=["analysis-only", "quick", "full"],
        default="analysis-only",
        help="analysis-only=只跑深度分析；quick=刷新+ETF扫描+深度；full=刷新+双扫描+深度",
    )
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--analysis-mode",
        choices=["auto", "llm", "rules"],
        default=os.environ.get("NASDX_ANALYSIS_MODE", "auto"),
        help="auto=LLM可用则用LLM，否则规则深度报告；rules=强制无API规则版；llm=强制LLM",
    )
    parser.add_argument(
        "--risk-profile",
        choices=["conservative", "balanced", "aggressive"],
        default=os.environ.get("NASDX_RISK_PROFILE", "balanced"),
    )
    parser.add_argument("--timeout", type=int, default=1800, help="单步骤超时秒数，0 表示不限制")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的步骤，不真正运行")
    parser.add_argument("--skip-portfolio-plan", action="store_true", help="跳过组合级投资路线生成")
    args = parser.parse_args()

    stock_code = args.stock_code.strip().zfill(6)
    steps = _workflow_steps(args.workflow)
    timeout = None if args.timeout == 0 else args.timeout

    print("=" * 72, flush=True)
    print(
        f"[NASDX-WORKFLOW] {WORKFLOW_LABELS[args.workflow]} | 标的 {stock_code} | "
        f"风险 {args.risk_profile} | 轮次 {args.rounds}",
        flush=True,
    )
    print(f"[NASDX-WORKFLOW] 开始时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print("=" * 72, flush=True)

    for idx, (step, label) in enumerate(steps, 1):
        cmd = _step_command(step, stock_code, args.rounds, args.risk_profile, args.analysis_mode)
        print(f"\n[STEP {idx}/{len(steps)}] {label}", flush=True)
        print("[CMD] " + " ".join(str(part) for part in cmd), flush=True)
        if args.dry_run:
            continue
        try:
            code = _run_step(cmd, timeout)
        except subprocess.TimeoutExpired:
            print(f"[NASDX-WORKFLOW] ❌ {label} 超时，已停止后续步骤", flush=True)
            return 2
        if code != 0:
            print(f"[NASDX-WORKFLOW] ❌ {label} 失败，退出码 {code}，已停止后续步骤", flush=True)
            return code

    if args.dry_run:
        print("\n[NASDX-WORKFLOW] DRY-RUN 完成：未执行任何步骤，未刷新产物", flush=True)
        return 0

    if not args.skip_portfolio_plan:
        print("\n[STEP FINAL] 生成组合级投资路线和最终简报", flush=True)
        brief, paths = build_and_save_investment_brief(risk_profile=args.risk_profile)
        print(f"[NASDX-WORKFLOW] 最终简报: {paths['markdown']}", flush=True)
        print(f"[NASDX-WORKFLOW] 方向: {brief.get('primary_bias')}", flush=True)

    print("\n[NASDX-WORKFLOW] 产物路径", flush=True)
    for name, path in _collect_artifacts(stock_code).items():
        if path:
            print(f"  {name}: {path}", flush=True)
    print("\n✅ 工作流完成", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
