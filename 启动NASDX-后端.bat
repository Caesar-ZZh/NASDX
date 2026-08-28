@echo off
chcp 65001 >nul
title NASDX 后端 (8901)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 未找到 .venv，请先在该 worktree 安装依赖：
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r server/requirements.txt
    pause
    exit /b 1
)

echo 正在启动 NASDX 后端（端口 8901，自动注入内置 key）...
.venv\Scripts\python.exe run_server.py
