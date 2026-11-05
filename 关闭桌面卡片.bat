@echo off
chcp 65001 >nul
setlocal
title 酷态科 10 号充电器 - 关闭桌面卡片

echo ==========================================================
echo   关闭桌面卡片
echo ==========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*widget_app.py*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; exit 0 } else { exit 2 }"

if errorlevel 2 (
    echo [i] 卡片本来就没在运行
    echo.
    pause
    exit /b 0
)
if errorlevel 1 (
    echo [!] 关闭失败
) else (
    echo [+] 卡片已关闭（控制台服务不受影响）
)
echo.
pause
