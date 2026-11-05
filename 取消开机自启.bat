@echo off
chcp 65001 >nul
setlocal
title 酷态科 10 号充电器 - 取消开机自启

set "CK_T1=CUKTECH充电器控制台"
set "CK_T2=CUKTECH桌面组件"

echo ==========================================================
echo   取消开机自启（控制台 + 桌面卡片）
echo ==========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$n=0; foreach ($t in @($env:CK_T1, $env:CK_T2)) { if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $t -Confirm:$false; $n++ } }; if ($n -gt 0) { exit 0 } else { exit 2 }"

if errorlevel 2 (
    echo [i] 开机自启本来就没有设置
    echo.
    pause
    exit /b 0
)
if errorlevel 1 (
    echo [!] 取消失败，可能权限不足
) else (
    echo [+] 已取消开机自启（当前正在运行的进程不受影响）
)
echo.
pause
