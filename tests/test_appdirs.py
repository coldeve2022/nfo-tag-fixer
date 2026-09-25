# -*- coding: utf-8 -*-
"""数据目录解析、原子写、配置自愈。

这些是"换台机器还能不能跑"的关键：原实现把数据一律写在程序目录，
装到 Program Files 就起不来；配置里的绝对路径（别的盘的库）换机器后
会让档案库 makedirs 失败并连带启动崩溃。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from core.appdirs import (ENV_HOME, PORTABLE_MARKER, atomic_write_bytes,
                          atomic_write_text, drive_exists, is_writable_dir,
                          path_reachable, resolve_data_dir, safe_dir,
                          user_data_dir)


def test_is_writable_dir(tmp_path):
    assert is_writable_dir(tmp_path) is True
    assert is_writable_dir(tmp_path / "new" / "nested") is True


def test_path_reachable_accepts_missing_leaf(tmp_path):
    assert path_reachable(str(tmp_path / "not-created-yet"))
    assert path_reachable(str(tmp_path))


def test_path_reachable_rejects_blank():
    assert path_reachable("") is False
    assert path_reachable("   ") is False


@pytest.mark.skipif(sys.platform != "win32", reason="盘符语义只在 Windows 上成立")
def test_drive_exists_and_unreachable_drive():
    assert drive_exists(os.environ.get("SystemDrive", "C:") + "\\")
    # 用一个几乎不可能存在的盘符验证"不可达"
    bogus = "Z:\\nonexistent\\folder\\archive.db"
    if not os.path.exists("Z:\\"):
        assert drive_exists(bogus) is False
        assert path_reachable(bogus) is False


def test_safe_dir_falls_back_when_unusable(tmp_path):
    good = tmp_path / "good"
    fallback = tmp_path / "fallback"
    assert safe_dir(str(good), str(fallback)) == str(good)
    # Windows 上传入不存在的盘符 → 回退
    if sys.platform == "win32" and not os.path.exists("Z:\\"):
        assert safe_dir("Z:\\nope\\sub", str(fallback)) == str(fallback)
        assert fallback.is_dir()


def test_atomic_write_bytes_and_text(tmp_path):
    p = tmp_path / "cfg.json"
    atomic_write_text(p, json.dumps({"a": 1}, ensure_ascii=False))
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}
    atomic_write_bytes(p, b"raw")
    assert p.read_bytes() == b"raw"
    # 不留临时文件
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")]


def test_atomic_write_cleans_up_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "x.bin"
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("模拟写入失败")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(p, b"payload")
    monkeypatch.setattr(os, "replace", real_replace)
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")]
    assert not p.exists()


# ---------- resolve_data_dir 的优先级 ----------

def test_env_var_wins(tmp_path, monkeypatch):
    target = tmp_path / "custom-home"
    monkeypatch.setenv(ENV_HOME, str(target))
    root, origin = resolve_data_dir()
    assert root == target.resolve()
    assert ENV_HOME in origin


def test_portable_marker_keeps_data_beside_program(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_HOME, raising=False)
    prog = tmp_path / "app"
    prog.mkdir()
    (prog / PORTABLE_MARKER).write_text("", encoding="utf-8")
    root, origin = resolve_data_dir(prog_dir=prog)
    assert root == prog
    assert "便携" in origin


def test_falls_back_to_user_data_dir_when_program_dir_readonly(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_HOME, raising=False)
    prog = tmp_path / "app"
    prog.mkdir()
    monkeypatch.setattr("core.appdirs.is_writable_dir", lambda _p: False)
    root, origin = resolve_data_dir(prog_dir=prog)
    assert root == user_data_dir()
    assert "用户数据目录" in origin


# ---------- 配置自愈 ----------

def test_settings_heal_clears_unreachable_paths(tmp_path, monkeypatch):
    from config import Settings

    if sys.platform != "win32" or os.path.exists("Z:\\"):
        pytest.skip("需要一个不存在的盘符来验证自愈")
    s = Settings(ffprobe_path="Z:\\tools\\ffprobe.exe",
                 archive_db_path="Z:\\lib\\archive.db",
                 log_dir="Z:\\logs")
    cleared = s.heal(str(tmp_path))
    assert s.ffprobe_path == ""
    assert s.archive_db_path == ""
    assert s.log_dir == ""
    assert set(cleared) >= {"ffprobe_path", "archive_db_path", "log_dir"}


def test_config_manager_keeps_usable_absolute_path(tmp_path):
    from config import ConfigManager

    cfg = ConfigManager(str(tmp_path / "data"))
    db = tmp_path / "elsewhere" / "archive.db"
    cfg.settings.archive_db_path = str(db)
    assert cfg.archive_db_path == str(db)
    cfg.settings.archive_db_path = ""
    assert cfg.archive_db_path.endswith("archive.db")


def test_config_manager_survives_corrupt_settings(tmp_path):
    from config import ConfigManager

    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text("{ 这不是 JSON", encoding="utf-8")
    cfg = ConfigManager(str(data))
    assert cfg.settings.theme == "dark"          # 回落到默认值而不是崩溃
    assert cfg.save_settings() is None
    assert json.loads((data / "settings.json").read_text(encoding="utf-8"))


def test_log_dir_effective_falls_back(tmp_path, monkeypatch):
    from config import ConfigManager

    cfg = ConfigManager(str(tmp_path / "data"))
    monkeypatch.setattr("config.is_writable_dir", lambda _p: False)
    assert cfg.log_dir_effective == cfg.default_log_dir
