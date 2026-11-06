"""用 GitHub Git Data API 把当前 HEAD 推上去，绕开 git push 的网络问题。

本机 `git push` 走 smart HTTP 时反复 `send-pack: unexpected disconnect`，
但 `gh api`（普通 HTTPS 请求）一直正常，所以直接按对象模型上传：
对每个 blob 单独 POST，再建 tree / commit、最后移动 ref。

内容一律从 `git cat-file` 读取（不是工作区文件），保证和本地 HEAD 逐字节一致，
不受 CRLF 自动转换影响。
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "lyy309335-ship-it/cuktech-source"
BRANCH = "master"


def git(*args: str, binary: bool = False):
    r = subprocess.run(["git", *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def gh(args: list[str], payload: dict | bytes | None = None) -> dict:
    cmd = ["gh", "api", *args]
    if payload is not None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        proc = subprocess.run(cmd + ["--input", "-"], input=data, capture_output=True)
    else:
        proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:300])
    out = proc.stdout.decode("utf-8", "replace").strip()
    return json.loads(out) if out else {}


def main() -> int:
    head = git("rev-parse", "HEAD").strip()
    msg = git("log", "-1", "--pretty=%B").strip()
    print("本地 HEAD:", head[:8], "|", msg.splitlines()[0])

    # 远端当前分支头（可能是旧的，用来做 parent）
    try:
        remote_head = gh([f"repos/{REPO}/git/ref/heads/{BRANCH}"])["object"]["sha"]
    except RuntimeError:
        remote_head = None
    print("远端 HEAD:", (remote_head or "(空)")[:8])
    if remote_head == head:
        print("已经一致，无需推送")
        return 0

    # ---- 列出本地 HEAD 的全部文件对象 ----
    entries = []
    for line in git("ls-tree", "-r", "-z", head).split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, otype, sha = meta.split()
        if otype == "blob":
            entries.append((mode, sha, path))
    print("待上传文件:", len(entries))

    # ---- 并发创建 blob ----
    def make_blob(item):
        mode, sha, path = item
        raw = git("cat-file", "-p", sha, binary=True)
        res = gh(["-X", "POST", f"repos/{REPO}/git/blobs"],
                 {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"})
        return {"path": path, "mode": mode, "type": "blob", "sha": res["sha"]}

    done = 0
    tree_items = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for item in pool.map(make_blob, entries):
            tree_items.append(item)
            done += 1
            if done % 15 == 0:
                print(f"  已上传 {done}/{len(entries)}")
    print("blob 全部完成")

    tree = gh(["-X", "POST", f"repos/{REPO}/git/trees"], {"tree": tree_items})
    print("tree:", tree["sha"][:8])

    commit_payload = {"message": msg, "tree": tree["sha"], "parents": []}
    if remote_head:
        commit_payload["parents"] = [remote_head]
    commit = gh(["-X", "POST", f"repos/{REPO}/git/commits"], commit_payload)
    print("commit:", commit["sha"][:8])

    if remote_head:
        gh(["-X", "PATCH", f"repos/{REPO}/git/refs/heads/{BRANCH}",
            "-F", "force=true"], {"sha": commit["sha"]})
    else:
        gh(["-X", "POST", f"repos/{REPO}/git/refs"],
           {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
    print("ref 已更新")

    now = gh([f"repos/{REPO}/commits/{BRANCH}"])["sha"]
    print("RESULT:", "PASS" if now == commit["sha"] else "FAIL 远端不一致")
    return 0 if now == commit["sha"] else 1


if __name__ == "__main__":
    sys.exit(main())
