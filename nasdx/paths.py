"""Runtime path helpers for NASDX reports and market snapshots."""
from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
APP_ROOT_ENV = "NASDX_APP_ROOT"
RUNTIME_DIR_ENV = "NASDX_RUNTIME_DIR"
REPORTS_DIR_ENV = "NASDX_REPORTS_DIR"
DATA_DIR_ENV = "NASDX_DATA_DIR"


def get_project_dir() -> Path:
    """Return the source or packaged application root."""
    configured = os.environ.get(APP_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return PROJECT_DIR


def get_runtime_dir(create: bool = False) -> Path:
    """Return the writable NASDX runtime directory.

    Source checkouts keep the historical default of writing beside the project.
    Desktop launchers can redirect all runtime data with NASDX_RUNTIME_DIR.
    """
    configured = os.environ.get(RUNTIME_DIR_ENV)
    path = Path(configured).expanduser().resolve() if configured else get_project_dir()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_reports_dir(create: bool = False) -> Path:
    """Return the reports directory, honoring NASDX_REPORTS_DIR when set."""
    configured = os.environ.get(REPORTS_DIR_ENV)
    path = (
        Path(configured).expanduser().resolve()
        if configured
        else get_runtime_dir(create=create) / "reports"
    )
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_market_data_dir(create: bool = False) -> Path:
    """Return the directory used for stock_data_YYYYMMDD.json snapshots."""
    configured = os.environ.get(DATA_DIR_ENV)
    path = Path(configured).expanduser().resolve() if configured else get_runtime_dir(create=create)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def latest_file(directory: Path, pattern: str) -> Path | None:
    """Return the newest file under a directory for a glob pattern."""
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None
