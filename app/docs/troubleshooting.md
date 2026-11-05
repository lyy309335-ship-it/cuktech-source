# Troubleshooting

## Import errors

Install dependencies in editable mode:

```bash
python -m pip install -e .
```

## Device not found

Run `python -m cuktech_ble_controller scan` near the charger. Confirm Bluetooth is enabled and no other client is connected.

## Authentication fails

Check that `CUKTECH_DEVICE_TOKEN` is a 12-byte hex string from your own charger. Placeholder values allow the package to import but cannot authenticate.

Do not use Xiaomi `serviceToken`, `passToken`, `CUKTECH_API_KEY`, BLE key, or another device's token as `CUKTECH_DEVICE_TOKEN`. See `docs/getting-started.zh-CN.md` for the full token guide.

## Web UI cannot connect

Start the server with `python -m cuktech_ble_controller web`, then open `http://127.0.0.1:8080`. If `CUKTECH_API_KEY` is set, API clients must send it as `X-API-Key`.
