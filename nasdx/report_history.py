"""Report history discovery for the NASDX product workflow."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from nasdx.paths import get_reports_dir


REPORT_PATTERNS = [
    ("investment_brief", "最终简报", "investment_brief_*.json"),
    ("portfolio_plan", "投资路线", "portfolio_plan_*.json"),
    ("recommendation_tracker", "建议漂移", "recommendation_tracker_*.json"),
    ("recommendation_review", "建议复盘", "recommendation_review_*.json"),
    ("account_review", "账户复盘", "account_review_*.json"),
    ("stock_selector", "今日选股", "stock_selector_*.json"),
    ("etf50", "ETF 50", "etf50_[0-9]*_[0-9]*.json"),
    ("stocks60", "个股扫描", "stocks60_*.json"),
    ("deep_report", "深度分析", "report_*.json"),
]


def list_report_history(
    limit: int = 40,
    reports_dir: str | Path | None = None,
) -> List[Dict[str, Any]]:
    """List recent generated JSON reports, newest first."""
    root = Path(reports_dir) if reports_dir else get_reports_dir()
    rows: list[Dict[str, Any]] = []
    if not root.exists():
        return rows

    for kind, label, pattern in REPORT_PATTERNS:
        for path in _matching_files(root, pattern):
            payload = _load_json(path)
            rows.append(_history_row(kind, label, path, payload))

    rows.sort(key=lambda item: item["modified_ts"], reverse=True)
    return rows[: max(0, int(limit))]


def _matching_files(root: Path, pattern: str) -> Iterable[Path]:
    for path in root.glob(pattern):
        if not path.is_file() or path.stem.endswith("_latest"):
            continue
        yield path


def _history_row(kind: str, label: str, path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    generated_at = str(payload.get("generated_at") or payload.get("datetime") or payload.get("date") or "")
    title = _title_for(kind, payload, path)
    return {
        "kind": kind,
        "label": label,
        "title": title,
        "generated_at": generated_at,
        "path": str(path),
        "file_name": path.name,
        "modified_at": _modified_at(path),
        "modified_ts": path.stat().st_mtime,
    }


def _title_for(kind: str, payload: Dict[str, Any], path: Path) -> str:
    if kind == "deep_report":
        code = str(payload.get("stock_code") or "")
        name = str(payload.get("stock_name") or "")
        return f"{code} {name}".strip() or path.stem
    if kind == "investment_brief":
        return str(payload.get("primary_bias") or "最终投资简报")
    if kind == "portfolio_plan":
        return str(payload.get("posture") or "投资路线")
    if kind == "stock_selector":
        summary = payload.get("summary") or {}
        return f"A级 {summary.get('tier_a', 0)} / B级 {summary.get('tier_b', 0)}"
    return path.stem


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _modified_at(path: Path) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
