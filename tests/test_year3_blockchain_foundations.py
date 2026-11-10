from __future__ import annotations

import unittest

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:  # pragma: no cover - optional until dependencies are installed
    serialization = None
    Ed25519PrivateKey = None

from trading_core.blockchain import (
    BlockchainEvent,
    CrossChainAwareness,
    CrossMarketCorrelationEngine,
    CrossMarketSeries,
    CryptographicGovernance,
    MempoolAnalyzer,
    OnChainAgentSandbox,
    OnChainIntelligence,
    PendingTransaction,
    ProtocolResearchLayer,
    SandboxAction,
    SmartContractRiskEngine,
    SmartContractRiskInput,
)
from trading_core.simulation import DeFiFailureScenario, DeFiFailureSimulator


class Year3BlockchainFoundationTests(unittest.TestCase):
    def test_onchain_and_cross_chain_signals(self) -> None:
        event = BlockchainEvent(
            chain="ethereum",
            event_type="bridge_transfer",
            tx_hash="tx-1",
            asset="USDC",
            amount_usd=2_000_000,
            metadata={"target_chain": "arbitrum"},
        )
        signals = OnChainIntelligence().analyze((event,))
        state = CrossChainAwareness().assess((event,))
        self.assertEqual({signal.signal_type for signal in signals}, {"whale_movement", "bridge_transfer"})
        self.assertEqual(state.dominant_destination, "arbitrum")
        self.assertTrue(state.liquidity_migration_detected)

    def test_mempool_and_contract_risk_surface_abnormality(self) -> None:
        report = MempoolAnalyzer().analyze(
            (PendingTransaction("tx-1", "ethereum", "a", "b", value_usd=2_000_000, gas_price_gwei=180),)
        )
        risk = SmartContractRiskEngine().assess(
            SmartContractRiskInput("bridge", 100_000, governance_concentration=0.9, bridge_dependency=1.0)
        )
        self.assertEqual(report.abnormal_transactions, ("tx-1",))
        self.assertGreaterEqual(risk.risk_score, 0.50)

    def test_signed_artifact_and_research_are_bounded(self) -> None:
        governance = CryptographicGovernance()
        artifact = governance.sign("decision", {"decision": "HOLD"}, secret="local-test", key_id="operator-1")
        finding = ProtocolResearchLayer().assess(
            protocol="protocol-a",
            claim="liquidity migration detected",
            confidence=0.8,
            sources=("source-a", "source-b"),
        )
        self.assertTrue(governance.verify(artifact, {"decision": "HOLD"}, secret="local-test"))
        self.assertTrue(finding.verified)
        self.assertFalse(ProtocolResearchLayer.can_execute(finding))

    @unittest.skipIf(Ed25519PrivateKey is None, "cryptography not installed")
    def test_ed25519_signed_artifact_verifies_without_shared_secret(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        governance = CryptographicGovernance()
        artifact = governance.sign_ed25519(
            "decision",
            {"decision": "HOLD", "risk": 0.2},
            private_key_pem=private_pem,
            key_id="operator-ed25519",
        )

        self.assertEqual(artifact.algorithm, "ed25519")
        self.assertTrue(
            governance.verify_ed25519(
                artifact,
                {"decision": "HOLD", "risk": 0.2},
                public_key_pem=public_pem,
            )
        )
        self.assertFalse(
            governance.verify_ed25519(
                artifact,
                {"decision": "BUY", "risk": 0.2},
                public_key_pem=public_pem,
            )
        )

    def test_simulation_and_sandbox_never_enable_live_onchain_actions(self) -> None:
        impact = DeFiFailureSimulator().run(DeFiFailureScenario("stablecoin_depeg", 1.0, "stablecoin"))
        live = OnChainAgentSandbox().authorize(SandboxAction("swap", "dex", 50), mode="live")
        self.assertLess(impact.trust_multiplier, 0.50)
        self.assertFalse(live.accepted)

    def test_cross_market_correlation_detects_systemic_pair(self) -> None:
        report = CrossMarketCorrelationEngine().assess(
            (
                CrossMarketSeries("btc", (1, 2, 3, 4)),
                CrossMarketSeries("eth", (2, 4, 6, 8)),
                CrossMarketSeries("cash", (1, 1, 1, 1)),
            )
        )
        self.assertEqual(len(report.systemic_pairs), 1)
        self.assertEqual(report.systemic_pairs[0].correlation, 1.0)


if __name__ == "__main__":
    unittest.main()
