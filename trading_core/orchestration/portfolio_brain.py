from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from trading_core.data_contracts import MarketState, StrategyAllocation
from trading_core.library.strategy_library import StrategyLibrary
from trading_core.portfolio.allocator import AllocationPolicy, CapitalAllocator
from trading_core.portfolio.performance_tracker import StrategyPerformanceTracker
from trading_core.portfolio.risk_balancer import RiskBalancePolicy, RiskBalancer
from trading_core.portfolio.selector import StrategySelector
from trading_core.portfolio.weight_store import WeightStore


@dataclass(frozen=True)
class PortfolioDecisionPlan:
    symbol: str
    market_regime: str
    selected_ids: tuple[str, ...] = ()
    blocked_ids: tuple[str, ...] = ()
    suppressed_ids: tuple[str, ...] = ()
    allocations: tuple[StrategyAllocation, ...] = ()
    review_status: dict[str, str] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


class PortfolioBrain:
    def __init__(
        self,
        library: StrategyLibrary,
        *,
        allocation_policy: AllocationPolicy | None = None,
        risk_policy: RiskBalancePolicy | None = None,
        weight_store_path: str | None = None,
    ):
        self.library = library
        self.selector = StrategySelector()
        self.tracker = StrategyPerformanceTracker(library.performance.copy())
        self.allocator = CapitalAllocator(allocation_policy or AllocationPolicy())
        self.risk_balancer = RiskBalancer(risk_policy or RiskBalancePolicy())
        self.weight_store = WeightStore.load(weight_store_path or "") if weight_store_path else None
        self.last_plan: PortfolioDecisionPlan | None = None

    def build_allocation(self, market_state: MarketState) -> List[StrategyAllocation]:
        return list(self.build_decision_plan(market_state).allocations)

    def build_decision_plan(self, market_state: MarketState) -> PortfolioDecisionPlan:
        profiles = self.library.list_profiles()
        profile_ids = [profile.strategy_id for profile in profiles]
        reviews = self.tracker.reviews(profile_ids)
        selection = self.selector.decide(profiles, market_state, reviews)
        base_allocations = self.allocator.allocate(self.tracker, list(selection.selected_ids))
        balanced = self.risk_balancer.balance(base_allocations)
        if self.weight_store:
            balanced = self.weight_store.apply(balanced)
        plan = PortfolioDecisionPlan(
            symbol=market_state.symbol,
            market_regime=selection.market_regime,
            selected_ids=selection.selected_ids,
            blocked_ids=selection.blocked_ids,
            suppressed_ids=selection.suppressed_ids,
            allocations=tuple(balanced),
            review_status={strategy_id: reviews[strategy_id].status for strategy_id in profile_ids if strategy_id in reviews},
            reasons=selection.reasons,
        )
        self.last_plan = plan
        return plan

