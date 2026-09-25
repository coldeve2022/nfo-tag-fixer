# -*- coding: utf-8 -*-
"""破解找回助手：演员检索 → 多线索打分 → 批量勾选 → 应用建档。

打分的作用：输入演员名后，候选文件可能很多。打分把「最可能是最近用 Lada
批量破解、且 tag 还没改好」的文件排到前面，你只需从上往下勾选确认。
每条线索都会在「线索」列展示，鼠标悬停分数可看全部依据。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.finder import Finder, ScoredCandidate
from core.scanner import scan_paths
from core.tag_analyzer import classify_tag
from ui.dialogs import ConfirmDialog
from ui.styles import check_bg
from ui.widgets import CheckTable, SortItem, colored_cell, open_in_explorer
from ui.worker import run_with_progress

from .fixer import human_size, human_time

COL_CHECK, COL_SCORE, COL_NUMBER, COL_NAME, COL_ACTORS, COL_CENSORED, \
    COL_UNCENSORED, COL_HINTS, COL_SIZE, COL_MTIME = range(10)


class FinderPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.items = []
        self.candidates: list[ScoredCandidate] = []
        # 行→候选的索引 + 分组缓存：避免每行 O(n) 线性查找（几千候选时会卡住）
        self._by_path: dict[str, ScoredCandidate] = {}
        self._groups_cache: tuple | None = None
        self._build_ui()
        self.state.logger.info("system", "破解找回页面已加载")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        top = QHBoxLayout()
        top.addWidget(QLabel("演员："))
        self.actor_edit = QLineEdit()
        self.actor_edit.setPlaceholderText("输入演员名（可多选，空格/、分隔）")
        top.addWidget(self.actor_edit, 1)
        self.btn_scan = QPushButton("扫描文件夹")
        self.btn_search = QPushButton("检索打分")
        self.btn_search.setProperty("class", "primary")
        top.addWidget(self.btn_scan)
        top.addWidget(self.btn_search)
        root.addLayout(top)

        tip = QLabel(
            "流程：① 扫描含候选视频的文件夹 → ② 输入演员名检索 → ③ 按分数从高到低确认候选 → "
            "④ 勾选（支持批量）→ ⑤ 应用修改并写入永久破解档案。\n"
            "「得分」高的文件 = 更可能是最近用 Lada 批量破解过的（同一时段的文件会被聚类），"
            "且 tag 仍为有码（该改）。分数列悬停可看全部线索依据。\n"
            "批量勾选：先用鼠标选中多行（Ctrl/Shift 多选），再点「☑ 勾选选中行」或右键菜单勾选；"
            "Ctrl+A 全选 / Ctrl+U 全不选 / Ctrl+I 反选 / 空格 切换当前行 / 「✅ 勾选有码」一键选待修文件")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa0ac; padding:4px 2px;")
        root.addWidget(tip)

        self.table = CheckTable(self, on_shortcut=self._on_shortcut)
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels(
            ["✓", "得分", "番号", "文件名", "演员", "有码tag", "无码tag",
             "线索", "视频大小", "修改时间"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.set_menu_builder(self._table_menu_builder)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for col, w in ((COL_SCORE, 50), (COL_NAME, 200), (COL_CENSORED, 110),
                       (COL_UNCENSORED, 110), (COL_HINTS, 180), (COL_MTIME, 125)):
            self.table.setColumnWidth(col, w)
        root.addWidget(self.table, 1)

        # 批量勾选工具行
        batch = QHBoxLayout()
        self.btn_check_sel = QPushButton("☑ 勾选选中行")
        self.btn_check_sel.setToolTip("把当前用鼠标选中的行全部勾选（按住 Ctrl/Shift 可多选）")
        self.btn_uncheck_sel = QPushButton("☐ 取消勾选选中行")
        self.btn_check_censored = QPushButton("✅ 勾选有码")
        self.btn_check_censored.setToolTip("一键勾选所有「有码tag」列非空的行（待修文件）")
        self.btn_check_all = QPushButton("全选")
        self.btn_check_none = QPushButton("全不选")
        self.btn_check_invert = QPushButton("反选")
        self.score_threshold = QLineEdit()
        self.score_threshold.setPlaceholderText("分数≥")
        self.score_threshold.setFixedWidth(70)
        self.btn_check_score = QPushButton("按分数勾选")
        batch.addWidget(self.btn_check_sel)
        batch.addWidget(self.btn_uncheck_sel)
        batch.addWidget(self.btn_check_censored)
        batch.addWidget(self.btn_check_all)
        batch.addWidget(self.btn_check_none)
        batch.addWidget(self.btn_check_invert)
        batch.addSpacing(8)
        batch.addWidget(self.score_threshold)
        batch.addWidget(self.btn_check_score)
        batch.addStretch()
        self._sel_label = QLabel("已勾选 0 行")
        batch.addWidget(self._sel_label)
        root.addLayout(batch)

        bottom = QHBoxLayout()
        self.count_label = QLabel("候选 0")
        self.target_tag_edit = QLineEdit()
        self.target_tag_edit.setPlaceholderText("目标无码 tag（默认取设置中的目标 tag）")
        self.target_tag_edit.setText(self.state.config.settings.target_tag)
        self.archive_check = QCheckBox("应用后写入破解档案")
        self.archive_check.setChecked(True)
        self.btn_apply = QPushButton("应用勾选修改")
        self.btn_apply.setProperty("class", "primary")
        self.btn_apply.setEnabled(False)
        bottom.addWidget(self.count_label)
        bottom.addWidget(QLabel("目标tag:"))
        bottom.addWidget(self.target_tag_edit, 1)
        bottom.addWidget(self.archive_check)
        bottom.addWidget(self.btn_apply)
        root.addLayout(bottom)

        self.btn_scan.clicked.connect(self._scan_dir)
        self.btn_search.clicked.connect(self.search)
        self.actor_edit.returnPressed.connect(self.search)
        self.btn_apply.clicked.connect(self.apply_selected)

        # 批量勾选
        self.btn_check_sel.clicked.connect(self.check_selected)
        self.btn_uncheck_sel.clicked.connect(self.uncheck_selected)
        self.btn_check_censored.clicked.connect(self.check_censored)
        self.btn_check_all.clicked.connect(self.check_all)
        self.btn_check_none.clicked.connect(self.check_none)
        self.btn_check_invert.clicked.connect(self.check_invert)
        self.btn_check_score.clicked.connect(self.check_by_score)
        self.score_threshold.returnPressed.connect(self.check_by_score)
        self.table.itemChanged.connect(self._on_item_changed)

    # ---------- 批量勾选 ----------

    def _set_check(self, row: int, checked: bool):
        it = self.table.item(row, COL_CHECK)
        if it is not None:
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def check_all(self):
        self.table.set_all_checked(True)
        self._refresh_count_label()

    def check_none(self):
        self.table.set_all_checked(False)
        self._refresh_count_label()

    def check_invert(self):
        self.table.check_invert()
        self._refresh_count_label()

    def _refresh_count_label(self):
        self._sel_label.setText(f"已勾选 {self.table.checked_count()} 行")

    def check_censored(self):
        """一键勾选所有「有码tag」列非空的行（待修文件）。"""
        n = 0
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_CENSORED)
            if it is not None and it.text().strip():
                self.table.set_row_checked(r, True)
                n += 1
        self._refresh_count_label()
        self.state.logger.info("search", f"一键勾选有码：{n} 行")

    def check_by_score(self):
        """按分数阈值勾选：勾选所有得分 >= 输入值的行。"""
        raw = self.score_threshold.text().strip()
        try:
            th = int(raw)
        except ValueError:
            QMessageBox.information(self, "提示", "请输入数字作为分数下限，如 40")
            return
        n = 0
        for r in range(self.table.rowCount()):
            sc = self.table.item(r, COL_SCORE)
            if sc is not None and sc.text().strip().isdigit() \
                    and int(sc.text()) >= th:
                self.table.set_row_checked(r, True)
                n += 1
        self._refresh_count_label()
        self.state.logger.info("search", f"按分数≥{th}勾选：{n} 行")

    def _on_item_changed(self, item):
        """勾选状态变化时：更新单元格背景色与已勾选计数，让选中状态醒目。"""
        if item.column() != COL_CHECK:
            return
        checked = item.checkState() == Qt.Checked
        if checked:
            item.setBackground(QColor(check_bg()))
        else:
            item.setData(Qt.BackgroundRole, None)  # 恢复默认背景
        # 更新计数
        self._refresh_count_label()

    def _on_shortcut(self, action: str):
        """表格快捷键回调：all / none / invert / toggle / toggle_selected / apply。"""
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
            rows = sorted({i.row() for i in self.table.selectedItems()})
            if not rows and self.table.currentRow() >= 0:
                rows = [self.table.currentRow()]
            for r in rows:
                self.table.set_row_checked(r, True)
            self._refresh_count_label()
        elif action == "uncheck_selected":
            rows = sorted({i.row() for i in self.table.selectedItems()})
            if not rows and self.table.currentRow() >= 0:
                rows = [self.table.currentRow()]
            for r in rows:
                self.table.set_row_checked(r, False)
            self._refresh_count_label()
        elif action == "apply":
            self.apply_selected()

    def check_selected(self):
        self._on_shortcut("check_selected")

    def uncheck_selected(self):
        self._on_shortcut("uncheck_selected")

    def _table_menu_builder(self, menu, row: int, selected: list[int]):
        """右键菜单扩展：打开文件夹 / 复制路径。"""
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication

        def cands():
            return [self._candidate_at(r) for r in selected]

        act_open = QAction("打开所在文件夹", self)
        act_copy = QAction("复制路径", self)
        act_open.triggered.connect(
            lambda: [open_in_explorer(c.item.nfo.path) for c in cands() if c])
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(
                "\n".join(c.item.nfo.path for c in cands() if c)))
        act_open.setEnabled(bool(selected))
        act_copy.setEnabled(bool(selected))
        menu.addAction(act_open)
        menu.addAction(act_copy)

    # ---------- 扫描 ----------

    def _scan_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹（嵌套扫描）")
        if not d:
            return
        self.items = scan_paths([d], recursive=True)
        ok = sum(1 for it in self.items if it.nfo.is_valid)
        self.state.logger.info("import", f"破解找回：扫描 {len(self.items)} 个 NFO",
                               result=f"成功 {ok}，失败 {len(self.items)-ok}", path=d)
        self._refresh_count()
        if ok:
            QMessageBox.information(self, "完成", f"扫描完成：{len(self.items)} 个 NFO")

    # ---------- 检索打分 ----------

    def search(self):
        actor_raw = self.actor_edit.text().strip()
        if not actor_raw:
            QMessageBox.information(self, "提示", "请先输入演员名")
            return
        if not self.items:
            QMessageBox.information(self, "提示", "请先点击「扫描文件夹」导入候选文件")
            return
        actors = [a.strip() for a in
                  actor_raw.replace("、", " ").replace(",", " ").split() if a.strip()]
        matched = []
        for it in self.items:
            nfo_actors = it.nfo.actors
            if any(any(a in na or na in a for na in nfo_actors) for a in actors):
                matched.append(it)
        if not matched:
            QMessageBox.information(self, "提示", f"没有找到包含「{actor_raw}」的 NFO")
            self.candidates = []
            self._refresh_table()
            self.state.logger.warn("search", f"演员 {actor_raw} 无匹配")
            return
        finder = Finder(self.state.config.settings.ffprobe_path,
                        enable_ffprobe=self.state.config.settings.enable_ffprobe)
        self.candidates = finder.score_items(matched)
        self._refresh_table()
        self.state.logger.info(
            "search", f"演员 {actor_raw}：候选 {len(matched)} 个",
            result=f"已打分，最高 {self.candidates[0].score if self.candidates else 0} 分")
        if finder.enable_ffprobe and not finder.ffprobe_available():
            self.state.logger.warn("search", finder.ffprobe_error())

    # ---------- 表格 ----------

    def _groups(self):
        """有码/无码标记集合：用户确认分组 + 关键词分类（tag + genre）。结果缓存。"""
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

    def _invalidate(self) -> None:
        self._by_path = {c.path: c for c in self.candidates}
        self._groups_cache = None

    def _refresh_count(self):
        self.count_label.setText(f"候选 {len(self.candidates)}")

    def _refresh_table(self):
        self._invalidate()
        c, u = self._groups()
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for cand in self.candidates:
                nfo = cand.item.nfo
                row = self.table.rowCount()
                self.table.insertRow(row)

                ck = QTableWidgetItem()
                ck.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                ck.setCheckState(Qt.Unchecked)
                ck.setData(Qt.UserRole, cand.path)
                self.table.setItem(row, COL_CHECK, ck)

                sc = SortItem(str(cand.score), "#ffd77a", sort_value=cand.score)
                sc.setTextAlignment(Qt.AlignCenter)
                sc.setToolTip("；\n".join(cand.reasons) or "无线索")
                self.table.setItem(row, COL_SCORE, sc)

                self.table.setItem(row, COL_NUMBER,
                                   SortItem(nfo.number, "#9fd0ff",
                                            sort_value=nfo.number))
                self.table.setItem(row, COL_NAME,
                                   colored_cell(nfo.basename, "#d7dae0", nfo.path))
                self.table.setItem(row, COL_ACTORS,
                                   colored_cell(", ".join(nfo.actors)[:40], "#d7dae0"))

                markers = nfo.markers
                c_tags = [t for t in markers if t in c]
                u_tags = [t for t in markers if t in u]
                self.table.setItem(row, COL_CENSORED,
                                   colored_cell(", ".join(c_tags)[:60], "#ff6b6b"))
                self.table.setItem(row, COL_UNCENSORED,
                                   colored_cell(", ".join(u_tags)[:60], "#8ae6a1"))

                hints = "；".join(cand.reasons[:2]) if cand.reasons else "—"
                hint_item = colored_cell(hints, "#9aa0ac")
                hint_item.setToolTip("；\n".join(cand.reasons) or "—")
                self.table.setItem(row, COL_HINTS, hint_item)

                self.table.setItem(row, COL_SIZE,
                                   SortItem(human_size(cand.item.video_size), "#b6bcc8",
                                            sort_value=cand.item.video_size))
                self.table.setItem(row, COL_MTIME,
                                   SortItem(human_time(cand.item.video_mtime), "#8a8f99",
                                            sort_value=cand.item.video_mtime))
        finally:
            self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        self._refresh_count()
        self.btn_apply.setEnabled(len(self.candidates) > 0)
        self._sel_label.setText("已勾选 0 行")

    def _candidate_at(self, row: int):
        """按行取候选（排序后行序与 candidates 不一致，用路径查索引表）。"""
        if row < 0 or row >= self.table.rowCount():
            return None
        cell = self.table.item(row, COL_NAME)
        if cell is None:
            return None
        return self._by_path.get(cell.data(Qt.UserRole))

    # ---------- 应用 ----------

    def apply_selected(self):
        selected = []
        for row in range(self.table.rowCount()):
            ck = self.table.item(row, COL_CHECK)
            if ck and ck.checkState() == Qt.Checked:
                c = self._candidate_at(row)
                if c is not None:
                    selected.append(c)
        if not selected:
            QMessageBox.information(self, "提示", "请先勾选要应用的候选")
            return
        target = self.target_tag_edit.text().strip() or self.state.config.settings.target_tag
        if not target:
            QMessageBox.warning(self, "提示", "请填写目标无码 tag")
            return
        if not ConfirmDialog.ask(
                self, "应用破解修改",
                f"将对 {len(selected)} 个文件应用修改：\n"
                f"把有码类 tag 替换为「{target}」（保留无码类 tag）。\n"
                "执行前会生成 .bak 备份。确认？",
                detail="\n".join(c.path for c in selected)):
            return
        sync_jf = self.state.config.settings.sync_jellyfin
        do_archive = self.archive_check.isChecked()
        backup = self.state.config.settings.backup_enabled
        # 分组集合只算一次（原先每个文件都重算一遍全部候选的标记 → O(n²)）
        censored_set, _u = self._groups()

        # 重活（逐文件改+存+建档+可选同步）放后台线程；backup/archive/sync 是全量一次性开关。
        def _work(_worker=None):
            ok = 0
            for i, c in enumerate(selected):
                if _worker and _worker.cancelled():
                    break
                nfo = c.item.nfo
                changed = self._apply_candidate(nfo, target, censored_set)
                if not changed:
                    continue
                err = nfo.save(backup=backup)
                if err:
                    self.state.logger.error("apply", err, path=nfo.path, result="失败")
                    continue
                ok += 1
                self.state.logger.info(
                    "apply", f"破解找回应用：tag → {target}",
                    path=nfo.path, field="tag",
                    old=", ".join(nfo.tags), new=target, result="成功")
                if do_archive:
                    self._archive(c, target)
                if sync_jf:
                    self._sync_jellyfin(nfo)
                if _worker:
                    _worker.report(i + 1, len(selected), "应用破解修改…")
            self.state.logger.info("apply", f"破解找回应用完成：{ok}/{len(selected)}")
            return ok

        def _on_done(ok, worker):
            self._refresh_table()
            if worker.isInterruptionRequested():
                QMessageBox.information(self, "已完成", f"应用已取消（成功 {ok} 个）")
                return
            msg = f"应用完成：{ok}/{len(selected)}"
            if sync_jf:
                msg += "（已自动同步到 Jellyfin）"
            QMessageBox.information(self, "完成", msg)

        def _on_error(e, worker):
            self.state.logger.error("apply", f"破解找回应用失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "应用失败", str(e))

        run_with_progress(self, "应用破解修改", "应用破解修改…", _work,
                          on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=len(selected))

    def _sync_jellyfin(self, nfo):
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

    def _apply_candidate(self, nfo, target: str, censored_set: set | None = None) -> bool:
        """把有码类标记（tag + genre）替换为目标，无码类保留。返回是否产生变更。"""
        if not nfo.is_valid or nfo.root is None:
            return False
        if censored_set is None:
            censored_set, _u = self._groups()
        changed = False
        for f in ("tag", "genre"):
            for el in list(nfo.root.iter(f)):
                t = (el.text or "").strip()
                if t in censored_set or classify_tag(t) == "censored":
                    el.text = target
                    changed = True
        if changed:
            nfo.mark_dirty()
        return changed

    def _archive(self, c: ScoredCandidate, target: str):
        nfo = c.item.nfo
        try:
            self.state.archive.add_record(
                nfo_path=nfo.path,
                video_path=c.item.video_path,
                number=nfo.number,
                actors=", ".join(nfo.actors),
                original_tag=", ".join(nfo.tags),
                new_tag=target,
                file_size=c.item.video_size,
                mtime=datetime.fromtimestamp(c.item.video_mtime).isoformat(timespec="seconds")
                if c.item.video_mtime else "",
            )
            self.state.logger.info("archive", "已写入永久破解档案", path=nfo.path,
                                   result=f"番号 {nfo.number or '?'}")
        except Exception as e:  # noqa: BLE001
            self.state.logger.error("archive", f"建档失败: {e}", path=nfo.path)
