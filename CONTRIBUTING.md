# 贡献指南

感谢有兴趣改进这个工具。下面几条是踩过坑之后定下来的硬约定，
违反它们会引入**很难被发现**的缺陷，所以请在提交前过一遍。

---

## 环境

```bash
git clone https://github.com/coldeve2022/nfo-tag-fixer.git
cd nfo-tag-fixer
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
```

## 提交前自检（三件事）

```bash
# 1. 静态检查：只留能真的拦住 bug 的规则族
ruff check . --select E4,E7,E9,F --ignore E501,E702,E741

# 2. 全部用例
python -m pytest -q

# 3. 确认没有把个人数据/运行态产物加进来
git add -A -n | grep -E "settings\.json|rules\.json|archive\.db|^add 'logs/|^add 'dist/|^add 'build/"
# ↑ 必须无输出
```

---

## 硬约定

### 1. 直接改 `nfo.root` 之后必须 `nfo.mark_dirty()`

`NfoFile.save()` 靠 `dirty` 标记决定要不要写盘；内容没改过就直接返回
（这是为了不刷掉 mtime —— mtime 是「破解找回」打分的最强线索）。

**后果**：绕开 `NfoFile` 的写方法、直接操作 XML 树却忘了标脏，`save()` 会
**静默跳过写入**——用户点了「应用」什么都没发生，还不报错。

`tests/test_write_paths.py` 逐条覆盖所有这类调用路径。新增一条就要加一条用例。

```python
for el in nfo.root.iter("tag"):
    if ...:
        el.text = new
        changed += 1
if changed:
    nfo.mark_dirty()        # ← 不能漏
```

### 2. 不要写死平台相关的东西

| 反面例子 | 正确做法 |
|---------|---------|
| `QFont("Microsoft YaHei UI")` | `ui.fonts.ui_font()`（探测真实存在的字体族） |
| `subprocess.run(["where", "ffprobe"])` | `core.toolchain.find_tool("ffprobe", cfg)` |
| `os.startfile(path)` | `ui.widgets.open_in_explorer(path)` |
| 默认值里写 `"H:/"` 或 `"J:\\..."` | 留空 → 回退到数据目录下的子目录 |
| `"C:\\Users\\me\\..."` | 用 `core.appdirs.resolve_data_dir()` |

判"某功能可不可用"要**真的跑一次**，不要看编译期列表：
`ffmpeg -encoders` 列出 `h264_nvenc` 只说明**这份构建编译时带了它**，
与**本机有没有 N 卡**无关。见 `core/toolchain.probe_encoder()`。

### 3. 单元格颜色要经 `ui.styles.cell_color()`

浅色主题的表格是白底。直接写按暗色背景设计的近白常量会变成白底白字。
新颜色需要在 `LIGHT_CELL_MAP` 里登记浅色等效值，`tests/test_theme_colors.py`
会强制执行。优先用 `ui.widgets.colored_cell()` / `SortItem()`，它们已内置映射。

### 4. 界面上被调用的函数不要联网

`describe()` 这类"当前生效配置"的提示函数会被页面 `__init__` 和下拉框信号调到。
里面做一次 HTTP 探测，结果就是**一打开设置页卡 5 秒（超时）**。
连接测试单独给一个显式函数（配按钮）。

### 5. 破坏性操作

- 任何批量修改都必须：**先预览 diff → 二次确认 → 改前备份 → 可回滚**
- 删除文件一律走系统回收站（`send2trash`），不可用时降级到本地
  `.nfo_trash_<时间>` 目录，绝不 `os.remove`
- 新增破坏性操作必须配一条用例，覆盖"取消后文件未被改动"

### 6. 长任务放后台线程

重 IO / 网络请求必须走 `ui.worker.run_with_progress`，并提供取消检查：
`fn` 接收 `_worker` 关键字参数，用 `_worker.report(done, total, label)` 报进度、
`_worker.cancelled()` 在批间检查。否则界面会冻结、进度条不动、取消按钮无效。

### 7. 不要提交个人数据

`tests/test_privacy.py` 和 CI 的隐私守卫 job 会检查源码里是否出现
个人路径、真实密钥、真实内网地址，以及仓库里是否存在运行态数据文件。
写测试夹具时用中性虚构示例（`D:\Media\Library`），**不要**把报错现场原样粘进去。

---

## 测试约定

- 用例必须写进 `tests/`，文件名 `test_*.py`（`smoke_test_*.py` 这种**不会被 pytest
  收集**，等于没有测试——历史上就因此漏掉了一个"打包即崩"的缺陷）。
- 所有会弹窗的路径依赖 `no_modal` 夹具（已设 autouse）把模态框打桩；
  离屏模式下 `QMessageBox.exec()` 会把 pytest 永久挂住。
- 经过 `run_with_progress` 的路径用 `sync_worker` 夹具同步执行，避免线程时序不确定。
- 用例绝不能往仓库里写文件：数据根目录由 `NFO_TAG_FIXER_HOME` 重定向到临时目录
  （见 `tests/conftest.py`）。CI 有一步专门检查 `git status --porcelain` 是否为空。

## 加一个新页面

1. 在 `ui/pages/` 新建模块
2. 在 `ui/main_window.py` 注册到 `tabs`
3. 把标签名加进 `tests/test_gui_smoke.py` 的 `NAV_LABELS`
   （该用例会断言侧边栏条目数与页面数一致，漏同步会直接红）

## 提交信息

用常规提交风格，一句话说清"改了什么 + 为什么"：

```
fix(nfo): save() 增加脏标记，避免直接改树时静默跳过写入

映射引擎 / 整理执行 / 演员补入都直接改 nfo.root，没有标脏，
于是点「应用」后文件没变且不报错。补 mark_dirty() 并加逐路径用例。
```

---

## 改 CI / workflow 之前请先读这段

workflow 的失败方式很难自查，每次都要推上去、等 CI、再回来看：
**如果 YAML 被 GitHub 拒绝，run 会直接失败、0 个 job、连一行日志都没有，
Run 标题还会显示文件路径而不是 `name:`。** 这几种写法最容易踩：

| 写法 | 结果 |
|------|------|
| `jobs.<id>.env` 里用 `${{ runner.* }}` | `Unrecognized named-value: 'runner'` —— `runner` 只在 step 级可用 |
| `run: \|` 里嵌**未缩进**的多行 `python -c "..."` | 块标量提前结束，报 `could not find expected ':'` 指向无关行。逻辑请抽成 `scripts/*.py` |
| `set -e` 下直接写 `git grep ...` | 无命中返回 1 会让 job 失败；写成 `if git grep ...; then ... fi` |
| 中文正则里数反斜杠层数 | YAML 块标量与 bash 单引号**都不做转义**，多写两个反斜杠就永远不命中。本项目改用 Python 实现扫描，并在 CI 里注入样本自证非空转 |
| 入口脚本直接 `print("中文")` | GitHub 的 Windows runner 上 stdout 是 cp1252，会 `UnicodeEncodeError`；本机 cp936 复现不出来。用 `core.console.force_utf8_stdout()` |

改完在本地跑一遍对应命令，再参考 [docs/RELEASING.md](docs/RELEASING.md) 的发版流程。
`tools/dev/ci_local_guard.py` 复刻了隐私守卫那一节的全部逻辑。
