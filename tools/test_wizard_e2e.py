"""端到端验证：扫码成功 -> 自动解析 -> 自动写入配置 -> 自动关窗。

没有真手机，所以直接往取码工具的结果路径写一份合法的 tokens.json，
再把取码进程结束掉，模拟「扫码 + 手机确认」完成的那一刻。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GH = ROOT / "GitHub"
sys.path.insert(0, str(GH))

DETACHED = 0x00000008
BREAKAWAY = 0x01000000
NEWGROUP = 0x00000200

FAKE_MAC = "9C:11:22:33:44:55"
FAKE_TOKEN = "0123456789abcdef01234567"          # 24 位
FAKE_KEY = "0123456789abcdef0123456789abcdef"    # 32 位


def fake_payload() -> str:
    return json.dumps([{
        "server": "cn",
        "homes": [{
            "home_id": 1, "home_owner": "test",
            "devices": [{
                "name": "酷态科10号充电器",
                "model": "cuktech.charger.v2",
                "mac": FAKE_MAC,
                "token": FAKE_TOKEN,
                "BLE_DATA": {"beaconkey": FAKE_KEY},
            }],
        }],
    }], ensure_ascii=False)


def main() -> int:
    from desktop import credentials, paths

    env = credentials.env_path()
    baks: list[tuple[Path, Path]] = []
    proc = None
    try:
        for src in (env, GH / ".env"):
            if src.is_file():
                dst = src.parent / (src.name + ".testbak")
                shutil.copy2(src, dst)
                src.unlink()
                baks.append((src, dst))
        print("准备完毕，is_configured =", credentials.is_configured())

        py = str(GH / ".venv" / "Scripts" / "python.exe")
        proc = subprocess.Popen(
            [py, "main.py", "--collect"],
            cwd=str(GH), creationflags=DETACHED | BREAKAWAY | NEWGROUP,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        print("向导 pid=", proc.pid)

        time.sleep(13)          # 等二维码就位
        out_json = paths.data_dir() / "collect" / "tokens.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(fake_payload(), encoding="utf-8")
        print("已写入模拟取码结果:", out_json)
        subprocess.run(["taskkill", "/F", "/IM", "token_extractor.exe"], capture_output=True)
        print("已结束取码进程（模拟扫码完成）")

        # 前端 600ms 轮询 -> 解析 -> 自动保存 -> 1.6s 后关窗
        for i in range(1, 11):
            time.sleep(1)
            if env.is_file():
                text = env.read_text(encoding="utf-8", errors="replace")
                if FAKE_MAC.replace(":", "") in text.replace(":", "").upper():
                    print(f"  {i}s: 配置已自动写入 ✔")
                    break
                print(f"  {i}s: 文件已出现但内容还没对上")
            else:
                print(f"  {i}s: 等待写入…")

        subprocess.run([py, str(HERE / "shot_window.py"), "连接充电器",
                        str(HERE / "out" / "wizard_done.png")],
                       capture_output=True, text=True)

        ok = env.is_file() and FAKE_MAC.replace(":", "") in env.read_text(
            encoding="utf-8", errors="replace").replace(":", "").upper()
        print("RESULT:", "PASS 自动写入成功" if ok else "FAIL 没有自动写入")
        if env.is_file():
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line:
                    k = line.split("=", 1)[0]
                    print(f"   {k} = <{len(line.split('=', 1)[1])} 字符>")
        return 0 if ok else 1
    finally:
        if proc is not None:
            subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "token_extractor.exe"], capture_output=True)
        if env.is_file():
            env.unlink()
        for src, dst in baks:
            if dst.is_file():
                shutil.copy2(dst, src)
                dst.unlink()
        print("已还原真实配置，is_configured =", credentials.is_configured())


if __name__ == "__main__":
    sys.exit(main())
