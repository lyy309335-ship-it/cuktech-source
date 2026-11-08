# 酷态科 BLE 控制器

这是酷态科 10 号 GaN 充电器 Ultra 的本地 BLE 控制器和 Web 面板。项目通过本机蓝牙直连设备，不依赖云端控制。公开版本不包含真实设备 MAC、token、BLE key、日志、bugreport 或抓包文件。

完整教程请看：[从零开始使用 CUKTECH BLE Controller](docs/getting-started.zh-CN.md)

## 快速开始

```powershell
cd cuktech-ble-controller
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

配置环境变量：

```text
CUKTECH_DEVICE_MAC=AA:BB:CC:DD:EE:FF
CUKTECH_DEVICE_TOKEN=你的12字节hex token
CUKTECH_DEVICE_BLE_KEY=可选的16字节hex BLE key
CUKTECH_API_KEY=可选的本地API密钥
CUKTECH_HOST=127.0.0.1
CUKTECH_PORT=8080
```

运行命令：

```bash
python -m cuktech_ble_controller scan
python -m cuktech_ble_controller status
python -m cuktech_ble_controller web
```

Web 面板默认地址是 `http://127.0.0.1:8080`。如需局域网访问，请明确设置 `CUKTECH_HOST=0.0.0.0` 并配置 `CUKTECH_API_KEY`。

## 安全提醒

不要提交真实 MAC、token、BLE key、API key、数据库、日志、bugreport、抓包文件或手机诊断包。获取 token 的第三方工具请从原项目使用，本仓库只提供链接，不复制其代码。
