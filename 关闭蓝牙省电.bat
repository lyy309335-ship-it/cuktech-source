@echo off
chcp 65001 >nul
setlocal
title 酷态科 10 号充电器 - 关闭蓝牙省电

rem 需要管理员权限：非提权时用 UAC 重新拉起自己
net session >nul 2>&1
if errorlevel 1 (
    echo [i] 需要管理员权限，请在弹窗里点「是」
    set "SELF=%~f0"
    powershell -NoProfile -Command "Start-Process -FilePath $env:SELF -Verb RunAs"
    exit /b 0
)

echo ==========================================================
echo   关闭蓝牙适配器的「允许计算机关闭此设备以节约电源」
echo ==========================================================
echo.
echo   原因：不关掉这一项，蓝牙空闲后会被系统挂起，
echo         常驻的 BLE 连接会莫名其妙断掉。
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = Get-PnpDevice -Class Bluetooth -PresentOnly | Where-Object {$_.FriendlyName -like 'Intel*Bluetooth*'} | Select-Object -First 1; if (-not $d) { Write-Output 'NOTFOUND'; exit 1 }; $id = $d.InstanceId; $pm = Get-CimInstance -Namespace root/wmi -ClassName MSPower_DeviceEnable | Where-Object {$_.InstanceName -like ('*' + $id + '*')}; if (-not $pm) { Write-Output 'NOPM'; exit 2 }; $pm.Enable = $false; Set-CimInstance -InputObject $pm -ErrorAction Stop; $chk = Get-CimInstance -Namespace root/wmi -ClassName MSPower_DeviceEnable | Where-Object {$_.InstanceName -like ('*' + $id + '*')}; Write-Output ('设备: ' + $d.FriendlyName); Write-Output ('省电开关现在 = ' + $chk.Enable + '  (False 就是已经关掉省电，正确)')"

echo.
echo ----------------------------------------------------------
echo   完成。这个窗口可以直接关掉。
echo ----------------------------------------------------------
pause
