"""Command entry point for `python -m cuktech_ble_controller`."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        from aiohttp import web
        from .server import HOST, PORT, create_app

        print("=" * 50)
        print("  CUKTECH BLE Controller")
        print(f"  http://{HOST}:{PORT}")
        print("=" * 50)
        web.run_app(create_app(), host=HOST, port=PORT)
        return

    from .ble import main as ble_main

    ble_main()


if __name__ == "__main__":
    main()
