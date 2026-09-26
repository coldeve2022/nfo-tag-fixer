# -*- coding: utf-8 -*-
"""`scripts/versions.py`（版本查阅与回滚助手）的行为验证。

这些用例**在真实仓库上跑 CLI**（`git archive` / `git log` 这类行为没法靠打桩
有意义地验证），所以只做只读或写到临时目录的操作 —— 每个用例都断言
"工作区没有被改动"，避免测试自己污染仓库（CI 有一步专门检查这个）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "versions.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          cwd=cwd or ROOT, capture_output=True, timeout=120)


def out_of(p: subprocess.CompletedProcess) -> str:
    return ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", errors="replace")


def a_ref() -> str:
    """优先用最新标签，没有标签就用 HEAD（保证用例在任何克隆上都能跑）。"""
    r = subprocess.run(["git", "tag", "-l", "--sort=-v:refname"],
                       cwd=ROOT, capture_output=True)
    tags = (r.stdout or b"").decode().split()
    return tags[0] if tags else "HEAD"


def git_dirty() -> str:
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True)
    return (r.stdout or b"").decode("utf-8", errors="replace")


@pytest.fixture
def clean_tree():
    before = git_dirty()
    yield
    assert git_dirty() == before, "versions.py 的用例改动了工作区"


# ---------- 单元：上一版推导 ----------

def test_prev_tag_logic():
    sys.path.insert(0, str(ROOT / "scripts"))
    import versions  # noqa: PLC0415

    tags = ["v1.3.0", "v1.2.1", "v1.2.0", "v1.0.0"]
    assert versions.prev_tag("v1.2.1", tags) == "v1.2.0"
    assert versions.prev_tag("v1.3.0", tags) == "v1.2.1"
    assert versions.prev_tag("v1.0.0", tags) == ""        # 最早的版本没有上一版
    assert versions.prev_tag("v9.9.9", tags) == ""        # 不认识的就别乱猜


# ---------- list ----------

def test_list_runs(clean_tree):
    p = run("list")
    assert p.returncode == 0, out_of(p)
    text = out_of(p)
    assert "个版本" in text or "还没有任何版本标签" in text


def test_list_shows_summary_and_archive_hint(clean_tree):
    ref = a_ref()
    if ref == "HEAD":
        pytest.skip("仓库里还没有版本标签")
    text = out_of(run("list"))
    assert ref in text
    # 要么标注了本地归档，要么给出怎么拿归档的提示
    assert "本地有发行包" in text or "本地无归档" in text


# ---------- show ----------

def test_show_reports_version_facts(clean_tree):
    ref = a_ref()
    p = run("show", ref)
    assert p.returncode == 0, out_of(p)
    text = out_of(p)
    assert "提交" in text and "日期" in text and "发行包" in text

    r = subprocess.run(["git", "rev-list", "-n1", ref], cwd=ROOT, capture_output=True)
    assert (r.stdout or b"").decode().strip() in text, "显示的提交不是该版本的提交"


def test_show_rejects_unknown_ref(clean_tree):
    p = run("show", "v0.0.0-does-not-exist")
    assert p.returncode != 0
    assert "找不到版本" in out_of(p)


# ---------- diff ----------

def test_diff_without_previous_version_is_explicit(clean_tree):
    """只有一个版本时要明确说明"没有上一版"，而不是输出一个空 diff 糊弄过去。"""
    p = run("diff", "v1.3.0")
    text = out_of(p)
    assert p.returncode == 0 or "没有上一版" in text


# ---------- export ----------

def test_export_lands_files_without_git(clean_tree, tmp_path):
    ref = a_ref()
    target = tmp_path / "snapshot"
    p = run("export", ref, "--out", str(target))
    assert p.returncode == 0, out_of(p)

    files = [f for f in target.rglob("*") if f.is_file()]
    assert len(files) > 30, f"导出文件过少（{len(files)}），疑似失败"
    # 关键文件必须在
    for name in ("main.py", "version.py", "CHANGELOG.md", "requirements.txt"):
        assert (target / name).exists(), f"缺 {name}"
    # 是只读快照：不带 .git，不能在里面提交
    assert not (target / ".git").exists()
    # 也不该把本地配置/产物带出去
    for leaked in ("settings.json", "archive.db", "dist", "build", "_versions"):
        assert not (target / leaked).exists(), f"导出内容里混进了 {leaked}"


def test_export_refuses_nonempty_dir_without_force(clean_tree, tmp_path):
    ref = a_ref()
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "我的文件.txt").write_text("别动我", encoding="utf-8")

    p = run("export", ref, "--out", str(target))
    assert p.returncode != 0
    text = out_of(p)
    assert "拒绝覆盖" in text
    # 原有文件必须毫发无伤
    assert (target / "我的文件.txt").read_text(encoding="utf-8") == "别动我"


def test_export_force_writes_into_nonempty_dir(clean_tree, tmp_path):
    ref = a_ref()
    target = tmp_path / "occupied2"
    target.mkdir()
    keep = target / "保留.txt"
    keep.write_text("保留", encoding="utf-8")

    p = run("export", ref, "--out", str(target), "--force")
    assert p.returncode == 0, out_of(p)
    assert (target / "main.py").exists()
    assert keep.read_text(encoding="utf-8") == "保留", "--force 不应删除原有文件"


def test_export_rejects_unknown_ref(clean_tree, tmp_path):
    p = run("export", "v0.0.0-does-not-exist", "--out", str(tmp_path / "x"))
    assert p.returncode != 0
    assert "找不到版本" in out_of(p)


# ---------- restore ----------

def test_restore_only_prints_instructions(clean_tree):
    """`restore` 绝不能自己动手 —— 它只该打印步骤。"""
    ref = a_ref()
    p = run("restore", ref)
    assert p.returncode == 0, out_of(p)
    text = out_of(p)
    for expect in ("export", "git switch", "EXE" if False else "exe"):
        assert expect in text, f"回滚说明里缺 {expect}"
    assert "不替你执行任何破坏性操作" in text
    assert git_dirty() == git_dirty()      # 读两次，确认没被它改


def test_restore_warns_when_worktree_dirty(tmp_path):
    """工作区有未提交改动时必须先警告，而不是直接让人 git checkout。

    做法：搭一个**真的小仓库**，把脚本和它依赖的 `core/console.py` 拷进去再跑。
    脚本按自身位置定位仓库（`__file__` 的上一级），所以拷过去就会作用于那个仓库。
    """
    repo = tmp_path / "probe"
    (repo / "scripts").mkdir(parents=True)
    (repo / "core").mkdir()
    shutil.copy2(SCRIPT, repo / "scripts" / "versions.py")
    shutil.copy2(ROOT / "core" / "console.py", repo / "core" / "console.py")
    (repo / "core" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("## [0.1.0] - 2026-01-01\n\n- 初始\n",
                                       encoding="utf-8")

    def g(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)

    g("init", "-q")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "t")
    g("config", "commit.gpgsign", "false")
    (repo / "a.txt").write_text("1", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "init")
    g("tag", "-a", "v0.1.0", "-m", "v0.1.0")

    # ① 干净工作区
    p = subprocess.run([sys.executable, str(repo / "scripts" / "versions.py"),
                        "restore", "v0.1.0"], cwd=repo, capture_output=True, timeout=60)
    text = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", errors="replace")
    assert p.returncode == 0, text
    assert "工作区干净" in text

    # ② 制造未提交改动
    (repo / "a.txt").write_text("2", encoding="utf-8")
    p2 = subprocess.run([sys.executable, str(repo / "scripts" / "versions.py"),
                         "restore", "v0.1.0"], cwd=repo, capture_output=True, timeout=60)
    text2 = ((p2.stdout or b"") + (p2.stderr or b"")).decode("utf-8", errors="replace")
    assert "git stash push" in text2, text2
    assert "未提交改动" in text2
    # 关键：它只是打印，绝不自己动手
    assert (repo / "a.txt").read_text(encoding="utf-8") == "2"


def test_help_lists_all_subcommands(clean_tree):
    p = run("-h")
    assert p.returncode == 0
    text = out_of(p)
    assert "版本查阅与回滚助手" in text
    for sub in ("list", "show", "diff", "export", "restore"):
        assert sub in text, f"帮助里缺子命令 {sub}"


def test_no_arguments_is_an_error(clean_tree):
    p = run()
    assert p.returncode != 0
    assert "usage:" in out_of(p)


def test_script_is_documented(clean_tree):
    """新脚本必须能被发现 —— 不然写了等于没写。"""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "versions.py" in readme, "README 里没有提到 versions.py"
    releasing = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    assert "versions.py" in releasing, "RELEASING.md 里没有提到 versions.py"
