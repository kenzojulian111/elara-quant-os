from __future__ import annotations

import unittest
from pathlib import Path

from trading_core.cognition import CognitiveOpinion
from trading_core.execution import VenueQuote
from trading_core.institutional import InstitutionalDecisionContext, InstitutionalFinancialCognition
from trading_core.portfolio import InstitutionalExposure
from trading_core.roadmap import Year5ReadinessChecker
from trading_core.simulation import BlackSwanScenario
from trading_core.world_model import CapitalFlowObservation


class Year5InstitutionalCognitionTests(unittest.TestCase):
    def test_institutional_decision_allows_governed_execution(self) -> None:
        report = InstitutionalFinancialCognition().decide(
            InstitutionalDecisionContext(
                symbol="BTCUSDT",
                quantity=2,
                runtime_mode="green",
                uncertainty=0.20,
                signed_artifact=True,
                capital_flows=(CapitalFlowObservation("stablecoin-treasury", "exchange-depth", "USDC", 500_000),),
                exposures=(InstitutionalExposure("BTCUSDT", 10_000),),
                stress_loss_by_symbol={"BTCUSDT": 0.05},
                venue_quotes=(VenueQuote("fast", 5, slippage_bps=3, latency_ms=12),),
                cognitive_opinions=(
                    CognitiveOpinion("macro", "BUY", 0.80, 1.0),
                    CognitiveOpinion("liquidity", "BUY", 0.70, 1.0),
                ),
            )
        )

        self.assertTrue(report.allowed)
        self.assertEqual("BUY", report.action)
        self.assertEqual("fast", report.execution_venue)
        self.assertEqual("allow", report.governance_mode)

    def test_institutional_decision_blocks_catastrophic_context(self) -> None:
        report = InstitutionalFinancialCognition().decide(
            InstitutionalDecisionContext(
                symbol="BTCUSDT",
                quantity=2,
                runtime_mode="yellow",
                uncertainty=0.30,
                signed_artifact=True,
                capital_flows=(CapitalFlowObservation("wallet", "exchange-binance", "BTC", 2_000_000),),
                exposures=(InstitutionalExposure("BTCUSDT", 10_000),),
                stress_loss_by_symbol={"BTCUSDT": 0.30},
                venue_quotes=(VenueQuote("fast", 5, slippage_bps=3, latency_ms=12),),
                cognitive_opinions=(CognitiveOpinion("risk", "BUY", 0.90, 1.0),),
                black_swan=BlackSwanScenario("liquidation_cascade", 1.0),
            )
        )

        self.assertFalse(report.allowed)
        self.assertEqual("HOLD", report.action)
        self.assertTrue(report.hedge_required)
        self.assertIn("portfolio_drawdown_risk_above_limit", report.reasons)
        self.assertIn("capital_flow_risk_off", report.reasons)
        self.assertIn("black_swan_execution_block", report.reasons)

    def test_year5_readiness_baseline_is_tracked(self) -> None:
        root = Path(__file__).resolve().parent.parent
        report = Year5ReadinessChecker(root).build_report()

        self.assertGreaterEqual(report.score, 90)
        self.assertTrue(report.baseline_ready)
        self.assertEqual((), report.missing_anchors)


if __name__ == "__main__":
    unittest.main()
