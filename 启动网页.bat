@echo off
chcp 65001 >nul
title NASDX Web UI
cd /d "%~dp0"
echo.
echo  ╔═══════════════════════════════════╗
echo  ║   NASDX A股多智能体分析系统        ║
echo  ║   正在启动网页界面...              ║
echo  ╚═══════════════════════════════════╝
echo.
echo  浏览器会自动打开，如未打开请访问：
echo  http://localhost:8501
echo.
python -m streamlit run app.py --server.port 8501 --server.headless false --browser.gatherUsageStats false
pause
