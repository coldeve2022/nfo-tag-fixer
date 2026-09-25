# -*- coding: utf-8 -*-
"""界面字体探测。

`QFont("Microsoft YaHei")` 在不装该字体的机器（精简版 Windows、Windows Server、
Linux、macOS）上 Qt 会**静默回退**，中文可能渲染成方块 □。按平台给候选列表并
校验字体真的存在，才是可移植写法。
"""

from __future__ import annotations

import sys

# 各平台候选（按优先级）
CANDIDATES: dict[str, list[str]] = {
    "win32": ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimHei",
              "SimSun", "Noto Sans CJK SC", "Source Han Sans SC"],
    "darwin": ["PingFang SC", "Hiragino Sans GB", "Heiti SC", "Apple SD Gothic Neo",
               "Helvetica Neue", "Arial"],
    "linux": ["Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
              "WenQuanYi Zen Hei", "Droid Sans Fallback", "DejaVu Sans"],
}

MONO_CANDIDATES: dict[str, list[str]] = {
    "win32": ["Consolas", "Cascadia Mono", "Courier New", "Noto Sans Mono CJK SC"],
    "darwin": ["Menlo", "Monaco", "Courier New"],
    "linux": ["DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono", "monospace"],
}


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _families() -> set[str]:
    """真实可用的字体族名集合。

    **必须在 QApplication 构造之后调用**：QFontDatabase 的静态接口在没有任何
    QGuiApplication 时不可用（Qt 会打印警告甚至直接 abort）。因此这里先探测实例，
    没有实例就返回空集——调用方会退回通用族名，界面照常可用。
    """
    try:
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover - 无 Qt 的纯核心环境
        return set()
    try:
        if QApplication.instance() is None:
            return set()
    except Exception:  # noqa: BLE001
        return set()
    try:
        return set(QFontDatabase.families())
    except Exception:  # noqa: BLE001
        return set()


def pick_family(candidates: list[str], available: set[str]) -> str:
    """从候选里挑第一个真实存在的；都不在则返回 ""。

    大小写/空格不敏感比较：Windows 注册表里的名称偶有 "Microsoft YaHei UI"
    与 "Microsoft YaHei  UI" 这样的差异。
    """
    if not available:
        return ""
    norm = {name.replace(" ", "").lower(): name for name in available}
    for cand in candidates:
        key = cand.replace(" ", "").lower()
        if key in norm:
            return norm[key]
    return ""


def ui_font_family() -> str:
    """返回界面主字体族名（用于 QSS 的 font-family）；找不到用通用回退串。"""
    return pick_family(CANDIDATES.get(_platform_key(), []), _families()) or "sans-serif"


def ui_font(size: int = 9):
    """返回界面主字体（QFont）。找不到任何候选时返回 Qt 默认字体。"""
    from PySide6.QtGui import QFont
    fam = pick_family(CANDIDATES.get(_platform_key(), []), _families())
    return QFont(fam, size) if fam else QFont()


def mono_font_family() -> str:
    """返回等宽字体族名（用于日志/diff 面板的 CSS）；找不到用通用回退串。"""
    fam = pick_family(MONO_CANDIDATES.get(_platform_key(), []), _families())
    return fam or "monospace"


__all__ = ["CANDIDATES", "MONO_CANDIDATES", "pick_family", "ui_font",
           "ui_font_family", "mono_font_family"]
