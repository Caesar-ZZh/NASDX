"""
Portfolio roadmap layer for NASDX.

This module aggregates scanner outputs and deep reports into a deterministic
investment roadmap. It is intentionally rules-based: AI reports explain a
single-name thesis, while this layer controls portfolio exposure, candidate
tiers, and review cadence.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from nasdx.data_quality import assess_data_quality
from nasdx.decision import RISK_PROFILES
from nasdx.history_store import record_artifact
from nasdx.paths import get_market_data_dir, get_reports_dir

EXPOSURE_RULES = {
    "conservative": {
        "max_total": "20%-35%",
        "etf_budget": "15%-25%",
        "stock_budget": "0%-10%",
        "single_stock_cap": "0%-5%",
        "cash_buffer": "65%-80%",
        "mode": "防守优先，先用 ETF 验证方向，个股只做小仓位观察。",
    },
    "balanced": {
        "max_total": "35%-60%",
        "etf_budget": "20%-35%",
        "stock_budget": "10%-25%",
        "single_stock_cap": "5%-10%",
        "cash_buffer": "40%-65%",
        "mode": "ETF 做主线暴露，个股做卫星增强，遇到风险红灯降仓。",
    },
    "aggressive": {
        "max_total": "50%-80%",
        "etf_budget": "25%-45%",
        "stock_budget": "20%-35%",
        "single_stock_cap": "5%-15%",
        "cash_buffer": "20%-50%",
        "mode": "允许更高试错仓位，但只在数据新鲜且信号一致时放大。",
    },
}

MIN_SCAN_COVERAGE = 0.50
MIN_COVERAGE_UNIVERSE = 10


def build_portfolio_plan(
    risk_profile: str = "balanced",
    max_etfs: int = 5,
    max_stocks: int = 5,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Build a portfolio-level roadmap from the latest local NASDX artifacts."""
    now = now or datetime.now()
    profile_key = risk_profile if risk_profile in RISK_PROFILES else "balanced"
    exposure = EXPOSURE_RULES[profile_key]

    reports_dir = get_reports_dir()
    data, data_path = _load_latest_json(get_market_data_dir(), "stock_data_*.json")
    etf_scan, etf_path = _load_latest_json(reports_dir, "etf50_[0-9]*_[0-9]*.json")
    stock_scan, stock_path = _load_latest_json(reports_dir, "stocks60_*.json")
    reports, stale_reports = _load_latest_reports(now)

    data_quality = assess_data_quality(data or {}, now=now) if data else _missing_quality("未找到行情数据文件，请先刷新行情。")
    etf_quality = _artifact_quality(etf_scan, etf_path, now)
    stock_quality = _artifact_quality(stock_scan, stock_path, now)
    usable_etf_scan = None if _is_low_coverage(etf_quality) else etf_scan
    usable_stock_scan = None if _is_low_coverage(stock_quality) else stock_scan

    ranked_etf_candidates = _rank_candidates(
        _iter_scan_rows(usable_etf_scan),
        asset_type="ETF",
        reports=reports,
    )
    ranked_stock_candidates = _rank_candidates(
        _iter_scan_rows(usable_stock_scan),
        asset_type="个股",
        reports=reports,
    )
    report_avoid = _report_avoid_candidates(ranked_etf_candidates + ranked_stock_candidates)
    etf_candidates = [item for item in ranked_etf_candidates if not _is_avoid_candidate(item)]
    stock_candidates = [item for item in ranked_stock_candidates if not _is_avoid_candidate(item)]

    action_gate = _combined_gate([data_quality, etf_quality, stock_quality])
    if action_gate == "refresh_required":
        posture = "先刷新，再决策"
        max_total = "0%-10%"
    elif action_gate == "position_cap":
        posture = "谨慎试错"
        max_total = _cap_total(exposure["max_total"], "0%-25%")
    else:
        posture = _market_posture(usable_etf_scan, usable_stock_scan)
        max_total = exposure["max_total"]

    core = etf_candidates[:max_etfs]
    satellite = stock_candidates[:max_stocks]
    watchlist = _build_watchlist(etf_candidates[max_etfs:], stock_candidates[max_stocks:])
    trim_or_avoid = _dedupe_candidates(_trim_or_avoid(usable_etf_scan, usable_stock_scan) + report_avoid)[:10]
    data_quality_map = {
        "market_data": data_quality,
        "etf_scan": etf_quality,
        "stock_scan": stock_quality,
        "deep_reports": _reports_quality(reports, stale_reports),
    }

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "risk_profile": profile_key,
        "risk_profile_label": RISK_PROFILES[profile_key]["label"],
        "posture": posture,
        "action_gate": action_gate,
        "allocation": _allocation_for_gate(exposure, max_total, action_gate),
        "core_candidates": core,
        "satellite_candidates": satellite,
        "watchlist": watchlist,
        "trim_or_avoid": trim_or_avoid,
        "next_actions": _next_actions(action_gate, core, satellite, reports, data_quality_map),
        "future_scenarios": _future_scenarios(action_gate, posture, core, satellite, trim_or_avoid),
        "decision_rules": _decision_rules(profile_key, action_gate),
        "monitoring_checklist": _monitoring_checklist(core, satellite),
        "review_cadence": [
            "盘中只看风险红灯，不因单日波动频繁换仓。",
            "每个交易日收盘后刷新扫描榜单。",
            "每周复核一次组合暴露和主题拥挤度。",
            "重大财报、公告、政策或海外指数急跌时立即复核。",
        ],
        "data_quality": data_quality_map,
        "source_files": {
            "market_data": str(data_path) if data_path else None,
            "etf_scan": str(etf_path) if etf_path else None,
            "stock_scan": str(stock_path) if stock_path else None,
            "deep_reports": {code: item["path"] for code, item in reports.items()},
            "stale_deep_reports": {code: item["path"] for code, item in stale_reports.items()},
        },
        "disclaimer": "研究辅助，不保证收益；下单前必须结合账户风险承受能力、最新行情和官方公告复核。",
    }


def format_portfolio_plan(plan: Dict[str, Any]) -> str:
    """Render a compact markdown roadmap."""
    lines = [
        "# NASDX 投资路线",
        "",
        f"- 生成时间：{plan.get('generated_at', '')}",
        f"- 风险画像：{plan.get('risk_profile_label', '')}",
        f"- 当前姿态：{plan.get('posture', '')}",
        f"- 总仓位上限：{plan.get('allocation', {}).get('max_total', '')}",
        f"- 现金缓冲：{plan.get('allocation', {}).get('cash_buffer', '')}",
        "",
        "## 仓位框架",
        "",
        f"- ETF 主线预算：{plan.get('allocation', {}).get('etf_budget', '')}",
        f"- 个股卫星预算：{plan.get('allocation', {}).get('stock_budget', '')}",
        f"- 单一个股上限：{plan.get('allocation', {}).get('single_stock_cap', '')}",
        f"- 执行模式：{plan.get('allocation', {}).get('mode', '')}",
        "",
        "## ETF 主线候选",
        "",
        _format_candidate_table(plan.get("core_candidates", [])),
        "",
        "## 个股卫星候选",
        "",
        _format_candidate_table(plan.get("satellite_candidates", [])),
        "",
        "## 观察名单",
        "",
        _format_candidate_table(plan.get("watchlist", [])[:8]),
        "",
        "## 回避/减仓池",
        "",
        _format_candidate_table(plan.get("trim_or_avoid", [])[:8]),
        "",
        "## 下一步",
        "",
        *[f"- {item}" for item in plan.get("next_actions", [])],
        "",
        "## 未来情景推演",
        "",
        _format_scenario_table(plan.get("future_scenarios", [])),
        "",
        "## 执行规则",
        "",
        *[f"- {item}" for item in plan.get("decision_rules", [])],
        "",
        "## 监控清单",
        "",
        *[f"- {item}" for item in plan.get("monitoring_checklist", [])],
        "",
        "## 复核节奏",
        "",
        *[f"- {item}" for item in plan.get("review_cadence", [])],
        "",
        "## 数据状态",
        "",
        *[
            f"- {label}：{quality.get('message', '未评估')}"
            for label, quality in plan.get("data_quality", {}).items()
        ],
        "",
        f"> {plan.get('disclaimer', '')}",
    ]
    return "\n".join(lines)


def save_portfolio_plan(plan: Dict[str, Any], output_dir: str | Path | None = None) -> Dict[str, str]:
    """Save markdown and JSON versions of the portfolio roadmap."""
    out_dir = Path(output_dir) if output_dir else get_reports_dir(create=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    md_path = out_dir / f"portfolio_plan_{stamp}.md"
    json_path = out_dir / f"portfolio_plan_{stamp}.json"
    md_path.write_text(format_portfolio_plan(plan), encoding="utf-8")
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_md = out_dir / "portfolio_plan_latest.md"
    latest_json = out_dir / "portfolio_plan_latest.json"
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    record_artifact(
        "portfolio_plan",
        "latest",
        plan,
        generated_at=plan.get("generated_at"),
        source_path=json_path,
    )
    return {
        "markdown": str(md_path),
        "json": str(json_path),
        "latest_markdown": str(latest_md),
        "latest_json": str(latest_json),
    }


def _load_latest_json(root: Path, pattern: str) -> Tuple[Dict[str, Any] | None, Path | None]:
    files = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            return json.loads(path.read_text(encoding="utf-8")), path
        except Exception:
            continue
    return None, None


def _load_latest_reports(now: datetime) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    reports: Dict[str, Dict[str, Any]] = {}
    stale_reports: Dict[str, Dict[str, Any]] = {}
    files = sorted(get_reports_dir().glob("report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        code = str(data.get("stock_code", ""))
        if not code or code in reports or code in stale_reports:
            continue
        quality = _artifact_quality(data, path, now)
        item = {"path": str(path), "data": data, "quality": quality}
        if quality.get("action_gate") == "refresh_required":
            stale_reports[code] = item
        else:
            reports[code] = item
    return reports, stale_reports


def _reports_quality(
    reports: Dict[str, Dict[str, Any]],
    stale_reports: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    active_count = len(reports)
    stale_count = len(stale_reports)
    if active_count == 0 and stale_count == 0:
        return {
            "status": "missing",
            "severity": "warning",
            "action_gate": "normal",
            "message": "未找到深度分析报告；组合路线仅使用规则扫描，候选升级前需补跑深度分析。",
            "active_count": 0,
            "stale_count": 0,
        }
    if active_count == 0:
        return {
            "status": "stale",
            "severity": "warning",
            "action_gate": "normal",
            "message": f"现有 {stale_count} 份深度报告已过期，未参与本次排序；候选升级前需重跑深度分析。",
            "active_count": 0,
            "stale_count": stale_count,
        }
    if stale_count:
        return {
            "status": "partial",
            "severity": "warning",
            "action_gate": "normal",
            "message": f"{active_count} 份深度报告可用，{stale_count} 份过期报告已排除。",
            "active_count": active_count,
            "stale_count": stale_count,
        }
    return {
        "status": "fresh",
        "severity": "ok",
        "action_gate": "normal",
        "message": f"{active_count} 份深度报告可用于辅助排序。",
        "active_count": active_count,
        "stale_count": 0,
    }


def _iter_scan_rows(scan: Dict[str, Any] | None) -> Iterable[Dict[str, Any]]:
    if not scan:
        return []
    rows = scan.get("results") or scan.get("top3") or []
    return rows if isinstance(rows, list) else []


def _rank_candidates(
    rows: Iterable[Dict[str, Any]],
    asset_type: str,
    reports: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        signal = row.get("signal") or row.get("factor_signal") or "neutral"
        if signal == "no_data":
            continue
        score = _score_of(row)
        code = str(row.get("code", ""))
        report = reports.get(code, {}).get("data", {})
        report_signal = report.get("final_signal")
        decision = report.get("decision_plan") or {}
        report_action = str(decision.get("action") or "")
        report_position_band = str(decision.get("position_band") or "")
        bullish_pct = report.get("bullish_pct")
        adjusted = score
        if signal == "bullish":
            adjusted += 8
        elif signal == "bearish":
            adjusted -= 18
        if report_signal == "bullish":
            adjusted += 10
        elif report_signal == "bearish":
            adjusted -= 20
        if _is_avoid_report_action(report_action):
            adjusted -= 35
        candidates.append(
            {
                "code": code,
                "name": row.get("name", code),
                "asset_type": asset_type,
                "category": row.get("category") or row.get("sector") or "",
                "score": round(score, 1),
                "adjusted_score": round(max(0, min(100, adjusted)), 1),
                "signal": signal,
                "deep_signal": report_signal,
                "bullish_pct": bullish_pct,
                "report_action": report_action,
                "report_position_band": report_position_band,
                "action": _candidate_action(signal, report_signal, adjusted, report_action),
                "reason": _candidate_reason(row, report),
            }
        )
    return sorted(candidates, key=lambda item: item["adjusted_score"], reverse=True)


def _score_of(row: Dict[str, Any]) -> float:
    for key in ("score", "quant_score"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    factor_score = row.get("factor_score")
    if isinstance(factor_score, (int, float)):
        return float(factor_score) * 100
    return 50.0


def _candidate_action(signal: str, deep_signal: str | None, adjusted_score: float, report_action: str = "") -> str:
    if signal == "bearish" or deep_signal == "bearish" or _is_avoid_report_action(report_action):
        return "回避/减仓"
    if deep_signal is None:
        if adjusted_score >= 65:
            return "观察，先补深度报告"
        return "只观察"
    if adjusted_score >= 80:
        return "优先跟踪，等待入场条件"
    if adjusted_score >= 65:
        return "观察试错"
    return "只观察"


def _is_avoid_report_action(action: str) -> bool:
    return "回避" in action or "减仓" in action


def _candidate_reason(row: Dict[str, Any], report: Dict[str, Any]) -> str:
    reasons = row.get("reasons") or []
    if reasons:
        return "；".join(str(item) for item in reasons[:2])
    decision = report.get("decision_plan") or {}
    flags = decision.get("risk_flags") or []
    if flags:
        return "；".join(str(item) for item in flags[:2])
    return "规则扫描与深度报告综合排序"


def _build_watchlist(etfs: List[Dict[str, Any]], stocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pool = [item for item in etfs + stocks if item.get("action") != "回避/减仓"]
    return sorted(pool, key=lambda item: item["adjusted_score"], reverse=True)[:10]


def _report_avoid_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for item in items if _is_avoid_candidate(item)]


def _is_avoid_candidate(item: Dict[str, Any]) -> bool:
    return item.get("action") == "回避/减仓"


def _dedupe_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for item in items:
        code = item.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        deduped.append(item)
    return deduped


def _trim_or_avoid(scan_a: Dict[str, Any] | None, scan_b: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    rows = list(_iter_scan_rows(scan_a)) + list(_iter_scan_rows(scan_b))
    weak = []
    for row in rows:
        signal = row.get("signal") or row.get("factor_signal")
        if signal != "bearish":
            continue
        weak.append(
            {
                "code": str(row.get("code", "")),
                "name": row.get("name", ""),
                "asset_type": "ETF" if row.get("category") else "个股",
                "category": row.get("category") or row.get("sector") or "",
                "score": round(_score_of(row), 1),
                "adjusted_score": round(_score_of(row), 1),
                "signal": signal,
                "deep_signal": None,
                "bullish_pct": None,
                "action": "回避/减仓",
                "reason": _candidate_reason(row, {}),
            }
        )
    return sorted(weak, key=lambda item: item["score"])[:10]


def _market_posture(etf_scan: Dict[str, Any] | None, stock_scan: Dict[str, Any] | None) -> str:
    bullish = int((etf_scan or {}).get("bullish", 0) or 0) + int((stock_scan or {}).get("bullish", 0) or 0)
    bearish = int((etf_scan or {}).get("bearish", 0) or 0) + int((stock_scan or {}).get("bearish", 0) or 0)
    neutral = int((etf_scan or {}).get("neutral", 0) or 0) + int((stock_scan or {}).get("neutral", 0) or 0)
    total = bullish + bearish + neutral
    if not total:
        return "等待扫描结果"
    bull_ratio = bullish / total
    if bull_ratio >= 0.55:
        return "顺势偏多"
    if bearish / total >= 0.45:
        return "防守优先"
    return "结构性轮动"


def _artifact_quality(data: Dict[str, Any] | None, path: Path | None, now: datetime) -> Dict[str, Any]:
    if not data:
        return _missing_quality("未找到扫描结果，请先运行对应扫描。")
    raw_dt = data.get("generated_at") or data.get("datetime")
    date_raw = data.get("date", "")
    if raw_dt or date_raw:
        quality = assess_data_quality({"generated_at": raw_dt, "date": date_raw}, now=now)
        return _with_scan_coverage(quality, data, path)
    if path:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        quality = assess_data_quality({"generated_at": mtime.isoformat(), "date": mtime.strftime("%Y%m%d")}, now=now)
        return _with_scan_coverage(quality, data, path)
    return _missing_quality("扫描结果缺少生成时间。")


def _with_scan_coverage(
    quality: Dict[str, Any],
    data: Dict[str, Any],
    path: Path | None,
) -> Dict[str, Any]:
    coverage = _scan_coverage(data, path)
    if not coverage:
        return quality

    merged = dict(quality)
    merged.update(coverage)
    base_message = str(quality.get("message") or "扫描数据已评估。")
    expected = coverage["expected_total"]
    valid = coverage["valid_count"]
    ratio = coverage["coverage_ratio"]
    coverage_text = f"扫描覆盖率 {valid}/{expected}（{ratio:.0%}）"

    if expected >= MIN_COVERAGE_UNIVERSE and ratio < MIN_SCAN_COVERAGE:
        merged["status"] = "low_coverage"
        merged["severity"] = "warning"
        if quality.get("action_gate") != "refresh_required":
            merged["action_gate"] = "position_cap"
        merged["message"] = f"{base_message}；{coverage_text}，本轮不把该榜单作为加仓依据。"
    else:
        merged["message"] = f"{base_message}；{coverage_text}。"
    return merged


def _scan_coverage(data: Dict[str, Any], path: Path | None) -> Dict[str, Any] | None:
    rows = list(_iter_scan_rows(data))
    expected_total = _as_nonnegative_int(
        data.get("expected_total") or data.get("scan_total") or data.get("universe_total")
    )
    if expected_total is None:
        expected_total = _expected_total_from_path(path)

    total = _as_nonnegative_int(data.get("total"))
    no_data = _as_nonnegative_int(data.get("no_data"))
    valid_count = _as_nonnegative_int(data.get("valid_count"))

    if valid_count is None:
        if rows:
            valid_count = sum(
                1
                for row in rows
                if row.get("has_data", True) is not False and row.get("signal") != "no_data"
            )
        elif total is not None and no_data is not None:
            valid_count = max(total - no_data, 0)
        else:
            valid_count = total

    if expected_total is None:
        if total is not None and no_data is not None:
            expected_total = max(total, (valid_count or 0) + no_data)
        elif total is not None:
            expected_total = max(total, valid_count or 0)
        else:
            expected_total = valid_count

    if expected_total is None or expected_total <= 0 or valid_count is None:
        return None

    valid_count = max(0, min(valid_count, expected_total))
    if no_data is None:
        no_data = max(expected_total - valid_count, 0)
    ratio = valid_count / expected_total
    return {
        "expected_total": expected_total,
        "valid_count": valid_count,
        "no_data": no_data,
        "coverage_ratio": round(ratio, 4),
    }


def _as_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _expected_total_from_path(path: Path | None) -> int | None:
    if not path:
        return None
    stem = path.stem.lower()
    if stem.startswith("etf50_"):
        return 50
    if stem.startswith("stocks60_"):
        return 60
    if stem.startswith("stocks50_"):
        return 50
    return None


def _is_low_coverage(quality: Dict[str, Any]) -> bool:
    return quality.get("status") == "low_coverage"


def _missing_quality(message: str) -> Dict[str, Any]:
    return {
        "status": "missing",
        "severity": "danger",
        "action_gate": "refresh_required",
        "data_date": "",
        "generated_at": None,
        "age_days": None,
        "message": message,
    }


def _combined_gate(qualities: Iterable[Dict[str, Any]]) -> str:
    gates = [item.get("action_gate") for item in qualities]
    if "refresh_required" in gates:
        return "refresh_required"
    if "position_cap" in gates:
        return "position_cap"
    return "normal"


def _cap_total(current: str, cap: str) -> str:
    order = {
        "0%-10%": 0,
        "0%-25%": 1,
        "20%-35%": 2,
        "35%-60%": 3,
        "50%-80%": 4,
    }
    return current if order.get(current, 99) <= order.get(cap, 99) else cap


def _next_actions(
    action_gate: str,
    core: List[Dict[str, Any]],
    satellite: List[Dict[str, Any]],
    reports: Dict[str, Dict[str, Any]],
    data_quality: Dict[str, Dict[str, Any]],
) -> List[str]:
    actions = []
    low_coverage_actions = _low_coverage_actions(data_quality)
    if low_coverage_actions:
        actions.extend(low_coverage_actions)
    elif action_gate == "refresh_required":
        target = (core[0]["code"] if core else satellite[0]["code"] if satellite else "603501")
        actions.append(f"先运行 `python run_investment_workflow.py {target} --workflow quick` 刷新行情和 ETF 扫描。")
    elif action_gate == "position_cap":
        actions.append("行情偏旧或扫描质量不足，本轮只允许小仓位验证，刷新后再放大仓位。")
    else:
        actions.append("按 ETF 主线优先、个股卫星补充的顺序建立观察和试错。")
        actions.extend(_deep_report_actions(core, satellite, reports))

    for item in core[:2] + satellite[:2]:
        if item.get("code") and item["code"] not in reports:
            actions.append(f"对 {item['code']} {item['name']} 跑深度分析，确认是否从观察升级为试错。")
    actions.append("所有候选必须满足入场条件后再执行，未满足时保留现金缓冲。")
    return actions[:6]


def _deep_report_actions(
    core: List[Dict[str, Any]],
    satellite: List[Dict[str, Any]],
    reports: Dict[str, Dict[str, Any]],
) -> List[str]:
    if not reports:
        return []

    actions: List[str] = []
    core_bullish = [item for item in core[:3] if item.get("deep_signal") == "bullish"]
    core_neutral = [item for item in core[:3] if item.get("deep_signal") == "neutral"]
    core_bearish = [item for item in core[:3] if item.get("deep_signal") == "bearish"]
    sat_bullish = [item for item in satellite[:3] if item.get("deep_signal") == "bullish"]
    sat_neutral = [item for item in satellite[:3] if item.get("deep_signal") == "neutral"]
    sat_bearish = [item for item in satellite[:3] if item.get("deep_signal") == "bearish"]

    if core_bullish:
        actions.append(f"ETF试错优先看 {_name_list(core_bullish[:2])}；只在回踩不破关键均线、成交/折溢价正常时分批。")
    if core_neutral:
        actions.append(f"{_name_list(core_neutral[:2])} 深度信号中性，保留主线观察，不作为加仓第一顺位。")
    if core_bearish:
        actions.append(f"{_name_list(core_bearish[:2])} 深度信号偏空，降级观察或剔出试错池。")
    if sat_bullish:
        actions.append(f"个股卫星只从 {_name_list(sat_bullish[:3])} 里小仓试错；公告/财报和成交复核不通过则不动。")
    if sat_neutral:
        actions.append(f"{_name_list(sat_neutral[:2])} 深度信号中性，等待趋势或基本面证据增强。")
    if sat_bearish:
        actions.append(f"{_name_list(sat_bearish[:2])} 深度信号偏空，暂不升级为试错。")
    return actions


def _low_coverage_actions(data_quality: Dict[str, Dict[str, Any]]) -> List[str]:
    labels = {"etf_scan": "ETF扫描", "stock_scan": "个股扫描"}
    actions = []
    for key, label in labels.items():
        item = data_quality.get(key, {})
        if not _is_low_coverage(item):
            continue
        valid = item.get("valid_count", 0)
        expected = item.get("expected_total", 0)
        actions.append(f"{label}覆盖率不足（{valid}/{expected}），本轮不把该榜单作为加仓依据；先重跑或修复对应扫描数据源。")
    return actions


def _future_scenarios(
    action_gate: str,
    posture: str,
    core: List[Dict[str, Any]],
    satellite: List[Dict[str, Any]],
    trim_or_avoid: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    top_core = _name_list(core[:3])
    top_satellite = _name_list(satellite[:3])
    weak = _name_list(trim_or_avoid[:3])

    if action_gate == "refresh_required":
        return [
            {
                "scenario": "数据恢复后再判断",
                "trigger": "行情、ETF扫描、个股扫描都刷新到2天内",
                "action": "重新生成投资路线；刷新前只观察，不扩大仓位",
                "position_rule": "总仓位0%-10%，现金90%-100%",
            },
            {
                "scenario": "刷新后强势延续",
                "trigger": f"ETF主线仍由 {top_core or '当前前排ETF'} 领涨，且看多数量占优",
                "action": "优先跑前排ETF深度分析，确认后小仓试错",
                "position_rule": "先用ETF 0%-10%验证，不直接上个股重仓",
            },
            {
                "scenario": "刷新后信号转弱",
                "trigger": f"{weak or '弱势池'} 扩大，或主线候选跌出前排",
                "action": "维持现金，回避弱势池，等待下一轮扫描修复",
                "position_rule": "总仓位维持0%-5%",
            },
        ]

    if action_gate == "position_cap":
        return [
            {
                "scenario": "数据质量修复",
                "trigger": "行情仍在2天内，且ETF/个股扫描覆盖率恢复到50%以上",
                "action": "重新生成投资路线；覆盖恢复后再开放个股卫星候选",
                "position_rule": "修复前总仓位0%-25%，修复后再按风险画像上限分批",
            },
            {
                "scenario": "ETF主线延续",
                "trigger": f"{top_core or 'ETF主线'} 继续保持前排，深度报告未出现风险红灯",
                "action": f"只用ETF主线小仓验证：{top_core or '前排ETF'}；暂不新增个股卫星",
                "position_rule": "ETF 0%-20%，个股0%，现金75%-100%",
            },
            {
                "scenario": "质量未修复且信号转弱",
                "trigger": f"{weak or '弱势池'} 扩大，或前排ETF连续跌出候选",
                "action": "维持现金，不用低覆盖榜单寻找替代标的",
                "position_rule": "总仓位维持0%-10%，等待下一轮有效扫描",
            },
        ]

    return [
        {
            "scenario": "顺势偏多",
            "trigger": f"{posture} 且 {top_core or 'ETF主线'} 连续保持高分，深度报告未出现风险红灯",
            "action": f"ETF主线优先：{top_core or '前排ETF'}；个股只选择深度分析确认的 {top_satellite or '前排个股'}",
            "position_rule": "按风险画像上限分批加仓，单日不一次性打满",
        },
        {
            "scenario": "结构轮动",
            "trigger": "ETF前排更换、个股强弱分化、看多/中性接近",
            "action": "降低个股卫星，保留ETF主线；新主题必须连续两次扫描靠前再进入观察",
            "position_rule": "总仓位降到画像上限的50%-70%",
        },
        {
            "scenario": "防守下行",
            "trigger": f"弱势池扩大，{weak or '当前弱势资产'} 继续走弱，或市场宽度转空",
            "action": "减掉弱势和未验证个股，保留现金；只跟踪防御或低波ETF",
            "position_rule": "总仓位降到0%-20%，个股不新增",
        },
    ]


def _decision_rules(profile_key: str, action_gate: str) -> List[str]:
    rules = [
        "先看数据闸门：行情或扫描过期时，不把任何榜单当作交易执行依据。",
        "先ETF后个股：ETF用于确认主线方向，个股必须经过深度分析再升级为试错。",
        "先分批后加仓：只有扫描、深度报告、风险维度三者同步时才提高仓位。",
        "先退出再寻找新机会：跌破止损/风险维度转空时先降仓，不用新标的掩盖旧风险。",
    ]
    if profile_key == "conservative":
        rules.append("保守画像：任何单一标的不得成为组合主风险来源，现金缓冲优先。")
    elif profile_key == "aggressive":
        rules.append("进取画像：可以更快试错，但数据过期、风险红灯、资金流转弱任一出现都要降仓。")
    else:
        rules.append("均衡画像：ETF主线和个股卫星分开管理，避免主题过度集中。")
    if action_gate == "refresh_required":
        rules.insert(0, "当前数据闸门关闭：刷新前只允许观察或极小仓验证。")
    elif action_gate == "position_cap":
        rules.insert(0, "当前仓位闸门半开：扫描覆盖不足或数据偏旧时，总仓位降到0%-25%，不新增个股卫星。")
    return rules


def _monitoring_checklist(core: List[Dict[str, Any]], satellite: List[Dict[str, Any]]) -> List[str]:
    targets = core[:3] + satellite[:3]
    checklist = [
        "每天收盘后刷新 `run_portfolio_plan.py`，确认总仓位上限有没有变化。",
        "每次加仓前检查 `portfolio_plan_latest.json` 的 `action_gate` 是否为 normal。",
        "每周检查前排候选是否连续稳定，避免只追单日高分。",
    ]
    for item in targets[:4]:
        checklist.append(f"跟踪 {item.get('code')} {item.get('name')}：若跌出候选前排或深度信号转空，降级为观察。")
    return checklist


def _allocation_for_gate(exposure: Dict[str, str], max_total: str, action_gate: str) -> Dict[str, str]:
    allocation = {
        "max_total": max_total,
        "etf_budget": exposure["etf_budget"],
        "stock_budget": exposure["stock_budget"],
        "single_stock_cap": exposure["single_stock_cap"],
        "cash_buffer": exposure["cash_buffer"],
        "mode": exposure["mode"],
    }
    if action_gate == "refresh_required":
        allocation.update(
            {
                "etf_budget": "0%-10%",
                "stock_budget": "0%-5%",
                "single_stock_cap": "0%-3%",
                "cash_buffer": "90%-100%",
                "mode": "数据过期时只允许观察或极小仓验证，刷新行情前不放大仓位。",
            }
        )
    elif action_gate == "position_cap":
        allocation.update(
            {
                "etf_budget": "0%-20%",
                "stock_budget": "0%",
                "single_stock_cap": "0%",
                "cash_buffer": "75%-100%",
                "mode": "数据偏旧或扫描覆盖不足时仓位上限下调；本轮只允许ETF小仓验证，个股卫星等覆盖恢复后再开放。",
            }
        )
    return allocation


def _name_list(items: List[Dict[str, Any]]) -> str:
    names = [f"{item.get('code')} {item.get('name')}" for item in items if item.get("code")]
    return "、".join(names)


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


def _format_candidate_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "暂无候选。"
    rows = ["| 代码 | 名称 | 类型 | 分数 | 信号 | 动作 | 理由 |", "|---|---|---|---:|---|---|---|"]
    for item in items:
        rows.append(
            "| {code} | {name} | {asset_type} | {score:.1f} | {signal} | {action} | {reason} |".format(
                code=item.get("code", ""),
                name=item.get("name", ""),
                asset_type=item.get("asset_type", ""),
                score=float(item.get("adjusted_score", 0) or 0),
                signal=item.get("signal", ""),
                action=item.get("action", ""),
                reason=str(item.get("reason", "")).replace("|", "/"),
            )
        )
    return "\n".join(rows)
