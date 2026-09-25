# -*- coding: utf-8 -*-
"""本地预演 CI 的隐私守卫与工作区检查。

CI 里那几步是用 Python 写的（不依赖 shell 反斜杠转义），这里在本地跑同一逻辑，
避免"配了 CI 但从没执行过"。

用法：python tools/dev/ci_local_guard.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8_stdout  # noqa: E402

force_utf8_stdout()


def git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    return r.returncode, (r.stdout or b"").decode("utf-8", errors="replace").strip()


def main() -> int:
    from tests.test_privacy import _FORBIDDEN, _read, _source_files

    failures: list[str] = []

    print("[1/4] 运行态数据文件检查")
    for name in ("settings.json", "rules.json", "archive.db", "config.json"):
        if (ROOT / name).exists():
            failures.append(f"仓库根存在 {name}")
    for name in ("logs", "dist", "build"):
        if (ROOT / name).exists():
            failures.append(f"仓库根存在目录 {name}/")
    print(f"      {'通过' if not failures else '失败'}")

    print("[2/4] git 索引检查")
    rc, out = git("ls-files")
    if rc != 0:
        print("      还不是 git 仓库，跳过")
    else:
        bad = [ln for ln in out.splitlines()
               if ln in ("settings.json", "rules.json", "archive.db", "portable.txt")
               or ln.startswith(("logs/", "dist/", "build/", "data/"))]
        if bad:
            failures.append(f"索引里含不该提交的文件: {bad[:5]}")
        print(f"      索引 {len(out.splitlines())} 个文件，"
              f"{'通过' if not bad else '失败'}")

    print("[3/4] 源码隐私扫描")
    for label, pattern in _FORBIDDEN:
        rx = re.compile(pattern)
        hits = []
        for p in _source_files():
            for i, line in enumerate(_read(p).splitlines(), 1):
                m = rx.search(line)
                if not m:
                    continue
                from tests.test_privacy import _ALLOWED_USERNAMES, ALLOWED_HINTS
                if any(h in line for h in ALLOWED_HINTS):
                    continue
                if m.groups() and m.group(1).lower() in _ALLOWED_USERNAMES:
                    continue
                hits.append(f"{p.relative_to(ROOT)}:{i}")
        if hits:
            failures.append(f"出现「{label}」：{hits[:5]}")
    print(f"      扫描 {len(_source_files())} 个文件，"
          f"{'通过' if len(failures) == 0 else '失败'}")

    print("[4/4] 守卫自检（证明扫描不是空转）")
    # 探针写到仓库**外面**的临时目录：这样它既不可能被本次扫描读到，
    # 也不可能因为中途异常而遗留在仓库里污染后续检查。
    import tempfile

    probe_dir = pathlib.Path(tempfile.mkdtemp(prefix="ntf-guard-"))
    probe = probe_dir / "_guard_probe.py"
    probe.write_text('x = "C:' + chr(92) + 'Users' + chr(92) + 'realsecretuser"\n',
                     encoding="utf-8")
    try:
        pattern = dict(_FORBIDDEN)["Windows 用户路径"]
        if not re.search(pattern, _read(probe)):
            failures.append("隐私守卫是空转的：注入的样本没被匹配")
            print("      失败")
        else:
            print("      注入样本被正确识别，通过")
    finally:
        probe.unlink(missing_ok=True)
        probe_dir.rmdir()

    print("[5/5] 工作区是否干净")
    rc, out = git("status", "--porcelain")
    if rc == 0 and out:
        failures.append("工作区有未提交改动（CI 会判红）：\n" + out[:600])
    print(f"      {'通过' if not out else '有改动（提交前正常）'}")

    if failures:
        print("\n发现问题：")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
