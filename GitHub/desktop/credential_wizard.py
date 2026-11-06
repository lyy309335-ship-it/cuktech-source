"""设备凭据导入向导（独立进程）。

由 `CUKTECH.exe --collect` 拉起，跑完就退。独立进程的理由：
manager 主线程被 `webview.start()` 占着，同进程再开第二个 WebView2 宿主
需要额外协调消息循环；而向导是一次性对话框，没必要让它常驻。

**首选通道是扫码**：`token_extractor.exe` 以交互模式启动后（stdin 喂 `q`），
会在 `127.0.0.1:31415` 上挂出登录二维码的 PNG。本模块轮询这个地址，
把图直接塞进网页里显示（不再弹系统看图窗口、不再开黑控制台），
用户用米家 App 扫一下，进程退出后解析它写出的 JSON 并自动落盘。

另外两条兜底通道都走 `credentials.save()`：
    · 粘贴整段文本（兼容任何取码方式）
    · 从文件导入 credentials.txt / tokens.json
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from desktop import credentials, paths
from desktop.wv2env import apply_sandbox_workaround, storage_path

APP_NAME = "CUKTECH"
EXE_NAME = "token_extractor.exe"
QR_URL = "http://127.0.0.1:31415"
QR_PORT = 31415
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
QR_MIN_BYTES = 1000

# 小米登录二维码的服务端有效期是 300 秒（longPolling/loginUrl 返回的 timeout 字段）。
# 一旦过期，token_extractor 的长轮询会走到它自己的 bug：
#     except Timeout: ... break        # response 从未赋值
#     if response.status_code != 200:  # → UnboundLocalError
# 这个 exe 是第三方编译产物，改不了，所以只能赶在它踩雷之前主动换一张新码。
QR_ROTATE_SECS = 240      # 留 60 秒余量
MAX_QR_RESTARTS = 6       # 自动重启上限，防止死循环

# 本地回环请求要明确绕开系统代理，否则会被 HTTPS_PROXY 之类的变量劫持
_QR_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 取码工具同样不能被代理劫持。实测（2026-11-06 sandbox6）：
# 本机挂着 HTTPS_PROXY=127.0.0.1:2329，token_extractor 继承了它，
# 扫码登录成功、也拿到了设备列表，但接着去探测其他服务器时连接被代理重置：
#     requests.exceptions.ConnectionError(10054) → get_homes 抛异常
#     → 整个进程崩掉 → tokens.json 没写成 → 用户看到「扫了没反应」。
# 它是第三方 exe，改不了源码，只能在启动时把这几个变量摘掉。
_PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


def _child_env() -> dict:
    """给取码子进程用的环境：去掉一切代理想直连小米服务器。"""
    e = dict(os.environ)
    for k in _PROXY_VARS:
        e.pop(k, None)
    return e



def _qr_port_busy() -> bool:
    """31415 还被占着就说明旧取码进程没退干净，新进程绑不上。"""
    import socket

    s = socket.socket()
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", QR_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _find_extractor() -> Path | None:
    """找 token_extractor.exe：打包后在 _internal\\tools，源码在项目 tools\\。"""
    base = paths.resource_dir()
    for candidate in (
        base / "tools" / EXE_NAME,
        base.parent / "tools" / EXE_NAME,
        base / EXE_NAME,
    ):
        if candidate.is_file():
            return candidate
    import shutil

    found = shutil.which(EXE_NAME)
    return Path(found) if found else None


def _field_ok(values: dict) -> dict:
    """逐项判断格式是否OK，用于界面上打勾/打叉。"""
    mac = values.get("mac", "")
    return {
        "mac": bool(re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", mac))
        and mac != credentials.PLACEHOLDER_MAC,
        "token": bool(re.fullmatch(r"[0-9a-f]{24}", values.get("token", ""))),
        "ble_key": bool(re.fullmatch(r"[0-9a-f]{32}", values.get("ble_key", ""))),
    }


class Api:
    """暴露给页面的方法（window.pywebview.api.*）。"""

    def __init__(self) -> None:
        # 下划线开头：pywebview 6.x 的 get_functions() 会把非下划线的非可调用属性
        # 当子对象递归遍历；挂 Window 会与 window._js_api 形成环，整个 API 注入失败。
        self._window = None
        self._proc: subprocess.Popen | None = None
        self._extract = {"running": False, "done": False, "values": None, "error": ""}
        self._qr_hash = ""          # 上一帧二维码的摘要，用来判断要不要推新图
        self._qr_started = 0.0      # 取码进程启动时刻，用于算「等了多久」
        self._auto_qr = False       # 本次是不是静默扫码模式（关窗时要把进程收掉）
        self._gen = 0               # 第几次启动取码进程，旧监视线程靠它失效
        self._restarts = 0          # 已自动重启次数
        self._restarting = False    # 重启中，避免并发触发两次
        self._server = "cn"         # 查设备用的区域；失败后回退成全区域空串

    # --- 状态
    def status(self) -> dict:
        vals = credentials.current()
        return {
            "configured": not credentials.validate(vals),
            "display": {
                "mac": credentials.mask(vals["mac"], 2),
                "token": credentials.mask(vals["token"]),
                "ble_key": credentials.mask(vals["ble_key"]),
            },
            "configPath": str(credentials.env_path()),
        }

    # --- 解析粘贴内容
    def parse(self, text: str) -> dict:
        found = credentials.parse_paste(text or "")
        errors = credentials.validate(found)
        return {
            "ok": not errors,
            "values": found,
            "parts": _field_ok(found),
            "display": {
                "mac": found["mac"],
                "token": credentials.mask(found["token"]),
                "ble_key": credentials.mask(found["ble_key"]),
            },
            "errors": errors,
        }

    # --- 文件导入
    def pick_file(self) -> dict:
        import webview

        try:
            picked = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=(
                    "凭据文件 (*.txt;*.json;*.env)",
                    "所有文件 (*.*)",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"打开文件对话框失败：{exc}"}
        if not picked:
            return {"cancelled": True}
        path = Path(picked[0])
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            return {"error": f"读不到这个文件：{exc}"}
        if len(text) > 20000:
            text = text[:20000]
        return {"text": text, "name": path.name}

    # --- 保存
    def save(self, values: dict) -> dict:
        try:
            target = credentials.save({
                "mac": (values or {}).get("mac", ""),
                "token": (values or {}).get("token", ""),
                "ble_key": (values or {}).get("ble_key", ""),
            })
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "error": f"写配置失败：{exc}"}
        return {"ok": True, "path": str(target)}

    # --- 调起取码工具（后台跑，页面轮询结果）
    def open_extractor(self, auto_qr: bool = False, server: str = "cn") -> dict:
        """启动 token_extractor。

        auto_qr=True：不开窗口，stdin 直接喂 `q` 进扫码模式，二维码由页面内嵌显示。
        auto_qr=False：保留老行为，弹一个黑控制台，二维码用系统看图程序打开。
        """
        if self._extract.get("running"):
            return {"ok": True, "already": True}

        # 31415 被占 = 上一次崩溃残留的僵尸取码进程，或另一个实例正在扫码。
        # 不先清掉的话新进程绑不上端口，页面照样能从 31415 取到图 —— 但那是
        # 「别人家的二维码」，而本实例盯的是自己的 tokens.json，
        # 用户扫完永远等不到结果（表现为「扫了没反应」）。所以开工前必须先拿回端口。
        if _qr_port_busy():
            subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True)
            for _ in range(25):            # 最多等 5 秒
                if not _qr_port_busy():
                    break
                time.sleep(0.2)

        exe = _find_extractor()
        if exe is None:
            return {"ok": False, "error": f"找不到 {EXE_NAME}，请改用「从文件导入」或粘贴"}

        out_dir = paths.data_dir() / "collect"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_json = out_dir / "tokens.json"
        try:
            if out_json.is_file():
                out_json.unlink()
        except OSError:
            pass

        if auto_qr:
            # 静默模式：不弹控制台，stdin 喂 q。stdout 落到日志文件，
            # 二维码本身从 127.0.0.1:31415 取，不依赖控制台文本。
            log_path = out_dir / "extractor.log"
            try:
                # 追加写：每次换码都会重启取码进程，用 "wb" 会把上一次崩溃的
                # 现场直接抹掉，事后无从排查。超过 512 KB 才清一次。
                try:
                    if log_path.is_file() and log_path.stat().st_size > 512 * 1024:
                        log_path.unlink()
                except OSError:
                    pass
                log_fh = open(log_path, "ab")
            except OSError:
                log_fh = subprocess.DEVNULL
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                proc = subprocess.Popen(
                    [str(exe), "-o", str(out_json)],
                    cwd=str(exe.parent),
                    stdin=subprocess.PIPE,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                    env=_child_env(),      # 必须摘掉代理，见 _PROXY_VARS 注释
                )
            except OSError as exc:
                return {"ok": False, "error": f"启动取码工具失败：{exc}"}
            try:
                if proc.stdin is not None:
                    # 两行，对应它前两次输入提示：
                    #   1) "p/q:"             → q，走扫码登录
                    #   2) "Select server"    → cn（中国区）
                    # 第二行原来喂空行=探测全部 8 个服务器，实测每个都要一次网络
                    # 往返，任一次被掐断整个进程就崩（sandbox6 就是这么崩的），
                    # 所以只查目标区域。万一账号不在 cn，_no_device_fallback()
                    # 会退回「全部服务器」再试一次。
                    # input() 是按行读的，多写一行留在缓冲里不会丢。
                    proc.stdin.write(f"q\n{server}\n\n".encode())
                    proc.stdin.flush()
            except (OSError, ValueError):
                pass
            self._qr_hash = ""
            self._qr_started = time.time()
        else:
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            try:
                # 新控制台：扫码提示、二维码、设备列表都在那个窗口里，和以前一样
                proc = subprocess.Popen(
                    [str(exe), "-o", str(out_json)],
                    cwd=str(exe.parent),
                    creationflags=flags,
                    env=_child_env(),
                )
            except OSError as exc:
                return {"ok": False, "error": f"启动取码工具失败：{exc}"}

        self._proc = proc
        self._auto_qr = auto_qr
        self._extract = {"running": True, "done": False, "values": None, "error": ""}
        self._gen += 1
        threading.Thread(
            target=self._wait_extract, args=(proc, out_json, self._gen), daemon=True
        ).start()
        return {"ok": True, "qr": auto_qr}

    def _restart_extractor(self, reason: str) -> bool:
        """把取码进程换一个。

        两个场景会走到这里：
          ① 二维码快到 300 秒有效期 → 主动换新码（否则 exe 必崩）；
          ② exe 已经崩了（长轮询异常）→ 拉起来重来，别让用户面对一个死界面。
        """
        if self._restarting or self._restarts >= MAX_QR_RESTARTS:
            return False
        self._restarting = True
        try:
            self._restarts += 1
            print(f"[wizard] {reason}，重启取码工具（第 {self._restarts} 次）", flush=True)

            proc = self._proc
            self._gen += 1                       # 旧监视线程从此不再写状态
            self._extract = {"running": False, "done": False, "values": None, "error": ""}
            self._proc = None

            if proc is not None and proc.poll() is None:
                # 必须杀进程树：token_extractor 是 PyInstaller 产物，跑起来是
                # 「bootloader + 脚本」两个进程，TerminateProcess 只干掉父进程，
                # 子进程照样占着 31415，新进程绑不上端口，页面就会一直看到旧码。
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                )
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass

            # 等端口释放，否则新进程绑不上 31415
            for _ in range(30):
                if not _qr_port_busy():
                    break
                time.sleep(0.2)
            else:
                # 还有残留（异常路径下可能出现），按镜像名兜底清一次
                subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True)
                time.sleep(0.6)

            self._qr_hash = ""
            self._qr_started = 0.0
            res = self.open_extractor(auto_qr=True, server=self._server)
            return bool(res.get("ok"))
        finally:
            self._restarting = False

    # --- 二维码（页面每 0.6 秒来问一次）
    def qr_frame(self) -> dict:
        """返回当前该显示什么。

        png      : 新的一帧二维码（base64，不含 data: 前缀）
        unchanged: 图没变，页面不用重画
        waiting  : 取码工具还没把服务挂起来（或二维码还没生成）
        phase    : starting | qrcode | done | error
        """
        state = self._elapsed()

        # ① 快到服务端有效期了：主动换码。不换的话 token_extractor 的长轮询
        #    会超时 → 走到它那个 response 未赋值的 bug → 整个进程崩掉。
        if (
            self._extract.get("running")
            and self._qr_started
            and time.time() - self._qr_started > QR_ROTATE_SECS
        ):
            if self._restart_extractor("二维码即将过期"):
                return {"phase": "rotating", "png": "", "unchanged": True, **self._elapsed()}

        # ② 取码进程异常退出：自动重来一次，而不是直接把「取码失败」甩给用户。
        if self._extract.get("done") and not self._extract.get("values"):
            why = self._extract.get("error") or "取码进程异常退出"
            # 中国区一个设备都没匹配上 → 账号大概率不在 cn（外区账号），
            # 退回「探测全部服务器」再试一次，别让这类用户直接卡死。
            if self._server == "cn" and "CUKTECH" in why:
                self._server = ""
            if self._restart_extractor(f"取码失败（{why}）"):
                return {"phase": "rotating", "png": "", "unchanged": True, **self._elapsed()}

        if self._extract.get("done"):
            if self._extract.get("values"):
                return {"phase": "done", "png": "", "unchanged": True, **state}
            return {"phase": "error", "error": self._extract.get("error") or "取码失败",
                    "png": "", "unchanged": True, **state}

        data = None
        try:
            with _QR_OPENER.open(QR_URL, timeout=2) as resp:
                if resp.status == 200:
                    data = resp.read()
        except Exception:  # noqa: BLE001
            data = None

        if not data or not data.startswith(PNG_MAGIC) or len(data) < QR_MIN_BYTES:
            return {"phase": "starting", "png": "", "unchanged": True, **state}

        digest = hashlib.sha1(data).hexdigest()
        if digest == self._qr_hash:
            return {"phase": "qrcode", "png": "", "unchanged": True, **state}
        self._qr_hash = digest
        return {
            "phase": "qrcode",
            "png": base64.b64encode(data).decode("ascii"),
            "unchanged": False,
            **state,
        }

    def _elapsed(self) -> dict:
        secs = int(time.time() - self._qr_started) if self._qr_started else 0
        return {"elapsed": secs, "running": bool(self._extract.get("running"))}

    def _wait_extract(self, proc: subprocess.Popen, out_json: Path, gen: int = 0) -> None:
        def alive() -> bool:
            """被重启换掉的旧进程，不许再往状态里写东西。"""
            return gen == self._gen

        try:
            # 不能一直傻等进程退出：上游 exe 把 tokens.json 写完，还会停在
            # "Press ENTER to finish" 再等一行输入。万一那行没喂进去，它就一直
            # 挂着不退出，界面误判成「还在取码」，240 秒后自动换码直接把这次
            # 成功登录杀掉 —— 用户看到的就是「扫了没反应」（实测 sandbox7）。
            # 所以盯着结果文件：大小不再变大就当作写完了，直接收掉进程往下走。
            deadline = time.time() + 900
            last_size = -1
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.5)
                if out_json.is_file():
                    size = out_json.stat().st_size
                    if size > 0 and size == last_size:
                        break          # 连续两次一样大 → 写完了
                    last_size = size
            if proc.poll() is None and out_json.is_file():
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                )
            code = proc.wait()
        except Exception as exc:  # noqa: BLE001
            if alive():
                self._extract.update(running=False, done=True, error=f"取码进程异常：{exc}")
            return

        if not alive():
            return

        if not out_json.is_file():
            self._extract.update(
                running=False, done=True,
                error=f"取码工具退出码 {code}，没有生成结果文件（可能中途取消了）",
            )
            return

        try:
            devices = credentials.parse_extractor_json(out_json)
        except ValueError as exc:
            self._extract.update(running=False, done=True, error=str(exc))
            return

        chosen = credentials.pick_cuktech(devices)
        if chosen is None:
            self._extract.update(
                running=False, done=True,
                error=f"解析到 {len(devices)} 台设备，但没有可用的 CUKTECH（token 为空）",
            )
            return

        self._extract.update(running=False, done=True, values={
            "mac": credentials.normalize_mac(chosen["mac"]),
            "token": credentials.normalize_hex(chosen["token"], 24),
            "ble_key": credentials.normalize_hex(chosen["ble_key"], 32),
        }, error="")

    def poll_extract(self) -> dict:
        state = dict(self._extract)
        if state.get("values"):
            vals = state["values"]
            state["display"] = {
                "mac": vals["mac"],
                "token": credentials.mask(vals["token"]),
                "ble_key": credentials.mask(vals["ble_key"]),
            }
            state["parts"] = _field_ok(vals)
            state["errors"] = credentials.validate(vals)
            state["ok"] = not state["errors"]
        return state

    # --- 窗口
    def close(self) -> dict:
        # 静默扫码模式下用户没有控制台可关，进程得由我们收掉，
        # 否则它会一直占着 127.0.0.1:31415，下次再扫码就取不到图。
        # 已经拿到结果时不动它（可能还在写 JSON）。
        # 必须是「杀进程树」：PyInstaller 产物有 bootloader + 脚本两个进程，
        # 只 terminate 父进程的话，子进程会一直占着端口，下次扫码必然取不到图。
        try:
            if self._proc and self._proc.poll() is None and not self._extract.get("done"):
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                    capture_output=True,
                )
                try:
                    self._proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        # 兜底：真还有残留就按镜像名扫一遍，别把 31415 留给幽灵进程
        if _qr_port_busy():
            subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True)
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True}


def wizard_html() -> Path:
    return paths.resource("credential_wizard.html")


def run() -> int:
    """向导入口。返回进程退出码。"""
    import webview

    page = wizard_html()
    if not page.is_file():
        print(f"[错误] 找不到向导页面：{page}", file=sys.stderr)
        return 1

    api = Api()
    window = webview.create_window(
        "连接充电器",
        url=page.as_uri(),
        js_api=api,
        width=620,
        height=720,
        min_size=(520, 620),
        background_color="#0f1117",
        # 别设 text_select=False：粘贴框要能选中文字，否则复制粘贴全废
    )
    api._window = window

    # 排障钩子：CUKTECH_WIZARD_DEMO 里用 | 当换行，塞进粘贴框看看识别效果。
    # 和项目里 CUKTECH_WIDGET_JS / CUKTECH_SNAPSHOT 一样，只在手动设了才生效。
    demo = os.environ.get("CUKTECH_WIZARD_DEMO", "").strip()
    if demo:
        def _fill() -> None:
            import json
            import time

            time.sleep(1.5)
            script = (
                "const t=document.getElementById('paste');"
                f"t.value={json.dumps(demo.replace('|', chr(10)))};"
                "t.dispatchEvent(new Event('input'));"
            )
            try:
                window.evaluate_js(script)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_fill, daemon=True).start()

    apply_sandbox_workaround()
    webview.start(
        gui="edgechromium",
        private_mode=False,
        # 和控制台/卡片各用一套目录，否则第二个宿主会 0x8007139F（见 wv2env.py）
        storage_path=storage_path("wizard"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
