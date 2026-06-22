"""
独立运行脚本 — 输出写入日志文件，避免终端超时
用法: python run_analysis.py 603501
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# 强制重置 LLM 单例（确保用最新配置）
import nasdx.llm as llm_mod
llm_mod.LLMClient._instance = None

import logging
logging.basicConfig(level=logging.WARNING)  # 静默第三方日志

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("stock_code", nargs="?", default="603501")
parser.add_argument("--rounds", type=int, default=1)
parser.add_argument(
    "--mode",
    choices=["auto", "llm", "rules"],
    default=os.environ.get("NASDX_ANALYSIS_MODE", "auto"),
    help="auto=LLM可用则用LLM，否则规则深度报告；rules=强制无API规则版；llm=强制LLM",
)
parser.add_argument(
    "--risk-profile",
    choices=["conservative", "balanced", "aggressive"],
    default=os.environ.get("NASDX_RISK_PROFILE", "balanced"),
)
args = parser.parse_args()
stock_code = args.stock_code
rounds = args.rounds
risk_profile = args.risk_profile
analysis_mode = args.mode

from nasdx.analyzer import NasdxAnalyzer
from nasdx.rule_based_analysis import build_rule_based_report, save_rule_based_report

print(f'[NASDX] 开始分析 {stock_code}', flush=True)

def _can_use_llm() -> bool:
    base_url = os.environ.get("NASDX_BASE_URL", "https://api.deepseek.com")
    has_key = bool(os.environ.get("NASDX_API_KEY", "").strip())
    return has_key or "localhost" in base_url or "127.0.0.1" in base_url


def _run_rules(reason: str):
    print(f"[NASDX] 使用规则深度报告：{reason}", flush=True)
    report = build_rule_based_report(stock_code, risk_profile=risk_profile)
    paths = save_rule_based_report(report)
    return report, paths["html"], paths["json"]


def _run_llm():
    analyzer = NasdxAnalyzer(
        max_steps=3,
        debate_rounds=rounds,
        agent_delay=0.2,
        battle_delay=0.2,
        risk_profile=risk_profile,
    )
    report = analyzer.analyze(stock_code, verbose=True)
    html_path = analyzer.save_report(report, fmt='html')
    json_path = analyzer.save_report(report, fmt='json')
    return report, html_path, json_path

try:
    if analysis_mode == "rules":
        report, html_path, json_path = _run_rules("命令行指定 --mode rules")
    elif analysis_mode == "auto" and not _can_use_llm():
        report, html_path, json_path = _run_rules("未检测到 NASDX_API_KEY 或本地 OpenAI 兼容模型")
    else:
        try:
            report, html_path, json_path = _run_llm()
        except Exception as e:
            if analysis_mode == "llm":
                raise
            print(f"[NASDX] LLM 分析失败，自动降级规则版：{e}", flush=True)
            report, html_path, json_path = _run_rules("LLM 调用失败后 fallback")
    print(f'\n✅ 分析完成！')
    mode_label = report.data_quality.get("analysis_mode_label") if report.data_quality else None
    print(f'   模式: {mode_label or "LLM多智能体"}')
    print(f'   信号: {report.final_signal}  看多占比: {report.bullish_pct:.1f}%')
    print(f'   HTML: {html_path}')
    print(f'   JSON: {json_path}')
except Exception as e:
    import traceback
    print(f'\n❌ 分析失败: {e}')
    traceback.print_exc()
