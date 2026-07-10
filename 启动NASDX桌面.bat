@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -B desktop\control_panel.py %*
) else (
  python -B desktop\control_panel.py %*
)

if errorlevel 1 (
  echo NASDX desktop control panel failed. Falling back to direct launcher...
  if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B desktop\launcher.py --webview --page plan %*
  ) else (
    python -B desktop\launcher.py --webview --page plan %*
  )
)

endlocal
