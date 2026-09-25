# -*- coding: utf-8 -*-
"""标签映射引擎：替换 / 追加 / 删除，多规则 + 正则。

规则模型（JSON 可序列化）：
{
    "id": "uuid",
    "name": "规则名",
    "match_tags": ["有码", "censored"],   # 命中任一即匹配（正则时为模式）
    "operation": "replace" | "append" | "remove",
    "new_tag": "无码破解",                 # replace/append 时使用
    "use_regex": false
}
"""

from __future__ import annotations

import copy
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict

from .nfo import NfoFile


@dataclass
class MappingRule:
    name: str = "未命名规则"
    match_tags: list[str] = field(default_factory=list)
    operation: str = "replace"          # replace / append / remove
    new_tag: str = ""
    use_regex: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @classmethod
    def from_dict(cls, d: dict) -> "MappingRule":
        return cls(
            name=str(d.get("name", "未命名规则")),
            match_tags=list(d.get("match_tags", [])),
            operation=str(d.get("operation", "replace")),
            new_tag=str(d.get("new_tag", "")),
            use_regex=bool(d.get("use_regex", False)),
            id=str(d.get("id", uuid.uuid4().hex[:8])),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def matches(self, markers: list[str]) -> bool:
        """markers = tag + genre 合并的打码标记列表。命中任一匹配项即 True。"""
        if not self.match_tags:
            return False
        for t in markers:
            for m in self.match_tags:
                if self.use_regex:
                    try:
                        if re.search(m, t):
                            return True
                    except re.error:
                        continue
                elif m == t:
                    return True
        return False

    def describe(self) -> str:
        op = {"replace": "替换为", "append": "追加", "remove": "删除"}.get(self.operation, self.operation)
        mt = ", ".join(self.match_tags) or "(无)"
        if self.operation == "remove":
            return f"删除 [{mt}]"
        return f"匹配 [{mt}] {op} 「{self.new_tag}」"


@dataclass
class Change:
    """一次映射产生的变更记录。"""
    item: object                # ScanItem
    rule: MappingRule
    old_tags: list[str]
    new_tags: list[str]
    old_xml: str
    new_xml: str

    @property
    def path(self) -> str:
        return self.item.nfo.path


class Mapper:
    """对一组 NFO 应用规则，生成变更（不落盘，由上层预览后统一执行）。"""

    def __init__(self, rules: list[MappingRule] | None = None):
        self.rules: list[MappingRule] = rules or []

    def add_rule(self, rule: MappingRule) -> None:
        self.rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        return len(self.rules) != before

    # ---------- 预览 ----------

    def preview_items(self, items: list) -> list[Change]:
        """对每个 item 依次应用全部规则，返回产生的变更列表。

        重要：预览在**深拷贝的 XML 树**上进行，不会污染 NFO 内存状态，
        可多次预览结果一致；真正落盘由上层调用 nfo.save() 完成。

        性能：先筛出真正命中的规则，未命中的规则完全不参与序列化；
        同一 item 的 old_xml 只序列化一次（每个规则命中的都是同一份"改前"状态）。
        """
        changes: list[Change] = []
        for item in items:
            nfo = item.nfo
            if not nfo.is_valid:
                continue
            matched = [r for r in self.rules if r.matches(nfo.markers)]
            if not matched:
                continue
            old_tags = list(nfo.markers)
            old_xml = nfo.to_xml_bytes().decode("utf-8", errors="replace")
            saved_root = nfo.root
            for rule in matched:
                # 深拷贝树预览，修改后恢复，避免污染原 NFO
                nfo.root = copy.deepcopy(saved_root)
                nfo.tree = ET.ElementTree(nfo.root)
                self._apply_to(nfo, rule)
                new_tags = nfo.markers
                new_xml = nfo.to_xml_bytes().decode("utf-8", errors="replace")
                nfo.root = saved_root
                nfo.tree = ET.ElementTree(saved_root)
                changes.append(Change(item=item, rule=rule,
                                      old_tags=old_tags, new_tags=new_tags,
                                      old_xml=old_xml, new_xml=new_xml))
        return changes

    def apply_items(self, items: list) -> list[Change]:
        """真实应用规则到 NFO **原树**（直接修改内存，随后由上层 save 落盘）。

        与 preview_items 的区别：preview 在深拷贝上计算（不污染、可重复预览），
        apply 直接修改原树，供「应用修改」流程使用。
        """
        changes: list[Change] = []
        for item in items:
            nfo = item.nfo
            if not nfo.is_valid:
                continue
            for rule in self.rules:
                markers = nfo.markers
                if not rule.matches(markers):
                    continue
                old_tags = list(markers)
                old_xml = nfo.to_xml_bytes().decode("utf-8", errors="replace")
                self._apply_to(nfo, rule)
                new_tags = nfo.markers
                new_xml = nfo.to_xml_bytes().decode("utf-8", errors="replace")
                changes.append(Change(item=item, rule=rule,
                                      old_tags=old_tags, new_tags=new_tags,
                                      old_xml=old_xml, new_xml=new_xml))
        return changes

    def _apply_to(self, nfo: NfoFile, rule: MappingRule) -> None:
        """把规则应用到 nfo（内存中修改）。

        replace / remove 同时作用于 tag 与 genre（类型）字段；
        append 只向 tag 追加。
        """
        if rule.operation == "replace":
            if apply_replace_matching(nfo, rule.match_tags, rule.new_tag,
                                      rule.use_regex):
                nfo.mark_dirty()
        elif rule.operation == "append":
            nfo.add_tag(rule.new_tag)        # add_tag 内部已标脏
        elif rule.operation == "remove":
            if _remove_matching(nfo, rule.match_tags, rule.use_regex):
                nfo.mark_dirty()

    # ---------- 序列化 ----------

    def to_dict_list(self) -> list[dict]:
        return [r.to_dict() for r in self.rules]

    @classmethod
    def from_dict_list(cls, data: list[dict]) -> "Mapper":
        return cls(rules=[MappingRule.from_dict(d) for d in data])


def apply_replace_matching(nfo: NfoFile, match_tags: list[str], new_tag: str,
                           use_regex: bool = False) -> int:
    """替换所有命中任一 match_tags 的 tag / genre 为 new_tag。返回变更数量。"""
    if not use_regex:
        changed = 0
        for m in match_tags:
            changed += nfo.replace_tag(m, new_tag)
            changed += nfo.replace_genre(m, new_tag)
        return changed
    # 正则：合并模式，一次性替换命中元素（tag + genre）
    patterns = []
    for m in match_tags:
        try:
            patterns.append(re.compile(m))
        except re.error:
            continue
    if not patterns:
        return 0
    changed = 0
    for f in ("tag", "genre"):
        for el in list(nfo.root.iter(f)):
            t = (el.text or "").strip()
            if any(p.search(t) for p in patterns):
                el.text = new_tag
                changed += 1
    return changed


def _remove_matching(nfo: NfoFile, match_tags: list[str], use_regex: bool = False) -> int:
    """删除所有命中任一 match_tags 的 tag / genre 元素。返回删除数量。"""
    removed = 0
    patterns = []
    if use_regex:
        for m in match_tags:
            try:
                patterns.append(re.compile(m))
            except re.error:
                continue
    for f in ("tag", "genre"):
        for el in list(nfo.root.iter(f)):
            t = (el.text or "").strip()
            hit = False
            if use_regex:
                hit = any(p.search(t) for p in patterns)
            else:
                hit = t in match_tags
            if hit:
                nfo.root.remove(el)
                removed += 1
    return removed
