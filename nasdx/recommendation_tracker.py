"""
Recommendation drift tracker for NASDX.

This module compares the latest investment brief with the prior distinct brief
so future recommendations can show what changed, not only what looks attractive
right now.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from nasdx.history_store import record_artifact
from nasdx.paths import get_reports_dir

def build_recommendation_tracker(
    reports_dir: str | Path | None = None,
    latest_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Compare the latest investment brief with the previous distinct brief."""
    root = Path(reports_dir) if reports_dir else get_reports_dir()
    latest = Path(latest_path) if latest_path else root / "investment_brief_latest.json"
    latest_brief = _load_json(latest)
    if not latest_brief:
        return _empty_tracker("缺少 investment_brief_latest.json，先生成最终投资简报。")

    prior_path, prior_brief = _find_prior_brief(root, latest_brief)
    current_candidates = _candidate_map(latest_brief)
    prior_candidates = _candidate_map(prior_brief)

    added = _candidate_rows(current_candidates, set(current_candidates) - set(prior_candidates))
    removed = _candidate_rows(prior_candidates, set(prior_candidates) - set(current_candidates))
    changed = _changed_candidates(current_candidates, prior_candidates)
    stable_trials = [
        _candidate_summary(item)
        for code, item in sorted(current_candidates.items())
        if code in prior_candidates and item.get("status_code") == "trial_candidate"
        and prior_candidates[code].get("status_code") == "trial_candidate"
    ]
    allocation_changes = _allocation_changes(
        prior_brief.get("allocation", {}) if prior_brief else {},
        latest_brief.get("allocation", {}),
    )

    tracker = {
        "schema": "nasdx_recommendation_tracker.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_brief_path": str(latest),
        "prior_brief_path": str(prior_path) if prior_path else None,
        "latest_generated_at": latest_brief.get("generated_at"),
        "prior_generated_at": prior_brief.get("generated_at") if prior_brief else None,
        "comparison_status": "compared" if prior_brief else "no_prior",
        "action_gate_change": _field_change(prior_brief, latest_brief, "action_gate"),
        "posture_change": _field_change(prior_brief, latest_brief, "posture"),
        "primary_bias_change": _field_change(prior_brief, latest_brief, "primary_bias"),
        "allocation_changes": allocation_changes,
        "added_candidates": added,
        "removed_candidates": removed,
        "changed_candidates": changed,
        "stable_trial_candidates": stable_trials,
        "counts": {
            "current_candidates": len(current_candidates),
            "prior_candidates": len(prior_candidates),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "current_trial": _count_status(current_candidates.values(), "trial_candidate"),
            "current_needs_report": _count_status(current_candidates.values(), "needs_report"),
            "current_watch": _count_status(current_candidates.values(), "watch"),
            "current_avoid": _count_status(current_candidates.values(), "avoid"),
        },
        "review_focus": _review_focus(latest_brief, added, removed, changed, allocation_changes),
        "disclaimer": "研究辅助的建议漂移追踪；变化原因仍需结合最新行情、公告、流动性和账户约束人工复核。",
    }
    tracker["markdown"] = format_recommendation_tracker(tracker)
    return tracker


def save_recommendation_tracker(
    tracker: Dict[str, Any],
    output_dir: str | Path | None = None,
) -> Dict[str, str]:
    """Save recommendation tracker Markdown/JSON files."""
    out_dir = Path(output_dir) if output_dir else get_reports_dir(create=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    md_path = out_dir / f"recommendation_tracker_{stamp}.md"
    json_path = out_dir / f"recommendation_tracker_{stamp}.json"
    latest_md = out_dir / "recommendation_tracker_latest.md"
    latest_json = out_dir / "recommendation_tracker_latest.json"
    payload = {key: value for key, value in tracker.items() if key != "markdown"}

    md_path.write_text(tracker.get("markdown") or format_recommendation_tracker(tracker), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    record_artifact(
        "recommendation_tracker",
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


def build_and_save_recommendation_tracker(
    reports_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Build and save a recommendation tracker from current report artifacts."""
    tracker = build_recommendation_tracker(reports_dir=reports_dir)
    return tracker, save_recommendation_tracker(tracker, output_dir=output_dir)


def format_recommendation_tracker(tracker: Dict[str, Any]) -> str:
    """Render recommendation drift tracker as Markdown."""
    if tracker.get("comparison_status") == "missing_latest":
        return f"# NASDX 建议漂移追踪\n\n- {tracker.get('message', '')}\n"

    lines = [
        "# NASDX 建议漂移追踪",
        "",
        f"- 生成时间：{tracker.get('generated_at', '')}",
        f"- 最新简报：{tracker.get('latest_generated_at') or '未知'}",
        f"- 对比简报：{tracker.get('prior_generated_at') or '暂无'}",
        f"- 对比状态：{tracker.get('comparison_status', '')}",
        "",
        "## 组合层变化",
        "",
        _format_change_table(
            [
                ("行动闸门", tracker.get("action_gate_change", {})),
                ("市场姿态", tracker.get("posture_change", {})),
                ("投资方向", tracker.get("primary_bias_change", {})),
            ]
        ),
        "",
        "## 仓位变化",
        "",
        _format_allocation_table(tracker.get("allocation_changes", [])),
        "",
        "## 候选变化",
        "",
        f"- 新增候选：{len(tracker.get('added_candidates', []))}",
        f"- 移除候选：{len(tracker.get('removed_candidates', []))}",
        f"- 状态变化：{len(tracker.get('changed_candidates', []))}",
        "",
        "### 新增候选",
        "",
        _format_candidate_table(tracker.get("added_candidates", [])),
        "",
        "### 移除候选",
        "",
        _format_candidate_table(tracker.get("removed_candidates", [])),
        "",
        "### 状态变化",
        "",
        _format_changed_table(tracker.get("changed_candidates", [])),
        "",
        "## 稳定试错候选",
        "",
        _format_candidate_table(tracker.get("stable_trial_candidates", [])),
        "",
        "## 下次复盘重点",
        "",
        *[f"- {item}" for item in tracker.get("review_focus", [])],
        "",
        f"> {tracker.get('disclaimer', '')}",
    ]
    return "\n".join(lines)


def _empty_tracker(message: str) -> Dict[str, Any]:
    return {
        "schema": "nasdx_recommendation_tracker.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "comparison_status": "missing_latest",
        "message": message,
        "markdown": f"# NASDX 建议漂移追踪\n\n- {message}\n",
    }


def _find_prior_brief(root: Path, latest_brief: Dict[str, Any]) -> Tuple[Path | None, Dict[str, Any]]:
    latest_generated_at = latest_brief.get("generated_at")
    files = sorted(root.glob("investment_brief_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    fallback: Tuple[Path | None, Dict[str, Any]] = (None, {})
    for path in files:
        if path.name == "investment_brief_latest.json":
            continue
        data = _load_json(path)
        if not data:
            continue
        if fallback[0] is None:
            fallback = (path, data)
        if data.get("generated_at") != latest_generated_at:
            return path, data
    return fallback if fallback[1].get("generated_at") != latest_generated_at else (None, {})


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _candidate_map(brief: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    candidates: Dict[str, Dict[str, Any]] = {}
    for item in brief.get("candidate_audits", []) if isinstance(brief, dict) else []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            code = _code_from_candidate(str(item.get("candidate") or ""))
        if code:
            candidates[code] = item
    return candidates


def _candidate_rows(source: Dict[str, Dict[str, Any]], codes: Iterable[str]) -> List[Dict[str, Any]]:
    return [_candidate_summary(source[code]) for code in sorted(codes) if code in source]


def _candidate_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": item.get("code", ""),
        "name": item.get("name", ""),
        "candidate": item.get("candidate", ""),
        "type": item.get("type", ""),
        "audit_status": item.get("audit_status", ""),
        "status_code": item.get("status_code", ""),
        "deep_signal": item.get("deep_signal", ""),
        "score": item.get("score"),
        "report_action": item.get("report_action", ""),
        "reason": _summary_reason(item),
    }


def _changed_candidates(
    current: Dict[str, Dict[str, Any]],
    prior: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    changes = []
    for code in sorted(set(current) & set(prior)):
        now = current[code]
        before = prior[code]
        fields = []
        for key, label in [
            ("status_code", "审计状态"),
            ("audit_status", "审计结论"),
            ("deep_signal", "深度信号"),
            ("report_action", "报告动作"),
        ]:
            if str(now.get(key) or "") != str(before.get(key) or ""):
                fields.append(
                    {
                        "field": label,
                        "from": before.get(key, ""),
                        "to": now.get(key, ""),
                    }
                )
        score_delta = _score_delta(before.get("score"), now.get("score"))
        if score_delta is not None and abs(score_delta) >= 0.5:
            fields.append({"field": "审计分", "from": before.get("score"), "to": now.get("score")})
        if fields:
            changes.append(
                {
                    "code": code,
                    "candidate": now.get("candidate", before.get("candidate", code)),
                    "type": now.get("type", ""),
                    "changes": fields,
                    "current_status": now.get("audit_status", ""),
                    "reason": _change_reason(now, before, fields),
                }
            )
    return changes


def _allocation_changes(prior: Dict[str, Any], current: Dict[str, Any]) -> List[Dict[str, str]]:
    keys = [
        ("max_total", "总仓位上限"),
        ("etf_budget", "ETF预算"),
        ("stock_budget", "个股预算"),
        ("single_stock_cap", "单一个股上限"),
        ("cash_buffer", "现金缓冲"),
    ]
    changes = []
    for key, label in keys:
        before = str(prior.get(key, "") or "")
        after = str(current.get(key, "") or "")
        if before != after:
            changes.append({"item": label, "from": before or "暂无", "to": after or "暂无"})
    return changes


def _field_change(prior: Dict[str, Any], current: Dict[str, Any], key: str) -> Dict[str, str]:
    before = str((prior or {}).get(key, "") or "")
    after = str((current or {}).get(key, "") or "")
    return {
        "from": before or "暂无",
        "to": after or "暂无",
        "changed": before != after,
    }


def _review_focus(
    current_brief: Dict[str, Any],
    added: List[Dict[str, Any]],
    removed: List[Dict[str, Any]],
    changed: List[Dict[str, Any]],
    allocation_changes: List[Dict[str, str]],
) -> List[str]:
    focus: List[str] = []
    gate = current_brief.get("action_gate", "")
    if gate != "normal":
        focus.append(f"行动闸门为 {gate}，下次复盘先确认行情和扫描覆盖率是否修复。")
    if allocation_changes:
        focus.append("仓位框架发生变化，先按新上限复核总仓位、现金缓冲和单票风险。")
    if added:
        names = "、".join(item.get("candidate", "") for item in added[:3])
        focus.append(f"新增候选 {names} 先补公告/财报/流动性复核，不直接放大仓位。")
    if removed:
        names = "、".join(item.get("candidate", "") for item in removed[:3])
        focus.append(f"移除候选 {names} 需要检查是否跌出前排、报告过期或风险转弱。")
    risky_changes = [
        item for item in changed
        if any(str(change.get("to")) in {"avoid", "watch", "needs_report"} for change in item.get("changes", []))
        or item.get("current_status") in {"回避/降级", "观察等待", "先补深度报告"}
    ]
    if risky_changes:
        names = "、".join(item.get("candidate", "") for item in risky_changes[:3])
        focus.append(f"状态降级或待补证据候选 {names} 不进入试错，先按执行队列处理。")
    trial_count = _count_status(_candidate_map(current_brief).values(), "trial_candidate")
    if trial_count:
        focus.append(f"当前 {trial_count} 个试错候选仍需逐项人工复核公告、成交/折溢价和账户仓位。")
    if not focus:
        focus.append("路线与候选较稳定，下次复盘重点看前排候选是否连续保持和风险红灯是否出现。")
    return focus[:6]


def _count_status(items: Iterable[Dict[str, Any]], status: str) -> int:
    return sum(1 for item in items if item.get("status_code") == status)


def _summary_reason(item: Dict[str, Any]) -> str:
    evidence = item.get("key_evidence") or []
    if evidence:
        return str(evidence[0])
    blockers = item.get("blocking_flags") or []
    if blockers:
        return str(blockers[0])
    return str(item.get("execution_bias") or "")


def _change_reason(now: Dict[str, Any], before: Dict[str, Any], fields: List[Dict[str, Any]]) -> str:
    labels = "、".join(str(item.get("field")) for item in fields)
    return f"{labels}变化；当前结论为 {now.get('audit_status', '') or now.get('status_code', '')}。"


def _score_delta(before: Any, after: Any) -> float | None:
    try:
        return float(after) - float(before)
    except (TypeError, ValueError):
        return None


def _code_from_candidate(candidate: str) -> str:
    return candidate.split(maxsplit=1)[0] if candidate else ""


def _format_change_table(items: List[Tuple[str, Dict[str, Any]]]) -> str:
    rows = ["| 项目 | 上次 | 本次 | 是否变化 |", "|---|---|---|---|"]
    for label, change in items:
        rows.append(
            "| {label} | {before} | {after} | {changed} |".format(
                label=_safe(label),
                before=_safe(change.get("from")),
                after=_safe(change.get("to")),
                changed="是" if change.get("changed") else "否",
            )
        )
    return "\n".join(rows)


def _format_allocation_table(items: List[Dict[str, str]]) -> str:
    if not items:
        return "仓位框架未变化。"
    rows = ["| 项目 | 上次 | 本次 |", "|---|---|---|"]
    for item in items:
        rows.append(f"| {_safe(item.get('item'))} | {_safe(item.get('from'))} | {_safe(item.get('to'))} |")
    return "\n".join(rows)


def _format_candidate_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "暂无。"
    rows = ["| 候选 | 类型 | 审计 | 深度信号 | 分数 | 说明 |", "|---|---|---|---|---:|---|"]
    for item in items:
        rows.append(
            "| {candidate} | {type} | {status} | {signal} | {score} | {reason} |".format(
                candidate=_safe(item.get("candidate")),
                type=_safe(item.get("type")),
                status=_safe(item.get("audit_status")),
                signal=_safe(item.get("deep_signal")),
                score=_safe(item.get("score")),
                reason=_safe(item.get("reason")),
            )
        )
    return "\n".join(rows)


def _format_changed_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "暂无。"
    rows = ["| 候选 | 类型 | 变化 | 当前结论 | 说明 |", "|---|---|---|---|---|"]
    for item in items:
        changes = "；".join(
            f"{change.get('field')}: {change.get('from')} -> {change.get('to')}"
            for change in item.get("changes", [])
        )
        rows.append(
            "| {candidate} | {type} | {changes} | {status} | {reason} |".format(
                candidate=_safe(item.get("candidate")),
                type=_safe(item.get("type")),
                changes=_safe(changes),
                status=_safe(item.get("current_status")),
                reason=_safe(item.get("reason")),
            )
        )
    return "\n".join(rows)


def _safe(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/")
