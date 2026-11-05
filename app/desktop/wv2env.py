"""WebView2 运行参数与兼容性修补（统一出口）。

## 1. 沙箱修补（必须保留，见下）

### 现象
窗口能创建、标题栏正常，页面 HTML / 脚本也都加载了（服务端能看到 GET /、/console.js、
WebSocket 连上、js_api 回调也回来了），但客户区**始终是一块纯背景色**——
控制台是 #0f1116，卡片是白/黑块。看起来像"页面没渲染"，其实是**一帧都没画到窗口上**。

### 取证
1. `data/webview2-console/EBWebView/Crashpad/reports/*.dmp` 每次启动都会多出几个。
   解析 minidump：异常码 `0x80000003`（STATUS_BREAKPOINT，即 Chromium 的 CHECK 失败），
   出错地址固定在 `msedge.dll+0xb47d1bd`，模块列表里带 `userenv.dll`。
2. 同一台机器上系统自带的 WebView2 应用（搜索 / 小组件）**没有任何 dmp**，
   说明运行时本身没坏，是我们这个宿主进程触发的。
3. 用 `Page.captureScreenshot` 让浏览器自己截图：**界面是完整的**（含实时功率数据），
   说明渲染管线正常，问题出在渲染进程被沙箱相关的 CHECK 打死 → 没有帧送到窗口。

### 修法
给 WebView2 追加 `--no-sandbox`（实测：崩溃 dmp 归零，窗口正常显示，数据实时刷新）。

安全性说明：本程序只加载 `http://127.0.0.1:8080/` 上的自有页面和本地 HTML，
不加载任何不可信的远端内容，关掉渲染沙箱的暴露面可以接受。
只关 GPU 沙箱（`--disable-gpu-sandbox`）实测会让 WebView2 直接起不来，所以不能用那条更窄的开关。

## 2. ⚠️ 两个宿主**必须各用各的** user data folder（v1.3.7 回退）

v1.2.0 曾把控制台和卡片指向同一个目录，想让 WebView2 复用 browser/gpu/utility
进程组（12 个进程 → 7 个）。这个优化在本机是**有害的**，v1.3.7 已撤销：

- 第二个宿主创建控制器时稳定抛
  `0x8007139F ERROR_INVALID_STATE`（`CreateCoreWebView2ControllerAsync`），
  窗口在、里面却永远是一块纯背景色 —— 就是用户报的「控制台全黑」。
- 决定性对照实验（v1.3.7 排查）：现有目录 / **全新空目录**只要两宿主共用都失败；
  全新目录且只跑控制台则 100% 正常。所以不是目录被写坏，是共用本身。
- 加延时错开启动也没用，别再试。

代价是多一套 browser/gpu/utility（约 5 个进程、几十 MB），换回窗口稳定，值得。
`storage_path(role)` 显式传角色，就是为了不让两边再不小心指向同一个目录。

**关掉用不到的后台能力**仍然保留：本地单页应用不需要组件更新、同步、后台网络、
以及 Edge 自带的下载/PDF 悬浮 UI。
`additionalBrowserArguments` 依然统一由 `apply_sandbox_workaround()` 提供，
不要在别处另设（虽然分开目录后不再强制要求一致，保持一致仍便于排查）。

## 3. 试过但走不通的：CoreWebView2.TrySuspendAsync

思路是对的（收进托盘后把渲染进程挂起），实测在本项目里跑不通，别再试：

- WinForms 版的 WebView2 控件**没有** `CoreWebView2Controller` 属性（那是 WPF/Win32 组合 API），
  想置 `IsVisible = false` 只能动控件的 `Visible`。
- 而一设 `webview.Visible = False`，控件随即进入 disposed 状态，
  紧接着 `TrySuspendAsync()` 必抛
  `InvalidOperationException: CoreWebView2 members cannot be accessed after the
  WebView2 control is disposed` + `COMException 0x8007139F (ERROR_INVALID_STATE)`。
- 不动 Visible、只靠 `Form.Hide()` 也不行：控制器仍认为内容可见，Task 直接返回 false。
- 改走「最小化 + ShowInTaskbar=false」理论上能让控制器自动判不可见，但会改变
  「收进托盘」的窗口语义，收益（控制台 renderer 约 59 MB）不值这个风险。

另外：v1.2.0 的「共用数据目录」把 12 个进程压到 7 个，但会稳定触发黑屏，
v1.3.7 已回退成各用各的（见第 2 节）。省内存不能拿窗口稳定性换。
"""

from __future__ import annotations

import os

# --- 沙箱修补（单独保留这个名字，别处可能在引用）
FLAG = "--no-sandbox"

# --- 完整参数表（两个宿主共用，顺序无关，但内容必须一致）
BROWSER_ARGS = (
    "--no-sandbox",
    # Edge 的下载 / PDF 悬浮 UI：本地页面用不到，省下常驻的 utility 工作
    "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection",
    # 组件更新、账号同步、后台联网：WebView2 里全是空转
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--no-pings",
    "--disable-domain-reliability",
    # HTTP 缓存上限压到 8 MB（页面全是本地服务，缓存本来就没什么可存）
    "--disk-cache-size=8388608",
    # Chromium 的低内存档：缩小图片解码缓存和合成 tile 预算、压低后台帧率。
    # 两个窗口加起来不到 1200×820，画面也以静态数据为主，砍掉这部分资源
    # 肉眼看不出来，但能实打实降下 renderer 和 gpu 的常驻占用。
    "--enable-low-end-device-mode",
)

ARGS_STRING = " ".join(BROWSER_ARGS)


def apply_sandbox_workaround() -> None:
    """在 webview.start() 之前调用；WebView2 的浏览器进程启动时才读这个环境变量。"""
    current = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").strip()
    missing = [a for a in BROWSER_ARGS if a not in current]
    if not missing:
        return
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        (current + " " + " ".join(missing)).strip()
    )


def storage_path(role: str = "console") -> str:
    """按角色返回**各自独立**的 WebView2 数据目录。

    role 只能是 "console"（管理器）或 "widget"（桌面卡片）。两边共用一个目录会让
    第二个宿主的 CreateCoreWebView2ControllerAsync 抛 0x8007139F，窗口永远纯黑
    —— 这是 v1.3.7 用三组对照实验坐实的，别再改回去（详见本模块第 2 节）。

    `CUKTECH_WV2_DIR` 只给排查用：指向一个**已存在的普通文件**可稳定复现黑屏，
    指向一个空目录可做对照。正常使用不要设它。
    """
    override = os.environ.get("CUKTECH_WV2_DIR", "").strip()
    if override:
        return override
    from . import paths

    return str(paths.data_dir() / f"webview2-{role}")


def tune_gc() -> None:
    """进程初始化完成后冻结垃圾回收的扫描范围。

    三个角色（管理器 / 采集服务 / 卡片）启动后会常驻很久，但启动阶段创建的
    import 对象、模块常量、资源表之后再也不会变。gc.freeze() 把它们挪进永久代，
    后续每轮 GC 不再扫描 —— 内存省不了多少，但能消掉周期性全量扫描带来的
    几十毫秒卡顿（对托盘刷新、BLE 回调这类小任务最明显）。

    必须放在初始化**之后**调用，否则会把还要改的对象冻住。
    """
    import gc

    gc.collect()
    gc.freeze()
    # 默认阈值 (700,10,10) 对常驻小进程偏激进：放宽后 GC 次数更少、单次稍长，
    # 但常驻进程本来就没多少垃圾，总体 CPU 更低
    gc.set_threshold(2000, 20, 20)
