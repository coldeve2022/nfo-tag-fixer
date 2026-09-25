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
