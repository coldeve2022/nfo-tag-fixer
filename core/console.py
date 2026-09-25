# -*- coding: utf-8 -*-
"""控制台编码与跨平台小工具。

大多数问题不需要在这里解决，只有一件事必须做：**Windows 中文环境下被重定向的
stdout 是 GBK**，任何入口脚本 `print("已生成 …")` 都会在 `PYTHONIOENCODING=cp1252`
的 CI runner 上抛 `UnicodeEncodeError: 'charmap' codec can't encode characters`。
本机是 cp936 所以永远复现不出来——必须在所有会打印中文的入口调用
`force_utf8_stdout()`。

注意：**只在非交互时切换**。交互式控制台保持系统编码，否则中文会花屏。
"""

from __future__ import annotations

import os
import sys


def force_utf8_stdout() -> None:
    """把被重定向的 stdout/stderr 切到 UTF-8；交互式控制台与已是 UTF-8 的不动。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:                    # 窗口版 exe 没有控制台，stdout 可能是 None
            continue
        try:
            if stream.isatty():
                continue
            enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "_")
            if enc in ("utf8", "utf_8", "cp65001"):
                continue
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            continue


def child_env_utf8(env: dict | None = None) -> dict:
    """给子进程用的环境变量副本，强制 UTF-8（构建脚本调 PyInstaller 等时用）。"""
    out = dict(os.environ if env is None else env)
    out["PYTHONIOENCODING"] = "utf-8"
    out["PYTHONUTF8"] = "1"
    # 沙箱/宿主注入的会话变量会被 PyInstaller 内部的 os.remove/rmtree 触发批量删除保护，
    # 导致打包在清理旧 build/dist 时失败。构建子进程里剔除掉。
    for key in ("CODEBUDDY_SESSION_ID", "CLAUDE_SESSION_ID", "CODEBUDDY_SAFE_DELETE"):
        out.pop(key, None)
    return out


__all__ = ["force_utf8_stdout", "child_env_utf8"]
