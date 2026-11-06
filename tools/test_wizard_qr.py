"""验证「首次安装、未配置」时的向导扫码流程。

真实凭据会被临时移开，测试完无条件恢复（finally 保证）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # tools\
ROOT = HERE.parent                               # 项目根
GH = ROOT / "GitHub"
sys.path.insert(0, str(GH))

DETACHED = 0x00000008
BREAKAWAY = 0x01000000
NEWGROUP = 0x00000200


def shot(py: str, tag: str) -> None:
    out = HERE / "out" / f"wizard_{tag}.png"
    r = subprocess.run(
        [py, str(HERE / "shot_window.py"), "连接充电器", str(out)],
        capture_output=True, text=True,
    )
    print("   ", tag, (r.stdout or r.stderr).strip()[:160])


def main() -> int:
    from desktop import credentials

    env = credentials.env_path()
    baks: list[tuple[Path, Path]] = []
    proc = None
    try:
        # ---- 备份真实凭据（主配置 + 源码模式的仓库根兜底 .env）----
        for src in (env, GH / ".env"):
            if src.is_file():
                dst = src.parent / (src.name + ".testbak")
                shutil.copy2(src, dst)
                src.unlink()
                baks.append((src, dst))
                print(f"已临时移开 {src}")
        print("is_configured =", credentials.is_configured())

        py = str(GH / ".venv" / "Scripts" / "python.exe")
        proc = subprocess.Popen(
            [py, "main.py", "--collect"],
            cwd=str(GH),
            creationflags=DETACHED | BREAKAWAY | NEWGROUP,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        print("向导已启动 pid=", proc.pid)

        for i in range(1, 19):
            time.sleep(1)
            if i in (4, 7, 10, 14, 18):
                shot(py, f"t{i}")
            if i == 12:
                r = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq token_extractor.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True,
                )
                print("   token_extractor:", (r.stdout or "").strip()[:120])
                import urllib.request
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    with op.open("http://127.0.0.1:31415", timeout=3) as resp:
                        data = resp.read()
                    print("   二维码服务:", resp.status, len(data), "字节 PNG=", data[:4] == b"\x89PNG")
                except Exception as exc:  # noqa: BLE001
                    print("   二维码服务取不到:", exc)
        return 0
    finally:
        if proc is not None:
            subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "token_extractor.exe"], capture_output=True)
        for src, dst in baks:
            if dst.is_file():
                shutil.copy2(dst, src)
                dst.unlink()
                print(f"已恢复 {src}")
        print("is_configured 复查 =", credentials.is_configured())
        print("清理完成")


if __name__ == "__main__":
    sys.exit(main())
