import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if "numpy" not in sys.modules:
    class _Array(list):
        def mean(self):
            return sum(self) / len(self) if self else 0.0

        def std(self, ddof=0):
            if not self:
                return 0.0
            denom = max(1, len(self) - int(ddof))
            avg = self.mean()
            variance = sum((value - avg) ** 2 for value in self) / denom
            return variance ** 0.5

    fake_numpy = types.ModuleType("numpy")
    fake_numpy.array = lambda values, dtype=float: _Array(dtype(value) for value in values)
    sys.modules["numpy"] = fake_numpy

from trading_core.data_contracts import (
    DecisionEnvelope,
    MarketState,
    StrategyAllocation,
    StrategyDecision,
    StrategyProfile,
)
from trading_core.orchestration.decision_governor import (
    DecisionGovernor,
    GovernanceContext,
)
from trading_core.orchestration.portfolio_brain import PortfolioDecisionPlan
from trading_core.orchestration.shadow_pipeline import ShadowPipelineConfig, ShadowTradingPipeline
from trading_core.strategy.base import PositionState


class _StaticStrategy:
    def __init__(self, profile: StrategyProfile, decision: StrategyDecision):
        self.profile = profile
        self._decision = decision

    def decide(self, state, indicators, position):
        _ = (state, indicators, position)
        return self._decision


class _FakeLibrary:
    def __init__(self, profile: StrategyProfile):
        self.profiles = {profile.strategy_id: profile}


class _FakePortfolioBrain:
    def __init__(self, profile: StrategyProfile, weight: float = 1.0):
        self.library = _FakeLibrary(profile)
        self._allocations = [StrategyAllocation(strategy_id=profile.strategy_id, weight=weight)]

    def build_allocation(self, market_state):
        _ = market_state
        return list(self._allocations)


class _ContradictoryPortfolioBrain(_FakePortfolioBrain):
    def build_decision_plan(self, market_state):
        return PortfolioDecisionPlan(
            symbol=market_state.symbol,
            market_regime="trend",
            selected_ids=(),
            suppressed_ids=(self._allocations[0].strategy_id,),
            allocations=tuple(self._allocations),
            reasons=("market_regime=trend", "portfolio_strategy_suppressed"),
        )


class TradingDecisionGovernorTests(unittest.TestCase):
    def _state(self, **overrides) -> MarketState:
        payload = {
            "ts": datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
            "symbol": "BTC/USDT",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 1000.0,
            "trend": "trend",
            "volatility": "normal",
            "liquidity": "deep",
        }
        payload.update(overrides)
        return MarketState(**payload)

    def test_operator_block_has_highest_priority_for_new_entries(self):
        governor = DecisionGovernor()
        envelope = DecisionEnvelope(
            trace_id="trace",
            strategy_id="trend_following_v1",
            symbol="BTC/USDT",
            status="ready",
            decision="enter_long",
            confidence=0.8,
            reasons=("trend_alignment",),
        )
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        decision = governor.assess(
            envelope=envelope,
            state=self._state(),
            strategy_profile=profile,
            context=GovernanceContext(
                operator_allows_trading=False,
                operator_reason="sleep_debt",
                system_health_score=95.0,
                dependency_states={"exchange": "up"},
            ),
        )

        self.assertFalse(decision.approved)
        self.assertEqual("red", decision.mode)
        self.assertEqual("governor_red_mode_blocks_entries", decision.reason)
        self.assertIn("operator_state_blocked", decision.reasons)
        self.assertEqual("safe", decision.runtime_mode)

    def test_exit_is_allowed_even_in_red_mode(self):
        governor = DecisionGovernor()
        envelope = DecisionEnvelope(
            trace_id="trace",
            strategy_id="trend_following_v1",
            symbol="BTC/USDT",
            status="exit_requested",
            decision="exit",
            confidence=0.7,
            reasons=("trend_long_exit_reversal",),
        )
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        decision = governor.assess(
            envelope=envelope,
            state=self._state(volatility="extreme"),
            strategy_profile=profile,
            context=GovernanceContext(operator_allows_trading=False, operator_reason="manual_freeze", system_health_score=20.0),
        )

        self.assertTrue(decision.approved)
        self.assertEqual("red", decision.mode)
        self.assertEqual("exit_permitted", decision.reason)
        self.assertIn("exit_actions_allowed_in_any_mode", decision.reasons)
        self.assertEqual("safe", decision.runtime_mode)

    def test_shadow_pipeline_blocks_entry_when_governor_turns_yellow(self):
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        portfolio = _FakePortfolioBrain(profile)
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.9,
            stop_loss=98.0,
            take_profit=108.0,
            reasons=("trend_alignment",),
        )

        with tempfile.TemporaryDirectory(prefix="elara_governor_ledger_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                portfolio,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)
            receipts = pipeline.process_state(
                self._state(volatility="extreme"),
                current_drawdown_pct=1.0,
                operator_allows_trading=True,
                system_health_score=95.0,
                dependency_states={"exchange": "up"},
            )
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        self.assertIsNone(pipeline.positions.get(profile.strategy_id, PositionState()).direction)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertTrue(decision_events)
        payload = decision_events[0]["payload"]
        trace = payload["pipeline_trace"]
        self.assertEqual("runtime_mode", trace[-3]["step"])
        self.assertEqual("governance", trace[-2]["step"])
        self.assertEqual("reasoning", trace[-1]["step"])
        self.assertEqual("blocked_governor", payload["status"])
        self.assertFalse(payload["governance_approved"])
        self.assertEqual("yellow", payload["governance_mode"])
        self.assertIn("new_entries_disabled", payload["reasons"])

    def test_shadow_pipeline_allows_exit_when_operator_is_blocked(self):
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        portfolio = _FakePortfolioBrain(profile)
        decision = StrategyDecision(
            action="exit",
            confidence=0.6,
            reasons=("trend_long_exit_reversal",),
        )

        with tempfile.TemporaryDirectory(prefix="elara_governor_exit_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                portfolio,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)
            pipeline.positions[profile.strategy_id] = PositionState(
                direction="long",
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                units=None,
            )
            receipts = pipeline.process_state(
                self._state(),
                current_drawdown_pct=1.0,
                operator_allows_trading=False,
                operator_reason="manual_freeze",
                system_health_score=30.0,
            )

        self.assertEqual([], receipts)
        self.assertIsNone(pipeline.positions[profile.strategy_id].direction)

    def test_shadow_pipeline_does_not_execute_when_portfolio_reasoning_vetoes_strategy(self):
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        portfolio = _ContradictoryPortfolioBrain(profile)
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.9,
            stop_loss=98.0,
            take_profit=108.0,
            reasons=("trend_alignment",),
        )

        with tempfile.TemporaryDirectory(prefix="elara_reasoning_veto_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                portfolio,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)
            receipts = pipeline.process_state(
                self._state(),
                current_drawdown_pct=1.0,
                operator_allows_trading=True,
                system_health_score=95.0,
                dependency_states={"exchange": "up"},
            )
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        self.assertIsNone(pipeline.positions.get(profile.strategy_id, PositionState()).direction)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertTrue(decision_events)
        payload = decision_events[0]["payload"]
        self.assertEqual("blocked_reasoning", payload["status"])
        self.assertFalse(payload["reasoned_decision"]["approved"])
        self.assertEqual("hold", payload["reasoned_decision"]["action"])
        self.assertIn("portfolio_strategy_suppressed", payload["reasoned_decision"]["constraints"])

    def test_shadow_pipeline_dependency_circuit_blocks_flapping_recovery(self):
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        portfolio = _FakePortfolioBrain(profile)
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.9,
            stop_loss=98.0,
            take_profit=108.0,
            reasons=("trend_alignment",),
        )
        clock = {"now": 100.0}

        with tempfile.TemporaryDirectory(prefix="elara_governor_circuit_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                portfolio,
                config=ShadowPipelineConfig(
                    ledger_path=os.path.join(tmpdir, "ledger.jsonl"),
                    dependency_circuit_fail_threshold=2,
                    dependency_circuit_cooldown_sec=60,
                ),
            )
            pipeline.dependency_circuit._time_fn = lambda: clock["now"]
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)

            pipeline.process_state(
                self._state(),
                current_drawdown_pct=1.0,
                operator_allows_trading=True,
                system_health_score=95.0,
                dependency_states={"exchange": "down"},
            )
            pipeline.process_state(
                self._state(),
                current_drawdown_pct=1.0,
                operator_allows_trading=True,
                system_health_score=95.0,
                dependency_states={"exchange": "down"},
            )
            clock["now"] = 110.0
            receipts = pipeline.process_state(
                self._state(),
                current_drawdown_pct=1.0,
                operator_allows_trading=True,
                system_health_score=95.0,
                dependency_states={"exchange": "up"},
            )
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        self.assertIsNone(pipeline.positions.get(profile.strategy_id, PositionState()).direction)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertTrue(decision_events)
        latest = decision_events[-1]["payload"]
        self.assertEqual("safe", latest["runtime_mode"])
        runtime_trace = [step for step in latest["pipeline_trace"] if step.get("step") == "runtime_mode"]
        self.assertTrue(runtime_trace)
        self.assertIn("dependency_circuit_open=exchange:up", runtime_trace[-1]["reasons"])

    def test_shadow_pipeline_blocks_entry_when_redis_dependency_is_down(self):
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        portfolio = _FakePortfolioBrain(profile)
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.9,
            stop_loss=98.0,
            take_profit=108.0,
            reasons=("trend_alignment",),
        )

        with tempfile.TemporaryDirectory(prefix="elara_governor_redis_down_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                portfolio,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)
            with patch(
                "trading_core.orchestration.shadow_pipeline.health_snapshot",
                return_value={
                    "db_write_lock_contention_rate_per_min": 0.0,
                    "loop_lag_ms": 5.0,
                    "mem_pct": 10.0,
                    "cpu_pct": 10.0,
                },
            ):
                receipts = pipeline.process_state(
                    self._state(),
                    current_drawdown_pct=1.0,
                    operator_allows_trading=True,
                    system_health_score=95.0,
                    dependency_states={"redis": "down", "exchange": "up"},
                )
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        self.assertIsNone(pipeline.positions.get(profile.strategy_id, PositionState()).direction)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertTrue(decision_events)
        latest = decision_events[-1]["payload"]
        self.assertEqual("degraded", latest["runtime_mode"])
        runtime_trace = [step for step in latest["pipeline_trace"] if step.get("step") == "runtime_mode"]
        self.assertTrue(runtime_trace)
        self.assertIn("support_dependency_down=redis:down:event_bus", runtime_trace[-1]["reasons"])

    def test_shadow_pipeline_blocks_entry_on_partial_dependency_failure(self):
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        portfolio = _FakePortfolioBrain(profile)
        decision = StrategyDecision(
            action="enter_long",
            confidence=0.9,
            stop_loss=98.0,
            take_profit=108.0,
            reasons=("trend_alignment",),
        )

        with tempfile.TemporaryDirectory(prefix="elara_governor_partial_dep_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                portfolio,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)
            with patch(
                "trading_core.orchestration.shadow_pipeline.health_snapshot",
                return_value={
                    "db_write_lock_contention_rate_per_min": 0.0,
                    "loop_lag_ms": 5.0,
                    "mem_pct": 10.0,
                    "cpu_pct": 10.0,
                },
            ):
                receipts = pipeline.process_state(
                    self._state(),
                    current_drawdown_pct=1.0,
                    operator_allows_trading=True,
                    system_health_score=95.0,
                    dependency_states={"exchange": "partial"},
                )
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertTrue(decision_events)
        latest = decision_events[-1]["payload"]
        self.assertEqual("degraded", latest["runtime_mode"])
        runtime_trace = [step for step in latest["pipeline_trace"] if step.get("step") == "runtime_mode"]
        self.assertTrue(runtime_trace)
        self.assertIn("core_dependency_degraded=exchange:partial:market_access", runtime_trace[-1]["reasons"])

    def test_shadow_pipeline_allows_exit_when_runtime_safe_due_to_db_contention(self):
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        portfolio = _FakePortfolioBrain(profile)
        decision = StrategyDecision(
            action="exit",
            confidence=0.6,
            reasons=("trend_long_exit_reversal",),
        )

        with tempfile.TemporaryDirectory(prefix="elara_governor_db_contention_") as tmpdir:
            pipeline = ShadowTradingPipeline(
                portfolio,
                config=ShadowPipelineConfig(ledger_path=os.path.join(tmpdir, "ledger.jsonl")),
            )
            pipeline.strategies[profile.strategy_id] = _StaticStrategy(profile, decision)
            pipeline.positions[profile.strategy_id] = PositionState(
                direction="long",
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                units=None,
            )
            with patch(
                "trading_core.orchestration.shadow_pipeline.health_snapshot",
                return_value={
                    "db_write_lock_contention_rate_per_min": 15.0,
                    "loop_lag_ms": 5.0,
                    "mem_pct": 10.0,
                    "cpu_pct": 10.0,
                },
            ):
                receipts = pipeline.process_state(
                    self._state(),
                    current_drawdown_pct=1.0,
                    operator_allows_trading=True,
                    system_health_score=95.0,
                    dependency_states={"exchange": "up"},
                )
            events = list(pipeline.ledger.read())

        self.assertEqual([], receipts)
        self.assertIsNone(pipeline.positions[profile.strategy_id].direction)
        decision_events = [event for event in events if event.get("event_type") == "decision"]
        self.assertTrue(decision_events)
        latest = decision_events[-1]["payload"]
        self.assertEqual("safe", latest["runtime_mode"])
        runtime_trace = [step for step in latest["pipeline_trace"] if step.get("step") == "runtime_mode"]
        self.assertTrue(runtime_trace)
        self.assertIn("db_contention_safe=15.00", runtime_trace[-1]["reasons"])

    def test_governor_ignores_experimental_dependency_failure_for_entry(self):
        governor = DecisionGovernor()
        envelope = DecisionEnvelope(
            trace_id="trace",
            strategy_id="trend_following_v1",
            symbol="BTC/USDT",
            status="ready",
            decision="enter_long",
            confidence=0.8,
            reasons=("trend_alignment",),
        )
        profile = StrategyProfile(strategy_id="trend_following_v1", name="Trend", tags={"trend"})
        decision = governor.assess(
            envelope=envelope,
            state=self._state(),
            strategy_profile=profile,
            context=GovernanceContext(
                operator_allows_trading=True,
                system_health_score=95.0,
                dependency_states={"voice": "down", "exchange": "up"},
                runtime_mode="full",
                runtime_reasons=("runtime_full",),
            ),
        )

        self.assertTrue(decision.approved)
        self.assertEqual("green", decision.mode)
        self.assertIn("experimental_dependency_isolated=voice:down:operator_voice", decision.reasons)


if __name__ == "__main__":
    unittest.main()

