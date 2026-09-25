# -*- coding: utf-8 -*-
"""设置页面：通用设置 + 规则模板 + 提效工具（编码统一 / 健康检查 / 字段替换）。"""

from __future__ import annotations

import os
import re
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from core.scanner import find_nfo_files, find_video_for, is_video, scan_paths
from ui.dialogs import ConfirmDialog
from ui.styles import qss, set_theme
from ui.widgets import open_in_explorer
from ui.worker import run_with_progress


class SettingsPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._build_ui()
        self.state.logger.info("system", "设置页面已加载")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # ---- 通用设置 ----
        gb = QGroupBox("通用设置")
        v = QVBoxLayout(gb)
        self.backup_check = QCheckBox("改前备份 (.bak)")
        self.backup_check.setChecked(self.state.config.settings.backup_enabled)
        self.recursive_check = QCheckBox("嵌套扫描子文件夹")
        self.recursive_check.setChecked(self.state.config.settings.recursive_scan)
        v.addWidget(self.backup_check)
        v.addWidget(self.recursive_check)

        row = QHBoxLayout()
        row.addWidget(QLabel("ffprobe 路径："))
        self.ffprobe_edit = QLineEdit(self.state.config.settings.ffprobe_path)
        self.ffprobe_edit.setPlaceholderText("留空则自动查找系统 PATH")
        row.addWidget(self.ffprobe_edit, 1)
        btn_detect = QPushButton("自动检测")
        btn_detect.clicked.connect(self._detect_ffprobe)
        row.addWidget(btn_detect)
        v.addLayout(row)

        self.ffprobe_check = QCheckBox(
            "破解找回时启用 ffprobe 探测（弱线索：时长/分辨率差异，较慢，可选）")
        self.ffprobe_check.setChecked(self.state.config.settings.enable_ffprobe)
        v.addWidget(self.ffprobe_check)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("档案库路径："))
        self.db_edit = QLineEdit(self.state.config.archive_db_path)
        row2.addWidget(self.db_edit, 1)
        btn_db = QPushButton("选择")
        btn_db.clicked.connect(self._pick_db)
        row2.addWidget(btn_db)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("主题："))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("暗色（推荐）", "dark")
        self.theme_combo.addItem("浅色", "light")
        idx = self.theme_combo.findData(self.state.config.settings.theme)
        self.theme_combo.setCurrentIndex(max(idx, 0))
        row3.addWidget(self.theme_combo)
        row3.addStretch()
        v.addLayout(row3)
        root.addWidget(gb)

        # ---- Jellyfin 同步 ----
        gj = QGroupBox("Jellyfin 同步（保留实时监控也能同步标签）")
        vj = QVBoxLayout(gj)
        tipj = QLabel(
            "应用修改后通过 Jellyfin API 直接更新数据库中的标签/类型，"
            "数据库旧值（如残留的「有码」）被清除后，实时监控怎么扫描都不会写回。\n"
            "获取 API Key：Jellyfin → 设置 → 高级 → API 密钥 → 新建。")
        tipj.setWordWrap(True)
        tipj.setStyleSheet("color:#9aa0ac;")
        vj.addWidget(tipj)
        row_srv = QHBoxLayout()
        row_srv.addWidget(QLabel("服务器地址："))
        self.jf_server_edit = QLineEdit(self.state.config.settings.jellyfin_server)
        self.jf_server_edit.setPlaceholderText("http://192.168.1.10:8096")
        row_srv.addWidget(self.jf_server_edit, 1)
        vj.addLayout(row_srv)
        row_key = QHBoxLayout()
        row_key.addWidget(QLabel("API Key："))
        self.jf_key_edit = QLineEdit(self.state.config.settings.jellyfin_api_key)
        self.jf_key_edit.setEchoMode(QLineEdit.Password)
        row_key.addWidget(self.jf_key_edit, 1)
        btn_test = QPushButton("测试连接")
        btn_test.clicked.connect(self._test_jellyfin)
        row_key.addWidget(btn_test)
        vj.addLayout(row_key)
        self.jf_sync_check = QCheckBox("应用修改后自动同步到 Jellyfin")
        self.jf_sync_check.setChecked(self.state.config.settings.sync_jellyfin)
        vj.addWidget(self.jf_sync_check)
        self.btn_jf_batch = QPushButton("追溯同步：批量同步已处理的文件…")
        self.btn_jf_batch.setToolTip(
            "扫描所选文件夹，把所有「已处理干净（不含'有码'标记）」的 NFO 一次性同步到 Jellyfin，"
            "清掉数据库里残留的旧标签（如'有码'）")
        self.btn_jf_batch.clicked.connect(self._batch_sync_jellyfin)
        vj.addWidget(self.btn_jf_batch)
        row_refresh = QHBoxLayout()
        self.btn_jf_refresh = QPushButton("🔄 触发全库刷新（清除已删标签）")
        self.btn_jf_refresh.setToolTip(
            "让 Jellyfin 重新扫描全库、重读所有 NFO，从而清掉 NFO 里早已删除的旧标签"
            "（如「片商:xx」「发行:xx」），并更新筛选/侧栏。扫描需几分钟，期间建议先别改文件。")
        self.btn_jf_refresh.clicked.connect(self._refresh_jellyfin)
        row_refresh.addWidget(self.btn_jf_refresh)
        row_refresh.addStretch()
        vj.addLayout(row_refresh)
        self.jf_status = QLabel("")
        self.jf_status.setStyleSheet("color:#9aa0ac;")
        vj.addWidget(self.jf_status)
        root.addWidget(gj)

        # ---- 规则模板 ----
        gb2 = QGroupBox("规则模板（按刮削源预设）")
        v2 = QVBoxLayout(gb2)
        tip = QLabel("一键套用常用映射规则（追加到当前规则列表）：")
        tip.setWordWrap(True)
        v2.addWidget(tip)
        row_t = QHBoxLayout()
        btn_tpl = QPushButton("套用预置模板")
        btn_tpl.clicked.connect(self._apply_template)
        row_t.addWidget(btn_tpl)
        row_t.addStretch()
        v2.addLayout(row_t)
        root.addWidget(gb2)

        # ---- 提效工具 ----
        gb3 = QGroupBox("提效工具")
        v3 = QVBoxLayout(gb3)
        row_tools = QHBoxLayout()
        self.btn_encoding = QPushButton("编码统一 (GBK→UTF-8)")
        self.btn_health = QPushButton("健康检查")
        self.btn_field = QPushButton("字段批量替换")
        self.btn_env = QPushButton("环境自检")
        self.btn_env.setToolTip(
            "检查数据目录可写性、ffprobe/send2trash/requests 是否可用，"
            "以及本机真实的硬件编码能力。提 Issue 时请附上这份输出。")
        row_tools.addWidget(self.btn_encoding)
        row_tools.addWidget(self.btn_health)
        row_tools.addWidget(self.btn_field)
        row_tools.addWidget(self.btn_env)
        v3.addLayout(row_tools)
        root.addWidget(gb3)

        # ---- 数据目录（只读展示） ----
        gb4 = QGroupBox("数据位置")
        v4 = QVBoxLayout(gb4)
        where = QLabel(
            f"数据目录：<code>{self.state.app_dir}</code>（{self.state.data_origin}）<br>"
            f"档案库：<code>{self.state.config.archive_db_path}</code><br>"
            f"日志目录：<code>{self.state.config.log_dir_effective}</code><br>"
            "想让数据跟着程序走：在程序目录放一个空的 <code>portable.txt</code>；"
            "或设置环境变量 <code>NFO_TAG_FIXER_HOME</code> 指定目录。")
        where.setWordWrap(True)
        where.setStyleSheet("color:#9aa0ac;")
        v4.addWidget(where)
        root.addWidget(gb4)

        # ---- 保存 ----
        row_save = QHBoxLayout()
        self.btn_save = QPushButton("保存设置")
        self.btn_save.setProperty("class", "primary")
        row_save.addWidget(self.btn_save)
        self.btn_logdir = QPushButton("打开日志目录")
        row_save.addWidget(self.btn_logdir)
        row_save.addStretch()
        root.addLayout(row_save)
        root.addStretch()

        self.btn_save.clicked.connect(self._save)
        self.btn_logdir.clicked.connect(
            lambda: open_in_explorer(self.state.config.log_dir_effective))
        self.btn_encoding.clicked.connect(self._encoding_unify)
        self.btn_health.clicked.connect(self._health_check)
        self.btn_field.clicked.connect(self._field_replace)
        self.btn_env.clicked.connect(self._show_env_check)

    # ---------- 设置 ----------

    def _save(self):
        s = self.state.config.settings
        s.backup_enabled = self.backup_check.isChecked()
        s.recursive_scan = self.recursive_check.isChecked()
        s.ffprobe_path = self.ffprobe_edit.text().strip()
        s.enable_ffprobe = self.ffprobe_check.isChecked()
        s.archive_db_path = self.db_edit.text().strip()
        s.theme = self.theme_combo.currentData()
        s.jellyfin_server = self.jf_server_edit.text().strip()
        s.jellyfin_api_key = self.jf_key_edit.text().strip()
        s.sync_jellyfin = self.jf_sync_check.isChecked()
        err = self.state.config.save_settings()
        if err:
            QMessageBox.warning(self, "保存失败", err)
            self.state.logger.error("settings", err)
            return
        # 设置保存后刷新 Finder：模块级固化的工具路径不会自己更新
        self.state.refresh_finder()
        set_theme(s.theme)
        QApplication.instance().setStyleSheet(qss(s.theme))
        self.state.logger.info("settings", "设置已保存")
        QMessageBox.information(
            self, "完成",
            "设置已保存并即时生效。\n"
            "（若主题观感有残留，重启一次应用即可完全刷新。）")

    def _test_jellyfin(self):
        from core.jellyfin import JellyfinClient
        client = JellyfinClient(self.jf_server_edit.text().strip(),
                                self.jf_key_edit.text().strip())
        ok, msg = client.ping()
        self.jf_status.setText(f"{'✅' if ok else '❌'} {msg}")
        self.state.logger.info("settings", f"Jellyfin 连接测试: {'成功' if ok else '失败'} {msg}",
                               result=msg)

    def _refresh_jellyfin(self):
        """触发 Jellyfin 全库刷新：重读所有 NFO，清掉 NFO 里已删除的旧标签。"""
        from PySide6.QtWidgets import QMessageBox
        from core.jellyfin import JellyfinClient
        client = JellyfinClient(self.jf_server_edit.text().strip(),
                                self.jf_key_edit.text().strip())
        ok, msg = client.ping()
        if not ok:
            QMessageBox.warning(self, "无法连接 Jellyfin", f"无法连接：{msg}")
            return
        if not ConfirmDialog.ask(
                self, "触发 Jellyfin 全库刷新",
                "这将让 Jellyfin <b>重新扫描整个媒体库、重读所有 NFO</b>。\n"
                "目的：清掉 NFO 里早已删除的旧标签（如「片商:xx」「发行:xx」「系列:xx」），"
                "并更新筛选/侧栏。\n\n"
                "扫描需要几分钟，期间可能短暂占用，建议先不要修改文件。确认触发？"):
            return
        self.jf_status.setText("⏳ 正在触发全库刷新…")
        got, msg2 = client.refresh_library()
        self.jf_status.setText("✅ 已触发" if got else f"❌ {msg2}")
        QMessageBox.information(
            self, "已触发全库刷新", msg2 if got else f"触发失败：{msg2}")
        self.state.logger.info("settings", f"Jellyfin 全库刷新: {msg2}", result=str(got))

    def _batch_sync_jellyfin(self):
        """追溯同步：把目录下已处理干净（不含'有码'标记）的 NFO 批量同步到 Jellyfin。"""
        from PySide6.QtWidgets import QProgressDialog
        from core.jellyfin import JellyfinClient
        from core.tag_analyzer import classify_tag

        # 直接从输入框读取（与「测试连接」同源，无需先保存），并同步到设置
        server = self.jf_server_edit.text().strip()
        api_key = self.jf_key_edit.text().strip()
        client = JellyfinClient(server, api_key)
        if not client.configured:
            QMessageBox.warning(
                self, "无法连接 Jellyfin",
                "连接失败：未配置服务器地址或 API Key\n请先在下方填写服务器地址与 API Key，并点「测试连接」。")
            return
        # 顺手保存到 settings，让自动同步功能也能用
        s = self.state.config.settings
        s.jellyfin_server = server
        s.jellyfin_api_key = api_key
        self.state.config.save_settings()
        ok, msg = client.ping()
        if not ok:
            QMessageBox.warning(
                self, "无法连接 Jellyfin",
                f"连接失败：{msg}\n请检查服务器地址与 API Key。")
            return
        d = QFileDialog.getExistingDirectory(self, "选择要追溯同步的文件夹（嵌套扫描）")
        if not d:
            return
        paths = find_nfo_files(d, recursive=True)
        targets = []
        for p in paths:
            nfo = _load_nfo(p)
            if nfo is None:
                continue
            # 只同步已处理干净的（无"有码"标记），避免把未处理的"有码"写进 Jellyfin
            if any(classify_tag(t) == "censored" for t in nfo.markers):
                continue
            targets.append(nfo)
        if not targets:
            QMessageBox.information(
                self, "提示", "该目录下没有「已处理干净（不含'有码'标记）」的 NFO")
            return
        est = max(1, int(len(targets) * 0.3))
        if not ConfirmDialog.ask(
                self, "追溯同步",
                f"将把 <b>{len(targets)}</b> 个已处理干净的 NFO 同步到 Jellyfin\n"
                "（用 NFO 的标签/类型覆盖 Jellyfin 数据库，清掉残留旧值如「有码」）。\n"
                f"预计需要 {est} 秒左右，可中途取消。确认？",
                detail="示例：\n" + "\n".join(
                    f"{nfo.number or nfo.basename}  [{', '.join(nfo.markers[:4])}]"
                    for nfo in targets[:10])):
            return
        prog = QProgressDialog("正在同步到 Jellyfin…", "取消", 0, len(targets), self)
        prog.setWindowTitle("Jellyfin 追溯同步")
        prog.setWindowModality(Qt.WindowModal)
        ok_n = fail_n = 0
        fail_samples = []
        for i, nfo in enumerate(targets):
            if prog.wasCanceled():
                break
            prog.setValue(i)
            prog.setLabelText(f"[{i+1}/{len(targets)}] {nfo.number or nfo.basename}")
            QApplication.processEvents()
            r_ok, r_msg = client.sync_nfo(nfo.number, nfo.path,
                                          nfo.tags, nfo.genres)
            if r_ok:
                ok_n += 1
            else:
                fail_n += 1
                if len(fail_samples) < 8:
                    fail_samples.append(f"{nfo.number or nfo.basename}: {r_msg}")
        prog.setValue(len(targets))
        self.state.logger.info(
            "sync", f"Jellyfin 追溯同步完成：成功 {ok_n}，失败 {fail_n}",
            path=d)
        for fs in fail_samples:
            self.state.logger.warn("sync", fs)
        QMessageBox.information(
            self, "完成",
            f"追溯同步完成：成功 {ok_n} 个，失败 {fail_n} 个\n"
            + ("\n失败示例：\n" + "\n".join(fail_samples) if fail_samples else "")
            + "\n失败通常为：未找到对应条目（番号不一致/文件不在媒体库）。")

    def _detect_ffprobe(self):
        """自动查找 ffprobe：配置 > 程序目录 > PATH > 常见安装位置（跨平台）。"""
        from core.toolchain import find_tool
        path = find_tool("ffprobe", self.ffprobe_edit.text().strip())
        if path:
            self.ffprobe_edit.setText(path)
            self.state.logger.info("settings", f"检测到 ffprobe: {path}")
            QMessageBox.information(self, "已找到 ffprobe", path)
            return
        QMessageBox.information(
            self, "未找到 ffprobe",
            "已查找：设置里的路径、程序目录（含 bin/、tools/、ffmpeg/）、系统 PATH、\n"
            "以及各平台常见安装位置，都没有找到 ffprobe。\n\n"
            "ffprobe 是可选的（只用于「破解找回」的时长/分辨率弱线索）。\n"
            "如需启用：下载 FFmpeg 后把 ffprobe.exe 的完整路径填到这里。")

    def _show_env_check(self):
        """环境自检：把探测结果摊开给用户看，也便于提 Issue 时附上。"""
        from core.appdirs import is_writable_dir
        from core.toolchain import doctor
        from version import __version__

        lines = [
            f"NFO 标签批量修改工具 v{__version__}",
            f"Python: {sys.version.split()[0]}  平台: {sys.platform}",
            f"打包版: {'是' if getattr(sys, 'frozen', False) else '否'}",
            f"数据目录: {self.state.app_dir}（{self.state.data_origin}）",
            f"数据目录可写: {is_writable_dir(self.state.app_dir)}",
            f"日志目录: {self.state.config.log_dir_effective}",
            f"档案库: {self.state.config.archive_db_path}"
            + ("" if self.state.archive.available
               else f"  ⚠ {self.state.archive.error}"),
            "-" * 56,
        ]
        lines += [f"{n:<16}{v}" for n, v in doctor(self.ffprobe_edit.text().strip())]
        if self.state.config.healed_fields:
            lines += ["", "本次启动已自动重置失效路径: "
                          + ", ".join(self.state.config.healed_fields)]

        dlg = QDialog(self)
        dlg.setWindowTitle("环境自检")
        dlg.resize(760, 520)
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText("\n".join(lines))
        lay.addWidget(txt)
        row = QHBoxLayout()
        btn_copy = QPushButton("复制全部")
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText("\n".join(lines)))
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        row.addStretch()
        row.addWidget(btn_copy)
        row.addWidget(btn_close)
        lay.addLayout(row)
        dlg.exec()

    def _pick_db(self):
        d = QFileDialog.getExistingDirectory(self, "选择档案库所在目录")
        if d:
            self.db_edit.setText(os.path.join(d, "archive.db"))

    def _apply_template(self):
        if ConfirmDialog.ask(self, "套用模板",
                             "将预置模板规则追加到当前规则列表？\n"
                             "（含：有码→无码破解 / 追加无码 / 清理流出 / uncensored 统一）"):
            rules = self.state.config.default_rules()
            n = 0
            for r in rules:
                if not any(x.id == r.id for x in self.state.config.mapper.rules):
                    self.state.config.mapper.rules.append(r)
                    n += 1
            self.state.config.save_rules()
            self.state.logger.info("settings", f"套用规则模板：新增 {n} 条")
            QMessageBox.information(self, "完成", f"已套用模板：新增 {n} 条规则")

    # ---------- 工具：编码统一 ----------

    def _encoding_unify(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹（统一其中 NFO 编码为 UTF-8）")
        if not d:
            return
        paths = find_nfo_files(d, recursive=True)
        if not paths:
            QMessageBox.information(self, "提示", "该目录下没有 NFO 文件")
            return
        if not ConfirmDialog.ask(
                self, "编码统一",
                f"将把 {len(paths)} 个 NFO 统一为 UTF-8 编码并重写（生成 .bak）。确认？",
                detail="\n".join(paths[:20]) + ("\n…" if len(paths) > 20 else "")):
            return
        backup = self.state.config.settings.backup_enabled

        # 批量读/存是重 IO，放后台线程，主线程不冻结、可取消。
        def _work(_worker=None):
            ok, skip = 0, 0
            for i, p in enumerate(paths):
                if _worker and _worker.cancelled():
                    break
                nfo = _load_nfo(p)
                if nfo is None:
                    continue
                if nfo.encoding.lower() in ("utf-8", "utf-8-sig"):
                    skip += 1
                    continue
                err = nfo.save(backup=backup)
                if err:
                    self.state.logger.error("encoding", err, path=p, result="失败")
                else:
                    ok += 1
                    self.state.logger.info("encoding", "编码统一为 UTF-8",
                                           path=p, old=nfo.encoding, new="utf-8",
                                           result="成功")
                if _worker:
                    _worker.report(i + 1, len(paths), "编码统一…")
            self.state.logger.info("encoding", f"编码统一完成：转换 {ok}，已是UTF-8 {skip}")
            return ok, skip

        def _on_done(result, worker):
            ok, skip = result
            QMessageBox.information(self, "完成", f"编码统一完成：转换 {ok} 个，跳过 {skip} 个")

        def _on_error(e, worker):
            self.state.logger.error("encoding", f"编码统一失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "编码统一失败", str(e))

        run_with_progress(self, "编码统一", "统一 NFO 编码…", _work,
                          on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=len(paths))

    # ---------- 工具：健康检查 ----------

    def _health_check(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹（健康检查）")
        if not d:
            return
        nfo_paths = set(find_nfo_files(d, recursive=True))

        # 目录全扫 + 逐个解析是重 IO，放后台线程；完成后主线程弹报告。
        def _work(_worker=None):
            videos: list[str] = []
            for dirpath, _d, files in os.walk(d):
                for fn in files:
                    if is_video(fn):
                        videos.append(os.path.join(dirpath, fn))
            orphan_nfo = [p for p in sorted(nfo_paths) if not find_video_for(p)]
            missing_nfo = [v for v in videos if not self._has_nfo(v, nfo_paths)]
            mismatch = []
            conflicts = []
            multi_nfo = []
            from core.tag_analyzer import classify_tag
            by_dir = {}
            for p in sorted(nfo_paths):
                d0 = os.path.dirname(p)
                by_dir.setdefault(d0, []).append(os.path.basename(p))
            for d0, files in by_dir.items():
                if len(files) >= 2:
                    multi_nfo.append(f"{d0}  [{', '.join(files)}]")
            for p in sorted(nfo_paths):
                nfo = _load_nfo(p)
                if nfo is None:
                    continue
                c_items = [t for t in nfo.markers if classify_tag(t) == "censored"]
                u_items = [t for t in nfo.markers if classify_tag(t) == "uncensored"]
                if c_items and u_items:
                    conflicts.append(f"{p}  [有码:{','.join(c_items[:2])} | 无码:{','.join(u_items[:2])}]")
                    continue
                if not nfo.number:
                    continue
                stem = os.path.splitext(os.path.basename(p))[0].upper()
                num = nfo.number.upper().replace(" ", "")
                if num and num.replace("-", "") not in stem.replace("-", "") \
                   and not re.search(rf"{re.escape(num)}", stem):
                    mismatch.append(p)
            return {"videos": videos, "orphan": orphan_nfo, "missing": missing_nfo,
                    "mismatch": mismatch, "conflicts": conflicts, "multi": multi_nfo}

        def _on_done(res, worker):
            d0 = d
            videos = res["videos"]
            orphan_nfo = res["orphan"]
            missing_nfo = res["missing"]
            mismatch = res["mismatch"]
            conflicts = res["conflicts"]
            multi_nfo = res["multi"]
            lines = [f"扫描目录：{d0}",
                     f"NFO 数量：{len(nfo_paths)}",
                     f"视频数量：{len(videos)}",
                     "", f"① 孤儿 NFO（无对应视频）：{len(orphan_nfo)}",
                     *[f"   {p}" for p in orphan_nfo[:30]],
                     "", f"② 缺 NFO 的视频：{len(missing_nfo)}",
                     *[f"   {v}" for v in missing_nfo[:30]],
                     "", f"③ 文件名与番号不一致：{len(mismatch)}",
                     *[f"   {p}" for p in mismatch[:30]],
                     "", f"④ 有码/无码标记矛盾（建议在标签修正页清理）：{len(conflicts)}",
                     *[f"   {p}" for p in conflicts[:30]],
                     "", f"⑤ 同目录多 NFO（Jellyfin 优先读 movie.nfo，需确保都已修改）：{len(multi_nfo)}",
                     *[f"   {p}" for p in multi_nfo[:30]],
                     ]
            dlg = QDialog(self)
            dlg.setWindowTitle("健康检查报告")
            dlg.resize(720, 520)
            lay = QVBoxLayout(dlg)
            txt = QPlainTextEdit()
            txt.setReadOnly(True)
            txt.setPlainText("\n".join(lines))
            lay.addWidget(txt)
            btn = QPushButton("关闭")
            btn.clicked.connect(dlg.accept)
            lay.addWidget(btn, 0, Qt.AlignRight)
            dlg.exec()
            self.state.logger.info("health", "健康检查完成",
                                   result=f"孤儿NFO {len(orphan_nfo)}，缺NFO视频 {len(missing_nfo)}，不一致 {len(mismatch)}")

        def _on_error(e, worker):
            self.state.logger.error("health", f"健康检查失败: {getattr(e, '_tb', '')}")
            QMessageBox.critical(self, "健康检查失败", str(e))

        run_with_progress(self, "健康检查", "扫描并分析…", _work,
                          on_done=_on_done, on_error=_on_error,
                          cancellable=True, initial_total=len(nfo_paths))

    def _has_nfo(self, video: str, nfo_paths: set[str]) -> bool:
        stem = os.path.splitext(video)[0] + ".nfo"
        return os.path.abspath(stem) in nfo_paths or os.path.abspath(stem).lower() in {p.lower() for p in nfo_paths}

    # ---------- 工具：字段批量替换 ----------

    def _field_replace(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹（对其中 NFO 批量替换字段）")
        if not d:
            return
        items = scan_paths([d], recursive=True)
        items = [it for it in items if it.nfo.is_valid]
        if not items:
            QMessageBox.information(self, "提示", "该目录下没有可解析的 NFO")
            return
        dlg = _FieldReplaceDialog(self, total=len(items))
        if dlg.exec() != QDialog.Accepted:
            return
        field = dlg.field_combo.currentData()
        find = dlg.find_edit.text()
        repl = dlg.replace_edit.text()
        use_regex = dlg.regex_check.isChecked()
        if not find:
            QMessageBox.warning(self, "提示", "查找内容不能为空")
            return
        try:
            rx = re.compile(find) if use_regex else None
        except re.error as e:
            QMessageBox.warning(self, "正则错误", str(e))
            return
        if not ConfirmDialog.ask(
                self, "字段批量替换",
                f"将对 {len(items)} 个 NFO 的 <{field}> 字段执行替换：\n"
                f"「{find}」→「{repl}」（正则: {'是' if use_regex else '否'}）\n"
                "执行前会生成 .bak 备份。确认？"):
            return
        ok, hit = 0, 0
        for it in items:
            nfo = it.nfo
            changed = _apply_field_replace(nfo, field, find, repl, rx)
            if not changed:
                continue
            err = nfo.save(backup=self.state.config.settings.backup_enabled)
            if err:
                self.state.logger.error("field_replace", err, path=nfo.path)
            else:
                ok += 1
                hit += changed
                self.state.logger.info("field_replace", f"{field}: {find} -> {repl}",
                                       path=nfo.path, field=field,
                                       old=find, new=repl, result=f"{changed} 处")
        self.state.logger.info("field_replace",
                               f"字段替换完成：{ok} 个文件，{hit} 处替换")
        QMessageBox.information(self, "完成",
                                f"字段替换完成：{ok} 个文件，共 {hit} 处替换")


def _load_nfo(path: str):
    from core.nfo import NfoFile
    nfo = NfoFile(path)
    return nfo if nfo.is_valid else None


def _apply_field_replace(nfo, field: str, find: str, repl: str, rx) -> int:
    """对 NFO 的某字段批量替换，返回替换次数。"""
    changed = 0
    for el in list(nfo.root.iter(field)):
        if el.text is None:
            continue
        t = el.text
        if rx:
            new_t, n = rx.subn(repl, t)
        else:
            n = t.count(find)
            new_t = t.replace(find, repl)
        if n and new_t != t:
            el.text = new_t
            changed += n
    if changed:
        nfo.mark_dirty()
    return changed


class _FieldReplaceDialog(QDialog):
    def __init__(self, parent=None, total: int = 0):
        super().__init__(parent)
        self.setWindowTitle("字段批量替换")
        self.resize(460, 220)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"作用范围：{total} 个 NFO 文件"))

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("字段："))
        self.field_combo = QComboBox()
        for name in ("tag", "genre", "title", "actor", "studio", "director", "plot", "year"):
            self.field_combo.addItem(name, name)
        r1.addWidget(self.field_combo, 1)
        lay.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("查找："))
        self.find_edit = QLineEdit()
        r2.addWidget(self.find_edit, 1)
        lay.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("替换为："))
        self.replace_edit = QLineEdit()
        r3.addWidget(self.replace_edit, 1)
        lay.addLayout(r3)

        self.regex_check = QCheckBox("使用正则表达式")
        lay.addWidget(self.regex_check)

        from PySide6.QtWidgets import QDialogButtonBox
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("开始替换")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
