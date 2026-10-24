"""Conservative extension-based planning when local AI is unavailable."""
import time

from app.ai.schema import OrganizationPlan, PlanAction
from app.constants import CATEGORY_EXTENSIONS, CONSERVATIVE_EXTENSIONS


def classify_inventory(inventory, confidence_threshold=0.7, shortcuts_movable=False,
                       cancelled=None):
    plan = OrganizationPlan(root=inventory.root)
    categories = {name.lower() for name in CATEGORY_EXTENSIONS} | {"misc"}
    for entry in inventory.entries:
        if cancelled and cancelled():
            raise InterruptedError("Planning stopped")
        category = entry.file_type_bucket
        parent = entry.parent_folder.lower()
        # Preserve project folders and other existing relationships.
        if entry.depth and (parent not in categories or parent == category.lower()):
            continue
        conservative = entry.is_conservative or entry.extension in CONSERVATIVE_EXTENSIONS
        if conservative and not shortcuts_movable:
            continue
        confidence = 0.7 if conservative else 0.85
        if entry.is_empty or category == "Misc":
            confidence = 0.5
        if confidence < confidence_threshold:
            continue
        destination = f"{category}/{entry.filename}"
        if destination == entry.rel_path:
            continue
        age_days = (time.time() - entry.modified_ts) / 86400 if entry.modified_ts else 365
        importance = 0.5 + (0.2 if not entry.depth else 0) + (0.2 if age_days < 30 else 0)
        plan.actions.append(PlanAction(
            id=f"rule-{len(plan.actions) + 1}", type="move", src_rel=entry.rel_path,
            dst_rel=destination, category=category, confidence=confidence,
            reason=f"Extension {entry.extension} belongs in {category}",
            importance_score=min(importance, 1), content_summary_type=entry.content_role,
        ))
    plan.excluded_count = inventory.total_files - len(plan.actions)
    return plan
