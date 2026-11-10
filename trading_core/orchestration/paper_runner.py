from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from trading_core.data_contracts import MarketState, StrategyProfile
from trading_core.execution.adapter import ExecutionReceipt
from trading_core.library.strategy_library import StrategyLibrary
from trading_core.orchestration.paper_pipeline import PaperPipelineConfig, PaperTradingPipeline
from trading_core.orchestration.portfolio_brain import PortfolioBrain


@dataclass(frozen=True)
class PaperRunReport:
    processed_states: int
    receipts: tuple[ExecutionReceipt, ...]
    rejected_states: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.rejected_states


class PaperMarketDataValidator:
    """Small, deterministic ingress guard for the MVP paper runner."""

    def __init__(self, *, max_gap_sec: float = 900.0):
        self.max_gap_sec = max(1.0, float(max_gap_sec))
        self._last_timestamp: datetime | None = None

    def validate(self, state: MarketState) -> str | None:
        values = (state.open, state.high, state.low, state.close, state.volume)
        if not str(state.symbol or "").strip():
            return "market_symbol_missing"
        if any(float(value) < 0 for value in values):
            return "market_value_negative"
        if float(state.close) <= 0 or float(state.high) < float(state.low):
            return "market_ohlcv_invalid"
        if not isinstance(state.ts, datetime) or state.ts.tzinfo is None:
            return "market_timestamp_not_timezone_aware"
        if self._last_timestamp is not None:
            gap = (state.ts - self._last_timestamp).total_seconds()
            if gap <= 0:
                return "market_timestamp_not_monotonic"
            if gap > self.max_gap_sec:
                return f"market_data_stale_gap={gap:.1f}"
        self._last_timestamp = state.ts
        return None


class PaperTradingRunner:
    """Runs one configured strategy from validated market states into paper mode."""

    def __init__(self, pipeline: PaperTradingPipeline, *, max_market_gap_sec: float = 900.0):
        self.pipeline = pipeline
        self.validator = PaperMarketDataValidator(max_gap_sec=max_market_gap_sec)

    @classmethod
    def with_trend_strategy(
        cls,
        *,
        config: PaperPipelineConfig | None = None,
        strategy_id: str = "trend_following_v1",
        max_market_gap_sec: float = 900.0,
    ) -> "PaperTradingRunner":
        library = StrategyLibrary()
        library.register_profile(
            StrategyProfile(
                strategy_id=strategy_id,
                name="Trend Following MVP",
                tags={"trend"},
            )
        )
        return cls(
            PaperTradingPipeline(PortfolioBrain(library), config=config),
            max_market_gap_sec=max_market_gap_sec,
        )

    def run(self, states: Iterable[MarketState]) -> PaperRunReport:
        receipts: list[ExecutionReceipt] = []
        rejected: list[str] = []
        processed = 0
        for state in states:
            rejection = self.validator.validate(state)
            if rejection:
                rejected.append(f"{state.symbol}:{state.ts.isoformat()}:{rejection}")
                continue
            processed += 1
            receipts.extend(self.pipeline.process_state(state))
        return PaperRunReport(processed, tuple(receipts), tuple(rejected))
