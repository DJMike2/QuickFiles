"""Validate a complete plan before showing or executing its changes."""
from __future__ import annotations

import copy
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.ai.schema import PlanWarning
from app.constants import CONSERVATIVE_EXTENSIONS
from app.core.blocked_paths import checked_path, resolve_safe, validate_root_selection
from app.core.naming import make_unique_dest, relative_parts


@dataclass
class ValidationReport:
    valid_actions: list = field(default_factory=list)
    rejected_actions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class Validator:
    def __init__(self, root: Path, *, allow_suffix_on_collision=True, shortcuts_movable=False):
        self.root = resolve_safe(root)
        self.allow_suffix_on_collision = allow_suffix_on_collision
        self.shortcuts_movable = shortcuts_movable

    def validate_plan(self, plan):
        valid, reason = validate_root_selection(self.root)
        if not valid:
            raise ValueError(reason)
        if resolve_safe(Path(plan.root)) != self.root:
            raise ValueError("The plan belongs to a different folder")
        report = ValidationReport(warnings=list(plan.warnings))
        sources = set()
        destinations = {}

        def key(value):
            normalized = os.path.normcase(value).replace("\\", "/")
            return normalized.casefold() if sys.platform == "darwin" else normalized

        def occupied(value):
            path = checked_path(self.root, value)
            normalized = key(value)
            return path.exists() or path.is_symlink() or normalized in destinations

        for original in plan.actions:
            action = copy.copy(original)
            try:
                if action.type not in {"move", "rename", "mkdir"}:
                    raise ValueError(f"Unsupported action: {action.type}")
                if not math.isfinite(action.confidence) or not 0 <= action.confidence <= 1:
                    raise ValueError("Invalid confidence")
                action.dst_rel = "/".join(relative_parts(action.dst_rel))
                destination = checked_path(self.root, action.dst_rel)
                if action.type != "mkdir":
                    action.src_rel = "/".join(relative_parts(action.src_rel))
                    source = checked_path(self.root, action.src_rel)
                    if not source.is_file():
                        raise ValueError(f"Source is not an existing file: {action.src_rel}")
                    if key(action.src_rel) in sources:
                        raise ValueError("More than one operation uses this source")
                    if source.suffix.lower() in CONSERVATIVE_EXTENSIONS and not self.shortcuts_movable:
                        raise ValueError("Moving executables and shortcuts is disabled")
                    if key(action.src_rel) == key(action.dst_rel):
                        raise ValueError("Source and destination are identical")
                    if action.type == "move" and PurePosixPath(action.src_rel).parent == PurePosixPath(action.dst_rel).parent:
                        raise ValueError("A move must change the containing folder")
                if occupied(action.dst_rel):
                    if action.type == "mkdir" or not self.allow_suffix_on_collision:
                        raise ValueError(f"Destination already exists: {action.dst_rel}")
                    action.dst_rel = make_unique_dest(action.dst_rel, occupied)
                    destination = checked_path(self.root, action.dst_rel)
                for parent in destination.parents:
                    if parent == self.root:
                        break
                    if parent.exists() and not parent.is_dir():
                        raise ValueError(f"Destination parent is a file: {parent.name}")
                    relative = parent.relative_to(self.root).as_posix()
                    if destinations.get(key(relative)) in {"move", "rename"}:
                        raise ValueError("Destination parent is a planned file")
                prefix = key(action.dst_rel) + "/"
                if action.type != "mkdir" and any(value.startswith(prefix) for value in destinations):
                    raise ValueError("Destination conflicts with a planned folder")
                if sys.platform == "win32" and len(str(destination)) >= 260:
                    raise ValueError("Destination exceeds the Windows path limit")
                report.valid_actions.append(action)
                destinations[key(action.dst_rel)] = action.type
                if action.type != "mkdir":
                    sources.add(key(action.src_rel))
            except (OSError, RuntimeError, ValueError) as exc:
                report.rejected_actions.append((original, str(exc)))
                report.warnings.append(PlanWarning(original.id, f"Rejected: {exc}"))
        return report
