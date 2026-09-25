# -*- coding: utf-8 -*-
"""「标签补全」管理页：审核 AI 从标题挖的稀疏标签建议。

背景：AI 补全稀疏标签（尤指 FC2 个人品）会产出成百上千条建议。原来用「确认补全」
对话框塞进滚动文本区，完全不可读、无法逐条决策。本页把建议整理成可读表格：

  - 文件维度：每行一个视频，看清 文件名/标题/原tag/AI建议tag，行内可编辑建议 tag，
    勾选决定是否写入；提供 全选/全不选/反选/只看有建议 等快捷键与按钮。
  - 标签维度：右侧汇总每个建议标签被多少视频使用，可全局勾选 采用/剔除，
    联动更新左侧文件维度的建议列表。

流入：AI 跑完后把 (candidates, proposed) 通过 load_result() 灌进来。
流出：check-rows() 拿勾选且有建议的文件 → apply_appended_tags 写入（.bak 备份）。

不碰 NFO（只读展示 + 编辑建议），写入动作由调用方（apply 按钮）触发。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
    QTableWidgetItem, QVBoxLayout, QWidget,
    QListWidget, QListWidgetItem,
)

from core.tag_enrich_model import (EnrichItem, build_enrich_items,
                                   build_tag_summary)
from ui.styles import check_bg
from ui.widgets import CheckTable, SortItem, open_in_explorer


class TagEnrichPage(QWidget):
    """标签补全审核页（文件维度 + 标签维度双视图）。"""

    # 用户点「应用」时回调：apply_requested = Signal(list) 传 (path, suggested) 列表
    apply_requested = Signal(list)

    COL_CHECK = 0
    COL_NAME = 1       # 文件名/标题
    COL_EXISTING = 2   # 原 tag
    COL_SUGGESTED = 3  # AI 建议（可编辑）
    COL_PATH = 4       # 完整路径（隐藏用，右键打开文件夹）

    HEADERS = ["✓", "文件名 / 标题", "原 tag", "AI 建议（可编辑）", "路径"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[EnrichItem] = []
        self._tag_decision: dict[str, bool] = {}  # {tag: 采用}
        self._build_ui()

    # ---------- UI ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 顶部工具行
        self._build_toolbar(root)

        # 主体：左右分栏
        splitter = QSplitter(Qt.Horizontal)

        # 左：文件维度表格
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        lv.addWidget(QLabel("文件维度：勾选要写入的视频，行内可编辑建议标签"))
        self.table = CheckTable(on_shortcut=self._on_shortcut)
        self.table.setColumnCount(len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        # 路径列默认隐藏
        self.table.setColumnHidden(self.COL_PATH, True)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_NAME, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_EXISTING, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_SUGGESTED, QHeaderView.ResizeToContents)
        # 行内可编辑：双击建议列直接编辑
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.set_menu_builder(self._build_menu)
        lv.addWidget(self.table, 1)
        splitter.addWidget(left)

        # 右：标签维度汇总
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(QLabel("标签维度：每个新标签被多少视频使用（勾选 = 采用）"))
        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QListWidget.NoSelection)
        self.tag_list.itemChanged.connect(self._on_tag_toggled)
        rv.addWidget(self.tag_list, 1)
        splitter.addWidget(right)

        # 左右分栏比例
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([640, 320])
        root.addWidget(splitter, 1)

        # 底部状态行
        self.status_label = QLabel("暂无数据")
        self.status_label.setStyleSheet("color:#9aa0ac;")
        root.addWidget(self.status_label)

    def _build_toolbar(self, root):
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.btn_all = QPushButton("全选")
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        self.btn_none = QPushButton("全不选")
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        self.btn_invert = QPushButton("反选")
        self.btn_invert.clicked.connect(lambda: self._check_invert())
        self.btn_only_suggested = QPushButton("只看有建议")
        self.btn_only_suggested.clicked.connect(self._filter_only_suggested)
        self.btn_search = QPushButton("清筛选")
        self.btn_search.clicked.connect(self._clear_filter)

        # 筛选框
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("筛选 文件名/番号/标签…")
        self.filter_edit.setFixedWidth(220)
        self.filter_edit.textChanged.connect(self._apply_filter)

        self.btn_apply = QPushButton("✅ 写入勾选")
        self.btn_apply.setProperty("class", "primary")
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_export = QPushButton("导出 CSV")
        self.btn_export.clicked.connect(self._export_csv)

        for b in (self.btn_all, self.btn_none, self.btn_invert,
                  self.btn_only_suggested, self.btn_search):
            bar.addWidget(b)
        bar.addWidget(self.filter_edit)
        bar.addStretch()
        bar.addWidget(self.btn_apply)
        bar.addWidget(self.btn_export)
        root.addLayout(bar)

    # ---------- 数据 ----------

    def load_result(self, candidates: list[dict], proposed: dict,
                    stats: dict | None = None):
        """灌入 AI 结果：合并候选元信息 + 建议，构造文件与标签两个维度。"""
        self._items = build_enrich_items(candidates, proposed)
        # 默认所有标签采用
        self._tag_decision = {t: True for t in
                              {t for it in self._items for t in it.suggested}}
        self._refill_table()
        self._refill_tags()

    def clear(self):
        self._items = []
        self._tag_decision = {}
        self.table.setRowCount(0)
        self.tag_list.clear()
        self.status_label.setText("暂无数据")

    def _refill_table(self):
        """按当前 items + tag_decision 重建文件维度表格。"""
        self.table.setSortingEnabled(False)   # 填数据时先禁排序
        self.table.blockSignals(True)         # 避免 itemChanged 逐格触发
        try:
            self.table.setRowCount(0)
            for it in self._items:
                self._append_row(it)
        finally:
            self.table.blockSignals(False)
            self.table.setSortingEnabled(True)
        self._update_status()

    def _append_row(self, it: EnrichItem):
        r = self.table.rowCount()
        self.table.insertRow(r)
        # 勾选列
        ck = QTableWidgetItem()
        ck.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        ck.setCheckState(Qt.Checked if it.suggested else Qt.Unchecked)
        self.table.setItem(r, self.COL_CHECK, ck)
        # 名称
        name_it = SortItem(it.display_name(), user_role_data=it.path)
        self.table.setItem(r, self.COL_NAME, name_it)
        # 原 tag
        ex = QTableWidgetItem("、".join(it.existing) if it.existing else "（无）")
        ex.setForeground(QColor("#9aa0ac"))
        self.table.setItem(r, self.COL_EXISTING, ex)
        # 建议 tag（可编辑），存到 UserRole 以跟踪
        sug = QTableWidgetItem("、".join(it.suggested))
        sug.setData(Qt.UserRole, list(it.suggested))   # 原始建议列表
        self.table.setItem(r, self.COL_SUGGESTED, sug)
        # 路径（隐藏）
        path_it = QTableWidgetItem(it.path)
        self.table.setItem(r, self.COL_PATH, path_it)
        # 勾选背景高亮
        self._sync_row_bg(r)

    def _refill_tags(self):
        """按标签维度重建右侧汇总列表。"""
        self.tag_list.blockSignals(True)
        try:
            self.tag_list.clear()
            summary = build_tag_summary(self._items)
            for s in summary:
                tag = s["tag"]
                it = QListWidgetItem(f"{tag}   ×{s['count']} 视频")
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setData(Qt.UserRole, tag)
                it.setCheckState(Qt.Checked if self._tag_decision.get(tag, True)
                                 else Qt.Unchecked)
                self.tag_list.addItem(it)
        finally:
            self.tag_list.blockSignals(False)

    # ---------- 勾选 & 过滤 ----------

    def _on_shortcut(self, action: str):
        if action == "all":
            self._set_all(True)
        elif action == "none":
            self._set_all(False)
        elif action == "invert":
            self._check_invert()
        elif action == "toggle":
            r = self.table.currentRow()
            if r >= 0:
                self.table.toggle_row(r)
        elif action == "toggle_selected":
            self.table.toggle_selected_rows()
        elif action == "apply":
            self._on_apply()

    def _set_all(self, checked: bool):
        for r in range(self.table.rowCount()):
            self._set_row_check(r, checked)

    def _set_row_check(self, r: int, checked: bool):
        it = self.table.item(r, self.COL_CHECK)
        if it is not None:
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self._sync_row_bg(r)

    def _check_invert(self):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, self.COL_CHECK)
            if it is not None:
                checked = it.checkState() != Qt.Checked
                it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                self._sync_row_bg(r)

    def _sync_row_bg(self, r: int):
        it = self.table.item(r, self.COL_CHECK)
        checked = it is not None and it.checkState() == Qt.Checked
        color = QColor(check_bg()) if checked else None   # None → 恢复默认背景
        for c in range(self.table.columnCount()):
            cell = self.table.item(r, c)
            if cell is not None:
                if color:
                    cell.setBackground(color)
                else:
                    cell.setData(Qt.BackgroundRole, None)

    def _filter_only_suggested(self):
        """只保留建议非空的行（其余隐藏）。"""
        self._apply_filter_to_visible(lambda it: bool(it.suggested))

    def _clear_filter(self):
        self.filter_edit.clear()
        self._show_all_rows()

    def _apply_filter(self, text: str):
        """按关键词过滤：匹配 文件名/番号/原tag/建议tag。"""
        kw = text.strip().lower()
        if not kw:
            self._show_all_rows()
            return
        for r in range(self.table.rowCount()):
            it = self._items[r] if r < len(self._items) else None
            hay = ""
            if it:
                hay = f"{it.display_name()} {it.name} {' '.join(it.existing)} {' '.join(it.suggested)}".lower()
            self.table.setRowHidden(r, kw not in hay)

    def _apply_filter_to_visible(self, pred):
        for r in range(self.table.rowCount()):
            it = self._items[r] if r < len(self._items) else None
            show = bool(it and pred(it))
            self.table.setRowHidden(r, not show)

    def _show_all_rows(self):
        for r in range(self.table.rowCount()):
            self.table.setRowHidden(r, False)

    # ---------- 编辑联动 ----------

    def _on_item_changed(self, item: QTableWidgetItem):
        """建议列被编辑后，同步到内存 items 与标签维度。"""
        if item.column() != self.COL_SUGGESTED:
            # 勾选列变化 → 更新行背景
            if item.column() == self.COL_CHECK:
                self._sync_row_bg(item.row())
            return
        # 解析逗号/顿号分隔的标签
        raw = item.text()
        tags = [t.strip() for t in raw.replace("，", ",").replace("、", ",").split(",")]
        tags = [t for t in tags if t]
        # 同步到内存 items；随后按标签维度决策剔除被全局否决的标签（保序）
        r = item.row()
        if 0 <= r < len(self._items):
            self._items[r].suggested = _apply_decision(tags, self._tag_decision)
        item.setData(Qt.UserRole, list(self._items[r].suggested))
        item.setText("、".join(self._items[r].suggested))
        self._refill_tags()
        self._update_status()

    def _on_tag_toggled(self, item: QListWidgetItem):
        """右侧标签维度勾选变化 → 更新全局决策并重刷左侧文件表。"""
        tag = item.data(Qt.UserRole)
        if not tag:
            return
        self._tag_decision[tag] = (item.checkState() == Qt.Checked)
        # 同步更新每个文件行的建议列表（剔除被否决标签）
        for it in self._items:
            before = list(it.suggested)
            if tag in before:
                it.suggested = _apply_decision(before, self._tag_decision)
        # 重刷左侧表格（含勾选状态）
        self._refill_table()
        self._update_status()

    def checked_pairs(self) -> list[tuple[str, list[str]]]:
        """返回 勾选行 && 建议非空 的 (path, suggested) 列表。"""
        out = []
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            ck = self.table.item(r, self.COL_CHECK)
            if ck is None or ck.checkState() != Qt.Checked:
                continue
            if r >= len(self._items):
                continue
            it = self._items[r]
            if it.suggested:
                out.append((it.path, list(it.suggested)))
        return out

    def _on_apply(self):
        pairs = self.checked_pairs()
        if not pairs:
            QMessageBox.information(self, "提示", "请先勾选要写入的视频（且建议非空）")
            return
        n_tags = sum(len(p[1]) for p in pairs)
        if QMessageBox.question(
                self, "确认写入",
                f"将给 <b>{len(pairs)}</b> 个视频写入共 <b>{n_tags}</b> 个标签"
                "（写入 <tag>，去重，.bak 备份）。确认？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        # 通过信号交给宿主（organizer / main_window）执行写入，避免这里 import 循环
        self.apply_requested.emit(pairs)

    def _export_csv(self):
        import csv
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", "标签补全建议.csv",
                                              "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["文件名", "标题", "原tag", "建议tag", "路径"])
                for it in self._items:
                    w.writerow([it.display_name(), it.title,
                                "、".join(it.existing), "、".join(it.suggested),
                                it.path])
            QMessageBox.information(self, "完成", f"已导出 {len(self._items)} 条到：\n{path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))

    # ---------- 右键菜单 ----------

    def _build_menu(self, menu, row, selected_rows):
        from PySide6.QtGui import QAction

        def get_path(r):
            it = self.table.item(r, self.COL_PATH)
            return it.text() if it else ""

        act_open = QAction("打开所在文件夹", self.table)
        act_open.triggered.connect(
            lambda: [open_in_explorer(get_path(r)) for r in selected_rows])
        act_copy = QAction("复制路径", self.table)
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(
                "\n".join(get_path(r) for r in selected_rows)))
        menu.addAction(act_open)
        menu.addAction(act_copy)

    # ---------- 状态 ----------

    def _update_status(self):
        n = self.table.rowCount()
        n_checked = len(self._items) if False else sum(
            1 for r in range(self.table.rowCount())
            if self.table.item(r, self.COL_CHECK) is not None
            and self.table.item(r, self.COL_CHECK).checkState() == Qt.Checked)
        n_sug = sum(1 for it in self._items if it.suggested)
        total_tags = sum(len(it.suggested) for it in self._items)
        self.status_label.setText(
            f"共 {n} 个视频 | 勾选 {n_checked} | 有建议 {n_sug} | 建议标签累计 {total_tags}")


def _apply_decision(tags, decision):
    """按标签维度全局决策（{tag: 采用}）过滤，保序去重。"""
    out, seen = [], set()
    for t in tags:
        if t and t not in seen and decision.get(t, True):
            seen.add(t)
            out.append(t)
    return out


class TagEnrichDialog(QDialog):
    """「标签补全建议审核」模态对话框：包一层 TagEnrichPage + 底部确认/取消。

    替代原来不可读的纯文本 ConfirmDialog。用户逐条核对 文件维度 + 标签维度，
    勾选决定哪些文件、哪些标签写入；点「写入」后由调用方拿 checked_pairs() 落盘。
    """

    def __init__(self, candidates: list[dict], proposed: dict,
                 stats: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 补全标签 · 建议审核")
        self.resize(1180, 720)

        v = QVBoxLayout(self)
        self.page = TagEnrichPage(self)
        self.page.load_result(candidates, proposed, stats)
        v.addWidget(self.page, 1)

        btns = QDialogButtonBox()
        b_write = btns.addButton("✍ 写入勾选", QDialogButtonBox.AcceptRole)
        b_write.setProperty("class", "primary")
        b_cancel = btns.addButton("取消", QDialogButtonBox.RejectRole)
        btns.accepted.connect(self._handle_write)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self.btn_write = b_write
        self.btn_cancel = b_cancel

    def _handle_write(self):
        """点击「写入勾选」：无可用勾选时提示；否则关闭对话框并交调用方落盘。"""
        if not self.page.checked_pairs():
            QMessageBox.information(self, "提示", "请先勾选要写入的视频（且建议非空）")
            return
        self.accept()

    def accepted_pairs(self) -> list[tuple[str, list[str]]]:
        """返回 勾选且有建议 的 (path, suggested) 列表（已按标签维度决策过滤）。"""
        return self.page.checked_pairs()
