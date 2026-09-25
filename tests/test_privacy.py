# -*- coding: utf-8 -*-
"""隐私守卫：源码里不许出现个人路径、真实密钥、真实内网地址，仓库里不许有运行态数据。

敏感串本身在测试文件里会被"自己举报自己"，所以全部用片段拼出来，
并且扫描时跳过本文件。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

# ---- 敏感模式（用片段拼装，避免测试文件本身命中） ----
# 「Windows 用户路径」用带捕获组的形式：匹配到的用户名若是明显的占位示例就放过，
# 否则一旦把 CONTRIBUTING 里的反面例子、CI 守卫自检的探针也算违规，
# 这条守卫就会被"合理豁免"淹没 —— 久而久之没人再看它。
_USER_PATH_RE = r"C:\\+Users\\+([A-Za-z0-9_.\-]+)"
_ALLOWED_USERNAMES = {"me", "user", "username", "sometest", "test", "yourname",
                      "your-user-name", "<user>", "example"}

_FORBIDDEN = [
    # 真实的下载目录名
    ("个人下载目录", "迅雷" + "下载"),
    # Windows 用户目录字面量（CI 里也是这条守卫）
    ("Windows 用户路径", _USER_PATH_RE),
    # 真实 Jellyfin API Key（32 位 hex）——只拦这一个已知值
    ("真实 API Key", "c54b30fef9" + "e64234b823c9228c5f7d54"),
    # 真实内网地址
    ("真实内网 IP", "192.168." + "31.176"),
]

# 允许出现的"通用示例"字样（不含真实用户名/盘符）
ALLOWED_HINTS = (
    "192.168.1.10",     # 文档里的通用示例
    "192.168.1.",       # 通用网段示例
)

TEXT_SUFFIXES = (".py", ".md", ".json", ".toml", ".yml", ".yaml", ".txt",
                 ".cfg", ".ini", ".bat", ".cmd", ".vbs", ".ps1", ".example")


def _source_files() -> list[Path]:
    out: list[Path] = []
    skip_parts = {"__pycache__", ".git", ".pytest_cache", "_patch", "dist", "build"}
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.resolve() == SELF:
            continue
        if skip_parts & set(p.parts):
            continue
        if p.suffix.lower() in TEXT_SUFFIXES:
            out.append(p)
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@pytest.mark.parametrize("label,pattern", _FORBIDDEN, ids=[f[0] for f in _FORBIDDEN])
def test_no_personal_data_in_sources(label, pattern):
    rx = re.compile(pattern)
    hits: list[str] = []
    for p in _source_files():
        for i, line in enumerate(_read(p).splitlines(), 1):
            m = rx.search(line)
            if not m:
                continue
            if any(h in line for h in ALLOWED_HINTS):
                continue
            # 用户名是明显的占位示例 → 放过（CONTRIBUTING 的反面例子、CI 自检探针）
            if m.groups() and m.group(1).lower() in _ALLOWED_USERNAMES:
                continue
            hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, f"源码里出现「{label}」：\n" + "\n".join(hits)


def _tracked_files() -> list[str] | None:
    """git 索引里的文件列表；不在 git 仓库里时返回 None。"""
    import subprocess

    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        return None
    return (r.stdout or b"").decode("utf-8", errors="replace").splitlines()


def test_no_runtime_data_files_are_tracked():
    """运行态产物不能被 **提交**。

    注意判据是"有没有被 git 跟踪"，不是"目录存不存在"——
    本地开发/打包时 `build/`、`dist/` 本来就会在，它们由 .gitignore 兜住。
    把"存在即失败"写进用例，会让每次打包后跑测试都红，最后大家就去掉这条用例了。
    """
    tracked = _tracked_files()
    if tracked is None:
        pytest.skip("不是 git 仓库，无法判断跟踪状态")
    forbidden = {"settings.json", "rules.json", "archive.db", "config.json",
                 "portable.txt"}
    bad = [f for f in tracked
           if f in forbidden or f.startswith(("logs/", "dist/", "build/", "data/"))]
    assert not bad, f"以下运行态产物被提交了：{bad}"


def test_no_large_binary_is_tracked():
    """>5MB 的文件不该进 git（发行包走 Release 附件，不进源码历史）。"""
    tracked = _tracked_files()
    if tracked is None:
        pytest.skip("不是 git 仓库，无法判断跟踪状态")
    big: list[str] = []
    for rel in tracked:
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > 5 * 1024 * 1024:
            big.append(f"{rel} ({size / 1048576:.1f} MB)")
    assert not big, "以下大文件被提交了：\n" + "\n".join(big)


def test_no_personal_state_json_in_tree():
    """状态/断点类文件（含完整文件路径清单）不该出现在任何位置。"""
    leftovers = [p.relative_to(ROOT) for p in ROOT.rglob("*_state.json")
                 if "__pycache__" not in p.parts and ".git" not in p.parts]
    assert not leftovers, f"发现状态/断点文件：{leftovers}"


def test_runtime_dirs_are_gitignored():
    """`build/`、`dist/`、`logs/` 等目录即便存在也必须被忽略。"""
    import subprocess

    if _tracked_files() is None:
        pytest.skip("不是 git 仓库")
    for path in ("build", "dist", "logs"):
        d = ROOT / path
        d.mkdir(exist_ok=True)
        probe = d / ".gitignore_probe"
        probe.write_text("x", encoding="utf-8")
        try:
            r = subprocess.run(["git", "check-ignore", "-q", str(probe)],
                               cwd=ROOT)
            assert r.returncode == 0, f"{path}/ 没有被 .gitignore 覆盖"
        finally:
            probe.unlink(missing_ok=True)
            try:
                d.rmdir()
            except OSError:
                pass


def test_gitignore_covers_runtime_artifacts():
    gi = _read(ROOT / ".gitignore")
    assert gi, "缺少 .gitignore"
    for token in ("settings.json", "rules.json", "archive.db", "logs/",
                  "__pycache__/", "dist/", "build/", ".pytest_home"):
        assert token in gi, f".gitignore 未覆盖 {token}"


def test_no_network_calls_to_third_party_at_import_time():
    """导入期不许联网（会表现为"一打开设置页卡 5 秒"）。"""
    import importlib

    for mod in ("core.tag_organizer", "core.jellyfin", "ui.styles", "ui.fonts"):
        importlib.import_module(mod)   # 能 import 完成即说明没有阻塞式网络调用


def test_ollama_calls_bypass_system_proxy():
    """本地 Ollama 请求必须绕开系统代理（用户环境常有 Clash 拦 localhost）。"""
    src = _read(ROOT / "core" / "tag_organizer.py")
    total = src.count("requests.get(") + src.count("requests.post(")
    with_proxies = src.count("proxies={}")
    assert total >= 6
    # 除了 DashScope 那一条云端调用，其余全是 localhost，必须禁代理
    assert with_proxies >= total - 1, (
        f"{total} 个 requests 调用里只有 {with_proxies} 个设置了 proxies={{}}；"
        "本地 Ollama 调用会被代理拦死")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 才有盘符语义")
def test_no_hardcoded_drive_letter_defaults():
    """配置默认值里不许出现盘符（没有那个盘的机器会直接崩）。"""
    from config import Settings

    s = Settings()
    for field in ("ffprobe_path", "archive_db_path", "log_dir", "last_rules_path"):
        value = getattr(s, field, "")
        assert not re.match(r"^[A-Za-z]:[\\/]", value or ""), \
            f"Settings.{field} 默认值含盘符: {value}"


def test_example_config_has_no_secrets():
    example = ROOT / "config.example.json"
    if not example.exists():
        pytest.skip("尚未提供 config.example.json")
    text = _read(example)
    assert "jellyfin_api_key" in text
    import json
    data = json.loads(text)
    assert data.get("jellyfin_api_key", "") == ""
    assert data.get("archive_db_path", "") == ""
    assert "Users" not in text
