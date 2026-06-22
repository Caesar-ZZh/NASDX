"""
Execution queue for NASDX investment guidance.

This layer turns candidate audits into a practical pre-market / intraday /
post-market queue. It is deliberately conservative: it never upgrades a
candidate beyond the audit status, and it marks missing reports or manual
checks as blockers rather than as trade permission.
"""
from __future__ import annotations

from typing import Any, Dict, List


def build_execution_queue(plan: Dict[str, Any], audits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build an ordered execution queue from a portfolio plan and audits."""
    action_gate = plan.get("action_gate", "refresh_required")
    risk_profile = plan.get("risk_profile", "balanced")
    allocation = plan.get("allocation", {})
    queue: List[Dict[str, Any]] = []

    queue.append(_data_gate_task(action_gate, risk_profile, audits))
    if action_gate != "normal":
        queue.append(_intraday_data_guard_task(action_gate))
    for audit in audits:
        status = audit.get("status_code")
        if status == "trial_candidate":
            queue.append(_trial_task(audit, allocation))
        elif status == "needs_report":
            queue.append(_report_task(audit, risk_profile))
        elif status == "watch":
            queue.append(_watch_task(audit))
        elif status == "avoid":
            queue.append(_avoid_task(audit))
        elif status == "refresh_data":
            queue.append(_refresh_blocker_task(audit, risk_profile))

    queue.append(_post_market_task(risk_profile, audits))
    return sorted(queue, key=lambda item: (_stage_order(item.get("stage", "")), item.get("priority", 99)))


def _data_gate_task(action_gate: str, risk_profile: str, audits: List[Dict[str, Any]]) -> Dict[str, Any]:
    target = _first_code(audits) or "603501"
    if action_gate == "normal":
        return {
            "stage": "盘前",
            "priority": 1,
            "target": "组合数据闸门",
            "decision": "可进入复核流程",
            "action": "确认 action_gate 仍为 normal，行情和扫描覆盖率未降级。",
            "condition": "若网页数据状态变成过期、低覆盖或缺失，暂停所有试错。",
            "blocker": "数据闸门不是 normal 时，候选只观察。",
            "command": f"python run_portfolio_plan.py --risk-profile {risk_profile}",
        }
    return {
        "stage": "盘前",
        "priority": 1,
        "target": "组合数据闸门",
        "decision": "先刷新数据",
        "action": "先刷新行情和扫描，再重新生成投资路线。",
        "condition": "刷新后 action_gate 恢复 normal，才进入候选复核。",
        "blocker": "当前数据闸门未打开。",
        "command": f"python run_investment_workflow.py {target} --workflow quick --analysis-mode rules --risk-profile {risk_profile}",
    }


def _intraday_data_guard_task(action_gate: str) -> Dict[str, Any]:
    return {
        "stage": "盘中",
        "priority": 5,
        "target": "盘中数据闸门",
        "decision": "不新增仓位",
        "action": "数据闸门恢复前不新增、不放大试错，只监控已有持仓风险。",
        "condition": "只有刷新行情、扫描和最终简报后 action_gate 恢复 normal，才重新评估候选。",
        "blocker": f"数据闸门当前为 {action_gate}。",
        "command": "",
    }


def _trial_task(audit: Dict[str, Any], allocation: Dict[str, Any]) -> Dict[str, Any]:
    cap = allocation.get("etf_budget") if audit.get("type") == "ETF" else allocation.get("single_stock_cap")
    return {
        "stage": "盘中",
        "priority": 10,
        "target": audit.get("candidate", ""),
        "decision": "小仓试错前复核",
        "action": audit.get("execution_bias", "仅在复核通过后分批试错。"),
        "condition": "公告/成交/流动性人工复核通过，且不追高、回踩不破关键均线。",
        "blocker": "任一人工复核未通过、放量破位、跌出前排或风险红灯转强时不动。",
        "command": "",
        "position_reference": audit.get("report_position_band") or cap or "",
    }


def _report_task(audit: Dict[str, Any], risk_profile: str) -> Dict[str, Any]:
    code = audit.get("code", "")
    return {
        "stage": "盘前",
        "priority": 20,
        "target": audit.get("candidate", ""),
        "decision": "先补深度报告",
        "action": "补跑规则深度报告；报告未生成前不进入试错。",
        "condition": "报告转为 bullish 且动作不是回避/减仓，才可进入试错复核。",
        "blocker": "缺少可用深度报告。",
        "command": f"python run_analysis.py {code} --mode rules --risk-profile {risk_profile}" if code else "",
    }


def _watch_task(audit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": "盘中",
        "priority": 30,
        "target": audit.get("candidate", ""),
        "decision": "观察等待",
        "action": audit.get("execution_bias", "只观察，等待证据增强。"),
        "condition": "连续保持前排、深度信号增强、人工复核通过后再评估。",
        "blocker": "当前深度信号未达到试错条件。",
        "command": "",
    }


def _avoid_task(audit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": "盘中",
        "priority": 40,
        "target": audit.get("candidate", ""),
        "decision": "回避/降级",
        "action": "从试错池剔除，已有持仓按风控规则复核是否降仓。",
        "condition": "重新生成报告后风险红灯解除，才可恢复观察。",
        "blocker": "深度信号或报告动作偏防守。",
        "command": "",
    }


def _refresh_blocker_task(audit: Dict[str, Any], risk_profile: str) -> Dict[str, Any]:
    code = audit.get("code") or "603501"
    return {
        "stage": "盘前",
        "priority": 15,
        "target": audit.get("candidate", ""),
        "decision": "先修数据",
        "action": "刷新行情、扫描和简报；刷新前不升级候选。",
        "condition": "数据闸门恢复 normal 后再看候选审计。",
        "blocker": "行情或扫描质量不足。",
        "command": f"python run_investment_workflow.py {code} --workflow quick --analysis-mode rules --risk-profile {risk_profile}",
    }


def _post_market_task(risk_profile: str, audits: List[Dict[str, Any]]) -> Dict[str, Any]:
    target = _first_code([item for item in audits if item.get("status_code") == "trial_candidate"]) or _first_code(audits) or "603501"
    return {
        "stage": "盘后",
        "priority": 90,
        "target": "复盘刷新",
        "decision": "重新生成明日路线",
        "action": "收盘后刷新路线和最终简报，确认试错、观察和补报告名单是否变化。",
        "condition": "若候选跌出前排、风险信号转空或人工复核失败，次日降级。",
        "blocker": "不使用隔夜旧简报扩大仓位。",
        "command": f"python run_investment_workflow.py {target} --workflow quick --analysis-mode rules --risk-profile {risk_profile}",
    }


def _first_code(audits: List[Dict[str, Any]]) -> str:
    for audit in audits:
        code = audit.get("code")
        if code:
            return str(code)
    return ""


def _stage_order(stage: str) -> int:
    order = {"盘前": 0, "盘中": 1, "盘后": 2}
    return order.get(stage, 9)
