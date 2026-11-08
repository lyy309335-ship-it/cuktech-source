"""CUKTECH BLE Controller 启动器。

上游项目只读 os.environ，不会自动加载 .env，且没有 python-dotenv 依赖。
这个薄封装负责：读 .env -> 注入环境变量 -> 转交上游 CLI / Web 服务。

用法（等价于上游 python -m cuktech_ble_controller ...）：
    python cuktech.py scan
    python cuktech.py info
    python cuktech.py auth
    python cuktech.py status
    python cuktech.py web

真实环境变量优先于 .env，方便临时覆盖排障。
"""

from __future__ import annotations

import io
import os
import socket
import sys
import threading
import time
from pathlib import Path

from desktop import credentials as _cred
from desktop import paths as _paths
from desktop.version import VERSION as APP_VERSION

# 打包后 BASE 是解包目录（只读），日志因此必须写到 paths.log_dir()
BASE = _paths.resource_dir()
SRC = BASE / "src"
LOG_PATH = _paths.log_dir() / "console.log"
_LOG_MAX_BYTES = 5 * 1024 * 1024
# 必须强引用：上游 ble.py 会再包一层 TextIOWrapper 到同一个底层缓冲区上，
# 我们这层一旦被 GC 回收，close 会把文件句柄一起关掉，后续写入直接 ValueError。
_LOG_STREAM: io.TextIOWrapper | None = None


class _RedactingStream(io.TextIOWrapper):
    """写日志前把 MAC / TOKEN / BLE KEY 打码。

    上游 ble.py 在认证失败时会把整帧十六进制打出来，那些内容里就含 token；
    日志是要发给别人排障的，不能带着设备凭据散出去。
    """

    def write(self, text: str) -> int:  # type: ignore[override]
        try:
            text = _cred.redact(text)
        except Exception:  # noqa: BLE001
            pass
        return super().write(text)


def _ensure_streams(force_log: bool) -> None:
    """pythonw 下 stdout 是个指向无效句柄的流，上游 ble.py 会直接对 sys.stdout.buffer 取值，
    所以无控制台或显式要求写日志时，先把 stdout/stderr 换成真实文件流：
    落 logs/console.log（超过 5MB 自动清空，避免无限膨胀）。
    """
    global _LOG_STREAM
    if sys.stdout is not None and sys.stderr is not None and not force_log:
        return

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
        LOG_PATH.unlink(missing_ok=True)

    handle = open(LOG_PATH, "ab", buffering=0)
    _LOG_STREAM = _RedactingStream(
        handle, encoding="utf-8", errors="replace", line_buffering=True
    )
    _LOG_STREAM.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n")
    sys.stdout = _LOG_STREAM
    sys.stderr = _LOG_STREAM


def _port_already_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", port)) == 0


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


async def _auto_connect_loop(mgr, attempts: int = 6, interval: float = 5.0) -> None:
    """启动即连设备，并在断线后自动重连。

    上游 Web 端要手动点「连接」，不适合常驻使用。这里补两层：
    1. 启动时按指数退避重试若干次（固件断开后需要时间恢复广播）；
    2. 连上之后做看门狗，状态掉回 DISCONNECTED 就重连。
    如果用户自己在网页上点了「断开」，看门狗不再抢连接。
    """
    import asyncio

    from cuktech_ble_controller.ble import diagnose_target

    async def _diagnose_if_not_found(attempt: int, error: str) -> None:
        """`Device ... was not found` 时补一次扫描取证，把结论写进日志。

        这条错误码是 WinRT 在「设备当前不可达」时抛的，但它不区分
        「没开 / 被占用 / 地址变了」，直接看日志根本判断不了。
        """
        if "was not found" not in (error or "").lower():
            return
        if attempt not in (1, 3, attempts):
            return
        try:
            await diagnose_target()
        except Exception as exc:  # noqa: BLE001
            print(f"[diag] 诊断扫描失败 {exc!r}")

    for attempt in range(1, attempts + 1):
        if mgr.state == "READY":
            break
        try:
            result = await mgr.connect_and_auth()
        except Exception as exc:  # noqa: BLE001 - 任何异常都只影响本次尝试
            result = {"ok": False, "error": str(exc)}
        if result.get("ok"):
            print(f"[auto] 已连接并认证，第 {attempt} 次尝试")
            break
        error = result.get("error") or ""
        print(f"[auto] 连接失败({attempt}/{attempts}): {error}")
        await _diagnose_if_not_found(attempt, error)
        if attempt < attempts:
            await asyncio.sleep(interval)

    # 看门狗：上面 6 次重试用完还没连上时，仍然每 15 秒试一次，
    # 但不能一声不响 —— 用户会以为程序卡死了。
    tick = 0
    while True:
        await asyncio.sleep(15)
        if getattr(mgr, "_manual_off", False):
            continue
        if mgr.state == "DISCONNECTED":
            tick += 1
            try:
                result = await mgr.connect_and_auth()
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": str(exc)}
            if result.get("ok"):
                print("[auto] 断线后已自动重连")
                tick = 0
                continue
            error = result.get("error") or ""
            if tick % 4 == 1:                     # 每 1 分钟记一次，别刷屏
                print(f"[auto] 仍在重连（第 {tick} 次）: {error}")
            if tick % 8 == 1:                     # 每 2 分钟做一次取证扫描
                await _diagnose_if_not_found(1, error)


def _run_web() -> None:
    # ⚠️ 必须在 import 上游 server 之前设：server.py 是在模块层读这个环境变量的
    # （WEB_INDEX = Path(os.environ.get("CUKTECH_WEB_INDEX", ...))）。
    #
    # 上游用 `Path(__file__).parents[2] / "web" / "index.html"` 反推首页路径。
    # 打包后 __file__ 是 _internal/cuktech_ble_controller/server.pyc，反推出的
    # 是安装根目录 Programs\CUKTECH\web\index.html —— 而资源实际在 _internal\web\，
    # 于是 GET / 抛 FileNotFoundError，浏览器/内置窗口看到的都是错误页。
    index_html = BASE / "web" / "index.html"
    if index_html.is_file():
        os.environ["CUKTECH_WEB_INDEX"] = str(index_html)
        print(f"[web] 首页 -> {index_html}", flush=True)

    import asyncio

    from aiohttp import web
    from cuktech_ble_controller.server import HOST, PORT, create_app

    target = "127.0.0.1" if HOST in ("0.0.0.0", "") else HOST
    if _port_already_listening(HOST, PORT):
        print(f"[!] {PORT} 端口已被占用，可能已有一个控制台在跑。")
        print(f"[i] 服务地址 http://{target}:{PORT}（由内置控制台窗口访问）")
        return

    print("=" * 50)
    print(f"  CUKTECH BLE Controller  v{APP_VERSION}")
    print(f"  服务地址 http://{target}:{PORT}")
    print("  不自动打开浏览器：请用托盘/桌面卡片的「控制台」窗口查看界面")
    print("  终止本进程即停止采集服务")
    print("=" * 50)

    app = create_app()

    # 控制台首页要额外挂一小段「运行管理浮层」脚本。
    #
    # 为什么由服务端注入、而不是主程序用 evaluate_js 注入：pywebview 的
    # evaluate_js 走 CoreWebView2.ExecuteScriptAsync，在这台机器上对真实控制页面
    # 会「调用不返回」（拿不到 JS 结果，也看不到任何报错），靠它注入等于把
    # 整个浮层的可用性押在一个不可靠的通道上。改成页面自己 <script> 加载之后，
    # 只依赖浏览器解析 HTML 这一件事，稳定得多。
    #
    # 浮层脚本自己会判断「有没有 window.pywebview」：普通浏览器里没有，它就
    # 自动收工不留按钮，所以浏览器端看到的还是原来的干净页面。
    console_page = None
    overlay_js = BASE / "console_overlay.js"
    if index_html.is_file():
        try:
            html = index_html.read_text(encoding="utf-8")
            # 带 mtime 查询串：WebView2 有持久化缓存，脚本改了不加版本号还会拿旧的
            try:
                stamp = int(overlay_js.stat().st_mtime)
            except OSError:
                stamp = 0
            tag = f'<script src="/console.js?v={stamp}" defer></script>'
            if "</body>" in html:
                html = html.replace("</body>", f"{tag}\n</body>", 1)
            else:
                html += tag
            console_page = html
            print(f"[web] 已为首页注入运行管理浮层脚本 (v={stamp})", flush=True)
        except OSError as exc:
            print(f"[!] 首页读取失败，浮层不可用: {exc}", flush=True)

    # 桌面组件从 file:// 加载，需要 CORS 才能调 /api/*
    @web.middleware
    async def _cors(request, handler):
        # WebSocket 不吃 CORS 头，且响应已 prepare，改头有风险：记一笔就直接放行。
        # 这行日志也是「内置控制台确实连上了实时通道」的唯一外部证据。
        if request.path == "/ws":
            print(f"[web] WebSocket 已连接 <- {request.remote}", flush=True)
            return await handler(request)
        # /api/status 每秒都被托盘和界面轮询，记它会淹掉日志，其余都记
        # （带查询串，控制台浮层的探针 /__overlay?p=... 就靠这个把页面状态报回来）
        if request.path != "/api/status":
            print(f"[http] {request.method} {request.path_qs}", flush=True)
        if request.method == "OPTIONS":
            response = web.Response(status=204)
        elif request.method == "GET" and console_page and request.path in ("/", "/index.html"):
            response = web.Response(
                text=console_page, content_type="text/html", charset="utf-8"
            )
            response.headers["Cache-Control"] = "no-store"
        else:
            response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
        return response

    app.middlewares.append(_cors)

    if overlay_js.is_file():

        async def _handle_console_js(_request):
            return web.FileResponse(
                overlay_js, headers={"Cache-Control": "no-store"}
            )

        app.router.add_get("/console.js", _handle_console_js)

    # /widget：给桌面组件与手机用的紧凑版界面，同源所以不受 CORS 限制
    widget_html = BASE / "widget.html"
    if widget_html.is_file():

        async def _handle_widget(_request):
            print("[web] GET /widget", flush=True)
            return web.FileResponse(widget_html)

        app.router.add_get("/widget", _handle_widget)

    # /vendor/*：前端第三方静态资源（Chart.js 等）。上游 server.py 只服务 index.html，
    # 没有静态目录，离线时 CDN 拉不到就没法画历史曲线，这里补一个只读白名单路由。
    vendor_dir = BASE / "web" / "vendor"
    if vendor_dir.is_dir():

        async def _handle_vendor(request):
            name = request.match_info["name"]
            target = (vendor_dir / name).resolve()
            # 防目录穿越：解析后必须仍落在 vendor 目录内
            if vendor_dir.resolve() not in target.parents or not target.is_file():
                raise web.HTTPNotFound()
            return web.FileResponse(target)

        app.router.add_get("/vendor/{name}", _handle_vendor)

    # /api/autoport：设备设置里「自动启动」的配置（开关 / 时间窗 / 端口范围）。
    #
    # v1.6.0 起这个功能从「运行管理」浮层搬到了控制台网页的「设备设置 > 场景设置」，
    # 所以得有一条页面能直接调的通路。为什么放在这里而不是 manager 里：页面本身是
    # 这个服务端进程提供的（浏览器直开 127.0.0.1:8080 也是它），如果只走
    # pywebview 的 js_api，那就只有桌面壳子里能用，网页版直接就废了。
    #
    # 端点只读写 autoport.json，不在这里执行开端口——真正的执行仍然由管理器
    # 在启动时做，避免两个进程同时抢 BLE。
    try:
        from desktop import autoport as _autoport
    except Exception as _exc:  # noqa: BLE001
        _autoport = None
        print(f"[!] 自动启动设置不可用（autoport 导入失败）: {_exc}", flush=True)

    if _autoport is not None:

        async def _handle_autoport_get(_request):
            return web.json_response(_autoport.public_state())

        async def _handle_autoport_set(request):
            try:
                payload = await request.json()
            except Exception:  # noqa: BLE001
                return web.json_response(
                    {**_autoport.public_state(), "ok": False, "error": "请求格式不对"},
                    status=400,
                )
            return web.json_response(_autoport.save_settings(payload))

        app.router.add_get("/api/autoport", _handle_autoport_get)
        app.router.add_post("/api/autoport", _handle_autoport_set)

    if _flag("CUKTECH_AUTO_CONNECT"):
        mgr = app["ble"]
        original_disconnect = mgr.do_disconnect

        async def _marked_disconnect(*args, **kwargs):
            mgr._manual_off = True  # 用户主动断开，看门狗别抢回来
            return await original_disconnect(*args, **kwargs)

        mgr.do_disconnect = _marked_disconnect

        async def _start_auto(app_):
            app_["_auto_task"] = asyncio.create_task(_auto_connect_loop(app_["ble"]))

        async def _stop_auto(app_):
            task = app_.get("_auto_task")
            if task:
                task.cancel()

        app.on_startup.append(_start_auto)
        # 让取消自动重连发生在上游断开连接之前
        app.on_cleanup.append(_stop_auto)

    web.run_app(app, host=HOST, port=PORT)


def parse_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE、忽略空行与 # 注释、去掉成对引号。"""
    data: dict[str, str] = {}
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            data[key] = value
    return data


def load_env() -> Path | None:
    """读配置并注入 os.environ，返回实际生效的那个文件（没有则 None）。

    查找顺序由 desktop.credentials 统一维护：
        %LOCALAPPDATA%\\CUKTECH\\.env  →  便携目录  →  包内/源码目录的 .env

    之所以不再只看 BASE/".env"：打包后 BASE 是 _internal\\，那份文件藏在安装目录里、
    而且重装会被 installer 清掉，用户往那儿填等于白填。
    """
    from desktop import credentials

    data, used = credentials.read_env()
    for key, value in data.items():
        os.environ.setdefault(key, value)

    # 相对路径的数据目录按 app 目录解析，避免受当前工作目录影响；
    # 打包运行时不写安装目录，统一落到 %LOCALAPPDATA%\CUKTECH\data
    data_dir = os.environ.get("CUKTECH_DATA_DIR", "").strip()
    if data_dir and not os.path.isabs(data_dir):
        if _paths.is_frozen():
            os.environ["CUKTECH_DATA_DIR"] = str(_paths.data_dir())
        else:
            os.environ["CUKTECH_DATA_DIR"] = str((BASE / data_dir).resolve())
    return used


def main() -> None:
    env_file = load_env()
    if str(SRC) not in sys.path and SRC.is_dir():
        sys.path.insert(0, str(SRC))

    # 本封装自己的开关，转发给上游前先摘掉
    #
    # 注意：以前这里支持 --open / CUKTECH_OPEN_BROWSER=1 自动打开系统浏览器，
    # 现在已移除 —— 界面统一走应用自己的控制台窗口，服务只负责输出运行日志。
    want_log = False
    argv = []
    for arg in sys.argv:
        if arg == "--log":
            want_log = True
        else:
            argv.append(arg)
    sys.argv = argv

    _ensure_streams(want_log)

    if env_file is None:
        print(
            "[warn] 未找到设备凭据配置文件，将以占位 MAC / 空 token 运行"
            "（在软件里点「设置设备凭据」即可配置）",
            file=sys.stderr,
        )
    else:
        from desktop import credentials

        if not credentials.is_configured():
            print(
                f"[warn] {env_file} 里的设备凭据不完整或格式不对，BLE 连接会失败；"
                "请在软件里「设置设备凭据」重新导入",
                file=sys.stderr,
            )

    if len(argv) > 1 and argv[1] == "web":
        _run_web()
        return

    from cuktech_ble_controller.__main__ import main as upstream_main

    upstream_main()


if __name__ == "__main__":
    main()
