@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem React 桌面模式：uvicorn server.main（API + 前端同源托管），pywebview 加载。
rem 前置：frontend/ 已 npm install && npm run build；后端依赖见 server/requirements.txt。
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -B desktop\launcher.py --webview --mode react %*
) else (
  python -B desktop\launcher.py --webview --mode react %*
)

endlocal
