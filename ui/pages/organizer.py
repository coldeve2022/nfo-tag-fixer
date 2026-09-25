# -*- coding: utf-8 -*-
"""标签整理页：扫描 → 规则检测 → AI 分析（Ollama/DashScope）→ 确认 → 应用。

- 表格列出所有 tag：操作 / 合并目标 / 标签 / 次数 / 来源 / 问题标记 / AI 建议 / 样本
- 操作列：保留 / 删除 / 合并到（可批量修改；「合并目标」列可逐行填写目标）
- 规则层：错误标记（演员名/片商/前缀/噪声）+ 同义/近义合并建议（日文友好，保守）
- AI 分析后弹「变更报告」：明确看到哪些标签合并到哪个、删除了哪些（可导出 HTML）
- 「备份恢复」：列出整理时生成的 .nfo.bak，多选一键恢复
- 应用时：改 NFO（生成 .bak）+ 可选同步 Jellyfin + 自动保存报告
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QTableWidget, QTabWidget, QVBoxLayout, QWidget,
)

from core.tag_organizer import (
    TagStat, ai_extract_type_tags, ai_result_path, apply_appended_tags,
    apply_organize, ai_classify, build_report, collect_tag_stats,
    effective_mapping, list_backups, load_ai_result,
    ollama_available, ollama_has_model, restore_backups, rule_detect,
    save_ai_result, save_report_html, suggest_similar_merges,
)
from ui.dialogs import ConfirmDialog
from ui.widgets import SortItem, open_in_explorer
from ui.worker import run_with_progress

ACTION_COLOR = {0: "#1e8e3e", 1: "#d93025", 2: "#b06000"}  # 保留绿 / 删除红 / 合并琥珀
ACTION_BG = {0: "#e6f4ea", 1: "#fce8e6", 2: "#fef7e0"}
FLAG_COLOR = {"前缀": "#e056fd", "演员名": "#ff6b6b", "片商": "#ff9f43",
              "噪声": "#b6bcc8", "近义": "#7ed6ff", "截断": "#c56cf0"}
AI_COLOR = {"keep": "#8ae6a1", "remove": "#ff6b6b", "merge": "#ffd77a"}


# ---------- 变更报告对话框 ----------

class ReportDialog(QDialog):
    """整理方案/结果报告：合并清单、删除清单、保留统计。"""

    def __init__(self, report: dict, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("标签整理变更报告")
        self.resize(760, 520)
        root = QVBoxLayout(self)

        merges, removes = report["merges"], report["removes"]
        summary = QLabel(
            f"涉及 <b>{report['nfo_count']}</b> 个 NFO ｜ 标签总数 <b>{report['tag_total']}</b> ｜ "
            f"合并 <b style='color:#b8860b'>{len(merges)}</b> 组 ｜ "
            f"删除 <b style='color:#c0392b'>{len(removes)}</b> 种 ｜ 保留 <b>{report['keeps']}</b> 种")
        summary.setWordWrap(True)
        summary.setStyleSheet("background:#f0f7ff;border:1px solid #cfe3ff;"
                              "border-radius:6px;padding:8px 10px;")
        root.addWidget(summary)

        tabs = QTabWidget()
        # 合并清单
        m_tab = QWidget()
        ml = QVBoxLayout(m_tab)
        m_table = QTableWidget(len(merges), 4)
        m_table.setHorizontalHeaderLabels(["原标签", "合并到", "次数", "依据"])
        m_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        m_table.verticalHeader().setVisible(False)
        m_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        m_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        for r, m in enumerate(merges):
            m_table.setItem(r, 0, SortItem(m["old"], "", sort_value=m["old"]))
            it = SortItem(m["target"], "#b8860b", sort_value=m["target"])
            m_table.setItem(r, 1, it)
            cnt = SortItem(str(m["count"]), "#9aa0ac", sort_value=m["count"])
            cnt.setTextAlignment(Qt.AlignCenter)
            m_table.setItem(r, 2, cnt)
            m_table.setItem(r, 3, SortItem(m["flag"] or "", "#7ed6ff"))
        m_table.setSortingEnabled(True)
        ml.addWidget(m_table)
        tabs.addTab(m_tab, f"合并 / 改名（{len(merges)}）")
        # 删除清单
        r_tab = QWidget()
        rl = QVBoxLayout(r_tab)
        r_table = QTableWidget(len(removes), 4)
        r_table.setHorizontalHeaderLabels(["标签", "次数", "依据", "原因"])
        r_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        r_table.verticalHeader().setVisible(False)
        r_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        r_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for r, x in enumerate(removes):
            r_table.setItem(r, 0, SortItem(x["name"], "", sort_value=x["name"]))
            cnt = SortItem(str(x["count"]), "#9aa0ac", sort_value=x["count"])
            cnt.setTextAlignment(Qt.AlignCenter)
            r_table.setItem(r, 1, cnt)
            r_table.setItem(r, 2, SortItem(x["flag"] or "", "#ff6b6b"))
            r_table.setItem(r, 3, SortItem(x["reason"] or "", "#9aa0ac"))
        r_table.setSortingEnabled(True)
        rl.addWidget(r_table)
        tabs.addTab(r_tab, f"删除（{len(removes)}）")
        root.addWidget(tabs, 1)

        btns = QDialogButtonBox()
        b_export = btns.addButton("导出报告 HTML…", QDialogButtonBox.ActionRole)
        b_close = btns.addButton("关闭", QDialogButtonBox.RejectRole)
        b_export.clicked.connect(self._export)
        b_close.clicked.connect(self.reject)
        root.addWidget(btns)

    def _export(self):
        default = os.path.join(
            os.path.expanduser("~"), "Desktop",
            f"标签整理报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
        p, _ = QFileDialog.getSaveFileName(
            self, "导出报告", default, "HTML 报告 (*.html)")
        if not p:
            return
        try:
            save_report_html(p, self.report)
            QMessageBox.information(self, "已导出", f"报告已保存到：\n{p}")
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))


# ---------- 备份恢复对话框 ----------

class CompareDialog(QDialog):
    """预览「恢复某个备份」会带来什么变化：对比当前 .nfo 与备份的字段差异。"""

    def __init__(self, nfo_path: str, bak_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("恢复预览：当前 vs 备份")
        self.resize(680, 520)
        from core.nfo import NfoFile
        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"<b>{os.path.basename(nfo_path)}</b>"))
        bak_lbl = QLabel(f"备份：{bak_path}")
        bak_lbl.setWordWrap(True)
        root.addWidget(bak_lbl)

        cur = NfoFile(nfo_path)
        bak = NfoFile(bak_path)
        c_tags, b_tags = set(cur.markers), set(bak.markers)
        c_act, b_act = set(cur.actors), set(bak.actors)

        def _block(title, color, items, empty_hint=""):
            box = QVBoxLayout()
            box.addWidget(QLabel(title))
            te = QPlainTextEdit()
            te.setReadOnly(True)
            te.setMaximumHeight(150)
            te.setPlainText("\n".join(sorted(items)) if items else empty_hint)
            box.addWidget(te)
            return box, items

        purged, _ = _block("🟢 恢复后将【移除】（仅当前整理后有，恢复即消失）",
                           "#d93025", c_tags - b_tags, "（无）")
        back, _ = _block("🔵 恢复后将【恢复】（仅备份有，整理时被删/被改走）",
                         "#1e8e3e", b_tags - c_tags, "（无）")
        act, _ = _block("👤 演员差异（当前 → 备份）", "#9aa0ac",
                        [f"{a}  →  移除" for a in c_act - b_act]
                        + [f"恢复：{a}" for a in b_act - c_act], "（无差异）")
        for _box in (purged, back, act):
            root.addLayout(_box)
        btns = QDialogButtonBox()
        btns.addButton("关闭", QDialogButtonBox.RejectRole).clicked.connect(self.reject)
        root.addWidget(btns)


class RestoreDialog(QDialog):
    """列出目录树中所有 .nfo.bak，支持搜索/排序/预览差异，多选一键恢复。"""

    def __init__(self, folders: list[str], parent=None):
        super().__init__(parent)
        self.folders = folders
        self.setWindowTitle("备份恢复")
        self.resize(920, 540)
        root = QVBoxLayout(self)
        tip = QLabel(
            "整理时每个 NFO 都会自动生成 <b>.nfo.bak</b>（整理前内容）。\n"
            "支持：🔍 按文件名搜索 ｜ 点表头排序 ｜ 双击行预览恢复差异 ｜ 勾选多选恢复。\n"
            "恢复用备份覆盖当前 .nfo，恢复前当前文件会自动另存 <b>.bak.pre_restore</b> 双保险。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa0ac;padding:4px 2px;")
        root.addWidget(tip)

        # 搜索 + 工具行
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 按文件名搜索（支持番号/演员等）")
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._refresh)
        top.addWidget(self.btn_refresh)
        self.btn_folder = QPushButton("打开所在文件夹")
        self.btn_folder.clicked.connect(self._open_folder)
        top.addWidget(self.btn_folder)
        root.addLayout(top)

        # 表格：恢复 | 状态 | NFO 文件 | 目录 | 大小 | 备份时间
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["恢复", "状态", "NFO 文件", "目录", "大小", "备份时间"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        h.setSortIndicator(5, Qt.DescendingOrder)  # 默认按备份时间倒序
        self.table.cellDoubleClicked.connect(self._preview)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_all = QPushButton("全选")
        self.btn_none = QPushButton("全不选")
        self.btn_restore = QPushButton("恢复选中")
        self.btn_restore.setProperty("class", "primary")
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_none)
        row.addStretch()
        self.summary = QLabel("")
        row.addWidget(self.summary)
        row.addWidget(self.btn_restore)
        root.addLayout(row)

        self.btn_all.clicked.connect(lambda: self._check_all(True))
        self.btn_none.clicked.connect(lambda: self._check_all(False))
        self.btn_restore.clicked.connect(self._restore)
        self._all_backups: list[dict] = []
        self.backups: list[dict] = []
        self._refresh()

    # ---------- 数据 ----------

    def _refresh(self):
        self._all_backups = list_backups(self.folders)
        self._apply_filter()

    def _apply_filter(self):
        kw = self.search.text().strip().lower()
        if kw:
            self.backups = [b for b in self._all_backups
                            if kw in os.path.basename(b["path"]).lower()
                            or kw in os.path.dirname(b["path"]).lower()]
        else:
            self.backups = list(self._all_backups)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for b in self.backups:
            self._add_row(b)
        self.table.setSortingEnabled(True)
        self._update_summary()

    def _add_row(self, b: dict):
        r = self.table.rowCount()
        self.table.insertRow(r)
        cb = QCheckBox()
        cb.bak = b  # 直接把备份信息挂在控件上，排序后也不串行
        cb.stateChanged.connect(self._update_summary)
        self.table.setCellWidget(r, 0, cb)
        # 状态列：当前 mtime 与备份一致说明已还原，否则待恢复
        same = abs(os.path.getmtime(b["path"]) - b["mtime"]) < 1.0 \
            if os.path.exists(b["path"]) else False
        st = "已一致" if same else "待恢复"
        st_item = SortItem(st, "#9aa0ac" if same else "#d93025",
                           sort_value=0 if same else 1)
        st_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(r, 1, st_item)
        self.table.setItem(r, 2, SortItem(os.path.basename(b["path"]), ""))
        self.table.setItem(r, 3, SortItem(os.path.dirname(b["path"]), "#9aa0ac"))
        self.table.setItem(r, 4, SortItem(
            f"{b['size']/1024:.0f} KB", "#9aa0ac", sort_value=b["size"]))
        self.table.setItem(r, 5, SortItem(
            datetime.fromtimestamp(b["mtime"]).strftime("%Y-%m-%d %H:%M:%S"),
            "#9aa0ac", sort_value=b["mtime"]))

    def _update_summary(self):
        n_sel = sum(1 for b in self._selected())
        self.summary.setText(
            f"共 {len(self.backups)}/{len(self._all_backups)} 个备份，已勾选 {n_sel}")
        self.btn_restore.setEnabled(n_sel > 0)

    # ---------- 操作 ----------

    def _check_all(self, on: bool):
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 0)
            if isinstance(cb, QCheckBox):
                cb.blockSignals(True)
                cb.setChecked(on)
                cb.blockSignals(False)
        self._update_summary()

    def _selected(self) -> list[dict]:
        sel = []
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 0)
            if isinstance(cb, QCheckBox) and cb.isChecked() and hasattr(cb, "bak"):
                sel.append(cb.bak)
        return sel

    def _preview(self, row: int, _col: int):
        if row < 0 or row >= len(self.backups):
            return
        b = self.backups[row]
        CompareDialog(b["path"], b["bak"], self).exec()

    def _open_folder(self):
        sel = self._selected() or (self.backups[:1] if self.backups else [])
        if not sel:
            return
        open_in_explorer(os.path.dirname(sel[0]["path"]))

    def _restore(self):
        sel = self._selected()
        if not sel:
            QMessageBox.information(self, "提示", "请先勾选要恢复的备份")
            return
        if not ConfirmDialog.ask(
                self, "恢复备份",
                f"将用 <b>{len(sel)}</b> 个备份覆盖对应的 NFO 文件。\n"
                "当前文件会先另存为 .bak.pre_restore。确认恢复？",
                detail="\n".join(os.path.basename(s["path"]) for s in sel[:15])):
            return
        ok, errs = restore_backups(sel)
        self._refresh()
        QMessageBox.information(
            self, "完成",
            f"恢复成功 {ok} 个文件" + (f"，失败 {len(errs)} 个（见日志）" if errs else ""))


# ---------- 主页面 ----------

class OrganizerPage(QWidget):
    # 后台探测 Ollama 的结果（跨线程发信号，Qt 自动排队到主线程）
    engine_probe_ready = Signal(bool, bool)      # (服务可用, 模型已就绪)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.folders: list[str] = []
        self.stats = None
        self._merge_inputs: list[QLineEdit] = []
        self._engine_state: tuple[bool, bool] | None = None
        self._build_ui()
        # 探测放到构造之后：界面先出来，探测结果回来再更新提示（见 _apply_engine_hint）
        self._start_engine_probe()
        self.state.logger.info("system", "标签整理页面已加载")

    # ---------- UI ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # 顶部工具行
        toolbar = QHBoxLayout()
        self.btn_pick = QPushButton("➕ 添加目录…")
        self.btn_pick.setToolTip(
            "可多次选择多个文件夹，一起扫描统计。\n标签词表会跨目录统一（避免同名标签在不同目录写法不一致）。")
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setEnabled(False)
        self.btn_scan = QPushButton("扫描分析")
        self.btn_scan.setEnabled(False)
        self.ai_engine = QComboBox()
        self.ai_engine.addItem("Ollama 本地 (qwen3:8b)", "ollama")
        self.ai_engine.addItem("DashScope 云 (qwen-plus)", "dashscope")
        self.ai_key = QLineEdit()
        self.ai_key.setPlaceholderText("DashScope API Key（用云引擎时需要）")
        self.ai_key.setFixedWidth(220)
        self.btn_ai = QPushButton("✨ AI 智能分析")
        self.btn_ai.setProperty("class", "primary")
        self.btn_ai.setEnabled(False)
        self.btn_report = QPushButton("📋 查看方案")
        self.btn_report.setEnabled(False)
        self.btn_restore = QPushButton("↩ 备份恢复")
        self.btn_restore.setEnabled(False)
        self.btn_enrich = QPushButton("🧩 补全稀疏标签")
        self.btn_enrich.setToolTip(
            "用本地 AI 从标题挖“类型标签”补进 <tag>，给 FC2 等个人上传（tag 又少又重复）增加区分度，"
            "便于之后按类型检索。写入前 .bak 备份。")
        self.btn_enrich.setEnabled(False)
        self.jf_check = QCheckBox("整理后同步 Jellyfin")
        self.jf_check.setChecked(self.state.config.settings.sync_jellyfin)
        self.btn_apply = QPushButton("应用整理")
        self.btn_apply.setEnabled(False)
        toolbar.addWidget(self.btn_pick)
        toolbar.addWidget(self.btn_clear)
        toolbar.addWidget(self.btn_scan)
        toolbar.addSpacing(8)
        toolbar.addWidget(QLabel("AI 引擎："))
        toolbar.addWidget(self.ai_engine)
        toolbar.addWidget(self.ai_key)
        toolbar.addWidget(self.btn_ai)
        toolbar.addWidget(self.btn_enrich)
        toolbar.addWidget(self.btn_report)
        toolbar.addWidget(self.btn_restore)
        toolbar.addStretch()
        toolbar.addWidget(self.jf_check)
        toolbar.addWidget(self.btn_apply)
        root.addLayout(toolbar)

        self.folder_label = QLabel("未选择目录")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color:#9aa0ac; padding:2px 2px;")
        root.addWidget(self.folder_label)

        tip = QLabel(
            "流程：① 选目录 → ② 扫描分析（规则自动标出演员名/片商/前缀/噪声等错误标签，"
            "并给出<b>同义/近义合并建议</b>，日文写法如 顔射/颜射、中出し/中出 会被识别）→ "
            "③ AI 智能分析（本地 qwen 无审查，处理剩余标签）→ "
            "④ 核对表格（可改每行操作与「合并目标」）→ ⑤ 应用整理（自动备份 .bak + 报告，可同步 Jellyfin）。\n"
            "整理出错可点「↩ 备份恢复」随时还原；「📋 查看方案」可随时查看合并/删除清单。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa0ac; padding:4px 2px;")
        root.addWidget(tip)

        # 表格：操作 | 合并目标 | 标签 | 次数 | 来源 | 问题标记 | AI 建议 | 样本文件
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["操作", "合并目标", "标签", "次数", "来源", "问题标记", "AI 建议", "样本文件"])
        for col, w in ((0, 110), (1, 130), (2, 170), (3, 55), (4, 65), (5, 85),
                       (6, 200), (7, 240)):
            self.table.setColumnWidth(col, w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        root.addWidget(self.table, 1)

        # 底部操作行
        bottom = QHBoxLayout()
        self.btn_del_sel = QPushButton("删除选中")
        self.btn_keep_sel = QPushButton("保留选中")
        self.btn_merge_sel = QPushButton("合并选中到…")
        self.merge_target = QLineEdit()
        self.merge_target.setPlaceholderText("合并目标 tag")
        self.merge_target.setFixedWidth(160)
        bottom.addWidget(self.btn_del_sel)
        bottom.addWidget(self.btn_keep_sel)
        bottom.addWidget(self.btn_merge_sel)
        bottom.addWidget(self.merge_target)
        bottom.addStretch()
        self.summary = QLabel("尚未扫描")
        bottom.addWidget(self.summary)
        root.addLayout(bottom)

        # 信号
        self.btn_pick.clicked.connect(self._pick_folders)
        self.btn_clear.clicked.connect(self._clear_folders)
        self.btn_scan.clicked.connect(self._scan)
        self.btn_ai.clicked.connect(self._ai_analyze)
        self.btn_enrich.clicked.connect(self._enrich_sparse)
        self.btn_report.clicked.connect(self._show_report)
        self.btn_restore.clicked.connect(self._open_restore)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_del_sel.clicked.connect(lambda: self._bulk_action(1))
        self.btn_keep_sel.clicked.connect(lambda: self._bulk_action(0))
        self.btn_merge_sel.clicked.connect(lambda: self._bulk_action(2))
        self.ai_engine.currentIndexChanged.connect(self._on_engine_changed)
        self._on_engine_changed()

    def _on_engine_changed(self):
        is_dash = self.ai_engine.currentData() == "dashscope"
        self.ai_key.setVisible(is_dash)
        if is_dash:
            self.ai_key.setText(os.environ.get("DASHSCOPE_API_KEY", ""))
            self.ai_key.setPlaceholderText("DashScope API Key（云引擎）")
            return
        self.ai_key.clear()
        self._apply_engine_hint()

    def _apply_engine_hint(self) -> None:
        """用**已缓存**的探测结果更新提示，不做任何网络 I/O。

        构造期联网是个隐蔽的体验杀手：`ollama_available()` 带 3 秒超时，
        而这台机器上 Ollama 没跑、或者被代理拦下时就是一个完整的 3 秒 ——
        「打开软件就开始卡」。所以探测放到后台线程，界面先用中性文案。
        """
        state = self._engine_state
        if state is None:
            self.ai_key.setPlaceholderText("正在检测本地 Ollama…")
        elif state[0] is False:
            self.ai_key.setPlaceholderText("⚠ Ollama 服务未运行")
        elif state[1] is False:
            self.ai_key.setPlaceholderText("⚠ 缺少 qwen3:8b（请先 ollama pull）")
        else:
            self.ai_key.setPlaceholderText("Ollama 就绪 ✓")

    def _start_engine_probe(self) -> None:
        """后台探测 Ollama 是否可用（不阻塞界面）。"""
        self.engine_probe_ready.connect(self._on_engine_probe)
        threading.Thread(target=self._probe_engine_worker, daemon=True).start()

    def _probe_engine_worker(self) -> None:
        try:
            avail = ollama_available()
            has_model = ollama_has_model() if avail else False
        except Exception:  # noqa: BLE001 - 探测失败等同于"不可用"
            avail, has_model = False, False
        self.engine_probe_ready.emit(avail, has_model)

    def _on_engine_probe(self, avail: bool, has_model: bool) -> None:
        self._engine_state = (avail, has_model)
        if self.ai_engine.currentData() != "dashscope":
            self._apply_engine_hint()

    # ---------- 扫描 ----------

    def _pick_folders(self):
        """追加式多目录选择：可多次添加，去重后一起扫描统计。"""
        d = QFileDialog.getExistingDirectory(
            self, "选择要整理标签的文件夹（可多次添加多个目录）")
        if d:
            if d not in self.folders:
                self.folders.append(d)
            self.btn_scan.setEnabled(True)
            self.btn_restore.setEnabled(True)
            self._update_folder_label()

    def _update_folder_label(self):
        n = len(self.folders)
        if not n:
            self.folder_label.setText("未选择目录")
            self.folder_label.setToolTip("")
            self.btn_clear.setEnabled(False)
            return
        shown = "、".join(self.folders[:3])
        more = f" 等 {n} 个目录" if n > 3 else ""
        self.folder_label.setText(
            f"已选 {n} 个目录：{shown}{more}（词表跨目录统一，同名标签不会各写各的）")
        self.folder_label.setToolTip("\n".join(self.folders))
        self.btn_clear.setEnabled(True)

    def _clear_folders(self):
        self.folders = []
        self.stats = None
        self.table.setRowCount(0)
        self.btn_scan.setEnabled(False)
        self.btn_ai.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_report.setEnabled(False)
        self.btn_enrich.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self._update_folder_label()
        self.summary.setText("尚未扫描")

    def _scan(self):
        if not self.folders:
            return
        self.state.logger.info("organize", f"标签扫描开始：{len(self.folders)} 个目录")

        def _do(folders, _worker=None):
            # 重活（遍历/解析全部 NFO）放后台线程，避免界面卡死
            return collect_tag_stats(
                folders,
                on_progress=lambda d, t: _worker.report(d, t, "扫描 NFO 文件…") if _worker else None,
                cancel_check=lambda: _worker.cancelled() if _worker else False,
            )

        def _on_done(stats, worker):
            if worker.isInterruptionRequested():
                self.summary.setText("扫描已取消")
                return
            self.stats = stats
            rule_detect(self.stats)
            n_groups = suggest_similar_merges(self.stats)
            # 回填上次 AI 分析缓存（同目录重开/重扫不丢 AI 决策）
            self._ai_cache_path = ai_result_path(self.folders, self.state.config.log_dir)
            n_cached = load_ai_result(self.stats, self._ai_cache_path)
            self._fill_table()
            n_rule = sum(1 for st in self.stats.tags.values() if st.rule_flag)
            n_sim = sum(1 for st in self.stats.tags.values() if st.sim_target)
            n_pending = len(self.stats.tags) - n_rule - n_sim
            self.btn_ai.setEnabled(True)
            self.btn_apply.setEnabled(True)
            self.btn_report.setEnabled(True)
            self.btn_enrich.setEnabled(True)
            self.summary.setText(
                f"{len(self.stats.nfo_files)} 个 NFO | {len(self.stats.tags)} 种标签 | "
                f"规则标错 {n_rule} | 同义/截断建议合并 {n_sim} 个({n_groups} 组) | 待 AI {n_pending}"
                + (f" | AI缓存已回填 {n_cached}" if n_cached else ""))
            self.state.logger.info(
                "organize", f"扫描完成：{len(self.stats.nfo_files)} NFO，{len(self.stats.tags)} 种标签",
                result=f"规则标记 {n_rule}，同义/截断合并建议 {n_sim}，待AI {n_pending}，AI缓存 {n_cached}")

        def _on_error(e, worker):
            self.state.logger.error("organize", f"扫描失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "扫描失败", str(e))

        run_with_progress(self, "标签扫描", "扫描 NFO 文件…", _do,
                          args=(self.folders,), on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=0)

    # ---------- 表格 ----------

    def _fill_table(self):
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self._merge_inputs = []
        try:
            self.table.setRowCount(0)
            for name, st in self.stats.tags.items():
                row = self.table.rowCount()
                self.table.insertRow(row)
                # 操作下拉（附颜色 + 隐藏排序项：可点列头按 保留/删除/合并 排序）
                combo = QComboBox()
                combo.addItems(["保留", "删除", "合并到"])
                combo.setProperty("row", row)
                combo.setCurrentIndex(self._default_action(st))
                self._paint_combo(combo)
                combo.currentIndexChanged.connect(
                    lambda idx, r=row: self._on_action_changed(r, idx))
                self.table.setCellWidget(row, 0, combo)
                act_item = SortItem("", sort_value=self._default_action(st))
                self.table.setItem(row, 0, act_item)
                # 合并目标（可编辑）
                edit = QLineEdit()
                edit.setPlaceholderText("合并到的标签名")
                pre = st.user_target or st.ai_target or st.sim_target or ""
                edit.setText(pre)
                edit.setEnabled(combo.currentIndex() == 2)
                self._merge_inputs.append(edit)
                self.table.setCellWidget(row, 1, edit)
                # 标签
                self.table.setItem(row, 2, SortItem(name, "", sort_value=name))
                # 次数
                cnt = SortItem(str(st.count), "#ffd77a", sort_value=st.count)
                cnt.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 3, cnt)
                # 来源
                src = ("tag" if st.in_tag else "") + ("+genre" if st.in_genre else "")
                self.table.setItem(row, 4, SortItem(src, "#9aa0ac", sort_value=src))
                # 问题标记（近义/截断）
                flag = st.rule_flag or st.sim_flag
                it = SortItem(flag, FLAG_COLOR.get(flag, "#9aa0ac"))
                it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 5, it)
                # AI 建议
                ai = self._ai_text(st)
                color = AI_COLOR.get(st.ai_action,
                                     FLAG_COLOR.get(st.sim_flag, "#9aa0ac"))
                self.table.setItem(row, 6, SortItem(ai, color))
                # 样本
                sample = os.path.basename(st.samples[0]) if st.samples else ""
                self.table.setItem(row, 7, SortItem(sample, "#8a8f99"))
        finally:
            self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        self._update_summary()

    @staticmethod
    def _paint_combo(combo: QComboBox):
        """按当前选项给下拉框文字/背景配色（保留绿/删除红/合并琥珀）。"""
        idx = combo.currentIndex()
        fg = ACTION_COLOR.get(idx, "#222")
        bg = ACTION_BG.get(idx, "#fff")
        combo.setStyleSheet(
            f"QComboBox {{ color:{fg}; background:{bg}; border:1px solid {fg}55;"
            f" border-radius:4px; padding:2px 6px; font-weight:600; }}")

    @staticmethod
    def _default_action(st: TagStat) -> int:
        if st.user_action == "remove":
            return 1
        if st.user_action == "merge":
            return 2
        if st.user_action == "keep":
            return 0
        if st.rule_flag:
            return 1
        if st.ai_action == "remove":
            return 1
        if st.ai_action == "merge":
            return 2
        if st.sim_target:
            return 2
        return 0

    @staticmethod
    def _ai_text(st: TagStat) -> str:
        if st.rule_flag:
            return f"规则: {st.rule_flag}"
        if st.ai_action == "remove":
            return f"AI删除: {st.ai_reason[:18]}"
        if st.ai_action == "merge":
            return f"AI合并→{st.ai_target}: {st.ai_reason[:12]}"
        if st.sim_target:
            kind = st.sim_flag or "近义"
            return f"规则{kind}→{st.sim_target}"
        if st.ai_action == "keep":
            return "AI保留"
        return "待分析"

    def _on_action_changed(self, row: int, idx: int):
        name_item = self.table.item(row, 2)
        if name_item is None:
            return
        name = name_item.text()
        st = self.stats.tags.get(name)
        if st is None:
            return
        edit = self._merge_inputs[row] if row < len(self._merge_inputs) else None
        if idx == 0:
            st.user_action = "keep"
            st.user_target = ""
            if edit:
                edit.setEnabled(False)
        elif idx == 1:
            st.user_action = "remove"
            st.user_target = ""
            if edit:
                edit.setEnabled(False)
        else:
            st.user_action = "merge"
            if edit:
                edit.setEnabled(True)
                txt = edit.text().strip()
                if txt:
                    st.user_target = txt
                elif st.ai_target or st.sim_target:
                    st.user_target = st.ai_target or st.sim_target
                    edit.setText(st.user_target)
        # 同步排序项 + 下拉配色
        sort_item = self.table.item(row, 0)
        if sort_item is not None:
            sort_item.setData(Qt.UserRole, str(idx))
            sort_item._sort_value = idx
        combo = self.table.cellWidget(row, 0)
        if isinstance(combo, QComboBox):
            self._paint_combo(combo)
        self._update_summary()

    def _update_summary(self):
        if self.stats is None:
            return
        mapping = effective_mapping(self.stats)
        n_rm = sum(1 for r in mapping.values() if r["action"] == "remove")
        n_mg = sum(1 for r in mapping.values() if r["action"] == "merge")
        self.summary.setText(
            f"将删除 {n_rm} 种标签 | 将合并 {n_mg} 组 | 保留 {len(self.stats.tags) - n_rm - n_mg} 种")

    # ---------- AI 分析 ----------

    def _ai_analyze(self):
        if self.stats is None:
            return
        engine = self.ai_engine.currentData()
        api_key = self.ai_key.text().strip()
        if engine == "dashscope" and not api_key:
            QMessageBox.information(self, "提示", "使用云引擎需要填写 DashScope API Key")
            return
        if engine == "ollama":
            if not ollama_available():
                QMessageBox.warning(self, "Ollama 不可用",
                                    "未检测到 Ollama 服务。\n请先启动 Ollama（ollama serve）后重试。")
                return
            if not ollama_has_model():
                QMessageBox.warning(
                    self, "缺少模型",
                    "未找到 qwen3:8b。\n请先运行：ollama pull qwen3:8b\n"
                    "（12G 显存可流畅运行，约 5GB 下载）")
                return
        n_pending = sum(1 for st in self.stats.tags.values()
                        if not st.rule_flag and not st.sim_target)
        if n_pending == 0:
            QMessageBox.information(self, "提示",
                                    "没有需要 AI 分析的标签（规则与同义检测已覆盖全部）")
            return
        est = max(1, int(n_pending / 120 * 25))
        if not ConfirmDialog.ask(
                self, "AI 智能分析",
                f"将对 <b>{n_pending}</b> 个标签进行 AI 分析（本地 Ollama，无审查、不上传数据）。\n"
                f"预计 {est} 秒左右。确认开始？",
                detail=f"引擎：{self.ai_engine.currentText()}"):
            return

        def _do(stats, eng, key, _worker=None):
            # 重活（逐批调 Ollama）放后台线程；期间 GUI 仍响应、可取消
            diag = ai_classify(stats, engine=eng, api_key=key,
                               on_progress=lambda d, t, name: _worker.report(d, t, f"[{d}/{t}] {name}") if _worker else None,
                               cancel_check=lambda: _worker.cancelled() if _worker else False)
            return stats, diag

        def _on_done(result, worker):
            stats, diag = result
            # 保存 AI 结果缓存（同目录重开/重扫自动回填，无需反复重新分析）
            try:
                path = getattr(self, "_ai_cache_path", None) or ai_result_path(
                    self.folders, self.state.config.log_dir)
                self._ai_cache_path = path
                save_ai_result(self.stats, path)
            except OSError as e:
                self.state.logger.error("organize", f"AI 缓存保存失败: {e}")
            self._fill_table()
            n_rm = sum(1 for st in self.stats.tags.values() if st.ai_action == "remove")
            n_mg = sum(1 for st in self.stats.tags.values() if st.ai_action == "merge")
            self.state.logger.info("organize", f"AI 分析完成：删除 {n_rm}，合并 {n_mg}")
            # 若存在失败批次，先提示诊断（如 Ollama 未响应/模型未加载/请求超时），再弹报告
            if diag and diag.get("failed"):
                QMessageBox.warning(
                    self, "AI 分析部分失败",
                    f"共 {diag['batches']} 批中，有 <b>{diag['failed']}</b> 批未能从 AI 得到结果"
                    "（该批标签保持「待分析」，可在下方表格手动处理）。\n\n"
                    "常见原因：Ollama 服务未响应 / 模型未加载 / 单批请求超时。\n"
                    "可检查 Ollama 是否运行、模型是否已 pull qwen3:8b，然后重试。")
            # 直接弹出变更报告，让用户明确看到合并/删除清单
            self._show_report()

        def _on_error(e, worker):
            self.state.logger.error("organize", f"AI 分析失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "AI 分析失败", str(e))

        run_with_progress(self, "AI 标签分析", "AI 分析中…", _do,
                          args=(self.stats, engine, api_key), on_done=_on_done,
                          on_error=_on_error, cancellable=True, initial_total=n_pending)

    def _enrich_sparse(self):
        """用本地 AI 从标题挖类型标签，补进 FC2 等稀疏 tag 的 <tag>（.bak 备份）。

        三阶段全部走后台线程（候选收集 / AI 挖标签 / 写后重扫），任一阶段异常均弹窗报错、
        不崩进程；期间 GUI 保持响应、可取消。
        """
        from core.nfo import NfoFile
        if not self.folders:
            QMessageBox.information(self, "提示", "请先选择目录")
            return
        if not ollama_available():
            QMessageBox.warning(self, "Ollama 不可用",
                                "未检测到 Ollama 服务。\n请先启动 Ollama 后重试。")
            return
        if not ollama_has_model():
            QMessageBox.warning(self, "缺少模型",
                                "未找到 qwen3:8b。\n请先运行：ollama pull qwen3:8b")
            return

        # ---- 阶段 A：后台收集稀疏标签候选（遍历目录较重）----
        def _collect(_worker=None):
            THRESHOLD = 5
            cand = []
            for d in self.folders:
                if not os.path.isdir(d):
                    continue
                for root, _dirs, files in os.walk(d):
                    if _worker and _worker.cancelled():
                        return cand
                    for fn in files:
                        if not fn.lower().endswith(".nfo") or fn.lower().endswith(".bak"):
                            continue
                        p = os.path.join(root, fn)
                        try:
                            nfo = NfoFile(p)
                            if not nfo.is_valid:
                                continue
                        except Exception:  # noqa: BLE001
                            continue
                        markers = set(nfo.markers)
                        if len(markers) >= THRESHOLD:
                            continue  # 标签已不少，跳过
                        title = (nfo.title or "").strip()
                        name = (nfo.number or fn).strip()
                        if not title and not name:
                            continue
                        cand.append({"path": p, "name": name, "title": title,
                                     "tags": markers})
            return cand

        def _on_collected(cand, worker):
            if worker.isInterruptionRequested():
                return
            if not cand:
                QMessageBox.information(
                    self, "提示", "所选目录里没有「稀疏标签」的 NFO（tag+genre < 5 个）")
                return
            if not ConfirmDialog.ask(
                    self, "AI 补全稀疏标签",
                    f"将用本地 qwen3:8b 从 <b>{len(cand)}</b> 个稀疏标签视频的标题里"
                    "挖「类型标签」，并<b>优先复用本库现有标签</b>（语义匹配接轨），"
                    "为它们增加检索区分度。\n"
                    "先出建议给你核对；确认后才写入 <tag>（每个文件 .bak 备份）。确认开始？",
                    detail="示例将覆盖 FC2/个人拍摄等 tag 很少的片子"):
                return
            # 现有标签词表（内容类、按出现频率取前 ~150 个，供 AI 复用，避免再造一套新词）
            vocab = []
            if self.stats is not None:
                from core.tag_organizer import _tag_write_ok
                scored = [(n, st.count) for n, st in self.stats.tags.items()
                          if _tag_write_ok(n)]
                scored.sort(key=lambda x: -x[1])
                vocab = [n for n, _ in scored[:150]]
            self._enrich_run_ai(cand, vocab)

        def _on_collected_err(e, worker):
            import traceback
            self.state.logger.error("organize", f"收集稀疏候选异常: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "收集失败", f"扫描稀疏标签时出错：\n{e}")

        run_with_progress(self, "扫描稀疏标签", "查找稀疏标签 NFO…", _collect,
                          on_done=_on_collected, on_error=_on_collected_err,
                          cancellable=True, initial_total=0)

    def _enrich_run_ai(self, cand, vocab):
        """阶段 B：后台用 AI 从标题挖类型标签；完成后弹确认写入对话框。

        支持暂停/继续（批间阻塞等待）、取消；进度条实时显示 已处理/批数/新增标签。
        """
        def _do(cand, vocab, _worker=None):
            return ai_extract_type_tags(
                cand, vocab=vocab,
                on_progress=lambda d, t, lbl: _worker.report(d, t, lbl) if _worker else None,
                cancel_check=lambda: _worker.cancelled() if _worker else False,
                pause_check=lambda: _worker.wait_if_paused() if _worker else None)

        def _on_done(proposed, worker):
            if worker.isInterruptionRequested():
                return
            diag = getattr(proposed, "stats", None)
            n_files = len(proposed)
            # 诊断摘要：即使没产出也要让用户知道卡在哪一步
            if diag and not n_files:
                sample = diag.get("ai_sample") or ""
                sample_txt = (f"\n\nAI 输出样本（前 400 字）：\n{sample}"
                              if sample else "")
                QMessageBox.information(
                    self, "没有可补全的标签",
                    f"AI 跑完 <b>{diag['batches']}</b> 批、<b>{diag['matched']}</b> 条标题被识别，"
                    f"但<b>最终 0 个标签</b>通过过滤（新挖 {diag['new_tags']} 个，全部被过滤）。\n\n"
                    f"诊断：请求失败 {diag['req_err']} 批、HTTP 异常 {diag['http_err']} 批、"
                    f"重试后仍无法解析 {diag['parse_fail']} 批、"
                    f"标题未匹配 {diag['no_match']} 条。\n\n"
                    "若「无法解析」>0，说明 AI 输出被截断或非 JSON——可把上方 AI 输出样本发我；"
                    "若「标题未匹配」占大头，说明 AI 返回的标题与输入有出入，可重试。"
                    f"{sample_txt}")
                return
            if not proposed:
                QMessageBox.information(self, "提示", "AI 未能从标题挖到可用的类型标签。")
                return
            # 用可读的「建议审核」表格替代原来不可读的纯文本 ConfirmDialog：
            # 逐条核对 文件维度（可编辑建议/勾选）+ 标签维度（全局采用/剔除），
            # 勾选后点「写入勾选」→ 拿 checked_pairs() 落盘（.bak 备份）。
            from ui.pages.tag_enrich import TagEnrichDialog
            dlg = TagEnrichDialog(cand, proposed, diag, self)
            if dlg.exec() != QDialog.Accepted:
                return
            pairs = dlg.accepted_pairs()
            if not pairs:
                QMessageBox.information(self, "提示", "未勾选任何要写入的标签。")
                return
            # 把 (path, suggested) 转成 proposed 字典（同路径多个文件，保序合并）
            merged: dict[str, list[str]] = {}
            for p, ts in pairs:
                merged.setdefault(p, [])
                for t in ts:
                    if t not in merged[p]:
                        merged[p].append(t)
            self._enrich_apply(merged)

        def _on_error(e, worker):
            import traceback
            self.state.logger.error("organize", f"补全稀疏标签异常: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "补全失败", f"AI 处理出错：\n{e}\n\n详见日志。")

        run_with_progress(self, "补全稀疏标签", "AI 从标题挖标签（优先复用现有标签）…",
                          _do, args=(cand, vocab), on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=len(cand),
                          pausable=True)

    def _enrich_apply(self, proposed):
        """阶段 C：写入 NFO（.bak 备份）+ 后台重扫刷新表格。"""
        try:
            ok, errs = apply_appended_tags(list(proposed.keys()), proposed)
        except Exception as e:  # noqa: BLE001
            import traceback
            QMessageBox.critical(self, "写入失败", f"写入 NFO 时出错：\n{e}\n\n详见日志。")
            self.state.logger.error("organize", f"补全写入异常: {e}\n{traceback.format_exc()}")
            return
        n_tags = sum(len(v) for v in proposed.values())
        self.state.logger.info(
            "organize", f"补全稀疏标签：成功 {ok} 个，失败 {len(errs)}",
            result=f"新增标签 {n_tags}")
        # 刷新统计表（重扫较重）——放后台线程，避免再次卡死
        def _rescan(_worker=None):
            stats = collect_tag_stats(self.folders)
            rule_detect(stats)
            suggest_similar_merges(stats)
            return stats

        def _on_rescan(stats, worker):
            if worker.isInterruptionRequested():
                return
            self.stats = stats
            self._fill_table()
            QMessageBox.information(
                self, "完成",
                f"已为 {ok} 个视频补全标签" + (f"，{len(errs)} 个失败（见日志）" if errs else "")
                + "\n之后可在 Jellyfin 按这些类型标签筛选/检索。")

        def _on_rescan_err(e, worker):
            import traceback
            self.state.logger.error("organize", f"刷新表格异常: {e}\n{traceback.format_exc()}")
            QMessageBox.information(
                self, "完成",
                f"已为 {ok} 个视频补全标签" + (f"，{len(errs)} 个失败（见日志）" if errs else "")
                + "\n（表格刷新失败，详见日志；可手动重新扫描）")

        run_with_progress(self, "刷新统计", "重新统计标签…", _rescan,
                          on_done=_on_rescan, on_error=_on_rescan_err,
                          cancellable=False, initial_total=0)

    def _show_report(self):
        if self.stats is None:
            return
        report = build_report(self.stats, effective_mapping(self.stats))
        ReportDialog(report, self).exec()

    # ---------- 批量修改 ----------

    def _bulk_action(self, action: int):
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要修改的行")
            return
        t = self.merge_target.text().strip()
        for r in rows:
            combo = self.table.cellWidget(r, 0)
            if isinstance(combo, QComboBox):
                combo.setCurrentIndex(action)
            st_name = self.table.item(r, 2).text()
            st = self.stats.tags.get(st_name)
            if st:
                if action == 0:
                    st.user_action = "keep"
                elif action == 1:
                    st.user_action = "remove"
                else:
                    st.user_action = "merge"
                    if t:
                        st.user_target = t
            edit = self._merge_inputs[r] if r < len(self._merge_inputs) else None
            if edit:
                edit.setEnabled(action == 2)
                if action == 2 and t:
                    edit.setText(t)
            # 同步排序项 + 配色
            sort_item = self.table.item(r, 0)
            if sort_item is not None:
                sort_item._sort_value = action
                sort_item.setData(Qt.UserRole, str(action))
            combo = self.table.cellWidget(r, 0)
            if isinstance(combo, QComboBox):
                self._paint_combo(combo)
        self._update_summary()

    # ---------- 备份恢复 ----------

    def _open_restore(self):
        RestoreDialog(self.folders, self).exec()

    # ---------- 应用 ----------

    def _apply(self):
        if self.stats is None:
            return
        mapping = effective_mapping(self.stats)
        if not mapping:
            QMessageBox.information(self, "提示", "当前没有需要执行的操作（没有删除/合并项）")
            return
        n_rm = sum(1 for r in mapping.values() if r["action"] == "remove")
        n_mg = sum(1 for r in mapping.values() if r["action"] == "merge")
        merge_desc = []
        for old, rule in mapping.items():
            if rule["action"] == "merge":
                merge_desc.append(f"  {old} → {rule['target']}")
        detail = "\n".join(merge_desc[:25]) + ("\n…" if len(merge_desc) > 25 else "")
        if not ConfirmDialog.ask(
                self, "应用标签整理",
                f"将对 {len(self.stats.nfo_files)} 个 NFO 执行：\n"
                f"• 删除 {n_rm} 种标签\n• 合并 {n_mg} 组标签\n\n"
                "每个文件修改前生成 .bak 备份，整理后自动保存报告。确认执行？",
                detail=detail):
            return
        backup = True

        # 重活（逐文件解析+改写+备份）放后台线程，主线程不冻结、可取消。
        def _do(nfo_files, mapping, sync, _worker=None):
            ok, errs = apply_organize(
                nfo_files, mapping, backup=backup,
                on_progress=lambda d, t: _worker.report(d, t, "整理标签…") if _worker else None,
                cancel_check=lambda: _worker.cancelled() if _worker else False,
            )
            return ok, errs

        def _on_done(result, worker):
            if worker.isInterruptionRequested():
                self.summary.setText("应用已取消")
                return
            ok, errs = result
            self.state.logger.info(
                "organize", f"标签整理完成：成功 {ok}，失败 {len(errs)}",
                result=f"删除 {n_rm}，合并 {n_mg}")
            for p, e in errs[:5]:
                self.state.logger.error("organize", e, path=p)
            # 自动保存报告
            report_path = ""
            try:
                report = build_report(self.stats, mapping)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                report_path = os.path.join(self.state.config.log_dir,
                                           f"标签整理报告_{ts}.html")
                save_report_html(report_path, report)
            except OSError as e:
                self.state.logger.error("organize", f"报告保存失败: {e}")
            msg = f"标签整理完成：成功 {ok} 个 NFO，失败 {len(errs)} 个\n"
            if self.jf_check.isChecked():
                msg += "\n正在后台同步到 Jellyfin…（完成后在日志可见）"
            if report_path:
                msg += f"\n变更报告已保存：\n{report_path}\n\n出错可到「↩ 备份恢复」还原"
            elif errs:
                msg += "\n失败详情见日志"
            QMessageBox.information(self, "完成", msg)
            # 异步触发 Jellyfin 同步（后台线程跑，完成后记日志，不阻塞完成弹窗）
            if self.jf_check.isChecked() and ok:
                self._sync_jellyfin()

        def _on_error(e, worker):
            self.state.logger.error("organize", f"应用整理失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "应用失败", str(e))

        run_with_progress(self, "应用标签整理", "整理标签…", _do,
                          args=(self.stats.nfo_files, mapping, self.jf_check.isChecked()),
                          on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=len(self.stats.nfo_files))

    def _sync_jellyfin(self) -> int:
        """把整理后的 NFO 同步到 Jellyfin（后台线程，逐目录挑最优，可取消）。"""
        from core.jellyfin import JellyfinClient
        from core.nfo import NfoFile
        s = self.state.config.settings
        client = JellyfinClient(s.jellyfin_server, s.jellyfin_api_key)
        if not client.configured:
            QMessageBox.warning(self, "未配置 Jellyfin",
                                "未配置 Jellyfin 服务器/API Key，跳过同步。\n"
                                "可在「设置与工具」页配置。")
            return 0
        # 同一目录可能存在多个 NFO（如 movie.nfo + 主名.nfo / 重复刮削文件）。
        # Jellyfin 每部影片通常对应一个条目，重复同步会造成"后写覆盖"，甚至被
        # 空的重复 NFO 把标签整体清空。因此：每个目录只挑"信息最完整"的一个同步，
        # 且空 NFO（无 tag 无 genre）一律不推送。
        best: dict[str, tuple] = {}
        for p in self.stats.nfo_files:
            nfo = NfoFile(p)
            if not nfo.is_valid:
                continue
            folder = os.path.dirname(p)
            score = len(nfo.tags) + len(nfo.genres) + (1 if nfo.number else 0)
            cur = best.get(folder)
            if cur is None or score > cur[2]:
                best[folder] = (p, nfo, score)
        items = [v for v in best.values() if (v[1].tags or v[1].genres)]

        # 同步是同步网络请求（每次 find_item + get item + POST，timeout 15-20s），放后台线程。
        def _do(items, _worker=None):
            ok_n = 0
            for i, (_p, nfo, _sc) in enumerate(items):
                if _worker and _worker.cancelled():
                    break
                if _worker:
                    _worker.report(i + 1, len(items), "同步到 Jellyfin…")
                r_ok, _msg = client.sync_nfo(nfo.number, _p, nfo.tags, nfo.genres)
                if r_ok:
                    ok_n += 1
            return ok_n

        def _on_done(ok_n, _worker):
            self.state.logger.info("organize", f"Jellyfin 同步完成：成功 {ok_n} 个")

        def _on_error(e, _worker):
            self.state.logger.error("organize", f"Jellyfin 同步失败: {getattr(e, '_tb', '')}")

        run_with_progress(self, "Jellyfin 同步", "同步到 Jellyfin…", _do,
                          args=(items,), on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=len(items))
        return 0
