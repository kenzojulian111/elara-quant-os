from __future__ import annotations

from dataclasses import dataclass

from trading_core.data_contracts import ExecutionPlan, MarketState, TradeCandidate
from trading_core.orchestration.portfolio_brain import PortfolioBrain
from trading_core.risk.risk_engine import RiskDecision, RiskEngine, RiskLimits


@dataclass
class LivePipelineConfig:
    max_risk_per_trade_pct: float = 0.02
    max_drawdown_pct: float = 15.0


class LiveTradingPipeline:
    def __init__(self, portfolio_brain: PortfolioBrain, config: LivePipelineConfig | None = None):
        self.portfolio_brain = portfolio_brain
        limits = RiskLimits(
            max_risk_per_trade_pct=(config.max_risk_per_trade_pct if config else 0.02),
            max_drawdown_pct=(config.max_drawdown_pct if config else 15.0),
        )
        self.risk_engine = RiskEngine(limits)

    def evaluate(self, candidate: TradeCandidate, current_drawdown_pct: float) -> RiskDecision:
        return self.risk_engine.evaluate(candidate, current_drawdown_pct)

    def build_execution_plan(
        self,
        candidate: TradeCandidate,
        decision: RiskDecision,
        *,
        trace_id: str = "",
        decision_reasons: tuple[str, ...] = (),
    ) -> ExecutionPlan | None:
        if not decision.approved:
            return None
        return ExecutionPlan(
            strategy_id=candidate.strategy_id,
            symbol=candidate.symbol,
            direction=candidate.direction,
            entry_price=candidate.entry_price,
            size_pct=decision.size_pct,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            trace_id=trace_id or str(getattr(decision, "trace_id", "") or ""),
            decision_reasons=decision_reasons,
            risk_reason=str(decision.reason or ""),
        )

    def allocate_for_market(self, market_state: MarketState):
        return self.portfolio_brain.build_allocation(market_state)

