# -*- coding: utf-8 -*-
"""通用对话框：标签统计分组确认 / 规则编辑 / 二次确认。"""

from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from core.tag_analyzer import suggest_groups


class ConfirmDialog(QMessageBox):
    """破坏性操作二次确认。"""

    @staticmethod
    def ask(parent, title: str, text: str, detail: str = "",
            default_no: bool = True) -> bool:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        if detail:
            box.setDetailedText(detail)
        yes = box.addButton("确认执行", QMessageBox.AcceptRole)
        no = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(no if default_no else yes)
        box.exec()
        return box.clickedButton() is yes


class TagAnalyzeDialog(QDialog):
    """标签统计 → 用户勾选确认「有码类 tag 组」与「无码类 tag 组」→ 指定目标 tag。

    支持批量操作：全选/清空、关键词批量勾选、智能建议。
    """

    def __init__(self, counter: Counter, parent=None,
                 pre_censored: list | None = None,
                 pre_uncensored: list | None = None,
                 pre_target: str = ""):
        super().__init__(parent)
        self.setWindowTitle("标签统计与分组确认")
        self.resize(760, 600)
        self.counter = counter
        self.censored: set[str] = set(pre_censored or [])
        self.uncensored: set[str] = set(pre_uncensored or [])
        self.target_tag: str = pre_target

        layout = QVBoxLayout(self)
        info = QLabel(f"共发现 <b>{len(counter)}</b> 种 tag（总计 {sum(counter.values())} 次出现）。\n"
                      "左列勾选「有码类」、右列勾选「无码类」。支持 <b>点击行切换勾选</b>、"
                      "关键词批量勾选、全选/清空。")
        info.setWordWrap(True)
        layout.addWidget(info)

        body = QHBoxLayout()
        # 有码类
        self.censored_list = QListWidget()
        self.censored_list.setSelectionMode(QListWidget.NoSelection)
        self.censored_list.itemClicked.connect(
            lambda it: self._toggle_item(it, "censored"))
        self._populate(self.censored_list, self.censored, "censored")
        body.addWidget(self._make_group("有码类 tag（将被替换）",
                                        self.censored_list, "censored"), 1)
        # 无码类
        self.uncensored_list = QListWidget()
        self.uncensored_list.setSelectionMode(QListWidget.NoSelection)
        self.uncensored_list.itemClicked.connect(
            lambda it: self._toggle_item(it, "uncensored"))
        self._populate(self.uncensored_list, self.uncensored, "uncensored")
        body.addWidget(self._make_group("无码类 tag（保持不变）",
                                        self.uncensored_list, "uncensored"), 1)
        layout.addLayout(body)

        # 目标 tag
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("目标无码 tag："))
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        all_tags = sorted(counter, key=lambda t: -counter[t])
        self.target_combo.addItem("")
        for t in all_tags:
            self.target_combo.addItem(t)
        if self.target_tag:
            self.target_combo.setCurrentText(self.target_tag)
        elif pre_uncensored:
            self.target_combo.setCurrentText(pre_uncensored[0])
        target_row.addWidget(self.target_combo, 1)
        btn_preset = QPushButton("✨ 一键智能建议")
        btn_preset.setProperty("class", "primary")
        btn_preset.setToolTip("按关键词自动归类所有 tag（有码类/无码类），覆盖当前勾选")
        btn_preset.clicked.connect(self._apply_suggestion)
        target_row.addWidget(btn_preset)
        layout.addLayout(target_row)

        hint = QLabel("提示：先点「一键智能建议」即可自动归组；目标 tag 选一个无码写法"
                      "（如：无码破解）。确定后可在右侧规则面板自动生成替换规则。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9aa0ac;")
        layout.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ---------- 组件 ----------

    def _make_group(self, title: str, list_widget: QListWidget, kind: str) -> QWidget:
        gb = QGroupBox(title)
        v = QVBoxLayout(gb)

        # 工具行：关键词批量 + 全选/清空
        tools = QHBoxLayout()
        kw = QLineEdit()
        kw.setPlaceholderText("关键词…")
        kw.setFixedWidth(130)
        btn_kw = QPushButton("批量勾选")
        btn_kw.setToolTip("勾选所有名称包含该关键词的 tag（如：流出、CENSORED）")
        btn_kw.clicked.connect(
            lambda: self._batch_check(list_widget, kw.text(), kind))
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(
            lambda: self._check_all(list_widget, kind, True))
        btn_none = QPushButton("清空")
        btn_none.clicked.connect(
            lambda: self._check_all(list_widget, kind, False))
        kw.returnPressed.connect(btn_kw.click)
        tools.addWidget(kw)
        tools.addWidget(btn_kw)
        tools.addWidget(btn_all)
        tools.addWidget(btn_none)
        tools.addStretch()
        v.addLayout(tools)
        v.addWidget(list_widget)
        return gb

    def _populate(self, lst: QListWidget, selected: set[str], kind: str) -> None:
        items = sorted(self.counter, key=lambda t: (-self.counter[t], t))
        for t in items:
            it = QListWidgetItem(f"{t}  ×{self.counter[t]}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setData(Qt.UserRole, t)
            it.setCheckState(Qt.Checked if t in selected else Qt.Unchecked)
            it.setForeground(Qt.red if kind == "censored" else Qt.green)
            lst.addItem(it)

    def _toggle_item(self, it: QListWidgetItem, kind: str):
        it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked
                         else Qt.Checked)
        self._sync(kind)

    def _check_all(self, lst: QListWidget, kind: str, checked: bool):
        for i in range(lst.count()):
            lst.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self._sync(kind)

    def _batch_check(self, lst: QListWidget, keyword: str, kind: str):
        kw = keyword.strip().lower()
        if not kw:
            return
        for i in range(lst.count()):
            it = lst.item(i)
            t = it.data(Qt.UserRole)
            if kw in t.lower():
                it.setCheckState(Qt.Checked)
        self._sync(kind)

    def _sync(self, kind: str):
        """把列表勾选状态同步到分组集合。"""
        lst = self.censored_list if kind == "censored" else self.uncensored_list
        target = self.censored if kind == "censored" else self.uncensored
        target.clear()
        for i in range(lst.count()):
            it = lst.item(i)
            if it.checkState() == Qt.Checked:
                target.add(it.data(Qt.UserRole))

    # ---------- 建议 / 收集 ----------

    def _apply_suggestion(self):
        """一键智能建议：按关键词自动归类所有 tag（覆盖勾选）。"""
        c_sug, u_sug = suggest_groups(self.counter)
        self.censored = set(c_sug)
        self.uncensored = set(u_sug)
        for i in range(self.censored_list.count()):
            it = self.censored_list.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in self.censored
                             else Qt.Unchecked)
        for i in range(self.uncensored_list.count()):
            it = self.uncensored_list.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in self.uncensored
                             else Qt.Unchecked)

    def _collect(self):
        self._sync("censored")
        self._sync("uncensored")
        self.target_tag = self.target_combo.currentText().strip()

    def accept(self):
        self._collect()
        super().accept()


class RuleSuggestionDialog(QDialog):
    """智能规则推荐：基于当前导入文件的 tag 统计，自动生成映射规则供勾选。

    数据规律（来自真实库挖掘）：
    - 有码类 tag 写法有限：有码 / CENSORED
    - 无码类写法：无码破解 / 无码 / 无码流出/破解 / FC2 / 系列: FC2系列 ...
    """

    def __init__(self, counter: Counter, items_count: int, parent=None,
                 censored: list | None = None, uncensored: list | None = None,
                 target: str = ""):
        super().__init__(parent)
        self.setWindowTitle("🤖 智能规则推荐（基于当前文件分析）")
        self.resize(760, 460)
        self.counter = counter
        self._items_count = items_count

        from core.mapper import MappingRule
        from core.tag_analyzer import classify_tag

        self.selected_rules: list[MappingRule] = []

        # 有码类 / 无码类 tag（关键词分类 + 用户已确认分组）
        c_set = set(censored or []) | {t for t in counter
                                       if classify_tag(t) == "censored"}
        u_set = set(uncensored or []) | {t for t in counter
                                         if classify_tag(t) == "uncensored"}
        censored_sorted = sorted(c_set, key=lambda t: -counter.get(t, 0))
        uncensored_sorted = sorted(u_set, key=lambda t: -counter.get(t, 0))

        # 目标 tag 默认值
        if not target and uncensored_sorted:
            target = max(uncensored_sorted, key=lambda t: counter.get(t, 0))
        if not target:
            target = "无码破解"
        self.target = target

        layout = QVBoxLayout(self)
        info = QLabel(f"根据当前 <b>{items_count}</b> 个文件分析：\n"
                      f"· 有码类 tag <b>{len(censored_sorted)}</b> 种："
                      f"{', '.join(censored_sorted[:6]) or '（无）'}\n"
                      f"· 无码类 tag <b>{len(uncensored_sorted)}</b> 种："
                      f"{', '.join(uncensored_sorted[:6]) or '（无）'}")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.rule_list = QListWidget()
        self.rule_list.setSelectionMode(QListWidget.NoSelection)
        self.rule_list.itemClicked.connect(self._toggle_item)
        layout.addWidget(self.rule_list, 1)

        # 候选规则
        candidates: list[tuple[str, MappingRule, str]] = []  # (说明, 规则, 影响)
        if censored_sorted:
            n_affected = sum(counter.get(t, 0) for t in censored_sorted)
            candidates.append((
                f"把有码类 tag 全部替换为「{target}」",
                MappingRule(name="🤖 有码→" + target, match_tags=censored_sorted,
                            operation="replace", new_tag=target),
                f"影响 ~{n_affected} 次 tag 出现"))
            candidates.append((
                f"保留原 tag，仅追加「{target}」标记",
                MappingRule(name="🤖 追加" + target, match_tags=censored_sorted,
                            operation="append", new_tag=target),
                f"影响 ~{n_affected} 次 tag 出现"))
        # 无码类"破解/流出"同义变体统一（FC2 平台 tag 不参与统一）
        crack_variants = [t for t in uncensored_sorted
                          if ("破解" in t or "流出" in t or "restored" in t.lower()
                              or "decensor" in t.lower() or "uncensor" in t.lower())]
        if len(crack_variants) > 1:
            top_crack = max(crack_variants, key=lambda t: counter.get(t, 0))
            others = [t for t in crack_variants if t != top_crack]
            n_uni = sum(counter.get(t, 0) for t in others)
            candidates.append((
                f"统一破解类写法：{', '.join(others[:3])} → 「{top_crack}」",
                MappingRule(name="🤖 统一破解类写法", match_tags=others,
                            operation="replace", new_tag=top_crack),
                f"影响 ~{n_uni} 次 tag"))
        # 清理"打码情况未知"类（保留，仅当存在）
        unknown = [t for t in counter if "打码情况" in t or "未知" in t]
        if unknown:
            candidates.append((
                f"清理状态 tag：{', '.join(unknown[:3])}（删除）",
                MappingRule(name="🤖 清理状态tag", match_tags=unknown,
                            operation="remove"),
                f"影响 ~{sum(counter.get(t, 0) for t in unknown)} 次 tag"))

        if not candidates:
            QMessageBox.information(self, "提示", "当前文件没有可推荐的有码/无码 tag 规律")
            self.selected_rules = []
            super().accept()  # 直接关闭
            return

        for desc, rule, impact in candidates:
            it = QListWidgetItem(f"☐ {desc}\n     {impact}")
            it.setData(Qt.UserRole, (desc, rule, impact))
            it.setData(Qt.UserRole + 1, False)
            self.rule_list.addItem(it)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("目标无码 tag："))
        self.target_edit = QLineEdit(self.target)
        target_row.addWidget(self.target_edit, 1)
        layout.addLayout(target_row)

        btn_all = QPushButton("全部勾选")
        btn_all.clicked.connect(lambda: self._check_all(True))
        btn_none = QPushButton("全部取消")
        btn_none.clicked.connect(lambda: self._check_all(False))
        layout.addWidget(btn_all)
        layout.addWidget(btn_none)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("添加所选规则")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _toggle_item(self, it):
        checked = it.data(Qt.UserRole + 1) or it.text().startswith("☑")
        checked = not checked
        it.setText(it.text().replace("☑", "☐").replace("☐", "☑") if checked
                   else it.text().replace("☑", "☐"))
        it.setData(Qt.UserRole + 1, checked)

    def _check_all(self, checked: bool):
        for i in range(self.rule_list.count()):
            it = self.rule_list.item(i)
            it.setData(Qt.UserRole + 1, checked)
            text = it.text()
            if checked:
                text = text.replace("☐", "☑")
            else:
                text = text.replace("☑", "☐")
            it.setText(text)

    def _on_ok(self):
        self.selected_rules = []
        target = self.target_edit.text().strip() or self.target
        for i in range(self.rule_list.count()):
            it = self.rule_list.item(i)
            checked = bool(it.data(Qt.UserRole + 1))
            if not checked:
                continue
            desc, rule, _impact = it.data(Qt.UserRole)
            rule.new_tag = target
            rule.name = f"🤖 {rule.name.split('🤖 ')[-1]}"
            self.selected_rules.append(rule)
        if not self.selected_rules:
            QMessageBox.information(self, "提示", "请至少勾选一条规则")
            return
        self.accept()


class RuleEditDialog(QDialog):
    """新增/编辑一条映射规则。"""

    def __init__(self, parent=None, rule=None):
        super().__init__(parent)
        self.setWindowTitle("编辑映射规则")
        self.resize(460, 300)
        from core.mapper import MappingRule
        self.rule = rule or MappingRule()

        form = QFormLayout(self)

        self.name_edit = QLineEdit(self.rule.name)
        form.addRow("规则名：", self.name_edit)

        self.match_edit = QLineEdit(", ".join(self.rule.match_tags))
        self.match_edit.setPlaceholderText("如：有码, censored（逗号分隔；勾选正则时按正则匹配）")
        form.addRow("匹配 tag：", self.match_edit)

        self.op_combo = QComboBox()
        self.op_combo.addItem("替换为 (replace)", "replace")
        self.op_combo.addItem("追加 (append)", "append")
        self.op_combo.addItem("删除 (remove)", "remove")
        idx = self.op_combo.findData(self.rule.operation)
        self.op_combo.setCurrentIndex(max(idx, 0))
        form.addRow("操作：", self.op_combo)

        self.new_tag_edit = QLineEdit(self.rule.new_tag)
        self.new_tag_edit.setPlaceholderText("replace/append 时填写目标 tag")
        form.addRow("目标 tag：", self.new_tag_edit)

        self.regex_check = QCheckBox("按正则匹配（match_tags 为正则模式）")
        self.regex_check.setChecked(self.rule.use_regex)
        form.addRow("", self.regex_check)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def accept(self):
        from core.mapper import MappingRule
        name = self.name_edit.text().strip() or "未命名规则"
        match = [t.strip() for t in self.match_edit.text().split(",") if t.strip()]
        if self.op_combo.currentData() != "remove" and not self.new_tag_edit.text().strip():
            QMessageBox.warning(self, "提示", "replace/append 操作需要填写目标 tag")
            return
        self.rule = MappingRule(
            id=self.rule.id,
            name=name,
            match_tags=match,
            operation=self.op_combo.currentData(),
            new_tag=self.new_tag_edit.text().strip(),
            use_regex=self.regex_check.isChecked(),
        )
        super().accept()


class ActorPickerDialog(QDialog):
    """破解找回：输入演员名。"""

    def __init__(self, parent=None, known_actors: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("破解找回 — 按演员检索")
        self.resize(420, 200)
        self.actor_name = ""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入演员名（支持多个，用空格或、分隔）："))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("如：相泽南 / 三上悠亚")
        layout.addWidget(self.edit)

        if known_actors:
            combo_row = QHBoxLayout()
            combo_row.addWidget(QLabel("或从档案库选择："))
            self.actor_combo = QComboBox()
            self.actor_combo.setEditable(True)
            self.actor_combo.addItems(known_actors[:200])
            combo_row.addWidget(self.actor_combo, 1)
            layout.addLayout(combo_row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("检索")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def accept(self):
        self.actor_name = self.edit.text().strip()
        if not self.actor_name and hasattr(self, "actor_combo"):
            self.actor_name = self.actor_combo.currentText().strip()
        if not self.actor_name:
            QMessageBox.warning(self, "提示", "请输入演员名")
            return
        super().accept()
