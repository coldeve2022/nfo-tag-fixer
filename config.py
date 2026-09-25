# -*- coding: utf-8 -*-
"""配置管理：软件设置 + 标签映射规则，JSON 存储，支持导出/导入。

settings.json   —— 软件设置
rules.json      —— 标签映射规则（匹配组 / 目标 tag / 操作模式 / 备份开关）

设计要点：
- **写入原子**（临时文件 + os.replace）：断电/被杀进程不会留下半个坏配置；
- **加载自愈**：配置里若残留另一台机器的路径（例如 `J:\\...` 而本机没有 J 盘），
  加载时自动清空该字段，交给「自动查找 / 默认目录」兜底，避免启动即崩；
- 目录不可写时不抛异常，只记录，保证界面仍能起来。
"""

from __future__ import annotations

import json
import os
import shutil

from core.appdirs import atomic_write_text, drive_exists, is_writable_dir, path_reachable
from core.mapper import Mapper, MappingRule

APP_DIR_NAME = "nfo-tag-fixer"


def _clean_path_field(value: str, *, kind: str) -> str:
    """把配置里的路径字段规范化；本机不可达（盘符不存在 / 根不存在）则清空。

    kind="file" 检查父目录是否可达（文件本身可以还没创建）；
    kind="dir"  检查路径本身或最近的已存在祖先是否可达。
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if not drive_exists(raw):
        return ""
    if kind == "file":
        target = os.path.dirname(raw)
        if not os.path.splitext(raw)[1]:        # 用户填的是目录
            target = raw
    else:
        target = raw
    return raw if path_reachable(target or ".") else ""


class Settings:
    """软件设置。字段增删请同步 ``__slots__`` 与 ``from_dict``。"""

    __slots__ = ("backup_enabled", "target_encoding", "recursive_scan",
                 "ffprobe_path", "enable_ffprobe", "archive_db_path", "log_dir",
                 "theme", "last_rules_path", "exclude_folders",
                 "censored_tags", "uncensored_tags", "target_tag",
                 "jellyfin_server", "jellyfin_api_key", "sync_jellyfin")

    def __init__(self, backup_enabled: bool = True, target_encoding: str = "utf-8",
                 recursive_scan: bool = True, ffprobe_path: str = "",
                 enable_ffprobe: bool = False, archive_db_path: str = "",
                 log_dir: str = "", theme: str = "dark", last_rules_path: str = "",
                 exclude_folders: list | None = None,
                 censored_tags: list | None = None,
                 uncensored_tags: list | None = None, target_tag: str = "",
                 jellyfin_server: str = "", jellyfin_api_key: str = "",
                 sync_jellyfin: bool = False):
        self.backup_enabled = backup_enabled        # 改前备份 .bak
        self.target_encoding = target_encoding      # 统一写回编码
        self.recursive_scan = recursive_scan        # 嵌套扫描
        self.ffprobe_path = ffprobe_path            # ffprobe 路径（空=自动查找）
        self.enable_ffprobe = enable_ffprobe        # 是否启用 ffprobe 弱线索（默认关）
        self.archive_db_path = archive_db_path      # 档案库路径（空=数据目录下 archive.db）
        self.log_dir = log_dir                      # 日志目录（空=数据目录下 logs）
        self.theme = theme                          # dark / light
        self.last_rules_path = last_rules_path      # 上次导入/导出的规则文件
        self.exclude_folders = list(exclude_folders or ["#recycle", "@eadir"])
        self.censored_tags = list(censored_tags or [])      # 用户确认的有码类 tag
        self.uncensored_tags = list(uncensored_tags or [])  # 用户确认的无码类 tag
        self.target_tag = target_tag                # 目标 tag（无码类）
        self.jellyfin_server = jellyfin_server      # 如 http://192.168.1.10:8096
        self.jellyfin_api_key = jellyfin_api_key
        self.sync_jellyfin = sync_jellyfin

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        if not isinstance(d, dict):
            return cls()
        s = cls()
        s.backup_enabled = bool(d.get("backup_enabled", True))
        s.target_encoding = str(d.get("target_encoding", "utf-8"))
        s.recursive_scan = bool(d.get("recursive_scan", True))
        s.ffprobe_path = str(d.get("ffprobe_path", ""))
        s.enable_ffprobe = bool(d.get("enable_ffprobe", False))
        s.archive_db_path = str(d.get("archive_db_path", ""))
        s.log_dir = str(d.get("log_dir", ""))
        s.theme = "light" if str(d.get("theme", "dark")).lower() == "light" else "dark"
        s.last_rules_path = str(d.get("last_rules_path", ""))
        s.exclude_folders = [str(x) for x in d.get("exclude_folders", ["#recycle", "@eadir"])
                             if str(x).strip()]
        s.censored_tags = [str(x) for x in d.get("censored_tags", []) if str(x).strip()]
        s.uncensored_tags = [str(x) for x in d.get("uncensored_tags", []) if str(x).strip()]
        s.target_tag = str(d.get("target_tag", ""))
        s.jellyfin_server = str(d.get("jellyfin_server", "")).strip()
        s.jellyfin_api_key = str(d.get("jellyfin_api_key", "")).strip()
        s.sync_jellyfin = bool(d.get("sync_jellyfin", False))
        return s

    # ---------- 自愈 ----------

    def heal(self, app_dir: str) -> list[str]:
        """清掉本机不可达的路径字段，返回被清空的字段名（供日志/诊断）。"""
        cleared: list[str] = []
        for field, kind in (("ffprobe_path", "file"), ("archive_db_path", "file"),
                            ("log_dir", "dir"), ("last_rules_path", "file")):
            raw = getattr(self, field)
            if not raw:
                continue
            fixed = _clean_path_field(raw, kind=kind)
            if not fixed and not os.path.isabs(raw):
                fixed = raw            # 相对路径（少见）原样保留
            if fixed != raw:
                setattr(self, field, fixed)
                cleared.append(field)
        if not is_writable_dir(app_dir):
            cleared.append("app_dir")
        return cleared


class ConfigManager:
    """管理应用数据目录下的配置与规则文件。"""

    def __init__(self, app_dir: str):
        self.app_dir = app_dir
        try:
            os.makedirs(app_dir, exist_ok=True)
        except OSError:
            pass
        self.settings_path = os.path.join(app_dir, "settings.json")
        self.rules_path = os.path.join(app_dir, "rules.json")
        self.default_db_path = os.path.join(app_dir, "archive.db")
        self.default_log_dir = os.path.join(app_dir, "logs")
        self.settings = Settings()
        self.mapper = Mapper()
        self.healed_fields: list[str] = []
        self.load_all()

    # ---------- 路径解析 ----------

    @property
    def archive_db_path(self) -> str:
        """档案库实际生效路径（父目录不可达时回退到数据目录）。"""
        p = self.settings.archive_db_path or self.default_db_path
        parent = os.path.dirname(os.path.abspath(p)) or "."
        if not path_reachable(parent):
            return self.default_db_path
        return os.path.abspath(p)

    @property
    def log_dir(self) -> str:
        return self.settings.log_dir or self.default_log_dir

    @property
    def log_dir_effective(self) -> str:
        """日志目录实际生效路径（不可写则回退数据目录）。"""
        d = self.log_dir
        return d if is_writable_dir(d) else self.default_log_dir

    # ---------- 加载 ----------

    def load_all(self) -> None:
        self.load_settings()
        self.load_rules()

    def load_settings(self) -> None:
        if not os.path.exists(self.settings_path):
            return
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                self.settings = Settings.from_dict(json.load(f))
        except (OSError, ValueError):
            self.settings = Settings()
            return
        # 换机器后配置里的绝对路径会失真 —— 加载时自愈，避免后续启动即崩
        self.healed_fields = self.settings.heal(self.app_dir)

    def load_rules(self) -> None:
        if not os.path.exists(self.rules_path):
            return
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.mapper = Mapper.from_dict_list(data)
        except (OSError, ValueError):
            self.mapper = Mapper()

    # ---------- 保存（原子写） ----------

    def save_settings(self) -> str | None:
        """保存设置。返回错误信息或 None。"""
        try:
            atomic_write_text(
                self.settings_path,
                json.dumps(self.settings.to_dict(), ensure_ascii=False, indent=2))
            return None
        except OSError as e:
            return f"设置保存失败: {e}"

    def save_rules(self) -> str | None:
        try:
            atomic_write_text(
                self.rules_path,
                json.dumps(self.mapper.to_dict_list(), ensure_ascii=False, indent=2))
            return None
        except OSError as e:
            return f"规则保存失败: {e}"

    # ---------- 规则导入导出 ----------

    def export_rules(self, path: str) -> int:
        """导出规则到指定 JSON 文件。返回规则数。"""
        from version import __version__
        atomic_write_text(path, json.dumps({
            "app": APP_DIR_NAME,
            "version": 1,
            "generator": f"{APP_DIR_NAME} {__version__}",
            "rules": self.mapper.to_dict_list(),
        }, ensure_ascii=False, indent=2))
        self.settings.last_rules_path = os.path.abspath(path)
        self.save_settings()
        return len(self.mapper.rules)

    def import_rules(self, path: str) -> int:
        """从 JSON 导入规则（追加合并，按 id 去重）。返回新增数。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("rules", [])
        if not isinstance(data, list):
            raise ValueError("规则文件格式不正确：应为规则数组或含 rules 字段的对象")
        existing = {r.id for r in self.mapper.rules}
        added = 0
        for d in data:
            if not isinstance(d, dict):
                continue
            rule = MappingRule.from_dict(d)
            if rule.id in existing:
                continue
            self.mapper.rules.append(rule)
            existing.add(rule.id)
            added += 1
        self.save_rules()
        self.settings.last_rules_path = os.path.abspath(path)
        self.save_settings()
        return added

    # ---------- 模板 ----------

    def default_rules(self) -> list[MappingRule]:
        """预置规则模板（按刮削源场景），可在设置页一键套用。"""
        return [
            MappingRule(name="有码 → 无码破解（标准）",
                        match_tags=["有码", "censored"],
                        operation="replace", new_tag="无码破解"),
            MappingRule(name="追加无码标记",
                        match_tags=["有码", "censored"],
                        operation="append", new_tag="无码"),
            MappingRule(name="清理流出类旧标签",
                        match_tags=["流出", "leaked"],
                        operation="remove"),
            MappingRule(name="uncensored 系列统一",
                        match_tags=[r"uncensored|decensored"],
                        operation="replace", new_tag="无码", use_regex=True),
        ]

    # ---------- 其他 ----------

    def backup_rules_file(self) -> None:
        """规则变更前备份旧文件。"""
        if os.path.exists(self.rules_path):
            try:
                shutil.copy2(self.rules_path, self.rules_path + ".bak")
            except OSError:
                pass
