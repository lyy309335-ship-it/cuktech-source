"""CUKTECH 桌面版 · 管理器。

一个进程管四件事：
  1. 托盘常驻，右键菜单就是全部操作入口
  2. 拉起并监管「采集服务」和「桌面卡片」两个子进程，崩了自动重启
  3. 主窗口 = 内部控制台：把完整控制界面装进应用自己窗口里，不再跳浏览器
  4. 运行管理（服务 / 卡片 / 开机自启）做成注入到控制台页面里的浮层（齿轮按钮）

主进程不碰蓝牙：采集全在子进程里，卡片崩了不影响采集，采集崩了也不连累界面。

为什么只有一个 WebView2 窗口（踩过的坑，别再改回两个窗口）：
    pywebview 6.2.1 的 WinForms 后端在同一进程里创建第二个窗口时，那个窗口的
    WebView2 控制器初始化失败（0x80070578 无效的窗口句柄）。症状极具欺骗性：
    load_url() 正常返回、窗口也显示出来，但页面永远不加载、不发任何请求，
    而 evaluate_js() 会一直挂在 js_result_semaphore 上不返回。
    所以运行管理不再用独立窗口，改成 console_overlay.js 注入浮层。

控制台窗口能「内置」的关键：窗口直接加载 http://127.0.0.1:8080/ —— 和浏览器
访问的是同一个页面、同一个源，所以 WebSocket 实时推送和 /api/* 调用原样可用，
不需要任何改写（同源就没有 CORS，也不存在把 fetch 转成 js_api 的语义丢失）。
服务还没起来时先显示本地引导页 console_host.html，由 Python 等到端口就绪再导航，
所以引导页自己不发任何网络请求，绕开了 file:// 跨源的不确定性。
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import autoport, autostart, credentials, paths, updater
from .icon import bolt_icon
from .version import VERSION

API = "http://127.0.0.1:8080"
CONSOLE_URL = API + "/"
API_HOST, API_PORT = "127.0.0.1", 8080
CREATE_NO_WINDOW = 0x08000000
LOG = paths.log_dir() / "manager.log"
CONF = paths.data_dir() / "manager.json"
# 自助重启的时间戳记录（防死循环），见 Manager._restart_allowed
RESTART_FILE = paths.data_dir() / "wv2_restarts.json"

# 设备端口号 -> 界面上的名字（协议里是 piid 1..4）
PORTS = (("1", "C1"), ("2", "C2"), ("3", "C3"), ("4", "A"))


# ---------------------------------------------------------------- 基础设施


def log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def setup_remote_debugging(webview) -> None:
    """CUKTECH_DEBUG_PORT=9333 时给 WebView2 开远程调试端口。

    为什么需要它：pywebview 的 evaluate_js 是「Python 执行 JS 再同步等结果」，
    一旦那层出问题（返回 undefined、脚本抛错、或页面还没就绪），Python 侧看到
    的只是「调用不返回」，完全拿不到页面真实状态。开一个 CDP 端口就能用
    Chrome DevTools 协议直接查 DOM、抓截图、看 console 错误，排查这类问题唯一靠谱。
    """
    port = os.environ.get("CUKTECH_DEBUG_PORT", "").strip()
    if not port.isdigit():
        return
    try:
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(port)
        log(f"已开启 WebView2 远程调试端口 {port}")
    except Exception as exc:  # noqa: BLE001
        log(f"开启远程调试失败 {exc!r}")


def setup_webview_log() -> None:
    """把 pywebview 自己的日志也落盘（webview.log）。

    界面出问题时唯一能看到的线索就在这里：它会在 DEBUG 级别记下
    「before_load / loaded 事件」和每一次注入异常。没有这一层，
    窗口白屏完全无从下手。
    """
    import logging

    try:
        handler = logging.FileHandler(paths.log_dir() / "webview.log", encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    # 只挂 pywebview 这一个 logger，别把 winrt/bleak 的 DEBUG 洪水引进来
    lg = logging.getLogger("pywebview")
    lg.setLevel(logging.DEBUG)
    lg.addHandler(handler)
    lg.propagate = False


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_get(path: str, timeout: float = 2.0):
    with _OPENER.open(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def port_ready(timeout: float = 0.5) -> bool:
    """8080 端口是否已经在监听（不依赖 HTTP 是否健康，只看 TCP）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((API_HOST, API_PORT)) == 0
    except OSError:
        return False


def screen_size() -> tuple[int, int]:
    try:
        u = ctypes.windll.user32
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return 1920, 1080


def load_conf() -> dict:
    try:
        data = json.loads(CONF.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_conf(**kw) -> None:
    try:
        d = load_conf()
        d.update(kw)
        CONF.parent.mkdir(parents=True, exist_ok=True)
        CONF.write_text(json.dumps(d), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log(f"保存配置失败 {exc!r}")


# ---------------------------------------------------------------- 子进程


class Child:
    """一个被监管的子进程。wanted=True 表示「用户希望它运行」。"""

    def __init__(self, key: str, label: str, args: list[str]):
        self.key = key
        self.label = label
        self.args = args
        self.proc: subprocess.Popen | None = None
        self.wanted = False
        self._last_start = 0.0
        self._fails = 0
        self._cooldown_until = 0.0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        if self.alive():
            return True
        now = time.monotonic()
        if now < self._cooldown_until:
            return False
        # 10 秒内连续重启 4 次就停 60 秒，避免死循环把 CPU 吃满
        if now - self._last_start < 10:
            self._fails += 1
        else:
            self._fails = 0
        if self._fails >= 4:
            self._fails = 0
            self._cooldown_until = now + 60
            log(f"{self.label} 反复退出，暂停 60 秒再试")
            return False
        self._last_start = now
        try:
            self.proc = subprocess.Popen(
                paths.launch_command(*self.args),
                cwd=str(paths.data_dir()),
                creationflags=CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self.wanted = True
            log(f"{self.label} 已启动 pid={self.proc.pid}")
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"{self.label} 启动失败 {exc!r}")
            return False

    def stop(self, timeout: float = 6.0) -> None:
        self.wanted = False
        p = self.proc
        self.proc = None
        if not p or p.poll() is not None:
            return
        try:
            p.terminate()
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            log(f"{self.label} 超时，已强制结束")
        except Exception as exc:  # noqa: BLE001
            log(f"{self.label} 停止异常 {exc!r}")
        log(f"{self.label} 已停止")


# ---------------------------------------------------------------- JS 桥


class Bridge:
    """注意：pywebview 会递归遍历 js_api 上的公开属性，App 必须挂私有名。"""

    def __init__(self, app: "Manager"):
        self._app = app

    # --- 状态与开关（浮层 / 引导页 / 托盘共用）
    def get_state(self):
        return self._app.snapshot()

    def set_service(self, running):
        return self._app.set_service(bool(running))

    def set_widget(self, running):
        return self._app.set_widget(bool(running))

    def set_autostart(self, enabled):
        return self._app.set_autostart(bool(enabled))

    # --- 自动开端口（时间窗 / 端口范围都可配）
    def set_auto_port(self, enabled):
        autoport.set_logger(log)
        s = autoport.set_enabled(bool(enabled))
        return {**self._app.snapshot(), "autoPort": s}

    def set_auto_port_window(self, start, end):
        """改时间窗。传 "HH:MM" 字符串；非法值后端会拒绝并返回 ok=False。"""
        autoport.set_logger(log)
        return autoport.set_window(str(start or ""), str(end or ""))

    def set_auto_port_ports(self, scope):
        """改端口范围。scope = "all" 或 "c1"/"c2"/"c3"/"a"。"""
        autoport.set_logger(log)
        return autoport.set_scope(scope)

    def auto_port_run(self):
        """手动立即检查一次，不管当前时间与当日是否已执行。"""
        autoport.set_logger(log)
        autoport.run_now()
        time.sleep(0.3)
        return autoport.snapshot()

    # --- 版本更新
    def check_update(self, force=True):
        """查一次更新。force=False 时受 24 小时节流约束。"""
        updater.set_logger(log)
        return updater.check(force=bool(force))

    def install_update(self):
        """下载并安装（后台线程跑，进度靠 get_state 轮询）。"""
        updater.set_logger(log)
        updater.set_quit_callback(lambda: threading.Thread(target=self._app.quit, daemon=True).start())
        return updater.start_install()

    # --- 窗口
    def open_console(self):
        """亮出内部控制台窗口（不跳浏览器）。"""
        self._app.show_console()
        return True

    def open_management(self):
        """亮出窗口并展开运行管理浮层。"""
        self._app.show_console()
        threading.Thread(target=self._app.open_overlay, daemon=True).start()
        return True

    def hide_console(self):
        self._app.hide_console()
        return True

    # --- 引导页
    def console_boot(self):
        return self._app.console_boot()

    def console_restart_service(self):
        return self._app.console_restart_service()

    def reload_console(self):
        return self._app.reload_console()

    def overlay_ready(self, info=""):
        self._app.on_overlay_ready(str(info)[:200])
        return True

    def open_data_dir(self):
        try:
            os.startfile(str(paths.data_dir()))  # noqa: S606
        except Exception:  # noqa: BLE001
            pass
        return True

    def quit_app(self):
        threading.Thread(target=self._app.quit, daemon=True).start()
        return True


# ---------------------------------------------------------------- 管理器


_RELEASE_MUTEX = None


def set_mutex_releaser(fn) -> None:
    """main.py 把「放掉单实例互斥量」的办法注进来（manager 不 import main，避免循环依赖）。"""
    global _RELEASE_MUTEX
    _RELEASE_MUTEX = fn


class Manager:
    def __init__(self, silent: bool = False):
        self.silent = silent
        self.service = Child("service", "采集服务", ["--service"])
        self.widget = Child("widget", "桌面卡片", ["--widget"])
        self.console = None            # 唯一的 WebView2 窗口
        self.tray_icon = None
        self._quitting = False
        self._state = {"ready": False, "connected": False, "total": 0.0, "raw": {}}
        self._console_open = False
        # idle -> loading -> ready / failed
        self._console_state = "idle"
        self._on_page = False          # 窗口里当前是不是控制页面
        self._offscreen = False        # 窗口是否还摆在屏幕外
        self._center = (80, 60)
        self._boot_lock = threading.Lock()
        self._overlay_js = ""
        self._overlay_ok = False       # 浮层是否已由页面自己报告「装上了」
        # --- WebView2 看门狗（v1.3.7）
        # 起不来的那次会让窗口一直停在「一块纯背景色」上：IsWindowVisible 是 True、
        # 窗口也有标题栏，但控件里从没创建过 CoreWebView2，所以永远不会有内容。
        # 这里记几个状态，给 _console_watchdog 用。
        self._start_url = ""
        self._geom = {"width": 960, "height": 720, "x": 80, "y": 60}
        self._wv2_revived = 0          # 已经救回来几次（日志/托盘用）
        self._wv2_dead = False         # 彻底救不回来，界面需要明确告知
        self._widget_started_at = 0.0

    # --- 状态
    def refresh(self) -> None:
        try:
            data = api_get("/api/status")
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            self._state.update(ready=False, connected=False, total=0.0, raw={})
            return
        ports = data.get("ports") or {}
        total = 0.0
        for key, _name in PORTS:
            d = ports.get(key) or ports.get(int(key))
            if isinstance(d, dict):
                try:
                    total += float(d.get("power") or 0)
                except (TypeError, ValueError):
                    pass
        self._state.update(
            ready=data.get("state") == "READY",
            connected=data.get("state") == "READY",
            total=total,
            raw=data,
        )

    def snapshot(self) -> dict:
        self.refresh()
        return {
            "version": VERSION,
            "serviceRunning": self.service.alive(),
            "serviceReady": self._state["ready"],
            "total": round(self._state["total"], 2),
            "widgetRunning": self.widget.alive(),
            "autostart": autostart.is_enabled(),
            "ports": self._state["raw"].get("ports") or {},
            "portNames": [n for _k, n in PORTS],
            "settings": self._state["raw"].get("settings") or {},
            "dataDir": str(paths.data_dir()),
            "logDir": str(paths.log_dir()),
            "consoleOpen": self._console_open,
            "consoleState": self._console_state,
            # 自动开端口（时间窗 / 端口范围可配，启动时检查一次）的当前状态，给浮层显示
            "autoPort": autoport.snapshot(),
            # 版本更新状态（查清单 / 下载 / 安装的进度都在这里）
            "update": updater.snapshot(),
        }

    # --- 操作
    def set_service(self, running: bool) -> dict:
        if running:
            ok = self.service.start()
            if ok:
                self._console_state = "idle"   # 允许下次打开时重新引导
        else:
            self.service.stop()
            ok = True
        log(f"采集服务 -> {'启动' if running else '停止'} ({ok})")
        self.sync_tray()
        return {"ok": ok, **self.snapshot()}

    def set_widget(self, running: bool) -> dict:
        if running:
            ok = self.widget.start()
        else:
            self.widget.stop()
            ok = True
        save_conf(widget=running)   # 下次启动沿用这个选择
        log(f"桌面卡片 -> {'显示' if running else '隐藏'} ({ok})")
        self.sync_tray()
        return {"ok": ok, **self.snapshot()}

    def set_autostart(self, enabled: bool) -> dict:
        if enabled:
            autostart.enable()
        else:
            autostart.disable()
        log(f"开机自启 -> {'开' if autostart.is_enabled() else '关'}")
        self.sync_tray()
        return {"ok": True, **self.snapshot()}

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        log("管理器退出")
        for ch in (self.service, self.widget):
            try:
                ch.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            if self.tray_icon:
                self.tray_icon.visible = False
                self.tray_icon.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.console:
                self.console.destroy()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)

    # ------------------------------------------------------------ 控制台窗口
    def _eval_js(self, code: str, timeout: float = 3.0):
        """evaluate_js 的带超时版本。

        pywebview 的 evaluate_js 内部是「ExecuteScriptAsync + Semaphore.acquire()」，
        只要那次 JS 没有正常跑出结果（真实页面上实测会这样），Semaphore 就永远
        不释放，调用线程被死等——界面本身没问题，但托盘线程、引导线程全被拖住。
        所以这里统一包一层超时：超时就把那个线程扔下不管（daemon），调用方拿 None
        继续往下走，绝不让一次可选的 JS 调用影响主流程。
        """
        w = self.console
        if not w:
            return None
        box: dict = {}

        def worker() -> None:
            try:
                box["r"] = w.evaluate_js(code)
            except Exception as exc:  # noqa: BLE001
                box["e"] = exc

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            return None
        return box.get("r") if "r" in box else box.get("e")

    def _console_js(self, call: str) -> None:
        """向页面推一个状态；页面可能还没加载完、或目标函数不存在，一律吞异常。"""
        code = (
            "(function(){try{var c=window.__ctl;if(!c)return;" + call + "}catch(e){}})()"
        )
        self._eval_js(code, timeout=2.0)

    def _console_page_js(self, call: str) -> None:
        """对**真正的控制台页面**（8080 上的 index.html）执行一段 JS。

        和 `_console_js` 不能混用：那个写死 `window.__ctl`，而 `__ctl` 只存在于
        引导页 console_host.html（窗口起不来时的兜底页）。控制台页面上没有它，
        调用会静默 no-op。这里用的是页面自己导出的 `window.__ck`（见 web/index.html）。
        """
        code = (
            "(function(){try{var c=window.__ck;if(!c)return;" + call + "}catch(e){}})()"
        )
        self._eval_js(code, timeout=2.0)

    def _set_page_paused(self, paused: bool) -> None:
        """控制台窗口隐藏/显示时，让页面停掉画图（v1.3.8）。

        收进托盘 ≠ 停止渲染：窗口隐藏后 WebView2 照样按 BLE 推送重绘，
        实测渲染进程常驻 3.2% CPU + GPU 1.8%。这条通道把它压到 0。

        「暂停」要重试：开机自启（--silent）时窗口是页面刚起来 3 秒就藏掉的，
        那会儿 `window.__ck` 可能还没挂上，一次调用会静默落空、页面继续白烧 CPU。
        所以让页面回一个标记，没挂上就等一会儿再试，最多 15 秒。
        """
        if not paused:
            self._console_page_js("c.setPaused(false)")
            return

        def worker():
            for _ in range(8):
                r = self._eval_js(
                    "(function(){try{var c=window.__ck;if(!c)return 'no';"
                    "c.setPaused(true);return 'ok';}catch(e){return 'err';}})()",
                    timeout=2.0,
                )
                if r == "ok":
                    log("控制台页面已暂停渲染")
                    return
                time.sleep(2.0)
            log("控制台页面暂停未确认（__ck 未就绪），隐藏期间可能仍有少量重绘")

        threading.Thread(target=worker, daemon=True).start()

    def show_console(self) -> None:
        """主入口：把内部控制台亮出来；没引导过就引导。"""
        w = self.console
        if not w:
            # 不再退回系统浏览器：窗口还没建起来时只记日志，靠托盘「重新加载控制台」重试
            log("控制台窗口尚未创建，无法显示（不退回浏览器）")
            return
        if self._wv2_dead:
            # 明知渲染不了就别把黑窗推给用户；顺手再救一次，救活了才显示
            log("控制台 WebView2 处于失效状态，先尝试恢复再显示")
            threading.Thread(target=self._heal_console_then_show, daemon=True).start()
            return
        self._console_open = True
        try:
            if getattr(w, "minimized", False):
                w.restore()
            if getattr(self, "_offscreen", False):
                try:
                    w.move(*getattr(self, "_center", (80, 60)))
                except Exception:  # noqa: BLE001
                    pass
                self._offscreen = False
            w.show()
        except Exception as exc:  # noqa: BLE001
            log(f"显示控制台失败 {exc!r}")
            return
        # 恢复画图（隐藏期间页面是暂停的），并补一次全量刷新
        self._set_page_paused(False)
        if self._console_state == "ready" and port_ready() and self._on_console_page():
            return
        self.console_boot()

    def hide_console(self) -> None:
        self._console_open = False
        # 先把页面停掉再藏窗口：隐藏后 WebView2 仍会按推送重绘，白烧 CPU
        self._set_page_paused(True)
        try:
            if self.console:
                self.console.hide()
        except Exception:  # noqa: BLE001
            pass

    def open_overlay(self) -> None:
        """展开控制台页面里的运行管理浮层。

        走的是「页面自己载入了浮层脚本」这条路，Python 侧只是可选地帮它展开一下；
        展开失败（或 evaluate_js 不返回）不影响用户手动点右下角的齿轮按钮。
        """
        time.sleep(0.45)
        self._eval_js(
            "(function(){try{window.__cktOverlay&&window.__cktOverlay.open(true)}catch(e){}})()",
            timeout=2.0,
        )

    def on_overlay_ready(self, info: str) -> None:
        """浮层注入成功后由页面回调，证明页面脚本和 js_api 双向都通。"""
        self._overlay_ok = True
        log(f"控制台页面就绪（浮层回报）: {info}")
        if os.environ.get("CUKTECH_SNAPSHOT"):
            self.snapshot_page()

    def snapshot_page(self) -> None:
        """把 WebView2 自己画出来的页面存成 PNG（排查「窗口黑屏」用）。

        走 DevTools 的 Page.captureScreenshot，取的是渲染进程合成的结果，
        和窗口有没有显示出来无关：
          - 截出来是完整界面 → 页面渲染没问题，问题在「呈现到窗口」这一层
          - 截出来是空白 → 渲染管线本身坏了
        由 CUKTECH_SNAPSHOT=1 触发，正常使用不会跑。
        """
        import base64
        from System import Action

        def work() -> None:
            time.sleep(1.2)
            try:
                form = getattr(self.console, "native", None)
                if form is None:
                    log("页面快照失败：拿不到窗口原生对象")
                    return
                holder: dict = {}

                def kick() -> None:
                    """必须在 UI 线程里发起（CoreWebView2 有线程亲和性）。"""
                    try:
                        core = form.webview.CoreWebView2
                        if core is None:
                            holder["err"] = "CoreWebView2 为空（控制器没建起来）"
                            return
                        holder["task"] = core.CallDevToolsProtocolMethodAsync(
                            "Page.captureScreenshot", '{"format":"png"}'
                        )
                    except Exception as exc:  # noqa: BLE001
                        holder["err"] = repr(exc)

                form.Invoke(Action(kick))          # 切到 UI 线程，只负责发起
                if "err" in holder:
                    log(f"页面快照失败：{holder['err']}")
                    return
                task = holder.get("task")
                for _ in range(80):
                    if task is not None and task.IsCompleted:
                        break
                    time.sleep(0.25)
                if task is None or not task.IsCompleted:
                    log("页面快照失败：captureScreenshot 超时")
                    return
                data = base64.b64decode(json.loads(task.Result)["data"])
                out = paths.log_dir() / "console-shot.png"
                out.write_bytes(data)
                log(f"页面快照已保存 {out}（{len(data)} 字节）")
            except Exception as exc:  # noqa: BLE001
                log(f"页面快照异常 {exc!r}")

        threading.Thread(target=work, daemon=True).start()

    def console_boot(self) -> dict:
        """确保采集服务在跑，就绪后把窗口导航到控制页面。可重复调用。"""
        if not self.console:
            return {"ok": False, "error": "no window"}
        if self._console_state == "ready" and port_ready() and self._on_console_page():
            return {"ok": True, "state": "ready"}
        with self._boot_lock:
            if self._console_state == "loading":
                return {"ok": True, "state": "loading"}
            self._console_state = "loading"
        log("控制台引导开始")
        self._overlay_ok = False        # 马上要重新导航，浮层得重新报到
        self._console_js("c.step(1,'启动采集服务进程')")
        self.service.start()
        threading.Thread(target=self._console_wait, daemon=True).start()
        return {"ok": True, "state": "loading"}

    def console_restart_service(self) -> dict:
        log("用户要求重启采集服务（从控制台引导页）")
        self.service.stop()
        self._console_state = "idle"
        return self.console_boot()

    def reload_console(self) -> dict:
        # 「重新加载」在 WebView2 已经坏了的情况下是没用的（页面根本没有控件可载），
        # 这时改走恢复流程：重新初始化 → 整进程重启。
        if self._wv2_dead or (not self._console_healthy() and self._wv2_alive() is False):
            log("用户点『重新加载控制台』，但 WebView2 未就绪，改为恢复窗口")
            threading.Thread(target=self._heal_console_then_show, daemon=True).start()
            return {"ok": True, "state": "healing"}
        self._console_state = "idle"
        return self.console_boot()

    def _on_console_page(self) -> bool:
        """窗口里现在是控制页面还是本地引导页。

        不用 get_current_url()：它带 @_loaded_call，一旦 loaded 事件没触发就会
        阻塞 20 秒，把调用它的线程（托盘/监管线程）一起拖死。我们自己记状态更可靠。
        """
        return self._on_page

    def _console_wait(self, timeout: float = 45.0) -> None:
        """等 8080 起来再导航。全程由 Python 驱动，页面不做任何网络请求。"""
        started = time.monotonic()
        deadline = started + timeout
        self._console_js("c.step(2)")
        slowed = False
        while time.monotonic() < deadline:
            if port_ready():
                break
            if not slowed and time.monotonic() - started > 12:
                slowed = True
                self._console_js("c.slow('启动比平时慢，蓝牙适配器可能正在初始化')")
            time.sleep(0.3)
        else:
            self._console_state = "failed"
            log("控制台引导失败：45 秒内 8080 未就绪")
            self._console_js(
                "c.fail('45 秒内没等到 8080 端口，可点「重启采集服务」，或查看日志')"
            )
            return

        self._console_js("c.ok()")
        time.sleep(0.15)
        try:
            self.console.load_url(CONSOLE_URL)
            self._console_state = "ready"
            self._on_page = True
            log(f"控制台已载入 {CONSOLE_URL}")
        except Exception as exc:  # noqa: BLE001
            self._console_state = "failed"
            log(f"控制台载入失败 {exc!r}")
            self._console_js(f"c.fail('载入界面失败：{exc}')")

    def _load_overlay(self) -> str:
        if self._overlay_js:
            return self._overlay_js
        try:
            path = paths.resource("console_overlay.js")
            self._overlay_js = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log(f"浮层脚本读取失败 {exc!r}")
            self._overlay_js = ""
        return self._overlay_js

    def _on_loaded(self, *_args) -> None:
        """每次页面加载完都确认一次浮层在不在。

        不在这里重置 _overlay_ok：一次页面加载可能触发多个 loaded 事件，
        重置过的那个线程等不到第二次回报，就会误报「浮层未自报就绪」。
        真正需要重置的时机是「我们主动重新导航」——见 console_boot / reload_console。
        """
        if self._overlay_ok:
            return
        threading.Thread(target=self._check_overlay, daemon=True).start()

    def _check_overlay(self) -> None:
        """浮层正常由页面自己 <script src="/console.js"> 载入，这里只做兜底。

        页面载入脚本后会用 js_api 回调 overlay_ready，_overlay_ok 变 True。
        等 6 秒还没回报，才退回老办法（Python 侧 evaluate_js 注入）——
        老办法在这台机器上经常不返回，所以它永远是「最后试一次」，不是主路径。
        """
        for _ in range(20):                      # 最多等 6 秒
            if self._overlay_ok:
                return
            time.sleep(0.3)
        if not self._on_console_page():
            return
        js = self._load_overlay()
        if not js:
            return
        log("浮层未自报就绪，尝试用 evaluate_js 兜底注入")
        got = self._eval_js(js, timeout=4.0)
        log(f"兜底注入返回: {'有结果' if got is not None else '超时（不影响主界面）'}")

    # ------------------------------------------------------------ 托盘
    def _cred_ok(self) -> bool:
        """凭据是否配好。按 .env 的 mtime 缓存，别每 3 秒读一次盘。"""
        try:
            mtime = credentials.env_path().stat().st_mtime
        except OSError:
            mtime = 0.0
        if getattr(self, "_cred_cache", None) != mtime:
            self._cred_cache = mtime
            try:
                self._cred_value = credentials.is_configured()
            except Exception:  # noqa: BLE001
                self._cred_value = False
        return bool(getattr(self, "_cred_value", False))

    def _tray_title(self, _item=None):
        if not self._cred_ok():
            return "未配置设备凭据 · 点「设置设备凭据」"
        if not self.service.alive():
            return "采集服务：已停止"
        if self._state["connected"]:
            return f"已连接 · {self._state['total']:.1f} W"
        return "采集服务：运行中（未连上设备）"

    def _console_text(self, _item=None):
        return ("■ " if self._console_open else "□ ") + "控制台"

    def _service_text(self, _item=None):
        return ("■ " if self.service.alive() else "□ ") + "采集服务"

    def _widget_text(self, _item=None):
        return ("■ " if self.widget.alive() else "□ ") + "桌面卡片"

    def _autostart_text(self, _item=None):
        return ("● " if autostart.is_enabled() else "○ ") + "开机自启"

    def _update_text(self, _item=None):
        """托盘里的更新项文字：有新版时直接写在菜单上，不用点进去才知道。"""
        s = updater.snapshot()
        phase = s.get("phase")
        if phase == "available":
            return f"↑ 有新版本 v{s.get('latest')}…"
        if phase == "downloading":
            return f"↓ 正在下载 {int(float(s.get('progress') or 0) * 100)}%…"
        if phase == "ready":
            return "正在准备安装…"
        if phase == "installing":
            return "正在安装，即将重启…"
        if phase == "checking":
            return "正在检查更新…"
        return "检查更新…"

    def _open_update(self):
        """点更新项 = 打开运行管理浮层（版本、说明、按钮都在那儿，菜单里放不下）。"""
        self.show_console()
        threading.Thread(target=self.open_overlay, daemon=True).start()
        if updater.snapshot().get("phase") not in (
            "available", "downloading", "ready", "installing"
        ):
            updater.set_logger(log)
            updater.start_check(force=True)

    def sync_tray(self) -> None:
        try:
            if not self.tray_icon:
                return
            self.refresh()
            self.tray_icon.icon = bolt_icon(self._state["total"], self._state["connected"])
            if not self._cred_ok():
                self.tray_icon.title = "CUKTECH 未配置凭据"
            elif self._state["connected"]:
                self.tray_icon.title = f"CUKTECH  {self._state['total']:.1f} W"
            else:
                self.tray_icon.title = "CUKTECH 未连接"
            self.tray_icon.update_menu()
        except Exception as exc:  # noqa: BLE001
            log(f"托盘刷新失败 {exc!r}")

    def _build_tray(self):
        import pystray

        Menu, MenuItem = pystray.Menu, pystray.MenuItem
        self.tray_icon = pystray.Icon(
            "cuktech",
            bolt_icon(0, False),
            "CUKTECH",
            menu=Menu(
                MenuItem(self._tray_title, None, enabled=False),
                Menu.SEPARATOR,
                MenuItem(self._console_text, lambda: self.toggle_console(), default=True),
                MenuItem("运行管理…", lambda: self.show_management()),
                MenuItem("设置设备凭据…", lambda: self.open_credentials()),
                MenuItem(self._widget_text, lambda: self.set_widget(not self.widget.alive())),
                Menu.SEPARATOR,
                MenuItem(self._service_text, lambda: self.set_service(not self.service.alive())),
                MenuItem(self._autostart_text, lambda: self.set_autostart(not autostart.is_enabled())),
                Menu.SEPARATOR,
                MenuItem(self._update_text, lambda: self._open_update()),
                Menu.SEPARATOR,
                MenuItem("重新加载控制台", lambda: self.reload_console()),
                MenuItem("打开数据目录", lambda: self._open_dir(paths.data_dir())),
                MenuItem("退出", lambda: threading.Thread(target=self.quit, daemon=True).start()),
            ),
        )

    def toggle_console(self) -> None:
        if self._console_open:
            self.hide_console()
        else:
            self.show_console()
        self.sync_tray()

    def show_management(self) -> None:
        self.show_console()
        self.open_overlay()
        self.sync_tray()

    # --- 设备凭据
    def open_credentials(self) -> None:
        """拉起凭据向导（独立进程）。

        不在本进程开窗口：主线程被 webview.start() 占着，向导是一次性对话框，
        跑完就退，没必要混进当前的窗口管理。
        """
        try:
            proc = subprocess.Popen(
                paths.launch_command("--collect"),
                cwd=str(paths.resource_dir()),
            )
        except OSError as exc:
            log(f"凭据向导启动失败 {exc!r}")
            return
        log("已拉起凭据向导")
        threading.Thread(target=self._after_wizard, args=(proc,), daemon=True).start()

    def _after_wizard(self, proc) -> None:
        """向导关掉之后：配置变了就把采集服务重启一次，让它重读凭据。"""
        try:
            proc.wait()
        except Exception:  # noqa: BLE001
            return
        if not credentials.is_configured():
            log("凭据向导已关闭，凭据仍未配置")
            try:
                self.sync_tray()
            except Exception:  # noqa: BLE001
                pass
            return
        log("凭据已更新，重启采集服务以重新连接")
        try:
            self.service.stop()
            time.sleep(0.5)
            self.service.start()
            self._console_state = "idle"
        except Exception as exc:  # noqa: BLE001
            log(f"重启采集服务失败 {exc!r}")
        try:
            self.sync_tray()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_credentials(self) -> None:
        """启动时凭据缺失就先把向导弹出来。

        --silent（开机自启）不弹：开机时打断用户很烦，托盘里有入口就够了。
        """
        if self.silent or credentials.is_configured():
            return
        log("设备凭据未配置，先打开导入向导")
        try:
            subprocess.run(
                paths.launch_command("--collect"),
                cwd=str(paths.resource_dir()),
            )
        except OSError as exc:
            log(f"凭据向导启动失败 {exc!r}")

    @staticmethod
    def _open_dir(path) -> None:
        try:
            os.startfile(str(path))  # noqa: S606
        except Exception:  # noqa: BLE001
            pass

    # --- 监管
    def _supervise(self) -> None:
        while not self._quitting:
            for ch in (self.service, self.widget):
                if ch.wanted and not ch.alive():
                    log(f"{ch.label} 意外退出，正在重启")
                    ch.start()
                    if ch is self.service and self._console_state == "ready" and not port_ready():
                        # 服务重启过，那次引导就作废了（页面自己会重连 WS，
                        # 但停在引导页的情况必须重新走一遍）
                        self._console_state = "idle"
            try:
                self.sync_tray()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3.0)

    # --- 主流程
    def _wait_port(self, timeout: float = 25.0) -> bool:
        """建窗口前先等本地接口起来，这样窗口可以「一出生」就加载控制页面。

        为什么要等而不是「先显示引导页再导航过去」：pywebview 的 loaded /
        _pywebviewready 事件是绑定在「当前文档」上的，file:// → http:// 这种
        运行时换文档很容易把事件和 WebView2 的注入搞乱（实测页面拿到了
        HTML 却不执行脚本、evaluate_js 一直挂住）。直接以最终地址建窗口，
        走的是和桌面卡片完全相同、已经验证稳定的路径。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_ready():
                return True
            time.sleep(0.3)
        return False

    def run(self) -> None:
        import webview

        setup_webview_log()
        setup_remote_debugging(webview)
        # 更新模块：日志走这里（独立进程 --update-runner 会退回写 updater.log）
        updater.set_logger(log)
        updater.set_quit_callback(
            lambda: threading.Thread(target=self.quit, daemon=True).start()
        )
        result = updater.take_install_result()
        if result:
            log(f"上次更新结果：{'成功' if result.get('ok') else '失败'} {result.get('detail', '')}")
        # 凭据缺失就先让用户填：否则采集服务起来只会一直连不上，
        # 用户看到的是「软件坏了」，而不是「还差一步配置」。
        self._ensure_credentials()
        conf = load_conf()
        self.service.start()
        # 自动开端口：只在「进程启动 + 落在所设时间窗内」时做一次（规则见 autoport.py）。
        # 必须在 service.start() 之后——它要等采集服务把 BLE 连上才读得到位图。
        autoport.start(log)
        # 卡片默认开（用户装它就是为了常驻桌面），之前手动关掉的会记住
        if conf.get("widget", True):
            self.widget.start()
            self._widget_started_at = time.monotonic()

        self._build_tray()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        threading.Thread(target=self._supervise, daemon=True).start()
        # 40 秒后再查更新：启动这一刻在连 BLE、等 8080 就绪，别抢时间
        updater.start_check_later(40.0)

        # 服务一般 3~5 秒就把 8080 挂起来；等得到就直接开控制台，
        # 等不到（蓝牙适配器异常等）才退回本地引导页，那时才用得到导航。
        ready = self._wait_port(25.0)
        if ready:
            self._console_state = "ready"
            self._on_page = True
            start_url = CONSOLE_URL
        else:
            self._console_state = "idle"
            self._on_page = False
            start_url = paths.resource("console_host.html").as_uri()
        log(f"控制台窗口创建，起始页 {'控制台' if ready else '本地引导页'}")

        # 窗口必须「出生时是显示的」：隐藏状态下的 WebView2 拿不到有效的合成目标，
        # 实测会出现「HTML 请求发出去了、脚本却不执行、evaluate_js 一直挂住」。
        # 于是 --silent（开机自启）时先把窗口摆到屏幕外，页面加载完再隐藏，
        # 开始正常的挂机状态；用户点开时再挪回屏幕中央。
        sw, sh = screen_size()
        width = max(880, min(1160, sw - 120))
        height = max(620, min(820, sh - 140))
        self._center = (max(0, (sw - width) // 2), max(0, (sh - height) // 3))
        self._offscreen = self.silent
        x, y = (-32000, 0) if self._offscreen else self._center
        self._start_url = start_url
        self._geom = {"width": width, "height": height, "x": x, "y": y}

        # 卡片和控制台各用各的 WebView2 数据目录（见 wv2env.storage_path）。
        # 以前共用 + 错开 2.5 秒仍会稳定触发 0x8007139F，v1.3.7 起不再需要错开。
        self._create_console(start_url)

        def after_start():
            time.sleep(0.6)
            if self.silent:
                # 给页面 3 秒把文档和脚本跑起来，再收进托盘（避免开机时闪一个窗口）
                time.sleep(3.0)
                self.hide_console()
            else:
                self.show_console()

        threading.Thread(target=after_start, daemon=True).start()
        # WebView2 起不来的兜底：它失败时 pywebview 只写一行日志就算了，
        # 窗口会永远停在一块纯背景色上，用户看到的正是「控制台全黑」。
        threading.Thread(target=self._console_watchdog, daemon=True).start()
        log(f"管理器启动（silent={self.silent}）version={VERSION}")
        from desktop.wv2env import apply_sandbox_workaround, storage_path, tune_gc

        apply_sandbox_workaround()      # 不加这行，窗口就是一块纯背景色（见 wv2env.py）
        tune_gc()                       # 初始化已完成，把启动期对象冻出 GC 扫描范围
        webview.start(
            gui="edgechromium",
            private_mode=False,
            # 独立目录！共用会让第二个宿主的控制器创建失败（0x8007139F → 纯黑窗口），
            # 详见 wv2env.py 第 2 节的三组对照实验。
            storage_path=storage_path("console"),
        )

    def _on_console_closing(self, *_args):
        # 点 X = 收进托盘，不退出
        if self._quitting:
            return True
        self.hide_console()
        return False

    # --- 控制台窗口 & WebView2 看门狗（v1.3.7）
    #
    # 背景：2026-11-04 出现过一次「控制台窗口在、里面全黑」，卡片却正常。
    # webview.log 里只有一行：
    #   ERROR pywebview WebView2 initialization failed with exception:
    #   (0x8007139F): 组或资源的状态不是执行请求操作的正确状态。
    # 0x8007139F = ERROR_INVALID_STATE，来自 CreateCoreWebView2ControllerAsync，
    # 即 WebView2 的**控制器**没建起来（不是页面没数据、也不是配色问题）。
    # pywebview 在 on_webview_ready 里看到 IsSuccess=False 只 log 然后 return，
    # 既不复位控件也不重试，于是那块窗体永远是背景色 #0f1116 —— 看起来就是纯黑。
    #
    # 处理分三层（能救就地救，救不动也要说清楚，绝不能留一块无声的黑窗）：
    #   1. 重新调一次 EnsureCoreWebView2Async（WebView2 明确支持重试）；
    #      成功后 pywebview 自己的 on_webview_ready 会再跑一遍并加载原地址。
    #   2. 还不行就整进程重启：停子进程 → 放互斥量 → 拉起自己 → 退出。
    #      ⚠️ 不能「销毁窗口再建一个」：pywebview 销毁最后一个窗口会退出 GUI 循环，
    #      实测新窗口再也起不来（进程还在，但没窗口也不写日志）。
    #   3. 重启也用尽（10 分钟 2 次上限）：隐藏黑窗 + 托盘气泡 + 日志，
    #      明确告诉用户该手动重开，而不是让他对着一块纯色发愣。

    def _create_console(self, url: str) -> None:
        """建控制台窗口。窗口几何参数存在 self._geom 里，便于排查时复用。"""
        import webview

        g = self._geom
        self.console = webview.create_window(
            "CUKTECH 控制台",
            url,
            js_api=Bridge(self),
            width=g["width"],
            height=g["height"],
            x=g["x"],
            y=g["y"],
            min_size=(880, 620),
            resizable=True,
            hidden=False,
            background_color="#0F1116",
            text_select=True,
        )
        self.console.events.closing += self._on_console_closing
        self.console.events.loaded += self._on_loaded

    def _wv2_control(self):
        """挖出底层 WinForms 的 WebView2 控件。

        pywebview 没有公开这个引用，只能顺着 window.native（BrowserForm）
        → .browser（edgechromium.EdgeChrome）→ .webview（WebView2 控件）摸下去。
        版本升级后这条链可能变，所以每一步都 getattr + 允许为 None。
        """
        win = self.console
        form = getattr(win, "native", None) if win is not None else None
        browser = getattr(form, "browser", None) if form is not None else None
        return getattr(browser, "webview", None) if browser is not None else None

    def _console_healthy(self) -> bool:
        """页面脚本跑起来了 ⟺ WebView2 控制器必然活着。

        **只能用 `_overlay_ok`**：那是浮层通过 js_api 主动回报的，证明 JS 真的执行过。
        ⚠️ 千万不要把 `self._on_page` 也算进来 —— 它只是 `run()` 在**创建窗口之前**
        根据「8080 端口通不通」预设的「打算加载哪个页」，跟渲染成功与否毫无关系。
        实测 WebView2 初始化失败（窗口纯黑）时 `_on_page` 同样是 True，
        把它当健康判据会让看门狗直接放行、彻底失效。

        比直接读 .NET 控件属性可靠得多：后者从非 UI 线程访问会抛
        InvalidOperationException（页面明明已就绪也会抛），旧版据此误判过一次。
        """
        return bool(getattr(self, "_overlay_ok", False))

    def _wv2_alive(self):
        """True=CoreWebView2 已就绪；False=明确为 None；None=读不到/控件还没出现。

        只做属性读，不跨线程 Invoke —— 万一 UI 线程正卡着初始化，
        Invoke 会把看门狗线程一起锁死，就彻底没有兜底了。

        ⚠️ 抛异常一律记作 None（未知），**绝不能记成 False**：pythonnet 从看门狗
        线程读 WinForms 控件属性时经常抛 InvalidOperationException，那只是跨线程，
        不代表控件坏了。把它当 False 会让正常启动的窗口被误重启。
        """
        ctrl = self._wv2_control()
        if ctrl is None:
            return None
        try:
            return ctrl.CoreWebView2 is not None
        except Exception:  # noqa: BLE001
            return None

    def _wv2_reinit(self) -> str:
        """在 UI 线程上重来一次初始化。返回给日志看的结果字符串。"""
        ctrl = self._wv2_control()
        form = getattr(self.console, "native", None)
        if ctrl is None or form is None:
            return "拿不到控件引用"
        from System import Action  # pythonnet

        box = {"error": ""}

        def call():
            try:
                ctrl.EnsureCoreWebView2Async(None)
            except Exception as exc:  # noqa: BLE001
                box["error"] = repr(exc)

        try:
            # 用 BeginInvoke 而不是 Invoke：排队即可，不阻塞看门狗
            form.BeginInvoke(Action(call))
        except Exception as exc:  # noqa: BLE001
            return f"投递失败 {exc!r}"
        time.sleep(0.8)
        return box["error"] or "已投递"

    def _console_watchdog(self) -> None:
        """只在「页面没跑起来 且 明确读到 CoreWebView2=None」时才动手。

        第一判据是页面自己回报（`_console_healthy`）：JS 能执行就等于渲染控件是好的，
        不需要也不应该去碰 .NET 属性。`_wv2_alive()` 返回 None 表示读不到
        （控件未出现 / 跨线程访问抛异常），那是启动慢或线程限制，不是故障；
        旧版把它当 False 会误重启正常窗口，现在给足 30 秒才按失败兜底。
        """
        time.sleep(6.0)                     # 正常初始化几百毫秒就好，先给它 6 秒
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if self._quitting:
                return
            if self._console_healthy():
                return                      # 页面都跑起来了，看门狗功成身退
            state = self._wv2_alive()
            if state:
                return
            if state is False:
                break                       # 明确读到 CoreWebView2=None，进入重试
            time.sleep(2.0)                 # 只是读不到/控件未出现，继续等，绝不误重启
        else:
            if self._console_healthy():
                return
            log("控制台 WebView2 控件 30 秒未能确认就绪，按初始化失败处理")

        for attempt in range(1, 4):
            if self._quitting:
                return
            if self._console_healthy() or self._wv2_alive():
                return
            log(f"控制台 WebView2 未完成初始化（第 {attempt}/3 次检测），重新初始化…")
            log(f"  重新初始化：{self._wv2_reinit()}")
            for _ in range(14):             # 等待最多 7 秒看有没有活过来
                time.sleep(0.5)
                if self._console_healthy() or self._wv2_alive():
                    self._wv2_revived += 1
                    log(f"控制台 WebView2 已初始化成功（第 {attempt} 次重试），正在加载页面")
                    return
        if self._quitting:
            return
        log("控制台 WebView2 重试无效，改为整进程重启（这是唯一能彻底换掉坏环境的办法）")
        if self._restart_process():
            return
        log("自动重启不可用或已达次数上限：控制台无法渲染（窗口已隐藏，请看托盘提示）")
        self._console_dead_notice()

    # --- 重启自救（v1.3.7）
    #
    # 为什么不是「销毁窗口再建一个」：pywebview 在最后一个窗口被销毁时会退出 GUI 循环，
    # 实测销毁后新窗口再也起不来（进程还在，但没有窗口、也没有日志）。
    # 所以真正的兜底只能换进程。先停掉子进程（否则新实例的 8080 会被占），
    # 再放掉单实例互斥量，然后拉起自己、立即退出。

    def _restart_allowed(self) -> bool:
        """10 分钟内最多自救 2 次，防止「起来就坏、坏了又起」的死循环。"""
        now = time.time()
        hist = []
        try:
            hist = [float(x) for x in json.loads(RESTART_FILE.read_text(encoding="utf-8"))]
        except Exception:  # noqa: BLE001
            hist = []
        hist = [t for t in hist if now - t < 600]
        if len(hist) >= 2:
            return False
        hist.append(now)
        try:
            RESTART_FILE.parent.mkdir(parents=True, exist_ok=True)
            RESTART_FILE.write_text(json.dumps(hist), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return True

    def _restart_process(self) -> bool:
        if not self._restart_allowed():
            log("  10 分钟内已自动重启过 2 次，不再重启")
            return False
        log("  正在重启程序（详情见 版本更新说明.md v1.3.7）")
        for ch in (self.service, self.widget):
            try:
                ch.stop()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(1.2)
        try:
            if _RELEASE_MUTEX is not None:
                _RELEASE_MUTEX()
            launcher = paths.launch_command(*sys.argv[1:])
            subprocess.Popen(launcher, close_fds=True)
        except Exception as exc:  # noqa: BLE001
            log(f"  拉起新进程失败：{exc!r}")
            return False
        self._quitting = True
        log("  旧进程退出，等新进程接管")
        time.sleep(0.4)
        os._exit(0)          # WebView2 已坏，走正常退出流程有卡死风险
        return True

    def _heal_console_then_show(self) -> None:
        """用户点了「控制台」但上次渲染是坏的：先试着重初始化，不行就整进程重启。

        修不好就不显示黑窗，只弹托盘气泡——这比展示一块没有内容的窗体诚实。
        """
        self._wv2_dead = False
        if self._console_healthy():
            log("控制台页面本来就是好的，直接显示")
            self._console_open = True
            self.console.show()
            return
        log("用户要求显示控制台，先尝试重新初始化 WebView2")
        log(f"  重新初始化：{self._wv2_reinit()}")
        for _ in range(20):
            time.sleep(0.5)
            if self._console_healthy() or self._wv2_alive():
                self._wv2_revived += 1
                log("控制台 WebView2 已恢复，正在显示")
                self._console_open = True
                self.console.show()
                self.console_boot()
                return
        log("重新初始化无效，改为整进程重启")
        if self._restart_process():
            return
        log("控制台仍无法渲染，已放弃显示（请退出后重新打开程序）")
        self._wv2_dead = True
        try:
            if self.tray_icon is not None:
                self.tray_icon.notify(
                    "控制台仍无法渲染。请右键托盘 → 退出，然后重新打开程序。",
                    "CUKTECH 控制台",
                )
        except Exception:  # noqa: BLE001
            pass

    def _console_dead_notice(self) -> None:
        """救不回来时的最后一步：别让用户对着一块黑屏发愣。"""
        self._wv2_dead = True
        self.hide_console()
        try:
            if self.tray_icon is not None:
                self.tray_icon.notify(
                    "控制台窗口无法渲染（WebView2 初始化失败），已自动重试 3 次仍未恢复。"
                    "请右键托盘 → 退出，然后重新打开程序。",
                    "CUKTECH 控制台",
                )
        except Exception:  # noqa: BLE001
            pass
