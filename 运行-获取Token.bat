@echo off
chcp 65001 >nul
setlocal
title 酷态科充电器 - 小米 Token 提取
cd /d "%~dp0tools"

echo ==========================================================
echo   酷态科 10 号充电器  -  小米 Token 提取
echo ==========================================================
echo.
echo   操作提示：
echo     1. 先把手机米家打开到「扫一扫」页面，再继续
echo     2. 登录方式选 q（二维码），不用在命令行输密码
echo     3. 二维码会自动弹出来，扫完必须立刻在手机上点「确认登录」
echo        不点确认的话，约 5 分钟后会静默超时，窗口直接退出
echo     4. 账号地区不确定就直接回车，让它全试一遍
echo     5. 抄下 CUKTECH 那条的 MAC 和 TOKEN（24 位十六进制）
echo.
echo ----------------------------------------------------------
echo.

if not exist "token_extractor.exe" (
    echo [错误] tools 目录里没找到 token_extractor.exe
    echo.
    pause
    exit /b 1
)

rem ---- 找一个可用的 python 来跑二维码抓取脚本 ----
set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py -3"

if exist "watch_qr.py" if defined PYEXE start "" /b %PYEXE% "watch_qr.py"

token_extractor.exe

echo.
echo ==========================================================
echo   已结束。窗口不会自动关闭，上面的 MAC / TOKEN 可以直接复制。
echo ==========================================================
pause
