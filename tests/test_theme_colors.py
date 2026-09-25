# -*- coding: utf-8 -*-
"""单元格配色必须登记浅色等效值。

各页面写 `colored_cell(text, "#d7dae0")` 这类"按暗色背景设计"的常量，
浅色主题下表格是白底，近白文字直接看不见。`ui.styles.cell_color()` 会按当前
主题映射，而映射表里**没登记**的颜色会原样返回 —— 也就是回到不可读状态。
本用例扫描所有会被写进单元格的颜色，强制它们已登记。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from ui.styles import LIGHT_CELL_MAP

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

# 这些是"背景/画布"色，不是单元格文字色，且两套主题下都可读，故豁免登记：
#   organizer.ACTION_BG —— 操作下拉框的浅色底（配深色字，两主题均可读）
CELL_FACTORIES = {"colored_cell", "SortItem"}
FOREGROUND_SETTERS = {"setForeground"}
BACKGROUND_ALLOWED = {"#e6f4ea", "#fce8e6", "#fef7e0"}


def _hex_colors(node: ast.AST) -> set[str]:
    return {n.value.lower() for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.startswith("#") and len(n.value) == 7}


def collect_cell_colors() -> dict[str, set[str]]:
    """返回 {颜色: {出现位置}} —— 只收"会写进单元格"的颜色。"""
    found: dict[str, set[str]] = {}

    def add(color: str, where: str) -> None:
        found.setdefault(color, set()).add(where)

    for path in sorted(UI.rglob("*.py")):
        if path.name == "styles.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(ROOT))

        # 1) 模块级 xxx_COLOR 字典（作为单元格颜色间接传入）
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.endswith("_COLOR") \
                        and "BG" not in tgt.id:
                    for c in _hex_colors(node.value):
                        add(c, f"{rel}:{tgt.id}")

        # 2) 直接调用工厂 / setForeground
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname in CELL_FACTORIES or fname in FOREGROUND_SETTERS:
                for c in _hex_colors(node):
                    add(c, f"{rel}:{node.lineno} {fname}()")
    return found


def test_all_cell_colors_have_light_theme_mapping():
    colors = collect_cell_colors()
    assert colors, "扫描不到任何单元格颜色，说明扫描逻辑失效了"
    missing = {c: sorted(where) for c, where in colors.items()
               if c not in LIGHT_CELL_MAP and c not in BACKGROUND_ALLOWED}
    assert not missing, (
        "以下颜色没有登记浅色主题等效值，浅色下会不可读：\n"
        + "\n".join(f"  {c}  {where}" for c, where in sorted(missing.items())))


def test_light_map_values_are_legal():
    for dark, light in LIGHT_CELL_MAP.items():
        assert dark.startswith("#") and len(dark) == 7
        assert light.startswith("#") and len(light) == 7
        assert dark != light or dark in ("#2f6fb0",), (
            f"{dark} 的浅色等效值没变，等于没做映射")


def test_check_bg_differs_between_themes():
    from ui.styles import check_bg, set_theme

    set_theme("dark")
    dark = check_bg()
    set_theme("light")
    light = check_bg()
    set_theme("dark")
    assert dark != light


def test_unregistered_color_passes_through_in_dark_theme():
    from ui.styles import cell_color, set_theme

    set_theme("dark")
    assert cell_color("#123456") == "#123456"
    set_theme("light")
    assert cell_color("#123456") == "#123456"   # 未登记：原样返回（由上面的用例兜住）
    assert cell_color("") == ""
    set_theme("dark")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_qss_has_font_substituted(theme):
    from ui.styles import qss

    css = qss(theme)
    assert "__FONT__" not in css
    assert "__MONO__" not in css
    assert "font-family" in css


def test_qss_dark_and_light_differ():
    from ui.styles import qss

    assert qss("dark") != qss("light")
