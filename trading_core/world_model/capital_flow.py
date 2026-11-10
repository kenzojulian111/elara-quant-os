from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CapitalFlowObservation:
    source: str
    target: str
    asset: str
    usd_value: float
    confidence: float = 1.0


@dataclass(frozen=True)
class CapitalFlowState:
    net_by_node: Mapping[str, float] = field(default_factory=dict)
    net_by_asset: Mapping[str, float] = field(default_factory=dict)
    dominant_destination: str = ""
    risk_state: str = "neutral"
    reasons: tuple[str, ...] = field(default_factory=tuple)


class CapitalFlowSimulator:
    def infer(self, observations: tuple[CapitalFlowObservation, ...]) -> CapitalFlowState:
        nodes: dict[str, float] = {}
        assets: dict[str, float] = {}
        for observation in observations:
            weighted = float(observation.usd_value) * _clamp(observation.confidence)
            nodes[observation.source] = nodes.get(observation.source, 0.0) - weighted
            nodes[observation.target] = nodes.get(observation.target, 0.0) + weighted
            assets[observation.asset] = assets.get(observation.asset, 0.0) + weighted
        # choose the node with the largest net inflow; use items() to satisfy type checkers
        dominant = (max(nodes.items(), key=lambda kv: kv[1])[0]) if nodes else ""
        exchange_inflow = sum(value for node, value in nodes.items() if "exchange" in node.lower() and value > 0)
        stablecoin_flow = sum(value for asset, value in assets.items() if "usd" in asset.lower())
        risk_state = "risk_off" if exchange_inflow > max(1.0, stablecoin_flow) else "neutral"
        return CapitalFlowState(
            net_by_node=nodes,
            net_by_asset=assets,
            dominant_destination=dominant,
            risk_state=risk_state,
            reasons=(f"dominant_destination={dominant or 'none'}", f"exchange_inflow={exchange_inflow:.2f}"),
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
