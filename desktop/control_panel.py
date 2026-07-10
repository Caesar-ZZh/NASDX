from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop.control import CONTROL_ACTIONS, ActionResult, DesktopSession  # noqa: E402
from desktop.runtime import DEFAULT_HOST  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the NASDX Windows desktop control panel.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Local bind host. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=None, help="Local Streamlit port. Defaults to 8501 when free.")
    parser.add_argument("--page", default="plan", help="Initial Streamlit page key.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Startup readiness timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print control panel metadata and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = DesktopSession(host=args.host, port=args.port, page=args.page)

    if args.dry_run:
        print(json.dumps(session.dry_run_payload(), ensure_ascii=False, indent=2))
        return 0

    return run_gui(session, timeout=args.timeout)


def run_gui(session: DesktopSession, *, timeout: float = 30.0) -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception as exc:  # noqa: BLE001 - report GUI availability clearly.
        print(f"Tkinter is unavailable: {exc}", file=sys.stderr)
        return 1

    root = tk.Tk()
    root.title("NASDX Desktop")
    root.geometry("420x280")
    root.minsize(380, 240)

    status_var = tk.StringVar(value="NASDX Desktop is ready.")
    status = tk.Label(root, textvariable=status_var, anchor="w", justify="left", wraplength=380)
    status.pack(fill="x", padx=16, pady=(16, 8))

    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True, padx=16, pady=8)

    def report(result: ActionResult) -> None:
        status_var.set(result.message)

    def report_error(title: str, exc: BaseException) -> None:
        status_var.set(f"{title} failed: {exc}")
        messagebox.showerror(title, str(exc))

    def background(title: str, callback) -> None:
        def worker() -> None:
            try:
                result = callback()
            except Exception as exc:  # noqa: BLE001 - show desktop users a concrete error.
                root.after(0, lambda: report_error(title, exc))
                return
            root.after(0, lambda: report(result))

        threading.Thread(target=worker, daemon=True).start()

    actions = {
        "Start": lambda: background("Start", lambda: session.start_app(timeout=timeout)),
        "Stop": lambda: background("Stop", session.stop_app),
        "Open App": lambda: background("Open App", session.open_app),
        "Settings": lambda: background("Settings", session.open_settings),
        "Logs": lambda: background("Logs", session.open_logs),
        "Data Refresh": lambda: background("Data Refresh", session.refresh_data),
    }

    for index, label in enumerate(CONTROL_ACTIONS):
        button = tk.Button(frame, text=label, width=18, command=actions[label])
        button.grid(row=index // 2, column=index % 2, padx=8, pady=8, sticky="ew")

    frame.columnconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)

    def on_close() -> None:
        session.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
