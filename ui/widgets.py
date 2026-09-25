# -*- coding: utf-8 -*-
"""复用组件：可拖拽表格（带勾选列）/ 日志面板 / Diff 展示 / 单元格工厂。

设计要点：
- 所有表格单元格颜色都经 :func:`ui.styles.cell_color` 按当前主题映射，
  浅色主题下不会出现"近白文字写在白底上"的不可读情况；
- :class:`CheckTable` 把「移除行」做成**信号**交给页面处理，
  绝不在控件内部直接 `removeRow`——那样会让页面持有的数据列表与表格视图失步，
  下一次刷新时被删掉的行又"复活"。
"""

from __future__ import annotations

import difflib
import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QMenu,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QTextBrowser,
)

from ui.styles import cell_color, check_bg


def open_in_explorer(path: str) -> None:
    """在系统文件管理器里定位文件 / 打开文件夹（跨平台）。"""
    p = os.path.abspath(path or "")
    if not p or not os.path.exists(p):
        return
    folder = p if os.path.isdir(p) else os.path.dirname(p)
    if os.name == "nt" and os.path.isfile(p):
        # Windows 下用 explorer /select 直接选中该文件，比只打开目录更省事
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
            return
        except OSError:
            pass
    QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


class LogPanel(QPlainTextEdit):
    """底部实时日志面板。"""

    def __init__(self, parent=None, max_blocks: int = 2000):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_blocks)
        self._set_font_css()
        self._count = 0

    def _set_font_css(self) -> None:
        try:
            from ui.fonts import mono_font_family
            fam = mono_font_family()
        except Exception:  # noqa: BLE001
            fam = "monospace"
        self.document().setDefaultStyleSheet(
            f"span {{ font-family: {fam}, monospace; }}")

    def append_html(self, html: str) -> None:
        self.appendHtml(html)
        self._count += 1

    @property
    def line_count(self) -> int:
        return self._count


class DiffView(QTextBrowser):
    """改前/改后 XML diff 高亮展示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.document().setDefaultStyleSheet("""
            .add { color: #2e9e5b; }
            .del { color: #d0463b; }
            .ctx { color: #7a828e; }
            pre { font-family: Consolas, monospace; font-size: 12px; margin: 0; }
        """)

    def set_diff(self, old_xml: str, new_xml: str) -> None:
        """用 unified diff 方式展示两段 XML 的差异。"""
        old_lines = old_xml.splitlines()
        new_lines = new_xml.splitlines()
        sm = difflib.SequenceMatcher(None, old_lines, new_lines)
        html = ["<pre>"]
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    html.append(f'<span class="ctx">{_esc(line)}</span>\n')
            elif tag == "replace":
                for line in old_lines[i1:i2]:
                    html.append(f'<span class="del">- {_esc(line)}</span>\n')
                for line in new_lines[j1:j2]:
                    html.append(f'<span class="add">+ {_esc(line)}</span>\n')
            elif tag == "delete":
                for line in old_lines[i1:i2]:
                    html.append(f'<span class="del">- {_esc(line)}</span>\n')
            elif tag == "insert":
                for line in new_lines[j1:j2]:
                    html.append(f'<span class="add">+ {_esc(line)}</span>\n')
        html.append("</pre>")
        self.setHtml("".join(html))

    def set_xml(self, xml: str) -> None:
        self.setHtml(f"<pre class='ctx'>{_esc(xml)}</pre>")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class CheckTable(QTableWidget):
    """带勾选列快捷键的通用表格（支持拖拽导入）。

    快捷键：
      Ctrl+A              全部勾选
      Ctrl+U              全部取消
      Ctrl+I              反向
      空格                切换当前行
      Ctrl+空格/Shift+空格 切换所有选中行（挑着选）
      Ctrl+Enter          提交（回调 on_shortcut("apply")）
      Delete              请求移除选中行（发 remove_rows_requested）
      Ctrl+Z              请求撤销（发 undo_requested）

    on_shortcut(action) 回调：all / none / invert / toggle / toggle_selected / apply
    """

    files_dropped = Signal(list)          # 拖入路径列表（支持拖拽导入）
    remove_rows_requested = Signal(list)  # Delete/右键"移除"：要移除的行号列表
    undo_requested = Signal()             # Ctrl+Z：撤销移除

    def __init__(self, parent=None, on_shortcut=None):
        super().__init__(parent)
        self._on_shortcut = on_shortcut
        self._menu_builder = None
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def set_menu_builder(self, fn):
        """设置右键菜单扩展：fn(menu, row, selected_rows)。"""
        self._menu_builder = fn

    # ---------- 右键菜单（勾选操作） ----------

    def _show_context_menu(self, pos):
        row = self.rowAt(pos.y())
        selected = sorted({i.row() for i in self.selectedItems()})
        if row >= 0 and row not in selected:
            selected = [row]
        menu = QMenu(self)

        # 勾选操作（用户核心需求：选中行批量勾选）
        act_check_sel = QAction("☑ 勾选选中行", self)
        act_uncheck_sel = QAction("☐ 取消勾选选中行", self)
        act_check_sel.setEnabled(bool(selected))
        act_uncheck_sel.setEnabled(bool(selected))
        act_check_sel.triggered.connect(
            lambda: self._emit_shortcut("check_selected"))
        act_uncheck_sel.triggered.connect(
            lambda: self._emit_shortcut("uncheck_selected"))
        menu.addAction(act_check_sel)
        menu.addAction(act_uncheck_sel)
        menu.addSeparator()

        act_all = QAction("全选勾选 (Ctrl+A)", self)
        act_none = QAction("全不选 (Ctrl+U)", self)
        act_invert = QAction("反选 (Ctrl+I)", self)
        act_all.triggered.connect(lambda: self._emit_shortcut("all"))
        act_none.triggered.connect(lambda: self._emit_shortcut("none"))
        act_invert.triggered.connect(lambda: self._emit_shortcut("invert"))
        menu.addAction(act_all)
        menu.addAction(act_none)
        menu.addAction(act_invert)

        # 页面扩展项（打开文件夹 / 复制路径 / 移除等）
        if self._menu_builder is not None:
            menu.addSeparator()
            self._menu_builder(menu, row, selected)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _emit_shortcut(self, action: str):
        if self._on_shortcut:
            self._on_shortcut(action)

    def _emit_remove(self) -> None:
        rows = sorted({i.row() for i in self.selectedItems()})
        if not rows and self.currentRow() >= 0:
            rows = [self.currentRow()]
        if rows:
            self.remove_rows_requested.emit(rows)

    # ---------- 拖拽 ----------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()
                     if u.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ---------- 快捷键 ----------

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.SelectAll):       # Ctrl+A
            self._emit_shortcut("all")
            return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_U:
            self._emit_shortcut("none")
            return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_I:
            self._emit_shortcut("invert")
            return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_Return:
            self._emit_shortcut("apply")
            return
        if event.matches(QKeySequence.Undo):            # Ctrl+Z
            self.undo_requested.emit()
            return
        if event.key() == Qt.Key_Space:
            if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
                self._emit_shortcut("toggle_selected")
            else:
                self._emit_shortcut("toggle")
            return
        if event.key() == Qt.Key_Delete:
            self._emit_remove()
            return
        super().keyPressEvent(event)

    # ---------- 勾选操作 ----------

    def checked_rows(self) -> list[int]:
        """返回勾选行的索引列表。"""
        return [r for r in range(self.rowCount())
                if self.item(r, 0) is not None
                and self.item(r, 0).checkState() == Qt.Checked]

    def checked_count(self) -> int:
        return len(self.checked_rows())

    def set_row_checked(self, row: int, checked: bool):
        """设置某行勾选状态，并同步高亮背景。"""
        it = self.item(row, 0)
        if it is not None:
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            it.setBackground(QColor(check_bg()) if checked else QBrush())

    def set_all_checked(self, checked: bool):
        """批量设置全部勾选（屏蔽信号，避免逐个触发回调）。"""
        self.blockSignals(True)
        try:
            for r in range(self.rowCount()):
                self.set_row_checked(r, checked)
        finally:
            self.blockSignals(False)

    def check_invert(self):
        """批量反选（屏蔽信号）。"""
        self.blockSignals(True)
        try:
            for r in range(self.rowCount()):
                self.toggle_row(r)
        finally:
            self.blockSignals(False)

    def toggle_row(self, row: int):
        it = self.item(row, 0)
        if it is not None:
            checked = it.checkState() != Qt.Checked
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            it.setBackground(QColor(check_bg()) if checked else QBrush())

    def toggle_selected_rows(self):
        rows = sorted({i.row() for i in self.selectedItems()})
        if not rows:
            r = self.currentRow()
            if r >= 0:
                rows = [r]
        self.blockSignals(True)
        try:
            for r in rows:
                self.toggle_row(r)
        finally:
            self.blockSignals(False)


def colored_cell(text: str, color: str = "", user_role_data: str = "") -> QTableWidgetItem:
    """带前景色的表格项（颜色按当前主题自动映射）。"""
    it = QTableWidgetItem(text)
    if color:
        it.setForeground(QColor(cell_color(color)))
    if user_role_data:
        it.setData(Qt.UserRole, user_role_data)
    return it


class SortItem(QTableWidgetItem):
    """按数值排序的表格项：传入 sort_value 后点击列头按数值而非字符串排序。

    适用于番号（数字前缀）、视频大小、修改时间、得分等列。
    颜色同样经主题映射，浅色主题下不会出现白底白字。
    """

    def __init__(self, text: str, color: str = "", sort_value=None,
                 user_role_data: str = ""):
        super().__init__(text)
        if color:
            self.setForeground(QColor(cell_color(color)))
        if user_role_data:
            self.setData(Qt.UserRole, user_role_data)
        self._sort_value = sort_value if sort_value is not None else text

    def __lt__(self, other):
        if not isinstance(other, QTableWidgetItem):
            return super().__lt__(other)
        sv = getattr(other, "_sort_value", other.text())
        try:
            return float(self._sort_value) < float(sv)
        except (TypeError, ValueError):
            return str(self._sort_value) < str(sv)


__all__ = ["open_in_explorer", "LogPanel", "DiffView", "CheckTable",
           "colored_cell", "SortItem"]
