from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class QuarterReadiness:
    quarter: str
    goal: str
    required_anchors: tuple[str, ...]
    present_anchors: tuple[str, ...]
    missing_anchors: tuple[str, ...]
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return not self.missing_anchors

    def to_dict(self) -> dict[str, object]:
        return {
            "quarter": self.quarter,
            "goal": self.goal,
            "ready": self.ready,
            "required_anchors": tuple(self.required_anchors),
            "present_anchors": tuple(self.present_anchors),
            "missing_anchors": tuple(self.missing_anchors),
            "capabilities": tuple(self.capabilities),
        }


@dataclass(frozen=True)
class Year1ReadinessReport:
    ready: bool
    quarters: tuple[QuarterReadiness, ...]
    summary: str

    def quarter(self, name: str) -> QuarterReadiness:
        normalized = str(name or "").strip().upper()
        for quarter in self.quarters:
            if quarter.quarter.upper() == normalized:
                return quarter
        raise KeyError(name)

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "summary": self.summary,
            "quarters": tuple(quarter.to_dict() for quarter in self.quarters),
        }


class Year1ReadinessChecker:
    Q1_ANCHORS = (
        "docs/observability_q1.md",
        "deploy/observability/prometheus/prometheus.yml",
        "deploy/observability/grafana/dashboards/elara-runtime.json",
        "utils/observability.py",
        "utils/json_logging.py",
        "runtime/interfaces/web_factory.py",
        "skills/advanced/trading/ipc_server.py",
        "scripts/profile_runtime.ps1",
    )
    Q2_ANCHORS = (
        "trading_core/coordination/event_stream.py",
        "trading_core/coordination/event_ledger.py",
        "trading_core/market/streaming.py",
        "trading_core/market/columnar.py",
        "utils/fast_json.py",
        "runtime/capabilities.py",
    )
    Q3_ANCHORS = (
        "trading_core/intelligence/regime.py",
        "trading_core/intelligence/bayesian.py",
        "trading_core/intelligence/uncertainty.py",
        "trading_core/intelligence/causal_graph.py",
        "trading_core/intelligence/probabilistic.py",
        "trading_core/intelligence/cognitive_modes.py",
        "trading_core/intelligence/explainability.py",
        "trading_core/orchestration/decision_reasoner.py",
        "trading_core/risk/risk_engine.py",
    )
    Q4_ANCHORS = (
        "trading_core/memory/operational_memory.py",
        "trading_core/simulation/fault_injection.py",
        "trading_core/simulation/macro.py",
        "trading_core/ml/lightweight.py",
        "trading_core/portfolio/intelligence.py",
    )

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def build_report(self) -> Year1ReadinessReport:
        quarters = (
            self._quarter(
                "Q1",
                "Create strong operational visibility and runtime reliability.",
                self.Q1_ANCHORS,
                (
                    "metrics",
                    "tracing_config",
                    "distributed_logs_config",
                    "runtime_health",
                    "profiling_workflow",
                ),
            ),
            self._quarter(
                "Q2",
                "Transform Elara into an event-driven, replayable, low-latency-ready runtime.",
                self.Q2_ANCHORS,
                (
                    "append_only_events",
                    "replay_cursor",
                    "backpressure",
                    "stream_normalization",
                    "serialization_boundary",
                    "capability_negotiation",
                ),
            ),
            self._quarter(
                "Q3",
                "Improve decision quality, adaptability, regime awareness, and explainability.",
                self.Q3_ANCHORS,
                (
                    "regime_detection",
                    "probabilistic_decision",
                    "uncertainty_profile",
                    "causal_graph_inference",
                    "runtime_cognitive_modes",
                    "decision_explainability",
                    "contextual_risk_sizing",
                ),
            ),
            self._quarter(
                "Q4",
                "Create adaptive learning capability without unrestricted self-modification.",
                self.Q4_ANCHORS,
                (
                    "operational_memory",
                    "fault_injection",
                    "macro_simulation",
                    "lightweight_ml",
                    "portfolio_intelligence",
                ),
            ),
        )
        ready = all(quarter.ready for quarter in quarters)
        missing_count = sum(len(quarter.missing_anchors) for quarter in quarters)
        summary = "Year 1 roadmap baseline ready." if ready else f"Year 1 roadmap baseline missing {missing_count} anchor(s)."
        return Year1ReadinessReport(ready=ready, quarters=quarters, summary=summary)

    def _quarter(
        self,
        quarter: str,
        goal: str,
        anchors: tuple[str, ...],
        capabilities: tuple[str, ...],
    ) -> QuarterReadiness:
        present = tuple(anchor for anchor in anchors if (self.root / anchor).exists())
        missing = tuple(anchor for anchor in anchors if anchor not in set(present))
        return QuarterReadiness(
            quarter=quarter,
            goal=goal,
            required_anchors=anchors,
            present_anchors=present,
            missing_anchors=missing,
            capabilities=capabilities,
        )
