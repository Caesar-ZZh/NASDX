from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlencode
from urllib.request import urlopen

from desktop.paths import build_desktop_env, resolve_app_root


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8501
PASS_THROUGH_ENV_KEYS = (
    "NASDX_API_KEY",
    "NASDX_BASE_URL",
    "NASDX_MODEL",
    "NASDX_FALLBACK_MODELS",
    "NASDX_LLM_MAX_ATTEMPTS",
    "NASDX_LLM_MAX_ELAPSED_SECONDS",
    "NASDX_LLM_MAX_RETRY_DELAY_SECONDS",
    "NASDX_CONFIG_FILE",
    "NASDX_HISTORY_DB",
    "NASDX_RUNTIME_DIR",
    "NASDX_REPORTS_DIR",
)


@dataclass(frozen=True)
class LaunchPlan:
    root: Path
    host: str
    port: int
    page: str | None
    url: str
    command: list[str]


def find_project_root(start: Path | None = None) -> Path:
    """Find the repository root that contains app.py and requirements_nasdx.txt."""
    return resolve_app_root(start)


def find_free_port(host: str = DEFAULT_HOST, preferred: int = DEFAULT_PORT) -> int:
    """Return preferred when free, otherwise ask the OS for an available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, preferred))
            return int(sock.getsockname()[1])
        except OSError:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def build_url(host: str, port: int, page: str | None = None) -> str:
    base = f"http://{host}:{port}/"
    if not page:
        return base
    return f"{base}?{urlencode({'page': page})}"


def build_streamlit_command(root: Path, host: str, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(root / "app.py"),
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]


def build_streamlit_env(parent_env: Mapping[str, str] | None = None) -> dict[str, str]:
    return build_desktop_env(find_project_root(), parent_env)


def create_launch_plan(
    *,
    root: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    page: str | None = None,
) -> LaunchPlan:
    project_root = find_project_root(root)
    selected_port = port if port is not None else find_free_port(host)
    return LaunchPlan(
        root=project_root,
        host=host,
        port=selected_port,
        page=page,
        url=build_url(host, selected_port, page),
        command=build_streamlit_command(project_root, host, selected_port),
    )


def wait_for_ready(host: str, port: int, timeout: float = 30.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    health_url = f"http://{host}:{port}/_stcore/health"
    while time.monotonic() < deadline:
        try:
            with urlopen(health_url, timeout=min(interval, 2.0)) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(interval)
    return False


def wait_for_http_ok(url: str, timeout: float = 10.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=min(interval, 2.0)) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(interval)
    return False


def start_streamlit(plan: LaunchPlan, env: Mapping[str, str] | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        plan.command,
        cwd=str(plan.root),
        env=build_desktop_env(plan.root, env),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_process(process: subprocess.Popen, timeout: float = 8.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)
