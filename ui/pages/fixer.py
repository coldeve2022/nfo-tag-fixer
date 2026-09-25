# -*- coding: utf-8 -*-
"""标签修正页：导入 → 勾选 → 统计 → 预览 → 应用 → 回滚。

核心交互（所见即所选）：
- 文件列表第一列为「✓ 勾选列」，**所有操作只作用于勾选的行**
- 导入后默认全部勾选，取消勾选即可排除某个文件
- 支持批量勾选：Ctrl+A 全选 / Ctrl+U 全不选 / Ctrl+I 反选 / 空格切换当前行 /
  Ctrl+空格 切换所有选中行 / ✅ 一键勾选有码
- 按钮实时显示将处理的数量：如「② 预览变更(23)」
"""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QTabWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core.mapper import Change, Mapper, MappingRule
from core.scanner import scan_paths
from core.tag_analyzer import classify_tag, count_markers
from ui.dialogs import (ConfirmDialog, RuleEditDialog, RuleSuggestionDialog,
                        TagAnalyzeDialog)
from ui.styles import check_bg
from ui.widgets import CheckTable, DiffView, SortItem, colored_cell, open_in_explorer
from ui.worker import run_with_progress

# 表格列
COL_CHECK, COL_STATUS, COL_NAME, COL_NUMBER, COL_ACTORS, COL_CENSORED, \
    COL_UNCENSORED, COL_OTHER, COL_SIZE, COL_MTIME = range(10)

STATUS_TEXT = {0: "有码", 1: "无码", 2: "待确认", 3: "无tag", 4: "异常",
               5: "冲突"}
STATUS_COLOR = {0: "#ff8a8a", 1: "#8ae6a1", 2: "#ffd77a", 3: "#9aa0ac",
                4: "#ff6b6b", 5: "#e056fd"}


def human_size(n: int) -> str:
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def human_time(ts: float) -> str:
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


class PreviewDialog(QDialog):
    """改前预览：变更列表 + diff 高亮。"""

    def __init__(self, changes: list[Change], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"预览变更（{len(changes)} 处）")
        self.resize(980, 620)
        self.changes = changes

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(f"共 <b>{len(changes)}</b> 处变更，请确认后应用："))
        top.addStretch()
        btn_apply = QPushButton("应用修改")
        btn_apply.setProperty("class", "primary")
        btn_apply.clicked.connect(self.accept)
        top.addWidget(btn_apply)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._show_diff)
        for i, c in enumerate(changes):
            op = c.rule.describe()
            it = QListWidgetItem(f"[{i+1}] {os.path.basename(c.path)}\n    {op}")
            it.setToolTip(c.path)
            self.list_widget.addItem(it)
        self.diff_view = DiffView()
        splitter.addWidget(self.list_widget)
        splitter.addWidget(self.diff_view)
        splitter.setSizes([340, 640])
        layout.addWidget(splitter, 1)
        if changes:
            self.list_widget.setCurrentRow(0)

    def _show_diff(self, row: int):
        if 0 <= row < len(self.changes):
            c = self.changes[row]
            self.diff_view.set_diff(c.old_xml, c.new_xml)


class FixerPage(QWidget):
    """标签修正页。"""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.items: list = []
        self._changes: list[Change] = []
        self._removed_stack: list = []
        self._checked_paths: set[str] = set()   # 勾选路径（刷新后恢复）
        # 索引/缓存：把"按行找对象"从 O(n) 线性扫描降为 O(1) 查表。
        # 上万 NFO 时原先的 _item_at_row 会让筛选/刷新变成 O(n²)（数千行卡好几秒）。
        self._by_path: dict[str, object] = {}
        self._groups_cache: tuple | None = None
        self._class_cache: dict[str, tuple] = {}
        self._build_ui()
        self._connect_signals()
        self.state.logger.info("system", "主页面已加载")

    def _invalidate(self) -> None:
        """数据或分组变化后重建索引与缓存。"""
        self._by_path = {it.nfo.path: it for it in self.items}
        self._groups_cache = None
        self._class_cache.clear()

    # ---------- UI ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # 顶部工具行
        toolbar = QHBoxLayout()
        self.btn_analyze = QPushButton("① 标签统计")
        self.btn_analyze.setProperty("class", "primary")
        self.btn_suggest = QPushButton("🤖 智能规则")
        self.btn_suggest.setToolTip("根据当前文件分析有码/无码规律，自动生成映射规则供勾选")
        self.btn_preview = QPushButton("② 预览变更")
        self.btn_apply = QPushButton("③ 应用修改")
        self.btn_rollback = QPushButton("回滚勾选")
        self.btn_import_dir = QPushButton("导入文件夹")
        self.btn_clear = QPushButton("清空列表")
        toolbar.addWidget(self.btn_analyze)
        toolbar.addWidget(self.btn_suggest)
        toolbar.addWidget(self.btn_preview)
        toolbar.addWidget(self.btn_apply)
        toolbar.addWidget(self.btn_rollback)
        toolbar.addSpacing(10)
        toolbar.addWidget(self.btn_import_dir)
        toolbar.addWidget(self.btn_clear)
        toolbar.addStretch()

        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部", -1)
        self.filter_combo.addItem("🔴 只看有码", 0)
        self.filter_combo.addItem("🟢 只看无码", 1)
        self.filter_combo.addItem("🔀 冲突(有码+无码)", 5)
        self.filter_combo.addItem("🟡 待确认", 2)
        self.filter_combo.addItem("无 tag", 3)
        self.filter_combo.setFixedWidth(160)
        toolbar.addWidget(self.filter_combo)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("过滤：番号/演员/文件名…")
        self.filter_edit.setFixedWidth(200)
        toolbar.addWidget(self.filter_edit)
        root.addLayout(toolbar)

        # 使用流程提示
        tip = QLabel(
            "流程：① 导入/拖入文件（默认全部勾选）→ 取消勾选可排除个别文件 → "
            "② 点「标签统计」确认有码/无码分组（自动生成规则）→ "
            "③ 「预览变更」查看 → ④ 「应用修改」。\n"
            "所有操作只作用于 <b>✓ 勾选</b> 的行。批量勾选：Ctrl+A 全选 / Ctrl+U 全不选 / "
            "Ctrl+I 反选 / 空格 切换当前行 / Ctrl+空格 切换选中行 / ✅ 一键勾选有码。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa0ac; padding:4px 2px;")
        root.addWidget(tip)

        # 三栏主体
        splitter = QSplitter(Qt.Horizontal)

        # 左：文件表格
        left_box = QVBoxLayout()
        self.table = CheckTable(self, on_shortcut=self._on_shortcut)
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels(
            ["✓", "状态", "文件名", "番号", "演员", "有码tag", "无码tag",
             "其他tag", "视频大小", "修改时间"])
        header = self.table.horizontalHeader()
        for col, w in ((COL_CHECK, 40), (COL_STATUS, 52), (COL_NAME, 170),
                       (COL_NUMBER, 85), (COL_ACTORS, 100), (COL_CENSORED, 100),
                       (COL_UNCENSORED, 110), (COL_OTHER, 130), (COL_SIZE, 75),
                       (COL_MTIME, 120)):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.set_menu_builder(self._table_menu_builder)

        # 勾选工具行
        batch = QHBoxLayout()
        self.btn_check_sel = QPushButton("☑ 勾选选中行")
        self.btn_check_sel.setToolTip("把当前用鼠标选中的行全部勾选（按住 Ctrl/Shift 可多选）")
        self.btn_uncheck_sel = QPushButton("☐ 取消勾选选中行")
        self.btn_check_censored = QPushButton("✅ 勾选有码")
        self.btn_check_censored.setToolTip("一键勾选所有「有码tag」列非空的行（待修文件）")
        self.btn_ai_recheck = QPushButton("🤖 AI 复核有码/无码")
        self.btn_ai_recheck.setToolTip(
            "用本地 qwen3:8b 复核「待确认」标记：把关键词表判不出的词条交给 AI 判定有码/无码/其它，"
            "AI 发现的码类写法会加入分组并可一键采纳（只读建议，不自动改文件）")
        self.btn_check_all = QPushButton("全选")
        self.btn_check_none = QPushButton("全不选")
        self.btn_check_invert = QPushButton("反选")
        batch.addWidget(self.btn_check_sel)
        batch.addWidget(self.btn_uncheck_sel)
        batch.addWidget(self.btn_check_censored)
        batch.addWidget(self.btn_ai_recheck)
        batch.addWidget(self.btn_check_all)
        batch.addWidget(self.btn_check_none)
        batch.addWidget(self.btn_check_invert)
        batch.addStretch()
        self._sel_label = QLabel("已勾选 0 行")
        batch.addWidget(self._sel_label)
        left_box.addWidget(self.table, 1)
        left_box.addLayout(batch)
        self.count_label = QLabel("0 个文件")
        left_box.addWidget(self.count_label)
        left_widget = QWidget()
        left_widget.setLayout(left_box)
        splitter.addWidget(left_widget)

        # 中：详情 / diff
        self.detail_tab = QTabWidget()
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.diff_view = DiffView()
        self.detail_tab.addTab(self.detail_text, "NFO 详情")
        self.detail_tab.addTab(self.diff_view, "变更 Diff")
        splitter.addWidget(self.detail_tab)

        # 右：规则面板
        splitter.addWidget(self._build_rule_panel())

        splitter.setSizes([470, 400, 300])
        root.addWidget(splitter, 1)

        self._update_buttons()

    def _build_rule_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        gb = QGroupBox("标签映射规则")
        v = QVBoxLayout(gb)

        self.rule_list = QListWidget()
        self._reload_rules()
        v.addWidget(self.rule_list, 1)

        btns = QHBoxLayout()
        self.btn_rule_add = QPushButton("+ 新增")
        self.btn_rule_edit = QPushButton("编辑")
        self.btn_rule_del = QPushButton("删除")
        self.btn_rule_import = QPushButton("导入")
        self.btn_rule_export = QPushButton("导出")
        btns.addWidget(self.btn_rule_add)
        btns.addWidget(self.btn_rule_edit)
        btns.addWidget(self.btn_rule_del)
        btns.addWidget(self.btn_rule_import)
        btns.addWidget(self.btn_rule_export)
        v.addLayout(btns)

        self.backup_check = QCheckBox("改前备份 (.bak)")
        self.backup_check.setChecked(self.state.config.settings.backup_enabled)
        v.addWidget(self.backup_check)

        self.target_tag_edit = QLineEdit()
        self.target_tag_edit.setPlaceholderText("目标无码 tag（如：无码破解）")
        self.target_tag_edit.setText(self.state.config.settings.target_tag)
        v.addWidget(QLabel("目标 tag："))
        v.addWidget(self.target_tag_edit)

        layout.addWidget(gb)
        return panel

    def _connect_signals(self):
        self.table.files_dropped.connect(self.import_paths)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.remove_rows_requested.connect(self.remove_rows)
        self.table.undo_requested.connect(self.undo_remove)
        self.btn_analyze.clicked.connect(self.analyze_tags)
        self.btn_suggest.clicked.connect(self.suggest_rules)
        self.btn_preview.clicked.connect(self.preview_changes)
        self.btn_apply.clicked.connect(self.apply_changes)
        self.btn_rollback.clicked.connect(self.rollback_checked)
        self.btn_import_dir.clicked.connect(self._import_dir_dialog)
        self.btn_clear.clicked.connect(self.clear_items)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.btn_rule_add.clicked.connect(self._rule_add)
        self.btn_rule_edit.clicked.connect(self._rule_edit)
        self.btn_rule_del.clicked.connect(self._rule_del)
        self.btn_rule_import.clicked.connect(self._rule_import)
        self.btn_rule_export.clicked.connect(self._rule_export)
        self.backup_check.toggled.connect(self._on_backup_toggled)
        self.target_tag_edit.editingFinished.connect(self._on_target_tag_changed)
        self.btn_check_censored.clicked.connect(self.check_censored)
        self.btn_ai_recheck.clicked.connect(self.ai_recheck_censorship)
        self.btn_check_all.clicked.connect(self.check_all)
        self.btn_check_none.clicked.connect(self.check_none)
        self.btn_check_invert.clicked.connect(self.check_invert)
        self.btn_check_sel.clicked.connect(self.check_selected)
        self.btn_uncheck_sel.clicked.connect(self.uncheck_selected)

    # ---------- 勾选管理 ----------

    def check_all(self):
        self.table.set_all_checked(True)
        self._rebuild_checked_paths()

    def check_none(self):
        self.table.set_all_checked(False)
        self._rebuild_checked_paths()

    def check_invert(self):
        self.table.check_invert()
        self._rebuild_checked_paths()

    def _rebuild_checked_paths(self):
        """批量勾选后重建路径集合并刷新 UI（信号被屏蔽，需手动同步）。"""
        self._checked_paths = {
            self.table.item(r, COL_NAME).data(Qt.UserRole)
            for r in self.table.checked_rows()
            if self.table.item(r, COL_NAME) is not None}
        self._sync_ui_state()

    def check_censored(self):
        """一键勾选所有「有码tag」列非空的行（待修文件）。"""
        n = 0
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_CENSORED)
            if it is not None and it.text().strip():
                self.table.set_row_checked(r, True)
                n += 1
        self._rebuild_checked_paths()
        self.state.logger.info("import", f"一键勾选有码：{n} 行")

    def ai_recheck_censorship(self):
        """用本地 qwen3:8b 复核「待确认」标记，AI 发现的码类写法可一键加入分组。"""
        from PySide6.QtWidgets import QProgressDialog
        from core.tag_organizer import ai_reclassify_censorship, \
            ollama_available, ollama_has_model
        if not self.items:
            QMessageBox.information(self, "提示", "请先导入文件")
            return
        if not ollama_available():
            QMessageBox.warning(self, "Ollama 不可用",
                                "未检测到 Ollama 服务。\n请先启动 Ollama 后重试。")
            return
        if not ollama_has_model():
            QMessageBox.warning(
                self, "缺少模型",
                "未找到 qwen3:8b。\n请先运行：ollama pull qwen3:8b")
            return
        c, u = self._groups()
        # 收集关键词表判不出的去重标记
        candidates = []
        seen = set()
        for it in self.items:
            if not it.nfo.is_valid:
                continue
            for t in it.nfo.markers:
                if t in c or t in u or t in seen:
                    continue
                seen.add(t)
                candidates.append(t)
        if not candidates:
            QMessageBox.information(self, "提示", "没有需要 AI 复核的标记（关键词表已全部判定）")
            return
        if not ConfirmDialog.ask(
                self, "AI 复核有码/无码",
                f"将对 <b>{len(candidates)}</b> 个「待确认」标记用本地 qwen3:8b 复核"
                "（判定为 有码/无码/其他）。\n"
                "只读建议，不自动改文件；复核出的码类写法可再一键采纳。确认开始？"):
            return
        prog = QProgressDialog("AI 复核中…", "取消", 0, len(candidates), self)
        prog.setWindowTitle("AI 复核有码/无码")
        prog.setWindowModality(Qt.WindowModal)

        def on_progress(done, total):
            prog.setValue(done)
            prog.setLabelText(f"[{done}/{total}]")
            QApplication.processEvents()

        try:
            result = ai_reclassify_censorship(candidates, on_progress=on_progress)
        finally:
            prog.close()
        new_c = sorted({m for m, v in result.items() if v == "censored" and m not in c})
        new_u = sorted({m for m, v in result.items() if v == "uncensored" and m not in u})
        if not new_c and not new_u:
            QMessageBox.information(
                self, "AI 复核完成",
                f"复核了 {len(candidates)} 个标记，AI 判定均为「其他」内容标签，未发现新的有码/无码写法。")
            return
        detail = []
        for m in new_c:
            detail.append(f"🔴 有码: {m}")
        for m in new_u:
            detail.append(f"🟢 无码: {m}")
        if not ConfirmDialog.ask(
                self, "AI 复核结果",
                f"AI 识别出 <b>{len(new_c)}</b> 个有码、<b>{len(new_u)}</b> 个无码写法"
                "（关键词表漏判的）。\n加入分组后对应文件会重新归类，可再用「勾选有码」处理。"
                "是否采纳？",
                detail="\n".join(detail)):
            return
        s = self.state.config.settings
        merged = False
        for m in new_c:
            if m not in s.censored_tags:
                s.censored_tags.append(m)
                merged = True
        for m in new_u:
            if m not in s.uncensored_tags:
                s.uncensored_tags.append(m)
                merged = True
        if merged:
            self.state.config.save_settings()
        self._refresh_table()
        self.state.logger.info(
            "import", f"AI 复核有码/无码：新增 有码 {len(new_c)} / 无码 {len(new_u)} 个写法")
        QMessageBox.information(
            self, "已采纳",
            f"已把 {len(new_c)} 个有码 + {len(new_u)} 个无码写法加入分组，"
            "对应文件已重新归类（见「待确认」以外的状态列）。")


    def _on_shortcut(self, action: str):
        if action == "all":
            self.check_all()
        elif action == "none":
            self.check_none()
        elif action == "invert":
            self.check_invert()
        elif action == "toggle":
            self.table.toggle_row(self.table.currentRow())
        elif action == "toggle_selected":
            self.table.toggle_selected_rows()
        elif action == "check_selected":
            # 勾选当前鼠标选中的行（右键菜单/按钮）
            rows = sorted({i.row() for i in self.table.selectedItems()})
            if not rows and self.table.currentRow() >= 0:
                rows = [self.table.currentRow()]
            for r in rows:
                self.table.set_row_checked(r, True)
            self._rebuild_checked_paths()
        elif action == "uncheck_selected":
            rows = sorted({i.row() for i in self.table.selectedItems()})
            if not rows and self.table.currentRow() >= 0:
                rows = [self.table.currentRow()]
            for r in rows:
                self.table.set_row_checked(r, False)
            self._rebuild_checked_paths()
        elif action == "apply":
            self.apply_changes()

    def check_selected(self):
        """工具栏按钮：勾选当前选中的行。"""
        self._on_shortcut("check_selected")

    def uncheck_selected(self):
        """工具栏按钮：取消勾选当前选中的行。"""
        self._on_shortcut("uncheck_selected")

    # ---------- 移除（列表层面，不动磁盘） ----------

    def remove_rows(self, rows: list[int]) -> None:
        """从列表移除指定行（可 Ctrl+Z 撤销）。仅在列表层面，不动磁盘。"""
        removed = []
        for r in sorted(set(rows), reverse=True):
            it = self._item_at_row(r)
            if it is not None and it in self.items:
                removed.append(it)
                self.items.remove(it)
                self._checked_paths.discard(it.nfo.path)
        if not removed:
            return
        self._removed_stack.append(removed)
        self._refresh_table()
        self.state.logger.info("import", f"从列表移除 {len(removed)} 个（Ctrl+Z 可撤销）")

    def _remove_selected(self):
        """兼容入口：移除当前选中的行。"""
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        self.remove_rows(rows)

    def undo_remove(self):
        if not self._removed_stack:
            self.state.logger.info("import", "没有可撤销的移除操作")
            return
        removed = self._removed_stack.pop()
        self.items.extend(removed)
        for it in removed:
            self._checked_paths.add(it.nfo.path)
        self._refresh_table()
        self.state.logger.info("import", f"已撤销移除 {len(removed)} 个")

    def _table_menu_builder(self, menu, row: int, selected: list[int]):
        """右键菜单扩展：打开文件夹 / 复制路径 / 从列表移除。"""
        from PySide6.QtGui import QAction

        def paths():
            return [self._item_at_row(r) for r in selected]

        act_open = QAction("打开所在文件夹", self)
        act_copy = QAction("复制路径", self)
        act_remove = QAction("从列表移除 (Delete)", self)
        act_open.triggered.connect(
            lambda: [open_in_explorer(it.nfo.path) for it in paths() if it])
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(
                "\n".join(it.nfo.path for it in paths() if it)))
        act_remove.triggered.connect(lambda: self.remove_rows(selected))
        act_open.setEnabled(bool(selected))
        act_copy.setEnabled(bool(selected))
        act_remove.setEnabled(bool(selected))
        menu.addAction(act_open)
        menu.addAction(act_copy)
        menu.addAction(act_remove)

    def _checked_items(self) -> list:
        """当前勾选的行对应的 ScanItem 列表（排序无关，按路径定位）。"""
        paths = {self.table.item(r, COL_NAME).data(Qt.UserRole)
                 for r in self.table.checked_rows()
                 if self.table.item(r, COL_NAME) is not None}
        return [it for it in self.items if it.nfo.path in paths]

    # ---------- 导入 ----------

    def import_paths(self, paths: list[str]):
        loaded = {it.nfo.path for it in self.items}
        recursive = self.state.config.settings.recursive_scan
        new_items = scan_paths(paths, recursive=recursive, already_loaded=loaded)
        if not new_items:
            self.state.logger.warn("import", "没有发现新的 NFO 文件", result="0 个")
            return
        self.items.extend(new_items)
        # 新导入默认勾选
        for it in new_items:
            self._checked_paths.add(it.nfo.path)
        self._refresh_table()
        ok = sum(1 for it in new_items if it.nfo.is_valid)
        self.state.logger.info(
            "import", f"导入 {len(new_items)} 个 NFO（默认勾选）",
            result=f"成功 {ok}，失败 {len(new_items)-ok}",
            path=", ".join(p[:60] for p in paths[:3]))

    def _import_dir_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹（嵌套扫描 NFO）")
        if d:
            self.import_paths([d])

    def clear_items(self):
        if self.items and not ConfirmDialog.ask(
                self, "清空列表", f"确定清空已导入的 {len(self.items)} 个文件？仅移除列表，不影响磁盘。",
                default_no=False):
            return
        self.items = []
        self._changes = []
        self._checked_paths = set()
        self._removed_stack = []
        self._refresh_table()
        self.state.logger.info("import", "已清空导入列表")

    # ---------- 分组逻辑 ----------

    def _groups(self):
        """返回 (censored_set, uncensored_set)：用户确认分组 + 关键词分类（tag+genre）。

        结果缓存——原先每次刷新/筛选都重扫全部 NFO 的全部标记，
        在「应用修改」里更是每个文件调一次，等于 O(文件数²)。
        """
        if self._groups_cache is None:
            c = set(self.state.config.settings.censored_tags)
            u = set(self.state.config.settings.uncensored_tags)
            for it in self.items:
                if it.nfo.is_valid:
                    for t in it.nfo.markers:
                        cls = classify_tag(t)
                        if cls == "censored":
                            c.add(t)
                        elif cls == "uncensored":
                            u.add(t)
            self._groups_cache = (c, u)
        return self._groups_cache

    def _classify_item(self, it, c: set | None = None, u: set | None = None
                       ) -> tuple[int, list, list, list]:
        """返回 (status, 有码标记, 无码标记, 其他标记)。基于 tag+genre，结果按路径缓存。"""
        key = it.nfo.path
        hit = self._class_cache.get(key)
        if hit is not None:
            return hit
        if c is None or u is None:
            c, u = self._groups()
        nfo = it.nfo
        if not nfo.is_valid:
            result = (4, [], [], [])
        else:
            markers = nfo.markers
            if not markers:
                result = (3, [], [], [])
            else:
                c_tags = [t for t in markers if t in c]
                u_tags = [t for t in markers if t in u]
                o_tags = [t for t in markers if t not in c and t not in u]
                if c_tags and u_tags:
                    status = 5
                elif c_tags:
                    status = 0
                elif u_tags:
                    status = 1
                else:
                    status = 2
                result = (status, c_tags, u_tags, o_tags)
        self._class_cache[key] = result
        return result

    def _item_at_row(self, row: int):
        """按行取 ScanItem（排序后行序与 self.items 不一致，用路径查索引表）。"""
        if row < 0 or row >= self.table.rowCount():
            return None
        cell = self.table.item(row, COL_NAME)
        if cell is None:
            return None
        return self._by_path.get(cell.data(Qt.UserRole))

    # ---------- 表格 ----------

    def _refresh_table(self):
        # 表格视图与数据列表的唯一同步点：重建索引/缓存后一次性重绘
        self._invalidate()
        c, u = self._groups()
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for it in self.items:
                status, c_tags, u_tags, o_tags = self._classify_item(it, c, u)
                row = self.table.rowCount()
                self.table.insertRow(row)

                # 勾选列（恢复之前勾选状态；新文件默认勾选）
                ck = QTableWidgetItem()
                ck.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                ck.setCheckState(Qt.Checked if it.nfo.path in self._checked_paths
                                 else Qt.Unchecked)
                self.table.setItem(row, COL_CHECK, ck)

                st = colored_cell(STATUS_TEXT[status], STATUS_COLOR[status])
                st.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, COL_STATUS, st)

                self.table.setItem(row, COL_NAME,
                                   colored_cell(it.nfo.basename, "#d7dae0", it.nfo.path))
                self.table.setItem(row, COL_NUMBER,
                                   SortItem(it.nfo.number, "#9fd0ff",
                                            sort_value=it.nfo.number))
                self.table.setItem(row, COL_ACTORS,
                                   colored_cell(", ".join(it.nfo.actors)[:40], "#d7dae0"))
                self.table.setItem(row, COL_CENSORED,
                                   colored_cell(", ".join(c_tags)[:60], "#ff6b6b"))
                self.table.setItem(row, COL_UNCENSORED,
                                   colored_cell(", ".join(u_tags)[:60], "#8ae6a1"))
                self.table.setItem(row, COL_OTHER,
                                   colored_cell(", ".join(o_tags)[:80], "#9aa0ac"))
                self.table.setItem(row, COL_SIZE,
                                   SortItem(human_size(it.video_size), "#b6bcc8",
                                            sort_value=it.video_size))
                self.table.setItem(row, COL_MTIME,
                                   SortItem(human_time(it.video_mtime), "#8a8f99",
                                            sort_value=it.video_mtime))
        finally:
            # 统一应用勾选背景色（仍在信号屏蔽内，避免批量触发 itemChanged）
            for r in self.table.checked_rows():
                it = self.table.item(r, COL_CHECK)
                if it is not None:
                    it.setBackground(QColor(check_bg()))
            self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        total = len(self.items)
        valid = sum(1 for it in self.items if it.nfo.is_valid)
        n_c = sum(1 for it in self.items if self._classify_item(it, c, u)[0] == 0)
        n_x = sum(1 for it in self.items if self._classify_item(it, c, u)[0] == 5)
        extra = f" | 🔀冲突 {n_x}" if n_x else ""
        self.count_label.setText(
            f"{total} 个文件 | 有效 {valid} | 🔴有码 {n_c}{extra}")
        self._update_buttons()
        self._sync_ui_state()
        self._on_select(self.table.currentRow())
        self._apply_filter()

    def _on_item_changed(self, item):
        """勾选变化：同步路径集合、蓝色高亮、计数、按钮文字。"""
        if item.column() != COL_CHECK:
            return
        checked = item.checkState() == Qt.Checked
        path_item = self.table.item(item.row(), COL_NAME)
        path = path_item.data(Qt.UserRole) if path_item else None
        if checked:
            item.setBackground(QColor(check_bg()))
            if path:
                self._checked_paths.add(path)
        else:
            item.setData(Qt.BackgroundRole, None)
            if path:
                self._checked_paths.discard(path)
        self._sync_ui_state()

    def _sync_ui_state(self):
        """更新已勾选计数与按钮数量提示。"""
        n = self.table.checked_count()
        self._sel_label.setText(f"已勾选 {n} 行")
        self.btn_preview.setText(f"② 预览变更({n})" if n else "② 预览变更")
        self.btn_apply.setText(f"③ 应用修改({n})" if n else "③ 应用修改")

    def _update_buttons(self):
        has = len(self.items) > 0
        self.btn_analyze.setEnabled(has)
        self.btn_suggest.setEnabled(has)
        self.btn_preview.setEnabled(has)
        self.btn_apply.setEnabled(has)
        self.btn_rollback.setEnabled(has)

    def _apply_filter(self):
        text = self.filter_edit.text().strip().lower()
        status = self.filter_combo.currentData()
        c, u = self._groups()
        for r in range(self.table.rowCount()):
            hidden = False
            if status is not None and status >= 0:
                it = self._item_at_row(r)
                if it is not None:
                    st = self._classify_item(it, c, u)[0]
                    match = (st == status) or (status == 0 and st == 5)
                    hidden = not match
                else:
                    hidden = True
            if not hidden and text:
                cells = [self.table.item(r, col).text() for col in
                         (COL_NAME, COL_NUMBER, COL_ACTORS, COL_CENSORED,
                          COL_UNCENSORED, COL_OTHER)]
                hidden = not any(text in cell.lower() for cell in cells)
            self.table.setRowHidden(r, hidden)

    def focus_filter_input(self):
        self.filter_edit.setFocus()

    # ---------- 详情 ----------

    def _on_select(self, row: int):
        it = self._item_at_row(row)
        if it is not None:
            nfo = it.nfo
            lines = [
                f"<b>文件：</b>{nfo.path}",
                f"<b>番号：</b>{nfo.number or '—'}",
                f"<b>标题：</b>{nfo.title or '—'}",
                f"<b>演员：</b>{', '.join(nfo.actors) or '—'}",
                f"<b>类型：</b>{', '.join(nfo.genres) or '—'}",
                f"<b>tag：</b>{', '.join(nfo.tags) or '—'}",
                f"<b>视频：</b>{it.video_path or '—'}",
                f"<b>视频大小：</b>{human_size(it.video_size)}",
                f"<b>视频修改时间：</b>{human_time(it.video_mtime)}",
                "",
                "<b>NFO 内容：</b>",
                "<pre>" + (nfo.to_xml_bytes().decode("utf-8", "replace") if nfo.is_valid
                           else nfo.load_error or "") + "</pre>",
            ]
            self.detail_text.setHtml("<br>".join(lines))
            self.detail_tab.setCurrentIndex(0)
        else:
            self.detail_text.clear()

    # ---------- 统计 / 智能推荐 ----------

    def analyze_tags(self):
        counter = count_markers([it.nfo for it in self.items])
        if not counter:
            self.state.logger.warn("analyze", "导入列表中没有任何 tag/类型")
            QMessageBox.information(self, "提示", "当前列表中没有任何 tag / 类型标记")
            return
        dlg = TagAnalyzeDialog(
            counter, self,
            pre_censored=self.state.config.settings.censored_tags,
            pre_uncensored=self.state.config.settings.uncensored_tags,
            pre_target=self.state.config.settings.target_tag)
        if dlg.exec() == QDialog.Accepted:
            self.state.config.settings.censored_tags = sorted(dlg.censored)
            self.state.config.settings.uncensored_tags = sorted(dlg.uncensored)
            self.state.config.settings.target_tag = dlg.target_tag
            self.state.config.save_settings()
            if dlg.target_tag:
                self.target_tag_edit.setText(dlg.target_tag)
            self._refresh_table()
            self.state.logger.info(
                "analyze",
                f"确认分组：有码 {len(dlg.censored)} 个 tag，无码 {len(dlg.uncensored)} 个 tag",
                new=dlg.target_tag or "(未设置目标)")
            if dlg.censored and dlg.target_tag:
                self._auto_add_replace_rule(sorted(dlg.censored), dlg.target_tag)

    def _auto_add_replace_rule(self, censored_tags: list[str], target: str):
        mapper = self.state.config.mapper
        for r in mapper.rules:
            if r.operation == "replace" and r.new_tag == target and \
               set(r.match_tags) == set(censored_tags):
                return
        rule = MappingRule(name="🤖 有码→" + target,
                           match_tags=list(censored_tags),
                           operation="replace", new_tag=target)
        mapper.rules.append(rule)
        self.state.config.save_rules()
        self._reload_rules()
        self.state.logger.info("settings", f"自动生成规则: {rule.describe()}")

    def suggest_rules(self):
        if not self.items:
            QMessageBox.information(self, "提示", "请先导入 NFO 文件")
            return
        counter = count_markers([it.nfo for it in self.items])
        if not counter:
            QMessageBox.information(self, "提示", "当前文件没有任何 tag / 类型标记")
            return
        dlg = RuleSuggestionDialog(
            counter, len(self.items), self,
            censored=self.state.config.settings.censored_tags,
            uncensored=self.state.config.settings.uncensored_tags,
            target=self.state.config.settings.target_tag)
        if dlg.exec() == QDialog.Accepted and dlg.selected_rules:
            n = 0
            for rule in dlg.selected_rules:
                if not any(r.id == rule.id for r in self.state.config.mapper.rules):
                    self.state.config.mapper.rules.append(rule)
                    n += 1
            self.state.config.save_rules()
            self._reload_rules()
            self.state.logger.info("settings", f"智能推荐：添加 {n} 条规则")
            QMessageBox.information(self, "完成", f"已添加 {n} 条规则，可在右侧面板查看")

    # ---------- 映射 ----------

    def _current_mapper(self) -> Mapper:
        m = Mapper(rules=list(self.state.config.mapper.rules))
        target = self.target_tag_edit.text().strip() or self.state.config.settings.target_tag
        censored = list(self.state.config.settings.censored_tags)
        if target and censored:
            has_replace = any(r.operation == "replace" for r in m.rules)
            if not has_replace:
                m.add_rule(MappingRule(name="(自动) 有码→目标",
                                       match_tags=censored,
                                       operation="replace",
                                       new_tag=target))
        return m

    def preview_changes(self):
        checked = self._checked_items()
        if not checked:
            QMessageBox.information(self, "提示",
                                    "请先勾选要处理的文件（可 Ctrl+A 全选，或点「✅ 勾选有码」）")
            return
        mapper = self._current_mapper()
        if not mapper.rules:
            QMessageBox.information(
                self, "提示",
                "没有可用的映射规则。\n"
                "建议：点「① 标签统计」确认有码分组并设置目标 tag（会自动生成规则），\n"
                "或点「🤖 智能规则」自动推荐。")
            return
        self._changes = mapper.preview_items(checked)
        if not self._changes:
            QMessageBox.information(self, "提示", "预览结果为空：勾选的文件没有命中映射规则")
            self.state.logger.warn("preview", "预览结果为空")
            return
        self.state.logger.info("preview", f"预览生成 {len(self._changes)} 处变更")
        dlg = PreviewDialog(self._changes, self)
        dlg.exec()

    # ---------- 应用 / 回滚 ----------

    def apply_changes(self):
        checked = self._checked_items()
        if not checked:
            QMessageBox.information(self, "提示",
                                    "请先勾选要处理的文件（可 Ctrl+A 全选，或点「✅ 勾选有码」）")
            return
        mapper = self._current_mapper()
        # 预览用于确认（在副本上计算，不污染原数据）
        self._changes = mapper.preview_items(checked)
        if not self._changes:
            QMessageBox.information(self, "提示", "勾选的文件没有检测到变更")
            return
        n_files = len({c.path for c in self._changes})
        if not ConfirmDialog.ask(
                self, "应用修改",
                f"将应用 {len(self._changes)} 处变更，涉及勾选的 {n_files} 个文件。\n"
                "执行后原文件会生成 .bak 备份。确认继续？",
                detail="\n".join(f"{os.path.basename(c.path)}  {c.rule.describe()}"
                                 for c in self._changes[:20]) +
                        (f"\n…共 {len(self._changes)} 条" if len(self._changes) > 20 else "")):
            return
        self._do_apply(checked, mapper)

    def _do_apply(self, items: list, mapper: Mapper):
        """真实应用：直接修改原树后保存（重活放后台线程，主线程不再冻结）。
        后台线程只做纯 IO/网络（apply+save+archive+sync），不触碰任何 UI 控件；
        完成后回到主线程刷新表格并弹结果。"""
        backup = self.backup_check.isChecked()
        sync_jf = self.state.config.settings.sync_jellyfin

        def _work(_worker=None):
            changes = mapper.apply_items(items)
            ok, fail = 0, 0
            seen: set[str] = set()
            for c in changes:
                if _worker and _worker.cancelled():
                    break
                nfo = c.item.nfo
                if nfo.path in seen:
                    continue
                seen.add(nfo.path)
                err = nfo.save(backup=backup)
                if err:
                    fail += 1
                    self.state.logger.error("apply", err, path=nfo.path, result="失败")
                else:
                    ok += 1
                    self.state.logger.info(
                        "apply", c.rule.describe(),
                        path=nfo.path, field="tag",
                        old=", ".join(c.old_tags), new=", ".join(c.new_tags),
                        result="成功")
                    self._write_archive(c)
                    if sync_jf:
                        self._sync_jellyfin(nfo)
                if _worker:
                    _worker.report(ok + fail, len(changes), "应用修改…")
            self.state.logger.info("apply", f"应用完成：成功 {ok}，失败 {fail}")
            return ok, fail

        def _on_done(result, worker):
            ok, fail = result
            self._refresh_table()
            if worker.isInterruptionRequested():
                QMessageBox.information(self, "已完成", f"应用已取消（成功 {ok} 个，失败 {fail} 个）")
                return
            msg = f"应用完成：成功 {ok} 个，失败 {fail} 个"
            if sync_jf:
                msg += "（已自动同步到 Jellyfin）"
            QMessageBox.information(self, "完成", msg)

        def _on_error(e, worker):
            self.state.logger.error("apply", f"应用失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "应用失败", str(e))

        run_with_progress(self, "应用修改", "应用修改…", _work,
                          on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=len(items))

    def _sync_jellyfin(self, nfo):
        """把修改后的 NFO 同步到 Jellyfin 数据库（清掉旧值）。"""
        try:
            from core.jellyfin import JellyfinClient
            client = JellyfinClient(self.state.config.settings.jellyfin_server,
                                    self.state.config.settings.jellyfin_api_key)
            ok, msg = client.sync_nfo(nfo.number, nfo.path,
                                      nfo.tags, nfo.genres)
            if ok:
                self.state.logger.info("sync", msg, path=nfo.path, result="成功")
            else:
                self.state.logger.warn("sync", msg, path=nfo.path, result="跳过")
        except Exception as e:  # noqa: BLE001
            self.state.logger.error("sync", f"Jellyfin 同步失败: {e}", path=nfo.path)

    def _write_archive(self, c: Change):
        it = c.item
        nfo = it.nfo
        try:
            self.state.archive.add_record(
                nfo_path=nfo.path,
                video_path=it.video_path,
                number=nfo.number,
                actors=", ".join(nfo.actors),
                original_tag=", ".join(c.old_tags),
                new_tag=", ".join(c.new_tags),
                file_size=it.video_size,
                mtime=datetime.fromtimestamp(it.video_mtime).isoformat(timespec="seconds")
                if it.video_mtime else "",
            )
        except Exception as e:  # noqa: BLE001
            self.state.logger.error("archive", f"写入档案失败: {e}", path=nfo.path)

    def rollback_checked(self):
        targets = self._checked_items()
        if not targets:
            QMessageBox.information(self, "提示",
                                    "请先勾选要回滚的文件（仅勾选的行会被回滚）")
            return
        with_bak = [it for it in targets if it.nfo.has_backup]
        if not with_bak:
            QMessageBox.information(self, "提示", "勾选的文件均没有 .bak 备份")
            return
        if not ConfirmDialog.ask(
                self, "回滚",
                f"将恢复 {len(with_bak)} 个勾选文件的 .bak 备份（覆盖当前 NFO）。确认？",
                detail="\n".join(it.nfo.path for it in with_bak)):
            return
        ok = 0
        for it in with_bak:
            err = it.nfo.restore_backup()
            if err:
                self.state.logger.error("rollback", err, path=it.nfo.path)
            else:
                ok += 1
                self.state.logger.info("rollback", "已从 .bak 恢复",
                                       path=it.nfo.path, result="成功")
        self._refresh_table()
        QMessageBox.information(self, "完成", f"回滚完成：{ok}/{len(with_bak)}")

    # ---------- 规则面板 ----------

    def _reload_rules(self):
        self.rule_list.clear()
        for r in self.state.config.mapper.rules:
            it = QListWidgetItem(f"{r.name}  [{r.operation}]")
            it.setToolTip(f"{r.describe()}\nid: {r.id}")
            self.rule_list.addItem(it)

    def _rule_add(self):
        dlg = RuleEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.state.config.mapper.add_rule(dlg.rule)
            self.state.config.save_rules()
            self._reload_rules()
            self.state.logger.info("settings", f"新增规则: {dlg.rule.describe()}")

    def _rule_edit(self):
        row = self.rule_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择要编辑的规则")
            return
        rule = self.state.config.mapper.rules[row]
        dlg = RuleEditDialog(self, rule)
        if dlg.exec() == QDialog.Accepted:
            self.state.config.mapper.rules[row] = dlg.rule
            self.state.config.save_rules()
            self._reload_rules()
            self.state.logger.info("settings", f"编辑规则: {dlg.rule.describe()}")

    def _rule_del(self):
        row = self.rule_list.currentRow()
        if row < 0:
            return
        rule = self.state.config.mapper.rules[row]
        if ConfirmDialog.ask(self, "删除规则", f"确定删除规则「{rule.name}」？"):
            self.state.config.mapper.rules.pop(row)
            self.state.config.save_rules()
            self._reload_rules()
            self.state.logger.info("settings", f"删除规则: {rule.name}")

    def _rule_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入规则", "", "规则文件 (*.json)")
        if not path:
            return
        try:
            n = self.state.config.import_rules(path)
            self.state.config.save_rules()
            self._reload_rules()
            self.state.logger.info("settings", f"导入规则 {n} 条", path=path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))

    def _rule_export(self):
        if not self.state.config.mapper.rules:
            QMessageBox.information(self, "提示", "当前没有规则可导出")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出规则", "nfo-rules.json", "规则文件 (*.json)")
        if path:
            n = self.state.config.export_rules(path)
            self.state.logger.info("settings", f"导出规则 {n} 条", path=path)

    def _on_backup_toggled(self, val: bool):
        self.state.config.settings.backup_enabled = val
        self.state.config.save_settings()

    def _on_target_tag_changed(self):
        self.state.config.settings.target_tag = self.target_tag_edit.text().strip()
        self.state.config.save_settings()
