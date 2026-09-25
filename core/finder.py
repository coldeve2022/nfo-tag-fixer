# -*- coding: utf-8 -*-
"""破解找回助手：多线索加权打分 + 候选排序。

线索（权重从高到低）：
1. 修改时间聚类   —— 批量操作的时间段里视频 mtime 集中（最强线索）
2. 文件大小异常   —— 无码/破解版码率通常更高，文件更大（辅助）
3. 文件名特征     —— -U 后缀 / fc2 / 无码 / uncensored / restored 等
4. ffprobe 特征   —— 视频时长/分辨率与 NFO 元数据差异（弱线索，可选）
5. NFO 现状       —— tag 仍为有码 = 「该改」的标志

ffprobe 说明：本模块**不假定 ffprobe 在 PATH 里**。用户填的路径 > 程序目录内置 >
PATH > 平台常见安装位置，由 :mod:`core.toolchain` 统一解析；探测连续失败 3 次熔断，
避免一个坏路径把整批候选拖成几百次 15 秒超时。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

from core.toolchain import find_tool, tool_version
from core.tag_analyzer import classify_tag

# 文件名强无码特征
FILENAME_UNCENSORED_RE = [
    re.compile(r"-U(?:\.|\s|$)", re.I),
    re.compile(r"\buncensored\b", re.I),
    re.compile(r"\bdecensored\b", re.I),
    re.compile(r"无码|无修|破解", ),
    re.compile(r"\bFC2[-\s]?\d{5,}", re.I),
    re.compile(r"restored", re.I),
    re.compile(r"-HD", re.I),
]

# 聚类时间窗口（秒）：mtime 相差在窗口内视为同一批操作
CLUSTER_WINDOW = 3 * 24 * 3600


@dataclass
class ScoredCandidate:
    """候选结果。"""
    item: object
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    ffprobe_ok: bool = False

    @property
    def path(self) -> str:
        return self.item.nfo.path

    @property
    def number(self) -> str:
        return self.item.nfo.number


class Finder:
    """多线索打分器。

    注意：ffprobe 探测为可选弱线索，默认关闭（enable_ffprobe=False）。
    高频调用 ffprobe 可能触发子进程问题且耗时，仅在用户在设置中显式启用时运行，
    且连续失败 3 次自动熔断。
    """

    def __init__(self, ffprobe_path: str = "", enable_ffprobe: bool = False):
        self.ffprobe_configured = ffprobe_path or ""
        self.enable_ffprobe = enable_ffprobe
        self._ffprobe_path: str | None = None
        self._ffprobe_checked = False
        self._version_ok: bool | None = None
        self._probe_failures = 0
        self._max_probe_failures = 3

    # ---------- 工具定位 ----------

    @property
    def ffprobe_path(self) -> str:
        """按 配置 > 程序目录 > PATH > 常见位置 解析（结果缓存）。"""
        if not self._ffprobe_checked:
            self._ffprobe_path = find_tool("ffprobe", self.ffprobe_configured)
            self._ffprobe_checked = True
        return self._ffprobe_path or ""

    def reset_tool_cache(self) -> None:
        self._ffprobe_path = None
        self._ffprobe_checked = False
        self._version_ok = None

    # ---------- 打分 ----------

    def score_items(self, items: list) -> list[ScoredCandidate]:
        """对候选 items 打分并降序排列。"""
        candidates = [ScoredCandidate(item=it) for it in items]
        self._score_mtime_cluster(candidates)
        self._score_file_size(candidates)
        self._score_filename(candidates)
        self._score_nfo_status(candidates)
        if self.enable_ffprobe:
            self._score_ffprobe(candidates)
        candidates.sort(key=lambda c: (-c.score, c.item.video_mtime))
        return candidates

    def _score_mtime_cluster(self, candidates: list[ScoredCandidate]) -> None:
        """修改时间聚类：按视频 mtime 分簇，簇内文件加分。

        排序后单遍扫描即可等价于原来的"first-fit 贪心"：簇按创建顺序检查时，
        要比较的总是各簇当前的**最大值**，而排序后单调不减，因此"能塞进较早的簇"
        一定等价于"与本簇上一个元素的时间差 ≤ 窗口"。
        """
        with_mtime = [(c, c.item.video_mtime) for c in candidates
                      if c.item.video_mtime > 0]
        if len(with_mtime) < 2:
            return
        with_mtime.sort(key=lambda x: x[1])
        clusters: list[list[ScoredCandidate]] = [[with_mtime[0][0]]]
        for c, mt in with_mtime[1:]:
            if mt - clusters[-1][-1].item.video_mtime <= CLUSTER_WINDOW:
                clusters[-1].append(c)
            else:
                clusters.append([c])
        big = [cl for cl in clusters if len(cl) >= 2]
        for cl in big:
            bonus = 25 + 10 * min(len(cl), 5)
            for c in cl:
                c.score += bonus
                c.reasons.append(f"修改时间聚类({len(cl)}个文件在同一时段)")
        # 孤立文件按与最大簇中心距离给弱分
        if big:
            center = max(big, key=len)
            center_t = sum(x.item.video_mtime for x in center) / len(center)
            in_center = set(map(id, center))
            for c, _mt in with_mtime:
                if id(c) in in_center:
                    continue
                if abs(c.item.video_mtime - center_t) / 3600 <= 72:
                    c.score += 12
                    c.reasons.append("修改时间接近批量时段")

    def _score_file_size(self, candidates: list[ScoredCandidate]) -> None:
        sizes = sorted(c.item.video_size for c in candidates if c.item.video_size > 0)
        if not sizes:
            return
        median = sizes[len(sizes) // 2]
        for c in candidates:
            if c.item.video_size <= 0:
                continue
            ratio = c.item.video_size / max(median, 1)
            if ratio >= 2.0:
                c.score += 25
                c.reasons.append(f"文件大小异常(×{ratio:.1f}中位数)")
            elif ratio >= 1.3:
                c.score += 12
                c.reasons.append(f"文件偏大(×{ratio:.1f}中位数)")

    def _score_filename(self, candidates: list[ScoredCandidate]) -> None:
        for c in candidates:
            stem = c.item.nfo.stem
            hit = None
            for rx in FILENAME_UNCENSORED_RE:
                if rx.search(stem):
                    hit = rx.pattern
                    break
            if hit:
                c.score += 18
                c.reasons.append(f"文件名特征({hit})")

    def _score_nfo_status(self, candidates: list[ScoredCandidate]) -> None:
        for c in candidates:
            nfo = c.item.nfo
            if not nfo.is_valid:
                continue
            tags = nfo.tags
            if not tags:
                c.score += 8
                c.reasons.append("NFO 无 tag（需补标记）")
                continue
            classes = {classify_tag(t) for t in tags}
            if "uncensored" in classes and "censored" not in classes:
                c.score -= 15
                c.reasons.append("NFO 已是无码类（可能已改过）")
            elif "censored" in classes:
                c.score += 20
                c.reasons.append("NFO tag 仍为有码（该改）")
            else:
                c.score += 5
                c.reasons.append("tag 无法分类，待人工确认")

    def _score_ffprobe(self, candidates: list[ScoredCandidate]) -> None:
        if not self.ffprobe_available():
            return
        for c in candidates:
            if self._probe_failures >= self._max_probe_failures:
                # 熔断：连续失败说明该 ffprobe 对本库不可用，本批不再尝试
                self.enable_ffprobe = False
                self._version_ok = False
                break
            if not c.item.video_path:
                continue
            info = probe_video(c.item.video_path, self.ffprobe_path)
            if info is None:
                self._probe_failures += 1
                continue
            self._probe_failures = 0
            c.ffprobe_ok = True
            nfo_dur = c.item.nfo.runtime_minutes
            if nfo_dur and info.get("duration_min"):
                diff = abs(info["duration_min"] - nfo_dur)
                if diff >= 5:
                    c.score += 8
                    c.reasons.append(f"时长与NFO差异({diff:.0f}分钟)")
            # 分辨率
            if info.get("height") and info["height"] >= 1080:
                res = c.item.nfo.resolution
                if res and res not in ("2160p", "4k", "3840"):
                    c.score += 4
                    c.reasons.append("视频为高清，NFO 记录偏低")

    # ---------- ffprobe ----------

    def ffprobe_available(self) -> bool:
        if not self.enable_ffprobe:
            return False
        if self._version_ok is None:
            tool = self.ffprobe_path
            if not tool:
                self._version_ok = False
            else:
                # 真的执行一次 `-version`：文件存在不等于能跑
                # （架构不符 / 缺 DLL 时 CreateProcess 会直接失败）
                self._version_ok = bool(tool_version(tool))
        return self._version_ok

    def ffprobe_error(self) -> str:
        if not self.enable_ffprobe:
            return "ffprobe 探测未启用（可在设置中开启，弱线索可选）"
        if self.ffprobe_available():
            return ""
        if not self.ffprobe_path:
            return "未找到 ffprobe（已查配置路径、程序目录、PATH 与常见安装位置）"
        return "ffprobe 不可用（无法执行），弱线索（时长/分辨率）已跳过"


def probe_video(path: str, ffprobe: str) -> dict | None:
    """用 ffprobe 探测视频时长/分辨率。失败返回 None。"""
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout.decode("utf-8", errors="replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    out: dict = {}
    fmt = data.get("format", {})
    if fmt.get("duration"):
        try:
            out["duration_min"] = float(fmt["duration"]) / 60.0
        except (TypeError, ValueError):
            pass
    for st in data.get("streams", []):
        if st.get("codec_type") == "video":
            if st.get("height"):
                out["height"] = int(st["height"])
            if not out.get("duration_min") and st.get("duration"):
                try:
                    out["duration_min"] = float(st["duration"]) / 60.0
                except (TypeError, ValueError):
                    pass
            break
    return out or None
