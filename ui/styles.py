# -*- coding: utf-8 -*-
"""主题：深色 / 浅色，以及**按主题自适应**的单元格配色。

关于配色：各页面调用 ``colored_cell(text, "#d7dae0")`` 这类写法时用的都是
"按暗色背景设计"的常量。浅色主题下表格背景是白的，再写 `#d7dae0`（近白）
文字就基本看不见了。因此所有单元格颜色都经过 :func:`cell_color` 做一次
**当前主题映射**——调用点不必关心主题，新增颜色只需在 ``LIGHT_CELL_MAP``
里登记一个浅色等效值（tests/test_theme_colors.py 会强制要求登记）。
"""

from __future__ import annotations

# ============================================================
# 单元格配色（语义 → 两套取值）
# ============================================================

# 键 = 暗色主题下的取值，值 = 浅色主题下的等效取值
LIGHT_CELL_MAP: dict[str, str] = {
    "#d7dae0": "#24292f",   # 常规文字（文件名/演员）
    "#b6bcc8": "#4b5563",   # 次级文字（文件大小）
    "#9fd0ff": "#1a6fb5",   # 番号
    "#9aa0ac": "#6b7280",   # 弱化文字（其他标签/提示）
    "#8a8f99": "#6b7280",   # 时间戳
    "#ff6b6b": "#c0392b",   # 有码 / 错误
    "#ff8a8a": "#c0392b",   # 有码（状态列）
    "#8ae6a1": "#1e8449",   # 无码 / 成功
    "#ffd77a": "#b9770e",   # 待确认 / 警告
    "#e056fd": "#8e44ad",   # 冲突
    "#c56cf0": "#8e44ad",   # 需人工确认
    "#7ed6ff": "#1a6fb5",   # 强调（线索/依据）
    "#1e8e3e": "#1e8449",   # 最佳 / 保留
    "#d93025": "#c0392b",   # 待恢复
    "#ff9f43": "#b9770e",   # 片商标记
    "#b8860b": "#8a6d00",   # 合并目标
    "#b06000": "#8a6d00",   # 合并操作
    "#ff7b7b": "#c0392b",   # 日志错误
}

CHECK_BG = {"dark": "#2f6fb0", "light": "#cde2f7"}   # 勾选行高亮背景

_current_theme = "dark"


def set_theme(theme: str) -> None:
    """设置当前主题（"dark" / "light"）。应用启动与切换主题时调用。"""
    global _current_theme
    _current_theme = "light" if str(theme).lower() == "light" else "dark"


def current_theme() -> str:
    return _current_theme


def is_dark() -> bool:
    return _current_theme == "dark"


def cell_color(color: str) -> str:
    """把单元格颜色映射为当前主题下的等效取值。

    未登记的颜色原样返回（QSS 里用的颜色不该走这里）。空串返回空串，
    表示"用控件默认前景色"。
    """
    if not color:
        return color
    if _current_theme == "dark":
        return color
    return LIGHT_CELL_MAP.get(color.lower(), color)


def check_bg() -> str:
    """勾选行的高亮背景色（跟随主题）。"""
    return CHECK_BG[_current_theme]


# ============================================================
# 深色主题
# ============================================================
_DARK_TEMPLATE = """
* {
    font-family: __FONT__;
    font-size: 13px;
}
QMainWindow, QDialog, QWidget {
    background-color: #1e2128;
    color: #d7dae0;
}
QToolBar {
    background-color: #262a33;
    border: none;
    padding: 4px 8px;
    spacing: 6px;
}
QToolBar QToolButton {
    background-color: #2f3542;
    color: #d7dae0;
    border: 1px solid #3a4150;
    border-radius: 6px;
    padding: 5px 14px;
}
QToolBar QToolButton:hover {
    background-color: #3a4150;
    border-color: #4a5266;
}
QToolBar QToolButton:pressed {
    background-color: #262b35;
}
QToolButton:disabled {
    color: #6a6f7a;
    background-color: #2a2e37;
}

QTabWidget::pane {
    border: 1px solid #333a47;
    background-color: #1e2128;
    border-radius: 6px;
}
QTabBar::tab {
    background-color: #262a33;
    color: #9aa0ac;
    padding: 7px 22px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #2f3542;
    color: #ffffff;
    border-bottom: 2px solid #4a9fd8;
}
QTabBar::tab:hover:!selected {
    background-color: #2b303b;
}

QSplitter::handle {
    background-color: #2a2e37;
    width: 3px;
}
QSplitter::handle:hover {
    background-color: #3a4150;
}

QTableWidget, QTableView {
    background-color: #232730;
    alternate-background-color: #262b35;
    color: #d7dae0;
    gridline-color: #333a47;
    border: 1px solid #333a47;
    border-radius: 6px;
    selection-background-color: #2f5d8a;
    selection-color: #ffffff;
}
QTableWidget::item, QTableView::item {
    padding: 3px 6px;
}
QHeaderView::section {
    background-color: #2a2e37;
    color: #b6bcc8;
    border: none;
    border-bottom: 1px solid #3a4150;
    padding: 5px 8px;
    font-weight: bold;
}

QPlainTextEdit, QTextEdit, QLineEdit, QComboBox, QSpinBox {
    background-color: #232730;
    color: #d7dae0;
    border: 1px solid #3a4150;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: #2f5d8a;
}
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: #4a9fd8;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #232730;
    color: #d7dae0;
    selection-background-color: #2f5d8a;
    border: 1px solid #3a4150;
}

QCheckBox, QRadioButton {
    spacing: 6px;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #6a7180;
    background-color: #16181e;
    border-radius: 4px;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: #9fb4d8;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #3f8ad4;
    border: 2px solid #8ec9ff;
}
QCheckBox::indicator:checked:hover, QRadioButton::indicator:checked:hover {
    background-color: #54a0e8;
}

QPushButton {
    background-color: #2f3542;
    color: #d7dae0;
    border: 1px solid #3a4150;
    border-radius: 6px;
    padding: 5px 16px;
}
QPushButton:hover {
    background-color: #3a4150;
    border-color: #4a5266;
}
QPushButton:pressed {
    background-color: #262b35;
}
QPushButton:disabled {
    color: #6a6f7a;
    background-color: #2a2e37;
}
QPushButton.primary {
    background-color: #2f6fb0;
    border-color: #3f83c8;
    color: #ffffff;
}
QPushButton.primary:hover {
    background-color: #3a7fc4;
}
QPushButton.danger {
    background-color: #8a3030;
    border-color: #b04848;
    color: #ffffff;
}
QPushButton.danger:hover {
    background-color: #a03a3a;
}

QGroupBox {
    border: 1px solid #333a47;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
    background-color: #20242c;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #9fd0ff;
}

QScrollBar:vertical {
    background: #232730;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3a4150;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #4a5266;
}
QScrollBar:horizontal {
    background: #232730;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #3a4150;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}

QStatusBar {
    background-color: #262a33;
    color: #9aa0ac;
}
QStatusBar::item {
    border: none;
}

QProgressBar {
    background-color: #232730;
    border: 1px solid #3a4150;
    border-radius: 5px;
    text-align: center;
    color: #d7dae0;
}
QProgressBar::chunk {
    background-color: #2f6fb0;
    border-radius: 4px;
}

QMenu {
    background-color: #232730;
    color: #d7dae0;
    border: 1px solid #3a4150;
}
QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #2f5d8a;
}
QMenu::separator {
    height: 1px;
    background-color: #3a4150;
    margin: 4px 8px;
}

/* 表格勾选列（QTableView::indicator）高对比 */
QTableView::indicator {
    width: 17px;
    height: 17px;
    border: 2px solid #6a7180;
    background-color: #16181e;
    border-radius: 4px;
}
QTableView::indicator:hover {
    border-color: #9fb4d8;
}
QTableView::indicator:checked {
    background-color: #3f8ad4;
    border: 2px solid #8ec9ff;
}

QLabel.tag-censored {
    color: #ff8a8a;
}
QLabel.tag-uncensored {
    color: #8ae6a1;
}
QLabel.tag-unknown {
    color: #ffd77a;
}
"""

# ============================================================
# 浅色主题
# ============================================================
_LIGHT_TEMPLATE = """
* {
    font-family: __FONT__;
    font-size: 13px;
}
QMainWindow, QDialog, QWidget {
    background-color: #f2f4f7;
    color: #24292f;
}
QToolBar {
    background-color: #ffffff;
    border: none;
    border-bottom: 1px solid #d8dde5;
    padding: 4px 8px;
    spacing: 6px;
}
QToolBar QToolButton {
    background-color: #ffffff;
    color: #24292f;
    border: 1px solid #c9d0da;
    border-radius: 6px;
    padding: 5px 14px;
}
QToolBar QToolButton:hover {
    background-color: #eef3fa;
    border-color: #8fb8e8;
}
QToolBar QToolButton:pressed {
    background-color: #e2e9f3;
}
QToolButton:disabled {
    color: #a7adb6;
    background-color: #f5f6f8;
}

QTabWidget::pane {
    border: 1px solid #d8dde5;
    background-color: #f2f4f7;
    border-radius: 6px;
}
QTabBar::tab {
    background-color: #e9edf2;
    color: #5a6472;
    padding: 7px 22px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    color: #1a6fb5;
    font-weight: bold;
    border-bottom: 2px solid #2f6fb0;
}
QTabBar::tab:hover:!selected {
    background-color: #f0f4f9;
}

QSplitter::handle {
    background-color: #d8dde5;
    width: 3px;
}
QSplitter::handle:hover {
    background-color: #a8c4e4;
}

QTableWidget, QTableView {
    background-color: #ffffff;
    alternate-background-color: #f7f9fc;
    color: #24292f;
    gridline-color: #e2e6ec;
    border: 1px solid #d8dde5;
    border-radius: 6px;
    selection-background-color: #d4e6fb;
    selection-color: #1a3a5c;
}
QTableWidget::item, QTableView::item {
    padding: 3px 6px;
}
QHeaderView::section {
    background-color: #eef1f5;
    color: #3d4754;
    border: none;
    border-bottom: 1px solid #d8dde5;
    padding: 5px 8px;
    font-weight: bold;
}

QPlainTextEdit, QTextEdit, QLineEdit, QComboBox, QSpinBox {
    background-color: #ffffff;
    color: #24292f;
    border: 1px solid #c9d0da;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: #b8d8f7;
}
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: #2f6fb0;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #24292f;
    selection-background-color: #d4e6fb;
    border: 1px solid #c9d0da;
}

QCheckBox, QRadioButton {
    spacing: 6px;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #9aa4b2;
    background-color: #ffffff;
    border-radius: 4px;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: #2f6fb0;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #2f6fb0;
    border: 2px solid #1f5c96;
}
QCheckBox::indicator:checked:hover, QRadioButton::indicator:checked:hover {
    background-color: #3a7fc4;
}

QPushButton {
    background-color: #ffffff;
    color: #24292f;
    border: 1px solid #c9d0da;
    border-radius: 6px;
    padding: 5px 16px;
}
QPushButton:hover {
    background-color: #eef3fa;
    border-color: #8fb8e8;
}
QPushButton:pressed {
    background-color: #e2e9f3;
}
QPushButton:disabled {
    color: #a7adb6;
    background-color: #f5f6f8;
}
QPushButton.primary {
    background-color: #2f6fb0;
    border-color: #1f5c96;
    color: #ffffff;
}
QPushButton.primary:hover {
    background-color: #3a7fc4;
}
QPushButton.danger {
    background-color: #c0392b;
    border-color: #a93226;
    color: #ffffff;
}
QPushButton.danger:hover {
    background-color: #d14537;
}

QGroupBox {
    border: 1px solid #d8dde5;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
    background-color: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #1a6fb5;
    font-weight: bold;
}

QScrollBar:vertical {
    background: #eef1f5;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #b9c2cf;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #9aa4b2;
}
QScrollBar:horizontal {
    background: #eef1f5;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #b9c2cf;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}

QStatusBar {
    background-color: #ffffff;
    color: #5a6472;
}
QStatusBar::item {
    border: none;
}

QProgressBar {
    background-color: #eef1f5;
    border: 1px solid #c9d0da;
    border-radius: 5px;
    text-align: center;
    color: #24292f;
}
QProgressBar::chunk {
    background-color: #2f6fb0;
    border-radius: 4px;
}

QMenu {
    background-color: #ffffff;
    color: #24292f;
    border: 1px solid #c9d0da;
}
QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #d4e6fb;
    color: #1a3a5c;
}
QMenu::separator {
    height: 1px;
    background-color: #e2e6ec;
    margin: 4px 8px;
}

/* 表格勾选列（QTableView::indicator）高对比 */
QTableView::indicator {
    width: 17px;
    height: 17px;
    border: 2px solid #9aa4b2;
    background-color: #ffffff;
    border-radius: 4px;
}
QTableView::indicator:hover {
    border-color: #2f6fb0;
}
QTableView::indicator:checked {
    background-color: #2f6fb0;
    border: 2px solid #1f5c96;
}

QLabel.tag-censored {
    color: #c0392b;
}
QLabel.tag-uncensored {
    color: #1e8449;
}
QLabel.tag-unknown {
    color: #b9770e;
}
"""


# ============================================================
# 主题装配
# ============================================================

def qss(theme: str = "", *, font_family: str = "", mono_family: str = "") -> str:
    """返回可直接 setStyleSheet 的 QSS。

    字体族由调用方（或 ui.fonts 探测）注入——写死 "Microsoft YaHei UI" 在
    没装该字体的机器上会被 Qt 静默回退，中文可能变成方块。
    """
    theme = theme or _current_theme
    if not font_family:
        try:
            from ui.fonts import mono_font_family, ui_font_family
            font_family = ui_font_family()
            mono_family = mono_family or mono_font_family()
        except Exception:  # noqa: BLE001 - Qt 不可用时退回通用族
            font_family = "sans-serif"
    if not mono_family:
        mono_family = "monospace"

    tmpl = _LIGHT_TEMPLATE if str(theme).lower() == "light" else _DARK_TEMPLATE
    return tmpl.replace("__FONT__", font_family).replace("__MONO__", mono_family)
