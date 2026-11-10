from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RoadmapAreaReadiness:
    area: str
    required_anchors: tuple[str, ...]
    present_anchors: tuple[str, ...]
    missing_anchors: tuple[str, ...]

    @property
    def score(self) -> int:
        if not self.required_anchors:
            return 100
        return round(100 * len(self.present_anchors) / len(self.required_anchors))

    @property
    def ready(self) -> bool:
        return not self.missing_anchors

    def to_dict(self) -> dict[str, object]:
        return {
            "area": self.area,
            "score": self.score,
            "ready": self.ready,
            "required_anchors": self.required_anchors,
            "present_anchors": self.present_anchors,
            "missing_anchors": self.missing_anchors,
        }


@dataclass(frozen=True)
class Year34ReadinessReport:
    score: int
    baseline_ready: bool
    areas: tuple[RoadmapAreaReadiness, ...]
    production_gaps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def missing_anchors(self) -> tuple[str, ...]:
        return tuple(anchor for area in self.areas for anchor in area.missing_anchors)

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "baseline_ready": self.baseline_ready,
            "production_ready": False,
            "missing_anchors": self.missing_anchors,
            "production_gaps": self.production_gaps,
            "areas": tuple(area.to_dict() for area in self.areas),
        }


class Year34ReadinessChecker:
    AREAS = {
        "year3_onchain_intelligence": (
            "trading_core/blockchain/events.py",
            "trading_core/blockchain/mempool.py",
            "trading_core/blockchain/wallet_graph.py",
        ),
        "year3_defi_and_governance": (
            "trading_core/blockchain/risk.py",
            "trading_core/blockchain/governance.py",
            "trading_core/blockchain/defi.py",
            "trading_core/simulation/defi_failure.py",
        ),
        "year3_hybrid_research": (
            "trading_core/blockchain/correlation.py",
            "trading_core/blockchain/fusion.py",
            "trading_core/blockchain/research.py",
            "trading_core/blockchain/sandbox.py",
        ),
        "year4_event_native_replay": (
            "trading_core/coordination/event_sourcing.py",
            "trading_core/coordination/external_bus.py",
            "trading_core/coordination/cqrs.py",
            "trading_core/coordination/snapshot_replay.py",
            "trading_core/evaluation/replay_evaluator.py",
            "trading_core/evaluation/replay_dataset.py",
        ),
        "year4_world_portfolio_governance": (
            "trading_core/world_model/causal_macro_graph.py",
            "trading_core/world_model/capital_flow.py",
            "trading_core/world_model/regime_graph.py",
            "trading_core/portfolio/institutional.py",
            "trading_core/governance/engine_v2.py",
        ),
        "year4_runtime_and_execution": (
            "crates/elara_runtime/Cargo.toml",
            "crates/elara_runtime/src/lib.rs",
            "trading_core/runtime/rust_gateway.py",
            "trading_core/execution/smart_router.py",
        ),
        "year4_validation_and_resilience": (
            "trading_core/cognition/validator.py",
            "trading_core/simulation/black_swan.py",
            "trading_core/simulation/latency_lab.py",
            "trading_core/research/evaluation_loop.py",
        ),
        "year4_operability": (
            "ELARA_THEORY.md",
            "docs/subsystems/constitution.md",
            "docs/decision_flow/decision_topology.md",
            "docs/year3_year4_execution.md",
            "trading_core/roadmap/complexity_governance.py",
        ),
    }
    PRODUCTION_GAPS = (
        "Validate Redis Streams high availability, retention, backup, and recovery drills.",
        "Move to NATS JetStream or Kafka only when measured scale requires it.",
        "Move Ed25519 private-key operations into isolated hardware-backed operator signing.",
        "Wire the Rust pilot into live streaming and execution processes.",
        "Capture production-grade datasets and validate replay determinism during shadow operation.",
    )

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def build_report(self) -> Year34ReadinessReport:
        areas = tuple(self._area(area, anchors) for area, anchors in self.AREAS.items())
        required = sum(len(area.required_anchors) for area in areas)
        present = sum(len(area.present_anchors) for area in areas)
        score = round(100 * present / required) if required else 100
        return Year34ReadinessReport(
            score=score,
            baseline_ready=score >= 90,
            areas=areas,
            production_gaps=self.PRODUCTION_GAPS,
        )

    def _area(self, area: str, anchors: tuple[str, ...]) -> RoadmapAreaReadiness:
        present = tuple(anchor for anchor in anchors if (self.root / anchor).exists())
        return RoadmapAreaReadiness(
            area=area,
            required_anchors=anchors,
            present_anchors=present,
            missing_anchors=tuple(anchor for anchor in anchors if anchor not in set(present)),
        )
