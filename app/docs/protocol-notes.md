# Redacted Protocol Notes

This project uses the Xiaomi MiOT BLE service exposed by the CUKTECH 10 GaN Charger Ultra. All identifiers below are public protocol structure notes; real device credentials are intentionally omitted.

## GATT Service

- Service: `0000fe95-0000-1000-8000-00805f9b34fb`
- Device info characteristic: `0000001c-0000-1000-8000-00805f9b34fb`
- Auth control characteristic: `00000010-0000-1000-8000-00805f9b34fb`
- Auth data characteristic: `00000019-0000-1000-8000-00805f9b34fb`
- Command send characteristic: `0000001a-0000-1000-8000-00805f9b34fb`
- Command receive characteristic: `0000001b-0000-1000-8000-00805f9b34fb`

## Common PIIDs

- `1`-`4`: C1, C2, C3, and USB-A port telemetry.
- `5`: scene mode.
- `6`: screen timeout.
- `9`-`12`: per-port timers.
- `13`: display language.
- `15`: USB-A low-current mode.
- `16`: port enable bitmask.
- `19`: idle screen-off setting.
- `20`: orientation lock.

## Safety

Do not publish raw logs, HMAC exchange output, token values, BLE keys, device IDs, or packet captures from your own device.
