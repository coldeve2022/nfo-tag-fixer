# -*- coding: utf-8 -*-
"""NFO 修复页：补救「重复/空 NFO 导致标签丢失」的普遍问题。

- 扫描目录，找出所有「同一影片目录存在多个 NFO」的情况
- 用【视频文件数】自动判定：单视频目录里的多套 NFO 全是重复刮削 → 标注"可安全移除"（一键批量）
- 空 NFO / 重复刮削 → 一键移到回收站（可撤销）
- 把最优 NFO 的正确标签重新同步到 Jellyfin，补救被清空的库内标签
- 交互与其它页一致：CheckTable（Ctrl+A/U/I、空格、右键菜单、批量勾选）
- 演员名仅存于 tag 且 <actor> 为空的检查与补入（防信息丢失）
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMenu, QMessageBox, QPushButton,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.nfo_repair import (
    backfill_actors, scan_actor_missing, scan_duplicates, trash_files,
)
from ui.dialogs import ConfirmDialog
from ui.widgets import CheckTable, SortItem, open_in_explorer
from ui.worker import run_with_progress

EMPTY_COLOR = "#d93025"
BEST_COLOR = "#1e8e3e"
MANUAL_COLOR = "#c56cf0"
VERDICT_COLOR = {"可安全移除": EMPTY_COLOR, "需人工确认": MANUAL_COLOR,
                 "无自动处理": "#9aa0ac"}


class RepairPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.folder = ""
        self.issues: list[dict] = []
        self._build_ui()
        self.state.logger.info("system", "NFO 修复页面已加载")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        toolbar = QHBoxLayout()
        self.btn_pick = QPushButton("选择目录…")
        self.btn_scan = QPushButton("扫描问题")
        self.btn_scan.setEnabled(False)
        self.btn_trash = QPushButton("🗑 移除冗余 NFO（回收站）")
        self.btn_trash.setProperty("class", "danger")
        self.btn_trash.setEnabled(False)
        self.btn_sync = QPushButton("🔁 补救同步到 Jellyfin")
        self.btn_sync.setProperty("class", "primary")
        self.btn_sync.setEnabled(False)
        self.btn_actor = QPushButton("🧑 演员名检查（防丢失）")
        toolbar.addWidget(self.btn_pick)
        toolbar.addWidget(self.btn_scan)
        toolbar.addSpacing(10)
        toolbar.addWidget(self.btn_trash)
        toolbar.addWidget(self.btn_sync)
        toolbar.addWidget(self.btn_actor)
        toolbar.addStretch()
        root.addLayout(toolbar)

        tip = QLabel(
            "背景：同目录常并存多套 NFO（另一工具的 movie.nfo 常被清空 + 正式 NFO），Jellyfin 读到空文件会把库内标签清空。\n"
            "本页按【视频文件数】自动判定：目录里只有 1 部视频却堆了多套 NFO → 非最优的多是重复刮削，标为"
            "<b>可安全移除</b>；有 ≥2 部视频的才标 <b>需人工确认</b>（可能真有多部）。\n"
            "操作：Ctrl+A 全选 / Ctrl+U 全不选 / Ctrl+I 反选 / 空格 切换当前行；右键菜单同理；"
            "一键「自动勾选可安全移除」→「移除冗余→回收站」，再「补救同步回 Jellyfin」。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa0ac; padding:4px 2px;")
        root.addWidget(tip)

        # 表格（复用成熟 CheckTable：勾选列 + 快捷键 + 右键）
        self.table = CheckTable(self, self._on_shortcut)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["☑", "目录", "视频", "NFO", "最佳 NFO", "冗余", "影集概要", "判定"])
        h = self.table.horizontalHeader()
        for col, w in ((0, 34), (1, 250), (2, 44), (3, 44), (4, 150), (5, 60),
                       (6, 240), (7, 120)):
            self.table.setColumnWidth(col, w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.set_menu_builder(self._build_menu)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSortIndicator(7, Qt.AscendingOrder)  # 默认按判定排序
        root.addWidget(self.table, 1)

        # 底部：批量勾选 + 汇总
        bottom = QHBoxLayout()
        self.btn_check_auto = QPushButton("✓ 自动勾选『可安全移除』")
        self.btn_check_all = QPushButton("全选")
        self.btn_check_none = QPushButton("全不选")
        self.btn_check_invert = QPushButton("反选")
        bottom.addWidget(self.btn_check_auto)
        bottom.addWidget(self.btn_check_all)
        bottom.addWidget(self.btn_check_none)
        bottom.addWidget(self.btn_check_invert)
        bottom.addStretch()
        self.summary = QLabel("尚未扫描")
        bottom.addWidget(self.summary)
        root.addLayout(bottom)

        self.btn_pick.clicked.connect(self._pick)
        self.btn_scan.clicked.connect(self._scan)
        self.btn_trash.clicked.connect(self._trash)
        self.btn_sync.clicked.connect(self._sync)
        self.btn_actor.clicked.connect(self._open_actor_check)
        self.btn_check_auto.clicked.connect(self._check_auto)
        self.btn_check_all.clicked.connect(lambda: self._on_shortcut("all"))
        self.btn_check_none.clicked.connect(lambda: self._on_shortcut("none"))
        self.btn_check_invert.clicked.connect(lambda: self._on_shortcut("invert"))

    # ---------- 快捷键 / 批量勾选 ----------

    def _on_shortcut(self, action: str):
        if action == "all":
            if self.issues:
                self.table.set_all_checked(True)
        elif action == "none":
            self.table.set_all_checked(False)
        elif action == "invert":
            self.table.check_invert()
        elif action == "toggle":
            self.table.toggle_selected_rows()
        elif action == "toggle_selected":
            self.table.toggle_selected_rows()
        self._update_summary()

    def _check_auto(self):
        """自动勾选所有『可安全移除』的行（需人工的不动）。"""
        self.table.blockSignals(True)
        try:
            for r in range(self.table.rowCount()):
                it = self.table.item(r, 0)
                issue = it.data(Qt.UserRole) if it else None
                checked = bool(issue and issue.get("verdict") == "可安全移除")
                self.table.set_row_checked(r, checked)
        finally:
            self.table.blockSignals(False)
        self._update_summary()

    def _checked_issues(self) -> list[dict]:
        out = []
        for r in self.table.checked_rows():
            it = self.table.item(r, 0)
            issue = it.data(Qt.UserRole) if it else None
            if issue:
                out.append(issue)
        return out

    # ---------- 右键菜单 ----------

    def _build_menu(self, menu: QMenu, _row: int, sel_rows: list[int]):
        issues = [self.table.item(r, 0).data(Qt.UserRole)
                  for r in sel_rows if self.table.item(r, 0)]
        issues = [i for i in issues if i]

        act_open = QAction("打开所在文件夹", self)
        act_open.triggered.connect(
            lambda: [open_in_explorer(i["dir"]) for i in issues])
        menu.addAction(act_open)

        act_trash = QAction("🗑 移除冗余 NFO（回收站）", self)
        act_trash.triggered.connect(self._trash)
        menu.addAction(act_trash)

        act_sync = QAction("🔁 补救同步到 Jellyfin", self)
        act_sync.triggered.connect(self._sync)
        menu.addAction(act_sync)

    def _update_summary(self):
        if not self.issues:
            return
        n_safe = sum(1 for i in self.issues if i["verdict"] == "可安全移除")
        n_manual = sum(1 for i in self.issues if i["verdict"] == "需人工确认")
        n_empty = sum(1 for i in self.issues if i["has_empty"])
        checked = len(self._checked_issues())
        self.summary.setText(
            f"受影响 {len(self.issues)} | 可安全移除 {n_safe} | 需人工 {n_manual} "
            f"| 含空NFO {n_empty} | 已勾选 {checked}")
        self.btn_trash.setEnabled(any(self._checked_issues()))
        self.btn_sync.setEnabled(any(self._checked_issues()))

    # ---------- 扫描 ----------

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "选择要扫描的目录（嵌套）")
        if d:
            self.folder = d
            self.btn_scan.setEnabled(True)
            self.summary.setText(f"已选目录：{d}")

    def _scan(self):
        if not self.folder:
            return

        # 目录全扫 + 逐个 NFO 解析是重 IO，放后台线程，主线程不冻结、可取消。
        def _do(_worker=None):
            return scan_duplicates(self.folder)

        def _on_done(issues, worker):
            if worker.isInterruptionRequested():
                self.summary.setText("扫描已取消")
                return
            self.issues = issues
            self._fill_table()

        def _on_error(e, worker):
            self.state.logger.error("repair", f"扫描失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "扫描失败", str(e))

        run_with_progress(self, "NFO 修复扫描", "正在扫描…", _do,
                          on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=0)

    def _fill_table(self):
        self.table.blockSignals(True)
        try:
            self.table.setSortingEnabled(False)
            self.table.setRowCount(0)
            for it in self.issues:
                r = self.table.rowCount()
                self.table.insertRow(r)
                chk = QTableWidgetItem("")
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Unchecked)
                chk.setData(Qt.UserRole, it)
                self.table.setItem(r, 0, chk)
                self.table.setItem(r, 1, SortItem(it["dir"], ""))
                self.table.setItem(r, 2, SortItem(
                    str(it["n_videos"]), "#9aa0ac", sort_value=it["n_videos"]))
                self.table.setItem(r, 3, SortItem(
                    str(len(it["nfos"])), "#9aa0ac", sort_value=len(it["nfos"])))
                self.table.setItem(r, 4, SortItem(
                    it["best_name"], BEST_COLOR, sort_value=it["best_name"]))
                self.table.setItem(r, 5, SortItem(
                    str(len(it["redundant"])), EMPTY_COLOR,
                    sort_value=len(it["redundant"])))
                parts = [f"{n['name']}({n['n_tags']}/{n['n_genres']}/{n['n_actors']})"
                         for n in it["nfos"]]
                self.table.setItem(r, 6, SortItem(" + ".join(parts), "#9aa0ac"))
                self.table.setItem(r, 7, SortItem(
                    it["verdict"], VERDICT_COLOR.get(it["verdict"], "#9aa0ac")))
            self.table.setSortingEnabled(True)
        finally:
            self.table.blockSignals(False)
        n_safe = sum(1 for i in self.issues if i["verdict"] == "可安全移除")
        n_manual = sum(1 for i in self.issues if i["verdict"] == "需人工确认")
        self.summary.setText(
            f"受影响 {len(self.issues)} | 可安全移除 {n_safe} | 需人工 {n_manual} | 已勾选 0")
        self.state.logger.info(
            "repair", f"NFO 修复扫描完成：受影响 {len(self.issues)}，"
                      f"可安全移除 {n_safe}，需人工 {n_manual}")

    # ---------- 移除冗余 ----------

    def _trash(self):
        sel = self._checked_issues()
        if not sel:
            sel = [i for i in self.issues if i["redundant"]]
        targets = [p for i in sel for p in i["redundant"]]
        if not targets:
            QMessageBox.information(self, "提示", "勾选范围内没有可移除的冗余 NFO")
            return
        if not ConfirmDialog.ask(
                self, "移除冗余 NFO",
                f"将把 <b>{len(targets)}</b> 个冗余/重复刮削 NFO 移到【回收站】（可恢复）。\n"
                "绝不删除最优 NFO、不改动视频。确认执行？",
                detail="\n".join(os.path.basename(p) for p in targets[:20])):
            return
        ok_rec, ok_local, errs = trash_files(targets, self.folder)
        self.state.logger.info("repair", f"移除冗余 NFO: 回收站{ok_rec} 本地备份{ok_local} 失败{len(errs)}",
                               result="\n".join(e[0] for e in errs[:5]) if errs else "")
        self._scan()
        lines = []
        if ok_rec:
            lines.append(f"已移入系统回收站 {ok_rec} 个")
        if ok_local:
            lines.append(f"回收站不可用，已移入本地 .nfo_trash {ok_local} 个（可手动恢复）")
        if errs:
            lines.append(f"失败 {len(errs)} 个（见日志）")
        QMessageBox.information(self, "完成", "\n".join(lines) or "无操作")

    # ---------- 补救同步 ----------

    def _sync(self):
        from core.jellyfin import JellyfinClient
        from core.nfo import NfoFile
        s = self.state.config.settings
        client = JellyfinClient(s.jellyfin_server, s.jellyfin_api_key)
        if not client.configured:
            QMessageBox.warning(self, "未配置 Jellyfin",
                                "未配置 Jellyfin 服务器/API Key，无法补救同步。\n"
                                "可在「设置与工具」页配置。")
            return
        sel = self._checked_issues() or self.issues

        # 网络请求 + 逐个同步放后台线程，主线程不冻结、可取消。
        def _do(_worker=None):
            ok_n, errs = 0, []
            for i, it in enumerate(sel):
                if _worker and _worker.cancelled():
                    break
                if _worker:
                    _worker.report(i + 1, len(sel), os.path.basename(it["dir"]))
                try:
                    nfo = NfoFile(it["best"])
                except Exception:  # noqa: BLE001
                    errs.append((it["dir"], "读取最优NFO失败"))
                    continue
                r_ok, msg = client.sync_nfo(nfo.number, it["best"],
                                            nfo.tags, nfo.genres)
                if r_ok:
                    ok_n += 1
                else:
                    errs.append((it["dir"], msg))
            return ok_n, errs

        def _on_done(result, worker):
            ok_n, errs = result
            self.state.logger.info(
                "repair", f"补救同步：成功 {ok_n}，失败 {len(errs)}",
                result="\n".join(f"{d}: {m}" for d, m in errs[:8]) if errs else "")
            if worker.isInterruptionRequested():
                QMessageBox.information(self, "已完成", f"补救同步已取消（成功 {ok_n} 个）")
                return
            QMessageBox.information(
                self, "完成",
                f"补救同步成功 {ok_n} 个" + (f"，失败 {len(errs)} 个（见日志）" if errs else ""))

        def _on_error(e, worker):
            self.state.logger.error("repair", f"补救同步失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "补救同步失败", str(e))

        run_with_progress(self, "补救同步到 Jellyfin", "补救同步中…", _do,
                          on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=len(sel))

    # ---------- 演员名仅存于 tag 的检查 ----------

    def _open_actor_check(self):
        if not self.folder:
            QMessageBox.information(self, "提示", "请先在顶部选择目录")
            return
        ActorCheckDialog(self.folder, self).exec()


class ActorCheckDialog(QDialog):
    """检查「tag 里有演员名、但 <actor> 栏目为空」的影片，并可补回 <actor>。"""

    def __init__(self, folder: str, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.setWindowTitle("演员名检查：仅存于 tag / <actor> 为空")
        self.resize(860, 460)
        root = QVBoxLayout(self)
        tip = QLabel(
            "以下是「tag 里出现演员名、但本片 <actor> 栏目为空」的影片（含从 .bak 整理的）。\n"
            "Ctrl+A 全选 / Ctrl+U 全不选 / Ctrl+I 反选 / 空格 切换；勾选后点「补入 <actor>」"
            "把名字写回演员栏目（自动 .bak 备份、不重复补）。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa0ac;padding:4px 2px;")
        root.addWidget(tip)

        self.items: list[dict] = scan_actor_missing(folder)
        self.table = CheckTable(self, self._on_shortcut)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["☑", "影片 NFO", "演员名(在tag中)"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for it in self.items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, it)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, SortItem(os.path.basename(it["path"]), ""))
            names = "、".join(it["names"])
            self.table.setItem(r, 2, SortItem(
                names, EMPTY_COLOR if it["in_tag_now"] else MANUAL_COLOR,
                sort_value=names))
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_all = QPushButton("全选")
        self.btn_none = QPushButton("全不选")
        self.btn_back = QPushButton("补入 <actor>（备份）")
        self.btn_back.setProperty("class", "primary")
        self.summary = QLabel(f"共 {len(self.items)} 个")
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_none)
        row.addStretch()
        row.addWidget(self.summary)
        row.addWidget(self.btn_back)
        root.addLayout(row)

        self.btn_all.clicked.connect(lambda: self._on_shortcut("all"))
        self.btn_none.clicked.connect(lambda: self._on_shortcut("none"))
        self.btn_back.clicked.connect(self._backfill)

    def _on_shortcut(self, action: str):
        if action == "all":
            self.table.set_all_checked(True)
        elif action == "none":
            self.table.set_all_checked(False)
        elif action == "invert":
            self.table.check_invert()
        elif action == "toggle" or action == "toggle_selected":
            self.table.toggle_selected_rows()

    def _selected(self) -> list[dict]:
        sel = []
        for r in self.table.checked_rows():
            it = self.table.item(r, 0).data(Qt.UserRole) if self.table.item(r, 0) else None
            if it:
                sel.append(it)
        return sel or list(self.items)

    def _backfill(self):
        sel = self._selected()
        n_names = sum(len(x["names"]) for x in sel)
        if not sel or not n_names:
            return
        if not ConfirmDialog.ask(
                self, "补入演员",
                f"将为 <b>{len(sel)}</b> 个影片把 <b>{n_names}</b> 个演员名补入 <actor> 字段。\n"
                "每个文件修改前自动 .bak 备份。确认执行？",
                detail="\n".join(
                    f"{os.path.basename(x['path'])}: {'、'.join(x['names'])}"
                    for x in sel[:15])):
            return
        ok, errs = backfill_actors(sel)
        self._refresh()
        QMessageBox.information(
            self, "完成",
            f"已补入 {ok} 个文件" + (f"，{len(errs)} 个失败（见日志）" if errs else ""))

    def _refresh(self):
        self.items = scan_actor_missing(self.folder)
        self.summary.setText(f"剩余待补入 {len(self.items)} 个")
