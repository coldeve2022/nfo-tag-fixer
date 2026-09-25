# -*- coding: utf-8 -*-
"""档案查询页面：破解档案库浏览 / 检索 / 导出。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget,
    QVBoxLayout, QWidget,
)

from ui.dialogs import ConfirmDialog
from ui.widgets import colored_cell, open_in_explorer

from .fixer import human_size

COLS = ["ID", "番号", "演员", "原 tag", "新 tag", "视频大小", "视频修改时间", "建档时间", "NFO 路径"]
COL_ID, COL_NUM, COL_ACTORS, COL_OLD, COL_NEW, COL_SIZE, COL_MTIME, COL_CREATED, COL_PATH = range(9)


class ArchivePage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._build_ui()
        self.refresh()
        self.state.logger.info("system", "档案页面已加载")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        top = QHBoxLayout()
        self.number_edit = QLineEdit()
        self.number_edit.setPlaceholderText("番号")
        self.actor_edit = QLineEdit()
        self.actor_edit.setPlaceholderText("演员")
        self.btn_search = QPushButton("检索")
        self.btn_search.setProperty("class", "primary")
        self.btn_reset = QPushButton("全部")
        self.btn_export = QPushButton("导出 CSV")
        self.btn_del = QPushButton("删除选中")
        top.addWidget(QLabel("番号:"))
        top.addWidget(self.number_edit)
        top.addWidget(QLabel("演员:"))
        top.addWidget(self.actor_edit)
        top.addWidget(self.btn_search)
        top.addWidget(self.btn_reset)
        top.addStretch()
        top.addWidget(self.btn_export)
        top.addWidget(self.btn_del)
        root.addLayout(top)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(COL_ACTORS, 120)
        self.table.setColumnWidth(COL_OLD, 100)
        self.table.setColumnWidth(COL_NEW, 100)
        self.table.setColumnWidth(COL_PATH, 320)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.count_label = QLabel()
        bottom.addWidget(self.count_label)
        bottom.addStretch()
        root.addLayout(bottom)

        self.btn_search.clicked.connect(self.search)
        self.btn_reset.clicked.connect(self.refresh)
        self.btn_export.clicked.connect(self.export_csv)
        self.btn_del.clicked.connect(self.delete_selected)
        self.number_edit.returnPressed.connect(self.search)
        self.actor_edit.returnPressed.connect(self.search)
        self.table.itemDoubleClicked.connect(self._open_row)

    def _records(self) -> list[dict]:
        num = self.number_edit.text().strip()
        actor = self.actor_edit.text().strip()
        if num or actor:
            return self.state.archive.search(number=num, actor=actor)
        return self.state.archive.all_records()

    def refresh(self):
        records = self._records()
        self.table.setRowCount(0)
        for r in records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, COL_ID, colored_cell(str(r["id"]), "#8a8f99"))
            self.table.setItem(row, COL_NUM, colored_cell(r["number"] or "—", "#9fd0ff"))
            self.table.setItem(row, COL_ACTORS, colored_cell(
                (r["actors"] or "—")[:40], "#d7dae0"))
            self.table.setItem(row, COL_OLD, colored_cell(
                (r["original_tag"] or "—")[:40], "#ff8a8a"))
            self.table.setItem(row, COL_NEW, colored_cell(
                (r["new_tag"] or "—")[:40], "#8ae6a1"))
            self.table.setItem(row, COL_SIZE, colored_cell(
                human_size(r["file_size"] or 0), "#b6bcc8"))
            self.table.setItem(row, COL_MTIME, colored_cell(
                (r["mtime"] or "")[:16], "#8a8f99"))
            self.table.setItem(row, COL_CREATED, colored_cell(
                (r["created_at"] or "")[:16], "#8a8f99"))
            self.table.setItem(row, COL_PATH, colored_cell(r["nfo_path"], "#d7dae0", r["nfo_path"]))
        self.count_label.setText(f"共 {len(records)} 条档案记录")
        self.state.logger.info("archive", f"档案检索：{len(records)} 条")

    def search(self):
        self.refresh()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出档案 CSV", "archive-export.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        try:
            n = self.state.archive.export_csv(path)
            self.state.logger.info("archive", f"导出 {n} 条记录到 CSV", path=path,
                                   result="成功")
            QMessageBox.information(self, "完成", f"已导出 {n} 条记录")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))

    def delete_selected(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "提示", "请先选择要删除的记录")
            return
        ids = []
        for r in rows:
            it = self.table.item(r, COL_ID)
            if it:
                ids.append(int(it.text()))
        if not ConfirmDialog.ask(self, "删除档案记录",
                                 f"确定删除 {len(ids)} 条档案记录？此操作不可撤销。",
                                 detail="\n".join(str(i) for i in ids)):
            return
        for rid in ids:
            self.state.archive.delete_record(rid)
        self.state.logger.info("archive", f"删除档案记录 {len(ids)} 条")
        self.refresh()

    def _open_row(self, item):
        row = item.row()
        it = self.table.item(row, COL_PATH)
        if it:
            p = it.data(Qt.UserRole) or it.text()
            if os.path.exists(p):
                open_in_explorer(p)
