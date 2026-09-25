# -*- coding: utf-8 -*-
"""Jellyfin 同步客户端：通过官方 REST API 更新条目元数据（Tags/Genres）。

用途：NFO 应用修改后，直接同步到 Jellyfin 数据库，避免 Jellyfin 数据库
残留旧值（如"有码"）在实时监控/扫描时回写 NFO。

Jellyfin 10.11 要点：
- GET /Items/{itemId} 必须带 userId → 用 /Users/{userId}/Items/{itemId}
- 更新条目用 POST /Items/{itemId}，body 为 BaseItemDto；
  Name 不能为 None（缺失时从 Path 文件名推导），复杂只读字段需剔除
- 只改 Tags/Genres 两个字段，不碰 UserData（收藏/已阅/进度安全）
"""

from __future__ import annotations

import os

# 提交时剔除的复杂/只读字段（避免 Jellyfin 反序列化 500）
DROP_FIELDS = ("MediaSources", "MediaStreams", "GenreItems", "ImageBlurHashes",
               "Trickplay", "UserData", "ImageTags", "BackdropImageTags",
               "ScreenshotImageTags", "ChapterImages")


class JellyfinClient:
    def __init__(self, server: str = "", api_key: str = ""):
        self.server = (server or "").rstrip("/")
        self.api_key = api_key or ""
        self._session = None
        self._user_id: str | None = None

    # ---------- 基础 ----------

    def _req(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({"X-Emby-Token": self.api_key})
        return self._session

    @property
    def configured(self) -> bool:
        return bool(self.server and self.api_key)

    def ping(self) -> tuple[bool, str]:
        """测试连接：返回 (是否成功, 服务器名或错误)。"""
        if not self.configured:
            return False, "未配置服务器地址或 API Key"
        try:
            r = self._req().get(f"{self.server}/System/Info", timeout=8)
            if r.status_code == 200:
                info = r.json()
                return True, info.get("ServerName", "Jellyfin")
            return False, f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]

    def _get_user_id(self) -> str | None:
        """获取第一个用户 id（缓存）。"""
        if self._user_id:
            return self._user_id
        try:
            r = self._req().get(f"{self.server}/Users", timeout=10)
            if r.status_code == 200:
                users = r.json()
                if users:
                    self._user_id = users[0]["Id"]
                    return self._user_id
        except Exception:  # noqa: BLE001
            pass
        return None

    # ---------- 条目查找 ----------

    def find_item(self, number: str, nfo_path: str = "") -> str | None:
        """按番号搜索条目，返回 itemId。nfo_path 提供时按目录精确匹配。"""
        if not self.configured:
            return None
        try:
            r = self._req().get(f"{self.server}/Items", params={
                "searchTerm": number,
                "includeItemTypes": "Movie,Video",
                "recursive": "true",
                "fields": "Path",
                "limit": 20,
            }, timeout=15)
            if r.status_code != 200:
                return None
            items = r.json().get("Items", [])
            if not items:
                return None
            if nfo_path:
                folder = os.path.dirname(nfo_path)
                fl = folder.lower().replace("\\", "/")
                for it in items:
                    p = (it.get("Path") or "").replace("\\", "/").lower()
                    if p.startswith(fl) or fl in p:
                        return it["Id"]
                # 找不到目录匹配则退回第一个
                return items[0]["Id"]
            return items[0]["Id"]
        except Exception:  # noqa: BLE001
            return None

    # ---------- 更新元数据 ----------

    def update_tags_genres(self, item_id: str, tags: list[str],
                           genres: list[str]) -> bool:
        """更新条目的 Tags 与 Genres（同步 NFO 修改到 Jellyfin 数据库）。"""
        if not self.configured or not item_id:
            return False
        try:
            s = self._req()
            user_id = self._get_user_id()
            if not user_id:
                return False
            # 1. 获取条目当前数据（带 userId）
            r = s.get(f"{self.server}/Users/{user_id}/Items/{item_id}",
                      params={"fields": "Path"}, timeout=15)
            if r.status_code != 200:
                return False
            item = r.json()
            # 2. Name 不能为 None，缺失时从 Path 文件名推导（保持原名）
            name = item.get("Name")
            if not name:
                path = item.get("Path") or ""
                name = os.path.splitext(os.path.basename(path))[0] or "Item"
            item["Name"] = name
            # 3. 修改目标字段
            item["Tags"] = list(tags)
            item["Genres"] = list(genres)
            # 4. 剔除复杂/只读字段
            for k in DROP_FIELDS:
                item.pop(k, None)
            # 5. 提交更新
            r2 = s.post(f"{self.server}/Items/{item_id}", json=item, timeout=20)
            return r2.status_code in (200, 204)
        except Exception:  # noqa: BLE001
            return False

    def sync_nfo(self, number: str, nfo_path: str, tags: list[str],
                 genres: list[str]) -> tuple[bool, str]:
        """一键同步：查找条目并更新。返回 (是否成功, 说明)。"""
        if not self.configured:
            return False, "未配置 Jellyfin"
        item_id = self.find_item(number, nfo_path)
        if not item_id:
            return False, f"未找到条目 {number}"
        if self.update_tags_genres(item_id, tags, genres):
            return True, f"已同步 {number} (Tags/Genres)"
        return False, f"更新失败 {number}"

    def refresh_library(self) -> tuple[bool, str]:
        """触发 Jellyfin 全库元数据刷新。

        NFO 改了标签后，Jellyfin 不会自动读取；调用 POST /Library/Refresh
        让 Jellyfin 重新扫描所有媒体、重读 NFO，从而清掉 NFO 里已删除的旧标签
        并重建筛选/侧栏索引。返回 (是否成功, 说明)。
        """
        if not self.configured:
            return False, "未配置 Jellyfin"
        try:
            r = self._req().post(f"{self.server}/Library/Refresh", timeout=15)
            if r.status_code in (200, 202, 204):
                return True, "已触发全库刷新，等待 Jellyfin 重新扫描后标签会自动更新"
            return False, f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]
