# -*- coding: utf-8 -*-
"""版本查阅与回滚助手。

回答两个日常问题：
- **"上一个版本改了哪些文件？"** → `show` / `diff`
- **"我要看/用某个旧版本的代码"** → `list` / `export`

设计立场：**旧版本不需要靠"每个版本一个文件夹"来保留**。
git 的每个提交/标签本身就是完整快照，"每版一个目录"只会带来
磁盘膨胀、改错副本、无法合并三个问题。真正需要"目录化"的只有两样东西：
1. **发行包**（exe/zip）—— 由 `build_release.py` 自动归档到 `release/vX.Y.Z/`
2. **需要对比/排查的源码快照** —— 用本脚本的 `export` 按需导出

安全性：本脚本**只读 git、只写新目录**，不执行任何删除；`export` 遇到
非空目标目录会拒绝（除非显式 `--force`），避免覆盖你的东西。

用法：
    python scripts/versions.py list                 # 列出所有版本
    python scripts/versions.py list --remote        # 先从远端同步标签再列
    python scripts/versions.py show v1.3.0          # 某版本的详情
    python scripts/versions.py diff v1.2.0 v1.3.0   # 两版本的差异文件
    python scripts/versions.py export v1.3.0        # 导出源码到 _versions/v1.3.0/
    python scripts/versions.py restore v1.2.0       # 打印回滚步骤（不自动执行）
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8_stdout  # noqa: E402

force_utf8_stdout()

DEFAULT_EXPORT_ROOT = ROOT / "_versions"
ARCHIVE_ROOT = ROOT / "release"


# ---------- git 封装 ----------

def git(*args: str, check: bool = False) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", errors="replace").strip()
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：\n{out}")
    return r.returncode, out


def in_git_repo() -> bool:
    return git("rev-parse", "--is-inside-work-tree")[0] == 0


# ---------- 版本枚举 ----------

def _changelog_text(ref: str) -> str:
    rc, out = git("show", f"{ref}:CHANGELOG.md")
    return out if rc == 0 else ""


def changelog_summary(ref: str) -> str:
    """取 CHANGELOG 里该版本段落的第一条要点（一行，便于列表展示）。"""
    text = _changelog_text(ref)
    if not text:
        return ""
    ver = ref[1:] if ref.startswith("v") else ref
    m = re.search(rf"^## \[{re.escape(ver)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
                  text, re.S | re.M)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s.lstrip("-• ").strip()
    return ""


def local_tags() -> list[dict]:
    """本地标签，按语义化版本倒序。"""
    rc, out = git("tag", "-l", "--sort=-v:refname")
    if rc != 0 or not out:
        return []
    rows: list[dict] = []
    for tag in out.splitlines():
        tag = tag.strip()
        if not tag:
            continue
        _, date = git("log", "-1", "--format=%cs", tag)
        _, commit = git("rev-list", "-n1", tag)
        _, subject = git("tag", "-l", tag, "--format=%(contents:subject)")
        rows.append({
            "tag": tag,
            "date": date.strip(),
            "commit": commit.strip()[:8],
            "subject": subject.strip() or "(无标签说明)",
            "summary": changelog_summary(tag),
            "archived": archived_dir(tag),
        })
    return rows


def archived_dir(tag: str) -> str:
    """本地发行包归档目录（存在才返回路径）。"""
    d = ARCHIVE_ROOT / tag
    if d.is_dir() and any(d.glob("*.zip")):
        return str(d)
    return ""


def remote_tags() -> list[str]:
    rc, out = git("ls-remote", "--tags", "origin")
    if rc != 0:
        return []
    tags = []
    for line in out.splitlines():
        ref = line.split("refs/tags/")[-1]
        if ref.endswith("^{}"):        # 解引用行，跳过
            continue
        tags.append(ref)
    return tags


def prev_tag(tag: str, tags: list[str]) -> str:
    """在按版本倒序的列表里取该标签的"上一版"。"""
    if tag in tags:
        i = tags.index(tag)
        if i + 1 < len(tags):
            return tags[i + 1]
    return ""


# ---------- 子命令 ----------

def cmd_list(args) -> int:
    if not in_git_repo():
        print("当前目录不是 git 仓库")
        return 1
    if args.remote:
        print("从远端同步标签…")
        rc, out = git("fetch", "--tags", "--prune")
        print("  " + (out.splitlines()[0] if out else "已是最新"))

    rows = local_tags()
    if not rows:
        print("还没有任何版本标签。发布时用 `git tag -a v1.0.0` 创建。")
        return 0

    remote = set(remote_tags()) if args.remote else set()
    print(f"\n共 {len(rows)} 个版本（新 → 旧）：\n")
    for r in rows:
        marks = []
        if r["archived"]:
            marks.append("本地有发行包")
        if remote:
            marks.append("远端已推送" if r["tag"] in remote else "⚠ 仅本地")
        head = f"  {r['tag']:<12} {r['date']}  {r['commit']}"
        print(head + ("   [" + " / ".join(marks) + "]" if marks else ""))
        if r["summary"]:
            print(f"      {r['summary'][:100]}")
        if not r["archived"]:
            print(f"      提示：本地无归档。可从 GitHub Release 下载，"
                  f"或 `python scripts/build_release.py`（会在 release/{r['tag']}/ 留档）")
    print("\n查看某版本：python scripts/versions.py show <tag>")
    print("导出源码：  python scripts/versions.py export <tag>")
    return 0


def cmd_show(args) -> int:
    ref = args.ref
    rc, _ = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    if rc != 0:
        print(f"找不到版本：{ref}")
        return 1

    _, commit = git("rev-list", "-n1", ref)
    _, date = git("log", "-1", "--format=%cs", ref)
    _, author = git("log", "-1", "--format=%an <%ae>", ref)
    # 注意要带上 ref：漏掉的话拿到的是 HEAD 的提交信息（看着"对"，其实不是这个版本的）
    _, msg = git("log", "-1", "--format=%B", ref)
    _, tags_at = git("tag", "--points-at", ref)

    print(f"版本    : {ref}")
    print(f"提交    : {commit.strip()}")
    print(f"日期    : {date.strip()}")
    print(f"作者    : {author.strip()}")
    if tags_at.strip():
        print(f"标签    : {tags_at.strip().replace(chr(10), ', ')}")

    archive = archived_dir(ref)
    print(f"发行包  : {archive or '本地无归档（见 GitHub Release）'}")

    print("\n提交信息:")
    for line in msg.splitlines()[:20]:
        print(f"  {line}")

    # 与上一版的差异（含"首次发布"的情况）
    tags = [r["tag"] for r in local_tags()]
    p = prev_tag(ref, tags)
    if p:
        _, stat = git("diff", "--stat", p, ref)
        print(f"\n相对 {p} 的变更:")
        for line in stat.splitlines()[-25:]:
            print(f"  {line}")
    else:
        print("\n（这是最早的版本，没有可比较的上一版）")

    section = _changelog_text(ref)
    ver = ref[1:] if ref.startswith("v") else ref
    m = re.search(rf"^## \[{re.escape(ver)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
                  section, re.S | re.M)
    if m:
        print("\nCHANGELOG 摘要:")
        for line in m.group(1).strip().splitlines()[:15]:
            print(f"  {line}")
    return 0


def cmd_diff(args) -> int:
    a = args.a
    tags = [r["tag"] for r in local_tags()]
    b = args.b or prev_tag(a, tags)
    if not b:
        print(f"{a} 没有上一版可比；请显式给出第二个版本：versions.py diff {a} <b>")
        return 1
    _, stat = git("diff", "--stat", b, a)
    _, name_status = git("diff", "--name-status", b, a)

    print(f"{b} → {a}\n")
    print(stat or "(无差异)")
    if args.files:
        print("\n变更文件明细（A=新增 M=修改 D=删除 R=重命名）:")
        for line in name_status.splitlines():
            print(f"  {line}")
    else:
        print("\n（加 --files 看逐个文件明细）")
    return 0


def cmd_export(args) -> int:
    ref = args.ref
    rc, _ = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    if rc != 0:
        print(f"找不到版本：{ref}")
        return 1

    target = Path(args.out).expanduser().resolve() if args.out \
        else (DEFAULT_EXPORT_ROOT / ref).resolve()

    if target.exists() and any(target.iterdir()):
        if not args.force:
            print(f"目标目录非空，拒绝覆盖：{target}")
            print("  换个目录：  --out <目录>")
            print("  确实要写入该目录：加 --force（只新增/覆盖同名文件，不删除原有文件）")
            return 1

    tmp_zip = Path(tempfile.mkdtemp(prefix="ntf-export-")) / f"{ref}.zip"
    try:
        rc, out = git("archive", "--format=zip", f"-o{tmp_zip}", ref)
        if rc != 0 or not tmp_zip.exists():
            print(f"导出失败：{out}")
            return 1
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp_zip) as z:
            names = z.namelist()
            z.extractall(target)
    finally:
        shutil.rmtree(tmp_zip.parent, ignore_errors=True)

    # 回读校验：真的落地了文件
    landed = sum(1 for _ in target.rglob("*") if _.is_file())
    if landed == 0:
        print("导出后目录为空，视为失败")
        return 1

    print(f"已导出 {ref} → {target}")
    print(f"  文件数：{len(names)}（落地 {landed}）")
    print("\n注意：这是**只读快照**，不含 .git，不能直接在里面提交。")
    print("  对比代码：用编辑器直接开两个目录，或 `versions.py diff <a> <b>`")
    print("  真要基于旧版改：`git switch -c hotfix/v1.3.1 v1.3.0`（在仓库里开分支）")
    return 0


def cmd_restore(args) -> int:
    ref = args.ref
    rc, _ = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    if rc != 0:
        print(f"找不到版本：{ref}")
        return 1
    _, cur = git("rev-parse", "--short", "HEAD")
    _, dirty = git("status", "--porcelain")

    print(f"""回滚到 {ref} —— 下面**只是步骤说明，本脚本不替你执行任何破坏性操作**。

当前 HEAD: {cur.strip()}""" + ("""

⚠ 工作区有未提交改动。先决定怎么处理：
   想留着：  git stash push -u -m "wip-回滚前"
   不要了：  git checkout -- .        # 丢弃改动（不可恢复）
""" if dirty else "\n工作区干净。") + f"""

场景 A：只想**看一眼**旧版代码（最常用，零风险）
    python scripts/versions.py export {ref}
    # 代码会出现在 _versions/{ref}/，原件与工作区都不受影响

场景 B：把**工作区**切回旧版（临时排查用）
    git switch --detach {ref}          # 进入游离 HEAD
    # …排查…
    git switch main                    # 回到最新

场景 C：基于旧版修 bug（正确做法，别在导出目录里改）
    git switch -c hotfix/{ref}-fix {ref}
    # 修完后按 docs/RELEASING.md 发一个新版本（如 v1.3.1）

场景 D：用户手里的 **exe** 要回到旧版
    1. 从 GitHub Release 下载旧版 zip，或解压 release/{ref}/
    2. 解压到**另一个目录**，不要覆盖当前版本（方便随时换回来）
    3. 数据目录（settings.json / rules.json / archive.db）**与代码版本无关**，不用动

⚠ 场景 B/C 都会改变工作区。执行前先 `git status` 确认没有未保存的改动。""")
    return 0


# ---------- 入口 ----------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="版本查阅与回滚助手（只读 git、只写新目录，不删除任何东西）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出所有版本")
    p.add_argument("--remote", action="store_true", help="先从远端同步标签")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="某版本的详情与相对上一版的变更")
    p.add_argument("ref")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("diff", help="两个版本的差异")
    p.add_argument("a")
    p.add_argument("b", nargs="?", default="")
    p.add_argument("--files", action="store_true", help="列出逐个文件明细")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("export", help="把某版本的源码导出到目录")
    p.add_argument("ref")
    p.add_argument("--out", "-o", default="", help="目标目录（默认 _versions/<ref>）")
    p.add_argument("--force", action="store_true", help="允许写入非空目录")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("restore", help="打印回滚步骤（不执行）")
    p.add_argument("ref")
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    if not in_git_repo():
        print(f"当前目录不是 git 仓库：{ROOT}")
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
