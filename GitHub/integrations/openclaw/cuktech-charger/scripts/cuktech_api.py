#!/usr/bin/env python3
"""Small HTTP client for the local CUKTECH BLE Controller server."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("CUKTECH_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
API_KEY = os.environ.get("CUKTECH_API_KEY", "")

MODE_MAP = {
    "ai": 1,
    "eco": 2,
    "apple": 2,
    "single": 3,
    "balance": 4,
}


def request(method: str, path: str, data: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"error": str(exc)}
        return {"ok": False, **payload}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"connection failed: {exc.reason}"}


def print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"help", "-h", "--help"}:
        print("usage: cuktech_api.py <status|connect|disconnect|power|mode|port|timer|set>")
        return 0

    cmd = argv[0].lower()
    if cmd == "status":
        print_json(request("GET", "/api/status"))
    elif cmd == "power":
        status = request("GET", "/api/status")
        print_json({"ports": status.get("ports", {})})
    elif cmd == "connect":
        print_json(request("POST", "/api/connect"))
    elif cmd == "disconnect":
        print_json(request("POST", "/api/disconnect"))
    elif cmd == "mode" and len(argv) >= 2:
        mode = MODE_MAP.get(argv[1].lower())
        if mode is None:
            print("mode must be one of: ai, eco, apple, single, balance", file=sys.stderr)
            return 2
        print_json(request("POST", "/api/set", {"piid": 5, "value": mode}))
    elif cmd == "port" and len(argv) >= 3:
        print_json(request("POST", "/api/port", {"port": argv[1].lower(), "action": argv[2].lower()}))
    elif cmd == "timer" and len(argv) >= 3:
        print_json(request("POST", "/api/timer", {"port": argv[1].lower(), "minutes": int(argv[2])}))
    elif cmd == "set" and len(argv) >= 3:
        print_json(request("POST", "/api/set", {"piid": int(argv[1]), "value": int(argv[2])}))
    else:
        print(f"unknown or incomplete command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
