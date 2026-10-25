"""Apply validated moves and journal the directories they create."""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.schema import OrganizationPlan
from app.core.blocked_paths import checked_path, resolve_safe
from app.core.validator import Validator
from app.storage import JournalOp


@dataclass
class ExecutionResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def safe_move(source: Path, destination: Path):
    """Move without replacing an existing file, including across volumes."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Destination is occupied: {destination}")
    try:
        if sys.platform == "win32":
            source.rename(destination)  # Windows rename refuses an occupied destination.
        else:
            os.link(source, destination, follow_symlinks=False)
            try:
                source.unlink()
            except OSError:
                destination.unlink()
                raise
        return
    except OSError as exc:
        if exc.errno not in {errno.EXDEV, errno.EPERM, errno.ENOSYS, errno.EOPNOTSUPP}:
            raise
    created = False
    try:
        with source.open("rb") as original, destination.open("xb") as copied:
            created = True
            shutil.copyfileobj(original, copied, length=1024 * 1024)
            copied.flush()
            os.fsync(copied.fileno())
        shutil.copystat(source, destination)
        if _file_hash(source) != _file_hash(destination):
            raise OSError("Copy verification failed; original was preserved")
        source.unlink()
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


class Executor:
    def __init__(self, root, journal, progress_cb=None, cancelled=None, shortcuts_movable=False,
                 executables_movable=False):
        self.root = resolve_safe(root)
        self.journal = journal
        self.progress_cb = progress_cb or (lambda current, total, name: None)
        self.cancelled = cancelled or (lambda: False)
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable
        self._order = 0

    def _record(self, batch_id, kind, source, destination):
        operation = JournalOp(
            batch_id, self._order, kind, str(source), str(destination),
            src_existed=source.exists(), dst_existed_pre=destination.exists(),
        )
        self._order += 1
        return self.journal.record_op(operation)

    def _ensure_parents(self, batch_id, destination):
        missing = []
        parent = destination.parent
        while parent != self.root and not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            checked_path(self.root, directory.relative_to(self.root).as_posix())
            op_id = self._record(batch_id, "mkdir", directory, directory)
            try:
                directory.mkdir()
                self.journal.mark_op_success(op_id)
            except Exception as exc:
                self.journal.mark_op_failed(op_id, str(exc))
                raise

    def apply(self, actions):
        result = ExecutionResult(total=len(actions))
        if not actions:
            return result
        batch_id, _ = self.journal.begin_batch(str(self.root), len(actions))
        for index, action in enumerate(actions):
            if self.cancelled():
                result.skipped += len(actions) - index
                break
            self.progress_cb(index + 1, len(actions), action.src_rel or action.dst_rel)
            op_id = None
            try:
                # Revalidate edits and changes since preview, without silently suffixing paths.
                report = Validator(
                    self.root, allow_suffix_on_collision=False,
                    shortcuts_movable=self.shortcuts_movable,
                    executables_movable=self.executables_movable,
                ).validate_plan(OrganizationPlan(str(self.root), [action]))
                if report.rejected_actions:
                    raise ValueError(report.rejected_actions[0][1])
                action = report.valid_actions[0]
                destination = checked_path(self.root, action.dst_rel)
                source = checked_path(self.root, action.src_rel) if action.type != "mkdir" else destination
                self._ensure_parents(batch_id, destination)
                op_id = self._record(batch_id, action.type, source, destination)
                if action.type == "mkdir":
                    destination.mkdir()
                else:
                    safe_move(source, destination)
                self.journal.mark_op_success(op_id)
                result.succeeded += 1
            except Exception as exc:
                if op_id is not None:
                    self.journal.mark_op_failed(op_id, str(exc))
                result.failed += 1
                result.errors.append(f"{action.src_rel or action.dst_rel}: {exc}")
        return result
