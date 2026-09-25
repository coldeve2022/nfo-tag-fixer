# -*- coding: utf-8 -*-
"""本地预演 CI：静态检查、workflow 体检、隐私守卫、版本一致、工作区干净。

"仓库里配了 CI" ≠ "CI 能过"。只要还没推上去，那些 job 就从没执行过 ——
这个脚本把 CI 里能本地跑的部分全跑一遍，推送前先跑它。

用法：python tools/dev/ci_local_guard.py
"""

from __future__ import annotations

import pathlib
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
    from tools.privacy_scan import check_runtime_artifacts, scan, self_test
    from tools.privacy_scan import source_files as _source_files

    failures: list[str] = []

    # ---------- 1) 静态检查 ----------
    print("[1/6] ruff 静态检查")
    r = subprocess.run([sys.executable, "-m", "ruff", "check", ".",
                        "--output-format=concise"], cwd=ROOT, capture_output=True)
    if r.returncode not in (0, 1):
        # 当前解释器里没装 ruff → 退回 PATH 上的 ruff
        r = subprocess.run(["ruff", "check", ".", "--output-format=concise"],
                           cwd=ROOT, capture_output=True)
    out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
    if r.returncode != 0:
        failures.append("ruff 未通过:\n" + out[-800:])
        print("      失败")
    else:
        print(f"      {out.splitlines()[-1] if out else '通过'}")

    # ---------- 2) workflow 体检 ----------
    print("[2/6] workflow 体检")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "dev" / "lint_workflows.py")],
                       cwd=ROOT, capture_output=True)
    out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
    if r.returncode != 0:
        failures.append("workflow 体检未通过:\n" + out[-600:])
        print("      失败")
    else:
        print("      通过")

    # ---------- 3) 版本一致 ----------
    print("[3/6] 版本号一致")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_version.py")],
                       cwd=ROOT, capture_output=True)
    out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
    if r.returncode != 0:
        failures.append("版本号不一致:\n" + out[-400:])
        print("      失败")
    else:
        print("      通过")

    # ---------- 4) 隐私守卫 ----------
    print("[4/6] 隐私守卫")
    ok, msg = self_test()
    print(f"      守卫自检：{'✓' if ok else '✗'} {msg}")
    if not ok:
        failures.append("隐私守卫是空转的")
    violations = scan(ROOT)
    for v in violations:
        failures.append(v)
    print(f"      扫描 {len(_source_files())} 个文件，"
          f"{'通过' if not violations else f'发现 {len(violations)} 处违规'}")
    artifacts = check_runtime_artifacts(ROOT)
    if artifacts:
        print("      提示：存在运行态产物（本地打包后正常，CI 会判失败）")
        for a in artifacts:
            print(f"        · {a}")

    # ---------- 5) git 索引 ----------
    print("[5/6] git 索引检查")
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

    # ---------- 6) 工作区 ----------
    print("[6/6] 工作区是否干净")
    rc, out = git("status", "--porcelain")
    if rc == 0 and out:
        print(f"      有 {len(out.splitlines())} 项未提交改动（提交前正常，CI 会判红）")
    else:
        print("      干净")

    if failures:
        print("\n发现问题：")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("\n全部通过。可以提交了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
