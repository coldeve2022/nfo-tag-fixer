# -*- coding: utf-8 -*-
"""「补全稀疏标签」的审核数据模型：把 AI 结果 + 候选元信息，整理成可展示/可编辑的行记录。

背景：ai_extract_type_tags() 只返回 {path: [新标签]}，不含标题/原 tag/文件名等展示元信息，
而候选 cand 里有这些但缺 AI 结果。本模型把两者合并，供「标签补全」管理页（文件维度 +
标签维度两个视图）消费。

设计（遵循用户的审核直觉）：
- 文件维度：每行一个视频，能看清 文件名/标题/原tag/AI建议tag，行内可编辑建议tag，
  勾选决定是否写入。
- 标签维度：把建议 tag 去重汇总，看每个标签被多少视频使用，可全局勾选采用/剔除。

不写盘、不碰 NFO——只是把「建议方案」整理成可读结构；真正写入由调用方
（apply_appended_tags）执行并带 .bak 备份。
"""

from __future__ import annotations

from collections import Counter, defaultdict


class EnrichItem:
    """一个视频的补全建议行。"""

    __slots__ = ("path", "name", "title", "existing", "suggested")

    def __init__(self, path: str, name: str, title: str,
                 existing: list[str], suggested: list[str]):
        self.path = path          # NFO 绝对路径
        self.name = name          # 番号/文件名（无标题时兜底）
        self.title = title        # 标题（可空）
        self.existing = list(existing)    # 已存在的 tag/genre（去重有序）
        self.suggested = list(suggested)  # AI 建议新增的 tag（可编辑，去重有序）

    def display_name(self) -> str:
        """表格里显示的名称：优先标题，其次番号/文件名。"""
        return (self.title or self.name or "").strip()

    def to_dict(self) -> dict:
        return {"path": self.path, "name": self.name, "title": self.title,
                "existing": list(self.existing), "suggested": list(self.suggested)}


def build_enrich_items(candidates: list[dict], proposed: dict) -> list[EnrichItem]:
    """合并候选元信息 + AI 建议，得到文件维度的行记录。

    candidates: [{path,name,title,tags:set}]   —— 阶段 A 收集的稀疏标签候选
    proposed:   {path: [新标签]}               —— ai_extract_type_tags 的返回

    只有同时出现在 candidates（有元信息）且 proposed（有 AI 建议）里的路径会被纳入。
    顺序按 candidates 出现顺序保持稳定。
    """
    meta = {}
    for c in candidates:
        p = c.get("path")
        if not p:
            continue
        meta[p] = c
    items: list[EnrichItem] = []
    for p, new_tags in proposed.items():
        c = meta.get(p)
        if not c:
            # proposed 里有的路径但候选元信息缺失（理论上不会，防御用）：用最小信息兜底
            items.append(EnrichItem(
                path=p, name=p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
                title="", existing=[], suggested=list(new_tags)))
            continue
        existing = sorted({str(t) for t in (c.get("tags") or set())})
        suggested = _dedup_ordered(new_tags)
        items.append(EnrichItem(
            path=p,
            name=(c.get("name") or "").strip(),
            title=(c.get("title") or "").strip(),
            existing=existing,
            suggested=suggested))
    return items


def build_tag_summary(items: list[EnrichItem]) -> list[dict]:
    """按标签维度汇总：每个建议标签被多少视频使用、覆盖哪些文件。

    返回 [{tag, count, 采用与否(由调用方决定), files:[EnrichItem]}...]，按 count 降序。
    """
    tag_files: dict[str, list[EnrichItem]] = defaultdict(list)
    for it in items:
        for t in it.suggested:
            tag_files[t].append(it)
    summary = []
    for tag, files in tag_files.items():
        summary.append({"tag": tag, "count": len(files), "files": files})
    summary.sort(key=lambda x: (-x["count"], x["tag"]))
    return summary


def _dedup_ordered(seq) -> list[str]:
    """去重且保持顺序（大小写敏感保留原样）。"""
    seen = set()
    out = []
    for x in seq:
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def apply_global_decision(items: list[EnrichItem], tag_decision: dict) -> list[EnrichItem]:
    """根据标签维度的全局决策（{tag: 采用 bool}），改写每个文件的建议列表。

    返回新的 items（不修改原对象），剔除被全局否决的标签。
    """
    new_items = []
    for it in items:
        kept = [t for t in it.suggested if tag_decision.get(t, True)]
        new_items.append(EnrichItem(it.path, it.name, it.title,
                                    list(it.existing), kept))
    return new_items


def count_tags(items: list[EnrichItem]) -> Counter:
    """统计建议标签整体出现次数（去重前）。"""
    c: Counter = Counter()
    for it in items:
        for t in it.suggested:
            c[t] += 1
    return c
