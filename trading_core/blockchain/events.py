from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


@dataclass(frozen=True)
class BlockchainEvent:
    chain: str
    event_type: str
    tx_hash: str
    asset: str = ""
    amount_usd: float = 0.0
    source: str = ""
    destination: str = ""
    block_number: int = 0
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class OnChainSignal:
    signal_type: str
    asset: str
    severity: str
    confidence: float
    value_usd: float
    reasons: tuple[str, ...] = ()


class OnChainIntelligence:
    def __init__(self, *, whale_threshold_usd: float = 1_000_000.0):
        self.whale_threshold_usd = max(1.0, float(whale_threshold_usd))

    def analyze(self, events: Iterable[BlockchainEvent]) -> tuple[OnChainSignal, ...]:
        signals: list[OnChainSignal] = []
        for event in events:
            event_type = str(event.event_type or "").lower()
            value = abs(float(event.amount_usd))
            if value >= self.whale_threshold_usd:
                signals.append(OnChainSignal("whale_movement", event.asset, "medium", 0.75, value, (f"chain={event.chain}",)))
            if event_type in {"exchange_inflow", "exchange_outflow", "stablecoin_mint", "stablecoin_burn", "bridge_transfer", "governance_vote"}:
                severity = "high" if value >= self.whale_threshold_usd * 5 else "medium"
                signals.append(OnChainSignal(event_type, event.asset, severity, 0.80, value, (f"chain={event.chain}",)))
        return tuple(signals)


@dataclass(frozen=True)
class CrossChainState:
    net_flow_by_chain: Mapping[str, float]
    dominant_destination: str
    liquidity_migration_detected: bool
    reasons: tuple[str, ...] = ()


class CrossChainAwareness:
    def assess(self, events: Iterable[BlockchainEvent]) -> CrossChainState:
        flows: dict[str, float] = {}
        for event in events:
            if str(event.event_type).lower() != "bridge_transfer":
                continue
            value = abs(float(event.amount_usd))
            flows[event.chain] = flows.get(event.chain, 0.0) - value
            target_chain = str(event.metadata.get("target_chain") or "")
            if target_chain:
                flows[target_chain] = flows.get(target_chain, 0.0) + value
        dominant = max(flows, key=flows.get) if flows else ""
        positive = max(flows.values(), default=0.0)
        negative = abs(min(flows.values(), default=0.0))
        detected = bool(flows and positive > 0 and negative > 0)
        reasons = (f"dominant_destination={dominant}", f"net_migration_usd={positive:.2f}") if detected else ("no_cross_chain_migration",)
        return CrossChainState(flows, dominant, detected, reasons)
