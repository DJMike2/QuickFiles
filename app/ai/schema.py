"""Data passed between the planner, preview, validator, and executor."""
from __future__ import annotations

import math
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
    importance_score: float = 0.5
    content_summary_type: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        confidence = float(value.get("confidence", 0.5))
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Confidence must be between zero and one")
        return cls(
            id=str(value.get("id", "")),
            type=str(value.get("type", "move")),
            src_rel=str(value.get("src_rel", "")),
            dst_rel=str(value.get("dst_rel", "")),
            category=str(value.get("category", "Misc")),
            confidence=confidence,
            reason=str(value.get("reason", "")),
            importance_score=float(value.get("importance_score", 0.5)),
            content_summary_type=str(value.get("content_summary_type", "")),
        )


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
