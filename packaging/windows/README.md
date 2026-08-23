# NASDX Windows Packaging

This folder contains the portable Windows packaging skeleton for NASDX.

For the full desktop startup, configuration, packaging, and troubleshooting guide, see `docs/WINDOWS_DESKTOP.md`.

The first packaging target is a folder, not a one-file executable:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall
```

An optional launcher-only exe can be built later, but first validate the plan without requiring PyInstaller:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_launcher_exe.ps1 -SkipBuild
```

This path freezes only `desktop\exe_launcher.py`; the generated exe delegates to `.venv\Scripts\python.exe -B desktop\control_panel.py` inside the portable folder. It is not a full one-file build of `app.py` or the analytics dependencies.

Without `-SkipDependencyInstall`, the script creates `.venv` inside the package and installs:

- `requirements_nasdx.txt`

The release gate can run that full dependency-contained package path explicitly:

```powershell
python -B run_desktop_release_check.py --full-package --zip-package --package-timeout 1200 --zip-timeout 900 --pip-timeout 120 --pip-retries 3
python -B run_desktop_release_check.py --full-package --zip-package --write-evidence --package-timeout 1200 --zip-timeout 900 --pip-timeout 120 --pip-retries 3
```

When `--full-package` is used, the release gate passes `-RequireVenv` to portable and installed-layout smoke so the package cannot accidentally fall back to the developer machine's global Python.
Without `--full-package`, the release gate writes the fast smoke package to `dist\NASDX-Desktop-check` so it does not overwrite a dependency-contained `dist\NASDX-Desktop` release artifact.

Optional WebView dependencies are installed only when requested:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -IncludeWebView
```

Run the package smoke check after building:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir dist\NASDX-Desktop -Timeout 60
```

These smoke scripts verify launcher dry-run, control-panel dry-run, desktop doctor JSON output, shortcut plan-only output, headless page startup, and process cleanup.

Build and verify a distributable portable zip:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable_zip.ps1 -PackageDir dist\NASDX-Desktop -OutputZip dist\NASDX-Desktop-portable.zip
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable_zip.ps1 -ZipPath dist\NASDX-Desktop-portable.zip -Timeout 60
```

The zip build also writes `dist\NASDX-Desktop-portable.zip.sha256` and `dist\NASDX-Desktop-portable.manifest.json`. The smoke command verifies both sidecars before extraction.

For a dependency-contained portable zip, pass `-RequireVenv` to both zip commands after building the package without `-SkipDependencyInstall`.

Validate the installer inputs without compiling:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild -SkipCompile
```

Prepare Inno Setup on a packaging machine. The bootstrap stays plan-only unless both `-Install` and `-AcceptAgreements` are passed:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\install_inno_setup.ps1
powershell -ExecutionPolicy Bypass -File packaging\windows\install_inno_setup.ps1 -Install -AcceptAgreements
```

Run the read-only installer release preflight before compiling:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\preflight_installer_release.ps1 -RequireVenv
```

After the portable folder is built and smoke-tested, compile the optional installer wrapper with Inno Setup:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild
```

The installer output goes under ignored `dist\installer\`. It creates Start Menu and optional Desktop shortcuts to `启动NASDX桌面.bat`, not directly to `app.py`. Its uninstaller removes the app-owned install directory, including runtime Python caches under `{app}`, while user config and reports stay outside `{app}`. Test the installer in a disposable Windows profile or VM before sharing it.

The build script looks for `ISCC.exe` on `PATH`, Windows uninstall registry entries, and common Inno Setup 7/6 install locations. You can also pass `-IsccPath "D:\Inno Setup 7\ISCC.exe"`.

After installing in that disposable profile or VM, run:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir "$env:LOCALAPPDATA\Programs\NASDX Desktop" -Timeout 60 -CheckShortcuts
```

To validate install, smoke, and uninstall in one disposable-profile command after `dist\installer\NASDX-Desktop-Setup.exe` exists:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installer_roundtrip.ps1 -InstallerPath dist\installer\NASDX-Desktop-Setup.exe -AllowInstall -CheckShortcuts -RequireVenv -Timeout 60
```

Without `-AllowInstall`, `smoke_installer_roundtrip.ps1` prints a plan-only preflight and does not run the installer. Use `-RequireVenv` for final release proof so the installed layout cannot fall back to a global Python. When the script creates the default temporary install directory itself, it removes the empty directory after uninstall.
After a successful install/smoke/uninstall run, the script writes ignored proof metadata to `dist\installer\NASDX-Desktop-roundtrip-proof.json`; `run_desktop_completion_audit.py` validates that proof against the current setup executable hash before marking installer roundtrip complete.

For slower or unstable networks, build a local wheelhouse first and then install from it:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_wheelhouse.ps1
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -WheelhouseDir wheelhouse\nasdx-win-py311
```

The package intentionally excludes runtime/user artifacts:

- `reports/`
- `stock_data_*.json`
- `nasdx_history.db`
- `config.toml`
- `.env`
- logs and cache folders
- `.git/`
- build outputs
- installer outputs under `dist\installer\`

User configuration is read at runtime by the desktop launcher from either:

- `%APPDATA%\NASDX\config.toml`
- the path set in `NASDX_CONFIG_FILE`

`config.example.toml` and `docs/WINDOWS_DESKTOP.md` are packaged as user-facing templates/guides. Real `config.toml` and `.env` files are never packaged or committed.

Launch from the package with:

```powershell
dist\NASDX-Desktop\启动NASDX桌面.bat
dist\NASDX-Desktop\启动NASDX桌面.bat --dry-run --page plan
```

The batch passes arguments through to the desktop control panel. The dry-run command verifies the real user entry without opening the GUI.

Run the packaged desktop diagnostic:

```powershell
python -B dist\NASDX-Desktop\scripts\run_desktop_doctor.py
python -B dist\NASDX-Desktop\scripts\run_desktop_doctor.py --check-write
```

Run the packaged desktop completion audit:

```powershell
python -B dist\NASDX-Desktop\scripts\run_desktop_completion_audit.py
python -B dist\NASDX-Desktop\scripts\run_desktop_completion_audit.py --json
```

Collect the packaged desktop release evidence:

```powershell
python -B dist\NASDX-Desktop\scripts\run_desktop_release_evidence.py --json
python -B dist\NASDX-Desktop\scripts\run_desktop_release_evidence.py --write
```

From the source checkout, pass `--package-dir` when you want the evidence to describe the package built by a specific release gate run:

```powershell
python -B scripts/run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop-check
python -B run_desktop_release_check.py --write-evidence --evidence-output dist\release-evidence\NASDX-desktop-release-evidence.json
```

The evidence includes `forbidden_present` and `package_forbidden_failures`; it fails if the tested package contains `.env`, `config.toml`, `reports/`, logs, `__pycache__/`, `*.pyc`, local databases, or build outputs, and it reports only package-relative paths rather than file contents.
Portable zip checks use the same boundary: `build_portable_zip.ps1` rejects forbidden package files before compression, `smoke_portable_zip.ps1` checks the extracted package, and release evidence counts forbidden zip entries in `zip_forbidden_failures`.
The default release gate uses `--skip-zip` unless `--zip-package` is explicit, so stale local zip files are not treated as artifacts from the current quick gate run.
`PACKAGING_MANIFEST.json` uses `path_policy=relative-or-redacted`; it records relative paths or placeholders such as `<source-checkout>` instead of packaging-machine absolute directories.

Create current-user shortcuts from the package, preview first and then apply:

```powershell
powershell -ExecutionPolicy Bypass -File dist\NASDX-Desktop\packaging\windows\create_shortcuts.ps1 -Desktop
powershell -ExecutionPolicy Bypass -File dist\NASDX-Desktop\packaging\windows\create_shortcuts.ps1 -Desktop -Apply
```

The package batch opens the desktop control panel first. The panel exposes Start, Stop, Open App, Settings, Logs, and Data Refresh. It still starts the existing Streamlit `app.py`; it does not replace the UI or remove the current CLI scripts. If the panel cannot start, the batch falls back to the direct launcher.
