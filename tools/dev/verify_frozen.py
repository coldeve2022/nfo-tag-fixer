# -*- coding: utf-8 -*-
"""冻结产物验证：真启动 GUI 并检查应用自己的错误日志。

**进程"活着"不等于没崩**：窗口版 exe 没有控制台，未捕获异常全靠 `sys.excepthook`
落到 `logs/crash.log`。我遇到过"进程存活 6 秒、看起来正常"，实际是构造页面时
AttributeError，主窗口根本没显示出来 —— 只有日志能暴露它。

另外宿主/终端会在命令结束时回收子进程树，用普通方式起的 GUI 会"看起来自己死了"，
所以必须用 DETACHED_PROCESS 起，再用 GetExitCodeProcess 采样判断是否存活。

用法：python tools/dev/verify_frozen.py "dist/NFO标签批量修改工具/NFO标签批量修改工具.exe"
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8_stdout  # noqa: E402

force_utf8_stdout()

DETACHED = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS|NEW_PROCESS_GROUP|CREATE_NO_WINDOW
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def read_version_resource(exe: Path) -> dict[str, str]:
    """读 exe 的版本资源（右键属性里能看到的那份）。"""
    if sys.platform != "win32":
        return {}
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        return {}
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(exe), 0, size, buf):
        return {}
    out: dict[str, str] = {}
    ptr = ctypes.c_void_p()
    length = ctypes.c_uint()
    if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr),
                          ctypes.byref(length)) and length.value >= 4:
        lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
        for key in ("FileVersion", "ProductVersion", "ProductName",
                    "LegalCopyright", "FileDescription"):
            sub = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{key}"
            if ver.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(length)):
                out[key] = ctypes.wstring_at(ptr, length.value).rstrip("\x00")
    return out


def read_resource_text(exe: Path) -> str:
    """读 exe 里内嵌的字符串（用于确认没有夹带个人路径）。"""
    raw = exe.read_bytes()
    found = []
    for token in (b"Settings.json", b"AppData", b"AppDemo", b"Users\\\\"):
        if token in raw:
            found.append(token.decode(errors="replace"))
    return ", ".join(found)


def main() -> int:
    exe = Path(sys.argv[1] if len(sys.argv) > 1 else "").resolve()
    if not exe.exists():
        print(f"找不到 exe: {exe}")
        return 2

    problems: list[str] = []
    print(f"目标: {exe}")
    print(f"大小: {exe.stat().st_size / 1048576:.1f} MB")

    # ---- 1. 版本资源 ----
    print("\n[1/4] 版本资源")
    info = read_version_resource(exe)
    if not info:
        problems.append("读不到版本资源")
        print("      读不到（非 Windows 或无资源）")
    else:
        for k, v in info.items():
            print(f"      {k:<16}{v}")
        from version import __version__
        if info.get("FileVersion") != __version__:
            problems.append(f"exe 版本 {info.get('FileVersion')} != version.py {__version__}")
        if __version__ not in info.get("ProductVersion", ""):
            problems.append("ProductVersion 与 version.py 不一致")

    # ---- 2. 全新安装目录下启动 ----
    print("\n[2/4] 模拟全新安装（数据目录指向空临时目录）")
    data_home = Path(tempfile.mkdtemp(prefix="ntf-verify-"))
    env = dict(os.environ)
    env["NFO_TAG_FIXER_HOME"] = str(data_home)
    env.pop("QT_QPA_PLATFORM", None)          # 要真的起窗口

    proc = subprocess.Popen([str(exe)], env=env, creationflags=DETACHED,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"      已启动 pid={proc.pid}，观察 8 秒…")
    alive_samples = 0
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid)
        code = ctypes.c_ulong()
        for _ in range(16):
            time.sleep(0.5)
            if h and k32.GetExitCodeProcess(h, ctypes.byref(code)):
                if code.value == STILL_ACTIVE:
                    alive_samples += 1
                else:
                    problems.append(f"进程提前退出，退出码 {code.value}")
                    break
        if h:
            k32.CloseHandle(h)
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)

    print(f"      存活采样 {alive_samples}/16")
    if alive_samples < 14:
        problems.append("进程没能稳定存活（可能启动即崩）")

    # ---- 3. 应用自己的错误日志（最重要的一步）----
    print("\n[3/4] 检查应用错误日志")
    crash = data_home / "logs" / "crash.log"
    if crash.exists() and crash.stat().st_size:
        problems.append("crash.log 非空：应用启动过程中抛了未捕获异常")
        print("      ✗ crash.log 有内容：")
        print("      " + crash.read_text(encoding="utf-8", errors="replace")[:1500])
    else:
        print("      ✓ 无 crash.log（没有未捕获异常）")

    day_log = sorted((data_home / "logs").glob("*.log")) if (data_home / "logs").exists() else []
    for f in day_log:
        text = f.read_text(encoding="utf-8", errors="replace")
        if "Traceback" in text or "[ERROR]" in text:
            problems.append(f"{f.name} 含错误记录")
            print(f"      ✗ {f.name} 含错误行：")
            print("      " + "\n      ".join(
                ln for ln in text.splitlines() if "ERROR" in ln or "Traceback" in ln)[:800])
        else:
            print(f"      ✓ {f.name} 无错误记录（{f.stat().st_size} 字节）")

    # ---- 4. 数据目录是否真的落地 ----
    print("\n[4/4] 数据目录落地情况")
    print(f"      数据目录: {data_home}")
    for name in ("archive.db", "logs"):
        p = data_home / name
        print(f"      {'✓' if p.exists() else '·'} {name}")
    if not (data_home / "archive.db").exists():
        problems.append("archive.db 未生成，数据目录逻辑在冻结环境下可能失效")

    shutil.rmtree(data_home, ignore_errors=True)

    print("\n" + "=" * 56)
    if problems:
        print("发现问题：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("冻结产物验证通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
