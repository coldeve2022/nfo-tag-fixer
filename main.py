# -*- coding: utf-8 -*-
"""NFO 标签批量修改工具 — 入口。

用法：
    python main.py              启动图形界面
    python main.py --doctor     打印环境自检（贴 Issue 时请附上这段输出）
    python main.py --version    打印版本号
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

# 确保项目根目录在 sys.path（PyInstaller 冻结时自动处理）
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.console import force_utf8_stdout  # noqa: E402

force_utf8_stdout()


def _install_excepthook(log_dir: str) -> None:
    """全局异常钩子：任何未捕获异常先写 crash.log 再弹窗，避免 PySide6 静默 abort。

    背景：PySide6 事件循环里未捕获的 Python 异常会直接 qFatal()/abort()，
    进程瞬间消失、日志不留痕——用户看到的「界面突然没了」就是这个机制。
    钩子保证：异常必落盘（crash.log），并尝试弹窗告知。
    """
    import threading
    import traceback as _tb

    def _write(text: str) -> None:
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "crash.log"), "a", encoding="utf-8") as f:
                f.write(f"\n===== {datetime.now().isoformat()} =====\n{text}\n")
        except OSError:
            pass

    def _hook(exc_type, exc_value, exc_tb):
        text = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
        _write(text)
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "程序错误",
                f"发生未捕获异常，程序可能不稳定：\n{exc_value}\n\n"
                f"完整堆栈已写入：\n{os.path.join(log_dir, 'crash.log')}")
        except Exception:  # noqa: BLE001
            pass

    def _thread_hook(args):
        text = "".join(_tb.format_exception(args.exc_type, args.exc_value,
                                            args.exc_traceback))
        _write("后台线程未捕获异常：\n" + text)

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


def _print_doctor() -> int:
    """环境自检：贴 Issue 时附上这段输出能省掉大量来回。"""
    from core.appdirs import is_writable_dir, resolve_data_dir
    from core.toolchain import doctor
    from version import APP_NAME, __version__

    root, origin = resolve_data_dir()
    print(f"{APP_NAME} v{__version__}")
    print(f"Python  : {sys.version.split()[0]} ({sys.executable})")
    print(f"平台    : {sys.platform}")
    print(f"打包版  : {'是' if getattr(sys, 'frozen', False) else '否'}")
    print(f"数据目录: {root}  [{origin}]  可写={is_writable_dir(root)}")
    print("-" * 60)
    for name, value in doctor():
        print(f"{name:<16}{value}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        from version import __version__
        print(__version__)
        return 0
    if "--doctor" in argv:
        return _print_doctor()

    from PySide6.QtWidgets import QApplication

    from ui.appstate import AppState
    from ui.fonts import ui_font
    from ui.main_window import MainWindow
    from ui.styles import qss, set_theme
    from version import APP_NAME, ORG_NAME, __version__

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")
    app.setFont(ui_font(9))          # 字体探测：没装 YaHei 的机器回退到可用族

    state = AppState()               # 数据目录：便携 → 程序目录可写 → 用户数据目录
    _install_excepthook(state.logger.log_dir)
    set_theme(state.config.settings.theme)
    app.setStyleSheet(qss(state.config.settings.theme))

    # 应用退出前统一清理：中断仍在跑的 AI/扫描后台线程 + 卸载 Ollama 驻留模型。
    # 解决「关掉应用后本地模型还持续工作」——因为 Ollama 模型的 keep_alive 驻留
    # 不会因 GUI 进程退出而自动释放，必须在退出的最后一刻主动卸载（keep_alive=0）。
    def _cleanup_on_quit():
        try:
            from ui.worker import Worker
            Worker.shutdown_all()  # 请求中断所有在跑线程，限时等待
        except Exception:  # noqa: BLE001
            pass
        try:
            from core.tag_organizer import OLLAMA_MODEL, _unload_ollama
            _unload_ollama(OLLAMA_MODEL, timeout=6)  # 立即卸载驻留模型，释放显存
        except Exception:  # noqa: BLE001
            pass

    app.aboutToQuit.connect(_cleanup_on_quit)

    win = MainWindow(state)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
