# -*- coding: utf-8 -*-
"""标签分析：统计全部 tag 出现频率，并给出「有码类 / 无码类」分组建议。

关键词体系（可配置扩展）：
- 有码类: 有码, censored, 骑兵, 素人除外...
- 无码类: 无码, 破解, uncensored, decensored, 流出, restored, 步兵, fc2
"""

from __future__ import annotations

import re
from collections import Counter

from .nfo import NfoFile

# 有码关键词（子串匹配）
CENSORED_KEYWORDS = ["有码", "骑兵", "censored", "已删减"]
# 无码关键词（子串匹配）：含简体/繁体/日文写法
UNCENSORED_KEYWORDS = ["无码", "無碼", "無修正", "破解", "uncensored", "decensored",
                       "流出", "restored", "步兵", "fc2", "无修", "無修"]

# 强无码特征（正则，文件名 / tag 命中即高置信）
STRONG_UNCENSORED_RE = [
    re.compile(r"uncensored", re.I),
    re.compile(r"decensored", re.I),
    re.compile(r"\bFC2[-\s]?\d{5,}", re.I),
    re.compile(r"无码|無碼", ),
    re.compile(r"无修|無修", ),
    re.compile(r"破解", ),
    re.compile(r"無修正", ),
]


def count_tags(nfo_files: list[NfoFile]) -> Counter:
    """统计所有 NFO 中 tag 值的出现频率。"""
    counter: Counter = Counter()
    for nfo in nfo_files:
        if not nfo.is_valid:
            continue
        for t in nfo.tags:
            counter[t] += 1
    return counter


def count_markers(nfo_files: list[NfoFile]) -> Counter:
    """统计 tag + genre（类型）中所有打码相关写法的出现频率。

    刮削源可能把「有码/无码」写在 tag 或 genre 里，统计时两者都纳入，
    方便用户一次确认全部分组。
    """
    counter: Counter = Counter()
    for nfo in nfo_files:
        if not nfo.is_valid:
            continue
        for t in nfo.markers:
            counter[t] += 1
    return counter


def classify_tag(tag: str) -> str:
    """分类单个 tag：返回 'uncensored' / 'censored' / 'unknown'。"""
    low = tag.lower()
    for kw in UNCENSORED_KEYWORDS:
        if kw.lower() in low:
            return "uncensored"
    for kw in CENSORED_KEYWORDS:
        if kw.lower() in low:
            return "censored"
    return "unknown"


def suggest_groups(counter: Counter) -> tuple[list[str], list[str]]:
    """基于关键词给出建议：返回 (有码类 tag 列表, 无码类 tag 列表)。

    只对出现过的 tag 给出建议，供用户在统计对话框里快速勾选。
    """
    censored, uncensored = [], []
    for tag in counter:
        cls = classify_tag(tag)
        if cls == "uncensored":
            uncensored.append(tag)
        elif cls == "censored":
            censored.append(tag)
    return censored, uncensored


def split_items_by_tag(items: list, censored_tags: set[str], uncensored_tags: set[str]):
    """把导入项按 tag 归属分为 (有码项, 无码项, 待确认项, 无tag项)。

    items: ScanItem 列表。返回四组。
    """
    c, u, unknown, no_tag = [], [], [], []
    for it in items:
        if not it.nfo.is_valid:
            unknown.append(it)
            continue
        tags = it.nfo.tags
        if not tags:
            no_tag.append(it)
            continue
        has_c = any(t in censored_tags for t in tags)
        has_u = any(t in uncensored_tags for t in tags)
        if has_c and not has_u:
            c.append(it)
        elif has_u and not has_c:
            u.append(it)
        else:
            unknown.append(it)
    return c, u, unknown, no_tag
