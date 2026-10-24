"""Build organization plans exclusively from the local LLM."""
import time

from app.ai.llm_server import LLMServerError
from app.ai.schema import OrganizationPlan, PlanAction


class Planner:
    def __init__(self, runtime, mode="fast", confidence_threshold=0.65,
                 progress_cb=None, cancelled=None):
        self.runtime = runtime
        self.mode = mode
        self.threshold = confidence_threshold
        self.progress_cb = progress_cb or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)

    def plan(self, inventory):
        if not self.runtime or not self.runtime.available:
            raise LLMServerError("Local AI is required. Install the AI engine and model using Setup AI.")
        profile = self.runtime.model_manager.profile(self.mode)
        batch_size = profile["batch_size"]
        folders = {directory.split("/")[0]: [] for directory in inventory.directories}
        active = set()
        cutoff = time.time() - 7 * 86400
        for entry in inventory.entries:
            if entry.depth:
                top = entry.rel_path.split("/")[0]
                samples = folders.setdefault(top, [])
                if len(samples) < 3:
                    samples.append(entry.filename)
                if entry.modified_ts > cutoff:
                    active.add(top)
        root_lower = inventory.root.lower()
        context = {
            "scan_context": next((word for word in ("desktop", "documents", "pictures") if word in root_lower), "folder"),
            "total_files": inventory.total_files,
            "active_folders": sorted(active),
            "crowded_root": sum(not e.depth for e in inventory.entries) > 20,
        }
        plan = OrganizationPlan(root=inventory.root)
        try:
            for offset in range(0, inventory.total_files, batch_size):
                if self.cancelled():
                    raise InterruptedError("Planning stopped")
                batch = inventory.entries[offset:offset + batch_size]
                self.progress_cb(f"Planning {min(offset + batch_size, inventory.total_files):,}/{inventory.total_files:,} files…")
                response = self.runtime.plan_batch(
                    self.mode, inventory.root, [e.to_compact_dict() for e in batch],
                    folders, context, self.progress_cb, self.cancelled,
                )
                batch_sources = {entry.rel_path for entry in batch}
                for value in response["actions"]:
                    try:
                        action = PlanAction.from_dict(value)
                        # The model cannot operate on files outside its input batch.
                        if action.src_rel in batch_sources and action.confidence >= self.threshold and action.type == "move":
                            action.id = f"ai-{len(plan.actions) + 1}"
                            plan.actions.append(action)
                    except (TypeError, ValueError):
                        continue
            if profile["two_pass"] and plan.actions:
                self.progress_cb("Verifying the proposed moves…")
                removed = self.runtime.verify_plan(self.mode, plan.actions, self.progress_cb, self.cancelled)
                plan.actions = [action for action in plan.actions if action.id not in removed]
        except LLMServerError:
            self.runtime.shutdown()
            raise
        for number, action in enumerate(plan.actions, 1):
            action.id = str(number)
        plan.excluded_count = inventory.total_files - len({a.src_rel for a in plan.actions})
        return plan
