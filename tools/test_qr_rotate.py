"""验证向导的两条自愈路径（不建窗口，直接调 Api）。

背景：token_extractor.exe 的长轮询有个必崩的 bug ——
    except Timeout: ... break        # response 从未赋值
    if response.status_code != 200:  # UnboundLocalError
小米给二维码的有效期是 300 秒，超过这个点再去扫，exe 必崩。
exe 是第三方编译产物改不了，只能在向导这一层提前换码 + 崩了自动重来。

本脚本把 300 秒这个真实等待压缩成几秒，验证：
  A. 二维码接近有效期 → qr_frame() 返回 rotating 并换出新码
  B. 取码进程被杀（模拟崩溃）→ qr_frame() 返回 rotating 并自动拉起新进程
  C. 自动重启有次数上限，不会无限刷

用法：python tools/test_qr_rotate.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GH = ROOT / "GitHub"
sys.path.insert(0, str(GH))

from desktop import credential_wizard as cw  # noqa: E402


def wait_phase(api, want: str, limit: float = 25.0) -> dict:
    end = time.time() + limit
    last = {}
    while time.time() < end:
        last = api.qr_frame()
        if last.get("phase") == want:
            return last
        time.sleep(0.4)
    return last


def wait_png(api, limit: float = 25.0) -> str:
    end = time.time() + limit
    while time.time() < end:
        f = api.qr_frame()
        if f.get("phase") == "qrcode" and f.get("png"):
            return f["png"]
        time.sleep(0.4)
    return ""


def main() -> int:
    api = cw.Api()
    print("=" * 62)
    print(f"ROTATE_SECS={cw.QR_ROTATE_SECS}  MAX_RESTARTS={cw.MAX_QR_RESTARTS}")
    print("=" * 62)

    ok = True
    try:
        # ---------- 起步：正常出一张码
        r = api.open_extractor(auto_qr=True)
        if not r.get("ok"):
            print("启动取码工具失败:", r)
            return 2
        png1 = wait_png(api)
        if not png1:
            print("RESULT: FAIL 第一张二维码没出来")
            return 1
        print(f"[1] 首张二维码 OK（base64 {len(png1)} 字符）")

        # ---------- A. 到期换码
        api._qr_started = time.time() - (cw.QR_ROTATE_SECS + 5)
        f = api.qr_frame()
        got_rotating = f.get("phase") == "rotating"
        print(f"[2] 把启动时刻拨到 {cw.QR_ROTATE_SECS + 5} 秒前 → phase={f.get('phase')} "
              f"{'✔ rotating' if got_rotating else '✘ 没触发换码'}")
        ok &= got_rotating

        png2 = wait_png(api)
        changed = bool(png2) and png2 != png1
        print(f"[3] 新码已出（base64 {len(png2)} 字符），与旧码不同: {'✔' if changed else '✘'}")
        ok &= changed

        # ---------- B. 崩溃自愈
        before = api._gen
        subprocess.run(["taskkill", "/F", "/IM", "token_extractor.exe"], capture_output=True)
        time.sleep(1.5)
        f = wait_phase(api, "rotating", 12)
        healed = f.get("phase") in ("rotating", "qrcode")
        print(f"[4] 强杀取码进程后 → phase={f.get('phase')}，gen {before}→{api._gen} "
              f"{'✔ 自动重生' if healed and api._gen > before else '✘ 没救回来'}")
        ok &= healed

        png3 = wait_png(api)
        print(f"[5] 重生后的二维码: {'✔' if png3 else '✘'}")

        # ---------- C. 上限保护
        api._restarts = cw.MAX_QR_RESTARTS
        api._qr_started = time.time() - (cw.QR_ROTATE_SECS + 5)
        f = api.qr_frame()
        capped = f.get("phase") != "rotating"
        print(f"[6] 重启次数用满后 → phase={f.get('phase')} "
              f"{'✔ 不再重启（有上限）' if capped else '✘ 仍在无限重启'}")
        ok &= capped

    finally:
        try:
            if api._proc and api._proc.poll() is None:
                api._proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        subprocess.run(["taskkill", "/F", "/IM", "token_extractor.exe"], capture_output=True)

    print("=" * 62)
    print("RESULT:", "PASS 两条自愈路径都生效" if ok else "FAIL 有断言未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
