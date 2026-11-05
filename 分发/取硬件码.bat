@echo off
chcp 65001 >nul
setlocal
title 酷态科充电器 - 读取硬件码
cd /d "%~dp0tools"

echo ==========================================================
echo   酷态科充电器  -  读取设备硬件码（MAC / TOKEN / BLE KEY）
echo ==========================================================
echo.
echo   建议顺序：先装同目录的 CUKTECH-Setup 安装包，再跑本脚本。
echo   取到的三项会自动写进 %LOCALAPPDATA%\CUKTECH\.env，装完直接用。
echo.
echo   操作提示：
echo     1. 先把手机米家打开到「扫一扫」页面，再继续
echo     2. 登录方式选 q（二维码），不用在命令行输密码
echo     3. 二维码会自动弹出来，扫完必须立刻在手机上点「确认登录」
echo        不点确认的话，约 5 分钟后会静默超时
echo     4. 账号地区不确定就直接回车，让它全试一遍
echo     5. 跑完会自动把硬件码写进软件配置，不用再手抄
echo.
echo ----------------------------------------------------------
echo.

rem ---- 找取码工具 ----
if not exist "token_extractor.exe" (
    echo [错误] 同目录里没找到 token_extractor.exe
    echo        请确认整个「分发」文件夹是完整拷贝过来的。
    echo.
    pause
    exit /b 4
)

rem ---- 找 python：只认系统 Python（分发版不带虚拟环境）----
set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py -3"

if not defined PYEXE goto NO_PYTHON

rem ---- 二维码窗口（缺依赖时静默跳过，不影响取码）----
if exist "watch_qr.py" if not "%~1"=="--no-qr" start "" /b %PYEXE% "watch_qr.py"

%PYEXE% "collect_credentials.py" %*
set "RC=%ERRORLEVEL%"

echo.
echo ==========================================================
if "%RC%"=="0" (
    echo   完成：硬件码已写入配置，直接启动软件即可。
) else if "%RC%"=="2" (
    echo   没在结果里找到可用的 CUKTECH 设备。
    echo   请确认充电器已绑到当前米家账号，或重跑一次。
    echo   也可以手工输入：在下面这条命令后加 --manual
) else if "%RC%"=="3" (
    echo   识别到了设备，但格式校验没通过（上面已列出原因）。
) else if "%RC%"=="4" (
    echo   找不到取码工具 token_extractor.exe。
) else if "%RC%"=="5" (
    echo   已取消。
) else (
    echo   取码过程出错（退出码 %RC%），请把上面的内容截图反馈。
)
echo ==========================================================
echo.
pause
exit /b %RC%

:NO_PYTHON
echo [警告] 这台机器上没找到 Python，无法自动解析取码结果。
echo.
echo   两个办法：
echo     1. 装一个 Python（python.org，勾选 Add to PATH）后重跑本脚本
echo     2. 直接双击 token_extractor.exe，把 MAC / TOKEN / BLE KEY 手抄下来，
echo        打开软件 - 「设置设备凭据」- 粘贴进去
echo.
set /p "GO=现在直接跑取码工具看结果吗？(y/N) "
if /i "%GO%"=="y" (
    token_extractor.exe
    echo.
    echo 结果在上面。请手抄 MAC / TOKEN / BLE KEY，到软件里粘贴导入。
)
echo.
pause
exit /b 6
