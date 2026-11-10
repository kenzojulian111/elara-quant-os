from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PendingTransaction:
    tx_hash: str
    chain: str
    sender: str
    destination: str
    value_usd: float = 0.0
    gas_price_gwei: float = 0.0
    method: str = ""


@dataclass(frozen=True)
class MempoolRiskReport:
    pending_count: int
    abnormal_transactions: tuple[str, ...]
    congestion_score: float
    whale_value_usd: float
    reasons: tuple[str, ...]


class MempoolAnalyzer:
    def __init__(self, *, whale_threshold_usd: float = 1_000_000.0, high_gas_gwei: float = 150.0):
        self.whale_threshold_usd = max(1.0, float(whale_threshold_usd))
        self.high_gas_gwei = max(1.0, float(high_gas_gwei))

    def analyze(self, transactions: Iterable[PendingTransaction]) -> MempoolRiskReport:
        rows = tuple(transactions)
        abnormal = tuple(
            tx.tx_hash
            for tx in rows
            if abs(float(tx.value_usd)) >= self.whale_threshold_usd or float(tx.gas_price_gwei) >= self.high_gas_gwei
        )
        whale_value = sum(abs(float(tx.value_usd)) for tx in rows if abs(float(tx.value_usd)) >= self.whale_threshold_usd)
        high_gas = sum(1 for tx in rows if float(tx.gas_price_gwei) >= self.high_gas_gwei)
        congestion = min(1.0, high_gas / max(1.0, float(len(rows))) + min(0.5, len(rows) / 5000.0))
        reasons = (
            f"pending_count={len(rows)}",
            f"abnormal_count={len(abnormal)}",
            f"congestion_score={congestion:.4f}",
        )
        return MempoolRiskReport(len(rows), abnormal, congestion, whale_value, reasons)
