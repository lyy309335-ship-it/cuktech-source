# -*- coding: utf-8 -*-
"""通过 WebView2 远程调试端口跑一段 JS / 抓一张图。

应用带 CUKTECH_DEBUG_PORT=9333 启动后，控制台窗口就是一个可调试的页面，
这样验证「浮层有没有渲染出某个元素」不需要人眼看，也不需要装自动化框架。

用法：
    python cdp_eval.py 9333 "document.title"
    python cdp_eval.py 9333 --shot out.png "window.__cktOverlay.open(true)"
"""
import asyncio
import base64
import json
import sys
import urllib.request


def targets(port):
    # 显式禁用代理：本机沙箱环境里 http_proxy 指向 127.0.0.1:1887，
    # 会把 http://127.0.0.1:9333 也当外网请求转出去，直接 502。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/json", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def run(port, expression, shot):
    import aiohttp

    pages = [t for t in targets(port) if t.get("type") == "page"]
    if not pages:
        print("没有可调试页面")
        return 1
    page = pages[0]
    print(f"目标页面: {page.get('title')!r} {page.get('url','')[:60]}")
    async with aiohttp.ClientSession(trust_env=False) as session:
        async with session.ws_connect(page["webSocketDebuggerUrl"], timeout=20) as ws:
            n = 0

            async def call(method, params=None):
                nonlocal n
                n += 1
                await ws.send_json({"id": n, "method": method, "params": params or {}})
                while True:
                    msg = await ws.receive_json()
                    if msg.get("id") == n:
                        return msg

            if expression:
                r = await call("Runtime.evaluate", {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                })
                res = r.get("result", {}).get("result", {})
                print("结果:", json.dumps(res.get("value", res), ensure_ascii=False))

            if shot:
                await asyncio.sleep(0.6)      # 让刚打开的浮层完成过渡动画
                r = await call("Page.captureScreenshot", {"format": "png"})
                data = r.get("result", {}).get("data", "")
                if not data:
                    print("截图失败:", json.dumps(r, ensure_ascii=False)[:200])
                    return 1
                with open(shot, "wb") as f:
                    f.write(base64.b64decode(data))
                print("截图已保存", shot)
    return 0


if __name__ == "__main__":
    port = int(sys.argv[1])
    rest = sys.argv[2:]
    shot = None
    if "--shot" in rest:
        i = rest.index("--shot")
        shot = rest[i + 1]
        rest = rest[:i] + rest[i + 2:]
    expr = rest[0] if rest else ""
    raise SystemExit(asyncio.run(run(port, expr, shot)))
