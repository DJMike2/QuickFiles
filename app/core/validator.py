"""Validate a complete plan before showing or executing its changes."""
from __future__ import annotations

import copy
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.ai.schema import PlanWarning
from app.constants import (
    EXECUTABLE_EXTENSIONS, LIBRARY_EXTENSIONS, LIVE_DATA_EXTENSIONS, SHORTCUT_EXTENSIONS,
)
from app.core.blocked_paths import checked_path, resolve_safe, validate_root_selection
from app.core.naming import relative_parts


@dataclass
class ValidationReport:
    valid_actions: list = field(default_factory=list)
    rejected_actions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


_PROJECT_MARKERS = (".git", "package.json", "pyproject.toml", "Cargo.toml", "go.mod")


class Validator:
    def __init__(self, root: Path, *, shortcuts_movable=False, executables_movable=False):
        self.root = resolve_safe(root)
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable

    def _reject_immovable(self, source: Path):
        suffix = source.suffix.lower()
        if suffix in LIBRARY_EXTENSIONS:
            raise ValueError("Moving libraries and drivers is disabled")
        if suffix in LIVE_DATA_EXTENSIONS:
            raise ValueError("Moving live databases and disk images is disabled")
        if suffix in SHORTCUT_EXTENSIONS and not self.shortcuts_movable:
            raise ValueError("Moving shortcuts is disabled")
        if suffix in EXECUTABLE_EXTENSIONS and not self.executables_movable:
            raise ValueError("Moving executables is disabled")

    def _project_warning(self):
        try:
            found = next((name for name in _PROJECT_MARKERS if (self.root / name).exists()), "")
        except OSError:
            return ""
        if not found:
            return ""
        return f"This folder looks like a project ({found}). Review moves so the source layout stays intact."

    def validate_plan(self, plan):
        valid, reason = validate_root_selection(self.root)
        if not valid:
            raise ValueError(reason)
        if resolve_safe(Path(plan.root)) != self.root:
            raise ValueError("The plan belongs to a different folder")
        report = ValidationReport(warnings=list(plan.warnings))
        project_warning = self._project_warning()
        if project_warning and all(item.message != project_warning for item in report.warnings):
            report.warnings.insert(0, PlanWarning("", project_warning))
        sources = set()
        destinations = {}

        def key(value):
            return os.path.normcase(value).replace("\\", "/")

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
                    self._reject_immovable(source)
                    if key(action.src_rel) == key(action.dst_rel):
                        raise ValueError("Source and destination are identical")
                    if action.type == "move" and PurePosixPath(action.src_rel).parent == PurePosixPath(action.dst_rel).parent:
                        raise ValueError("A move must change the containing folder")
                if occupied(action.dst_rel):
                    raise ValueError(f"Destination already exists: {action.dst_rel}")
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
                # MAX_PATH includes the terminating null, so 259 characters can still fail.
                if sys.platform == "win32" and len(str(destination)) >= 259:
                    raise ValueError("Destination exceeds the Windows path limit")
                report.valid_actions.append(action)
                destinations[key(action.dst_rel)] = action.type
                if action.type != "mkdir":
                    sources.add(key(action.src_rel))
            except (OSError, RuntimeError, ValueError) as exc:
                report.rejected_actions.append((original, str(exc)))
                report.warnings.append(PlanWarning(original.id, f"Rejected: {exc}"))
        return report
