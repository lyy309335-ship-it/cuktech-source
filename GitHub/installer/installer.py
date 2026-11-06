"""CUKTECH 安装 / 卸载器。

按用户安装（per-user），全程不需要管理员权限：
    程序 → %LOCALAPPDATA%\\Programs\\CUKTECH
    数据 → %LOCALAPPDATA%\\CUKTECH（配置、历史功率、日志）

用法：
    CUKTECH-Setup.exe              图形安装
    CUKTECH-Setup.exe --uninstall  图形卸载（安装目录里的 Uninstall.exe 就是这个）
    CUKTECH-Setup.exe /S           静默安装（默认选项）
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

APP_NAME = "CUKTECH"
APP_TITLE = "CUKTECH 充电器控制台"
APP_EXE = "CUKTECH.exe"
# 随安装包附带的更新记录。构建时用 --add-data 塞进安装器的 _MEIPASS，
# 装完复制到程序目录根下，用户双击就能看历次改了什么。
CHANGELOG_NAME = "版本更新说明.md"
# 构建时由 build_installer.py 用 desktop/version.py 的值覆盖（唯一来源），
# 这里的值只是「直接手跑 installer.py」时的兜底 —— 但它必须跟版本号一起改，
# 否则兜底路径会写出一个「装完显示旧版本」的安装器（v1.4.1 之前一直停在 1.3.0）。
VERSION = "1.8.0"
PUBLISHER = "CUKTECH Desktop"

# 不能只信环境变量：从某些宿主（服务、计划任务、精简 shell）启动时
# APPDATA / USERPROFILE 可能根本不存在，快捷方式就会建到当前目录去。
# SHGetFolderPathW 走的是系统实际配置（含 OneDrive 重定向的桌面）。
_CSIDL_DESKTOPDIRECTORY = 0x0010
_CSIDL_PROGRAMS = 0x0002
_CSIDL_APPDATA = 0x001A
_CSIDL_LOCAL_APPDATA = 0x001C


def _shell_folder(csidl: int, fallback: Path) -> Path:
    try:
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:  # noqa: BLE001
        pass
    return fallback


_HOME = Path(os.environ.get("USERPROFILE") or Path.home())
LOCALAPPDATA = _shell_folder(_CSIDL_LOCAL_APPDATA, _HOME / "AppData" / "Local")
APPDATA = _shell_folder(_CSIDL_APPDATA, _HOME / "AppData" / "Roaming")
PROGRAMS = _shell_folder(_CSIDL_PROGRAMS, APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs")
DESKTOP = _shell_folder(_CSIDL_DESKTOPDIRECTORY, _HOME / "Desktop")

APP_DIR = LOCALAPPDATA / "Programs" / APP_NAME
DATA_DIR = LOCALAPPDATA / APP_NAME
START_MENU = PROGRAMS / APP_NAME
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
LOG_FILE = Path(tempfile.gettempdir()) / "cuktech-setup.log"


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 工具


def payload_zip() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "payload" / "payload.zip"


def _running() -> bool:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {APP_EXE}", "/FO", "CSV", "/NH"],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15,
        )
        out = (r.stdout or b"").decode("mbcs", errors="replace")
        return APP_EXE in out
    except Exception:  # noqa: BLE001
        return False


def kill_running() -> None:
    """关闭正在跑的实例。

    先「请」它自己关：管理器收到 WM_CLOSE 会正常收尾，WebView2 子进程才会跟着退出、
    释放 _internal 里的 dll/pyd。直接 /F 强杀会把 WebView2 进程留成孤儿，
    文件就一直被占着，卸载删不干净。
    """
    if not _running():
        return
    subprocess.run(["taskkill", "/IM", APP_EXE], capture_output=True,
                   creationflags=CREATE_NO_WINDOW)
    for _ in range(6):  # 最多等 3 秒：管理器把「关窗口」做成了收进托盘，多半不会响应
        time.sleep(0.5)
        if not _running():
            log("应用已正常退出")
            time.sleep(1.0)
            return
    log("正常关闭未响应，强制结束")
    subprocess.run(["taskkill", "/IM", APP_EXE, "/F"], capture_output=True,
                   creationflags=CREATE_NO_WINDOW)
    time.sleep(2.0)


def disable_legacy_tasks() -> None:
    """早期手动部署的两套计划任务会和本应用抢 8080 端口与蓝牙链路，装新版时顺手停掉。"""
    for name in ("CUKTECH充电器控制台", "CUKTECH桌面组件"):
        for args in (["/End", "/TN", name], ["/Change", "/TN", name, "/DISABLE"]):
            try:
                subprocess.run(
                    ["schtasks", *args],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=15,
                )
            except Exception:  # noqa: BLE001
                pass


def make_shortcut(target: Path, link: Path, workdir: Path | None = None) -> bool:
    """用 PowerShell 建 .lnk，不依赖 pywin32。"""
    link.parent.mkdir(parents=True, exist_ok=True)
    wd = str(workdir or target.parent)
    ps = (
        "$W=New-Object -ComObject WScript.Shell;"
        f"$S=$W.CreateShortcut('{link}');"
        f"$S.TargetPath='{target}';"
        f"$S.WorkingDirectory='{wd}';"
        f"$S.IconLocation='{target},0';"
        f"$S.Description='{APP_TITLE}';"
        "$S.Save()"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=30,
        )
        # 偶尔返回码非零但 .lnk 其实建好了，以文件是否存在为准
        return r.returncode == 0 or link.is_file()
    except Exception:  # noqa: BLE001
        return False


def register_uninstall(install_dir: Path, size_kb: int) -> None:
    import winreg

    exe = install_dir / APP_EXE
    uninst = install_dir / "Uninstall.exe"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_TITLE)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{uninst}" --uninstall')
        winreg.SetValueEx(k, "QuietUninstallString", 0, winreg.REG_SZ, f'"{uninst}" --uninstall /S')
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, max(size_kb, 1))


def remove_uninstall_key() -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def set_autostart(enable: bool, exe: Path) -> None:
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{exe}" --manager --silent')
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass


def is_installed() -> bool:
    return (APP_DIR / APP_EXE).is_file()


def has_desktop_shortcut() -> bool:
    try:
        return (DESKTOP / f"{APP_NAME}.lnk").is_file()
    except OSError:
        return False


def autostart_enabled() -> bool:
    """读现有的开机自启状态。和 set_autostart 写的是同一个 Run 值。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def dir_size_kb(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total // 1024


# ---------------------------------------------------------------- 安装逻辑


def copy_changelog(install_dir: Path) -> None:
    """把版本更新说明放到程序目录根下。

    payload.zip 里只有程序文件，不含文档，所以这份是构建时单独塞进安装器的。
    拿不到就只记一行日志——文档缺失不该让整个安装失败。
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    src = base / CHANGELOG_NAME
    if not src.is_file():
        log(f"没有找到 {CHANGELOG_NAME}（{src}），跳过")
        return
    dst = install_dir / CHANGELOG_NAME
    try:
        shutil.copy2(src, dst)
        log(f"版本更新说明 -> {dst}")
    except OSError as exc:
        log(f"复制版本更新说明失败 {exc!r}")


def do_install(create_desktop: bool, autostart: bool, progress=None) -> None:
    def step(msg: str, pct: int) -> None:
        if progress:
            progress(msg, pct)

    step("正在关闭已运行的实例…", 3)
    kill_running()
    disable_legacy_tasks()
    log(f"开始安装 -> {APP_DIR}")

    src = payload_zip()
    if not src.is_file():
        raise RuntimeError("安装包损坏：找不到 payload.zip")

    step("正在清理旧版本文件…", 8)
    if APP_DIR.exists():
        # 保留用户数据（数据在 DATA_DIR，不在这里），只清程序文件
        for child in APP_DIR.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            except OSError:
                pass
    APP_DIR.mkdir(parents=True, exist_ok=True)

    step("正在解压程序文件…", 15)
    with zipfile.ZipFile(src) as zf:
        members = zf.infolist()
        total = len(members) or 1
        for i, item in enumerate(members):
            zf.extract(item, APP_DIR)
            if i % 40 == 0:
                step("正在解压程序文件…", 15 + int(60 * i / total))

    # zip 里包了一层 CUKTECH/，把内层提到顶层
    inner = APP_DIR / APP_NAME
    if (inner / APP_EXE).is_file():
        for child in list(inner.iterdir()):
            shutil.move(str(child), str(APP_DIR / child.name))
        shutil.rmtree(inner, ignore_errors=True)

    exe = APP_DIR / APP_EXE
    if not exe.is_file():
        raise RuntimeError("解压后没有找到 CUKTECH.exe")

    step("正在写入版本更新说明…", 78)
    copy_changelog(APP_DIR)

    step("正在创建快捷方式…", 80)
    ok_desk = make_shortcut(exe, DESKTOP / f"{APP_NAME}.lnk", APP_DIR) if create_desktop else None
    ok_menu = make_shortcut(exe, START_MENU / f"{APP_NAME}.lnk", APP_DIR)
    log(f"快捷方式 桌面={ok_desk} 开始菜单={ok_menu}（{START_MENU}）")

    step("正在写入卸载信息…", 88)
    # 卸载器 = 安装器自己的副本
    try:
        shutil.copy2(sys.executable, APP_DIR / "Uninstall.exe")
    except Exception:  # noqa: BLE001
        pass
    register_uninstall(APP_DIR, dir_size_kb(APP_DIR))

    step("正在设置开机自启…", 94)
    set_autostart(autostart, exe)

    step("安装完成，正在启动…", 100)
    log(f"安装完成，启动 {exe}")
    try:
        # 必须脱离安装器：否则安装器进程一退出，刚拉起的应用会被一起收走
        subprocess.Popen(
            [str(exe)],
            cwd=str(APP_DIR),
            close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"启动失败 {exc!r}")


# ---------------------------------------------------------------- 卸载逻辑


def schedule_delete_dir(install_dir: Path) -> None:
    """在安装目录「外面」写一个延时批处理，等本进程退出后再删整个目录。

    不用「复制自身到 temp 再执行」那一套：onefile 的卸载器本身有 30+MB，
    复制 + 二次解压又慢又容易在受限环境下失败；批处理只有几百字节，
    而且带重试，能兜住 WebView2 之类还没释放的文件。
    """
    bat = Path(tempfile.gettempdir()) / f"cuktech-uninst-{os.getpid()}.bat"
    # 用 ping 而不是 timeout：DETACHED_PROCESS 下没有控制台，
    # timeout 会直接报「输入重定向错误」并立刻返回，重试循环等于空转。
    lines = [
        "@echo off",
        "ping -n 4 127.0.0.1 >nul",
        "for /L %%i in (1,1,15) do (",
        f'  rmdir /s /q "{install_dir}" 2>nul',
        f'  if not exist "{install_dir}" goto done',
        "  ping -n 4 127.0.0.1 >nul",
        ")",
        ":done",
        'del "%~f0"',
    ]
    try:
        bat.write_bytes("\r\n".join(lines).encode("gbk", errors="replace"))
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            close_fds=True,
        )
        log(f"已安排延时删除 {install_dir}（脚本 {bat}）")
    except Exception as exc:  # noqa: BLE001
        log(f"安排延时删除失败 {exc!r}")


def do_uninstall(remove_data: bool, progress=None) -> None:
    def step(msg: str, pct: int) -> None:
        if progress:
            progress(msg, pct)

    log(f"开始卸载 remove_data={remove_data} exe={sys.executable}")
    step("正在关闭程序…", 5)
    kill_running()

    set_autostart(False, APP_DIR / APP_EXE)

    step("正在删除快捷方式…", 15)
    for link in (DESKTOP / f"{APP_NAME}.lnk", START_MENU / f"{APP_NAME}.lnk"):
        try:
            link.unlink()
            log(f"删除快捷方式 {link}")
        except OSError:
            pass
    try:
        START_MENU.rmdir()
    except OSError:
        pass

    step("正在清理注册表…", 25)
    remove_uninstall_key()

    if remove_data:
        step("正在删除配置与历史数据…", 40)
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        log(f"删除数据目录 {DATA_DIR} -> 存在={DATA_DIR.exists()}")

    step("正在删除程序文件…", 60)
    in_place = Path(sys.executable).resolve().parent == APP_DIR.resolve()
    if in_place:
        # 先就地删掉能删的，把批处理要处理的量降到最低（主要是自己和被锁的 dll）
        for child in list(APP_DIR.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                elif child.name.lower() != Path(sys.executable).name.lower():
                    child.unlink()
            except OSError:
                pass
        left = [p.name for p in APP_DIR.iterdir()] if APP_DIR.exists() else []
        log(f"就地删除完成，剩余 {left}")
        if APP_DIR.exists():
            schedule_delete_dir(APP_DIR)
        step("卸载完成", 100)
        time.sleep(0.4)
        os._exit(0)

    shutil.rmtree(APP_DIR, ignore_errors=True)
    log(f"删除程序目录 {APP_DIR} -> 存在={APP_DIR.exists()}")
    step("卸载完成", 100)


# ---------------------------------------------------------------- 界面


def run_gui(uninstall_mode: bool) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    BG, CARD, FG, MUTE, ACCENT = "#0f1116", "#171a21", "#e8ecf4", "#8b93a5", "#2a9d8f"

    root = tk.Tk()
    root.title(f"{APP_TITLE} · {'卸载' if uninstall_mode else '安装'}")
    root.configure(bg=BG)
    root.resizable(False, False)
    root.geometry("460x300" if not uninstall_mode else "460x260")

    try:
        if getattr(sys, "frozen", False):
            root.iconbitmap(default=str(Path(getattr(sys, "_MEIPASS", "")) / "app.ico"))
    except Exception:  # noqa: BLE001
        pass

    pad = {"padx": 24}
    tk.Label(root, text=f"{APP_TITLE}", bg=BG, fg=FG,
             font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(22, 2), **pad)
    tk.Label(root, text=f"版本 {VERSION} · {'卸载程序' if uninstall_mode else '安装到当前用户，无需管理员权限'}",
             bg=BG, fg=MUTE, font=("Microsoft YaHei UI", 9)).pack(anchor="w", **pad)

    box = tk.Frame(root, bg=CARD)
    box.pack(fill="x", pady=14, **pad)

    def cb(text, var, y):
        c = tk.Checkbutton(box, text=text, variable=var, bg=CARD, fg=FG,
                           selectcolor=CARD, activebackground=CARD, activeforeground=FG,
                           font=("Microsoft YaHei UI", 9), anchor="w", bd=0, highlightthickness=0)
        c.pack(anchor="w", padx=14, pady=y)
        return c

    if uninstall_mode:
        var_data = tk.BooleanVar(value=False)
        cb("同时删除配置与历史数据（不勾选则保留，重装后继续用）", var_data, 12)
        var_desk = None
        var_auto = None
        target_text = f"程序目录  {APP_DIR}"
    else:
        var_desk = tk.BooleanVar(value=True)
        var_auto = tk.BooleanVar(value=True)
        cb("创建桌面快捷方式", var_desk, 8)
        cb("开机自动启动（登录后在托盘运行）", var_auto, 2)
        var_data = None
        target_text = f"安装位置  {APP_DIR}"

    tk.Label(root, text=target_text, bg=BG, fg="#5d6474",
             font=("Microsoft YaHei UI", 8)).pack(anchor="w", **pad)

    bar = ttk.Progressbar(root, mode="determinate", maximum=100)
    bar.pack(fill="x", pady=(12, 4), **pad)
    status = tk.Label(root, text="就绪", bg=BG, fg=MUTE, font=("Microsoft YaHei UI", 8), anchor="w")
    status.pack(fill="x", **pad)

    btns = tk.Frame(root, bg=BG)
    btns.pack(fill="x", pady=(10, 0), **pad)

    running = {"v": False}

    def progress(msg, pct):
        root.after(0, lambda: (status.config(text=msg), bar.configure(value=pct)))

    def worker(fn):
        try:
            fn()
            root.after(0, lambda: finish(True))
        except Exception as exc:  # noqa: BLE001
            err = repr(exc)
            root.after(0, lambda: finish(False, err))

    def finish(ok, err=""):
        running["v"] = False
        btn.config(state="normal")
        if ok:
            bar.configure(value=100)
            status.config(text="完成")
            cancel.config(text="关闭")
            if not uninstall_mode:
                messagebox.showinfo(APP_TITLE, "安装完成，程序已在托盘运行。")
                root.destroy()
            else:
                messagebox.showinfo(APP_TITLE, "卸载完成。")
                root.destroy()
        else:
            status.config(text="失败")
            messagebox.showerror(APP_TITLE, f"操作失败：\n{err}")

    def go():
        if running["v"]:
            return
        running["v"] = True
        btn.config(state="disabled")
        cancel.config(text="取消" )
        if uninstall_mode:
            threading.Thread(target=worker, args=(lambda: do_uninstall(var_data.get(), progress),), daemon=True).start()
        else:
            threading.Thread(
                target=worker,
                args=(lambda: do_install(var_desk.get(), var_auto.get(), progress),),
                daemon=True,
            ).start()

    def close():
        if running["v"]:
            return
        root.destroy()

    btn = tk.Button(btns, text="卸载" if uninstall_mode else "安装", command=go,
                    bg=ACCENT, fg="#ffffff", activebackground="#248277", activeforeground="#fff",
                    font=("Microsoft YaHei UI", 10), bd=0, relief="flat", cursor="hand2")
    btn.pack(side="right", ipadx=24, ipady=6)
    cancel = tk.Button(btns, text="取消", command=close,
                       bg=CARD, fg=FG, activebackground="#252a35", activeforeground=FG,
                       font=("Microsoft YaHei UI", 10), bd=0, relief="flat", cursor="hand2")
    cancel.pack(side="right", ipadx=18, ipady=6, padx=(0, 10))

    root.mainloop()


def main() -> None:
    argv = [a.lower() for a in sys.argv[1:]]
    uninstall_mode = "--uninstall" in argv
    silent = "/s" in argv or "--silent" in argv or "--from-temp" in argv

    if uninstall_mode:
        purge = "--purge" in argv
        if silent:
            do_uninstall(remove_data=purge)
            return
        run_gui(uninstall_mode=True)
        return

    if silent:
        # 覆盖安装（用户点「立即更新」走的也是这条）不能把用户的选择改回去。
        # 以前这里无条件 autostart=True / create_desktop=True，实测有两个副作用：
        #   ① 用户手动关掉的「开机自启」，更新一次又被悄悄打开；
        #   ② 用户删掉的桌面快捷方式，更新一次又冒出来。
        # 改成：只有首次安装才用默认值，之后一律沿用现状。
        first_install = not is_installed()
        do_install(
            create_desktop=True if first_install else has_desktop_shortcut(),
            autostart=True if first_install else autostart_enabled(),
        )
        return
    run_gui(uninstall_mode=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        try:
            from tkinter import messagebox

            messagebox.showerror(APP_TITLE, f"启动失败：{exc!r}")
        except Exception:  # noqa: BLE001
            pass
        raise
