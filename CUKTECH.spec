# -*- mode: python ; coding: utf-8 -*-
"""CUKTECH 桌面版打包配置（onedir）。

一般不用直接调它，走一键构建：
    .venv\\Scripts\\python.exe build_installer.py

产物：dist\\CUKTECH\\CUKTECH.exe + _internal\\...
安装包（onefile，PyInstaller 打的 tkinter 安装器）由 build_installer.py 在其上生成，
不是 Inno Setup —— 早先的注释写成 Inno Setup，跟实现不符。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH).resolve()

# ---- 包内 .env 必须是一份「空模板」----
# 这里绝对不能直接打 HERE/".env"：那是开发机的工作配置，里面是真 MAC / TOKEN / BLE KEY。
# 一旦打进包，任何人解压 _internal\.env 就拿到设备控制权。
# PyInstaller 的 datas 不支持给文件改名，所以先把 .env.example 复制成
# build/_envshim/.env，再把那份打进去，落点仍然是 _internal\.env。
_ENV_SHIM = HERE / "build" / "_envshim" / ".env"
_ENV_SHIM.parent.mkdir(parents=True, exist_ok=True)
_ENV_SRC = HERE / ".env.example"
_ENV_SHIM.write_text(
    _ENV_SRC.read_text(encoding="utf-8") if _ENV_SRC.is_file() else "",
    encoding="utf-8",
)

# 只读资源：运行时要能从 sys._MEIPASS 读到
datas = [
    (str(_ENV_SHIM), "."),
    (str(HERE / "widget.html"), "."),
    (str(HERE / "console_host.html"), "."),   # 内部控制台窗口的本地引导页
    (str(HERE / "console_overlay.js"), "."),  # 注入控制台页面的运行管理浮层
    (str(HERE / "credential_wizard.html"), "."),  # 凭据导入向导页面
    (str(HERE / "web"), "web"),               # index.html + vendor/chart.umd.min.js
]

# 取码工具跟着安装包走（用户不用另外找），落在 _internal\tools\。
# 只挑运行期真要用的三个文件：整个 tools\ 目录塞进去会把调试脚本、截图和
# __pycache__ 一起带进包里（v1.8.0 实测多出 200 KB 且毫无意义）。
# 旧开发工作区里 tools\ 在上一层，标准结构里就在仓库根，两种都认。
_TOOLS = next(
    (p for p in (HERE / "tools", HERE.parent / "tools") if p.is_dir()),
    HERE / "tools",
)
for _name in ("token_extractor.exe", "collect_credentials.py", "watch_qr.py"):
    _f = _TOOLS / _name
    if _f.is_file():
        datas.append((str(_f), "tools"))

hiddenimports = [
    # 上游包是 editable 安装，PyInstaller 分析不到，必须点名
    "cuktech_ble_controller",
    "cuktech_ble_controller.ble",
    "cuktech_ble_controller.server",
    "cuktech_ble_controller.__main__",
    # 本项目
    "desktop",
    "desktop.paths",
    "desktop.manager",
    "desktop.autostart",
    "desktop.icon",
    "desktop.version",      # 版本号唯一来源，管理器和卡片都从这里取
    "desktop.autoport",     # 7:00–12:00 启动时自动开充电口
    "desktop.wv2env",       # 函数内 import，点名更保险
    "desktop.webview2_check",  # 启动闸门，函数内 import
    "winreg",               # 预检读注册表，动态 import
    "desktop.credentials",  # 凭据读写，向导和采集服务都要用
    "desktop.credential_wizard",  # --collect 角色，函数内 import
    # 托盘后端与 webview 后端都是运行期动态 import 的
    "pystray._win32",
    "webview.platforms.winforms",
]

# bleak 在 Windows 上走 winrt，这些模块全是动态导入
hiddenimports += collect_submodules("winrt")

a = Analysis(
    [str(HERE / "main.py")],
    pathex=[str(HERE / "src"), str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "scipy", "pandas",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "IPython", "jupyter", "notebook", "docutils",
        "pytest",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CUKTECH",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 模式：子进程也不弹黑框
    disable_windowed_traceback=False,
    icon=str(HERE / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CUKTECH",
)
