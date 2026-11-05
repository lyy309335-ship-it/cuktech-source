"""
CUKTECH BLE web controller.

Run with:
  python -m cuktech_ble_controller web
"""

import asyncio
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:
    from aiohttp import web
except ImportError:
    print("请安装 aiohttp: pip install aiohttp")
    sys.exit(1)

from .ble import (
    CuktechBLEController, DEVICE_MAC, SIID_CHARGER,
    PIID_NAMES, PIID_DISPLAY, PORT_BITS, TIMER_PORTS,
    CHAR_CMD_RECV,
)

# 设置元数据: 前端渲染控件所需的信息
SETTINGS_META = {
    5: {
        "name": "场景模式", "type": "select",
        "options": [
            {"value": 1, "label": "AI"},
            {"value": 2, "label": "数码生态"},
            {"value": 3, "label": "单口"},
            {"value": 4, "label": "均衡"},
        ],
    },
    6: {
        "name": "息屏时间", "type": "select",
        "options": [
            {"value": 5, "label": "1分钟"},
            {"value": 1, "label": "5分钟"},
            {"value": 2, "label": "10分钟"},
            {"value": 3, "label": "30分钟"},
            {"value": 4, "label": "常亮"},
        ],
    },
    13: {
        "name": "语言", "type": "select",
        "options": [
            {"value": 0, "label": "English"},
            {"value": 1, "label": "中文"},
        ],
    },
    15: {"name": "USB-A 小电流(4小时自动关闭)", "type": "toggle"},
    19: {"name": "空闲息屏", "type": "toggle"},
    20: {"name": "屏幕方向锁", "type": "toggle"},
}

READABLE_PIIDS = [5, 6, 13, 15, 16, 19, 20]
PORT_NAMES = {"c1": "C1", "c2": "C2", "c3": "C3", "a": "A"}

# API Key 认证（环境变量 CUKTECH_API_KEY 设置后启用）
API_KEY = os.environ.get("CUKTECH_API_KEY", "")
DEBUG_PORT_PUSH = os.environ.get("CUKTECH_DEBUG_PORT", "").lower() in ("1", "true", "yes", "on")
HOST = os.environ.get("CUKTECH_HOST", "127.0.0.1")
PORT = int(os.environ.get("CUKTECH_PORT", "8080"))
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_INDEX = Path(os.environ.get("CUKTECH_WEB_INDEX", PROJECT_ROOT / "web" / "index.html"))
DATA_DIR = Path(os.environ.get("CUKTECH_DATA_DIR", Path.home() / ".cuktech-ble-controller"))
# 每次启动生成随机 session token，浏览器通过 cookie 自动携带
SESSION_TOKEN = secrets.token_hex(32)

# 能力声明: 供 OpenClaw 等外部 Agent 自动发现
CAPABILITIES = {
    "device": "CUKTECH 10 GaN Charger Ultra (210W)",
    "version": "1.0.0",
    "controls": [
        {
            "id": "connection", "type": "action",
            "description": "BLE 连接管理",
            "actions": ["connect", "disconnect"],
            "endpoints": {
                "connect": {"method": "POST", "path": "/api/connect"},
                "disconnect": {"method": "POST", "path": "/api/disconnect"},
            },
        },
        {
            "id": "scene_mode", "type": "enum", "piid": 5,
            "description": "充电场景模式",
            "allowed": [
                {"value": 1, "label": "AI"},
                {"value": 2, "label": "数码生态"},
                {"value": 3, "label": "单口 (Single)"},
                {"value": 4, "label": "均衡 (Balance)"},
            ],
            "endpoint": {"method": "POST", "path": "/api/set", "body": {"piid": 5, "value": "<int>"}},
        },
        {
            "id": "screen_timeout", "type": "enum", "piid": 6,
            "description": "屏幕自动息屏时间",
            "allowed": [
                {"value": 5, "label": "1分钟"},
                {"value": 1, "label": "5分钟"},
                {"value": 2, "label": "10分钟"},
                {"value": 3, "label": "30分钟"},
                {"value": 4, "label": "常亮 (Always on)"},
            ],
            "endpoint": {"method": "POST", "path": "/api/set", "body": {"piid": 6, "value": "<int>"}},
        },
        {
            "id": "language", "type": "enum", "piid": 13,
            "description": "设备显示语言",
            "allowed": [{"value": 0, "label": "English"}, {"value": 1, "label": "中文"}],
            "endpoint": {"method": "POST", "path": "/api/set", "body": {"piid": 13, "value": "<int>"}},
        },
        {
            "id": "usb_a_always_on", "type": "boolean", "piid": 15,
            "description": "USB-A 小电流模式（约4小时自动关闭，适合手环/耳机等）",
            "allowed": [{"value": 0, "label": "关闭"}, {"value": 1, "label": "开启"}],
            "endpoint": {"method": "POST", "path": "/api/set", "body": {"piid": 15, "value": "<0|1>"}},
        },
        {
            "id": "idle_screen_off", "type": "boolean", "piid": 19,
            "description": "空闲时自动息屏",
            "allowed": [{"value": 0, "label": "关闭"}, {"value": 1, "label": "开启"}],
            "endpoint": {"method": "POST", "path": "/api/set", "body": {"piid": 19, "value": "<0|1>"}},
        },
        {
            "id": "screen_orientation_lock", "type": "boolean", "piid": 20,
            "description": "锁定屏幕方向",
            "allowed": [{"value": 0, "label": "关闭"}, {"value": 1, "label": "开启"}],
            "endpoint": {"method": "POST", "path": "/api/set", "body": {"piid": 20, "value": "<0|1>"}},
        },
        {
            "id": "port_control", "type": "action",
            "description": "开关充电端口 (C1/C2/C3/USB-A/全部)",
            "params": {"port": "c1|c2|c3|a|all", "action": "on|off"},
            "endpoint": {"method": "POST", "path": "/api/port", "body": {"port": "<str>", "action": "<on|off>"}},
        },
        {
            "id": "timer", "type": "action",
            "description": "设置端口定时关闭（分钟, 0=取消）",
            "params": {"port": "c1|c2|c3|a", "minutes": "0-1440"},
            "endpoint": {"method": "POST", "path": "/api/timer", "body": {"port": "<str>", "minutes": "<int>"}},
        },
    ],
    "queries": [
        {
            "id": "status", "description": "获取设备连接状态、所有设置和端口功率数据",
            "endpoint": {"method": "GET", "path": "/api/status"},
        },
        {
            "id": "capabilities", "description": "获取设备能力声明（本接口）",
            "endpoint": {"method": "GET", "path": "/api/capabilities"},
        },
    ],
}


@web.middleware
async def api_key_middleware(request, handler):
    """API Key 认证中间件。仅在 CUKTECH_API_KEY 环境变量设置后启用。

    认证方式（满足任一即可）:
    1. X-API-Key header — 供 OpenClaw / curl 等外部调用
    2. cuktech_session cookie — 浏览器访问首页时自动获得

    不设置 CUKTECH_API_KEY 环境变量时，所有请求直接放行。
    """
    path = request.path

    # 未配置 API Key → 全部放行
    if not API_KEY:
        return await handler(request)

    # 首页: 放行并种 cookie，浏览器后续请求自动带上
    if path == "/":
        resp = await handler(request)
        resp.set_cookie(
            "cuktech_session", SESSION_TOKEN,
            httponly=True, samesite="Strict",
        )
        return resp

    # WebSocket 也检查 cookie（浏览器会自动带）
    if path == "/ws":
        cookie = request.cookies.get("cuktech_session", "")
        if cookie and secrets.compare_digest(cookie, SESSION_TOKEN):
            return await handler(request)
        return web.json_response(
            {"ok": False, "error": "未认证，请先在浏览器打开首页"}, status=401,
        )

    # /api/* 路由: 接受 header 或 cookie
    if path.startswith("/api/"):
        api_key = request.headers.get("X-API-Key", "")
        cookie = request.cookies.get("cuktech_session", "")

        key_ok = api_key and secrets.compare_digest(api_key, API_KEY)
        cookie_ok = cookie and secrets.compare_digest(cookie, SESSION_TOKEN)

        if not (key_ok or cookie_ok):
            return web.json_response(
                {"ok": False, "error": "需要认证。浏览器请先访问首页；API 调用请提供 X-API-Key header"},
                status=401,
            )

    return await handler(request)


class BLEConnectionManager:
    """管理 BLE 连接生命周期，串行化命令执行。"""

    def __init__(self):
        self.ctrl = None
        self.state = "DISCONNECTED"
        self._cmd_lock = None  # 延迟到事件循环启动后创建
        self._ws_clients = set()
        self._cached_settings = {}
        self._port_data = {1: None, 2: None, 3: None, 4: None}
        self._drain_task = None
        self._debug_port_push = DEBUG_PORT_PUSH
        self._history_lock = None
        self._history_dir = DATA_DIR / "history"
        self._db_path = self._history_dir / "cuktech_history.db"

    async def async_init(self):
        """在 aiohttp 事件循环启动后初始化异步原语（Python 3.9 兼容）。"""
        self._cmd_lock = asyncio.Lock()
        self._history_lock = asyncio.Lock()
        self._history_dir.mkdir(parents=True, exist_ok=True)
        await self._init_history_db()

    async def _init_history_db(self):
        """初始化本地历史数据库。"""
        async with self._history_lock:
            conn = sqlite3.connect(str(self._db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        day TEXT NOT NULL,
                        port TEXT NOT NULL,
                        voltage REAL NOT NULL,
                        current REAL NOT NULL,
                        power REAL NOT NULL,
                        active INTEGER NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_day_port_ts ON telemetry(day, port, ts)")
                conn.commit()
            finally:
                conn.close()

    async def list_history_dates(self):
        """列出已有历史数据日期（YYYY-MM-DD）。"""
        dates = []
        async with self._history_lock:
            conn = sqlite3.connect(str(self._db_path))
            try:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT day FROM telemetry ORDER BY day DESC")
                dates = [row[0] for row in cur.fetchall()]
            finally:
                conn.close()
        today = datetime.now().strftime("%Y-%m-%d")
        if today not in dates:
            dates.insert(0, today)
        return dates

    async def get_history(self, date_str, port, granularity="second"):
        """读取指定端口的历史采样，支持按时间粒度聚合。"""
        records = []
        granularity = (granularity or "second").lower()
        bucket_expr_map = {
            "second": "ts",
            "minute": "substr(ts, 1, 16)",
            "hour": "substr(ts, 1, 13)",
            "day": "day",
            "month": "substr(day, 1, 7)",
            "year": "substr(day, 1, 4)",
        }
        bucket_expr = bucket_expr_map.get(granularity, "ts")

        fine_granularity = granularity in ("second", "minute", "hour")
        async with self._history_lock:
            conn = sqlite3.connect(str(self._db_path))
            try:
                cur = conn.cursor()
                if fine_granularity:
                    cur.execute(
                        f"""
                        SELECT {bucket_expr} AS bucket, port,
                               AVG(voltage) AS voltage,
                               AVG(current) AS current,
                               AVG(power) AS power,
                               MAX(active) AS active
                        FROM telemetry
                        WHERE day = ? AND port = ?
                        GROUP BY bucket, port
                        ORDER BY bucket ASC
                        """,
                        (date_str, port),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT {bucket_expr} AS bucket, port,
                               AVG(voltage) AS voltage,
                               AVG(current) AS current,
                               AVG(power) AS power,
                               MAX(active) AS active
                        FROM telemetry
                        WHERE port = ?
                        GROUP BY bucket, port
                        ORDER BY bucket ASC
                        """,
                        (port,),
                    )

                for bucket, p, voltage, current, power, active in cur.fetchall():
                    records.append({
                        "ts": bucket,
                        "port": p,
                        "voltage": float(voltage),
                        "current": float(current),
                        "power": float(power),
                        "active": bool(active),
                    })
            finally:
                conn.close()
        return records

    async def _append_history_sample(self, port_name, info):
        """追加写入历史采样（SQLite）。"""
        if not info:
            return
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        ts = now.isoformat(timespec="seconds")

        voltage = float(info.get("voltage", 0) or 0)
        current = float(info.get("current", 0) or 0)
        power = float(info.get("power", 0) or 0)
        active = 1 if bool(info.get("active", False)) else 0

        async with self._history_lock:
            conn = sqlite3.connect(str(self._db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO telemetry(ts, day, port, voltage, current, power, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, date_str, port_name, voltage, current, power, active),
                )
                conn.commit()
            finally:
                conn.close()

    async def broadcast(self, msg_type, data):
        """向所有 WebSocket 客户端广播消息。"""
        msg = json.dumps({"type": msg_type, **data}, ensure_ascii=False)
        closed = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(msg)
            except (ConnectionResetError, ConnectionError):
                closed.add(ws)
        self._ws_clients -= closed

    async def _set_state(self, new_state):
        self.state = new_state
        await self.broadcast("state", {"state": new_state})

    async def connect_and_auth(self):
        """连接设备并完成认证。"""
        if self.state != "DISCONNECTED":
            return {"ok": False, "error": f"当前状态: {self.state}，请先断开"}

        try:
            await self._set_state("CONNECTING")
            self.ctrl = CuktechBLEController()
            await self.ctrl.connect()

            await self._set_state("AUTHENTICATING")
            result = await self.ctrl.authenticate()
            if not result:
                await self.ctrl.disconnect()
                self.ctrl = None
                await self._set_state("DISCONNECTED")
                return {"ok": False, "error": "认证失败，请检查 Token"}

            await self._set_state("READY")

            # 启动后台 push drain
            self._drain_task = asyncio.create_task(self._background_drain())

            # 读取所有设置
            await self._refresh_all_settings()

            # 主动读一遍四个口，否则重连后要等设备推一次变化才有读数
            await self._read_all_ports()

            return {
                "ok": True, "state": "READY",
                "settings": self._cached_settings,
            }
        except Exception as e:
            if self.ctrl:
                try:
                    await self.ctrl.disconnect()
                except Exception:
                    pass
                self.ctrl = None
            await self._set_state("DISCONNECTED")
            return {"ok": False, "error": str(e)}

    async def do_disconnect(self):
        """断开 BLE 连接。"""
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

        if self.ctrl:
            try:
                await self.ctrl.disconnect()
            except Exception:
                pass
            self.ctrl = None

        self._cached_settings = {}
        await self._set_state("DISCONNECTED")
        return {"ok": True}

    async def execute_command(self, siid, piid, value=None):
        """执行一条 BLE 命令（串行化）。"""
        if self.state != "READY":
            return {"ok": False, "error": "设备未连接"}

        try:
            async with self._cmd_lock:
                result = await asyncio.wait_for(
                    self.ctrl.send_miot_command(siid, piid, value),
                    timeout=15,
                )
        except asyncio.TimeoutError:
            return {"ok": False, "error": "命令超时，设备可能繁忙"}
        except Exception as e:
            # BLE 断连
            await self.do_disconnect()
            return {"ok": False, "error": f"BLE 错误: {e}"}

        if result is None:
            return {"ok": False, "error": "设备无响应"}

        # 更新缓存
        if result.get("value") is not None:
            self._cached_settings[str(piid)] = result["value"]
        elif value is not None:
            # ACK-only 响应: 设备已确认，用发送的值更新缓存
            self._cached_settings[str(piid)] = value

        return {"ok": True, "piid": piid, "value": result.get("value") if result.get("value") is not None else value}

    async def _refresh_all_settings(self):
        """读取所有可读属性到缓存（持锁一次批量读取）。"""
        async with self._cmd_lock:
            for piid in READABLE_PIIDS:
                # 关键状态做轻量重试，避免偶发无响应导致 UI 显示错误
                retries = 2 if piid in (6, 13, 15, 16, 19, 20) else 1
                for _ in range(retries):
                    try:
                        result = await self.ctrl.send_miot_command(SIID_CHARGER, piid)
                        if result and result.get("value") is not None:
                            self._cached_settings[str(piid)] = result["value"]
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.15)

        await self.broadcast("settings", {"settings": self._cached_settings})

    async def _read_all_ports(self):
        """主动读一遍四个口的端口数据。

        设备只在数值发生变化时才推送，所以重连后若没人插拔，面板会一直显示为空，
        直到电流/电压真的变一次。连接成功和手动刷新时各补一次主动 GET。
        """
        if not self.ctrl:
            return
        async with self._cmd_lock:
            for piid in (1, 2, 3, 4):
                try:
                    result = await self.ctrl.send_miot_command(SIID_CHARGER, piid)
                except Exception:
                    continue
                raw = (result or {}).get("raw")
                if not raw:
                    continue
                try:
                    self._decode_port_push(piid, bytes(raw))
                except Exception:
                    pass
                await asyncio.sleep(0.1)

    def _decode_port_push(self, piid, plaintext):
        """解码端口充电推送数据。

        实测规则（2026-11-05 四轮抓包复核）：
        - 四个口统一：b0 = 有无输出，b2 = 电流（0.1A），b3 = 电压（0.1V）
        - A 口旧规则「电压在 b1（0.05V）」已废止：A 口空载时 b1 在 0x30/0x60/0x70
          之间跳，会凭空显示 2.4~4.8V（而 b2/b3 恒为 0、电流恒 0A）。
          现改为统一取 b3，仅在 b3 为 0 且确实有电流时才用 b1 兜底。
        - 活跃判定统一使用电压/电流是否非零
        """
        if len(plaintext) < 12:
            return

        # 端口值统一取最后 4 字节，避免不同 type 编码位置差异导致漏解析
        b = plaintext[-4:]

        if self._debug_port_push:
            self._debug_dump_port_candidates(piid, plaintext, b)

        current = b[2] / 10.0
        voltage = b[3] / 10.0
        if voltage <= 0 and piid == 4 and current > 0:
            # A 口兜底：万一这口只把电压放在 b1（0.05V）。空载时 b1 是未初始化值，
            # 所以必须卡住 current > 0，否则又会变出假的 2.4~4.8V
            voltage = b[1] / 20.0
        active = (voltage > 0) or (current > 0)

        power = voltage * current

        port_info = {
            "voltage": round(voltage, 1),
            "current": round(current, 1),
            "power": round(power, 1),
            "active": active,
        }

        prev = self._port_data.get(piid)
        self._port_data[piid] = port_info

        # 只在数据变化时广播
        if prev != port_info:
            port_name = {1: "c1", 2: "c2", 3: "c3", 4: "a"}.get(piid, str(piid))
            asyncio.create_task(
                self.broadcast("port", {"port": port_name, "piid": piid, **port_info})
            )
            asyncio.create_task(self._append_history_sample(port_name, port_info))

            # 记录总功率历史（便于后续按日期查看总功率曲线）
            total_power = 0.0
            for info in self._port_data.values():
                if info and info.get("active") and (info.get("power") or 0) > 0:
                    total_power += float(info.get("power") or 0)
            asyncio.create_task(
                self._append_history_sample("total", {
                    "voltage": 0,
                    "current": 0,
                    "power": round(total_power, 2),
                    "active": total_power > 0,
                })
            )

    def _debug_dump_port_candidates(self, piid, plaintext, b):
        """打印端口推送原始数据和候选解码结果，便于按实测值反推协议。"""
        try:
            b0, b1, b2, b3 = b[0], b[1], b[2], b[3]
            p16_hi = (b0 << 8) | b1
            p16_lo = (b2 << 8) | b3

            cand = {
                "tail_b": [b0, b1, b2, b3],
                "tail_hex": b.hex(),
                "plain_len": len(plaintext),
                "plain_hex": plaintext.hex(),
                "active_flag": b3,
                "a_rule": {
                    "v": round(b1 / 20.0, 2),
                    "i": round(b2 / 10.0, 2),
                    "p": round((b1 / 20.0) * (b2 / 10.0), 2),
                },
                "c_rule": {
                    "v": round(b1 / 10.0, 2),
                    "i": round(b2 / 10.0, 2),
                    "p": round((b1 / 10.0) * (b2 / 10.0), 2),
                },
                "u16_rule": {
                    "v": round(p16_hi / 100.0, 2),
                    "i": round(p16_lo / 100.0, 2),
                    "p": round((p16_hi / 100.0) * (p16_lo / 100.0), 2),
                },
            }
            port_name = {1: "C1", 2: "C2", 3: "C3", 4: "A"}.get(piid, str(piid))
            print(f"[DBG][PORT {port_name}] {json.dumps(cand, ensure_ascii=False)}")
        except Exception as e:
            print(f"[DBG][PORT] 解析候选输出失败: {e}")

    async def _background_drain(self):
        """后台消费设备推送通知，解码端口数据并广播。

        关键：持锁读队列，防止与 send_miot_command 竞争同一个 asyncio.Queue。
        锁超时 0.15s，保证命令不会等太久。
        """
        q = self.ctrl._notify_queues.get("cmd_recv")
        if not q:
            return

        while True:
            try:
                async with self._cmd_lock:
                    try:
                        data = await asyncio.wait_for(q.get(), timeout=0.15)
                    except asyncio.TimeoutError:
                        continue  # 释放锁，让命令有机会执行

                    if not data or len(data) < 4:
                        continue

                    # ACK 内联推送
                    if data[2] == 0x02 and len(data) >= 4:
                        encrypted_payload = data[4:]
                        try:
                            await self.ctrl.client.write_gatt_char(
                                CHAR_CMD_RECV,
                                bytes([0x00, 0x00, 0x03, 0x00]),
                                response=False,
                            )
                        except Exception:
                            pass

                        # 解码推送数据
                        if encrypted_payload:
                            try:
                                pt = self.ctrl._decrypt(encrypted_payload)
                                if pt and len(pt) >= 12:
                                    # 属性通知: opcode=0x04, siid=2, piid=1..4
                                    if pt[4] == 0x04 and pt[6] == SIID_CHARGER:
                                        piid = pt[7]
                                        if 1 <= piid <= 4:
                                            self._decode_port_push(piid, pt)
                            except Exception:
                                pass

                    # ACK 多帧推送
                    elif data[2] == 0x00 and len(data) >= 6:
                        frame_count = data[4] + 0x100 * data[5]
                        try:
                            await self.ctrl.client.write_gatt_char(
                                CHAR_CMD_RECV,
                                bytes([0x00, 0x00, 0x01, 0x01]),
                                response=False,
                            )
                            for _ in range(frame_count):
                                try:
                                    await asyncio.wait_for(q.get(), timeout=2.0)
                                except asyncio.TimeoutError:
                                    break
                            await self.ctrl.client.write_gatt_char(
                                CHAR_CMD_RECV,
                                bytes([0x00, 0x00, 0x01, 0x00]),
                                response=False,
                            )
                        except Exception:
                            pass

            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(0.5)


# ============================================================
# HTTP 路由
# ============================================================

async def handle_index(request):
    return web.FileResponse(WEB_INDEX)


async def handle_connect(request):
    mgr = request.app["ble"]
    result = await mgr.connect_and_auth()
    return web.json_response(result)


async def handle_disconnect(request):
    mgr = request.app["ble"]
    result = await mgr.do_disconnect()
    return web.json_response(result)


async def handle_status(request):
    mgr = request.app["ble"]
    return web.json_response({
        "state": mgr.state,
        "settings": mgr._cached_settings,
        "ports": mgr._port_data,
        "meta": {str(k): v for k, v in SETTINGS_META.items()},
    })


async def handle_capabilities(request):
    """返回设备能力声明，供 OpenClaw 等 Agent 自动发现可用控件。"""
    mgr = request.app["ble"]
    return web.json_response({
        **CAPABILITIES,
        "state": mgr.state,
        "base_url": f"http://{request.host}",
    })


async def handle_set(request):
    mgr = request.app["ble"]
    body = await request.json()
    piid = body.get("piid")
    value = body.get("value")
    if piid is None or value is None:
        return web.json_response({"ok": False, "error": "需要 piid 和 value"}, status=400)

    piid_int = int(piid)
    value_int = int(value)
    result = await mgr.execute_command(SIID_CHARGER, piid_int, value_int)

    if result["ok"]:
        # 关键可读属性写入后立即读回，避免缓存与设备实际值不一致
        if piid_int in READABLE_PIIDS:
            readback = await mgr.execute_command(SIID_CHARGER, piid_int)
            if readback.get("ok") and readback.get("value") is not None:
                mgr._cached_settings[str(piid_int)] = readback["value"]
        await mgr.broadcast("settings", {"settings": mgr._cached_settings})

    return web.json_response(result)


async def handle_port(request):
    mgr = request.app["ble"]
    body = await request.json()
    port = body.get("port", "").lower()
    action = body.get("action", "").lower()

    if action not in ("on", "off"):
        return web.json_response({"ok": False, "error": "action 必须是 on/off"}, status=400)

    # 读取当前端口状态
    cur = await mgr.execute_command(SIID_CHARGER, 16)
    if not cur["ok"]:
        return web.json_response(cur)

    cur_val = cur.get("value", 0x0F) or 0x0F

    if port == "all":
        new_val = 0x0F if action == "on" else 0x00
    elif port in PORT_BITS:
        bit = PORT_BITS[port]
        if action == "on":
            new_val = cur_val | (1 << bit)
        else:
            new_val = cur_val & ~(1 << bit)
    else:
        return web.json_response(
            {"ok": False, "error": f"未知端口: {port}，可选: c1/c2/c3/a/all"}, status=400)

    result = await mgr.execute_command(SIID_CHARGER, 16, new_val)

    if result["ok"]:
        await mgr.broadcast("settings", {"settings": mgr._cached_settings})

        # 关闭端口时立即清零功率数据
        if action == "off":
            port_map = {"c1": 1, "c2": 2, "c3": 3, "a": 4}
            zero = {"voltage": 0, "current": 0, "power": 0, "active": False}
            ports_to_clear = port_map if port == "all" else {port: port_map[port]} if port in port_map else {}
            for pname, ppiid in ports_to_clear.items():
                mgr._port_data[ppiid] = zero
                await mgr.broadcast("port", {"port": pname, "piid": ppiid, **zero})

    return web.json_response(result)


async def handle_timer(request):
    mgr = request.app["ble"]
    body = await request.json()
    port = body.get("port", "").lower()
    minutes = body.get("minutes", 0)

    if port not in TIMER_PORTS:
        return web.json_response(
            {"ok": False, "error": f"未知端口: {port}，可选: c1/c2/c3/a"}, status=400)

    piid = TIMER_PORTS[port]
    result = await mgr.execute_command(SIID_CHARGER, piid, int(minutes))
    return web.json_response(result)


async def handle_refresh(request):
    """强制重新从设备读取所有设置。"""
    mgr = request.app["ble"]
    if mgr.state != "READY":
        return web.json_response({"ok": False, "error": "设备未连接"})

    await mgr._refresh_all_settings()
    await mgr._read_all_ports()
    return web.json_response({"ok": True, "settings": mgr._cached_settings})


async def handle_history_dates(request):
    """返回历史数据日期列表。"""
    mgr = request.app["ble"]
    dates = await mgr.list_history_dates()
    return web.json_response({"ok": True, "dates": dates})


async def handle_history(request):
    """按日期与端口返回历史曲线数据。"""
    mgr = request.app["ble"]
    date_str = request.query.get("date", "").strip()
    port = request.query.get("port", "c1").strip().lower()
    granularity = request.query.get("granularity", "second").strip().lower()

    allowed_granularity = ("second", "minute", "hour", "day", "month", "year")
    if granularity not in allowed_granularity:
        return web.json_response({"ok": False, "error": "granularity 参数应为 second/minute/hour/day/month/year"}, status=400)

    if granularity in ("second", "minute", "hour"):
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        if len(date_str) != 10:
            return web.json_response({"ok": False, "error": "date 参数格式应为 YYYY-MM-DD"}, status=400)
    else:
        if not date_str:
            date_str = "all"

    if port not in ("c1", "c2", "c3", "a", "total"):
        return web.json_response({"ok": False, "error": "port 参数应为 c1/c2/c3/a/total"}, status=400)

    records = await mgr.get_history(date_str, port, granularity)
    return web.json_response({"ok": True, "date": date_str, "port": port, "granularity": granularity, "records": records})


async def handle_ws(request):
    """WebSocket 端点: 实时推送状态更新。"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    mgr = request.app["ble"]
    mgr._ws_clients.add(ws)

    # 发送当前状态
    await ws.send_str(json.dumps({
        "type": "state", "state": mgr.state,
    }, ensure_ascii=False))
    if mgr._cached_settings:
        await ws.send_str(json.dumps({
            "type": "settings", "settings": mgr._cached_settings,
        }, ensure_ascii=False))
    # 发送已有的端口数据
    for piid, info in mgr._port_data.items():
        if info:
            port_name = {1: "c1", 2: "c2", 3: "c3", 4: "a"}.get(piid, str(piid))
            await ws.send_str(json.dumps({
                "type": "port", "port": port_name, "piid": piid, **info,
            }, ensure_ascii=False))

    try:
        async for msg in ws:
            pass  # 不处理客户端消息
    finally:
        mgr._ws_clients.discard(ws)

    return ws


async def on_startup(app):
    """服务器启动时初始化 BLE 管理器（确保在正确的事件循环上）。"""
    await app["ble"].async_init()


async def on_shutdown(app):
    """服务器关闭时断开 BLE。"""
    mgr = app["ble"]
    await mgr.do_disconnect()
    for ws in set(mgr._ws_clients):
        await ws.close()


def create_app():
    app = web.Application(middlewares=[api_key_middleware])
    app["ble"] = BLEConnectionManager()

    app.router.add_get("/", handle_index)
    app.router.add_post("/api/connect", handle_connect)
    app.router.add_post("/api/disconnect", handle_disconnect)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/capabilities", handle_capabilities)
    app.router.add_post("/api/set", handle_set)
    app.router.add_post("/api/port", handle_port)
    app.router.add_post("/api/timer", handle_timer)
    app.router.add_post("/api/refresh", handle_refresh)
    app.router.add_get("/api/history/dates", handle_history_dates)
    app.router.add_get("/api/history", handle_history)
    app.router.add_get("/ws", handle_ws)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    print("=" * 50)
    print("  CUKTECH BLE 控制面板")
    print(f"  http://{HOST}:{PORT}")
    if API_KEY:
        print(f"  API Key 认证: 已启用")
    else:
        print("  API Key 认证: 未启用 (设置 CUKTECH_API_KEY 环境变量开启)")
    print("=" * 50)
    web.run_app(create_app(), host=HOST, port=PORT)
