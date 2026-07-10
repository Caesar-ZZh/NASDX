from __future__ import annotations

import shutil
from pathlib import Path


def find_iscc() -> str | None:
    for candidate in _candidate_paths():
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    return None


def _candidate_paths() -> list[str]:
    candidates: list[str | None] = [
        shutil.which("ISCC.exe"),
        shutil.which("iscc"),
    ]
    candidates.extend(_registry_candidates())
    candidates.extend(
        [
            r"C:\Program Files\Inno Setup 7\ISCC.exe",
            r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        ]
    )
    return [str(Path(item)) for item in candidates if item]


def _registry_candidates() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []

    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    candidates: list[str] = []
    for root, subkey in roots:
        try:
            with winreg.OpenKey(root, subkey) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        app_key_name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, app_key_name) as app_key:
                            values = _read_registry_values(winreg, app_key)
                    except OSError:
                        continue
                    if not _looks_like_inno(values):
                        continue
                    install_location = values.get("InstallLocation", "")
                    if install_location:
                        candidates.append(str(Path(install_location) / "ISCC.exe"))
                    for name in ("DisplayIcon", "UninstallString"):
                        parent = _parent_from_registry_command(values.get(name, ""))
                        if parent:
                            candidates.append(str(parent / "ISCC.exe"))
        except OSError:
            continue
    return candidates


def _read_registry_values(winreg_module, key) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in ("DisplayName", "InstallLocation", "DisplayIcon", "UninstallString"):
        try:
            values[name] = str(winreg_module.QueryValueEx(key, name)[0])
        except OSError:
            values[name] = ""
    return values


def _looks_like_inno(values: dict[str, str]) -> bool:
    haystack = "\n".join(values.values()).lower()
    return "inno setup" in haystack


def _parent_from_registry_command(value: str) -> Path | None:
    command = value.strip()
    if not command:
        return None
    if command.startswith('"'):
        end_quote = command.find('"', 1)
        if end_quote > 1:
            command = command[1:end_quote]
    else:
        command = command.split(",", 1)[0].strip('"')
    path = Path(command)
    return path.parent if path.name else None
