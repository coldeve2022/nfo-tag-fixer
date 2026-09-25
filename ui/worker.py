# -*- coding: utf-8 -*-
"""后台线程任务封装：把重活（扫描 / AI 网络请求 / 批量写盘）移出 Qt 主线程，UI 不再卡死。

背景：原代码所有重操作都跑在 GUI 线程，期间 Qt 事件循环被完全阻塞——界面冻结、
进度条不动、取消按钮失效，本地大模型一次 requests.post(timeout=300) 就会卡死整段。

本模块提供：
- Worker(QThread)：在子线程跑任意 fn，跨线程发 progress / result_ready / failed 信号
  （Qt 自动把跨线程连接排成队列，线程安全）。
  约定：fn 可接收一个名为 _worker 的关键字参数（即 Worker 自身），
        · 用 _worker.report(done, total, label) 回报进度
        · 用 _worker.cancelled() 检查是否已被取消（用于批间/文件间提前退出）
- run_with_progress(parent, title, label, fn, args, kwargs, on_done, on_error,
                    cancellable, initial_total)：一行式把「阻塞函数 + 模态进度弹窗 +
                    取消」串起来；on_done(result, worker) / on_error(exc, worker) 在**主线程**回调。
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QProgressDialog


class Worker(QThread):
    # 跨线程安全：Qt 会自动把连接排成队列（QueuedConnection）
    progress = Signal(int, int, str)        # done, total(可为 0 表示未知), label
    result_ready = Signal(object)           # 任务返回值
    failed = Signal(object)                 # 异常对象（带 _tb 属性=完整堆栈）

    # 全局活跃 worker 注册表：任何后台 AI/扫描任务启动时登记，应用退出时可统一中断。
    # 解决「应用关了但 AI Worker 线程还在跑、Ollama 模型持续占算力」的问题。
    _registry: list["Worker"] = []

    def __init__(self, fn: Callable, args: tuple = (), kwargs: Optional[dict] = None,
                 parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = tuple(args)
        self._kwargs = dict(kwargs) if kwargs else {}
        # 暂停机制：用一个 Event 表示「暂停中」——置位=暂停，清位=运行。
        # 跨线程：pause()/resume() 从 GUI 线程调，wait_if_paused() 从 worker 线程批间调。
        self._paused = threading.Event()
        self._stop_event = threading.Event()

    def run(self):
        Worker._registry.append(self)  # 登记：运行中
        try:
            self._kwargs["_worker"] = self
            # 若启动前已被暂停（极端情况），先等住
            self.wait_if_paused()
            result = self._fn(*self._args, **self._kwargs)
            self.result_ready.emit(result)
        except Exception as e:  # noqa: BLE001
            e._tb = traceback.format_exc()  # 挂到异常上，便于上层写日志
            self.failed.emit(e)
        finally:
            try:
                Worker._registry.remove(self)  # 解除登记：运行结束
            except ValueError:
                pass

    def report(self, done: int, total: int = 0, label: str = "") -> None:
        """在子线程里调用，回报进度（跨线程信号）。"""
        self.progress.emit(done, total, label)

    def cancelled(self) -> bool:
        """在子线程里调用，检查是否已被用户取消。"""
        return self.isInterruptionRequested()

    # ---- 暂停/恢复 API（GUI 线程调用）----
    def pause(self) -> None:
        """请求暂停：置位暂停事件。worker 在下一批开始前会停下。"""
        self._paused.set()

    def resume(self) -> None:
        """请求恢复：清暂停事件，worker 从暂停处继续。"""
        self._paused.clear()

    def wait_if_paused(self) -> None:
        """在子线程的批间调用：若被暂停则阻塞，直到恢复或请求中断。

        返回后调用方应检查 self.cancelled()——若用户同时点了取消，应放弃任务。
        阻塞期间不消耗算力（threading.Event.wait 释放 GIL）。
        """
        # 循环等待：暂停中则停；若期间被取消也要能跳出
        while self._paused.is_set():
            if self.isInterruptionRequested():
                return
            self._paused.wait(timeout=0.5)

    @staticmethod
    def shutdown_all(timeout_ms: int = 6000) -> int:
        """请求中断所有仍在运行的 Worker，并等待它们结束（限时，避免卡死退出）。

        用于应用关闭（closeEvent / aboutToQuit）：让 AI/扫描后台线程停下来，
        配合 _unload_ollama 立即释放 Ollama 驻留的模型。返回仍存活数（0=全部结束）。
        """
        alive = [w for w in list(Worker._registry) if w.isRunning()]
        if not alive:
            return 0
        for w in alive:
            w.requestInterruption()  # 批间/文件间会检查 cancelled() 提前退出
        # 限时等待：给线程一个自然收敛的窗口（单次 requests 无法硬打断，需它自个超时返回）
        deadline = time.time() + timeout_ms / 1000.0
        while alive and time.time() < deadline:
            alive = [w for w in alive if w.isRunning()]
            if alive:
                QThread.msleep(100)
        return len([w for w in alive if w.isRunning()])


def run_with_progress(parent, title: str, label: str, fn: Callable,
                      args: tuple = (), kwargs: Optional[dict] = None,
                      on_done: Optional[Callable] = None,
                      on_error: Optional[Callable] = None,
                      cancellable: bool = True, initial_total: int = 0,
                      pausable: bool = False, pause_label: str = "暂停",
                      resume_label: str = "继续",
                      on_pause_toggle: Optional[Callable] = None):
    """在后台线程跑 fn，期间弹模态进度条；fn 通过 _worker 汇报进度/可取消/可暂停。

    on_done(result, worker) / on_error(exc, worker) 在**主线程**回调，可安全操作 UI。
    返回 worker（任务结束后由 finished 信号自动 deleteLater）。

    pausable=True 时进度栏带「暂停/继续」切换按钮（点暂停后 worker 在批间停下、不再发
    新请求但保持模型驻留，可随时继续或取消）。on_pause_toggle(is_paused) 用于外部感知状态。
    """
    from PySide6.QtWidgets import QPushButton
    from PySide6.QtCore import Qt

    prog = QProgressDialog(label, "取消" if cancellable else None,
                           0, max(0, initial_total), parent)
    prog.setWindowTitle(title)
    prog.setWindowModality(Qt.WindowModal)
    prog.setMinimumDuration(0)
    prog.setAutoClose(False)
    prog.setAutoReset(False)

    # 暂停/继续按钮：动态切换文本
    pause_btn = None
    if pausable:
        pause_btn = QPushButton(pause_label, prog)
        prog.setCancelButton(pause_btn)

    worker = Worker(fn, args=args, kwargs=kwargs)
    worker.finished.connect(worker.deleteLater)  # 跑完自动释放，避免悬空

    def _on_progress(d, t, l):
        if t and t > 0:
            prog.setMaximum(t)
        else:
            prog.setMaximum(0)  # 未知总数 → 忙等待动画
        prog.setValue(d)
        if l:
            prog.setLabelText(l)

    _closed = {"v": False}

    def _close():
        if not _closed["v"]:
            _closed["v"] = True
            prog.accept()

    def _on_result(r):
        if on_done:
            on_done(r, worker)
        _close()

    def _on_error(e):
        if on_error:
            on_error(e, worker)
        _close()

    # 暂停/恢复切换
    _state = {"paused": False}

    def _toggle_pause():
        if _state["paused"]:
            worker.resume()
            pause_btn.setText(pause_label)
            _state["paused"] = False
            if on_pause_toggle:
                on_pause_toggle(False)
        else:
            worker.pause()
            pause_btn.setText(resume_label)
            _state["paused"] = True
            if on_pause_toggle:
                on_pause_toggle(True)

    worker.progress.connect(_on_progress)
    worker.result_ready.connect(_on_result)
    worker.failed.connect(_on_error)
    if pausable and pause_btn:
        pause_btn.clicked.connect(_toggle_pause)
    if cancellable and not pausable:
        # 非暂停模式下取消按钮直接中断
        prog.canceled.connect(worker.requestInterruption)
        prog.rejected.connect(worker.requestInterruption)
    elif pausable:
        # 暂停模式下：点右上角 X 视作取消
        prog.rejected.connect(worker.requestInterruption)

    worker.start()
    prog.exec()  # 本地事件循环；worker 信号在 exec 期间被处理，进度条实时更新
    return worker
