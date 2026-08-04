"""HTML table builders for the Streamlit investment-plan page."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from nasdx.ui_security import escape_html, safe_external_link


EMPTY_CARD = '<div class="n-card" style="font-size:13px;color:rgba(255,255,255,0.45)">{}</div>'
SIGNAL_COLORS = {
    "bullish": "#22c55e",
    "neutral": "#f59e0b",
    "bearish": "#ef4444",
    "missing": "#8b949e",
}
AUDIT_COLORS = {
    "小仓试错候选": "#22c55e",
    "观察等待": "#f59e0b",
    "先补深度报告": "#f59e0b",
    "先修数据": "#ef4444",
    "回避/降级": "#ef4444",
}


@dataclass(frozen=True)
class _TrustedHtml:
    value: str


def _colored(value: Any, color: str) -> _TrustedHtml:
    return _TrustedHtml(f'<span style="color:{color};font-weight:700">{escape_html(value)}</span>')


def _render_table(
    rows: list[Mapping[str, Any]],
    *,
    empty_message: str,
    min_width: int | None = None,
    include_styles: bool = False,
) -> str:
    if not rows:
        return EMPTY_CARD.format(escape_html(empty_message))

    head = "".join(f"<th>{escape_html(key)}</th>" for key in rows[0])
    body_parts = []
    for row in rows:
        cells = []
        for value in row.values():
            cell = value.value if isinstance(value, _TrustedHtml) else escape_html(value)
            cells.append(f"<td>{cell}</td>")
        body_parts.append("<tr>" + "".join(cells) + "</tr>")

    width = f";min-width:{min_width}px" if min_width else ""
    styles = ""
    if include_styles:
        styles = """
        <style>
          .plan-table table th{color:rgba(255,255,255,0.42);font-weight:600;text-align:left;padding:9px 10px;border-bottom:1px solid rgba(255,255,255,0.08);white-space:nowrap}
          .plan-table table td{color:rgba(255,255,255,0.75);padding:9px 10px;border-bottom:1px solid rgba(255,255,255,0.06);vertical-align:top}
          .plan-table table tr:last-child td{border-bottom:0}
        </style>"""
    return f"""
    <div class="n-card plan-table" style="padding:0;overflow:auto">
      <table style="width:100%;border-collapse:collapse;font-size:12px{width}">
        <thead><tr>{head}</tr></thead>
        <tbody>{''.join(body_parts)}</tbody>
      </table>
    </div>{styles}"""


def candidate_table(items: Iterable[Mapping[str, Any]]) -> str:
    rows = [
        {
            "代码": item.get("code", ""),
            "名称": item.get("name", ""),
            "类型": item.get("asset_type", ""),
            "分数": item.get("adjusted_score", item.get("score", 0)),
            "信号": item.get("signal", ""),
            "动作": item.get("action", ""),
            "理由": item.get("reason", ""),
        }
        for item in items
    ]
    return _render_table(rows, empty_message="暂无候选", include_styles=True)


def scenario_table(items: Iterable[Mapping[str, Any]]) -> str:
    rows = [
        {
            "情景": item.get("scenario", ""),
            "触发条件": item.get("trigger", ""),
            "动作": item.get("action", ""),
            "仓位规则": item.get("position_rule", ""),
        }
        for item in items
    ]
    return _render_table(rows, empty_message="暂无情景")


def brief_playbook_table(items: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for item in items:
        signal = item.get("deep_signal", "")
        rows.append(
            {
                "候选": item.get("candidate", ""),
                "类型": item.get("type", ""),
                "深度信号": _colored(signal, SIGNAL_COLORS.get(str(signal), "#8b949e")),
                "优先级": item.get("priority", ""),
                "入场条件": item.get("entry_condition", ""),
                "复核动作": item.get("review", ""),
            }
        )
    return _render_table(rows, empty_message="暂无候选剧本")


def audit_table(items: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for item in items:
        status = item.get("audit_status", "")
        signal = item.get("deep_signal", "")
        rows.append(
            {
                "候选": item.get("candidate", ""),
                "审计结论": _colored(status, AUDIT_COLORS.get(str(status), "#8b949e")),
                "深度信号": _colored(signal, SIGNAL_COLORS.get(str(signal), "#8b949e")),
                "核心证据": "；".join(str(value) for value in item.get("key_evidence", [])[:3]),
                "待人工复核": "；".join(str(value) for value in item.get("manual_checks", [])[:3]) or "无",
                "阻断项": "；".join(str(value) for value in item.get("blocking_flags", [])[:2]) or "无",
            }
        )
    return _render_table(rows, empty_message="暂无候选证据核查", min_width=980)


def execution_queue_table(items: Iterable[Mapping[str, Any]]) -> str:
    stage_colors = {"盘前": "#60a5fa", "盘中": "#22c55e", "盘后": "#a78bfa"}
    decision_colors = {
        "可进入复核流程": "#22c55e",
        "小仓试错前复核": "#22c55e",
        "先补深度报告": "#f59e0b",
        "观察等待": "#f59e0b",
        "回避/降级": "#ef4444",
        "先刷新数据": "#ef4444",
        "先修数据": "#ef4444",
        "重新生成明日路线": "#a78bfa",
    }
    rows = []
    for item in items:
        stage = item.get("stage", "")
        decision = item.get("decision", "")
        command = item.get("command", "") or "无"
        command_cell: Any = command
        if command != "无":
            command_cell = _TrustedHtml(
                f'<code style="white-space:nowrap;color:#9cdcfe">{escape_html(command)}</code>'
            )
        rows.append(
            {
                "阶段": _colored(stage, stage_colors.get(str(stage), "#8b949e")),
                "对象": item.get("target", ""),
                "决策": _colored(decision, decision_colors.get(str(decision), "#8b949e")),
                "动作": item.get("action", ""),
                "条件": item.get("condition", ""),
                "阻断": item.get("blocker", ""),
                "命令": command_cell,
            }
        )
    return _render_table(rows, empty_message="暂无执行队列", min_width=1120)


def external_review_table(items: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for item in items:
        links = [
            safe_external_link(link.get("label", ""), link.get("url", ""), title=link.get("usage", ""))
            for link in item.get("source_links", [])[:3]
        ]
        gate = item.get("review_gate", "")
        rows.append(
            {
                "候选": item.get("candidate", ""),
                "复核闸门": _colored(gate, "#f59e0b"),
                "通过时间": item.get("must_pass_before", ""),
                "必查项": "；".join(str(value) for value in item.get("required_checks", [])[:3]),
                "来源入口": _TrustedHtml("；".join(links) or "无"),
                "失败动作": item.get("failure_action", ""),
            }
        )
    return _render_table(rows, empty_message="暂无外部复核包", min_width=1120)


def position_sizing_table(items: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for item in items:
        status = item.get("audit_status", "")
        rows.append(
            {
                "候选": item.get("candidate", ""),
                "类型": item.get("type", ""),
                "审计": _colored(status, AUDIT_COLORS.get(str(status), "#8b949e")),
                "最多新增": _colored(f'{float(item.get("max_new_amount") or 0):,.0f}', "#9cdcfe"),
                "第一笔试错": _colored(f'{float(item.get("first_lot_amount") or 0):,.0f}', "#9cdcfe"),
                "说明": item.get("reason", ""),
            }
        )
    return _render_table(rows, empty_message="暂无仓位换算候选", min_width=1040)


def account_review_table(items: Iterable[Mapping[str, Any]]) -> str:
    route_colors = {
        "小仓试错候选": "#22c55e",
        "trial_candidate": "#22c55e",
        "先补深度报告": "#f59e0b",
        "needs_report": "#f59e0b",
        "观察等待": "#f59e0b",
        "watch": "#f59e0b",
        "回避/降级": "#ef4444",
        "avoid": "#ef4444",
        "not_in_current_route": "#8b949e",
    }
    rows = []
    for item in items:
        pnl = "NA" if item.get("unrealized_pnl") is None else f'{float(item.get("unrealized_pnl") or 0):,.0f}'
        route = item.get("route_audit") or item.get("route_status", "")
        pnl_cell: Any = pnl
        if pnl != "NA":
            pnl_cell = _colored(pnl, "#ef4444" if pnl.replace(",", "").startswith("-") else "#22c55e")
        rows.append(
            {
                "持仓": f'{item.get("code", "")} {item.get("name", "")}'.strip(),
                "数量": f'{float(item.get("quantity") or 0):,.0f}',
                "成本": f'{float(item.get("avg_cost") or 0):,.3f}',
                "最新价": "NA" if item.get("latest_price") is None else f'{float(item.get("latest_price") or 0):,.3f}',
                "市值": "NA" if item.get("market_value") is None else f'{float(item.get("market_value") or 0):,.0f}',
                "浮盈亏": pnl_cell,
                "路线": _colored(route, route_colors.get(str(route), "#8b949e")),
                "动作": item.get("route_action", ""),
            }
        )
    return _render_table(rows, empty_message="暂无真实持仓", min_width=1060)


def portfolio_snapshot_table(items: Iterable[Mapping[str, Any]]) -> str:
    """Render the authoritative ledger positions as a read-only table (Issue #66)."""
    rows = []
    for item in items:
        unrealized = item.get("unrealized_pnl")
        pnl_cell: Any = "NA"
        if unrealized is not None:
            pnl_cell = _colored(
                f'{float(unrealized):,.0f}',
                "#ef4444" if float(unrealized) < 0 else "#22c55e",
            )
        priced = item.get("valuation_status") == "priced" or item.get("last_price") is not None
        rows.append(
            {
                "持仓": f'{item.get("code", "")} {item.get("name", "")}'.strip(),
                "类别": item.get("asset_class", ""),
                "行业": item.get("industry", "") or "未分类",
                "数量": f'{float(item.get("quantity") or 0):,.0f}',
                "成本": f'{float(item.get("avg_cost") or 0):,.3f}',
                "最新价": "NA" if item.get("last_price") is None else f'{float(item.get("last_price") or 0):,.3f}',
                "市值": "NA" if item.get("market_value") is None else f'{float(item.get("market_value")):,.0f}',
                "浮盈亏": pnl_cell,
                "估值": _colored("已估值" if priced else "缺价", "#22c55e" if priced else "#f59e0b"),
            }
        )
    return _render_table(rows, empty_message="账本内暂无持仓", min_width=1100)


def tracker_change_table(items: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for item in items:
        changes = "；".join(
            f'{change.get("field", "")}: {change.get("from", "")} → {change.get("to", "")}'
            for change in item.get("changes", [])
        )
        rows.append(
            {
                "候选": item.get("candidate", ""),
                "类型": item.get("type", ""),
                "变化": changes,
                "当前结论": item.get("current_status", ""),
                "说明": item.get("reason", ""),
            }
        )
    return _render_table(rows, empty_message="暂无状态变化", min_width=980)


def recommendation_review_table(items: Iterable[Mapping[str, Any]]) -> str:
    label_map = {
        "signal_continues": "信号延续",
        "downgrade_review": "降级复核",
        "pending_evidence": "仍待补证据",
        "missing_current_data": "缺当前数据",
    }
    status_colors = {
        "信号延续": "#22c55e",
        "降级复核": "#ef4444",
        "仍待补证据": "#f59e0b",
        "缺当前数据": "#8b949e",
    }
    rows = []
    for item in items:
        status = label_map.get(item.get("review_status", ""), item.get("review_status", ""))
        rows.append(
            {
                "候选": item.get("candidate", ""),
                "基准状态": item.get("baseline_audit") or item.get("baseline_status", ""),
                "当前状态": item.get("current_audit") or item.get("current_status", ""),
                "最新信号": item.get("current_signal", ""),
                "最新分数": item.get("current_score", ""),
                "涨跌幅": item.get("latest_change_pct", ""),
                "复盘": _colored(status, status_colors.get(str(status), "#8b949e")),
                "动作": item.get("review_action", ""),
            }
        )
    return _render_table(rows, empty_message="暂无建议结果复盘", min_width=1120)
