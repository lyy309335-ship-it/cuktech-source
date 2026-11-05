"""设备凭据（MAC / TOKEN / BLE KEY）的唯一读写入口。

为什么不能让用户去改包里的 .env
--------------------------------
打包版（onedir）的 `sys._MEIPASS` 指向 `_internal\\`，那份 .env 技术上可写，
但：① 路径藏在 `%LOCALAPPDATA%\\Programs\\CUKTECH\\_internal\\` 里，普通用户找不到；
② installer 重装/升级会把整个 APP_DIR 清空（见 installer.py 的 clear 逻辑），
用户辛苦填的凭据会一起没掉。

所以凭据统一落到 **%LOCALAPPDATA%\\CUKTECH\\.env**：
    - 用户级、可写、路径可查（托盘「打开配置目录」直达）
    - 不在 APP_DIR 下，重装升级不受影响
    - 和运行时数据、日志同目录，卸载时是否清理由用户决定

写入一律走 `save()`：它是全项目唯一的写入口，保证「向导 / 取码脚本 / 命令行」
三条通道写出来的格式完全一致。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

APP_NAME = "CUKTECH"

MAC_KEY = "CUKTECH_DEVICE_MAC"
TOKEN_KEY = "CUKTECH_DEVICE_TOKEN"
BLE_KEY = "CUKTECH_DEVICE_BLE_KEY"
SECRET_KEYS = (MAC_KEY, TOKEN_KEY, BLE_KEY)

PLACEHOLDER_MAC = "AA:BB:CC:DD:EE:FF"

# 保存时如果还没有配置文件，用这份兜底内容当底稿（保证 HOST/PORT 等默认值不丢）
_FALLBACK_ENV = """\
# CUKTECH 设备凭据（由程序自动生成，可在软件「设置设备凭据」里修改）
CUKTECH_DEVICE_MAC=
CUKTECH_DEVICE_TOKEN=
CUKTECH_DEVICE_BLE_KEY=

CUKTECH_HOST=127.0.0.1
CUKTECH_PORT=8080
CUKTECH_AUTO_CONNECT=1
"""


# ---------------------------------------------------------------- 路径

def config_dir() -> Path:
    """可写的用户配置目录。

    便携模式（设了 CUKTECH_APP_DATA_DIR）时跟着数据目录走，
    否则用 %LOCALAPPDATA%\\CUKTECH —— 装到 Program Files 也不影响，这里一定有写权限。
    """
    portable = os.environ.get("CUKTECH_APP_DATA_DIR", "").strip()
    if portable:
        base = Path(portable).expanduser()
    else:
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(root) / APP_NAME
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return base


def env_path() -> Path:
    """主配置文件的完整路径（便携模式落在便携目录）。"""
    return config_dir() / ".env"


def backup_path() -> Path:
    return config_dir() / ".env.bak"


def iter_env_paths() -> list[Path]:
    """只读兜底候选（**不含主配置**，主配置单独处理）。

    主配置 = `env_path()`，它是这个用户的权威来源。这里剩下的只是
    「用户还没配置过」时才会被读到的下家。
    """
    paths: list[Path] = []
    # 源码运行时上一级目录的 .env（开发者习惯把配置放仓库根）
    try:
        from desktop import paths as _paths

        if not _paths.is_frozen():
            paths.append(_paths.resource_dir().parent / ".env")
        paths.append(_paths.resource_dir() / ".env")
    except Exception:  # noqa: BLE001
        pass
    # 去重且保持顺序
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


# ---------------------------------------------------------------- 解析

def parse_env_text(text: str) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，忽略空行与 # 注释，去掉成对引号。"""
    data: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            data[key] = value
    return data


def _safe_parse(path: Path) -> dict[str, str] | None:
    try:
        return parse_env_text(path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return None


def _has_credentials(data: dict[str, str] | None) -> bool:
    if not data:
        return False
    return bool(data.get(TOKEN_KEY, "").strip() or data.get(MAC_KEY, "").strip())


def read_env() -> tuple[dict[str, str], Path | None]:
    """读当前生效的配置，返回 (键值, 实际来源文件)。

    规则很明确：**主配置存在，就只认主配置**。
    主配置写坏（三码全空/文件被截断）时只回落到它自己的 .env.bak，
    绝不继续往下读包内模板 —— 否则用户会以为「我配好了」，实际程序在别处读，
    排障时永远找不到原因。

    只有主配置根本不存在（全新安装）时，才接着找源码目录/包内的模板。
    """
    primary = env_path()
    if primary.is_file():
        data = _safe_parse(primary)
        if _has_credentials(data):
            return data or {}, primary
        bak = backup_path()
        if bak.is_file():
            backup = _safe_parse(bak)
            if _has_credentials(backup):
                return backup or {}, bak
        # 有文件但没内容：如实返回主配置，让上层判定「未配置」
        return data or {}, primary

    for candidate in iter_env_paths():
        if not candidate.is_file():
            continue
        data = _safe_parse(candidate)
        if _has_credentials(data):
            return data or {}, candidate

    # 全新安装：拿模板里的 HOST/PORT 等默认值
    for candidate in iter_env_paths():
        if candidate.is_file():
            data = _safe_parse(candidate)
            if data:
                return data, None
    return parse_env_text(_FALLBACK_ENV), None


def current() -> dict[str, str]:
    """当前生效的三项凭据（已归一化，缺的给空串）。"""
    data, _ = read_env()
    return {
        "mac": normalize_mac(data.get(MAC_KEY, "")),
        "token": normalize_hex(data.get(TOKEN_KEY, ""), 24),
        "ble_key": normalize_hex(data.get(BLE_KEY, ""), 32),
    }


def is_configured() -> bool:
    """三项齐全且格式合法才算「配置好了」。"""
    vals = current()
    return not validate(vals)


# ---------------------------------------------------------------- 归一化

def normalize_mac(value: str) -> str:
    """统一成 AA:BB:CC:DD:EE:FF：去分隔符、转大写、每两位插冒号。

    用户粘进来的可能是 aa-bb-cc-dd-ee-ff、aabbccddeeff 或带空格的形态。
    """
    if not value:
        return ""
    hexonly = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(hexonly) != 12:
        return value.strip()
    hexonly = hexonly.upper()
    return ":".join(hexonly[i:i + 2] for i in range(0, 12, 2))


def normalize_hex(value: str, length: int) -> str:
    """TOKEN / BLE KEY 统一成小写无分隔的定长十六进制。"""
    if not value:
        return ""
    hexonly = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(hexonly) != length:
        return value.strip()
    return hexonly.lower()


def validate(values: dict[str, str]) -> list[str]:
    """返回人话错误列表；空列表表示通过。"""
    errors: list[str] = []
    mac = values.get("mac", "")
    token = values.get("token", "")
    key = values.get("ble_key", "")

    if not mac:
        errors.append("缺少设备 MAC（形如 AA:BB:CC:DD:EE:FF）")
    elif not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
        errors.append(f"MAC 格式不对：需要 12 位十六进制，当前是「{mac}」")
    elif mac == PLACEHOLDER_MAC:
        errors.append("MAC 还是占位符 AA:BB:CC:DD:EE:FF，请填入真实地址")

    if not token:
        errors.append("缺少 TOKEN（24 位十六进制）")
    elif not re.fullmatch(r"[0-9a-f]{24}", token):
        errors.append(f"TOKEN 格式不对：需要 24 位十六进制，当前是 {len(token)} 位")

    if not key:
        errors.append("缺少 BLE KEY（32 位十六进制）")
    elif not re.fullmatch(r"[0-9a-f]{32}", key):
        errors.append(f"BLE KEY 格式不对：需要 32 位十六进制，当前是 {len(key)} 位")

    return errors


# ---------------------------------------------------------------- 写入

def _render(base: dict[str, str], values: dict[str, str]) -> str:
    merged = dict(base)
    merged[MAC_KEY] = values["mac"]
    merged[TOKEN_KEY] = values["token"]
    merged[BLE_KEY] = values["ble_key"]
    merged.setdefault("CUKTECH_HOST", "127.0.0.1")
    merged.setdefault("CUKTECH_PORT", "8080")
    merged.setdefault("CUKTECH_AUTO_CONNECT", "1")

    # 相对路径的 DATA_DIR 抄进用户配置没有意义（相对谁？），只会误导
    if not os.path.isabs(merged.get("CUKTECH_DATA_DIR", "").strip()):
        merged.pop("CUKTECH_DATA_DIR", None)

    lines = [
        "# CUKTECH 设备凭据",
        f"# 由程序写入于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，"
        "请勿手工改格式（可在软件里重新设置）",
        "",
    ]
    for key in SECRET_KEYS:
        lines.append(f"{key}={merged.pop(key)}")
    lines.append("")
    for key, value in merged.items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def save(values: dict[str, str]) -> Path:
    """校验通过后写配置。先备份上一版，再原子替换。

    返回写入路径；校验失败抛 ValueError（带可读原因）。
    """
    mac = normalize_mac(values.get("mac", ""))
    token = normalize_hex(values.get("token", ""), 24)
    key = normalize_hex(values.get("ble_key", ""), 32)
    cleaned = {"mac": mac, "token": token, "ble_key": key}

    errors = validate(cleaned)
    if errors:
        raise ValueError("；".join(errors))

    target = env_path()
    # 底稿取「现有配置 + 包内模板」，这样 HOST/PORT 之类的自定义值不会丢
    base: dict[str, str] = parse_env_text(_FALLBACK_ENV)
    for candidate in iter_env_paths():
        if candidate.is_file() and candidate != target:
            try:
                base.update(parse_env_text(candidate.read_text(encoding="utf-8-sig", errors="replace")))
            except OSError:
                continue
    if target.is_file():
        try:
            base.update(parse_env_text(target.read_text(encoding="utf-8-sig", errors="replace")))
        except OSError:
            pass

    text = _render(base, cleaned)

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        try:
            shutil.copy2(target, backup_path())
        except OSError:
            pass

    tmp = target.with_suffix(".env.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)

    # 收紧权限：只允许当前用户读，避免同机其他账户直接抄走
    _restrict_acl(target)
    return target


def _restrict_acl(path: Path) -> None:
    """尽力而为地收紧 ACL；失败不影响主流程（icacls 在部分精简系统上不存在）。"""
    if sys.platform != "win32":
        return
    try:
        import subprocess

        user = os.environ.get("USERNAME", "")
        if not user:
            return
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001
        pass


def clear() -> bool:
    """删掉主配置和备份（托盘「清除设备凭据」用）。"""
    removed = False
    for path in (env_path(), backup_path()):
        try:
            if path.is_file():
                path.unlink()
                removed = True
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------- 从文本里抠凭据

_MAC_TEXT = re.compile(
    r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})(?![0-9A-Fa-f])"
)
_MAC_BARE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{12})(?![0-9A-Fa-f])")
_HEX24 = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{24})(?![0-9A-Fa-f])")
_HEX32 = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{32})(?![0-9A-Fa-f])")

_LABELLED = (
    ("ble_key", re.compile(r"(?:ble[\s_-]*key|blekey|蓝牙密钥)\s*[:=：]\s*([0-9A-Fa-f]{32})", re.I)),
    ("token", re.compile(r"(?:token|令牌)\s*[:=：]\s*([0-9A-Fa-f]{24})", re.I)),
    ("mac", re.compile(r"(?:mac|地址)\s*[:=：]\s*([0-9A-Fa-f][0-9A-Fa-f:\- ]{10,22}[0-9A-Fa-f])", re.I)),
)


def parse_paste(text: str) -> dict[str, str]:
    """从任意文本里提取三项凭据。

    先按 `token: xxx` / `ble key: xxx` 这种带标签的行找（最准），
    找不到再按「24 位 hex / 32 位 hex / MAC 形态」的特征扫全文。
    返回的字典只包含**真的找到**的键，缺什么由调用方比对。
    """
    found: dict[str, str] = {}
    if not text:
        return found

    for name, pattern in _LABELLED:
        m = pattern.search(text)
        if m:
            found[name] = m.group(1)

    if "mac" not in found:
        m = _MAC_TEXT.search(text) or _MAC_BARE.search(text)
        if m:
            found["mac"] = m.group(1)
    if "token" not in found:
        m = _HEX24.search(text)
        if m:
            found["token"] = m.group(1)
    if "ble_key" not in found:
        m = _HEX32.search(text)
        if m:
            found["ble_key"] = m.group(1)

    return {
        "mac": normalize_mac(found.get("mac", "")),
        "token": normalize_hex(found.get("token", ""), 24),
        "ble_key": normalize_hex(found.get("ble_key", ""), 32),
    }


def pick_device(text: str, keyword: str = "CUKTECH") -> dict[str, str]:
    """从「设备列表」式输出里挑出目标设备那一段再解析。

    token_extractor 会一口气打印家里所有设备，直接全文特征匹配容易抓错行。
    这里先按设备名切段，只留含 keyword 的段落；切不出来就退回全文。
    """
    if not text:
        return parse_paste(text)
    if keyword.lower() not in text.lower():
        return parse_paste(text)

    lines = text.splitlines()
    keep: list[str] = []
    active = False
    for line in lines:
        low = line.lower()
        if keyword.lower() in low:
            active = True
            keep.append(line)
            continue
        if active:
            # 遇到明显是「下一个设备」的起始行就收手
            if re.match(r"^\s*(?:-{3,}|=+|name\s*[:=])", line, re.I) and not re.search(
                r"mac|token|key", line, re.I
            ):
                active = False
                continue
            keep.append(line)
    chunk = "\n".join(keep)
    result = parse_paste(chunk)
    if not result.get("token"):
        return parse_paste(text)
    return result


# ---------------------------------------------------------------- 解析取码结果

def _walk_beaconkey(node, depth: int = 0) -> str:
    """在 BLE_DATA 里递归找 beaconkey。

    上游把它塞在 `BLE_DATA.beaconkey`，但那是米家 API 原样回包，
    换个固件/接口结构就可能多包一层，所以按「找键名」而不是「按固定路径取值」。
    """
    if depth > 4:
        return ""
    if isinstance(node, dict):
        for name in ("beaconkey", "beaconKey", "ble_key", "bleKey"):
            value = node.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for child in node.values():
            got = _walk_beaconkey(child, depth + 1)
            if got:
                return got
    elif isinstance(node, list):
        for child in node:
            got = _walk_beaconkey(child, depth + 1)
            if got:
                return got
    return ""


def parse_extractor_json(path: Path) -> list[dict]:
    """把 token_extractor `-o` 写出的 JSON 摊平成设备列表。

    结构是 [{server, homes:[{home_id, home_owner, devices:[{name, mac, token, BLE_DATA:{...}}]}]}]，
    设备字典是米家 API 原样展开，字段名不保证稳定，所以都做 .get 兜底。
    """
    import json

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
    except OSError as exc:
        raise ValueError(f"读不到取码结果文件：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"取码结果不是合法 JSON：{exc}") from exc

    if isinstance(data, dict):          # 兼容只返回一台服务器的老版本
        data = [data]

    devices: list[dict] = []
    for server in data if isinstance(data, list) else []:
        if not isinstance(server, dict):
            continue
        for home in server.get("homes") or []:
            if not isinstance(home, dict):
                continue
            for dev in home.get("devices") or []:
                if not isinstance(dev, dict):
                    continue
                devices.append({
                    "name": str(dev.get("name") or "").strip(),
                    "model": str(dev.get("model") or "").strip(),
                    "mac": str(dev.get("mac") or "").strip(),
                    "token": str(dev.get("token") or "").strip(),
                    "ble_key": _walk_beaconkey(dev),
                    "server": str(server.get("server") or ""),
                })
    return devices


def pick_cuktech(devices: list[dict]) -> dict | None:
    """挑出充电器那台：优先 name/model 含 cuktech，否则退回唯一一台有 token 的。"""
    usable = [d for d in devices if d.get("token")]
    if not usable:
        return None
    for dev in usable:
        blob = f"{dev.get('name', '')} {dev.get('model', '')}".lower()
        if "cuktech" in blob or "酷态科" in blob:
            return dev
    return usable[0] if len(usable) == 1 else None


# ---------------------------------------------------------------- 日志脱敏

def mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 6}{value[-keep:]}"


def redact(text: str) -> str:
    """把日志里的三码形态全部打码（24/32 位 hex、MAC）。"""
    if not text:
        return text
    out = _HEX32.sub(lambda m: mask(m.group(0)), text)
    out = _HEX24.sub(lambda m: mask(m.group(0)), out)
    out = _MAC_TEXT.sub(lambda m: mask(m.group(0), 2), out)
    return out
