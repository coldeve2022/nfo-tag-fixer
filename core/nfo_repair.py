# -*- coding: utf-8 -*-
"""重复 / 空 NFO 检测与修复（补救「标签丢失」的普遍问题）。

背景：同一影片目录里常并存多套 NFO——
- 另一款 JAV 工具生成的 movie.nfo（可能已被清空成 0 标签 0 类型）
- 本项目 / Jellyfin 用的正式 NFO（含正确标签）
若 Jellyfin 读到了那个空的重复 NFO，库内标签会被清空。

本模块：
1. scan_duplicates(root)：找出所有「同一目录存在多个 NFO」的目录，标记空 NFO 与最优 NFO
2. trash_files(paths, root)：把冗余 NFO 移到系统回收站（send2trash；失败则移到 .nfo_trash 子目录，可撤销）
3. 全程只读取/移动 NFO 文件，绝不改动视频、不删除"最优 NFO"。
"""

from __future__ import annotations

import os
from datetime import datetime

from core.nfo import NfoFile


def _nfo_score(nfo: NfoFile) -> int:
    """信息完整度评分：tag+genre+actor 越多越完整，有番号再 +1。"""
    return (len(nfo.tags) + len(nfo.genres) + len(nfo.actors)
            + (1 if nfo.number else 0))


# 视频扩展名（用于判断目录里有几部真实影片）
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".ts", ".m2ts", ".mov",
              ".flv", ".webm", ".rmvb", ".mpg", ".mpeg", ".m4v"}


def is_empty_nfo(nfo: NfoFile) -> bool:
    """是否为空 NFO 残壳：无标签、无类型、且无演员信息。

    必须三者皆空才算"残壳"——若某部真实影片只有 tag/genre 为 0 但
    <actor> 里有演员（或其它元数据），绝不能当空壳删除（否则丢失演员信息）。
    """
    return not nfo.tags and not nfo.genres and not nfo.actors


def scan_duplicates(root: str, on_progress=None) -> list[dict]:
    """扫描目录树，返回所有「同目录存在多个 NFO」的受影响目录。

    每项：
    dir          目录路径
    nfos         [{path,name,n_tags,n_genres,n_actors,number,score,is_empty,is_best}]
    best         最优 NFO（信息最完整）的 path 或 None
    redundant    [待移除的冗余 NFO path]（仅当它是空壳时才算"建议移除"）
    ambiguous    同目录多个有内容的 NFO（可能是多部影片或多套刮削）→ 需人工，不自动移除
    """
    issues: list[dict] = []
    for root_dir, _dirs, files in os.walk(root):
        nfo_files = [f for f in files
                     if f.lower().endswith(".nfo") and not f.lower().endswith(".bak")]
        if len(nfo_files) < 2:
            continue
        parsed = []
        for fn in nfo_files:
            p = os.path.join(root_dir, fn)
            try:
                nfo = NfoFile(p)
                if not nfo.is_valid:
                    continue
            except Exception:  # noqa: BLE001
                continue
            parsed.append({
                "path": p, "name": fn,
                "n_tags": len(nfo.tags), "n_genres": len(nfo.genres),
                "n_actors": len(nfo.actors), "number": nfo.number or "",
                "score": _nfo_score(nfo),
                "is_empty": is_empty_nfo(nfo),
                "_nfo": nfo,
            })
        if on_progress:
            on_progress(root_dir)
        if len(parsed) < 2:
            continue
        # 视频文件数：决定目录里真实影片的数量，用于自动判定
        n_videos = sum(1 for f in files
                       if os.path.splitext(f)[1].lower() in VIDEO_EXTS)
        parsed.sort(key=lambda x: (-x["score"], x["name"]))
        best = parsed[0]
        best["is_best"] = True
        for p in parsed[1:]:
            p["is_best"] = False
        # 冗余（可自动移除，都进回收站）：
        # - 空残壳：只要目录里至少有一个非空 NFO 就允许移除
        # - 若目录里【恰有 1 部视频】→ 只对应一部真实影片，非最优的 NFO 全是重复刮削 → 全部可移除
        #   （若有内容重复刮削 NFO，也并入；绝对不删最优）
        nonempty = sum(1 for p in parsed if not p["is_empty"])
        redundant: list[str] = []
        if nonempty >= 1:
            if n_videos == 1:
                redundant = [p["path"] for p in parsed[1:]]
            else:
                redundant = [p["path"] for p in parsed[1:] if p["is_empty"]]
        # 多视频（≥2 部）或视频数无法判断时，非空且未被纳入冗余的内容 NFO → 可能是不同的影片 → 需人工
        rem_nonbest_content = [p for p in parsed[1:]
                               if not p["is_empty"] and p["path"] not in redundant]
        ambiguous = len(rem_nonbest_content) > 0
        has_empty = any(p["is_empty"] for p in parsed)
        if ambiguous:
            verdict = "需人工确认"
        elif redundant:
            verdict = "可安全移除"
        else:
            verdict = "无自动处理"
        # 去掉 internal _nfo 对象（保留纯数据，便于序列化/展示）
        for p in parsed:
            p.pop("_nfo", None)
        issues.append({
            "dir": root_dir,
            "nfos": parsed,
            "best": best["path"],
            "best_name": best["name"],
            "redundant": redundant,
            "ambiguous": ambiguous,
            "has_empty": has_empty,
            "n_videos": n_videos,
            "verdict": verdict,
        })
    return issues


def trash_files(paths: list[str], fallback_root: str = "") -> tuple[int, int, list]:
    """把冗余 NFO 移到回收站（可撤销）。系统回收站不可用时降级到本地 .nfo_trash。

    返回 (进系统回收站数, 进本地.nfo_trash数, [(path, 错误)]) —— 前两者都是成功，
    errs 只含真正删除失败的项目（不会把"成功降级"误报为失败）。
    """
    ok_rec = ok_local = 0
    errs: list = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for p in paths:
        if not os.path.exists(p):
            continue
        # 1) 先尝试系统回收站
        try:
            from send2trash import send2trash
            send2trash(p)
            ok_rec += 1
            continue
        except Exception:  # noqa: BLE001
            # send2trash 有时移走/删除后仍抛异常；文件已不在即视为回收站成功
            if not os.path.exists(p):
                ok_rec += 1
                continue
        # 2) 降级：移到扫描根目录下的 .nfo_trash_<时间>（同盘，可靠、可手动恢复）
        try:
            base = fallback_root or os.path.dirname(os.path.dirname(p))
            trash_dir = os.path.join(base, f".nfo_trash_{ts}")
            os.makedirs(trash_dir, exist_ok=True)
            dst = os.path.join(trash_dir, os.path.basename(p))
            os.replace(p, dst)
            ok_local += 1
        except OSError as e:
            errs.append((p, str(e)[:80]))
    return ok_rec, ok_local, errs


# ---------- 演员名仅存于 tag 的检查与补救 ----------

def scan_actor_missing(root: str) -> list[dict]:
    """找出「tag 里含演员名、但 <actor> 字段为空」的影片。

    演员名判定：文字在库内任意影片的 <actor><name> 出现过（全局交叉验证，可靠）。
    返回 [{path, dir, names:[在tag中出现的演员名], src: "当前"|"整理前(.bak)", in_tag_now:bool}]
    """
    # 收集全局演员名
    global_actors: set[str] = set()
    files: list[str] = []
    for r, _d, fs in os.walk(root):
        for fn in fs:
            if fn.lower().endswith(".nfo") and not fn.lower().endswith(".bak"):
                files.append(os.path.join(r, fn))
    for p in files:
        try:
            n = NfoFile(p)
            if n.is_valid:
                global_actors.update(n.actors)
            bak = p + ".bak"
            if os.path.exists(bak):
                b = NfoFile(bak)
                if b.is_valid:
                    global_actors.update(b.actors)
        except Exception:  # noqa: BLE001
            continue
    # 判定
    out: list[dict] = []
    for p in files:
        try:
            n = NfoFile(p)
            if not n.is_valid:
                continue
        except Exception:  # noqa: BLE001
            continue
        cur_actors = set(n.actors)
        cur_names = set(n.tags) & global_actors
        bak = p + ".bak"
        bak_names: set[str] = set()
        if os.path.exists(bak):
            try:
                b = NfoFile(bak)
                if b.is_valid:
                    bak_names = set(b.tags) & global_actors
            except Exception:  # noqa: BLE001
                pass
        names = cur_names | bak_names
        if not names:
            continue
        if cur_actors:
            continue  # 有 actor 栏，不算丢失
        # src: 若 bak 有而当前没了 → "整理前残留已删"；若当前 tag 还有 → "当前仍存在"
        in_tag_now = bool(cur_names)
        out.append({"path": p, "names": sorted(names),
                    "in_tag_now": in_tag_now,
                    "bak_exists": os.path.exists(bak)})
    return out


def backfill_actors(files_with_names: list[dict]) -> tuple[int, list]:
    """把「仅存于 tag 的演员名」补回 <actor><name> 字段，防止信息丢失。

    files_with_names: scan_actor_missing 返回的项。
    修改前生成 .bak 备份；每个名字只补一次（已存在则跳过）。
    返回 (成功数, [(path, 错误)]).
    """
    import xml.etree.ElementTree as ET
    ok, errs = 0, []
    for item in files_with_names:
        p = item["path"]
        names = item.get("names") or []
        if not names:
            continue
        try:
            n = NfoFile(p)
            if not n.is_valid:
                continue
            have = set(n.actors)
            to_add = [x for x in names if x not in have and x.strip()]
            if not to_add:
                continue
            root = n.root
            for x in to_add:
                act = ET.SubElement(root, "actor")
                nm = ET.SubElement(act, "name")
                nm.text = x
            n.mark_dirty()
            e = n.save(backup=True)
            if e:
                errs.append((p, e))
            else:
                ok += 1
        except Exception as e:  # noqa: BLE001
            errs.append((p, str(e)[:80]))
    return ok, errs
