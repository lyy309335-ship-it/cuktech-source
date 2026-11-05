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
  （默认当天 + 按分钟，同一张图里「所选端口 vs 总功率」两条线对照）
- **桌面卡片**：贴在桌面上的小窗，显示总功率和前两口状态（可单独关闭省内存）
- **托盘常驻**：左键唤出控制台，右键进「运行管理」（采集服务 / 桌面卡片 / 开机自启 / 自动开启设置）
- **自动开启设置**：在所设时间窗内启动时，检查所选充电口，有未开启的自动打开
- **全本地**：BLE 直连充电器，不经过云端；Web 服务只监听 `127.0.0.1`

## 安装（Windows 10/11，64 位）

从 [Releases](../../releases) 下载 `CUKTECH-Setup.exe` 双击安装，装完在桌面和托盘里启动。

首次启动前需要填设备凭据，见下一节。

## 配置设备凭据

设备 MAC / TOKEN / BLE KEY 这三项要用你自己的米家账号获取，缺一不可。

**方式一（推荐）**：双击根目录的 `取硬件码.bat`
扫码登录米家 → 脚本从取码结果里解析出三项并**自动写入配置**，跑完直接启动软件就行。

**方式二**：软件首次启动时会弹「设置设备凭据」向导
把取码窗口里的内容**整段粘进去**即可（不用挑格式，会自动识别）；也可以「从文件导入」或点「打开取码工具」直接取码。
运行中随时可以从托盘菜单 →「设置设备凭据…」重新打开。

**配置存在哪**：`%LOCALAPPDATA%\CUKTECH\.env`

```ini
CUKTECH_DEVICE_MAC=AA:BB:CC:DD:EE:FF
CUKTECH_DEVICE_TOKEN=你的24位token
CUKTECH_DEVICE_BLE_KEY=你的32位blekey
```

放在这里而不是安装目录，是因为安装目录重装时会被清空；这个位置可写、可查（托盘「打开配置目录」直达），
升级和重装都不受影响。程序启动时按 `环境变量 → %LOCALAPPDATA%\CUKTECH\.env → 便携目录 → 包内空模板`
的顺序查找；一旦用户配置存在就只认它，读坏了会回落到 `.env.bak`。

**`.env` 已在 `.gitignore` 中，不会被提交。** 打包时也只把 `.env.example` 的空模板打进安装包
（`build_installer.py` 里有构建门禁，检测到真实值会直接中止出包）。详见 [GitHub/SECURITY.md](GitHub/SECURITY.md)。

## 从源码运行

```powershell
cd GitHub
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # 然后填入凭据
.venv\Scripts\pythonw main.py
```

> 装了打包版就不需要源码运行了，而且两边会抢 8080 与蓝牙连接——先退出安装版再跑源码。

## 打包安装包

```powershell
cd GitHub
.venv\Scripts\python build_installer.py
```

产物：`GitHub\dist\CUKTECH-Setup.exe`（安装器）、`GitHub\dist\CUKTECH\`（onedir 应用）。
出包后脚本会自动把它归档成 `安装包\CUKTECH-Setup_v版本号.exe`、刷新 `分发\` 里的那一份，并同步两份文档；
构建前还会校验版本号 / 更新说明 / 文档里的安装包名是否一致（对不上直接中止出包）。
安装器支持 `/S` 静默安装、`--uninstall` 图形卸载。

## 技术要点

| 部分 | 说明 |
|---|---|
| `GitHub/src/cuktech_ble_controller/` | 上游 BLE 服务（MIT），负责连接、MiOT 认证、HTTP/WebSocket |
| `GitHub/desktop/manager.py` | 管理器：托盘、双窗口宿主、子进程看护、单实例互斥 |
| `GitHub/desktop/autoport.py` | 「自动开启设置」的时间窗与端口范围逻辑 |
| `GitHub/desktop/wv2env.py` | WebView2 环境隔离（两个窗口必须各用一套 user data folder） |
| `GitHub/console_overlay.js` | 注入控制台页面的「运行管理」浮层 |
| `GitHub/web/index.html` | 控制台前端（Chart.js + WebSocket，带合帧与脏检查；动画只走 transform / 合成层，不碰 layout） |

## 目录

```
.
├── GitHub/                 # 源码仓库工作区（改代码只动这里）
│   ├── desktop/            # 桌面外壳（托盘、窗口、自启、自动开启）
│   ├── src/                # 上游 BLE 服务
│   ├── web/                # 控制台前端
│   ├── installer/          # 安装器（tkinter）
│   ├── docs/               # 配置、协议、排错文档
│   ├── .venv/  data/  logs/  dist/   # 运行环境、运行数据、日志、构建产物
│   └── build_installer.py  # 一键出安装包
├── 安装包/                  # 版本档案库（只增不改）
│   ├── CUKTECH-Setup_v1.5.0.exe
│   └── 历史/               # v1.1.0 / 1.1.1 / 1.1.2 / 1.2.0 / 1.4.1 + 未标注的中间构建
├── 分发/                    # 整个文件夹可直接发给别人（安装包 + 取码工具 + 文档）
├── tools/                  # 调试脚本与取码工具原件（CDP 取值、窗口截图、报文探针…）
├── 取硬件码.bat            # 扫码取 MAC / TOKEN / BLE KEY 并写入配置
└── 关闭蓝牙省电.bat        # 关掉蓝牙适配器的省电挂起（需管理员）
```

## 许可证

本项目遵循 **MIT License**，见 [GitHub/LICENSE](GitHub/LICENSE)。
BLE 协议实现部分版权归上游 [kairui1108/cuktech-ble-server](https://github.com/kairui1108/cuktech-ble-server) 作者所有，同为 MIT。
前端图表使用 [Chart.js](https://www.chartjs.org/)（MIT）。
