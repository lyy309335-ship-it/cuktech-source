# Configuration

The controller is configured through environment variables so public code can stay free of device-specific secrets.

| Variable | Required | Description |
| --- | --- | --- |
| `CUKTECH_DEVICE_MAC` | Yes for direct device commands | Charger BLE MAC address, for example `AA:BB:CC:DD:EE:FF`. |
| `CUKTECH_DEVICE_TOKEN` | Yes for authenticated commands | 12-byte hex `TOKEN` from your own charger device entry. |
| `CUKTECH_DEVICE_BLE_KEY` | No | Optional 16-byte BLE key retained for future protocol work; current auth primarily uses `CUKTECH_DEVICE_TOKEN`. |
| `CUKTECH_API_KEY` | Recommended for LAN use | Optional local API key for the Web UI. |
| `CUKTECH_HOST` | No | Web bind address, default `127.0.0.1`. |
| `CUKTECH_PORT` | No | Web port, default `8080`. |
| `CUKTECH_DATA_DIR` | No | Runtime history directory, default user home. |

Keep real values in your shell, `.env`, password manager, or deployment environment. Do not commit them.

For token discovery and first-run setup, see `docs/getting-started.zh-CN.md`.
