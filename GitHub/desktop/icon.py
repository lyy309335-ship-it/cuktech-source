"""托盘图标绘制：数字是主体，闪电缩成右上角的小角标。

两条硬约束决定了这里的画法：

1. 不能「在 64px 画布上画好、让 Windows 去缩」。
   pystray 走 LoadImage(0, 0)，即按 SM_CXICON（125% DPI 下 40px）取图标，
   通知区域再把它缩到 SM_CXSMICON（20px）。两次非整数缩放 + 抗锯齿，数字会糊。
   所以每个尺寸都按自己的像素网格直接画：3x7 点阵、整数像素、无抗锯齿；
   ICO 里再放 32/40/48 = 16/20/24 的 2 倍最近邻放大，系统取 2 倍图再 2:1 缩回，
   正好落回原像素格，边缘不出灰边。

2. 托盘里只有 16~24px，数字和闪电抢地方。
   现在的分工是：数字吃满可用高度（贴底、水平居中），闪电退到右上角当角标，
   尺寸约 icon 的 27%。没有数字可写时（未连上 / 空载），闪电才居中放大——
   那种状态下它就是唯一的信息，不需要给谁让位。
"""

from __future__ import annotations

import time

# ---------------------------------------------------------------- 配色

# 每档两个色：（任务栏背景暗时用亮色，背景亮时用深色）
# 只按注册表 SystemUsesLightTheme 判断是不够的：系统设成浅色 + 深色壁纸/透明
# 任务栏时，任务栏实际是深色的，深青色数字压上去几乎看不见。所以真的去采样。
_IDLE = ((110, 231, 214), (13, 148, 136))
_LOW = ((94, 234, 212), (13, 148, 136))
_MID = ((250, 204, 21), (161, 98, 7))
_HIGH = ((251, 146, 60), (194, 65, 12))
_OFF = ((140, 146, 158), (100, 116, 139))

# 点阵图标只需要 16/20/24 三档原生绘制，另外三档由它们放大而来
_NATIVE = (16, 20, 24)
_SCALED = {32: 16, 40: 20, 48: 24}

# ---------------------------------------------------------------- 3x7 点阵数字

_GLYPHS = {
    "0": ("###", "# #", "# #", "# #", "# #", "# #", "###"),
    "1": (" # ", "## ", " # ", " # ", " # ", " # ", "###"),
    "2": ("###", "  #", "  #", "###", "#  ", "#  ", "###"),
    "3": ("###", "  #", "  #", "###", "  #", "  #", "###"),
    "4": ("# #", "# #", "# #", "###", "  #", "  #", "  #"),
    "5": ("###", "#  ", "#  ", "###", "  #", "  #", "###"),
    "6": ("###", "#  ", "#  ", "###", "# #", "# #", "###"),
    "7": ("###", "  #", "  #", "  #", "  #", "  #", "  #"),
    "8": ("###", "# #", "# #", "###", "# #", "# #", "###"),
    "9": ("###", "# #", "# #", "###", "  #", "  #", "###"),
}
_GW, _GH = 3, 7

# 闪电轮廓（取自旧版 64px 画布），用于「没有数字可显示」时居中放大
_BOLT = ((37, 4), (16, 37), (29, 37), (25, 60), (48, 25), (33, 25))

# 右上角角标：5x5 手工点阵。这个尺寸下把 64px 的曲线闪电缩下来只剩一个方块，
# 必须直接按像素画，才认得出是闪电。
_BOLT5 = (
    "...##",
    "..##.",
    "#####",
    "...##",
    "..##.",
)
_BOLT5_W = max(len(r) for r in _BOLT5)
_BOLT5_H = len(_BOLT5)

_ts = 0.0
_val = False
_TTL = 30.0


def _taskbar_luma() -> float | None:
    """采样任务栏实际背景亮度（0-255）。透明任务栏叠在壁纸上，注册表看不出真实明暗。"""
    try:
        import ctypes
        import statistics
        from ctypes import wintypes

        from PIL import ImageGrab

        u = ctypes.windll.user32
        hwnd = u.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return None
        rc = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rc))
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w <= 0 or h <= 0:
            return None
        # 只取中间那条横带：避开任务栏上下的高光/阴影，也避开托盘图标的上下留白
        hh = max(1, h // 3)
        top = rc.top + (h - hh) // 2
        img = ImageGrab.grab(bbox=(rc.left, top, rc.right, top + hh)).convert("RGB")
        px = list(img.getdata())[::9]
        if not px:
            return None
        # 图标只占少数像素，中位数≈背景
        lum = sorted(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px)
        return lum[len(lum) // 2]
    except Exception:  # noqa: BLE001  采不到就不猜，交给注册表
        return None


def light_taskbar() -> bool:
    """任务栏背景是不是偏亮（决定用深一档的配色）。30 秒缓存。"""
    global _ts, _val
    now = time.monotonic()
    if now - _ts < _TTL:
        return _val
    _ts = now

    luma = _taskbar_luma()
    if luma is not None:
        _val = luma > 140.0        # 浅灰任务栏 ~240，深色任务栏 ~35
        return _val

    try:                            # 采不到像素时才退回注册表
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "SystemUsesLightTheme")
        _val = bool(int(val))
    except Exception:  # noqa: BLE001
        _val = False
    return _val


def _color(power: float, connected: bool, light: bool) -> tuple[int, int, int, int]:
    if not connected:
        pair = _OFF
    elif power < 1:
        pair = _IDLE
    elif power < 60:
        pair = _LOW
    elif power < 110:
        pair = _MID
    else:
        pair = _HIGH
    r, g, b = pair[1] if light else pair[0]
    return (r, g, b, 255)


def label_for(power: float, connected: bool) -> str:
    """图标上要写的字：没连上 / 空载就不写（只留闪电），否则写整数瓦数。

    小数位放不进 16px，取整；上限 999（三个口同时满载也到不了，纯属防御）。
    """
    if not connected:
        return ""
    try:
        w = int(round(float(power)))
    except (TypeError, ValueError):
        return ""
    if w < 1:
        return ""
    return str(min(w, 999))


# ---------------------------------------------------------------- 布局


def _gap(scale: int) -> int:
    """字间距。放大后留 1px 就够，太宽会把 3 位数挤出图标。"""
    return max(1, scale // 2)


def _metrics(size: int, n: int) -> tuple[int, int, int, int]:
    """在 size×size 里给 n 个数字找最大的整数倍放大，返回 (scale, gap, w, h)。

    优先保证「高」——数字高度就是可读性本身；宽度实在放不下才降一档。
    """
    for s in (4, 3, 2, 1):
        gap = _gap(s)
        w = n * _GW * s + (n - 1) * gap
        h = _GH * s
        if w <= size and h <= size:
            return s, gap, w, h
    return 1, 1, n * _GW + n - 1, _GH


# ---------------------------------------------------------------- 绘制


def _draw_digits(img, text: str, color, s: int, gap: int, x0: int, y0: int) -> None:
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    for i, ch in enumerate(text):
        rows = _GLYPHS.get(ch)
        if not rows:
            continue
        gx = x0 + i * (_GW * s + gap)
        for ry, row in enumerate(rows):
            for rx, cell in enumerate(row):
                if cell == " ":
                    continue
                x = gx + rx * s
                y = y0 + ry * s
                d.rectangle([x, y, x + s - 1, y + s - 1], fill=color)


def _stamp_bolt(img, x: int, y: int, size: int, color) -> None:
    """整块（无数字时）的大闪电：曲线图形，超采样后降采样换平滑边缘。"""
    from PIL import Image, ImageDraw

    ss = 8
    big = Image.new("L", (64 * ss, 64 * ss), 0)
    ImageDraw.Draw(big).polygon([(px * ss, py * ss) for px, py in _BOLT], fill=255)
    mask = big.resize((size, size), Image.LANCZOS)
    img.paste(Image.new("RGBA", (size, size), color), (x, y), mask)


def _draw_badge(img, x: int, y: int, color) -> None:
    """右上角的小闪电角标：纯点阵，硬像素，不做抗锯齿。"""
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    for ry, row in enumerate(_BOLT5):
        for rx, cell in enumerate(row):
            if cell == "#":
                d.point((x + rx, y + ry), fill=color)


def _render(size: int, power: float, connected: bool, light: bool):
    from PIL import Image

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    color = _color(power, connected, light)
    text = label_for(power, connected)

    if not text:                       # 未连接 / 空载：闪电居中放大，它就是主体
        _stamp_bolt(img, 0, 0, size, color)
        return img

    s, gap, w, h = _metrics(size, len(text))
    x0 = (size - w) // 2               # 水平居中
    y0 = (size - h) // 2               # 垂直居中
    # 上方还剩得下 5 行，才把数字稍微下移、让出右上角给角标；
    # 剩不下（16px 的两位数）就让数字独大，宁可没有角标。
    badge = size - h >= _BOLT5_H
    if badge:
        y0 = max(y0, _BOLT5_H)
    _draw_digits(img, text, color, s, gap, x0, y0)

    if badge:
        _draw_badge(img, size - _BOLT5_W, 0, color)
    return img


class TrayIcon:
    """给 pystray 用的图标对象。

    pystray 只对图标做一件事：``image.save(fp, format="ICO")``。直接给它
    PIL 图像的话，ICO 插件会把大图 LANCZOS 缩成各个尺寸（数字就糊了）。
    这里自己接管 save，把我们预渲染好的每一档原样写进去。
    """

    __slots__ = ("_frames", "_base")

    def __init__(self, frames: dict):
        self._frames = frames
        self._base = frames[max(frames)]

    @property
    def size(self):
        return self._base.size

    def save(self, fp, format=None, **kwargs):  # pystray 的调用点，签名要对得上
        sizes = sorted(self._frames)
        base = self._frames[max(sizes)]
        extra = [self._frames[s] for s in sizes if s != base.width]
        # bitmap_format="bmp" 很关键：默认会把每帧存成 PNG，而 Windows 的
        # LoadImage/DrawIconEx 对「非 256px 的 PNG 帧」支持有缺陷 —— 出来的
        # HICON 是错位/裁切的（数字看着像横条）。小尺寸必须用 BMP。
        base.save(
            fp,
            format="ICO",
            sizes=[(s, s) for s in sizes],
            append_images=extra,
            bitmap_format="bmp",
        )

    def __bool__(self):  # pystray 用真值判断「有没有图标」
        return True


_cache: dict = {}


def tray_icon(power: float, connected: bool, light: bool | None = None) -> TrayIcon:
    """按当前瓦数生成多尺寸托盘图标（同一瓦数只画一次）。"""
    if light is None:
        light = light_taskbar()
    key = (label_for(power, connected), bool(connected), bool(light))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    frames = {s: _render(s, power, connected, light) for s in _NATIVE}
    for big, small in _SCALED.items():
        frames[big] = frames[small].resize((big, big), 0)  # 0 = NEAREST，硬像素放大
    icon = TrayIcon(frames)
    if len(_cache) > 32:
        _cache.clear()
    _cache[key] = icon
    return icon


def bolt_icon(power: float, connected: bool, size: int = 64):
    """旧接口：只画闪电、不写数字。保留给兜底和窗口里的大图。"""
    from PIL import Image

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    _stamp_bolt(img, 0, 0, size, _color(power, connected, light_taskbar()))
    return img


__all__ = ["tray_icon", "bolt_icon", "label_for", "light_taskbar", "TrayIcon",
           "tray_pixels", "patch_pystray_icon_loader"]


def tray_pixels() -> int:
    """通知区域里图标的真实像素边长（物理像素）。

    本进程没有 dpiAware manifest，GetSystemMetrics(SM_CXSMICON) 报的是逻辑值
    （125% 缩放时报 16），实际要画 20px。用任务栏窗口的 DPI 换算回物理像素。
    """
    try:
        import ctypes

        u = ctypes.windll.user32
        dpi = 96
        try:
            hwnd = u.FindWindowW("Shell_TrayWnd", None)
            if hwnd and hasattr(u, "GetDpiForWindow"):
                dpi = int(u.GetDpiForWindow(hwnd)) or 96
        except Exception:  # noqa: BLE001
            dpi = 96
        return max(12, int(round(u.GetSystemMetrics(49) * dpi / 96.0)))
    except Exception:  # noqa: BLE001
        return 16


def patch_pystray_icon_loader() -> bool:
    """让 pystray 按「通知区域真实像素」加载 HICON，而不是 SM_CXICON。

    pystray 里是 ``LoadImage(..., 0, 0, LR_DEFAULTSIZE | LR_LOADFROMFILE)``，
    拿的是 SM_CXICON（本进程逻辑 32px）那一帧，再由 Shell 缩到 20px 物理 ——
    0.625 倍的非整数缩放会把点阵数字糊掉，数字也显小。
    换成 20px 请求后是 1:1 贴着显示，像素完全对齐。

    Returns:
        True 表示已接管；False 表示 pystray 结构不符，保持原样（不影响功能）。
    """
    try:
        from pystray import _win32 as pst
        from pystray._util import serialized_image
        from pystray._util import win32 as w32
    except Exception:  # noqa: BLE001
        return False

    original = getattr(pst.Icon, "_assert_icon_handle", None)
    if original is None:
        return False

    def _assert_icon_handle(self):  # noqa: ANN001
        if getattr(self, "_icon_handle", None):
            return
        px = tray_pixels()
        try:
            with serialized_image(self.icon, "ICO") as path:
                self._icon_handle = w32.LoadImage(
                    None, path, w32.IMAGE_ICON, px, px, w32.LR_LOADFROMFILE
                )
        except Exception:  # noqa: BLE001  拿不到就退回 pystray 的原逻辑
            self._icon_handle = None
            return original(self)

    _assert_icon_handle._cuktech_patched = True  # type: ignore[attr-defined]
    pst.Icon._assert_icon_handle = _assert_icon_handle
    return True
