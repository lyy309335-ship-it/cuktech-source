# CUKTECH 充电器控制台（Windows 桌面版）

给 **酷态科 10 号 GaN 充电器 Ultra** 用的 Windows 桌面控制台：托盘常驻、桌面小卡片、
四口实时功率、场景模式、协议开关、充电历史曲线，以及一套「运行管理」面板。

核心 BLE 协议实现来自上游开源项目 [kairui1108/cuktech-ble-server](https://github.com/kairui1108/cuktech-ble-server)（MIT）。
本项目在它之上加的是 **Windows 一侧的桌面外壳**：pywebview 双窗口、Progman 桌面贴附、
pystray 托盘、PyInstaller 安装器、开机自启、自动开启设置。

> 非官方项目，与酷态科官方无关。本项目不收集、不上传任何数据，所有通信只在本机 `127.0.0.1` 内完成。

---

## 功能

- **控制台窗口**：四口实时功率 / 电压电流、总功率、场景模式、每口开关、历史功率曲线
- **桌面卡片**：贴在桌面上的小窗，显示总功率和前两口状态（可单独关闭省内存）
- **托盘常驻**：左键唤出控制台，右键进「运行管理」（采集服务 / 桌面卡片 / 开机自启 / 自动开启设置）
- **自动开启设置**：在所设时间窗内启动时，检查所选充电口，有未开启的自动打开
- **全本地**：BLE 直连充电器，不经过云端；Web 服务只监听 `127.0.0.1`

## 安装（Windows 10/11，64 位）

从 [Releases](../../releases) 下载 `CUKTECH-Setup.exe` 双击安装，装完在桌面和托盘里启动。

首次启动前需要填设备凭据，见下一节。

## 配置设备凭据

设备 MAC / TOKEN / BLE KEY 需要用你自己的米家账号获取（三者缺一不可）：

1. 双击根目录 `运行-获取Token.bat`，按提示扫码登录米家
2. 记下输出里 CUKTECH 那条的 **MAC**、**TOKEN**（24 位十六进制）、**BLE KEY**（32 位十六进制）
3. 把 `app/.env.example` 复制成 `app/.env`，填入上面三个值

> `tools/token_extractor.exe` 不随源码分发（19 MB 第三方二进制）。从 [Releases](../../releases) 下载放进
> `tools/` 即可，或自行使用 [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)。

```ini
CUKTECH_DEVICE_MAC=AA:BB:CC:DD:EE:FF
CUKTECH_DEVICE_TOKEN=你的24位token
CUKTECH_DEVICE_BLE_KEY=你的32位blekey
```

**`.env` 已在 `.gitignore` 中，不会被提交。** 详见 [app/SECURITY.md](app/SECURITY.md)。

## 从源码运行

```powershell
cd app
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # 然后填入凭据
.venv\Scripts\pythonw main.py
```

根目录的 `启动控制台.bat` / `启动桌面卡片.bat` 是源码版的快捷方式（装了打包版就不需要了）。

## 打包安装包

```powershell
cd app
.venv\Scripts\python build_installer.py
```

产物：`app\dist\CUKTECH-Setup.exe`（安装器）、`app\dist\CUKTECH\`（onedir 应用）。
安装器支持 `/S` 静默安装、`--uninstall` 图形卸载。

## 技术要点

| 部分 | 说明 |
|---|---|
| `app/src/cuktech_ble_controller/` | 上游 BLE 服务（MIT），负责连接、MiOT 认证、HTTP/WebSocket |
| `app/desktop/manager.py` | 管理器：托盘、双窗口宿主、子进程看护、单实例互斥 |
| `app/desktop/autoport.py` | 「自动开启设置」的时间窗与端口范围逻辑 |
| `app/desktop/wv2env.py` | WebView2 环境隔离（两个窗口必须各用一套 user data folder） |
| `app/console_overlay.js` | 注入控制台页面的「运行管理」浮层 |
| `app/web/index.html` | 控制台前端（Chart.js + WebSocket，带合帧与脏检查） |

## 目录

```
.
├── app/                    # 主程序
│   ├── desktop/            # 桌面外壳（托盘、窗口、自启、自动开启）
│   ├── src/                # 上游 BLE 服务
│   ├── web/                # 控制台前端
│   ├── installer/          # 安装器（tkinter）
│   └── docs/               # 配置、协议、排错文档
├── tools/                  # 调试脚本（CDP 取值、窗口截图、二维码抓取）
└── *.bat                   # 源码版启动 / 停止 / 自启脚本
```

## 许可证

本项目遵循 **MIT License**，见 [app/LICENSE](app/LICENSE)。
BLE 协议实现部分版权归上游 [kairui1108/cuktech-ble-server](https://github.com/kairui1108/cuktech-ble-server) 作者所有，同为 MIT。
前端图表使用 [Chart.js](https://www.chartjs.org/)（MIT）。
