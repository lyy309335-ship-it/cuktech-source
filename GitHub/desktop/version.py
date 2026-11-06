"""版本号的唯一来源。

以前 manager.py 和 widget_app.py 各写一份 VERSION，改版本时容易漏掉一处，
运行管理面板显示的版本和日志里写的不一致就很难排查。全部从这里取。
"""

from __future__ import annotations

VERSION = "1.8.2"

# 运行管理面板、日志、安装包文件名都用这一份
__all__ = ["VERSION"]
