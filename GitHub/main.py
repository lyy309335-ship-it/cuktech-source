"""CUKTECH 桌面版 · 统一入口。

同一个 exe 承担五个角色，用命令行参数区分，安装包里只有一个可执行文件：

    CUKTECH.exe                管理器：托盘 + 内部控制台窗口 + 运行管理窗口 + 监管子进程（单实例）
    CUKTECH.exe --silent       同上，但启动时不弹窗口（开机自启用这个）
    CUKTECH.exe --service      采集服务：BLE 后台 + 本地 Web API（127.0.0.1:8080）
    CUKTECH.exe --widget       桌面卡片：贴在桌面上的那块小窗
    CUKTECH.exe --collect      凭据导入向导：填 MAC / TOKEN / BLE KEY，跑完即退
    CUKTECH.exe --update-runner <setup.exe> [sha256]
                               更新进程：等管理器退出后静默覆盖安装，见 desktop/updater.py

为什么不合成一个进程：采集走 asyncio + BLE 长连接，界面走 WinForms 消息循环，
放一起任何一边卡住都会拖死另一边；分开后卡片崩了采集还在，采集崩了界面还在。

控制台窗口加载的就是 http://127.0.0.1:8080/，和浏览器访问的是同一个页面、同一个源，
所以控制交互、WebSocket 推送、历史曲线全部原样可用 —— 只是不再离开应用。
"""

from __future__ import annotations

import ctypes
import io
import sys

_STREAM = None          # 必须模块级强引用，否则文件流被回收后写入报 closed file
_MUTEX = None


def _patch_streams() -> None:
    """windowed 模式下 stdout/stderr 是 None，任何一句 print 都会 AttributeError。"""
    global _STREAM
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from desktop import paths

        handle = open(paths.log_dir() / "app.log", "ab", buffering=0)
        _STREAM = io.TextIOWrapper(
            handle, encoding="utf-8", errors="replace", line_buffering=True
        )
    except Exception:  # noqa: BLE001
        _STREAM = open(__import__("os").devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _STREAM
    if sys.stderr is None:
        sys.stderr = _STREAM


def _single_instance() -> bool:
    """只有管理器需要单实例；服务和卡片可以被管理器重复拉起（它们各有自己的互斥）。"""
    global _MUTEX
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _MUTEX = kernel32.CreateMutexW(None, False, "Local\\CUKTECH_Desktop_Manager")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _wake_existing() -> None:
    """用户重复双击图标时，把已经在跑的那个面板叫出来，而不是默默退出。"""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.restype = ctypes.c_void_p
        kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenEventW(0x0002, False, "Local\\CUKTECH_ShowPanel")  # EVENT_MODIFY_STATE
        if handle:
            kernel32.SetEvent(handle)
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        pass


def run_service() -> None:
    """采集服务：等价于 `python cuktech.py web --log`。"""
    import cuktech

    sys.argv = ["cuktech.py", "web", "--log"]
    cuktech.main()


def run_widget() -> None:
    import os

    import widget_app

    # 托盘统一由管理器提供，卡片自己不再建一个（否则托盘里会出现两个闪电图标）
    os.environ["CUKTECH_NO_TRAY"] = "1"
    sys.argv = ["widget_app.py"]
    widget_app.main()


def run_collect() -> None:
    """凭据导入向导：独立进程跑一个 WebView2 窗口，存完就退。"""
    from desktop import credential_wizard

    code = credential_wizard.run()
    if code:
        sys.exit(code)


def run_update_runner(argv: list[str]) -> None:
    """更新进程：等管理器退出 → 二次校验 → 静默覆盖安装。

    为什么要单独占一个进程：`installer.do_install()` 第一步就是
    `taskkill /IM CUKTECH.exe`，让管理器「自己杀自己」会留下 WebView2 孤儿进程占住
    `_internal` 里的 dll，而清理旧文件是 `except OSError: pass` —— 被占的文件被跳过，
    结果新旧文件混装。所以必须是「管理器先退干净，外部进程再装」。
    """
    from desktop import updater

    rest = argv[argv.index("--update-runner") + 1:]
    setup = rest[0] if rest else ""
    sha = rest[1] if len(rest) > 1 else ""
    if not setup:
        print("用法：CUKTECH.exe --update-runner <setup.exe> [sha256]", file=sys.stderr)
        sys.exit(2)
    sys.exit(updater.run_runner(setup, sha))


def _release_single_instance() -> None:
    """提前放掉单实例互斥量。

    只在「WebView2 起不来、要整进程重启」这一条路上用：不先放掉，
    新进程会被 CreateMutexW 判定为「已有实例」，直接调头退出。
    """
    global _MUTEX
    try:
        if _MUTEX:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(_MUTEX)
    except Exception:  # noqa: BLE001
        pass
    _MUTEX = None


def run_manager(silent: bool) -> None:
    if not _single_instance():
        _wake_existing()
        return

    from desktop import manager as manager_mod
    from desktop.manager import Manager

    # 窗口渲染失败时的重启要用到它（见 Manager._restart_process）
    manager_mod.set_mutex_releaser(_release_single_instance)
    manager = Manager(silent=silent)
    _watch_wake_event(manager)
    manager.run()


def _watch_wake_event(manager) -> None:
    """等「已有实例被再次双击」的事件，收到就把控制台窗口亮出来。"""
    import threading

    def loop():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        event = kernel32.CreateEventW(None, False, False, "Local\\CUKTECH_ShowPanel")
        while True:
            kernel32.WaitForSingleObject(event, 0xFFFFFFFF)  # INFINITE
            try:
                manager.show_console()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=loop, daemon=True).start()


def _gate_webview2(argv: list[str]) -> None:
    """创建任何窗口之前，先确认 WebView2 运行时在。

    缺了它窗口不会报错也不会退出，只是「什么都不出现」—— 这是发给非技术用户时
    最容易变成「软件打不开」投诉的失败模式，所以自己先拦一道。
    采集服务（--service）与更新进程（--update-runner）不建窗口，不拦。
    """
    role = "widget" if "--widget" in argv else ("collect" if "--collect" in argv else "manager")
    interactive = "--silent" not in argv      # 开机自启是 --silent，绝对不能弹模态框
    try:
        from desktop import webview2_check

        ok = webview2_check.ensure(interactive=interactive, role=role)
    except Exception:  # noqa: BLE001
        # 预检自身出问题不该把程序搞挂，放行让 WebView2 自己去报错
        return
    if not ok:
        sys.exit(1)


def main() -> None:
    _patch_streams()
    argv = sys.argv[1:]
    if "--service" not in argv and "--update-runner" not in argv:
        _gate_webview2(argv)
    if "--service" in argv:
        run_service()
    elif "--widget" in argv:
        run_widget()
    elif "--collect" in argv:
        run_collect()
    elif "--update-runner" in argv:
        run_update_runner(argv)
    else:
        run_manager(silent="--silent" in argv)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        try:
            from desktop.manager import log

            log(f"入口异常 {exc!r}")
        except Exception:  # noqa: BLE001
            pass
        raise
