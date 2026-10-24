"""Before/After trees, editable destinations, and selected-file details."""
from __future__ import annotations

import copy
import uuid
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QSplitter, QTreeView,
    QVBoxLayout, QWidget,
)

from app.ai.schema import OrganizationPlan, PlanAction
from app.core.naming import is_valid_filename
from app.core.simulator import Simulator
from app.core.validator import Validator


class DiffTreeModel(QAbstractItemModel):
    def __init__(self, editable=False, parent=None):
        super().__init__(parent)
        self.root = None
        self.editable = editable
        self.rename = None
        self.dark = False

    def set_root(self, root):
        self.beginResetModel()
        self.root = root
        self.endResetModel()

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        node = parent.internalPointer() if parent.isValid() else self.root
        return self.createIndex(row, column, node.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        parent = index.internalPointer().parent
        if parent is None or parent is self.root or parent.parent is None:
            return QModelIndex()
        row = next(i for i, child in enumerate(parent.parent.children) if child is parent)
        return self.createIndex(row, 0, parent)

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() != 0:
            return 0
        node = parent.internalPointer() if parent.isValid() else self.root
        return len(node.children) if node else 0

    def columnCount(self, parent=QModelIndex()):
        return 1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.UserRole:
            return node
        if role == Qt.EditRole:
            return node.name
        if role == Qt.DisplayRole:
            suffix = {"new": "  [Moved here]", "created_dir": "  [new]"}.get(node.diff_state, "")
            return ("📁 " if node.is_dir else "") + node.name + suffix
        if role == Qt.ToolTipRole:
            if node.action:
                action = node.action
                return f"{action.src_rel} → {action.dst_rel}\n{action.confidence:.0%} confidence\n{action.reason}"
            return node.rel_path
        if role == Qt.ForegroundRole:
            colors = {"new": "#55bbff" if self.dark else "#0077bb",
                      "created_dir": "#55e055" if self.dark else "#1a7a1a"}
            return QColor(colors[node.diff_state]) if node.diff_state in colors else None
        if role == Qt.FontRole and node.diff_state != "unchanged":
            font = QFont()
            font.setBold(True)
            return font
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        node = index.internalPointer()
        if self.editable and (not node.is_dir or node.diff_state == "created_dir"):
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole or not (self.flags(index) & Qt.ItemIsEditable):
            return False
        name = str(value)
        node = index.internalPointer()
        if not is_valid_filename(name) or name == node.name or self.rename is None:
            return False
        return self.rename(node, name)


class PreviewPanel(QWidget):
    planChanged = Signal(object)
    editRejected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.inventory = None
        self.plan = None
        self.shortcuts_movable = False
        self._pending_edit = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)
        self.models = []
        self.trees = []
        self.headers = []
        for title, editable in (("Before", False), ("After", True)):
            panel = QWidget()
            column = QVBoxLayout(panel)
            column.setContentsMargins(3, 0, 3, 0)
            label = QLabel(title)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            column.addWidget(label)
            tree = QTreeView()
            tree.setHeaderHidden(True)
            tree.setUniformRowHeights(True)
            tree.setAlternatingRowColors(True)
            tree.setEditTriggers(
                QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed if editable
                else QAbstractItemView.NoEditTriggers
            )
            model = DiffTreeModel(editable, tree)
            model.rename = self._rename
            tree.setModel(model)
            tree.selectionModel().currentChanged.connect(self._selected)
            column.addWidget(tree)
            splitter.addWidget(panel)
            self.models.append(model)
            self.trees.append(tree)
            self.headers.append(label)
        details = QWidget()
        details.setMinimumWidth(180)
        details.setMaximumWidth(280)
        detail_layout = QVBoxLayout(details)
        detail_layout.addWidget(QLabel("Details"))
        self.details = QLabel("Select a file to see its details.")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.PlainText)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addWidget(self.details)
        detail_layout.addStretch()
        splitter.addWidget(details)
        splitter.setSizes([450, 450, 220])

    def clear(self):
        self.inventory = None
        self.plan = None
        for model in self.models:
            model.set_root(None)
        self.details.setText("Select a file to see its details.")
        self.headers[1].setText("After")

    def set_plan(self, inventory, plan, shortcuts_movable=False):
        self.inventory = inventory
        self.plan = plan
        self.shortcuts_movable = shortcuts_movable
        before, after = Simulator(Path(inventory.root)).build_trees(inventory, plan)
        self.headers[1].setText(f"After · {len(plan.actions):,} changes · double-click to rename")
        for model, tree, root in zip(self.models, self.trees, (before, after)):
            model.set_root(root)
            if inventory.total_files < 40:
                tree.expandAll()
            else:
                tree.expandToDepth(0)
        self.details.setText("Blue: moved here\nGreen: new folder\n\nSelect a file to see its details.")

    def set_dark(self, dark):
        for model in self.models:
            model.dark = dark
        for tree in self.trees:
            tree.viewport().update()

    def _selected(self, current, previous):
        if not current.isValid():
            return
        node = current.data(Qt.UserRole)
        lines = [node.name, node.rel_path, "Folder" if node.is_dir else "File"]
        if node.action:
            action = node.action
            lines.extend(["", f"From: {action.src_rel}", f"To: {action.dst_rel}",
                          f"Category: {action.category}", f"Confidence: {action.confidence:.0%}",
                          f"Reason: {action.reason}", f"Importance: {action.importance_score:.0%}"])
            if action.content_summary_type:
                lines.append(f"Content: {action.content_summary_type}")
        self.details.setText("\n".join(lines))

    def _rename(self, node, name):
        if self.plan is None or self._pending_edit:
            return False
        old = node.rel_path
        new = str(PurePosixPath(old).with_name(name))
        actions = copy.deepcopy(self.plan.actions)
        changed = False
        for action in actions:
            if action.dst_rel == old or (node.is_dir and action.dst_rel.startswith(old + "/")):
                action.dst_rel = new + action.dst_rel[len(old):]
                action.reason = "Destination edited in preview"
                changed = True
        if not changed and not node.is_dir:
            actions.append(PlanAction(str(uuid.uuid4()), "rename", old, new,
                                      confidence=1, reason="Renamed in preview"))
            changed = True
        if not changed:
            return False
        candidate = OrganizationPlan(self.plan.root, actions, list(self.plan.warnings))
        report = Validator(Path(self.plan.root), allow_suffix_on_collision=False,
                           shortcuts_movable=self.shortcuts_movable).validate_plan(candidate)
        if report.rejected_actions:
            self.editRejected.emit(report.rejected_actions[0][1])
            return False
        candidate.actions = report.valid_actions
        candidate.excluded_count = self.inventory.total_files - len({a.src_rel for a in candidate.actions})
        # Let Qt finish closing the editor before resetting its model.
        self._pending_edit = True

        def publish():
            self._pending_edit = False
            self.set_plan(self.inventory, candidate, self.shortcuts_movable)
            self.planChanged.emit(candidate)

        QTimer.singleShot(0, publish)
        return True
