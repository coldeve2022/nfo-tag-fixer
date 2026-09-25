# -*- coding: utf-8 -*-
"""共享应用状态：config / logger / archive / finder。"""

from __future__ import annotations

from config import ConfigManager
from core.appdirs import resolve_data_dir
from core.archive import ArchiveDB
from core.finder import Finder
from core.logger import AppLogger


class AppState:
    def __init__(self, app_dir: str | None = None):
        # app_dir 为 None 时按「便携 → 程序目录可写 → 用户数据目录」自动定根，
        # 避免装在 Program Files 时写配置失败导致软件起不来。
        if app_dir:
            self.app_dir = app_dir
            self.data_origin = "显式指定"
        else:
            root, self.data_origin = resolve_data_dir()
            self.app_dir = str(root)

        self.config = ConfigManager(self.app_dir)
        self.logger = AppLogger(self.config.log_dir_effective)
        self.archive = ArchiveDB(self.config.archive_db_path)
        self.finder = Finder(self.config.settings.ffprobe_path,
                             enable_ffprobe=self.config.settings.enable_ffprobe)
        self.quit_requested = False
        self._log_startup()

    def _log_startup(self) -> None:
        from core.appdirs import is_frozen
        from version import __version__
        self.logger.info(
            "system",
            f"v{__version__} 启动｜数据目录 {self.app_dir}（{self.data_origin}）"
            f"｜{'打包版' if is_frozen() else '源码版'}")
        if not self.logger.enabled:
            self.logger.warn("system", "日志目录不可写，本次运行不落盘日志")
        if self.config.healed_fields:
            self.logger.warn(
                "system",
                "配置里存在本机不可达的路径，已自动重置为默认："
                + ", ".join(self.config.healed_fields))
        if self.archive.error:
            self.logger.error("system", f"档案库不可用：{self.archive.error}")

    @property
    def is_dark(self) -> bool:
        return self.config.settings.theme == "dark"

    @property
    def ffprobe_path(self) -> str:
        return self.config.settings.ffprobe_path

    def refresh_finder(self) -> None:
        """设置保存后刷新 Finder（模块级固化会让设置改动不生效）。"""
        self.finder = Finder(self.config.settings.ffprobe_path,
                             enable_ffprobe=self.config.settings.enable_ffprobe)

    def close(self) -> None:
        try:
            self.archive.close()
        except Exception:  # noqa: BLE001
            pass
        self.quit_requested = True
