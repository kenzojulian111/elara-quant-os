from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class LayerDecision:
    layer: str
    action: str
    approved: bool
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HierarchicalDecision:
    final_action: str
    approved: bool
    controlling_layer: str
    confidence: float
    layers: tuple[LayerDecision, ...]
    reasons: tuple[str, ...]


class HierarchicalDecisionSystem:
    priority = ("reflex", "governance", "tactical", "strategic")

    def decide(self, layers: Iterable[LayerDecision]) -> HierarchicalDecision:
        rows = tuple(layers or ())
        by_layer = {row.layer: row for row in rows}
        reasons: list[str] = []
        for layer_name in self.priority:
            decision = by_layer.get(layer_name)
            if not decision:
                continue
            reasons.extend(decision.reasons)
            if not decision.approved:
                return HierarchicalDecision(
                    final_action="hold" if decision.action not in {"exit", "flatten"} else decision.action,
                    approved=False,
                    controlling_layer=decision.layer,
                    confidence=float(decision.confidence),
                    layers=rows,
                    reasons=tuple(reasons),
                )
            if layer_name == "reflex" and decision.action in {"exit", "flatten"}:
                return HierarchicalDecision(
                    final_action=decision.action,
                    approved=True,
                    controlling_layer=decision.layer,
                    confidence=float(decision.confidence),
                    layers=rows,
                    reasons=tuple(reasons),
                )
        approved_rows = [row for row in rows if row.approved]
        chosen = approved_rows[-1] if approved_rows else LayerDecision("none", "hold", False, 0.0, ("no_layer_approved",))
        return HierarchicalDecision(
            final_action=chosen.action,
            approved=bool(chosen.approved),
            controlling_layer=chosen.layer,
            confidence=float(chosen.confidence),
            layers=rows,
            reasons=tuple(reasons or chosen.reasons),
        )
