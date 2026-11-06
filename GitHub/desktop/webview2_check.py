"""WebView2 运行时预检。

为什么需要这个模块：界面全靠 WebView2 渲染（pywebview 6.x + .NET 控制器）。
运行时缺失时，窗口不会报错、不会退出，就是**什么都没有**——
用户双击图标后一片安静，以为程序坏了，卸载重装也没用。这是发给别人时
唯一会让人彻底懵掉的失败模式，所以启动前先自己查一遍。

检测走两条独立路径，任一命中即视为已装（两路都查是为了兼容各种装法）：

    1. 注册表：EdgeUpdate 给自己登记的客户端项，name = "Microsoft Edge WebView2 Runtime"
       —— 微软官方推荐的检测方式，per-machine 与 per-user 各有一份
    2. 文件：<Program Files>/Microsoft/EdgeWebView/Application/<版本>/msedgewebview2.exe

注册表读不到不代表没装（有的精简系统会清 EdgeUpdate 项但不影响运行时），
所以文件检测是必要的兜底，别删。
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

# 微软官方的 Evergreen Bootstrapper（约 1.5 MB，装完自动拉最新运行时）
DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

_NAME_HINTS = ("webview2", "edge webview")

_EXE_REL = Path("Microsoft") / "EdgeWebView" / "Application"

# EdgeUpdate 的客户端项在这三个位置之一，per-machine 的 32/64 位视图都要看
_REG_ROOTS: tuple[tuple[int, str], ...] = (
    ("HKLM32", r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
    ("HKLM64", r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
    ("HKCU", r"Software\Microsoft\EdgeUpdate\Clients"),
)


def _reg_candidates() -> list[tuple[str, str]]:
    """返回 (根键标识, 子键路径) 的列表，用于逐个遍历查找运行时版本。"""
    import winreg

    roots = {
        "HKLM32": (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        "HKLM64": (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        "HKCU": (winreg.HKEY_CURRENT_USER, 0),
    }
    found: list[tuple[str, str]] = []
    for tag, sub in _REG_ROOTS:
        hive, view = roots[tag]
        try:
            with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        name = winreg.EnumKey(key, i)
                    except OSError:
                        continue
                    found.append((tag, sub + "\\" + name))
        except OSError:
            continue
    return found


def _version_from_registry() -> str:
    import winreg

    roots = {
        "HKLM32": (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        "HKLM64": (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        "HKCU": (winreg.HKEY_CURRENT_USER, 0),
    }
    for tag, path in _reg_candidates():
        hive, view = roots[tag]
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
                try:
                    head = winreg.QueryValueEx(key, "name")[0]
                except OSError:
                    continue
                if not isinstance(head, str):
                    continue
                if not any(h in head.lower() for h in _NAME_HINTS):
                    continue
                try:
                    pv = winreg.QueryValueEx(key, "pv")[0]
                except OSError:
                    pv = ""
                return str(pv or "unknown")
        except OSError:
            continue
    return ""


def _version_from_files() -> str:
    """按目录里有几份可选安装位置找 msedgewebview2.exe，取版本号最大的那个。"""
    bases: list[Path] = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        raw = os.environ.get(env, "").strip()
        if raw:
            bases.append(Path(raw) / _EXE_REL)
    # 环境变量在某些宿主里可能缺失，补两个硬编码的常规位置
    bases.append(Path(r"C:\Program Files (x86)") / _EXE_REL)
    bases.append(Path(r"C:\Program Files") / _EXE_REL)

    def key(name: str):
        parts = []
        for chunk in name.split("."):
            parts.append(int(chunk) if chunk.isdigit() else 0)
        return parts

    versions: list[str] = []
    for base in bases:
        try:
            for child in base.iterdir():
                if (child / "msedgewebview2.exe").is_file():
                    versions.append(child.name)
        except OSError:
            continue
    if not versions:
        return ""
    versions.sort(key=key, reverse=True)
    return versions[0]


def runtime_version() -> str:
    """已安装则返回版本号字符串，未安装返回空串。每次启动只调一次。"""
    ver = ""
    try:
        ver = _version_from_registry()
    except Exception:  # noqa: BLE001
        ver = ""
    if ver:
        return ver
    try:
        return _version_from_files()
    except Exception:  # noqa: BLE001
        return ""


def _log(msg: str) -> None:
    try:
        from desktop.manager import log

        log(msg)
    except Exception:  # noqa: BLE001
        try:
            print(msg, file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass


def _ask_install() -> bool:
    """弹原生对话框（不走 tkinter/Qt，此时任何 GUI 框架都还没起来）。

    返回 True 表示用户点了「是」，调用方负责打开下载页。
    """
    text = (
        "这台电脑缺少 WebView2 运行时，程序界面无法显示。\n\n"
        "它是微软随 Microsoft Edge 提供的组件，绝大多数 Windows 10 / 11 都已自带，"
        "你这台没有。\n\n"
        "点「是」打开微软官方下载页（约 1.5 MB），装完再双击本程序即可。\n"
        "点「否」退出。"
    )
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        ]
        # MB_YESNO=0x4 | MB_ICONWARNING=0x30 | MB_SETFOREGROUND=0x10000 | MB_TOPMOST=0x40000
        ret = user32.MessageBoxW(None, text, "CUKTECH 缺少运行组件", 0x4 | 0x30 | 0x10000 | 0x40000)
        return ret == 6  # IDYES
    except Exception:  # noqa: BLE001
        return False


def _open_download() -> None:
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell32.ShellExecuteW(None, "open", DOWNLOAD_URL, None, None, 1)
    except Exception as exc:  # noqa: BLE001
        _log(f"打开 WebView2 下载页失败：{exc!r}")


def ensure(interactive: bool = True, role: str = "") -> bool:
    """启动前的闸门。已装返回 True；缺失时按角色决定怎么收场。

    interactive=False 用于 `--silent`（开机自启）与后台角色 —— 这些场景
    绝对不能弹模态框，否则会卡在启动项里，用户开机后看到莫名其妙一个窗口。
    """
    ver = runtime_version()
    if ver:
        _log(f"WebView2 运行时 {ver}")
        return True

    _log(f"未检测到 WebView2 运行时（角色={role or 'manager'}，interactive={interactive}）")
    if not interactive:
        return False
    if _ask_install():
        _open_download()
    return False
