"""Run the ETF50 scan and publish one validated report to the deploy branch."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from nasdx.cloud_sync import CloudSyncError, publish_latest_etf_report
from nasdx.paths import get_reports_dir


ROOT = Path(__file__).parent


def run_etf50_scan() -> None:
    """Run the existing scanner without leaving a global requests patch behind."""
    import requests

    original_get = requests.get

    def patched_get(url, **kwargs):
        if "eastmoney" in url:
            session = requests.Session()
            session.trust_env = True
            return session.get(url, **kwargs)
        return original_get(url, **kwargs)

    print(f"\n{'=' * 55}")
    print(f"  ETF50 扫描  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'=' * 55}\n")
    requests.get = patched_get
    try:
        scan_path = ROOT / "scan_etf50.py"
        namespace = {"__name__": "__scan__", "__file__": str(scan_path)}
        source = scan_path.read_text(encoding="utf-8")
        exec(compile(source, str(scan_path), "exec"), namespace)
    finally:
        requests.get = original_get


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ETF50 scan and publish a validated report.")
    parser.add_argument("--no-sync", action="store_true", help="run the scan without publishing")
    args = parser.parse_args(argv)

    try:
        run_etf50_scan()
    except Exception as exc:
        print(f"[SCAN_FAILED] {str(exc)[:300]}")
        return 1
    print("[SCAN_OK] ETF50 scan completed")

    if args.no_sync:
        print("[PUBLISH_SKIPPED] --no-sync")
        return 0

    try:
        result = publish_latest_etf_report(ROOT, reports_dir=get_reports_dir(create=True))
    except CloudSyncError as exc:
        print(f"[PUBLISH_FAILED] {str(exc)[:300]}")
        return 1

    if result.status == "no_changes":
        print(f"[PUBLISH_SKIPPED] no changes for {result.artifact}")
    else:
        print(f"[PUBLISH_OK] {result.artifact} @ {result.commit[:12] if result.commit else 'unknown'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
