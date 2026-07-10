from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop.config import load_desktop_config  # noqa: E402
from desktop.paths import HISTORY_DB_ENV, REPORTS_DIR_ENV, RUNTIME_DIR_ENV, build_desktop_env  # noqa: E402
from desktop.runtime import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    PASS_THROUGH_ENV_KEYS,
    create_launch_plan,
    start_streamlit,
    stop_process,
    wait_for_http_ok,
    wait_for_ready,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch NASDX Streamlit as a Windows-friendly desktop entry.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Local bind host. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=None, help=f"Local port. Defaults to {DEFAULT_PORT} when free.")
    parser.add_argument("--page", default=None, help="Optional Streamlit page key, for example plan or quant.")
    parser.add_argument("--webview", action="store_true", help="Open the app in optional pywebview window.")
    parser.add_argument("--window-title", default="NASDX Desktop", help="Desktop window title for --webview.")
    parser.add_argument("--browser", dest="browser", action="store_true", default=True, help="Open the app URL.")
    parser.add_argument("--no-browser", dest="browser", action="store_false", help="Do not open a browser window.")
    parser.add_argument("--dry-run", action="store_true", help="Print launch plan and exit without starting Streamlit.")
    parser.add_argument("--headless-smoke", action="store_true", help="Start Streamlit, wait until ready, then stop.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Startup readiness timeout in seconds.")
    return parser.parse_args(argv)


def plan_to_json(plan) -> str:
    env = build_desktop_env(plan.root)
    config = load_desktop_config(plan.root)
    payload = {
        "root": str(plan.root),
        "host": plan.host,
        "port": plan.port,
        "page": plan.page,
        "url": plan.url,
        "command": plan.command,
        "runtime_dir": env[RUNTIME_DIR_ENV],
        "history_db": env[HISTORY_DB_ENV],
        "reports_dir": env[REPORTS_DIR_ENV],
        "config_file": str(config.path),
        "config_exists": config.exists,
        "config_loaded_keys": config.loaded_keys,
        "env_passthrough": [key for key in PASS_THROUGH_ENV_KEYS if key in os.environ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = create_launch_plan(host=args.host, port=args.port, page=args.page)

    if args.dry_run:
        print(plan_to_json(plan))
        return 0

    process = start_streamlit(plan)
    try:
        if not wait_for_ready(plan.host, plan.port, timeout=args.timeout):
            print(f"NASDX desktop launch timed out waiting for {plan.url}", file=sys.stderr)
            return 1

        print(f"NASDX desktop app ready: {plan.url}")

        if args.headless_smoke:
            if not wait_for_http_ok(plan.url, timeout=args.timeout):
                print(f"NASDX desktop page smoke failed for {plan.url}", file=sys.stderr)
                return 1
            print(f"NASDX desktop page smoke OK: {plan.url}")
            return 0

        if args.webview:
            from desktop.webview_shell import open_webview

            opened = open_webview(plan.url, title=args.window_title, on_closed=lambda: stop_process(process))
            if opened:
                return 0
            print("pywebview is unavailable; falling back to browser.", file=sys.stderr)

        if args.browser:
            webbrowser.open(plan.url)

        process.wait()
        return int(process.returncode or 0)
    except KeyboardInterrupt:
        return 130
    finally:
        stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
