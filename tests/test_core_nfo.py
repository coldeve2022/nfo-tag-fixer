# -*- coding: utf-8 -*-
"""core.nfo：编码检测、解析、原子写、备份/回滚。"""

from __future__ import annotations

import os

import pytest

from core.nfo import NfoFile, detect_encoding
from tests.conftest import SAMPLE_NFO, SAMPLE_NFO_PLAIN, make_nfo


def test_detect_encoding_utf8_gbk_bom():
    assert detect_encoding("中文".encode("utf-8")) == "utf-8"
    assert detect_encoding("中文".encode("gbk")) == "gbk"
    assert detect_encoding(b"\xef\xbb\xbf" + "中文".encode("utf-8")) == "utf-8-sig"


def test_parse_utf8_and_fields(tmp_path):
    p = make_nfo(tmp_path / "a.nfo", SAMPLE_NFO)
    nfo = NfoFile(str(p))
    assert nfo.is_valid
    assert nfo.title == "示例影像 A"
    assert nfo.tags == ["有码", "HD"]
    assert nfo.genres == ["剧情"]
    assert nfo.actors == ["示例演员"]
    assert nfo.number == "ABC-123"
    # markers = tag + genre 合并
    assert nfo.markers == ["有码", "HD", "剧情"]


def test_parse_gbk_with_mismatched_xml_declaration(tmp_path):
    """刮削源常写 encoding="UTF-8" 但实际是 GBK —— 必须能读出来。"""
    p = make_nfo(tmp_path / "gbk.nfo", SAMPLE_NFO, encoding="gbk")
    nfo = NfoFile(str(p))
    assert nfo.is_valid
    assert nfo.encoding == "gbk"
    assert nfo.title == "示例影像 A"


def test_invalid_xml_sets_load_error(tmp_path):
    p = tmp_path / "bad.nfo"
    p.write_text("<movie><tag>未闭合", encoding="utf-8")
    nfo = NfoFile(str(p))
    assert not nfo.is_valid
    assert "解析失败" in (nfo.load_error or "")


def test_save_is_atomic_and_creates_backup(tmp_path):
    p = make_nfo(tmp_path / "a.nfo", SAMPLE_NFO)
    nfo = NfoFile(str(p))
    nfo.replace_tag("有码", "无码破解")
    assert nfo.save(backup=True) is None
    assert os.path.exists(str(p) + ".bak")
    assert NfoFile(str(p)).tags == ["无码破解", "HD"]
    # 原子写的临时文件不应残留
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")]


def test_save_without_change_does_not_touch_mtime(tmp_path):
    """mtime 是「破解找回」打分的最强线索，内容没变就不该改写。"""
    p = make_nfo(tmp_path / "a.nfo", SAMPLE_NFO)
    nfo = NfoFile(str(p))
    before = os.path.getmtime(str(p))
    assert nfo.save(backup=False) is None
    assert os.path.getmtime(str(p)) == before
    assert not os.path.exists(str(p) + ".bak")


def test_restore_backup_roundtrip(tmp_path):
    p = make_nfo(tmp_path / "a.nfo", SAMPLE_NFO)
    nfo = NfoFile(str(p))
    nfo.replace_tag("有码", "无码破解")
    nfo.save(backup=True)
    assert nfo.restore_backup() is None
    assert NfoFile(str(p)).tags == ["有码", "HD"]


def test_add_remove_replace_tag(tmp_path):
    p = make_nfo(tmp_path / "a.nfo", SAMPLE_NFO)
    nfo = NfoFile(str(p))
    assert nfo.add_tag("新增") is True
    assert nfo.add_tag("新增") is False          # 去重
    assert nfo.remove_tag("HD") == 1
    assert nfo.replace_genre("剧情", "故事") == 1
    assert nfo.genres == ["故事"]
    assert set(nfo.tags) == {"有码", "新增"}


def test_set_actors_keeps_and_trims(tmp_path):
    p = make_nfo(tmp_path / "a.nfo", SAMPLE_NFO)
    nfo = NfoFile(str(p))
    nfo.set_actors(["甲", "乙"])
    assert nfo.actors == ["甲", "乙"]
    nfo.set_actors(["丙"])
    assert nfo.actors == ["丙"]


def test_empty_nfo_has_no_tags(tmp_path):
    p = make_nfo(tmp_path / "e.nfo", SAMPLE_NFO_PLAIN)
    nfo = NfoFile(str(p))
    assert nfo.tags == []
    assert nfo.markers == []
    assert nfo.is_valid


@pytest.mark.parametrize("stem,expect", [
    ("ABC-123", "ABC-123"),
    ("abc-123-1080p", "abc-123"),
])
def test_number_from_filename_fallback(tmp_path, stem, expect):
    p = tmp_path / f"{stem}.nfo"
    p.write_text('<?xml version="1.0" encoding="UTF-8"?><movie><title>x</title></movie>',
                 encoding="utf-8")
    assert NfoFile(str(p)).number.lower() == expect.lower()


def test_find_video_for_is_deterministic(tmp_path):
    """同目录多个候选视频时结果必须稳定。

    `os.listdir` 的顺序由文件系统决定，不排序会导致"同一目录重复扫描
    关联到不同视频" —— 打分与建档都会跟着飘。
    """
    from core.scanner import find_video_for

    nfo = tmp_path / "ABC-123.nfo"
    nfo.write_text("x", encoding="utf-8")
    for name in ("ABC-123-b.mp4", "ABC-123-a.mp4", "ABC-123-c.mkv"):
        (tmp_path / name).write_bytes(b"\x00")
    first = find_video_for(str(nfo))
    for _ in range(5):
        assert find_video_for(str(nfo)) == first
    # 按文件名排序 → 稳定的第一个候选
    assert os.path.basename(first) == "ABC-123-a.mp4", first


@pytest.mark.parametrize("video,expect_match", [
    ("ABC-123-1080p.mp4", True),      # 主名 + 分隔符后缀（历史缺陷：漏掉）
    ("ABC-123_U.mp4", True),          # 主名 + 下划线
    ("ABC-123.1080p.mp4", True),      # 主名 + 点
    ("ABC-1231080p.mp4", False),      # 主名直接跟数字：与"另一部作品"无法区分，宁可漏认
    ("ABC-1234.mp4", False),          # 是另一部作品，不是这部
    ("ABC-999.mp4", False),           # 只共享短前缀，不能乱认亲
])
def test_find_video_for_suffix_matching(tmp_path, video, expect_match):
    """主名 + 分隔符后缀的命名必须能关联上，但不能乱认亲。"""
    from core.scanner import find_video_for

    nfo = tmp_path / "ABC-123.nfo"
    nfo.write_text("x", encoding="utf-8")
    (tmp_path / video).write_bytes(b"\x00")
    got = find_video_for(str(nfo))
    assert bool(got) is expect_match, f"{video} → {got!r}"


def test_find_video_for_prefers_exact_name(tmp_path):
    """完全同名的优先级最高（不能因为排序把别的候选提前）。"""
    from core.scanner import find_video_for

    nfo = tmp_path / "ABC-123.nfo"
    nfo.write_text("x", encoding="utf-8")
    (tmp_path / "ABC-123-a.mp4").write_bytes(b"\x00")
    (tmp_path / "ABC-123.mkv").write_bytes(b"\x00")
    assert os.path.basename(find_video_for(str(nfo))) == "ABC-123.mkv"
