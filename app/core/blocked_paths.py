"""Canonical path checks shared by scanning, applying, and undo."""
import os
import stat
import sys
from pathlib import Path

from app.config import app_data_path
from app.core.naming import relative_parts

UNIX_BLOCKED_ROOTS = (
    "/bin", "/sbin", "/usr", "/lib", "/lib64", "/lib32", "/etc", "/var",
    "/run", "/sys", "/proc", "/dev", "/boot", "/snap", "/opt",
)
_WIN_DRIVE_NAMES = (
    "Windows", "Program Files", "Program Files (x86)", "Program Files (Arm)",
    "ProgramData", "$Recycle.Bin", "Recovery", "System Volume Information",
    "Windows.old", "Config.Msi", "Boot", "EFI", "PerfLogs", "inetpub",
)
# FILE_ATTRIBUTE_SYSTEM, plus offline / recall attributes used by cloud placeholders.
_WIN_SYSTEM = 0x4
_WIN_CLOUD = 0x1000 | 0x40000 | 0x400000
_SHELL_NAMES = {"desktop.ini", "thumbs.db", "ehthumbs.db"}


class BlockedPathError(ValueError):
    pass


def enforce_project_boundary(root: Path, path: Path):
    """Recheck the native scanner's markers and special project names before writes."""
    from app.core.scanner import PROJECT_MARKERS
    markers = {name.casefold() for name in PROJECT_MARKERS}
    for parent in path.parents:
        if parent == root:
            break  # The explicitly selected root follows the scanner's root policy.
        protected = parent.name.casefold() == 'issetupprerequisites'
        if not protected and parent.is_dir():
            protected = any(child.name.casefold() in markers
                            or child.name.casefold().endswith(('.sln', '.csproj')) for child in parent.iterdir())
        if protected:
            raise BlockedPathError(f'Application/project folder is protected: {parent.name}')


def resolve_safe(path: Path) -> Path:
    # Resolution errors must not bypass symlink checks.
    return path.expanduser().resolve()


def blocked_roots() -> list[Path]:
    if sys.platform != "win32":
        roots = [Path(value) for value in UNIX_BLOCKED_ROOTS]
        home = Path.home()
        roots.append(home / "snap")
    else:
        drive = os.environ.get("SystemDrive", "C:")
        roots = [Path(drive + "/") / name for name in _WIN_DRIVE_NAMES]
        for variable in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            if os.environ.get(variable):
                roots.append(Path(os.environ[variable]))
        roots.extend(_windows_profile_roots())
    roots.append(app_data_path())
    return roots


def _windows_profile_roots() -> list[Path]:
    roots = []
    appdata_covered = False
    for variable in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if not value:
            continue
        path = Path(value)
        if path.parent.name.casefold() == "appdata":
            if path.parent not in roots:
                roots.append(path.parent)
            appdata_covered = True
        else:
            roots.append(path)
    if not appdata_covered:
        roots.append(Path.home() / "AppData")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(Path(local) / "Temp")
            roots.append(Path(local) / "Programs")
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


def _protected_filename(name: str) -> bool:
    folded = name.casefold()
    if folded.startswith("~$") or folded in _SHELL_NAMES or folded == "ntuser.ini":
        return True
    for stem in ("ntuser.dat", "usrclass.dat"):
        if folded == stem or folded.startswith((stem + ".", stem + "{")):
            return True
    return False


def _inside_link(path: Path, root: Path) -> bool:
    current = path.parent
    while current != root and current != current.parent:
        try:
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                return True
        except OSError:
            return True
        current = current.parent
    return False


def _windows_file_block_reason(path: Path) -> str:
    if sys.platform != "win32":
        return ""
    try:
        info = path.lstat()
    except OSError:
        return ""
    if stat.S_ISDIR(info.st_mode):
        return ""
    attributes = getattr(info, "st_file_attributes", 0)
    if attributes & _WIN_SYSTEM:
        return f"System file: {path.name}"
    if attributes & _WIN_CLOUD:
        return f"Cloud-only file: {path.name}"
    return ""


def checked_path(root: Path, relative: str) -> Path:
    path = root.joinpath(*relative_parts(relative))
    return _checked_local_path(root, path)


def checked_read_path(root: Path, relative: str) -> Path:
    """Read-only searches accept local filenames without portable rename restrictions."""
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise BlockedPathError('Search path must stay inside the selected folder')
    return _checked_local_path(root, root / relative)


def _checked_local_path(root: Path, path: Path) -> Path:
    enforce_inside_root(path, root)
    enforce_not_blocked(path)
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise BlockedPathError(f"Links are not movable: {path}")
    if _inside_link(path, resolve_safe(root)):
        raise BlockedPathError(f"Path is inside a link: {path}")
    if _protected_filename(path.name):
        raise BlockedPathError(f"Protected file: {path.name}")
    reason = _windows_file_block_reason(path)
    if reason:
        raise BlockedPathError(reason)
    return path


def _account_parents() -> list[Path]:
    if sys.platform == "win32":
        return [Path(os.environ.get("SystemDrive", "C:") + "/") / "Users"]
    return [Path("/home")]


def _account_selection_error(root: Path) -> str:
    canonical = resolve_safe(root)
    try:
        home = resolve_safe(Path.home())
    except (OSError, RuntimeError, ValueError):
        home = None
    for parent in _account_parents():
        try:
            resolved = resolve_safe(parent)
        except (OSError, RuntimeError, ValueError):
            continue
        if canonical == resolved:
            return "Select a folder inside your account. This folder contains every account."
        if canonical.parent == resolved and canonical != home:
            return "This folder belongs to another account. Select a folder inside your account."
    return ""


def validate_root_selection(root: Path) -> tuple[bool, str]:
    try:
        if not root.is_dir():
            return False, f"Folder does not exist: {root}"
        enforce_not_blocked(root)
        if resolve_safe(root).parent == resolve_safe(root):
            return False, "Select a folder rather than the filesystem root."
        reason = _account_selection_error(root)
        if reason:
            return False, reason
        return True, ""
    except (OSError, RuntimeError, ValueError) as exc:
        return False, str(exc)
