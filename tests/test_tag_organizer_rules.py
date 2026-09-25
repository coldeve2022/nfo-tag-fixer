# -*- coding: utf-8 -*-
"""tags 整理规则：归一化、同义/截断合并方向与护栏。

数据全部合成，不依赖任何真实素材目录。
"""

from __future__ import annotations

import pytest

from core.nfo import NfoFile
from core.tag_organizer import (TagStat, TagStats, apply_organize,
                                effective_mapping, norm_key, rule_detect,
                                suggest_similar_merges)
from tests.conftest import SAMPLE_NFO, make_nfo


def make_stats(pairs: list[tuple[str, int]], *, actors: set | None = None,
               studios: set | None = None) -> TagStats:
    stats = TagStats()
    for name, count in pairs:
        stats.tags[name] = TagStat(name=name, count=count, in_tag=True)
    stats.actors = set(actors or ())
    stats.studios = set(studios or ())
    return stats


def targets(stats: TagStats) -> dict[str, str]:
    return {n: st.sim_target for n, st in stats.tags.items() if st.sim_target}


# ---------- 归一化 ----------

@pytest.mark.parametrize("a,b", [
    ("顔射", "颜射"),
    ("無碼", "无码"),
    ("高画質", "高画质"),
    ("ハメ撮り", "はめ撮り"),      # 片假名/平假名
    ("ロングヘア", "ロングヘアー"),  # 长音符
    ("中 出し", "中出し"),          # 空白
    ("3P・4P", "3P、4P"),           # 标点
    ("3P、4P", "3p・4p"),           # 大小写
    ("騎乗位", "骑乘位"),
    ("証拠", "证据"),
    ("衛星", "卫星"),
])
def test_norm_key_equivalences(a, b):
    assert norm_key(a) == norm_key(b)


def test_norm_key_keeps_meaningful_differences():
    assert norm_key("素人") != norm_key("熟女")
    assert norm_key("美少女") != norm_key("少女")


# ---------- 同义合并（近义） ----------

def test_synonym_merges_to_simplified_when_counts_close():
    """次数接近（≤3 或 ≤15%）时简体优先 → 合并到 颜射。"""
    stats = make_stats([("顔射", 9), ("颜射", 8)])
    n = suggest_similar_merges(stats)
    assert n == 1
    assert targets(stats).get("顔射") == "颜射"


def test_synonym_merges_to_majority_when_counts_far_apart():
    """次数差距大时以主流写法为准（少往多合）。"""
    stats = make_stats([("顔射", 20), ("颜射", 3)])
    suggest_similar_merges(stats)
    assert targets(stats).get("颜射") == "顔射"


def test_okurigana_suffix_merges():
    stats = make_stats([("中出し", 10), ("中出", 9)])
    suggest_similar_merges(stats)
    assert targets(stats).get("中出し") == "中出"


def test_decor_suffix_merges():
    stats = make_stats([("美少女系", 6), ("美少女", 8)])
    suggest_similar_merges(stats)
    assert targets(stats).get("美少女系") == "美少女"


# ---------- 截断合并 ----------

def test_truncation_merges_fragment_into_mainstream():
    stats = make_stats([("素人娘", 4), ("素人", 12)])
    suggest_similar_merges(stats)
    assert targets(stats).get("素人娘") == "素人"


# ---------- 护栏：不该合并的 ----------

def test_passive_kana_suffix_is_not_merged():
    stats = make_stats([("寝取られ", 7), ("寝取", 9)])
    suggest_similar_merges(stats)
    assert "寝取られ" not in targets(stats)


def test_numeric_and_latin_suffixes_are_not_merged():
    stats = make_stats([("3P", 5), ("4P", 3)])
    suggest_similar_merges(stats)
    assert targets(stats) == {}

    stats2 = make_stats([("アナル貫通ATM", 3), ("アナル貫通", 4)])
    suggest_similar_merges(stats2)
    assert targets(stats2) == {}


def test_bare_number_short_word_not_merged():
    stats = make_stats([("18歳", 4), ("18", 6)])
    suggest_similar_merges(stats)
    assert "18歳" not in targets(stats)


def test_modifier_prefix_compound_kept_separate():
    """守卫A：无码破解 / 无码流出 语义 ≠ 无码，不能被吞进「无码」。"""
    stats = make_stats([("无码破解", 12), ("无码", 20), ("无码流出", 6)])
    suggest_similar_merges(stats)
    t = targets(stats)
    assert "无码破解" not in t
    assert "无码流出" not in t


def test_bare_tail_is_absorbed_into_compound():
    """守卫B：独立短词「破解」应从属于完整复合词「无码破解」。"""
    stats = make_stats([("无码破解", 12), ("破解", 5)])
    suggest_similar_merges(stats)
    assert targets(stats).get("破解") == "无码破解"


# ---------- 规则层误标 ----------

@pytest.mark.parametrize("name,flag", [
    ("片商: MOODYZ", "前缀"),
    ("发行: 某社", "前缀"),
    ("某些演员名", "演员名"),
])
def test_rule_detect_flags(name, flag):
    stats = make_stats([(name, 3)], actors={"某些演员名"})
    rule_detect(stats)
    assert stats.tags[name].rule_flag == flag


def test_rule_detect_leaves_normal_tags_alone():
    stats = make_stats([("巨乳", 30), ("素人", 20)])
    rule_detect(stats)
    assert all(st.rule_flag == "" for st in stats.tags.values())


# ---------- 执行 ----------

def test_effective_mapping_prefers_user_over_ai_over_rule():
    stats = make_stats([("A", 1), ("B", 1), ("C", 1), ("D", 1)])
    stats.tags["A"].rule_flag = "噪声"
    stats.tags["B"].ai_action = "merge"
    stats.tags["B"].ai_target = "D"
    stats.tags["C"].user_action = "keep"
    stats.tags["C"].rule_flag = "噪声"
    m = effective_mapping(stats)
    assert m["A"] == {"action": "remove"}
    assert m["B"] == {"action": "merge", "target": "D"}
    assert "C" not in m


def test_apply_organize_backs_up_and_writes(tmp_path):
    p = make_nfo(tmp_path / "x.nfo",
                 '<?xml version="1.0" encoding="UTF-8"?><movie>'
                 '<tag>片商: MOODYZ</tag><tag>素人娘</tag></movie>')
    ok, errs = apply_organize([str(p)], {
        "片商: MOODYZ": {"action": "remove"},
        "素人娘": {"action": "merge", "target": "素人"},
    }, backup=True)
    assert (ok, errs) == (1, [])
    assert (tmp_path / "x.nfo.bak").exists()
    assert NfoFile(str(p)).tags == ["素人"]


def test_apply_organize_does_not_touch_untouched_files(tmp_path):
    p = make_nfo(tmp_path / "y.nfo")
    ok, errs = apply_organize([str(p)], {"不存在的标签": {"action": "remove"}})
    assert (ok, errs) == (0, [])
    assert not (tmp_path / "y.nfo.bak").exists()


def test_apply_organize_reports_cancel(tmp_path):
    p = make_nfo(tmp_path / "z.nfo", SAMPLE_NFO)
    ok, errs = apply_organize([str(p)], {"有码": {"action": "remove"}},
                              cancel_check=lambda: True)
    assert ok == 0
    assert NfoFile(str(p)).tags == ["有码", "HD"]   # 取消后未被改动
