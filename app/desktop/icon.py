"""托盘 / 窗口共用的图标绘制。颜色随总功率变化，一眼看出负载。"""

from __future__ import annotations


def bolt_icon(power: float, connected: bool, size: int = 64):
    """闪电图标：灰=未连接，青=空载，黄=中功率，橙=大功率。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
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

    scale = size / 64
    pts = [(37, 4), (16, 37), (29, 37), (25, 60), (48, 25), (33, 25)]
    d.polygon([(x * scale, y * scale) for x, y in pts], fill=color)
    return img
