"""
Candidate-level evidence audit for NASDX investment briefs.

The audit keeps every candidate recommendation inspectable: which local data
supports it, which checks pass automatically, and which checks still require a
human review before any real trade.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def build_candidate_audits(plan: Dict[str, Any], max_each: int = 3) -> List[Dict[str, Any]]:
    """Build candidate audit cards from a portfolio plan."""
    candidates = plan.get("core_candidates", [])[:max_each] + plan.get("satellite_candidates", [])[:max_each]
    source_files = plan.get("source_files", {})
    report_paths = source_files.get("deep_reports") or {}
    data_quality = plan.get("data_quality", {})
    allocation = plan.get("allocation", {})
    action_gate = plan.get("action_gate", "refresh_required")

    audits: List[Dict[str, Any]] = []
    for item in candidates:
        code = str(item.get("code", ""))
        report = _load_json(report_paths.get(code))
        report_path = report_paths.get(code)
        decision = report.get("decision_plan", {}) if isinstance(report, dict) else {}
        scan_quality = data_quality.get("etf_scan" if item.get("asset_type") == "ETF" else "stock_scan", {})
        market_quality = data_quality.get("market_data", {})

        checklist = _checklist(
            item=item,
            report=report,
            decision=decision,
            action_gate=action_gate,
            scan_quality=scan_quality,
            market_quality=market_quality,
            allocation=allocation,
        )
        status = _audit_status(item, report, decision, action_gate, scan_quality)
        audits.append(
            {
                "code": code,
                "name": item.get("name", code),
                "candidate": f"{code} {item.get('name', code)}".strip(),
                "type": item.get("asset_type", ""),
                "audit_status": status["label"],
                "status_code": status["code"],
                "execution_bias": status["execution_bias"],
                "score": item.get("adjusted_score"),
                "scan_signal": item.get("signal"),
                "deep_signal": item.get("deep_signal") or "missing",
                "bullish_pct": item.get("bullish_pct"),
                "report_action": decision.get("action", ""),
                "report_position_band": decision.get("position_band", ""),
                "report_confidence": decision.get("confidence"),
                "report_path": report_path,
                "key_evidence": _key_evidence(item, decision, scan_quality, market_quality),
                "risk_flags": list(decision.get("risk_flags") or [])[:3],
                "manual_checks": [
                    check["name"]
                    for check in checklist
                    if check.get("status") == "manual"
                ],
                "blocking_flags": [
                    check["evidence"]
                    for check in checklist
                    if check.get("status") == "fail"
                ],
                "checklist": checklist,
            }
        )
    return audits


def _load_json(path_value: Any) -> Dict[str, Any]:
    if not path_value:
        return {}
    try:
        path = Path(str(path_value))
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _checklist(
    item: Dict[str, Any],
    report: Dict[str, Any],
    decision: Dict[str, Any],
    action_gate: str,
    scan_quality: Dict[str, Any],
    market_quality: Dict[str, Any],
    allocation: Dict[str, Any],
) -> List[Dict[str, str]]:
    deep_signal = item.get("deep_signal") or "missing"
    scan_gate = scan_quality.get("action_gate")
    market_gate = market_quality.get("action_gate")
    has_report = bool(report)
    report_action = str(decision.get("action") or item.get("report_action") or "")
    risk_flags = list(decision.get("risk_flags") or [])

    checks = [
        {
            "name": "数据闸门",
            "status": "pass" if action_gate == "normal" and scan_gate == "normal" and market_gate == "normal" else "fail",
            "evidence": _join_nonempty(
                scan_quality.get("message"),
                market_quality.get("message"),
            )
            or "行情/扫描数据状态未评估。",
        },
        {
            "name": "扫描排序",
            "status": "pass" if float(item.get("adjusted_score") or 0) >= 65 else "warn",
            "evidence": (
                f"调整分 {float(item.get('adjusted_score') or 0):.1f}，"
                f"扫描信号 {item.get('signal') or 'unknown'}；{item.get('reason') or '无补充理由'}"
            ),
        },
        {
            "name": "深度报告",
            "status": _report_check_status(has_report, deep_signal, report_action),
            "evidence": _report_evidence(has_report, deep_signal, decision),
        },
        {
            "name": "风险红灯",
            "status": "warn" if risk_flags else "pass",
            "evidence": "；".join(str(flag) for flag in risk_flags[:2]) or "未发现强风险红灯，仍需按仓位纪律执行。",
        },
        {
            "name": "公告/财报/重大事项",
            "status": "manual",
            "evidence": "项目未接入官方公告源；真实下单前必须人工核验最新公告、财报、停复牌和重大事项。",
        },
        {
            "name": "成交/折溢价/流动性",
            "status": "manual",
            "evidence": (
                "ETF 需核验折溢价和成交额；个股需核验成交放大是否异常、盘口流动性和交易成本。"
            ),
        },
        {
            "name": "仓位纪律",
            "status": "pass" if allocation.get("max_total") else "warn",
            "evidence": (
                f"总仓位上限 {allocation.get('max_total', '未设置')}，"
                f"ETF预算 {allocation.get('etf_budget', '未设置')}，"
                f"个股预算 {allocation.get('stock_budget', '未设置')}，"
                f"单一个股上限 {allocation.get('single_stock_cap', '未设置')}。"
            ),
        },
    ]
    return checks


def _report_check_status(has_report: bool, deep_signal: str, report_action: str = "") -> str:
    if not has_report or deep_signal == "missing":
        return "fail"
    if _is_avoid_report_action(report_action):
        return "fail"
    if deep_signal == "bearish":
        return "fail"
    if deep_signal == "neutral":
        return "warn"
    return "pass"


def _report_evidence(has_report: bool, deep_signal: str, decision: Dict[str, Any]) -> str:
    if not has_report:
        return "缺少可用深度报告；候选不能从观察升级为试错。"
    parts = [
        f"深度信号 {deep_signal}",
        f"动作 {decision.get('action', '未给出')}",
        f"仓位 {decision.get('position_band', '未给出')}",
    ]
    bullish_pct = decision.get("bullish_pct")
    if isinstance(bullish_pct, (int, float)):
        parts.append(f"看多票 {bullish_pct:.1f}%")
    confidence = decision.get("confidence")
    if isinstance(confidence, (int, float)):
        parts.append(f"置信度 {confidence:.2f}")
    return "，".join(parts) + "。"


def _audit_status(
    item: Dict[str, Any],
    report: Dict[str, Any],
    decision: Dict[str, Any],
    action_gate: str,
    scan_quality: Dict[str, Any],
) -> Dict[str, str]:
    deep_signal = item.get("deep_signal") or "missing"
    report_action = str(decision.get("action") or item.get("report_action") or "")
    if action_gate != "normal" or scan_quality.get("action_gate") != "normal":
        return {
            "code": "refresh_data",
            "label": "先修数据",
            "execution_bias": "刷新行情/扫描前不升级为试错。",
        }
    if not report or deep_signal == "missing":
        return {
            "code": "needs_report",
            "label": "先补深度报告",
            "execution_bias": "先补深度分析，再判断是否进入试错池。",
        }
    if deep_signal == "bearish" or _is_avoid_report_action(report_action):
        return {
            "code": "avoid",
            "label": "回避/降级",
            "execution_bias": "深度信号或报告动作偏防守，默认不升级。",
        }
    if deep_signal == "neutral":
        return {
            "code": "watch",
            "label": "观察等待",
            "execution_bias": "只观察，等趋势或基本面证据增强。",
        }
    return {
        "code": "trial_candidate",
        "label": "小仓试错候选",
        "execution_bias": "仅在人工复核通过且满足入场条件后分批试错。",
    }


def _key_evidence(
    item: Dict[str, Any],
    decision: Dict[str, Any],
    scan_quality: Dict[str, Any],
    market_quality: Dict[str, Any],
) -> List[str]:
    evidence = [
        f"扫描: 调整分 {float(item.get('adjusted_score') or 0):.1f}，信号 {item.get('signal') or 'unknown'}",
        f"深度: {item.get('deep_signal') or 'missing'}，动作 {decision.get('action', '未给出')}",
        f"数据: {scan_quality.get('status', 'unknown')} / {market_quality.get('status', 'unknown')}",
    ]
    if item.get("reason"):
        evidence.append(f"理由: {item.get('reason')}")
    return evidence[:4]


def _join_nonempty(*values: Any) -> str:
    return "；".join(str(value) for value in values if value)


def _is_avoid_report_action(action: str) -> bool:
    return "回避" in action or "减仓" in action
