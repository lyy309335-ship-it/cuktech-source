"""软件更新：查清单 → 下载 → 校验 → 交给独立进程安装。

三条硬约束（原因见 docs/软件更新与发行方案.md，改之前先读）：

1. **只信 HTTPS + SHA256**。项目没有代码签名证书，哈希是唯一防篡改手段；
   清单里出现 `http://` 的下载地址一律丢弃，不商量。
2. **清单地址固定，安装包地址由清单给**。清单走 GitHub 的
   `releases/latest/download/update.json`，这个路径不随版本变化，所以换安装包、
   换版本号都不用改代码；以后换托管也只改清单内容。
3. **安装必须由外部进程触发**。`installer.do_install()` 第一步是
   `taskkill /IM CUKTECH.exe`（3 秒不响应就 `/F`），强杀会留下 WebView2 孤儿进程占住
   `_internal` 里的 dll，而清理旧文件是 `except OSError: pass` —— 被占的文件直接被跳过，
   结果是新旧文件混装（表现为「更新完某些功能还跑旧代码」，极难排查）。
   所以顺序只能是：主程序自己退干净 → `--update-runner` 独立进程跑 `setup /S`。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import paths, updatecfg
from .version import VERSION

# 显式禁用代理：本机系统代理会把 github 和 127.0.0.1 一并劫走（之前抓本地页面时踩过）。
# autoport.py 里也是这个写法。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CHECK_TIMEOUT = 6.0
DOWNLOAD_TIMEOUT = 45.0
DOWNLOAD_CHUNK = 256 * 1024
MIN_CHECK_INTERVAL = 24 * 3600          # 24 小时内最多查一次
MANIFEST_MAX_BYTES = 64 * 1024
MANAGER_MUTEX = "Local\\CUKTECH_Desktop_Manager"

_STATE_PATH = "update_state.json"        # 纯机器状态，和 config.json 分开放
_MANIFEST_CACHE = "update.json"
_DOWNLOAD_NAME = "setup.exe"

_DETACHED = 0x00000008
_BREAKAWAY = 0x01000000
_NEW_GROUP = 0x00000200
_NO_WINDOW = 0x08000000

_LOCK = threading.RLock()
_LOGGER = None
_QUIT_CB = None

_STATE = {
    "phase": "idle",      # idle/checking/uptodate/available/downloading/ready/installing/error/disabled
    "current": VERSION,
    "latest": "",
    "notes": [],
    "progress": 0.0,
    "message": "",
    "error": "",
    "checkedAt": 0.0,
}
_MANIFEST: dict | None = None


# ---------------------------------------------------------------- 基础设施


def set_logger(fn) -> None:
    """由 manager 注入日志函数；独立进程里退回写文件。"""
    global _LOGGER
    _LOGGER = fn


def log(msg: str) -> None:
    if _LOGGER:
        try:
            _LOGGER(f"[更新] {msg}")
            return
        except Exception:  # noqa: BLE001
            pass
    _file_log(msg)


def _file_log(msg: str) -> None:
    try:
        d = paths.log_dir()
        with open(d / "updater.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def set_quit_callback(fn) -> None:
    """安装前请主程序自己退出的回调（manager 注入 self.quit）。"""
    global _QUIT_CB
    _QUIT_CB = fn


def _state_file() -> Path:
    return paths.data_dir() / _STATE_PATH


def _download_dir() -> Path:
    d = paths.data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log(f"写状态失败 {exc!r}")


def _set(**kw) -> None:
    with _LOCK:
        _STATE.update(kw)


# ---------------------------------------------------------------- 版本比较


def _vtuple(v: str) -> tuple[int, ...]:
    """只取数字段。`1.7.0-beta` 这种也按 1.7.0 处理。"""
    out = []
    for part in str(v).replace("v", "").replace("V", "").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            out.append(int(digits))
        elif out:
            break
    return tuple(out)


def is_newer(remote: str, local: str) -> bool:
    """**必须按 int 分段比**。字符串比会让 "1.10.0" < "1.9.0"，是这类功能最常见的线上事故。"""
    a, b = _vtuple(remote), _vtuple(local)
    if not a:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# ---------------------------------------------------------------- 清单


def manifest_url() -> str:
    repo = (updatecfg.REPO or "").strip().strip("/")
    if not repo or "/" not in repo:
        return ""
    return f"https://github.com/{repo}/releases/latest/download/{updatecfg.MANIFEST}"


def _normalize(raw: object) -> dict | None:
    """校验清单。字段不全或含 http 地址就整份作废 —— 宁可这次不更新，也不装可疑的包。"""
    if not isinstance(raw, dict):
        return None
    version = str(raw.get("version") or "").strip().lstrip("vV")
    if not _vtuple(version):
        return None

    downloads = []
    for item in raw.get("downloads") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        sha = str(item.get("sha256") or "").strip().lower()
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if not url.startswith("https://"):
            log(f"丢弃非 https 下载地址：{url[:80]}")
            continue
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            log(f"丢弃 sha256 非法的条目：{url[:80]}")
            continue
        downloads.append({"url": url, "sha256": sha, "size": size})

    if not downloads:
        return None

    notes = raw.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    notes = [str(n)[:200] for n in notes if str(n).strip()][:8]

    return {
        "version": version,
        "min_version": str(raw.get("min_version") or "").strip(),
        "pub_date": str(raw.get("pub_date") or "").strip(),
        "notes": notes,
        "downloads": downloads,
    }


def fetch_manifest() -> dict:
    url = manifest_url()
    if not url:
        raise RuntimeError("未配置更新源")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"CUKTECH/{VERSION}",
            "Cache-Control": "no-cache",
            "Accept": "application/json",
        },
    )
    with _OPENER.open(req, timeout=CHECK_TIMEOUT) as resp:
        body = resp.read(MANIFEST_MAX_BYTES + 1)
    if len(body) > MANIFEST_MAX_BYTES:
        raise ValueError("清单异常大")
    data = _normalize(json.loads(body.decode("utf-8")))
    if not data:
        raise ValueError("清单字段不完整或下载地址不合法")
    return data


# ---------------------------------------------------------------- 状态


def snapshot() -> dict:
    with _LOCK:
        s = dict(_STATE)
        s["notes"] = list(_STATE["notes"])
        s["available"] = _STATE["phase"] in ("available", "downloading", "ready", "installing")
        s["configured"] = bool(manifest_url())
        return s


def _save_check_time(result: str, seen: str = "") -> None:
    data = _read_json(_state_file())
    data.update({
        "last_check": time.time(),
        "last_result": result,
        "seen_version": seen,
        "current": VERSION,
    })
    _write_json(_state_file(), data)


def last_check_time() -> float:
    try:
        return float(_read_json(_state_file()).get("last_check") or 0)
    except (TypeError, ValueError):
        return 0.0


def take_install_result() -> dict | None:
    """读一次上次安装的结果（有就清掉），给启动时提示用。"""
    data = _read_json(_state_file())
    result = data.pop("install_result", None)
    if result:
        _write_json(_state_file(), data)
    return result if isinstance(result, dict) else None


# ---------------------------------------------------------------- 检查


def check(force: bool = False) -> dict:
    """查一次清单。**所有失败都静默**：用户不该因为更新检查看到报错。"""
    if not manifest_url():
        _set(phase="disabled", message="未配置更新源")
        return snapshot()

    if not force:
        last = last_check_time()
        if last and time.time() - last < MIN_CHECK_INTERVAL:
            if _STATE["phase"] == "idle":
                _set(phase="uptodate", message="已是最新版本", checkedAt=last)
            return snapshot()

    _set(phase="checking", message="正在检查更新…", error="")
    try:
        manifest = fetch_manifest()
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        log(f"检查失败 {exc!r}")
        _set(phase="error", message="检查更新失败，稍后自动重试", error=repr(exc))
        _save_check_time("fetch_failed")
        return snapshot()

    global _MANIFEST
    _MANIFEST = manifest
    _save_check_time("ok", manifest["version"])

    if is_newer(manifest["version"], VERSION):
        _set(
            phase="available",
            latest=manifest["version"],
            notes=list(manifest["notes"]),
            message=f"发现新版本 v{manifest['version']}",
            checkedAt=time.time(),
            error="",
        )
        log(f"发现新版本 {VERSION} -> {manifest['version']}")
    else:
        _set(
            phase="uptodate",
            latest=manifest["version"],
            message="已是最新版本",
            checkedAt=time.time(),
            error="",
        )
    return snapshot()


def start_check(force: bool = False) -> None:
    threading.Thread(target=lambda: check(force), daemon=True, name="update-check").start()


def start_check_later(delay: float = 40.0, force: bool = False) -> None:
    """启动后延迟再查：别和 BLE 连接、8080 端口就绪抢时间。"""

    def waiter():
        try:
            time.sleep(delay)
            check(force)
        except Exception as exc:  # noqa: BLE001
            log(f"延迟检查异常 {exc!r}")

    threading.Thread(target=waiter, daemon=True, name="update-check-later").start()


# ---------------------------------------------------------------- 下载与安装


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _free_space(path: Path) -> int:
    try:
        import shutil

        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _download(entry: dict) -> Path:
    d = _download_dir()
    final = d / _DOWNLOAD_NAME
    part = d / (_DOWNLOAD_NAME + ".part")

    need = int(entry.get("size") or 0) * 2 + 80 * 1024 * 1024
    free = _free_space(d)
    if free and free < need:
        raise OSError(f"磁盘空间不足（需要约 {need >> 20} MB，剩余 {free >> 20} MB）")

    for p in (part, final):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    req = urllib.request.Request(
        entry["url"],
        headers={
            "User-Agent": f"CUKTECH/{VERSION}",
            "Accept": "application/octet-stream",
        },
    )
    total = int(entry.get("size") or 0)
    got = 0
    hasher = hashlib.sha256()
    _set(phase="downloading", progress=0.0, message="正在下载安装包…")
    with _OPENER.open(req, timeout=DOWNLOAD_TIMEOUT) as resp, open(part, "wb") as f:
        total = int(resp.headers.get("Content-Length") or total or 0)
        while True:
            chunk = resp.read(DOWNLOAD_CHUNK)
            if not chunk:
                break
            f.write(chunk)
            hasher.update(chunk)
            got += len(chunk)
            if total:
                _set(progress=round(min(got / total, 1.0), 3))
    if total and got != total:
        raise OSError(f"下载不完整（{got} / {total} 字节）")

    actual = hasher.hexdigest()
    if actual != entry["sha256"]:
        try:
            part.unlink()
        except OSError:
            pass
        raise ValueError(
            f"安装包校验失败，已丢弃（期望 {entry['sha256'][:12]}…，实际 {actual[:12]}…）"
        )
    part.replace(final)
    log(f"下载完成 {got} 字节，校验通过")
    return final


def _launch_runner(setup: Path, sha: str) -> bool:
    import subprocess

    cmd = paths.launch_command("--update-runner", str(setup), sha)
    flags = _DETACHED | _NEW_GROUP
    try:
        subprocess.Popen(
            cmd,
            cwd=str(paths.resource_dir()),
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags | _BREAKAWAY,
        )
        return True
    except OSError as exc:
        # 不在 job 对象里时 BREAKAWAY 会报 ACCESS_DENIED，退回普通分离
        log(f"带 BREAKAWAY 启动失败，退回普通分离：{exc!r}")
        try:
            subprocess.Popen(
                cmd,
                cwd=str(paths.resource_dir()),
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            return True
        except OSError as exc2:
            log(f"启动更新进程失败 {exc2!r}")
            return False


def _install_worker() -> None:
    with _LOCK:
        manifest = _MANIFEST
    if not manifest:
        _set(phase="error", message="没有可用的更新信息，请重新检查")
        return

    last_err = ""
    for entry in manifest["downloads"]:
        try:
            setup = _download(entry)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            log(f"下载失败 {entry['url'][:70]} → {last_err}")
            continue

        _set(phase="ready", progress=1.0, message="安装包已就绪，正在重启安装…")
        if not _launch_runner(setup, entry["sha256"]):
            last_err = "无法启动安装进程"
            _set(phase="error", message="安装失败，请稍后手动下载", error=last_err)
            return
        _set(phase="installing", message="正在安装，程序即将重启…")
        log("更新进程已拉起，主程序准备退出")
        cb = _QUIT_CB
        if cb:
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                log(f"退出回调异常 {exc!r}")
        return

    _set(phase="error", message="下载失败，请检查网络后重试", error=last_err)


def start_install() -> dict:
    """开始下载安装。必须在「已发现新版本」之后调用。"""
    if _STATE["phase"] in ("downloading", "ready", "installing"):
        return snapshot()
    if not _MANIFEST:
        _set(phase="error", message="没有可用的更新信息，请先检查更新")
        return snapshot()
    threading.Thread(target=_install_worker, daemon=True, name="update-install").start()
    return snapshot()


# ---------------------------------------------------------------- 更新进程（--update-runner）


def _manager_alive() -> bool:
    """主程序（管理器）是否还活着：看它的单实例互斥量在不在。"""
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.CreateMutexW(None, False, MANAGER_MUTEX)
        alive = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        if handle:
            k32.CloseHandle(handle)
        return bool(alive)
    except Exception:  # noqa: BLE001
        return False


def _wait_manager_exit(timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _manager_alive():
            return True
        time.sleep(0.4)
    return False


def _save_install_result(ok: bool, detail: str) -> None:
    data = _read_json(_state_file())
    data["install_result"] = {
        "ok": bool(ok),
        "detail": detail[:300],
        "version": (_MANIFEST or {}).get("version", ""),
        "at": time.time(),
    }
    _write_json(_state_file(), data)


def run_runner(setup: str, sha: str = "") -> int:
    """`--update-runner` 角色：等主程序退出 → 二次校验 → 静默安装。

    退出码：0 成功 / 2 找不到安装包 / 3 校验失败 / 4 安装超时 / 5 安装器报错
    """
    import subprocess

    log("更新进程启动，等待主程序退出…")
    path = Path(setup)
    if not path.is_file():
        log(f"安装包不存在：{setup}")
        _save_install_result(False, "安装包不存在")
        return 2

    if sha:
        actual = sha256_of(path)
        if actual.lower() != sha.lower():
            log(f"二次校验失败 {actual[:12]}… != {sha[:12]}…")
            _save_install_result(False, "安装包校验失败")
            return 3
        log("二次校验通过")

    if _wait_manager_exit(45.0):
        log("主程序已退出")
    else:
        log("等待主程序退出超时，仍然继续（安装器会自行结束它）")
    time.sleep(1.2)     # 给 WebView2 子进程和文件句柄收尾留点时间

    try:
        rc = subprocess.run(
            [str(path), "/S"],
            timeout=300,
            close_fds=True,
            creationflags=_NO_WINDOW,
        ).returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"调用安装器失败 {exc!r}")
        _save_install_result(False, f"安装器调用失败：{exc!r}")
        return 4

    log(f"安装器退出码 {rc}")
    if rc == 0:
        _save_install_result(True, "安装完成")
        try:
            path.unlink()
        except OSError:
            pass
        return 0
    _save_install_result(False, f"安装器退出码 {rc}")
    return 5
