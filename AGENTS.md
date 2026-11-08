# Repository Guidelines

## Project Structure & Module Organization

Windows-only desktop console for the CUKTECH 10 GaN Charger Ultra (Linux/macOS are not supported).

- `main.py` — single executable entry, switches role by flag: manager (default), `--service`,
  `--widget`, `--collect`, `--update-runner <setup.exe> [sha256]`.
- `cuktech.py` — launcher for the collection service (reads `.env`, starts the local web server).
- `widget_app.py` — the desktop-card window (pywebview host, drag/pin, watchdog).
- `desktop/` — Windows shell: `manager.py` (tray + window hosting), `autoport.py` (auto re-open
  ports), `updater.py` (self-update), `credentials.py` / `credential_wizard.py` (tokens + QR
  wizard), `wv2env.py` (per-window WebView2 data folders), `icon.py` (tray icon bitmaps),
  `paths.py` (data/log paths), `version.py` (single source of truth for the version).
- `web/` — console front end (`index.html`, `vendor/` offline Chart.js).
- `src/cuktech_ble_controller/` — upstream BLE package (MIT): `ble.py` protocol and MiOT auth,
  `server.py` HTTP/WebSocket API, `__main__.py` CLI.
- `installer/` — tkinter onefile installer.
- `docs/` — user manual (`使用说明.md`), configuration, protocol notes, troubleshooting.
- `tools/` — debugging / credential scripts; `tests/` — hardware-free regression tests;
  `examples/` — sample launch scripts; `integrations/openclaw/` — optional integration.
- Root `*.html` / `*.js` — page assets shipped with the app (`widget.html`, `console_host.html`,
  `credential_wizard.html`, `console_overlay.js`).
- `build_installer.py` + `CUKTECH.spec` — one-command packaging.

## Build, Test, and Development Commands

- `.venv\Scripts\python.exe build_installer.py`: build the Windows installer (onedir app + onefile
  setup). Output lands in `dist\` (`CUKTECH-Setup.exe`, `CUKTECH\`). In the maintainer's two-layer
  dev workspace the same run also archives the package to `..\安装包\` and refreshes `..\分发\`;
  in a plain clone those directories do not exist and the step is skipped.
- `python -m pip install -e .`: install the package and runtime dependencies in editable mode
  (`aiohttp`, `bleak`, `cryptography`). Add `pywebview pystray pillow` to run the desktop shell.
- `python -m cuktech_ble_controller scan`: scan nearby BLE devices.
- `python -m cuktech_ble_controller status`: read charger status using configured credentials.
- `python -m cuktech_ble_controller web`: start the local Web UI.
- `python main.py`: run the full desktop app (tray + console window + desktop card).
- `python -m py_compile src/cuktech_ble_controller/ble.py src/cuktech_ble_controller/server.py`: run
  a quick syntax check.

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
  2. `CHANGELOG.md` — new `## vX.Y.Z（YYYY-MM-DD）` section at the top
  3. `installer/installer.py` — `VERSION` (overwritten at build time, but the fallback must match)
  4. `docs/使用说明.md` / `README.md` / `README.zh-CN.md` — the `CUKTECH-Setup_vX.Y.Z.exe` filename

`build_installer.py` enforces this in `assert_version_consistency()`: it aborts before packing if the
changelog has no section for the current version, or if those docs still name an older installer.
Historical references to past versions are fine. The changelog is bundled into the installer and
installed as `版本更新说明.md` in the program directory (so the file the user double-clicks keeps
the Chinese name, while the repository copy is `CHANGELOG.md`).


## Commit & Pull Request Guidelines

Use concise imperative commit subjects, for example `Sanitize BLE credential configuration`. PRs should describe affected behavior, commands run, hardware assumptions, and screenshots for UI changes. Do not include real MAC addresses, tokens, logs, databases, bugreports, or packet captures.

## Security & Configuration

Use `.env.example` at the repository root as the template. Keep the Web UI bound to `127.0.0.1` by default; set `CUKTECH_API_KEY` before exposing it on a LAN.
