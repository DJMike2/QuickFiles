"""Reverse successful journal entries, retaining conflicts for a later retry."""
from dataclasses import dataclass, field
import logging
from pathlib import Path

from app.core.blocked_paths import checked_path, enforce_project_boundary, resolve_safe
from app.core.executor import safe_move
from app.core.file_state import file_hash, identity

log = logging.getLogger(__name__)


@dataclass
class UndoResult:
    batch_id: int
    total: int = 0
    reversed_ok: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class UndoEngine:
    def __init__(self, root, journal, progress_cb=None):
        self.root = resolve_safe(root)
        self.journal = journal
        self.progress_cb = progress_cb or (lambda current, total, name: None)

    def undo_batch(self, batch_id):
        batch = self.journal.get_batch_by_id(batch_id)
        if not batch or batch.undone:
            raise ValueError("No pending batch to undo")
        if resolve_safe(Path(batch.root)) != self.root:
            raise ValueError("Journal root does not match the selected batch")
        operations = [op for op in batch.ops if op.succeeded and not op.undone]
        result = UndoResult(batch_id, total=len(operations))
        for index, operation in enumerate(reversed(operations)):
            self.progress_cb(index + 1, len(operations), operation.dst_abs)
            try:
                self._reverse(operation)
                self.journal.mark_op_undone(operation.id)
                result.reversed_ok += 1
            except (OSError, RuntimeError, ValueError) as exc:
                log.warning('Undo failed for %s -> %s', operation.dst_abs, operation.src_abs, exc_info=True)
                result.skipped += 1
                result.errors.append(str(exc))
        if not result.skipped:
            self.journal.mark_batch_undone(batch_id)
        return result

    def _path(self, absolute):
        path = Path(absolute)
        relative = path.relative_to(self.root).as_posix()
        return checked_path(self.root, relative)

    def _reverse(self, operation):
        destination = self._path(operation.dst_abs)
        enforce_project_boundary(self.root, destination)
        if operation.op_type == "mkdir":
            if not operation.dst_existed_pre and destination.exists():
                destination.rmdir()  # Never remove a directory that now contains files.
            return
        if operation.op_type not in {"move", "rename"}:
            raise ValueError(f"Cannot undo unsupported operation: {operation.op_type}")
        source = self._path(operation.src_abs)
        enforce_project_boundary(self.root, source)
        if not destination.is_file():
            raise FileNotFoundError(f"File is no longer at {destination}")
        snapshot = identity(destination.stat())
        if operation.sha256 and file_hash(destination) != operation.sha256:
            raise ValueError(f'File changed since apply; undo preserved it at {destination}')
        if identity(destination.stat()) != snapshot:
            raise ValueError(f'File changed while checking undo: {destination}')
        if source.exists() or source.is_symlink():
            raise FileExistsError(f"Original location is occupied: {source}")
        source.parent.mkdir(parents=True, exist_ok=True)
        if identity(destination.stat()) != snapshot:
            raise ValueError(f'File changed before undo: {destination}')
        safe_move(destination, source)
        if operation.sha256 and file_hash(source) != operation.sha256:
            raise OSError(f'Undo verification failed at {source}')
