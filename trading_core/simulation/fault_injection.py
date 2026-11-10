from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable

from trading_core.data_contracts import MarketState


@dataclass(frozen=True)
class FaultRule:
    name: str
    kind: str
    severity: float = 1.0
    probability: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FaultResult:
    state: MarketState | None
    applied: tuple[str, ...] = ()
    dropped: bool = False
    latency_ms: float = 0.0


class FaultInjector:
    def __init__(self, rules: Iterable[FaultRule] = (), *, seed: int | None = None):
        self.rules = tuple(rules or ())
        self.random = random.Random(seed)

    def apply(self, state: MarketState) -> FaultResult:
        current = state
        applied: list[str] = []
        latency_ms = 0.0
        dropped = False
        for rule in self.rules:
            if self.random.random() > max(0.0, min(1.0, float(rule.probability))):
                continue
            kind = str(rule.kind or "").strip().lower()
            applied.append(rule.name)
            if kind == "packet_loss":
                dropped = True
                current = None
                break
            if kind == "latency":
                latency_ms += max(0.0, float(rule.severity) * 100.0)
            if kind == "slippage" and current is not None:
                current = self._shift_price(current, pct=float(rule.severity))
            if kind == "liquidity_collapse" and current is not None:
                current = self._replace(current, volume=max(0.0, current.volume * max(0.0, 1.0 - float(rule.severity))))
            if kind == "exchange_outage":
                dropped = True
                current = None
                break
        return FaultResult(state=current, applied=tuple(applied), dropped=dropped, latency_ms=latency_ms)

    def _shift_price(self, state: MarketState, *, pct: float) -> MarketState:
        multiplier = 1.0 + (float(pct) / 100.0)
        return self._replace(
            state,
            open=state.open * multiplier,
            high=state.high * multiplier,
            low=state.low * multiplier,
            close=state.close * multiplier,
        )

    @staticmethod
    def _replace(state: MarketState, **updates) -> MarketState:
        payload = {
            "ts": state.ts,
            "symbol": state.symbol,
            "open": state.open,
            "high": state.high,
            "low": state.low,
            "close": state.close,
            "volume": state.volume,
            "order_book_imbalance": state.order_book_imbalance,
            "funding_rate": state.funding_rate,
            "macro_signal": state.macro_signal,
            "trend": state.trend,
            "volatility": state.volatility,
            "liquidity": state.liquidity,
        }
        payload.update(updates)
        return MarketState(**payload)


def reconnect_storm_rules(probability: float = 1.0) -> tuple[FaultRule, ...]:
    return (
        FaultRule("reconnect_latency", "latency", severity=5.0, probability=probability),
        FaultRule("reconnect_packet_loss", "packet_loss", severity=1.0, probability=min(1.0, probability * 0.2)),
    )
