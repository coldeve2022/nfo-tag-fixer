# -*- coding: utf-8 -*-
"""AST 静态审计：找出「页面能构造、点一下才崩」的缺陷。

三类筛子：
  1. self._xxx() 调用但类内没有定义（只查下划线开头，几乎无误报）
  2. self.X 读取但从未被赋值（含 QWidget 基类白名单）
  3. 模块内重复定义的顶层函数/类（后一份静默覆盖前一份）

用法：python tools/audit/ast_audit.py [根目录]
"""

from __future__ import annotations

import ast
import collections
import sys
from pathlib import Path

# Qt 基类常见成员白名单（避免把基类方法误报成缺陷）
QT_BASE_MEMBERS = {
    "layout", "setLayout", "parent", "setParent", "children", "deleteLater",
    "close", "show", "hide", "update", "repaint", "resize", "move", "setWindowTitle",
    "setWindowIcon", "setEnabled", "setVisible", "setMinimumWidth", "setFixedHeight",
    "setMaximumHeight", "setMinimumHeight", "setFixedWidth", "setSizePolicy",
    "setObjectName", "setContentsMargins", "setSpacing", "setAlignment",
    "setStyleSheet", "setToolTip", "setStatusTip", "setWhatsThis", "setAccessibleName",
    "setShortcut", "setCheckable", "setChecked", "isChecked", "setCurrentIndex",
    "currentIndex", "count", "addItem", "addItems", "clear", "setText", "text",
    "setPlaceholderText", "setReadOnly", "setValue", "value", "setRange",
    "setSingleStep", "setRowCount", "rowCount", "setColumnCount", "columnCount",
    "insertRow", "insertColumn", "setItem", "item", "setHorizontalHeaderLabels",
    "setHorizontalHeaderItem", "horizontalHeader", "verticalHeader", "setModel",
    "model", "setSelectionMode", "setSelectionBehavior", "setEditTriggers",
    "selectedItems", "selectedIndexes", "selectRow", "selectAll", "clearSelection",
    "setSortingEnabled", "sortItems", "setColumnWidth", "columnWidth",
    "setTextAlignment", "setForeground", "setBackground", "setFont", "font",
    "setIcon", "icon", "setWindowModality", "setModal", "exec", "accept", "reject",
    "setAcceptDrops", "setDragEnabled", "setAcceptDrops", "setAlternatingRowColors",
    "setShowGrid", "setWordWrap", "setLineWrapMode", "setMaximumBlockCount",
    "setContextMenuPolicy", "setSizeAdjustPolicy", "setDefaultSectionSize",
    "setSectionResizeMode", "setStretchLastSection", "setVisible", "viewport",
    "setFrameShape", "setFrameShadow", "setAutoFillBackground", "setAttribute",
    "setWindowFlags", "windowFlags", "setWindowState", "setGeometry", "geometry",
    "setFocusPolicy", "setFocus", "hasFocus", "setTabOrder", "setMinimumSize",
    "setMaximumSize", "sizeHint", "minimumSizeHint", "height", "width", "setMouseTracking",
    "setCursor", "unsetCursor", "installEventFilter", "removeEventFilter",
    "setProperty", "property", "style", "setStyle", "blockSignals", "signalsBlocked",
    "disconnect", "connect", "emit", "setChecked", "isVisible", "isEnabled",
    "setAutoDefault", "setDefault", "setIconSize", "setToolButtonStyle",
    "setTextElideMode", "setRootIsDecorated", "setUniformRowHeights",
    "setHeaderHidden", "setIndentation", "setAnimated", "setExpandsOnDoubleClick",
    "setCurrentItem", "currentItem", "setCurrentRow", "currentRow", "setCurrentColumn",
    "currentColumn", "setCurrentCell", "setSpan", "setRowHidden", "setColumnHidden",
    "setItemDelegate", "setViewportMargins", "verticalScrollBar", "horizontalScrollBar",
    "ensureVisible", "scrollTo", "scrollToItem", "visualItemRect", "indexAt",
    "rowAt", "columnAt", "itemAt", "items", "takeItem", "removeRow", "removeColumn",
    "insertItem", "insertItems", "setUniformItemSizes", "isRowSelected",
    "clearContents", "setCellWidget", "cellWidget", "setIndexWidget",
    "setItemSelected", "setRangeSelected", "setLineWidth", "setMidLineWidth",
    "setTitle", "title", "setCheckState", "checkState", "setFlags", "flags",
    "setData", "data", "setTristate", "setFieldGrowthPolicy", "setRowWrapPolicy",
    "setLabelAlignment", "setFormAlignment", "addWidget", "addLayout", "addStretch",
    "addSpacing", "insertWidget", "insertStretch", "insertSpacing", "removeWidget",
    "setStretchFactor", "setColumnStretch", "setRowStretch", "setColumnMinimumWidth",
    "setRowMinimumHeight", "activate", "minimumWidth", "maximumWidth", "minimumHeight",
    "maximumHeight", "size", "pos", "x", "y", "rect", "setContentsMargins",
    "setWindowOpacity", "windowOpacity", "setWindowTitle", "setWindowFilePath",
    "setWindowRole", "metaObject", "findChild", "findChildren", "childAt",
    "isAncestorOf", "setTabPosition", "setTabsClosable", "setMovable", "setDocumentMode",
    "addTab", "insertTab", "removeTab", "setTabText", "tabText", "setCurrentWidget",
    "currentWidget", "count", "widget", "indexOf", "setTabEnabled", "setTabVisible",
    "setTabToolTip", "tabToolTip", "setElideMode", "setUsesScrollButtons",
    "setIconSize", "setCornerWidget", "setDocumentMode", "changeEvent", "closeEvent",
    "showEvent", "hideEvent", "resizeEvent", "paintEvent", "mousePressEvent",
    "mouseReleaseEvent", "mouseDoubleClickEvent", "mouseMoveEvent", "keyPressEvent",
    "keyReleaseEvent", "wheelEvent", "dragEnterEvent", "dragMoveEvent", "dropEvent",
    "enterEvent", "leaveEvent", "focusInEvent", "focusOutEvent", "contextMenuEvent",
    "eventFilter", "event", "sizeHint", "initStyleOption", "setAcceptDrops",
    "setAccessibleDescription", "setStatusTip", "setWindowIconText", "setShortcutContext",
    "setAutoRepeat", "setAutoRepeatDelay", "setAutoRepeatInterval", "setDown",
    "setCheckable", "setChecked", "click", "animateClick", "toggle", "setArrowType",
    "setMenu", "menu", "showMenu", "setPopupMode", "setSizeAdjustPolicy",
    "setDefaultAction", "defaultAction", "actions", "addAction", "insertAction",
    "removeAction", "setContextMenuPolicy", "setTextInteractionFlags",
    "setOpenExternalLinks", "setOpenLinks", "setSource", "setHtml", "setPlainText",
    "toPlainText", "toHtml", "append", "appendPlainText", "appendHtml", "insertPlainText",
    "setTextCursor", "textCursor", "moveCursor", "undo", "redo", "cut", "copy", "paste",
    "selectAll", "setCurrentCharFormat", "currentCharFormat", "mergeCurrentCharFormat",
    "setUndoRedoEnabled", "isUndoRedoEnabled", "setAcceptRichText", "setTabChangesFocus",
    "zoomIn", "zoomOut", "setReadOnly", "document", "setDocument", "setMarkdown",
    "setSearchPaths", "reload", "back", "forward", "setHistory", "history",
    "setHeaderLabels", "expandAll", "collapseAll", "expandItem", "collapseItem",
    "setHeaderLabel", "invisibleRootItem", "takeTopLevelItem", "addTopLevelItem",
    "topLevelItem", "topLevelItemCount", "setFirstColumnSpanned", "setExpanded",
    "setChildIndicatorPolicy", "setTreePosition", "setHeaderItem", "setHeader",
    "setItemWidget", "itemWidget", "openPersistentEditor", "closePersistentEditor",
    "setCompleter", "completer", "setValidator", "validator", "setEchoMode",
    "setMaxLength", "maxLength", "setInputMask", "inputMask", "setFrame",
    "setMargins", "setStepType", "setDisplayFormat", "setCalendarPopup",
    "date", "time", "setDate", "setTime", "setDateTime", "dateTime", "setDateRange",
    "setStepEnabled", "stepBy", "setSectionResizeMode", "resizeSections",
    "setColumnWidth", "setItemPrototype", "setResizeMode", "setLayoutMode",
    "setBatchSize", "setFlow", "setWrapping", "setMovement", "setViewMode",
    "setUniformItemSizes", "setGridSize", "setSpacing", "setEnabled",
    "setWindowModality", "setModal", "open", "done", "setResult", "result",
    "setSizeGripEnabled", "isSizeGripEnabled", "setMinimumDuration",
    "setCancelButton", "cancelButton", "setLabelText", "setBar", "setRange",
    "wasCanceled", "setAutoClose", "autoClose", "setAutoReset", "reset",
    "setInvertedAppearance", "setTextVisible", "setFormat", "setValue",
    "setWrapping", "setOrientation", "orientation", "setSliderPosition",
    "sliderPosition", "setTickPosition", "setTickInterval", "setPageStep",
    "setSingleStep", "trigger", "setDefault", "setCheckable", "setChecked",
    "setText", "setIcon", "setShortcut", "setStatusTip", "setWhatsThis",
    # QMainWindow / QWidget / QThread 上由基类提供的成员（不以下划线开头，
    # 但确实不在类体里定义；漏掉会变成误报）
    "setCentralWidget", "centralWidget", "addToolBar", "toolBar", "addDockWidget",
    "statusBar", "setStatusBar", "menuBar", "setMenuBar", "setMenuWidget",
    "setDragDropMode", "dragDropMode", "customContextMenuRequested",
    "isInterruptionRequested", "requestInterruption", "start", "quit", "exit",
    "isRunning", "isFinished", "wait", "terminate", "setTerminationEnabled",
    "msleep", "usleep", "sleep", "currentThread", "idealThreadCount",
    "setPriority", "priority", "moveToThread", "thread", "run",
    "window", "windowHandle", "screen", "devicePixelRatio", "setWindowModified",
    "sizeHint", "minimumSizeHint", "heightForWidth", "hasHeightForWidth",
    "setAutoFillBackground", "autoFillBackground", "setUpdatesEnabled",
    "isActiveWindow", "activateWindow", "raise_", "lower", "stackUnder",
    "setAttribute", "testAttribute", "setWindowFlag", "setWindowFlags",
    "windowFlags", "setWindowState", "windowState", "isMaximized", "isMinimized",
    "showMaximized", "showMinimized", "showNormal", "showFullScreen",
    "grab", "render", "setGraphicsEffect", "graphicsEffect",
    "acceptDrops", "setAcceptDrops", "toolTip", "setToolTip", "toolTipDuration",
    "setToolTipDuration", "statusTip", "whatsThis", "accessibleName",
    "accessibleDescription", "setAccessibleName", "setAccessibleDescription",
    "styleSheet", "setStyleSheet", "setLocale", "locale", "setLayoutDirection",
    "layoutDirection", "setTabletTracking", "setMouseTracking", "mouseTracking",
}


def module_classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def class_members(cls: ast.ClassDef, base_index: dict[str, ast.ClassDef],
                  seen: set[str] | None = None) -> set[str]:
    """类体里的 def/赋值 + 所有 self.X = 的 X + 父类成员。"""
    seen = seen or set()
    if cls.name in seen:
        return set()
    seen.add(cls.name)

    members: set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    members.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            members.add(node.target.id)

    # self.X = ... 出现在任何方法里
    for sub in ast.walk(cls):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self"):
                    members.add(t.attr)
        elif isinstance(sub, ast.AnnAssign):
            t = sub.target
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                    and t.value.id == "self":
                members.add(t.attr)
        elif isinstance(sub, ast.AugAssign):
            t = sub.target
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                    and t.value.id == "self":
                members.add(t.attr)
        elif isinstance(sub, ast.For):
            t = sub.target
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                    and t.value.id == "self":
                members.add(t.attr)
        elif isinstance(sub, ast.withitem):
            pass

    for base in cls.bases:
        name = None
        if isinstance(base, ast.Name):
            name = base.id
        elif isinstance(base, ast.Attribute):
            name = base.attr
        if name and name in base_index and name != cls.name:
            members |= class_members(base_index[name], base_index, seen)
    return members


def check_private_calls(path: Path, tree: ast.Module) -> list[str]:
    """self._xxx() 调用但类内（含同模块父类）没有定义。"""
    problems = []
    base_index = module_classes(tree)
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        known = class_members(cls, base_index)
        for sub in ast.walk(cls):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "self"):
                name = sub.func.attr
                if name.startswith("_") and not name.startswith("__") and name not in known:
                    problems.append(f"{path}:{sub.lineno}: {cls.name}.{name}() 调用但未定义")
    return problems


def check_self_reads(path: Path, tree: ast.Module) -> list[str]:
    """self.X 读取但从未赋值（排除已知 Qt 基类成员）。"""
    problems = []
    base_index = module_classes(tree)
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        known = class_members(cls, base_index)
        assigned_in_init = set()
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name == "__init__":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Assign):
                        for t in sub.targets:
                            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                                    and t.value.id == "self":
                                assigned_in_init.add(t.attr)
                    elif isinstance(sub, ast.AnnAssign):
                        t = sub.target
                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                                and t.value.id == "self":
                            assigned_in_init.add(t.attr)
        for sub in ast.walk(cls):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                    and sub.value.id == "self" and isinstance(sub.ctx, ast.Load)):
                name = sub.attr
                if name in known or name in QT_BASE_MEMBERS:
                    continue
                if name.startswith("__"):
                    continue
                problems.append(f"{path}:{sub.lineno}: {cls.name}.self.{name} 读取但未赋值")
    return problems


def check_duplicate_defs(path: Path, tree: ast.Module) -> list[str]:
    problems = []
    names = [s.name for s in tree.body
             if isinstance(s, (ast.FunctionDef, ast.ClassDef))]
    for n, k in collections.Counter(names).items():
        if k > 1:
            problems.append(f"{path}: 顶层 {n} 重复定义 {k} 次")
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        mnames = [s.name for s in cls.body
                  if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for n, k in collections.Counter(mnames).items():
            if k > 1:
                problems.append(f"{path}: {cls.name}.{n} 方法重复定义 {k} 次")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    targets = sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and not p.name.startswith("_audit")
    )
    all_problems: list[str] = []
    for path in targets:
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except SyntaxError as e:
            all_problems.append(f"{path}: 语法错误 {e}")
            continue
        rel = path.relative_to(root)
        all_problems += check_duplicate_defs(rel, tree)
        all_problems += check_private_calls(rel, tree)
        all_problems += check_self_reads(rel, tree)

    if all_problems:
        print(f"发现 {len(all_problems)} 个疑似问题：\n")
        for p in all_problems:
            print("  " + p)
    else:
        print("未发现问题。")
    return 1 if all_problems else 0


if __name__ == "__main__":
    sys.exit(main())
