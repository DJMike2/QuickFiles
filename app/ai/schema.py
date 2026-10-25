"""Data passed between the planner, preview, validator, and executor."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class PlanAction:
    id: str
    type: str
    src_rel: str
    dst_rel: str
    category: str = "Misc"
    confidence: float = 0.5
    reason: str = ""

    def to_dict(self):
        return asdict(self)

@dataclass
class PlanWarning:
    action_id: str
    message: str


@dataclass
class OrganizationPlan:
    root: str
    actions: list[PlanAction] = field(default_factory=list)
    warnings: list[PlanWarning] = field(default_factory=list)
    excluded_count: int = 0

    def to_dict(self):
        return asdict(self)
