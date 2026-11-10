from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trading_core.roadmap.year34_readiness import RoadmapAreaReadiness


@dataclass(frozen=True)
class Year5ReadinessReport:
    score: int
    baseline_ready: bool
    areas: tuple[RoadmapAreaReadiness, ...]

    @property
    def missing_anchors(self) -> tuple[str, ...]:
        return tuple(anchor for area in self.areas for anchor in area.missing_anchors)

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "baseline_ready": self.baseline_ready,
            "missing_anchors": self.missing_anchors,
            "areas": tuple(area.to_dict() for area in self.areas),
        }


class Year5ReadinessChecker:
    AREAS = {
        "institutional_cognition": (
            "trading_core/institutional/platform.py",
            "trading_core/world_model/capital_flow.py",
            "trading_core/portfolio/institutional.py",
        ),
        "governed_execution": (
            "trading_core/governance/engine_v2.py",
            "trading_core/execution/smart_router.py",
            "trading_core/blockchain/governance.py",
        ),
        "cognitive_mesh_validation": (
            "trading_core/cognition/validator.py",
            "trading_core/cognition/reasoning_node.py",
            "trading_core/cognition/compute_router.py",
        ),
        "institutional_resilience": (
            "trading_core/simulation/black_swan.py",
            "trading_core/simulation/latency_lab.py",
            "docs/year5_institutional_baseline.md",
        ),
    }

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def build_report(self) -> Year5ReadinessReport:
        areas = tuple(self._area(area, anchors) for area, anchors in self.AREAS.items())
        required = sum(len(area.required_anchors) for area in areas)
        present = sum(len(area.present_anchors) for area in areas)
        score = round(100 * present / required) if required else 100
        return Year5ReadinessReport(score=score, baseline_ready=score >= 90, areas=areas)

    def _area(self, area: str, anchors: tuple[str, ...]) -> RoadmapAreaReadiness:
        present = tuple(anchor for anchor in anchors if (self.root / anchor).exists())
        return RoadmapAreaReadiness(
            area=area,
            required_anchors=anchors,
            present_anchors=present,
            missing_anchors=tuple(anchor for anchor in anchors if anchor not in set(present)),
        )
