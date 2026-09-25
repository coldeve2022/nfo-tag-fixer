# -*- coding: utf-8 -*-
"""导入扫描：拖拽路径（文件/文件夹）→ 嵌套扫描 NFO → 去重 → 关联视频文件。

视频扩展名：mp4/mkv/avi/wmv/flv/mov/ts/m2ts/webm
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .nfo import NfoFile

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".flv", ".mov", ".ts", ".m2ts", ".webm", ".rmvb"}
NFO_EXT = ".nfo"


@dataclass
class ScanItem:
    """导入列表中的一项：NFO 文件 + 可选关联视频。"""
    nfo: NfoFile
    video_path: str = ""
    video_size: int = 0
    video_mtime: float = 0.0
    scan_errors: list[str] = field(default_factory=list)

    @property
    def dir(self) -> str:
        return os.path.dirname(self.nfo.path)


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def find_nfo_files(root: str, recursive: bool = True) -> list[str]:
    """在目录（或单个文件）中找 NFO。返回去重后的绝对路径列表。"""
    out: list[str] = []
    if os.path.isfile(root):
        if root.lower().endswith(NFO_EXT):
            out.append(os.path.abspath(root))
        return out
    if not os.path.isdir(root):
        return out
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.lower().endswith(NFO_EXT):
                    out.append(os.path.abspath(os.path.join(dirpath, fn)))
    else:
        try:
            for fn in os.listdir(root):
                if fn.lower().endswith(NFO_EXT):
                    out.append(os.path.abspath(os.path.join(root, fn)))
        except OSError:
            pass
    # 去重保序
    seen = set()
    dedup = []
    for p in out:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup


def find_video_for(nfo_path: str) -> str:
    """找与 NFO 关联的视频文件。返回路径或 ""。

    两级判定：

    1. **完全同名**（只有扩展名不同）——绝大多数刮削源的命名方式。
    2. **以 NFO 主名开头，且后面紧跟分隔符** ——
       覆盖 `abc-123-1080p.mp4` / `abc-123_U.mp4` / `abc-123.1080p.mp4` 这类
       "主名 + 分隔符 + 后缀"的命名。

    刻意**不**接受"主名 + 直接跟数字"（`abc-1231080p.mp4`）：那和
    `abc-1234.mp4`（另一部作品）无法区分。宁可漏认，也不能错认 ——
    错认会让健康检查误报"视频存在"，并让破解找回拿别的文件大小去打分。

    关于 `os.path.splitext`：历史缺陷是 `base.startswith(stem.split("-")[0])`，
    而 `stem` 是**完整路径**，于是拿"目录路径 + 文件名前缀"去和一个纯文件名比，
    永远为假，兜底分支从来没生效过。后果：`abc-123.nfo` 旁边叫
    `abc-123-1080p.mp4` 的视频关联不上 → 文件大小/修改时间为空、
    破解找回的"文件大小异常"线索失效、**健康检查误报"孤儿 NFO"**。

    结果按文件名排序，保证同一目录重复扫描得到同一结果。
    """
    stem_path = os.path.splitext(nfo_path)[0]
    d = os.path.dirname(nfo_path)
    base_stem = os.path.basename(stem_path)

    for ext in sorted(VIDEO_EXTS):
        cand = stem_path + ext
        if os.path.isfile(cand):
            return cand

    if not base_stem:
        return ""
    try:
        for fn in sorted(os.listdir(d)):
            low = fn.lower()
            if not low.endswith(tuple(VIDEO_EXTS)):
                continue
            base = os.path.splitext(fn)[0]
            if not base.startswith(base_stem) or len(base) <= len(base_stem):
                continue
            rest = base[len(base_stem):]
            if rest[:1] in ("-", "_", ".", " ", "+"):
                return os.path.join(d, fn)
    except OSError:
        pass
    return ""


def scan_paths(paths: list[str], recursive: bool = True,
               already_loaded: set[str] | None = None) -> list[ScanItem]:
    """扫描拖入的路径，返回 ScanItem 列表。

    already_loaded: 已加载的 NFO 路径集合（绝对路径），用于导入去重。
    """
    if already_loaded is None:
        already_loaded = set()
    nfo_paths: list[str] = []
    for p in paths:
        nfo_paths.extend(find_nfo_files(p, recursive=recursive))
    # 去掉已加载的
    nfo_paths = [p for p in nfo_paths if p not in already_loaded]

    items: list[ScanItem] = []
    for p in nfo_paths:
        item = ScanItem(nfo=NfoFile(p))
        if not item.nfo.is_valid:
            item.scan_errors.append(item.nfo.load_error or "解析失败")
        vp = find_video_for(p)
        if vp:
            item.video_path = vp
            try:
                item.video_size = os.path.getsize(vp)
                item.video_mtime = os.path.getmtime(vp)
            except OSError:
                pass
        items.append(item)
    return items


def get_video_info(video_path: str) -> tuple[int, float]:
    """返回 (size, mtime)。"""
    try:
        return os.path.getsize(video_path), os.path.getmtime(video_path)
    except OSError:
        return 0, 0.0
