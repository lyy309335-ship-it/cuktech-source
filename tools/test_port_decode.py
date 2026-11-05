"""端口解码回归测试：把真机抓到的报文喂给 _decode_port_push，检查解析结果。

用到的报文全部来自 2026-11-05 的四轮实机抓包，用来锁死：
  1. A 口空载不能再凭空变出 2.4~4.8V（旧规则把 b1 当电压的 bug）
  2. C 口电压电流仍然取 b3 / b2

跑法：
    app\\.venv\\Scripts\\python.exe tools\\test_port_decode.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from cuktech_ble_controller import server as S  # noqa: E402


def find_manager_class():
    for obj in vars(S).values():
        if isinstance(obj, type) and hasattr(obj, "_decode_port_push"):
            return obj
    raise SystemExit("没找到带 _decode_port_push 的类")


CASES = [
    # (说明, piid, 真机 GET 原始帧, 期望电压, 期望电流, 期望 active)
    ("C1 空载", 1, "1120010003010201000000045000070000", 0.0, 0.0, False),
    ("C2 JBL 5.1V/0.6A", 2, "1120020003010202000000045001010633", 5.1, 0.6, True),
    ("C1 iPad 5.1V/1.2A", 1, "11200100030102010000000450000a0c33", 5.1, 1.2, True),
    ("C3 空载（旧规则会读出 9.6V）", 3, "1120030003010203000000045000600000", 0.0, 0.0, False),
    ("A 口空载（旧规则读出 2.4V）", 4, "1120040003010204000000045000300000", 0.0, 0.0, False),
    ("A 口空载（旧规则读出 4.8V）", 4, "1120040003010204000000045000600000", 0.0, 0.0, False),
    ("A 口空载（旧规则读出 5.6V）", 4, "1120040003010204000000045000700000", 0.0, 0.0, False),
]


async def main() -> int:
    cls = find_manager_class()
    fake = types.SimpleNamespace()
    fake._debug_port_push = False
    fake._port_data = {}
    fake._ws_clients = set()

    async def _noop(*_a, **_k):
        return None

    fake.broadcast = _noop
    fake._append_history_sample = _noop

    failed = 0
    for name, piid, hexstr, want_v, want_a, want_active in CASES:
        fake._port_data.pop(piid, None)
        cls._decode_port_push(fake, piid, bytes.fromhex(hexstr))
        info = fake._port_data.get(piid) or {}
        got = (info.get("voltage"), info.get("current"), info.get("active"))
        ok = got == (want_v, want_a, want_active)
        failed += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name:<28} 得 {got} 期望 {(want_v, want_a, want_active)}")

    # 让 create_task 的广播/落库任务跑完，避免 "Task was destroyed" 噪音
    await asyncio.sleep(0.05)
    print(f"\n{'全部通过' if not failed else f'{failed} 项失败'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
