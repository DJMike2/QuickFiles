"""Plan loose-file moves using one shared LLM folder plan and one leftover pass."""
from collections import Counter
from pathlib import PurePosixPath

from app.ai.content import read_content
from app.ai.llm_server import LLMServerError
from app.ai.schema import OrganizationPlan, PlanAction, PlanWarning
from app.core.naming import relative_parts
from app.constants import SHORTCUT_EXTENSIONS, EXECUTABLE_EXTENSIONS


class Planner:
    def __init__(self, runtime, progress_cb=None, cancelled=None, *, shortcuts_movable=False,
                 executables_movable=False):
        self.runtime = runtime
        self.progress_cb = progress_cb or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable

    def _check_cancelled(self):
        if self.cancelled():
            raise InterruptedError("Planning stopped")

    def plan(self, inventory):
        if not self.runtime or not self.runtime.available:
            raise LLMServerError("Local AI is required. Install the AI engine and model using Setup AI.")
        plan = OrganizationPlan(root=inventory.root)
        protected = sorted(inventory.protected_projects)
        blocked = [name.casefold() for name in protected]

        def off_limits(path):
            lower = path.casefold()
            return any(lower == project or lower.startswith(project + "/") for project in blocked)

        entries = [entry for entry in inventory.entries if entry.depth == 0
                   and (not entry.is_conservative
                        or self.shortcuts_movable and entry.extension in SHORTCUT_EXTENSIONS
                        or self.executables_movable and entry.extension in EXECUTABLE_EXTENSIONS)]
        if not entries:
            plan.excluded_count = inventory.total_files
            return plan
        evidence = {}

        def hint(entry):
            self._check_cancelled()
            if entry.rel_path not in evidence:
                evidence[entry.rel_path] = read_content(entry, inventory.content_read_bytes)
            return evidence[entry.rel_path]

        # Preserve subfolder context without opening project/application folders.
        samples = {directory: [] for directory in inventory.directories
                   if len(PurePosixPath(directory).parts) <= 2 and not off_limits(directory)}
        for entry in inventory.entries:
            parts = PurePosixPath(entry.rel_path).parts
            for depth in (1, 2):
                if len(parts) > depth:
                    parent = "/".join(parts[:depth])
                    if parent in samples and len(samples[parent]) < 3:
                        samples[parent].append(entry)
        self.progress_cb("Reading file and folder context…")
        folders = {}
        for directory, examples in samples.items():
            folders[directory] = "; ".join(
                entry.filename + (f" ({hint(entry)[:60]})" if hint(entry) else "")
                for entry in examples
            ) or "existing empty folder"
        files = [{'id': index, 'name': entry.filename, 'rel_path': entry.rel_path, 'hint': hint(entry)}
                 for index, entry in enumerate(entries, 1)]
        existing = set(folders)

        def add_folders(rows, leftovers=False):
            self._check_cancelled()
            proposals = self.runtime.plan_folders(
                rows, {path: folders[path] for path in existing_in_order}, protected, list(folders),
                leftovers=leftovers, status_cb=self.progress_cb, cancelled=self.cancelled,
            )
            occupied = {path.casefold() for path in folders}
            for item in proposals:
                path = item['path']
                try:
                    parts = relative_parts(path)
                    if len(parts) > 2 or off_limits(path):
                        raise ValueError("Folder is too deep or belongs to a protected project")
                    path = "/".join(parts)
                except ValueError as exc:
                    plan.warnings.append(PlanWarning("", f"Ignored folder {path!r}: {exc}"))
                    continue
                if path.casefold() not in occupied:
                    folders[path] = item['holds']
                    occupied.add(path.casefold())

        existing_in_order = list(folders)
        try:
            self.progress_cb("Planning a shared folder structure…")
            add_folders(files)
            chosen = self.runtime.assign_files(files, folders, self.progress_cb, self.cancelled)
            left = [row for row in files if row['rel_path'] not in chosen]
            if len(left) >= 2:
                self.progress_cb("Checking files that did not fit…")
                before = len(folders)
                add_folders(left, leftovers=True)
                if len(folders) > before:
                    chosen.update(self.runtime.assign_files(left, folders, self.progress_cb, self.cancelled))
            counts = Counter(chosen.values())
            for row in files:
                folder = chosen.get(row['rel_path'])
                if not folder or (folder not in existing and counts[folder] < 2):
                    continue
                plan.actions.append(PlanAction(
                    str(row['id']), "move", row['rel_path'], folder + "/" + row['name'],
                    category=folder, confidence=0.0, reason="Local AI matched the file to this folder",
                ))
            self._check_cancelled()
        except (LLMServerError, InterruptedError):
            self.runtime.shutdown()
            raise
        for number, action in enumerate(plan.actions, 1):
            action.id = str(number)
        plan.excluded_count = inventory.total_files - len(plan.actions)
        return plan
