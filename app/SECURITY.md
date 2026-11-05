# Security Policy

## Sensitive Data

Never publish real values for:

- `CUKTECH_DEVICE_MAC`
- `CUKTECH_DEVICE_TOKEN`
- `CUKTECH_DEVICE_BLE_KEY`
- `CUKTECH_API_KEY`
- Xiaomi account credentials, service tokens, logs, bugreports, packet captures, or local databases

If a secret was committed, rotate or revoke it before making the repository public. Removing it from the latest commit is not enough if it exists in Git history.

## Network Exposure

The web server defaults to `127.0.0.1`. Only bind to `0.0.0.0` on trusted networks, and set `CUKTECH_API_KEY` before exposing the service.

## Reporting

For public issues, avoid including private device identifiers or logs. Share minimal reproduction steps and redact all credentials.
