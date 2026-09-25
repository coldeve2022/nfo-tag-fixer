# -*- coding: utf-8 -*-
"""版本号一致性校验：version.py / pyproject.toml / git 标签 / CHANGELOG。

为什么单独一个脚本：这段逻辑写在 workflow 的 `run: |` 里很难写对 ——
YAML 块标量里嵌 `python -c "多行代码"` 会因为缩进问题让块提前结束，
报出来的错还是 "could not find expected ':'"，跟真正的问题八竿子打不着。
抽成脚本后本地也能跑同一条命令。

用法：
    python scripts/check_version.py                # 只校验 version.py 与 pyproject.toml
    python scripts/check_version.py v1.3.0         # 再校验标签与 CHANGELOG
    python scripts/check_version.py v1.3.0 --fix-pyproject   # 不一致时同步 pyproject
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8_stdout  # noqa: E402

force_utf8_stdout()


def read_version_py() -> str:
    ns: dict = {}
    exec((ROOT / "version.py").read_text(encoding="utf-8"), ns)  # noqa: S102
    return str(ns["__version__"])


def read_pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("::error::pyproject.toml 里找不到 version 字段")
    return m.group(1)


def write_pyproject_version(old: str, new: str) -> None:
    p = ROOT / "pyproject.toml"
    text = p.read_text(encoding="utf-8")
    text = text.replace(f'version = "{old}"', f'version = "{new}"', 1)
    p.write_text(text, encoding="utf-8")
    # 回读校验：编辑在带杀软/索引服务的机器上可能静默失效
    back = p.read_text(encoding="utf-8")
    assert f'version = "{new}"' in back, "pyproject.toml 写入未生效"


def changelog_has(ver: str) -> bool:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return f"## [{ver}]" in text


def emit_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", nargs="?", default="", help="git 标签，如 v1.3.0")
    ap.add_argument("--fix-pyproject", action="store_true",
                    help="pyproject 与 version.py 不一致时自动同步")
    args = ap.parse_args()

    ver = read_version_py()
    pv = read_pyproject_version()
    print(f"version.py   = {ver}")
    print(f"pyproject    = {pv}")

    if ver != pv:
        if args.fix_pyproject:
            print(f"  [同步] pyproject.toml {pv} → {ver}")
            write_pyproject_version(pv, ver)
            pv = read_pyproject_version()
            assert pv == ver
        else:
            print("::error::version.py 与 pyproject.toml 版本不一致")
            return 1

    if args.tag:
        tag_ver = args.tag[1:] if args.tag.startswith("v") else args.tag
        print(f"git tag      = {tag_ver}")
        if tag_ver != ver:
            print(f"::error::标签 {args.tag} 与 version.py {ver} 不一致")
            return 1
        if not changelog_has(ver):
            print(f"::error::CHANGELOG.md 里没有 ## [{ver}] 段落")
            return 1
        print(f"CHANGELOG 已记录 v{ver} ✓")

    emit_output("version", ver)
    print(f"\n版本一致 ✓  v{ver}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
