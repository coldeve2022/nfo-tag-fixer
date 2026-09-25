# -*- coding: utf-8 -*-
"""生成 README 用的界面截图（手动运行，不进 CI）。

要点：
- **演示数据放在中性路径**（默认 `D:\\AppDemo`，可用 `NFO_DEMO_ROOT` 覆盖），
  否则界面上会显示构建目录甚至用户名，截图一旦提交就泄露了；
- 离屏模式下 Qt 找不到系统字体，中文会渲染成方块 □ —— 必须指定 `QT_QPA_FONTDIR`；
- 数据根目录用 `NFO_TAG_FIXER_HOME` 指到演示目录，避免污染真实配置；
- 跑完会清理演示目录。

用法：
    python tools/dev/make_screenshots.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

DEFAULT_ROOT = r"D:\AppDemo"
DEMO_ROOT = Path(os.environ.get("NFO_DEMO_ROOT") or DEFAULT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["NFO_TAG_FIXER_HOME"] = str(DEMO_ROOT / "_appdata")

from core.console import force_utf8_stdout  # noqa: E402

force_utf8_stdout()

OUT = ROOT / "assets" / "screenshots"

NFO_A = """<?xml version="1.0" encoding="UTF-8"?>
<movie>
  <title>【示例】夏日回忆 第一话</title>
  <originaltitle>サンプル作品 第一話</originaltitle>
  <uniqueid type="num">ABC-123</uniqueid>
  <year>2024</year>
  <runtime>120</runtime>
  <studio>示例制片</studio>
  <actor><name>示例演员A</name></actor>
  <actor><name>示例演员B</name></actor>
  <genre>剧情</genre>
  <tag>有码</tag>
  <tag>HD</tag>
  <tag>片商: 示例制片</tag>
  <tag>素人娘</tag>
  <plot>这是一段用于演示的剧情简介，仅存在于截图脚本生成的临时数据里。</plot>
</movie>
"""

NFO_B = """<?xml version="1.0" encoding="UTF-8"?>
<movie>
  <title>【示例】冬日回声</title>
  <uniqueid type="num">DEF-456</uniqueid>
  <year>2024</year>
  <runtime>135</runtime>
  <actor><name>示例演员A</name></actor>
  <genre>剧情</genre>
  <tag>无码破解</tag>
  <tag>無修正</tag>
  <tag>顔射</tag>
  <tag>颜射</tag>
  <tag>巨乳</tag>
  <tag>中出し</tag>
  <tag>HD</tag>
  <tag>1080P</tag>
  <plot>另一段演示用剧情简介。</plot>
</movie>
"""

NFO_C = """<?xml version="1.0" encoding="UTF-8"?>
<movie>
  <title>【示例】街角记录</title>
  <uniqueid type="num">FC2-1234567</uniqueid>
  <year>2023</year>
  <runtime>95</runtime>
  <tag>有码</tag>
  <tag>FC2</tag>
  <tag>素人</tag>
  <tag>高画質</tag>
  <tag>高画质</tag>
  <plot>演示条目。</plot>
</movie>
"""

NFO_EMPTY = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<movie><title>【示例】重复刮削的空壳</title></movie>\n')


def build_demo_library() -> Path:
    lib = DEMO_ROOT / "Library"
    if lib.exists():
        shutil.rmtree(lib, ignore_errors=True)
    dirs = [
        ("ArtistA/ABC-123", [("ABC-123.nfo", NFO_A), ("movie.nfo", NFO_EMPTY)]),
        ("ArtistA/DEF-456", [("DEF-456.nfo", NFO_B)]),
        ("ArtistB/GHI-789", [("GHI-789.nfo", NFO_C)]),
        ("ArtistB/JKL-012", [("JKL-012.nfo", NFO_A)]),
        ("ArtistC/MNO-345", [("MNO-345.nfo", NFO_B)]),
    ]
    sizes = {
        "ABC-123": 3 * 1024 ** 3,
        "DEF-456": 6 * 1024 ** 3,
        "GHI-789": 1400 * 1024 ** 2,
        "JKL-012": 2 * 1024 ** 3,
        "MNO-345": 5 * 1024 ** 3,
    }
    for sub, files in dirs:
        d = lib / sub
        d.mkdir(parents=True, exist_ok=True)
        for name, content in files:
            (d / name).write_text(content, encoding="utf-8")
        stem = os.path.basename(sub)
        video = d / f"{stem}.mp4"
        with open(video, "wb") as f:
            f.write(b"\x00" * 4096)
            f.truncate(sizes.get(stem, 1024 ** 3))
    # 孤儿 NFO（没有对应视频）
    (lib / "ArtistC/PQR-678").mkdir(parents=True, exist_ok=True)
    (lib / "ArtistC/PQR-678/PQR-678.nfo").write_text(NFO_C, encoding="utf-8")
    return lib


def ensure_qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def shoot(widget, name: str, *, settle: int = 6) -> None:
    from PySide6.QtCore import QCoreApplication

    OUT.mkdir(parents=True, exist_ok=True)
    for _ in range(settle):                    # 让布局与绘制真正生效
        QCoreApplication.processEvents()
    path = OUT / name
    ok = widget.grab().save(str(path), "PNG")
    print(f"  {'✓' if ok else '✗'} {path.relative_to(ROOT)}  "
          f"{widget.width()}x{widget.height()}")


def main() -> int:
    app = ensure_qapp()
    print(f"演示库：{DEMO_ROOT / 'Library'}")
    lib = build_demo_library()

    from core.mapper import MappingRule
    from core.tag_organizer import (collect_tag_stats, rule_detect,
                                    suggest_similar_merges)
    from ui.appstate import AppState
    from ui.main_window import MainWindow
    from ui.styles import qss, set_theme

    set_theme("dark")
    app.setStyleSheet(qss("dark"))

    state = AppState(str(DEMO_ROOT / "_appdata"))
    state.config.mapper.rules = [
        MappingRule(name="🤖 有码 → 无码破解", match_tags=["有码"],
                    operation="replace", new_tag="无码破解"),
        MappingRule(name="清理前缀噪声", match_tags=["片商:", "发行:"],
                    operation="remove"),
    ]
    state.config.settings.censored_tags = ["有码", "CENSORED"]
    state.config.settings.uncensored_tags = ["无码破解", "無修正", "FC2"]
    state.config.settings.target_tag = "无码破解"
    state.config.save_settings()

    win = MainWindow(state)
    win.resize(1440, 880)
    win.show()

    # ---- ① 标签修正页 ----
    fixer = win.fixer_page
    fixer.import_paths([str(lib)])
    fixer.check_censored()
    fixer._on_select(0)
    win.tabs.setCurrentIndex(0)
    shoot(win, "01-fixer.png")

    # ---- ⑤ 预览与 diff（用真实规则算出来的变更）----
    from ui.pages.fixer import PreviewDialog

    changes = fixer._current_mapper().preview_items(fixer._checked_items() or fixer.items)
    if changes:
        dlg = PreviewDialog(changes, win)
        dlg.resize(980, 620)
        dlg.show()
        dlg.list_widget.setCurrentRow(0)
        shoot(dlg, "05-preview-diff.png")
        dlg.close()
        fixer._changes = changes

    # ---- ④ 标签整理页 ----
    org = win.organizer_page
    org.folders = [str(lib)]
    stats = collect_tag_stats([str(lib)])
    rule_detect(stats)
    suggest_similar_merges(stats)
    # 造几条 AI 决策，让"AI 建议"列有内容（纯演示）
    for name, act, target in (("顔射", "merge", "颜射"), ("高画質", "merge", "高画质"),
                              ("片商: 示例制片", "remove", ""),
                              ("1080P", "remove", "")):
        st = stats.tags.get(name)
        if st and not st.rule_flag and not st.sim_target:
            st.ai_action, st.ai_target = act, target
            st.ai_reason = "同义写法，统一到简体" if act == "merge" else "画质标记，非内容标签"
    org.stats = stats
    win.tabs.setCurrentIndex(3)
    org._fill_table()
    shoot(win, "02-organizer.png")

    # ---- ③ NFO 修复页 ----
    from core.nfo_repair import scan_duplicates

    rep = win.repair_page
    rep.folder = str(lib)
    rep.issues = scan_duplicates(str(lib))
    win.tabs.setCurrentIndex(4)
    rep._fill_table()
    rep._check_auto()
    shoot(win, "03-repair.png")

    # ---- ⑥ 设置与工具页 ----
    win.tabs.setCurrentIndex(5)
    win.settings_page.jf_server_edit.setText("http://192.168.1.10:8096")
    win.settings_page.jf_key_edit.setText("")
    shoot(win, "04-settings.png")

    win.close()
    state.close()

    # 清理演示数据（含数据根目录），不留痕迹
    shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    print(f"\n已清理 {DEMO_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
