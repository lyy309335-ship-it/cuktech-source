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
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
QR_MIN_BYTES = 1000

# 本地回环请求要明确绕开系统代理，否则会被 HTTPS_PROXY 之类的变量劫持
_QR_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
    def open_extractor(self, auto_qr: bool = False) -> dict:
        """启动 token_extractor。

        auto_qr=True：不开窗口，stdin 直接喂 `q` 进扫码模式，二维码由页面内嵌显示。
        auto_qr=False：保留老行为，弹一个黑控制台，二维码用系统看图程序打开。
        """
        if self._extract.get("running"):
            return {"ok": True, "already": True}

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
                log_fh = open(log_path, "wb")
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
                )
            except OSError as exc:
                return {"ok": False, "error": f"启动取码工具失败：{exc}"}
            try:
                if proc.stdin is not None:
                    proc.stdin.write(b"q\n")
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
                )
            except OSError as exc:
                return {"ok": False, "error": f"启动取码工具失败：{exc}"}

        self._proc = proc
        self._auto_qr = auto_qr
        self._extract = {"running": True, "done": False, "values": None, "error": ""}
        threading.Thread(target=self._wait_extract, args=(proc, out_json), daemon=True).start()
        return {"ok": True, "qr": auto_qr}

    # --- 二维码（页面每 0.6 秒来问一次）
    def qr_frame(self) -> dict:
        """返回当前该显示什么。

        png      : 新的一帧二维码（base64，不含 data: 前缀）
        unchanged: 图没变，页面不用重画
        waiting  : 取码工具还没把服务挂起来（或二维码还没生成）
        phase    : starting | qrcode | done | error
        """
        state = self._elapsed()
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

    def _wait_extract(self, proc: subprocess.Popen, out_json: Path) -> None:
        try:
            code = proc.wait()
        except Exception as exc:  # noqa: BLE001
            self._extract.update(running=False, done=True, error=f"取码进程异常：{exc}")
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
        try:
            if (self._auto_qr and self._proc and self._proc.poll() is None
                    and not self._extract.get("done")):
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    self._proc.kill()
        except Exception:  # noqa: BLE001
            pass
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
