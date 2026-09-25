# -*- coding: utf-8 -*-
"""单一版本来源。

窗口标题、侧边栏、关于对话框、exe 版本资源、README 与 pyproject.toml 全部引用
本文件——散落的版本号必然写歪（曾经出现过"界面写 1.2、文件属性写 1.0"）。
发版时只改这里，再跑 scripts/build_release.py 同步 pyproject 与版本资源。
"""

from __future__ import annotations

__version__ = "1.3.0"
__version_info__ = (1, 3, 0)

APP_NAME = "NFO 标签批量修改工具"
APP_NAME_EN = "nfo-tag-fixer"
APP_ID = "nfo-tag-fixer"          # 数据目录名 / 便携标记名
EXE_NAME = "NFO标签批量修改工具"    # 打包产物的可执行文件名（不含空格，便于命令行）
ORG_NAME = "coldeve2022"
HOMEPAGE = "https://github.com/coldeve2022/nfo-tag-fixer"

__author__ = "coldeve2022"
__license__ = "MIT"
__copyright__ = "Copyright (c) 2026 coldeve2022"

# 一条命令即可拿到用于发布说明的字符串
__all__ = [
    "__version__", "__version_info__", "APP_NAME", "APP_NAME_EN", "APP_ID",
    "EXE_NAME", "ORG_NAME", "HOMEPAGE", "__author__", "__license__",
    "__copyright__", "window_title",
]


def window_title(suffix: str = "") -> str:
    """统一窗口标题：`应用名 vX.Y.Z`（可选后缀）。"""
    base = f"{APP_NAME} v{__version__}"
    return f"{base} — {suffix}" if suffix else base
