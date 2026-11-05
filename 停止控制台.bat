@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 酷态科 10 号充电器 - 停止控制台

set "PORT=8080"
set "TARGET="

for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:"127.0.0.1:%PORT%" ^| findstr /C:"LISTENING"') do set "TARGET=%%p"

if not defined TARGET (
    echo [i] 没有发现正在运行的控制台
    echo.
    pause
    exit /b 0
)

echo [i] 正在停止控制台 (PID !TARGET!) ...
taskkill /PID !TARGET! /F >nul 2>nul
if errorlevel 1 (
    echo [!] 停止失败，可能权限不足
) else (
    echo [+] 已停止
)
echo.
pause
