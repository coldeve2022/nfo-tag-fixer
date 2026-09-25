# -*- coding: utf-8 -*-
"""日志系统：界面实时 + 落盘 logs/YYYY-MM-DD.log + 应用修改类 JSON 结构化输出。

设计：
- 每条记录含：时间戳 / 操作类型 / 文件路径 / 字段变更 (old -> new) / 结果
- 应用修改类操作额外输出 JSON 行（category=apply），便于程序化追溯与导出
- UI 通过 set_sink(callable) 挂接界面输出
- **日志目录不可写时静默降级**：宁可没有日志，也不能因为日志失败让软件起不来
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Callable, Optional

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


class AppLogger:
    """轻量日志器，线程安全（操作文件时加锁）。"""

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        self._sink: Optional[Callable[[str], None]] = None
        self._lock = threading.Lock()
        self.enabled = True
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError:
            self.enabled = False
        if self.enabled:
            self.enabled = self._probe()

    def _probe(self) -> bool:
        """确认真的能写（只读共享/ACL 会骗过 os.access）。"""
        try:
            p = os.path.join(self.log_dir, ".write_test")
            with open(p, "w", encoding="utf-8") as f:
                f.write("ok")
            os.unlink(p)
            return True
        except OSError:
            return False

    def set_sink(self, fn: Optional[Callable[[str], None]]) -> None:
        """挂接界面回调，接收带颜色的 HTML 行。"""
        self._sink = fn

    # ---------- 落盘 ----------

    def _file(self) -> str:
        return os.path.join(self.log_dir, datetime.now().strftime("%Y-%m-%d") + ".log")

    def _append(self, line: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            try:
                with open(self._file(), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    # ---------- 核心 ----------

    def log(self, category: str, message: str, *, path: str = "",
            field: str = "", old: str = "", new: str = "",
            result: str = "", level: str = "INFO") -> None:
        """记录一条日志。

        category: 操作类型（import/analyze/apply/rollback/archive/search/settings/system...）
        category 为 apply/rollback/archive 或 result 非空时，额外输出 JSON 行。
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [f"[{ts}]", f"[{category.upper()}]"]
        if path:
            parts.append(path)
        if field:
            parts.append(f"{field}: {old} -> {new}")
        if message:
            parts.append(message)
        if result:
            parts.append(f"结果: {result}")
        line = " ".join(parts)

        self._append(line)
        if self._sink:
            color = {"INFO": "#9fe8a6", "WARN": "#ffd77a", "ERROR": "#ff7b7b"}.get(level, "#8ab4f8")
            html = (f"<span style='color:#808080'>{ts}</span> "
                    f"<span style='color:{color}'>{line[len(ts) + 3:]}</span>")
            try:
                self._sink(html)
            except Exception:  # noqa: BLE001
                pass

        # 结构化 JSON 行：应用修改 / 回滚 / 建档
        if category in ("apply", "rollback", "archive", "archive_apply") or (path and result):
            try:
                j = {
                    "ts": ts, "category": category, "path": path,
                    "field": field, "old": old, "new": new,
                    "message": message, "result": result,
                }
                self._append(json.dumps(j, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                pass

    def info(self, category: str, message: str, **kw) -> None:
        kw.setdefault("level", "INFO")
        self.log(category, message, **kw)

    def warn(self, category: str, message: str, **kw) -> None:
        kw.setdefault("level", "WARN")
        self.log(category, message, **kw)

    def error(self, category: str, message: str, **kw) -> None:
        kw.setdefault("level", "ERROR")
        self.log(category, message, **kw)

    # ---------- 便捷 ----------

    def open_log_dir(self) -> str:
        return self.log_dir
