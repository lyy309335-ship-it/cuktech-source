# CUKTECH BLE Controller / 酷态科 BLE 控制器

Local BLE controller and web UI for the CUKTECH 10 GaN Charger Ultra. It talks to the charger directly over Bluetooth LE and does not require cloud access after you provide your own device credentials.

本项目用于通过本机蓝牙直接控制酷态科 10 号 GaN 充电器 Ultra。公开仓库不包含任何真实 MAC、token、BLE key 或日志。

## Features / 功能

- Scan nearby BLE devices and inspect GATT services.
- Authenticate with a charger using your own token.
- Read charger status, port power, scene mode, screen timeout, timers, and common settings.
- Control scene mode, USB-A low-current mode, port switches, timers, language, idle screen, and orientation lock.
- Run a local Web UI with optional `CUKTECH_API_KEY` protection.
- Optional OpenClaw integration under `integrations/openclaw/`.

## Installation / 安装

For a complete step-by-step Chinese tutorial, including how to obtain the correct Xiaomi device token, read:

中文完整教程（包含如何获取正确 token）：[`docs/getting-started.zh-CN.md`](docs/getting-started.zh-CN.md)

```powershell
cd cuktech-ble-controller
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Configuration / 配置

Copy `examples/.env.example` and set values for your own device:

```text
CUKTECH_DEVICE_MAC=AA:BB:CC:DD:EE:FF
CUKTECH_DEVICE_TOKEN=your-12-byte-hex-token
CUKTECH_DEVICE_BLE_KEY=optional-16-byte-hex-ble-key
CUKTECH_API_KEY=optional-local-api-key
CUKTECH_HOST=127.0.0.1
CUKTECH_PORT=8080
```

Token discovery is intentionally not vendored here. The current authentication flow requires the 12-byte device `TOKEN` from your own charger. Use the upstream Xiaomi token extractor if needed, and follow its license and security guidance:
https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor

## Usage / 使用

```bash
python -m cuktech_ble_controller scan
python -m cuktech_ble_controller info
python -m cuktech_ble_controller status
python -m cuktech_ble_controller set-mode ai
python -m cuktech_ble_controller web
```

Open `http://127.0.0.1:8080` for the Web UI. Keep the server bound to `127.0.0.1` unless you intentionally expose it on your LAN.

## Development / 开发

```bash
python -m py_compile src/cuktech_ble_controller/ble.py src/cuktech_ble_controller/server.py
python -m cuktech_ble_controller --help
```

Before publishing, scan for private values:

```bash
rg -n "my-secret|real-device-mac|real-device-token|real-ble-key|bugreport|serviceToken" .
```

Do not commit real device credentials, logs, databases, bugreports, or packet captures.
