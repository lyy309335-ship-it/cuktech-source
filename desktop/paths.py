"""CUKTECH 桌面版 · 公共路径解析。

两种运行形态必须分开处理，否则装到 Program Files 下会写不进文件：

  源码运行（python main.py）
      资源 = app/ 目录；数据 = app/data、app/logs（和原来的 bat 方案完全一致）

  打包运行（CUKTECH.exe）
      资源 = PyInstaller 解包目录（sys._MEIPASS，只读）
      数据 = %LOCALAPPDATA%\\CUKTECH（可写，卸载时由安装器清理）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "CUKTECH"
APP_TITLE = "CUKTECH 充电器控制台"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """只读资源根目录：web/index.html、widget.html、console_host.html、.env 等。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]  # app/


def resource(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def _ensure(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def data_dir() -> Path:
    """可写数据目录：历史功率库、卡片位置、WebView2 缓存。"""
    override = os.environ.get("CUKTECH_APP_DATA_DIR", "").strip()
    if override:
        return _ensure(Path(override).expanduser())
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return _ensure(Path(base) / APP_NAME / "data")
    return _ensure(resource_dir() / "data")


def log_dir() -> Path:
    override = os.environ.get("CUKTECH_APP_LOG_DIR", "").strip()
    if override:
        return _ensure(Path(override).expanduser())
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return _ensure(Path(base) / APP_NAME / "logs")
    return _ensure(resource_dir() / "logs")


def exe_path() -> Path:
    """当前可执行文件；源码运行时返回 python 解释器。"""
    return Path(sys.executable)


def launch_command(*args: str) -> list[str]:
    """拉起自身某个角色时用的命令行。

    打包后直接调用 exe；源码运行时用 pythonw（python.exe 会弹黑框）。
    """
    if is_frozen():
        return [str(exe_path()), *args]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = pythonw if pythonw.is_file() else Path(sys.executable)
    return [str(launcher), str(resource("main.py")), *args]
