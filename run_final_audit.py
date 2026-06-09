"""
NASDX final delivery audit.

This script checks the investment-facing delivery chain without requiring a
live LLM call or a fresh market-data pull. It is meant to be run before handing
the project to a user as the "final version" for research guidance.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).parent
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]{20,}")


def main() -> int:
    checks: list[tuple[str, Callable[[], str]]] = [
        ("Python 语法", check_python_syntax),
        ("硬编码 API Key", check_no_hardcoded_api_keys),
        ("Serenity 五维 Agent", check_serenity_agent),
        ("单票决策契约", check_single_name_contract),
        ("组合路线契约", check_portfolio_contract),
        ("一键工作流 Dry-run", check_workflow_dry_run),
        ("网页投资路线入口", check_streamlit_markers),
        ("README/决策文档", check_documentation),
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    print("NASDX final audit")
    print("=" * 72)
    for name, func in checks:
        try:
            detail = func()
        except Exception as exc:  # noqa: BLE001 - audit should report context.
            failed.append((name, str(exc)))
            print(f"[FAIL] {name}: {exc}")
        else:
            passed += 1
            print(f"[PASS] {name}: {detail}")

    print("=" * 72)
    print(f"通过: {passed}  失败: {len(failed)}")
    if failed:
        print("失败项:")
        for name, detail in failed:
            print(f"- {name}: {detail}")
        return 1
    return 0


def check_python_syntax() -> str:
    files = [path for path in _project_files((".py",)) if not _is_ignored(path)]
    failures = []
    for path in files:
        try:
            ast.parse(_read_text(path), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{_rel(path)}:{exc.lineno}:{exc.msg}")
    if failures:
        raise AssertionError("; ".join(failures[:5]))
    return f"{len(files)} 个 Python 文件可解析"


def check_no_hardcoded_api_keys() -> str:
    suffixes = (".py", ".md", ".toml", ".bat")
    hits = []
    for path in _project_files(suffixes):
        if _is_ignored(path):
            continue
        text = _read_text(path)
        for match in SECRET_RE.findall(text):
            if match.lower().startswith("sk-xxxx"):
                continue
            hits.append(f"{_rel(path)}:{match[:8]}...")
    if hits:
        raise AssertionError("发现疑似真实密钥: " + ", ".join(hits[:5]))
    return "未发现疑似真实 sk-* 密钥"


def check_serenity_agent() -> str:
    agent_path = ROOT / "nasdx" / "agents" / "chokepoint.py"
    research_path = ROOT / "nasdx" / "environments" / "research.py"
    if not agent_path.exists():
        raise AssertionError("缺少 nasdx/agents/chokepoint.py")
    research_text = _read_text(research_path)
    if "ChokepointAgent" not in research_text:
        raise AssertionError("研究环境未接入 ChokepointAgent")
    agent_text = _read_text(agent_path)
    required = ["供应链", "需求", "贝叶斯"]
    missing = [word for word in required if word not in agent_text]
    if missing:
        raise AssertionError("ChokepointAgent 缺少关键框架: " + ", ".join(missing))
    return "供应链瓶颈 Agent 已进入研究环境"


def check_single_name_contract() -> str:
    from nasdx.decision import RISK_PROFILES
    from nasdx.schema import FinalReport

    if set(RISK_PROFILES) != {"conservative", "balanced", "aggressive"}:
        raise AssertionError("风险画像不是保守/均衡/进取三档")
    fields = getattr(FinalReport, "model_fields", None) or getattr(FinalReport, "__fields__", {})
    required = {"decision_plan", "data_quality"}
    missing = required - set(fields)
    if missing:
        raise AssertionError("FinalReport 缺少字段: " + ", ".join(sorted(missing)))
    return "风险画像、行动计划、数据状态字段齐全"


def check_portfolio_contract() -> str:
    from nasdx.portfolio import build_portfolio_plan

    required_fields = {
        "allocation",
        "core_candidates",
        "satellite_candidates",
        "watchlist",
        "trim_or_avoid",
        "next_actions",
        "future_scenarios",
        "decision_rules",
        "monitoring_checklist",
        "data_quality",
        "source_files",
        "disclaimer",
    }
    gates = set()
    for profile in ("conservative", "balanced", "aggressive"):
        plan = build_portfolio_plan(risk_profile=profile)
        missing = required_fields - set(plan)
        if missing:
            raise AssertionError(f"{profile} 组合路线缺少字段: {', '.join(sorted(missing))}")
        if len(plan["future_scenarios"]) < 3:
            raise AssertionError(f"{profile} 缺少未来情景推演")
        if len(plan["decision_rules"]) < 5:
            raise AssertionError(f"{profile} 缺少执行规则")
        if len(plan["monitoring_checklist"]) < 3:
            raise AssertionError(f"{profile} 缺少监控清单")
        if "不保证收益" not in str(plan["disclaimer"]):
            raise AssertionError(f"{profile} 缺少研究辅助免责声明")
        etf_path = str(plan["source_files"].get("etf_scan") or "")
        if "etf50_quant" in etf_path:
            raise AssertionError("组合路线误用了 etf50_quant 文件")
        source_files = plan["source_files"]
        active_reports = set((source_files.get("deep_reports") or {}).keys())
        stale_reports = set((source_files.get("stale_deep_reports") or {}).keys())
        overlap = active_reports & stale_reports
        if overlap:
            raise AssertionError("过期深度报告仍被当作可用报告: " + ", ".join(sorted(overlap)))
        deep_quality = plan["data_quality"].get("deep_reports", {})
        if stale_reports and deep_quality.get("stale_count", 0) < len(stale_reports):
            raise AssertionError("深度报告数据状态未统计过期报告")
        gates.add(plan["action_gate"])
        if plan["action_gate"] == "refresh_required":
            allocation = plan["allocation"]
            if allocation.get("max_total") != "0%-10%" or allocation.get("cash_buffer") != "90%-100%":
                raise AssertionError("数据过期时未强制降到观察仓位")
            if not any("run_investment_workflow.py" in item for item in plan["next_actions"]):
                raise AssertionError("数据过期时未给出刷新工作流动作")
    return f"3 档风险画像路线可生成，行动闸门: {', '.join(sorted(gates))}"


def check_workflow_dry_run() -> str:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            sys.executable,
            "run_investment_workflow.py",
            "603501",
            "--workflow",
            "full",
            "--risk-profile",
            "balanced",
            "--rounds",
            "1",
            "--dry-run",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip() or proc.stdout.strip() or f"退出码 {proc.returncode}")
    output = proc.stdout
    required = ["刷新行情", "ETF50", "60只个股", "多 Agent 深度分析", "DRY-RUN"]
    missing = [word for word in required if word not in output]
    if missing:
        raise AssertionError("Dry-run 输出缺少步骤: " + ", ".join(missing))
    return "full 工作流步骤链完整"


def check_streamlit_markers() -> str:
    text = _read_text(ROOT / "app.py")
    required = [
        "投资路线",
        "load_portfolio_latest",
        "build_portfolio_plan",
        "future_scenarios",
        "decision_rules",
        "monitoring_checklist",
        "data_quality",
    ]
    missing = [word for word in required if word not in text]
    if missing:
        raise AssertionError("app.py 缺少页面标记: " + ", ".join(missing))
    return "投资路线页包含生成、情景、规则、监控和数据状态"


def check_documentation() -> str:
    readme = _read_text(ROOT / "README.md")
    doc_path = ROOT / "docs" / "INVESTMENT_DECISION_FRAMEWORK.md"
    if not doc_path.exists():
        raise AssertionError("缺少 docs/INVESTMENT_DECISION_FRAMEWORK.md")
    framework = _read_text(doc_path)
    required_readme = [
        "组合级投资路线",
        "未来情景推演",
        "run_investment_workflow.py",
        "run_portfolio_plan.py",
        "风险画像",
    ]
    missing_readme = [word for word in required_readme if word not in readme]
    if missing_readme:
        raise AssertionError("README 缺少: " + ", ".join(missing_readme))
    required_framework = ["nasdx.decision", "nasdx.portfolio", "nasdx.data_quality", "不保证收益"]
    missing_framework = [word for word in required_framework if word not in framework]
    if missing_framework:
        raise AssertionError("决策文档缺少: " + ", ".join(missing_framework))
    return "运行方式和投资决策边界已记录"


def _project_files(suffixes: Iterable[str]) -> list[Path]:
    suffix_set = tuple(suffixes)
    return [path for path in ROOT.rglob("*") if path.is_file() and path.suffix.lower() in suffix_set]


def _is_ignored(path: Path) -> bool:
    ignored_parts = {".git", ".venv", "venv", "__pycache__", "reports", "models"}
    parts = set(path.relative_to(ROOT).parts)
    return bool(parts & ignored_parts)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


if __name__ == "__main__":
    raise SystemExit(main())
