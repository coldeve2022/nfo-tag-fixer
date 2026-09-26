# 发版工作流

本文件规定版本号怎么定、产物放哪、发版按什么顺序做。
目标只有一个：**任何时候都能明确回答"现在线上是哪个版本、它的源码是哪个提交"
以及"下一版该怎么发"**。

---

## 1. 版本号：单一来源

**唯一真相是 [`version.py`](../version.py) 的 `__version__`。**

其它地方一律引用它，不允许各写各的：

| 位置 | 来源 |
|------|------|
| 窗口标题 / 关于对话框 | `version.window_title()` / `version.__version__` |
| exe 文件属性里的版本/版权 | `scripts/build_release.py` 生成的 `build/version_info.txt` |
| 打包产物的文件名 | `scripts/build_release.py` 读 `__version__` |
| `pyproject.toml` 的 `version` | 由 `scripts/build_release.py` 同步；CI 会校验 |
| `CHANGELOG.md` | 人工维护，但 CI 会检查对应段落存在 |

校验命令（本地与 CI 跑的是同一个脚本）：

```bash
python scripts/check_version.py             # 只校验 version.py 与 pyproject.toml
python scripts/check_version.py v1.3.0      # 再校验 git 标签与 CHANGELOG
```

> 为什么单独抽成脚本：这段逻辑写在 workflow 的 `run: |` 里很难写对 ——
> YAML 块标量里嵌 `python -c "多行代码"` 会因为缩进让块提前结束，
> 报出来的错还是 `could not find expected ':'`，跟真正的问题八竿子打不着。

### 版本号怎么涨

遵循[语义化版本](https://semver.org/lang/zh-CN/)：

| 变化 | 版本位 | 例子 |
|------|--------|------|
| 只修缺陷、无行为变化 | PATCH | `1.3.0 → 1.3.1` |
| 加功能，向后兼容 | MINOR | `1.3.0 → 1.4.0` |
| 配置格式/数据目录/CLI 参数不兼容 | MAJOR | `1.3.0 → 2.0.0` |

对本项目来说，"破坏性"主要指：`settings.json` 字段语义改变、
数据目录规则改变、`--doctor`/CLI 参数改名。

---

## 2. 目录约定

| 目录 | 用途 | 提交? | 谁产生 |
|------|------|:----:|--------|
| 仓库根 | 源码（唯一真相） | ✅ | 人 |
| `assets/` | 图标、截图 | ✅ | `tools/make_icon.py`、`tools/dev/make_screenshots.py` |
| `tests/` `tools/` `scripts/` | 测试、开发工具、构建脚本 | ✅ | 人 |
| `build/` | 版本资源 + PyInstaller 中间产物 | ❌ | `scripts/build_release.py` |
| `dist/` | 分发用 zip 与 `.sha256` | ❌ | 同上 |
| `release/` | 手工归档的历史发行包（可选） | ❌ | 人 |
| 数据目录（`settings.json` 等） | 运行时配置与档案 | ❌ | 程序 |

`build/` 与 `dist/` 都是**可重建产物**，任何时候都能删掉重跑：

```bash
python scripts/build_release.py
```

> 打包脚本会先整个删掉 `build/` 再重建（`shutil.rmtree`）。
> PyInstaller 自带的批量清理在带沙箱/杀软的环境里会被拦下，
> 报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 之类。

---

## 3. 发版前自检

**把 CI 会跑的命令在本地跑一遍。** "仓库里配了 CI" ≠ "CI 能过" ——
只要还没推上去，那些 job 就从没执行过。

```bash
# 1) 静态检查（规则集在 pyproject.toml，含收窄原因）
ruff check . --select E4,E7,E9,F --ignore E501,E702,E741

# 2) 全部用例（约 10 秒，145 个）
python -m pytest -q

# 3) 隐私守卫 + 工作区检查（CI 同款逻辑，本地版）
python tools/dev/ci_local_guard.py

# 4) 版本号一致
python scripts/check_version.py v1.3.0
```

如果这次改了界面或数据目录逻辑，再加两项：

```bash
python tools/dev/make_screenshots.py                       # 重新生成 README 截图
python scripts/build_release.py                            # 打包
python tools/dev/verify_frozen.py "dist/NFO标签批量修改工具/NFO标签批量修改工具.exe"
```

`verify_frozen.py` 做的事（每一条都是踩过的坑）：

- 读 exe 版本资源，确认与 `version.py` 一致
- 用 `DETACHED_PROCESS` 真启动 GUI 并采样 `GetExitCodeProcess`
  （宿主会在命令结束时回收子进程树，普通方式起的 GUI 会"看起来自己死了"）
- **读应用自己的 `logs/crash.log`** —— 窗口版 exe 没有控制台，未捕获异常只在这里留痕。
  进程"活着"不等于没崩：曾经出现过"存活 6 秒、看起来正常"，实际是构造页面时
  `AttributeError`，主窗口根本没显示出来，只有日志能暴露它
- 确认首启会在全新数据目录里建出 `archive.db` 与 `logs/`

---

## 4. 完整发版流程

```bash
# ① 涨版本 + 写变更日志
#    - 改 version.py 的 __version__（唯一来源）
#    - 在 CHANGELOG.md 顶部加 "## [x.y.z] - YYYY-MM-DD" 段落（CI 会检查）
#    - 把 [未发布] 段落里的内容挪进新版本（有的话）

# ② 本地过一遍第 3 节的自检

# ③ 提交并推送代码（**先不带标签**）
git commit -am "chore(release): v1.3.0"
git push origin main

# ④ 等 tests workflow 变绿
gh run list --limit 3
gh run watch <run-id> --exit-status

# ⑤ 绿了再打标签 —— 标签会触发 release workflow
git tag -a v1.3.0 -m "v1.3.0"
git push origin v1.3.0

# ⑥ 等 release workflow 出包并创建 Release
gh run watch <release-run-id> --exit-status
gh release view v1.3.0 --json assets --jq '.assets[] | "\(.name)  \(.size)"'
```

**顺序不能颠倒。** 一上来就推标签等于给一份还没验证过的代码发版。

### 收尾核对

- [ ] `tests` workflow：静态检查 / 隐私守卫 / 三平台矩阵 / 冻结产物冒烟，全绿
- [ ] `release` workflow：校验版本 → 构建 → 冒烟 → 创建 Release，全绿
- [ ] Release 页面有 `nfo-tag-fixer-vX.Y.Z-win64.zip` 与 `.sha256`，且二者对得上
- [ ] 远端 `main` 与本地 HEAD 一致（别只看标签）

```bash
git ls-remote origin refs/heads/main refs/tags/v1.3.0^{}
```

---

## 5. 出问题时怎么补救

### 标签打错了 / release 失败

**在 Release 还没创建之前，搬标签比"再发一个补丁版本"更干净：**

```bash
git tag -d v1.3.0
git push origin :refs/tags/v1.3.0     # 删远端
# …修好、提交、推 main…
git tag -a v1.3.0 -m "v1.3.0"
git push origin v1.3.0                # 重新触发
```

如果 Release 已经创建了，就应该发一个 `v1.3.1` 而不是覆盖历史。

### 推 main 时网络抖动

`git push origin main` 可能静默失败（`SSL_ERROR_SYSCALL`），
而紧接着的 `git push origin <tag>` 仍然成功 —— 结果是"标签指向一个 main 上还没有的提交"。
所以推完一定要两边都核对（见上面的 `git ls-remote`）。

### 代理

本仓库的 `.git/config` 里写死了本地代理（`git config --local http.proxy`），
**不要动全局配置**，也不要把它写进会被提交的文件。若代理端口变了，只改本地：

```bash
git config --local http.proxy  http://127.0.0.1:7897
git config --local https.proxy http://127.0.0.1:7897
```

排查时先看环境变量里有没有别的代理在捣乱，再分别测直连与各代理端口：

```bash
env | grep -i proxy
env -u HTTPS_PROXY -u HTTP_PROXY git -c http.proxy= ls-remote <repo-url>
env -u HTTPS_PROXY -u HTTP_PROXY git -c http.proxy=http://127.0.0.1:7897 ls-remote <repo-url>
```

> `ls-remote` 对**空仓库**本来就无输出，别把"没有输出"误判成失败 —— 要看有没有报错。

---

## 6. CI 里踩过的坑（写 workflow 前请读）

| 症状 | 真凶 |
|------|------|
| workflow 直接失败、**0 个 job**、连一行日志都没有，Run 标题显示文件路径而不是 `name:` | YAML 结构被 GitHub 拒绝。本例是 `jobs.<id>.env` 里用了 `${{ runner.temp }}` —— **`runner` 上下文只在 step 级可用**，报错是 `Unrecognized named-value: 'runner'` |
| 报 `could not find expected ':'`，指向一段无关的 Python 代码 | `run: \|` 块标量里嵌了**未缩进**的多行 `python -c "..."`，导致块提前结束。把逻辑抽成 `scripts/*.py` |
| 有 `::error::` 但 job 仍然绿 | `set -e` 下 `git grep` 无命中会返回 1，把它写成 `if git grep ...; then` 才是安全的 |
| 隐私守卫"看起来在跑"但永远不报 | 正则里的反斜杠层数写错（`grep -E 'C:\\Users\\'` 在 YAML 块标量 + bash 单引号下**不做转义**，多写两个反斜杠就永远不命中）。所以本项目改用 Python 实现扫描，并在 CI 里**注入样本自证非空转** |
| Windows runner 上脚本第一步就 `UnicodeEncodeError: 'charmap' codec` | GitHub 的 Windows runner 上 stdout 是 cp1252，打印中文直接崩；本机是 cp936 所以永远复现不出来。所有会打印中文的入口都要 `core.console.force_utf8_stdout()` |
| 测试全绿但 job 仍红 | 后面还有"确认测试没有污染工作区"（`git status --porcelain`）等步骤 |

---

## 7. 版本历史去哪看

- 面向用户：GitHub 的 [Releases](https://github.com/coldeve2022/nfo-tag-fixer/releases) 页面
  （发布说明由 `release.yml` 从 `CHANGELOG.md` 自动抽取）
- 面向开发：`CHANGELOG.md`（记录**改了什么以及为什么**）
- 面向排查：`git log`（提交信息写清"改了什么 + 为什么"）

---

## 8. 怎么找回旧版本、怎么回滚

**先纠正一个常见误解：旧版本不需要靠"每个版本复制一份文件夹"来保留。**
git 的每个提交、每个标签本身就是一份完整快照，随时可取出、可对比、可回滚。
"v1.0 / v1.0_new / v1.0_final / v1.0_备份" 这种目录式版本管理有三个硬伤：

1. **磁盘膨胀且无法合并** —— 改了一处要同步 N 份，很快就不知道哪份是对的；
2. **看不出差异** —— 想知道"这两个版本差在哪"只能靠人肉比对；
3. **容易改错副本** —— 在旧目录里改了 bug，却发的是新目录。

真正需要"目录化"的只有两样：**发行包**和**需要对比的源码快照**。前者自动归档，
后者按需导出。

### 8.1 代码：一切都还在，三条命令取出来

```bash
python scripts/versions.py list                 # 列出所有版本（日期/提交/归档状态/变更摘要）
python scripts/versions.py list --remote        # 先从远端同步标签再列
python scripts/versions.py show v1.3.0          # 某版本的详情 + 相对上一版改了哪些文件
python scripts/versions.py diff v1.2.0 v1.3.0 --files   # 两版本的逐文件差异
python scripts/versions.py export v1.2.0        # 把该版本源码导出到 _versions/v1.2.0/
```

`export` 是**只读快照**（不含 `.git`，不能在里面提交），适合"开两个目录对着看"或
拿来排查。默认导出到仓库内的 `_versions/<tag>/`（已 gitignore）。

### 8.2 回滚：按场景选，不要一律 `git reset --hard`

```bash
python scripts/versions.py restore v1.2.0       # 打印各场景的具体步骤，不替你执行
```

| 场景 | 做法 | 风险 |
|------|------|------|
| 只想**看一眼**旧版代码 | `versions.py export v1.2.0` | 无 |
| 把**工作区**切回旧版临时排查 | `git switch --detach v1.2.0` → 排查完 `git switch main` | 中（会换掉工作区文件，先确认没有未提交改动） |
| 基于旧版**修 bug** | `git switch -c hotfix/v1.3.1 v1.2.0`，改完发 `v1.3.1` | 低（在分支上，不影响 main） |
| 用户手里的 **exe** 回旧版 | 从 Release 下载旧 zip，解压到**另一个目录** | 无（数据目录不受影响） |
| 彻底放弃某次提交 | `git revert <commit>`（**保留历史**） | 低 |
| 重写历史 | `git reset --hard` + 强推 | **高，公开发布过的仓库不要做** |

> 已经发布过的提交不要用 `reset` 抹掉：别人可能已经 clone 或下载了。
> 用 `revert` 产生一个"反向提交"，历史保持线性可追溯。

### 8.3 发行包（exe）：两个长期归档点

这是**唯一值得"放成不同文件夹"的东西**：

| 归档点 | 性质 | 位置 |
|--------|------|------|
| GitHub Release 附件 | 永久、在线、可分享 | 每个标签自动创建 |
| 本地 `release/vX.Y.Z/` | 离线可用 | `build_release.py` 每次构建自动写入 |

`release/vX.Y.Z/` 里除了 zip 与 `.sha256`，还有一份 `build-manifest.json`：

```json
{
  "version": "1.3.0", "tag": "v1.3.0",
  "built_at": "2026-09-26T02:27:11",
  "python": "3.13.14", "pyinstaller": "6.16.0",
  "git_branch": "main", "git_commit": "<40 位提交>",
  "git_dirty": false,
  "asset": "nfo-tag-fixer-v1.3.0-win64.zip",
  "sha256": "...", "size_bytes": 52894815
}
```

`git_commit` 与 `git_dirty` 是关键：出问题时能回答**"用户手上这个包到底是哪个提交构建的、
构建时工作区干不干净"**。工作区不干净会打印警告 —— 那种包不完全等于标签内容。

> ⚠️ **CI 上传的 artifact 有保留期（本仓库设的是 14 天）会过期**。
> 长期归档只能靠 Release 附件和本地 `release/`，别指望 CI artifact。

### 8.4 数据与代码是解耦的（升级/回滚都不丢）

`settings.json`、`rules.json`、`archive.db`、`logs/` 都在**数据目录**里，
不在程序目录。所以：

- 换版本（升级或回滚）**不会**动你的配置与档案；
- 解压新版 exe 到另一个目录，新旧两份可以并存、随时切换；
- 回滚旧版 exe 后，配置仍然是你最新改过的那些。

**唯一的兼容性注意点**：`Settings.from_dict()` 是"未知字段忽略、缺失字段用默认值"。
所以把**新版**的 `settings.json` 喂给**旧版**程序时，旧版不认识的新字段会在它下次
保存时被丢掉。回滚到旧版前，建议先备份一份数据目录：

```bash
# 数据目录位置用 --doctor 查；找不到就用 %APPDATA%\nfo-tag-fixer
python main.py --doctor
```

如果将来改动数据格式，发版说明里要**显式标注**，并考虑把 `archive.db` 的
schema 版本号写进库内（目前表结构只在 `core/archive.py` 的 `SCHEMA` 里）。
