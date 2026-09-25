# -*- coding: utf-8 -*-
"""NFO 文件解析 / 写入 / 编码检测。

NFO 是 XML 文件，刮削软件可能输出 UTF-8 或 GBK 编码（有时 XML 声明与实际编码不一致）。
本模块负责：
- 编码自动检测（UTF-8 / GBK），允许 XML 声明与实际编码不符的情况
- 结构化解析（tag / actor / title / uniqueid / runtime 等字段）
- 修改字段、增删 tag
- 保存（可选生成 .bak 备份），统一写回 UTF-8
"""

from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET

from core.appdirs import atomic_write_bytes

# 常见字段，用于 diff 展示时的顺序
FIELD_ORDER = ["title", "originaltitle", "sorttitle", "uniqueid", "id", "number",
               "year", "runtime", "mpaa", "country", "studio", "director",
               "actor", "genre", "tag", "plot", "outline", "thumb", "fanart"]


class NfoError(Exception):
    """NFO 解析相关错误。"""


def detect_encoding(raw: bytes) -> str:
    """检测字节流的编码。优先 UTF-8（含 BOM），其次 GBK，兜底 UTF-8。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        raw.decode("gbk")
        return "gbk"
    except UnicodeDecodeError:
        # 部分文件混用编码，兜底用 errors=replace 的 utf-8
        return "utf-8"


def _normalize_uniqueid(root: ET.Element) -> None:
    """把 <uniqueid>xxx</uniqueid>（纯文本）或 <uniqueid type="x"> 都视为普通文本字段。"""
    for el in root.iter("uniqueid"):
        if el.text is not None:
            el.text = el.text.strip()


class NfoFile:
    """单个 NFO 文件的封装。"""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.raw_bytes: bytes | None = None
        self.encoding: str = "utf-8"
        self.tree: ET.ElementTree | None = None
        self.root: ET.Element | None = None
        self.load_error: str | None = None
        self.dirty: bool = False        # 内容是否被改过（未改则 save 直接返回）
        self.reload()

    # ---------- 解析 ----------

    def reload(self) -> bool:
        """读取并解析文件。成功返回 True。"""
        try:
            with open(self.path, "rb") as f:
                self.raw_bytes = f.read()
            self.encoding = detect_encoding(self.raw_bytes)
            text = self.raw_bytes.decode(self.encoding, errors="replace")
            # 去掉 BOM
            if text.startswith("\ufeff"):
                text = text[1:]
            # 若 XML 声明声明了与实际不同的编码，修正声明（避免 lxml/ET 报错）
            if text.startswith("<?xml"):
                m = re.match(r"<\?xml[^>]*encoding\s*=\s*[\"']([^\"']+)[\"']", text)
                if m and m.group(1).lower() not in ("utf-8", "utf8"):
                    text = re.sub(r"<\?xml[^>]*\?>",
                                  '<?xml version="1.0" encoding="UTF-8"?>',
                                  text, count=1)
            self.tree = ET.ElementTree(ET.fromstring(text))
            self.root = self.tree.getroot()
            self.load_error = None
            self.dirty = False
            return True
        except ET.ParseError as e:
            self.load_error = f"XML 解析失败: {e}"
        except OSError as e:
            self.load_error = f"读取失败: {e}"
        except Exception as e:  # noqa: BLE001
            self.load_error = f"未知错误: {e}"
        self.tree = None
        self.root = None
        return False

    @property
    def is_valid(self) -> bool:
        return self.root is not None

    @property
    def basename(self) -> str:
        return os.path.basename(self.path)

    @property
    def stem(self) -> str:
        return os.path.splitext(self.basename)[0]

    # ---------- 字段读取 ----------

    def get_texts(self, tag: str) -> list[str]:
        """取某标签全部文本（strip 且去空）。"""
        if self.root is None:
            return []
        out = []
        for el in self.root.iter(tag):
            t = (el.text or "").strip()
            if t:
                out.append(t)
        return out

    def get_text(self, tag: str, default: str = "") -> str:
        vals = self.get_texts(tag)
        return vals[0] if vals else default

    @property
    def tags(self) -> list[str]:
        return self.get_texts("tag")

    @property
    def actors(self) -> list[str]:
        if self.root is None:
            return []
        out = []
        for el in self.root.iter("actor"):
            name = None
            for sub in el.iter("name"):
                t = (sub.text or "").strip()
                if t:
                    name = t
                    break
            if name is None:
                t = (el.text or "").strip()
                if t:
                    name = t
            if name and name not in out:
                out.append(name)
        return out

    @property
    def genres(self) -> list[str]:
        return self.get_texts("genre")

    @property
    def markers(self) -> list[str]:
        """打码相关标记 = tag + genre（类型）合并去重。

        刮削源可能把「有码/无码」信息写在 tag 里，也可能写在 genre（类型）里，
        两者都要纳入分组与映射。
        """
        out = list(self.tags)
        for g in self.genres:
            if g not in out:
                out.append(g)
        return out

    @property
    def title(self) -> str:
        return self.get_text("title")

    @property
    def uniqueids(self) -> list[str]:
        """所有 uniqueid / id 的文本值（去重保序）。"""
        if self.root is None:
            return []
        out = []
        for el in self.root.iter():
            if el.tag in ("uniqueid", "id") and el.text and el.text.strip():
                t = el.text.strip()
                if t not in out:
                    out.append(t)
        return out

    @property
    def number(self) -> str:
        """番号：优先 uniqueid，其次 id，最后从文件名猜测。"""
        if self.root is None:
            return ""
        for el in self.root.iter("uniqueid"):
            t = (el.text or "").strip()
            if t and re.search(r"[A-Za-z]{2,}", t):
                return t
        for el in self.root.iter("id"):
            t = (el.text or "").strip()
            if t:
                return t
        # 文件名猜测：取连续的 字母+数字 组合
        m = re.search(r"([A-Za-z]{2,}\s*-?\s*\d{2,5})", self.stem)
        if m:
            return m.group(1).replace(" ", "")
        return ""

    @property
    def runtime_minutes(self) -> int | None:
        if self.root is None:
            return None
        t = self.get_text("runtime").strip()
        if not t:
            return None
        m = re.match(r"(\d+)", t)
        return int(m.group(1)) if m else None

    @property
    def resolution(self) -> str:
        """从文件名或 NFO 猜测分辨率（1080p / 720p / 4K...）。"""
        if self.root is None:
            return ""
        for el in self.root.iter():
            if el.tag in ("res", "resolution", "fileinfo", "video"):
                t = (el.text or "").strip()
                m = re.search(r"\b(?:3840|2160|4k|1080|720|480|540)\b", t, re.I)
                if m:
                    return m.group(0).lower()
        m = re.search(r"\b(4k|2160p|1080p|1080|720p|720)\b", self.stem, re.I)
        if m:
            return m.group(1).lower()
        return ""

    # ---------- 字段写入 ----------

    def mark_dirty(self) -> None:
        """标记内容已改动。

        **直接改 ``nfo.root`` 的调用方必须调用本方法**（映射引擎、整理执行、
        演员补入、字段替换等都走这条路）。否则 ``save()`` 会认为"没改过"而跳过
        写盘，用户的修改就会静默丢失。
        tests/test_write_paths.py 逐个覆盖这些调用路径。
        """
        self.dirty = True

    def set_tag_text(self, tag: str, text: str) -> None:
        """设置某标签第一个元素的文本；不存在则追加。"""
        if self.root is None:
            return
        self.dirty = True
        els = list(self.root.iter(tag))
        if els:
            els[0].text = text
        else:
            el = ET.SubElement(self.root, tag)
            el.text = text

    def set_text(self, tag: str, text: str) -> None:
        self.set_tag_text(tag, text)

    def add_tag(self, tag: str) -> bool:
        """追加一个 tag（去重）。返回是否实际新增。"""
        if self.root is None:
            return False
        tag = tag.strip()
        if not tag or tag in self.tags:
            return False
        el = ET.SubElement(self.root, "tag")
        el.text = tag
        self.dirty = True
        return True

    def remove_tag(self, tag: str, use_regex: bool = False) -> int:
        """删除匹配的 tag 元素。返回删除数量。"""
        if self.root is None:
            return 0
        pattern = re.compile(tag) if use_regex else None
        removed = 0
        for el in list(self.root.iter("tag")):
            t = (el.text or "").strip()
            if (pattern and pattern.search(t)) or (not use_regex and t == tag):
                self.root.remove(el)
                removed += 1
                self.dirty = True
        return removed

    def replace_tag(self, old: str, new: str, use_regex: bool = False) -> int:
        """把匹配的 tag 文本替换为新 tag。返回替换数量。"""
        if self.root is None:
            return 0
        pattern = re.compile(old) if use_regex else None
        changed = 0
        for el in list(self.root.iter("tag")):
            t = (el.text or "").strip()
            if pattern:
                if pattern.search(t):
                    el.text = new
                    changed += 1
                    self.dirty = True
            elif t == old:
                el.text = new
                changed += 1
                self.dirty = True
        return changed

    def replace_genre(self, old: str, new: str, use_regex: bool = False) -> int:
        """把匹配的 genre（类型）文本替换为新值。返回替换数量。"""
        if self.root is None:
            return 0
        pattern = re.compile(old) if use_regex else None
        changed = 0
        for el in list(self.root.iter("genre")):
            t = (el.text or "").strip()
            if pattern:
                if pattern.search(t):
                    el.text = new
                    changed += 1
                    self.dirty = True
            elif t == old:
                el.text = new
                changed += 1
                self.dirty = True
        return changed

    def clear_tags(self) -> int:
        """清空全部 tag。返回移除数量。"""
        if self.root is None:
            return 0
        n = 0
        for el in list(self.root.iter("tag")):
            self.root.remove(el)
            n += 1
        if n:
            self.dirty = True
        return n

    def set_actors(self, actor_names: list[str]) -> None:
        """整体替换 actor 列表（保留既有 actor 元素，改 name 文本；不足则追加）。"""
        if self.root is None:
            return
        self.dirty = True
        actors = [a for a in actor_names if a.strip()]
        els = [el for el in self.root.findall("actor")]
        for i, el in enumerate(els):
            name_el = el.find("name")
            if i < len(actors):
                if name_el is None:
                    name_el = ET.SubElement(el, "name")
                name_el.text = actors[i]
            else:
                self.root.remove(el)
        for i in range(len(els), len(actors)):
            a = ET.SubElement(self.root, "actor")
            n = ET.SubElement(a, "name")
            n.text = actors[i]

    def to_xml_bytes(self) -> bytes:
        """序列化为 UTF-8 字节（带 XML 声明、缩进）。"""
        if not self.tree:
            return b""
        # 简易缩进
        self._indent(self.root)
        return ET.tostring(self.root, encoding="utf-8", xml_declaration=True)

    def _indent(self, elem: ET.Element, level: int = 0) -> None:
        i = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            for child in elem:
                self._indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = i
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

    # ---------- 保存 / 备份 ----------

    def save(self, backup: bool = True, target_encoding: str = "utf-8") -> str | None:
        """写回文件。backup=True 时先生成 .bak。返回错误信息或 None。

        - **原子写**：先写同目录临时文件再 os.replace，断电/被杀进程不会留下半个
          NFO（NFO 是 Jellyfin 的元数据源，写坏一个直接影响整个媒体条目）。
        - 内容与磁盘上已有字节完全一致时**不写**，避免无谓改动 mtime
          （mtime 是「破解找回」打分的最强线索，被自己刷掉会毁掉排序依据）。
        """
        if not self.tree:
            return "NFO 未解析成功，无法保存"
        if not self.dirty:
            # 内容没被改过：不写盘、也不生成 .bak（避免把 mtime 刷掉）
            return None
        data = self.to_xml_bytes()
        if backup:
            bak_path = self.path + ".bak"
            try:
                shutil.copy2(self.path, bak_path)
            except OSError as e:
                return f"备份失败: {e}"
        try:
            atomic_write_bytes(self.path, data)
        except OSError as e:
            return f"写入失败: {e}"
        self.raw_bytes = data
        self.encoding = "utf-8"
        self.dirty = False
        return None

    def restore_backup(self) -> str | None:
        """从 .bak 恢复。返回错误信息或 None。"""
        bak_path = self.path + ".bak"
        if not os.path.exists(bak_path):
            return "没有找到备份文件"
        try:
            shutil.copy2(bak_path, self.path)
        except OSError as e:
            return f"恢复失败: {e}"
        self.reload()
        return None

    @property
    def has_backup(self) -> bool:
        return os.path.exists(self.path + ".bak")

    # ---------- 其他 ----------

    def mtime(self) -> float:
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return 0.0

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NfoFile {self.basename}>"
