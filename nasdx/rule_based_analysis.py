"""
Rule-based deep report generator.

This module produces the same FinalReport contract as the LLM analyzer, but
without calling an external model. It keeps NASDX usable when no API key or
local Ollama model is configured.
"""
from __future__ import annotations

import json
import glob
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from nasdx.data_loader import get_stock_data, load_latest_data
from nasdx.data_quality import assess_data_quality
from nasdx.decision import build_decision_plan, format_decision_plan
from nasdx.history_store import record_report_history
from nasdx.paths import get_reports_dir
from nasdx.report import generate_html_report
from nasdx.schema import AnalysisResult, BattleVote, FinalReport

def build_rule_based_report(
    stock_code: str,
    data: Dict[str, Any] | None = None,
    risk_profile: str = "balanced",
) -> FinalReport:
    """Build a deterministic single-name report from the latest local data."""
    data = data or load_latest_data()
    stock = get_stock_data(data, stock_code)
    source_data = data
    if not stock:
        stock, source_data = _stock_from_latest_scan(stock_code)
    if not stock:
        raise ValueError(f"股票 {stock_code} 不在监控池或最新扫描候选中，请检查代码")

    stock_name = stock.get("name", stock_code)
    data_quality = dict(assess_data_quality(_quality_source(source_data)))
    data_quality["analysis_mode"] = "rules"
    data_quality["analysis_mode_label"] = "规则深度报告"

    research = {
        "technical": _technical_result(stock),
        "fund_flow": _fund_flow_result(stock),
        "risk": _risk_result(stock, data_quality),
        "sector": _sector_result(stock, data),
        "chokepoint": _chokepoint_result(stock_code, stock),
    }
    transcript, votes, bullish_pct = _battle_from_rules(stock_code, stock_name, research)
    synthesis = _synthesis_result(stock_code, stock_name, research, bullish_pct)

    final_signal = synthesis.signal
    if final_signal == "neutral" and bullish_pct >= 60:
        final_signal = "bullish"

    decision_plan = build_decision_plan(
        stock_code=stock_code,
        stock_name=stock_name,
        final_signal=final_signal,
        bullish_pct=bullish_pct,
        research_results=research,
        synthesis=synthesis,
        risk_profile=risk_profile,
        data_quality=data_quality,
    )

    research_with_synthesis = dict(research)
    research_with_synthesis["synthesis"] = synthesis

    return FinalReport(
        stock_code=stock_code,
        stock_name=stock_name,
        date=_report_date(source_data),
        research_results=research_with_synthesis,
        battle_transcript=transcript,
        votes=votes,
        final_signal=final_signal,
        bullish_pct=bullish_pct,
        summary=synthesis.conclusion,
        operation_advice=format_decision_plan(decision_plan),
        decision_plan=decision_plan,
        data_quality=data_quality,
    )


def save_rule_based_report(report: FinalReport, output_dir: str | Path | None = None) -> Dict[str, str]:
    """Save JSON and HTML report files using the normal NASDX naming contract."""
    out_dir = Path(output_dir) if output_dir else get_reports_dir(create=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"report_{report.stock_code}_{report.date}"
    html_path = base.with_suffix(".html")
    json_path = base.with_suffix(".json")
    payload = _report_payload(report)
    html_path.write_text(generate_html_report(report), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    record_report_history(
        report.stock_code,
        report.date,
        payload,
        source_path=json_path,
        generated_at=report.date,
    )
    return {"html": str(html_path), "json": str(json_path)}


def technical_signal(stock: Dict[str, Any]) -> AnalysisResult:
    """Deterministic technical verdict for one stock dict (no LLM, no I/O).

    Public entry point so the intraday copilot (#67) can reuse the exact same
    rules instead of re-implementing them.
    """
    return _technical_result(stock)


def fund_flow_signal(stock: Dict[str, Any]) -> AnalysisResult:
    """Deterministic fund-flow verdict for one stock dict (no LLM, no I/O)."""
    return _fund_flow_result(stock)


def _technical_result(stock: Dict[str, Any]) -> AnalysisResult:
    ind = stock.get("indicators", {}) or {}
    scan_score = _optional_num(stock.get("scan_score"))
    scan_signal = stock.get("scan_signal")
    scan_reasons = [str(item) for item in stock.get("scan_reasons", []) if item]
    close = _num(ind.get("close") or ind.get("current_price"))
    change = _num(ind.get("change_pct") or ind.get("price_change_pct"))
    ma5 = _num(ind.get("ma5"))
    ma20 = _num(ind.get("ma20"))
    ma60 = _num(ind.get("ma60"))
    dif = _num(ind.get("dif") or ind.get("macd_dif"))
    dea = _num(ind.get("dea") or ind.get("macd_dea"))
    macd = _num(ind.get("macd_bar"))
    rsi = _num(ind.get("rsi") or ind.get("rsi14"))
    vol_ratio = _num(ind.get("vol_ratio"), default=1.0)
    up_days = _num(ind.get("up_days_20"), default=10.0)

    score = 0.0
    points: List[str] = []
    if scan_score is not None:
        if scan_score >= 80:
            score += 1.1
        elif scan_score >= 65:
            score += 0.45
        elif scan_score <= 40:
            score -= 0.9
        if scan_signal == "bullish":
            score += 0.6
        elif scan_signal == "bearish":
            score -= 0.8
        points.extend(scan_reasons[:2])
    if ma5 and ma20:
        if ma5 > ma20:
            score += 0.9
            points.append(f"MA5({ma5:.2f})高于MA20({ma20:.2f})，短线趋势占优")
        else:
            score -= 0.9
            points.append(f"MA5({ma5:.2f})低于MA20({ma20:.2f})，短线趋势偏弱")
    if close and ma20:
        if close > ma20:
            score += 0.6
            points.append(f"收盘价{close:.2f}站上MA20")
        else:
            score -= 0.6
            points.append(f"收盘价{close:.2f}低于MA20")
    if ma20 and ma60:
        score += 0.5 if ma20 > ma60 else -0.5
    if macd:
        score += 0.7 if macd > 0 else -0.7
        points.append(f"MACD柱{macd:+.4f}")
    if dif is not None and dea is not None:
        score += 0.3 if dif > dea else -0.3
    if rsi:
        if 45 <= rsi <= 68:
            score += 0.4
            points.append(f"RSI={rsi:.0f}处于健康区间")
        elif rsi > 75:
            score -= 0.7
            points.append(f"RSI={rsi:.0f}进入超买区")
        elif rsi < 30:
            score -= 0.2
            points.append(f"RSI={rsi:.0f}进入超卖区，需等反转确认")
    if vol_ratio > 1.2 and change > 0:
        score += 0.4
        points.append(f"放量上涨，量比{vol_ratio:.2f}")
    elif vol_ratio > 1.5 and change < 0:
        score -= 0.5
        points.append(f"放量下跌，量比{vol_ratio:.2f}")
    elif vol_ratio < 0.7:
        score -= 0.2
        points.append(f"缩量运行，量比{vol_ratio:.2f}")
    score += 0.3 if up_days >= 12 else -0.3 if up_days <= 8 else 0.0

    signal, confidence = _signal_from_score(score)
    conclusion = (
        f"规则技术面评分为{score:+.2f}。"
        f"{'；'.join(points[:4]) if points else '指标缺少明显方向'}。"
        "该信号只描述短中期量价状态，入场仍需等回踩或突破确认。"
    )
    return AnalysisResult(
        agent_name="rule_technical_agent",
        dimension="technical",
        conclusion=conclusion,
        signal=signal,
        confidence=confidence,
        key_points=points[:5] or ["技术指标方向不明显"],
        raw_data_summary=f"close={close}; ma5={ma5}; ma20={ma20}; macd={macd}; rsi={rsi}",
    )


def _fund_flow_result(stock: Dict[str, Any]) -> AnalysisResult:
    flow = stock.get("fund_flow") or []
    if not flow:
        return AnalysisResult(
            agent_name="rule_fund_flow_agent",
            dimension="fund_flow",
            conclusion="当前本地数据未提供可用资金流，资金维度不参与加分；执行前仍需复核成交额和盘口承接。",
            signal="neutral",
            confidence=0.0,
            key_points=["资金流数据缺失，维持中性"],
            raw_data_summary="no_fund_flow",
        )

    recent = flow[-5:]
    total = sum(_num(row.get("主力净流入-净额")) for row in recent)
    positive_days = sum(1 for row in recent if _num(row.get("主力净流入-净额")) > 0)
    avg_pct = sum(_num(row.get("主力净流入-净占比")) for row in recent) / max(len(recent), 1)
    last = recent[-1]
    last_change = _num(last.get("涨跌幅"))

    score = 0.0
    score += 1.0 if total > 0 else -1.0
    score += 0.45 if positive_days >= 3 else -0.45 if positive_days <= 1 else 0.0
    score += 0.4 if avg_pct > 3 else -0.4 if avg_pct < -3 else 0.0
    if total > 0 and last_change < 0:
        score += 0.25
    elif total < 0 and last_change > 0:
        score -= 0.25

    signal, confidence = _signal_from_score(score)
    points = [
        f"近5日主力净流入{total / 1e8:+.2f}亿元",
        f"净流入天数{positive_days}/{len(recent)}",
        f"平均主力净占比{avg_pct:+.1f}%",
    ]
    conclusion = (
        f"资金流规则评分为{score:+.2f}。{points[0]}，{points[1]}，{points[2]}。"
        "若价格上涨但资金持续流出，应降低追高权重。"
    )
    return AnalysisResult(
        agent_name="rule_fund_flow_agent",
        dimension="fund_flow",
        conclusion=conclusion,
        signal=signal,
        confidence=confidence,
        key_points=points,
        raw_data_summary=f"total_main_5d={total}; positive_days={positive_days}; avg_pct={avg_pct:.2f}",
    )


def _risk_result(stock: Dict[str, Any], data_quality: Dict[str, Any]) -> AnalysisResult:
    ind = stock.get("indicators", {}) or {}
    close = _num(ind.get("close") or ind.get("current_price"))
    ma20 = _num(ind.get("ma20"))
    rsi = _num(ind.get("rsi") or ind.get("rsi14"))
    upper = _num(ind.get("boll_upper"))
    lower = _num(ind.get("boll_lower"))
    vol_ratio = _num(ind.get("vol_ratio"), default=1.0)
    change = _num(ind.get("change_pct") or ind.get("price_change_pct"))

    score = 0.0
    points: List[str] = []
    if data_quality.get("action_gate") != "normal":
        score -= 0.9
        points.append(data_quality.get("message", "数据闸门未完全打开"))
    if rsi:
        if rsi > 75:
            score -= 1.2
            points.append(f"RSI={rsi:.0f}严重超买")
        elif rsi > 68:
            score -= 0.5
            points.append(f"RSI={rsi:.0f}偏热")
        elif rsi < 30:
            score += 0.3
            points.append(f"RSI={rsi:.0f}超卖，风险释放但需反转确认")
    if upper and lower and close:
        pos = (close - lower) / (upper - lower + 1e-9)
        if pos > 0.9:
            score -= 0.7
            points.append("价格靠近布林上轨，追高风险上升")
        elif pos < 0.2:
            score += 0.4
            points.append("价格靠近布林下轨，短线风险已有释放")
    if close and ma20 and close < ma20:
        score -= 0.45
        points.append("价格低于MA20，趋势风险仍在")
    if vol_ratio > 1.6 and change < 0:
        score -= 0.7
        points.append("放量下跌，需防止承接不足")

    signal, confidence = _signal_from_score(score, bull_threshold=0.7, bear_threshold=-0.8)
    conclusion = (
        f"风险规则评分为{score:+.2f}。"
        f"{'；'.join(points[:4]) if points else '未出现明确风险极值'}。"
        "风险维度为仓位闸门，不用于单独追涨。"
    )
    return AnalysisResult(
        agent_name="rule_risk_agent",
        dimension="risk",
        conclusion=conclusion,
        signal=signal,
        confidence=confidence,
        key_points=points[:5] or ["技术风险中性"],
        raw_data_summary=f"rsi={rsi}; close={close}; ma20={ma20}; vol_ratio={vol_ratio}",
    )


def _sector_result(stock: Dict[str, Any], data: Dict[str, Any]) -> AnalysisResult:
    sector_name = stock.get("sector_name", "")
    sector = _find_sector(data, sector_name)
    rows = list((sector or {}).get("stocks", [])) + list((sector or {}).get("etfs", []))
    changes = [
        _num((item.get("indicators") or {}).get("change_pct"))
        for item in rows
        if (item.get("indicators") or {}).get("change_pct") is not None
    ]
    if not changes:
        scan_score = _optional_num(stock.get("scan_score"))
        scan_signal = stock.get("scan_signal")
        if scan_score is not None:
            score = (scan_score - 50) / 25
            signal, confidence = _signal_from_score(score)
            return AnalysisResult(
                agent_name="rule_sector_agent",
                dimension="sector",
                conclusion=(
                    f"{sector_name or '扫描候选'}来自最新扫描榜单，规则分{scan_score:.1f}，"
                    f"扫描信号为{scan_signal or 'neutral'}。该板块维度以榜单排序替代横向板块比较。"
                ),
                signal=signal,
                confidence=confidence,
                key_points=[f"扫描分{scan_score:.1f}", f"扫描信号：{scan_signal or 'neutral'}"],
                raw_data_summary=f"scan_score={scan_score}; scan_signal={scan_signal}",
            )
        return AnalysisResult(
            agent_name="rule_sector_agent",
            dimension="sector",
            conclusion=f"{sector_name or '未知'}板块缺少可比较涨跌数据，板块维度维持中性。",
            signal="neutral",
            confidence=0.0,
            key_points=[f"所属板块：{sector_name or '未知'}"],
        )

    up_ratio = sum(1 for value in changes if value > 0) / len(changes)
    avg_change = sum(changes) / len(changes)
    target_change = _num((stock.get("indicators") or {}).get("change_pct"))
    score = (up_ratio - 0.5) * 3 + avg_change / 3
    score += 0.25 if target_change > avg_change else -0.15
    signal, confidence = _signal_from_score(score)
    points = [
        f"{sector_name}板块上涨占比{up_ratio:.0%}",
        f"板块平均涨跌{avg_change:+.2f}%",
        f"标的当日涨跌{target_change:+.2f}%",
    ]
    conclusion = (
        f"板块规则评分为{score:+.2f}。{points[0]}，{points[1]}；"
        f"标的相对板块{'更强' if target_change > avg_change else '偏弱或跟随'}。"
    )
    return AnalysisResult(
        agent_name="rule_sector_agent",
        dimension="sector",
        conclusion=conclusion,
        signal=signal,
        confidence=confidence,
        key_points=points,
        raw_data_summary=f"sector={sector_name}; up_ratio={up_ratio:.4f}; avg_change={avg_change:.4f}",
    )


def _chokepoint_result(stock_code: str, stock: Dict[str, Any]) -> AnalysisResult:
    sector = stock.get("sector_name", "")
    name = stock.get("name", stock_code)
    note = stock.get("note", "")
    demand = _infer_demand_shock(sector, note)
    node = _infer_supply_node(sector, name, note)
    text = f"{sector} {name} {note}"
    high_signal_topic = any(
        word in text
        for word in ("通信", "光模块", "CPO", "半导体设备", "特高压", "算力", "机器人", "军工", "科技")
    )
    ind = stock.get("indicators", {}) or {}
    close = _num(ind.get("close"))
    ma20 = _num(ind.get("ma20"))
    macd = _num(ind.get("macd_bar"))
    if high_signal_topic and close and ma20 and close > ma20 and macd > 0:
        signal = "bullish"
        confidence = 0.56
    else:
        signal = "neutral"
        confidence = 0.45 if high_signal_topic else 0.35
    points = [
        f"需求冲击：{demand}",
        f"候选卡点：{node}",
        "公告/订单/客户/产能证据仍需人工核验",
    ]
    conclusion = (
        f"Serenity规则框架给出的候选需求冲击为：{demand}；候选供应链节点为：{node}。"
        "当前项目没有直接接入公告、财报和新闻源，因此只把它作为研究假设，"
        "升级仓位前必须核验订单、客户、产能和财务映射。"
    )
    return AnalysisResult(
        agent_name="rule_chokepoint_agent",
        dimension="chokepoint",
        conclusion=conclusion,
        signal=signal,
        confidence=confidence,
        key_points=points,
        raw_data_summary=f"demand={demand}; node={node}",
    )


def _synthesis_result(
    stock_code: str,
    stock_name: str,
    research: Dict[str, AnalysisResult],
    bullish_pct: float,
) -> AnalysisResult:
    weights = {
        "technical": 0.32,
        "fund_flow": 0.18,
        "risk": 0.2,
        "sector": 0.15,
        "chokepoint": 0.15,
    }
    score = 0.0
    used_weight = 0.0
    for dim, weight in weights.items():
        result = research.get(dim)
        if not result or result.confidence <= 0:
            continue
        score += _signal_value(result.signal) * result.confidence * weight
        used_weight += weight
    norm_score = score / used_weight if used_weight else 0.0
    signal, confidence = _signal_from_score(norm_score, bull_threshold=0.18, bear_threshold=-0.18)
    evidence = [
        f"{dim}:{result.signal}/{result.confidence:.0%}"
        for dim, result in research.items()
        if result
    ]
    conclusion = (
        f"{stock_code} {stock_name} 的规则综合分为{norm_score:+.2f}，规则投票看多占比{bullish_pct:.1f}%。"
        f"核心证据为：{'；'.join(evidence)}。"
        "结论用于候选升级前的深度复核，不构成无条件买卖指令。"
    )
    return AnalysisResult(
        agent_name="rule_synthesis_agent",
        dimension="synthesis",
        conclusion=conclusion,
        signal=signal,
        confidence=confidence,
        key_points=[f"规则综合分{norm_score:+.2f}", f"看多占比{bullish_pct:.1f}%"],
        raw_data_summary=f"weighted_score={norm_score:.4f}",
    )


def _battle_from_rules(
    stock_code: str,
    stock_name: str,
    research: Dict[str, AnalysisResult],
) -> Tuple[List[str], List[BattleVote], float]:
    tech = research["technical"].signal
    flow = research["fund_flow"].signal
    risk = research["risk"].signal
    sector = research["sector"].signal
    chokepoint = research["chokepoint"].signal
    votes = [
        BattleVote(
            agent_name="短线交易员",
            vote="bullish" if tech == "bullish" and risk != "bearish" else "bearish" if tech == "bearish" else "neutral",
            reasoning="跟随技术趋势，并受风险维度约束。",
        ),
        BattleVote(
            agent_name="中线投资者",
            vote="bullish" if sector == "bullish" and flow != "bearish" else "bearish" if sector == "bearish" else "neutral",
            reasoning="以板块强弱和资金承接为主。",
        ),
        BattleVote(
            agent_name="风险控制官",
            vote="bearish" if risk == "bearish" else "neutral",
            reasoning="风险红灯优先于机会信号。",
        ),
        BattleVote(
            agent_name="Serenity研究员",
            vote="bullish" if chokepoint == "bullish" and risk != "bearish" else "neutral",
            reasoning="供应链卡点只作为待核验假设。",
        ),
        BattleVote(
            agent_name="组合经理",
            vote=_majority_vote([tech, flow, risk, sector, chokepoint]),
            reasoning="综合多维信号决定是否升级候选。",
        ),
    ]
    bullish_pct = sum(1 for vote in votes if vote.vote == "bullish") / len(votes) * 100
    bull_points = _collect_points(research, "bullish")
    bear_points = _collect_points(research, "bearish")
    transcript = [
        f"多头规则观点：{bull_points or '暂未形成强看多证据'}",
        f"空头规则观点：{bear_points or '暂未形成强看空证据'}",
        f"裁判规则意见：{stock_code} {stock_name} 看多占比{bullish_pct:.1f}%，需要按数据闸门和仓位纪律执行。",
    ]
    return transcript, votes, bullish_pct


def _majority_vote(signals: Iterable[str]) -> str:
    values = list(signals)
    bullish = values.count("bullish")
    bearish = values.count("bearish")
    if bullish > bearish and bullish >= 2:
        return "bullish"
    if bearish > bullish and bearish >= 2:
        return "bearish"
    return "neutral"


def _collect_points(research: Dict[str, AnalysisResult], signal: str) -> str:
    points = []
    for result in research.values():
        if result.signal == signal and result.key_points:
            points.append(result.key_points[0])
    return "；".join(points[:3])


def _signal_from_score(
    score: float,
    bull_threshold: float = 1.0,
    bear_threshold: float = -1.0,
) -> Tuple[str, float]:
    if score >= bull_threshold:
        signal = "bullish"
    elif score <= bear_threshold:
        signal = "bearish"
    else:
        signal = "neutral"
    confidence = min(0.86, max(0.45, 0.48 + abs(score) * 0.12))
    return signal, round(confidence, 3)


def _signal_value(signal: str) -> float:
    if signal == "bullish":
        return 1.0
    if signal == "bearish":
        return -1.0
    return 0.0


def _find_sector(data: Dict[str, Any], sector_name: str) -> Dict[str, Any] | None:
    for sector in data.get("sectors", []):
        if sector.get("name") == sector_name:
            return sector
    return None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _stock_from_latest_scan(stock_code: str) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
    reports_dir = get_reports_dir()
    for pattern in ("etf50_[0-9]*_[0-9]*.json", "stocks60_*.json"):
        files = sorted(glob.glob(str(reports_dir / pattern)), key=os.path.getmtime, reverse=True)
        for path_str in files:
            path = Path(path_str)
            try:
                scan = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows = scan.get("results") or scan.get("top3") or []
            for row in rows:
                if str(row.get("code", "")) != stock_code:
                    continue
                stock = _stock_from_scan_row(row)
                source = {
                    "date": scan.get("date") or row.get("data_date"),
                    "generated_at": scan.get("generated_at") or scan.get("datetime"),
                    "scan_path": str(path),
                }
                return stock, source
    return None, {}


def _stock_from_scan_row(row: Dict[str, Any]) -> Dict[str, Any]:
    category = row.get("category") or row.get("sector") or "扫描候选"
    indicators = {
        "close": row.get("close") if row.get("close") is not None else row.get("spot_price"),
        "change_pct": row.get("chg") if row.get("chg") is not None else row.get("spot_chg"),
        "ma5": row.get("ma5"),
        "ma10": row.get("ma10"),
        "ma20": row.get("ma20"),
        "ma60": row.get("ma60"),
        "dif": row.get("dif"),
        "dea": row.get("dea"),
        "macd_bar": row.get("macd_bar"),
        "rsi": row.get("rsi"),
        "boll_upper": row.get("boll_upper"),
        "boll_lower": row.get("boll_lower"),
        "vol_ratio": row.get("vol_ratio"),
        "up_days_20": row.get("up_days_20"),
    }
    indicators = {key: value for key, value in indicators.items() if value is not None}
    return {
        "code": str(row.get("code", "")),
        "name": row.get("name") or str(row.get("code", "")),
        "note": "来自最新扫描榜单",
        "type": "etf" if row.get("category") else "stock",
        "sector_name": category,
        "indicators": indicators,
        "fund_flow": [],
        "main_net_3d": [],
        "data_source": row.get("data_source") or "scan_result",
        "data_date": row.get("data_date"),
        "scan_score": row.get("score") if row.get("score") is not None else row.get("quant_score"),
        "scan_signal": row.get("signal") or row.get("factor_signal"),
        "scan_reasons": row.get("reasons") or [],
    }


def _quality_source(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": data.get("date"),
        "generated_at": data.get("generated_at") or data.get("datetime"),
    }


def _report_date(data: Dict[str, Any]) -> str:
    raw_date = str(data.get("date") or "")
    if raw_date:
        return raw_date.replace("-", "")
    raw_dt = data.get("generated_at") or data.get("datetime")
    if raw_dt:
        try:
            return datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00")).strftime("%Y%m%d")
        except Exception:
            pass
    return datetime.now().strftime("%Y%m%d")


def _infer_demand_shock(sector: str, note: str) -> str:
    text = f"{sector} {note}"
    if any(k in text for k in ("通信", "光模块", "光器件", "CPO", "5G")):
        return "AI数据中心网络升级、CPO/高速光模块、通信基础设施扩容"
    if any(k in text for k in ("半导体设备", "刻蚀", "PVD", "CMP", "涂胶", "清洗")):
        return "国内晶圆厂扩产、先进制程迭代、半导体设备国产化"
    if any(k in text for k in ("半导体", "芯片", "晶圆", "传感器", "Flash", "DRAM", "MCU")):
        return "AI芯片、汽车电子、国产替代与先进封装带来的半导体需求"
    if any(k in text for k in ("电力", "电网", "特高压", "变压", "新能源")):
        return "AI数据中心用电、特高压、电网升级和新能源消纳"
    if any(k in text for k in ("AI", "算力", "服务器", "GPU")):
        return "AI训练/推理算力扩张和国产算力基础设施建设"
    if any(k in text for k in ("军工", "航空", "航发", "导弹", "雷达")):
        return "国防装备更新、军工供应链国产化和高可靠零部件需求"
    return "所属主题需求冲击尚不明确，需要结合公告和行业资料核验"


def _infer_supply_node(sector: str, name: str, note: str) -> str:
    text = f"{sector} {name} {note}"
    rules = [
        (("光模块", "光器件", "光纤", "通信", "CPO"), "光模块/光器件/激光器/光纤等数据中心网络节点"),
        (("刻蚀", "PVD", "CMP", "涂胶", "显影", "清洗", "设备"), "晶圆制造设备与关键工艺装备节点"),
        (("晶圆", "代工", "特色工艺", "射频", "功率"), "晶圆代工、特色工艺或功率器件制造产能节点"),
        (("传感器", "CMOS"), "图像传感器及汽车/消费电子感知器件节点"),
        (("Flash", "DRAM", "MCU", "存储"), "存储、控制器或嵌入式处理器节点"),
        (("电网", "特高压", "调度", "变压", "电力"), "电网调度、变压器、开关设备或电力基础设施节点"),
        (("算力", "服务器", "GPU", "AI"), "AI服务器、国产算力或基础设施集成节点"),
        (("航空", "航发", "连接器", "雷达", "军工"), "高可靠军工零部件、航空装备或国产替代节点"),
    ]
    for keywords, node in rules:
        if any(k in text for k in keywords):
            return node
    return "候选供应链节点待进一步拆解"


def _report_payload(report: FinalReport) -> Dict[str, Any]:
    return {
        "stock_code": report.stock_code,
        "stock_name": report.stock_name,
        "date": report.date,
        "analysis_mode": "rules",
        "final_signal": report.final_signal,
        "bullish_pct": report.bullish_pct,
        "summary": report.summary,
        "operation_advice": report.operation_advice,
        "decision_plan": report.decision_plan,
        "data_quality": report.data_quality,
        "research_results": {
            dim: {
                "agent_name": result.agent_name,
                "signal": result.signal,
                "confidence": result.confidence,
                "key_points": result.key_points,
                "conclusion": result.conclusion[:900],
                "raw_data_summary": result.raw_data_summary,
            }
            for dim, result in report.research_results.items()
            if result
        },
        "votes": [
            {"agent": vote.agent_name, "vote": vote.vote, "reasoning": vote.reasoning}
            for vote in report.votes
        ],
        "battle_transcript": report.battle_transcript,
    }
