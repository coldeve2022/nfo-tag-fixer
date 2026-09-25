# -*- coding: utf-8 -*-
"""破解档案库 (SQLite)：记录每次破解修改，支持按番号/演员检索、导出。

要点：
- **库打不开不能拖垮软件**：路径不可写时降级为内存库并把原因记在 ``error`` 上，
  界面照常可用（只是本次运行不落档），而不是启动即崩；
- **批量写入一次提交**：原先每条记录一次 ``commit()``，几千条建档要几十秒磁盘同步；
- 演员字段用 ``,`` 连接后按 LIKE 模糊匹配——单表、单机、几万行规模下完全够用。
"""

from __future__ import annotations

import csv
import os
import sqlite3
from datetime import datetime
from typing import Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS archive (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nfo_path     TEXT NOT NULL,
    video_path   TEXT,
    number       TEXT,
    actors       TEXT,
    original_tag TEXT,
    new_tag      TEXT,
    file_hash    TEXT,
    file_size    INTEGER,
    mtime        TEXT,
    created_at   TEXT NOT NULL
)
"""


class ArchiveDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.error: Optional[str] = None
        self._conn = self._open(db_path)
        try:
            self._init_schema()
        except sqlite3.Error as e:
            self.error = f"初始化档案表失败: {e}"

    # ---------- 连接 ----------

    def _open(self, db_path: str) -> sqlite3.Connection:
        try:
            parent = os.path.dirname(os.path.abspath(db_path)) or "."
            os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except (OSError, sqlite3.Error) as e:
            self.error = f"档案库不可用（改用临时内存库）: {e}"
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            return conn

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(SCHEMA)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_number ON archive(number)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_actors ON archive(actors)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_nfo ON archive(nfo_path)")
        self._conn.commit()

    @property
    def available(self) -> bool:
        return self.error is None

    # ---------- 写入 ----------

    def _upsert(self, cur: sqlite3.Cursor, *, nfo_path: str, video_path: str = "",
                number: str = "", actors: str = "", original_tag: str = "",
                new_tag: str = "", file_hash: str = "", file_size: int = 0,
                mtime: str = "", now: str) -> int:
        cur.execute("SELECT id FROM archive WHERE nfo_path=? ORDER BY id DESC LIMIT 1",
                    (nfo_path,))
        row = cur.fetchone()
        if row:
            cur.execute("""
                UPDATE archive SET video_path=?, number=?, actors=?, original_tag=?,
                    new_tag=?, file_hash=?, file_size=?, mtime=?, created_at=?
                WHERE id=?
            """, (video_path, number, actors, original_tag, new_tag,
                  file_hash, file_size, mtime, now, row["id"]))
            return int(row["id"])
        cur.execute("""
            INSERT INTO archive (nfo_path, video_path, number, actors, original_tag,
                new_tag, file_hash, file_size, mtime, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (nfo_path, video_path, number, actors, original_tag,
              new_tag, file_hash, file_size, mtime, now))
        return int(cur.lastrowid or 0)

    def add_record(self, *, nfo_path: str, video_path: str = "", number: str = "",
                   actors: str = "", original_tag: str = "", new_tag: str = "",
                   file_hash: str = "", file_size: int = 0,
                   mtime: str = "") -> int:
        """新增一条档案记录，返回 id。同一 NFO 已存在则更新旧记录。"""
        cur = self._conn.cursor()
        now = datetime.now().isoformat(timespec="seconds")
        rid = self._upsert(cur, nfo_path=nfo_path, video_path=video_path,
                           number=number, actors=actors, original_tag=original_tag,
                           new_tag=new_tag, file_hash=file_hash,
                           file_size=file_size, mtime=mtime, now=now)
        self._conn.commit()
        return rid

    def add_records(self, records: Iterable[dict]) -> int:
        """批量新增：一次事务一次提交（比逐条 commit 快一个数量级）。"""
        cur = self._conn.cursor()
        now = datetime.now().isoformat(timespec="seconds")
        n = 0
        try:
            for r in records:
                data = dict(r)
                data.pop("id", None)
                data.setdefault("now", now)
                self._upsert(cur, **data)
                n += 1
            self._conn.commit()
        except (sqlite3.Error, TypeError):
            self._conn.rollback()
            raise
        return n

    # ---------- 查询 ----------

    def search(self, number: str = "", actor: str = "") -> list[dict]:
        """按番号 / 演员模糊检索。"""
        sql = "SELECT * FROM archive WHERE 1=1"
        params: list = []
        if number:
            sql += " AND (number LIKE ? OR nfo_path LIKE ?)"
            params += [f"%{number}%", f"%{number}%"]
        if actor:
            sql += " AND actors LIKE ?"
            params.append(f"%{actor}%")
        sql += " ORDER BY created_at DESC, id DESC"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def match_by_actor(self, actor: str) -> list[dict]:
        """精确匹配某个演员的档案记录（用于找回助手检索）。"""
        rows = self._conn.execute(
            "SELECT * FROM archive WHERE actors LIKE ? ORDER BY created_at DESC",
            (f"%{actor}%",)).fetchall()
        return [dict(r) for r in rows]

    def find_by_number(self, number: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM archive WHERE number=? ORDER BY id DESC", (number,)).fetchall()
        return [dict(r) for r in rows]

    def find_by_nfo(self, nfo_path: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM archive WHERE nfo_path=? ORDER BY id DESC LIMIT 1",
            (nfo_path,)).fetchone()
        return dict(row) if row else None

    def all_records(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM archive ORDER BY created_at DESC, id DESC").fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM archive").fetchone()[0])

    def numbers_by_actor(self, actor: str) -> list[str]:
        """某演员的全部番号（从档案库索引，用于找回）。"""
        rows = self._conn.execute(
            "SELECT number FROM archive WHERE actors LIKE ? AND number != ''",
            (f"%{actor}%",)).fetchall()
        return [r["number"] for r in rows]

    # ---------- 维护 ----------

    def delete_record(self, rid: int) -> bool:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM archive WHERE id=?", (rid,))
        self._conn.commit()
        return cur.rowcount > 0

    def export_csv(self, path: str) -> int:
        """导出全部记录到 CSV（UTF-8 BOM，Excel 友好）。返回行数。"""
        records = self.all_records()
        if not records:
            try:
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    f.write("")
            except OSError:
                pass
            return 0
        keys = list(records[0].keys())
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(records)
        return len(records)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "ArchiveDB":
        return self

    def __exit__(self, *args) -> None:
        self.close()
