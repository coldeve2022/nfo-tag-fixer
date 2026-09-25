# -*- coding: utf-8 -*-
"""workflow 体检脚本：确认它既**不误报**、也**不是空转**。

这条用例的存在理由：workflow 被 GitHub 拒绝时的表现是
「run 直接失败、0 个 job、连一行日志都没有」，本地完全没有反馈；
而写一个本地检查又很容易写成"永远不报"或"什么都报"。
所以两个方向都要钉住。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_lint_workflows", ROOT / "tools" / "dev" / "lint_workflows.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_lint_workflows"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def linter():
    return _load()


def test_repo_workflows_pass(linter):
    problems: list[str] = []
    for p in linter.find_workflows():
        problems += linter.lint(p)
    assert not problems, "workflow 体检未通过：\n" + "\n".join(problems)


def test_detects_runner_in_job_level_env(linter, tmp_path):
    """反向验证：注入 `${{ runner.temp }}` 到 job 级 env，必须被抓到。"""
    wf = tmp_path / "bad.yml"
    wf.write_text(
        "name: x\n"
        "on: push\n"
        "jobs:\n"
        "  a:\n"
        "    runs-on: ubuntu-latest\n"
        "    env:\n"
        "      P: ${{ runner.temp }}/x\n"
        "    steps:\n"
        "      - run: echo hi\n",
        encoding="utf-8")
    problems = linter.lint(wf)
    assert problems and "runner" in problems[0]


def test_detects_steps_in_job_level_if(linter, tmp_path):
    wf = tmp_path / "bad2.yml"
    wf.write_text(
        "name: x\n"
        "on: push\n"
        "jobs:\n"
        "  a:\n"
        "    runs-on: ubuntu-latest\n"
        "    if: ${{ steps.build.outputs.ok == '1' }}\n"
        "    steps:\n"
        "      - id: build\n"
        "        run: echo hi\n",
        encoding="utf-8")
    problems = linter.lint(wf)
    assert problems and "steps" in problems[0]


def test_does_not_flag_legal_matrix_in_job_name(linter, tmp_path):
    """job 级 `name` 里用 ${{ matrix.* }} 是常见且合法的写法，不能误报。"""
    wf = tmp_path / "good.yml"
    wf.write_text(
        "name: x\n"
        "on: push\n"
        "jobs:\n"
        "  a:\n"
        "    name: test ${{ matrix.os }} / py${{ matrix.python }}\n"
        "    runs-on: ${{ matrix.os }}\n"
        "    strategy:\n"
        "      matrix:\n"
        "        os: [ubuntu-latest]\n"
        "        python: ['3.12']\n"
        "    steps:\n"
        "      - run: echo hi\n",
        encoding="utf-8")
    assert linter.lint(wf) == []


def test_detects_tab_indentation(linter, tmp_path):
    wf = tmp_path / "tab.yml"
    wf.write_text("name: x\non: push\njobs:\n\ta:\n", encoding="utf-8")
    problems = linter.lint(wf)
    assert any("制表符" in p for p in problems)


def test_detects_yaml_syntax_error(linter, tmp_path):
    wf = tmp_path / "broken.yml"
    wf.write_text("name: x\non: push\njobs:\n  a:\n   runs-on: [oops\n",
                  encoding="utf-8")
    problems = linter.lint(wf)
    assert problems
