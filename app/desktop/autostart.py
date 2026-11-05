"""开机自启：写 HKCU\\...\\Run，免管理员权限。

之所以不用「计划任务」：安装包是 per-user 安装（不弹 UAC），
注册表 Run 项同样能登录即启动，而且开关状态用 winreg 一问即知，
不需要像 schtasks 那样解析命令行输出。
"""

from __future__ import annotations

import sys
import winreg

from . import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = paths.APP_NAME


def command() -> str:
    """自启时执行的命令行：--manager --silent = 直接进托盘，不弹主窗口。"""
    if paths.is_frozen():
        return f'"{paths.exe_path()}" --manager --silent'
    pythonw = paths.exe_path().with_name("pythonw.exe")
    if not pythonw.is_file():
        pythonw = paths.exe_path()
    return f'"{pythonw}" "{paths.resource("main.py")}" --manager --silent'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> bool:
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        return True
    except OSError:
        return False


def disable() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def toggle() -> bool:
    """切换并返回切换后的状态。"""
    if is_enabled():
        disable()
        return False
    enable()
    return True
