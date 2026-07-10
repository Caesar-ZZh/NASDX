"""
Final investment brief layer for NASDX.

This module turns the portfolio roadmap into a compact, auditable brief. It is
rule-based by design, so the project can still guide research direction when no
LLM API key is configured.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from nasdx.candidate_audit import build_candidate_audits
from nasdx.execution_queue import build_execution_queue
from nasdx.external_review import build_external_review_pack
from nasdx.history_store import record_artifact
from nasdx.paths import get_reports_dir
from nasdx.portfolio import build_portfolio_plan, save_portfolio_plan

def build_investment_brief(
    risk_profile: str = "balanced",
    max_etfs: int = 5,
    max_stocks: int = 5,
) -> Dict[str, Any]:
    """Build a concise investment-direction brief from the latest local data."""
    plan = build_portfolio_plan(risk_profile=risk_profile, max_etfs=max_etfs, max_stocks=max_stocks)
    return brief_from_plan(plan)


def brief_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a portfolio plan into a reader-facing brief."""
    allocation = plan.get("allocation", {})
    core = plan.get("core_candidates", [])
    satellite = plan.get("satellite_candidates", [])
    data_quality = plan.get("data_quality", {})
    action_gate = plan.get("action_gate", "refresh_required")

    primary_bias = _primary_bias(plan)
    exposure_action = _exposure_action(action_gate, allocation)
    candidate_audits = build_candidate_audits(plan, max_each=3)
    execution_queue = build_execution_queue(plan, candidate_audits)
    external_review_pack = build_external_review_pack(candidate_audits)
    evidence = _evidence_snapshot(plan, candidate_audits)

    brief = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_plan_generated_at": plan.get("generated_at"),
        "risk_profile": plan.get("risk_profile"),
        "risk_profile_label": plan.get("risk_profile_label"),
        "action_gate": action_gate,
        "posture": plan.get("posture"),
        "primary_bias": primary_bias,
        "exposure_action": exposure_action,
        "allocation": allocation,
        "priority_routes": _priority_routes(core, satellite),
        "candidate_playbook": _candidate_playbook(core, satellite, action_gate),
        "candidate_audits": candidate_audits,
        "execution_queue": execution_queue,
        "external_review_pack": external_review_pack,
        "future_scenarios": plan.get("future_scenarios", []),
        "risk_controls": _risk_controls(plan),
        "next_actions": plan.get("next_actions", []),
        "monitoring_checklist": plan.get("monitoring_checklist", []),
        "data_evidence": evidence,
        "source_files": plan.get("source_files", {}),
        "disclaimer": (
            "研究辅助，不构成任何收益承诺或直接下单指令；执行前必须复核最新行情、"
            "公告、流动性、账户风险承受能力和交易成本。"
        ),
    }
    brief["markdown"] = format_investment_brief(brief)
    return brief


def save_investment_brief(
    brief: Dict[str, Any],
    output_dir: str | Path | None = None,
) -> Dict[str, str]:
    """Save markdown and JSON versions of the investment brief."""
    out_dir = Path(output_dir) if output_dir else get_reports_dir(create=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    payload = {k: v for k, v in brief.items() if k != "markdown"}
    md_path = out_dir / f"investment_brief_{stamp}.md"
    json_path = out_dir / f"investment_brief_{stamp}.json"
    latest_md = out_dir / "investment_brief_latest.md"
    latest_json = out_dir / "investment_brief_latest.json"

    md_path.write_text(brief.get("markdown") or format_investment_brief(brief), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    record_artifact(
        "investment_brief",
        "latest",
        payload,
        generated_at=payload.get("generated_at"),
        source_path=json_path,
    )
    return {
        "markdown": str(md_path),
        "json": str(json_path),
        "latest_markdown": str(latest_md),
        "latest_json": str(latest_json),
    }


def build_and_save_investment_brief(
    risk_profile: str = "balanced",
    max_etfs: int = 5,
    max_stocks: int = 5,
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Refresh the portfolio plan and save the final brief."""
    plan = build_portfolio_plan(risk_profile=risk_profile, max_etfs=max_etfs, max_stocks=max_stocks)
    save_portfolio_plan(plan)
    brief = brief_from_plan(plan)
    return brief, save_investment_brief(brief)


def format_investment_brief(brief: Dict[str, Any]) -> str:
    """Render the brief as compact Markdown."""
    allocation = brief.get("allocation", {})
    lines = [
        "# NASDX 最终投资简报",
        "",
        f"- 生成时间：{brief.get('generated_at', '')}",
        f"- 风险画像：{brief.get('risk_profile_label', '')}",
        f"- 当前姿态：{brief.get('posture', '')}",
        f"- 投资方向：{brief.get('primary_bias', '')}",
        f"- 仓位动作：{brief.get('exposure_action', '')}",
        f"- 总仓位上限：{allocation.get('max_total', '')}",
        f"- ETF / 个股预算：{allocation.get('etf_budget', '')} / {allocation.get('stock_budget', '')}",
        "",
        "## 优先路线",
        "",
        _format_route_table(brief.get("priority_routes", [])),
        "",
        "## 候选执行剧本",
        "",
        _format_playbook_table(brief.get("candidate_playbook", [])),
        "",
        "## 候选证据核查",
        "",
        _format_audit_table(brief.get("candidate_audits", [])),
        "",
        "## 执行队列",
        "",
        _format_execution_queue_table(brief.get("execution_queue", [])),
        "",
        "## 外部复核包",
        "",
        _format_external_review_table(brief.get("external_review_pack", [])),
        "",
        "## 未来情景",
        "",
        _format_scenario_table(brief.get("future_scenarios", [])),
        "",
        "## 风险控制",
        "",
        *[f"- {item}" for item in brief.get("risk_controls", [])],
        "",
        "## 下一步",
        "",
        *[f"- {item}" for item in brief.get("next_actions", [])],
        "",
        "## 数据证据",
        "",
        *[f"- {item}" for item in brief.get("data_evidence", [])],
        "",
        f"> {brief.get('disclaimer', '')}",
    ]
    return "\n".join(lines)


def _primary_bias(plan: Dict[str, Any]) -> str:
    gate = plan.get("action_gate")
    posture = plan.get("posture")
    core = plan.get("core_candidates", [])
    satellite = plan.get("satellite_candidates", [])
    trial_core = _names(_trial_candidates(core[:3]))
    watch_core = _names(_watch_or_pending_candidates(core[:3]))
    trial_satellite = _names(_trial_candidates(satellite[:3]))
    if gate == "refresh_required":
        return "数据闸门关闭，当前不做方向放大，先刷新再判断。"
    if gate == "position_cap":
        focus = trial_core or watch_core or "ETF主线"
        return f"{posture}，只保留 {focus} 小仓验证或观察，暂不放大个股。"
    parts = [f"{posture}"]
    if trial_core:
        parts.append(f"以 {trial_core} 做ETF试错主线")
    if watch_core:
        parts.append(f"{watch_core} 只观察或先补深度报告")
    if trial_satellite:
        parts.append(f"用 {trial_satellite} 做个股卫星增强")
    return "，".join(parts) + "。"


def _exposure_action(action_gate: str, allocation: Dict[str, Any]) -> str:
    if action_gate == "refresh_required":
        return "只观察或极小仓验证；刷新行情和扫描前不扩大仓位。"
    if action_gate == "position_cap":
        return "仓位上限受限，优先保留现金，等待数据质量或趋势确认。"
    return f"按 {allocation.get('max_total', '')} 总仓位上限分批推进，不一次性打满。"


def _priority_routes(core: List[Dict[str, Any]], satellite: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "route": "ETF主线",
            "focus": _route_focus(core[:3]),
            "role": "用已核查ETF试错确认方向，缺报告或中性候选只观察",
            "budget": "优先使用 ETF 预算，分批验证趋势",
        },
        {
            "route": "个股卫星",
            "focus": _route_focus(satellite[:3]),
            "role": "增强弹性，但必须通过深度报告和公告复核",
            "budget": "单一标的不得突破组合规则上限",
        },
        {
            "route": "现金缓冲",
            "focus": "未满足入场条件时保留现金",
            "role": "应对回撤、数据失效和主题拥挤",
            "budget": "按风险画像保留现金缓冲",
        },
    ]


def _candidate_playbook(
    core: List[Dict[str, Any]],
    satellite: List[Dict[str, Any]],
    action_gate: str,
) -> List[Dict[str, Any]]:
    candidates = core[:3] + satellite[:3]
    playbook = []
    for item in candidates:
        asset_type = item.get("asset_type", "")
        code_name = f"{item.get('code')} {item.get('name')}"
        playbook.append(
            {
                "candidate": code_name,
                "type": asset_type,
                "deep_signal": item.get("deep_signal") or "missing",
                "priority": item.get("action", ""),
                "entry_condition": _entry_condition(item, action_gate),
                "invalidation": _invalidation_condition(item),
                "review": _review_instruction(item),
            }
        )
    return playbook


def _entry_condition(item: Dict[str, Any], action_gate: str) -> str:
    if action_gate != "normal":
        return "等待数据闸门恢复 normal，再按趋势条件评估。"
    if item.get("action") == "回避/减仓":
        return "当前报告动作或风险信号要求回避/减仓，不作为入场候选。"
    deep_signal = item.get("deep_signal")
    if item.get("asset_type") == "ETF":
        if deep_signal == "bullish":
            return "连续保持前排，深度报告不转空，折溢价/成交未异常，回踩不破关键均线。"
        return "连续保持前排，折溢价/成交未异常，回踩不破关键均线。"
    if deep_signal == "bullish":
        return "扫描继续前排，深度报告偏多，公告/财报和资金面未亮红灯。"
    return "扫描继续前排，深度报告不转空，风险维度和资金面未亮红灯。"


def _invalidation_condition(item: Dict[str, Any]) -> str:
    if item.get("action") == "回避/减仓":
        return "深度报告动作转向试错/布局且风险红灯解除前，不升级。"
    if item.get("signal") == "bearish":
        return "当前已偏弱，默认回避；重新转强前不升级。"
    if item.get("deep_signal") == "bearish":
        return "深度报告偏空，默认不升级；重新生成报告转中性/偏多前只观察。"
    return "跌出候选前排、风险信号转空、放量破位或数据质量降级。"


def _review_instruction(item: Dict[str, Any]) -> str:
    if item.get("action") == "回避/减仓":
        return "报告动作或风险维度不支持试错；仅观察，等待重新生成报告后再评估。"
    deep_signal = item.get("deep_signal")
    if deep_signal == "bullish":
        return "已有深度报告偏多；执行前复核公告/成交，满足入场条件后小仓试错。"
    if deep_signal == "neutral":
        return "已有深度报告中性；只保留观察，等趋势或基本面证据增强再试错。"
    if deep_signal == "bearish":
        return "已有深度报告偏空；不升级为试错，等待信号修复。"
    return "先跑深度分析/公告复核，再从观察升级为试错。"


def _risk_controls(plan: Dict[str, Any]) -> List[str]:
    allocation = plan.get("allocation", {})
    rules = [
        f"总仓位不超过 {allocation.get('max_total', '')}，单一个股不超过 {allocation.get('single_stock_cap', '')}。",
        "只在 action_gate 为 normal 且数据覆盖率达标时，把榜单用于加仓候选。",
        "ETF 先行、个股后置；个股必须经过深度分析和公告/财报复核。",
        "若行情过期、扫描低覆盖、风险维度转空或主题拥挤，先降仓再找新机会。",
    ]
    return rules


def _evidence_snapshot(plan: Dict[str, Any], candidate_audits: List[Dict[str, Any]] | None = None) -> List[str]:
    evidence = []
    for label, quality in plan.get("data_quality", {}).items():
        msg = quality.get("message")
        if msg:
            evidence.append(f"{label}: {msg}")
    sources = plan.get("source_files", {})
    if sources.get("etf_scan"):
        evidence.append(f"ETF扫描文件: {sources['etf_scan']}")
    if sources.get("stock_scan"):
        evidence.append(f"个股扫描文件: {sources['stock_scan']}")
    audits = candidate_audits or []
    if audits:
        evidence.append(f"候选证据核查: {len(audits)} 个候选已逐项审计。")
    return evidence


def _names(items: List[Dict[str, Any]]) -> str:
    return "、".join(f"{item.get('code')} {item.get('name')}" for item in items if item.get("code"))


def _trial_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("deep_signal") == "bullish" and item.get("action") != "回避/减仓"
    ]


def _watch_or_pending_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("deep_signal") != "bullish" and item.get("action") != "回避/减仓"
    ]


def _route_focus(items: List[Dict[str, Any]]) -> str:
    trial = _names(_trial_candidates(items))
    watch = _names(_watch_or_pending_candidates(items))
    if trial and watch:
        return f"试错：{trial}；观察/待补：{watch}"
    if trial:
        return f"试错：{trial}"
    if watch:
        return f"观察/待补：{watch}"
    return "暂无"


def _format_route_table(items: List[Dict[str, Any]]) -> str:
    rows = ["| 路线 | 重点 | 作用 | 预算纪律 |", "|---|---|---|---|"]
    for item in items:
        rows.append(
            "| {route} | {focus} | {role} | {budget} |".format(
                route=str(item.get("route", "")).replace("|", "/"),
                focus=str(item.get("focus", "")).replace("|", "/"),
                role=str(item.get("role", "")).replace("|", "/"),
                budget=str(item.get("budget", "")).replace("|", "/"),
            )
        )
    return "\n".join(rows)


def _format_playbook_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "暂无候选。"
    rows = ["| 候选 | 类型 | 深度信号 | 优先级 | 入场条件 | 失效条件 | 复核 |", "|---|---|---|---|---|---|---|"]
    for item in items:
        rows.append(
            "| {candidate} | {type} | {deep_signal} | {priority} | {entry_condition} | {invalidation} | {review} |".format(
                candidate=str(item.get("candidate", "")).replace("|", "/"),
                type=str(item.get("type", "")).replace("|", "/"),
                deep_signal=str(item.get("deep_signal", "")).replace("|", "/"),
                priority=str(item.get("priority", "")).replace("|", "/"),
                entry_condition=str(item.get("entry_condition", "")).replace("|", "/"),
                invalidation=str(item.get("invalidation", "")).replace("|", "/"),
                review=str(item.get("review", "")).replace("|", "/"),
            )
        )
    return "\n".join(rows)


def _format_audit_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "暂无候选审计。"
    rows = ["| 候选 | 审计结论 | 核心证据 | 待人工复核 | 阻断项 |", "|---|---|---|---|---|"]
    for item in items:
        rows.append(
            "| {candidate} | {status} | {evidence} | {manual} | {blockers} |".format(
                candidate=str(item.get("candidate", "")).replace("|", "/"),
                status=str(item.get("audit_status", "")).replace("|", "/"),
                evidence="；".join(str(x).replace("|", "/") for x in item.get("key_evidence", [])[:3]),
                manual="；".join(str(x).replace("|", "/") for x in item.get("manual_checks", [])[:3]) or "无",
                blockers="；".join(str(x).replace("|", "/") for x in item.get("blocking_flags", [])[:2]) or "无",
            )
        )
    return "\n".join(rows)


def _format_execution_queue_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "暂无执行队列。"
    rows = ["| 阶段 | 对象 | 决策 | 动作 | 条件 | 阻断 | 命令 |", "|---|---|---|---|---|---|---|"]
    for item in items:
        rows.append(
            "| {stage} | {target} | {decision} | {action} | {condition} | {blocker} | {command} |".format(
                stage=str(item.get("stage", "")).replace("|", "/"),
                target=str(item.get("target", "")).replace("|", "/"),
                decision=str(item.get("decision", "")).replace("|", "/"),
                action=str(item.get("action", "")).replace("|", "/"),
                condition=str(item.get("condition", "")).replace("|", "/"),
                blocker=str(item.get("blocker", "")).replace("|", "/"),
                command=str(item.get("command", "")).replace("|", "/") or "无",
            )
        )
    return "\n".join(rows)


def _format_external_review_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "暂无外部复核包。"
    rows = ["| 候选 | 复核闸门 | 必须通过时间 | 必查项 | 来源入口 | 失败动作 |", "|---|---|---|---|---|---|"]
    for item in items:
        links = []
        for link in item.get("source_links", [])[:3]:
            label = str(link.get("label", "")).replace("|", "/")
            url = str(link.get("url", "")).replace("|", "%7C")
            links.append(f"[{label}]({url})")
        rows.append(
            "| {candidate} | {gate} | {before} | {checks} | {links} | {failure} |".format(
                candidate=str(item.get("candidate", "")).replace("|", "/"),
                gate=str(item.get("review_gate", "")).replace("|", "/"),
                before=str(item.get("must_pass_before", "")).replace("|", "/"),
                checks="；".join(str(x).replace("|", "/") for x in item.get("required_checks", [])[:3]),
                links="；".join(links) or "无",
                failure=str(item.get("failure_action", "")).replace("|", "/"),
            )
        )
    return "\n".join(rows)


def _format_scenario_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "暂无情景。"
    rows = ["| 情景 | 触发条件 | 动作 | 仓位规则 |", "|---|---|---|---|"]
    for item in items:
        rows.append(
            "| {scenario} | {trigger} | {action} | {position_rule} |".format(
                scenario=str(item.get("scenario", "")).replace("|", "/"),
                trigger=str(item.get("trigger", "")).replace("|", "/"),
                action=str(item.get("action", "")).replace("|", "/"),
                position_rule=str(item.get("position_rule", "")).replace("|", "/"),
            )
        )
    return "\n".join(rows)
