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
import os
import tempfile
import zipfile
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Iterable, List

from nasdx.investment_brief import build_and_save_investment_brief
from nasdx.paths import get_reports_dir
from nasdx.recommendation_review import build_recommendation_review, format_recommendation_review
from nasdx.recommendation_tracker import build_recommendation_tracker, format_recommendation_tracker


class SnapshotValidationError(ValueError):
    """Raised when a required snapshot source is missing or invalid."""


BRIEF_LIST_FIELDS = ("candidate_audits", "execution_queue", "external_review_pack")

def build_review_snapshot(
    risk_profile: str = "balanced",
    output_dir: str | Path | None = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Build a zipped review snapshot from latest NASDX investment artifacts."""
    if refresh:
        build_and_save_investment_brief(risk_profile=risk_profile)

    reports_dir = get_reports_dir()
    snapshot_dir = Path(output_dir) if output_dir else reports_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = snapshot_dir / f"nasdx_review_snapshot_{stamp}.zip"
    brief = _load_required_json(_latest_brief_json(), "investment brief")
    plan = _load_required_json(reports_dir / "portfolio_plan_latest.json", "portfolio plan")
    _validate_brief(brief)
    _validate_plan(plan)
    tracker = build_recommendation_tracker(reports_dir=reports_dir)
    review = build_recommendation_review(reports_dir=reports_dir)
    manifest = _manifest(brief, plan, tracker, review)

    descriptor, temp_name = tempfile.mkstemp(prefix=f".{zip_path.stem}-", suffix=".tmp", dir=snapshot_dir)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_if_exists(archive, reports_dir / "investment_brief_latest.md", "investment_brief_latest.md")
            archive.write(reports_dir / "investment_brief_latest.json", "investment_brief_latest.json")
            _write_if_exists(archive, reports_dir / "portfolio_plan_latest.md", "portfolio_plan_latest.md")
            archive.write(reports_dir / "portfolio_plan_latest.json", "portfolio_plan_latest.json")
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
            archive.writestr("candidate_audits.csv", _table_csv(brief["candidate_audits"], _audit_columns()))
            archive.writestr("execution_queue.csv", _table_csv(brief["execution_queue"], _queue_columns()))
            archive.writestr("external_review_pack.csv", _external_review_csv(brief["external_review_pack"]))
        with zipfile.ZipFile(temp_path, "r") as archive:
            if archive.testzip() is not None:
                raise OSError("snapshot ZIP integrity check failed")
        os.replace(temp_path, zip_path)
    finally:
        temp_path.unlink(missing_ok=True)

    return {
        "zip_path": str(zip_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "manifest": manifest,
        "files": _zip_listing(zip_path),
    }


def _latest_brief_json() -> Path:
    return get_reports_dir() / "investment_brief_latest.json"


def _load_required_json(path: Path, label: str) -> Dict[str, Any]:
    if not path.exists():
        raise SnapshotValidationError(f"required {label} is missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotValidationError(f"required {label} is unreadable or malformed: {path}") from exc
    if not isinstance(data, dict):
        raise SnapshotValidationError(f"required {label} must be a JSON object: {path}")
    return data


def _validate_brief(brief: Dict[str, Any]) -> None:
    if not isinstance(brief.get("generated_at"), str) or not brief["generated_at"].strip():
        raise SnapshotValidationError("investment brief generated_at must be a non-empty string")
    for field in BRIEF_LIST_FIELDS:
        if not isinstance(brief.get(field), list):
            raise SnapshotValidationError(f"investment brief {field} must be a list")
    if not isinstance(brief.get("source_files"), dict):
        raise SnapshotValidationError("investment brief source_files must be an object")


def _validate_plan(plan: Dict[str, Any]) -> None:
    if not isinstance(plan.get("generated_at"), str) or not plan["generated_at"].strip():
        raise SnapshotValidationError("portfolio plan generated_at must be a non-empty string")
    if not isinstance(plan.get("source_files"), dict):
        raise SnapshotValidationError("portfolio plan source_files must be an object")


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
        "schema": "nasdx_review_snapshot.v2",
        "validation_status": "valid",
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
            flattened = "；".join(str(x) for x in value)
        elif isinstance(value, dict):
            flattened = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            flattened = value
        flat[column] = _safe_csv_cell(flattened)
    return flat


def _safe_csv_cell(value: Any) -> Any:
    """Keep numeric values numeric and neutralize formula-like text for spreadsheets."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value)
    if text.lstrip("\t\r\n ").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


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
