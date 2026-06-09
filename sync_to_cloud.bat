@echo off
chcp 65001 >nul
title NASDX — 同步数据到 Cloud

cd /d "%~dp0"

echo.
echo  ╔═══════════════════════════════════════╗
echo  ║   NASDX 数据同步到 Streamlit Cloud    ║
echo  ╚═══════════════════════════════════════╝
echo.

REM 检查是否有新的扫描数据
if not exist "reports\etf50_*.json" (
    echo  [!] 未找到 ETF50 扫描数据，请先运行 scan_etf50.py
    pause
    exit /b 1
)

echo  [1/4] 切换到 deploy 分支...
git checkout deploy >nul 2>&1
if errorlevel 1 (
    echo  [!] 切换分支失败，请检查 git 状态
    pause
    exit /b 1
)

echo  [2/4] 添加最新报告数据...

REM 找最新的报告文件（按日期降序）
for /f "delims=" %%f in ('dir /b /od reports\etf50_2*.json 2^>nul') do set LATEST_ETF50=%%f
for /f "delims=" %%f in ('dir /b /od reports\stocks60_*.json 2^>nul') do set LATEST_ST60=%%f
for /f "delims=" %%f in ('dir /b /od reports\etf50_quant_*.json 2^>nul') do set LATEST_QUANT=%%f

echo    ETF50:   %LATEST_ETF50%
echo    个股:    %LATEST_ST60%
echo    量化:    %LATEST_QUANT%

REM 添加所有 json 报告（强制忽略 gitignore）
git add -f reports\*.json >nul 2>&1

REM 检查是否有变化
git diff --cached --quiet
if errorlevel 1 (
    echo  [3/4] 提交数据更新...
    REM 获取当前时间
    for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set TODAY=%%c%%b%%a
    for /f "tokens=1-2 delims=: " %%a in ('time /t') do set NOWTIME=%%a%%b

    git commit -m "data: 同步本地扫描数据 %TODAY% %NOWTIME%" >nul 2>&1
    echo  [4/4] 推送到 Cloud...
    git push origin deploy
    if errorlevel 1 (
        echo  [!] 推送失败，请检查网络
    ) else (
        echo.
        echo  ✅ 同步完成！
        echo     Streamlit Cloud 将在 1-2 分钟内自动更新
        echo     网址: https://zgkg9ihfefze8jnttnunhz.streamlit.app
    )
) else (
    echo  [3/4] 数据无变化，跳过提交
    echo  ✅ Cloud 数据已是最新
)

echo  [回退] 切换回 master 分支...
git checkout master >nul 2>&1

echo.
pause
