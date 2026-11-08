# 酷态科 10 号充电器 · Windows 桌面控制台（中文速览）

酷态科 10 号 GaN 充电器 Ultra 的本地蓝牙控制器。Windows 电脑通过 BLE 直连充电器，
不经过米家云、不需要网关，就能实时看四个口的功率、开关端口、切场景模式、看历史曲线，
并把桌面卡片和托盘读数常驻在屏幕上。

**完整说明（功能清单、系统要求、目录结构、源码运行方式）见 [README.md](README.md)。**

## 三句话上手

1. 到 [Releases](https://github.com/lyy309335-ship-it/cuktech-source/releases/latest) 下载
   `CUKTECH-Setup_v2.0.0.exe`，双击安装（不需要管理员权限）。
2. 首次启动用米家 App 扫窗口里的二维码，程序自动写入凭据并连上充电器。
3. 平时只用两样东西：托盘图标（悬停看功率）和桌面上的小卡片（点端口直接开关）。

## 只支持 Windows

当前版本只支持 **Windows 10 / 11（64 位）**：界面依赖 WebView2，桌面卡片是桌面窗口的子窗口，
托盘走 Windows 通知区域，这些都没有 Linux / macOS 实现。装好后完整手册在程序目录，
仓库里是 [`docs/使用说明.md`](docs/使用说明.md)。

## 安全提醒

公开仓库不含真实 MAC、token、BLE key、日志或抓包文件。凭据只写在本机
`%LOCALAPPDATA%\CUKTECH\.env`，不要外发；要开给局域网请先设置 `CUKTECH_API_KEY`。
详见 [`SECURITY.md`](SECURITY.md)。
