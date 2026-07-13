"""Data-completeness contract shared by ETF scanning and cloud publishing."""
from __future__ import annotations

from typing import Any, Iterable


MIN_PUBLISH_COVERAGE = 0.80
VALID_SIGNALS = {"bullish", "neutral", "bearish"}


def summarize_scan_results(results: Iterable[dict[str, Any]], *, pool_total: int) -> dict[str, Any]:
    rows = list(results)
    counts = {signal: sum(row.get("signal") == signal for row in rows) for signal in VALID_SIGNALS}
    success_count = sum(counts.values())
    no_data_count = max(0, pool_total - success_count)
    coverage = success_count / pool_total if pool_total > 0 else 0.0
    if success_count == 0:
        status = "failed"
    elif coverage < MIN_PUBLISH_COVERAGE:
        status = "partial"
    else:
        status = "success"
    return {
        "pool_total": pool_total,
        "success_count": success_count,
        "no_data_count": no_data_count,
        "coverage_ratio": round(coverage, 4),
        "scan_status": status,
        **counts,
    }
