from __future__ import annotations

from dataclasses import dataclass

from trading_core.data_contracts import ReasonedDecision


@dataclass(frozen=True)
class Attribution:
    factor: str
    contribution: float
    direction: str
    reason: str


@dataclass(frozen=True)
class AttributionReport:
    action: str
    approved: bool
    attributions: tuple[Attribution, ...]


class ExplainableAttributionEngine:
    def attribute(self, decision: ReasonedDecision) -> AttributionReport:
        rows = []
        for component in decision.components:
            contribution = float(component.confidence) * float(component.weight)
            if component.verdict == "block":
                contribution *= -1
            elif component.verdict == "degrade":
                contribution *= 0.5
            rows.append(
                Attribution(
                    factor=component.source,
                    contribution=contribution,
                    direction="support" if contribution >= 0 else "oppose",
                    reason=",".join(component.reasons[:3]),
                )
            )
        rows.sort(key=lambda item: abs(item.contribution), reverse=True)
        return AttributionReport(
            action=decision.action,
            approved=decision.approved,
            attributions=tuple(rows),
        )
