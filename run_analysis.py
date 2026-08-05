"""
独立运行脚本 — 输出写入日志文件，避免终端超时
用法: python run_analysis.py 603501
"""
import sys, os, json
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
parser.add_argument(
    "--depth",
    choices=["full", "intraday", "refresh"],
    default=os.environ.get("NASDX_ANALYSIS_DEPTH", "full"),
    help=(
        "full=完整多智能体分析（默认，行为不变）；"
        "intraday=盘中增量，复用缓存的慢变量结论，只刷新失效的行情维度，不重跑辩论；"
        "refresh=只重跑被失效规则命中的维度。与 --mode 相互独立。"
    ),
)
parser.add_argument(
    "--no-cache",
    action="store_true",
    help="禁用分析快照缓存（既不读也不写），等价于每次强制完整重算",
)
parser.add_argument(
    "--fact-check",
    action="store_true",
    help="开启 quant 事实校验：对最终结论做数值一致性检查（真相源经 NASDX_FACT_GROUND JSON 提供）",
)
parser.add_argument(
    "--no-portfolio-link",
    action="store_true",
    help=(
        "不接入权威组合账本（#66）。默认接入：账本已初始化时，持仓快照参与组合闸门"
        "与盘中缓存失效；账本未初始化时行为与未接入一致。"
    ),
)
parser.add_argument(
    "--decision-mode",
    default=None,
    help=(
        "覆盖落库决策记录的 mode（#74）。默认按 --mode 推导：rules→rules，"
        "其余→full。消融运行可传 full-no_battle 之类变体。"
    ),
)
args = parser.parse_args()
stock_code = args.stock_code
rounds = args.rounds
risk_profile = args.risk_profile
analysis_mode = args.mode
fact_check = args.fact_check
analysis_depth = args.depth
use_cache = not args.no_cache
link_portfolio = not args.no_portfolio_link

from nasdx.analyzer import NasdxAnalyzer
from nasdx.rule_based_analysis import build_rule_based_report, save_rule_based_report
from nasdx.debate_review import summarize_counter_argument, format_counter_argument_block
from nasdx.decision_log import log_decision
from nasdx.memory import record_decision
from nasdx.fact_check import check_consistency
from nasdx.decision_wiring import (
    market_snapshot_hash_from_data,
    record_report_if_enabled,
)
from nasdx.data_loader import get_stock_data, load_latest_data

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
        depth=analysis_depth,
        use_cache=use_cache,
        link_portfolio=link_portfolio,
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
    perf = getattr(report, "performance", None) or {}
    if perf:
        print(
            f'   深度: {perf.get("effective_depth", analysis_depth)}'
            f'  LLM调用: {perf.get("llm_call_count", "-")} 次'
            f'  耗时: {perf.get("total_elapsed_ms", "-")} ms'
        )
        if perf.get("cache_hit_dimensions"):
            print(f'   复用维度: {", ".join(perf["cache_hit_dimensions"])}（未重新核验）')
        if perf.get("degraded_reason"):
            print(f'   ⚠️ 深度回退: {perf["degraded_reason"]}')
    print(f'   HTML: {html_path}')
    print(f'   JSON: {json_path}')

    # —— TradingAgents 借鉴机制接入（薄附加，高可逆）——
    try:
        transcript = getattr(report, "battle_transcript", None)
        if os.environ.get("NASDX_DEBATE_REVIEW", "1") != "0" and transcript:
            rendered = format_counter_argument_block(
                summarize_counter_argument(transcript, getattr(report, "votes", None))
            )
            if rendered:
                print("\n" + rendered, flush=True)
        log_decision(
            "analysis", "finalize",
            inputs={"stock_code": stock_code, "mode": analysis_mode},
            output={"signal": getattr(report, "final_signal", None),
                    "bullish_pct": getattr(report, "bullish_pct", None)},
        )
        record_decision(
            stock_code, getattr(report, "date", ""),
            getattr(report, "final_signal", "neutral"),
            float(getattr(report, "confidence", 0) or 0),
            getattr(report, "summary", "") or "", source="run_analysis",
        )
        # —— #74 决策记录落库（fail-open，不影响主流程）——
        try:
            market_data = load_latest_data()
            stock = get_stock_data(market_data, stock_code)
            ref_price = float(stock["close"]) if stock and stock.get("close") is not None else None
            wiring_mode = args.decision_mode or ("rules" if analysis_mode == "rules" else "full")
            record_report_if_enabled(
                report,
                reference_price=ref_price,
                mode=wiring_mode,
                industry=(stock or {}).get("sector_name") or (stock or {}).get("industry") or "",
                market_snapshot_hash=market_snapshot_hash_from_data(market_data),
            )
        except Exception as wire_e:  # noqa: BLE001
            print(f"[NASDX] 决策记录落库跳过：{wire_e}", flush=True)
        if fact_check:
            ground: dict = {}
            raw = os.environ.get("NASDX_FACT_GROUND", "")
            if raw:
                try:
                    ground = json.loads(raw)
                except json.JSONDecodeError:
                    pass
            warns = check_consistency(getattr(report, "summary", "") or "", ground)
            if warns:
                print("[NASDX][事实校验] " + "；".join(warns), flush=True)
    except Exception as hook_e:  # noqa: BLE001
        print(f"[NASDX] 借鉴机制接入跳过：{hook_e}", flush=True)
except Exception as e:
    import traceback
    print(f'\n❌ 分析失败: {e}')
    traceback.print_exc()
