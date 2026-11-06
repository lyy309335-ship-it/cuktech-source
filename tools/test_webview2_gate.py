"""WebView2 预检闸门的回归测试。

覆盖三条路径：
    1. 已装            → ensure() 返回 True，不弹任何框
    2. 缺失 + 后台角色 → 返回 False，绝不弹框（否则开机自启会卡住）
    3. 缺失 + 交互角色 → 返回 False，且确实弹了一次、选「是」时打开了下载页

用法：.venv\\Scripts\\python.exe tools\\test_webview2_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "GitHub"))

from desktop import webview2_check  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    real_runtime = webview2_check.runtime_version
    real_ask = webview2_check._ask_install
    real_open = webview2_check._open_download
    real_log = webview2_check._log

    logs: list[str] = []
    webview2_check._log = lambda m: logs.append(m)

    # --- 1. 真实环境：本机装了运行时，应直接放行 ---
    ver = real_runtime()
    check("本机检测到运行时", bool(ver), ver or "未检测到")
    logs.clear()
    check("已装 → 放行", webview2_check.ensure(interactive=True, role="manager") is True)

    # --- 2. 模拟缺失 + 非交互（--silent / 开机自启）---
    webview2_check.runtime_version = lambda: ""
    asked = {"n": 0}
    webview2_check._ask_install = lambda: asked.__setitem__("n", asked["n"] + 1) or True
    opened = {"n": 0}
    webview2_check._open_download = lambda: opened.__setitem__("n", opened["n"] + 1)

    logs.clear()
    got = webview2_check.ensure(interactive=False, role="manager")
    check("缺失+静默 → 不放行", got is False)
    check("缺失+静默 → 不弹框", asked["n"] == 0, f"弹了 {asked['n']} 次")
    check("缺失+静默 → 不打开浏览器", opened["n"] == 0)
    check("缺失+静默 → 有日志留痕", any("未检测到" in m for m in logs), str(logs[:1]))

    # --- 3. 模拟缺失 + 交互，用户点「是」---
    asked["n"] = 0
    opened["n"] = 0
    webview2_check._ask_install = lambda: asked.__setitem__("n", asked["n"] + 1) or True
    got = webview2_check.ensure(interactive=True, role="collect")
    check("缺失+交互 → 不放行", got is False)
    check("缺失+交互 → 弹框一次", asked["n"] == 1, f"弹了 {asked['n']} 次")
    check("缺失+交互 → 点「是」后打开下载页", opened["n"] == 1)

    # --- 4. 模拟缺失 + 交互，用户点「否」---
    asked["n"] = 0
    opened["n"] = 0
    webview2_check._ask_install = lambda: asked.__setitem__("n", asked["n"] + 1) or False
    got = webview2_check.ensure(interactive=True, role="widget")
    check("缺失+点「否」 → 不放行且不跳转", got is False and opened["n"] == 0)

    # 还原，避免污染后续
    webview2_check.runtime_version = real_runtime
    webview2_check._ask_install = real_ask
    webview2_check._open_download = real_open
    webview2_check._log = real_log

    # --- 5. 下载地址必须是微软官方域名 ---
    check(
        "下载地址是微软官方",
        webview2_check.DOWNLOAD_URL.startswith("https://go.microsoft.com/"),
        webview2_check.DOWNLOAD_URL,
    )

    print()
    if FAILS:
        print(f"RESULT: FAIL 共 {len(FAILS)} 项：{', '.join(FAILS)}")
        return 1
    print("RESULT: PASS 预检闸门三条路径全部正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
