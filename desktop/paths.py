from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from desktop.config import absolute_path, load_desktop_config


APP_ROOT_ENV = "NASDX_APP_ROOT"
RUNTIME_DIR_ENV = "NASDX_RUNTIME_DIR"
REPORTS_DIR_ENV = "NASDX_REPORTS_DIR"
HISTORY_DB_ENV = "NASDX_HISTORY_DB"


def resolve_app_root(start: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    configured = source.get(APP_ROOT_ENV)
    if configured:
        root = absolute_path(Path(configured).expanduser())
        if _looks_like_app_root(root):
            return root
        raise FileNotFoundError(f"{APP_ROOT_ENV} does not point to a NASDX app root: {root}")

    start_path = (start or Path(__file__)).resolve()
    current = start_path if start_path.is_dir() else start_path.parent
    for candidate in (current, *current.parents):
        if _looks_like_app_root(candidate):
            return candidate
    raise FileNotFoundError("Could not find NASDX app root containing app.py")


def resolve_runtime_dir(app_root: Path, env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    configured = source.get(RUNTIME_DIR_ENV)
    if configured:
        return absolute_path(Path(configured).expanduser())

    if is_source_checkout(app_root):
        return app_root.resolve()

    return _windows_user_data_dir() / "NASDX"


def build_desktop_env(app_root: Path, parent_env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = dict(parent_env) if parent_env is not None else dict(os.environ)
    config = load_desktop_config(app_root, source)

    env = dict(source)
    for key, value in config.values.items():
        env.setdefault(key, value)

    runtime_dir = resolve_runtime_dir(app_root, env)

    env["PYTHONIOENCODING"] = "utf-8"
    env[APP_ROOT_ENV] = str(absolute_path(app_root))
    env[RUNTIME_DIR_ENV] = str(runtime_dir)
    env.setdefault(HISTORY_DB_ENV, str(runtime_dir / "nasdx_history.db"))
    env.setdefault(REPORTS_DIR_ENV, str(runtime_dir / "reports"))
    return env


def is_source_checkout(app_root: Path) -> bool:
    root = app_root.resolve()
    return (root / ".git").exists()


def _looks_like_app_root(path: Path) -> bool:
    return (path / "app.py").exists() and (path / "requirements_nasdx.txt").exists()


def _windows_user_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data)
    return Path.home() / "AppData" / "Local"
