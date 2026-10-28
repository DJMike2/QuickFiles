"""Adapt the native scanner's JSONL stream to the app's inventory interface."""
from __future__ import annotations

from contextlib import contextmanager
import fnmatch
import heapq
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from app.constants import (
    CONSERVATIVE_EXTENSIONS, DEFAULT_CONTENT_READ_BYTES, DEFAULT_MAX_FILES,
    EXT_CATEGORY_MAP,
)
from app.core.blocked_paths import blocked_roots, resolve_safe, validate_root_selection
from app.core.inventory import FileEntry, ScanInventory
from app.core.naming import relative_parts
from app.platforms import require_supported_platform

PROJECT_MARKERS = {".git", "package.json", "manifest.json", "pyproject.toml", "requirements.txt",
                   "Cargo.toml", "go.mod", "CMakeLists.txt", "node_modules", ".venv", "venv", "setup.py",
                   ".project", "steam_appid.txt", "UnityPlayer.dll", "unityplayer.dll",
                   "GameAssembly.dll", "gameassembly.dll", "resources.pak",
                   "unins000.exe", "Uninstall.exe", "uninstall.exe", "SupportPackages"}
NATIVE_DIR = Path(__file__).resolve().parent / "native"
log = logging.getLogger(__name__)


def _entry_order(entry):
    return (tuple(entry.rel_path.split("/")[:-1]), entry.filename)


class _Candidate:
    """A reverse-order heap retains only the first max_files deterministic paths."""
    __slots__ = ("entry", "key")

    def __init__(self, entry):
        self.entry = entry
        self.key = _entry_order(entry)

    def __lt__(self, other):
        return self.key > other.key


def scanner_binary() -> Path:
    """Resolve independently of cwd; packaged apps must include core/native/bin."""
    require_supported_platform()
    return NATIVE_DIR / "bin" / ("fastscan.exe" if sys.platform == "win32" else "fastscan")


def _native_glob(pattern: str) -> str:
    # fnmatch supplies escaping, ranges and malformed-bracket semantics. Translate
    # its Python-only dotall/atomic syntax to equivalent ECMAScript regex syntax.
    expression = fnmatch.translate(os.path.normcase(pattern))
    expression = expression[4:expression.rfind(")")].replace("(?>", "(?:")
    result = []
    in_class = False
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "\\":
            result.append(expression[index:index + 2])
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        result.append("[\\s\\S]" if char == "." and not in_class else char)
        index += 1
    return "".join(result)


def _write_policy(path: Path, excludes):
    fields = [("exclude", _native_glob(pattern)) for pattern in excludes]
    fields.extend(("marker", name) for name in sorted(PROJECT_MARKERS))
    fields.extend(("blocked", str(resolve_safe(root))) for root in blocked_roots())
    with path.open("wb") as output:
        for key, value in fields:
            if "\0" in value:
                raise ValueError("Scanner policy cannot contain NUL characters")
            output.write(key.encode("utf-8") + b"\0" + value.encode("utf-8") + b"\0")


@contextmanager
def _scratch_dir():
    """Windows may keep a dead child's stderr file locked briefly; cleanup must not mask the scan result."""
    path = Path(tempfile.mkdtemp(prefix="quickfiles-scan-"))
    try:
        yield path
    finally:
        for attempt in range(20):
            try:
                shutil.rmtree(path)
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(0.05 * (attempt + 1))
        else:
            shutil.rmtree(path, ignore_errors=True)


def _read_output(pipe, chunks, stop):
    """A bounded queue keeps cancellation responsive even on a stalled filesystem."""
    try:
        while not stop.is_set():
            chunk = pipe.read1(64 * 1024)
            while not stop.is_set():
                try:
                    chunks.put(chunk, timeout=0.05)
                    break
                except queue.Full:
                    continue
            if not chunk:
                return
    except (OSError, ValueError) as exc:
        while not stop.is_set():
            try:
                chunks.put(exc, timeout=0.05)
                return
            except queue.Full:
                continue


class Scanner:
    def __init__(self, root: Path, *, max_files=DEFAULT_MAX_FILES,
                 content_read_bytes=DEFAULT_CONTENT_READ_BYTES, exclude_patterns=None,
                 progress_cb=None, cancelled=None, threads=0):
        self.root = resolve_safe(root)
        self._root_string = str(self.root)
        self.max_files = max(1, int(max_files))
        self.content_read_bytes = max(0, min(int(content_read_bytes), 256 * 1024))
        self.exclude_patterns = exclude_patterns or []
        self.progress_cb = progress_cb or (lambda n, total, name: None)
        self.cancelled = cancelled or (lambda: False)
        self.threads = int(threads)
        if not 0 <= self.threads <= 64:
            raise ValueError("Scanner threads must be between 0 (auto) and 64")
        self.binary_path = scanner_binary()
        self.native_threads = 0

    def scan(self) -> ScanInventory:
        valid, reason = validate_root_selection(self.root)
        if not valid:
            raise ValueError(reason)
        if self.cancelled():
            raise InterruptedError("Scan stopped")
        if not self.binary_path.is_file():
            raise OSError("Native scanner is missing. Run 'python tools/build_scanner.py' "
                          "or install a build containing the scanner binary.")
        started = time.monotonic()
        inventory = ScanInventory(root=str(self.root), content_read_bytes=self.content_read_bytes)
        candidates = [] if self.max_files < DEFAULT_MAX_FILES else None
        self._seen_files = 0
        # Close the policy before launching to support Windows file sharing.
        with _scratch_dir() as temporary:
            policy = temporary / "policy.bin"
            _write_policy(policy, self.exclude_patterns)
            with (temporary / "stderr.txt").open("w+b") as diagnostics:
                process = subprocess.Popen(
                    [str(self.binary_path), str(self.root), str(policy), str(self.threads)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=diagnostics,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                stop = threading.Event()
                chunks = queue.Queue(maxsize=4)
                reader = threading.Thread(target=_read_output, args=(process.stdout, chunks, stop),
                                          name="fastscan-output", daemon=True)
                reader.start()
                header = summary = False
                pending = b""
                try:
                    while True:
                        if self.cancelled():
                            raise InterruptedError("Scan stopped")
                        try:
                            chunk = chunks.get(timeout=0.05)
                        except queue.Empty:
                            continue
                        if isinstance(chunk, Exception):
                            raise OSError(f"Could not read native scanner output: {chunk}")
                        if not chunk:
                            break
                        lines = (pending + chunk).split(b"\n")
                        pending = lines.pop()
                        if len(pending) > 1024 * 1024:
                            raise OSError("Native scanner emitted an oversized record")
                        for raw in lines:
                            if self.cancelled():
                                raise InterruptedError("Scan stopped")
                            try:
                                record = json.loads(raw)
                                kind = record["t"]
                                if summary:
                                    raise ValueError("Records after scanner summary")
                                if kind == "H" and not header:
                                    if record["protocol"] != 1:
                                        raise ValueError("Unsupported scanner protocol; rebuild the binary")
                                    self.native_threads = int(record["threads"])
                                    header = True
                                elif not header:
                                    raise ValueError("Missing scanner protocol header")
                                elif kind == "S":
                                    inventory.skipped_count += int(record["skipped"])
                                    inventory.error_count += int(record["errors"])
                                    summary = True
                                elif kind in {"D", "P", "F"}:
                                    self._add_record(record, inventory, candidates)
                                else:
                                    raise ValueError("Unknown scanner record")
                            except (KeyError, TypeError, ValueError, UnicodeError) as exc:
                                raise OSError(f"Invalid native scanner output: {exc}") from exc
                    while process.poll() is None:
                        if self.cancelled():
                            raise InterruptedError("Scan stopped")
                        try:
                            process.wait(timeout=0.05)
                        except subprocess.TimeoutExpired:
                            pass
                    if process.returncode:
                        diagnostics.seek(0)
                        message = diagnostics.read(8192).decode("utf-8", errors="replace").strip()
                        raise OSError(f"Native scanner failed ({process.returncode}): {message}")
                    if pending or not header or not summary:
                        raise OSError("Native scanner returned an incomplete inventory")
                    if inventory.error_count:
                        diagnostics.seek(0)
                        detail = diagnostics.read(8192).decode("utf-8", errors="replace").strip()
                        log.warning('Scan completed with %d errors in %s%s', inventory.error_count,
                                    self.root, f': {detail}' if detail else '')
                finally:
                    stop.set()
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    reader.join(timeout=2)
                    process.stdout.close()
        # Native workers finish out of order. Preserve stable planner IDs and caps.
        if candidates is not None:
            inventory.entries = [candidate.entry for candidate in candidates]
        inventory.entries.sort(key=_entry_order)
        inventory.skipped_count += self._seen_files - len(inventory.entries)
        inventory.directories.sort()
        inventory.protected_projects.sort()
        inventory.scan_duration_s = time.monotonic() - started
        return inventory

    def _add_record(self, record, inventory, candidates):
        relative = record["p"]
        try:
            parts = relative_parts(relative)
        except ValueError:
            if record["t"] == "F":
                inventory.skipped_count += 1
            return
        if "/".join(parts) != relative:
            raise ValueError("Scanner path is not a normalized relative path")
        if record["t"] in {"P", "D"}:
            inventory.directories.append(relative)
            if record["t"] == "P":
                inventory.protected_projects.append(relative)
            return
        name = parts[-1]
        _, dot, suffix = name.rpartition(".")
        extension = (dot + suffix).lower() if dot else ""
        size = int(record["sz"])
        entry = FileEntry(
            rel_path=relative, filename=name, extension=extension,
            abs_path=os.path.join(self._root_string, *parts), size_bytes=size,
            modified_ts=float(record["mt"]), file_type_bucket=EXT_CATEGORY_MAP.get(extension, "Misc"),
            is_conservative=extension in CONSERVATIVE_EXTENSIONS, is_empty=size == 0,
            parent_folder=parts[-2] if len(parts) > 1 else "", depth=len(parts) - 1,
        )
        self._seen_files += 1
        if candidates is None:
            inventory.entries.append(entry)
        else:
            candidate = _Candidate(entry)
            if len(candidates) < self.max_files:
                heapq.heappush(candidates, candidate)
            elif candidate.key < candidates[0].key:
                heapq.heapreplace(candidates, candidate)
        if self._seen_files % 100 == 0:
            self.progress_cb(min(self._seen_files, self.max_files), 0, name)
