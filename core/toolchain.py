# -*- coding: utf-8 -*-
"""外部工具定位（ffprobe / ffmpeg）。

只查 `shutil.which` 不够——大量用户是把 FFmpeg 解压到某个目录、只把 `ffmpeg.exe`
加进 PATH，`ffprobe.exe` 并不在 PATH 里；也有装在 `C:\\ffmpeg\\bin` 这种"顺手解压"
位置的情况。查找顺序：

1. 用户在设置里显式填写的路径
2. 程序目录本身 + 内置子目录（`ffmpeg/`、`bin/`、`tools/`、`ffmpeg/bin/`）
3. 系统 PATH
4. 各平台常见安装位置

**缓存键含 (路径, 大小, mtime)**：换了 ffmpeg 版本、或路径被替换成另一个文件时
缓存自动失效，避免"设置里改了工具但程序还用旧的"。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_CACHE: dict[str, bool] = {}
CACHE_VERSION = 1

# ---- 平台常见安装位置（用系统环境变量拼，避免源码里出现具体盘符字面量）----

def _common_roots() -> list[Path]:
    roots: list[Path] = []
    sysdrive = os.environ.get("SystemDrive") or "C:"
    progfiles = os.environ.get("ProgramFiles") or str(Path(sysdrive + os.sep) / "Program Files")
    progdata = os.environ.get("ProgramData") or str(Path(sysdrive + os.sep) / "ProgramData")
    local = os.environ.get("LOCALAPPDATA") or ""
    home = os.path.expanduser("~")

    if sys.platform == "win32":
        for base in (Path(sysdrive + os.sep), Path(progfiles), Path(progdata),
                     Path(local) if local else None, Path(home)):
            if base is None:
                continue
            for sub in ("ffmpeg/bin", "ffmpeg", "FFmpeg/bin", "ffmpeg/bin64"):
                roots.append(base / sub)
    else:
        roots += [Path("/usr/bin"), Path("/usr/local/bin"), Path("/opt/homebrew/bin"),
                  Path("/snap/bin"), Path(home) / ".local/bin"]
    return roots


def _candidates(name: str, configured: str = "", prog_dir: Path | None = None) -> list[Path]:
    exe = name + (".exe" if sys.platform == "win32" else "")
    out: list[Path] = []

    if configured and configured.strip():
        c = Path(os.path.expandvars(os.path.expanduser(configured.strip())))
        # 允许用户填"目录"或"可执行文件"
        out.append(c if c.suffix.lower() == ".exe" or c.is_file() else c / exe)

    prog = Path(prog_dir) if prog_dir else Path(__file__).resolve().parent.parent
    for sub in ("", "bin", "tools", "ffmpeg", "ffmpeg/bin", "vendor/ffmpeg",
                "vendor/ffmpeg/bin"):
        out.append(prog / sub / exe)

    which = shutil.which(name)
    if which:
        out.append(Path(which))

    for r in _common_roots():
        out.append(r / exe)

    # 去重保序
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p).lower() if sys.platform == "win32" else str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def find_tool(name: str, configured: str = "",
              prog_dir: Path | None = None) -> str:
    """按多路兜底找到工具的可执行路径；找不到返回 ""。"""
    for p in _candidates(name, configured, prog_dir):
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    return ""


def _cache_key(tool: str, args: tuple[str, ...]) -> str:
    try:
        st = os.stat(tool)
        return f"{tool}|{st.st_size}|{int(st.st_mtime)}|{args}|v{CACHE_VERSION}"
    except OSError:
        return f"{tool}|missing|{args}|v{CACHE_VERSION}"


def _run(tool: str, args: list[str], timeout: float = 10.0):
    try:
        r = subprocess.run(
            [tool, *args], capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or b"").decode("utf-8", errors="replace") + \
              (r.stderr or b"").decode("utf-8", errors="replace")
        return r.returncode, out
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def probe_encoder(tool: str, codec: str) -> bool:
    """**真的跑一次编码**来判断本机能不能用某编码器。

    `ffmpeg -encoders` 列出 `h264_nvenc/h264_qsv/h264_amf` 只说明"这份构建编译时
    带了它们"，与本机有没有对应显卡无关——任何官方构建都会同时列出三者。
    只有在没有对应设备的机器上真跑一次，才会得到
    `Error creating a MFX session` / `amfrt64.dll failed to open`。

    尺寸别太小：64x64 会被 NVENC 以 "Frame Dimension less than ..." 拒绝，
    造成假阴性。
    """
    key = _cache_key(tool, ("probe", codec))
    if key in _CACHE:
        return _CACHE[key]

    code, out = _run(tool, ["-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.2",
                            "-frames:v", "3", "-c:v", codec, "-f", "null", "-"])
    if code == 0:
        _CACHE[key] = True
        return True
    low = out.lower()
    # 极简构建没有 lavfi → 真跑必然失败，退回编译期列表，避免全部误判
    if "lavfi" in low or "unknown input format" in low:
        result = codec in compiled_encoders(tool)
    else:
        result = False
    _CACHE[key] = result
    return result


def compiled_encoders(tool: str) -> set[str]:
    """解析 `ffmpeg -encoders` 的输出。

    **注意行首有空格**（` V....D h264_nvenc ...`），用 `line[:1] in ("V","A","S")`
    判断会全部漏掉，得到空集合 → 误判成"本机没有硬件编码器"。
    还要跳过图例行（` V..... = Video`、`------`），否则会把 `=` 当成编码器名。
    """
    key = _cache_key(tool, ("encoders",))
    cached = _CACHE.get(key)
    if isinstance(cached, set):
        return cached
    _, out = _run(tool, ["-hide_banner", "-encoders"])
    found: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        flags, name = parts[0], parts[1]
        if set(flags) <= {".", "-", "="}:      # 分隔线
            continue
        if name in ("=", "------"):            # 图例行
            continue
        if flags[:1] in ("V", "A", "S"):
            found.add(name)
    _CACHE[key] = found  # type: ignore[assignment]
    return found


def tool_version(tool: str) -> str:
    """取工具版本首行（用于诊断输出）。"""
    _, out = _run(tool, ["-version"], timeout=8)
    return (out.splitlines() or [""])[0][:120]


def doctor(ffprobe_configured: str = "") -> list[tuple[str, str]]:
    """环境自检：返回 [(项目, 结果)]，供设置页/`--doctor` 展示。"""
    rows: list[tuple[str, str]] = []

    ffprobe = find_tool("ffprobe", ffprobe_configured)
    if ffprobe:
        rows.append(("ffprobe", f"✅ {ffprobe}"))
        rows.append(("ffprobe 版本", tool_version(ffprobe)))
    else:
        rows.append(("ffprobe", "⚪ 未找到（可选：仅「破解找回」的弱线索需要）"))

    ffmpeg = find_tool("ffmpeg")
    if ffmpeg:
        rows.append(("ffmpeg", f"✅ {ffmpeg}"))
        encs = compiled_encoders(ffmpeg)
        for codec in ("libx264", "h264_nvenc", "h264_qsv", "h264_amf"):
            if codec in encs:
                ok = probe_encoder(ffmpeg, codec)
                rows.append((f"编码器 {codec}",
                             "✅ 本机可用" if ok else "⚪ 编译期存在但本机不可用"))
    else:
        rows.append(("ffmpeg", "⚪ 未找到（本工具不依赖）"))

    try:
        from send2trash import send2trash  # noqa: F401
        rows.append(("send2trash", "✅ 可用（移除冗余 NFO 走系统回收站）"))
    except ImportError:
        rows.append(("send2trash", "⚪ 未安装（将降级为本地 .nfo_trash 目录）"))

    try:
        import requests  # noqa: F401
        rows.append(("requests", "✅ 可用（AI 分析与 Jellyfin 同步）"))
    except ImportError:
        rows.append(("requests", "❌ 未安装：AI 分析与 Jellyfin 同步不可用"))
    return rows


__all__ = ["find_tool", "probe_encoder", "compiled_encoders", "tool_version", "doctor"]
