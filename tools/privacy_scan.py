# -*- coding: utf-8 -*-
"""隐私扫描：源码里不许出现个人路径 / 真实密钥 / 真实内网地址。

**零第三方依赖**（只用标准库），因此 CI 的隐私守卫 job 可以直接
`python tools/privacy_scan.py` 跑，不必为了一个守卫去装 pytest 等开发依赖。

被 `tests/test_privacy.py` 与 `tools/dev/ci_local_guard.py` 共同引用 ——
逻辑只写一份，避免"测试说干净、CI 说脏"这种双份实现漂移。

⚠️ 关于反斜杠层数：这是这类扫描最经典的坑。`grep -E 'C:\\Users\\'` 里的反斜杠
层数**极易写错**，写错就变成永远不命中的空转守卫，而且看起来一切正常；
某些 shell（Windows 上的 Git Bash/MSYS）还会在传参时改写反斜杠，让本地自测
与 CI 结果不一致。所以这里的模式全部用 Python 字符串写，并且 `self_test()`
会注入一个必然命中的样本，自证扫描不是空转。

用法：
    python tools/privacy_scan.py            # 扫描整个仓库，退出码 0/1
    python tools/privacy_scan.py --self-test-only
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---------- 敏感模式 ----------
# 用片段拼装：否则本文件会"自己举报自己"。
_USER_PATH = r"C:\\+Users\\+([A-Za-z0-9_.\-]+)"
_KEY = "c54b30fef9" + "e64234b823c9228c5f7d54"          # 已知的真实 Key（只拦这一个）
_LAN = "192.168." + "31.176"                             # 已知的真实内网地址
_DOWNLOAD_DIR = "迅雷" + "下载"                            # 已知的真实下载目录名

FORBIDDEN: list[tuple[str, str]] = [
    ("个人下载目录", _DOWNLOAD_DIR),
    ("Windows 用户路径", _USER_PATH),
    ("真实 API Key", _KEY),
    ("真实内网 IP", _LAN),
]

# 明显是占位示例的用户名 → 放过
ALLOWED_USERNAMES = {"me", "user", "username", "sometest", "test", "yourname",
                     "your-user-name", "<user>", "example"}

# 允许出现的通用示例
ALLOWED_HINTS = ("192.168.1.10", "192.168.1.")

TEXT_SUFFIXES = (".py", ".md", ".json", ".toml", ".yml", ".yaml", ".txt",
                 ".cfg", ".ini", ".bat", ".cmd", ".vbs", ".ps1", ".example")
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache",
             ".pytest_home", "dist", "build", "_patch"}
# 本文件与守卫自身（含反面例子/注入样本）豁免
SELF_FILES = {"tools/privacy_scan.py", "tests/test_privacy.py"}

# 运行态产物：绝不能出现在仓库里
RUNTIME_FILES = ("settings.json", "rules.json", "archive.db", "config.json",
                 "portable.txt")
RUNTIME_DIRS = ("logs", "dist", "build", "data")


def source_files(root: pathlib.Path = ROOT) -> list[pathlib.Path]:
    """要扫描的文本文件列表（已跳过缓存/构建目录与本文件）。"""
    out: list[pathlib.Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if SKIP_DIRS & set(p.parts):
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(p)
        if rel in SELF_FILES:
            continue
        out.append(p)
    return out


def read_text(p: pathlib.Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scan(root: pathlib.Path = ROOT) -> list[str]:
    """返回违规描述列表；空列表 = 干净。"""
    violations: list[str] = []
    compiled = [(label, re.compile(pat)) for label, pat in FORBIDDEN]

    for p in source_files(root):
        rel = str(p.relative_to(root)) if root in p.parents or p.is_relative_to(root) else str(p)
        for lineno, line in enumerate(read_text(p).splitlines(), 1):
            if any(h in line for h in ALLOWED_HINTS):
                continue
            for label, rx in compiled:
                m = rx.search(line)
                if not m:
                    continue
                if m.groups() and m.group(1).lower() in ALLOWED_USERNAMES:
                    continue
                violations.append(f"{rel}:{lineno}: 出现「{label}」— {line.strip()[:100]}")
    return violations


def self_test() -> tuple[bool, str]:
    """注入一个必然命中的样本，证明扫描不是空转。样本写在仓库**外面**。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ntf-privacy-probe-"))
    probe = tmp / "_probe.py"
    probe.write_text('x = "C:' + chr(92) + 'Users' + chr(92) + 'realsecretuser"\n',
                     encoding="utf-8")
    try:
        hits = scan(tmp)
        if not hits:
            return False, "注入的样本没有被匹配 —— 扫描是空转的"
        if "Windows 用户路径" not in hits[0]:
            return False, f"命中但分类不对：{hits[0]}"
        return True, "注入样本被正确识别"
    finally:
        probe.unlink(missing_ok=True)
        tmp.rmdir()


def check_runtime_artifacts(root: pathlib.Path = ROOT) -> list[str]:
    problems: list[str] = []
    for name in RUNTIME_FILES:
        if (root / name).exists():
            problems.append(f"仓库根存在运行态文件：{name}")
    for name in RUNTIME_DIRS:
        if (root / name).exists():
            problems.append(f"仓库根存在运行态目录：{name}/")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test-only", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--strict-artifacts", action="store_true",
                    help="运行态目录（build/ dist/ logs/）存在即失败。"
                         "本地打包后这些目录本来就有，所以默认只提示；"
                         "CI 的隐私守卫 job 用这个开关做硬检查。")
    args = ap.parse_args()
    root = pathlib.Path(args.root).resolve()

    ok, msg = self_test()
    print(f"[守卫自检] {'✓' if ok else '✗'} {msg}")
    if args.self_test_only:
        return 0 if ok else 1

    print(f"[扫描范围] {len(source_files(root))} 个文本文件")
    for label, _pat in FORBIDDEN:
        print(f"           - 检查「{label}」")

    violations = scan(root)
    artifacts = check_runtime_artifacts(root)

    if violations or not ok:
        print("\n发现隐私问题：")
        for v in violations:
            print(f"  ✗ {v}")
        if not ok:
            print(f"  ✗ {msg}")
        return 1

    if artifacts:
        head = "发现运行态产物：" if args.strict_artifacts else \
               "提示：当前存在运行态产物（默认提示，本地打包后属正常；CI 用 --strict-artifacts 判失败）："
        print("\n" + head)
        for a in artifacts:
            print(f"  {'✗' if args.strict_artifacts else '·'} {a}")
        if args.strict_artifacts:
            return 1

    print("\n未发现个人数据 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
