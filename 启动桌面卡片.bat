@echo off
chcp 65001 >nul
setlocal
title 酷态科 10 号充电器 - 桌面卡片

echo ==========================================================
echo   酷态科 10 号充电器  -  桌面悬浮卡片
echo ==========================================================
echo.

pushd "%~dp0app"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [错误] 没找到虚拟环境，环境没装好
    echo.
    pause
    exit /b 1
)

rem 控制台没起来的话先提示一下，卡片会显示「控制台未响应」
netstat -ano | findstr /C:"127.0.0.1:8080" | findstr /C:"LISTENING" >nul 2>nul
if errorlevel 1 (
    echo [!] 本地控制台没在运行，卡片会显示「控制台未响应」
    echo     建议先双击「启动控制台.bat」
    echo.
)

echo [i] 正在启动桌面卡片...
start "" ".venv\Scripts\pythonw.exe" "widget_app.py"
popd

echo [+] 已发出启动指令。
echo     如果卡片已经开着，不会重复出现（单实例保护）。
echo     卡片可以拖到任意位置，位置会自动记住。
echo     关掉卡片是收进托盘，右下角闪电图标可以再点出来。
echo.
pause
