# Repository Guidelines

## Project Structure & Module Organization

`src/cuktech_ble_controller/` contains the Python package. `ble.py` implements BLE protocol commands and `server.py` serves the local Web UI. `web/index.html` is the browser UI. `docs/` contains public, redacted protocol and setup notes. `integrations/openclaw/` contains optional integration helpers. `examples/` stores safe sample configuration and launch scripts. `tests/` is for smoke tests and regression tests.

## Build, Test, and Development Commands

- `.venv\Scripts\python.exe build_installer.py`: build the Windows installer (onedir app + onefile setup).
  It also archives the result to `..\安装包\` , refreshes `..\分发\`, and syncs the docs there.
- `python -m pip install -e .`: install the package and runtime dependencies in editable mode.
- `python -m cuktech_ble_controller scan`: scan nearby BLE devices.
- `python -m cuktech_ble_controller status`: read charger status using configured credentials.
- `python -m cuktech_ble_controller web`: start the local Web UI.
- `python -m py_compile src/cuktech_ble_controller/ble.py src/cuktech_ble_controller/server.py`: run a quick syntax check.

## Coding Style & Naming Conventions

Use Python 3.9+ with 4-space indentation and clear snake_case names. Keep device secrets outside code and load them from environment variables. Prefer small functions around protocol parsing and command execution. Preserve UTF-8 for Chinese documentation and UI text.

## Testing Guidelines

Add tests under `tests/` for configuration parsing, CLI smoke behavior, and protocol helpers that do not require hardware. Hardware-dependent tests should be documented as manual checks. Before publishing, run a secret scan and syntax checks.

## Versioning (mandatory)

**Every functional change must bump the version.** No exceptions, and not only when cutting a release — an unversioned behavior change makes it impossible to tell which build a user is running.

- Single source of truth: `desktop/version.py` (`VERSION`). Never hardcode a version string elsewhere.
- Semver `MAJOR.MINOR.PATCH`:
  - **MAJOR** — incompatible change: config format, data directory, or HTTP/WS contract breaks existing installs.
  - **MINOR** — new feature, changed behavior, or a visible UI/interaction change.
  - **PATCH** — pure fix: no new feature, no UI change, only making existing behavior correct.
- MINOR resets PATCH to 0; MAJOR resets both MINOR and PATCH to 0. A change that is both a feature and a fix takes the higher slot.
- Docs, comments, and build-script-only changes may skip the bump. Touching `web/`, `desktop/`, `src/`, or `widget.html` never may.
- One bump must land in all four places together:
  1. `desktop/version.py` — `VERSION`
  2. `版本更新说明.md` — new `## vX.Y.Z（YYYY-MM-DD）` section at the top
  3. `installer/installer.py` — `VERSION` (overwritten at build time, but the fallback must match)
  4. `../使用说明.md` / `../README.md` — the `CUKTECH-Setup_vX.Y.Z.exe` filename

`build_installer.py` enforces this in `assert_version_consistency()`: it aborts before packing if the changelog has no section for the current version, or if those docs still name an older installer. Historical references to past versions are fine.


## Commit & Pull Request Guidelines

Use concise imperative commit subjects, for example `Sanitize BLE credential configuration`. PRs should describe affected behavior, commands run, hardware assumptions, and screenshots for UI changes. Do not include real MAC addresses, tokens, logs, databases, bugreports, or packet captures.

## Security & Configuration

Use `examples/.env.example` as the template. Keep the Web UI bound to `127.0.0.1` by default; set `CUKTECH_API_KEY` before exposing it on a LAN.
