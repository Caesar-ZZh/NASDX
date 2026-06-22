"""
Review snapshot export for NASDX.

The snapshot packages the current portfolio plan, final investment brief,
candidate audits, execution queue, external review pack, and source manifest
into a zip file for post-market review and decision traceability.
"""
from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Iterable, List

from nasdx.investment_brief import build_and_save_investment_brief
from nasdx.recommendation_review import build_recommendation_review, format_recommendation_review
from nasdx.recommendation_tracker import build_recommendation_tracker, format_recommendation_tracker


PROJECT_DIR = Path(__file__).parent.parent


def build_review_snapshot(
    risk_profile: str = "balanced",
    output_dir: str | Path | None = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Build a zipped review snapshot from latest NASDX investment artifacts."""
    if refresh or not _latest_brief_json().exists():
        build_and_save_investment_brief(risk_profile=risk_profile)

    reports_dir = PROJECT_DIR / "reports"
    snapshot_dir = Path(output_dir) if output_dir else reports_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = snapshot_dir / f"nasdx_review_snapshot_{stamp}.zip"
    brief = _load_json(_latest_brief_json())
    plan = _load_json(reports_dir / "portfolio_plan_latest.json")
    tracker = build_recommendation_tracker(reports_dir=reports_dir)
    review = build_recommendation_review(reports_dir=reports_dir)
    manifest = _manifest(brief, plan, tracker, review)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_if_exists(archive, reports_dir / "investment_brief_latest.md", "investment_brief_latest.md")
        _write_if_exists(archive, reports_dir / "investment_brief_latest.json", "investment_brief_latest.json")
        _write_if_exists(archive, reports_dir / "portfolio_plan_latest.md", "portfolio_plan_latest.md")
        _write_if_exists(archive, reports_dir / "portfolio_plan_latest.json", "portfolio_plan_latest.json")
        archive.writestr("recommendation_tracker.md", format_recommendation_tracker(tracker))
        archive.writestr(
            "recommendation_tracker.json",
            json.dumps({k: v for k, v in tracker.items() if k != "markdown"}, ensure_ascii=False, indent=2),
        )
        archive.writestr("recommendation_review.md", format_recommendation_review(review))
        archive.writestr(
            "recommendation_review.json",
            json.dumps({k: v for k, v in review.items() if k != "markdown"}, ensure_ascii=False, indent=2),
        )
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("candidate_audits.csv", _table_csv(brief.get("candidate_audits", []), _audit_columns()))
        archive.writestr("execution_queue.csv", _table_csv(brief.get("execution_queue", []), _queue_columns()))
        archive.writestr("external_review_pack.csv", _external_review_csv(brief.get("external_review_pack", [])))

    return {
        "zip_path": str(zip_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "manifest": manifest,
        "files": _zip_listing(zip_path),
    }


def _latest_brief_json() -> Path:
    return PROJECT_DIR / "reports" / "investment_brief_latest.json"


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _manifest(
    brief: Dict[str, Any],
    plan: Dict[str, Any],
    tracker: Dict[str, Any] | None = None,
    review: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    source_files = brief.get("source_files") or plan.get("source_files") or {}
    candidates = brief.get("candidate_audits", [])
    queue = brief.get("execution_queue", [])
    external_review = brief.get("external_review_pack", [])
    tracker = tracker or {}
    review = review or {}
    tracker_counts = tracker.get("counts", {})
    review_counts = review.get("counts", {})
    return {
        "schema": "nasdx_review_snapshot.v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "brief_generated_at": brief.get("generated_at"),
        "plan_generated_at": plan.get("generated_at"),
        "tracker_generated_at": tracker.get("generated_at"),
        "tracker_comparison_status": tracker.get("comparison_status"),
        "review_generated_at": review.get("generated_at"),
        "review_baseline_generated_at": review.get("baseline_generated_at"),
        "risk_profile": brief.get("risk_profile"),
        "risk_profile_label": brief.get("risk_profile_label"),
        "action_gate": brief.get("action_gate"),
        "posture": brief.get("posture"),
        "candidate_count": len(candidates),
        "execution_action_count": len(queue),
        "external_review_count": len(external_review),
        "recommendation_changes": {
            "added": tracker_counts.get("added", 0),
            "removed": tracker_counts.get("removed", 0),
            "changed": tracker_counts.get("changed", 0),
        },
        "recommendation_review": {
            "signal_continues": review_counts.get("signal_continues", 0),
            "downgrade_review": review_counts.get("downgrade_review", 0),
            "pending_evidence": review_counts.get("pending_evidence", 0),
            "missing_current_data": review_counts.get("missing_current_data", 0),
        },
        "trial_candidates": [
            item.get("candidate", "")
            for item in candidates
            if item.get("status_code") == "trial_candidate"
        ],
        "pending_manual_review": [
            item.get("candidate", "")
            for item in external_review
            if item.get("review_status") == "pending_manual_review"
        ],
        "source_files": _source_manifest(source_files),
        "boundary": "研究辅助快照；外部复核包链接存在不代表复核已通过。",
    }


def _source_manifest(source_files: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in source_files.items():
        if isinstance(value, dict):
            result[key] = {code: _file_info(Path(path)) for code, path in value.items()}
        elif value:
            result[key] = _file_info(Path(str(value)))
        else:
            result[key] = None
    return result


def _file_info(path: Path) -> Dict[str, Any]:
    exists = path.exists()
    info: Dict[str, Any] = {"path": str(path), "exists": exists}
    if not exists or not path.is_file():
        return info
    info["bytes"] = path.stat().st_size
    info["sha256"] = _sha256(path)
    return info


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_if_exists(archive: zipfile.ZipFile, path: Path, arcname: str) -> None:
    if path.exists() and path.is_file():
        archive.write(path, arcname)


def _table_csv(rows: Iterable[Dict[str, Any]], columns: List[str]) -> str:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(_flatten_row(row, columns))
    return output.getvalue()


def _external_review_csv(rows: Iterable[Dict[str, Any]]) -> str:
    columns = [
        "candidate",
        "review_status",
        "review_gate",
        "must_pass_before",
        "required_checks",
        "source_links",
        "failure_action",
    ]
    flattened = []
    for row in rows:
        item = dict(row)
        item["required_checks"] = "；".join(str(x) for x in row.get("required_checks", []))
        item["source_links"] = "；".join(
            f"{link.get('label')}={link.get('url')}" for link in row.get("source_links", [])
        )
        flattened.append(item)
    return _table_csv(flattened, columns)


def _flatten_row(row: Dict[str, Any], columns: List[str]) -> Dict[str, str]:
    flat = {}
    for column in columns:
        value = row.get(column, "")
        if isinstance(value, list):
            flat[column] = "；".join(str(x) for x in value)
        elif isinstance(value, dict):
            flat[column] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            flat[column] = str(value)
    return flat


def _audit_columns() -> List[str]:
    return [
        "candidate",
        "type",
        "audit_status",
        "status_code",
        "scan_signal",
        "deep_signal",
        "score",
        "report_action",
        "report_position_band",
        "manual_checks",
        "blocking_flags",
    ]


def _queue_columns() -> List[str]:
    return ["stage", "target", "decision", "action", "condition", "blocker", "command"]


def _zip_listing(path: Path) -> List[str]:
    with zipfile.ZipFile(path, "r") as archive:
        return archive.namelist()
