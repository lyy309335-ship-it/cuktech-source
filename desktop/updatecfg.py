"""更新源地址（编译进 exe 的常量）。

`REPO` 留空 = 未配置，程序不做更新检查（`updater.manifest_url()` 返回空串）。
构建脚本 `build_installer.py --publish` 会用 `gh repo view` 读到的仓库名自动填这里，
所以正常情况下不用手改。

地址形态是 `https://github.com/<repo>/releases/latest/download/update.json`：

- `releases/latest` 永远指向最新的正式 release，**这个 URL 不随版本变化**。
  换安装包、换版本号都不用改代码 —— 这是选 GitHub Releases 的最大好处。
- 它只认正式 release：draft 和 pre-release 会被跳过，正好符合「灰度测试别发给用户」。
- 仓库必须是**公开的**：私有仓库的 release 下载要带 token，匿名拉不到。
"""

from __future__ import annotations

REPO = "lyy309335-ship-it/cuktech-charger-control"   # 形如 "owner/repo"
MANIFEST = "update.json"
