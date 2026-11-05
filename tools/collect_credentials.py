"""命令行取码：跑 token_extractor，解析结果，写进用户配置目录。

用法（在任意目录都行，脚本自己定位 exe）：
    python collect_credentials.py            # 正常取码，结果自动落盘
    python collect_credentials.py --dry-run  # 只打印要写什么，不写盘
    python collect_credentials.py --from-json out/tokens.json   # 跳过取码，直接解析已有结果
    python collect_credentials.py --manual   # 不走提取器，手工粘三行

设计上刻意**不接管 stdout**：token_extractor 的交互提示（选 q、扫码、确认登录）
全部原样走控制台，用户看到的和以前一模一样；我们只在它退出后去读
`--output` 写出的 JSON。这样既不会因为管道缓冲把提示吞掉，也不依赖屏幕文本格式。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXE_NAME = "token_extractor.exe"
OUT_DIR = HERE / "out"


def _import_credentials():
    """源码运行时 desktop 包在 ../app 下；打包运行时它是 frozen 模块，直接 import 即可。"""
    try:
        from desktop import credentials  # type: ignore

        return credentials
    except ImportError:
        pass
    for candidate in (HERE.parent / "app", HERE.parent):
        if (candidate / "desktop" / "credentials.py").is_file():
            sys.path.insert(0, str(candidate))
            from desktop import credentials  # type: ignore

            return credentials
    raise SystemExit(
        "找不到 desktop/credentials.py —— 请把本脚本放在项目的 tools\\ 目录下运行"
    )


def find_exe() -> Path | None:
    for candidate in (HERE / EXE_NAME, HERE / "bin" / EXE_NAME):
        if candidate.is_file():
            return candidate
    found = shutil.which(EXE_NAME)
    return Path(found) if found else None


# ---------------------------------------------------------------- 选设备

def pick(devices: list[dict], credentials, non_interactive: bool) -> dict | None:
    """挑目标设备：能唯一确定就直接用，多台都像就让人选。

    解析和类型识别都在 desktop.credentials 里，这里只负责交互。
    """
    usable = [d for d in devices if d.get("token")]
    if not usable:
        return None

    chosen = credentials.pick_cuktech(devices)
    cands = [chosen] if chosen else usable

    if len(cands) == 1 or non_interactive:
        return cands[0]

    print("\n找到多台设备，请输入序号：")
    for i, dev in enumerate(cands, 1):
        print(f"  {i}. {dev['name'] or '(无名)'}   {dev['mac']}   "
              f"{'有 BLE KEY' if dev['ble_key'] else '无 BLE KEY'}")
    while True:
        try:
            raw = input("序号（直接回车选 1）：").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            return cands[0]
        if raw.isdigit() and 1 <= int(raw) <= len(cands):
            return cands[int(raw) - 1]


# ---------------------------------------------------------------- 取码

def run_extractor(exe: Path, out_json: Path) -> int:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    if out_json.exists():
        out_json.unlink()
    cmd = [str(exe), "-o", str(out_json)]
    print(f"» 启动取码工具：{exe.name} -o out\\{out_json.name}")
    print("  （登录方式选 q 扫码；扫完记得在手机上点「确认登录」）\n")
    try:
        # 不捕获 stdout/stderr/stdin：交互和二维码提示全部原样走当前控制台
        return subprocess.call(cmd, cwd=str(exe.parent))
    except OSError as exc:
        print(f"[错误] 启动失败：{exc}", file=sys.stderr)
        return 127


def read_manual(credentials) -> dict:
    """纯手工输入兜底：三项分别读，允许整段粘贴。"""
    print("\n请把三行内容粘进来（MAC / TOKEN / BLE KEY，每行一项）：")
    lines = []
    for i in range(3):
        try:
            lines.append(input(f"  {i + 1}/3 > ").strip())
        except (EOFError, KeyboardInterrupt):
            break
    parsed = credentials.parse_paste("\n".join(lines))
    if not parsed.get("token"):
        parsed = credentials.parse_paste(" ".join(lines))
    return parsed


# ---------------------------------------------------------------- 主流程

def report(values: dict, credentials) -> None:
    print("\n" + "=" * 58)
    print("  识别到的设备凭据")
    print("=" * 58)
    print(f"  MAC      {values.get('mac')}")
    print(f"  TOKEN    {credentials.mask(values.get('token', ''))}  "
          f"({len(values.get('token', ''))} 位)")
    print(f"  BLE KEY  {credentials.mask(values.get('ble_key', ''))}  "
          f"({len(values.get('ble_key', ''))} 位)")


def main() -> int:
    parser = argparse.ArgumentParser(description="读取 CUKTECH 充电器硬件码")
    parser.add_argument("--from-json", metavar="PATH", help="跳过取码，解析已有的 JSON")
    parser.add_argument("--manual", action="store_true", help="不跑提取器，手工粘贴三项")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写配置")
    parser.add_argument("--non-interactive", action="store_true",
                        help="不询问，多设备时取第一台 CUKTECH")
    args = parser.parse_args()

    credentials = _import_credentials()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.manual:
        values = read_manual(credentials)
    else:
        json_path = Path(args.from_json) if args.from_json else None
        if json_path is None:
            exe = find_exe()
            if exe is None:
                print(f"[错误] 没找到 {EXE_NAME}", file=sys.stderr)
                print("        请把它放到 tools\\ 目录下，或改用 --manual 手工输入",
                      file=sys.stderr)
                return 4
            json_path = OUT_DIR / "tokens.json"
            code = run_extractor(exe, json_path)
            if code != 0:
                print(f"\n[警告] 取码工具退出码 {code}，仍尝试解析已有结果")

        try:
            devices = credentials.parse_extractor_json(json_path)
        except ValueError as exc:
            print(f"\n[错误] {exc}", file=sys.stderr)
            return 2
        print(f"\n共解析到 {len(devices)} 台设备")
        chosen = pick(devices, credentials, args.non_interactive)
        if chosen is None:
            print("\n[错误] 没在结果里找到可用设备（token 为空）。", file=sys.stderr)
            print("       可以重新跑一次，或改用 --manual 手工输入三项。", file=sys.stderr)
            return 2
        values = {
            "mac": chosen["mac"],
            "token": chosen["token"],
            "ble_key": chosen["ble_key"],
        }

    values = {
        "mac": credentials.normalize_mac(values.get("mac", "")),
        "token": credentials.normalize_hex(values.get("token", ""), 24),
        "ble_key": credentials.normalize_hex(values.get("ble_key", ""), 32),
    }
    report(values, credentials)

    errors = credentials.validate(values)
    if errors:
        print("\n[失败] 校验没通过：", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 3

    if args.dry_run:
        print("\n(--dry-run，未写入配置)")
        return 0

    target = credentials.save(values)
    print("\n" + "=" * 58)
    print(f"  已写入  {target}")
    print("  现在启动控制台就能连上充电器了。")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(5)
