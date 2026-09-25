# 安全策略

## 数据边界

这个工具**完全本地运行**，不发送遥测、不上报任何数据。它读写的全部内容：

| 内容 | 位置 | 说明 |
|------|------|------|
| NFO 文件 | 你自己选择的媒体库目录 | 只读写 `tag` / `genre` 等元数据字段，不动视频 |
| 配置 | 数据目录下的 `settings.json` | 含你的路径与 Jellyfin API Key |
| 映射规则 | 数据目录下的 `rules.json` | |
| 破解档案 | 数据目录下的 `archive.db` | SQLite，含文件路径与操作留痕 |
| 日志 | 数据目录下的 `logs/` | 含被处理文件的路径 |
| 备份 | 与 NFO 同目录的 `.nfo.bak` | 改前副本 |

### 网络出口（全部由使用者显式触发）

| 目标 | 触发条件 | 是否可能带出数据 |
|------|---------|----------------|
| `http://localhost:11434`（本地 Ollama） | 使用「标签整理 / AI 复核 / 补全稀疏标签」 | 否，请求不出本机；且已显式绕过系统代理 |
| 你填写的 Jellyfin 地址 | 点「测试连接 / 同步 / 全库刷新」 | 仅标签与类型，无文件内容 |
| `https://dashscope.aliyuncs.com` | **仅当**你主动把 AI 引擎切到云引擎并填入自己的 API Key | 会把标签词条发往阿里云 |

代码里没有任何硬编码的第三方地址或遥测端点。`tests/test_privacy.py` 会持续检查这一点。

### 密钥处理

- Jellyfin API Key 只存在本地 `settings.json`（该文件已被 `.gitignore` 排除）
- 界面上以密码模式回显，不写入日志
- **仓库里绝不含任何真实密钥**；`config.example.json` 中该字段为空

## 报告安全问题

请**不要**通过公开 Issue 报告安全或隐私问题（那会让问题在你修复之前就公开）。

推荐方式：

1. GitHub 的 [Private vulnerability reporting](https://docs.github.com/zh/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
   （仓库 → Security → Report a vulnerability）
2. 或在 Issue 里**只写**"有一个安全问题，请提供私下联系方式"，不要包含细节

请附上：

- 受影响的版本（`--version` 输出）
- 复现步骤（越具体越好）
- `--doctor` 的输出（含环境信息，便于判断是否平台相关）

## 已知的非目标

- 本工具不校验 NFO 的来源可信度。**处理来路不明的 NFO 前请先备份**——虽然
  `xml.etree` 默认不解析外部实体，但任何解析器面对恶意构造的 XML 都可能出问题。
- 本工具不对媒体库内容的合法性作任何判断，请自行确保你对所处理的数据拥有合法权利。
