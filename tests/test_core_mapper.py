# -*- coding: utf-8 -*-
"""core.mapper：规则匹配、预览不污染原树、应用落盘。"""

from __future__ import annotations

from core.mapper import (Mapper, MappingRule, apply_replace_matching,
                         _remove_matching)
from core.nfo import NfoFile
from core.scanner import scan_paths
from tests.conftest import make_nfo


def _items(demo_library, recursive=True):
    return scan_paths([str(demo_library)], recursive=recursive)


def test_matches_exact_and_regex():
    r = MappingRule(match_tags=["有码", "censored"])
    assert r.matches(["有码"])
    assert r.matches(["CENSORED"]) is False      # 精确匹配大小写敏感
    assert not r.matches(["HD"])
    rr = MappingRule(match_tags=[r"uncensored|decensored"], use_regex=True)
    assert rr.matches(["uncensored"])
    assert rr.matches(["Decensored"]) is False
    assert not rr.matches(["HD"])
    assert MappingRule(match_tags=[]).matches(["有码"]) is False


def test_preview_does_not_pollute_tree(demo_library):
    items = _items(demo_library)
    mapper = Mapper([MappingRule(match_tags=["有码"], new_tag="无码破解")])
    before = [list(it.nfo.tags) for it in items]
    changes = mapper.preview_items(items)
    after = [list(it.nfo.tags) for it in items]
    assert before == after, "preview 污染了原 NFO 树"
    assert changes, "应当有命中的变更"
    # 可重复预览，结果一致
    again = mapper.preview_items(items)
    assert len(again) == len(changes)


def test_preview_skips_unmatched_rules(demo_library):
    items = _items(demo_library)
    mapper = Mapper([MappingRule(match_tags=["完全不存在的标签"], new_tag="x")])
    assert mapper.preview_items(items) == []


def test_apply_items_mutates_and_returns_changes(demo_library):
    items = _items(demo_library)
    mapper = Mapper([MappingRule(match_tags=["有码"], new_tag="无码破解")])
    changes = mapper.apply_items(items)
    assert changes
    hit = next(it for it in items if it.nfo.basename == "ABC-123.nfo")
    assert "无码破解" in hit.nfo.tags
    assert "有码" not in hit.nfo.tags


def test_remove_matching_covers_tag_and_genre(demo_library):
    items = _items(demo_library)
    nfo = next(it.nfo for it in items if it.nfo.basename == "ABC-123.nfo")
    assert _remove_matching(nfo, ["剧情"]) == 1
    assert nfo.genres == []
    assert apply_replace_matching(nfo, ["有码"], "无码破解") >= 1
    assert "无码破解" in nfo.tags


def test_rule_serialisation_roundtrip():
    r = MappingRule(name="测试规则", match_tags=["有码"], operation="append",
                    new_tag="无码", use_regex=False)
    back = MappingRule.from_dict(r.to_dict())
    assert back.to_dict() == r.to_dict()
    assert "追加" in r.describe()
    assert "删除" in MappingRule(operation="remove", match_tags=["a"]).describe()


def test_append_only_touches_tag_not_genre(tmp_path):
    p = make_nfo(tmp_path / "a.nfo",
                 '<?xml version="1.0" encoding="UTF-8"?><movie>'
                 '<tag>有码</tag><genre>有码</genre></movie>')
    nfo = NfoFile(str(p))
    Mapper([MappingRule(match_tags=["有码"], operation="append",
                        new_tag="无码")]).apply_items([type("I", (), {"nfo": nfo})()])
    assert nfo.tags == ["有码", "无码"]
    assert nfo.genres == ["有码"]
