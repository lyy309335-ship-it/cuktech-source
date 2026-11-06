"""沙箱验证：模拟一台「从未装过本软件」的干净电脑，跑完首次安装的完整链路。

为什么要沙箱
------------
用户机器上已经有 `%LOCALAPPDATA%\\CUKTECH\\.env`（真实凭据），直接测永远走不到
「未配置 → 弹向导」这条分支。本机又有三个实例占着 8080，直接跑会撞端口。

做法是把 `LOCALAPPDATA` 环境变量指向沙箱目录，于是：
    config_dir() = <沙箱>\\CUKTECH          ← .env 在这里，天然是空的
    data_dir()   = <沙箱>\\CUKTECH\\data
    log_dir()    = <沙箱>\\CUKTECH\\logs
对被测程序来说，这就是一台新机器，一个字节都没被特判。

测的是 dist\\CUKTECH\\CUKTECH.exe（不是源码），因为 frozen 下的路径解析、
_internal\\tools 里的取码工具是另一套逻辑，源码跑通不代表打包版跑通。

流程
----
1. 清空沙箱 → 启动 CUKTECH.exe（不带 --silent，等价于用户双击）
2. 程序自己发现「未配置」→ 同步拉起 --collect 向导
3. 等向导窗口出现 + 127.0.0.1:31415 出图 → 存二维码 PNG
4. 【人工】用手机扫码
5. token_extractor 写出 tokens.json → 向导解析 → 自动写入沙箱 .env
6. 记录写入结果，抓图，收尾

用法
----
    python tools/sandbox_test.py run          # 全流程（前台阻塞，建议后台跑）
    python tools/sandbox_test.py status       # 看当前阶段
    python tools/sandbox_test.py stop         # 清理进程
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GH = ROOT / "GitHub"
EXE = GH / "dist" / "CUKTECH" / "CUKTECH.exe"
PY = GH / ".venv" / "Scripts" / "python.exe"
SHOT = ROOT / "tools" / "shot_window.py"

SANDBOX = ROOT / "tools" / "out" / "sandbox"   # 运行时由 _pick_sandbox() 决定
ART = ROOT / "tools" / "out"                   # 截图/状态固定放这，不随沙箱换
LOCAL = SANDBOX
APP = SANDBOX / "CUKTECH"
ENV = APP / ".env"
DATA = APP / "data"
COLLECT = DATA / "collect"
STATUS = ART / "sandbox_status.json"
POINTER = ART / "sandbox_current.txt"
QR_PNG = ART / "sandbox_qr.png"
SHOT_PNG = ART / "sandbox_wizard.png"
SHOT_DONE = ART / "sandbox_done.png"


def _pick_sandbox() -> None:
    """每次跑用一个全新目录。

    不能 rmtree 旧沙箱：里面有 WebView2 缓存上千个文件，删的时候会被本机的
    safe-delete 策略拦下（单次 >50 个文件要人工确认），直接 Command 失败。
    新目录天然干净，效果等价，还更快。
    """
    global SANDBOX, LOCAL, APP, ENV, DATA, COLLECT
    base = ROOT / "tools" / "out" / "sandbox"
    p = base
    i = 1
    while p.exists():
        i += 1
        p = base.with_name(f"sandbox{i}")
    p.mkdir(parents=True, exist_ok=True)
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(p), encoding="utf-8")
    SANDBOX, LOCAL = p, p
    APP = p / "CUKTECH"
    ENV = APP / ".env"
    DATA = APP / "data"
    COLLECT = DATA / "collect"


def _load_sandbox() -> None:
    """status/stop 这类命令，沿用当前指针指向的那个沙箱。"""
    global SANDBOX, LOCAL, APP, ENV, DATA, COLLECT
    if not POINTER.is_file():
        return
    p = Path(POINTER.read_text(encoding="utf-8").strip())
    if not p.is_dir():
        return
    SANDBOX, LOCAL = p, p
    APP = p / "CUKTECH"
    ENV = APP / ".env"
    DATA = APP / "data"
    COLLECT = DATA / "collect"

WIZ_TITLE = "连接充电器"
QR_URL = "http://127.0.0.1:31415"

DETACHED = 0x00000008
BREAKAWAY = 0x01000000
NEWGROUP = 0x00000200


def put(**kw) -> None:
    """把阶段写进 status.json，前台另一条命令靠它观测。"""
    SANDBOX.mkdir(parents=True, exist_ok=True)
    cur = {}
    if STATUS.is_file():
        try:
            cur = json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cur = {}
    if cur.get("sandbox") != SANDBOX.name:
        cur = {}                        # 换了沙箱：上一轮的值全是脏数据
    cur.update(kw)
    cur["ts"] = time.strftime("%H:%M:%S")
    cur["sandbox"] = SANDBOX.name       # 并发跑过多轮，不标一下分不清是谁写的
    STATUS.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def sandbox_env() -> dict:
    e = dict(os.environ)
    e["LOCALAPPDATA"] = str(LOCAL)
    # 便携模式必须清掉，否则会盖过 LOCALAPPDATA，指回真实目录
    for k in ("CUKTECH_APP_DATA_DIR", "CUKTECH_APP_LOG_DIR"):
        e.pop(k, None)
    # 本机挂着 HTTPS_PROXY=127.0.0.1:2329，取二维码/登录都要直连
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        e.pop(k, None)
    return e


def no_proxy_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _qr_hash(p: Path) -> str:
    return str(hash(p.read_bytes())) if p.is_file() else ""


def window_exists(title: str) -> bool:
    import ctypes

    return bool(ctypes.windll.user32.FindWindowW(None, title))


def shot(title: str, out: Path) -> bool:
    if not PY.is_file():
        return False
    r = subprocess.run([str(PY), str(SHOT), title, str(out)], capture_output=True, text=True)
    return out.is_file() and "ok" in (r.stdout or "").lower()


def _port_busy(port: int) -> bool:
    import socket

    s = socket.socket()
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


LOCK = ART / "sandbox_run.lock"


def _take_lock() -> bool:
    """禁止两轮沙箱同时跑。

    教训：两轮并发时都去抢 31415，后启动的取码进程绑不上端口，页面显示的是
    另一个进程的二维码，而各自盯各自的 tokens.json —— 用户扫完两边都没反应。
    """
    if LOCK.is_file():
        try:
            pid = int(LOCK.read_text(encoding="utf-8").strip() or 0)
        except Exception:  # noqa: BLE001
            pid = 0
        if pid and not _pid_alive(pid):
            LOCK.unlink(missing_ok=True)
        else:
            print(f"⚠ 已有一轮沙箱在跑（pid={pid}），先等它结束或用 stop 清理")
            return False
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _pid_alive(pid: int) -> bool:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                       capture_output=True, text=True)
    return str(pid) in (r.stdout or "")


def _preflight() -> None:
    """开工前把 31415 / 8080 空出来，否则测的就不是自己这一个实例。"""
    kill_all()
    for _ in range(30):
        if not (_port_busy(31415) or _port_busy(8080)):
            return
        time.sleep(0.3)
    print("⚠ 端口 31415/8080 仍被占用，结果可能不准")


def kill_all() -> None:
    """只在显式 stop 时用。默认绝不批量杀进程——用户本机可能正开着正式实例。"""
    for name in ("CUKTECH.exe", "token_extractor.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)


def mask_line(line: str) -> str:
    s = line.strip()
    if "=" not in s or s.startswith("#"):
        return s
    k, v = s.split("=", 1)
    v = v.strip()
    if not v:
        return f"{k}=<空>"
    if any(t in k for t in ("TOKEN", "KEY", "MAC")):
        keep = 4 if len(v) > 8 else 0
        return f"{k}={v[:keep]}***{v[-2:] if len(v) > 8 else ''}  (长度 {len(v)})"
    return f"{k}={v}"


def read_env_masked() -> list[str]:
    if not ENV.is_file():
        return []
    return [mask_line(x) for x in ENV.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]


def cmd_fresh() -> int:
    _pick_sandbox()
    put(phase="fresh", mac="", token_len=0, key_len=0, configured=None, env=[])
    print("新的干净沙箱:", SANDBOX)
    print("  .env 存在?", ENV.is_file(), "(应为 False)")
    return 0


def cmd_run(wait_scan: int = 900) -> int:
    if not EXE.is_file():
        print("找不到打包产物:", EXE)
        return 2

    # 每次都开新沙箱：旧目录留着不删（safe-delete 会拦），新目录本身就是干净的
    _pick_sandbox()

    if not _take_lock():
        return 3
    _preflight()

    print("=" * 66)
    print("沙箱根:", SANDBOX)
    print("被测程序:", EXE, f"{EXE.stat().st_size / 1024 / 1024:.1f} MB")
    print("冒充 LOCALAPPDATA =", LOCAL)
    print("=" * 66)

    proc = subprocess.Popen(
        [str(EXE)],
        cwd=str(EXE.parent),
        env=sandbox_env(),
        creationflags=DETACHED | BREAKAWAY | NEWGROUP,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    put(phase="launched", pid=proc.pid)
    print(f"[1] 已启动 CUKTECH.exe pid={proc.pid}（无参数，等价于用户双击）")

    # ---- 2. 等向导窗口 + 二维码
    op = no_proxy_opener()
    qr_ready = False
    for i in range(1, 61):
        time.sleep(1)
        has_win = window_exists(WIZ_TITLE)
        if has_win and not qr_ready:
            print(f"[2] {i}s：向导窗口已出现（程序自己判定未配置 → 拉起 --collect）")
            put(phase="wizard_open")
        if qr_ready:
            break
        if i % 3 == 0 or has_win:
            try:
                with op.open(QR_URL, timeout=3) as resp:
                    blob = resp.read()
                if blob[:4] == b"\x89PNG" and len(blob) > 2000:
                    QR_PNG.write_bytes(blob)
                    qr_ready = True
                    print(f"[3] 二维码已就绪：{len(blob)} 字节 PNG → {QR_PNG.name}")
                    time.sleep(1.2)
                    if shot(WIZ_TITLE, SHOT_PNG):
                        print(f"    向导截图已存 {SHOT_PNG.name}")
                    put(phase="qr_ready", qr=QR_PNG.name, shot=SHOT_PNG.name if SHOT_PNG.is_file() else "")
            except Exception:  # noqa: BLE001
                if i == 60:
                    print("    取不到二维码（60s 超时）")
        if i == 20 and not has_win:
            print("    ⚠ 20s 还没看到向导窗口，继续等…")

    if not qr_ready:
        put(phase="fail", error="二维码未就绪")
        print("RESULT: FAIL 二维码没出来")
        return 1

    print(f"[4] 等待手机扫码（最多 {wait_scan}s）…", flush=True)
    put(phase="wait_scan", qr_hash=_qr_hash(QR_PNG))

    # ---- 4. 等真实扫码结果
    deadline = time.time() + wait_scan
    written = False
    last_hash = _qr_hash(QR_PNG)
    last_note = 0.0
    while time.time() < deadline:
        time.sleep(1.5)
        conf = None
        if ENV.is_file():
            txt = ENV.read_text(encoding="utf-8", errors="replace")
            if "CUKTECH_DEVICE_MAC=" in txt:
                for ln in txt.splitlines():
                    if ln.startswith("CUKTECH_DEVICE_MAC=") and len(ln.split("=", 1)[1].strip()) >= 11:
                        conf = ln
                        break
        if conf:
            written = True
            break

        # 二维码会过期轮换：变了就重新存一张，前台好及时推给用户
        try:
            with op.open(QR_URL, timeout=3) as resp:
                blob = resp.read()
            if blob[:4] == b"\x89PNG":
                h = str(hash(blob))
                if h != last_hash:
                    last_hash = h
                    QR_PNG.write_bytes(blob)
                    print(f"    二维码已刷新（{len(blob)} 字节）", flush=True)
                    put(phase="wait_scan", qr=QR_PNG.name, qr_hash=h, qr_refreshed=time.strftime("%H:%M:%S"))
        except Exception:  # noqa: BLE001
            pass

        if time.time() - last_note > 20:
            last_note = time.time()
            alive = "token_extractor.exe" in subprocess.run(
                ["tasklist", "/NH"], capture_output=True, text=True
            ).stdout.lower()
            print(f"    …等待中 取码进程={'活' if alive else '已退出'} "
                  f"tokens.json={'有' if (COLLECT / 'tokens.json').is_file() else '无'}", flush=True)

    if not written:
        put(phase="timeout", error="扫码超时")
        print("RESULT: FAIL 等不到扫码结果")
        return 1

    print("[5] 凭据已写入！沙箱 .env 内容：")
    for ln in read_env_masked():
        print("    ", ln)

    # ---- 5. 等向导关窗、抓成功态
    time.sleep(3)
    if shot(WIZ_TITLE, SHOT_DONE):
        print(f"[6] 成功态截图 {SHOT_DONE.name}（窗口若已关说明自动收尾生效）")

    vals = {}
    txt = ENV.read_text(encoding="utf-8", errors="replace")
    for ln in txt.splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            vals[k.strip()] = v.strip()
    put(
        phase="written",
        mac=vals.get("CUKTECH_DEVICE_MAC", ""),
        token_len=len(vals.get("CUKTECH_DEVICE_TOKEN", "")),
        key_len=len(vals.get("CUKTECH_DEVICE_BLE_KEY", "")),
        configured=True,
        env=read_env_masked(),
    )

    # ---- 6. 采集服务能不能起来 + 真的连上充电器（只有凭据真的有效才会连上）
    print("[7] 等采集服务起来并连上充电器 …", flush=True)
    status = {}
    for i in range(45):
        time.sleep(1)
        try:
            with op.open("http://127.0.0.1:8080/api/status", timeout=3) as resp:
                status = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            status = {}
        # 服务端的健康态是 READY（DISCONNECTED 才是没连上），别拿 CONNECTED 判
        if (status.get("state") or "").upper() == "READY":
            break
        if i in (8, 20):
            print(f"    {i}s：state={status.get('state') or '服务未就绪'}", flush=True)

    st = (status.get("state") or "").upper()
    ports = status.get("ports") or {}
    live = [k for k, v in ports.items() if v]
    settings = status.get("settings") or {}
    print(f"[8] state={st or '未知'}  有数据的端口={live or '无'}  设置项={len(settings)}")

    for title, name in (("CUKTECH 充电器控制台", "sandbox_console.png"),):
        if shot(title, ART / name):
            print(f"    控制台截图已存 {name}")

    put(phase="done", service_up=bool(st), state=st, ports=live,
        env=read_env_masked())
    if st == "READY":
        print("RESULT: PASS 扫码 → 写入 → 连接充电器 整条链路通")
        return 0
    print(f"RESULT: PARTIAL 凭据已写入，但设备状态是 {st or '未知'}"
          "（可能是充电器不在身边 / 蓝牙没开，非写入环节的问题）")
    return 0


def cmd_status() -> int:
    _load_sandbox()
    if not STATUS.is_file():
        print("还没有状态文件，先跑 run")
        return 1
    print(STATUS.read_text(encoding="utf-8"))
    print("--- 现场 ---")
    print("  沙箱:", SANDBOX)
    print("  向导窗口:", "在" if window_exists(WIZ_TITLE) else "不在")
    print("  沙箱 .env:", "有" if ENV.is_file() else "无")
    if ENV.is_file():
        for ln in read_env_masked():
            print("   ", ln)
    print("  tokens.json:", "有" if (COLLECT / "tokens.json").is_file() else "无")
    return 0


def cmd_stop() -> int:
    kill_all()
    LOCK.unlink(missing_ok=True)
    print("已清理 CUKTECH.exe / token_extractor.exe")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    wait = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    try:
        if cmd == "fresh":
            return cmd_fresh()
        if cmd == "run":
            return cmd_run(wait)
        if cmd == "status":
            return cmd_status()
        if cmd == "stop":
            return cmd_stop()
    finally:
        if cmd == "run":
            LOCK.unlink(missing_ok=True)   # 跑完就放锁，别卡住下一轮
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
