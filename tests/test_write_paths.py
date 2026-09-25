# -*- coding: utf-8 -*-
"""写盘路径全覆盖：每一个"直接改 XML 树"的入口都必须真的落盘 + 生成 .bak。

为什么单独一个文件：`NfoFile.save()` 靠 ``dirty`` 标记判断要不要写。
凡是绕开 `NfoFile` 的写方法、直接改 ``nfo.root`` 的代码，一旦忘记
``nfo.mark_dirty()``，save() 就会**静默跳过写盘**——用户点了"应用"却什么都没发生，
且不报错。这类缺陷只有真的把每条路径跑一遍才抓得到。
"""

from __future__ import annotations

from core.mapper import Mapper, MappingRule
from core.nfo import NfoFile
from core.nfo_repair import backfill_actors
from core.tag_organizer import apply_appended_tags, apply_organize
from tests.conftest import SAMPLE_NFO, make_nfo

TWO_TAGS = ('<?xml version="1.0" encoding="UTF-8"?><movie>'
            '<tag>片商: MOODYZ</tag><tag>素人娘</tag><tag>HD</tag></movie>')


def test_mapper_apply_then_save_writes(tmp_path):
    p = make_nfo(tmp_path / "a.nfo", SAMPLE_NFO)
    nfo = NfoFile(str(p))
    item = type("I", (), {"nfo": nfo})()
    Mapper([MappingRule(match_tags=["有码"], new_tag="无码破解")]).apply_items([item])
    assert nfo.save(backup=True) is None
    assert (tmp_path / "a.nfo.bak").exists()
    assert NfoFile(str(p)).tags == ["无码破解", "HD"]


def test_organize_mapping_writes_and_backs_up(tmp_path):
    p = make_nfo(tmp_path / "b.nfo", TWO_TAGS)
    ok, errs = apply_organize([str(p)], {
        "片商: MOODYZ": {"action": "remove"},
        "素人娘": {"action": "merge", "target": "素人"},
    }, backup=True)
    assert (ok, errs) == (1, [])
    assert (tmp_path / "b.nfo.bak").exists()
    assert sorted(NfoFile(str(p)).tags) == ["HD", "素人"]


def test_append_tags_writes_and_backs_up(tmp_path):
    p = make_nfo(tmp_path / "c.nfo", SAMPLE_NFO)
    ok, errs = apply_appended_tags([str(p)], {str(p): ["Fカップ", "中出"]})
    assert (ok, errs) == (1, [])
    assert (tmp_path / "c.nfo.bak").exists()
    assert {"Fカップ", "中出"} <= set(NfoFile(str(p)).tags)


def test_backfill_actors_writes_and_backs_up(tmp_path):
    p = make_nfo(tmp_path / "d.nfo",
                 '<?xml version="1.0" encoding="UTF-8"?><movie>'
                 '<tag>示例演员</tag></movie>')
    ok, errs = backfill_actors([{"path": str(p), "names": ["示例演员"]}])
    assert (ok, errs) == (1, [])
    assert (tmp_path / "d.nfo.bak").exists()
    assert NfoFile(str(p)).actors == ["示例演员"]


def test_untouched_file_is_never_written_or_backed_up(tmp_path):
    """没有任何改动时 save() 必须直接返回：不写盘、不生成 .bak、不动 mtime。"""
    import os
    p = make_nfo(tmp_path / "e.nfo", SAMPLE_NFO)
    nfo = NfoFile(str(p))
    before = os.path.getmtime(str(p))
    assert nfo.save(backup=True) is None
    assert not (tmp_path / "e.nfo.bak").exists()
    assert os.path.getmtime(str(p)) == before


def test_minimal_nfo_can_still_gain_tags(tmp_path):
    """根元素没有子节点时也必须能加标签。

    旧代码用 ``if not self.root`` 判空——ElementTree 的 ``Element.__bool__``
    判的是"有没有子节点"，所以空 ``<movie/>`` 会被当成 None，标签加不进去。
    """
    p = tmp_path / "f.nfo"
    p.write_text('<?xml version="1.0" encoding="UTF-8"?><movie></movie>',
                 encoding="utf-8")
    nfo = NfoFile(str(p))
    assert nfo.is_valid
    assert nfo.add_tag("巨乳") is True
    assert nfo.save(backup=False) is None
    assert NfoFile(str(p)).tags == ["巨乳"]
