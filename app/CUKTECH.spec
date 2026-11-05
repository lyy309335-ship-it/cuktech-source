# -*- mode: python ; coding: utf-8 -*-
"""CUKTECH 桌面版打包配置（onedir）。

用法（在 app 目录下）：
    .venv\\Scripts\\pyinstaller.exe --noconfirm --clean CUKTECH.spec

产物：dist\\CUKTECH\\CUKTECH.exe + _internal\\...
安装包由 installer\\build_installer.py（Inno Setup）在其上生成。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH).resolve()

# 只读资源：运行时要能从 sys._MEIPASS 读到
datas = [
    (str(HERE / ".env"), "."),
    (str(HERE / "widget.html"), "."),
    (str(HERE / "console_host.html"), "."),   # 内部控制台窗口的本地引导页
    (str(HERE / "console_overlay.js"), "."),  # 注入控制台页面的运行管理浮层
    (str(HERE / "web"), "web"),               # index.html + vendor/chart.umd.min.js
]

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
