# Repository Guidelines

## Project Structure & Module Organization

`src/cuktech_ble_controller/` contains the Python package. `ble.py` implements BLE protocol commands and `server.py` serves the local Web UI. `web/index.html` is the browser UI. `docs/` contains public, redacted protocol and setup notes. `integrations/openclaw/` contains optional integration helpers. `examples/` stores safe sample configuration and launch scripts. `tests/` is for smoke tests and regression tests.

## Build, Test, and Development Commands

- `python -m pip install -e .`: install the package and runtime dependencies in editable mode.
- `python -m cuktech_ble_controller scan`: scan nearby BLE devices.
- `python -m cuktech_ble_controller status`: read charger status using configured credentials.
- `python -m cuktech_ble_controller web`: start the local Web UI.
- `python -m py_compile src/cuktech_ble_controller/ble.py src/cuktech_ble_controller/server.py`: run a quick syntax check.

## Coding Style & Naming Conventions

Use Python 3.9+ with 4-space indentation and clear snake_case names. Keep device secrets outside code and load them from environment variables. Prefer small functions around protocol parsing and command execution. Preserve UTF-8 for Chinese documentation and UI text.

## Testing Guidelines

Add tests under `tests/` for configuration parsing, CLI smoke behavior, and protocol helpers that do not require hardware. Hardware-dependent tests should be documented as manual checks. Before publishing, run a secret scan and syntax checks.

## Commit & Pull Request Guidelines

Use concise imperative commit subjects, for example `Sanitize BLE credential configuration`. PRs should describe affected behavior, commands run, hardware assumptions, and screenshots for UI changes. Do not include real MAC addresses, tokens, logs, databases, bugreports, or packet captures.

## Security & Configuration

Use `examples/.env.example` as the template. Keep the Web UI bound to `127.0.0.1` by default; set `CUKTECH_API_KEY` before exposing it on a LAN.
