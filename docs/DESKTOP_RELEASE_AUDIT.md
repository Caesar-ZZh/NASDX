# NASDX Desktop Release Audit

本轮范围：只做审计和文档，不运行 installer，不重新打包。结论先行：桌面 MVP 已成型，但当前正式 portable 包 release evidence 失败，这是正式发布前 P0 阻塞。

## Current Read-Only Evidence

| Check | Result |
|---|---|
| `python -B run_desktop_doctor.py --json` | PASS: 15 项通过，含 pywebview 和 Inno Setup 7 |
| `python -B run_desktop_completion_audit.py --json` | PASS: 10 项通过，含 installer roundtrip proof |
| `python -B run_desktop_release_evidence.py --json --package-dir dist\\NASDX-Desktop` | FAIL: `package_forbidden_failures=23` |

## Findings

| Priority | Problem | Evidence | Impact | Fix plan | Verification |
|---|---|---|---|---|---|
| P0 | 当前正式 portable 包证据失败：`.venv` 内有 `__pycache__` | `run_desktop_release_evidence.py:22`, `desktop/runtime.py:67`, `packaging/windows/smoke_installed.ps1:177`, `packaging/windows/build_portable.ps1:343`; 本轮 evidence 输出 `package_forbidden_failures=23` | 正式发布证据不能通过，zip/installer 可能基于 smoke 后污染包 | full package smoke 后再次 scrub，或让 smoke 使用临时 runtime 不污染 `.venv` | `python -B run_desktop_release_evidence.py --json --package-dir dist\\NASDX-Desktop` 必须返回 0 且 forbidden 为空 |
| P1 | `release_evidence` 在 zip/installer 命令前运行 | `run_desktop_release_check.py:140`, `run_desktop_release_check.py:209`, `run_desktop_release_check.py:215`, `README.md:395` | `--zip-package` 或 `--compile-installer` 时可能引用旧 zip/旧 installer | 调整 release gate 顺序：package -> smoke -> zip -> zip smoke -> installer -> evidence | `python -B run_desktop_release_check.py --zip-package --skip-final-audit` |
| P1 | Data Refresh 仍写应用目录 | `desktop/control.py:57`, `fetch_stock_data.py:23`, `fetch_stock_data.py:233`, `packaging/windows/smoke_installed.ps1:109` | 安装版点击刷新会生成 `stock_data_*.json` 到 `{app}` | 让数据刷新使用 `NASDX_RUNTIME_DIR` 或显式 output dir | installed smoke 检查 `{app}` 无 stock_data |
| P1 | 报告路径还没真正桌面化 | `app.py:103`, `app.py:1434`, `nasdx/portfolio.py:65`, `README.md:304`, `packaging/windows/NASDX-Desktop.iss:72` | 用户报告可能留在安装目录，卸载时丢失 | 统一 reports path service，逐步尊重 `NASDX_REPORTS_DIR` | 临时 reports dir 测 plan/brief/snapshot 全链路 |
| P2 | 安装器快捷方式仍指向 `.bat` | `packaging/windows/NASDX-Desktop.iss:63`, `packaging/windows/README.md:13`, `启动NASDX桌面.bat:6` | 普通用户可能看到控制台窗口 | 默认快捷方式指向 launcher exe，`.bat` 保留兜底 | 安装后快捷方式 target 验证 |
| P2 | `Open App` 不会自动启动服务 | `desktop/control.py:133`, `docs/WINDOWS_DESKTOP.md:89` | 用户先点 Open App 会打开未启动端口 | Open App 若服务未启动，则启动并等待 ready | control contract + manual smoke |
| P2 | installer plan-only 文案仍写 Inno Setup 6 | `packaging/windows/smoke_installer_roundtrip.ps1:98`, `packaging/windows/README.md:88` | 排障提示与 Inno Setup 7 支持不一致 | 文案改成 Inno Setup 7/6 | docs/contract marker |

## Release Validation Checklist

| Area | Must verify |
|---|---|
| Start | `.bat --dry-run --page plan`, control-panel dry-run, launcher headless smoke, browser fallback |
| Config | `%APPDATA%\\NASDX\\config.toml`, `NASDX_CONFIG_FILE`, no API key printing, Settings creates user config |
| Paths | `NASDX_RUNTIME_DIR`, `NASDX_HISTORY_DB`, `NASDX_REPORTS_DIR`; no Data Refresh/report/log writes into `{app}` |
| Portable | full package includes `.venv`; smoke after scrub; release evidence forbidden list empty |
| Zip | pre-zip forbidden scan, sha256/manifest, extract smoke, evidence points at current zip |
| Installer | preflight, compile, disposable profile roundtrip, proof hash matches setup, uninstall removes `{app}` and shortcuts |
| Uninstall | user config, external reports, and history DB survive |

