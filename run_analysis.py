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
    "--risk-profile",
    choices=["conservative", "balanced", "aggressive"],
    default=os.environ.get("NASDX_RISK_PROFILE", "balanced"),
)
args = parser.parse_args()
stock_code = args.stock_code
rounds = args.rounds
risk_profile = args.risk_profile

from nasdx.analyzer import NasdxAnalyzer

print(f'[NASDX] 开始分析 {stock_code}', flush=True)

analyzer = NasdxAnalyzer(
    max_steps=3,
    debate_rounds=rounds,
    agent_delay=0.2,
    battle_delay=0.2,
    risk_profile=risk_profile,
)

try:
    report = analyzer.analyze(stock_code, verbose=True)
    html_path = analyzer.save_report(report, fmt='html')
    json_path = analyzer.save_report(report, fmt='json')
    print(f'\n✅ 分析完成！')
    print(f'   信号: {report.final_signal}  看多占比: {report.bullish_pct:.1f}%')
    print(f'   HTML: {html_path}')
    print(f'   JSON: {json_path}')
except Exception as e:
    import traceback
    print(f'\n❌ 分析失败: {e}')
    traceback.print_exc()
