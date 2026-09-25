# -*- coding: utf-8 -*-
"""workflow 本地体检：抓住 PyYAML 放过、但 GitHub 会拒绝的写法。

为什么需要：workflow 被 GitHub 拒绝时的表现是
**run 直接失败、0 个 job、连一行日志都没有，Run 标题还会显示文件路径而不是 `name:`**。
本地 `yaml.safe_load` 通过并不能说明它合法 —— 上下文可用性是 GitHub 自己的规则。

目前检查：
1. job 级 `env` / `if` 里用了 `runner` 或 `steps` 上下文（只在 step 级可用）
2. 任何 `run:` 块里出现制表符
3. 顶层缺少 `on:` / `jobs:`
4. `runs-on` 缺失

用法：python tools/dev/lint_workflows.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8_stdout  # noqa: E402

force_utf8_stdout()

EXPR = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")

# GitHub 官方"上下文可用性"表里，job 级各键允许的上下文。
# 注意 job 级 `name` 里用 ${{ matrix.* }} 是**合法且常见**的写法（别误报），
# 而 `runner` / `steps` / `job` 在任何 job 级键里都不可用。
ALLOWED_AT_JOB_LEVEL: dict[str, set[str]] = {
    "env": {"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"},
    "if": {"github", "needs", "vars", "inputs"},
    "name": {"github", "needs", "strategy", "matrix", "vars", "inputs"},
    "concurrency": {"github", "needs", "strategy", "matrix", "inputs", "vars"},
    "runs-on": {"github", "needs", "strategy", "matrix", "vars", "inputs"},
}


def find_workflows() -> list[pathlib.Path]:
    return sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))


def _rel(path: pathlib.Path) -> str:
    """相对仓库根的显示路径；文件在仓库外（测试注入的样本）时退回绝对路径。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def lint(path: pathlib.Path) -> list[str]:
    # PyYAML 是开发依赖（requirements-dev.txt）。缺了就给明确提示，
    # 而不是抛一个看不出所以然的 ModuleNotFoundError。
    try:
        import yaml
    except ImportError:
        raise SystemExit(
            "workflow 体检需要 PyYAML：pip install -r requirements-dev.txt") from None

    text = path.read_text(encoding="utf-8")
    rel = _rel(path)
    problems: list[str] = []

    # 1) 制表符
    for i, line in enumerate(text.splitlines(), 1):
        if "\t" in line:
            problems.append(f"{rel}:{i} 行内出现制表符（YAML 不允许用 tab 缩进）")

    # 2) 语法
    try:
        doc = yaml.safe_load(text)
    except Exception as e:  # noqa: BLE001
        problems.append(f"{rel} YAML 解析失败: {e}")
        return problems

    if not isinstance(doc, dict):
        problems.append(f"{rel} 顶层不是映射")
        return problems

    # YAML 1.1 会把裸 `on:` 解析成布尔 True，所以两种键名都要认
    has_on = "on" in doc or True in doc
    if not has_on:
        problems.append(f"{rel} 缺少 on: 触发器")
    if "jobs" not in doc:
        problems.append(f"{rel} 缺少 jobs:")
        return problems

    # 3) 上下文可用性
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        if "runs-on" not in job and "uses" not in job:
            problems.append(f"{rel} job `{job_id}` 缺少 runs-on")
        for key, allowed in ALLOWED_AT_JOB_LEVEL.items():
            value = job.get(key)
            if value is None:
                continue
            blob = value if isinstance(value, str) else repr(value)
            for expr in EXPR.findall(blob):
                head = expr.split(".")[0].strip().strip("'\"")
                if head and head not in allowed:
                    problems.append(
                        f"{rel} job `{job_id}` 的 {key} 里用了 `{head}` 上下文"
                        f"（job 级 {key} 只允许 {sorted(allowed)}；"
                        f"GitHub 会报 Unrecognized named-value 并直接拒绝整个 workflow）"
                        f" → 把该表达式挪到 step 级")
    return problems


def main() -> int:
    files = find_workflows()
    if not files:
        print("没找到 workflow 文件")
        return 0
    all_problems: list[str] = []
    for p in files:
        problems = lint(p)
        print(f"{'✓' if not problems else '✗'} {p.relative_to(ROOT)}")
        all_problems += problems
    if all_problems:
        print("\n发现问题：")
        for p in all_problems:
            print(f"  ✗ {p}")
        return 1
    print("\nworkflow 体检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
