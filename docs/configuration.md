# Configuration

本项目通过环境变量配置，代码里不落任何设备专属凭据。源码运行时读仓库根目录的 `.env`
（由 `.env.example` 复制而来）；安装包运行时读 `%LOCALAPPDATA%\CUKTECH\.env`
（扫码向导自动写入）。两处都已在 `.gitignore` 里。

> 当前版本只支持 **Windows 10 / 11（64 位）**。

| Variable | Required | Description |
| --- | --- | --- |
| `CUKTECH_DEVICE_MAC` | Yes for direct device commands | 充电器 BLE MAC，例如 `AA:BB:CC:DD:EE:FF`。 |
| `CUKTECH_DEVICE_TOKEN` | Yes for authenticated commands | 自己充电器的 12 字节 hex `TOKEN`（24 个十六进制字符）。 |
| `CUKTECH_DEVICE_BLE_KEY` | No | 可选 16 字节 BLE key，留给后续协议工作；当前认证主要用 `CUKTECH_DEVICE_TOKEN`。 |
| `CUKTECH_API_KEY` | Recommended for LAN use | 可选的本地 API 密钥，开了以后客户端要带 `X-API-Key`。 |
| `CUKTECH_HOST` | No | Web 监听地址，默认 `127.0.0.1`。 |
| `CUKTECH_PORT` | No | Web 端口，默认 `8080`。 |
| `CUKTECH_DATA_DIR` | No | 历史功率库所在目录。命令行 / Web 面板默认 `~/.cuktech-ble-controller`，`.env.example` 里给的是 `./data`；桌面端固定用 `%LOCALAPPDATA%\CUKTECH\data`，不受这一项影响。 |
| `CUKTECH_AUTO_CONNECT` | No | 启动即连接设备并在断线后重连，`1` 开 / `0` 关，默认 `1`。 |
| `CUKTECH_APP_DATA_DIR` | No | 仅桌面端：覆盖数据目录（默认 `%LOCALAPPDATA%\CUKTECH\data`，源码运行时是 `data\`）。 |
| `CUKTECH_APP_LOG_DIR` | No | 仅桌面端：覆盖日志目录（默认 `%LOCALAPPDATA%\CUKTECH\logs`，源码运行时是 `logs\`）。 |

真实值只放在自己的 shell、`.env`、密码管理器或部署环境里，不要提交。
`build_installer.py` 出包前会扫描 `CUKTECH_DEVICE_*`，检测到非空值直接中止。

取 token 与首次配置的完整流程见 `docs/getting-started.zh-CN.md`；
面向使用者的功能说明见 `docs/使用说明.md`。
