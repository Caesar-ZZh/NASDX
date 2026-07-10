"""
Recommendation outcome review for NASDX.

This module reviews a prior investment brief against the latest local market
snapshot and scan outputs. It deliberately separates follow-through evidence
from realized PnL: without a real executed price and position ledger, the output
is a research review, not a performance statement.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from nasdx.history_store import record_artifact
from nasdx.paths import get_market_data_dir, get_reports_dir

def build_recommendation_review(
    reports_dir: str | Path | None = None,
    baseline_path: str | Path | None = None,
    current_brief_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Review a baseline brief against the latest local market and scan state."""
    root = Path(reports_dir) if reports_dir else get_reports_dir()
    current_path = Path(current_brief_path) if current_brief_path else root / "investment_brief_latest.json"
    current_brief = _load_json(current_path)
    if not current_brief:
        return _empty_review("缺少 investment_brief_latest.json，先生成最终投资简报。")

    baseline_file, baseline_brief = _resolve_baseline(root, baseline_path, current_brief)
    if not baseline_brief:
        return _empty_review("缺少可复盘的历史简报；至少需要两份不同时间的 investment_brief JSON。")

    market_data, market_path = _load_latest_json(get_market_data_dir(), "stock_data_*.json")
    etf_scan, etf_path = _load_latest_json(root, "etf50_[0-9]*_[0-9]*.json")
    stock_scan, stock_path = _load_latest_json(root, "stocks60_*.json")
    market_map = _market_map(market_data)
    scan_map = _scan_map(etf_scan, stock_scan)
    current_status = _current_status_map(current_brief)

    rows = [
        _review_candidate(item, current_status.get(_candidate_code(item), {}), market_map, scan_map)
        for item in baseline_brief.get("candidate_audits", [])
        if isinstance(item, dict)
    ]
    counts = _counts(rows)
    review = {
        "schema": "nasdx_recommendation_review.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "baseline_brief_path": str(baseline_file) if baseline_file else None,
        "current_brief_path": str(current_path),
        "baseline_generated_at": baseline_brief.get("generated_at"),
        "current_generated_at": current_brief.get("generated_at"),
        "market_data_file": str(market_path) if market_path else None,
        "market_data_date": (market_data or {}).get("date") or _latest_market_date(market_map),
        "etf_scan_file": str(etf_path) if etf_path else None,
        "stock_scan_file": str(stock_path) if stock_path else None,
        "time_context": _time_context(baseline_brief, current_brief, market_data),
        "review_rows": rows,
        "counts": counts,
        "summary": _summary(counts),
        "next_review_actions": _next_review_actions(rows, counts),
        "disclaimer": (
            "本复盘只验证建议后的信号延续、降级和待补证据，不等同于真实交易收益；"
            "真实收益需要成交价、持仓、费用和账户流水。"
        ),
    }
    review["markdown"] = format_recommendation_review(review)
    return review


def save_recommendation_review(
    review: Dict[str, Any],
    output_dir: str | Path | None = None,
) -> Dict[str, str]:
    """Save recommendation review Markdown/JSON files."""
    out_dir = Path(output_dir) if output_dir else get_reports_dir(create=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    md_path = out_dir / f"recommendation_review_{stamp}.md"
    json_path = out_dir / f"recommendation_review_{stamp}.json"
    latest_md = out_dir / "recommendation_review_latest.md"
    latest_json = out_dir / "recommendation_review_latest.json"
    payload = {key: value for key, value in review.items() if key != "markdown"}

    md_path.write_text(review.get("markdown") or format_recommendation_review(review), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    record_artifact(
        "recommendation_review",
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


def build_and_save_recommendation_review(
    reports_dir: str | Path | None = None,
    baseline_path: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Build and save a recommendation outcome review."""
    review = build_recommendation_review(reports_dir=reports_dir, baseline_path=baseline_path)
    return review, save_recommendation_review(review, output_dir=output_dir)


def format_recommendation_review(review: Dict[str, Any]) -> str:
    """Render recommendation outcome review as Markdown."""
    if review.get("review_status") == "missing":
        return f"# NASDX 建议结果复盘\n\n- {review.get('message', '')}\n"

    counts = review.get("counts", {})
    lines = [
        "# NASDX 建议结果复盘",
        "",
        f"- 生成时间：{review.get('generated_at', '')}",
        f"- 复盘简报：{review.get('baseline_generated_at') or '暂无'}",
        f"- 当前简报：{review.get('current_generated_at') or '暂无'}",
        f"- 行情日期：{review.get('market_data_date') or '未知'}",
        f"- 时间判断：{review.get('time_context', '')}",
        f"- 摘要：{review.get('summary', '')}",
        "",
        "## 复盘计数",
        "",
        "| 类型 | 数量 |",
        "|---|---:|",
        f"| 信号延续 | {counts.get('signal_continues', 0)} |",
        f"| 降级复核 | {counts.get('downgrade_review', 0)} |",
        f"| 仍待补证据 | {counts.get('pending_evidence', 0)} |",
        f"| 缺当前数据 | {counts.get('missing_current_data', 0)} |",
        "",
        "## 候选复盘",
        "",
        _format_review_table(review.get("review_rows", [])),
        "",
        "## 下一步",
        "",
        *[f"- {item}" for item in review.get("next_review_actions", [])],
        "",
        f"> {review.get('disclaimer', '')}",
    ]
    return "\n".join(lines)


def _empty_review(message: str) -> Dict[str, Any]:
    return {
        "schema": "nasdx_recommendation_review.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "review_status": "missing",
        "message": message,
        "markdown": f"# NASDX 建议结果复盘\n\n- {message}\n",
    }


def _resolve_baseline(
    root: Path,
    baseline_path: str | Path | None,
    current_brief: Dict[str, Any],
) -> Tuple[Path | None, Dict[str, Any]]:
    if baseline_path:
        path = Path(baseline_path)
        return path, _load_json(path)

    current_time = current_brief.get("generated_at")
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
        if data.get("generated_at") != current_time:
            return path, data
    return fallback if fallback[1].get("generated_at") != current_time else (None, {})


def _review_candidate(
    baseline: Dict[str, Any],
    current: Dict[str, Any],
    market_map: Dict[str, Dict[str, Any]],
    scan_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    code = _candidate_code(baseline)
    market = market_map.get(code, {})
    scan = scan_map.get(code, {})
    current_status = current.get("status_code", "")
    current_audit = current.get("audit_status", "")
    current_signal = scan.get("signal") or current.get("deep_signal") or ""
    current_score = _as_float(scan.get("score") or scan.get("adjusted_score") or current.get("score"))
    change_pct = _as_float(scan.get("chg") or scan.get("spot_chg") or market.get("change_pct"))
    latest_close = _as_float(scan.get("close") or scan.get("spot_price") or market.get("close"))
    review_status, action = _review_status(
        baseline_status=str(baseline.get("status_code") or ""),
        current_status=str(current_status or ""),
        current_signal=str(current_signal or ""),
        current_score=current_score,
        has_market=bool(market or scan),
    )

    return {
        "code": code,
        "candidate": baseline.get("candidate", ""),
        "type": baseline.get("type", ""),
        "baseline_status": baseline.get("status_code", ""),
        "baseline_audit": baseline.get("audit_status", ""),
        "current_status": current_status,
        "current_audit": current_audit,
        "baseline_deep_signal": baseline.get("deep_signal", ""),
        "current_signal": current_signal,
        "baseline_score": baseline.get("score"),
        "current_score": current_score,
        "latest_close": latest_close,
        "latest_change_pct": change_pct,
        "data_date": scan.get("data_date") or market.get("data_date") or "",
        "review_status": review_status,
        "review_action": action,
        "evidence": _evidence(scan, market, current),
    }


def _review_status(
    baseline_status: str,
    current_status: str,
    current_signal: str,
    current_score: float | None,
    has_market: bool,
) -> Tuple[str, str]:
    if not has_market:
        return "missing_current_data", "缺少当前行情或扫描数据，先刷新后再复盘。"
    if baseline_status == "needs_report":
        if current_status == "trial_candidate":
            return "signal_continues", "原先待补报告候选已升级为试错候选，执行前仍需人工复核。"
        return "pending_evidence", "原先缺深度报告，本轮仍以补报告和公告核验为主。"
    if baseline_status in {"watch", "avoid", "refresh_data"}:
        if current_status == "trial_candidate":
            return "signal_continues", "原先非试错候选已转强，先复核变化原因再考虑小仓。"
        return "pending_evidence", "原先非试错候选仍未满足试错条件。"
    if baseline_status == "trial_candidate":
        if current_status in {"avoid", "watch", "needs_report", "refresh_data"}:
            return "downgrade_review", "原试错候选在当前简报中降级，先暂停新增并查降级原因。"
        if current_signal == "bearish" or (current_score is not None and current_score < 55):
            return "downgrade_review", "最新扫描信号转弱，先降级复核。"
        if current_signal == "neutral" or (current_score is not None and current_score < 65):
            return "pending_evidence", "信号未明显延续，保留观察，不放大仓位。"
        return "signal_continues", "最新扫描/简报仍支持试错候选身份，但不等于下单许可。"
    return "pending_evidence", "候选状态不完整，按待补证据处理。"


def _market_map(data: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for sector in (data or {}).get("sectors", []):
        for group in ("stocks", "etfs"):
            for item in sector.get(group, []) or []:
                code = str(item.get("code") or "")
                indicators = item.get("indicators") or {}
                if not code:
                    continue
                result[code] = {
                    "code": code,
                    "name": item.get("name", ""),
                    "type": item.get("type", group[:-1]),
                    "close": indicators.get("close"),
                    "change_pct": indicators.get("change_pct"),
                    "data_date": item.get("data_date") or (data or {}).get("date"),
                    "data_source": item.get("data_source", ""),
                }
    return result


def _scan_map(etf_scan: Dict[str, Any] | None, stock_scan: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for scan, asset_type in ((etf_scan or {}, "ETF"), (stock_scan or {}, "个股")):
        for row in _scan_rows(scan):
            code = str(row.get("code") or "")
            if not code:
                continue
            item = dict(row)
            item["asset_type"] = asset_type
            result[code] = item
    return result


def _current_status_map(brief: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result = {}
    for item in brief.get("candidate_audits", []) if isinstance(brief, dict) else []:
        if not isinstance(item, dict):
            continue
        code = _candidate_code(item)
        if code:
            result[code] = item
    return result


def _scan_rows(scan: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = scan.get("results") or scan.get("top3") or []
    return rows if isinstance(rows, list) else []


def _counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    keys = ["signal_continues", "downgrade_review", "pending_evidence", "missing_current_data"]
    return {key: sum(1 for row in rows if row.get("review_status") == key) for key in keys} | {
        "total": len(rows)
    }


def _summary(counts: Dict[str, int]) -> str:
    total = counts.get("total", 0)
    if not total:
        return "暂无可复盘候选。"
    return (
        f"{total} 个候选中，{counts.get('signal_continues', 0)} 个信号延续，"
        f"{counts.get('downgrade_review', 0)} 个需要降级复核，"
        f"{counts.get('pending_evidence', 0)} 个仍待补证据，"
        f"{counts.get('missing_current_data', 0)} 个缺当前数据。"
    )


def _next_review_actions(rows: List[Dict[str, Any]], counts: Dict[str, int]) -> List[str]:
    actions: List[str] = []
    downgrade = [row for row in rows if row.get("review_status") == "downgrade_review"]
    pending = [row for row in rows if row.get("review_status") == "pending_evidence"]
    missing = [row for row in rows if row.get("review_status") == "missing_current_data"]
    continues = [row for row in rows if row.get("review_status") == "signal_continues"]
    if missing:
        actions.append("先刷新行情和扫描数据；缺当前数据的候选不参与复盘结论。")
    if downgrade:
        actions.append(f"{_name_list(downgrade[:3])} 需要降级复核，暂停新增仓位并检查风险红灯。")
    if pending:
        actions.append(f"{_name_list(pending[:3])} 仍待补深度报告、公告/财报或流动性证据。")
    if continues:
        actions.append(f"{_name_list(continues[:3])} 信号延续，但仍只能按资金仓位换算和人工复核分批。")
    if not actions:
        actions.append("候选复盘未发现硬阻断，继续按执行队列和账户约束复核。")
    actions.append("真实收益复盘需要导入成交价、持仓数量、费用和卖出记录，本模块不替代账户流水。")
    return actions[:6]


def _time_context(
    baseline: Dict[str, Any],
    current: Dict[str, Any],
    market_data: Dict[str, Any] | None,
) -> str:
    baseline_time = str(baseline.get("generated_at") or "")
    current_time = str(current.get("generated_at") or "")
    market_date = str((market_data or {}).get("date") or "")
    if baseline_time[:10] == current_time[:10]:
        return "同日简报对比；若尚未刷新下一交易日行情，只能验证信号延续，不能判断真实收益。"
    if market_date and baseline_time[:10].replace("-", "") >= market_date:
        return "行情日期未晚于基准简报，先等待下一交易日数据。"
    return "已有后续行情或简报，可做信号延续/降级复盘。"


def _evidence(scan: Dict[str, Any], market: Dict[str, Any], current: Dict[str, Any]) -> List[str]:
    evidence = []
    if scan:
        score = scan.get("score") or scan.get("quant_score")
        evidence.append(f"扫描信号 {scan.get('signal', 'unknown')}，分数 {score if score is not None else 'NA'}")
    if market:
        close = market.get("close")
        change = market.get("change_pct")
        evidence.append(f"行情 {market.get('data_date', '')}: 收盘 {close if close is not None else 'NA'}，涨跌 {change if change is not None else 'NA'}%")
    if current:
        evidence.append(f"当前简报审计 {current.get('audit_status', current.get('status_code', 'unknown'))}")
    return evidence or ["暂无当前行情/扫描证据"]


def _load_latest_json(root: Path, pattern: str) -> Tuple[Dict[str, Any] | None, Path | None]:
    files = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        data = _load_json(path)
        if data:
            return data, path
    return None, None


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _latest_market_date(market_map: Dict[str, Dict[str, Any]]) -> str:
    dates = sorted(str(item.get("data_date") or "") for item in market_map.values() if item.get("data_date"))
    return dates[-1] if dates else ""


def _candidate_code(item: Dict[str, Any]) -> str:
    code = str(item.get("code") or "").strip()
    if code:
        return code
    candidate = str(item.get("candidate") or "")
    return candidate.split(maxsplit=1)[0] if candidate else ""


def _name_list(rows: List[Dict[str, Any]]) -> str:
    return "、".join(str(row.get("candidate") or row.get("code") or "") for row in rows if row)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_review_table(rows: Iterable[Dict[str, Any]]) -> str:
    rows = list(rows)
    if not rows:
        return "暂无候选。"
    lines = [
        "| 候选 | 基准状态 | 当前状态 | 最新信号 | 最新分数 | 涨跌幅 | 复盘结论 | 动作 |",
        "|---|---|---|---|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {candidate} | {baseline} | {current} | {signal} | {score} | {chg} | {status} | {action} |".format(
                candidate=_safe(row.get("candidate")),
                baseline=_safe(row.get("baseline_audit") or row.get("baseline_status")),
                current=_safe(row.get("current_audit") or row.get("current_status")),
                signal=_safe(row.get("current_signal")),
                score=_safe(row.get("current_score")),
                chg=_safe(row.get("latest_change_pct")),
                status=_review_label(row.get("review_status")),
                action=_safe(row.get("review_action")),
            )
        )
    return "\n".join(lines)


def _review_label(status: Any) -> str:
    return {
        "signal_continues": "信号延续",
        "downgrade_review": "降级复核",
        "pending_evidence": "仍待补证据",
        "missing_current_data": "缺当前数据",
    }.get(str(status), str(status or ""))


def _safe(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/")
