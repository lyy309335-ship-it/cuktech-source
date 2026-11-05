# 从零开始使用 CUKTECH BLE Controller

这份教程面向第一次使用本项目的用户，目标是让你从设备准备、获取正确 token、配置环境变量，到成功读取充电器状态。

## 1. 你需要准备什么

- 一台支持蓝牙的 Windows / Linux / macOS 电脑。
- Python 3.9 或更高版本。
- 一台已通电、可被蓝牙扫描到的 CUKTECH 10 号 GaN 充电器 Ultra。
- 绑定过该充电器的米家 / Xiaomi Home 账号。
- 该账号所在地区信息。如果不确定，token 工具可以尝试扫描全部地区。

注意：本项目不需要小米云控制设备，但首次获取设备 token 需要登录绑定过设备的小米账号。

## 2. 安装项目

```powershell
git clone https://github.com/zhyzhaogit/cuktech-ble-controller.git
cd cuktech-ble-controller
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Linux / macOS:

```bash
git clone https://github.com/zhyzhaogit/cuktech-ble-controller.git
cd cuktech-ble-controller
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 3. 获取设备 MAC

先让电脑靠近充电器，然后运行：

```bash
python -m cuktech_ble_controller scan
```

输出里会列出附近 BLE 设备。找到类似 CUKTECH、MiBeacon、Xiaomi、或者信号最强且与你充电器相关的设备，记录它的 MAC 地址，格式类似：

```text
AA:BB:CC:DD:EE:FF
```

如果扫描不到：

- 确认电脑蓝牙已开启。
- 确认充电器已通电。
- 关闭手机上可能正在连接该设备的米家 App。
- 尽量靠近设备重新扫描。

## 4. 获取正确的 token

本项目当前认证真正需要的是：

- `CUKTECH_DEVICE_MAC`
- `CUKTECH_DEVICE_TOKEN`

其中 `CUKTECH_DEVICE_TOKEN` 必须是你自己充电器的 **12 字节 hex token**，也就是 **24 个十六进制字符**。

### 4.1 什么不是正确 token

不要把下面这些值填到 `CUKTECH_DEVICE_TOKEN`：

- 小米账号密码。
- `serviceToken`。
- `passToken`。
- `CUKTECH_API_KEY`。
- Web UI 访问密钥。
- 其他设备的 token。
- Midea / msmart 设备的长 token。
- BLE key、bind key、beacon key。

`CUKTECH_API_KEY` 只是本项目 Web API 的访问保护密钥，和设备认证无关。

### 4.2 推荐工具

使用上游项目 Xiaomi Cloud Tokens Extractor：

https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor

本仓库不复制该工具代码，原因是避免许可证、维护和安全风险。请从原项目下载或运行，并遵守它的说明。

### 4.3 Windows 获取流程

1. 打开上游项目 release 页面。
2. 下载 Windows 版本的 `token_extractor.exe`。
3. 运行工具。
4. 按提示登录绑定过充电器的 Xiaomi Home / 米家账号。
5. 如果支持二维码登录，优先使用二维码登录，避免在命令行输入密码。
6. 选择你的账号地区；不确定时可按工具说明尝试全部地区。
7. 在输出设备列表中找到 CUKTECH 充电器。
8. 记录该设备的：
   - `MAC`
   - `TOKEN`
   - 如果工具输出 BLE 相关 key，也可以先保存，但当前本项目主要使用 `TOKEN`。

你要填入本项目的是该设备条目下的 `TOKEN`，不是账号登录过程中的临时 token。

### 4.4 Linux / macOS 获取流程

按上游项目 README 操作。常见手动方式是：

```bash
python3 -m venv token-env
source token-env/bin/activate
python3 -m pip install -r requirements.txt
python3 token_extractor.py
```

具体命令以 Xiaomi Cloud Tokens Extractor 当前 README 为准。

### 4.5 如何判断 token 看起来正确

正确的 `CUKTECH_DEVICE_TOKEN` 应该满足：

- 只包含 `0-9`、`a-f`、`A-F`。
- 长度是 24 个 hex 字符。
- 来自你绑定 CUKTECH 充电器的设备条目。
- 不是空值，不是全 0，不是示例值。

示例格式：

```text
00112233445566778899aabb
```

上面只是格式示例，不是真实 token。

## 5. 配置环境变量

复制示例文件：

```bash
copy examples\.env.example .env
```

PowerShell 用户可以直接设置当前终端环境变量：

```powershell
$env:CUKTECH_DEVICE_MAC="AA:BB:CC:DD:EE:FF"
$env:CUKTECH_DEVICE_TOKEN="00112233445566778899aabb"
$env:CUKTECH_HOST="127.0.0.1"
$env:CUKTECH_PORT="8080"
```

Linux / macOS:

```bash
export CUKTECH_DEVICE_MAC="AA:BB:CC:DD:EE:FF"
export CUKTECH_DEVICE_TOKEN="00112233445566778899aabb"
export CUKTECH_HOST="127.0.0.1"
export CUKTECH_PORT="8080"
```

如果你要让局域网内其他设备访问 Web UI，再设置：

```bash
CUKTECH_HOST=0.0.0.0
CUKTECH_API_KEY=设置一个你自己的长随机密钥
```

不要把真实 `.env` 提交到 GitHub。

## 6. 验证连接

先确认能扫描：

```bash
python -m cuktech_ble_controller scan
```

再读取基础信息：

```bash
python -m cuktech_ble_controller info
```

测试认证：

```bash
python -m cuktech_ble_controller auth
```

如果认证成功，再读取状态：

```bash
python -m cuktech_ble_controller status
```

成功后你应该能看到充电器状态、设置项或端口数据。

## 7. 启动 Web UI

```bash
python -m cuktech_ble_controller web
```

默认打开：

```text
http://127.0.0.1:8080
```

如果设置了 `CUKTECH_API_KEY`，浏览器首次访问首页会写入本地 session cookie；外部 API 客户端需要带上：

```text
X-API-Key: 你的密钥
```

## 8. 常见问题

### scan 找不到设备

- 充电器必须通电。
- 电脑蓝牙必须可用。
- 关闭手机米家 App 或其他可能占用连接的软件。
- 尽量靠近充电器。

### auth 失败

优先检查：

- `CUKTECH_DEVICE_TOKEN` 是否正好 24 个 hex 字符。
- token 是否来自同一个充电器。
- 账号地区是否选错。
- 充电器是否已在米家中重新绑定，导致 token 变化。
- 是否误填了 `serviceToken`、`passToken`、BLE key 或 API key。

### token extractor 登录失败

可尝试：

- 确认使用的是 Xiaomi Home / 米家账号。
- 使用二维码登录。
- 检查 2FA 邮件，包括垃圾箱。
- 暂时关闭会影响登录的 DNS 过滤、广告过滤、代理或防火墙规则。
- 换一个网络环境后重试。

### Web UI 能打开但没有数据

- 先在命令行确认 `auth` 和 `status` 可以成功。
- 确认 Web 服务是在设置环境变量之后启动的。
- 如果浏览器和服务不在同一台机器，确认 `CUKTECH_HOST` 和 `CUKTECH_API_KEY` 配置正确。

## 9. 安全提醒

以下内容都不要公开：

- 真实 MAC。
- 真实 `CUKTECH_DEVICE_TOKEN`。
- BLE key / bind key / beacon key。
- 小米账号、密码、`serviceToken`、`passToken`。
- `.env` 文件。
- 日志、数据库、bugreport、抓包文件。

如果这些内容已经提交到 GitHub，仅删除最新文件不够，还需要清理 Git 历史并更换相关凭据。
