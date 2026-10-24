"""Coordinate local AI batches, falling back to rules after one failure."""
from dataclasses import dataclass
import logging
import time

from app.ai.llm_server import LLMServerError
from app.ai.rule_classifier import classify_inventory
from app.ai.schema import OrganizationPlan, PlanAction
from app.core.inventory import ScanInventory

log = logging.getLogger(__name__)


@dataclass
class PlanResult:
    plan: OrganizationPlan
    used_rule_fallback: bool = False
    fallback_reason: str = ""


class Planner:
    def __init__(self, runtime=None, mode="fast", confidence_threshold=0.65,
                 shortcuts_movable=False, progress_cb=None, cancelled=None, use_ai=True):
        self.runtime = runtime
        self.mode = mode
        self.threshold = confidence_threshold
        self.shortcuts_movable = shortcuts_movable
        self.progress_cb = progress_cb or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self.use_ai = use_ai

    def _rules(self, inventory):
        return classify_inventory(inventory, self.threshold, self.shortcuts_movable, self.cancelled)

    def plan(self, inventory):
        if not self.use_ai or not self.runtime or not self.runtime.available:
            self.progress_cb("Planning with built-in rules…")
            return PlanResult(self._rules(inventory), True, "Using the built-in rule classifier")
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
        failure = ""
        for offset in range(0, inventory.total_files, batch_size):
            if self.cancelled():
                raise InterruptedError("Planning stopped")
            batch = inventory.entries[offset:offset + batch_size]
            self.progress_cb(f"Planning {min(offset + batch_size, inventory.total_files):,}/{inventory.total_files:,} files…")
            if not failure:
                try:
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
                    continue
                except LLMServerError as exc:
                    failure = str(exc).splitlines()[0][:200]
                    log.warning("Using rule fallback for remaining batches: %s", failure)
                    self.runtime.shutdown()
            plan.actions.extend(self._rules(ScanInventory(inventory.root, entries=batch)).actions)
        if not failure and profile["two_pass"] and plan.actions:
            self.progress_cb("Verifying the proposed moves…")
            removed = self.runtime.verify_plan(self.mode, plan.actions, self.progress_cb, self.cancelled)
            plan.actions = [action for action in plan.actions if action.id not in removed]
        for number, action in enumerate(plan.actions, 1):
            action.id = str(number)
        plan.excluded_count = inventory.total_files - len({a.src_rel for a in plan.actions})
        return PlanResult(plan, bool(failure), f"Local AI unavailable: {failure}" if failure else "")
