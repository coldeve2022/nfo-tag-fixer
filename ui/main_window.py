# -*- coding: utf-8 -*-
"""主窗口：顶部工具栏 + 页面 Tab + 底部实时日志。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QTabWidget, QToolBar,
    QVBoxLayout, QWidget,
)

from ui.pages.archive import ArchivePage
from ui.pages.finder import FinderPage
from ui.pages.fixer import FixerPage
from ui.pages.nfo_repair import RepairPage
from ui.pages.organizer import OrganizerPage
from ui.pages.settings import SettingsPage
from ui.widgets import LogPanel
from version import APP_NAME, HOMEPAGE, __author__, __license__, __version__


class MainWindow(QMainWindow):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1440, 880)
        self._build_ui()
        self._build_toolbar()
        self._build_menu()
        self.state.logger.set_sink(self._log_sink)
        self.state.logger.info("system", "应用启动完成")

    # ---------- UI ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        splitter = QSplitter(Qt.Vertical)

        self.tabs = QTabWidget()
        self.fixer_page = FixerPage(self.state)
        self.finder_page = FinderPage(self.state)
        self.archive_page = ArchivePage(self.state)
        self.organizer_page = OrganizerPage(self.state)
        self.repair_page = RepairPage(self.state)
        self.settings_page = SettingsPage(self.state)
        self.tabs.addTab(self.fixer_page, "标签修正")
        self.tabs.addTab(self.finder_page, "破解找回")
        self.tabs.addTab(self.archive_page, "破解档案")
        self.tabs.addTab(self.organizer_page, "标签整理")
        self.tabs.addTab(self.repair_page, "NFO 修复")
        self.tabs.addTab(self.settings_page, "设置与工具")
        splitter.addWidget(self.tabs)

        # 底部日志
        self.log_panel = LogPanel()
        self.log_panel.setPlaceholderText("操作日志…")
        self.log_panel.setMaximumHeight(180)
        splitter.addWidget(self.log_panel)
        splitter.setSizes([700, 160])
        layout.addWidget(splitter)

        self.statusBar().showMessage("就绪 | 拖拽 NFO / 文件夹 到左栏列表即可导入")

    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)

        # 说明：Ctrl+I 已被 CheckTable（各页文件表格）占用为「反选」，是表格核心交互约定。
        # 工具栏的「导入文件夹」用 Ctrl+O，避免焦点在表格时两处快捷键冲突。
        act_import = QAction("导入文件夹", self)
        act_import.setShortcut(QKeySequence("Ctrl+O"))
        act_import.triggered.connect(self._import_folder)
        tb.addAction(act_import)

        act_analyze = QAction("标签统计", self)
        act_analyze.setShortcut(QKeySequence("Ctrl+T"))
        act_analyze.triggered.connect(self.fixer_page.analyze_tags)
        tb.addAction(act_analyze)

        act_preview = QAction("预览变更", self)
        act_preview.setShortcut(QKeySequence("Ctrl+P"))
        act_preview.triggered.connect(self.fixer_page.preview_changes)
        tb.addAction(act_preview)

        act_apply = QAction("应用修改", self)
        act_apply.setShortcut(QKeySequence("Ctrl+Enter"))
        act_apply.triggered.connect(self.fixer_page.apply_changes)
        tb.addAction(act_apply)

        act_rollback = QAction("回滚", self)
        act_rollback.setShortcut(QKeySequence("Ctrl+R"))
        act_rollback.triggered.connect(self.fixer_page.rollback_checked)
        tb.addAction(act_rollback)

        # 「标签统计/预览/应用/回滚」语义只在「标签修正」页成立；切换 Tab 时禁用，
        # 避免「在整理页点预览却误解为修正页动作」的断层。导入文件夹对 fixer/finder 均可用。
        self.toolbar_actions = [act_analyze, act_preview, act_apply, act_rollback]
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int):
        # 仅「标签修正」页（index 0）启用修正专用动作；其它页会误触，统一禁用。
        is_fixer = (index == 0)
        for act in self.toolbar_actions:
            act.setEnabled(is_fixer)

    def _import_folder(self):
        # 导入文件夹：找回页用其 _scan_dir，其余页统一落到修正页的导入对话框。
        if self.tabs.currentIndex() == 1:
            self.finder_page._scan_dir()
        else:
            self.fixer_page._import_dir_dialog()

    def _build_menu(self):
        m_file = self.menuBar().addMenu("文件")
        act_quit = QAction("退出", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_help = self.menuBar().addMenu("帮助")
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)

    def _show_about(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(
            self, f"关于 {APP_NAME}",
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "功能：<br>"
            "· 拖拽导入 NFO（嵌套扫描）→ 标签统计分组 → 映射预览 → 应用<br>"
            "· 破解找回助手：多线索打分 + 人工勾选确认 + 永久档案<br>"
            "· 标签整理（本地 AI 同义/截断合并）／NFO 修复／编码统一／健康检查<br>"
            "· 改前备份 .bak，支持一键回滚；全程日志留痕<br><br>"
            f"许可证：{__license__}　作者：{__author__}<br>"
            f"项目主页：<a href='{HOMEPAGE}'>{HOMEPAGE}</a><br><br>"
            "安全提示：所有修改前均会生成备份；删除类操作仅移除列表或进回收站。")

    # ---------- 日志 ----------

    def _log_sink(self, html: str):
        self.log_panel.append_html(html)

    # ---------- 关闭 ----------

    def closeEvent(self, event):
        # 关闭时中断仍在跑的 AI/扫描后台线程（尽快释放）。
        # 注意：Ollama 驻留模型的卸载统一交由 main.py 的 aboutToQuit -> _cleanup_on_quit
        # 在应用退出的最后一刻执行（幂等），这里不重复调 _unload_ollama，避免双重卸载。
        try:
            from ui.worker import Worker
            Worker.shutdown_all()
        except Exception:  # noqa: BLE001
            pass
        self.state.logger.info("system", "应用退出")
        self.state.close()
        super().closeEvent(event)
