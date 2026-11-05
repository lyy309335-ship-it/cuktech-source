@echo off
chcp 65001 >nul
setlocal
title 酷态科 10 号充电器 - 控制台
cd /d "%~dp0app"

echo ==========================================================
echo   酷态科 10 号充电器  -  本地控制台
echo ==========================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 没找到虚拟环境 .venv，环境没装好
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [错误] 没找到 .env，缺少 MAC / TOKEN 配置
    echo.
    pause
    exit /b 1
)

rem 端口已在监听 = 已经有一个控制台在跑，别开第二个抢蓝牙连接
netstat -ano | findstr /C:"127.0.0.1:8080" | findstr /C:"LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo [i] 采集服务已经在运行，不会重复启动。
    echo     界面请用托盘图标 / 桌面卡片打开（v1.3.0 起不再自动开浏览器）。
    echo.
    pause
    exit /b 0
)

echo [i] 正在启动采集服务，日志就打在下面，不再自动打开浏览器...
echo     界面走程序自己的控制台窗口（托盘图标 / 桌面卡片 / 开始菜单）
echo     关掉这个窗口就等于停止采集服务
echo.

".venv\Scripts\python.exe" cuktech.py web

echo.
echo ==========================================================
echo   控制台已停止
echo ==========================================================
pause
