# -*- coding: utf-8 -*-
"""关键交互路径：导入 → 勾选 → 预览 → 应用 → 回滚，以及各聚合方法。

重点覆盖两类历史缺陷：
1. 「控件 → 配置」的聚合方法（`SettingsPage._save()`）从来没有用例真的调用过；
2. 表格刷新里用了未导入的名字（`QTableWidgetItem`），只有真的检索一次才暴露。
"""

from __future__ import annotations

import os

from core.nfo import NfoFile
from core.scanner import scan_paths
from ui.pages.archive import ArchivePage
from ui.pages.finder import FinderPage
from ui.pages.fixer import FixerPage
from ui.pages.nfo_repair import RepairPage
from ui.pages.organizer import OrganizerPage
from ui.pages.settings import SettingsPage


# ---------- 标签修正页 ----------

def test_fixer_import_preview_apply_rollback(qapp, state, demo_library, no_modal,
                                              sync_worker):
    page = FixerPage(state)
    page.import_paths([str(demo_library)])
    assert len(page.items) >= 5
    assert page.table.rowCount() == len(page.items)
    # 导入后默认全选
    assert page.table.checked_count() == len(page.items)

    # 一键勾选有码
    page.check_censored()
    assert page.table.checked_count() >= 1

    # 生成一条映射规则（等价于「标签统计 → 一键智能建议」的结果）
    from core.mapper import MappingRule
    state.config.mapper.rules = [MappingRule(name="有码→无码破解",
                                             match_tags=["有码"],
                                             operation="replace",
                                             new_tag="无码破解")]
    state.config.settings.censored_tags = ["有码"]

    page.preview_changes()
    assert page._changes, "预览应当产生变更"

    page.apply_changes()
    target = demo_library / "ArtistA" / "ABC-123" / "ABC-123.nfo"
    assert "无码破解" in NfoFile(str(target)).tags
    assert os.path.exists(str(target) + ".bak")
    assert state.archive.count() >= 1

    # 回滚
    page.check_censored()
    page.rollback_checked()
    assert "有码" in NfoFile(str(target)).tags


def test_fixer_filter_and_remove_rows(qapp, state, demo_library, no_modal):
    page = FixerPage(state)
    page.import_paths([str(demo_library)])
    total = len(page.items)

    page.filter_edit.setText("abc-123")
    page._apply_filter()
    visible = [r for r in range(page.table.rowCount())
               if not page.table.isRowHidden(r)]
    assert 1 <= len(visible) <= 2

    page.filter_edit.clear()
    page.table.selectRow(0)
    page.remove_rows([0])
    assert len(page.items) == total - 1
    page.undo_remove()
    assert len(page.items) == total

    page.clear_items()
    assert page.items == [] and page.table.rowCount() == 0


def test_fixer_group_cache_invalidated_after_import(qapp, state, demo_library):
    page = FixerPage(state)
    assert page._groups() == (set(), set())
    page.import_paths([str(demo_library)])
    c, u = page._groups()
    assert "有码" in c and "无码破解" in u


# ---------- 破解找回页 ----------

def test_finder_search_populates_table(qapp, state, demo_library, no_modal,
                                       sync_worker):
    """回归：`_refresh_table` 里用了未导入的 QTableWidgetItem，一点检索就崩。"""
    page = FinderPage(state)
    page.items = scan_paths([str(demo_library)], recursive=True)
    page.actor_edit.setText("示例演员")
    page.search()
    assert page.candidates
    assert page.table.rowCount() == len(page.candidates)
    # 勾选并应用
    page.check_censored()
    page.target_tag_edit.setText("无码破解")
    page.apply_selected()
    target = demo_library / "ArtistA" / "ABC-123" / "ABC-123.nfo"
    assert "无码破解" in NfoFile(str(target)).tags


def test_finder_groups_cache_reset_on_rescan(qapp, state, demo_library, no_modal):
    page = FinderPage(state)
    page.items = scan_paths([str(demo_library)], recursive=True)
    page.actor_edit.setText("示例演员")
    page.search()
    first = page._groups()
    page.search()
    assert page._groups() == first


# ---------- 档案页 ----------

def test_archive_page_lists_and_searches(qapp, state, tmp_path, no_modal):
    state.archive.add_record(nfo_path=str(tmp_path / "a.nfo"), number="ABC-123",
                             actors="示例演员", original_tag="有码",
                             new_tag="无码破解", file_size=1024, mtime="")
    page = ArchivePage(state)
    assert page.table.rowCount() == 1
    page.number_edit.setText("ABC-123")
    page.search()
    assert page.table.rowCount() == 1
    page.number_edit.setText("不存在")
    page.search()
    assert page.table.rowCount() == 0


# ---------- NFO 修复页 ----------

def test_repair_page_scan_and_auto_check(qapp, state, demo_library, sync_worker,
                                         no_modal):
    page = RepairPage(state)
    page.folder = str(demo_library)
    page._scan()
    assert page.issues, "合成的演示库里有一个多 NFO 目录"
    assert page.table.rowCount() == len(page.issues)
    page._check_auto()
    assert page.table.checked_count() >= 1


# ---------- 标签整理页 ----------

def test_organizer_page_scan_and_summary(qapp, state, demo_library, sync_worker,
                                         no_modal):
    page = OrganizerPage(state)
    page.folders = [str(demo_library)]
    page._scan()
    assert page.stats is not None
    assert page.table.rowCount() == len(page.stats.tags)
    assert "标签" in page.summary.text() or "删除" in page.summary.text()
    # 批量改操作
    page.table.selectRow(0)
    page._bulk_action(1)
    assert page.stats is not None


def test_organizer_construction_does_not_probe_ollama_synchronously(qapp, state,
                                                                    monkeypatch):
    """构造期绝不能联网。

    `ollama_available()` 带 3 秒超时；在没跑 Ollama（或被代理拦下）的机器上，
    写在页面构造里就等于「打开软件就开始卡」。探测必须放后台。
    """
    import time

    import core.tag_organizer as to

    def slow_probe():
        time.sleep(0.4)                 # 模拟一个"慢"的探测
        return False

    monkeypatch.setattr(to, "ollama_available", slow_probe)
    monkeypatch.setattr(to, "ollama_has_model", lambda: False)

    t0 = time.monotonic()
    page = OrganizerPage(state)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.25, f"构造被探测阻塞了 {elapsed:.2f}s"
    # 构造完成时探测结果还没回来 → 说明确实没同步等待
    assert page._engine_state is None
    assert "Ollama" in page.ai_key.placeholderText()


# ---------- 设置页 ----------

def test_settings_save_persists_every_field(qapp, state, no_modal, tmp_path):
    """「控件 → 配置」的聚合方法必须真的调用一次并回读。"""
    page = SettingsPage(state)
    page.backup_check.setChecked(False)
    page.recursive_check.setChecked(False)
    page.ffprobe_edit.setText("")
    page.ffprobe_check.setChecked(True)
    page.db_edit.setText(str(tmp_path / "my-archive.db"))
    page.theme_combo.setCurrentIndex(
        page.theme_combo.findData("light"))
    page.jf_server_edit.setText("http://127.0.0.1:8096")
    page.jf_key_edit.setText("dummy-key")
    page.jf_sync_check.setChecked(True)

    page._save()

    from config import ConfigManager
    reloaded = ConfigManager(state.app_dir)
    s = reloaded.settings
    assert s.backup_enabled is False
    assert s.recursive_scan is False
    assert s.enable_ffprobe is True
    assert s.archive_db_path == str(tmp_path / "my-archive.db")
    assert s.theme == "light"
    assert s.jellyfin_server == "http://127.0.0.1:8096"
    assert s.jellyfin_api_key == "dummy-key"
    assert s.sync_jellyfin is True


def test_settings_env_check_and_detect_ffprobe_do_not_crash(qapp, state, no_modal):
    page = SettingsPage(state)
    page._show_env_check()
    page._detect_ffprobe()
    state.config.settings.ffprobe_path = ""
    state.refresh_finder()
    assert state.finder is not None


def test_settings_apply_template_adds_rules(qapp, state, no_modal):
    page = SettingsPage(state)
    before = len(state.config.mapper.rules)
    page._apply_template()
    assert len(state.config.mapper.rules) == before + 4


# ---------- 规则导入导出 ----------

def test_rules_export_import_roundtrip(qapp, state, tmp_path, no_modal):
    state.config.mapper.rules = state.config.default_rules()
    out = tmp_path / "rules.json"
    n = state.config.export_rules(str(out))
    assert n == 4
    assert state.config.settings.last_rules_path == str(out)

    state.config.mapper.rules = []
    assert state.config.import_rules(str(out)) == 4
    assert state.config.settings.last_rules_path == str(out)


def test_version_single_source(qapp, state):
    from version import APP_NAME, __version__
    page = SettingsPage(state)
    assert page is not None
    assert __version__[0].isdigit()
    assert APP_NAME
