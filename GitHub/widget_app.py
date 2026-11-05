"""CUKTECH 桌面卡片（钉在桌面上）+ 系统托盘。

窗口用 pywebview + 系统自带 Edge WebView2 渲染 widget.html，
托盘用 pystray，两者互不影响：关掉卡片只是收进托盘，服务照常跑。

两种显示模式（托盘可切，默认「固定在桌面」）：
  desktop —— **挂成桌面窗口（Progman）的子窗口**，和桌面壁纸、桌面图标、
             「腾讯桌面整理」的格子在同一层：看桌面时它在，一开别的窗口就被盖住；
             按 Win+D 不会消失（子窗口不参与最小化）。
  top     —— 悬浮置顶，永远压在最上层。

窗口位置和模式存在 data/widget_window.json，下次启动恢复。

关于「为什么这次真的 SetParent 到桌面了」（v1.3.4 修正了 v1.3.0 的结论）
-------------------------------------------------------------------------
v1.3.0 记的是「SetParent 到 Progman/WorkerW 后什么都不渲染」，于是退而求其次用
「z-order 紧贴桌面之上」。但那个做法有两个治不好的毛病：

  1. 按 Win+D 时 Shell 会把桌面（Progman + SHELLDLL_DefView）提到最前，
     卡片作为独立顶层窗口被压到它下面 —— 屏幕上是「卡片消失了」，
     而 IsWindowVisible 仍然是 True，看门狗根本发现不了（v1.3.4 实测：
     按 Win+D 后 visible=True iconic=False，但截屏里只有桌面图标）。
  2. 顶层窗口的圆角要靠窗体背景色混，透明没真正透到壁纸，
     四角看起来是直角（v1.3.4 用户反馈「三个直角一个圆角」）。

2026-11-04 重新实测，SetParent 这条路是通的，之前失败的原因是**漏了配套动作**：

  - 必须同时把窗口样式从 WS_POPUP 换成 WS_CHILD（只 SetParent 不改样式，
    DWM 仍按顶层窗口处理，子窗口的绘制结果是空的 —— 这就是当初"什么都不显示"）；
  - 必须再 ShowWindow(SW_SHOWNOACTIVATE) 一次；
  - 位置要按父窗口客户区坐标重设（Progman 全屏且在 (0,0)，屏幕坐标可直接用）。

实测结果：挂到 Progman 后卡片正常渲染、四角圆角是真的透出壁纸，按 Win+D 依然在，
开应用窗口照样被盖住。挂到 SHELLDLL_DefView（图标层）反而会被 SysListView32 盖住，
所以父窗口选 Progman，并让自己保持在兄弟窗口最上面。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from desktop import paths as _paths
from desktop.version import VERSION

BASE = _paths.resource_dir()
CONF = _paths.data_dir() / "widget_window.json"
LOG = _paths.log_dir() / "widget.log"
WIDGET_URL = (BASE / "widget.html").as_uri()
# ⚠️ 上面这个常量只是「本地页面的纯路径」，不要再在后面拼 ?shell=1：
#    pywebview 的 winforms 后端用 .NET `Uri(url)` 构造地址，file 协议里出现 `?`
#    会被转义成 %3F，文件名就变了（v1.3.5 踩过，见 pick_page_url 的注释）。
#    「我是被桌面窗口加载的」这件事由页面按 location.protocol 自己认。

API = "http://127.0.0.1:8080"
WIN_W, WIN_H = 324, 216
# 卡片底色（和 widget.html 的 --bg 对应）。
# 透明窗口里，圆角抗锯齿那圈半透明像素没法带 alpha 交给桌面，
# 只能和窗体背景色混合 —— 窗体默认背景是系统浅灰，混出来就是「白色描边」。
# 把窗体背景改成同一个深色，边缘就融进卡片里看不出来了。
CARD_BG = "#111318"
# 实测：frameless=True 时 pywebview 会把传入尺寸削掉 14×38（它以为还有窗口边框），
# 所以要补回去，否则卡片底部会被裁掉。
FRAME_INSET = (14, 38)
EDGE_MARGIN = 24
_MUTEX_HANDLE = None

MODES = [(1, "AI 智能"), (2, "数码生态"), (3, "单口优先"), (4, "均衡")]
PORTS = [("c1", "C1", 0, 1), ("c2", "C2", 1, 2), ("c3", "C3", 2, 3), ("a", "A", 3, 4)]

# ---------------------------------------------------------------- 基础设施


def log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


# 显式绕过系统代理：本机环境可能带着 http_proxy，会把 127.0.0.1 也代理走
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_get(path: str, timeout: float = 3.0):
    with _OPENER.open(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def api_post(path: str, payload: dict, timeout: float = 12.0):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API + path, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def primary_screen() -> tuple[int, int]:
    try:
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def load_conf() -> dict:
    try:
        data = json.loads(CONF.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_pos() -> tuple[int, int]:
    d = load_conf()
    try:
        x, y = int(d["x"]), int(d["y"])
        sw, sh = primary_screen()
        # 屏幕分辨率变了导致窗口跑到屏幕外时，拉回来
        if -WIN_W < x < sw and -WIN_H < y < sh:
            return x, y
    except Exception:
        pass
    sw, sh = primary_screen()
    return sw - WIN_W - EDGE_MARGIN, sh - WIN_H - 96


def load_mode() -> str:
    mode = load_conf().get("mode")
    return mode if mode in ("desktop", "top") else "desktop"


def load_pinned() -> bool:
    """卡片是否被「固定」在当前位置（固定后拖不动，重启也还在原处）。"""
    return bool(load_conf().get("pinned"))


def save_pos(
    x: int, y: int, mode: str | None = None, pinned: bool | None = None
) -> None:
    try:
        d = load_conf()
        d["x"], d["y"] = int(x), int(y)
        if mode:
            d["mode"] = mode
        if pinned is not None:
            d["pinned"] = bool(pinned)
        CONF.parent.mkdir(parents=True, exist_ok=True)
        CONF.write_text(json.dumps(d), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log(f"保存位置失败: {exc}")


# ---------------------------------------------------------------- 状态

STATE: dict = {"connected": False, "ready": False, "total": 0.0, "settings": {}, "ports": {}}


def refresh_state() -> bool:
    try:
        data = api_get("/api/status")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        STATE.update(connected=False, ready=False, total=0.0)
        return False
    STATE["ready"] = data.get("state") == "READY"
    STATE["connected"] = STATE["ready"]
    STATE["settings"] = data.get("settings") or {}
    STATE["ports"] = data.get("ports") or {}
    total = 0.0
    for _, _, _, piid in PORTS:
        d = STATE["ports"].get(str(piid)) or STATE["ports"].get(piid)
        if isinstance(d, dict):
            total += float(d.get("power") or 0)
    STATE["total"] = total
    return True


def port_enabled(bit: int) -> bool:
    v = STATE["settings"].get("16")
    if v is None:
        return True
    return bool(int(v) & (1 << bit))


# ---------------------------------------------------------------- 钉在桌面

GWL_EXSTYLE = -20
GWL_STYLE = -16
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
SWP_NOZORDER, SWP_NOOWNERZORDER = 0x0004, 0x0200
SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0020, 0x0040
GW_HWNDNEXT = 2
GW_HWNDPREV = 3
HWND_TOP = 0
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0

_u32 = ctypes.windll.user32
_u32.FindWindowW.restype = ctypes.c_void_p
_u32.FindWindowExW.restype = ctypes.c_void_p
_u32.GetWindow.restype = ctypes.c_void_p
_u32.GetWindowLongW.restype = ctypes.c_long
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _h(v):
    """句柄统一包一层，避免 ctypes 按 c_int 截断 64 位句柄。"""
    return ctypes.c_void_p(int(v)) if v else None


def find_card_window() -> int:
    """pywebview 没暴露 native handle，只能按「进程 + 类名 + 标题」把自己找回来。

    ⚠️ 必须卡 PID：EnumWindows 是**全系统**枚举，而管理器的控制台窗口标题是
    「CUKTECH 控制台」、类名同样是 WindowsForms，前缀完全一样。只按标题匹配时
    hits[0] 会拿到控制台窗口（EnumWindows 按 z-order 从上到下，桌面模式的卡片
    永远在最底下），于是「钉桌面」实际在挪控制台 —— 表现就是卡片拖走后弹不回、
    固定位置记的全是控制台的坐标。本进程自己的窗口才是卡片。

    ⚠️ v1.3.4 起卡片是 Progman 的**子窗口**，`EnumWindows`（只枚举顶层窗口）
    再也找不到它。所以顶层扫一遍没结果时，还要往桌面窗口的子窗口里扫。
    """
    me = os.getpid()
    hits: list[int] = []

    def cb(hwnd, _lp):
        pid = wt.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != me:
            return True
        n = _u32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 2)
        _u32.GetWindowTextW(hwnd, buf, n + 2)
        # 页面 <title> 是「CUKTECH 桌面组件」，窗口标题可能被同步也可能不，两种都认
        if not buf.value.startswith("CUKTECH"):
            return True
        cls = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(hwnd, cls, 256)
        if "WindowsForms" in cls.value:
            hits.append(hwnd)
        return True

    proc = _WNDENUMPROC(cb)
    _u32.EnumWindows(proc, 0)
    if hits:
        return hits[0]
    for top in ("Progman", "WorkerW"):
        d = _u32.FindWindowW(top, None) or 0
        if d:
            _u32.EnumChildWindows(_h(d), proc, 0)
            if hits:
                return hits[0]
    return 0


class DesktopPin:
    """把卡片窗口按模式摆到正确的 z-order 上，并负责「被系统藏起来后自己回来」。

    桌面模式下窗口紧贴 Progman（桌面）上方：任何应用窗口都在它上面，
    所以看桌面时它在、干活时它不挡。按 Win+D 会被系统一起藏起来，
    这里每 0.4 秒检查一次，发现被藏就重新显示（用户主动隐藏的不算）。
    """

    TICK = 0.4

    def __init__(self, want_visible):
        self.hwnd = 0
        self.mode = "desktop"
        self._want_visible = want_visible   # 回调：用户此刻是否希望它显示
        self._stop = False
        self._applied_once = False
        # 「固定」：记下锁定时窗口的物理矩形，之后一旦被移动就复位。
        # 用物理矩形而不是窗口逻辑坐标，省掉 125% DPI 下的换算。
        self.locked = False
        self._lock_rect: tuple[int, int, int, int] | None = None

    # --- 目标窗口
    def _desktop(self) -> int:
        h = _u32.FindWindowW("Progman", None) or 0
        if not h:
            h = _u32.FindWindowW("WorkerW", None) or 0
        return h

    def ensure(self) -> int:
        if not self.hwnd or not _u32.IsWindow(_h(self.hwnd)):
            self.hwnd = find_card_window()
        return self.hwnd

    # --- 两种模式
    def _to_desktop(self) -> None:
        """挂成桌面的子窗口，并确保自己在兄弟窗口里是最上面那个。

        子窗口的坐标是相对**父窗口客户区**的。Progman 占满主屏、客户区原点在
        (0,0)，所以屏幕坐标可以直接当子窗口坐标用；换多屏/非 (0,0) 的父窗口时
        这里要改成 MapWindowPoints 换算。
        """
        h, d = self.hwnd, self._desktop()
        if not h or not d:
            return
        parent = int(_u32.GetParent(_h(h)) or 0)
        if parent != d:
            rect = self._rect()
            if not rect:
                return
            x, y, w, hh = rect
            _u32.SetParent(_h(h), _h(d))
            style = _u32.GetWindowLongW(_h(h), GWL_STYLE)
            _u32.SetWindowLongW(_h(h), GWL_STYLE, (style | WS_CHILD) & ~WS_POPUP)
            _u32.SetWindowPos(_h(h), ctypes.c_void_p(HWND_TOP), x, y, w, hh,
                              SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
            # 换了父子关系必须重新 show 一次，否则 DWM 不给子窗口出画面
            _u32.ShowWindow(_h(h), SW_SHOWNOACTIVATE)
            # 光 show 还不够：WebView2 的合成器不知道窗口换了父，画面会停在
            # 挂父前那一帧（表现是「窗口在、位置对、屏幕上什么都没有」）。
            # 把高度抖 1px 再抖回来，逼它重新跑一次布局合成。
            time.sleep(0.05)
            _u32.SetWindowPos(_h(h), None, x, y, w, hh + 1, SWP_NOZORDER | SWP_NOACTIVATE)
            _u32.SetWindowPos(_h(h), None, x, y, w, hh, SWP_NOZORDER | SWP_NOACTIVATE)
            log(f"卡片已挂到桌面层（父 {hex(d)}，{w}×{hh} @ {x},{y}）")
            return
        # 已在桌面层：Win+D / 桌面刷新会把 SHELLDLL_DefView 提到我们上面，
        # 表现是「卡片被桌面图标盖住」。已经在最上面时这里只是一次 GetWindow。
        if int(_u32.GetWindow(_h(h), GW_HWNDPREV) or 0) != 0:
            _u32.SetWindowPos(_h(h), ctypes.c_void_p(HWND_TOP), 0, 0, 0, 0,
                              SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

    def _detach_from_desktop(self) -> None:
        """从桌面层摘出来，变回普通顶层窗口（切「悬浮置顶」用）。"""
        h = self.hwnd
        if not h or not int(_u32.GetParent(_h(h)) or 0):
            return
        rect = self._rect()
        _u32.SetParent(_h(h), None)
        style = _u32.GetWindowLongW(_h(h), GWL_STYLE)
        _u32.SetWindowLongW(_h(h), GWL_STYLE, (style & ~WS_CHILD) | WS_POPUP)
        if rect:
            x, y, w, hh = rect
            _u32.SetWindowPos(_h(h), ctypes.c_void_p(HWND_TOP), x, y, w, hh, SWP_NOACTIVATE)
        _u32.ShowWindow(_h(h), SW_SHOWNOACTIVATE)
        log("卡片已从桌面层脱出（改回普通窗口）")

    def _to_top(self) -> None:
        if not self.hwnd:
            return
        self._detach_from_desktop()
        # -1 = HWND_TOPMOST
        _u32.SetWindowPos(_h(self.hwnd), ctypes.c_void_p(-1), 0, 0, 0, 0,
                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

    def set_noactivate(self, on: bool = True) -> None:
        """桌面模式下不激活窗口，点卡片不会把它弹到最前面（否则会闪一下再沉下去）。"""
        h = self.ensure()
        if not h:
            return
        ex = _u32.GetWindowLongW(_h(h), GWL_EXSTYLE)
        new = (ex | WS_EX_NOACTIVATE) if on else (ex & ~WS_EX_NOACTIVATE)
        if new != ex:
            _u32.SetWindowLongW(_h(h), GWL_EXSTYLE, new)

    def set_toolwindow(self, on: bool = True) -> None:
        """把卡片从任务栏摘掉（WS_EX_TOOLWINDOW）。

        卡片是贴在桌面上的小挂件，不该在任务栏占一个按钮 —— 而且留着它是**真 bug**：
        点那个按钮，shell 会去「激活」一个带 WS_EX_NOACTIVATE 的窗口，系统把它
        最小化了；最小化时 IsWindowVisible 仍为 True，原来的看门狗只认「不可见」，
        于是卡片闪一下就再也没回来（要重新点开关才恢复）。
        控制台窗口是普通窗口，任务栏图标跟着它 show/hide 自然出现和消失。

        ⚠️ 只设 TOOLWINDOW 不够：pywebview/WinForms 建出来的窗口同时带
        **WS_EX_APPWINDOW (0x40000)**，而它的优先级更高，会强行把窗口塞进任务栏。
        必须两个一起处理（加 TOOLWINDOW 的同时清掉 APPWINDOW）。
        """
        h = self.ensure()
        if not h:
            return
        ex = _u32.GetWindowLongW(_h(h), GWL_EXSTYLE)
        if on:
            new = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        else:
            new = (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        if new == ex:
            return
        _u32.SetWindowLongW(_h(h), GWL_EXSTYLE, new)
        # 任务栏按钮是 shell 在窗口显示时登记的，只改 exstyle 不会自动撤掉，
        # 必须让它重新登记一次（hide → show，且不激活、不让它跳到最前）
        if _u32.IsWindowVisible(_h(h)) and self._want_visible():
            _u32.ShowWindow(_h(h), SW_HIDE)
            _u32.ShowWindow(_h(h), SW_SHOWNOACTIVATE)
            if self.mode == "desktop":
                self._to_desktop()
        log(f"任务栏按钮 {'已移除' if on else '已恢复'}（窗口 {hex(h)}，exstyle {hex(ex)} -> {hex(new)}）")

    def apply(self) -> None:
        if self.ensure():
            self.set_toolwindow(True)
            # ⚠️ 桌面层模式**不能**加 WS_EX_NOACTIVATE（v1.3.0~v1.3.3 一直加着，是错的）。
            # 卡片挂成 Progman 的子窗口以后，点它本来就不会改变顶层 z 序，用不上这个样式；
            # 而它会让窗口拿不到焦点，WebView2 于是把系统鼠标消息全丢掉 ——
            # 实测：挂父 + NOACTIVATE 时页面里 mousedown 计数恒为 0，去掉就是 1。
            self.set_noactivate(False)
            if self.mode == "desktop":
                self._to_desktop()
            else:
                self._to_top()
            if not self._applied_once:
                self._applied_once = True
                log(f"显示模式 = {self.mode}（窗口 {hex(self.hwnd)}）")

    def set_mode(self, mode: str) -> None:
        if mode not in ("desktop", "top"):
            return
        self.mode = mode
        self._applied_once = False
        self.apply()
        log(f"切换显示模式 -> {mode}")

    # --- 固定位置
    def _rect(self) -> tuple[int, int, int, int] | None:
        h = self.ensure()
        if not h:
            return None
        r = wt.RECT()
        if not _u32.GetWindowRect(_h(h), ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def lock(self) -> bool:
        """把卡片钉死在当前位置。"""
        rect = self._rect()
        if not rect:
            return False
        self._lock_rect = rect
        self.locked = True
        log(f"卡片已固定 ({rect[0]},{rect[1]})")
        return True

    def unlock(self) -> None:
        self.locked = False
        self._lock_rect = None
        log("卡片已解除固定")

    def _enforce_lock(self) -> None:
        """被别的程序/误拖移动了就拉回去。"""
        if not self.locked or not self._lock_rect:
            return
        cur = self._rect()
        if cur is None or cur == self._lock_rect:
            return
        x, y, w, h = self._lock_rect
        _u32.SetWindowPos(
            _h(self.hwnd), None, x, y, w, h,
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        )

    def desktop_alive(self) -> bool:
        """父窗口还在不在。Explorer 重启后旧 Progman 会被销毁，卡片得重新挂。"""
        if self.mode != "desktop":
            return True
        d = self._desktop()
        return bool(d) and bool(_u32.IsWindow(_h(d)))

    def reparent_if_orphan(self) -> None:
        """父窗口换了（Explorer 重启）就把自己挂到新的 Progman 上。"""
        h = self.hwnd
        if not h:
            return
        parent = int(_u32.GetParent(_h(h)) or 0)
        if parent and _u32.IsWindow(_h(parent)):
            return
        if parent:
            # 旧父窗口已经没了，窗口多半也成了孤儿，让它按顶层窗口重新走一遍流程
            _u32.SetParent(_h(h), None)
            style = _u32.GetWindowLongW(_h(h), GWL_STYLE)
            _u32.SetWindowLongW(_h(h), GWL_STYLE, (style & ~WS_CHILD) | WS_POPUP)
            log("桌面窗口已重建（Explorer 重启？），卡片重新挂载")
        self._to_desktop()

    # --- 守护线程
    def _loop(self) -> None:
        while not self._stop:
            try:
                if self.ensure():
                    # 窗口句柄被重建（比如 pywebview 改了 ShowInTaskbar）时
                    # 样式会退回默认值，任务栏按钮就回来了；每轮顺手校正一次，
                    # 已经正确时只是一次 GetWindowLong，几乎不花钱。
                    self.set_toolwindow(True)
                    if not self._want_visible():
                        pass
                    elif _u32.IsIconic(_h(self.hwnd)):
                        # 被最小化（点过任务栏按钮、或某些 Win+D 变体）：
                        # 最小化的窗口 IsWindowVisible 仍然是 True，只查可见性发现不了，
                        # 必须单独看 IsIconic，否则卡片就"闪一下然后消失"。
                        _u32.ShowWindow(_h(self.hwnd), SW_SHOWNOACTIVATE)
                        log("卡片被最小化，已恢复显示")
                    elif not _u32.IsWindowVisible(_h(self.hwnd)):
                        # 按了 Win+D / 显示桌面：桌面模式下卡片应该继续待在桌面上
                        if self.mode == "desktop":
                            _u32.ShowWindow(_h(self.hwnd), SW_SHOWNOACTIVATE)
                    if self.mode == "desktop":
                        # 先把父子关系修好（Explorer 重启过会变孤儿），再摆 z 序
                        if not self.desktop_alive():
                            self.hwnd = 0
                            log("桌面窗口不存在，跳过本轮（等下一个 tick 重找）")
                        else:
                            self.reparent_if_orphan()
                            self._to_desktop()
                    elif _u32.GetWindowLongW(_h(self.hwnd), GWL_EXSTYLE) & WS_EX_NOACTIVATE:
                        self.set_noactivate(False)
                    self._enforce_lock()
            except Exception as exc:  # noqa: BLE001
                log(f"[钉桌面] {exc!r}")
            time.sleep(self.TICK)

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop = True


# ---------------------------------------------------------------- 托盘


def _bolt_icon(power: float, connected: bool):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if not connected:
        color = (140, 146, 158, 255)
    elif power < 1:
        color = (110, 231, 214, 255)
    elif power < 60:
        color = (94, 234, 212, 255)
    elif power < 110:
        color = (250, 204, 21, 255)
    else:
        color = (251, 146, 60, 255)
    d.polygon([(37, 4), (16, 37), (29, 37), (25, 60), (48, 25), (33, 25)], fill=color)
    return img


class Tray:
    def __init__(self, on_toggle, on_quit, on_panel, on_display=None, get_display=None):
        import pystray

        self.pystray = pystray
        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self.on_panel = on_panel
        self.on_display = on_display
        self.get_display = get_display
        self.icon = pystray.Icon(
            "cuktech",
            _bolt_icon(0, False),
            "CUKTECH 充电器",
            menu=self._build_menu(),
        )

    def _build_menu(self):
        Menu, MenuItem = self.pystray.Menu, self.pystray.MenuItem
        return Menu(
            MenuItem("显示 / 隐藏卡片", self._toggle, default=True),
            MenuItem("打开完整面板", self._panel),
            Menu.SEPARATOR,
            MenuItem("显示方式", Menu(
                self._display_item("desktop", "固定在桌面"),
                self._display_item("top", "悬浮置顶"),
            )),
            MenuItem("场景模式", Menu(*[self._mode_item(m) for m in MODES])),
            MenuItem("端口开关", Menu(*[self._port_item(p) for p in PORTS])),
            Menu.SEPARATOR,
            MenuItem("退出", self._quit),
        )

    def _display_item(self, mode, label):
        def text(_item):
            cur = self.get_display() if self.get_display else "desktop"
            return ("● " if cur == mode else "○ ") + label

        def action(_icon, _item):
            if self.on_display:
                self.on_display(mode)

        return self.pystray.MenuItem(text, action)

    # pystray 只接受 0/1/2 个参数的动作函数（3 个及以上直接抛 ValueError），
    # 所以这里用闭包捕获变量，不用默认参数，否则 co_argcount 会算成 3。
    def _mode_item(self, mode):
        mode_id, label = mode

        def text(_item):
            return ("● " if STATE["settings"].get("5") == mode_id else "○ ") + label

        def action(_icon, _item):
            self._set_mode(mode_id)

        return self.pystray.MenuItem(text, action)

    def _port_item(self, port):
        key, label, bit, _piid = port

        def text(_item):
            return ("● " if port_enabled(bit) else "○ ") + label

        def action(_icon, _item):
            self._toggle_port(key)

        return self.pystray.MenuItem(text, action)

    # --- 菜单动作
    def _toggle(self, *_):
        self.on_toggle()

    def _panel(self, *_):
        self.on_panel()

    def _quit(self, *_):
        self.on_quit()

    def _set_mode(self, mode: int):
        try:
            api_post("/api/set", {"piid": 5, "value": mode})
            log(f"托盘：切换场景模式 -> {mode}")
        except Exception as exc:  # noqa: BLE001
            log(f"托盘：切模式失败 {exc}")
        self.sync()

    def _toggle_port(self, key: str):
        name, bit = next((p[1], p[2]) for p in PORTS if p[0] == key)
        action = "off" if port_enabled(bit) else "on"
        try:
            api_post("/api/port", {"port": key, "action": action})
            log(f"托盘：{name} -> {action}")
        except Exception as exc:  # noqa: BLE001
            log(f"托盘：{name} 开关失败 {exc}")
        self.sync()

    # --- 刷新
    def sync(self) -> None:
        refresh_state()
        try:
            self.icon.icon = _bolt_icon(STATE["total"], STATE["connected"])
            if STATE["connected"]:
                self.icon.title = f"CUKTECH  {STATE['total']:.1f} W"
            else:
                self.icon.title = "CUKTECH  未连接"
            self.icon.update_menu()
        except Exception as exc:  # noqa: BLE001
            log(f"托盘刷新失败 {exc}")

    def run(self) -> None:
        threading.Thread(target=self.icon.run, daemon=True).start()


# ---------------------------------------------------------------- JS 桥


class Bridge:
    # 注意：pywebview 会递归遍历 js_api 上所有非下划线属性来生成 JS 代理，
    # 把整个 App 挂成公开属性会让它一路走到 WinForms 的 AccessibilityObject 里，
    # 刷出成百上千行 "Error while processing ..."。必须用私有名。
    def __init__(self, app: "DesktopApp"):
        self._app = app

    def hide_window(self):
        self._app.hide()
        return True

    def report(self, text):
        """页面把实际渲染结果回报过来，便于确认窗口内部真的拿到了数据。"""
        log(f"[页面] {text}")
        return True

    def open_panel(self):
        open_console()
        return True

    def set_pinned(self, pinned):
        """固定 / 解除固定（卡片右下角的图钉）。"""
        return self._app.set_pinned(bool(pinned))

    def get_pinned(self):
        return {"pinned": self._app.is_pinned(), "version": VERSION}

    def toggle_port(self, key: str):
        name, bit = next((p[1], p[2]) for p in PORTS if p[0] == key)
        action = "off" if port_enabled(bit) else "on"
        return api_post("/api/port", {"port": key, "action": action})

    def set_mode(self, mode: int):
        return api_post("/api/set", {"piid": 5, "value": int(mode)})


def signal_manager_console() -> bool:
    """让管理器把内部控制台窗口亮出来（复用单实例的唤醒事件）。

    卡片是独立进程，自己开不了管理器的窗口，所以走命名事件通知它。
    管理器没在跑（单独跑 bat 调试时）返回 False，由调用方退回浏览器。
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.restype = ctypes.c_void_p
        kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenEventW(0x0002, False, "Local\\CUKTECH_ShowPanel")  # EVENT_MODIFY_STATE
        if not handle:
            return False
        kernel32.SetEvent(handle)
        kernel32.CloseHandle(handle)
        return True
    except Exception:  # noqa: BLE001
        return False


def open_console() -> None:
    """让管理器把内部控制台窗口亮出来。

    卡片是独立进程，开不了别人的窗口，只能通过命名事件通知管理器。
    管理器没在跑时**不再退回系统浏览器**，只写日志（按新规矩：界面只走内置控制台）。
    """
    if not signal_manager_console():
        log("管理器未运行，无法唤出控制台窗口（不退回浏览器）；请先启动 CUKTECH.exe")


def pick_page_url() -> str:
    """优先从本地服务加载（同源，肯定能拿到数据）；服务没起来时退回 file://，
    这样窗口至少能显示「控制台未响应」，而不是一片空白。

    ⚠️ v1.3.5：file:// 分支**绝对不能带 ?shell=1**。pywebview 的 winforms 后端用
    .NET 的 `Uri(url)` 构造地址，而 .NET 认为 file URI 的 path 里不该出现 `?`，
    会把它转义成 `%3F` —— 于是「widget.html?shell=1」变成了要找
    「widget.html%3Fshell=1」这个文件，必然 404，窗口显示白底「未找到文件」。
    实测：页面 URL 读出来就是 `.../widget.html%3Fshell=1`。
    页面那边改成按 `location.protocol === 'file:'` 认 shell，这里只给纯路径。
    """
    for _ in range(12):
        try:
            api_get("/api/status", timeout=1.5)
            return API + "/widget?shell=1"
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
    log("服务未就绪，退回本地文件模式")
    return (BASE / "widget.html").as_uri()


# ---------------------------------------------------------------- 应用


class DesktopApp:
    def __init__(self):
        self.window = None
        self.tray: Tray | None = None
        self.pin: DesktopPin | None = None
        self._save_timer: threading.Timer | None = None
        self._quitting = False
        self._visible = True
        self._want_pin = load_pinned()     # 上次退出时是固定状态

    # --- 窗口
    def create(self):
        import webview

        x, y = load_pos()
        self.window = webview.create_window(
            "CUKTECH",
            pick_page_url(),
            js_api=Bridge(self),
            width=WIN_W + FRAME_INSET[0],
            height=WIN_H + FRAME_INSET[1],
            x=x,
            y=y,
            resizable=False,
            frameless=True,
            easy_drag=False,      # 只让 .pywebview-drag-region 区域可拖
            on_top=False,         # 置顶与否交给 DesktopPin 按模式决定
            transparent=True,
            shadow=True,
            hidden=True,
            background_color="#111318",
            text_select=False,
        )
        self.window.events.loaded += self._on_loaded
        self.window.events.moved += self._on_moved
        self.window.events.closing += self._on_closing
        return self.window

    def _on_loaded(self):
        # 等页面渲染完再显示，避免透明窗口先闪一块白；3 秒后再兜一次
        self._fix_window_backcolor()
        # 先摘任务栏按钮再显示：这时窗口还没 show，改 exstyle 不会闪一下
        if self.pin:
            self.pin.set_toolwindow(True)
        self._show_now()
        threading.Timer(0.8, self._apply_display).start()
        threading.Timer(3.0, self._show_now).start()

    def _fix_window_backcolor(self):
        """把窗体背景色改成卡片底色，消掉圆角那圈白色描边。

        WebView2 的透明是「默认背景色 = 透明」，但半透明的抗锯齿像素最终要和
        窗体背景混合；WinForms 窗体默认背景是系统浅灰（近白），所以四个圆角
        外侧会留一圈白边。改成和卡片同色的深色后，边缘就看不出来了。
        """
        try:
            from System import Action
            from System.Drawing import ColorTranslator

            form = getattr(self.window, "native", None)
            if form is None:
                return
            color = ColorTranslator.FromHtml(CARD_BG)

            def apply():
                form.BackColor = color

            # 必须用 BeginInvoke（异步投递）而不是 Invoke：
            # loaded 回调是在 WebView2 的事件线程上跑的，此时 UI 线程还在等这次导航
            # 收尾，同步 Invoke 会两边互等、把整个窗口卡成「隐藏状态」再也 show 不出来
            # （实测：窗口 IsWindowVisible=False，evaluate_js 也一起挂住）。
            form.BeginInvoke(Action(apply))
            log(f"窗体背景色已投递为 {CARD_BG}（消除圆角白边）")
        except Exception as exc:  # noqa: BLE001
            log(f"设置窗体背景色失败（不影响使用）: {exc!r}")

    def _apply_display(self):
        if self.pin:
            self.pin.apply()
            # 上次退出时是「固定」状态：窗口显示出来后按记录的坐标重新钉住
            if self._want_pin and not self.pin.locked:
                if self.pin.lock():
                    self._want_pin = False

    def _show_now(self):
        try:
            if self.window:
                prev = _u32.GetForegroundWindow() or 0
                self.window.show()
                self._apply_display()
                # 卡片不能再带 WS_EX_NOACTIVATE（否则 WebView2 收不到鼠标事件），
                # 于是 show 会把前台抢过来。开机自启时会打断正在打字的用户，
                # 所以显示完立刻把前台还给原来那个窗口（系统可能拒绝，失败就算了）。
                h = self.pin.hwnd if self.pin else 0
                if prev and h and int(prev) != int(h):
                    _u32.SetForegroundWindow(_h(prev))
        except Exception as exc:  # noqa: BLE001
            log(f"显示窗口失败: {exc}")

    def _on_moved(self, *_args):
        if self._save_timer:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(1.0, self._persist_pos)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _persist_pos(self):
        try:
            if self.window:
                x, y = int(self.window.x), int(self.window.y)
                # 最小化时窗口坐标是 (-32000,-32000) 这种哨兵值，存下去等于把
                # 用户摆好的位置弄丢（下次启动 load_pos 只能回落到默认角落）。
                h = self.pin.hwnd if self.pin else 0
                if (h and _u32.IsIconic(_h(h))) or x <= -30000 or y <= -30000:
                    log("窗口在最小化/屏外，跳过位置保存")
                    return
                save_pos(x, y, self.pin.mode if self.pin else None)
        except Exception as exc:  # noqa: BLE001
            log(f"取窗口位置失败 {exc}")

    def _on_closing(self, *_args):
        # 点关闭（Alt+F4）改成隐藏；真正退出只走管理器/托盘的「退出」。
        # 托管模式下没有自建托盘，销毁窗口会让整个进程退出，所以也必须拦成隐藏。
        if self._quitting:
            return True
        self.hide()
        return False

    def hide(self):
        if self.window:
            self._persist_pos()
            self.window.hide()
        self._visible = False

    def show(self):
        if self.window:
            self.window.show()
        self._visible = True
        self._apply_display()

    def toggle(self):
        if self._visible:
            self.hide()
        else:
            self.show()

    def set_display(self, mode: str):
        """托盘里切换「固定在桌面 / 悬浮置顶」。"""
        if self.pin:
            self.pin.set_mode(mode)
        try:
            if self.window:
                save_pos(self.window.x, self.window.y, mode)
        except Exception as exc:  # noqa: BLE001
            log(f"保存显示模式失败 {exc}")
        if self.tray:
            self.tray.icon.update_menu()

    # --- 固定位置
    def set_pinned(self, pinned: bool) -> dict:
        """把卡片钉在当前位置（或解除）。位置会写进配置，重启仍生效。"""
        ok = True
        if pinned:
            ok = bool(self.pin and self.pin.lock())
            if not ok:
                log("固定失败：还没拿到窗口句柄")
        elif self.pin:
            self.pin.unlock()
        settled = bool(pinned and ok)
        try:
            if self.window:
                save_pos(self.window.x, self.window.y, pinned=settled)
        except Exception as exc:  # noqa: BLE001
            log(f"保存固定状态失败 {exc}")
        return {"ok": ok, "pinned": settled, "version": VERSION}

    def is_pinned(self) -> bool:
        return bool(self.pin and self.pin.locked)

    def quit(self):
        self._quitting = True
        self._persist_pos()
        if self.pin:
            self.pin.stop()
        try:
            if self.tray:
                self.tray.icon.visible = False
                self.tray.icon.stop()
        except Exception:
            pass
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass

    # --- 主流程
    def _probe(self):
        """诊断用：在窗口内执行 JS，把 DOM 的真实状态写到日志。"""
        time.sleep(8)
        expr = (
            "JSON.stringify({"
            "url: location.href.slice(-40),"
            "hasPywebview: !!window.pywebview,"
            "hasApi: !!(window.pywebview && window.pywebview.api),"
            "apiKeys: window.pywebview && window.pywebview.api ? Object.keys(window.pywebview.api) : [],"
            "total: (document.getElementById('total')||{}).textContent,"
            "sub: (document.getElementById('sub')||{}).textContent,"
            "bodyH: document.body.scrollHeight,"
            "cardH: (document.querySelector('.card')||{}).offsetHeight"
            "})"
        )
        try:
            result = self.window.evaluate_js(expr)
            log(f"[probe] {result}")
        except Exception as exc:  # noqa: BLE001
            log(f"[probe 失败] {exc!r}")

    def _run_js_hook(self):
        """CUKTECH_WIDGET_JS=<表达式>：在窗口里跑一段 JS 并把结果写日志。

        用来做端到端验证（比如点一下图钉再读回固定状态），
        避免为了验证一个按钮去装自动化框架。
        """
        expr = os.environ.get("CUKTECH_WIDGET_JS", "").strip()
        if not expr:
            return
        # 窗口从「创建」到「能跑 JS」之间还有一段（要等页面 load 完、js_api 注入），
        # 一次性调用容易撞上 WebViewException('Main window failed to start')，所以要重试。
        last = ""
        for attempt in range(1, 9):
            time.sleep(4)
            try:
                result = self.window.evaluate_js(expr)
                log(f"[js-hook] 第{attempt}次 {expr} => {result!r}")
                return
            except Exception as exc:  # noqa: BLE001
                last = repr(exc)
        log(f"[js-hook 失败] {expr} => {last}")

    def run(self):
        import webview

        self.create()
        self.pin = DesktopPin(
            want_visible=lambda: self._visible and not self._quitting
        )
        self.pin.mode = load_mode()
        # 被管理器拉起来时不要再占一个托盘图标：托盘统一归管理器，
        # 否则用户会看到两个一模一样的闪电图标，分不清哪个管什么。
        if os.environ.get("CUKTECH_NO_TRAY") == "1":
            log("由管理器托管，跳过自建托盘")
            self.tray = None
        else:
            try:
                self.tray = Tray(
                    on_toggle=self.toggle,
                    on_quit=self.quit,
                    on_panel=open_console,
                    on_display=self.set_display,
                    get_display=lambda: self.pin.mode if self.pin else "desktop",
                )
            except Exception as exc:  # noqa: BLE001  pystray/pillow 缺失也不该让卡片打不开
                log(f"托盘不可用: {exc!r}")
                self.tray = None

        def refresh_loop():
            while not self._quitting:
                try:
                    if self.tray:
                        self.tray.sync()
                except Exception:
                    pass
                time.sleep(3.0)

        if self.tray:
            self.tray.run()
        self.pin.start()
        threading.Thread(target=refresh_loop, daemon=True).start()
        if os.environ.get("CUKTECH_WIDGET_PROBE", "").strip() == "1":
            threading.Thread(target=self._probe, daemon=True).start()
        if os.environ.get("CUKTECH_WIDGET_JS", "").strip():
            threading.Thread(target=self._run_js_hook, daemon=True).start()
        log(f"桌面组件启动 v{VERSION}")
        from desktop.wv2env import apply_sandbox_workaround, storage_path, tune_gc

        apply_sandbox_workaround()      # 卡片和控制台同一个坑，见 desktop/wv2env.py
        tune_gc()                       # 初始化已完成，把启动期对象冻出 GC 扫描范围
        # 不设 REMOTE_DEBUGGING_PORT：v1.3.7 起两边各有独立的浏览器进程组，
        # 再让卡片也绑同一个调试端口会端口冲突。CDP 只需连控制台那一个。
        webview.start(
            gui="edgechromium",
            private_mode=False,
            # 独立目录！共用会让第二个宿主控制器创建失败（0x8007139F → 纯黑窗口），
            # 详见 desktop/wv2env.py 第 2 节。
            storage_path=storage_path("widget"),
        )
        # webview 退出后收尾
        try:
            if self.tray:
                self.tray.icon.stop()
        except Exception:
            pass
        log("桌面组件退出")


def main() -> None:
    # 单实例：已有卡片在跑就不要再开一个
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    global _MUTEX_HANDLE
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "CUKTECH_DesktopWidget_Mutex")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        log("已有实例在运行，退出")
        return
    DesktopApp().run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log(f"启动失败: {exc!r}")
        raise
