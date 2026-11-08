"""开机自动开启充电口（时间窗与端口范围都可自定义，进程启动时做一次）。

这是「运行管理」里的一个自动化小功能，规则来自用户 2026-11-04 的需求：

  在某段时间内（默认 7:00–12:00，可在运行管理里改），软件启动时检查充电口，
  把「所选范围内」没开启的打开；已经开着的不用管。

触发时机（关键，别改成常驻定时器）
--------------------------------
只有一处：**管理器进程启动**时判断一次。不是 7:00 整点触发，也不是每 10 分钟轮询。

  | 开机/启动时间        | 行为                                      |
  |---------------------|-------------------------------------------|
  | 落在所设时间窗内      | 执行一次检查                              |
  | 时间窗之前 / 之后     | 不执行，只写一行日志                      |
  | 同一天已开过口        | 重启也不重复（避免反复发 BLE 写命令）      |
  | 同一天检查过但没动手  | **不算数**，当天再启动还会再查一次        |

可配置项（存 `data/autoport.json`，默认值就是原来的 7:00–12:00 / 四口）
------------------------------------------------------------------
  {
    "enabled": true,
    "start":   "07:00",      # 时间窗开始（含）
    "end":     "12:00",      # 时间窗结束（含整分钟，见下面的边界说明）
    "ports":   "all"         # "all" = 全部充电口；或 "c1"/"c2"/"c3"/"a" 指定单口
  }

端口范围（用户 2026-11-04 追加要求）
----------------------------------
  - ``"all"``  → 检查 C1 / C2 / C3 / A 四个口，有未开启的就开，已开的不碰。
  - 单个端口    → **只看那一个口、只动那一个口**，其余三个口即使是关的也不碰。

「算不算今天做过了」跟着范围走：单口模式下，只要那个口是开着的就算「无需处理」，
不占当日名额（和四口全开时的判定一致）。

「算不算一次」的判据（用户 2026-11-04 明确要求）
----------------------------------------------
不是「连上设备就算」，而是 **本次真的开过口** 才算：

  - 检查时所选端口本来就全开 → 什么都不用做 → **不占当日名额**。
    这样「上午全开被记成已完成、下午你手动关了一个」的坑就不存在了：
    当天再启动程序，它还会自己查一次并补开。
  - 本次成功补开了至少一个口、复核到所选端口全开 → 占名额，当天不再自动跑。
  - 失败（读不到位图 / 等不到 READY / 复核后仍有未开）→ 不占名额，重启重试。

代价：端口一直全开时，每次启动都会多读一次位图（`/api/refresh` 一次 BLE 读），
换来的是「当天任何时候关了口，重启就能自愈」。这是有意的取舍。

时间边界（含开始与结束）
----------------------
窗口是「左闭右开」，结束时间按**整分钟含进去**处理：``start <= now < end + 1 分钟``
  - 默认 07:00–12:00 → 实际判定为 07:00:00 <= now < 12:01:00；
  - 12:00 这一整分钟（12:00:00–12:00:59）**仍算在窗口内**；
  - 12:01:00 起不再触发。
比「7 点到 12 点」直观更宽一分钟，是为了不出现「12:00 整开机反而不触发」这种别扭情况。
**这条规则没有因为可配置而改变**，只是把 7 / 12 换成了配置值。

检测范围
--------
只看 piid=16 这个**端口使能位图**（bit0=C1, bit1=C2, bit2=C3, bit3=A，0x0F=四口全开）。
它不是「有没有在充电」：`ports[*].active` 表示端口上有负载，跟「端口开没开」是两件事，
别混用（关掉的端口 active 也为 False，拿它判断会把四个口全当关闭）。

为什么不用 /api/port?port=all&action=on 一把梭
--------------------------------------------
需求是「存在的未开启的才开」，逐口控制才能准确记下「哪几个原本是关的」，
日志和运行管理里的提示才有意义；且 handle_port 每次都会先读当前位图再 OR，
逐口下发不会误关别人。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import paths

API = "http://127.0.0.1:8080"

# 默认时间窗（也是配置文件缺失/损坏时的兜底）。改默认值只改这里，别在别处硬编码 7 / 12。
DEFAULT_START = "07:00"
DEFAULT_END = "12:00"
DEFAULT_PORTS = "all"

# (api 里的端口名, 界面名字, 位图里的位)
PORTS = (("c1", "C1", 0), ("c2", "C2", 1), ("c3", "C3", 2), ("a", "A", 3))
PORT_NAMES = {p: name for p, name, _bit in PORTS}

WAIT_READY_SECONDS = 180.0   # 开机后 BLE 连接慢，自动触发时最多等 3 分钟
WAIT_READY_MANUAL = 30.0     # 手动点「立即」最多等 30 秒，别让人盯着按钮转 3 分钟
POLL_SECONDS = 3.0

STATE_FILE = paths.data_dir() / "auto_port.json"
CONF_FILE = paths.data_dir() / "autoport.json"   # 开关 + 时间窗 + 端口范围，别和运行状态混一个文件

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCK = threading.Lock()
_RUN = threading.Lock()          # 自动 / 手动执行串行，避免同一时刻发两串命令
_LOG = None

_STATE = {
    "enabled": True,
    "window": "07:00–12:00",
    "scope": "全部充电口",
    "inWindow": False,
    "phase": "idle",             # idle / waiting / running / done / skipped / error / disabled
    "date": "",
    "at": "",
    "opened": [],                # 本次被自动打开的端口，如 ["C3"]
    "mask": None,                # 执行后的位图
    "message": "等待启动检查",
}


# ---------------------------------------------------------------- 基础设施


def set_logger(fn) -> None:
    """管理器把 log() 注进来——autoport 不 import manager，否则循环依赖。"""
    global _LOG
    _LOG = fn


def log(msg: str) -> None:
    if _LOG:
        try:
            _LOG(msg)
            return
        except Exception:  # noqa: BLE001
            pass
    print(msg, flush=True)


def _set(**kw) -> None:
    with _LOCK:
        _STATE.update(kw)


def snapshot() -> dict:
    cfg = conf()
    with _LOCK:
        d = dict(_STATE)
    d["enabled"] = cfg["enabled"]
    d["start"] = cfg["start"]
    d["end"] = cfg["end"]
    d["ports"] = cfg["ports"]
    d["window"] = window_label(cfg)
    d["scope"] = scope_label(cfg["ports"])
    d["inWindow"] = in_window()
    return d


# ---------------------------------------------------------------- 配置


def _parse_hm(value) -> str | None:
    """把 "7:00" / "07:00" / "07:00:00" 归一成 "HH:MM"；不合法返回 None。"""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def _normalize_scope(value) -> str:
    """端口范围归一：'all' 或单个端口 key（'c1'/'c2'/'c3'/'a'）。

    兼容三种写法，避免旧配置/手改配置炸掉：
      "all" / "c3"  /  ["c1","c2","c3","a"]（全给=all，只给一个=那个口）
    """
    if isinstance(value, (list, tuple)):
        picked = [str(v).strip().lower() for v in value]
        picked = [v for v in picked if v in PORT_NAMES]
        if len(picked) >= len(PORTS):
            return "all"
        return picked[0] if picked else DEFAULT_PORTS
    if isinstance(value, str):
        v = value.strip().lower()
        if v in PORT_NAMES:
            return v
    return DEFAULT_PORTS


def load_conf() -> dict:
    enabled, start, end, ports = True, DEFAULT_START, DEFAULT_END, DEFAULT_PORTS
    try:
        raw = json.loads(CONF_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if "enabled" in raw:
                enabled = bool(raw["enabled"])
            start = _parse_hm(raw.get("start")) or DEFAULT_START
            end = _parse_hm(raw.get("end")) or DEFAULT_END
            ports = _normalize_scope(raw.get("ports"))
    except Exception:  # noqa: BLE001
        pass
    return {"enabled": enabled, "start": start, "end": end, "ports": ports}


def conf() -> dict:
    """每次都从磁盘读，不缓存。

    为什么放弃缓存（v1.6.0）：这个配置文件现在有**两个进程**会写——管理器改设置，
    采集服务响应网页的 `/api/autoport`。缓存住就变成「网页改了、管理器还按旧值
    执行」，界面上显示的开关和实际生效的对不上。文件不到 200 字节，一天读几千次
    的成本远小于这种不一致。
    """
    return load_conf()


def _write_conf(**changes) -> dict:
    """合并写回配置文件。**必须合并**：只写一个键会把别的设置冲掉。"""
    data = conf()
    data.update(changes)
    try:
        CONF_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONF_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log(f"[自动开端口] 保存设置失败 {exc!r}")
    return data


def load_enabled() -> bool:
    """开关默认开启——这是用户要的功能，装完就生效。"""
    return load_conf()["enabled"]


def is_enabled() -> bool:
    return conf()["enabled"]


def set_enabled(on: bool) -> dict:
    """浮层开关。关掉只是「启动时不再自动检查」，手动「立即」仍然可用。"""
    _write_conf(enabled=bool(on))

    if not on:
        _set(enabled=False, phase="disabled", message="已关闭，启动时不会自动检查")
        log("[自动开端口] 已关闭（启动时不再自动检查）")
        return snapshot()

    _set(enabled=True)
    saved = _load_saved()
    today = datetime.now().strftime("%Y-%m-%d")
    if saved.get("date") == today and saved.get("ok"):
        _set(phase="done", at=saved.get("at") or "", opened=list(saved.get("opened") or []),
             mask=saved.get("mask"),
             message=f"已开启（今日 {saved.get('at') or ''} 已自动开过口）")
    elif in_window():
        _set(phase="waiting", message="已开启，正在检查…")
        log("[自动开端口] 已开启，当前在时间窗内，立即补做一次检查")
        threading.Thread(target=lambda: _execute(force=False), daemon=True,
                         name="autoport-enable").start()
    else:
        _set(phase="idle", message=f"已开启，将在 {window_label()} 启动时自动检查")
    log("[自动开端口] 已开启")
    return snapshot()


def set_window(start: str, end: str) -> dict:
    """保存时间窗。只改「在什么时间范围内检查」，判定逻辑本身不动。

    结束时间必须不早于开始时间；相等表示只在那一个整分钟内检查。
    """
    s = _parse_hm(start)
    e = _parse_hm(end)
    if not s or not e:
        return {**snapshot(), "ok": False, "error": "时间格式不对，应该是 HH:MM"}
    if _minutes(e) < _minutes(s):
        return {**snapshot(), "ok": False, "error": "结束时间不能早于开始时间"}

    _write_conf(start=s, end=e)
    cfg = conf()
    log(f"[自动开端口] 时间窗改为 {window_label(cfg)}（{s}–{e}，含结束那一分钟）")
    # 改完立刻刷新 inWindow 与提示语，别让界面停在旧文案上
    _refresh_idle_message()
    return {**snapshot(), "ok": True}


def set_scope(value) -> dict:
    """保存端口范围："all" 或 "c1"/"c2"/"c3"/"a"。"""
    scope = _normalize_scope(value)
    _write_conf(ports=scope)
    log(f"[自动开端口] 端口范围改为 {scope_label(scope)}")
    _refresh_idle_message()
    return {**snapshot(), "ok": True}


def public_state() -> dict:
    """给网页 `/api/autoport` 读的配置快照。

    这个函数会在**采集服务进程**里被调用，所以刻意只做两件事：读磁盘配置、
    现算在不在时间窗内。**不碰 `_STATE`** —— 那里存的是「上次执行结果」，
    只属于管理器进程，服务进程读到的会是一份空壳，拿它显示只会误导。

    与 `snapshot()` 的分工：snapshot 给桌面浮层/托盘看（含执行进度），
    public_state 给网页看（只有配置）。两者共用同一份 autoport.json。
    """
    cfg = load_conf()
    return {
        "ok": True,
        "enabled": cfg["enabled"],
        "start": cfg["start"],
        "end": cfg["end"],
        "ports": cfg["ports"],
        "window": window_label(cfg),
        "scope": scope_label(cfg["ports"]),
        "inWindow": in_window(cfg=cfg),
    }


def save_settings(patch: dict) -> dict:
    """网页上改「自动启动」的设置：**只落盘**，不触发执行、不改 `_STATE`。

    为什么不复用 `set_enabled()`：那个函数在时间窗内会立刻起线程去开端口，
    它服务的是「桌面浮层里手动拨开关」这个场景，而且会改写本进程的 _STATE。
    网页这边只负责改配置，真正的执行仍然由管理器在启动时做——两个进程各管
    一件事，不会同时抢 BLE。

    返回最新的 public_state，前端拿到就能直接重绘，不用再拉一次。
    """
    if not isinstance(patch, dict):
        return {**public_state(), "ok": False, "error": "请求格式不对"}

    changes: dict = {}
    if "enabled" in patch:
        changes["enabled"] = bool(patch["enabled"])

    if "start" in patch or "end" in patch:
        cfg = conf()
        s = _parse_hm(patch.get("start", cfg["start"]))
        e = _parse_hm(patch.get("end", cfg["end"]))
        if not s or not e:
            return {**public_state(), "ok": False, "error": "时间格式不对，应该是 HH:MM"}
        if _minutes(e) < _minutes(s):
            return {**public_state(), "ok": False, "error": "结束时间不能早于开始时间"}
        changes["start"], changes["end"] = s, e

    if "ports" in patch:
        changes["ports"] = _normalize_scope(patch["ports"])

    if not changes:
        return public_state()

    _write_conf(**changes)
    log("[自动开端口] 设置已从控制台更新：" + "、".join(f"{k}={v}" for k, v in changes.items()))
    return public_state()


def _refresh_idle_message() -> None:
    """设置变化后，把「还没到点/已关闭」这类**静态提示**同步成新文案。

    只动 idle / skipped / disabled 这三种「等待型」状态：
      - waiting / running：正在执行，覆盖会把「正在开启…」冲掉；
      - done / error：这是上一次真跑出来的结果（如「今日 07:12 已自动开过口」），
        属于历史记录，不该因为改了设置就被抹成「将在…自动检查」。
    """
    with _LOCK:
        phase = _STATE.get("phase")
    if phase not in ("idle", "skipped", "disabled", None, ""):
        return
    if not is_enabled():
        _set(phase="disabled", message="已关闭，启动时不会自动检查")
    elif in_window():
        _set(phase="idle", message=f"当前在 {window_label()} 时段内，下次启动会检查")
    else:
        _set(phase="idle", message=f"已开启，将在 {window_label()} 启动时自动检查")


def _api(path: str, payload=None, timeout: float = 6.0):
    req = urllib.request.Request(API + path)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with _OPENER.open(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- 规则


def _minutes(hm: str) -> int:
    """"HH:MM" → 当天第几分钟。"""
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def _end_exclusive(hm: str) -> int:
    """结束时间按「整分钟含进去」处理，返回**不含**的上界（当天第几分钟）。

    12:00 → 12:01（即 12:00:00–12:00:59 都算窗口内）。这条就是原来
    WINDOW_END = 12:01 的规则，换成配置值后保持一模一样。
    23:59 + 1 分钟会越界到次日，这里封顶为 24:00（= 当天结束）。
    """
    return min(_minutes(hm) + 1, 24 * 60)


def window_label(cfg: dict | None = None) -> str:
    """给界面显示的时间窗文案，如 "07:00–12:00"。"""
    c = cfg or conf()
    return f"{c['start']}–{c['end']}"


def in_window(now: datetime | None = None, cfg: dict | None = None) -> bool:
    """是否落在所设时间窗内：start（含）– end+1 分钟（不含）。

    秒不单独处理：12:00:59 的 cur_min 仍是 720，落在窗口内；12:01:00 起 cur_min=721 出去。
    cfg 可显式传入，避免调用方已经读过一次配置还要再读一遍盘。
    """
    c = cfg or conf()
    t = (now or datetime.now()).time()
    cur_min = t.hour * 60 + t.minute
    start = _minutes(c["start"])
    end = _end_exclusive(c["end"])
    if start <= end:
        return start <= cur_min < end
    # 防御：配置里出现 end < start（手改文件）时，按「跨零点」理解，不至于整天失效
    return cur_min >= start or cur_min < end


def target_ports(cfg: dict | None = None) -> list[tuple[str, str, int]]:
    """本次要处理的端口（(api名, 界面名, 位) 三元组列表）。

    "all" → 四个口全给；单口 → 只给那一个。其余逻辑一律基于这个列表，
    这样「只动所选范围」这件事只有一处实现，不会漏。
    """
    scope = (cfg or conf())["ports"]
    if scope == "all":
        return list(PORTS)
    picked = tuple(x for x in PORTS if x[0] == scope)
    return list(picked) if picked else list(PORTS)


def scope_label(scope: str | None = None) -> str:
    """给界面显示的端口范围文案："全部充电口" / "C3"。"""
    s = scope if scope is not None else conf()["ports"]
    if s == "all":
        return "全部充电口"
    return PORT_NAMES.get(s, "全部充电口")


def closed_ports(mask: int, ports: list[tuple[str, str, int]] | None = None) -> list[str]:
    """位图里没开启的端口名（界面名，如 ["C3"]）。只统计**所选范围**内的口。"""
    picked = ports if ports is not None else target_ports()
    return [name for _p, name, bit in picked if not (mask >> bit) & 1]


def _ports_mask(ports: list[tuple[str, str, int]]) -> int:
    """所选端口对应的位图掩码（全部模式就是 0x0F）。"""
    m = 0
    for _p, _name, bit in ports:
        m |= 1 << bit
    return m


def _mask_text(mask) -> str:
    if mask is None:
        return "未知"
    return f"0x{int(mask) & 0xFF:02X}"


def read_mask(timeout: float = 25.0):
    """读回 piid=16 位图。先让服务重新读设备（/api/refresh），失败再退回缓存。

    为什么要 refresh：启动时 `_cached_settings` 可能还是空的或上次的值，
    拿缓存判断可能把「已开」看成「未开」。
    """
    try:
        r = _api("/api/refresh", {}, timeout=timeout)
        s = r.get("settings") if isinstance(r, dict) else None
        if s and s.get("16") is not None:
            return int(s["16"])
    except Exception:  # noqa: BLE001
        pass
    try:
        st = _api("/api/status", timeout=4.0) or {}
        v = (st.get("settings") or {}).get("16")
        if v is not None:
            return int(v)
    except Exception:  # noqa: BLE001
        pass
    return None


def _wait_ready(deadline: float, respect_window: bool = True) -> bool:
    """等采集服务连上充电器。

    respect_window=True（自动触发）时，一旦超出时间窗立即放弃——开机后拖到 12:01
    才连上就没必要再开端口了；手动「立即」不受这个限制。
    """
    while time.monotonic() < deadline:
        if respect_window and not in_window():
            return False
        try:
            st = _api("/api/status", timeout=3.0) or {}
            if st.get("state") == "READY":
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(POLL_SECONDS)
    return False


def _load_saved() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_saved(d: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log(f"[自动开端口] 写状态失败 {exc!r}")


# ---------------------------------------------------------------- 执行


def _execute(force: bool = False) -> dict:
    if not _RUN.acquire(blocking=False):
        log("[自动开端口] 已有一次检查在执行，忽略本次请求")
        return snapshot()
    try:
        return _execute_locked(force)
    finally:
        _RUN.release()


def _execute_locked(force: bool) -> dict:
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    saved = _load_saved()
    targets = target_ports()
    scope_txt = scope_label()
    window_txt = window_label()
    scope_all = len(targets) == len(PORTS)

    if not force and not is_enabled():
        _set(enabled=False, phase="disabled", message="已关闭，启动时不会自动检查")
        log("[自动开端口] 开关是关的，跳过")
        return snapshot()

    if not force:
        if not in_window(now):
            msg = f"不在 {window_txt} 时段（现在 {now:%H:%M:%S}），本次不执行"
            _set(phase="skipped", inWindow=False, message=msg)
            log(f"[自动开端口] {msg}")
            return snapshot()
        if saved.get("date") == day and saved.get("ok"):
            at = saved.get("at") or ""
            _set(phase="done", date=day, at=at, opened=list(saved.get("opened") or []),
                 mask=saved.get("mask"),
                 message=f"今日 {at} 已自动开过口，不重复执行")
            log(f"[自动开端口] {_STATE['message']}")
            return snapshot()

    log(f"[自动开端口] 触发：{now:%H:%M:%S}（窗口 {window_txt}，含两端；范围 {scope_txt}"
        f"{'，手动强制' if force else ''}）")

    _set(phase="waiting", inWindow=in_window(), date=day, at=now.strftime("%H:%M:%S"),
         opened=[], mask=None, message="等待采集服务连接充电器…")

    wait = WAIT_READY_MANUAL if force else WAIT_READY_SECONDS
    if not _wait_ready(time.monotonic() + wait, respect_window=not force):
        if not force and not in_window():
            msg = f"等待期间已过 {window_txt} 的结束时间，本次放弃"
        elif force:
            msg = "设备还没连上（服务不在 READY），稍等几秒再点「立即」"
        else:
            msg = "采集服务 3 分钟内未连上充电器，本次未执行（请看 manager.log 里的 BLE 诊断）"
        _set(phase="error", message=msg)
        log(f"[自动开端口] {msg}")
        return snapshot()

    _set(phase="running", message="正在读取端口状态…")
    mask = read_mask()
    if mask is None:
        msg = "读不到端口状态（piid 16），本次未执行"
        _set(phase="error", message=msg)
        log(f"[自动开端口] {msg}")
        return snapshot()

    closed = closed_ports(mask, targets)
    at = datetime.now().strftime("%H:%M:%S")
    if not closed:
        # 所选端口本来就全开：什么都没动，**不写 ok=True**，不占当日名额。
        # 只留一条 checked 记录，界面上能看到「上次检查是什么时候」。
        done_txt = "四口均已开启" if scope_all else f"{scope_txt} 已开启"
        msg = f"今日 {at} 自动检查：{done_txt}（{_mask_text(mask)}），无需处理，不占今日名额"
        _set(phase="done", mask=mask, opened=[], at=at, message=msg)
        _save_saved({"date": day, "at": at, "ok": False, "checked": True,
                     "opened": [], "mask": mask})
        log(f"[自动开端口] {msg}")
        return snapshot()

    log(f"[自动开端口] 位图={_mask_text(mask)}，{scope_txt} 内未开启：{'、'.join(closed)}，开始逐个开启")
    _set(message=f"正在开启 {'、'.join(closed)}…")

    opened: list[str] = []
    failed: list[str] = []
    for p, name, bit in targets:
        if (mask >> bit) & 1:
            continue          # 已开启的端口不碰
        try:
            r = _api("/api/port", {"port": p, "action": "on"}, timeout=12.0) or {}
            if r.get("ok"):
                opened.append(name)
                log(f"[自动开端口] {name} 已开启")
            else:
                failed.append(name)
                log(f"[自动开端口] {name} 开启失败：{r.get('error')}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            log(f"[自动开端口] {name} 开启异常 {exc!r}")
        time.sleep(0.4)       # 让上一条 BLE 写回稳，别把命令挤在一起

    after = read_mask()
    # 复核只看**所选范围**：单口模式下别的口关着不算失败（我们本来就不该碰它）
    still = closed_ports(after, targets) if after is not None else []
    want = _ports_mask(targets)
    fully_on = (after is not None) and ((int(after) & want) == want)
    at = datetime.now().strftime("%H:%M:%S")

    if fully_on:
        delta = f"（{_mask_text(mask)}→{_mask_text(after)}）"
        tail = "已全部开启" if scope_all else "已开启"
        msg = f"今日 {at} 自动检查：{'、'.join(closed)} 未开 → {tail}{delta}"
        _set(phase="done", mask=after, opened=opened, at=at, message=msg)
        _save_saved({"date": day, "at": at, "ok": True, "opened": opened, "mask": after})
        log(f"[自动开端口] {msg}，复核 {scope_txt} 全开")
    else:
        left = "、".join(still) if still else "（复核读数缺失）"
        msg = f"今日 {at} 自动开启未完全成功，仍关闭：{left}"
        _set(phase="error", mask=after, opened=opened, at=at, message=msg)
        log(f"[自动开端口] {msg}，失败端口 {'、'.join(failed) or '无'}；不记当日完成，下次启动会重试")
    return snapshot()


# ---------------------------------------------------------------- 入口


def _background() -> None:
    try:
        _execute(force=False)
    except Exception as exc:  # noqa: BLE001
        _set(phase="error", message=f"执行异常：{exc!r}")
        log(f"[自动开端口] 执行异常 {exc!r}")


def start(logger=None) -> None:
    """管理器启动时调用一次；关着或不在时间窗内都只写一行日志，不起无用的线程。"""
    if logger:
        set_logger(logger)
    if not is_enabled():
        _set(enabled=False, phase="disabled", message="已关闭，启动时不会自动检查")
        log("[自动开端口] 开关是关的，本次启动不检查")
        return
    _set(enabled=True)
    threading.Thread(target=_background, daemon=True, name="autoport").start()


def run_now() -> bool:
    """运行管理里的「立即」按钮：忽略时间窗与当日去重，强制检查一次。"""
    threading.Thread(target=lambda: _execute(force=True), daemon=True,
                     name="autoport-manual").start()
    return True
