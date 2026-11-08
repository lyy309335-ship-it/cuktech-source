# CUKTECH 10 号充电器 · Windows 桌面控制台

> 酷态科 10 号 GaN 充电器 Ultra（`njcuk.fitting.ad1204`）的本地蓝牙控制器：Windows 电脑通过 BLE
> 直连充电器，不经过米家云、不需要网关，就能实时看功率、开关端口、切场景模式、看历史曲线。
>
> A local BLE console for the CUKTECH 10 GaN Charger Ultra. Windows only, no cloud in the loop.

## 这个程序是做什么的

把充电器插在常开的 Windows 电脑旁，这个程序就顶替了米家网关的位置：

- **实时读数**：C1 / C2 / C3 / A 四个口各自的电压、电流、功率与当前协商协议，加总功率。
- **控制**：逐口开关、逐口倒计时、场景模式（AI / 数码生态 / 单口 / 均衡）、息屏时间、语言、
  USB-A 小电流常开、空闲息屏、屏幕方向锁。
- **历史曲线**：本地 SQLite 按分钟记账，页面上一张按「日期 / 端口 / 粒度」切换的双线图
  （所选端口功率 + 全场总功率），断网也能画。
- **常驻**：桌面小卡片 + 托盘图标直接显示瓦数，可开机自启、静默自动更新。
- **自动补开**：设好时间窗（默认 07:00–12:00）和端口范围，程序启动时把关着的口补开 ——
  专治「晚上设了 60 分钟倒计时断电，第二天口是关的，米家里又没网关」。
- **凭据**：首次启动弹米家登录二维码，手机扫一下就自动写入 MAC / TOKEN，不用手抄、不用命令行。

不需要 root，不上传任何数据，凭据只落在本机 `%LOCALAPPDATA%\CUKTECH\.env`。

## 系统要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | **Windows 10 / 11（64 位）**。Linux、macOS 暂不支持 |
| WebView2 | 界面依赖微软 WebView2 运行时；Win11 与打过 Edge 更新的 Win10 自带，缺了启动时会提示并给下载链接 |
| 蓝牙 | 笔记本自带即可；台式机需要蓝牙适配器 |
| 账号 | 一个绑定过这台充电器的米家 / 小米账号（只在首次扫码取凭据时用） |

## 安装（给使用者）

1. 到 [Releases](https://github.com/lyy309335-ship-it/cuktech-source/releases/latest) 下载
   `CUKTECH-Setup_v2.0.0.exe`。
2. 双击安装，勾选需要的组件，全程不需要管理员权限。
3. 首次启动按提示用米家 App 扫码，之后程序常驻托盘。

装完默认开机自启，可以在「运行管理」里关掉。完整图文手册（安装 / 每个功能怎么用 / 排障）：
[`docs/使用说明.md`](docs/使用说明.md)。程序目录里另有一份 `版本更新说明.md`，记着这一版改了什么。

## 目录结构

| 路径 | 是什么 | 里面有什么 |
| --- | --- | --- |
| `main.py` | 唯一可执行入口，一个 exe 按参数分五种角色 | 管理器 / 采集服务 / 桌面卡片 / 凭据向导 / 更新进程 |
| `cuktech.py` | 采集服务的启动器 | 读 `.env`、起本地 8080、拉起 BLE 连接，供 `--service` 角色调用 |
| `widget_app.py` | 桌面卡片窗口 | pywebview 宿主、卡片拖动与钉住、服务失联时的看门狗 |
| `desktop/` | Windows 外壳 | `manager.py` 托盘与双窗口宿主、`autoport.py` 自动补开端口、`updater.py` 自动更新、`credentials.py` 凭据读写、`credential_wizard.py` 扫码向导、`wv2env.py` WebView2 数据目录隔离、`icon.py` 托盘图标点阵绘制、`version.py` 版本号唯一来源 |
| `web/` | 控制台前端 | `index.html`（Chart.js + WebSocket，含合帧与脏检查）、`vendor/` 离线图表库 |
| `src/cuktech_ble_controller/` | 上游 BLE 服务（MIT） | `ble.py` 协议与 MiOT 认证、`server.py` HTTP / WebSocket 接口、`__main__.py` 命令行入口 |
| `installer/` | 安装器（tkinter，onefile） | 解包 payload、写快捷方式、注册卸载项、复制更新说明 |
| `docs/` | 文档 | 用户手册、环境变量、协议备注、排障、发行方案 |
| `tools/` | 调试与取码脚本 | CDP 取值、窗口截图、报文探针、二维码监听、打包后冒烟测试 |
| `tests/` | 不依赖硬件的回归测试 | 配置解析、控制台看门狗 |
| `examples/` | 示例启动脚本 | `run-web.bat` / `run-web.ps1` |
| `integrations/openclaw/` | 可选的 OpenClaw 集成 | `cuktech-charger` skill 与本地 HTTP 客户端脚本 |
| 根目录 `*.html` / `*.js` | 随程序分发的页面资源 | `widget.html` 卡片、`console_host.html` 服务未就绪时的引导页、`credential_wizard.html` 扫码向导、`console_overlay.js` 运行管理浮层 |
| `build_installer.py`、`CUKTECH.spec` | 一键出包 | onedir 应用 + onefile 安装器，含版本一致性与凭据门禁 |

## 从源码运行（给开发者）

需要 Python 3.9+ 与 Windows。只跑命令行 / Web 面板：

```powershell
git clone https://github.com/lyy309335-ship-it/cuktech-source.git
cd cuktech-source
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

要跑完整桌面端（托盘 + 控制台窗口 + 桌面卡片），再补三个界面依赖
（`pyproject.toml` 里也有对应的 `desktop` 可选组）：

```powershell
python -m pip install pywebview pystray pillow
# 或者：python -m pip install -e ".[desktop]"
```

配置：复制根目录的 `.env.example` 为 `.env`，填自己的设备凭据：

```text
CUKTECH_DEVICE_MAC=AA:BB:CC:DD:EE:FF
CUKTECH_DEVICE_TOKEN=your-12-byte-hex-token
CUKTECH_DEVICE_BLE_KEY=optional-16-byte-hex-ble-key
CUKTECH_API_KEY=optional-local-api-key
CUKTECH_HOST=127.0.0.1
CUKTECH_PORT=8080
```

变量含义见 [`docs/configuration.md`](docs/configuration.md)；第一次怎么拿到 token 见
[`docs/getting-started.zh-CN.md`](docs/getting-started.zh-CN.md)。

常用命令：

```powershell
python -m cuktech_ble_controller scan      # 扫附近的 BLE 设备
python -m cuktech_ble_controller status    # 读充电器状态
python -m cuktech_ble_controller web       # 起本地 Web 面板（默认 127.0.0.1:8080）
python main.py                             # 起完整桌面端（管理器 + 采集服务 + 卡片）
```

出安装包：

```powershell
.\.venv\Scripts\python.exe build_installer.py
```

产物是 `dist\CUKTECH-Setup.exe`。构建门禁会核对版本号、`CHANGELOG.md` 里有没有当前版本的小节、
文档里写死的安装包名是不是当前版本，对不上直接中止出包。

## 版本与历史

- 版本号唯一来源：`desktop/version.py`，构建时自动同步到 `pyproject.toml` 与
  `src/cuktech_ble_controller/__init__.py`。
- 每次改动的说明记在 [`CHANGELOG.md`](CHANGELOG.md)。

## 安全

公开仓库不含任何真实 MAC、token、BLE key、日志或抓包文件。不要提交真实凭据，规则见
[`SECURITY.md`](SECURITY.md)。Web 服务默认只监听 `127.0.0.1`；要开给局域网请先设置
`CUKTECH_API_KEY`。

## 许可证

MIT，见 [`LICENSE`](LICENSE)。BLE 协议实现版权归上游
[kairui1108/cuktech-ble-server](https://github.com/kairui1108/cuktech-ble-server) 作者所有（MIT）；
图表使用 [Chart.js](https://www.chartjs.org/)（MIT）。
