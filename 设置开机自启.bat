@echo off
chcp 65001 >nul
setlocal
title 酷态科 10 号充电器 - 设置开机自启

set "HERE=%~dp0"
set "CK_APP=%HERE:~0,-1%\app"
set "CK_PYW=%CK_APP%\.venv\Scripts\pythonw.exe"
set "CK_T1=CUKTECH充电器控制台"
set "CK_T2=CUKTECH桌面组件"

echo ==========================================================
echo   设置开机自启
echo ==========================================================
echo.
echo   程序: %CK_PYW%
echo   目录: %CK_APP%
echo.
echo   会注册两个任务：
echo     1. 控制台服务  登录后 15 秒启动（后台无窗口）
echo     2. 桌面卡片    登录后 30 秒启动（悬浮小窗 + 托盘）
echo.

if not exist "%CK_PYW%" (
    echo [错误] 没找到 pythonw.exe，环境没装好
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$e=$env:CK_PYW;$w=$env:CK_APP;$u=($env:USERDOMAIN + '\' + $env:USERNAME); $set=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew; $prn=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited; $a1=New-ScheduledTaskAction -Execute $e -Argument 'cuktech.py web --log' -WorkingDirectory $w; $t1=New-ScheduledTaskTrigger -AtLogOn -User $u; try { $t1.Delay='PT15S' } catch {}; Register-ScheduledTask -TaskName $env:CK_T1 -Action $a1 -Trigger $t1 -Settings $set -Principal $prn -Description '酷态科充电器本地BLE控制台(127.0.0.1:8080)' -Force | Out-Null; $a2=New-ScheduledTaskAction -Execute $e -Argument 'widget_app.py' -WorkingDirectory $w; $t2=New-ScheduledTaskTrigger -AtLogOn -User $u; try { $t2.Delay='PT30S' } catch {}; Register-ScheduledTask -TaskName $env:CK_T2 -Action $a2 -Trigger $t2 -Settings $set -Principal $prn -Description '酷态科充电器桌面悬浮卡片' -Force | Out-Null; if ($?) { 'OK' } else { 'FAIL' }"

if errorlevel 1 (
    echo.
    echo [!] 注册失败，请把上面的报错发我
) else (
    echo.
    echo [+] 已设置：登录 Windows 后会自动启动控制台与桌面卡片
    echo.
)
pause
