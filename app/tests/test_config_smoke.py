from __future__ import annotations

import importlib
import os


def test_package_imports_without_runtime_ble_dependencies() -> None:
    module = importlib.import_module("cuktech_ble_controller")
    assert module.__version__


def test_placeholder_config_is_public() -> None:
    old_mac = os.environ.get("CUKTECH_DEVICE_MAC")
    os.environ.pop("CUKTECH_DEVICE_MAC", None)
    try:
        ble = importlib.reload(importlib.import_module("cuktech_ble_controller.ble"))
        assert ble.DEVICE_MAC == "AA:BB:CC:DD:EE:FF"
        assert ble.DEVICE_TOKEN == bytes(12)
        assert ble.DEVICE_BLE_KEY == bytes(16)
    finally:
        if old_mac is not None:
            os.environ["CUKTECH_DEVICE_MAC"] = old_mac
