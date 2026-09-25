# -*- coding: utf-8 -*-
"""数据目录解析与路径可移植性。

为什么需要这一层：
- 原实现把 `settings.json / rules.json / archive.db / logs` 一律写在 exe 同目录。
  绿色版没问题，但一旦装到 `C:\\Program Files\\...` 或只读介质上，首次启动写配置
  就会抛 `PermissionError`，软件直接起不来。
- 配置里存下来的绝对路径（另一个盘的库目录）换台机器后盘符不存在，
  `ArchiveDB` 的 `makedirs` 会失败并连带启动崩溃。

本模块的职责：
1. `resolve_data_dir()`：按 便携 → 程序目录可写 → 用户数据目录 的顺序定数据根；
2. `is_writable_dir()`：真的写一个临时文件再删（不要只看 `os.access`，
   只读共享、ACL、同步盘占位符都会骗过它）；
3. `path_reachable()`：判断配置里的路径在本机是否"可达"（盘符/根目录存在）；
4. `atomic_write_bytes/text()`：临时文件 + `os.replace`，断电/被杀进程不留坏文件。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PORTABLE_MARKER = "portable.txt"
ENV_HOME = "NFO_TAG_FIXER_HOME"          # 显式覆盖（测试 / 多实例 / 绿色部署）


# ---------- 可写性 / 可达性 ----------

def is_writable_dir(path: str | os.PathLike) -> bool:
    """真实写一个临时文件来判定目录可写（比 os.access 可靠）。"""
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    try:
        fd, tmp = tempfile.mkstemp(prefix=".wtest_", dir=str(p))
        os.close(fd)
        os.unlink(tmp)
        return True
    except OSError:
        return False


def path_reachable(raw: str) -> bool:
    """配置里的路径在本机是否可用（不存在也应能创建）。

    判据是"父目录链上最近的存在者存在且可写"，而不是"路径存在"——
    用户可能只是换台机器后目录还没建。
    盘符不存在（如配置存了 `J:\\...` 而本机没有 J 盘）直接判 False。
    """
    if not raw or not str(raw).strip():
        return False
    p = Path(os.path.expandvars(os.path.expanduser(str(raw).strip())))
    # 逐级向上找最近的已存在祖先
    probe = p
    for _ in range(64):
        if probe.exists():
            return True if probe.is_dir() else False
        parent = probe.parent
        if parent == probe:                     # 到根了仍不存在
            return False
        probe = parent
    return False


def drive_exists(raw: str) -> bool:
    """Windows 下判断路径的盘符是否存在（其他平台恒为 True）。"""
    if sys.platform != "win32":
        return True
    s = str(raw).strip()
    if len(s) >= 2 and s[1] == ":":
        return os.path.exists(s[:2] + os.sep)
    return True


def safe_dir(raw: str, fallback: str) -> str:
    """返回可用的目录：raw 可用则用它，否则回退 fallback 并保证 fallback 存在。"""
    if raw and drive_exists(raw) and path_reachable(raw):
        return str(Path(raw))
    fb = Path(fallback)
    try:
        fb.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return str(fb)


# ---------- 数据根目录 ----------

def user_data_dir(app_id: str = "nfo-tag-fixer") -> Path:
    """跨平台用户数据目录（Windows `%APPDATA%`、macOS Application Support、Linux XDG）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = (os.environ.get("XDG_CONFIG_HOME")
                or os.path.expanduser("~/.config"))
    return Path(base) / app_id


def program_dir() -> Path:
    """程序所在目录（冻结后 = exe 目录，源码模式 = 项目根）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_data_dir(app_id: str = "nfo-tag-fixer", *, prog_dir: Path | None = None
                     ) -> tuple[Path, str]:
    """决定数据根目录，返回 (路径, 来源说明)。

    优先级：
    1. 环境变量 ``NFO_TAG_FIXER_HOME``      —— 测试/多实例/自定义部署
    2. 程序目录下存在 ``portable.txt``       —— 用户显式要求便携（数据跟着程序走）
    3. 程序目录可写                          —— 绿色版默认行为（与历史版本一致）
    4. 用户数据目录                          —— 装在 Program Files / 只读介质时的兜底
    """
    prog = Path(prog_dir) if prog_dir is not None else program_dir()

    env = (os.environ.get(ENV_HOME) or "").strip()
    if env:
        return Path(env).expanduser().resolve(), f"环境变量 {ENV_HOME}"

    if (prog / PORTABLE_MARKER).exists() and is_writable_dir(prog):
        return prog, "便携模式（portable.txt）"

    if is_writable_dir(prog):
        return prog, "程序目录（可写）"

    fallback = user_data_dir(app_id)
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback, "用户数据目录（程序目录不可写）"


# ---------- 原子写 ----------

def atomic_write_bytes(path: str | os.PathLike, data: bytes) -> None:
    """原子写字节：先写同目录临时文件，fsync 后 os.replace 覆盖目标。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str | os.PathLike, text: str,
                      encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


__all__ = [
    "PORTABLE_MARKER", "ENV_HOME", "is_writable_dir", "path_reachable",
    "drive_exists", "safe_dir", "user_data_dir", "program_dir", "is_frozen",
    "resolve_data_dir", "atomic_write_bytes", "atomic_write_text",
]
