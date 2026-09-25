# -*- coding: utf-8 -*-
"""GUI 冒烟：每个页面能构造、能导航、能 shutdown，键盘交互不炸。

历史背景（这几条用例就是为此写的）：
- `CheckTable.keyPressEvent` 里用了没导入的 `QKeySequence` → **任意按键**都抛
  NameError，而界面测试只测"能构造"，所以一路带到了发布；
- `FinderPage._refresh_table` 里用了没导入的 `QTableWidgetItem` → 一点「检索打分」就崩；
- `FixerPage.ai_recheck_censorship` 里用了没导入的 `QApplication` → 一点就崩。
"""

from __future__ import annotations

import importlib

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

PAGE_SPECS = [
    ("ui.pages.fixer", "FixerPage"),
    ("ui.pages.finder", "FinderPage"),
    ("ui.pages.archive", "ArchivePage"),
    ("ui.pages.organizer", "OrganizerPage"),
    ("ui.pages.nfo_repair", "RepairPage"),
    ("ui.pages.settings", "SettingsPage"),
]

NAV_LABELS = ["标签修正", "破解找回", "破解档案", "标签整理", "NFO 修复", "设置与工具"]


@pytest.mark.parametrize("module,cls", PAGE_SPECS)
def test_page_constructs(qapp, state, module, cls):
    page = getattr(importlib.import_module(module), cls)(state)
    shutdown = getattr(page, "shutdown", None)
    if callable(shutdown):
        shutdown()


def test_main_window_nav_matches_pages(qapp, state):
    from ui.main_window import MainWindow

    win = MainWindow(state)
    assert win.tabs.count() == len(PAGE_SPECS) == len(NAV_LABELS)
    labels = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert labels == NAV_LABELS, "新增/删除页面时忘了同步侧边栏条目"
    win.close()


def test_window_title_carries_version(qapp, state):
    from ui.main_window import MainWindow
    from version import __version__

    win = MainWindow(state)
    assert __version__ in win.windowTitle()
    win.close()


def test_toolbar_actions_disabled_outside_fixer_tab(qapp, state):
    from ui.main_window import MainWindow

    win = MainWindow(state)
    for idx in range(win.tabs.count()):
        win.tabs.setCurrentIndex(idx)
        enabled = {a.isEnabled() for a in win.toolbar_actions}
        assert enabled == ({True} if idx == 0 else {False})
    win.close()


def test_about_dialog_renders(qapp, state, no_modal):
    from ui.main_window import MainWindow

    win = MainWindow(state)
    win._show_about()
    win.close()


# ---------- 键盘交互（回归：QKeySequence 未导入） ----------

def test_checktable_keys_do_not_raise(qapp):
    from PySide6.QtWidgets import QTableWidgetItem

    from ui.widgets import CheckTable

    seen: list[str] = []
    table = CheckTable(on_shortcut=seen.append)
    table.setColumnCount(1)
    table.insertRow(0)
    item = QTableWidgetItem("x")
    item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
    item.setCheckState(Qt.Unchecked)
    table.setItem(0, 0, item)
    table.setCurrentCell(0, 0)

    # 这些组合以前全部抛 NameError（QKeySequence 未导入）
    QTest.keyClick(table, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(table, Qt.Key_U, Qt.ControlModifier)
    QTest.keyClick(table, Qt.Key_I, Qt.ControlModifier)
    QTest.keyClick(table, Qt.Key_Z, Qt.ControlModifier)
    QTest.keyClick(table, Qt.Key_Space)
    QTest.keyClick(table, Qt.Key_Space, Qt.ControlModifier)

    assert {"all", "none", "invert", "toggle_selected"} <= set(seen)


def test_checktable_delete_emits_rows(qapp):
    from PySide6.QtWidgets import QTableWidgetItem

    from ui.widgets import CheckTable

    got: list[list[int]] = []
    table = CheckTable()
    table.remove_rows_requested.connect(got.append)
    table.setColumnCount(1)
    table.insertRow(0)
    table.setItem(0, 0, QTableWidgetItem("x"))
    table.setCurrentCell(0, 0)
    QTest.keyClick(table, Qt.Key_Delete)
    assert got == [[0]]


def test_checktable_toggle_and_background_follow_theme(qapp):
    from PySide6.QtWidgets import QTableWidgetItem

    from ui.styles import check_bg, set_theme
    from ui.widgets import CheckTable

    table = CheckTable()
    table.setColumnCount(1)
    table.insertRow(0)
    item = QTableWidgetItem("x")
    item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
    item.setCheckState(Qt.Unchecked)
    table.setItem(0, 0, item)

    for theme in ("dark", "light"):
        set_theme(theme)
        table.set_row_checked(0, True)
        assert item.checkState() == Qt.Checked
        assert item.background().color().name() == check_bg().lower()
        table.set_row_checked(0, False)
        assert item.checkState() == Qt.Unchecked
    set_theme("dark")


def test_colored_cell_is_readable_in_light_theme(qapp):
    """浅色主题下不能把近白文字写到白底上。"""
    from ui.styles import cell_color, set_theme
    from ui.widgets import SortItem, colored_cell

    set_theme("dark")
    assert cell_color("#d7dae0") == "#d7dae0"
    set_theme("light")
    assert cell_color("#d7dae0") == "#24292f"
    item = colored_cell("文件名", "#d7dae0")
    assert item.foreground().color().name() == "#24292f"
    assert SortItem("x", "#9aa0ac").foreground().color().name() == "#6b7280"
    set_theme("dark")
