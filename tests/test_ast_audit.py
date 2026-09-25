# -*- coding: utf-8 -*-
"""把静态审计固化成用例。

`self._私有方法()` 调用了但类内没定义、顶层函数被重复定义 —— 这类缺陷
import 能过、页面能构造，只有真的点到那个按钮才炸。所以用 AST 常驻守卫，
下次重构改了方法名，CI 立刻红，而不是等用户点出来。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_auditor():
    spec = importlib.util.spec_from_file_location(
        "_ast_audit", ROOT / "tools" / "audit" / "ast_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def auditor():
    return _load_auditor()


def _sources() -> list[pathlib.Path]:
    return sorted(p for p in ROOT.rglob("*.py")
                  if "__pycache__" not in p.parts
                  and "tests" not in p.parts
                  and "_patch" not in p.parts
                  and not p.name.startswith("_"))
def test_ast_parse_all_sources(auditor):
    import ast

    for p in _sources():
        ast.parse(p.read_text(encoding="utf-8"))     # 语法必须干净


def test_no_duplicate_toplevel_definitions(auditor):
    import ast

    problems: list[str] = []
    for p in _sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        problems += auditor.check_duplicate_defs(p.relative_to(ROOT), tree)
    assert not problems, "重复定义（后一份会静默覆盖前一份）：\n" + "\n".join(problems)


def test_private_self_calls_are_defined(auditor):
    import ast

    problems: list[str] = []
    for p in _sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        problems += auditor.check_private_calls(p.relative_to(ROOT), tree)
    assert not problems, "调用了但没定义的私有方法：\n" + "\n".join(problems)


def test_self_reads_are_assigned(auditor):
    import ast

    problems: list[str] = []
    for p in _sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        problems += auditor.check_self_reads(p.relative_to(ROOT), tree)
    assert not problems, "读取了但从没赋值的属性：\n" + "\n".join(problems)


# ---------- 关键模块必须具备的基本能力 ----------

REQUIRED = {
    "config.py": ["ConfigManager", "Settings"],
    "version.py": ["__version__", "APP_NAME", "window_title"],
    "core/appdirs.py": ["resolve_data_dir", "atomic_write_text", "is_writable_dir"],
    "core/console.py": ["force_utf8_stdout"],
    "core/toolchain.py": ["find_tool", "probe_encoder", "doctor"],
    "core/nfo.py": ["NfoFile", "detect_encoding"],
    "core/mapper.py": ["Mapper", "MappingRule"],
    "core/tag_organizer.py": ["norm_key", "suggest_similar_merges", "apply_organize"],
    "core/archive.py": ["ArchiveDB"],
    "core/logger.py": ["AppLogger"],
    "ui/fonts.py": ["ui_font", "ui_font_family", "mono_font_family"],
    "ui/styles.py": ["qss", "cell_color", "check_bg", "set_theme"],
    "ui/widgets.py": ["CheckTable", "colored_cell", "SortItem", "open_in_explorer"],
}


@pytest.mark.parametrize("rel,names", sorted(REQUIRED.items()))
def test_module_exposes_required_names(rel, names):
    import ast

    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    defined = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
    missing = [n for n in names if n not in defined]
    assert not missing, f"{rel} 缺少 {missing}"
