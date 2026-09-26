# 更新日志

本文件记录每个版本的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- `scripts/versions.py`：版本查阅与回滚助手。`list` 列出所有版本（日期 / 提交 /
  本地归档状态 / 变更摘要）、`show` 看某版本详情与相对上一版改了哪些文件、
  `diff` 比两版差异、`export` 把旧版源码导出到 `_versions/<tag>/`、
  `restore` 按场景打印回滚步骤（只打印，不替你执行破坏性操作）。
  只读 git、只写新目录，`export` 遇到非空目标目录会拒绝（除非 `--force`）。
- 构建时自动把发行包归档到 `release/vX.Y.Z/`，并写一份 `build-manifest.json`
  （构建时间 / Python 与 PyInstaller 版本 / git 提交 / `git_dirty` / SHA256）。
  理由是 `dist/` 每次构建都被清空、CI artifact 有保留期会过期，
  **长期可离线检索的历史发行包此前无处可放**。工作区不干净时会告警 ——
  那种包不完全等于标签内容。
- `docs/RELEASING.md` 新增「怎么找回旧版本、怎么回滚」一节：为什么不需要
  "每个版本一个文件夹"、按场景选择回滚方式（含"已发布过的提交不要 `reset`"）、
  发行包的两个长期归档点、以及数据文件与代码版本解耦带来的兼容性注意点。

### 测试

- `tests/test_versions_script.py`（15 个用例）：在真实仓库上跑 CLI，
  覆盖导出内容正确性（无 `.git`、无本地配置泄漏）、非空目录保护、
  `--force` 不删除原有文件、`restore` 只打印不动手，
  以及"工作区有未提交改动时必须先警告"。

## [1.3.0] - 2026-09-26

首个公开发布版。相对内部使用版本的核心变化是**发布加固**：修掉了几处"点一下才崩"
的缺陷、把只在本机能跑的实现改成跨机器可用、补齐 GitHub 发布所需的一切。

### 修复

- **键盘交互全线失效**：`ui/widgets.py` 里用了没导入的 `QKeySequence`，导致
  `CheckTable.keyPressEvent` 在**任意按键**时抛 `NameError`（Ctrl+A / Ctrl+U /
  Ctrl+I / Ctrl+Z 全部失效）。界面测试只验证"能构造"，所以一路带到了发布。
- **「AI 复核有码/无码」必崩**：`ui/pages/fixer.py` 里用了没导入的 `QApplication`。
- **「检索打分」必崩**：`ui/pages/finder.py` 里用了没导入的 `QTableWidgetItem`，
  破解找回页只要刷新一次表格就挂。
- **空根元素无法写入**：`core/nfo.py` 用 `if not self.root` 判空，而 ElementTree 的
  `Element.__bool__` 判的是"有没有子节点"，于是 `<movie></movie>` 这种根会被当成
  不存在，`add_tag()` 静默失败。全部改成 `is None` 判断（顺带消除 DeprecationWarning）。
- **修改被静默丢弃**：`NfoFile.save()` 增加了脏标记，但映射引擎、整理执行、演员补入、
  字段替换、破解找回这些"直接改 XML 树"的路径没有标脏，于是点「应用」后文件没变、
  也不报错。现在这些路径显式 `mark_dirty()`，并由 `tests/test_write_paths.py` 逐条守住。
- **浅色主题下表格文字几乎不可见**：文件名等单元格用的是按暗色背景设计的近白常量
  (`#d7dae0`)，白底上阅读困难。新增 `ui.styles.cell_color()` 按主题映射，
  并加测试强制新颜色必须登记浅色等效值。
- **「关联视频」的兜底分支从来没生效过**：`finder.find_video_for()` 里
  `base.startswith(stem.split("-")[0])` 的 `stem` 是**完整路径**（`os.path.splitext`
  的结果），于是拿"目录路径 + 文件名前缀"去和一个纯文件名比，永远为假。
  后果：`abc-123.nfo` 旁边叫 `abc-123-1080p.mp4` 的视频关联不上 ——
  列表里文件大小/修改时间空白、「破解找回」的"文件大小异常"线索失效、
  **「健康检查」把这些条目误报成"孤儿 NFO"**。
  已改为"以 NFO 主名开头 + 紧跟分隔符"的精确规则（刻意不接受"主名直接跟数字"，
  因为那和 `abc-1234.mp4` 这样的另一部作品无法区分），并对目录列举排序
  以保证重复扫描结果一致。
- 清理「从列表移除」的数据/视图失步：表格的右键"移除"原先直接 `removeRow()`，
  会让页面持有的数据列表与视图不一致，下次刷新被删的行又"复活"。改为发信号交页面处理。
- 补齐 `requirements.txt`：原先只写了 `PySide6`，缺 `requests`（AI 分析 / Jellyfin 同步）
  与 `send2trash`（回收站），干净机器上装完打开相关页面会直接 ImportError。
- 修掉配置里的两个死字段（写进 JSON 但没有任何入口/用途）。
- 修掉字典字面量重复键、lambda 赋值、无占位符 f-string 等静态问题。

### 变更

- **数据目录可移植**：新增 `core/appdirs.py`。优先环境变量 `NFO_TAG_FIXER_HOME` →
  `portable.txt` 便携模式 → 程序目录（可写时）→ 用户数据目录
  （`%APPDATA%` / `Application Support` / XDG）。原先一律写程序目录，
  装在 `C:\Program Files` 会因为无法写配置而**启动即崩**。
- **配置自愈**：加载时清除本机不可达的路径字段（如换机器后不存在的盘符），
  回退到默认值，而不是让档案库 `makedirs` 抛错拖垮启动。
- **字体探测**：不再写死 `Microsoft YaHei UI`，按平台候选列表校验真实存在后选用。
- **ffprobe 多路定位**：用户配置 > 程序目录及内置子目录 > PATH > 各平台常见安装位置；
  设置页「自动检测」由 Windows 专属的 `where` 改为跨平台实现。
- **配置写入原子化**，并用真实写文件探测目录可写性（`os.access` 会被只读共享/ACL 骗过）。
- **NFO 写入原子化**（临时文件 + `os.replace`），且内容无变化时不写盘（保护 mtime）。
- **日志目录不可写时静默降级**，不再因为日志失败让软件起不来。
- **档案库打不开时降级为内存库**并把原因记在 `AppState` 日志里，界面照常可用。
- 本地 Ollama 请求统一显式绕过系统代理（用户环境常有代理软件拦 localhost）。
- 打开所在文件夹改为跨平台实现（Windows 下自动选中文件）。

### 性能

- `FixerPage` / `FinderPage` 的「按行取对象」从线性扫描改为索引字典，
  筛选与刷新不再随文件数平方增长（上万 NFO 时原本要卡好几秒）。
- `_groups()` 分组计算结果缓存；原先「破解找回」应用阶段每个文件都重算一遍全部
  候选的标记，整体是 O(文件数²)。
- 映射预览只在规则真正命中时才序列化 XML，且同一文件的"改前"内容只算一次。
- 修改时间聚类改为排序后单遍扫描（保持原有 first-fit 语义）。
- 档案库批量写入一次事务提交；启用 WAL 与 `synchronous=NORMAL`。

### 新增

- `--doctor` 环境自检（数据目录可写性、ffprobe、可选依赖、硬件编码器**实测**），
  以及设置页的「环境自检」按钮（可一键复制）。
- `version.py` 作为版本号唯一来源。
- 测试体系：核心解析/映射/整理规则、写盘路径全覆盖、数据目录与配置自愈、
  外部工具定位、GUI 冒烟（每页构造 + 导航 + 键盘交互）、静态审计守卫、隐私守卫、
  主题配色守卫。
- GitHub 发布配套：`LICENSE`、`.gitignore`、`.gitattributes`、`CHANGELOG.md`、
  `CONTRIBUTING.md`、`SECURITY.md`、`config.example.json`、`pyproject.toml`、
  CI（测试矩阵 + 隐私守卫）与自动发版 workflow、Issue 模板、
  `scripts/build_release.py` 打包脚本、`tools/make_icon.py` 图标生成。

### 移除

- 一批写死本机路径的一次性开发脚本（数据分析、诊断、临时回归），
  它们既是隐私泄露源也是维护负担；同类能力已由 `tests/` 与 `tools/dev/` 覆盖。
- 未被任何页面使用的 `DragDropTableView`（含上文那个数据/视图失步缺陷）。
