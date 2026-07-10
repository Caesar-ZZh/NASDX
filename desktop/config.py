from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]


CONFIG_FILE_ENV = "NASDX_CONFIG_FILE"
APP_NAME = "NASDX"

LLM_ENV_KEYS = {
    "api_key": "NASDX_API_KEY",
    "base_url": "NASDX_BASE_URL",
    "model": "NASDX_MODEL",
}

PATH_ENV_KEYS = {
    "runtime_dir": "NASDX_RUNTIME_DIR",
    "history_db": "NASDX_HISTORY_DB",
    "reports_dir": "NASDX_REPORTS_DIR",
}


@dataclass(frozen=True)
class DesktopConfig:
    path: Path
    exists: bool
    values: dict[str, str]

    @property
    def loaded_keys(self) -> list[str]:
        return sorted(self.values)


def load_desktop_config(app_root: Path, env: Mapping[str, str] | None = None) -> DesktopConfig:
    source = dict(env) if env is not None else dict(os.environ)
    path, explicit = resolve_config_file(app_root, source)

    if not path.exists():
        if explicit:
            raise FileNotFoundError(f"{CONFIG_FILE_ENV} does not point to an existing config file: {path}")
        return DesktopConfig(path=path, exists=False, values={})

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid NASDX config TOML: {path}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"NASDX config must be a TOML table: {path}")

    values: dict[str, str] = {}
    values.update(_read_llm_table(data.get("llm"), path))
    values.update(_read_paths_table(data, path, source))
    return DesktopConfig(path=path, exists=True, values=values)


def resolve_config_file(app_root: Path, env: Mapping[str, str] | None = None) -> tuple[Path, bool]:
    source = dict(env) if env is not None else dict(os.environ)
    configured = source.get(CONFIG_FILE_ENV)
    if configured:
        return absolute_path(Path(os.path.expandvars(configured)).expanduser()), True

    user_config = user_config_dir(source) / "config.toml"
    if user_config.exists():
        return absolute_path(user_config), False

    source_config = absolute_path(app_root) / "config.toml"
    if source_config.exists():
        return absolute_path(source_config), False

    return absolute_path(user_config), False


def user_config_dir(env: Mapping[str, str] | None = None) -> Path:
    source = dict(env) if env is not None else dict(os.environ)
    roaming_app_data = source.get("APPDATA")
    if roaming_app_data:
        return Path(roaming_app_data).expanduser() / APP_NAME
    return Path.home() / ".config" / APP_NAME


def _read_llm_table(raw: object, path: Path) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"[llm] must be a table in {path}")

    values: dict[str, str] = {}
    for config_key, env_key in LLM_ENV_KEYS.items():
        value = _read_optional_string(raw, config_key, path)
        if not value:
            continue
        if config_key == "api_key" and _looks_like_placeholder(value):
            continue
        if config_key == "base_url" and not value.startswith(("http://", "https://")):
            raise ValueError(f"llm.base_url must start with http:// or https:// in {path}")
        values[env_key] = value
    return values


def _read_paths_table(data: Mapping[str, object], path: Path, env: Mapping[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}

    paths = data.get("paths")
    if paths is not None:
        if not isinstance(paths, dict):
            raise ValueError(f"[paths] must be a table in {path}")
        for config_key, env_key in PATH_ENV_KEYS.items():
            value = _read_optional_string(paths, config_key, path)
            if value:
                values[env_key] = _expand_config_path(value, env, path.parent)

    output = data.get("output")
    if output is not None:
        if not isinstance(output, dict):
            raise ValueError(f"[output] must be a table in {path}")
        output_dir = _read_optional_string(output, "dir", path)
        if output_dir and "NASDX_REPORTS_DIR" not in values:
            values["NASDX_REPORTS_DIR"] = _expand_config_path(output_dir, env, path.parent)

    return values


def _read_optional_string(table: Mapping[str, object], key: str, path: Path) -> str:
    value = table.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string in {path}")
    return value.strip()


def _expand_config_path(value: str, env: Mapping[str, str], base_dir: Path) -> str:
    expanded = value
    for key, replacement in env.items():
        expanded = expanded.replace(f"%{key}%", replacement)
    path = Path(os.path.expandvars(expanded)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(absolute_path(path))


def absolute_path(path: str | Path) -> Path:
    """Make a path absolute without rewriting Windows long names to 8.3 aliases."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return Path(os.path.normpath(os.fspath(candidate)))


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("your-api-key", "example", "placeholder", "填写", "xxxx"))
