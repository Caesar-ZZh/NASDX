# NASDX Windows Desktop Guide

This guide describes the Windows desktop path for NASDX without replacing the existing Streamlit app or CLI scripts.

## What stays the same

- `app.py` remains the Streamlit UI entry.
- Existing CLI scripts remain usable, including `scan_etf50.py`, `scan_stocks_full.py`, `run_analysis.py`, `run_investment_workflow.py`, `run_portfolio_plan.py`, and `run_final_audit.py`.
- Reports still use the current `reports/` structure.
- API keys must stay in environment variables, Streamlit session input, or local user config outside Git.

## Install for development

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements-dev.txt
```

Run the regular local Streamlit app:

```powershell
streamlit run app.py
```

Run the desktop launcher dry-run:

```powershell
python -B desktop\launcher.py --dry-run --page plan
```

Run a desktop smoke check:

```powershell
python -B desktop\launcher.py --headless-smoke --timeout 30 --no-browser --page plan
```

Run a read-only desktop environment diagnostic:

```powershell
python -B run_desktop_doctor.py
python -B run_desktop_doctor.py --json
```

Run the read-only desktop completion evidence matrix:

```powershell
python -B run_desktop_completion_audit.py
python -B run_desktop_completion_audit.py --json
```

Use the write probe only when you want to verify runtime/report paths:

```powershell
python -B run_desktop_doctor.py --check-write
```

The doctor reports config paths and loaded key names only; it does not print API key values.

Open the desktop control panel:

```powershell
python -B desktop\control_panel.py
```

Run the Windows desktop batch entry:

```powershell
.\启动NASDX桌面.bat
.\启动NASDX桌面.bat --dry-run --page plan
```

The batch passes arguments through to the control panel, so `--dry-run` verifies the real desktop entry without opening the GUI.

Preview or create current-user shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\create_shortcuts.ps1 -Desktop
powershell -ExecutionPolicy Bypass -File packaging\windows\create_shortcuts.ps1 -Desktop -Apply
```

Without `-Apply`, `create_shortcuts.ps1` only prints the Start Menu and Desktop paths it would write. With `-Apply`, shortcuts point to `启动NASDX桌面.bat`, not directly to `app.py`.

The control panel provides these user-facing entry points:

| Button | Action |
|---|---|
| Start | Start the existing Streamlit `app.py` locally. |
| Stop | Stop only the Streamlit child process started by the panel. |
| Open App | Open the current local NASDX URL. |
| Settings | Open or create the local user config file. |
| Logs | Open the desktop log folder. |
| Data Refresh | Run the existing `fetch_stock_data.py` refresh command. |

Direct launcher command for development and smoke tests:

```powershell
python -B desktop\launcher.py --page plan
```

## Local user configuration

Recommended config path:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\NASDX"
Copy-Item config.example.toml "$env:APPDATA\NASDX\config.toml"
notepad "$env:APPDATA\NASDX\config.toml"
```

Alternative explicit config path:

```powershell
$env:NASDX_CONFIG_FILE="D:\secure\nasdx\config.toml"
python -B desktop\launcher.py --dry-run --page plan
```

Configuration priority:

1. Current process environment variables.
2. `NASDX_CONFIG_FILE`.
3. `%APPDATA%\NASDX\config.toml`.
4. Ignored project-local `config.toml`.

Allowed config mappings:

| TOML field | Runtime environment variable |
|---|---|
| `llm.api_key` | `NASDX_API_KEY` |
| `llm.base_url` | `NASDX_BASE_URL` |
| `llm.model` | `NASDX_MODEL` |
| `paths.runtime_dir` | `NASDX_RUNTIME_DIR` |
| `paths.history_db` | `NASDX_HISTORY_DB` |
| `paths.reports_dir` | `NASDX_REPORTS_DIR` |

`desktop\launcher.py --dry-run` reports only config path metadata and loaded field names. It must not print API key values.

## Portable package

Build the portable folder quickly without installing dependencies:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
```

Build the portable folder with an internal `.venv`:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -PipTimeout 30 -PipRetries 1
```

Optionally validate the launcher-only executable path:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_launcher_exe.ps1 -SkipBuild
```

`build_launcher_exe.ps1` is intentionally not a full one-file application build. It uses PyInstaller only for the tiny `desktop\exe_launcher.py` shim, which delegates to `.venv\Scripts\python.exe -B desktop\control_panel.py` from the portable folder. It must not bundle `app.py`, AkShare, pandas, reports, local config, logs, or cache files into the executable. Remove `-SkipBuild` only on a packaging machine where PyInstaller is already installed.

Smoke test the portable folder:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 60
```

The portable smoke verifies launcher dry-run, control-panel dry-run, desktop doctor JSON output, shortcut plan-only output, headless page startup, and process cleanup.

Build and smoke-test a distributable portable zip:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable_zip.ps1 -PackageDir dist\NASDX-Desktop -OutputZip dist\NASDX-Desktop-portable.zip
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable_zip.ps1 -ZipPath dist\NASDX-Desktop-portable.zip -Timeout 60
```

The zip build also writes `dist\NASDX-Desktop-portable.zip.sha256` and `dist\NASDX-Desktop-portable.manifest.json`. Zip smoke verifies the SHA256 sidecar and `nasdx_portable_release.v1` manifest before extraction.

For dependency-contained artifacts, add `-RequireVenv` to both zip commands after building with the internal `.venv`.

Launch the packaged desktop entry:

```powershell
dist\NASDX-Desktop\启动NASDX桌面.bat
dist\NASDX-Desktop\启动NASDX桌面.bat --dry-run --page plan
```

Preview or create current-user shortcuts from the portable package:

```powershell
powershell -ExecutionPolicy Bypass -File dist\NASDX-Desktop\packaging\windows\create_shortcuts.ps1 -Desktop
powershell -ExecutionPolicy Bypass -File dist\NASDX-Desktop\packaging\windows\create_shortcuts.ps1 -Desktop -Apply
```

The packaged batch opens the control panel first. If the panel cannot start, it falls back to the direct launcher. The launcher starts the existing Streamlit app. If `pywebview` is not installed, it falls back to the browser.

## Installer wrapper

The installer layer is a thin wrapper around the tested portable folder. Build the portable package first:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1
```

Compile the installer with Inno Setup:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild
```

Expected installer output:

```powershell
dist\installer\NASDX-Desktop-Setup.exe
```

Validate installer inputs without compiling:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild -SkipCompile
```

Prepare the Inno Setup compiler on a packaging machine. The first command is plan-only; the second command is the explicit installer bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\install_inno_setup.ps1
powershell -ExecutionPolicy Bypass -File packaging\windows\install_inno_setup.ps1 -Install -AcceptAgreements
```

Run the read-only installer release preflight before compiling:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\preflight_installer_release.ps1 -RequireVenv
```

The build script locates `ISCC.exe` from `PATH`, Windows uninstall registry entries, and common Inno Setup 7/6 install paths. If needed, pass:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild -IsccPath "D:\Inno Setup 7\ISCC.exe"
```

The installer creates Start Menu and optional Desktop shortcuts to `启动NASDX桌面.bat`, not directly to `app.py`. It installs under the current user profile and must not package `config.toml`, `.env`, `reports/`, `stock_data_*.json`, `nasdx_history.db`, logs, cache folders, `wheelhouse/`, `dist/`, or `build/`.

Uninstall removes the app-owned install directory, including generated Python caches under `{app}`. User runtime state such as `%APPDATA%\NASDX\config.toml`, external report folders, and history databases must remain unless the user explicitly deletes them.

If the optional WebView path is used, the target Windows machine may need the Microsoft Edge WebView2 Runtime. Browser fallback remains supported.

After installing in a disposable Windows profile or VM, validate the installed app directory:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir "$env:LOCALAPPDATA\Programs\NASDX Desktop" -Timeout 60
```

This smoke test runs launcher/control-panel dry-runs and a headless `?page=plan` check. It uses a temporary runtime directory and must not write reports, history, `.env`, or `config.toml` into the installed application directory.

## Optional WebView

`pywebview` is optional. Keep it out of the default package unless a native window is required:

```powershell
python -m pip install -r requirements_desktop.txt
python -B desktop\launcher.py --webview --page plan
```

For package builds:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -IncludeWebView
```

## Wheelhouse path

Release builds use the exact Python/pip versions in `packaging/windows/toolchain-win.json`. Core and optional WebView dependencies are fully pinned with hashes in `requirements-win-core.lock` and `requirements-win-webview.lock`; package installation uses `--require-hashes` and records the selected lock hash plus `pip freeze --all` in `PACKAGING_MANIFEST.json`.

Validate or refresh the locks with:

```powershell
python -B run_dependency_lock_check.py
powershell -ExecutionPolicy Bypass -File packaging\windows\refresh_dependency_locks.ps1
```

For slow or unstable networks, create a local wheelhouse first:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_wheelhouse.ps1
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -WheelhouseDir wheelhouse\nasdx-win-py311
```

`wheelhouse/` is ignored and must not be committed.

## Verification

Use these commands before handing off a Windows desktop change:

```powershell
python -m pytest tests
python -m ruff check --no-cache .
python -B run_security_checks.py --skip-optional
python -B run_desktop_doctor.py
python -B run_desktop_completion_audit.py
python -B run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop-check
python -B run_desktop_release_check.py --write-evidence
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop -Timeout 60
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir dist\NASDX-Desktop -Timeout 60
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild -SkipCompile
python -B run_desktop_release_check.py
python -B run_final_audit.py
python -B run_product_readiness.py
```

`run_security_checks.py` is intentionally lightweight by default. It scans versionable text files for likely committed API keys and skips optional external tools unless `--run-optional` is passed. If `pip-audit`, `bandit`, and `detect-secrets` are installed locally, use:

```powershell
python -B run_security_checks.py --run-optional
```

`run_desktop_release_check.py` is the local desktop release gate. By default it runs lint, desktop contract tests, lightweight security checks, desktop doctor, desktop completion audit, portable package build, portable smoke, installed-layout smoke using the isolated `dist\NASDX-Desktop-check` directory, release evidence JSON for that package via `--package-dir`, installer input validation with `-SkipCompile`, and final audit. It does not run or install the generated setup executable, and it does not overwrite a dependency-contained `dist\NASDX-Desktop` release package unless `--full-package` is explicit. Add `--write-evidence` only when a packaging machine should persist ignored `dist\release-evidence\NASDX-desktop-release-evidence.json`; use `--evidence-output` for a different ignored handoff path.

The default release gate passes `--skip-zip` to release evidence because it does not build a portable zip unless `--zip-package` is explicit. When `--zip-package` is used, release evidence points at the zip and manifest produced and smoked in that run.

`run_desktop_completion_audit.py` is a read-only evidence matrix. It reports preserved entrypoints, launcher MVP, local config, packaging chain, portable runtime bundle status, release gates, ignored generated files, optional WebView availability, Inno Setup availability, and installer roundtrip status. Missing `pywebview` is a WARN because browser fallback is acceptable for the first MVP. Missing `ISCC.exe` or an unproven installer roundtrip remains INCOMPLETE until tested on a packaging machine or disposable Windows VM. `ISCC.exe` discovery checks PATH, Inno Setup 7/6 common locations, and Windows uninstall registry metadata.

`run_desktop_release_evidence.py` is a read-only release evidence bundle. It combines completion audit output, desktop doctor output, portable package/zip/installer artifact metadata, ignored path checks, and next packaging commands. It prints JSON by default; `--package-dir` points the evidence at the package under test, and `--write` stores ignored `dist\release-evidence\NASDX-desktop-release-evidence.json` for handoff or PR notes. The `forbidden_present` field lists forbidden package-relative paths only, and `package_forbidden_failures` fails the evidence when `.env`, `config.toml`, `reports/`, logs, `__pycache__/`, `*.pyc`, local databases, or build outputs are present without reading or printing their contents.

The packaged `PACKAGING_MANIFEST.json` uses `path_policy=relative-or-redacted`: it records relative paths or `<source-checkout>` / `<external-path>` placeholders instead of packaging-machine absolute paths.

Portable zip safety is checked twice: `build_portable_zip.ps1` rejects forbidden package files before compression, and `smoke_portable_zip.ps1` checks the extracted package again. Release evidence also records forbidden zip entries and counts them in `zip_forbidden_failures`.

`run_final_audit.py` also checks the desktop delivery assets: launcher, control panel, desktop doctor, desktop completion audit, release evidence bundle, desktop batch entry, shortcut script, portable package scripts, installer wrapper, release check script, security check script, documentation markers, and ignored build-output paths.

For a fuller local package check after dependencies and Inno Setup 7/6 are ready:

```powershell
python -B run_desktop_release_check.py --full-package --package-timeout 1200 --pip-timeout 120 --pip-retries 3
python -B run_desktop_release_check.py --full-package --zip-package --package-timeout 1200 --zip-timeout 900 --pip-timeout 120 --pip-retries 3
python -B run_desktop_release_check.py --full-package --compile-installer
```

`--full-package` installs runtime dependencies into `dist\NASDX-Desktop\.venv`, keeps that bundled runtime, scrubs Python caches from it, and then runs smoke with `-RequireVenv`, so the check fails if the package falls back to the developer machine's global Python. Slow networks can exceed the default fast local gate, so use `--package-timeout`, `--pip-timeout`, and `--pip-retries` on build machines that do real dependency installation.
`--zip-package` additionally creates `dist\NASDX-Desktop-portable.zip`, `dist\NASDX-Desktop-portable.zip.sha256`, and `dist\NASDX-Desktop-portable.manifest.json`; zip smoke verifies hash/manifest, extracts to a temporary directory, and runs installed-layout smoke before installer input validation. Use `--zip-timeout` when zipping or extracting a dependency-contained `.venv` is slow.

After `dist\installer\NASDX-Desktop-Setup.exe` exists, validate the real installer only in a disposable Windows profile or VM:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installer_roundtrip.ps1 -InstallerPath dist\installer\NASDX-Desktop-Setup.exe -AllowInstall -CheckShortcuts -RequireVenv -Timeout 60
```

Without `-AllowInstall`, `smoke_installer_roundtrip.ps1` stays in plan-only mode and does not run the installer. With `-AllowInstall`, it installs into a temporary directory by default, calls `smoke_installed.ps1`, then runs the Inno Setup uninstaller, checks that the app files and shortcuts are removed, and removes the empty temporary install directory when it created that directory itself. Add `-RequireVenv` for final release proof so the installed app must use its bundled `.venv`.

After a successful real roundtrip, the script writes ignored proof metadata to `dist\installer\NASDX-Desktop-roundtrip-proof.json` by default. The completion audit treats installer roundtrip as PASS only when that proof uses schema `nasdx_installer_roundtrip_proof.v1`, matches the current setup executable SHA256, and proves installed smoke, uninstall, `-RequireVenv`, and `-CheckShortcuts`.

The repository also includes a Windows GitHub Actions workflow at `.github/workflows/windows-desktop.yml`. It uses the Node 24 based `actions/checkout@v5` and `actions/setup-python@v6`, runs `python -B run_desktop_release_check.py --skip-final-audit --fail-fast` on `windows-latest`, then delivery-asset contracts. The CI job includes the lightweight security check but intentionally skips `run_final_audit.py` because a fresh checkout does not include local market snapshots or generated reports.

## Troubleshooting

| Symptom | Check |
|---|---|
| Launcher cannot find the app | Run `python -B desktop\launcher.py --dry-run --page plan` and confirm `root` points to the NASDX folder containing `app.py`. |
| API settings are not used | Confirm environment variables first, then `NASDX_CONFIG_FILE`, then `%APPDATA%\NASDX\config.toml`. Environment variables win. |
| Package build is slow | Use `build_wheelhouse.ps1` and then `build_portable.ps1 -WheelhouseDir wheelhouse\nasdx-win-py311`. |
| WebView does not open | Install optional `requirements_desktop.txt`; browser fallback should still work. |
| Port conflict | Omit `--port` so the launcher can choose a free local port, or pass a known free port. |

## Do not commit

- `config.toml`
- `.env`
- API keys
- `reports/`
- `stock_data_*.json`
- `nasdx_history.db`
- logs
- cache folders
- `dist/`
- `build/`
- `wheelhouse/`
- packaged executables or installers
