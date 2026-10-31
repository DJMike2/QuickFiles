"""Build both preview trees without touching the filesystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.ai.schema import PlanAction


@dataclass
class TreeNode:
    name: str
    is_dir: bool
    rel_path: str
    children: list[TreeNode] = field(default_factory=list)
    parent: TreeNode | None = field(default=None, repr=False)
    diff_state: str = "unchanged"
    action: PlanAction | None = None


def build_trees(inventory, plan, order=None, displaced=None):
    return _tree(inventory, None), _tree(inventory, plan, order, displaced)


def _tree(inventory, plan, order=None, displaced=None):
    root = TreeNode(Path(inventory.root).name, True, "")
    nodes = {"": root}

    def insert(relative, is_dir=False, action=None):
        parts = PurePosixPath(relative).parts
        for index, part in enumerate(parts):
            current = "/".join(parts[:index + 1])
            if current not in nodes:
                parent = nodes["/".join(parts[:index])]
                directory = is_dir or index < len(parts) - 1
                state = "unchanged" if action is None else ("created_dir" if directory else "new")
                node = TreeNode(part, directory, current, parent=parent, diff_state=state)
                parent.children.append(node)
                nodes[current] = node
        node = nodes[relative]
        if action:
            node.action = action
        return node

    # Existing folders remain even when their files are moved away.
    displaced = displaced or set()
    for directory in inventory.directories:
        if directory in displaced:
            continue
        insert(directory, is_dir=True)
    moves = {a.src_rel: a for a in plan.actions if a.type in {"move", "rename"}} if plan else {}
    for entry in inventory.entries:
        if entry.rel_path not in moves:
            insert(entry.rel_path)
    if plan:
        for action in plan.actions:
            insert(action.dst_rel, is_dir=action.type == "mkdir", action=action)

    def sort(node):
        ranks = {path: index for index, path in enumerate((order or {}).get(node.rel_path, []))}
        node.children.sort(key=lambda child: (
            (0, ranks[child.rel_path]) if child.rel_path in ranks else (1, 0 if child.is_dir else 1),
            child.name.casefold(),
        ))
        for child in node.children:
            sort(child)

    sort(root)
    return root
