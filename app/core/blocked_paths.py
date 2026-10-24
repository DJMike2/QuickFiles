"""Canonical path checks shared by scanning, applying, and undo."""
import os
import sys
from pathlib import Path

from app.core.naming import relative_parts

UNIX_BLOCKED_ROOTS = (
    "/bin", "/sbin", "/usr", "/lib", "/lib64", "/lib32", "/etc", "/var",
    "/run", "/sys", "/proc", "/dev", "/boot", "/snap", "/opt",
    "/System", "/Library", "/private",
)


class BlockedPathError(ValueError):
    pass


def resolve_safe(path: Path) -> Path:
    # Resolution errors must not bypass symlink checks.
    return path.expanduser().resolve()


def blocked_roots() -> list[Path]:
    if sys.platform != "win32":
        return [Path(value) for value in UNIX_BLOCKED_ROOTS]
    drive = os.environ.get("SystemDrive", "C:")
    roots = [Path(drive + "/") / name for name in (
        "Windows", "Program Files", "Program Files (x86)", "ProgramData",
        "$Recycle.Bin", "Recovery", "System Volume Information",
    )]
    for variable in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]))
    for variable in ("APPDATA", "LOCALAPPDATA"):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]) / "Microsoft")
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Temp")
    return roots


def is_inside_root(path: Path, root: Path) -> bool:
    try:
        return resolve_safe(path).is_relative_to(resolve_safe(root))
    except (OSError, RuntimeError, ValueError):
        return False


def is_blocked(path: Path) -> bool:
    try:
        canonical = resolve_safe(path)
        return any(canonical.is_relative_to(resolve_safe(root)) for root in blocked_roots())
    except (OSError, RuntimeError, ValueError):
        return True


def enforce_not_blocked(path: Path):
    if is_blocked(path):
        raise BlockedPathError(f"Protected system location: {path}")


def enforce_inside_root(path: Path, root: Path):
    if not is_inside_root(path, root):
        raise BlockedPathError(f"Path is outside the selected folder: {path}")


def checked_path(root: Path, relative: str) -> Path:
    path = root.joinpath(*relative_parts(relative))
    enforce_inside_root(path, root)
    enforce_not_blocked(path)
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise BlockedPathError(f"Links are not movable: {path}")
    return path


def validate_root_selection(root: Path) -> tuple[bool, str]:
    try:
        if not root.is_dir():
            return False, f"Folder does not exist: {root}"
        enforce_not_blocked(root)
        if resolve_safe(root).parent == resolve_safe(root):
            return False, "Select a folder rather than the filesystem root."
        return True, ""
    except (OSError, RuntimeError, ValueError) as exc:
        return False, str(exc)
