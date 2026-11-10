from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ProtocolHealth:
    protocol: str
    tvl_usd: float
    stablecoin_deviation_pct: float = 0.0
    liquidation_risk: float = 0.0
    oracle_health: float = 1.0


@dataclass(frozen=True)
class DeFiHealthReport:
    protocol_count: int
    systemic_risk: float
    unhealthy_protocols: tuple[str, ...]
    reasons: tuple[str, ...]


class DeFiInfrastructureMonitor:
    def assess(self, protocols: Iterable[ProtocolHealth]) -> DeFiHealthReport:
        rows = tuple(protocols)
        risk_by_protocol = {
            row.protocol: min(
                1.0,
                (0.25 if row.tvl_usd < 1_000_000 else 0.0)
                + min(0.5, abs(float(row.stablecoin_deviation_pct)) / 10.0)
                + min(0.5, max(0.0, float(row.liquidation_risk)) * 0.5)
                + min(0.5, max(0.0, 1.0 - float(row.oracle_health)) * 0.5),
            )
            for row in rows
        }
        systemic = max(risk_by_protocol.values(), default=0.0)
        unhealthy = tuple(sorted(name for name, risk in risk_by_protocol.items() if risk >= 0.45))
        return DeFiHealthReport(len(rows), systemic, unhealthy, (f"systemic_risk={systemic:.4f}", f"unhealthy={len(unhealthy)}"))
