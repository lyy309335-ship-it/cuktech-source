"""自动抓取 token_extractor 的登录二维码并弹到屏幕上。

用法（一般由 运行-获取Token.bat 自动调用，不需要手动跑）：
    python watch_qr.py

原理：token_extractor 会把二维码图片挂在自己起的本地 HTTP 服务上
（默认 http://127.0.0.1:31415），在它等你扫码的那几分钟里反复请求即可拿到。
拿到后存成本地 PNG 并交给系统默认看图程序打开，省得在终端里找链接、粘地址。

文件名带时间戳：每次都是新文件，避免看图软件显示上一次的缓存图
（扫到过期旧码会白等 5 分钟）。旧的会自动清理，只留最新一张。

参数：
    --url      二维码服务地址，默认 http://127.0.0.1:31415
    --out      指定保存路径（可选，默认自动命名）
    --timeout  最长等待秒数，默认 200
    --no-open  只保存不打开（自动化测试用）
"""

import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_BYTES = 1000
STALE_PATTERN = "登录二维码*.png"


def fetch(url: str, opener: urllib.request.OpenerDirector) -> bytes | None:
    try:
        with opener.open(url, timeout=2) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except Exception:
        return None


def cleanup_stale(keep: Path) -> int:
    removed = 0
    for old in keep.parent.glob(STALE_PATTERN):
        if old.resolve() == keep.resolve():
            continue
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:31415")
    ap.add_argument("--out", default=None)
    ap.add_argument("--timeout", type=float, default=200.0)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    out = Path(args.out) if args.out else (
        here.parent / f"登录二维码_{time.strftime('%H%M%S')}.png"
    )

    # 明确绕过系统代理，否则本地回环请求可能被代理劫持
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.time() + args.timeout
    last_size = -1
    announced = False

    while time.time() < deadline:
        data = fetch(args.url, opener)
        if data and data.startswith(PNG_MAGIC) and len(data) >= MIN_BYTES:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            removed = cleanup_stale(out)
            print(f"[二维码] 已取到二维码（{len(data)} 字节）-> {out.name}", flush=True)
            if removed:
                print(f"[二维码] 已清理 {removed} 张过期旧图", flush=True)
            if args.no_open:
                return 0
            try:
                os.startfile(str(out))
                print("[二维码] 已弹出图片窗口，请立刻用米家 App 扫一扫，"
                      "扫完记得在手机上点『确认登录』", flush=True)
            except OSError as exc:
                print(f"[二维码] 自动打开失败（{exc}），请手动双击：{out}", flush=True)
            return 0
        if data is not None and len(data) != last_size:
            last_size = len(data)
            if not announced:
                announced = True
                print("[二维码] 服务已就绪，正在等待二维码生成…", flush=True)
        time.sleep(0.4)

    print(f"[二维码] 等待超时（{args.timeout:.0f} 秒）仍未取到图片。", flush=True)
    print("[二维码] 若工具已经退出或你已登录，这条提示可以忽略。", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
