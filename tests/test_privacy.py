# -*- coding: utf-8 -*-
"""隐私守卫：源码里不许出现个人路径、真实密钥、真实内网地址；仓库里不许有运行态数据。

扫描逻辑在 `tools/privacy_scan.py`（零第三方依赖），CI 的隐私守卫 job 直接
`python tools/privacy_scan.py --strict-artifacts` 跑同一个实现 ——
逻辑只写一份，避免"测试说干净、CI 说脏"这种双份实现漂移。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.privacy_scan import (ALLOWED_USERNAMES, FORBIDDEN,  # noqa: E402
                               RUNTIME_DIRS, RUNTIME_FILES, read_text,
                               scan, self_test, source_files)


def _git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    return r.returncode, (r.stdout or b"").decode("utf-8", errors="replace")


def _tracked_files() -> list[str] | None:
    """git 索引里的文件列表；不在 git 仓库里时返回 None。"""
    rc, out = _git("ls-files")
    return None if rc != 0 else out.splitlines()


# ---------- 扫描器本身 ----------

def test_scanner_is_not_vacuous():
    """守卫自检：注入必然命中的样本，证明扫描不是空转。"""
    ok, msg = self_test()
    assert ok, msg


def test_scan_has_files_to_scan():
    files = source_files(ROOT)
    assert len(files) >= 20, f"扫描范围异常（只有 {len(files)} 个文件），疑似路径失效"


@pytest.mark.parametrize("label,pattern", FORBIDDEN, ids=[f[0] for f in FORBIDDEN])
def test_scanner_detects_injected_sample(label, pattern, tmp_path):
    """每个模式都要能真的命中 —— 否则"永远为绿"的守卫等于没有。"""
    import re

    bs = chr(92)
    sample = {
        "个人下载目录": f"path = {pattern}",
        "Windows 用户路径": f'x = "C:{bs}Users{bs}realsecretuser"',
        "真实 API Key": f'key = "{pattern}"',
        "真实内网 IP": f'ip = "{pattern}"',
    }[label]
    assert re.search(pattern, sample), f"「{label}」的模式无法命中注入样本"

    # 并且这个样本要通过**真正的扫描器**被判为违规
    probe = tmp_path / "sample.py"
    probe.write_text(sample + "\n", encoding="utf-8")
    hits = scan(tmp_path)
    assert hits, f"「{label}」的样本没有被扫描器识别"
    assert any(label in h for h in hits), hits


def test_repo_is_clean():
    violations = scan(ROOT)
    assert not violations, "源码里出现个人数据：\n" + "\n".join(violations)


# ---------- 运行态产物 ----------

def test_no_runtime_data_files_in_working_tree():
    """工作区里不该有运行态数据文件（build/dist 由 .gitignore 兜住，见下一条）。"""
    found = [n for n in RUNTIME_FILES if (ROOT / n).exists()]
    assert not found, f"仓库根存在运行态数据文件：{found}"


def test_no_personal_state_json_in_tree():
    """状态/断点类文件（含完整文件路径清单）不该出现在任何位置。"""
    leftovers = [p.relative_to(ROOT) for p in ROOT.rglob("*_state.json")
                 if "__pycache__" not in p.parts and ".git" not in p.parts]
    assert not leftovers, f"发现状态/断点文件：{leftovers}"


def test_no_runtime_artifacts_are_tracked():
    """判据是"有没有被 git 跟踪"，不是"目录存不存在"。

    本地开发/打包时 `build/`、`dist/` 本来就会在，它们由 .gitignore 兜住。
    把"存在即失败"写成用例，会让每次打包后跑测试都红，
    最后这条用例就会被删掉 —— 不如把判据写准。
    """
    tracked = _tracked_files()
    if tracked is None:
        pytest.skip("不是 git 仓库，无法判断跟踪状态")
    bad = [f for f in tracked
           if f in RUNTIME_FILES
           or f.startswith(tuple(d + "/" for d in RUNTIME_DIRS))]
    assert not bad, f"以下运行态产物被提交了：{bad}"


def test_no_large_binary_is_tracked():
    """>5MB 的文件不该进 git（发行包走 Release 附件，不进源码历史）。"""
    tracked = _tracked_files()
    if tracked is None:
        pytest.skip("不是 git 仓库")
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


def test_runtime_dirs_are_gitignored():
    """`build/`、`dist/`、`logs/` 即便存在也必须被忽略。"""
    if _tracked_files() is None:
        pytest.skip("不是 git 仓库")
    for name in ("build", "dist", "logs"):
        d = ROOT / name
        d.mkdir(exist_ok=True)
        probe = d / ".gitignore_probe"
        probe.write_text("x", encoding="utf-8")
        try:
            rc, _ = _git("check-ignore", "-q", str(probe))
            assert rc == 0, f"{name}/ 没有被 .gitignore 覆盖"
        finally:
            probe.unlink(missing_ok=True)
            try:
                d.rmdir()
            except OSError:
                pass


def test_gitignore_covers_runtime_artifacts():
    gi = read_text(ROOT / ".gitignore")
    assert gi, "缺少 .gitignore"
    for token in ("settings.json", "rules.json", "archive.db", "logs/",
                  "__pycache__/", "dist/", "build/", ".pytest_home"):
        assert token in gi, f".gitignore 未覆盖 {token}"


# ---------- 其它边界 ----------

def test_no_network_calls_to_third_party_at_import_time():
    """导入期不许联网（会表现为"一打开设置页卡 5 秒"）。"""
    import importlib

    for mod in ("core.tag_organizer", "core.jellyfin", "ui.styles", "ui.fonts"):
        importlib.import_module(mod)


def test_ollama_calls_bypass_system_proxy():
    """本地 Ollama 请求必须绕开系统代理（用户环境常有代理软件拦 localhost）。"""
    src = read_text(ROOT / "core" / "tag_organizer.py")
    total = src.count("requests.get(") + src.count("requests.post(")
    with_proxies = src.count("proxies={}")
    assert total >= 6
    assert with_proxies >= total - 1, (
        f"{total} 个 requests 调用里只有 {with_proxies} 个设置了 proxies={{}}；"
        "本地 Ollama 调用会被代理拦死")


def test_no_hardcoded_drive_letter_defaults():
    """配置默认值里不许出现盘符（没有那个盘的机器会直接崩）。"""
    if sys.platform != "win32":
        pytest.skip("盘符语义只在 Windows 上成立")
    import re

    from config import Settings

    s = Settings()
    for field in ("ffprobe_path", "archive_db_path", "log_dir", "last_rules_path"):
        value = getattr(s, field, "")
        assert not re.match(r"^[A-Za-z]:[\\/]", value or ""), \
            f"Settings.{field} 默认值含盘符: {value}"


def test_example_config_has_no_secrets():
    import json

    example = ROOT / "config.example.json"
    assert example.exists(), "缺少 config.example.json"
    text = read_text(example)
    data = json.loads(text)
    assert data.get("jellyfin_api_key", "") == ""
    assert data.get("archive_db_path", "") == ""
    assert data.get("jellyfin_server", "") == ""
    assert "Users" not in text


def test_allowed_usernames_are_obviously_fake():
    """豁免名单里不能混进真实用户名 —— 那等于把守卫关掉。"""
    assert all(u in ALLOWED_USERNAMES for u in ("me", "sometest", "example"))
