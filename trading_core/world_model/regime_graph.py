from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class RegimeTransition:
    source: str
    target: str
    probability: float


@dataclass(frozen=True)
class RegimeInference:
    current: str
    probabilities: Mapping[str, float] = field(default_factory=dict)
    likely_next: str = ""


class ProbabilisticRegimeGraph:
    def __init__(self, transitions: tuple[RegimeTransition, ...]):
        self.transitions = tuple(transitions)

    def infer(self, current: str) -> RegimeInference:
        outgoing = tuple(transition for transition in self.transitions if transition.source == current)
        total = sum(max(0.0, float(transition.probability)) for transition in outgoing)
        probabilities = {
            transition.target: max(0.0, float(transition.probability)) / total
            for transition in outgoing
            if total > 0
        }
        # Use a lambda to access values so static checkers understand the key returns a float
        likely = max(probabilities, key=lambda k: probabilities[k]) if probabilities else current
        return RegimeInference(current=current, probabilities=probabilities, likely_next=likely)


def default_regime_graph() -> ProbabilisticRegimeGraph:
    return ProbabilisticRegimeGraph(
        (
            RegimeTransition("risk_on", "risk_on", 0.65),
            RegimeTransition("risk_on", "neutral", 0.25),
            RegimeTransition("risk_on", "risk_off", 0.10),
            RegimeTransition("neutral", "risk_on", 0.25),
            RegimeTransition("neutral", "neutral", 0.50),
            RegimeTransition("neutral", "risk_off", 0.25),
            RegimeTransition("risk_off", "risk_on", 0.10),
            RegimeTransition("risk_off", "neutral", 0.25),
            RegimeTransition("risk_off", "risk_off", 0.65),
        )
    )
