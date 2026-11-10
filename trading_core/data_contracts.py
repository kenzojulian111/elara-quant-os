from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class MarketState:
    ts: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    order_book_imbalance: Optional[float] = None
    funding_rate: Optional[float] = None
    macro_signal: Optional[str] = None
    trend: Optional[str] = None
    volatility: Optional[str] = None
    liquidity: Optional[str] = None


@dataclass(frozen=True)
class IndicatorSnapshot:
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    atr_pct: float
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    adx: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_mid: float = 0.0
    bb_width: float = 0.0
    rsi_slope: float = 0.0


@dataclass(frozen=True)
class StrategyProfile:
    strategy_id: str
    name: str
    tags: set[str] = field(default_factory=set)
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyDecision:
    action: str
    confidence: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeCandidate:
    strategy_id: str
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: Optional[float]
    size_pct: float


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    size_pct: float
    stop_loss: float
    take_profit: Optional[float]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    risk_budget_pct: float = 0.0
    trace_id: str = ""


@dataclass(frozen=True)
class DecisionTraceStep:
    step: str
    status: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReasoningComponent:
    source: str
    label: str
    confidence: float
    weight: float
    verdict: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReasonedDecision:
    action: str
    approved: bool
    confidence: float
    summary: str
    explanation: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    constraints: tuple[str, ...] = field(default_factory=tuple)
    regime: str = ""
    risk_score: float = 0.0
    probability: float = 0.0
    uncertainty: float = 0.0
    expected_utility: float = 0.0
    cognitive_mode: str = ""
    component_scores: Dict[str, Any] = field(default_factory=dict)
    components: tuple[ReasoningComponent, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GovernanceDecision:
    approved: bool
    mode: str
    reason: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    severity: str = "info"
    runtime_mode: str = ""
    runtime_reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExecutionPlan:
    strategy_id: str
    symbol: str
    direction: str
    entry_price: float
    size_pct: float
    stop_loss: float
    take_profit: Optional[float]
    trace_id: str = ""
    decision_reasons: tuple[str, ...] = field(default_factory=tuple)
    risk_reason: str = ""
    execution_id: str = ""


@dataclass(frozen=True)
class DecisionEnvelope:
    trace_id: str
    strategy_id: str
    symbol: str
    status: str
    decision: str
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    pipeline_trace: tuple[DecisionTraceStep, ...] = field(default_factory=tuple)
    allocation_weight: float = 0.0
    current_drawdown_pct: float = 0.0
    candidate: Optional[TradeCandidate] = None
    risk: Optional[RiskDecision] = None
    governance: Optional[GovernanceDecision] = None
    execution_plan: Optional[ExecutionPlan] = None
    reasoned_decision: Optional[ReasonedDecision] = None


@dataclass(frozen=True)
class StrategyPerformance:
    strategy_id: str
    trades: int
    pnl: float
    max_drawdown_pct: float
    sharpe: float
    win_rate: float


@dataclass(frozen=True)
class StrategyAllocation:
    strategy_id: str
    weight: float

