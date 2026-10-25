"""Before/After trees, editable destinations, and selected-file details."""
from __future__ import annotations

import copy
import uuid
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, Qt, QTimer, Signal
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
    _PATH_MIME = "application/x-quickfiles-path"

    def __init__(self, editable=False, parent=None):
        super().__init__(parent)
        self.root = None
        self.editable = editable
        self.rename = None
        self.move_to = None
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
                return f"{action.src_rel} → {action.dst_rel}\n{action.reason}"
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
            return Qt.ItemIsDropEnabled if self.editable else Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        node = index.internalPointer()
        if self.editable:
            flags |= Qt.ItemIsDropEnabled | Qt.ItemIsDragEnabled
            if not node.is_dir or node.diff_state == "created_dir":
                flags |= Qt.ItemIsEditable
        return flags

    def supportedDropActions(self):
        return Qt.MoveAction

    def mimeTypes(self):
        return [self._PATH_MIME]

    def mimeData(self, indexes):
        data = QMimeData()
        if len(indexes) == 1 and indexes[0].isValid():
            node = indexes[0].internalPointer()
            data.setData(self._PATH_MIME, node.rel_path.encode("utf-8"))
        return data

    def dropMimeData(self, data, action, row, column, parent):
        if not self.editable or self.move_to is None or action != Qt.MoveAction:
            return False
        if not data.hasFormat(self._PATH_MIME):
            return False
        rel_path = bytes(data.data(self._PATH_MIME)).decode("utf-8")
        node = self._find_node(rel_path)
        if node is None:
            return False
        if row >= 0:
            folder = "" if not parent.isValid() else parent.internalPointer().rel_path
            index = row
        elif not parent.isValid():
            folder, index = "", None
        else:
            target = parent.internalPointer()
            if target.is_dir:
                folder, index = target.rel_path, None
            else:
                folder = target.parent.rel_path if target.parent else ""
                index = None
        if node.is_dir and (folder == node.rel_path or folder.startswith(node.rel_path + "/")):
            return False
        new_rel = f"{folder}/{node.name}" if folder else node.name
        return bool(self.move_to(node, new_rel, index))

    def _find_node(self, rel_path):
        pending = [self.root] if self.root else []
        while pending:
            node = pending.pop()
            if node is not self.root and node.rel_path == rel_path:
                return node
            pending.extend(node.children)
        return None

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
        self.executables_movable = False
        self._pending_edit = False
        self._order = None
        self._displaced = set()
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
            if editable:
                model.move_to = self._move
                tree.setDragEnabled(True)
                tree.setAcceptDrops(True)
                tree.setDropIndicatorShown(True)
                tree.setDragDropMode(QAbstractItemView.DragDrop)
                tree.setDefaultDropAction(Qt.MoveAction)
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
        self._order = None
        self._displaced = set()
        for model in self.models:
            model.set_root(None)
        self.details.setText("Select a file to see its details.")
        self.headers[1].setText("After")

    def set_plan(self, inventory, plan, shortcuts_movable=False, executables_movable=False):
        if inventory is not self.inventory:
            self._order = None
            self._displaced = set()
        self.inventory = inventory
        self.plan = plan
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable
        before, after = Simulator(Path(inventory.root)).build_trees(
            inventory, plan, self._order, self._displaced,
        )
        self.headers[1].setText(
            f"After · {len(plan.actions):,} changes · drag above, below, or into a folder"
        )
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
        if self.inventory and node.rel_path in self.inventory.protected_projects:
            lines.extend(["", "Protected project folder. Contents are excluded and left unchanged."])
        if node.action:
            action = node.action
            lines.extend(["", f"From: {action.src_rel}", f"To: {action.dst_rel}",
                          f"Category: {action.category}", f"Reason: {action.reason}"])
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
        return self._commit(actions)

    def _move(self, node, new_rel, index=None):
        if self.plan is None or self._pending_edit:
            return False
        nested = node.is_dir and new_rel.startswith(node.rel_path + "/")
        if nested or (new_rel == node.rel_path and index is None):
            return False
        actions = copy.deepcopy(self.plan.actions)
        displaced = set(self._displaced)
        if new_rel != node.rel_path and node.is_dir:
            actions, displaced = self._retarget_folder(node, new_rel, actions, displaced)
        elif new_rel != node.rel_path:
            actions = self._retarget_file(node, new_rel, actions)
            if actions is None:
                return False
        return self._commit(actions, self._placed_order(node, new_rel, index), displaced)

    def _retarget_file(self, node, new_rel, actions):
        source = node.action.src_rel if node.action else node.rel_path
        if new_rel == source:
            remaining = [action for action in actions if action.dst_rel != node.rel_path]
            return None if len(remaining) == len(actions) else remaining
        changed = False
        for action in actions:
            if action.dst_rel == node.rel_path:
                action.dst_rel = new_rel
                action.type = "move"
                action.reason = "Moved in preview"
                changed = True
        if not changed:
            actions.append(PlanAction(str(uuid.uuid4()), "move", source, new_rel,
                                      confidence=1, reason="Moved in preview"))
        return actions

    def _retarget_folder(self, node, new_rel, actions, displaced):
        prefix = node.rel_path + "/"
        for action in actions:
            if action.dst_rel == node.rel_path or action.dst_rel.startswith(prefix):
                action.dst_rel = new_rel + action.dst_rel[len(node.rel_path):]
                if action.type != "mkdir":
                    action.type = "move"
                action.reason = "Moved in preview"
        known = {action.src_rel for action in actions if action.type != "mkdir"}
        for file_node in self._files_under(node):
            suffix = file_node.rel_path[len(prefix):]
            dest = f"{new_rel}/{suffix}" if suffix else new_rel
            source = file_node.action.src_rel if file_node.action else file_node.rel_path
            if source in known or dest == source:
                continue
            actions.append(PlanAction(str(uuid.uuid4()), "move", source, dest,
                                      confidence=1, reason="Moved in preview"))
            known.add(source)
        for directory in self.inventory.directories:
            if directory != node.rel_path and not directory.startswith(prefix):
                continue
            dest_dir = new_rel + directory[len(node.rel_path):]
            if dest_dir == directory:
                displaced.discard(directory)
                continue
            displaced.add(directory)
            covered = any(
                action.dst_rel.startswith(dest_dir + "/") or action.dst_rel == dest_dir
                for action in actions
            )
            exists = Path(self.plan.root).joinpath(*dest_dir.split("/")).is_dir()
            if not covered and not exists:
                actions.append(PlanAction(str(uuid.uuid4()), "mkdir", directory, dest_dir,
                                          confidence=1, reason="Moved in preview"))
        return self._settle_folders(actions, displaced)

    def _settle_folders(self, actions, displaced):
        root = Path(self.plan.root)
        kept = []
        for action in actions:
            same_place = action.src_rel == action.dst_rel
            restored = action.type == "mkdir" and root.joinpath(*action.dst_rel.split("/")).is_dir()
            if same_place or restored:
                continue
            kept.append(action)
        for directory in list(displaced):
            away = False
            for action in kept:
                sourced = action.src_rel == directory or action.src_rel.startswith(directory + "/")
                placed = action.dst_rel == directory or action.dst_rel.startswith(directory + "/")
                if action.type == "mkdir" and action.src_rel == directory and action.dst_rel != directory:
                    away = True
                if action.type in {"move", "rename"} and sourced and not placed:
                    away = True
            if not away:
                displaced.discard(directory)
        return kept, displaced

    def _files_under(self, node):
        pending = list(node.children)
        files = []
        while pending:
            current = pending.pop()
            if current.is_dir:
                pending.extend(current.children)
            else:
                files.append(current)
        return files

    def _placed_order(self, node, new_rel, index):
        root = self.models[1].root
        if root is None or (index is None and self._order is None):
            return self._order
        order = {parent: list(children) for parent, children in (self._order or self._capture(root)).items()}
        old_parent = node.parent.rel_path if node.parent else ""
        siblings = order.setdefault(old_parent, [])
        old_index = siblings.index(node.rel_path) if node.rel_path in siblings else None
        if old_index is not None:
            siblings.pop(old_index)
        new_parent = str(PurePosixPath(new_rel).parent)
        if new_parent == ".":
            new_parent = ""
        destination = order.setdefault(new_parent, [])
        if index is None:
            index = len(destination)
        elif old_parent == new_parent and old_index is not None and old_index < index:
            index -= 1
        if new_rel not in destination:
            destination.insert(max(0, min(index, len(destination))), new_rel)
        if node.is_dir and node.rel_path != new_rel:
            order = self._rename_paths(order, node.rel_path, new_rel)
        return order

    def _capture(self, node):
        order = {}
        pending = [node]
        while pending:
            current = pending.pop()
            order[current.rel_path] = [child.rel_path for child in current.children]
            pending.extend(child for child in current.children if child.is_dir)
        return order

    def _rename_paths(self, order, old, new):
        updated = {}
        for parent, children in order.items():
            if parent == old or parent.startswith(old + "/"):
                parent = new + parent[len(old):]
            renamed = []
            for child in children:
                if child == old or child.startswith(old + "/"):
                    child = new + child[len(old):]
                renamed.append(child)
            updated[parent] = renamed
        return updated

    def _commit(self, actions, order=None, displaced=None):
        if self.plan is None or self._pending_edit:
            return False
        candidate = OrganizationPlan(self.plan.root, actions, list(self.plan.warnings))
        report = Validator(Path(self.plan.root), allow_suffix_on_collision=False,
                           shortcuts_movable=self.shortcuts_movable,
                           executables_movable=self.executables_movable).validate_plan(candidate)
        if report.rejected_actions:
            self.editRejected.emit(report.rejected_actions[0][1])
            return False
        candidate.actions = report.valid_actions
        candidate.excluded_count = self.inventory.total_files - len({a.src_rel for a in candidate.actions})
        # Let Qt finish closing the editor before resetting its model.
        self._pending_edit = True

        def publish():
            self._pending_edit = False
            if order is not None:
                self._order = order
            if displaced is not None:
                self._displaced = displaced
            self.set_plan(self.inventory, candidate, self.shortcuts_movable, self.executables_movable)
            self.planChanged.emit(candidate)

        QTimer.singleShot(0, publish)
        return True
