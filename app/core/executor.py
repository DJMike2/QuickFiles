"""Apply the approved map with source snapshots, checksums and a persistent undo journal."""
from __future__ import annotations

import errno
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.schema import OrganizationPlan
from app.core.blocked_paths import checked_path, resolve_safe
from app.core.validator import Validator
from app.core.file_state import file_hash, identity
from app.storage import JournalOp

log = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    batch_id: int | None = None


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
        if file_hash(source) != file_hash(destination):
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

    def _record(self, batch_id, kind, source, destination, sha256=''):
        operation = JournalOp(
            batch_id, self._order, kind, str(source), str(destination),
            src_existed=source.exists(), dst_existed_pre=destination.exists(),
            sha256=sha256,
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
        if self.cancelled():
            result.skipped = len(actions)
            return result
        # Check the complete approved map before creating a batch or any directory.
        report = Validator(self.root, allow_suffix_on_collision=False,
            shortcuts_movable=self.shortcuts_movable,
            executables_movable=self.executables_movable).validate_plan(OrganizationPlan(str(self.root), actions))
        if report.rejected_actions:
            result.failed = len(report.rejected_actions)
            result.skipped = len(actions) - result.failed
            result.errors = [f'{a.src_rel or a.dst_rel}: {reason}' for a, reason in report.rejected_actions]
            log.warning('Apply preflight failed for %s: %s', self.root, '; '.join(result.errors))
            return result
        actions = report.valid_actions
        batch_id, _ = self.journal.begin_batch(str(self.root), len(actions))
        result.batch_id = batch_id
        for index, action in enumerate(actions):
            if self.cancelled():
                result.skipped += len(actions) - index
                break
            self.progress_cb(index + 1, len(actions), action.src_rel or action.dst_rel)
            op_id = None
            moved = False
            try:
                # Revalidate edits and changes since preview.
                report = Validator(
                    self.root, shortcuts_movable=self.shortcuts_movable,
                    executables_movable=self.executables_movable,
                ).validate_plan(OrganizationPlan(str(self.root), [action]))
                if report.rejected_actions:
                    raise ValueError(report.rejected_actions[0][1])
                action = report.valid_actions[0]
                destination = checked_path(self.root, action.dst_rel)
                source = checked_path(self.root, action.src_rel) if action.type != "mkdir" else destination
                checksum = ''
                if action.type != 'mkdir':
                    checksum = file_hash(source, self.cancelled)
                    if identity(source.stat()) != action.source_identity:
                        raise ValueError('Source changed while preparing to move; scan again')
                if self.cancelled():
                    raise InterruptedError('Apply stopped before moving the file')
                self._ensure_parents(batch_id, destination)
                op_id = self._record(batch_id, action.type, source, destination, checksum)
                if action.type == "mkdir":
                    destination.mkdir()
                else:
                    # Check again after parent creation/journaling, immediately before mutation.
                    if identity(source.stat()) != action.source_identity:
                        raise ValueError('Source changed before moving; scan again')
                    safe_move(source, destination)
                moved = True
                # Once a move starts, finish verifying that file even if cancellation arrives.
                # This keeps the journal truthful and leaves cancellation at a file boundary.
                if action.type == 'mkdir':
                    if not destination.is_dir():
                        raise OSError('Created folder could not be verified')
                elif (source.exists() or not destination.is_file() or destination.is_symlink()
                      or file_hash(destination) != checksum):
                    raise OSError('Destination verification failed; consult the journal before retrying')
                self.journal.mark_op_success(op_id)
                result.succeeded += 1
            except InterruptedError:
                result.skipped += len(actions) - index
                break
            except Exception as exc:
                log.exception('Apply failed for %s -> %s', action.src_rel, action.dst_rel)
                if op_id is not None:
                    if moved:
                        self.journal.mark_op_verification_failed(op_id, str(exc))
                    else:
                        self.journal.mark_op_failed(op_id, str(exc))
                result.failed += 1
                result.errors.append(f"{action.src_rel or action.dst_rel}: {exc}")
                if moved:
                    result.skipped += len(actions) - index - 1
                    break
        return result
