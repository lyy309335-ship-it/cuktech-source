"""一次性探针：抓端口推送的原始明文，看除电压/电流外还有没有别的字段。

用途：判断「充电头屏幕上的 Apple 6A / Samsung 3A 这类协议标签」有没有通过
MiOT 属性暴露出来。做法是直连设备、认证、把 cmd_recv 通道收到的明文原样打出来，
再把 PIID 1-20 逐个 GET 一遍看有没有未知属性返回数据。

跑法（需要控制台服务先断开，否则 BLE 连接被占用）：
    app\\.venv\\Scripts\\python.exe tools\\probe_port_raw.py

不写配置、不改设备状态（只做 GET，不做任何 SET）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from desktop import credentials  # noqa: E402

cred = credentials.current()
if not all(cred.values()):
    print("凭据不全，先跑「取硬件码.bat」")
    raise SystemExit(2)

os.environ["CUKTECH_DEVICE_MAC"] = cred["mac"]
os.environ["CUKTECH_DEVICE_TOKEN"] = cred["token"]
os.environ["CUKTECH_DEVICE_BLE_KEY"] = cred["ble_key"]

from cuktech_ble_controller import ble as B  # noqa: E402

LISTEN_SECONDS = float(os.environ.get("PROBE_LISTEN", "12"))
PORT_NAMES = {1: "C1", 2: "C2", 3: "C3", 4: "A"}


async def dump_gets(ctrl):
    print("\n=== GET SIID=2 / PIID 1-20 ===")
    ports: dict[int, bytes] = {}
    for piid in range(1, 21):
        if piid == 14:                      # 只写
            continue
        name = B.PIID_NAMES.get(piid, "?")
        try:
            res = await ctrl.send_miot_command(B.SIID_CHARGER, piid)
        except Exception as exc:            # noqa: BLE001
            print(f"  PIID {piid:2d} [{name}]: 异常 {exc!r}")
            continue
        if not res:
            print(f"  PIID {piid:2d} [{name}]: 无响应")
        else:
            raw = (res.get("raw") or b"").hex()
            print(f"  PIID {piid:2d} [{name}]: value={res.get('value')} raw={raw}")
            if piid in (1, 2, 3, 4) and res.get("raw"):
                ports[piid] = bytes(res["raw"])[-4:]
        await asyncio.sleep(0.25)

    print("\n=== 四口 4 字节快照（b0 状态 / b1 协议位? / b2 电流 / b3 电压）===")
    print("   口   b0   b1   b2   b3    电压     电流")
    for piid in (1, 2, 3, 4):
        b = ports.get(piid)
        if not b:
            print(f"   {PORT_NAMES[piid]:<3}  --")
            continue
        voltage = b[3] / 10.0 if piid in (1, 2, 3) else b[1] / 20.0
        current = b[2] / 10.0
        print(f"   {PORT_NAMES[piid]:<3}  {b[0]:02x}   {b[1]:02x}   "
              f"{b[2]:02x}   {b[3]:02x}   {voltage:5.1f}V   {current:4.1f}A")


async def listen_pushes(ctrl, seconds):
    """把 cmd_recv 明文原样打出来，按端口去重。"""
    print(f"\n=== 监听端口推送 {seconds:.0f}s（明文原样）===")
    q = ctrl._notify_queues.get("cmd_recv")
    if q is None:
        print("  拿不到 cmd_recv 队列")
        return {}
    seen: dict[int, str] = {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            data = await asyncio.wait_for(q.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        if not data or len(data) < 4 or data[2] != 0x02:
            continue
        enc = data[4:]
        if not enc:
            continue
        try:
            await ctrl.client.write_gatt_char(
                B.CHAR_CMD_RECV, bytes([0x00, 0x00, 0x03, 0x00]), response=False)
        except Exception:                   # noqa: BLE001
            pass
        try:
            pt = ctrl._decrypt(enc)
        except Exception as exc:            # noqa: BLE001
            print(f"  解密失败: {exc!r}")
            continue
        if not pt or len(pt) < 12:
            continue
        if pt[4] != 0x04 or pt[6] != B.SIID_CHARGER:
            print(f"  [其他] {pt.hex()}")
            continue
        piid = pt[7]
        if not 1 <= piid <= 4:
            print(f"  [PIID {piid}] {pt.hex()}")
            continue
        hexs = pt.hex()
        if seen.get(piid) == hexs:
            continue
        seen[piid] = hexs
        print(f"  {PORT_NAMES[piid]} len={len(pt):2d}  {hexs}")
    return seen


async def robust_connect(ctrl):
    """连上并等 GATT 服务真正可枚举，再订阅通知。

    上游 connect() 连上就立刻 start_notify，设备刚断开/重启时服务表还没解析完，
    会抛 BleakCharacteristicNotFoundError —— 抢连接的时代价就是一次失败就输给看门狗。
    """
    ctrl.client = B.BleakClient(ctrl.mac)
    await ctrl.client.connect()
    need = [B.CHAR_AUTH_CTRL, B.CHAR_AUTH_DATA, B.CHAR_CMD_SEND, B.CHAR_CMD_RECV]
    for _ in range(40):
        try:
            await ctrl.client.get_services()
        except Exception:                   # noqa: BLE001
            pass
        have = {str(c.uuid).lower() for c in ctrl.client.services}
        if all(u.lower() in have for u in need):
            break
        await asyncio.sleep(0.2)
    for name, uuid in (
        ("auth_ctrl", B.CHAR_AUTH_CTRL), ("auth_data", B.CHAR_AUTH_DATA),
        ("cmd_send", B.CHAR_CMD_SEND), ("cmd_recv", B.CHAR_CMD_RECV),
    ):
        await ctrl.client.start_notify(uuid, ctrl._make_notify_handler(name))
    print(f"[+] 已连接! MTU={ctrl.client.mtu_size}")


async def main():
    ctrl = B.CuktechBLEController()
    for attempt in range(1, 4):
        try:
            await robust_connect(ctrl)
            break
        except Exception as exc:            # noqa: BLE001
            print(f"[!] 第 {attempt} 次连接失败：{exc!r}")
            try:
                await ctrl.disconnect()
            except Exception:               # noqa: BLE001
                pass
            ctrl = B.CuktechBLEController()
            if attempt == 3:
                print("    多半是控制台服务正占着 BLE 连接，先断开它再跑本脚本")
                return 3
            await asyncio.sleep(1.0)
    try:
        await ctrl.read_device_info()
        if not await ctrl.authenticate():
            print("[!] 认证失败")
            return 4
        seen = await listen_pushes(ctrl, LISTEN_SECONDS)
        await dump_gets(ctrl)
        print("\n=== 汇总 ===")
        for piid in sorted(seen):
            print(f"  {PORT_NAMES[piid]}: {seen[piid]}")
        if not seen:
            print("  这段窗口内没收到端口推送（设备空闲或推送很稀疏）")
    finally:
        try:
            await ctrl.disconnect()
        except Exception:                   # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
