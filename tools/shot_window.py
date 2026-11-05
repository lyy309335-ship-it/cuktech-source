"""用 PrintWindow 抓指定标题窗口的位图，用于确认窗口内确实画出了内容。

不依赖窗口是否在最前（PW_RENDERFULLCONTENT 让 DWM 把合成结果拷过来），
也不需要人眼看 —— 输出像素统计：颜色数太少 / 单一主色占比过高 = 白板或黑屏。
"""

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def grab(title: str, out: str) -> dict:
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return {"ok": False, "err": f"找不到窗口 {title!r}"}
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return {"ok": False, "err": f"窗口尺寸异常 {w}x{h}"}

    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 2)      # PW_RENDERFULLCONTENT

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h                 # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    # 统计颜色分布（每 4 字节一跳，忽略 alpha）
    from collections import Counter
    px = buf.raw
    cnt = Counter()
    for i in range(0, len(px), 4 * 7):         # 抽样，够判断了
        cnt[px[i:i + 3]] += 1
    total = sum(cnt.values())
    top = cnt.most_common(1)[0]

    from PIL import Image
    img = Image.frombytes("RGBA", (w, h), px).convert("RGB")
    img.save(out)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return {
        "ok": bool(ok), "w": w, "h": h, "colors": len(cnt),
        "top_share": round(top[1] / total * 100, 1), "saved": out,
    }


if __name__ == "__main__":
    print(grab(sys.argv[1], sys.argv[2]))
