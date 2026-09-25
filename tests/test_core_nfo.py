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
