"""验证打包后的安装版本：首次启动（未配置）能否自动出二维码并自动写入。

测的是 dist\\CUKTECH\\CUKTECH.exe，不是源码，因为 frozen 路径和
_internal\\tools 里的取码工具是两条完全不同的解析路径。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GH = ROOT / "GitHub"
EXE = GH / "dist" / "CUKTECH" / "CUKTECH.exe"
HERE = Path(__file__).resolve().parent

DETACHED = 0x00000008
BREAKAWAY = 0x01000000
NEWGROUP = 0x00000200

FAKE_MAC = "9C:11:22:33:44:55"
FAKE_TOKEN = "0123456789abcdef01234567"
FAKE_KEY = "0123456789abcdef0123456789abcdef"

ROOT_APP = Path(os.environ["LOCALAPPDATA"]) / "CUKTECH"   # 配置、日志、数据都在这一层
DATA = ROOT_APP / "data"        # paths.data_dir() 打包后带一层 data 子目录
ENV = ROOT_APP / ".env"
COLLECT = DATA / "collect"


def payload() -> str:
    return json.dumps([{
        "server": "cn",
        "homes": [{"home_id": 1, "home_owner": "t", "devices": [{
            "name": "酷态科10号充电器", "model": "cuktech.charger.v2",
            "mac": FAKE_MAC, "token": FAKE_TOKEN,
            "BLE_DATA": {"beaconkey": FAKE_KEY},
        }]}],
    }], ensure_ascii=False)


def main() -> int:
    if not EXE.is_file():
        print("找不到打包产物:", EXE)
        return 2
    print("被测程序:", EXE, f"{EXE.stat().st_size / 1024 / 1024:.1f} MB")

    bak = ROOT_APP / ".env.pkgbak"
    proc = None
    try:
        if ENV.is_file():
            shutil.copy2(ENV, bak)
            ENV.unlink()
            print("真实凭据已临时移开")

        proc = subprocess.Popen(
            [str(EXE), "--collect"],
            cwd=str(EXE.parent),
            creationflags=DETACHED | BREAKAWAY | NEWGROUP,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        print("打包版向导 pid=", proc.pid)

        for i in range(1, 20):
            time.sleep(1)
            if i == 10:
                r = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq token_extractor.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True,
                )
                print("   token_extractor:", (r.stdout or "").strip()[:120] or "未启动")
                import urllib.request
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    with op.open("http://127.0.0.1:31415", timeout=3) as resp:
                        data = resp.read()
                    print("   二维码服务:", resp.status, len(data), "字节 PNG=", data[:4] == b"\x89PNG")
                except Exception as exc:  # noqa: BLE001
                    print("   二维码服务取不到:", exc)
            if i == 13:
                subprocess.run([sys.executable, str(HERE / "shot_window.py"),
                                "连接充电器", str(HERE / "out" / "wizard_pkg.png")],
                               capture_output=True, text=True)
                # 模拟扫码完成
                out_json = COLLECT / "tokens.json"
                out_json.parent.mkdir(parents=True, exist_ok=True)
                out_json.write_text(payload(), encoding="utf-8")
                subprocess.run(["taskkill", "/F", "/IM", "token_extractor.exe"], capture_output=True)
                print("   已写入模拟取码结果并结束取码进程")
            if i == 16:
                r = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq token_extractor.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True,
                )
                print("   取码进程残留:", (r.stdout or "").strip()[:100] or "无")
                subprocess.run([sys.executable, str(HERE / "shot_window.py"),
                                "连接充电器", str(HERE / "out" / "wizard_pkg_after.png")],
                               capture_output=True, text=True)
            if i > 13 and ENV.is_file():
                txt = ENV.read_text(encoding="utf-8", errors="replace")
                if FAKE_MAC.replace(":", "") in txt.replace(":", "").upper():
                    print(f"   {i - 13}s 后：配置已自动写入 ✔")
                    break
                print(f"   {i - 13}s：文件在但内容未对上")

        ok = ENV.is_file() and FAKE_MAC.replace(":", "") in ENV.read_text(
            encoding="utf-8", errors="replace").replace(":", "").upper()
        print("RESULT:", "PASS 打包版首次启动可自动扫码写入" if ok else "FAIL")
        if ENV.is_file():
            for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line:
                    print("   ", line.split("=", 1)[0], "=<", len(line.split("=", 1)[1]), "字符>")
        return 0 if ok else 1
    finally:
        if proc is not None:
            subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "token_extractor.exe"], capture_output=True)
        if ENV.is_file():
            ENV.unlink()
        if bak.is_file():
            shutil.copy2(bak, ENV)
            bak.unlink()
            print("真实凭据已还原")


if __name__ == "__main__":
    sys.exit(main())
