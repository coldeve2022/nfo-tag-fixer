# -*- coding: utf-8 -*-
"""pytest 全局夹具。

三件必须在这里做成"唯一入口"的事：

1. **离屏 + 字体目录**：`QT_QPA_PLATFORM=offscreen` 下 Qt 找不到系统字体，
   中文会渲染成方块 □；Windows 上显式指定 `QT_QPA_FONTDIR` 即可正常渲染。
2. **数据根目录重定向**：本应用源码模式下会把 `settings.json / rules.json /
   archive.db / logs` 写在项目目录旁边。若不重定向，跑一次测试就在仓库根留下
   运行时产物（CI 的"确认测试没有污染工作区"会直接红）。
   这里用 `NFO_TAG_FIXER_HOME` 环境变量把根目录指到临时目录——这是
   `core.appdirs.resolve_data_dir()` 的最高优先级来源，因此**所有**走
   `AppState()` 的代码都会自动落在临时目录里。
3. **模态框打桩**：离屏模式下 `QMessageBox.exec()` 会阻塞事件循环把测试挂死，
   凡是会弹窗的路径必须先把 exec 换掉。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ---- 必须在导入 PySide6 之前设置 ----
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 会话级：所有测试的数据根目录都指向同一个临时目录（pytest tmp_path_factory）
_DATA_HOME = Path(os.environ.get("PYTEST_DATA_HOME") or (ROOT / ".pytest_home"))
os.environ.setdefault("NFO_TAG_FIXER_HOME", str(_DATA_HOME))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def no_modal(monkeypatch):
    """把所有模态弹窗替换为"直接返回默认值"，避免离屏下挂死。

    **默认 autouse**：任何一条用例漏掉它，一次 `QMessageBox.exec()` 就会把
    pytest 永久挂住（表现为进程被外部信号杀掉、连一行输出都没有）。
    """
    from PySide6.QtWidgets import QDialog, QMessageBox

    from ui.dialogs import ConfirmDialog

    calls: list[tuple[str, str]] = []

    def _record(kind):
        def _fn(parent=None, title="", text="", *a, **kw):
            calls.append((kind, str(title)))
            return QMessageBox.Ok
        return _fn

    monkeypatch.setattr(QMessageBox, "information", _record("info"))
    monkeypatch.setattr(QMessageBox, "warning", _record("warn"))
    monkeypatch.setattr(QMessageBox, "critical", _record("error"))
    monkeypatch.setattr(QMessageBox, "about", _record("about"))
    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *a, **kw: True))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **kw: QDialog.Accepted)
    return calls


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    """把数据根目录指到本用例独立的临时目录。"""
    home = tmp_path / "appdata"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NFO_TAG_FIXER_HOME", str(home))
    return home


@pytest.fixture
def sync_worker(monkeypatch):
    """把「后台线程 + 模态进度条」替换为同步执行。

    页面用 `from ui.worker import run_with_progress` 绑定了模块级名字，
    因此必须逐模块替换。同步执行让用例确定、不依赖线程时序，
    也让 offscreen 下的 QProgressDialog.exec() 不会挂住事件循环。
    """
    import ui.pages.finder
    import ui.pages.fixer
    import ui.pages.nfo_repair
    import ui.pages.organizer
    import ui.pages.settings
    import ui.pages.tag_enrich

    calls: list[str] = []

    class _FakeWorker:
        def isInterruptionRequested(self) -> bool:
            return False

        def cancelled(self) -> bool:
            return False

        def report(self, *_a, **_kw) -> None:
            return None

        def wait_if_paused(self) -> None:
            return None

    def _sync_run(parent=None, title="", label="", fn=None, args=(), kwargs=None,
                  on_done=None, on_error=None, cancellable=True,
                  initial_total=0, **_ignored):
        calls.append(str(title))
        w = _FakeWorker()
        try:
            result = fn(*tuple(args), **dict(kwargs or {}), _worker=w)
        except Exception as exc:  # noqa: BLE001 - 与 Worker 行为一致：交给 on_error
            if on_error:
                on_error(exc, w)
            return w
        if on_done:
            on_done(result, w)
        return w

    for mod in (ui.pages.finder, ui.pages.fixer, ui.pages.nfo_repair,
                ui.pages.organizer, ui.pages.settings, ui.pages.tag_enrich):
        if hasattr(mod, "run_with_progress"):
            monkeypatch.setattr(mod, "run_with_progress", _sync_run)
    return calls


# ---------- 合成演示库（不依赖任何真实素材） ----------

SAMPLE_NFO = """<?xml version="1.0" encoding="UTF-8"?>
<movie>
  <title>示例影像 A</title>
  <uniqueid type="num">ABC-123</uniqueid>
  <actor><name>示例演员</name></actor>
  <genre>剧情</genre>
  <tag>有码</tag>
  <tag>HD</tag>
  <plot>这是一段用于测试的剧情简介。</plot>
</movie>
"""

SAMPLE_NFO_UNCENSORED = """<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>示例影像 B</title>
  <uniqueid type="num">DEF-456</uniqueid>
  <actor><name>另一个演员</name></actor>
  <tag>无码破解</tag>
</movie>
"""

SAMPLE_NFO_PLAIN = """<?xml version="1.0" encoding="UTF-8"?>
<movie>
  <title>无标记影像</title>
  <plot>没有 tag 的条目。</plot>
</movie>
"""


def make_nfo(path: Path, content: str = SAMPLE_NFO, *, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode(encoding))
    return path


@pytest.fixture
def demo_library(tmp_path):
    """合成一个媒体库目录树（中性路径，不含任何真实个人数据）。

    结构：
      Library/
        ArtistA/ABC-123/ABC-123.nfo + ABC-123.mp4
        ArtistA/ABC-123/movie.nfo            （重复刮削，空壳）
        ArtistA/DEF-456/DEF-456.nfo + DEF-456.mkv
        ArtistB/GHI-789/ghi-789.nfo          （无视频的孤儿 NFO）
        ArtistB/JKL-012/                     （有视频缺 NFO）
        Broken/bad.nfo                       （非法 XML）
        Gbk/gbk-001.nfo                      （GBK 编码）
    """
    root = tmp_path / "Library"
    make_nfo(root / "ArtistA" / "ABC-123" / "ABC-123.nfo")
    (root / "ArtistA" / "ABC-123" / "ABC-123.mp4").write_bytes(b"\x00" * 4096)
    make_nfo(root / "ArtistA" / "ABC-123" / "movie.nfo",
             '<?xml version="1.0" encoding="UTF-8"?><movie><tag></tag></movie>')
    make_nfo(root / "ArtistA" / "DEF-456" / "DEF-456.nfo", SAMPLE_NFO_UNCENSORED)
    (root / "ArtistA" / "DEF-456" / "DEF-456.mkv").write_bytes(b"\x00" * 8192)
    make_nfo(root / "ArtistB" / "GHI-789" / "ghi-789.nfo", SAMPLE_NFO_PLAIN)
    (root / "ArtistB" / "JKL-012").mkdir(parents=True, exist_ok=True)
    (root / "ArtistB" / "JKL-012" / "JKL-012.mp4").write_bytes(b"\x00" * 2048)
    (root / "Broken").mkdir(parents=True, exist_ok=True)
    (root / "Broken" / "bad.nfo").write_text("<movie><tag>未闭合", encoding="utf-8")
    make_nfo(root / "Gbk" / "gbk-001.nfo", SAMPLE_NFO, encoding="gbk")
    return root


@pytest.fixture
def state(data_home, qapp):
    """构造一个指向临时数据目录的 AppState。"""
    from ui.appstate import AppState

    st = AppState(str(data_home))
    yield st
    st.close()
