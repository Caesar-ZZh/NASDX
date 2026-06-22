"""Run NASDX product-readiness checks without persisting secrets."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parent


@dataclass(frozen=True)
class CommandSpec:
    label: str
    argv: list[str]
    timeout: int
    requires_api_key: bool = False


@dataclass(frozen=True)
class CommandResult:
    label: str
    returncode: int
    skipped: bool
    output_tail: str


def build_commands(include_llm_smoke: bool = False) -> list[CommandSpec]:
    commands = [
        CommandSpec(
            label="unit_tests",
            argv=[sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
            timeout=300,
        ),
        CommandSpec(
            label="final_audit",
            argv=[sys.executable, "-B", "run_final_audit.py"],
            timeout=900,
        ),
    ]
    if include_llm_smoke:
        commands.append(
            CommandSpec(
                label="llm_smoke",
                argv=[
                    sys.executable,
                    "-B",
                    "run_analysis.py",
                    "603501",
                    "--mode",
                    "llm",
                    "--risk-profile",
                    "balanced",
                    "--rounds",
                    "1",
                ],
                timeout=900,
                requires_api_key=True,
            )
        )
    return commands


def run_command(spec: CommandSpec) -> CommandResult:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("NASDX_BASE_URL", "https://api.deepseek.com")
    env.setdefault("NASDX_MODEL", "deepseek-v4-pro")

    if spec.requires_api_key and not env.get("NASDX_API_KEY"):
        return CommandResult(
            label=spec.label,
            returncode=0,
            skipped=True,
            output_tail="SKIP: NASDX_API_KEY is not set.",
        )

    proc = subprocess.run(
        spec.argv,
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=spec.timeout,
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return CommandResult(
        label=spec.label,
        returncode=proc.returncode,
        skipped=False,
        output_tail=_tail(output),
    )


def run_readiness(include_llm_smoke: bool = False, fail_fast: bool = False) -> list[CommandResult]:
    results: list[CommandResult] = []
    for spec in build_commands(include_llm_smoke=include_llm_smoke):
        result = run_command(spec)
        results.append(result)
        if fail_fast and result.returncode != 0:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NASDX product-readiness checks.")
    parser.add_argument("--llm-smoke", action="store_true", help="also run one LLM-backed stock analysis when NASDX_API_KEY is set")
    parser.add_argument("--fail-fast", action="store_true", help="stop after the first failed check")
    args = parser.parse_args()

    results = run_readiness(include_llm_smoke=args.llm_smoke, fail_fast=args.fail_fast)
    failed = 0
    for result in results:
        status = "SKIP" if result.skipped else "PASS" if result.returncode == 0 else "FAIL"
        if result.returncode != 0:
            failed += 1
        print(f"[{status}] {result.label}")
        if result.output_tail:
            print(result.output_tail)
            print()

    passed = sum(1 for item in results if item.returncode == 0 and not item.skipped)
    skipped = sum(1 for item in results if item.skipped)
    print(f"summary: passed={passed} failed={failed} skipped={skipped}")
    return 1 if failed else 0


def _tail(text: str, max_lines: int = 80) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


if __name__ == "__main__":
    raise SystemExit(main())
