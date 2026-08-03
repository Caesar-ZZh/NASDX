"""
Position sizing calculator for NASDX investment briefs.

The calculator converts percentage allocation bands into money amounts. It does
not fetch prices or turn research candidates into automatic trade instructions.

Two exposure sources are supported:

* the legacy manual snapshot (``current_etf_exposure`` and friends), kept for
  ad-hoc "what if" sizing, and
* the authoritative ledger snapshot from :mod:`nasdx.portfolio_store` passed as
  ``portfolio`` (Issue #66), which supersedes the manual amounts and makes a
  fail-closed ledger block every new-money suggestion.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Tuple


PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

PORTFOLIO_BLOCKED_GATE = "portfolio_fail_closed"


def parse_percent_band(value: Any) -> Tuple[float, float]:
    """Parse strings like ``35%-60%`` into decimal low/high values."""
    numbers = [float(item) / 100 for item in PERCENT_RE.findall(str(value or ""))]
    if not numbers:
        return 0.0, 0.0
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    low, high = numbers[0], numbers[1]
    return (low, high) if low <= high else (high, low)


def build_position_sizing(
    brief: Dict[str, Any],
    total_capital: float | None = None,
    current_etf_exposure: float = 0.0,
    current_stock_exposure: float = 0.0,
    current_other_exposure: float = 0.0,
    round_to: float = 100.0,
    now: datetime | None = None,
    portfolio: Any = None,
) -> Dict[str, Any]:
    """Build a money-based position plan from a NASDX investment brief.

    When ``portfolio`` (a ``PortfolioSnapshot`` or its ``to_dict()`` mapping) is
    supplied, exposures and cash come from the authoritative ledger instead of
    manually retyped amounts, ``total_capital`` defaults to the snapshot total
    assets, and a fail-closed snapshot forces every candidate to zero new money.
    """
    now = now or datetime.now()
    snapshot = _as_snapshot(portfolio)
    portfolio_notes: List[str] = []

    if snapshot is None:
        capital = _nonnegative(total_capital)
        etf_current = _nonnegative(current_etf_exposure)
        stock_current = _nonnegative(current_stock_exposure)
        other_current = _nonnegative(current_other_exposure)
        snapshot_cash: float | None = None
        portfolio_context: Dict[str, Any] = {"portfolio_linked": False}
    else:
        derived = _exposures_from_snapshot(snapshot)
        manual_total = (
            _nonnegative(current_etf_exposure)
            + _nonnegative(current_stock_exposure)
            + _nonnegative(current_other_exposure)
        )
        if manual_total > 0:
            portfolio_notes.append(
                "已接入组合账本快照，手工填写的当前敞口被忽略，以账本为准。"
            )
        etf_current = derived["ETF"]
        stock_current = derived["股票"]
        other_current = derived["其他"]
        snapshot_cash = _optional_float(snapshot.get("cash"))
        capital = _nonnegative(total_capital)
        if capital <= 0:
            capital = _nonnegative(snapshot.get("total_assets"))
        portfolio_context = _portfolio_context(snapshot)

    if capital <= 0:
        raise ValueError("total_capital must be greater than 0")

    current_total = etf_current + stock_current + other_current

    allocation = brief.get("allocation") or {}
    bands = {
        "max_total": parse_percent_band(allocation.get("max_total")),
        "etf_budget": parse_percent_band(allocation.get("etf_budget")),
        "stock_budget": parse_percent_band(allocation.get("stock_budget")),
        "single_stock_cap": parse_percent_band(allocation.get("single_stock_cap")),
        "cash_buffer": parse_percent_band(allocation.get("cash_buffer")),
    }

    max_total_amount = capital * bands["max_total"][1]
    target_total_low = capital * bands["max_total"][0]
    max_etf_budget = capital * bands["etf_budget"][1]
    max_stock_budget = capital * bands["stock_budget"][1]
    single_stock_cap = capital * bands["single_stock_cap"][1]
    min_cash_buffer = capital * bands["cash_buffer"][0]
    current_cash = snapshot_cash if snapshot_cash is not None else max(capital - current_total, 0.0)

    remaining_total = max(max_total_amount - current_total, 0.0)
    remaining_etf = max(max_etf_budget - etf_current, 0.0)
    remaining_stock = max(max_stock_budget - stock_current, 0.0)
    if snapshot_cash is not None:
        # Never suggest more new money than the ledger says is actually available.
        available_cash = max(snapshot_cash, 0.0)
        remaining_total = min(remaining_total, available_cash)
        remaining_etf = min(remaining_etf, available_cash)
        remaining_stock = min(remaining_stock, available_cash)

    portfolio_blocked = bool(portfolio_context.get("fail_closed"))
    effective_gate = (
        PORTFOLIO_BLOCKED_GATE if portfolio_blocked else str(brief.get("action_gate") or "")
    )

    audits = _candidate_audits(brief)
    trial_etfs = [item for item in audits if _is_trial(item) and item.get("type") == "ETF"]
    trial_stocks = [item for item in audits if _is_trial(item) and item.get("type") != "ETF"]
    candidate_sizing = [
        _candidate_size(
            audit=item,
            action_gate=effective_gate,
            remaining_total=remaining_total,
            remaining_etf=remaining_etf,
            remaining_stock=remaining_stock,
            single_stock_cap=single_stock_cap,
            etf_slots=max(1, len(trial_etfs)),
            stock_slots=max(1, len(trial_stocks)),
            round_to=round_to,
        )
        for item in audits
    ]

    warnings = _warnings(
        capital=capital,
        current_total=current_total,
        max_total_amount=max_total_amount,
        current_cash=current_cash,
        min_cash_buffer=min_cash_buffer,
        action_gate=str(brief.get("action_gate") or ""),
        portfolio_notes=portfolio_notes + _portfolio_warnings(portfolio_context),
    )

    return {
        "schema": "nasdx_position_sizing.v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "source_brief_generated_at": brief.get("generated_at"),
        "risk_profile": brief.get("risk_profile"),
        "risk_profile_label": brief.get("risk_profile_label"),
        "action_gate": brief.get("action_gate"),
        "posture": brief.get("posture"),
        "allocation": allocation,
        "capital_inputs": {
            "total_capital": _money(capital, round_to),
            "current_etf_exposure": _money(etf_current, round_to),
            "current_stock_exposure": _money(stock_current, round_to),
            "current_other_exposure": _money(other_current, round_to),
            "current_total_exposure": _money(current_total, round_to),
        },
        "exposure": {
            "target_total_low": _money(target_total_low, round_to),
            "max_total_amount": _money(max_total_amount, round_to),
            "remaining_total_capacity": _money(remaining_total, round_to),
            "max_etf_budget": _money(max_etf_budget, round_to),
            "remaining_etf_budget": _money(remaining_etf, round_to),
            "max_stock_budget": _money(max_stock_budget, round_to),
            "remaining_stock_budget": _money(remaining_stock, round_to),
            "single_stock_cap": _money(single_stock_cap, round_to),
            "min_cash_buffer": _money(min_cash_buffer, round_to),
            "current_cash": _money(current_cash, round_to),
            "status": _exposure_status(current_total, max_total_amount, current_cash, min_cash_buffer),
        },
        "candidate_sizing": candidate_sizing,
        "warnings": warnings,
        "portfolio_linked": bool(portfolio_context.get("portfolio_linked")),
        "portfolio_context": portfolio_context,
        "portfolio_snapshot_hash": str(portfolio_context.get("snapshot_hash") or ""),
        "portfolio_version": int(portfolio_context.get("portfolio_version") or 0),
        "allow_new_position": not portfolio_blocked,
        "assumptions": _assumptions(portfolio_context),
        "disclaimer": "研究辅助和仓位纪律换算，不构成投资建议或下单指令。",
    }


def format_position_sizing(sizing: Dict[str, Any]) -> str:
    """Render position sizing as compact Markdown."""
    inputs = sizing.get("capital_inputs", {})
    exposure = sizing.get("exposure", {})
    lines = [
        "# NASDX 仓位换算",
        "",
        f"- 生成时间：{sizing.get('generated_at', '')}",
        f"- 风险画像：{sizing.get('risk_profile_label', '')}",
        f"- 行动闸门：{sizing.get('action_gate', '')}",
        f"- 总资金：{_fmt(inputs.get('total_capital'))}",
        f"- 当前已投入：{_fmt(inputs.get('current_total_exposure'))}",
        f"- 总仓位上限金额：{_fmt(exposure.get('max_total_amount'))}",
        f"- 剩余可新增上限：{_fmt(exposure.get('remaining_total_capacity'))}",
        f"- 状态：{exposure.get('status', '')}",
        "",
        "## 账户边界",
        "",
        "| 项目 | 金额 |",
        "|---|---:|",
        f"| ETF预算上限 | {_fmt(exposure.get('max_etf_budget'))} |",
        f"| ETF剩余额度 | {_fmt(exposure.get('remaining_etf_budget'))} |",
        f"| 个股预算上限 | {_fmt(exposure.get('max_stock_budget'))} |",
        f"| 个股剩余额度 | {_fmt(exposure.get('remaining_stock_budget'))} |",
        f"| 单一个股上限 | {_fmt(exposure.get('single_stock_cap'))} |",
        f"| 最低现金缓冲 | {_fmt(exposure.get('min_cash_buffer'))} |",
        "",
        "## 候选金额",
        "",
        _format_candidate_table(sizing.get("candidate_sizing", [])),
        "",
        "## 风险提示",
        "",
        *[f"- {item}" for item in sizing.get("warnings", [])],
        *[f"- {item}" for item in sizing.get("assumptions", [])],
        "",
        f"> {sizing.get('disclaimer', '')}",
    ]
    return "\n".join(lines)


def dumps_position_sizing(sizing: Dict[str, Any]) -> str:
    """Serialize sizing with UTF-8 friendly JSON formatting."""
    return json.dumps(sizing, ensure_ascii=False, indent=2)


def _candidate_size(
    audit: Dict[str, Any],
    action_gate: str,
    remaining_total: float,
    remaining_etf: float,
    remaining_stock: float,
    single_stock_cap: float,
    etf_slots: int,
    stock_slots: int,
    round_to: float,
) -> Dict[str, Any]:
    asset_type = str(audit.get("type") or "")
    eligible = action_gate == "normal" and _is_trial(audit)
    if not eligible:
        return _blocked_candidate(audit, action_gate)

    if asset_type == "ETF":
        route_remaining = remaining_etf
        per_candidate_cap = min(remaining_total, route_remaining / max(1, etf_slots))
        first_lot = per_candidate_cap * 0.33
        reason = "按ETF预算剩余额度和总仓位剩余额度取较低值，第一笔约为单候选上限的三分之一。"
    else:
        route_remaining = remaining_stock
        per_candidate_cap = min(
            remaining_total,
            route_remaining / max(1, stock_slots),
            single_stock_cap,
        )
        first_lot = per_candidate_cap * 0.25
        reason = "按个股预算、单一个股上限和总仓位剩余额度取较低值，第一笔约为单候选上限的四分之一。"

    max_new = _money(max(per_candidate_cap, 0.0), round_to)
    first = _money(min(max(first_lot, 0.0), max_new), round_to)
    if max_new <= 0:
        reason = "当前路线或账户输入已没有可新增空间。"
    return {
        "code": audit.get("code", ""),
        "name": audit.get("name", ""),
        "candidate": audit.get("candidate", ""),
        "type": asset_type,
        "audit_status": audit.get("audit_status", ""),
        "status_code": audit.get("status_code", ""),
        "deep_signal": audit.get("deep_signal", ""),
        "max_new_amount": max_new,
        "first_lot_amount": first,
        "position_reference": audit.get("report_position_band", ""),
        "reason": reason,
    }


def _as_snapshot(portfolio: Any) -> Dict[str, Any] | None:
    """Coerce a PortfolioSnapshot / mapping into a plain dict."""
    if portfolio is None:
        return None
    if isinstance(portfolio, Mapping):
        return dict(portfolio)
    to_dict = getattr(portfolio, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        if isinstance(candidate, Mapping):
            return dict(candidate)
    raise TypeError(
        "portfolio must be a PortfolioSnapshot or mapping, got " f"{type(portfolio).__name__}"
    )


def _exposures_from_snapshot(snapshot: Mapping[str, Any]) -> Dict[str, float]:
    """Split ledger market value into ETF / stock / other buckets."""
    buckets = {"ETF": 0.0, "股票": 0.0, "其他": 0.0}
    exposure = snapshot.get("asset_class_exposure")
    if isinstance(exposure, Mapping):
        for key, value in exposure.items():
            amount = _nonnegative(value)
            bucket = str(key) if str(key) in buckets else "其他"
            buckets[bucket] += amount
    return {key: round(value, 4) for key, value in buckets.items()}


def _portfolio_context(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "portfolio_linked": True,
        "schema": str(snapshot.get("schema") or ""),
        "generated_at": str(snapshot.get("generated_at") or ""),
        "snapshot_hash": str(snapshot.get("snapshot_hash") or ""),
        "portfolio_version": int(_nonnegative(snapshot.get("portfolio_version"))),
        "fail_closed": bool(snapshot.get("fail_closed")),
        "cash": _optional_float(snapshot.get("cash")),
        "cash_status": str(snapshot.get("cash_status") or "unknown"),
        "total_assets": _optional_float(snapshot.get("total_assets")),
        "total_market_value": _optional_float(snapshot.get("total_market_value")),
        "position_count": len(snapshot.get("positions") or []),
        "blocking_reasons": [
            str(item) for item in (snapshot.get("blocking_reasons") or []) if str(item).strip()
        ],
    }


def _portfolio_warnings(context: Mapping[str, Any]) -> List[str]:
    if not context.get("portfolio_linked"):
        return []
    if not context.get("fail_closed"):
        return []
    reasons = list(context.get("blocking_reasons") or [])
    return ["组合账本 fail-closed，本次不分配任何新增金额：" + item for item in reasons] or [
        "组合账本 fail-closed，本次不分配任何新增金额。"
    ]


def _assumptions(context: Mapping[str, Any]) -> List[str]:
    if context.get("portfolio_linked"):
        return [
            "当前敞口与现金来自本地组合账本快照，不再依赖手工填写的汇总金额。",
            "候选金额是单候选上限，不代表可以同时买满所有候选。",
            "真实执行前仍需复核最新行情、公告、流动性、交易成本和个人风险承受能力。",
        ]
    return [
        "本计算只把路线百分比换算成金额，不读取也不保存真实账户信息。",
        "候选金额是单候选上限，不代表可以同时买满所有候选。",
        "真实执行前仍需复核最新行情、公告、流动性、交易成本和个人风险承受能力。",
    ]


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _blocked_candidate(audit: Dict[str, Any], action_gate: str) -> Dict[str, Any]:
    status = str(audit.get("audit_status") or "")
    if action_gate == PORTFOLIO_BLOCKED_GATE:
        reason = "组合账本 fail-closed（缺价/缺现金/账本损坏），不输出确定性买入金额。"
    elif action_gate != "normal":
        reason = "行动闸门未打开，先刷新或修复数据，不新增仓位。"
    elif audit.get("status_code") == "needs_report":
        reason = "缺少深度报告，先补报告后再评估金额。"
    elif audit.get("status_code") == "watch":
        reason = "当前只观察，等趋势或基本面证据增强。"
    elif audit.get("status_code") == "avoid":
        reason = "审计结论要求回避或降级，不分配新增金额。"
    else:
        reason = status or "未通过小仓试错条件。"
    return {
        "code": audit.get("code", ""),
        "name": audit.get("name", ""),
        "candidate": audit.get("candidate", ""),
        "type": audit.get("type", ""),
        "audit_status": status,
        "status_code": audit.get("status_code", ""),
        "deep_signal": audit.get("deep_signal", ""),
        "max_new_amount": 0.0,
        "first_lot_amount": 0.0,
        "position_reference": audit.get("report_position_band", ""),
        "reason": reason,
    }


def _candidate_audits(brief: Dict[str, Any]) -> List[Dict[str, Any]]:
    audits = brief.get("candidate_audits") or []
    if isinstance(audits, list) and audits:
        return [item for item in audits if isinstance(item, dict)]
    playbook = brief.get("candidate_playbook") or []
    fallback: List[Dict[str, Any]] = []
    for item in playbook if isinstance(playbook, list) else []:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("candidate") or "")
        code, name = _split_candidate(candidate)
        fallback.append(
            {
                "code": code,
                "name": name,
                "candidate": candidate,
                "type": item.get("type", ""),
                "audit_status": item.get("priority", ""),
                "status_code": "watch",
                "deep_signal": item.get("deep_signal", ""),
            }
        )
    return fallback


def _is_trial(audit: Dict[str, Any]) -> bool:
    return audit.get("status_code") == "trial_candidate" or audit.get("audit_status") == "小仓试错候选"


def _warnings(
    capital: float,
    current_total: float,
    max_total_amount: float,
    current_cash: float,
    min_cash_buffer: float,
    action_gate: str,
    portfolio_notes: List[str] | None = None,
) -> List[str]:
    warnings: List[str] = list(portfolio_notes or [])
    if current_total > capital:
        warnings.append("当前已投入金额超过总资金，请检查输入。")
    if current_total > max_total_amount:
        warnings.append("当前已投入金额超过路线总仓位上限，优先复核是否需要降仓。")
    if current_cash < min_cash_buffer:
        warnings.append("当前现金低于路线最低现金缓冲，新增仓位前应先恢复现金垫。")
    if action_gate != "normal":
        warnings.append("当前行动闸门不是 normal，候选金额默认不开放新增。")
    if not warnings:
        warnings.append("未发现账户金额层面的硬阻断，但候选仍需人工复核通过。")
    return warnings


def _exposure_status(
    current_total: float,
    max_total_amount: float,
    current_cash: float,
    min_cash_buffer: float,
) -> str:
    if current_total > max_total_amount:
        return "已超路线总仓位上限"
    if current_cash < min_cash_buffer:
        return "现金缓冲不足"
    return "仍有新增空间" if max_total_amount > current_total else "已接近上限"


def _format_candidate_table(items: Iterable[Dict[str, Any]]) -> str:
    rows = list(items)
    if not rows:
        return "暂无候选。"
    lines = [
        "| 候选 | 类型 | 审计 | 最多新增 | 第一笔试错 | 说明 |",
        "|---|---|---|---:|---:|---|",
    ]
    for item in rows:
        lines.append(
            "| {candidate} | {type} | {status} | {max_new} | {first_lot} | {reason} |".format(
                candidate=_safe(item.get("candidate")),
                type=_safe(item.get("type")),
                status=_safe(item.get("audit_status")),
                max_new=_fmt(item.get("max_new_amount")),
                first_lot=_fmt(item.get("first_lot_amount")),
                reason=_safe(item.get("reason")),
            )
        )
    return "\n".join(lines)


def _split_candidate(value: str) -> Tuple[str, str]:
    parts = value.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return value, value


def _nonnegative(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _money(value: Any, round_to: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number <= 0:
        return 0.0
    if round_to <= 0:
        return round(number, 2)
    rounded = math.floor(number / round_to) * round_to
    return round(rounded, 2)


def _fmt(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.0f} 元"


def _safe(value: Any) -> str:
    return str(value or "").replace("|", "/")
