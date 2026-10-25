"""Before/After trees, editable destinations, and selected-file details."""
from __future__ import annotations

import copy
from html import escape
import uuid
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy, QSplitter,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QVBoxLayout, QWidget,
)

from app.ai.schema import OrganizationPlan, PlanAction
from app.core.naming import is_valid_filename
from app.core.simulator import Simulator
from app.core.validator import Validator

# state: (label, light colour, dark colour). Struck-through states show where things left.
STATES = {
    "new": ("Moved here", "#0077bb", "#55bbff"),
    "created_dir": ("New folder", "#1a7a1a", "#55e055"),
    "removed": ("Removed", "#cc1111", "#ff5555"),
}
STRUCK = {"removed"}


def _tooltip(node, after):
    action = node.action
    if action and not node.is_dir:
        lines = ["🔵 Moved here", f"  From:  {action.src_rel}", f"  To:    {action.dst_rel}"]
        if action.category:
            lines.append(f"  Category: {action.category}")
        if action.reason:
            lines.append(f"  Reason: {action.reason}")
        if after:
            lines += ["", "✏  Double-click to rename, or drag to move"]
        return "\n".join(lines)
    if node.diff_state == "created_dir":
        files = sum(1 for child in node.children if not child.is_dir)
        return "🟢 New folder" + (f"\n  Contains: {files} file{'s' if files != 1 else ''} being moved here" if files else "")
    if node.diff_state == "removed":
        return "🔴 Removed\n  This folder no longer exists after the plan."
    return f"{'📁 Folder' if node.is_dir else '📄 File'}  {node.name}\n  Path: {node.rel_path or '/'}"


class DiffItemDelegate(QStyledItemDelegate):
    """Draws the name in its state colour and a dimmer italic [label] after it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dark = False

    def paint(self, painter, option, index):
        node = index.data(Qt.UserRole)
        if node is None:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QStyle()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        state = STATES.get(node.diff_state)
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            color = option.palette.highlightedText().color()
        elif state:
            color = QColor(state[2] if self.dark else state[1])
        else:
            color = option.palette.text().color()
        name = ("📁 " if node.is_dir else "   ") + node.name
        suffix = f"  [{state[0]}]" if state else ""
        rect = opt.rect.adjusted(4, 0, -4, 0)
        if not opt.icon.isNull():
            rect = rect.adjusted(opt.decorationSize.width() + 4, 0, 0, 0)
        font = QFont(option.font)
        font.setBold(node.diff_state in {"new", "created_dir"})
        painter.save()
        painter.setFont(font)
        painter.setPen(QPen(color))
        painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, name)
        width = min(QFontMetrics(font).horizontalAdvance(name), rect.width())
        if node.diff_state in STRUCK:
            middle = rect.top() + rect.height() // 2
            painter.setPen(QPen(color, 1.5))
            painter.drawLine(rect.left(), middle, rect.left() + width, middle)
        if suffix:
            label_font = QFont(option.font)
            label_font.setItalic(True)
            label_font.setPointSize(max(label_font.pointSize() - 1, 8))
            dim = QColor(color)
            dim.setAlpha(200 if selected else 160)
            painter.setFont(label_font)
            painter.setPen(QPen(dim))
            painter.drawText(QRect(rect.left() + width, rect.top(), rect.width() - width, rect.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, suffix)
        painter.restore()

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        return QSize(hint.width() + 90, max(hint.height(), 22))

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setFrame(False)
        editor.setStyleSheet(
            "QLineEdit { padding:0 4px; margin:0; border:1.5px solid #4d9fff; border-radius:3px; "
            + ("background:#28305a; color:#e2e4f0; }" if self.dark else "background:#ffffff; color:#1a1a1a; }")
        )
        return editor

    def setEditorData(self, editor, index):
        editor.setText(str(index.data(Qt.EditRole) or ""))
        editor.selectAll()

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect.adjusted(2, 0, 0, 0)
        rect.setHeight(max(rect.height(), editor.fontMetrics().height() + 6))
        editor.setGeometry(rect)


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
            suffix = f"  [{STATES[node.diff_state][0]}]" if node.diff_state in STATES else ""
            return ("📁 " if node.is_dir else "") + node.name + suffix
        if role == Qt.ToolTipRole:
            return "<qt>" + escape(_tooltip(node, self.editable)).replace("\n", "<br>") + "</qt>"
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled if self.editable else Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        node = index.internalPointer()
        if self.editable:
            flags |= Qt.ItemIsDropEnabled | Qt.ItemIsDragEnabled | Qt.ItemIsEditable
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


class _FolderTree(QTreeView):
    """Any click left of a folder's name opens or closes it, not just the small arrow."""

    def _margin_folder(self, event):
        index = self.indexAt(event.position().toPoint())
        if (event.button() == Qt.LeftButton and index.isValid() and self.model().hasChildren(index)
                and event.position().x() < self.visualRect(index).left()):
            return index
        return None

    def mousePressEvent(self, event):
        index = self._margin_folder(event)
        if index is None:
            super().mousePressEvent(event)
        else:
            self.setExpanded(index, not self.isExpanded(index))

    def mouseDoubleClickEvent(self, event):
        # Qt already delivered a press for this click; swallow the double-click so it neither
        # toggles back nor starts renaming.
        if self._margin_folder(event) is None:
            super().mouseDoubleClickEvent(event)


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
        self._dark = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)
        trees = QSplitter(Qt.Horizontal)
        splitter.addWidget(trees)
        self.models = []
        self.trees = []
        self.delegates = []
        for title, editable in (("Before", False), ("After", True)):
            panel = QWidget()
            column = QVBoxLayout(panel)
            column.setContentsMargins(4, 4, 4, 2)
            column.setSpacing(2)
            header_row = QWidget()
            header_row.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            header = QHBoxLayout(header_row)
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(6)
            label = QLabel(title)
            label.setStyleSheet("font-weight:600; font-size:13px;")
            header.addWidget(label)
            if editable:
                self.badge = QLabel()
                self.badge.hide()
                header.addWidget(self.badge)
                separator = QLabel("│")
                separator.setStyleSheet("color:#888; font-size:11px;")
                header.addWidget(separator)
                self.legend = QLabel()
                self.legend.setTextFormat(Qt.RichText)
                self.legend.setStyleSheet("font-size:10px;")
                self.legend.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                header.addWidget(self.legend)
                # The hint absorbs any shortage of width so the legend is never covered.
                hint = QLabel("✏ Double-click: rename · Drag: move")
                hint.setStyleSheet("font-size:10px; color:#888; font-style:italic; margin-left:6px;")
                hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                header.addWidget(hint, 1)
            else:
                header.addStretch()
            column.addWidget(header_row)
            tree = _FolderTree()
            tree.setHeaderHidden(True)
            tree.setAnimated(True)
            tree.setUniformRowHeights(True)
            tree.setAlternatingRowColors(True)
            tree.setMouseTracking(True)
            # Folders open from the arrow only; double-click is reserved for renaming.
            tree.setExpandsOnDoubleClick(False)
            delegate = DiffItemDelegate(tree)
            tree.setItemDelegate(delegate)
            self.delegates.append(delegate)
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
            trees.addWidget(panel)
            self.models.append(model)
            self.trees.append(tree)
        trees.setSizes([500, 500])
        details = QWidget()
        details.setMinimumWidth(190)
        details.setMaximumWidth(280)
        details.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        outer = QVBoxLayout(details)
        outer.setContentsMargins(8, 8, 8, 8)
        title = QLabel("Details")
        title.setStyleSheet("font-weight:600; font-size:13px;")
        outer.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        self.details = QVBoxLayout(content)
        self.details.setAlignment(Qt.AlignTop)
        self.details.setSpacing(6)
        scroll.setWidget(content)
        splitter.addWidget(details)
        splitter.setSizes([860, 200])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        self._refresh_legend()
        self._show_details(None)

    def clear(self):
        self.inventory = None
        self.plan = None
        self._order = None
        self._displaced = set()
        for model in self.models:
            model.set_root(None)
        self.badge.hide()
        self._show_details(None)

    def _refresh_legend(self):
        entries = []
        for state, (text, light, dark) in STATES.items():
            mark = "✕" if state in STRUCK else "●"
            entries.append(f"<span style='color:{dark if self._dark else light}'>{mark}&nbsp;{text}</span>")
        self.legend.setText("&nbsp;&nbsp;".join(entries))

    def _set_badge(self, count):
        if count:
            color, text = ("#2a9a2a" if self._dark else "#1a7a1a"), "#ffffff"
        else:
            color, text = ("#555577" if self._dark else "#9090a0"), "#ccccdd"
        self.badge.setStyleSheet(f"font-size:11px; font-weight:700; padding:1px 7px; "
                                 f"border-radius:9px; background:{color}; color:{text};")
        held = len(self.plan.review.get('held', {})) if self.plan else 0
        self.badge.setText(f"{count:,} changes" + (f" · {held:,} held by review" if held else ''))
        self.badge.show()

    def _show_details(self, node):
        while self.details.count():
            item = self.details.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if node is None:
            placeholder = QLabel("Select a file in the preview\nto see details.")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color:#888; font-size:12px;")
            self.details.addWidget(placeholder)
            return
        self._row("Name", node.name)
        self._row("Path", node.rel_path or "/")
        self._row("Type", "Folder" if node.is_dir else "File")
        state = STATES.get(node.diff_state)
        self._row("Status", state[0] if state else "Unchanged",
                  (state[2] if self._dark else state[1]) if state else "#888")
        if self.inventory and node.rel_path in self.inventory.protected_projects:
            self._row("Protected", "Project folder. Contents are excluded and left unchanged.", "#888")
        if node.action:
            action = node.action
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setStyleSheet("color:#ddd;")
            self.details.addWidget(line)
            self._row("From", action.src_rel)
            self._row("To", action.dst_rel)
            self._row("Category", action.category)
            self._row("Reason", action.reason)
        self.details.addStretch()

    def _row(self, label, value, color=""):
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        name = QLabel(label)
        name.setStyleSheet("color:#888; font-size:11px;")
        text = QLabel(value)
        text.setTextFormat(Qt.PlainText)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text.setStyleSheet(f"color:{color}; font-size:12px;" if color else "font-size:12px;")
        column.addWidget(name)
        column.addWidget(text)
        self.details.addWidget(row)

    def set_plan(self, inventory, plan, shortcuts_movable=False, executables_movable=False):
        if any(a.type != 'mkdir' and a.source_identity is None for a in plan.actions):
            # Sample/imported previews need the same stale-file protection as AI plans.
            report = Validator(Path(plan.root), allow_suffix_on_collision=False,
                shortcuts_movable=shortcuts_movable, executables_movable=executables_movable).validate_plan(plan)
            plan.actions, plan.warnings = report.valid_actions, report.warnings
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
        pending = [before]
        while pending:
            node = pending.pop()
            if node.is_dir and node.rel_path in self._displaced:
                node.diff_state = "removed"
            pending.extend(node.children)
        self._set_badge(len(plan.actions))
        for model, tree, root in zip(self.models, self.trees, (before, after)):
            model.set_root(root)
            if inventory.total_files < 40:
                tree.expandAll()
            else:
                tree.expandToDepth(0)
        self._show_details(None)

    def set_dark(self, dark):
        self._dark = dark
        for model, delegate in zip(self.models, self.delegates):
            model.dark = dark
            delegate.dark = dark
        for tree in self.trees:
            tree.viewport().update()
        self._refresh_legend()
        if self.badge.isVisible() and self.plan is not None:
            self._set_badge(len(self.plan.actions))

    def _selected(self, current, previous):
        if current.isValid():
            self._show_details(current.data(Qt.UserRole))

    def _rename(self, node, name):
        if self.plan is None or self._pending_edit:
            return False
        old = node.rel_path
        new = str(PurePosixPath(old).with_name(name))
        if node.is_dir and node.diff_state != "created_dir":
            # Renaming an existing folder moves its contents, like dragging it to a new name.
            return self._move(node, new)
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
        candidate = OrganizationPlan(self.plan.root, actions, list(self.plan.warnings), review={
            'user_edited': True, 'original_proposed': self.plan.review.get('proposed'),
            'original_retained': self.plan.review.get('retained')})
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
