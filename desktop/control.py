from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TextIO

from desktop.config import resolve_config_file
from desktop.paths import RUNTIME_DIR_ENV, build_desktop_env, resolve_app_root
from desktop.runtime import DEFAULT_HOST, LaunchPlan, create_launch_plan, stop_process, wait_for_ready


CONTROL_ACTIONS = ("Start", "Stop", "Open App", "Settings", "Logs", "Data Refresh")


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str
    url: str | None = None
    path: Path | None = None


@dataclass
class _ProcessRecord:
    process: subprocess.Popen
    log_handle: TextIO
    log_path: Path


def resolve_log_dir(app_root: Path, env: Mapping[str, str] | None = None, *, create: bool = False) -> Path:
    desktop_env = build_desktop_env(app_root, env)
    log_dir = Path(desktop_env[RUNTIME_DIR_ENV]) / "desktop_logs"
    if create:
        log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def ensure_user_config(app_root: Path, env: Mapping[str, str] | None = None) -> Path:
    config_path, _explicit = resolve_config_file(app_root, env)
    if config_path.exists():
        return config_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    example_path = app_root / "config.example.toml"
    if example_path.exists():
        shutil.copyfile(example_path, config_path)
    else:
        config_path.write_text('[llm]\napi_key = ""\nbase_url = ""\nmodel = ""\n', encoding="utf-8")
    return config_path


def data_refresh_command(app_root: Path) -> list[str]:
    return [sys.executable, "-B", str(app_root / "fetch_stock_data.py")]


def open_path(path: Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if path.exists():
        webbrowser.open(path.resolve().as_uri())
    else:
        webbrowser.open(str(path))


class DesktopSession:
    def __init__(
        self,
        *,
        root: Path | None = None,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        page: str | None = "plan",
        parent_env: Mapping[str, str] | None = None,
        opener: Callable[[str], object] = webbrowser.open,
        path_opener: Callable[[Path], object] = open_path,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        ready_probe: Callable[[str, int, float], bool] = wait_for_ready,
    ) -> None:
        self.root = resolve_app_root(root)
        self.host = host
        self.port = port
        self.page = page
        self.parent_env = dict(parent_env) if parent_env is not None else None
        self.opener = opener
        self.path_opener = path_opener
        self.popen_factory = popen_factory
        self.ready_probe = ready_probe
        self.plan: LaunchPlan | None = None
        self._app: _ProcessRecord | None = None
        self._refresh: _ProcessRecord | None = None

    @property
    def app_running(self) -> bool:
        return self._is_running(self._app)

    @property
    def refresh_running(self) -> bool:
        return self._is_running(self._refresh)

    def start_app(self, *, timeout: float = 30.0, wait: bool = True) -> ActionResult:
        if self.app_running and self.plan is not None:
            return ActionResult(True, f"NASDX is already running: {self.plan.url}", url=self.plan.url)

        self.plan = create_launch_plan(root=self.root, host=self.host, port=self.port, page=self.page)
        log_handle, log_path = self._open_log("streamlit.log")
        try:
            process = self._spawn(self.plan.command, log_handle)
        except Exception:
            log_handle.close()
            raise

        self._app = _ProcessRecord(process=process, log_handle=log_handle, log_path=log_path)
        if wait and not self.ready_probe(self.plan.host, self.plan.port, timeout):
            self.stop_app()
            return ActionResult(False, f"Timed out waiting for NASDX at {self.plan.url}", url=self.plan.url, path=log_path)
        return ActionResult(True, f"NASDX is running: {self.plan.url}", url=self.plan.url, path=log_path)

    def stop_app(self) -> ActionResult:
        if self._app is None:
            return ActionResult(True, "NASDX is not running.")
        record = self._app
        stop_process(record.process)
        self._close_record(record)
        self._app = None
        return ActionResult(True, "NASDX stopped.", path=record.log_path)

    def open_app(self) -> ActionResult:
        if self.plan is None:
            self.plan = create_launch_plan(root=self.root, host=self.host, port=self.port, page=self.page)
        self.opener(self.plan.url)
        return ActionResult(True, f"Opened NASDX: {self.plan.url}", url=self.plan.url)

    def open_settings(self) -> ActionResult:
        config_path = ensure_user_config(self.root, self.parent_env)
        self.path_opener(config_path)
        return ActionResult(True, f"Opened settings: {config_path}", path=config_path)

    def open_logs(self) -> ActionResult:
        log_dir = resolve_log_dir(self.root, self.parent_env, create=True)
        self.path_opener(log_dir)
        return ActionResult(True, f"Opened logs: {log_dir}", path=log_dir)

    def refresh_data(self) -> ActionResult:
        if self.refresh_running:
            assert self._refresh is not None
            return ActionResult(True, "Data refresh is already running.", path=self._refresh.log_path)

        log_handle, log_path = self._open_log("data_refresh.log")
        try:
            process = self._spawn(data_refresh_command(self.root), log_handle)
        except Exception:
            log_handle.close()
            raise
        self._refresh = _ProcessRecord(process=process, log_handle=log_handle, log_path=log_path)
        return ActionResult(True, "Data refresh started.", path=log_path)

    def shutdown(self) -> None:
        if self._app is not None:
            self.stop_app()
        if self._refresh is not None:
            record = self._refresh
            stop_process(record.process)
            self._close_record(record)
            self._refresh = None

    def dry_run_payload(self) -> dict[str, object]:
        config_path, _explicit = resolve_config_file(self.root, self.parent_env)
        return {
            "root": str(self.root),
            "actions": list(CONTROL_ACTIONS),
            "page": self.page,
            "config_file": str(config_path),
            "log_dir": str(resolve_log_dir(self.root, self.parent_env, create=False)),
            "data_refresh_command": data_refresh_command(self.root),
        }

    def _spawn(self, command: list[str], log_handle: TextIO) -> subprocess.Popen:
        return self.popen_factory(
            command,
            cwd=str(self.root),
            env=build_desktop_env(self.root, self.parent_env),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )

    def _open_log(self, name: str) -> tuple[TextIO, Path]:
        log_dir = resolve_log_dir(self.root, self.parent_env, create=True)
        log_path = log_dir / name
        return log_path.open("a", encoding="utf-8"), log_path

    @staticmethod
    def _is_running(record: _ProcessRecord | None) -> bool:
        return bool(record and record.process.poll() is None)

    @staticmethod
    def _close_record(record: _ProcessRecord) -> None:
        if not record.log_handle.closed:
            record.log_handle.close()
