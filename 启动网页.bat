@echo off
chcp 65001 >nul
title NASDX Web UI
cd /d "%~dp0"

echo.
echo  ╔═══════════════════════════════════════════════╗
echo  ║   NASDX A股多智能体量化分析平台               ║
echo  ║   正在启动...                                  ║
echo  ╚═══════════════════════════════════════════════╝
echo.
echo  如果浏览器没有自动打开，请手动访问：
echo  http://127.0.0.1:8501
echo.
echo  注意：如果使用 Clash，请确保代理绕过列表包含：
echo    localhost / 127.0.0.1
echo  或临时切换 Clash 为「规则」模式（非全局）
echo.

python -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1 --browser.gatherUsageStats false --server.headless false

pause
